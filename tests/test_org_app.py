#!/usr/bin/env python3
"""Route GitHub requests and credentials to the correct account or organization using
two loopback GitHub App fixtures.

Run: python3 tests/test_org_app.py
"""

from __future__ import annotations

import base64
import copy
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_engineer as eng  # noqa: E402
import loam_github  # noqa: E402
import loam_github_app as gh  # noqa: E402


def load_script(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


prov = load_script("provision_org", AGENTS / "bin" / "provision")

# Render the shared role using matching substitution tables.
_shipped = prov.ENGINEER_SHIPPED / "role.md"
if not prov.ENGINEER_ROLE_SRC.is_file() or (_shipped.is_file() and prov.ENGINEER_ROLE_SRC.read_text(
        encoding="utf-8") == _shipped.read_text(encoding="utf-8")):
    print(f"SKIP  no shared engineer role a project engineer renders from "
          f"({prov.ENGINEER_ROLE_SRC})")
    sys.exit(0)
cli = load_script("github_app_cli", AGENTS / "bin" / "github-app")

results: list[bool] = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:70} got={got!r}"[:240])
    if not ok:
        print(f"      wanted {want!r}")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True, text=True, check=True).stdout.strip()


def pem(key) -> str:
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.TraditionalOpenSSL,
                             serialization.NoEncryption()).decode()


# ---------------------------------------------------------------------------
# A fake GitHub with two apps.
# ---------------------------------------------------------------------------

APPS = {
    "Iv23liFIRST": {"key": rsa.generate_private_key(public_exponent=65537, key_size=2048),
                    "slug": "my-loam-app", "owner": "octo-owner"},
    "Iv23liACME": {"key": rsa.generate_private_key(public_exponent=65537, key_size=2048),
                   "slug": "loam-for-acme-co", "owner": "acme-co"},
}


class Fake:
    calls: list[tuple[str, str, dict, str | None]] = []
    # installation id -> (owning app's client id, account)
    installs = {99: ("Iv23liFIRST", "octo-owner"),
                555: ("Iv23liFIRST", "acme-co"),       # what the first app must never use
                888: ("Iv23liACME", "acme-co"),
                889: ("Iv23liACME", "octo-owner")}  # an org app on the wrong account
    login = "octo-owner"
    tokens: dict[str, str] = {}       # user token -> client id
    pair = 0
    minted = 0
    conversion_owner = "acme-co"
    permissions = {"contents": "write", "metadata": "read", "pull_requests": "write",
                   "issues": "write", "actions": "read", "checks": "read"}
    repos = {"acme-co/acme-platform": {"default_branch": "main"},
             "acme-co/tracked": {"default_branch": "main"}}


