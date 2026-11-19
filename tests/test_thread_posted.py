#!/usr/bin/env python3
"""Check that an out-of-turn post wakes mentioned members and respects thread membership rules."""

from __future__ import annotations

import asyncio
import http.server
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-posted-"))
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
for who in ("ada", "cleo", "iris"):
    (TMP / "ai-labs" / who).mkdir(parents=True)
    (TMP / "ai-labs" / who / "agent.json").write_text(json.dumps({"name": who.capitalize()}))
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents" / "comms.md").write_text("# From agents\n")
sys.path.insert(0, str(ROOT / "lib"))

FAILURES = 0


def check(label, got, want):
    global FAILURES  # noqa: PLW0603
    ok = got == want
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


broker_mod = load("session_broker_posted", ROOT / "bin" / "session-broker")
engine_mod = broker_mod.loam_thread_engine
threads = broker_mod.threads_rec
threads.roster = lambda: ["ada", "cleo", "iris"]
OWNER = threads.OWNER


class Writer:
    def __init__(self):
        self.chunks = []

    def write(self, d):
        self.chunks.append(d)

    async def drain(self):
        pass

    def status(self):
        return int(b"".join(self.chunks).split(b" ", 2)[1])

    def json(self):
        return json.loads(b"".join(self.chunks).decode().split("\r\n\r\n", 1)[1])


class FakeBroker:
    def __init__(self):
        self.sessions, self.record_subscribers = {}, set()
        self.stopping = False

    def live(self):
        return []


async def route(server, method, path, payload=None):
    wr = Writer()
    await server.route(method, path, {}, payload or {}, wr)
    return wr.status(), wr.json()


async def main():
    server = broker_mod.Server.__new__(broker_mod.Server)
    server.broker = FakeBroker()
    engine = server.threads = engine_mod.ThreadEngine(server.broker)
    turns = []
    engine.schedule = lambda turn: turns.append((turn.persona, turn.chain, turn.reason))
    meta = threads.create("ada", "Two agents", members=["ada", "cleo"])
    tid = meta["id"]
    stream = asyncio.Queue()
    engine.listeners.setdefault(tid, set()).add(stream)

    threads.append(tid, OWNER, "Ada, ask Cleo about the page.")
    post = threads.append(tid, "ada", "@cleo can you look at the front page?")
    code, body = await route(server, "POST", f"/v1/threads/{tid}/posted",
                             {"message": post["id"], "by": "ada"})
    check("a post naming a member wakes that member", (code, body["woke"], turns),
          (200, ["cleo"], [("cleo", 1, "mention")]))
    events = []
    while not stream.empty():
        events.append(stream.get_nowait())
    check("...and the post is on the thread's stream at once",
          [e["message"]["id"] for e in events if e.get("type") == "message.done"], [post["id"]])
    code, body = await route(server, "POST", f"/v1/threads/{tid}/posted",
                             {"message": post["id"], "by": "ada"})
    check("the same post told twice wakes nobody twice", (code, body["woke"], len(turns)),
          (200, [], 1))

    plain = threads.append(tid, "ada", "Nothing for anyone here. @all of it is fine.")
    code, body = await route(server, "POST", f"/v1/threads/{tid}/posted",
                             {"message": plain["id"], "by": "ada"})
    check("a post naming nobody gives no turn; @all from an agent is text",
          (body["woke"], len(turns)), ([], 1))

    # Ping-pong by posts: bounded by the cap, then the thread's own mark.
    turns.clear()
    who = ["cleo", "ada"]
    for i in range(6):
        by, other = who[i % 2], who[(i + 1) % 2]
        msg = threads.append(tid, by, f"@{other} round {i}")
        await route(server, "POST", f"/v1/threads/{tid}/posted", {"message": msg["id"], "by": by})
    cap = threads.AGENT_CHAIN_CAP
    check(f"two agents posting at each other stop at the cap ({cap})",
          max(c for _, c, _ in turns) <= cap and len(turns) < 6, True)
    lines = [m["text"] for m in threads.read(tid)[1] if m.get("kind") == "system"]
    check("...with no line in the record", any(line.startswith("Paused after") for line in lines), False)
    marks = [m["text"] for m in server.threads.marks_for(tid)]
    check("...and a mark on the thread that says so",
          any(t.startswith("Paused after") for t in marks), True)
    threads.append(tid, OWNER, "Carry on, both of you.")
    turns.clear()
    again = threads.append(tid, "ada", "@cleo once more")
    code, body = await route(server, "POST", f"/v1/threads/{tid}/posted",
                             {"message": again["id"], "by": "ada"})
    check("...and the owner writing starts a new chain", body["woke"], ["cleo"])

    try:
        code, _ = await route(server, "POST", f"/v1/threads/{tid}/posted",
                              {"message": again["id"], "by": "cleo"})
    except broker_mod.BrokerError as exc:
        code = exc.status
    check("a message that is not the poster's is refused", code, 400)

    # bin/thread post tells the broker: a stub answers as the route would.
    told = []

    class Stub(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            told.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            out = json.dumps({"woke": ["cleo"]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    stub = http.server.HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    cli = load("thread_cli_posted", ROOT / "bin" / "thread")
    import loam_broker as cfg
    cfg.BIND_ADDR, cfg.PORT, cfg.read_token = "127.0.0.1", stub.server_address[1], lambda: "t"
    import contextlib
    import io
    out = io.StringIO()
    sys.argv = ["thread", "post", tid, "@cleo from the command", "--by", "ada"]
    with contextlib.redirect_stdout(out):
        rc = cli.main()
    printed = json.loads(out.getvalue())
    check("bin/thread post tells the broker which message, by whom",
          (rc, told[0][0], told[0][1]), (0, f"/v1/threads/{tid}/posted",
                                         {"message": printed["message"]["id"], "by": "ada"}))
    check("...and says who was woken", printed.get("woke"), ["cleo"])
    stub.shutdown()


asyncio.run(main())
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'FAILED' if FAILURES else 'OK'}: {FAILURES} failure(s)")
sys.exit(1 if FAILURES else 0)
