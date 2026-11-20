#!/usr/bin/env python3
"""Keep the opening session snapshot separate from numbered journal events."""

import asyncio
import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
import types
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
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
    print(f"{'PASS' if ok else 'FAIL'}  {label:62} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


class FakeStdin:
    def __init__(self) -> None:
        self.lines: list[dict] = []

    def write(self, data: bytes) -> None:
        self.lines.append(json.loads(data.decode()))

    async def drain(self) -> None:
        return None


class FakeProc:
    returncode = None

    def __init__(self) -> None:
        self.stdin = FakeStdin()


class FakeWriter:
    """An SSE client's end of the socket, kept so the test can read the wire."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None

    def items(self) -> list[dict]:
        """Every ``data:`` frame, in the order it was written."""
        text = b"".join(self.chunks).decode()
        return [json.loads(line[len("data: "):])
                for line in text.splitlines()
                if line.startswith("data: ")]


def running(agent: str = "probe", session_id: str | None = None):
    session = broker_mod.Session(agent, session_id)
    session.state = "running"
    session.proc = FakeProc()
    return session


def broker_for(*sessions):
    """Build an isolated broker without minting a token or reading installed session
    records.
    """
    stand_in = types.SimpleNamespace(
        sessions={s.id: s for s in sessions},
        record_subscribers=set(), _scanned=0.0)
    for name in ("adopt", "record_changed", "load", "known", "_source_moved"):
        setattr(stand_in, name,
                getattr(broker_mod.Broker, name).__get__(stand_in))
    return stand_in


def envelope(total, session_id="s"):
    return {"type": "result", "subtype": "success", "session_id": session_id,
            "total_cost_usd": total, "duration_ms": 2249, "num_turns": 1,
            "usage": {"input_tokens": 4, "output_tokens": 76},
            "permission_denials": []}


def assistant(text: str, model: str = "claude-fable-5-1",
              parent: str | None = None) -> dict:
    return {"type": "assistant", "parent_tool_use_id": parent,
            "message": {"model": model,
                        "content": [{"type": "text", "text": text}]}}


async def conversation(session) -> None:
    """Give the log a final event distinct from its starting position."""
    await session.send_message("read /etc/hostname")
    await session._handle({"type": "system", "subtype": "init"})
    await session._handle(assistant("The file contains one line."))
    await session._handle(envelope(0.34253275))
    await session.send_message("and now count to three")
    await session._handle(assistant("one two three"))
    await session._handle(envelope(0.35690825))


async def main() -> None:
    # -- the snapshot has no seq, and is never logged -----------------------
    session = running()
    await conversation(session)
    snapshot = session.snapshot()
    logged = session.replay(0)
    check("the snapshot is a broker.session", snapshot["type"], "broker.session")
    check("it carries no seq", "seq" in snapshot, False)
    check("its position field is record_seq", snapshot["record_seq"],
          session.seq)
    check("...which is the last event's number",
          snapshot["record_seq"], logged[-1]["seq"])
    check("every seq in the log is unique",
          len({e["seq"] for e in logged}), len(logged))
    check("the snapshot was never written to the log",
          [e["type"] for e in logged].count("broker.session"), 0)

    # -- a turn record reaches a live subscriber ----------------------------
    #
    # A listener on the session (what a thread turn holds, `Session.watch`):
    # the snapshot must arrive when the turn is recorded, carrying the turn.
    session = running()
    queue: asyncio.Queue = asyncio.Queue()
    session.watch(queue)
    check("the listener is subscribed", len(session.subscribers), 1)
    await session.send_message("count to three")
    await session._handle(assistant("one two three"))
    await session._handle(envelope(0.01192425))
    await asyncio.sleep(0.05)
    live = []
    while not queue.empty():
        live.append(queue.get_nowait())
    session.unwatch(queue)

    check("the listener read: message, assistant, result, snapshot",
          [e["type"] for e in live],
          ["broker.user_message", "assistant", "result", "broker.session"])
    turn = live[-1]["turns"][-1]
    check("the re-sent snapshot carries the turn that just ended",
          turn["total_cost_usd"], 0.01192425)
    check("...with the cost only the broker can compute",
          turn["cost_delta_usd"], 0.01192425)
    check("...and the model it ran on", turn["model"], "claude-fable-5-1")
    check("the re-sent snapshot has no seq either", "seq" in live[-1], False)
    check("...and its record_seq is the result event's number",
          live[-1]["record_seq"], live[-2]["seq"])
    check("re-sending it numbered nothing", session.seq, live[-2]["seq"])
    check("...and logged nothing",
          [e["type"] for e in session.replay(0)].count("broker.session"), 0)

    # -- the model on a turn record ----------------------------------------
    session = running()
    await session._handle(assistant("thinking", model="claude-fable-5-1"))
    # A child session can use a different model.
    await session._handle(assistant("from a subagent",
                                    model="claude-haiku-4-5-20251001",
                                    parent="toolu_01"))
    await session._handle(envelope(0.1))
    check("the turn's model is its top-level messages'",
          session.turns[0]["model"], "claude-fable-5-1")

    await session._handle(envelope(0.2))
    check("a turn whose top-level messages named no model is null",
          session.turns[1]["model"], None)
    check("...and the key is there regardless",
          "model" in session.turns[1], True)
    check("a later turn does not inherit an earlier turn's model",
          [t["model"] for t in session.turns],
          ["claude-fable-5-1", None])

    await session._handle(assistant("second", model="claude-opus-5"))
    await session._handle(envelope(0.3))
    check("and the next turn states its own", session.turns[2]["model"],
          "claude-opus-5")

    # -- a record written under the old name still resumes ------------------
    #
    # The rename is a rename of a stored key too, and a session whose record
    # says `seq` must not restart its numbering at zero: that is the defect
    # that made reconnection worthless in the first place.
    old = running()
    await conversation(old)
    stored = json.loads(old.meta_path.read_text(encoding="utf-8"))
    stored["seq"] = stored.pop("record_seq")
    old.meta_path.write_text(json.dumps(stored), encoding="utf-8")
    reopened = broker_mod.Session("probe", old.id)
    check("a pre-rename record is read for its numbering",
          reopened.seq, old.seq)
    check("...and carries its turns across as before",
          len(reopened.turns), len(old.turns))
    listed = broker_for().known()
    listed = [r for r in listed if r["session_id"] == old.id][0]
    check("...and the listing answers it under the new name either way",
          (listed.get("record_seq"), "seq" in listed), (old.seq, False))
    reopened.save()
    rewritten = json.loads(reopened.meta_path.read_text(encoding="utf-8"))
    check("...and the file itself once the session is touched again",
          (rewritten.get("record_seq"), "seq" in rewritten),
          (old.seq, False))


root = Path(tempfile.mkdtemp(prefix="loam-broker-snapshot-test-"))
saved_session_dir = cfg.SESSION_DIR
saved_ai_labs = cfg.AI_LABS
try:
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
