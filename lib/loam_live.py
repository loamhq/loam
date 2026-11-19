"""What is live on the box, for the app's live-sessions view (2026-11-10).

The owner, 11-10: "an easy and intuitive way in the UI to see what sessions
are currently active and the ability to end them from the UI". The broker
answers ``GET /v1/sessions/live`` (and its stream) from the rows built in
``bin/session-broker`` (``Server.live_view``); this module is the part that
reads the box rather than the broker: a process tree's memory, a process's
start time, the box's RAM, and the systemd units outside the broker that
hold memory (engineer jobs, wakes, crons).

Everything here reads ``/proc`` and ``/sys/fs/cgroup`` only, no network, and
every function takes its roots as arguments so a test can hand it a fake.

**The state words** (``state_of``): ``working`` (a turn is open),
``asking`` (waiting on a permission or a question from a person),
``background`` (its turn ended but work it launched is still running), and
``idle``. They are the broker's ``Session.busy()`` renamed for a person:
``needs_input`` -> asking, ``waiting`` -> background, None -> idle. The
first three are what ``end-idle`` never touches.
"""

from __future__ import annotations

import os
from pathlib import Path

PROC = Path("/proc")
APP_SLICE = Path("/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice")

STATES = {"working": "working", "needs_input": "asking", "waiting": "background",
          None: "idle"}

# What ending a session costs, in the app's words, by state.
ENDING = {
    "working": (True, "ends a turn in progress"),
    "asking": (True, "ends a turn waiting on your answer"),
    "background": (True, "ends the background work it is waiting on"),
    "idle": (False, "resumes later exactly where it was"),
}

# The units outside the broker that hold memory, by kind: the unit name's
# prefix. Engineer jobs are `loam-engineer-<job>`, wakes `loam-wake-<agent>`,
# crons `loam-cron-<agent>-<name>`. (`rc_servers`, `loam-rc-<agent>`, went
# with Remote Control on 2026-11-19.)
UNIT_KINDS = (("engineer_jobs", "loam-engineer-"), ("wakes", "loam-wake-"),
              ("crons", "loam-cron-"))


# What lets a planned broker restart go (2026-11-17, replacing the rules of
# 11-12 and 11-16): positive evidence that nothing would be cut. Every row of
# the live list reads exactly `idle`, and no thread turn is in flight.
# Anything else holds it: `working` (a turn open, including one sitting
# silent in a long tool call or a call waiting on an engineer job),
# `asking`, `background` (a turn that ended and waits on work it launched:
# a subagent dies with the session's process tree, and a waiting call is
# cut), a state this rule does not know, a row with none, and a list that
# could not be read. On 11-16 the ceiling let a restart go past four silent
# `working` turns, three of them waiting on engineer jobs, and cut them. A
# planned restart now defers instead (bin/restart-broker); an explicit
# restart, without `--when-idle`, is the operator's and unchanged.
RESTART_IDLE = "idle"
# Kept for readers that name the holding states (lib/loam_follow.py): the
# known ones. An unknown state holds a restart too; see `restart_blockers`.
RESTART_HOLDS = ("working", "asking", "background")
# How long a planned restart waits for idle before it defers: no restart,
# exit 75, and the caller tries again later. Never a restart at the ceiling.
RESTART_CEILING_S = 30 * 60


def _who(r: dict) -> str:
    return f"{r.get('agent')}/{str(r.get('id'))[:8]}"