def unb64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def jwt_app(header: str) -> str | None:
    """The client id whose key signed this JWT, or None."""
    try:
        head, claims, sig = header[7:].split(".")
        iss = json.loads(unb64(claims))["iss"]
        APPS[iss]["key"].public_key().verify(unb64(sig), f"{head}.{claims}".encode(),
                                             padding.PKCS1v15(), hashes.SHA256())
        return iss
    except Exception:  # noqa: BLE001
        return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def answer(self, status: int, body) -> None:
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
        auth = self.headers.get("Authorization", "")
        signer = jwt_app(auth)
        Fake.calls.append(("POST", self.path, body, signer))
        parts = self.path.strip("/").split("/")
        if parts[:1] == ["app-manifests"] and parts[2:] == ["conversions"]:
            cid = "Iv23liACME"
            return self.answer(201, {"id": 2002, "slug": APPS[cid]["slug"],
                                     "owner": {"login": Fake.conversion_owner},
                                     "client_id": cid, "client_secret": "acme-secret",
                                     "pem": pem(APPS[cid]["key"]),
                                     "html_url": f"https://github.com/apps/{APPS[cid]['slug']}"})
        if self.path == "/login/oauth/access_token":
            cid = body.get("client_id")
            Fake.pair += 1
            tok = f"ghu_{cid}_{Fake.pair}"
            Fake.tokens[tok] = cid
            return self.answer(200, {"access_token": tok, "expires_in": 28800,
                                     "refresh_token": f"ghr_{Fake.pair}",
                                     "refresh_token_expires_in": 15897600})
        if parts[:2] == ["app", "installations"] and parts[3:] == ["access_tokens"]:
            inst = int(parts[2])
            owner_app, account = Fake.installs[inst]
            if signer != owner_app:
                return self.answer(401, {"message": "this app does not own the installation"})
            for name in body.get("repositories") or []:
                if f"{account}/{name}" not in Fake.repos and account != "octo-owner":
                    return self.answer(422, {"message": "not accessible"})
            Fake.minted += 1
            return self.answer(201, {"token": f"ghs_{inst}_{Fake.minted}",
                                     "expires_at": time.strftime(
                                         "%Y-%m-%dT%H:%M:%SZ",
                                         time.gmtime(time.time() + 3600))})
        return self.answer(404, {"message": "Not Found"})

    def do_GET(self):  # noqa: N802
        auth = self.headers.get("Authorization", "")
        signer = jwt_app(auth)
        Fake.calls.append(("GET", self.path, {}, signer))
        path = self.path.partition("?")[0]
        parts = path.strip("/").split("/")
        if parts[:2] == ["app", "installations"] and len(parts) == 3:
            inst = int(parts[2])
            if inst not in Fake.installs or Fake.installs[inst][0] != signer:
                return self.answer(404, {"message": "Not Found"})
            return self.answer(200, {"id": inst,
                                     "account": {"login": Fake.installs[inst][1]},
                                     "repository_selection": "selected",
                                     "permissions": dict(Fake.permissions)})
        if path == "/user":
            return self.answer(200, {"login": Fake.login})
        if path == "/user/installations":
            cid = Fake.tokens.get(auth[7:])
            if cid is None:
                return self.answer(401, {"message": "Bad credentials"})
            mine = [i for i, (owner, _) in Fake.installs.items() if owner == cid]
            return self.answer(200, {"total_count": len(mine),
                                     "installations": [{"id": i} for i in mine]})
        if parts[:1] == ["repos"] and len(parts) == 3:
            repo = f"{parts[1]}/{parts[2]}"
            if repo not in Fake.repos:
                return self.answer(404, {"message": "Not Found"})
            return self.answer(200, {"full_name": repo, "name": parts[2], "private": True,
                                     "visibility": "private",
                                     "html_url": f"https://github.com/{repo}",
                                     "default_branch": Fake.repos[repo]["default_branch"]})
        return self.answer(404, {"message": "Not Found"})


def raised(fn, *args, **kwargs) -> str | None:
    try:
        fn(*args, **kwargs)
        return None
    except (gh.IntegrationError, SystemExit) as exc:
        return str(exc) or "exit"


def mints(inst: int) -> list[str | None]:
    """Who signed each mint asked of installation ``inst``."""
    return [c[3] for c in Fake.calls if c[1] == f"/app/installations/{inst}/access_tokens"]


# ---------------------------------------------------------------------------

root = Path(tempfile.mkdtemp(prefix="loam-org-app-test-"))
httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
fake_url = f"http://127.0.0.1:{httpd.server_address[1]}"
threading.Thread(target=httpd.serve_forever, daemon=True).start()
saved_gh = {n: getattr(gh, n) for n in ("STORE_DIR", "STORE_PATH", "LOCK_PATH", "API", "WEB",
                                        "app_url")}
