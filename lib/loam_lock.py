"""One run per agent at a time, and admission to the box by its memory.

Until Move 8 the grid was a fixed nightly line and nothing enforced it. Seven
reflection wakes twenty minutes apart never overlapped **because of the
spacing**, not because anything stopped them. Adding cron lanes removed the
spacing as a guarantee, so the invariant moved out of the timetable and into
the launcher. Two things, in this order, held for the whole run:

  ``agent-<name>.lock``   one run per agent. Reflection and cron alike; the
                          lane does not matter, the agent does. Two sessions
                          of one agent writing the same workspace files is
                          the failure this exists for, and it is not a memory
                          problem — it is two writers with no merge.
  ``run-<pid>-<n>.lock``  a run's place on the box: its record, held for the
                          run, naming who it is. **Admitted by memory, not by
                          count, since 2026-11-10** (the owner, that day: "why
                          do we have limits on the number of engineering
                          tasks?"). Until then this was one of
                          ``GLOBAL_SLOTS`` interchangeable slot files -- 2 on
                          the 3.8 GB box, 3 from 2026-09-07 -- and a fourth
                          run waited whatever the box had free. The rule is
                          the session broker's since 2026-11-09
                          (lib/loam_broker.py, ``memory()``): a run starts
                          while MemAvailable, less what the runs admitted in
                          the last ``ADMIT_GRACE_S`` will still take, leaves
                          ``MEMORY_RESERVE_MB`` free after ``RUN_START_MB``
                          for the new one. What bounds a run once it is going
                          is its unit's own memory cap, not this.

**Agent lock first, admission second, and that order is not arbitrary.** A
run waiting for memory already holds its agent lock, so two runs of one agent
can never wait on each other.

A launch that cannot get its agent lock or its memory **waits**, up to
``WAIT_SECONDS``, and then gives up (a wake or a cron; an engineer job queues
for memory with no deadline, bin/engineer-job ``take_slot``). It never kills
the holder: the holder is a live session mid-write, and the cost of a skipped
run is one missed night, while the cost of a killed one is a half-written
workspace. The give-up is recorded by the caller through ``lib/loam_inbox``,
so a skipped run lands where the owner already looks, naming what it waited on.

The lock file carries its holder's identity, so a waiter can say who it is
waiting for. That record is advisory — it is read without the lock and a
holder killed by the kernel leaves its line behind — so every reader here
treats it as a hint and validates the pid against /proc before believing it.

flock, not a pidfile: the kernel releases it when the process dies, however
it dies. That is the whole reason. A wake OOM-killed mid-run (which has
happened twice) must not leave a lock nobody can clear; a run record left
behind by such a run is swept by the next admission, which finds its flock
free.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import loam_config as lc

# LOAM_LOCK_DIR moves the whole lock table, for a landing's test run only
# (lib/loam_worktree.run_tests, since 2026-10-02): the suite takes real run
# slots and waits for them, and a landing runs while parallel jobs hold every
# slot -- caused, 2026-10-02, a Loam landing's suite stuck behind two live
# jobs and its own. Nothing in a unit or the installer sets it.
LOCK_DIR = Path(os.environ.get("LOAM_LOCK_DIR") or lc.STATE / "locks")

# Admission by memory (2026-11-10). The broker's numbers, restated here so a
# wake does not import the broker (tests/test_lock_admission.py holds the two
# equal): 2 GB or 12% of RAM kept free for everything that is not a run --
# 3,870 MB on the 32 GB cpx62 -- and what a session needs to start, the CLI
# and its stdio MCP servers, ~394-450 MB measured 2026-09-16 and 2026-10-14.
# A start is the one moment a size is assumed; after it, a run takes what it
# takes, with no ceiling (2026-11-16): the kernel's OOM order
# (lib/loam_config.py) is what stands if the box truly runs out.
def _box_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 7750


BOX_MB = _box_mb()
MEMORY_RESERVE_MB = max(2048, BOX_MB * 12 // 100)
RUN_START_MB = 450

# A run admitted this recently is still growing into its start size, and
# MemAvailable has not seen it yet: each such run counts RUN_START_MB against
# the next admission, so three jobs briefed in one second are not all
# admitted on one reading.
ADMIT_GRACE_S = 60

# LOAM_LOCK_MEMINFO points the admission at a file shaped like /proc/meminfo:
# a probe's simulated box, and nothing else sets it.
MEMINFO = os.environ.get("LOAM_LOCK_MEMINFO") or "/proc/meminfo"

# How long a launch waits before giving up. Twenty minutes is the grid's floor
# of clear air — the gap between two adjacent reflection wakes — so a run that
# is merely running long is waited out, and one that is genuinely stuck is not
# waited on forever.
#
# The environment override exists for probes, which cannot spend twenty
# minutes proving the give-up branch. The operative number is the default
# here; nothing in the units or the installer sets it.
WAIT_SECONDS = int(os.environ.get("LOAM_LOCK_WAIT_S", "1200"))

# How often a waiter re-tries. Cheap: an flock attempt is a syscall.
POLL_SECONDS = 5


class Busy(Exception):
    """The deadline passed with the lock still held. Always a skipped run."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_holder(path: Path) -> dict | None:
    """Who claims to hold this lock, or None if nobody credible does.

    Read without the lock, so a torn write is possible and a stale record is
    normal — a holder killed by the kernel never clears its own line. Both are
    handled by returning None rather than by trusting the file: this is only
    ever used to make a message more useful.
    """
    try:
        holder = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(holder, dict):
        return None
    pid = holder.get("pid")
    if isinstance(pid, int) and not Path(f"/proc/{pid}").exists():
        return None
    return holder


