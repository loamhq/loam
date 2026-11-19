#!/usr/bin/env python3
"""Preserve provider compaction boundaries in the event log and stream."""

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
import loam_broker as cfg  # noqa: E402
# Use fixture memory to isolate the broker from machine load.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_transcript  # noqa: E402

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
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


# The CLI's own marker, as the probe's stream carried it (uuids trimmed).
BOUNDARY = {
    "type": "system", "subtype": "compact_boundary",
    "session_id": "3f0c1f5e-8c1a-4b7e-9a51-0c0ffee00001",
    "uuid": "60cfaa23-1aac-43f8-9f26-9cf3c9ffb6a0",
    "compact_metadata": {
        "trigger": "manual", "pre_tokens": 22230, "post_tokens": 1810,
        "cumulative_dropped_tokens": 20420, "duration_ms": 9306},
    "logical_parent_uuid": "a9754ce0-8b5d-45cd-8586-259aab991a2e",
}


class FakeStdin:
    def __init__(self) -> None:
        self.lines: list[dict] = []

    def write(self, data: bytes) -> None:
        self.lines.append(json.loads(data.decode()))

    async def drain(self) -> None:
        return None


class FakeStream:
    async def readline(self) -> bytes:
        return b""


class FakeProc:
    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.stdout = FakeStream()
        self.stderr = FakeStream()
        self.returncode = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


def running(broker, agent="probe"):
    session = broker.adopt(broker_mod.Session(agent))
    session.state = "running"
    session.proc = FakeProc()
    broker.sessions[session.id] = session
    return session


def logged(session) -> list[dict]:
    return [json.loads(line) for line in
            session.log_path.read_text(encoding="utf-8").splitlines()]


LAUNCHES: list[dict] = []


async def main() -> None:
    broker = broker_mod.Broker()

    # -- 1. the window ------------------------------------------------------
    check("the broker has no window of its own",
          hasattr(cfg, "COMPACT_WINDOW"), False)
    # So an inherited variable in whatever runs this cannot pass for one the
    # broker set.
    os.environ.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)
    started = await broker.start_session("probe", provider="claude-code")
    record = started.meta()
    check("a session starts", started.state, "running")
    env = LAUNCHES[-1]["env"]
    check("...and its process was launched without the variable",
          "CLAUDE_CODE_AUTO_COMPACT_WINDOW" in env, False)
    check("...and the record carries no compact_window",
          "compact_window" in record, False)
    await started.stop()

    # -- 2. the divider ------------------------------------------------------
    live = running(broker)
    await live._handle(dict(BOUNDARY))
    log = logged(live)
    check("the CLI's marker is in the log as it came",
          [e.get("subtype") for e in log][-2], "compact_boundary")
    entry = log[-1]
    check("...and a broker.compaction follows it",
          entry["type"], "broker.compaction")
    check("...carrying the marker's numbers under the page's names",
          (entry["trigger"], entry["tokens_before"], entry["tokens_after"],
           entry["duration_ms"]), ("manual", 22230, 1810, 9306))
    check("...with a seq of its own, the one after the marker's",
          entry["seq"], log[-2]["seq"] + 1)
    check("...and a time", isinstance(entry["at"], str) and entry["at"]
          .endswith("Z"), True)
    check("...naming this record, not the CLI's id",
          entry["session_id"], live.id)

    await live._handle({"type": "system", "subtype": "compact_boundary"})
    bare = logged(live)[-1]
    check("a marker with no metadata gives nulls, not guesses",
          (bare["trigger"], bare["tokens_before"], bare["tokens_after"]),
          (None, None, None))
    check("the marker alone writes no turn record", live.turns, [])

    # A compaction's own turn: the marker, then the init the CLI says after
    # it, then a result with no assistant message before it. Its context is
    # the marker's post_tokens in the window the init named, read out of the
    # envelope's modelUsage as any turn's is.
    await live._handle({"type": "system", "subtype": "init",
                        "model": "claude-fable-5-1"})
    await live._handle(dict(BOUNDARY))
    await live._handle({"type": "result", "subtype": "success",
                        "is_error": False, "session_id": live.id,
                        "modelUsage": {"claude-fable-5-1":
                                       {"contextWindow": 1000000}}})
    check("a compaction's turn reads the marker's post_tokens as its context",
          live.turns[-1]["context"], {"used": 1810, "window": 1000000})
    check("...and the turn names no model, because no message did",
          live.turns[-1]["model"], None)
    await live._handle({"type": "result", "subtype": "success",
                        "is_error": False, "session_id": live.id})
    check("...and the turn after it does not inherit the figure",
          live.turns[-1]["context"], {"used": None, "window": None})


def transcript_check() -> None:
    # -- 3. the transcript writer -------------------------------------------
    turns = loam_transcript.parse_session(
        AGENTS / "fixtures" / "compacted-session.jsonl", "probe")
    said = [t.content for t in turns if t.speaker == "user"]
    check("the person's five messages are the only user turns",
          said, ["Say the word apple.", "Now say banana.", "Now say cherry.",
                 "List the three fruits you said.",
                 "What fruits did you say before?"])
    check("...so the summary, /compact and 'Compacted' are none of them",
          any("Compacted" in t.content or "continued from a previous" in
              t.content or "/compact" in t.content for t in turns), False)


root = Path(tempfile.mkdtemp(prefix="loam-broker-compact-test-"))
saved = {name: getattr(cfg, name) for name in
         ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "USAGE_PATH", "AI_LABS",
          "CREDENTIALS_PATH")}
saved_exec = (asyncio.subprocess.create_subprocess_exec,
              asyncio.create_subprocess_exec)
try:
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.USAGE_PATH = cfg.STATE_DIR / "provider-usage.json"
    cfg.AI_LABS = root / "agents"
    cfg.CREDENTIALS_PATH = root / "credentials.json"
    (cfg.AI_LABS / "probe" / ".claude").mkdir(parents=True)
    (cfg.AI_LABS / "probe" / "agent.json").write_text("{}", encoding="utf-8")
    (cfg.AI_LABS / "probe" / ".claude" / "settings.json").write_text(
        json.dumps({"model": "fable"}), encoding="utf-8")

    async def no_process(*argv, **kwargs):
        LAUNCHES.append({"argv": argv, "env": kwargs.get("env") or {}})
        return FakeProc()
    asyncio.subprocess.create_subprocess_exec = no_process
    asyncio.create_subprocess_exec = no_process

    asyncio.run(main())
    transcript_check()
finally:
    for name, value in saved.items():
        setattr(cfg, name, value)
    (asyncio.subprocess.create_subprocess_exec,
     asyncio.create_subprocess_exec) = saved_exec
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