def restart_blockers(rows: list[dict] | None,
                     turns: list[dict] | None = None) -> list[str]:
    """Why a planned broker restart may not go now, one line a reason; empty
    means it may. ``rows`` is `GET /v1/sessions/live`'s ``sessions``; None
    (a failed read) holds it. ``turns`` is its ``turns`` (thread turns in
    flight, 2026-11-17), None when the broker is older than that field: then
    the rows alone are the evidence, and a turn about to start is caught by
    the clear reads bin/restart-broker takes."""
    if not isinstance(rows, list):
        return ["the broker's live list could not be read (counted as busy)"]
    held = []
    for r in rows:
        if not isinstance(r, dict):
            held.append(f"a live row that is not a row ({r!r:.40}; counted as busy)")
            continue
        state = r.get("state")
        if state == RESTART_IDLE:
            continue
        held.append(f"{_who(r)} {state}" if state in RESTART_HOLDS
                    else f"{_who(r)} in an unknown state ({state!r}; counted as busy)")
    if turns is not None and not isinstance(turns, list):
        held.append("the broker's turns in flight could not be read (counted as busy)")
        turns = []
    for t in turns or []:
        who = t.get("persona") if isinstance(t, dict) else None
        tid = t.get("thread_id") if isinstance(t, dict) else None
        held.append(f"{who}'s turn in {tid} "
                    f"{'queued' if isinstance(t, dict) and t.get('queued') else 'in flight'}")
    return held


def state_of(busy: str | None) -> str:
    return STATES.get(busy, "working")


def ending(state: str) -> dict:
    loses, text = ENDING.get(state, ENDING["working"])
    return {"loses_work": loses, "text": text}


def children(proc: Path = PROC) -> dict[int, list[int]]:
    """Every process's children, read once from ``/proc/*/stat``."""
    kids: dict[int, list[int]] = {}
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        # The command name is in parentheses and may hold spaces.
        fields = stat.rsplit(")", 1)[-1].split()
        try:
            kids.setdefault(int(fields[1]), []).append(int(entry.name))
        except (IndexError, ValueError):
            continue
    return kids


def _rss_kb(pid: int, proc: Path) -> int:
    try:
        for line in (proc / str(pid) / "status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def tree_rss_mb(pid: int | None, kids: dict[int, list[int]],
                proc: Path = PROC) -> int | None:
    """The RSS of a process and every descendant, in MB (the CLI and its MCP
    servers). Shared pages count once per process, so it reads a little high;
    for a gauge, not a bill. None when there is no process."""
    if not pid:
        return None
    total, todo, seen = 0, [pid], set()
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        total += _rss_kb(p, proc)
        todo.extend(kids.get(p, ()))
    return total // 1024


def started_ts(pid: int | None, proc: Path = PROC) -> float | None:
    """When a process started, as epoch seconds, from its stat's starttime
    (clock ticks since boot) and the kernel's btime."""
    if not pid:
        return None
    try:
        fields = (proc / str(pid) / "stat").read_text().rsplit(")", 1)[-1].split()
        ticks = int(fields[19])
        btime = next(int(line.split()[1]) for line in (proc / "stat").read_text().splitlines()
                     if line.startswith("btime "))
        return btime + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def meminfo_mb(proc: Path = PROC) -> dict:
    out = {}
    try:
        for line in (proc / "meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            out[k] = int(v.split()[0]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return out


def units(slice_dir: Path = APP_SLICE) -> dict:
    """The units outside the broker that hold memory, by kind:
    ``{kind: {"count", "mb", "units": [{"name", "mb"}]}}``, biggest first."""
    out = {kind: {"count": 0, "mb": 0, "units": []} for kind, _ in UNIT_KINDS}
    try:
        entries = list(slice_dir.iterdir())
    except OSError:
        return out
    for d in entries:
        name = d.name.removesuffix(".service")
        kind = next((k for k, prefix in UNIT_KINDS if name.startswith(prefix)), None)
        if kind is None:
            continue
        try:
            mb = int((d / "memory.current").read_text()) // (1024 * 1024)
        except (OSError, ValueError):
            continue
        group = out[kind]
        group["count"] += 1
        group["mb"] += mb
        group["units"].append({"name": name, "mb": mb})
    for group in out.values():
        group["units"].sort(key=lambda u: -u["mb"])
    return out


def can_start(available: int | None, reserve: int, need: int, starting: int,
              cgroup_room: int | None, seats_left: int) -> int | None:
    """How many more sessions admission would let start now, the way
    ``Broker.room`` reads it, each assumed to take ``need`` MB. None when
    the box's memory cannot be read."""
    if available is None:
        return None
    n = max(0, (available - starting * need - reserve) // need)
    if cgroup_room is not None:
        n = min(n, max(0, (cgroup_room - starting * need) // need))
    return max(0, min(n, seats_left))
