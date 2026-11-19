#!/usr/bin/env python3
"""Exercise memory admission, burst accounting, lock order and stale-record cleanup."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_lock  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:58} {got!r}"[:200]
          + ("" if ok else f"   want {want!r}"))


root = Path(tempfile.mkdtemp(prefix="loam-admission-"))
saved = (loam_lock.LOCK_DIR, loam_lock.MEMINFO)
meminfo = root / "meminfo"


def box(available_mb: int) -> None:
    meminfo.write_text(f"MemTotal: {64 * 1024 * 1024} kB\n"
                       f"MemAvailable: {available_mb * 1024} kB\n")


try:
    loam_lock.LOCK_DIR = root / "locks"
    loam_lock.MEMINFO = str(meminfo)

    import loam_broker  # noqa: E402,PLC0415
    check("the reserve is the broker's", loam_lock.MEMORY_RESERVE_MB,
          loam_broker.MEMORY_RESERVE_MB)
    check("the start size is the broker's", loam_lock.RUN_START_MB,
          loam_broker.SESSION_START_MB)
    check("no slot count is left", hasattr(loam_lock, "GLOBAL_SLOTS"), False)

    reserve, need = loam_lock.MEMORY_RESERVE_MB, loam_lock.RUN_START_MB

    box(reserve + need * 20)
    held = []
    for n in range(8):
        cm = loam_lock.hold_slot("probe", f"job:probe-{n}", wait_seconds=0)
        cm.__enter__()
        held.append(cm)
    check("no count: eight runs admitted at once",
          len(loam_lock.running()), 8)
    check("each run has a record naming its lane",
          sorted(h["lane"] for h in loam_lock.running()),
          sorted(f"job:probe-{n}" for n in range(8)))
    check("in flight: the eight count against the next",
          loam_lock.room()["starting"], 8)
    for cm in held:
        cm.__exit__(None, None, None)
    check("release: every record is gone",
          list(loam_lock.LOCK_DIR.glob("run-*.lock")), [])

    # In flight: room for one start and not two on one reading (the rule
    # admits while what is left after the start is at least the reserve).
    box(reserve + need * 2 - 1)
    a = loam_lock.hold_slot("probe", "job:a", wait_seconds=0)
    a.__enter__()
    check("in flight: the first of a burst is admitted",
          loam_lock.room()["starting"], 1)
    try:
        b = loam_lock.hold_slot("probe", "job:b", wait_seconds=0)
        b.__enter__()
        refused = None
    except loam_lock.Busy as exc:
        refused = str(exc)
        b = None
    check("in flight: the second, on the same reading, is not",
          refused is not None and "still starting" in refused, True)
    print("      refusal:", refused)
    if b is not None:
        b.__exit__(None, None, None)
    # Past the grace, the first no longer counts as starting.
    real_grace = loam_lock.ADMIT_GRACE_S
    loam_lock.ADMIT_GRACE_S = 0
    try:
        check("past the grace, a running run no longer counts",
              loam_lock.room()["fits"], True)
    finally:
        loam_lock.ADMIT_GRACE_S = real_grace
    a.__exit__(None, None, None)

    box(reserve - 1)
    r = loam_lock.room()
    check("no room: below the reserve does not fit", r["fits"], False)
    try:
        with loam_lock.hold_slot("probe", "job:c", wait_seconds=0.2, poll=0.05):
            pass
        refused = None
    except loam_lock.Busy as exc:
        refused = str(exc)
    check("no room: Busy, with the box's numbers",
          refused is not None and "MB available" in refused, True)

    loam_lock.MEMINFO = str(root / "absent")
    check("unreadable meminfo admits", loam_lock.room()["fits"], True)
    loam_lock.MEMINFO = str(meminfo)

    # A dead holder's record is swept by the next admission.
    box(reserve + need * 20)
    stale = loam_lock.LOCK_DIR / "run-999999-1.lock"
    stale.write_text('{"agent": "gone", "lane": "job:gone", "pid": 999999}')
    check("a dead holder is not running", loam_lock.running(), [])
    with loam_lock.hold_slot("probe", "job:d", wait_seconds=0):
        check("the next admission swept the dead holder's record",
              stale.exists(), False)

    with loam_lock.hold("probeagent", "reflection", wait_seconds=0):
        try:
            with loam_lock.hold("probeagent", "cron:x", wait_seconds=0.2, poll=0.05):
                pass
            refused = None
        except loam_lock.Busy as exc:
            refused = str(exc)
    check("the agent lock: a second run of one agent is refused for it",
          refused is not None and "run lock" in refused, True)
    check("nothing is left running", loam_lock.running(), [])
finally:
    loam_lock.LOCK_DIR, loam_lock.MEMINFO = saved
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'all passed' if not fails else f'{fails} failed'}")
sys.exit(1 if fails else 0)
