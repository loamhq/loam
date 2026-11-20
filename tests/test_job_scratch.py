#!/usr/bin/env python3
"""Run the real inner launcher with a probe session. The job must receive its own
temporary directory and clean it on exit.

Run: python3 tests/test_job_scratch.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
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


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


job = load("engineer_job_scratch", AGENTS / "bin" / "engineer-job")
sweep = load("sweep_tmp", AGENTS / "bin" / "sweep-tmp")

root = Path(tempfile.mkdtemp(prefix="loam-scratch-test-"))
scratch_root = root / "tmp"
scratch_root.mkdir()
seen: dict = {}


class Session:
    """Replace the probe sleep with a check of the session's scratch environment."""

    def __init__(self, fail: bool = False):
        self.fail = fail

    def __getattr__(self, name):
        return getattr(time, name)

    def sleep(self, seconds):
        if seconds != 1:                       # the heartbeat's, not the probe's
            return time.sleep(seconds)
        tmpdir = os.environ.get("TMPDIR")
        seen["tmpdir"] = tmpdir
        if tmpdir and Path(tmpdir).is_dir():
            seen["mode"] = stat.S_IMODE(Path(tmpdir).stat().st_mode)
            seen["empty"] = not any(Path(tmpdir).iterdir())
            # What a session leaves: a store, and a read-only cache in it.
            cache = Path(tmpdir) / "store" / "mod"
            cache.mkdir(parents=True)
            (cache / "f").write_text("x")
            cache.chmod(0o500)
        if self.fail:
            raise RuntimeError("the session blew up")
        return None


def make_project(name: str) -> eng.Project:
    repo = root / name
    repo.mkdir()
    (repo / "lines.txt").write_text("one\n")
    for argv in (["init", "-q", "-b", "main"], ["config", "user.name", "t"],
                 ["config", "user.email", "t@t"], ["add", "-A"],
                 ["commit", "-qm", "start"]):
        subprocess.run(["git", "-C", str(repo), *argv], check=True)
    return eng.Project(name, repo, root / "jobs", platform=False,
                       record={"test_command": "true"})


def write_brief(job_id: str, title: str) -> Path:
    jdir = root / "jobs" / job_id
    jdir.mkdir(parents=True)
    (jdir / "brief.md").write_text(
        f"---\ntitle: {title}\nbriefed_by: ada\nmax_minutes: 2\n---\n\nx\n")
    return jdir


saved = (job.STATE_DIR, eng.VAULT, eng.log_line, eng.job_dir, eng.projects,
         job.loam_lock.LOCK_DIR, job.wt.WORKTREES, job.resume_queued,
         eng.SCRATCH_ROOT, job.time, os.environ.get(job.PROBE_SECONDS_VAR),
         os.environ.get("TMPDIR"), job.JOB_PROMPT)
try:
    project = make_project("scratch")
    job.STATE_DIR = root / "state"
    eng.VAULT = root / "vault"
    eng.log_line = lambda line: None
    eng.job_dir = lambda engineer, jid: root / "jobs" / jid
    eng.projects = lambda: {project.name: project}
    job.loam_lock.LOCK_DIR = root / "locks"
    job.wt.WORKTREES = root / "worktrees"
    job.resume_queued = lambda: []
    eng.SCRATCH_ROOT = scratch_root
    os.environ[job.PROBE_SECONDS_VAR] = "1"
    os.environ.pop("TMPDIR", None)
    prompts = []
    job.JOB_PROMPT = type("P", (), {"format": lambda self, **k: prompts.append(
        k["tree_note"]) or saved[-1].format(**k)})()

    # A job that succeeds, with a dead run's scratch already in the way.
    ok_id = "20261021-180000-5c7a7c01"
    stale = scratch_root / f"loam-job-{ok_id}"
    stale.mkdir()
    (stale / "left-behind").write_text("x")
    write_brief(ok_id, "Scratch probe")
    job.time = Session()
    job.probe_session = lambda seconds: job.time.sleep(seconds) or 0
    code = job.run_inner(project, ok_id)
    want = str(scratch_root / f"loam-job-{ok_id}")
    check("the probe job completed", code, 0)
    check("during: TMPDIR is the job's own scratch", seen.get("tmpdir"), want)
    check("during: it is private", seen.get("mode"), 0o700)
    check("during: it starts empty, a dead run's leavings gone",
          seen.get("empty"), True)
    check("during: the prompt names it", any(want in p for p in prompts), True)
    check("success: the scratch is gone, read-only cache and all",
          Path(want).exists(), False)
    check("success: TMPDIR is not left set", "TMPDIR" in os.environ, False)

    # A job whose session raises.
    seen.clear()
    bad_id = "20261021-180100-5c7a7c02"
    write_brief(bad_id, "Scratch failure")
    job.time = Session(fail=True)
    raised = None
    try:
        job.run_inner(project, bad_id)
    except RuntimeError as exc:
        raised = str(exc)
    bad = scratch_root / f"loam-job-{bad_id}"
    check("failure: the session's error came through", raised, "the session blew up")
    check("failure: the session had its scratch", seen.get("tmpdir"), str(bad))
    check("failure: the scratch is gone anyway", bad.exists(), False)
    job.time = time

    # Recovery: a killed unit ran no `finally`.
    dead_id = "20261021-180200-5c7a7c03"
    write_brief(dead_id, "Scratch killed")
    dead = eng.make_scratch(dead_id)
    (dead / "big").write_text("x" * 1000)
    job.publish = lambda *a, **k: None
    job.recover_interrupted(project, dead_id, job.paths(project, dead_id))
    check("recovery: a killed job's scratch is removed", dead.exists(), False)

    try:
        eng.scratch_dir("../../etc")
        refused = False
    except eng.BriefError:
        refused = True
    check("a name that is not a job id makes no scratch path", refused, True)
