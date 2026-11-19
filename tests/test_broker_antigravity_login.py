#!/usr/bin/env python3
"""Check provider sign-in metadata, the Antigravity login flow and cancellation."""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
os.environ["LOAM_USAGE_READS"] = "off"
import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_antigravity as agy  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


# The real URL's shape, with its PKCE and state values replaced.
REAL_URL = ("https://accounts.google.com/o/oauth2/auth?access_type=offline"
            "&client_id=1071006060591-x.apps.googleusercontent.com"
            "&code_challenge=abc&code_challenge_method=S256&prompt=consent"
            "&redirect_uri=https%3A%2F%2Fantigravity.google%2Foauth-callback"
            "&response_type=code&scope=openid&state=xyz")

# What `agy acp --login` and `agy models` print, verbatim but for the URL.
STANDIN = f'''#!/usr/bin/env python3
import os, sys
token = os.environ["AGY_FAKE_TOKEN"]
if sys.argv[1:] == ["models"]:
    if os.path.exists(token):
        print("gemini-4-pro   Gemini 4 Pro"); sys.exit(0)
    print("Please sign in"); sys.exit(1)
assert sys.argv[1:] == ["acp", "--login"], sys.argv
print("Open this link in a browser to log in with Google:\\n", flush=True)
print("  {REAL_URL}\\n", flush=True)
print("Then paste the authorization code, or the full URL of the page you "
      "land on, and press Enter:", flush=True)
line = sys.stdin.readline().strip()
if not line:
    print("login cancelled: no authorization code was entered"); sys.exit(1)
if os.environ["AGY_FAKE_GOOD"] in line:
    open(token, "w").write("not-a-real-token")
    print("Logged in as someone@example.com."); sys.exit(0)
print('couldn\\'t log in: token exchange failed: oauth2: "invalid_grant" '
      '"Bad Request"'); sys.exit(1)
'''

LOGIN = "/v1/providers/antigravity/login"
CODE = LOGIN + "/code"
LOGOUT = "/v1/providers/antigravity/logout"


def test_rows(server) -> None:
    rows = {p["id"]: p for p in server.providers()}
    check("antigravity: paste-code, with every route",
          rows["antigravity"]["sign_in"],
          {"kind": "paste-code", "label": "Sign in with Google",
           "account": None, "start": LOGIN, "status": LOGIN,
           "cancel": LOGIN, "code": CODE, "sign_out": LOGOUT})
    check("claude-code: paste-code, with a sign-out",
          (rows["claude-code"]["sign_in"]["kind"],
           rows["claude-code"]["sign_in"]["code"],
           rows["claude-code"]["sign_in"]["sign_out"]),
          ("paste-code", "/v1/providers/claude-code/login/code",
           "/v1/providers/claude-code/logout"))
    check("codex: device-code, nothing to paste",
          (rows["codex"]["sign_in"]["kind"], rows["codex"]["sign_in"]["code"]),
          ("device-code", None))
    check("an unwired row: sign_in null", rows["grok"]["sign_in"], None)
    check("antigravity login carries account",
          "account" in rows["antigravity"]["login"], True)


