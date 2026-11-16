"""What is live on the box, for the app's live-sessions view (2026-11-10).

The owner, 11-10: "an easy and intuitive way in the UI to see what sessions
are currently active and the ability to end them from the UI". The broker
answers ``GET /v1/sessions/live`` (and its stream) from the rows built in
``bin/session-broker`` (``Server.live_view``); this module is the part that
reads the box rather than the broker: a process tree's memory, a process's
start time, the box's RAM, and the systemd units outside the broker that
hold memory (engineer jobs, wakes, crons, the RC servers).

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
# crons `loam-cron-<agent>-<name>`, the RC servers `loam-rc-<agent>`.
UNIT_KINDS = (("engineer_jobs", "loam-engineer-"), ("wakes", "loam-wake-"),
              ("crons", "loam-cron-"), ("rc_servers", "loam-rc-"))


# What holds a broker restart back (2026-11-12): a session producing a turn
# now (`working`), or one whose open turn waits on the owner's answer
# (`asking`, which a restart would lose). Nothing else. `background` -- a
# turn that ended and waits on work it launched, an engineer job or a
# subagent -- is idle for a restart: it can wait for hours (it did on 11-12,
# the Appearance restart armed at 17:17 PT), the job runs on as its own
# unit, and its receipt reaches the thread when the job finishes
# (lib/loam_waiters.py). What a restart costs such a session is the
# subagent's own run, if it was a subagent and not a job: that work ends
# with the session's process tree. The one rule, for bin/restart-broker
# --when-idle and lib/loam_follow.py alike.
RESTART_HOLDS = ("working", "asking")


def restart_blockers(rows: list[dict] | None) -> list[str]:
    """Why the broker may not restart now, one line a row; empty means it
    may. ``rows`` is `GET /v1/sessions/live`'s; None (a failed read)
    holds the restart, because a broker that cannot answer may be mid-turn."""
    if rows is None:
        return ["the broker's live list could not be read (counted as busy)"]
    return [f"{r.get('agent')}/{str(r.get('id'))[:8]} {r.get('state')}"
            for r in rows if r.get("state") in RESTART_HOLDS]


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
