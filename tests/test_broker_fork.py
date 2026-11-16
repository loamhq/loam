#!/usr/bin/env python3
"""Sending into a session the owner had in the app, without touching their file.

An adopted record's id is the app's session id, and that id names a file the
Remote Control server may still be appending to. So the first message into
one does not resume it -- it forks it: `claude -p --resume <the app's id>
--fork-session`, which reads that conversation and continues it in a new
session file of the CLI's own. Four things have to hold, and each is a way
the conversation gets damaged or lost:

  1. **The app's file is never written to.** The launch line carries
     `--fork-session` the first time and every later launch resumes the
     fork, not the app. A launch line that resumed the app's id in place is
     two processes on one file, which is a corrupted conversation of
     the owner's and not a bug that shows up as an error.
  2. **The record keeps its own identity.** The row, the log, the URL and
     the replayed history stay under the app's id; the CLI's new id is
     stored beside it as `cli_session_id`, with `forked_from` saying where
     it came from. A record that re-identified itself as the fork would be a
     new row on the home screen and the history would be under the old one.
  3. **The fork's id survives a restart.** It is read back off the record,
     because a broker that forgot it would fork the app's session a second
     time and the turns taken here would be in a file nothing points at.
  4. **One stream, continuing numbering.** The replayed history is
     1..`replayed` and what happens after the fork is numbered from there,
     and every event on it names the record -- the CLI states its own id on
     each envelope, and a client keying by `session_id` would otherwise read
     one conversation as two.

No route reaches this since the classic chat view retired (2026-11-12):
`POST /v1/sessions` with `resume: true` was the only way an adopted record
was sent into, and a thread starts sessions of its own. The launch path stays
in the broker, so it is driven here as `Broker.start_session(resume=True)`
and `Session.send_message`, the calls that route made.

The CLI is faked here, which is what makes this a test of the broker and not
of Claude Code: a real one costs money, takes a minute and would prove the
same lines. What a real one does was caused once, by hand, and is in the
receipt for 2026-09-20; what cannot be faked is that `--fork-session` leaves
the original file alone, so the file's size and mtime are asserted anyway.

Run: python3 tests/test_broker_fork.py
"""

import asyncio
import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_adopt as adoption  # noqa: E402
import loam_broker as cfg
# Admission reads no real box here: a fixture session must not be refused
# because the machine running the suite is short of memory (a landing
# got 429 for one, 2026-11-09). test_broker_seats is where admission is tested.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_clock  # noqa: E402

# Admission reads the box's real memory since 2026-11-09, so on a full box a
# start here was refused and this test failed for a reason it is not about
# (two landings refused on 11-09 at ~2.8 GB free). The box always has room
# here; test_broker_seats is the test of admission.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

APP_ID = "aaaaaaaa-1111-4111-8111-111111111111"
CLI_ID = "bbbbbbbb-2222-4222-8222-222222222222"

results = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:68} got={got!r:.130}")
    if not ok:
        print(f"      wanted {want!r:.200}")


# -- a CLI that is not there ------------------------------------------------
#
# Enough of one for the broker to talk to: a stdin that remembers what was
# written to it, a stdout the test feeds line by line, and a process that
# stays alive until the test ends it. The pump reads it the same way it reads
# the real thing, which is the point -- everything under test here runs.

class FakeStdin:
    def __init__(self) -> None:
        self.lines: list[dict] = []

    def write(self, data: bytes) -> None:
        for line in data.decode().splitlines():
            if line.strip():
                self.lines.append(json.loads(line))

    async def drain(self) -> None:
        return None


class FakeProc:
    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.returncode = None
        self._ended = asyncio.Event()

    def says(self, obj: dict) -> None:
        self.stdout.feed_data((json.dumps(obj) + "\n").encode())

    def terminate(self) -> None:
        self.returncode = 0
        self.stdout.feed_eof()
        self._ended.set()

    def kill(self) -> None:
        self.terminate()

    async def wait(self) -> int:
        await self._ended.wait()
        return self.returncode