async def test_flow(tmp: Path, server) -> None:
    check("idle before anything",
          (await route(server, "GET", LOGIN))[1]["state"], "idle")

    status, body = await route(server, "POST", LOGIN)
    check("start answers 201", status, 201)
    check("...pending", body["state"], "pending")
    check("...with the Google link", body["url"], REAL_URL)
    check("...kind paste-code, and a code is accepted",
          (body["kind"], body["accepts_code"], body["code"]),
          ("paste-code", True, None))
    check("...and an expiry", body["expires_at"] is not None, True)
    check("...not signed in yet", body["login"]["source"], "none")

    status, again = await route(server, "POST", LOGIN)
    check("a second POST is the same flow", (status, again["url"],
          again["started_at"]), (200, body["url"], body["started_at"]))

    status, _ = await route(server, "POST", CODE, {"code": " "})
    check("a blank code is refused", status, 400)

    status, body = await route(server, "POST", CODE,
                               {"code": "4/0Afake-code"})
    check("a fake code: 200 with the failure", status, 200)
    check("...failed", body["state"], "failed")
    check("...in the CLI's words", body["message"],
          'couldn\'t log in: token exchange failed: oauth2: "invalid_grant" '
          '"Bad Request"')
    check("...the code is not in the answer",
          "4/0Afake-code" in json.dumps(body), False)
    check("...no token written", agy.TOKEN_FILE.exists(), False)

    status, _ = await route(server, "POST", CODE, {"code": "x"})
    check("nothing waits for a code now", status, 409)

    await route(server, "POST", LOGIN)
    status, body = await route(server, "DELETE", LOGIN)
    check("DELETE calls it off", (body["state"], body["url"]), ("idle", None))
    check("...and leaves no process", server.broker.antigravity_login.proc, None)

    await route(server, "POST", LOGIN)
    status, body = await route(
        server, "POST", CODE,
        {"code": "https://antigravity.google/oauth-callback?code=good-one"})
    check("the right code (as the landing URL): done", body["state"], "done")
    check("...signed in as", (body["login"]["source"], body["login"]["account"]),
          ("google-account", "someone@example.com"))
    rows = {p["id"]: p for p in server.providers()}
    check("the row says so too",
          (rows["antigravity"]["connected"],
           rows["antigravity"]["login"]["account"]),
          (True, "someone@example.com"))
    check("...and so does sign_in.account",
          rows["antigravity"]["sign_in"]["account"], "someone@example.com")

    status, body = await route(server, "POST", LOGOUT)
    check("sign out: 200, removed", (status, body["removed"]), (200, True))
    check("...the token file is gone", agy.TOKEN_FILE.exists(), False)
    check("...and the row is signed out",
          (body["login"]["source"], body["login"]["account"]), ("none", None))
    status, _ = await route(server, "POST", "/v1/providers/grok/logout")
    check("an unwired provider: 404", status, 404)
    status, _ = await route(server, "POST", "/v1/providers/codex/login/code",
                            {"code": "x"})
    check("codex still takes no pasted code", status, 405)


class FakeWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None

    def body(self):
        head, _, payload = b"".join(self.chunks).decode().partition("\r\n\r\n")
        return int(head.split()[1]), json.loads(payload)


async def route(server, method, path, payload=None):
    writer = FakeWriter()
    try:
        await server.route(method, path, {}, payload or {}, writer)
    except broker_mod.BrokerError as exc:
        return exc.status, {"error": exc.message}
    return writer.body()


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="broker-agy-login-test-"))
    keep = (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH,
            agy.AGY, agy.TOKEN_FILE, agy.ACCOUNT_FILE, agy._login_cache,
            agy.LOGIN_SUBMIT_TIMEOUT_S)
    try:
        cfg.STATE_DIR = tmp
        cfg.SESSION_DIR = tmp / "sessions"
        cfg.USAGE_PATH = tmp / "provider-usage.json"
        cfg.TOKEN_PATH = tmp / "token"
        standin = tmp / "agy"
        standin.write_text(STANDIN, encoding="utf-8")
        standin.chmod(0o755)
        agy.AGY = str(standin)
        agy.TOKEN_FILE = tmp / "antigravity-oauth-token"
        agy.ACCOUNT_FILE = tmp / "antigravity" / "account.json"
        agy._login_cache = None
        agy.LOGIN_SUBMIT_TIMEOUT_S = 10
        os.environ["AGY_FAKE_TOKEN"] = str(agy.TOKEN_FILE)
        os.environ["AGY_FAKE_GOOD"] = "good-one"
        server = broker_mod.Server(broker_mod.Broker())
        test_rows(server)
        await test_flow(tmp, server)
    finally:
        (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH,
         agy.AGY, agy.TOKEN_FILE, agy.ACCOUNT_FILE, agy._login_cache,
         agy.LOGIN_SUBMIT_TIMEOUT_S) = keep
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