def describe(holder: dict | None) -> str:
    if not holder:
        return "a run that left no identity behind"
    return (
        f"{holder.get('agent', '?')}'s {holder.get('lane', '?')} lane "
        f"(pid {holder.get('pid', '?')}, since {holder.get('since', '?')})"
    )


def _try_take(path: Path, me: dict):
    """Take the lock without blocking, or return None. Stamps the holder."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return None
        raise
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(me))
    handle.flush()
    return handle


def _release(handle) -> None:
    """Drop the lock and the identity line with it.

    Truncating first matters: a released lock whose file still names its last
    holder would make the next waiter report someone who finished hours ago.
    """
    try:
        handle.seek(0)
        handle.truncate()
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        handle.close()


def _wait_for_one(path: Path, me: dict, deadline: float, poll: float):
    """Poll one lock until taken or the deadline passes. (handle, blocker)."""
    while True:
        handle = _try_take(path, me)
        if handle is not None:
            return handle, None
        blocker = _read_holder(path)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, blocker
        time.sleep(min(poll, remaining))


def available_mb() -> int | None:
    """The kernel's MemAvailable, in MB, or None when it cannot be read."""
    try:
        for line in Path(MEMINFO).read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _run_paths() -> list[Path]:
    try:
        return sorted(LOCK_DIR.glob("run-*.lock"))
    except OSError:
        return []


def _sweep() -> None:
    """Remove the record of every run whose holder is gone. Called under the
    admission lock only, so it never races a record being made."""
    for path in _run_paths():
        try:
            handle = path.open("a+", encoding="utf-8")
        except OSError:
            continue
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            continue          # held: a live run
        try:
            path.unlink(missing_ok=True)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def running() -> list[dict]:
    """Every run on the box now, oldest first: its holder record with its
    pid checked against /proc. What the status reads where it read slots."""
    out = []
    for path in _run_paths():
        holder = _read_holder(path)
        if holder:
            out.append(holder)
    return sorted(out, key=lambda h: (str(h.get("since") or ""),
                                      float(h.get("admitted") or 0)))


