#!/usr/bin/env python3
"""Check Claude Code sign-in state and interactive login handling."""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
os.environ["LOAM_USAGE_READS"] = "off"  # no real CLI reads; see loam_provider_usage
import loam_broker as cfg  # noqa: E402
# Use fixture memory to isolate the broker from machine load.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}

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


# Preserve terminal escapes and both occurrences of the login URL.
REAL_DRAWING = (
    b"Opening browser to sign in\xe2\x80\xa6\r\n"
    b"If the browser didn't open, visit: \x1b]8;;"
    b"https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a"
    b"&response_type=code&code_challenge_method=S256&state=abc\x07"
    b"\x1b[94mhttps://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a"
    b"&response_type=code&code_challenge_method=S256&state=abc\x1b[39m"
    b"\x1b]8;;\x07\r\n"
    b"Paste code here if prompted > ")
REAL_URL = ("https://claude.com/cai/oauth/authorize?code=true"
            "&client_id=9d1c250a&response_type=code"
            "&code_challenge_method=S256&state=abc")

MS = 1000.0


def credentials(expires_ms=None, token="a-token", key="refreshTokenExpiresAt"):
    oauth = {"accessToken": token}
    if expires_ms is not None:
        oauth[key] = expires_ms
    return {"claudeAiOauth": oauth}


# -- 1. what the app can see -----------------------------------------------

def test_login_state(tmp: Path) -> None:
    creds = tmp / ".credentials.json"
    cfg.CREDENTIALS_PATH = creds
    env = {}          # neither variable set, which is this box

    # A file with a date: three days and a bit out.
    soon = (time.time() + 3.4 * 86400) * MS
    creds.write_text(json.dumps(credentials(soon)), encoding="utf-8")
    state = cfg.login_state(env)
    check("a file with a date: source", state["source"], "credentials-file")
    check("a file with a date: connected", state["connected"], True)
    check("a file with a date: reason", state["reason"], None)
    check("a file with a date: days_left rounds DOWN",
          state["days_left"], 3)
    check("a file with a date: expires_at is an instant, not an epoch",
          state["expires_at"], cfg.epoch_iso(soon / MS))

    # No file at all.
    creds.unlink()
    state = cfg.login_state(env)
    check("no file: source", state["source"], "none")
    check("no file: connected", state["connected"], False)
    check("no file: reason", state["reason"], "no login on the box")
    check("no file: expires_at", state["expires_at"], None)
    check("no file: days_left", state["days_left"], None)

    # A login that has already gone.
    creds.write_text(json.dumps(credentials((time.time() - 90000) * MS)),
                     encoding="utf-8")
    state = cfg.login_state(env)
    check("expired: connected", state["connected"], False)
    check("expired: reason", state["reason"],
          "the login has expired and must be made again")
    check("expired: days_left is negative, not None", state["days_left"], -2)

    # The access token's expiry is the fallback when there is no refresh one,
    # which is what the row answered before this field existed.
    creds.write_text(json.dumps(credentials(soon, key="expiresAt")),
                     encoding="utf-8")
    check("only expiresAt: it is used", cfg.login_state(env)["days_left"], 3)

    # A file with a token and no date at all: connected, and honest that it
    # cannot say until when.
    creds.write_text(json.dumps(credentials()), encoding="utf-8")
    state = cfg.login_state(env)
    check("no date: connected", state["connected"], True)
    check("no date: expires_at", state["expires_at"], None)

    # A file with no token in it.
    creds.write_text(json.dumps({"claudeAiOauth": {}}), encoding="utf-8")
    check("no token in the file", cfg.login_state(env)["reason"],
          "the login holds no token")

    # A file that will not parse.
    creds.write_text("{not json", encoding="utf-8")
    state = cfg.login_state(env)
    check("unparseable: connected", state["connected"], False)
    check("unparseable: reason names the failure", state["reason"],
          "the login could not be read (JSONDecodeError)")

    # The source is read off the environment a session would inherit, not
    # hard-coded: a token in the unit changes the answer.
    creds.write_text(json.dumps(credentials(soon)), encoding="utf-8")
    check("a token in the environment wins",
          cfg.login_state({"CLAUDE_CODE_OAUTH_TOKEN": "sk-x"})["source"],
          "oauth-token")
    check("and nothing can read an expiry out of it",
          cfg.login_state({"CLAUDE_CODE_OAUTH_TOKEN": "sk-x"})["expires_at"],
          None)
    check("an API key wins over both",
          cfg.login_state({"ANTHROPIC_API_KEY": "sk-y",
                           "CLAUDE_CODE_OAUTH_TOKEN": "sk-x"})["source"],
          "api-key")
    check("the old two-field reading still agrees",
          cfg.claude_code_login(), (True, None))


