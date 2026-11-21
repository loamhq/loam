#!/usr/bin/env python3
"""Require positive evidence of idle before restarting. Defer at the ceiling when a
session is busy or state cannot be read.

Run: python3 tests/test_restart_ceiling.py
"""

from __future__ import annotations

import contextlib
import functools
import importlib.machinery
import importlib.util
import io
import sys
from pathlib import Path
from unittest.mock import patch

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


def row(agent, state, **kw):
    return {"agent": agent, "id": agent[0] * 10, "state": state, **kw}


class World:
    """A fake clock, a fake live list and a fake lock and restart, which
    record what they were asked to do."""

    def __init__(self, live_at):
        self.t = 0.0
        self.live_at = live_at          # (t, locked) -> {"sessions", "turns"} | None
        self.reads = 0
        self.locked = False
        self.events = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def read(self):
        self.reads += 1
        live = self.live_at(self.t, self.locked)
        if isinstance(live, Exception):
            raise live
        if live is None:
            return loam_live.restart_blockers(None)
        return loam_live.restart_blockers(live.get("sessions"), live.get("turns"))

    @contextlib.contextmanager
    def lock(self):
        self.events.append(("lock", self.t))
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def restart(self):
        self.events.append(("restart", self.t, self.reads))
        return 0


def run(world, argv):
    wait = functools.partial(rb.wait_idle, read=world.read, clock=world.clock,
                             sleep=world.sleep)
    # Exercise the combined broker with the fake restart even on a split host.
    with patch("loam_units.split_marker") as marker, contextlib.redirect_stdout(io.StringIO()) as out:
        marker.return_value.exists.return_value = False
        rc = rb.main(argv, wait=lambda *a: wait(*a), lock=world.lock,
                     restart=world.restart, clock=world.clock)
    return rc, out.getvalue()


def restarted(world):
    return any(e[0] == "restart" for e in world.events)


WHEN_IDLE = ["--when-idle", "--poll", "20", "--ceiling", "1800"]

# -- 1. busy past the ceiling ------------------------------------------------
stuck = World(lambda t, locked: {"sessions": [row("ada", "working",
                                                  last_event_at="2026-11-16T07:00:00Z")],
                                 "turns": [{"thread_id": "t-1a2b3c4d", "persona": "ada"}]})
rc, out = run(stuck, WHEN_IDLE)
check("1. a silent working turn past the ceiling: deferred (75)", rc, 75)
check("...the restart was never called", restarted(stuck), False)
check("...it gave up at the ceiling, not before", stuck.t, 1800.0)
check("...and says what still holds it",
      "deferred" in out and "1800 s ceiling" in out and "ada/aaaaaaaa working" in out, True)
check("...the lock was never taken", stuck.events, [])

asking = World(lambda t, locked: {"sessions": [row("pax", "asking")], "turns": []})
rc, _ = run(asking, WHEN_IDLE)
check("1. an unanswered question past the ceiling: deferred, not restarted",
      (rc, restarted(asking)), (75, False))

# Busy until t=2400, idle after: no restart at the ceiling; a second run
# (the caller trying again later) goes once it is idle.
late = World(lambda t, locked: {"sessions": [row("ada", "working" if t < 2400 else "idle")],
                                "turns": []})
check("1. busy at the ceiling, idle later: the first run defers",
      (run(late, WHEN_IDLE)[0], restarted(late)), (75, False))
late.t = 2400
check("...and the run that tries again later restarts", (run(late, WHEN_IDLE)[0],
                                                         restarted(late)), (0, True))

check("--max-wait ends the wait the same way: deferred, not restarted",
      run(World(stuck.live_at), ["--when-idle", "--ceiling", "0", "--max-wait", "600"])[0], 75)

# -- 2. waiting on an engineer job -------------------------------------------
eng = World(lambda t, locked: {"sessions": [row("ada", "working"), row("iris", "idle")],
                               "turns": [{"thread_id": "t-1a2b3c4d", "persona": "ada"}]})
rc, out = run(eng, WHEN_IDLE)
check("2. a turn waiting on an engineer call (working, silent): deferred, not restarted",
      (rc, restarted(eng)), (75, False))
check("...named twice: the session and its thread turn",
      "ada/aaaaaaaa working" in out and "ada's turn in t-1a2b3c4d in flight" in out, True)

bg = World(lambda t, locked: {"sessions": [row("juno", "background")], "turns": []})
check("2. a session waiting on background work (a job, a subagent) holds it",
      (run(bg, WHEN_IDLE)[0], restarted(bg)), (75, False))

queued = World(lambda t, locked: {"sessions": [row("iris", "idle")],
                                  "turns": [{"thread_id": "t-1", "persona": "iris",
                                             "queued": True}]})
rc, out = run(queued, WHEN_IDLE)
check("2. an idle session with a thread turn queued for it holds it",
      (rc, restarted(queued), "iris's turn in t-1 queued" in out), (75, False, True))

