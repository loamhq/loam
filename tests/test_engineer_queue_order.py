#!/usr/bin/env python3
"""Verify FIFO admission when a newly arriving job competes with a queued job for
available memory.

Run: python3 tests/test_engineer_queue_order.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:60} {got!r}"[:220]
          + ("" if ok else f"   want {want!r}"))


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


job = load("engineer_job_queue_order", AGENTS / "bin" / "engineer-job")
eng = job.eng
job.QUEUE_POLL_SECONDS = 0.05

taken: list[str] = []


class Slot:
    def __init__(self, lane: str):
        self.lane = lane

    def __enter__(self):
        taken.append(self.lane)
        return self

    def __exit__(self, *exc):
        return False


job.loam_lock.hold_slot = lambda agent, lane, wait_seconds=None: Slot(lane)

root = Path(tempfile.mkdtemp(prefix="loam-queue-order-"))
saved_projects, saved_vault = eng.projects, eng.VAULT
try:
    project = eng.Project("demo", root / "checkout", root / "demo-jobs",
                          platform=False, record={"vault_folder": "Demo"})
    other = eng.Project("other", root / "other", root / "other-jobs",
                        platform=False, record={})
    project.jobs.mkdir()
    other.jobs.mkdir()
    eng.projects = lambda: {"demo": project, "other": other}
    eng.VAULT = root / "vault"

    waiting = []
    for proj, job_id, joined in (
            (other, "20261013-092638-00000001", "2026-10-13T16:26:38Z"),
            (project, "20261013-092646-00000002", "2026-10-13T16:26:47Z")):
        d = proj.jobs / job_id
        d.mkdir()
        (d / "queued").write_text(json.dumps({"joined": joined}))
        waiting.append(d)

    newcomer = "20261013-094904-00000003"
    p = job.paths(project, newcomer)
    p["dir"].mkdir()
    got: dict = {}
    runner = threading.Thread(target=lambda: got.update(
        lock=job.take_slot(project, newcomer, f"job:{newcomer}", p)))
    runner.start()
    time.sleep(0.5)
    check("a queue is waiting: the new job takes no slot", taken, [])
    state = json.loads(p["queued"].read_text())
    check("...it joins the queue, behind both", job.queue_position(newcomer),
          (3, 3))
    check("...and says how many are ahead of it", state["held_by"],
          "2 job(s) queued ahead of this one")

    (waiting[0] / "result.json").write_text("{}")
    time.sleep(0.3)
    check("one left ahead: still no slot", taken, [])
    (waiting[1] / "result.json").write_text("{}")
    runner.join(timeout=5)
    check("the queue empties: it takes the slot", taken, [f"job:{newcomer}"])
    check("...its marker is gone", p["queued"].exists(), False)
    check("...and its queue.log says it started after waiting",
          "started, after waiting since" in (p["dir"] / "queue.log").read_text(),
          True)

    taken.clear()
    alone = "20261013-100000-00000004"
    q = job.paths(project, alone)
    q["dir"].mkdir()
    lock = job.take_slot(project, alone, f"job:{alone}", q)
    check("nobody waiting: a new job takes the slot at once",
          (lock is not None, taken), (True, [f"job:{alone}"]))
    check("...and never queues", (q["queued"].exists(),
                                  (q["dir"] / "queue.log").exists()),
          (False, False))
finally:
    eng.projects, eng.VAULT = saved_projects, saved_vault
    shutil.rmtree(root, ignore_errors=True)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