launches: list[list[str]] = []
procs: list[FakeProc] = []


async def fake_exec(*argv, **kwargs):
    launches.append(list(argv))
    proc = FakeProc()
    procs.append(proc)
    return proc


async def settle(times: int = 8) -> None:
    """Let the stdout pump run. The broker reads its CLI on a task of its
    own, so a test that asserted straight after feeding a line would be
    asserting on the line before it."""
    for _ in range(times):
        await asyncio.sleep(0)


# -- the conversation the app left behind ----------------------------------

def person(text, ts):
    return {"type": "user", "promptSource": "sdk", "origin": {"kind": "human"},
            "timestamp": ts, "message": {"role": "user", "content": text}}


def spoke(text, ts):
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "model": "claude-fable-5-1",
                        "content": [{"type": "text", "text": text}]}}


NOW = time.time()


def ago(days: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(NOW - days * 86400))


CONVERSATION = [
    person("what did the market watch turn up?", ago(2)),
    spoke("Two things stood out.", ago(2)),
]


async def main(root: Path) -> None:
    ada = cfg.workspace("ada")
    ada.mkdir(parents=True, exist_ok=True)
    (ada / "agent.json").write_text(
        json.dumps({"name": "ada", "display": "Ada", "emoji": "🧱"}),
        encoding="utf-8")
    folder = adoption.project_dir(ada)
    folder.mkdir(parents=True, exist_ok=True)
    app_file = folder / f"{APP_ID}.jsonl"
    with app_file.open("w", encoding="utf-8") as handle:
        for entry in CONVERSATION:
            handle.write(json.dumps(entry) + "\n")
    untouched = (app_file.stat().st_size, app_file.stat().st_mtime_ns)

    broker = broker_mod.Broker()
    broker.adopt_app_sessions()
    replayed = broker.load(APP_ID).replayed
    check("the app's conversation is a record with a history to replay",
          replayed, 3)

    # -- the launch line, before anything has run -------------------------
    fresh = broker.load(APP_ID)
    check("an adopted session is forked, never resumed in place",
          fresh.argv(resume=True)[-3:], ["--resume", APP_ID, "--fork-session"])
    check("...and never asks the CLI for an id of its own",
          "--session-id" in fresh.argv(resume=True), False)

    # -- the first message ------------------------------------------------
    session = await broker.start_session("ada", APP_ID, resume=True)
    check("starting one launches the fork", launches[0][-3:],
          ["--resume", APP_ID, "--fork-session"])
    check("...in the persona's own workspace, as any session is",
          str(session.workspace), str(ada))

    proc = procs[0]
    proc.says({"type": "system", "subtype": "init", "session_id": CLI_ID,
               "model": "claude-fable-5-1"})
    await settle()

    record = broker.load(APP_ID).meta()
    check("the record keeps the app's id as its identity",
          record["session_id"], APP_ID)
    check("...and stores the CLI id it now drives, and where it came from",
          (record["cli_session_id"], record["forked_from"]), (CLI_ID, APP_ID))
    check("...while the rest of what adoption learned is untouched",
          (record["origin"], record["source"], record["replayed"]),
          ("claude-code-app", str(app_file), 3))

    seq = await session.send_message("adoption check")
    check("a message into the forked session is logged after the history",
          seq, 5)
    line = proc.stdin.lines[-1]
    # The clock line opens the text since 2026-11-09 (lib/loam_clock.py).
    check("...and reaches the CLI's stdin as a user message",
          {**line, "message": {**line["message"],
                               "content": loam_clock.strip(line["message"]["content"])}},
          {"type": "user", "message": {"role": "user",
                                       "content": "adoption check"}})

    proc.says({"type": "assistant", "session_id": CLI_ID,
               "parent_tool_use_id": None,
               "message": {"role": "assistant", "model": "claude-fable-5-1",
                           "content": [{"type": "text",
                                        "text": "Still here."}]}})
    proc.says({"type": "result", "subtype": "success", "is_error": False,
               "session_id": CLI_ID, "duration_ms": 4200, "num_turns": 1,
               "total_cost_usd": 0.03, "usage": {"input_tokens": 10}})
    await settle()

    record = broker.load(APP_ID).meta()
    check("the turn moves the record the way any turn does",
          (record["status"], record["last_message"]), ("done", "Still here."))
    check("...and the turn record says which process ran it, and what it cost",
          [(t["session_id"], t["cost_delta_usd"]) for t in record["turns"]],
          [(CLI_ID, 0.03)])

    # -- one stream -------------------------------------------------------
    events = broker.load(APP_ID).replay(0)
    check("the replayed history stays first and the new events follow it",
          [(e["seq"], e["type"]) for e in events],
          [(1, "broker.user_message"), (2, "assistant"), (3, "result"),
           (4, "system"), (5, "broker.user_message"), (6, "assistant"),
           (7, "result")])
    check("...the history says it was adopted and the new events do not",
          [e.get("adopted") for e in events],
          [True, True, True, None, None, None, None])
    check("...and every event on the stream names the record, not the fork",
          {e.get("session_id") for e in events}, {APP_ID})

    # -- a restart --------------------------------------------------------
    await session.stop()
    broker.sessions.clear()
    after = broker.load(APP_ID)
    check("the fork's id is read back off the record",
          (after.cli_session_id, after.forked_from), (CLI_ID, APP_ID))
    check("...so the next launch resumes the fork and forks nothing twice",
          (after.argv(resume=True)[-2:], "--fork-session" in after.argv(True)),
          (["--resume", CLI_ID], False))
    await broker.start_session("ada", APP_ID, resume=True)
    check("...which is the line it really launches",
          launches[-1][-2:], ["--resume", CLI_ID])
    procs[-1].terminate()
    await settle()

    # -- the file this was all to protect ---------------------------------
    check("the app's own session file was never written to",
          (app_file.stat().st_size, app_file.stat().st_mtime_ns), untouched)
    check("...and the broker's log for this session is its own file",
          (cfg.SESSION_DIR / f"{APP_ID}.jsonl").exists(), True)

    # -- an adopted record with nothing behind it -------------------------
    app_file.unlink()
    broker.sessions.clear()
    gone = broker.load(APP_ID)
    gone.cli_session_id = None
    gone.forked_from = None
    gone.save()
    broker.sessions.clear()
    try:
        await broker.start_session("ada", APP_ID, resume=True)
        status, said = None, ""
    except broker_mod.BrokerError as exc:
        status, said = exc.status, exc.message
    check("a session whose file is gone is refused, and told why",
          (status, "nothing to fork from" in said), (409, True))


root = Path(tempfile.mkdtemp(prefix="broker-fork-"))
saved = {name: getattr(cfg, name)
         for name in ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "USAGE_PATH",
                      "AI_LABS")}
saved_adopt = {name: getattr(adoption, name)
               for name in ("PROJECTS", "TRANSCRIPT_STATE")}
real_exec = asyncio.create_subprocess_exec
try:
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.USAGE_PATH = cfg.STATE_DIR / "provider-usage.json"
    cfg.SESSION_DIR.mkdir(parents=True)
    cfg.AI_LABS = root / "agents"
    adoption.PROJECTS = root / "projects"
    adoption.TRANSCRIPT_STATE = root / "transcripts"
    asyncio.create_subprocess_exec = fake_exec
    asyncio.run(main(root))
finally:
    asyncio.create_subprocess_exec = real_exec
    for name, value in saved.items():
        setattr(cfg, name, value)
    for name, value in saved_adopt.items():
        setattr(adoption, name, value)
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