saved_eng = {n: getattr(eng, n) for n in ("PROJECTS_DIR", "PROJECT_STATE", "PROJECT_CHECKOUTS")}
saved_other = (prov.CLAUDE_JSON, loam_github.https_remote, cli.broker_knows_org_apps, prov.vault_folder_for)
try:
    gh.STORE_DIR = root / "github"
    gh.STORE_PATH = gh.STORE_DIR / "integration.json"
    gh.LOCK_PATH = gh.STORE_DIR / "integration.lock"
    gh.API = gh.WEB = fake_url
    gh.app_url = lambda: "https://loam-ui.example.test"
    eng.PROJECTS_DIR = root / "projects"
    eng.PROJECT_STATE = root / "project-state"
    eng.PROJECT_CHECKOUTS = root / "checkouts"
    prov.CLAUDE_JSON = root / "claude.json"
    prov.CLAUDE_JSON.write_text("{}")
    # A vault of our own: the real one has a `Acme/` folder, which a project
    # named `acme` would be refused as the case-only twin of.
    real_vault_folder_for = prov.vault_folder_for
    prov.vault_folder_for = lambda name, given, vault=None: real_vault_folder_for(
        name, given, root / "vault")

    # The box's store today: one app, one installation, the owner authorized.
    first_user = "ghu_Iv23liFIRST_0"
    Fake.tokens[first_user] = "Iv23liFIRST"
    gh.save({"app": {"id": 1, "slug": "my-loam-app", "client_id": "Iv23liFIRST",
                     "client_secret": "secret", "pem": pem(APPS["Iv23liFIRST"]["key"])},
             "installation": {"installation_id": 99, "account": "octo-owner",
                              "repository_selection": "all"},
             "user": {"login": "octo-owner", "token": first_user,
                      "token_expires_at": "2099-01-01T00:00:00Z",
                      "refresh_token": "ghr_0"},
             "health": {}})

    # == store ==============================================================
    view = gh.public_view()
    check("test_store_without_org_apps_unchanged: no migration, same answer",
          (view["status"], view["installed"]["installation_id"], view["org_apps"]),
          ("authorized", 99, []))
    tok = gh.installation_token("octo-owner/loam-ui")
    check("...octo-owner/* still from the first app's installation",
          (tok["token"].startswith("ghs_99_"), mints(99)[-1]), (True, "Iv23liFIRST"))
    first_part = {k: copy.deepcopy(v) for k, v in gh.load().items() if k != "states"}

    # == manifest ===========================================================
    started = gh.start_manifest(None, "acme-co")
    man = started["manifest"]
    check("test_org_manifest_posts_to_the_org_create_page",
          started["post_url"].split("?")[0], f"{fake_url}/organizations/acme-co/settings/apps/new")
    check("test_org_manifest_private_with_org_permissions_no_administration",
          (man["public"], man["default_permissions"], "administration" in man["default_permissions"],
           man["request_oauth_on_install"]),
          (False, gh.ORG_PERMISSIONS, False, True))
    check("...the way back is loam-ui's, as for the first app",
          (man["redirect_url"], man["setup_url"]),
          ("https://loam-ui.example.test/integrations/github/registered",
           "https://loam-ui.example.test/integrations/github/installed"))
    entry = gh.load()["states"][started["state"]]
    check("test_org_state_refused_by_the_old_code: its purpose is not `register`",
          (entry["purpose"], entry["org"]), ("register-org", "acme-co"))
    check("...and an org login that is not one is refused",
          "not a GitHub organization login" in (raised(gh.start_manifest, None, "../x") or ""),
          True)

    Fake.conversion_owner = "octo-owner"
    said = raised(gh.register, "code-1", gh.start_manifest(None, "acme-co")["state"])
    Fake.conversion_owner = "acme-co"
    check("test_org_app_owned_elsewhere_records_nothing",
          ("owned by octo-owner, not acme-co" in (said or ""), gh.load().get("org_apps")),
          (True, None))

    view = gh.register("code-2", started["state"])
    store = gh.load()
    check("test_register_org_app_into_its_own_slot",
          (store["org_apps"]["acme-co"]["app"]["slug"], store["org_apps"]["acme-co"]["org"],
           [o["org"] for o in view["org_apps"]], view["org_apps"][0]["status"]),
          ("loam-for-acme-co", "acme-co", ["acme-co"], "registered"))
    check("test_first_app_untouched_by_org_register: app, installation, user, health",
          {k: v for k, v in store.items() if k not in ("states", "org_apps")}, first_part)
    check("...the register answer offers the org app's install page",
          view["install"]["url"].split("?")[0],
          f"{fake_url}/apps/loam-for-acme-co/installations/new")
    check("...and no secret is in the public view",
          any(s in json.dumps(gh.public_view()) for s in ("acme-secret", "PRIVATE KEY")), False)

    # == install ============================================================
    said = raised(gh.callback, "oauth", 888, "install", None)
    check("test_org_install_needs_our_state", "not one this box issued" in (said or ""), True)
    said = raised(gh.callback, "oauth", 888, "install", gh.start_install()["state"])
    check("...a state for the first app does not install the org's",
          "not one" in (said or "") or "401" in (said or "") or "404" in (said or ""), True)
    check("...and recorded nothing for the org",
          gh.load()["org_apps"]["acme-co"].get("installation"), None)
    said = raised(gh.callback, "oauth", 889, "install", gh.start_install("acme-co")["state"])
    check("test_org_app_on_another_account_records_nothing",
          ("is acme-co's app, and this installation is on octo-owner" in (said or ""),
           gh.load()["org_apps"]["acme-co"].get("installation")), (True, None))
    Fake.login = "someone-else"
    said = raised(gh.callback, "oauth", 888, "install", gh.start_install("acme-co")["state"])
    Fake.login = "octo-owner"
    check("test_org_install_by_another_user_refused",
          ("authorized by someone-else" in (said or ""),
           gh.load()["org_apps"]["acme-co"].get("user")), (True, None))

    view = gh.callback("oauth", 888, "install", gh.start_install("acme-co")["state"])
    store = gh.load()
    own = store["org_apps"]["acme-co"]
    check("test_org_install_recorded_on_the_org_with_its_own_user",
          (own["installation"]["installation_id"], own["installation"]["account"],
           own["user"]["login"], Fake.tokens[own["user"]["token"]], view["org_apps"][0]["status"]),
          (888, "acme-co", "octo-owner", "Iv23liACME", "authorized"))
    check("...verified against the org app's own /user/installations",
          [c for c in Fake.calls if c[1].startswith("/user/installations")] != [], True)
    check("test_first_app_untouched_by_org_install",
          {k: v for k, v in store.items() if k not in ("states", "org_apps")}, first_part)
    check("...GitHub's update redirect for the org's installation goes to the org app",
          (raised(gh.callback, None, 888, "update", None),
           gh.load()["org_apps"]["acme-co"]["installation"]["installation_id"],
           gh.load()["installation"]["installation_id"]), (None, 888, 99))

    # == routing ============================================================
    # The worst case: the first app also holds an installation on acme-co.
    with gh.locked():
        data = gh.load()
        data["added_installations"] = [{"installation_id": 555, "account": "acme-co",
                                        "repository_selection": "selected"}]
        gh.save(data)
    n555, n888, n99 = len(mints(555)), len(mints(888)), len(mints(99))
    tok = gh.installation_token("acme-co/acme-platform")
    gh.read_token("acme-co/acme-platform")
    check("test_acme_token_never_from_first_app: acme-co/* from 888, signed by Acme's key",
          (tok["token"].startswith("ghs_888_"), set(mints(888)[n888:]),
           len(mints(555)) - n555, len(mints(99)) - n99),
          (True, {"Iv23liACME"}, 0, 0))
    tok = gh.installation_token("octo-owner/loam-ui")
    check("test_personal_token_never_from_acme_app: octo-owner/* from 99, first key",
          (tok["token"].startswith("ghs_99_"), mints(99)[-1], len(mints(888)) - n888 - 2,
           mints(889)), (True, "Iv23liFIRST", 0, []))
    check("...route names the slot each comes from",
          (gh.route(gh.load(), "acme-co/x")[0].get("org"),
           gh.route(gh.load(), "octo-owner/x")[0].get("org")), ("acme-co", None))
    with gh.locked():
        data = gh.load()
        held = data["org_apps"]["acme-co"].pop("installation")
        gh.save(data)
    said = raised(gh.installation_token, "acme-co/acme-platform")
    check("test_org_app_uninstalled_never_falls_back_to_first_app",
          ("comes from that app or not at all" in (said or ""), len(mints(555)) - n555), (True, 0))
    with gh.locked():
        data = gh.load()
        data["org_apps"]["acme-co"]["installation"] = held
        data.pop("added_installations")
        gh.save(data)

    # == the nightly check, per app ========================================
    Fake.installs.pop(888)
    warnings = gh.nightly_warnings()
    view = gh.public_view()
    Fake.installs[888] = ("Iv23liACME", "acme-co")
    check("test_org_app_health_is_its_own: gone on GitHub, first app still healthy",
          (view["status"], view["org_apps"][0]["status"],
           "acme-co's app's installation is gone" in (view["org_apps"][0]["health"]["last_error"]
                                                     or "")),
          ("authorized", "unhealthy", True))
    check("...and the nightly line names it",
          [w for w in warnings if "installation is gone" in w],
          ["GitHub says acme-co's app's installation is gone (on acme-co); "
           "install it again from the app"])
    gh.check()
    check("...healthy again once GitHub has it", gh.public_view()["org_apps"][0]["status"],
          "authorized")

    # == adoption ===========================================================
    def remote_for(name: str, extra: dict | None = None) -> Path:
        bare = root / "remotes" / f"{name}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        seed = root / "seeds" / name
        subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
        (seed / "app.py").write_text("print('acme')\n")
        for rel, text in (extra or {}).items():
            (seed / rel).parent.mkdir(parents=True, exist_ok=True)
            (seed / rel).write_text(text)
        git(seed, "add", "-A")
        git(seed, "commit", "-qm", "theirs")
        git(seed, "push", "-q", str(bare), "main")
        return bare

    remotes = {"acme-co/acme-platform": remote_for("acme-platform"),
               "acme-co/tracked": remote_for("tracked", {".claude/settings.json": "{}\n"})}
    loam_github.https_remote = lambda repo: str(remotes[repo])
    theirs = git(remotes["acme-co/acme-platform"], "for-each-ref")
    n888 = len(mints(888))
    prov.adopt_project("acme", "acme-co/acme-platform", None, None)
    record = json.loads(eng.project_record_path("acme").read_text())
    check("test_adopt_acme_through_the_org_app",
          (record["repo"], record["landing"], record["default_branch"],
           set(mints(888)[n888:]), git(remotes["acme-co/acme-platform"], "for-each-ref") == theirs),
          ("acme-co/acme-platform", "pull_request", "main", {"Iv23liACME"}, True))
    check("...cloned with a read-only token from 888",
          [c[2].get("permissions") for c in Fake.calls
           if c[1] == "/app/installations/888/access_tokens"][-1],
          {"contents": "read", "metadata": "read"})
    gh.installation_token("acme-co/acme-platform")
    tok_perms = [c[2]["permissions"] for c in Fake.calls
                 if c[1] == "/app/installations/888/access_tokens"][-1]
    check("test_acme_job_token_read_only: contents read, the rest of the org app's grant",
          tok_perms, {"actions": "read", "checks": "read", "contents": "read",
                      "issues": "write", "metadata": "read", "pull_requests": "write"})
    said = raised(prov.adopt_project, "acme-tracked", "acme-co/tracked", None, None)
    check("test_adopt_still_refuses_a_tracked_harness_on_an_org_app",
          ((eng.PROJECT_CHECKOUTS / "acme-tracked").exists(),
           eng.project_record_path("acme-tracked").exists(), said is not None),
          (False, False, True))
    check("...and the name `Acme` is refused: project names are lowercase",
          raised(prov.adopt_project, "Acme", "acme-co/acme-platform", None, None) is not None,
          True)

    # == the CLI ============================================================
    cli.broker_knows_org_apps = lambda: "the running broker is on the code from before org apps"
    states_before = set(gh.load().get("states") or {})
    check("test_cli_refuses_before_the_broker_restart: no state minted",
          (cli.main(["org-manifest", "acme-co"]), set(gh.load().get("states") or {})),
          (1, states_before))
    cli.broker_knows_org_apps = lambda: None
    url, started = cli.manifest_page("acme-co", None)
    page = urllib.parse.unquote(url.partition(",")[2])
    check("test_cli_manifest_page_posts_the_manifest_to_the_org",
          (url.startswith("data:text/html"), f'action="{started["post_url"]}"'.replace(
              "&", "&amp;") in page, "name=manifest" in page,
           "&quot;administration&quot;" in page),
          (True, True, True, False))
    check("...and the state it carries is the org's",
          gh.load()["states"][started["state"]]["purpose"], "register-org")

    # An installation may await a subsequent user action.
    page_dir = root / "page"
    started = cli.write_page("acme-co", None, cli.duration("48h"), page_dir)
    html_page = (page_dir / "index.html").read_text()
    check("test_cli_page_posts_itself_to_the_org_create_page",
          (f'action="{started["post_url"]}"'.replace("&", "&amp;") in html_page,
           "name=manifest" in html_page, ".submit()" in html_page,
           oct((page_dir / "index.html").stat().st_mode & 0o777)),
          (True, True, True, "0o644"))
    entry = gh.load()["states"][started["state"]]
    check("...its state keeps the lifetime it was minted with",
          (entry["purpose"], entry["ttl"]), ("register-org", 172800))
    real_now = gh.now
    try:
        gh.now = lambda: real_now() + 25 * 3600
        short = gh.start_manifest(None, "acme-co")  # pruning runs; the long one stays
        check("...a day later it is still held, beside a fresh hour's state",
              (started["state"] in gh.load()["states"],
               short["state"] in gh.load()["states"]), (True, True))
        gh.now = lambda: real_now() + 27 * 3600
        gh.start_manifest(None, "acme-co")
        check("...an hour's state is pruned after its hour",
              short["state"] in gh.load()["states"], False)
        gh.now = lambda: real_now() + 49 * 3600
        said = raised(gh.register, "code-x", started["state"])
        check("...and the long one is refused once its own time is up",
              "expired" in (said or ""), True)
    finally:
        gh.now = real_now
    check("...a lifetime past a week is refused",
          "a week" in (raised(gh.start_manifest, None, "acme-co", 8 * 86400) or ""), True)
    check("...and durations read as hours or days",
          (cli.duration("48h"), cli.duration("2d"), raised(cli.duration, "soon") is not None),
          (172800, 172800, True))

    # == org-watch: the note once the org's app is installed ================
    note = root / "notes" / "installed.md"
    check("test_org_watch_writes_the_note_once_installed",
          cli.watch("acme-co", note, 0, None, None), 0)
    text = note.read_text()
    check("...naming the app and its installation as GitHub answers, no secret",
          ("is installed" in text, "`loam-for-acme-co`" in text or "installation 888" in text,
           any(x in text for x in ("acme-secret", "PRIVATE KEY", "ghs_", "ghu_"))),
          (True, True, False))
    late = root / "notes" / "late.md"
    cli.watch("nobody-org", late, 0, None, None)
    check("test_org_watch_says_when_the_wait_ran_out",
          "the wait ran out" in late.read_text() and "not registered" in late.read_text(), True)

    # == forgetting the org's app ==========================================
    out = gh.forget("acme-co")
    check("test_forget_org_app_leaves_the_first_app",
          (gh.load().get("org_apps"), gh.load()["installation"]["installation_id"],
           out["delete_app_url"].endswith("/organizations/acme-co/settings/apps/loam-for-acme-co/advanced")),
          ({}, 99, True))
finally:
    for n, v in saved_gh.items():
        setattr(gh, n, v)
    for n, v in saved_eng.items():
        setattr(eng, n, v)
    prov.CLAUDE_JSON, loam_github.https_remote, cli.broker_knows_org_apps, prov.vault_folder_for = saved_other
    httpd.shutdown()
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
