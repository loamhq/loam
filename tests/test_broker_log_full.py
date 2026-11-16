#!/usr/bin/env python3
"""A write the log cannot take never strands a chat.

On 2026-10-22 `/` filled while session f3069d8e was streaming. The event
write in `emit` raised ENOSPC, the exception killed that session's
`_pump_stdout`, and the chat sat on `working` while the persona went on
answering into a pipe nobody read. The failed write had also left half a line
in the log, and the next event appended onto it was lost to every replay.

Proved here with a simulated ENOSPC against a Session with a stand-in process:

  - the reader goes on reading, and every event still reaches a live page;
  - the turn the lost event belonged to is `failed`, with a sentence a page
    can show, and the next good turn is `done` again;
  - a partial line is followed by a fresh line, so the next event replays --
    including a log a previous broker left torn;
  - a record that cannot be saved does not end the reader either.

Run: python3 tests/test_broker_log_full.py
"""

import asyncio
import errno
import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
# Admission reads no real box here: a fixture session must not be refused
# because the machine running the suite is short of memory (a landing
# got 429 for one, 2026-11-09). test_broker_seats is where admission is tested.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results = []
FULL = "couldn't save this reply: the server's disk is full"


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:58} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


class FakeStdout:
    """The CLI's stdout: lines handed over one at a time, then EOF."""

    def __init__(self, lines: list[dict], before_each=None) -> None:
        self.lines = [json.dumps(line).encode() + b"\n" for line in lines]
        self.before_each = before_each
        self.read = 0

    async def readline(self) -> bytes:
        if not self.lines:
            return b""
        if self.before_each is not None:
            self.before_each(self.read)
        self.read += 1
        return self.lines.pop(0)


class FakeProc:
    returncode = None

    def __init__(self, stdout: FakeStdout) -> None:
        self.stdout = stdout
        self.stdin = None

    async def wait(self) -> int:
        return 0


class Disk:
    """A disk that fills on cue. While full, a write to a session log lands
    `keep` bytes and then raises ENOSPC, which is what a real full disk does
    to a buffered write: some of it may be on the platter."""

    def __init__(self, keep: int = 30) -> None:
        self.full = False
        self.keep = keep
        self.real_open = Path.open
        disk = self

        def fake_open(path, mode="r", *args, **kwargs):
            handle = disk.real_open(path, mode, *args, **kwargs)
            if disk.full and "a" in mode and path.suffix == ".jsonl":
                return Torn(handle, disk.keep)
            return handle

        self.fake_open = fake_open

    def __enter__(self):
        Path.open = self.fake_open
        return self

    def __exit__(self, *exc):
        Path.open = self.real_open


class Torn:
    def __init__(self, handle, keep: int) -> None:
        self.handle = handle
        self.keep = keep

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.handle.close()

    def tell(self):
        return self.handle.tell()

    def write(self, data):
        self.handle.write(data[:self.keep])
        self.handle.flush()
        raise OSError(errno.ENOSPC, "No space left on device")

    def flush(self):
        pass


def log_lines(session) -> list[str]:
    return session.log_path.read_text(encoding="utf-8").split("\n")


def parsed(session) -> list[dict]:
    out = []
    for line in log_lines(session):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def assistant(text: str) -> dict:
    return {"type": "assistant", "message": {
        "model": "claude-fable-5-1",
        "content": [{"type": "text", "text": text}]}}


def result() -> dict:
    return {"type": "result", "subtype": "success", "is_error": False,
            "session_id": "s", "total_cost_usd": 0.1, "duration_ms": 10,
            "num_turns": 1, "usage": {}, "permission_denials": []}


INIT = {"type": "system", "subtype": "init", "model": "claude-fable-5-1"}


