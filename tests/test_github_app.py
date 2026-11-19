#!/usr/bin/env python3
"""GitHub as a GitHub App: the broker's routes, the tokens, the helper, the
nightly check. Added 2026-09-30 with lib/loam_github_app.py.

Everything runs against a fake GitHub on loopback that answers the six calls
the integration makes -- the manifest conversion, the OAuth token endpoint
(code and refresh), the installation, the user, and installation-token
minting -- and that CHECKS the app JWT's RS256 signature against the public
half of a key generated for this run. The broker is the shipped
bin/session-broker, loaded as a module with its state in a temp directory;
requests go through its real `_handle`, header parsing and bearer check
included. The store, its lock and the nightly date file are in the same temp
directory. Nothing here touches the box's broker, its store or GitHub.

What a fake cannot prove is which real token GitHub accepts for
`POST /user/repos`; that is in broker/README.md, "GitHub".

Run: python3 tests/test_github_app.py
"""

from __future__ import annotations

import asyncio
import base64
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# A fault's fact for the system persona lands in scratch, never the box's.
os.environ.setdefault("LOAM_ATTENTION_DIR", tempfile.mkdtemp(prefix="loam-attention-"))
os.environ.setdefault("LOAM_WAITERS_DIR", os.environ["LOAM_ATTENTION_DIR"] + "/waiters")

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
# Admission reads no real box here: a fixture session must not be refused
# because the machine running the suite is short of memory (a landing
# got 429 for one, 2026-11-09). test_broker_seats is where admission is tested.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_github  # noqa: E402
import loam_github_app as gh  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results: list[bool] = []
SHAPES: dict[str, dict] = {}


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} got={got!r}"[:220])
    if not ok:
        print(f"      wanted {want!r}")


# ---------------------------------------------------------------------------
# A fake GitHub
# ---------------------------------------------------------------------------

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.TraditionalOpenSSL,
                        serialization.NoEncryption()).decode()
CLIENT_ID = "Iv23liTESTCLIENT"


class Fake:
    calls: list[tuple[str, str, dict]] = []
    user_token = None
    refresh_token = None
    pair = 0
    minted = 0
    installation_gone = False
    jwt_ok: list[bool] = []
    selection = "all"
    # What the installation has accepted. A mint that asks for more is a 422,
    # as on GitHub.
    permissions = {"contents": "write", "metadata": "read", "workflows": "write",
                   "administration": "write"}
    # What the installation reaches, by name; and repo id -> name, for the
    # PUT that adds one. A token for a name outside `reaches` is a 422.
    reaches = {"loam-ui"}
    ids = {7001: "fresh", 7002: "orphan"}

    @classmethod
    def issue(cls) -> dict:
        cls.pair += 1
        cls.user_token = f"ghu_user{cls.pair}"
        cls.refresh_token = f"ghr_refresh{cls.pair}"
        return {"access_token": cls.user_token, "expires_in": 28800,
                "refresh_token": cls.refresh_token,
                "refresh_token_expires_in": 15897600,
                "token_type": "bearer", "scope": ""}