def room(now: float | None = None) -> dict:
    """The box as admission reads it, in MB: ``available`` (MemAvailable),
    ``starting`` (runs admitted inside ADMIT_GRACE_S, each counted at
    RUN_START_MB), ``reserve``, ``need`` (RUN_START_MB), ``running`` (how
    many runs there are) and ``fits``. Unreadable memory admits: the
    kernel's OOM order and each unit's cap still stand, and a box that
    cannot read /proc/meminfo has worse problems than a queue."""
    now = time.time() if now is None else now
    runs = running()
    starting = sum(1 for h in runs
                   if now - float(h.get("admitted") or 0) < ADMIT_GRACE_S)
    available = available_mb()
    fits = (available is None or
            available - starting * RUN_START_MB - RUN_START_MB >= MEMORY_RESERVE_MB)
    return {"available": available, "starting": starting,
            "reserve": MEMORY_RESERVE_MB, "need": RUN_START_MB,
            "running": len(runs), "fits": fits}


def short_text(r: dict) -> str:
    """Why a run cannot start, as a sentence: the box's numbers."""
    pending = (f", less {r['starting']} run(s) still starting at "
               f"{r['need']} MB each" if r.get("starting") else "")
    return (f"the box has {r.get('available')} MB available{pending}, and a "
            f"run needs {r['need']} MB to start with {r['reserve']} MB kept "
            f"free ({r.get('running', 0)} run(s) going)")


_COUNTER = [0]


