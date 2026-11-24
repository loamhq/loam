"""Serialize writes to an agent's workspace and admit runs by available memory.

Wake and cron runs take the agent lock before waiting for admission. Jobs
have separate worktrees and need only admission. Both hold a run record
until exit. The kernel releases flocks on death, so the next admission can
reclaim abandoned records. Holder metadata is advisory."""

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
import loam_limits as limits

# Landing tests use a private table so their locks never contend with live runs.
LOCK_DIR = Path(os.environ.get("LOAM_LOCK_DIR") or lc.STATE / "locks")


def _box_mb() -> int:
    return limits.box_memory_mb()


# Keep the reserve and start size in sync with broker admission via loam_limits.
BOX_MB = _box_mb()
MEMORY_RESERVE_MB = limits.memory_reserve_mb(BOX_MB)
RUN_START_MB = limits.start_allowance_mb(BOX_MB)

# Charge recent starts until their footprint is visible in MemAvailable.
ADMIT_GRACE_S = 60

MEMINFO = os.environ.get("LOAM_LOCK_MEMINFO") or "/proc/meminfo"

WAIT_SECONDS = int(os.environ.get("LOAM_LOCK_WAIT_S", "1200"))

POLL_SECONDS = 5


class Busy(Exception):
    """The deadline passed with the lock still held. Always a skipped run."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_holder(path: Path) -> dict | None:
    """Read advisory holder metadata, ignoring torn records and vanished PIDs."""
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
    """Clear the holder before unlocking so waiters cannot report a previous owner."""
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
    """List run records with live PIDs, oldest first."""
    out = []
    for path in _run_paths():
        holder = _read_holder(path)
        if holder:
            out.append(holder)
    return sorted(out, key=lambda h: (str(h.get("since") or ""),
                                      float(h.get("admitted") or 0)))


def room(now: float | None = None) -> dict:
    """Report headroom after accounting for recent starts. Missing memory data permits admission."""
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


# Advisory demand signal: a wake, cron, reviewer or engineer job blocked on
# memory touches this marker on each retry (every 5 s). The session host's
# pressure pass reads it so idle chats release RAM promptly when real work is
# waiting, without changing admission or the memory reserve.
ROOM_WANTED_FILE = "room-wanted"
ROOM_WANTED_TTL_S = 30


def note_room_wanted() -> None:
    """Record that a non-chat run is waiting for memory right now. Fail-soft."""
    try:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        path = LOCK_DIR / ROOM_WANTED_FILE
        path.touch(exist_ok=True)
        os.utime(path, None)
    except OSError:
        pass


def room_wanted(now: float | None = None) -> bool:
    """True when a wake, cron, reviewer or engineer job was refused for memory
    within ``ROOM_WANTED_TTL_S`` seconds."""
    now = time.time() if now is None else now
    try:
        mtime = (LOCK_DIR / ROOM_WANTED_FILE).stat().st_mtime
    except OSError:
        return False
    return (now - mtime) <= ROOM_WANTED_TTL_S


_COUNTER = [0]


def _try_admit(me: dict):
    """Check headroom and claim a record under one lock so concurrent starts share a budget."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    with (LOCK_DIR / "admit.lock").open("a+", encoding="utf-8") as gate:
        fcntl.flock(gate.fileno(), fcntl.LOCK_EX)
        try:
            _sweep()
            r = room()
            if not r["fits"]:
                note_room_wanted()
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
    """Poll admission until admitted or the deadline passes.
    Returns ``(handle, room, wait_info)``."""
    waited = False
    retries = 0
    wait_start: float | None = None
    started_at: str | None = None
    first_mem: dict | None = None
    while True:
        handle, r = _try_admit(me)
        if handle is not None:
            info = {
                "waited": waited,
                "retries": retries,
                "duration_s": round(time.monotonic() - wait_start, 1) if wait_start is not None else 0.0,
                "started_at": started_at,
                "first_mem": first_mem,
            }
            return handle, r, info
        if not waited:
            waited = True
            wait_start = time.monotonic()
            started_at = now_iso()
            first_mem = limits.snapshot_memory(
                meminfo_path=MEMINFO,
                available_mb=r.get("available"),
                reserve_mb=r.get("reserve"),
                start_mb=r.get("need"),
            )
        retries += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            info = {
                "waited": waited,
                "retries": retries,
                "duration_s": round(time.monotonic() - wait_start, 1) if wait_start is not None else 0.0,
                "started_at": started_at,
                "first_mem": first_mem,
            }
            return None, r, info
        time.sleep(min(poll, remaining))


@contextmanager
def hold(agent: str, lane: str, wait_seconds: float | None = None,
         poll: float = POLL_SECONDS, info: dict | None = None):
    """Hold the agent lock and memory admission. Both waits share one deadline."""
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
        run_handle, r, wait_info = _wait_for_room(me, deadline, poll)
        if run_handle is None:
            limits.record_safe(
                "run_skipped",
                outcome="timed_out",
                subject={"agent": agent, "lane": lane},
                duration_s=wait_info["duration_s"],
                started_at=wait_info["started_at"],
                memory=wait_info["first_mem"],
                detail={"reason": "memory", "retries": wait_info["retries"]},
            )
            raise Busy(
                f"{lane} waited {minutes} minutes for memory on this box and "
                f"gave up: {short_text(r)}. This run is skipped."
            )
        if wait_info["waited"]:
            limits.record_safe(
                "run_wait",
                outcome="started",
                subject={"agent": agent, "lane": lane},
                duration_s=wait_info["duration_s"],
                started_at=wait_info["started_at"],
                memory=wait_info["first_mem"],
                detail={
                    "reason": "memory",
                    "retries": wait_info["retries"],
                    "resolved_available_mb": r.get("available"),
                },
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
    """Admit an independent job by memory. Raise Busy if its wait expires."""
    wait = WAIT_SECONDS if wait_seconds is None else wait_seconds
    me = {"agent": agent, "lane": lane, "pid": os.getpid(), "since": now_iso()}
    handle, r, _ = _wait_for_room(me, time.monotonic() + wait, poll)
    if handle is None:
        raise Busy(short_text(r))
    try:
        yield
    finally:
        _leave(handle)


# Children inherit held project locks so nested live operations can reenter them.

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
    # Root deployments create the lock for the user who owns the directory.
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
