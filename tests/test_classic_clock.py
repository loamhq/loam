#!/usr/bin/env python3
"""Verify the clock line in the provider prompt and the words stored in the session
journal.

Run: python3 tests/test_classic_clock.py"""

import asyncio
import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
# Use fixture admission so available host memory cannot change the result.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_clock  # noqa: E402
import loam_transcript as lt  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results = []


def check(label, got, want):
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


class Stdin:
    def __init__(self):
        self.lines = []

    def write(self, data):
        self.lines.append(json.loads(data.decode()))

    async def drain(self):
        return None


class Proc:
    def __init__(self):
        self.stdin = Stdin()
        self.returncode = None
        self.pid = 0


def running(agent, sid):
    s = broker_mod.Session(agent, sid)
    s.state, s.proc = "running", Proc()
    return s


async def main():
    monday = datetime(2026, 11, 9, 18, 25, tzinfo=timezone.utc)
    check("the sentence: weekday, date, time and zone",
          loam_clock.sentence(monday),
          "It is now Monday, 2026-11-09, 10:25 AM PST (America/Los_Angeles).")

    s = running("probe1", "clock-1")
    await s.send_message("What day is it?")
    sent = s.proc.stdin.lines[-1]["message"]["content"]
    today = datetime.now(timezone.utc).astimezone(loam_clock.PACIFIC)
    check("the harness gets the clock first, then the person's text",
          (sent.startswith("<loam-clock>It is now " + today.strftime("%A")),
           sent.endswith("\n\nWhat day is it?")), (True, True))
    logged = [json.loads(l) for l in s.log_path.read_text().splitlines()]
    check("...while the log keeps exactly what was typed",
          [e["text"] for e in logged if e["type"] == "broker.user_message"],
          ["What day is it?"])

    s._turn_active = False
    await s.send_message("/context")
    check("a slash command goes to the harness as typed",
          s.proc.stdin.lines[-1]["message"]["content"], "/context")

    t = running("probe1", "clock-2")
    t.tell_time = False
    await t.send_message("thread prompt")
    check("a thread session's prompt is sent with no second clock",
          t.proc.stdin.lines[-1]["message"]["content"], "thread prompt")

    stamped = loam_clock.stamp("Hello there", monday)
    check("the transcript writer reads the person's words without the line",
          lt.person_text(stamped), "Hello there")
    check("...and the adoption replay's strip does the same",
          loam_clock.strip(stamped), "Hello there")
    check("...and leaves a message with no line untouched",
          loam_clock.strip("Plain\n\ntext"), "Plain\n\ntext")


root = Path(tempfile.mkdtemp(prefix="loam-classic-clock-test-"))
saved = {n: getattr(cfg, n) for n in ("STATE_DIR", "SESSION_DIR", "AI_LABS")}
try:
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.AI_LABS = root / "agents"
    (cfg.AI_LABS / "probe1").mkdir(parents=True)
    (cfg.AI_LABS / "probe1" / "agent.json").write_text("{}")
    asyncio.run(main())
finally:
    for n, v in saved.items():
        setattr(cfg, n, v)
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