finally:
    (job.STATE_DIR, eng.VAULT, eng.log_line, eng.job_dir, eng.projects,
     job.loam_lock.LOCK_DIR, job.wt.WORKTREES, job.resume_queued,
     eng.SCRATCH_ROOT, job.time, probe, tmpdir, job.JOB_PROMPT) = saved
    for name, value in ((job.PROBE_SECONDS_VAR, probe), ("TMPDIR", tmpdir)):
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

# The backstop sweep, on a /tmp of its own. Everything here was made a moment
# ago, so its ctime is now: "stale" is read by moving the sweep's clock three
# days on, which is what the `now` seam is for.
try:
    tmp = root / "sweeproot"
    tmp.mkdir()
    for name in ("old-store", "live-one", "systemd-private-x", "claude-1000",
                 "loam-job-20261021-190000-0a0b0c0d", "loam-job-20261021-190100-0a0b0c0e"):
        (tmp / name).mkdir()
        (tmp / name / "f").write_text("x")
    (tmp / "old-file.log").write_text("x")
    sock = socket.socket(socket.AF_UNIX)
    # Bind through a directory descriptor so a long job TMPDIR does not
    # exceed the Unix socket address length limit.
    dir_fd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
    try:
        sock.bind(f"/proc/self/fd/{dir_fd}/a.sock")
    finally:
        os.close(dir_fd)
    later = time.time() + 3 * 86400
    doomed, kept = sweep.candidates(
        48, root=tmp, live={"live-one"}, jobs={"20261021-190000-0a0b0c0d"},
        now=later)
    reasons = {r["name"]: r["kept"] for r in kept}
    check("sweep: stale entries of ours are removed",
          sorted(r["name"] for r in doomed),
          ["loam-job-20261021-190100-0a0b0c0e", "old-file.log", "old-store"])
    check("sweep: a live process's entry is kept", reasons.get("live-one"),
          "live process")
    check("sweep: a running job's scratch is kept",
          reasons.get("loam-job-20261021-190000-0a0b0c0d"), "running job")
    check("sweep: systemd's and Claude Code's own are kept",
          (reasons.get("systemd-private-x"), reasons.get("claude-1000")),
          ("system", "system"))
    check("sweep: a socket is kept", reasons.get("a.sock"), "socket or device")

    # Recent: one fresh file deep in an old tree keeps the tree.
    doomed, kept = sweep.candidates(48, root=tmp, live=set(), jobs=set(),
                                    now=time.time())
    check("sweep: nothing is stale at its own age", doomed, [])

    # The real run, end to end, on that root.
    sweep_log = root / "sweep.jsonl"
    out = subprocess.run([sys.executable, str(AGENTS / "bin" / "sweep-tmp"),
                          "--hours", "0", "--root", str(tmp), "--log",
                          str(sweep_log)], capture_output=True, text=True,
                         check=False)
    left = sorted(p.name for p in tmp.iterdir())
    check("sweep: the run exits clean", out.returncode, 0)
    check("sweep: at --hours 0 only the kept kinds are left",
          left, ["a.sock", "claude-1000", "systemd-private-x"])
    check("sweep: each removal is logged", len(sweep_log.read_text().splitlines()), 5)
    sock.close()
finally:
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'all passed' if not fails else f'{fails} failed'}")
sys.exit(1 if fails else 0)
