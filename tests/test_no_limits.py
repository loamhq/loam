#!/usr/bin/env python3
"""No clock, no round-trip cap, and a recycle that yields (since 2026-11-10).

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
  the waiter      with no ceiling, a running job is waited for past any
                  old clock, and the call still ends at its own ceiling with
                  "Still running", never killing the job.
  the recycle     bin/recycle-rc restarts every server holding no waiting
                  call, waits for one that holds a call and restarts it when
                  the call ends, and skips -- and says so -- one whose call
                  outlasts the wait. A job's own inner (`--in-unit`) is not
                  a waiting call.

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
import threading
import time
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
rc = load("recycle_rc_no_limits", AGENTS / "bin" / "recycle-rc")

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

    # The waiter, with no ceiling: waits past what used to be the clock.
    job.POLL_SECONDS = 0.05
    job.STARTUP_GRACE_SECONDS = 0.2
    job.GRACE_SECONDS = 0.1
    job.unit_state = lambda name: "active"
    job.resume_queued = lambda: []
    w = root / "wait"
    w.mkdir()
    p = {"dir": w, "queued": w / "queued", "result": w / "result.json",
         "heartbeat": w / "heartbeat", "landing": w / "landing"}

    def settle():
        time.sleep(1.0)
        p["result"].write_text(json.dumps({"ok": True, "stage": "done",
                                           "receipt_text": "finished"}))
    threading.Thread(target=settle, daemon=True).start()
    got = job.wait_for(p, "w1", None)
    check("waiter: no ceiling waits for the job to finish", got["stage"], "done")
    p["result"].unlink()
    job.WAITER_CEILING_SECONDS = 0.5
    got = job.wait_for(p, "w2", None)
    check("waiter: the call's own ceiling ends the call, not the job",
          (got["stage"], "Still running" in got["receipt_text"],
           "the job did not stop" in got["receipt_text"]),
          ("waiter_timeout", True, True))
finally:
    shutil.rmtree(root, ignore_errors=True)

# The recycle.
check("recycle: the outer of a brief is a waiting call",
      rc.is_waiter(["python3", "/home/loam/agents/bin/engineer-job", "--brief"]), True)
check("recycle: a job's own inner is not",
      rc.is_waiter(["python3", "/home/loam/agents/bin/engineer-job",
                    "Loam", "x", "--in-unit"]), False)
check("recycle: an unrelated process is not",
      rc.is_waiter(["/home/loam/.local/bin/claude", "-p", "engineer-job"]), False)

holding = {"loam-rc-juno.service": [[101], [101], []],      # ends after two looks
           "loam-rc-iris.service": [[202]] * 50}            # never ends
restarts: list[list[str]] = []


def find(unit):
    plan = holding.get(unit)
    return plan.pop(0) if plan and len(plan) > 1 else (plan[0] if plan else [])


record = rc.recycle(
    units=lambda: ["loam-rc-cleo.service", "loam-rc-iris.service",
                   "loam-rc-juno.service", "loam-rc-pax.service"],
    find=find, do_restart=lambda units, dry: restarts.append(list(units)) or None,
    wait_s=0.6, poll_s=0.1, sleep=time.sleep)
check("recycle: the free servers restart at once, together",
      restarts[0], ["loam-rc-cleo.service", "loam-rc-pax.service"])
check("recycle: a held server restarts when its call ends",
      ["loam-rc-juno.service"] in restarts[1:] and "loam-rc-juno.service" in record["waited"],
      True)
check("recycle: one whose call outlasts the wait is skipped, with its pids",
      record["skipped"], {"loam-rc-iris.service": [202]})
check("recycle: the skipped one was never restarted",
      any("loam-rc-iris.service" in r for r in restarts), False)
check("recycle: the record lists every restart",
      sorted(record["restarted"]),
      ["loam-rc-cleo.service", "loam-rc-juno.service", "loam-rc-pax.service"])

print(f"\n{'all passed' if not fails else f'{fails} failed'}")
sys.exit(1 if fails else 0)
