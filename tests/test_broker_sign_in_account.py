#!/usr/bin/env python3
"""The sign-in card's words and whose login it is, and signing out of
Claude Code and Codex from the app (2026-11-11).

  1. Every wired row's `sign_in` carries `label` (the button's words) and
     `account` (the row's `login.account` while connected, else null).
  2. Claude Code's account is `oauthAccount.emailAddress` out of the CLI's
     settings file, named only when the sessions run on the login file and
     it is connected -- never under a token in the environment.
  3. `POST /v1/providers/claude-code/logout` and `.../codex/logout` run the
     CLI's own logout and answer `{removed, login}`; a failure carries the
     CLI's words; any provider's sign-out is a 409 while one of its chats is
     mid-turn or asking.

**No real login is read or removed.** `claude` and `codex` are stand-ins
that delete a file in a temp directory, and every path the broker reads a
login from points there for the whole run.

Run: python3 tests/test_broker_sign_in_account.py
"""

import asyncio
import base64
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
for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
    os.environ.pop(name, None)
import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_antigravity as agy  # noqa: E402
import loam_codex as codex  # noqa: E402

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


# `claude auth logout` as 2.1.322 does it: the login file goes, and the
# account leaves the settings file. FAKE_FAIL makes it refuse.
CLAUDE_STANDIN = '''#!/usr/bin/env python3
import json, os, sys
assert sys.argv[1:] == ["auth", "logout"], sys.argv
if os.environ.get("FAKE_FAIL"):
    print("Failed to log out: something went wrong", file=sys.stderr)
    sys.exit(1)
try:
    os.unlink(os.environ["FAKE_CRED"])
except FileNotFoundError:
    pass
cfg = os.environ["FAKE_CONFIG"]
data = json.load(open(cfg))
data.pop("oauthAccount", None)
json.dump(data, open(cfg, "w"))
print("Successfully logged out from your Anthropic account.")
'''

# `codex logout` and `codex login status` as 0.167.1 answers them.
CODEX_STANDIN = '''#!/usr/bin/env python3
import os, sys
auth = os.path.join(os.environ["CODEX_HOME"], "auth.json")
if sys.argv[1:] == ["login", "status"]:
    print("Logged in using ChatGPT" if os.path.exists(auth) else "Not logged in")
    sys.exit(0)
assert sys.argv[1:] == ["logout"], sys.argv
if os.path.exists(auth):
    os.unlink(auth); print("Successfully logged out")
else:
    print("Not logged in")
'''

AGY_STANDIN = '''#!/usr/bin/env python3
print("Please sign in"); raise SystemExit(1)
'''


def jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
    return f"x.{body.rstrip('=')}.y"


def write_claude_login(tmp: Path, email: str = "claude@example.com") -> None:
    cfg.CREDENTIALS_PATH.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "not-a-token", "refreshToken": "not-a-token",
        "expiresAt": 4102444800000}}), encoding="utf-8")
    cfg.CLAUDE_CONFIG_JSON.write_text(json.dumps({
        "userID": "u", "oauthAccount": {"emailAddress": email,
                                        "organizationName": "o"}}),
        encoding="utf-8")


def write_codex_login() -> None:
    codex.AUTH_PATH.write_text(json.dumps({"tokens": {
        "access_token": "not-a-token", "refresh_token": "not-a-token",
        "id_token": jwt({"email": "codex@example.com",
                         "https://api.openai.com/auth":
                         {"chatgpt_plan_type": "pro"}})}}), encoding="utf-8")


def rows(server) -> dict:
    return {p["id"]: p for p in server.providers()}


class Busy:
    """A live session mid-turn, as far as the sign-out route asks."""

    def __init__(self, provider: str) -> None:
        self.provider = provider

    def is_live(self) -> bool:
        return True

    def busy(self) -> str:
        return "working"


def test_rows(tmp: Path, server) -> None:
    r = rows(server)
    check("labels: each provider's own words",
          {p: r[p]["sign_in"]["label"] for p in cfg.WIRED_PROVIDERS},
          {"claude-code": "Sign in with Claude",
           "codex": "Sign in with ChatGPT",
           "antigravity": "Sign in with Google"})
    check("claude-code: signed in as, on the row's login",
          (r["claude-code"]["connected"], r["claude-code"]["login"]["account"]),
          (True, "claude@example.com"))
    check("...and on sign_in", r["claude-code"]["sign_in"]["account"],
          "claude@example.com")
    check("codex: the id token's email on sign_in",
          r["codex"]["sign_in"]["account"], "codex@example.com")
    check("antigravity, signed out: account null",
          r["antigravity"]["sign_in"]["account"], None)
    check("every wired row offers a sign-out",
          {p: r[p]["sign_in"]["sign_out"] for p in cfg.WIRED_PROVIDERS},
          {p: f"/v1/providers/{p}/logout" for p in cfg.WIRED_PROVIDERS})
    check("no credential in the answer",
          "not-a-token" in json.dumps(server.providers()), False)

    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "not-a-token"
    try:
        r = rows(server)
        check("under a token in the environment: no account named",
              (r["claude-code"]["login"]["source"],
               r["claude-code"]["sign_in"]["account"]), ("oauth-token", None))
    finally:
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN")

    cfg.CLAUDE_CONFIG_JSON.write_text("{not json", encoding="utf-8")
    check("an unreadable settings file: account null, still connected",
          (rows(server)["claude-code"]["connected"],
           rows(server)["claude-code"]["sign_in"]["account"]), (True, None))
    write_claude_login(tmp)


