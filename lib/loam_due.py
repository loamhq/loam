"""Was this timer-fired run due? The lane's own answer, whatever systemd did.

Added 2026-11-14 (first release, with L3) after the state move restarted the
timers at 18:51:47Z and every persona's reflection wake, the reviewer,
the Remote Control recycle (retired 2026-11-19), a persona's infra-watch (a
Wednesdays cron, on a Saturday) and
provider-follow all ran on the spot. The cause, read in systemd 252's
src/core/timer.c and reproduced on a live every-minute timer at 22:55Z: starting a
timer clears its last trigger (`timer_start`, line 638); a timer without a
stamp file (every `Persistent=false` one) then dates its next elapse from
the unit's previous activation, and when that activation is older than the
last calendar time, the next elapse is already past and the timer fires at
once. The five persistent timers, whose stamp file restores the last
trigger, did not fire.

So two rules. Tooling never stops and starts a standing timer: a changed
timer is moved by `daemon-reload` alone, which keeps its last trigger
(lib/loam_units.py, bin/move-state). And, in case anything ever does, a run
a timer started checks that the timer's own calendar fell due in the minutes
before it fired, and goes no further when it did not: `not_due()`.

systemd tells a triggered service which timer started it and when
(`TRIGGER_UNIT`, `TRIGGER_TIMER_REALTIME_USEC`, systemd 251+). A run started
any other way (by hand, "Run now" in the app, a test) has neither and is
never refused here.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone

import loam_config as lc

# A due fire is the calendar time plus the timer's accuracy (one minute by
# default) and the manager's own delay; ten minutes is generous for both and
# far short of any calendar these timers carry.
TOLERANCE_S = 10 * 60

LOG = lc.LOGS / "not-due.jsonl"


def _calendars(unit: str) -> list[str]:
    out = subprocess.run(["systemctl", "--user", "show", "-p", "TimersCalendar",
                          "--value", unit], capture_output=True, text=True,
                         check=False, timeout=20)
    return re.findall(r"OnCalendar=(.*?) ;", out.stdout)


def _next_after(spec: str, base: float) -> float | None:
    out = subprocess.run(["systemd-analyze", "calendar", f"--base-time=@{int(base)}",
                          spec], capture_output=True, text=True, check=False,
                         timeout=20)
    m = re.search(r"\(in UTC\):\s*\w+ (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC",
                  out.stdout)
    if not m:
        return None
    return datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc).timestamp()


def not_due(env: dict | None = None) -> str | None:
    """None when this run may go on; else the sentence saying why it may not.
    Fails open: anything it cannot read lets the run go, because a run that
    was due and refused is the worse mistake."""
    env = os.environ if env is None else env
    unit, usec = env.get("TRIGGER_UNIT", ""), env.get("TRIGGER_TIMER_REALTIME_USEC")
    if not unit.endswith(".timer") or not usec:
        return None
    try:
        fired = int(usec) / 1_000_000
        specs = _calendars(unit)
    except (ValueError, OSError, subprocess.SubprocessError):
        return None
    if not specs:
        return None                      # a monotonic timer: no calendar to hold it to
    for spec in specs:
        try:
            due = _next_after(spec, fired - TOLERANCE_S)
        except (OSError, subprocess.SubprocessError):
            return None
        if due is None or due <= fired + 1:
            return None
    at = datetime.fromtimestamp(fired, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (f"not due: {unit} fired at {at}, and its calendar "
            f"({'; '.join(specs)}) names no time in the {TOLERANCE_S // 60} "
            f"minutes before. A timer restarted after its last activation "
            f"fires like this (lib/loam_due.py); nothing ran.")


def refuse_if_not_due(lane: str) -> bool:
    """For a lane's entry point: True (and a log line) when it must stop."""
    why = not_due()
    if why is None:
        return False
    print(f"{lane}: {why}", flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "lane": lane, "why": why}) + "\n")
    except OSError:
        pass
    return True
