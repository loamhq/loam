#!/usr/bin/env python3
"""No clock and no round-trip cap (since 2026-11-10).

The owner, 2026-11-10: "Please remove any limitations on how long a turn or
session can run. I don't want any limitations at all." Asserted here, by
calling the shipped functions:

  job clock       no default and no upper bound: `max_minutes` absent, 0,
                  negative or not a number is None; 500 stays 500. A brief's
                  header writes `none` and reads back as None, and the
                  session's kill clock is then None.
  the unit        a job's transient unit carries TimeoutStartSec=infinity
                  with no ceiling, and the ceiling plus grace with one.
  the cap         no engineer, wake or reviewer `--max-turns` by default.
  the waiter      there is none (2026-11-19): a brief returns at once.
  the recycle     there is none (Remote Control, which it restarted, was
                  retired 2026-11-19): no recycle-rc, and no recycle timer.

Nothing outside a temporary directory is written; no unit is launched.

Run: python3 tests/test_no_limits.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
for var in ("LOAM_ENGINEER_MAX_TURNS", "LOAM_WAKE_MAX_TURNS",
            "LOAM_REVIEWER_MAX_TURNS", "LOAM_REVIEWER_WALL_S"):
    os.environ.pop(var, None)
import loam_engineer as eng  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:60} {got!r}"[:200]
          + ("" if ok else f"   want {want!r}"))


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


job = load("engineer_job_no_limits", AGENTS / "bin" / "engineer-job")
wake = load("wake_no_limits", AGENTS / "bin" / "wake")
review = load("review_no_limits", AGENTS / "bin" / "review")

# The job's clock.
for sent in (None, "", 0, -5, "none", "soon"):
    check(f"max_minutes {sent!r}: no ceiling", job.clamp_minutes(sent), None)
check("max_minutes 500: kept, no upper bound", job.clamp_minutes(500), 500)
check("max_minutes '45': kept", job.clamp_minutes("45"), 45)
check("no kill clock with no ceiling", job.ceiling_seconds({"max_minutes": None}), None)
check("a ceiling's kill clock", job.ceiling_seconds({"max_minutes": 2}), 120)

root = Path(tempfile.mkdtemp(prefix="loam-no-limits-"))
try:
    d = root / "jobs" / "20261110-160000-0000aaaa"
    d.mkdir(parents=True)
    job.write_brief(d, eng.Project("Loam", root, root / "jobs", platform=True),
                    "No clock probe", "ada", "go", None, "", "body")
    header = (d / "brief.md").read_text()
    check("the brief's header says none", "max_minutes: none\n" in header, True)
    meta = job.read_brief_meta({"brief": d / "brief.md"})
    check("and reads back as no ceiling", meta["max_minutes"], None)

    # The unit.
    seen: list[list[str]] = []
    real_run = job.subprocess.run
    job.subprocess.run = lambda argv, **k: seen.append(argv)
    try:
        project = eng.Project("Loam", root, root / "jobs", platform=True)
        job.launch(project, "20261110-160000-0000aaaa", {"dir": d}, None)
        job.launch(project, "20261110-160000-0000aaaa", {"dir": d}, 2)
    finally:
        job.subprocess.run = real_run
    check("the unit: no ceiling is TimeoutStartSec=infinity",
          "--property=TimeoutStartSec=infinity" in seen[0], True)
    check("the unit: a ceiling is its seconds plus the grace",
          f"--property=TimeoutStartSec={2 * 60 + job.GRACE_SECONDS + 60}" in seen[1], True)

    # The caps.
    check("engineer: no round-trip cap", eng.MAX_TURNS, None)
    check("wake: no round-trip cap", wake.MAX_TURNS, None)
    argv = wake.claude_argv("ada", "reflection", "p", Path("/dev/null"))
    check("wake: no --max-turns on the command line", "--max-turns" in argv, False)
    check("reviewer: no round-trip cap and no wall clock",
          (review.MAX_TURNS, review.WALL_SECONDS), (None, None))
    import loam_cron  # noqa: PLC0415
    check("wake and cron: no lane clock",
          loam_cron.lane_timeout_s("ada", "reflection"), None)
    check("no unattended job clock left",
          hasattr(eng, "UNATTENDED_MAX_MINUTES"), False)

    # Nothing waits on a job (2026-11-19): no waiter and no ceiling of its
    # own, and the engineer tool's transport timeout is no longer four hours.
    check("no waiter and no waiter ceiling",
          (hasattr(job, "wait_for"), hasattr(job, "WAITER_CEILING_SECONDS")),
          (False, False))
finally:
    shutil.rmtree(root, ignore_errors=True)

# The recycle is gone with what it restarted.
check("no recycle-rc, and no recycle timer",
      ((AGENTS / "bin" / "recycle-rc").exists(),
       sorted(p.name for p in (AGENTS / "systemd").glob("loam-recycle-rc.*"))),
      (False, []))

print(f"\n{'all passed' if not fails else f'{fails} failed'}")
sys.exit(1 if fails else 0)