async def test_sign_out(tmp: Path, server) -> None:
    broker = server.broker
    broker.sessions["busy-codex"] = Busy("codex")
    status, body = await route(server, "POST", "/v1/providers/codex/logout")
    check("a codex chat mid-turn: 409", status, 409)
    check("...the login is still there", codex.AUTH_PATH.exists(), True)
    status, _ = await route(server, "POST", "/v1/providers/claude-code/logout")
    check("...and does not hold up another provider's sign-out", status, 200)
    del broker.sessions["busy-codex"]
    write_claude_login(tmp)

    status, body = await route(server, "POST", "/v1/providers/codex/logout")
    check("codex sign-out: 200, removed", (status, body["removed"]),
          (200, True))
    check("...auth.json is gone", codex.AUTH_PATH.exists(), False)
    check("...the login answered is signed out",
          (body["login"]["source"], body["login"]["account"],
           body["login"]["status"]), ("none", None, "Not logged in"))
    status, body = await route(server, "POST", "/v1/providers/codex/logout")
    check("a second codex sign-out: 200, nothing removed",
          (status, body["removed"], "message" in body), (200, False, False))

    os.environ["FAKE_FAIL"] = "1"
    try:
        status, body = await route(server, "POST",
                                   "/v1/providers/claude-code/logout")
    finally:
        os.environ.pop("FAKE_FAIL")
    check("claude-code: a CLI that refuses says so",
          (status, body["removed"], body.get("message")),
          (200, False, "Failed to log out: something went wrong"))
    check("...and the login is still there", cfg.CREDENTIALS_PATH.exists(), True)

    status, body = await route(server, "POST", "/v1/providers/claude-code/logout")
    check("claude-code sign-out: 200, removed", (status, body["removed"]),
          (200, True))
    check("...the login file is gone", cfg.CREDENTIALS_PATH.exists(), False)
    check("...the login answered is signed out",
          (body["login"]["source"], body["login"]["account"]), ("none", None))
    r = rows(server)
    check("the rows say signed out, and name nobody",
          (r["claude-code"]["connected"], r["claude-code"]["sign_in"]["account"],
           r["codex"]["connected"], r["codex"]["sign_in"]["account"]),
          (False, None, False, None))


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


def standin(tmp: Path, name: str, text: str) -> str:
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="broker-sign-in-account-test-"))
    keep = (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH,
            cfg.CLAUDE, cfg.CREDENTIALS_PATH, cfg.CLAUDE_CONFIG_JSON,
            cfg._account_cache, codex.CODEX, codex.CODEX_HOME,
            codex.AUTH_PATH, codex._status_cache, agy.AGY, agy.TOKEN_FILE,
            agy.ACCOUNT_FILE, agy._login_cache)
    try:
        cfg.STATE_DIR = tmp
        cfg.SESSION_DIR = tmp / "sessions"
        cfg.USAGE_PATH = tmp / "provider-usage.json"
        cfg.TOKEN_PATH = tmp / "token"
        cfg.CLAUDE = standin(tmp, "claude", CLAUDE_STANDIN)
        cfg.CREDENTIALS_PATH = tmp / "claude-home" / ".credentials.json"
        cfg.CREDENTIALS_PATH.parent.mkdir()
        cfg.CLAUDE_CONFIG_JSON = tmp / ".claude.json"
        cfg._account_cache = None
        codex.CODEX = standin(tmp, "codex", CODEX_STANDIN)
        codex.CODEX_HOME = tmp / "codex-home"
        codex.AUTH_PATH = codex.CODEX_HOME / "auth.json"
        codex._status_cache = None
        codex.env()  # makes the home
        agy.AGY = standin(tmp, "agy", AGY_STANDIN)
        agy.TOKEN_FILE = tmp / "antigravity-oauth-token"
        agy.ACCOUNT_FILE = tmp / "antigravity" / "account.json"
        agy._login_cache = None
        os.environ["FAKE_CRED"] = str(cfg.CREDENTIALS_PATH)
        os.environ["FAKE_CONFIG"] = str(cfg.CLAUDE_CONFIG_JSON)
        write_claude_login(tmp)
        write_codex_login()
        server = broker_mod.Server(broker_mod.Broker())
        test_rows(tmp, server)
        await test_sign_out(tmp, server)
    finally:
        (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH,
         cfg.CLAUDE, cfg.CREDENTIALS_PATH, cfg.CLAUDE_CONFIG_JSON,
         cfg._account_cache, codex.CODEX, codex.CODEX_HOME,
         codex.AUTH_PATH, codex._status_cache, agy.AGY, agy.TOKEN_FILE,
         agy.ACCOUNT_FILE, agy._login_cache) = keep
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