def unb64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def jwt_valid(header: str) -> bool:
    if not header.startswith("Bearer "):
        return False
    try:
        head, claims, sig = header[7:].split(".")
        KEY.public_key().verify(unb64(sig), f"{head}.{claims}".encode(),
                                padding.PKCS1v15(), hashes.SHA256())
        body = json.loads(unb64(claims))
        return (json.loads(unb64(head))["alg"] == "RS256"
                and body["iss"] == CLIENT_ID and body["exp"] > time.time()
                and body["exp"] - body["iat"] <= 600)
    except Exception:  # noqa: BLE001
        return False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def answer(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.headers.get("Content-Type", "").startswith("application/x-www-form"):
            return dict(urllib.parse.parse_qsl(raw.decode()))
        return json.loads(raw) if raw else {}

    def do_POST(self):  # noqa: N802
        body = self.body()
        Fake.calls.append(("POST", self.path, body))
        if self.path.startswith("/app-manifests/") and self.path.endswith("/conversions"):
            code = self.path.split("/")[2]
            if code != "manifest-code":
                return self.answer(404, {"message": "Not Found"})
            return self.answer(201, {
                "id": 424242, "slug": "loam", "html_url": "https://github.com/apps/loam",
                "owner": {"login": "octo-owner"}, "client_id": CLIENT_ID,
                "client_secret": "secret", "pem": PEM, "webhook_secret": None})
        if self.path == "/login/oauth/access_token":
            if body.get("grant_type") == "refresh_token":
                if body.get("refresh_token") != Fake.refresh_token:
                    return self.answer(200, {"error": "bad_refresh_token"})
                return self.answer(200, Fake.issue())
            if body.get("code") == "oauth-code" and body.get("client_secret") == "secret":
                return self.answer(200, Fake.issue())
            return self.answer(200, {"error": "bad_verification_code"})
        if self.path.startswith("/app/installations/") and self.path.endswith("/access_tokens"):
            ok = jwt_valid(self.headers.get("Authorization", ""))
            Fake.jwt_ok.append(ok)
            if not ok:
                return self.answer(401, {"message": "A JSON web token could not be decoded"})
            asked = body.get("permissions") or {}
            if any(Fake.permissions.get(k) not in (v, "write") for k, v in asked.items()):
                return self.answer(422, {"message": "The permissions requested are "
                                         "not granted to this installation."})
            wanted = set(body.get("repositories") or [])
            if wanted - Fake.reaches:
                return self.answer(422, {"message": "There is at least one repository "
                                         "that does not exist or is not accessible "
                                         "to the parent installation."})
            Fake.minted += 1
            return self.answer(201, {"token": f"ghs_install{Fake.minted}",
                                     "expires_at": time.strftime(
                                         "%Y-%m-%dT%H:%M:%SZ",
                                         time.gmtime(time.time() + 3600))})
        return self.answer(404, {"message": "Not Found"})

    def do_GET(self):  # noqa: N802
        Fake.calls.append(("GET", self.path, {}))
        auth = self.headers.get("Authorization", "")
        if self.path.startswith("/app/installations/"):
            ok = jwt_valid(auth)
            Fake.jwt_ok.append(ok)
            if not ok:
                return self.answer(401, {"message": "bad JWT"})
            if Fake.installation_gone:
                return self.answer(404, {"message": "Not Found"})
            return self.answer(200, {"id": 99, "account": {"login": "octo-owner"},
                                     "repository_selection": Fake.selection,
                                     "permissions": dict(Fake.permissions)})
        if self.path == "/user":
            if auth != f"Bearer {Fake.user_token}":
                return self.answer(401, {"message": "Bad credentials"})
            return self.answer(200, {"login": "octo-owner"})
        return self.answer(404, {"message": "Not Found"})

    def do_PUT(self):  # noqa: N802
        Fake.calls.append(("PUT", self.path, {}))
        parts = self.path.strip("/").split("/")
        if parts[:2] == ["user", "installations"] and parts[3:4] == ["repositories"]:
            if self.headers.get("Authorization") != f"Bearer {Fake.user_token}":
                return self.answer(401, {"message": "Bad credentials"})
            Fake.reaches.add(Fake.ids[int(parts[4])])
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return self.answer(404, {"message": "Not Found"})


# ---------------------------------------------------------------------------
# Requests through the real broker
# ---------------------------------------------------------------------------

class FakeWriter:
    def __init__(self):
        self.buf = bytearray()

    def write(self, data):
        self.buf += data

    async def drain(self):
        pass

    def close(self):
        pass


async def request(server, method, target, payload=None, token=None):
    body = json.dumps(payload).encode() if payload is not None else b""
    head = (f"{method} {target} HTTP/1.1\r\n"
            f"Authorization: Bearer {token or server.broker.token}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n\r\n")
    reader = asyncio.StreamReader()
    reader.feed_data(head.encode() + body)
    reader.feed_eof()
    writer = FakeWriter()
    await server.handle(reader, writer)
    head, _, rest = bytes(writer.buf).decode().partition("\r\n\r\n")
    return int(head.split()[1]), json.loads(rest)


def store() -> dict:
    return json.loads(gh.STORE_PATH.read_text())


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


async def main(root: Path, fake_url: str) -> None:
    server = broker_mod.Server(broker_mod.Broker())
    base = "/v1/integrations/github"

    # -- test_bearer_required ------------------------------------------------
    status, _ = await request(server, "GET", base, token="wrong")
    check("test_bearer_required: GET without the bearer is 401", status, 401)

    # -- test_state_unregistered --------------------------------------------
    status, view = await request(server, "GET", base)
    SHAPES["unregistered"] = view
    check("test_state_unregistered: status", (status, view["status"]),
          (200, "unregistered"))
    check("...every part null", (view["app"], view["installed"], view["user"]),
          (None, None, None))
    check("...and the uses listed", view["uses"], ["provisioning", "engineer pushes"])
    # The personal-token fallback is gone (2026-10-01): a token in the
    # environment is not a credential, and not connected is an error.
    os.environ["GITHUB_TOKEN"] = "ghp_should_never_be_read"
    try:
        loam_github.token()
        refused = None
    except loam_github.GitHubError as exc:
        refused = str(exc)
    finally:
        os.environ.pop("GITHUB_TOKEN", None)
    check("...token() refuses when not connected, GITHUB_TOKEN set or not",
          bool(refused and "not connected" in refused
               and "ghp_" not in refused), True)

    # -- test_manifest_and_state --------------------------------------------
    status, out = await request(server, "POST", f"{base}/manifest", {})
    m = out["manifest"]
    check("test_manifest_and_state: 200", status, 200)
    check("...name Loam, private, oauth on install, setup on update",
          (m["name"], m["public"], m["request_oauth_on_install"], m["setup_on_update"]),
          ("Loam", False, True, True))
    # The app's address is its deploy record's: the domain itself since
    # 2026-11-15 (L14, `apex = true`), loam-ui.<domain> before.
    import loam_deploy  # noqa: PLC0415
    app_base = "https://" + next(a.fqdn for a in loam_deploy.records() if a.name == "loam-ui")
    check("...redirect_url on the app's public address",
          m["redirect_url"], f"{app_base}/integrations/github/registered")
    check("...callback_urls on the app's public address",
          m["callback_urls"], [f"{app_base}/integrations/github/installed"])
    check("...webhook inactive", m["hook_attributes"]["active"], False)
    check("...the four permissions and nothing else", m["default_permissions"],
          {"administration": "write", "contents": "write", "metadata": "read",
           "workflows": "write"})
    check("...post_url carries the state", out["post_url"],
          f"{fake_url}/settings/apps/new?state={out['state']}")
    check("...the state is remembered on disk", out["state"] in store()["states"], True)
    check("...the store is mode 600", oct(gh.STORE_PATH.stat().st_mode & 0o777), "0o600")
    _, suffixed = await request(server, "POST", f"{base}/manifest", {"name": "Loam (myloam)"})
    check("...a name GitHub refused can carry a suffix", suffixed["manifest"]["name"],
          "Loam (myloam)")

    status, err = await request(server, "POST", f"{base}/register",
                                {"code": "manifest-code", "state": "not-ours"})
    check("test_manifest_and_state: a state we did not issue is refused",
          status, 400)

    # -- test_register_exchanges_code ---------------------------------------
    status, reg = await request(server, "POST", f"{base}/register",
                                {"code": "manifest-code", "state": out["state"]})
    check("test_register_exchanges_code: 200 and status registered",
          (status, reg["status"]), (200, "registered"))
    check("...called the conversion endpoint", any(
        c[1] == "/app-manifests/manifest-code/conversions" for c in Fake.calls), True)
    check("...the key and secret are stored, not answered",
          (store()["app"]["pem"] == PEM, "pem" in json.dumps(reg),
           "secret" in json.dumps(reg).replace("webhook", "")), (True, False, False))
    check("...an install URL with a fresh state",
          reg["install"]["url"],
          f"{fake_url}/apps/loam/installations/new?state={reg['install']['state']}")
    status, again = await request(server, "POST", f"{base}/register",
                                  {"code": "manifest-code", "state": out["state"]})
    check("...a state is single-use", status, 400)
    SHAPES["registered"] = {k: v for k, v in reg.items() if k != "install"}

    # -- test_callback_installed_only (no OAuth code) ----------------------
    status, inst = await request(server, "POST", f"{base}/callback", {
        "installation_id": 99, "setup_action": "install",
        "state": reg["install"]["state"]})
    SHAPES["installed"] = inst
    check("test_callback_installed_only: status installed",
          (status, inst["status"]), (200, "installed"))
    check("...verified with an app JWT whose signature checks",
          Fake.jwt_ok[-1], True)
    check("...installation recorded", (inst["installed"]["installation_id"],
                                       inst["installed"]["account"],
                                       inst["installed"]["repository_selection"]),
          (99, "octo-owner", "all"))

    # -- test_callback_authorizes (install again, with the OAuth code) ------
    _, again = await request(server, "POST", f"{base}/install", {})
    status, auth = await request(server, "POST", f"{base}/callback", {
        "code": "oauth-code", "installation_id": 99, "setup_action": "install",
        "state": again["state"]})
    SHAPES["authorized"] = auth
    check("test_callback_authorizes: status authorized",
          (status, auth["status"]), (200, "authorized"))
    check("...the user verified with GET /user", auth["user"]["login"], "octo-owner")
    check("...no token in the answer", "ghu_" in json.dumps(auth), False)
    check("...token pair stored", (store()["user"]["token"],
                                   store()["user"]["refresh_token"]),
          ("ghu_user1", "ghr_refresh1"))
    status, _ = await request(server, "POST", f"{base}/callback", {
        "code": "oauth-code", "installation_id": 99, "setup_action": "update"})
    check("...GitHub's own update redirect (no state) accepted for this installation",
          status, 200)
    status, _ = await request(server, "POST", f"{base}/callback", {
        "code": "oauth-code", "installation_id": 7, "setup_action": "update"})
    check("...but not for another installation", status, 400)

    # -- test_refresh_route --------------------------------------------------
    before = Fake.pair
    status, ref = await request(server, "POST", f"{base}/refresh", {})
    check("test_refresh_route: refreshes now and records health",
          (status, Fake.pair, ref["health"]["last_error"], bool(ref["health"]["last_ok_at"])),
          (200, before + 1, None, True))
    check("...the refresh used grant_type=refresh_token with the old refresh token",
          [c[2].get("grant_type") for c in Fake.calls
           if c[1] == "/login/oauth/access_token"][-1], "refresh_token")

    # -- test_refresh_on_demand ---------------------------------------------
    data = store()
    data["user"]["token_expires_at"] = iso(time.time() + 1800)
    gh.save(data)
    before = Fake.pair
    tok = gh.user_token()
    check("test_refresh_on_demand: within an hour of expiry, refreshed first",
          (tok, Fake.pair), (Fake.user_token, before + 1))
    tok2 = gh.user_token()
    check("...and not again while it is fresh", (tok2, Fake.pair), (tok, before + 1))
    check("...loam_github.token() is the integration's user token",
          loam_github.token(), tok)

    # -- test_installation_token_minting ------------------------------------
    minted = Fake.minted
    status, t1 = await request(server, "POST", f"{base}/token",
                               {"repository": "octo-owner/loam-ui"})
    check("test_installation_token_minting: minted with a signed JWT",
          (status, t1["token"], Fake.jwt_ok[-1]), (200, f"ghs_install{minted + 1}", True))
    last = [c for c in Fake.calls if c[1].endswith("/access_tokens")][-1]
    check("...scoped to the one repo, with what the installation grants less "
          "administration", last[2],
          {"repositories": ["loam-ui"],
           "permissions": {"contents": "write", "metadata": "read", "workflows": "write"}})
    # The 2026-10-01 case: the app widened to Issues, Pull requests and Actions,
    # and the installation accepted it. The old fixed set never asked for them.
    Fake.permissions = {**Fake.permissions, "issues": "write",
                        "pull_requests": "write", "actions": "read"}
    status, _ = await request(server, "POST", f"{base}/token",
                              {"repository": "octo-owner/loam-ui"})
    last = [c for c in Fake.calls if c[1].endswith("/access_tokens")][-1]
    check("test_project_token_carries_the_installation: accepted permissions reach "
          "the token, administration never", (status, last[2]["permissions"]),
          (200, {"actions": "read", "contents": "write", "issues": "write",
                 "metadata": "read", "pull_requests": "write", "workflows": "write"}))
    Fake.permissions = {k: v for k, v in Fake.permissions.items()
                        if k not in ("issues", "pull_requests", "actions")}
    minted = Fake.minted
    _, t2 = await request(server, "POST", f"{base}/token",
                          {"repository": "octo-owner/loam-ui"})
    check("...never cached: the second ask mints again (a cached token outlived "
          "a deleted-and-recreated repo, 2026-10-01)", (t2["token"], Fake.minted),
          (f"ghs_install{minted + 1}", minted + 1))
    status, _ = await request(server, "POST", f"{base}/token",
                              {"repository": "octo-owner/orphan"})
    check("...a repo the installation does not reach: GitHub's refusal, no token",
          status != 200, True)
    check("...installation tokens never reach the store",
          "ghs_" in gh.STORE_PATH.read_text(), False)

    # -- test_add_to_installation -------------------------------------------
    gh.PROVE_WAIT_S = 0
    Fake.selection = "all"
    check("test_add_to_installation: on All repositories, covered, and proved by a "
          "token minted for it",
          (loam_github.add_to_installation({"id": 7001, "full_name": "octo-owner/loam-ui"}),
           [c[1] for c in Fake.calls if c[0] == "PUT"]),
          ("covered", []))
    try:
        loam_github.add_to_installation({"id": 7002, "full_name": "octo-owner/orphan"})
        said = "no error"
    except loam_github.GitHubError as exc:
        said = str(exc)
    check("...the 2026-10-01 case: All repositories in the store, but the repo is "
          "not reached -- an error, never a quiet no-op",
          "does not reach octo-owner/orphan" in said, True)
    Fake.selection = "selected"
    out = loam_github.add_to_installation({"id": 7001, "full_name": "octo-owner/fresh"})
    check("...the selection is read live: selected on GitHub, so the repo is added "
          "with the user token, then proved",
          (out, [c[1] for c in Fake.calls if c[0] == "PUT"], "fresh" in Fake.reaches),
          ("added", ["/user/installations/99/repositories/7001"], True))
    check("...and the store's stale selection is corrected",
          store()["installation"]["repository_selection"], "selected")
    Fake.selection = "all"

    # -- test_credential_helper_fill ----------------------------------------
    tcp = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    port = tcp.sockets[0].getsockname()[1]
    env = {**os.environ, **loam_github.helper_env("octo-owner/loam-ui"),
           "LOAM_BROKER_URL": f"http://127.0.0.1:{port}",
           "LOAM_BROKER_TOKEN_FILE": str(cfg.TOKEN_PATH), "HOME": str(root)}

    async def fill(path: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "git", "credential", "fill", env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate(
            f"protocol=https\nhost=github.com\npath={path}\n\n".encode())
        return proc.returncode, out.decode(), err.decode()

    code, out, err = await fill("octo-owner/loam-ui.git")
    check("test_credential_helper_fill: git credential fill answers",
          (code, "username=x-access-token" in out, "password=ghs_install" in out),
          (0, True, True))
    code, out, err = await fill("octo-owner/other.git")
    check("...another repository gets nothing, and git does not prompt",
          (code != 0, "password=" in out, "may reach octo-owner/loam-ui" in err),
          (True, False, True))
    repo = root / "checkout"
    repo.mkdir()
    proc = await asyncio.create_subprocess_exec(
        "sh", "-c", "git init -q && git remote add origin "
        "git@github.com:octo-owner/loam-ui.git && git remote get-url --push origin",
        cwd=repo, env=env, stdout=asyncio.subprocess.PIPE)
    url = (await proc.communicate())[0].decode().strip()
    check("...the ssh remote is pushed over https, no token in the URL",
          url, "https://github.com/octo-owner/loam-ui.git")
    tcp.close()
    await tcp.wait_closed()

    # -- test_nightly_refresh_near_expiry -----------------------------------
    vault = root / "vault"
    data = store()
    data["user"]["refresh_expires_at"] = iso(time.time() + 5 * 86400)
    data["user"]["token_expires_at"] = iso(time.time() + 6 * 3600)  # no refresh due
    gh.save(data)
    wrote = gh.daily_check(today="2026-10-01")
    import loam_check  # noqa: PLC0415 -- the check on a clock reads the store

    def told() -> str:
        """What the check on a clock holds false, read from the store
        without calling GitHub (lib/loam_check.py)."""
        return "\n".join(f["says"] for f in loam_check.github() if f["ok"] is False)
    comms = told()
    check("test_nightly_refresh_near_expiry: one warning",
          len(wrote), 1)
    check("...a false fact for the check, nothing on the board",
          "the GitHub authorization expires in" in comms
          and not (vault / "00_INBOX" / "from-agents" / "comms.md").exists(), True)
    check("...once a day: the next call of the date does nothing",
          gh.daily_check(today="2026-10-01"), None)

    # -- test_nightly_installation_gone -------------------------------------
    data = store()
    data["user"]["refresh_expires_at"] = iso(time.time() + 150 * 86400)
    gh.save(data)
    Fake.installation_gone = True
    wrote = gh.daily_check(today="2026-10-02")
    check("test_nightly_installation_gone: one warning, the installation",
          [("installation is gone" in w) for w in wrote], [True])
    check("...a false fact for the check", "installation is gone" in told(), True)
    status, sick = await request(server, "GET", base)
    SHAPES["unhealthy"] = sick
    check("...and the page sees status unhealthy",
          (sick["status"], sick["health"]["last_error"] is not None), ("unhealthy", True))
    Fake.installation_gone = False
    _, well = await request(server, "POST", f"{base}/refresh", {})
    check("...a refresh that finds it again clears it", well["status"], "authorized")

    # -- test_nightly_silent_when_healthy -----------------------------------
    check("test_nightly_silent_when_healthy",
          (gh.daily_check(today="2026-10-03"), told()), ([], ""))

    # -- test_accounts (2026-11-13): one account at a time -----------------
    original = store()
    data = store()
    data["added_installations"] = [{"installation_id": 555, "account": "side-org",
                                    "repository_selection": "selected",
                                    "installed_at": "2026-11-01T00:00:00Z"}]
    data["org_apps"] = {"acme-co": {
        "org": "acme-co",
        "app": {**data["app"], "slug": "loam-for-acme-co", "owner": "acme-co"},
        "installation": {"installation_id": 777, "account": "acme-co",
                         "repository_selection": "all",
                         "installed_at": "2026-11-02T00:00:00Z"},
        "user": dict(data["user"]), "health": {}}}
    gh.STORE_PATH.write_text(json.dumps(data))
    status, out = await request(server, "GET", f"{base}/accounts")
    SHAPES["accounts"] = out
    rows = {a["account"]: a for a in out["accounts"]}
    check("test_accounts: three accounts, personal first, then the other "
          "installation, then the org",
          (status, [(a["account"], a["kind"]) for a in out["accounts"]]),
          (200, [("octo-owner", "personal"), ("side-org", "installation"),
                 ("acme-co", "org")]))
    check("...disconnecting the personal account names what goes with it",
          rows["octo-owner"]["disconnect_also"], ["side-org"])
    check("...each authorized, with its installation and app",
          [(a["status"], a["installation"]["installation_id"], a["app"]["slug"])
           for a in out["accounts"]],
          [("authorized", 99, "loam"), ("authorized", 555, "loam"),
           ("authorized", 777, "loam-for-acme-co")])
    raw = json.dumps(out)
    check("...and no token, key or secret in the answer",
          any(x in raw for x in ("ghu_", "ghr_", "BEGIN RSA", '"secret"', "pem")), False)
    status, one = await request(server, "POST", f"{base}/accounts/octo-owner/refresh", {})
    check("refresh octo-owner: that account, healthy, its own check recorded",
          (status, one["account"]["account"], one["account"]["status"],
           bool(one["account"]["health"]["checked_at"])),
          (200, "octo-owner", "authorized", True))
    calls_before = len(Fake.calls)
    Fake.installation_gone = True
    status, one = await request(server, "POST", f"{base}/accounts/side-org/refresh", {})
    Fake.installation_gone = False
    asked = [c[1] for c in Fake.calls[calls_before:] if c[1].startswith("/app/installations/")]
    check("refresh side-org: only its installation is asked about",
          asked, ["/app/installations/555"])
    rows = {a["account"]: a for a in one["accounts"]}
    check("...it is unhealthy, saying its installation is gone",
          (one["account"]["status"], "side-org" in (one["account"]["health"]["last_error"] or "")),
          ("unhealthy", True))
    check("...and octo-owner and acme-co are untouched",
          (rows["octo-owner"]["status"], rows["acme-co"]["status"]),
          ("authorized", "authorized"))
    status, said = await request(server, "POST", f"{base}/accounts/nobody/refresh", {})
    check("refresh an account not connected: 404", status, 404)
    status, add = await request(server, "POST", f"{base}/accounts", {})
    check("add another: the Loam app's install screen",
          (status, add["step"], "/apps/loam/installations/new?state=" in add["url"]),
          (200, "install", True))
    status, add = await request(server, "POST", f"{base}/accounts",
                                {"org": "new-org", "own_app": True})
    check("add an org with its own app: the manifest to post on its create page",
          (status, add["step"], add["post_url"].split("?")[0]),
          (200, "register", f"{gh.WEB}/organizations/new-org/settings/apps/new"))
    status, gone = await request(server, "DELETE", f"{base}/accounts/side-org")
    after = store()
    check("disconnect side-org: it alone goes; the app, octo-owner and acme-co stay",
          (status, gone["forgot"], after.get("added_installations"),
           after["installation"]["account"], sorted(after["org_apps"])),
          (200, ["side-org"], [], "octo-owner", ["acme-co"]))
    status, gone = await request(server, "DELETE", f"{base}/accounts/acme-co")
    after = store()
    check("disconnect acme-co: its app goes, the Loam app stays",
          (status, gone["kind"], after.get("org_apps"), after["app"]["slug"],
           gone["uninstall_url"]),
          (200, "org", {}, "loam",
           f"{gh.WEB}/organizations/acme-co/settings/installations"))
    gh.STORE_PATH.write_text(json.dumps(data))
    status, gone = await request(server, "DELETE", f"{base}/accounts/octo-owner")
    after = store()
    check("disconnect octo-owner: the Loam app and its installations go, the org's "
          "app stays",
          (status, gone["forgot"], "app" in after, "added_installations" in after,
           sorted(after["org_apps"]), [a["account"] for a in gone["accounts"]]),
          (200, ["octo-owner", "side-org"], False, False, ["acme-co"], ["acme-co"]))
    status, gone = await request(server, "DELETE", f"{base}/accounts/acme-co")
    check("disconnecting the last account removes the store",
          (status, gh.STORE_PATH.exists(), gone["accounts"]), (200, False, []))
    gh.STORE_PATH.write_text(json.dumps(original))

    # -- test_forget_one_org ------------------------------------------------
    # DELETE with `org` forgets that organization's app and nothing else.
    data = store()
    data["org_apps"] = {
        "acme-co": {"app": {"slug": "loam-for-acme-co", "app_id": 7}},
        "other-org": {"app": {"slug": "loam-for-other-org", "app_id": 8}}}
    gh.STORE_PATH.write_text(json.dumps(data))
    status, gone = await request(server, "DELETE", base, {"org": "acme-co"})
    after = store()
    check("test_forget_one_org: only that org's app is gone",
          (status, gone["org"], sorted(after["org_apps"]),
           after["installation"] == data["installation"], after["app"] == data["app"]),
          (200, "acme-co", ["other-org"], True, True))
    check("...answering with its uninstall and delete pages",
          (gone["uninstall_url"], gone["delete_app_url"]),
          (f"{gh.WEB}/organizations/acme-co/settings/installations",
           f"{gh.WEB}/organizations/acme-co/settings/apps/loam-for-acme-co/advanced"))
    status, gone = await request(server, "DELETE", f"{base}?org=other-org")
    check("...the org in the query works too",
          (status, gone.get("org"), store()["org_apps"]), (200, "other-org", {}))
    status, said = await request(server, "DELETE", base, {"org": "acme-co"})
    check("...an org with no app here is a 404, and the rest is kept",
          (status, gh.STORE_PATH.exists(), store()["app"] == data["app"]),
          (404, True, True))

    # -- test_forget ---------------------------------------------------------
    status, gone = await request(server, "DELETE", base)
    check("test_forget: forgets locally and names where to uninstall and revoke",
          (status, gone["forgotten"], bool(gone["uninstall_url"]),
           bool(gone["revoke_url"]), gh.STORE_PATH.exists()),
          (200, True, True, True, False))
    _, view = await request(server, "GET", base)
    check("...and the state is unregistered again", view["status"], "unregistered")
    check("...helper_env is empty when nothing is installed",
          loam_github.helper_env("octo-owner/loam-ui"), {})
    status, _ = await request(server, "POST", f"{base}/token",
                              {"repository": "octo-owner/loam-ui"})
    check("...and a token is refused with 409", status, 409)


root = Path(tempfile.mkdtemp(prefix="loam-github-app-"))
httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
fake_url = f"http://127.0.0.1:{httpd.server_address[1]}"
threading.Thread(target=httpd.serve_forever, daemon=True).start()
saved_cfg = {n: getattr(cfg, n) for n in
             ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "USAGE_PATH", "AI_LABS")}
saved_gh = {n: getattr(gh, n) for n in
            ("STORE_DIR", "STORE_PATH", "LOCK_PATH", "CHECKED_PATH", "API", "WEB")}
try:
    cfg.STATE_DIR = root / "broker"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.USAGE_PATH = cfg.STATE_DIR / "provider-usage.json"
    cfg.AI_LABS = root / "agents"
    cfg.AI_LABS.mkdir()
    gh.STORE_DIR = root / "github"
    gh.STORE_PATH = gh.STORE_DIR / "integration.json"
    gh.LOCK_PATH = gh.STORE_DIR / "integration.lock"
    gh.CHECKED_PATH = gh.STORE_DIR / "checked-on"
    gh.API = gh.WEB = fake_url
    os.environ.pop("GITHUB_TOKEN", None)
    asyncio.run(main(root, fake_url))
finally:
    for n, v in saved_cfg.items():
        setattr(cfg, n, v)
    for n, v in saved_gh.items():
        setattr(gh, n, v)
    httpd.shutdown()
    shutil.rmtree(root, ignore_errors=True)

if "--shapes" in sys.argv:
    print(json.dumps(SHAPES, indent=2))
print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
