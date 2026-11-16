#!/usr/bin/env python3
"""bin/restart-broker-report reads a stop out of the journal and says so.

Since 2026-10-13, when a scheduled restart was armed to report into the
vault with nobody watching. The fixture is the 10-09 16:29Z stop in shape:
Stopping, the 90 s stop-sigterm timeout, two SIGKILLs, Started -- and a
second stop after it that must not be folded in. A clean stop reports no
SIGKILL; a restart that failed says it did not complete.

Run: python3 tests/test_restart_broker_report.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader(
    "restart_broker_report", str(ROOT / "bin" / "restart-broker-report"))
spec = importlib.util.spec_from_loader(loader.name, loader)
rbr = importlib.util.module_from_spec(spec)
loader.exec_module(rbr)

T0 = datetime(2026, 10, 9, 16, 29, 31, 1000, tzinfo=timezone.utc)
UNIT = "loam-session-broker.service"


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def check(name: str, ok: bool) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return ok


def main() -> int:
    killed = [
        (at(0), "Stopping loam-session-broker.service - Loam session broker"),
        (at(20), "shutdown: SIGTERM 20.0s ago and the broker has not exited; "
                 "sessions, threads and tasks follow"),
        (at(90), f"{UNIT}: State 'stop-sigterm' timed out. Killing."),
        (at(90), f"{UNIT}: Killing process 1640373 (python3) with signal SIGKILL."),
        (at(90.1), "Started loam-session-broker.service - Loam session broker"),
        (at(300), "Stopping loam-session-broker.service - Loam session broker"),
        (at(301), "Started loam-session-broker.service - Loam session broker"),
    ]
    stop = rbr.read_stop(killed)
    clean = rbr.read_stop([
        (at(0), "Stopping loam-session-broker.service - Loam session broker"),
        (at(1.2), "Started loam-session-broker.service - Loam session broker"),
    ])
    common = dict(fired=at(-1), before={"MainPID": "1"}, stop=stop, chats=[],
                  commit="abc1234 x")
    mem = {"at": "2026-10-15T05:01:00Z", "current": 420 * rbr.MIB,
           "peak": 610 * rbr.MIB, "high": 1800 * rbr.MIB, "max": "max",
           "high_events": 0, "procs": [("2", "python3", 380 * rbr.MIB),
                                       ("3", "claude", 40 * rbr.MIB)]}
    good = rbr.render("[[Ada]]", after={"MainPID": "2", "ActiveState": "active"},
                      code=0, error="", probed=(True, "HTTP 200"), mem=mem,
                      **common)
    failed = rbr.render("[[Ada]]", after={"MainPID": "0", "ActiveState": "failed"},
                        code=1, error="", probed=None, **common)

    results = [
        check("the first stop's bracket is read", stop["stopping"] == at(0)
              and stop["started"] == at(90.1)),
        check("SIGKILL is seen and quoted", stop["sigkill"]
              and len(stop["kill_lines"]) == 2),
        check("the shutdown dump is seen", stop["dump"]),
        check("the report says where the dump is",
              f"--since @{int(at(20).timestamp())} "
              f"--until @{int(at(90.1).timestamp()) + 1}" in good
              and "read it with" not in rbr.render(
                  "[[Ada]]", after={"MainPID": "2", "ActiveState": "active"},
                  code=0, error="", probed=None,
                  **{**common, "stop": clean})),
        check("a clean stop has no SIGKILL", clean["sigkill"] is False
              and clean["started"] == at(1.2)),
        check("a good restart says it went through",
              "The restart went through" in good and "SIGKILL: yes" in good),
        check("provenance is the brief's", 'created_by: "[[Ada]]"' in good),
        check("the chats it ended are counted",
              "## The chats it ended\n\nNone." in good
              and "2 chats:" in rbr.render(
                  "[[Ada]]", after={"MainPID": "2", "ActiveState": "active"},
                  code=0, error="", probed=None,
                  **(common | {"chats": [
                      {"title": t, "session_id": "abcdef0123", "ended": "x"}
                      for t in ("one", "two")]}))),
        check("a failed restart says so",
              "did not complete cleanly" in failed and "not tried" in failed),
        check("memory a minute in is reported",
              "Current: 420 MiB; peak since the start: 610 MiB" in good
              and "MemoryMax: no limit" in good and "claude (3) 40 MiB" in good
              and "Not read (the restart did not finish)." in failed),
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