def test_provider_row(server) -> None:
    rows = {p["id"]: p for p in server.providers()}
    wired = rows["claude-code"]
    check("the wired row carries login", sorted(wired["login"]),
          ["account", "days_left", "expires_at", "source"])
    check("and no token, nor any piece of one",
          any("token" in json.dumps(wired).lower().split('"login"')[0]
              for _ in [0]) is False or "accessToken" not in json.dumps(wired),
          True)
    check("an unwired row carries no login key at all",
          "login" in rows["grok"], False)


# -- 2. reading what the CLI drew ------------------------------------------

def test_parsing() -> None:
    check("the URL out of a real pty drawing",
          cfg.login_url(REAL_DRAWING), REAL_URL)
    check("nothing to find before the CLI has drawn it",
          cfg.login_url(b"Opening browser to sign in\xe2\x80\xa6"), None)
    check("a URL that is not the sign-in one is not it",
          cfg.login_url(b"see https://docs.claude.com/whatever"), None)

    failed = REAL_DRAWING + b"my-pasted-code\r\nLogin failed: Request failed with status code 400\r\n"
    check("the CLI's last word, which is what a person must act on",
          cfg.login_message(failed, "my-pasted-code"),
          "Login failed: Request failed with status code 400")
    echoed = REAL_DRAWING + b"\r\nmy-pasted-code\r\n"
    check("the pasted code is scrubbed out of anything answered",
          cfg.login_message(echoed, "my-pasted-code"), "<code>")
    # The prompt line is where the pty echoes the code, and the URL line is
    # the same sentence for everybody. Neither is ever the message.
    check("neither the prompt nor the URL line is the message",
          cfg.login_message(REAL_DRAWING), "Opening browser to sign in…")


# -- 3. the flow, on a stand-in CLI ----------------------------------------

STANDIN = """#!/usr/bin/env python3
import os, sys
sys.stdout.write({drawing!r}.decode("utf-8", "replace"))
sys.stdout.flush()
code = sys.stdin.readline().strip()
if code == os.environ.get("LOGIN_FAKE_GOOD"):
    open(os.environ["LOGIN_FAKE_CREDS"], "w").write(
        '{{"claudeAiOauth": {{"accessToken": "new", '
        '"refreshTokenExpiresAt": %d}}}}' % int((__import__("time").time()
                                                 + 30 * 86400) * 1000))
    print("\\nLogin successful")
    sys.exit(0)
print("\\nLogin failed: Request failed with status code 400")
sys.exit(1)
""".format(drawing=REAL_DRAWING)


