#!/usr/bin/env python3
"""bin/restart-broker --when-idle has a ceiling (2026-11-16).

On 11-12 a restart that waited for idle looped for over an hour. The wait
is now bounded: before `--ceiling` the rule is today's (a `working` or
`asking` session holds it, three clear reads); past it, only a session that
produced output in the last `loam_live.RESTART_OUTPUT_S` holds it, and one
clear read is the quiet moment it goes at. Checked on a fake clock and a
fake live list; nothing here reads the broker or restarts anything.

  1. A turn stuck `working` with no output holds the restart until the
     ceiling, and not after it.
  2. A session producing output past the ceiling still holds it, until it
     stops.
  3. `--ceiling 0` is today's rule with no limit (`--max-wait` ends it).
  4. The second wait, under the live lock, counts the ceiling from the
     first wait's start.

Run: python3 tests/test_restart_ceiling.py
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "lib"))
import loam_live  # noqa: E402

loader = importlib.machinery.SourceFileLoader("restart_broker", str(ROOT / "bin" / "restart-broker"))
rb = importlib.util.module_from_spec(importlib.util.spec_from_loader("restart_broker", loader))
loader.exec_module(rb)

fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"  | got={got!r} want={want!r}"))


class World:
    """A fake clock that is also the wall clock the rows are stamped by."""

    def __init__(self, rows_at):
        self.t = 0.0
        self.rows_at = rows_at          # t -> rows
        self.reads = 0

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def read(self):
        self.reads += 1
        return self.rows_at(self.t)


EPOCH = 1_800_000_000.0


def iso(t):
    return datetime.fromtimestamp(EPOCH + t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(world, **kw):
    # The rows' stamps are wall-clock; restart_blockers reads time.time()
    # past the ceiling, so it is pointed at the fake clock.
    import time as real_time
    real = real_time.time
    real_time.time = lambda: EPOCH + world.t
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            ok = rb.wait_idle(poll=20, read=world.read, clock=world.clock,
                              sleep=world.sleep, **kw)
    finally:
        real_time.time = real
    return ok, world.t, out.getvalue()


# 1. A turn stuck `working`, silent since t=0.
stuck = World(lambda t: [{"agent": "ada", "id": "a" * 10, "state": "working",
                          "last_active": iso(0), "last_event_at": iso(0)}])
ok, at, out = run(stuck, max_wait=None, ceiling=1800)
check("a silent stuck turn: restarts, at the ceiling", (ok, at), (True, 1800))
check("...and says it went past the ceiling", "past the 1800 s ceiling" in out, True)

# An unanswered question is the same.
asking = World(lambda t: [{"agent": "pax", "id": "p" * 10, "state": "asking",
                           "last_active": iso(0), "last_event_at": iso(0)}])
check("an unanswered question: restarts at the ceiling",
      run(asking, max_wait=None, ceiling=1800)[:2], (True, 1800))

# 2. Output every poll until t=2400, then silence.
talking = World(lambda t: [{"agent": "ada", "id": "a" * 10, "state": "working",
                            "last_active": iso(0),
                            "last_event_at": iso(min(t, 2400))}])
ok, at, _ = run(talking, max_wait=None, ceiling=1800)
check("output past the ceiling holds it until a read with none in the last minute",
      (ok, 2400 + loam_live.RESTART_OUTPUT_S <= at <= 2400 + loam_live.RESTART_OUTPUT_S + 20),
      (True, True))

# Before the ceiling a quiet box still takes three clear reads.
quiet = World(lambda t: [{"agent": "iris", "id": "i" * 10, "state": "background"}])
check("a quiet box: three clear reads, today's rule", run(quiet, max_wait=None, ceiling=1800)[:2],
      (True, 40))

# 3. No ceiling: the stuck turn holds it until --max-wait gives up.
stuck2 = World(stuck.rows_at)
check("--ceiling 0: held, and --max-wait ends it without a restart",
      run(stuck2, max_wait=3600, ceiling=0)[:2], (False, 3600))

# 4. The wait under the lock counts from the first wait's start.
stuck3 = World(stuck.rows_at)
stuck3.t = 1700
check("the second wait counts the ceiling from the first's start",
      run(stuck3, max_wait=None, ceiling=1800, started=0)[:2], (True, 1800))

# A failed read is busy, past the ceiling too.
gone = World(lambda t: None)
check("a broker that cannot be read holds it past the ceiling",
      run(gone, max_wait=4000, ceiling=1800)[0], False)

print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
