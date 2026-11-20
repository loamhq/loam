"""Recover interrupted jobs from their receipts, leases and Git state without
duplicating an external action.

Run: python3 tests/test_engineer_recover.py
"""

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_engineer as eng  # noqa: E402
import loam_lock  # noqa: E402
import loam_worktree as wt  # noqa: E402

LAUNCHER = str(Path(__file__).resolve().parent.parent / "bin" / "engineer-job")
loader = importlib.machinery.SourceFileLoader("engineer_job_recover", LAUNCHER)
spec = importlib.util.spec_from_loader("engineer_job_recover", loader)
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} {got!r}"
          + ("" if ok else f"\n      wanted {want!r}"))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


root = Path(tempfile.mkdtemp(prefix="loam-recover-test-"))
saved = (eng.locate_job, eng.projects, eng.VAULT, wt.WORKTREES, loam_lock.LOCK_DIR, job.STATE_DIR,
         job.RECOVERY_LOG, job.RECOVERY_STATE, job.unit_state, job.launch)
os.environ.pop(loam_lock.LIVE_HELD_VAR, None)
try:
    eng.VAULT = root / "vault"
    wt.WORKTREES = root / "worktrees"
    loam_lock.LOCK_DIR = root / "locks"
    loam_lock.LOCK_DIR.mkdir()
    job.STATE_DIR = root / "wake"
    job.STATE_DIR.mkdir()
    job.RECOVERY_LOG = root / "recovery.jsonl"
    job.RECOVERY_STATE = root / "recovery.json"

    origin = root / "demo.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = root / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "a.txt").write_text("a\n")
    git(seed, "add", "-A")
    git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "start")
    git(seed, "push", "-q", str(origin), "main")
    checkout = root / "demo"
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
    git(checkout, "config", "user.name", "demo engineer")
    git(checkout, "config", "user.email", "engineer+demo@noreply.local")
    project = eng.Project("demo", checkout, root / "demo-jobs", platform=False,
                          record={"name": "demo", "test_command": "true"})
    loam_root = root / "loam"
    (loam_root / "engineer").mkdir(parents=True)
    loam = eng.Project("Loam", loam_root / "engineer", root / "loam-jobs",
                       platform=True, record={})
    project.jobs.mkdir()
    loam.jobs.mkdir()
    eng.projects = lambda: {"demo": project, "Loam": loam}

    units: dict[str, str] = {}
    launched: list[str] = []
    job.unit_state = lambda unit: units.get(unit, "inactive")

    def fake_launch(proj, job_id, p, minutes):
        launched.append(job_id)
        units[job.unit_name(job_id)] = "active"
    job.launch = fake_launch

    def new_job(proj, job_id, *, title="Edit a", extra=""):
        d = proj.jobs / job_id
        d.mkdir()
        (d / "brief.md").write_text(
            f"---\nproject: {proj.name}\ntitle: {title}\nbriefed_by: otto\n"
            f"involvement: go\nmax_minutes: 15\n{extra}---\n\nDo it.\n")
        return job.paths(proj, job_id)

    def died_mid_run(proj, job_id, **kw):
        """A job whose inner ran and was SIGKILLed: tree, session, lease, no
        verdict, lock free."""
        p = new_job(proj, job_id, **kw)
        record = wt.setup(proj, job_id, p["dir"])
        p["session"].write_text("11111111-2222-3333-4444-555555555555\n")
        p["owner"].write_text(json.dumps({"pid": 999999, "unit": job.unit_name(job_id)}))
        return p, record

    def sweep():
        return job.sweep()

    # Queued, unit gone.
    q = new_job(project, "20261002-120000-0000000a")
    q["queued"].write_text(json.dumps({"joined": "2026-10-02T12:00:00Z"}))
    # Queued, then cancelled.
    c = new_job(project, "20261002-120000-0000000c")
    c["queued"].write_text(json.dumps({"joined": "2026-10-02T12:00:01Z"}))
    subprocess_stop = []
    job.subprocess.run, real_run = (lambda argv, **kw: subprocess_stop.append(argv)
                                    or subprocess.CompletedProcess(argv, 0, "", "")
                                    if argv[:2] == ["systemctl", "--user"] else
                                    real_run(argv, **kw)), subprocess.run
    job.eng.locate_job = lambda jid: project if (project.jobs / jid).is_dir() else None
    check("cancel: a queued job is cancelled without running",
          job.run_cancel("20261002-120000-0000000c"), 0)
    check("...its result says cancelled and its queue marker is gone",
          (json.loads(c["result"].read_text())["stage"], c["queued"].exists()),
          ("cancelled", False))
    job.subprocess.run = real_run

    # An active unit with no lease (its inner has not taken the job yet, so no
    # session has started): stopped, and recorded as never having run. A
    # running job's cancel is tests/test_engineer_cancel.py's.
    r = new_job(project, "20261002-120000-0000000d")
    units[job.unit_name("20261002-120000-0000000d")] = "active"
    subprocess_stop.clear()
    job.subprocess.run, real_run = (lambda argv, **kw: subprocess_stop.append(argv)
                                    or subprocess.CompletedProcess(argv, 0, "", "")
                                    if argv[:2] == ["systemctl", "--user"] else
                                    real_run(argv, **kw)), subprocess.run
    job.run_cancel("20261002-120000-0000000d")
    job.subprocess.run = real_run
    check("cancel: an active unit with no lease is stopped, as not started",
          (subprocess_stop, r["cancelled"].exists(),
           "before it ran" in json.loads(r["result"].read_text())["receipt_text"]),
          ([["systemctl", "--user", "stop",
             "loam-engineer-20261002-120000-0000000d.service"]], True, True))
    units.pop(job.unit_name("20261002-120000-0000000d"))
    shutil.rmtree(r["dir"])

    # Interrupted, worker alive (holds the tree lock).
    alive, alive_rec = died_mid_run(project, "20261002-120000-000000a1")
    holder = subprocess.Popen([sys.executable, "-c", (
        f"import sys, time; sys.path.insert(0, {str(Path(__file__).resolve().parent.parent / 'lib')!r})\n"
        "import loam_lock\n"
        f"loam_lock.LOCK_DIR = __import__('pathlib').Path({str(loam_lock.LOCK_DIR)!r})\n"
        f"h = loam_lock._try_take(__import__('pathlib').Path({str(wt.tree_lock(alive_rec))!r}), {{'pid': 1}})\n"
        "print('held' if h else 'no', flush=True); time.sleep(30)\n")],
        stdout=subprocess.PIPE, text=True)
    check("fault: a surviving worker holds its lease", holder.stdout.readline().strip(), "held")

    # Interrupted with unlanded work.
    lost, lost_rec = died_mid_run(project, "20261002-120000-000000b1")
    (Path(lost_rec["path"]) / "b.txt").write_text("b\n")
    git(Path(lost_rec["path"]), "add", "-A")
    git(Path(lost_rec["path"]), "commit", "-qm", "half")

    # Interrupted after the canonical advanced, before the push.
    pushed, pushed_rec = died_mid_run(project, "20261002-120000-000000d1")
    (Path(pushed_rec["path"]) / "d.txt").write_text("d\n")
    git(Path(pushed_rec["path"]), "add", "-A")
    git(Path(pushed_rec["path"]), "commit", "-qm", "landed")
    git(checkout, "fetch", "-q", pushed_rec["path"], pushed_rec["branch"])
    git(checkout, "merge", "-q", "--ff-only", "FETCH_HEAD")
    behind = git(checkout, "ls-remote", "origin", "refs/heads/main").split()[0]

    # Interrupted after the receipt was published.
    pub, _ = died_mid_run(project, "20261002-120000-000000e1")
    receipt = project.receipts_dir(eng.VAULT) / "2026-10-02-20261002-120000-000000e1-edit-a.md"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text("the engineer's receipt\n")

    # Interrupted at the end of its chain.
    end, _ = died_mid_run(project, "20261002-120000-000000f1",
                          extra="auto_continuation: 2\n")

    # Legacy: ran before the lease existed. Finished: a verdict.
    legacy = new_job(project, "20261001-090000-00000001")
    legacy["heartbeat"].write_text("2026-10-01T09:00:00Z\n")
    done = new_job(project, "20261002-120000-0000d0e0")
    done["result"].write_text(json.dumps({"ok": True, "stage": "done"}))
    done["owner"].write_text("{}")

    # A Loam deploy that died.
    deploy = new_job(loam, "20261002-120000-000000aa", title="Deploy demo")
    deploy["owner"].write_text("{}")
    deploy["session"].write_text("11111111-2222-3333-4444-555555555555\n")

    # Cancelled while running, and its unit died with it (no verdict): the
    # sweep finalizes it as cancelled, and never resumes it.
    cr, cr_rec = died_mid_run(project, "20261002-120000-000000c2")
    cr["owner"].write_text(json.dumps({"pid": 999999, "since": "2026-10-02T12:00:00Z"}))
    (Path(cr_rec["path"]) / "c.txt").write_text("c\n")
    git(Path(cr_rec["path"]), "add", "-A")
    git(Path(cr_rec["path"]), "commit", "-qm", "half of a cancelled job")
    cr["cancelled"].write_text(json.dumps({"at": "2026-10-02T12:05:00Z", "by": "ada"}))

    first = sweep()
    by_job = {r["job"]: r["state"] for r in first["recovered"]}
    crr = json.loads(cr["result"].read_text())
    check("cancelled while running, unit dead: finalized cancelled, not resumed",
          ("20261002-120000-000000c2" in first["cancelled"],
           "20261002-120000-000000c2" in by_job, crr["stage"],
           "Cancelled while running" in crr["receipt_text"],
           "half of a cancelled job" in crr["receipt_text"],
           Path(crr["vault_receipt"]).is_file()),
          (True, False, "cancelled", True, True, True))
    check("queued, unit gone: relaunched once", first["requeued"],
          ["20261002-120000-0000000a"])
    check("worker alive: untouched, no verdict",
          ("20261002-120000-000000a1" in by_job, alive["result"].exists()), (False, False))
    check("unlanded work: continued once, as a bounded continuation",
          by_job.get("20261002-120000-000000b1"), "redispatched")
    child = json.loads(lost["result"].read_text())["dispatched"]
    head = job.front_matter((project.jobs / child / "brief.md").read_text())
    check("...the continuation resumes its session in its tree, count 1",
          (head.get("continue_job"), head.get("auto_continuation"), child in launched),
          ("20261002-120000-000000b1", "1", True))
    check("...its brief says not to repeat external steps",
          "do NOT repeat a deploy" in (project.jobs / child / "brief.md").read_text(), True)
    check("...the branch and its commit are kept",
          git(Path(lost_rec["path"]), "log", "-1", "--format=%s"), "half")
    check("crash after the canonical advanced: recorded as landed",
          by_job.get("20261002-120000-000000d1"), "landed_before_death")
    origin_now = git(checkout, "ls-remote", "origin", "refs/heads/main").split()[0]
    check("...and the pending push is completed (origin was behind)",
          (behind != git(checkout, "rev-parse", "main"),
           origin_now == git(checkout, "rev-parse", "main"),
           first["pushes"][0]["pushed"]), (True, True, True))
    check("crash after the receipt was published: that receipt is the verdict",
          (by_job.get("20261002-120000-000000e1"),
           json.loads(pub["result"].read_text())["receipt_text"]),
          ("receipt_already_published", "the engineer's receipt\n"))
    check("end of its chain: surfaced for a persona, nothing dispatched",
          (by_job.get("20261002-120000-000000f1"),
           json.loads(end["result"].read_text())["dispatched"]), ("needs_persona", None))
    check("a deploy that died: surfaced, never repeated on its own",
          (by_job.get("20261002-120000-000000aa"),
           "a deploy is not repeated" in json.loads(deploy["result"].read_text())["receipt_text"]),
          ("needs_persona", True))
    check("every surfaced job has a receipt in the vault",
          all(Path(r["receipt"]).is_file() for r in first["recovered"]), True)
    check("legacy (no lease): reported, left alone",
          ("20261001-090000-00000001" in first["legacy"], legacy["result"].exists()),
          (True, False))
    check("finished: never touched",
          json.loads(done["result"].read_text()), {"ok": True, "stage": "done"})
    check("cancelled: not relaunched", "20261002-120000-0000000c" in launched, False)

    launches = list(launched)
    second = sweep()
    check("a second sweep: nothing recovered, nothing relaunched, no push",
          (second["recovered"], second["requeued"], second["pushes"], launched == launches),
          ([], [], [], True))

    # The worker dies; now the sweep may take it.
    holder.kill()
    holder.wait()
    third = sweep()
    check("the worker's lease gone: its job is recovered exactly once",
          [r["job"] for r in third["recovered"]], ["20261002-120000-000000a1"])
    check("...and a claim already made is not made again",
          job.claim(alive), False)

    # Two sweeps at once: the second finds the sweep lock held.
    sweeper = subprocess.Popen([sys.executable, "-c", (
        f"import sys, time; sys.path.insert(0, {str(Path(__file__).resolve().parent.parent / 'lib')!r})\n"
        "import loam_lock, pathlib\n"
        f"h = loam_lock._try_take(pathlib.Path({str(loam_lock.LOCK_DIR / 'engineer-recover.lock')!r}), {{'pid': 1}})\n"
        "print('held', flush=True); time.sleep(10)\n")], stdout=subprocess.PIPE, text=True)
    sweeper.stdout.readline()
    out = subprocess.run([sys.executable, "-c", (
        "import importlib.machinery, importlib.util, pathlib, sys\n"
        f"l = importlib.machinery.SourceFileLoader('j', {LAUNCHER!r})\n"
        "s = importlib.util.spec_from_loader('j', l); j = importlib.util.module_from_spec(s); s.loader.exec_module(j)\n"
        f"j.loam_lock.LOCK_DIR = pathlib.Path({str(loam_lock.LOCK_DIR)!r})\n"
        "sys.exit(j.run_recover())\n")], capture_output=True, text=True)
    sweeper.kill()
    check("overlapping sweeps: the second stands down",
          json.loads(out.stdout.strip().splitlines()[-1]), {"skipped": "another sweep is running"})
finally:
    (eng.locate_job, eng.projects, eng.VAULT, wt.WORKTREES, loam_lock.LOCK_DIR, job.STATE_DIR,
     job.RECOVERY_LOG, job.RECOVERY_STATE, job.unit_state, job.launch) = saved
    shutil.rmtree(root, ignore_errors=True)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
