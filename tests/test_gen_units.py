#!/usr/bin/env python3
"""bin/gen-units: --help is help, and a timer it writes for the first time is armed.

  - `--help` prints the docstring and writes nothing (it used to run live);
    an unknown flag is refused;
  - a first run into an empty unit folder arms every timer it writes, as the
    installer does, so an update run by an older release's code still
    leaves a box with a new release's timers armed;
  - a second run arms nothing: a timer already installed is left as it
    stands, armed or disarmed by its owner;
  - with a hand-made crontab commit, loam-autocommit.timer is written and
    not armed (lib/loam_autocommit.py: a hand-made routine wins).

systemctl is a fake on PATH that records its calls; the home is scratch
(`LOAM_CONFIG` paths.home). Nothing on the box is read or written.

Run: python3 tests/test_gen_units.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:62}{'  | ' + detail if detail else ''}")


tmp = Path(tempfile.mkdtemp(prefix="gen-units-"))
try:
    home = tmp / "home"
    home.mkdir()
    cfg = tmp / "loam.toml"
    cfg.write_text(f'[paths]\nhome = "{home}"\nstate = "{tmp}/state"\nlogs = "{tmp}/logs"\n')
    fake = tmp / "bin"
    fake.mkdir()
    calls = tmp / "calls"
    (fake / "systemctl").write_text(
        f"#!/bin/sh\necho \"$*\" >> {calls}\n"
        # `show -p ... --value`: an inactive timer systemd has never activated.
        "case \"$*\" in *show*) echo 0;; esac\nexit 0\n")
    (fake / "systemctl").chmod(0o755)
    cron = tmp / "crontab"
    cron.write_text("")
    env = {**os.environ, "LOAM_CONFIG": str(cfg), "PATH": f"{fake}:{os.environ['PATH']}",
           "LOAM_CRONTAB_FILE": str(cron), "PYTHONDONTWRITEBYTECODE": "1"}
    units = home / ".config" / "systemd" / "user"

    def gen(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ROOT / "bin" / "gen-units"), *args],
                              capture_output=True, text=True, env=env)

    res = gen("--help")
    check("--help prints the docstring, exit 0", res.returncode == 0 and "gen-units --dry-run" in res.stdout)
    check("--help writes nothing", not units.exists() and not calls.exists())
    res = gen("--bogus")
    check("an unknown flag is refused, writes nothing", res.returncode == 2 and not units.exists(),
          res.stderr.strip())

    timers = sorted(p.name for p in (ROOT / "systemd").glob("*.timer"))
    res = gen()
    started = [ln.split()[-1] for ln in calls.read_text().splitlines() if ln.startswith("--user start ")]
    check("first run: every timer it wrote is armed", sorted(started) == timers,
          f"{len(started)} of {len(timers)}")
    check("first run says which it armed", res.stdout.count("armed ") == len(timers))
    calls.write_text("")
    res = gen()
    check("second run: nothing armed again", "start " not in calls.read_text()
          and "armed" not in res.stdout, res.stdout.strip()[-120:])

    # A release adds a timer; a box with a hand-made vault commit.
    shutil.rmtree(units)
    cron.write_text("*/15 * * * * /x/bin/vault-autocommit\n")
    calls.write_text("")
    res = gen()
    started = [ln.split()[-1] for ln in calls.read_text().splitlines() if ln.startswith("--user start ")]
    check("hand-made routine: autocommit written, not armed",
          (units / "loam-autocommit.timer").exists() and "loam-autocommit.timer" not in started
          and "not armed" in res.stdout)
    check("...the other new timers still armed", len(started) == len(timers) - 1)
    shutil.rmtree(units)
    calls.write_text("")
    res = gen("--dry-run")
    check("--dry-run writes and arms nothing", res.returncode == 0 and not units.exists()
          and "start " not in calls.read_text() and "would arm" in res.stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
