#!/usr/bin/env python3
"""Cancel queued, running and landing jobs. Once cancellation wins, recovery must leave
the job stopped.

Run: python3 tests/test_engineer_cancel.py
"""

from __future__ import annotations

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
OWNER = eng.lc.OWNER_ID

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} {got!r}"[:240]
          + ("" if ok else f"   want {want!r}"))


loader = importlib.machinery.SourceFileLoader(
    "engineer_job_cancel", str(AGENTS / "bin" / "engineer-job"))
spec = importlib.util.spec_from_loader("engineer_job_cancel", loader)
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)

root = Path(tempfile.mkdtemp(prefix="loam-cancel-test-"))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def make_project(name: str, test_command: str = "true") -> tuple[eng.Project, Path]:
    repo = root / name
    repo.mkdir()
    (repo / "lines.txt").write_text("one\n")
    for argv in (["init", "-q", "-b", "main"], ["config", "user.name", "t"],
                 ["config", "user.email", "t@t"], ["add", "-A"],
                 ["commit", "-qm", "start"]):
        subprocess.run(["git", "-C", str(repo), *argv], check=True)
    return (eng.Project(name, repo, root / "jobs", platform=False,
                        record={"test_command": test_command}), repo)


def write_brief(job_id: str, title: str) -> Path:
    jdir = root / "jobs" / job_id
    jdir.mkdir(parents=True)
    (jdir / "brief.md").write_text(
        f"---\ntitle: {title}\nbriefed_by: ada\nmax_minutes: 2\n---\n\nx\n")
    return jdir


