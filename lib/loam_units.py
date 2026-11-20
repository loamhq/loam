"""Render static user unit templates from installation paths. Generation and drift
checks use the same bytes. Replace longer path tokens first to preserve nested
paths.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import loam_config as lc

TEMPLATES = lc.ROOT / "systemd"


def tokens() -> list[tuple[str, str]]:
    """(token, value), in the order a template is made from a unit."""
    return [
        ("@WAKE_ENV@", str(lc.WAKE_ENV)),
        ("@SECRETS_ENV@", str(lc.SECRETS_ENV)),
        ("@LOGS@", str(lc.LOGS)),
        ("@STATE@", str(lc.STATE)),
        ("@MCP_VENV@", str(lc.MCP_VENV)),
        ("@ROOT@", str(lc.ROOT)),
        ("@UNIT_PATH@", lc.UNIT_PATH),
        ("@UV@", str(lc.UV_BIN)),
        ("@CLAUDE@", str(lc.CLAUDE_BIN)),
        ("@PYCACHE@", str(lc.PYCACHE)),
        ("@HOME@", str(lc.HOME)),
        ("@TZ@", lc.TIMEZONE),
        ("@ZONE_WORD@", lc.ZONE_WORD),
        ("@AGENT_OOM@", str(lc.AGENT_OOM_SCORE_ADJ)),
        ("@CORE_OOM@", str(lc.CORE_OOM_SCORE_ADJ)),
    ]


def render(text: str) -> str:
    for token, value in tokens():
        text = text.replace(token, value)
    return text


def templates(root: Path | None = None) -> list[Path]:
    """List the shipped user service, timer and path templates. System-level units are
    handled separately.
    """
    base = root or TEMPLATES
    return sorted(p for p in base.iterdir()
                  if p.suffix in (".service", ".timer", ".path") and p.is_file())


API_UNIT = "loam-session-broker.service"
HOST_UNIT = "loam-session-host.service"


def split_marker():
    return lc.STATE / "broker" / "separate-host"


def rendered(root: Path | None = None, *, split: bool | None = None) -> dict[str, str]:
    base = root or TEMPLATES
    units = templates(base)
    separate = split_marker().exists() if split is None else split
    if separate:
        units.extend(templates(base / "session"))
    return {p.name: render(p.read_text(encoding="utf-8")) for p in units}


# ---------------------------------------------------------------------------
# Arming a timer without firing it (2026-11-14, first release L4)
# ---------------------------------------------------------------------------
#
# Starting a timer that has stood before can fire it on the spot: systemd 252
# clears its last trigger at start and, with no stamp file, dates the next
# elapse from the unit's previous activation (lib/loam_due.py has the whole
# finding). A timer systemd has never activated, or one it has forgotten,
# cannot: its base is the moment it starts. systemd forgets an inactive
# timer once its file is gone and the manager reloads (measured 2026-11-14:
# InactiveExitTimestampMonotonic 373094343313 before, 0 after). So:
#
# - a timer that is active is left alone: the reload that rewrote it moved it;
# - an inactive one that has stood before is made fresh first (its file set
#   aside across a reload) and only then started;
# - a new one is enabled and started.

def systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True,
                          text=True, check=False)


def _show(unit: str, prop: str, run=systemctl) -> str:
    out = run("show", "-p", prop, "--value", unit)
    return ((getattr(out, "stdout", "") or "").strip().splitlines() or [""])[0]


def stood_before(unit: str, run=systemctl) -> bool:
    return _show(unit, "InactiveExitTimestampMonotonic", run) not in ("", "0")


def arm_timer(timer: str, unit_dir: Path | None = None,
              runtime: bool = False, run=systemctl) -> str:
    """Enable and start a timer so that it does not fire on the spot; what
    was done: "standing", "started", or "made fresh, started". ``run`` is how
    systemctl is called (a test's fake records it)."""
    assert timer.endswith(".timer")
    unit_dir = unit_dir or lc.USER_UNIT_DIR
    flags = ["--runtime"] if runtime else []
    ctl = run

    if _show(timer, "ActiveState", run) == "active":
        ctl("enable", *flags, timer)
        return "standing"
    did = "started"
    if stood_before(timer, run):
        path = unit_dir / timer
        aside = unit_dir / f".{timer}.aside"
        ctl("disable", *flags, timer)
        os.replace(path, aside)
        ctl("daemon-reload")
        os.replace(aside, path)
        ctl("daemon-reload")
        did = "made fresh, started"
    ctl("enable", *flags, timer)
    ctl("start", timer)
    return did


# Units this repository once shipped and has retired. `gen-units` stops,
# disables and removes any it finds installed, so an update to a release
# without them does not leave them firing a command that is gone; check-drift
# reports one still there. The provider follower went on 2026-11-19 (the
# owner: the CLIs are pinned to each release, lib/loam_pins.py).
RETIRED = ("loam-provider-follow.timer", "loam-provider-follow.service",
           # Remote Control, retired 2026-11-19: its recycle timer would fire
           # a script that is gone, and each agent's server would keep
           # running with no one to talk to (found walking 0.2.0 -> 0.2.12).
           # A pattern is matched with fnmatch.
           "loam-recycle-rc.timer", "loam-recycle-rc.service", "loam-rc-*.service",
           # The separate watchers, replaced 2026-11-19 by the one check on
           # a clock (loam-check.timer, lib/loam_check.py, batch B step 6):
           # drift-watch, check-memory and the sign-in watch, whose login
           # reading is now the check's login fact.
           "loam-check-drift.timer", "loam-check-drift.service",
           "loam-check-memory.timer", "loam-check-memory.service",
           "loam-claude-signin.path", "loam-claude-signin.service")


def retire(unit_dir: Path | None = None, dry: bool = False,
           run=systemctl) -> list[str]:
    """Every RETIRED unit installed here, stopped, disabled and removed
    (timers first, so nothing fires in between); the names it found."""
    unit_dir = unit_dir or lc.USER_UNIT_DIR
    import fnmatch  # noqa: PLC0415
    try:
        here = [p.name for p in unit_dir.iterdir() if p.is_file() or p.is_symlink()]
    except OSError:
        here = []
    found = sorted((n for n in here if any(fnmatch.fnmatchcase(n, pat) for pat in RETIRED)),
                   key=lambda n: (not n.endswith(".timer"), n))
    if dry or not found:
        return found
    for unit in found:
        run("disable", "--now", unit)
    for unit in found:
        (unit_dir / unit).unlink(missing_ok=True)
        # An enable link left in a .wants/ directory.
        for wants in unit_dir.glob("*.wants"):
            (wants / unit).unlink(missing_ok=True)
    run("daemon-reload")
    run("reset-failed", *found)
    return found
