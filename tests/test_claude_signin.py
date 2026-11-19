#!/usr/bin/env python3
"""The Claude Code sign-in is noticed and written down (bin/claude-signin).

  the watch    loam-claude-signin.path is a shipped template, PathChanged on
               the login file, wanted by default.target, rendered by
               gen-units; its service runs bin/claude-signin, once a change.
  signed in    the record says so.
  signed out   the file is looked at again before a sign-out is recorded (a
               rewrite is not a sign-out).
  dry run      says, writes nothing.
  nothing else no systemctl call at all: since Remote Control was retired
               (2026-11-19) a sign-in starts and stops nothing, and no
               loam-rc-* unit ships.

Run: python3 tests/test_claude_signin.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_config as lc  # noqa: E402
import loam_units  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:62} {got!r}"[:220]
          + ("" if ok else f"   want {want!r}"))


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


sig = load("claude_signin_test", AGENTS / "bin" / "claude-signin")

# -- the watch -------------------------------------------------------------------
cred = str(lc.CLAUDE_HOME / ".credentials.json")
check("claude-signin watches the login file", str(sig.CREDENTIALS), cred)
units = loam_units.rendered(AGENTS / "systemd")
path = units.get("loam-claude-signin.path", "")
check("loam-claude-signin.path ships, a path unit gen-units renders",
      (f"PathChanged={cred}" in path, "Unit=loam-claude-signin.service" in path,
       "WantedBy=default.target" in path), (True, True, True))
check("...PathChanged, never PathExists (which re-fires on every exit)",
      any(l.startswith("PathExists=") for l in path.splitlines()), False)
check("...its service runs claude-signin, once a change",
      ("Type=oneshot" in units.get("loam-claude-signin.service", ""),
       f"{lc.ROOT}/bin/claude-signin" in units.get("loam-claude-signin.service", "")),
      (True, True))
check("no Remote Control unit ships", [n for n in units if n.startswith("loam-rc-")
                                       or "recycle" in n], [])
check("claude-signin calls no systemctl", "systemctl" in
      (AGENTS / "bin" / "claude-signin").read_text(encoding="utf-8"), False)

# -- the record --------------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    login, record = root / ".credentials.json", root / "state" / "claude-signin.json"
    slept: list[float] = []
    login.write_text("{}")
    check("signed in: exit 0", sig.main([], credentials=login, sleep=slept.append,
                                        record=record), 0)
    check("...recorded signed in", json.loads(record.read_text())["signed_in"], True)
    check("...and nothing was waited for", slept, [])
    login.unlink()
    check("dry run writes nothing", (sig.main(["--dry-run"], credentials=login,
                                              sleep=slept.append, record=record),
                                     json.loads(record.read_text())["signed_in"]), (0, True))
    sig.main([], credentials=login, sleep=slept.append, record=record)
    check("signed out: recorded, after a second look",
          (json.loads(record.read_text())["signed_in"], len(slept) >= 1), (False, True))

    def rewritten(_s: float) -> None:
        login.write_text("{}")
    sig.main([], credentials=login, sleep=rewritten, record=record)
    check("a rewrite mid-look is not a sign-out",
          json.loads(record.read_text())["signed_in"], True)

print()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