def commit_in_tree(jdir: Path, name: str, subject: str) -> None:
    tree = Path(job.wt.read_record(jdir)["path"])
    (tree / name).write_text("work\n")
    git(tree, "add", "-A")
    git(tree, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", subject)


def wait_until(predicate, seconds: float = 20) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


projects: dict[str, eng.Project] = {}
units: dict[str, str] = {}
stops: list[list[str]] = []
real_run = subprocess.run


def fake_run(argv, *a, **k):
    if list(argv[:3]) == ["systemctl", "--user", "stop"]:
        stops.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")
    return real_run(argv, *a, **k)


saved = (job.STATE_DIR, eng.VAULT, eng.log_line, eng.job_dir, eng.projects,
         eng.locate_job, job.loam_lock.LOCK_DIR, job.wt.WORKTREES,
         job.resume_queued, job.unit_state, job.land_job, job.subprocess.run,
         os.environ.get(job.PROBE_SECONDS_VAR))
try:
    job.STATE_DIR = root / "state"
    eng.VAULT = root / "vault"
    eng.log_line = lambda line: None
    eng.job_dir = lambda engineer, jid: root / "jobs" / jid
    eng.projects = lambda: dict(projects)
    eng.locate_job = lambda jid: next(iter(projects.values())) if (
        root / "jobs" / jid).is_dir() else None
    job.loam_lock.LOCK_DIR = root / "locks"
    job.wt.WORKTREES = root / "worktrees"
    job.resume_queued = lambda: []
    job.unit_state = lambda unit: units.get(unit, "inactive")
    job.subprocess.run = fake_run

    # -- queued ------------------------------------------------------------
    project, repo = make_project("cancelq")
    projects[project.name] = project
    pid_hex = f"{os.getpid() & 0xffffff:06x}"
    qid = f"20261110-190000-{pid_hex}01"
    qdir = write_brief(qid, "Queued cancel")
    (qdir / "queued").write_text(json.dumps({"joined": "2026-11-10T19:00:00Z"}))
    units[job.unit_name(qid)] = "active"
    code, said = job.cancel(qid, "ada", wait=5)
    result = json.loads((qdir / "result.json").read_text())
    check("queued: cancelled, its unit stopped, out of the queue",
          (code, stops[-1][-1] if stops else None, (qdir / "queued").exists(),
           job.queued_jobs()), (0, f"{job.unit_name(qid)}.service", False, []))
    check("queued: its verdict says it never ran",
          (result["stage"], "before it ran" in result["receipt_text"],
           "had not started" in said), ("cancelled", True, True))
    units.pop(job.unit_name(qid))

    # -- running -----------------------------------------------------------
    os.environ[job.PROBE_SECONDS_VAR] = "60"
    rid = f"20261110-190100-{pid_hex}02"
    rdir = write_brief(rid, "Running cancel")
    units[job.unit_name(rid)] = "active"
    main_before = git(repo, "rev-parse", "main")
    outcome: dict = {}
    inner = threading.Thread(target=lambda: outcome.update(
        code=job.run_inner(project, rid)), daemon=True)
    started = time.monotonic()
    inner.start()
    alive = wait_until(lambda: (rdir / "owner.json").is_file() and any(
        "sleep" in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
        for pid in job.descendants(os.getpid()) if Path(f"/proc/{pid}").exists()))
    check("running: the probe's session (a sleep child) is up", alive, True)
    commit_in_tree(rdir, "half.txt", "half of the work")
    stops.clear()
    code, said = job.cancel(rid, OWNER, wait=30)
    inner.join(timeout=30)
    elapsed = time.monotonic() - started
    result = json.loads((rdir / "result.json").read_text())
    record = job.wt.read_record(rdir)
    check("running: the cancel returns once the inner has recorded it",
          (code, "its session was stopped" in said, inner.is_alive()), (0, True, False))
    check("running: well before the probe's 60 s were up", elapsed < 40, True)
    check("running: the verdict is the inner's, stage cancelled, not ok",
          (result["stage"], result["ok"], result["outcome"].startswith(
              "cancelled at "), f"by {OWNER}" in result["outcome"]),
          ("cancelled", False, True, True))
    check("running: it says how long it ran and what it had committed",
          (bool(result.get("ran_for")), [c.split(" ", 1)[1] for c in result["commits"]],
           "half of the work" in result["receipt_text"]),
          (True, ["half of the work"], True))
    check("running: the cancel's own sentence says how long and what it committed",
          ("it ran " in said, "committed 1 commit(s)" in said,
           "half of the work" in said, "nothing ran" not in said.lower()),
          (True, True, True, True))
    check("running: nothing landed, the branch and tree are kept",
          (git(repo, "rev-parse", "main") == main_before, result["landing_state"],
           result["landing_reason"], Path(record["path"]).is_dir()),
          (True, "not_landed", "cancelled", True))
    check("running: no continuation is dispatched", result["dispatched"], None)
    check("running: its unit was not stopped by hand (the inner ends it)",
          stops, [])
    check("running: the inner exits 0 (a cancel is not a unit failure)",
          outcome.get("code"), 0)
    check("running: the recovery sweep reads it as done, never resumes it",
          job.classify(project, rid), "done")
    units.pop(job.unit_name(rid))

    # -- landing: a cancel during the landing ------------------------------
    os.environ[job.PROBE_SECONDS_VAR] = "1"
    lid = f"20261110-190200-{pid_hex}03"
    ldir = write_brief(lid, "Landing cancel")
    units[job.unit_name(lid)] = "active"
    real_land_job = job.land_job
    said_during: dict = {}

    def cancelling_land_job(proj, job_id, rec, *a, **k):
        commit_in_tree(root / "jobs" / job_id, "landed.txt", "would land")
        canceller = threading.Thread(target=lambda: said_during.update(
            zip(("code", "said"), job.cancel(job_id, OWNER, wait=30))),
            daemon=True)
        canceller.start()
        wait_until(lambda: (root / "jobs" / job_id / "cancelled").is_file())
        said_during["descendants_killed"] = False
        result = real_land_job(proj, job_id, rec, *a, **k)
        said_during["thread"] = canceller
        return result

    job.land_job = cancelling_land_job
    main_before = git(repo, "rev-parse", "main")
    job.run_inner(project, lid)
    said_during["thread"].join(timeout=30)
    result = json.loads((ldir / "result.json").read_text())
    check("landing: main did not move", git(repo, "rev-parse", "main"), main_before)
    check("landing: the landing stopped on the cancel, before anything moved",
          (result["stage"], result["landing_state"], result["landing_reason"],
           "during its landing" in result["outcome"]),
          ("cancelled", "not_landed", "cancelled", True))
    check("landing: the cancel killed nothing and said the landing stopped",
          (said_during.get("code"), "its landing stopped before merging"
           in said_during.get("said", "")), (0, True))
    check("landing: the receipt offers no continuation",
          "continue_job:" in result["receipt_text"].split("## The launcher's record")[-1],
          False)
    units.pop(job.unit_name(lid))

    # -- landing: a cancel after the landing's first check -----------------
    # The branch's tests write the marker, as a cancel that came while they
    # ran would: the check immediately before `main` moves is what stops it.
    late, late_repo = make_project("cancellate")
    projects.clear()
    projects[late.name] = late
    tid = f"20261110-190300-{pid_hex}04"
    tdir = write_brief(tid, "Late cancel")
    late.record["test_command"] = (
        f"printf '%s' '{{\"at\": \"2026-11-10T19:03:30Z\", \"by\": \"owner\"}}' "
        f"> {tdir / 'cancelled'}")

    def committing_land_job(proj, job_id, rec, *a, **k):
        commit_in_tree(root / "jobs" / job_id, "late.txt", "would land late")
        return real_land_job(proj, job_id, rec, *a, **k)

    job.land_job = committing_land_job
    main_before = git(late_repo, "rev-parse", "main")
    job.run_inner(late, tid)
    result = json.loads((tdir / "result.json").read_text())
    log = (tdir / "landing.log").read_text()
    check("late: the branch's tests ran and passed (the first check was clear)",
          ("testing the branch" in log, "nothing lands" in log), (True, True))
    check("late: the check before `main` moves stopped it; main did not move",
          (git(late_repo, "rev-parse", "main") == main_before,
           result["landing_reason"], result["stage"]), (True, "cancelled", "cancelled"))

    # -- status ------------------------------------------------------------
    sid = f"20261110-190400-{pid_hex}05"
    sdir = write_brief(sid, "Status cancel")
    held = job.loam_lock._try_take(  # noqa: SLF001
        job.loam_lock.LOCK_DIR / f"run-{os.getpid()}-99.lock",
        {"agent": "cancellate", "lane": f"job:{sid}", "pid": os.getpid(),
         "since": "2026-11-10T19:04:00Z"})
    check("status: a running job is listed", [s.get("job") for s in
                                               job.box_status()["slots"]], [sid])
    (sdir / "cancelled").write_text(json.dumps({"at": "2026-11-10T19:04:10Z",
                                                "by": "ada"}))
    check("status: a cancelled one is dropped at once",
          job.box_status()["slots"], [])
    job.loam_lock._release(held)  # noqa: SLF001
finally:
    (job.STATE_DIR, eng.VAULT, eng.log_line, eng.job_dir, eng.projects,
     eng.locate_job, job.loam_lock.LOCK_DIR, job.wt.WORKTREES,
     job.resume_queued, job.unit_state, job.land_job, job.subprocess.run,
     probe) = saved
    if probe is None:
        os.environ.pop(job.PROBE_SECONDS_VAR, None)
    else:
        os.environ[job.PROBE_SECONDS_VAR] = probe
    shutil.rmtree(root, ignore_errors=True)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
