#!/usr/bin/env python3
"""Broadcast permission resolution to all subscribers and record the model that handled the turn."""

import asyncio
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
    print(f"{'PASS' if ok else 'FAIL'}  {label:58} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


class FakeStdin:
    """The CLI's stdin. Keeps what was written so the test can assert the
    control_response really went out before the event was emitted."""

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


def session_with_pending(agent: str = "probe") -> "broker_mod.Session":
    session = broker_mod.Session(agent)
    session.state = "running"
    session.proc = FakeProc()
    session.pending["req-1"] = {
        "tool_name": "Read",
        "input": {"file_path": "/somewhere/outside"},
        "decision_reason": "Path is outside allowed working directories",
    }
    return session


async def main() -> None:
    # -- an allow ---------------------------------------------------------
    session = session_with_pending()
    await session.answer_permission("req-1", "allow")

    sent = session.proc.stdin.lines
    check("the control_response went out", len(sent), 1)
    check("...as an allow", sent[0]["response"]["response"]["behavior"], "allow")

    log = [json.loads(line) for line in
           session.log_path.read_text(encoding="utf-8").splitlines() if line]
    check("one event was emitted", len(log), 1)
    event = log[0]
    check("its type", event["type"], "broker.permission_resolved")
    check("request_id is flat, not nested", event["request_id"], "req-1")
    check("behavior is flat", event["behavior"], "allow")
    check("tool_name comes from the request", event["tool_name"], "Read")
    check("an allow carries no message", event["message"], None)
    check("session_id is on it", event["session_id"], session.id)
    check("it is numbered like any event", event["seq"], 1)
    check("nothing is nested under a payload key",
          sorted(event), ["behavior", "kind", "message", "request_id", "seq",
                          "session_id", "tool_name", "type"])
    check("a tool call's card is an approval", event["kind"], "approval")
    check("the request is no longer pending", session.pending, {})
    check("the record agrees", session.meta()["pending_permissions"], [])

    # Answering it a second time is a 404, not a second resolution.
    try:
        await session.answer_permission("req-1", "allow")
        check("answering twice raises", False, True)
    except broker_mod.BrokerError as exc:
        check("answering twice is a 404", exc.status, 404)
    log = [json.loads(line) for line in
           session.log_path.read_text(encoding="utf-8").splitlines() if line]
    check("and emits nothing further", len(log), 1)

    # -- a deny -----------------------------------------------------------
    session = session_with_pending()
    await session.answer_permission("req-1", "deny", "not this time")
    log = [json.loads(line) for line in
           session.log_path.read_text(encoding="utf-8").splitlines() if line]
    event = log[0]
    check("a deny is reported as a deny", event["behavior"], "deny")
    check("...with the client's own message", event["message"], "not this time")
    check("...and the deny reached the CLI",
          session.proc.stdin.lines[0]["response"]["response"]["message"],
          "not this time")

    # -- a live subscriber sees it too ------------------------------------
    session = session_with_pending()
    queue: asyncio.Queue = asyncio.Queue()
    session.subscribers.add(queue)
    await session.answer_permission("req-1", "allow")
    live = queue.get_nowait()
    check("a client watching live gets the same event",
          live["type"], "broker.permission_resolved")
    check("...the same one that is on disk", live["seq"], 1)

    # Use the top-level assistant model to identify the responding model.
    envelope = {"type": "result", "subtype": "success", "modelUsage": {
        "claude-haiku-4-5-20251001": {"costUSD": 0.001002, "outputTokens": 14},
        "claude-opus-5": {"costUSD": 0.34, "outputTokens": 136},
    }}
    session = session_with_pending()
    await session._handle({"type": "assistant", "parent_tool_use_id": None,
                           "message": {"model": "claude-opus-5",
                                       "content": []}})
    await session._handle(envelope)
    check("the assistant message decides, not modelUsage",
          session.turns[0]["model"], "claude-opus-5")

    session = session_with_pending()
    await session._handle(envelope)
    check("with no assistant message, null rather than a pick from modelUsage",
          session.turns[0]["model"], None)
    check("...and model is a key on the record either way",
          "model" in session.turns[0], True)


root = Path(tempfile.mkdtemp(prefix="loam-broker-test-"))
saved_session_dir = cfg.SESSION_DIR
saved_ai_labs = cfg.AI_LABS
try:
    # Sessions are written under a temporary directory: this test must never
    # touch the box's real session logs, and Session.__init__ reads any
    # record it finds for the id it was given.
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
