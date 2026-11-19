#!/usr/bin/env python3
"""LIVE: the launcher's user-bus default reaches this box's real user manager.

Not a landing test (it asks the box): run by hand with
`python3 tests/run-all --live`. tests/test_engineer_bus.py proves, sealed,
that the launcher hands `systemd-run` /run/user/<uid> under Codex's bare
environment; this proves that on this box that value is a manager that
answers. Split out of test_engineer_bus on 2026-11-19, when a landing's
tests stopped seeing the box's user manager.

Run: python3 tests/live_user_bus.py
"""

import subprocess
import sys
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_config as lc  # noqa: E402

PROBE = (
    "import importlib.machinery as m, importlib.util as u, os, subprocess\n"
    f"l = m.SourceFileLoader('j', {str(AGENTS / 'bin' / 'engineer-job')!r})\n"
    "s = u.spec_from_loader('j', l); j = u.module_from_spec(s)\n"
    "l.exec_module(j); j.user_bus_env()\n"
    "print(subprocess.run(['systemctl', '--user', 'is-system-running'],\n"
    "                     capture_output=True, text=True).stdout.strip())\n")

out = subprocess.run(["env", "-i", f"HOME={lc.HOME}", f"USER={lc.USER}",
                      "PATH=/usr/bin:/bin", "/usr/bin/python3", "-c", PROBE],
                     capture_output=True, text=True, timeout=60, check=False)
state = out.stdout.strip()
ok = state in ("running", "degraded")
print(f"{'PASS' if ok else 'FAIL'}  bare env: the user manager answered  {state!r}"
      + ("" if ok else f"\n      {out.stderr.strip()[-400:]}"))
sys.exit(0 if ok else 1)
