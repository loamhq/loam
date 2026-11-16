"""The static systemd units, rendered for this install.

``systemd/`` holds templates: every path in them is a token, filled from
``lib/loam_config.py``. ``bin/gen-units`` writes the rendered units into the
user unit directory, and ``bin/check-drift`` compares the installed ones
against the same rendering, so the template is the truth and an installed
unit is the drift. On a box whose config carries the values the units were
written with, the rendering is byte-identical to the old static file.

Added 2026-11-14 (first release, job L1). The tokens, longest path first so
that a shorter one never eats a longer one's prefix:
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
    ]


def render(text: str) -> str:
    for token, value in tokens():
        text = text.replace(token, value)
    return text


def templates(root: Path | None = None) -> list[Path]:
    """The user units this repository ships (not ``system/``). A `.path`
    since 2026-11-14 (loam-claude-signin.path, bin/rc-arm)."""
    base = root or TEMPLATES
    return sorted(p for p in base.iterdir()
                  if p.suffix in (".service", ".timer", ".path") and p.is_file())


def rendered(root: Path | None = None) -> dict[str, str]:
    return {p.name: render(p.read_text(encoding="utf-8"))
            for p in templates(root)}


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