async def main() -> None:
    # -- the disk fills mid-turn, and empties before the next one ----------
    disk = Disk()
    stream = [INIT, assistant("one"), assistant("two"), result(),
              INIT, assistant("three"), result()]

    def weather(index: int) -> None:
        # Full for "two" and the result after it; free again from the next
        # turn's init on.
        disk.full = index in (2, 3)

    session = broker_mod.Session("probe")
    session.proc = FakeProc(FakeStdout(stream, weather))
    session.state = "running"
    queue: asyncio.Queue = asyncio.Queue()
    session.subscribers.add(queue)
    with disk:
        await session._pump_stdout()

    live = []
    while not queue.empty():
        item = queue.get_nowait()
        if item is not None:
            live.append(item)
    check("the reader read to the end: the process was reaped",
          session.state, "exited")
    check("a live page got every event, the unsaved ones included",
          [e.get("type") for e in live if e.get("type") != "broker.session"],
          ["system", "assistant", "broker.write_failed", "assistant",
           "result", "system", "assistant", "result", "broker.exit"])
    notice = next(e for e in live if e["type"] == "broker.write_failed")
    check("the notice says why, in words a page can show",
          notice["reason"], FULL)
    check("...and where the loss began", notice["from_seq"], 3)
    check("the notice is not numbered: the log is what failed",
          "seq" in notice, False)
    check("the turn that lost an event is failed, with the reason",
          session.turns[0].get("not_saved"), FULL)
    check("the next turn, written whole, is not marked",
          "not_saved" in session.turns[1], False)
    check("and the session is done again after it",
          session.status(), "done")

    lines = log_lines(session)
    # The second failed write began with the newline the first one owed,
    # so its 30 bytes are that newline and 29 of the event.
    check("the torn fragments stand on lines of their own",
          [line for line in lines if line and not line.endswith("}")],
          [json.dumps({**assistant("two"), "seq": 3})[:30],
           json.dumps({**result(), "seq": 4})[:29]])
    check("everything written after the disk emptied replays",
          [e["seq"] for e in session.replay(0)], [1, 2, 5, 6, 7, 8])

    # -- the turn is failed while the disk is still full -------------------
    session = broker_mod.Session("probe")
    session.proc = FakeProc(FakeStdout([INIT, assistant("x"), result()]))
    session.state = "running"
    saw = []
    real_reap = session._reap

    async def reap_after_look() -> None:
        saw.append(session.status())
        await real_reap()

    session._reap = reap_after_look
    with Disk() as full:
        full.full = True
        await session._pump_stdout()
    check("a turn none of which was saved reads failed",
          saw, ["failed"])
    check("...and says why", session.turns[0].get("not_saved"), FULL)

    # -- a log an earlier broker left torn ---------------------------------
    #
    # The exact shape found on session f3069d8e: a fragment with no newline.
    session = broker_mod.Session("probe")
    session.log_path.parent.mkdir(parents=True, exist_ok=True)
    session.log_path.write_text(
        json.dumps({"type": "system", "seq": 1}) + "\n"
        + '{"type": "stream_event", "ev', encoding="utf-8")
    fresh = broker_mod.Session("probe", session.id)
    fresh.state = "running"
    fresh.seq = 2
    await fresh.emit({"type": "broker.user_message", "text": "Why?"})
    check("the first write after a start begins a fresh line",
          log_lines(fresh)[1:], ['{"type": "stream_event", "ev',
                                 json.dumps({"type": "broker.user_message",
                                             "text": "Why?", "seq": 3}), ""])
    check("...so the message replays",
          [e.get("text") for e in fresh.replay(0)], [None, "Why?"])
    await fresh.emit({"type": "system"})
    check("a clean log gains no blank lines",
          log_lines(fresh)[-2:], [json.dumps({"type": "system", "seq": 4}), ""])

    # -- a record that cannot be saved -------------------------------------
    session = broker_mod.Session("probe")
    session.proc = FakeProc(FakeStdout([INIT, assistant("a"), result()]))
    session.state = "running"
    real_save = session.save
    calls = {"n": 0}

    def failing_save() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.ENOSPC, "No space left on device")
        real_save()

    session.save = failing_save
    await session._pump_stdout()
    check("a failed save does not end the reader",
          [e["type"] for e in parsed(session)],
          ["system", "assistant", "result", "broker.exit"])
    check("...and the turn it fell in is failed with the reason",
          session.turns[0].get("not_saved"), FULL)

    # -- the whole disk full at the turn's end: the record still goes out --
    #
    # The log and the record both fail. The page must still be told the turn
    # failed, or the chat is stranded on its last word again.
    session = broker_mod.Session("probe")
    session.state = "running"
    session._turn_active = True
    announced = []
    session.on_record_change = lambda s: announced.append(s.status())
    queue = asyncio.Queue()
    session.subscribers.add(queue)
    real_write_text = Path.write_text

    def no_room(path, *args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    Path.write_text = no_room
    try:
        with Disk() as full:
            full.full = True
            try:
                await session._handle(result())
                check("a failed save is still raised to the pump", False, True)
            except OSError:
                pass
    finally:
        Path.write_text = real_write_text
    snapshots = []
    while not queue.empty():
        item = queue.get_nowait()
        if item.get("type") == "broker.session":
            snapshots.append(item)
    check("the live list was told the record changed, failed",
          announced, ["failed"])
    check("the page got the record, failed, with the reason",
          [(s["status"], s["turns"][-1].get("not_saved")) for s in snapshots],
          [("failed", FULL)])


root = Path(tempfile.mkdtemp(prefix="loam-broker-test-"))
saved_session_dir = cfg.SESSION_DIR
saved_ai_labs = cfg.AI_LABS
try:
    # Under a temporary directory: this test must never touch the box's real
    # session logs.
    cfg.SESSION_DIR = root / "sessions"
    cfg.AI_LABS = root / "agents"
    (cfg.AI_LABS / "probe").mkdir(parents=True)
    (cfg.AI_LABS / "probe" / "agent.json").write_text("{}", encoding="utf-8")
    asyncio.run(main())
finally:
    cfg.SESSION_DIR = saved_session_dir
    cfg.AI_LABS = saved_ai_labs
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