# -- 3. failed or unknown status is not idle ---------------------------------
for label, live in (("None (the read failed)", None),
                    ("a reader that raises", OSError("connection refused")),
                    ("a row with no state", {"sessions": [{"agent": "x", "id": "1"}], "turns": []}),
                    ("a state the rule does not know", {"sessions": [row("x", "paused")], "turns": []}),
                    ("a row that is not a row", {"sessions": ["ada"], "turns": []}),
                    ("sessions not a list", {"sessions": {"ada": "idle"}, "turns": []}),
                    ("turns not a list", {"sessions": [], "turns": "?"})):
    w = World(lambda t, locked, live=live: live)
    rc, out = run(w, WHEN_IDLE)
    check(f"3. {label}: deferred, not restarted, said busy",
          (rc, restarted(w), "counted as busy" in out), (75, False, True))

# A provider can be idle between two messages in the same conversation.
from datetime import datetime, timezone
now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
resident = row("ada", "idle", kind="thread", idle_since="2026-10-01T11:50:00Z")
snapshot = {"sessions": [resident], "turns": [], "opening": 0}
def allows(view):
    return not loam_live.planned_restart_blockers(view, now)
check("thread's full idle window elapsed", allows(snapshot), True)
for field, value in (("idle_since", "2026-10-01T11:59:59Z"),
                     ("last_event_at", "2026-10-01T11:59:57Z"),
                     ("idle_since", None), ("kind", "classic")):
    check("recent or unreadable resident holds: " + field,
          allows({**snapshot, "sessions": [{**resident, field: value}]}), False)
for field, value in (("opening", 1), ("opening", None), ("turns", None), ("sessions", None)):
    check("partial or busy live response holds: " + field, allows({**snapshot, field: value}), False)

# -- 4. all idle --------------------------------------------------------------
idle = World(lambda t, locked: {"sessions": [row("ada", "idle"), row("iris", "idle")],
                                "turns": []})
rc, out = run(idle, WHEN_IDLE)
check("4. all idle: restarted (0)", (rc, restarted(idle)), (0, True))
check("...after three clear reads, the lock, three more, then the restart",
      idle.events, [("lock", 40.0), ("restart", 80.0, 6)])
quiet = World(lambda t, locked: {"sessions": [], "turns": []})
check("4. an empty box (no sessions, no turns) is idle", run(quiet, WHEN_IDLE)[0], 0)
old = World(lambda t, locked: {"sessions": [row("iris", "idle")]})
check("4. a broker older than `turns`: the rows alone decide", run(old, WHEN_IDLE)[0], 0)

# One busy read among clear ones starts the count again.
blip = World(lambda t, locked: {"sessions": [row("iris", "working" if t == 20 else "idle")],
                                "turns": []})
run(blip, WHEN_IDLE)
check("4. a busy read between clear ones starts the count again",
      blip.events[0], ("lock", 80.0))

# -- 5. stale state at the final decision -------------------------------------
stale = World(lambda t, locked: {"sessions": [row("ada", "working" if locked else "idle")],
                                 "turns": []})
rc, out = run(stale, WHEN_IDLE)
check("5. idle before the lock, busy under it: deferred, not restarted",
      (rc, restarted(stale)), (75, False))
check("...and it did not sit on the lock past a few reads",
      stale.t - stale.events[0][1] <= rb.UNDER_LOCK_READS * 20, True)
turn_late = World(lambda t, locked: {"sessions": [row("ada", "idle")],
                                     "turns": [{"thread_id": "t-2", "persona": "ada"}]
                                     if locked and t >= 80 else []})
check("5. a turn taken on the last read under the lock: deferred, not restarted",
      (run(turn_late, WHEN_IDLE)[0], restarted(turn_late)), (75, False))
late_lock = World(lambda t, locked: {"sessions": [row("ada", "idle")], "turns": []})


@contextlib.contextmanager
def slow_lock():
    late_lock.t += 3600                 # a landing held the lock for an hour
    late_lock.events.append(("lock", late_lock.t))
    yield


wait = functools.partial(rb.wait_idle, read=late_lock.read, clock=late_lock.clock,
                         sleep=late_lock.sleep)
with contextlib.redirect_stdout(io.StringIO()):
    rc = rb.main(WHEN_IDLE, wait=lambda *a: wait(*a), lock=slow_lock,
                 restart=late_lock.restart, clock=late_lock.clock)
check("...an hour behind a landing, still idle: restarted after fresh reads",
      (rc, late_lock.events[-1][0], late_lock.reads), (0, "restart", 6))

# -- 6. the explicit emergency restart ----------------------------------------
busy = World(lambda t, locked: {"sessions": [row("ada", "working")],
                                "turns": [{"thread_id": "t-1", "persona": "ada"}]})
rc, _ = run(busy, [])
check("6. no --when-idle: restarted under the lock, whatever is running",
      (rc, busy.events), (0, [("lock", 0.0), ("restart", 0.0, 0)]))
check("...and nothing was read", busy.reads, 0)


class Fail(World):
    def restart(self):
        self.events.append(("restart", self.t, self.reads))
        return 5


f = Fail(busy.live_at)
check("6. its exit code is systemctl's", run(f, [])[0], 5)

# -- the rule itself -----------------------------------------------------------
check("only `idle` is idle", loam_live.restart_blockers(
    [row("a", "idle"), row("b", "background"), row("c", "working"), row("d", "asking")]),
    ["b/bbbbbbbb background", "c/cccccccc working", "d/dddddddd asking"])
check("a failed read holds it", bool(loam_live.restart_blockers(None)), True)

print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