def _try_admit(me: dict):
    """(handle, room): this run's record, held, if the box has room now;
    otherwise (None, room). Decided under one admission lock across the
    box, so two starts never read the same MemAvailable as room for both."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    with (LOCK_DIR / "admit.lock").open("a+", encoding="utf-8") as gate:
        fcntl.flock(gate.fileno(), fcntl.LOCK_EX)
        try:
            _sweep()
            r = room()
            if not r["fits"]:
                return None, r
            _COUNTER[0] += 1
            path = LOCK_DIR / f"run-{os.getpid()}-{_COUNTER[0]}.lock"
            handle = _try_take(path, {**me, "admitted": time.time()})
            if handle is None:       # cannot happen: the name is this pid's
                return None, r
            handle.loam_path = path  # type: ignore[attr-defined]
            return handle, r
        finally:
            fcntl.flock(gate.fileno(), fcntl.LOCK_UN)


def _leave(handle) -> None:
    """Drop a run record: truncate, unlock, and remove the file."""
    path = getattr(handle, "loam_path", None)
    _release(handle)
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _wait_for_room(me: dict, deadline: float, poll: float):
    """Poll admission until admitted or the deadline passes. (handle, room)."""
    while True:
        handle, r = _try_admit(me)
        if handle is not None:
            return handle, r
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, r
        time.sleep(min(poll, remaining))


@contextmanager
def hold(agent: str, lane: str, wait_seconds: float | None = None,
         poll: float = POLL_SECONDS, info: dict | None = None):
    """Hold this agent's run lock and the run's admission for the whole run.

    Raises ``Busy`` if either could not be had inside one shared deadline. The
    deadline is shared on purpose: the caller asked to wait twenty minutes to
    start, not twenty minutes per step. ``info`` (since 2026-11-12) is added
    to the holder record -- a wake's `engine` and `model` -- for the status.
    """
    wait = WAIT_SECONDS if wait_seconds is None else wait_seconds
    me = {"agent": agent, "lane": lane, "pid": os.getpid(), "since": now_iso(),
          **(info or {})}
    deadline = time.monotonic() + wait
    minutes = round(wait / 60, 1)

    agent_handle, blocker = _wait_for_one(
        LOCK_DIR / f"agent-{agent}.lock", me, deadline, poll)
    if agent_handle is None:
        raise Busy(
            f"{lane} waited {minutes} minutes for {agent}'s run lock and gave "
            f"up; it is held by {describe(blocker)}. One run per agent at a "
            f"time, so this run is skipped rather than overlapped."
        )
    try:
        run_handle, r = _wait_for_room(me, deadline, poll)
        if run_handle is None:
            raise Busy(
                f"{lane} waited {minutes} minutes for memory on this box and "
                f"gave up: {short_text(r)}. This run is skipped."
            )
        try:
            yield
        finally:
            _leave(run_handle)
    finally:
        _release(agent_handle)


@contextmanager
def hold_slot(agent: str, lane: str, wait_seconds: float | None = None,
              poll: float = POLL_SECONDS):
    """Admission and nothing else: an engineer job's since 2026-10-02 (the
    name is from when it was a slot). Raises ``Busy`` with the box's numbers
    when there is no room inside ``wait_seconds``.

    Until 2026-10-02 a job held its engineer's agent lock as well, because
    every job of a project edited the project's one checkout. Each job now
    edits its own worktree (lib/loam_worktree.py), so two jobs of one project
    are two writers with a merge between them, and the only thing left to
    ration is the box. What still runs one at a time is a step that changes
    live state, and that is ``live`` below, held for the step and not for the
    job.
    """
    wait = WAIT_SECONDS if wait_seconds is None else wait_seconds
    me = {"agent": agent, "lane": lane, "pid": os.getpid(), "since": now_iso()}
    handle, r = _wait_for_room(me, time.monotonic() + wait, poll)
    if handle is None:
        raise Busy(short_text(r))
    try:
        yield
    finally:
        _leave(handle)


# ---------------------------------------------------------------------------
# The live lock (since 2026-10-02). One per project, held only while a step
# that changes live state runs: landing a job's branch on `main`, `bin/deploy`,
# `bin/provision`, the generators that write the host's units and configs, the
# broker restart. A step that finds it held WAITS and says what it is waiting
# for -- it is never refused, because the holder is a step of minutes, not a
# job of an hour.
#
# Re-entrant across processes by an environment variable: a holder's children
# (provision running a generator, a landing running the test suite) inherit
# LOAM_LIVE_LOCKS and do not wait on their own parent, which flock alone would
# make them do forever.
# ---------------------------------------------------------------------------

LIVE_HELD_VAR = "LOAM_LIVE_LOCKS"


def live_lock_path(name: str) -> Path:
    return LOCK_DIR / f"live-{name}.lock"


def _say_waiting(name: str, holder: dict | None) -> None:
    import sys  # noqa: PLC0415
    print(f"waiting for the {name} live lock, held by {describe(holder)} -- "
          f"a step that changes live state runs one at a time",
          file=sys.stderr, flush=True)


@contextmanager
def live(name: str, lane: str, wait_seconds: float | None = None,
         poll: float = POLL_SECONDS, on_wait=_say_waiting):
    """Hold project `name`'s live lock for one step. ``on_wait(name, holder)``
    is called whenever the holder seen changes, so a waiter's status is
    always the current truth. Raises ``Busy`` after ``wait_seconds``."""
    held = [n for n in os.environ.get(LIVE_HELD_VAR, "").split(",") if n]
    if name in held:
        yield
        return
    wait = WAIT_SECONDS if wait_seconds is None else wait_seconds
    me = {"agent": os.environ.get("LOAM_WAKE") or name, "lane": lane,
          "pid": os.getpid(), "since": now_iso()}
    path = live_lock_path(name)
    # bin/deploy runs as root. A lock file root created would be one this
    # user can no longer open, so it is made first and given to the
    # directory's owner.
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    if os.geteuid() == 0:
        owner = LOCK_DIR.stat()
        os.chown(path, owner.st_uid, owner.st_gid)
    deadline = time.monotonic() + wait
    last_seen = object()
    while True:
        handle = _try_take(path, me)
        if handle is not None:
            break
        holder = _read_holder(path)
        if holder != last_seen:
            on_wait(name, holder)
            last_seen = holder
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Busy(
                f"{lane} waited {round(wait / 60, 1)} minutes for the {name} "
                f"live lock and gave up; it is held by {describe(holder)}.")
        time.sleep(min(poll, remaining))
    previous = os.environ.get(LIVE_HELD_VAR)
    os.environ[LIVE_HELD_VAR] = ",".join(held + [name])
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(LIVE_HELD_VAR, None)
        else:
            os.environ[LIVE_HELD_VAR] = previous
        _release(handle)
