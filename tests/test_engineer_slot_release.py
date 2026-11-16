#!/usr/bin/env python3
"""A job lets its admission go when its session ends (since 2026-10-13).

Admission was a run slot until 2026-11-10 and is the box's memory since
(lib/loam_lock.py): a job holds a run record for its session, and the
status lists every record, with no count and so no free rows.

Until then a job held its slot through the landing -- the live-lock wait,
the suite, the merge, the push, the receipt -- and the 10-13 audit found
11.5% of slot-hours spent there, with jobs queued behind it. This drives the
real `run_inner` of this tree's bin/engineer-job, as a probe job (the sleep
stands where the session would), against a repository in a temp directory
and a lock table of its own, and looks from inside the landing:

  released        the job's run record is gone while the job lands,
                  and `resume_queued` ran after the session and before it.
  once            the admission's context is exited exactly once, on a normal
                  landing and on a refused one; a second free_slot is a no-op.
  lease           the tree lock -- the lease the recovery sweep reads -- is
                  still held while the job lands.
  status          `box_status` lists the job under `landing`, not as idle,
                  with its title and briefer; nothing shows as running.
  after           the `landing` record is gone once the verdict is written.
  waiter          `wait_for` waits past the job's clock while a live landing
                  record is there, and still reports a dead unit as died.

Nothing outside a temporary directory is written; no unit is launched.

Run: python3 tests/test_engineer_slot_release.py
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_engineer as eng  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:62} {got!r}"[:220]
          + ("" if ok else f"   want {want!r}"))


loader = importlib.machinery.SourceFileLoader(
    "engineer_job_slot_release", str(AGENTS / "bin" / "engineer-job"))
spec = importlib.util.spec_from_loader("engineer_job_slot_release", loader)
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)

root = Path(tempfile.mkdtemp(prefix="loam-slot-release-"))
lock_dir = root / "locks"
events: list[str] = []
seen: dict = {}
exits = {"n": 0}

real_hold_slot = job.loam_lock.hold_slot


@contextlib.contextmanager
def counted_slot(*a, **k):
    with real_hold_slot(*a, **k):
        yield
    exits["n"] += 1


def make_project(name: str) -> tuple[eng.Project, Path]:
    repo = root / name
    repo.mkdir()
    (repo / "lines.txt").write_text("one\n")
    for argv in (["init", "-q", "-b", "main"], ["config", "user.name", "t"],
                 ["config", "user.email", "t@t"], ["add", "-A"],
                 ["commit", "-qm", "start"]):
        subprocess.run(["git", "-C", str(repo), *argv], check=True)
    return (eng.Project(name, repo, root / "jobs", platform=False,
                        record={"test_command": "true"}), repo)


def write_brief(job_id: str, title: str) -> Path:
    jdir = root / "jobs" / job_id
    jdir.mkdir(parents=True)
    (jdir / "brief.md").write_text(
        f"---\ntitle: {title}\nbriefed_by: ada\nmax_minutes: 2\n---\n\nx\n")
    return jdir


saved = (job.STATE_DIR, eng.VAULT, eng.log_line, eng.job_dir, eng.projects,
         job.loam_lock.LOCK_DIR,
         job.loam_lock.hold_slot, job.wt.WORKTREES, job.resume_queued,
         job.land_job, os.environ.get(job.PROBE_SECONDS_VAR))
try:
    project, repo = make_project("slotrel")
    job_id = "20261013-210000-5e1ea5e0"
    jdir = write_brief(job_id, "Slot release probe")
    job.STATE_DIR = root / "state"
    eng.VAULT = root / "vault"
    eng.log_line = lambda line: None
    eng.job_dir = lambda engineer, jid: root / "jobs" / jid
    eng.projects = lambda: {project.name: project}
    job.loam_lock.LOCK_DIR = lock_dir
    job.loam_lock.hold_slot = counted_slot
    job.wt.WORKTREES = root / "worktrees"
    job.resume_queued = lambda: events.append("resume_queued") or []
    os.environ[job.PROBE_SECONDS_VAR] = "1"
    real_land_job = job.land_job

    def spying_land_job(project, job_id, record, *a, **k):
        events.append("land")
        seen["slot_free"] = job.loam_lock.running() == []
        tree = job.loam_lock._try_take(job.wt.tree_lock(record),  # noqa: SLF001
                                       {"agent": "sweep", "pid": os.getpid()})
        seen["tree_held"] = tree is None
        if tree is not None:
            job.loam_lock._release(tree)  # noqa: SLF001
        seen["status"] = job.box_status()
        seen["text"] = job.status_text(seen["status"])
        seen["exits_during"] = exits["n"]
        return real_land_job(project, job_id, record, *a, **k)

    job.land_job = spying_land_job
    code = job.run_inner(project, job_id)

    result = json.loads((jdir / "result.json").read_text())
    check("the probe job completed", (result.get("outcome"), code), ("completed", 0))
    check("released: the job's run record is gone during the landing",
          seen.get("slot_free"), True)
    check("released: resume_queued ran after the session, before the landing",
          events[:2], ["resume_queued", "land"])
    check("once: the slot was exited once, before the landing",
          (seen.get("exits_during"), exits["n"]), (1, 1))
    check("lease: the tree lock stayed held through the landing",
          seen.get("tree_held"), True)
    status = seen.get("status") or {}
    check("status: the job is listed as landing, with title and briefer",
          [(r["job"], r["title"], r["briefed_by"], r["phase"])
           for r in status.get("landing", [])],
          [(job_id, "Slot release probe", "ada", "landing")])
    check("status: nothing reads as running (no free rows: no count)",
          status.get("slots"), [])
    check("status: the text says landing, not idle",
          f"{job_id} (slotrel, briefed by ada): Slot release probe — landing"
          in seen.get("text", ""), True)
    check("after: the landing record is gone with the verdict written",
          (jdir / "landing").exists(), False)
    check("after: the box reads idle again", job.box_status()["landing"], [])

    # A refused job (no tree can be made) frees its slot once, too.
    exits["n"] = 0
    job.land_job = real_land_job
    bad = eng.Project("gone", root / "no-such-repo", root / "jobs", platform=False,
                      record={})
    bad_id = "20261013-210100-5e1ea5e1"
    write_brief(bad_id, "Refused")
    job.run_inner(bad, bad_id)
    check("once: a refused job exits its slot once",
          (json.loads((root / "jobs" / bad_id / "result.json").read_text())["stage"],
           exits["n"]), ("refused", 1))
finally:
    (job.STATE_DIR, eng.VAULT, eng.log_line, eng.job_dir, eng.projects,
     job.loam_lock.LOCK_DIR,
     job.loam_lock.hold_slot, job.wt.WORKTREES, job.resume_queued,
     job.land_job, probe) = saved
    if probe is None:
        os.environ.pop(job.PROBE_SECONDS_VAR, None)
    else:
        os.environ[job.PROBE_SECONDS_VAR] = probe

# A wake running: named by its agent and lane, with no project and no
# briefer (null), never a job's "?" and "unknown". Activity's "Now" rows are
# these, through GET /v1/activity's `jobs.slots` (the name kept).
saved = (job.loam_lock.LOCK_DIR, job.loam_broker.AI_LABS)
try:
    labs = root / "labs"
    (labs / "ada").mkdir(parents=True)
    (labs / "ada" / "agent.json").write_text(json.dumps({"display": "Ada"}))
    job.loam_broker.AI_LABS = labs
    wake_locks = root / "wake-locks"
    wake_locks.mkdir()
    job.loam_lock.LOCK_DIR = wake_locks
    held = [job.loam_lock._try_take(wake_locks / f"run-{os.getpid()}-{n}.lock",  # noqa: SLF001
                                    {"agent": "ada", "lane": lane, "admitted": n,
                                     "pid": os.getpid(), "since": "2026-10-15T10:24:00Z"})
            for n, lane in ((1, "reflection"), (2, "cron:infra-watch"))]
    slots = job.box_status()["slots"]
    for h in held:
        job.loam_lock._release(h)  # noqa: SLF001
    print("wake slot row:", json.dumps(slots[0]))
    check("wake row: named by agent and lane, no project, no briefer",
          [(s["kind"], s["agent"], s["lane"], s["name"], s["label"],
            s["project"], s["briefed_by"]) for s in slots],
          [("wake", "ada", "reflection", "reflection", "Ada · reflection",
            None, None),
           ("wake", "ada", "cron:infra-watch", "infra-watch",
            "Ada · infra-watch", None, None)])
finally:
    (job.loam_lock.LOCK_DIR, job.loam_broker.AI_LABS) = saved

# The waiter. A landing record whose pid is alive extends the job's clock;
# one whose pid is dead does not, and a dead unit is still `died`.
job.POLL_SECONDS = 0.1
job.STARTUP_GRACE_SECONDS = 0.5
job.GRACE_SECONDS = 1
unit = {"state": "active"}
job.unit_state = lambda name: unit["state"]
job.resume_queued = lambda: []
try:
    d = root / "wait"
    d.mkdir()
    p = {"dir": d, "queued": d / "queued", "result": d / "result.json",
         "heartbeat": d / "heartbeat", "landing": d / "landing"}
    p["landing"].write_text(json.dumps({"pid": os.getpid(), "since": "x",
                                        "phase": "landing"}))

    def settle():
        time.sleep(3)
        p["result"].write_text(json.dumps({"ok": True, "stage": "done",
                                           "receipt_text": "landed late"}))
    threading.Thread(target=settle, daemon=True).start()
    got = job.wait_for(p, "w1", 0.01)    # a ceiling: 0.6 s + 1 s grace
    check("waiter: a landing past the job's clock is waited for",
          (got["stage"], got["receipt_text"]), ("done", "landed late"))

    p["result"].unlink()
    dead = subprocess.Popen(["true"])
    dead.wait()
    p["landing"].write_text(json.dumps({"pid": dead.pid, "since": "x"}))
    check("waiter: a dead landing's record reads as nothing",
          job.read_landing(p), None)
    p["landing"].write_text(json.dumps({"pid": os.getpid(), "since": "x"}))
    unit["state"] = "inactive"
    got = job.wait_for(p, "w2", None)
    check("waiter: a landing whose unit is gone is still `died`",
          got["stage"], "died")
finally:
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'all passed' if not fails else f'{fails} failed'}")
sys.exit(1 if fails else 0)