async def test_flow(tmp: Path, server) -> None:
    standin = tmp / "claude-standin"
    standin.write_text(STANDIN, encoding="utf-8")
    standin.chmod(0o755)
    cfg.CLAUDE = str(standin)
    cfg.LOGIN_URL_TIMEOUT_S = 10
    cfg.LOGIN_SUBMIT_TIMEOUT_S = 10
    os.environ["LOGIN_FAKE_CREDS"] = str(cfg.CREDENTIALS_PATH)
    os.environ["LOGIN_FAKE_GOOD"] = "the-right-code"

    check("idle before anything", (await route(server, "GET", LOGIN))[1]["state"],
          "idle")

    status, body = await route(server, "POST", LOGIN)
    check("starting one answers 201", status, 201)
    check("...pending", body["state"], "pending")
    check("...with the URL the CLI drew", body["url"], REAL_URL)
    check("...and no code of its own: this CLI asks for one",
          body["code"], None)
    check("...and says what a person must do",
          body["instructions"].startswith("Open the URL, sign in"), True)
    check("...and states the login as it stands, for the page to show",
          body["login"]["source"], "credentials-file")

    status, again = await route(server, "POST", LOGIN)
    check("a second POST is the same flow, not a second one", status, 200)
    check("...same URL", again["url"], body["url"])
    check("...same start time", again["started_at"], body["started_at"])

    status, body = await route(server, "POST", CODE, {"code": "  "})
    check("a blank code is refused", status, 400)
    check("...and the flow is untouched",
          (await route(server, "GET", LOGIN))[1]["state"], "pending")

    status, body = await route(server, "POST", CODE, {"code": "wrong-code"})
    check("a wrong code: answered 200 with the failure, not an error",
          status, 200)
    check("...state", body["state"], "failed")
    check("...the CLI's own words", body["message"],
          "Login failed: Request failed with status code 400")
    check("...and the code is not in them", "wrong-code" in
          json.dumps(body), False)
    check("...and the login on the box is unchanged",
          body["login"]["connected"], True)

    status, body = await route(server, "POST", CODE, {"code": "x"})
    check("nothing is waiting for a code now", status, 409)

    # The one path a person completes.
    await route(server, "POST", LOGIN)
    status, body = await route(server, "POST", CODE,
                               {"code": "the-right-code"})
    check("the right code: done", body["state"], "done")
    check("...no message to show", body["message"], None)
    check("...and the answer carries the NEW expiry, read from the file",
          body["login"]["days_left"], 29)

    # Called off.
    await route(server, "POST", LOGIN)
    check("pending again", (await route(server, "GET", LOGIN))[1]["state"],
          "pending")
    status, body = await route(server, "DELETE", LOGIN)
    check("DELETE calls it off", body["state"], "idle")
    check("...and forgets the URL", body["url"], None)
    check("...and leaves no process behind",
          server.broker.login.proc, None)

    status, body = await route(server, "POST", "/v1/providers/grok/login")
    check("an unwired provider has no login to make", status, 404)
    status, body = await route(server, "GET", "/v1/providers/grok/login")
    check("...whichever way it is asked", status, 404)


# -- harness ----------------------------------------------------------------

LOGIN = "/v1/providers/claude-code/login"
CODE = LOGIN + "/code"


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
    tmp = Path(tempfile.mkdtemp(prefix="broker-login-test-"))
    keep = (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH,
            cfg.CREDENTIALS_PATH, cfg.CLAUDE, cfg.LOGIN_URL_TIMEOUT_S,
            cfg.LOGIN_SUBMIT_TIMEOUT_S)
    try:
        cfg.STATE_DIR = tmp
        cfg.SESSION_DIR = tmp / "sessions"
        cfg.USAGE_PATH = tmp / "provider-usage.json"
        cfg.TOKEN_PATH = tmp / "token"
        test_login_state(tmp)
        # The row is read with a good file in place, which is where
        # test_login_state left it.
        cfg.CREDENTIALS_PATH.write_text(
            json.dumps(credentials((time.time() + 5 * 86400) * MS)),
            encoding="utf-8")
        server = broker_mod.Server(broker_mod.Broker())
        test_provider_row(server)
        test_parsing()
        await test_flow(tmp, server)
    finally:
        (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH,
         cfg.CREDENTIALS_PATH, cfg.CLAUDE, cfg.LOGIN_URL_TIMEOUT_S,
         cfg.LOGIN_SUBMIT_TIMEOUT_S) = keep
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
