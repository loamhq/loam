"""Check that a commit made during testing stays on the job branch for the next landing."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="loam-land-meanwhile-test-"))
os.environ["LOAM_LOCK_DIR"] = str(ROOT / "locks")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_engineer as eng  # noqa: E402
import loam_lock  # noqa: E402
import loam_worktree as wt  # noqa: E402

wt.WORKTREES = ROOT / "worktrees"
loam_lock.LOCK_DIR = ROOT / "locks"
os.environ.pop(loam_lock.LIVE_HELD_VAR, None)

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} {got!r}"[:240]
          + ("" if ok else f"\n      wanted {want!r}"))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True, text=True, check=True).stdout.strip()


def make_project(name: str) -> eng.Project:
    origin = ROOT / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = ROOT / f"{name}-seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    for i in range(1, 6):
        (seed / f"f{i}.txt").write_text(f"{i}\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "start")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "main")
    checkout = ROOT / name
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
    git(checkout, "config", "user.name", f"{name} engineer")
    git(checkout, "config", "user.email", f"engineer+{name}@noreply.local")
    record = {"name": name, "checkout": str(checkout), "test_command": "true"}
    return eng.Project(name, checkout, ROOT / f"{name}-jobs", platform=False,
                       record=record)


def start(project: eng.Project, job_id: str) -> dict:
    job_dir = project.jobs / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return wt.setup(project, job_id, job_dir, None)


def commit(tree: Path, path: str, text: str) -> str:
    (tree / path).write_text(text)
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", f"edit {path}")
    return git(tree, "rev-parse", "HEAD")


def on_branch(tree: Path) -> bool:
    return subprocess.run(["git", "-C", str(tree), "symbolic-ref", "-q", "HEAD"],
                          capture_output=True).returncode == 0


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor",
                           older, newer], capture_output=True).returncode == 0


real_run_tests = wt.run_tests
try:
    # -- detached: a commit on the landing's detached merge --------------------
    proj = make_project("detached")
    other = start(proj, "20261119-100001-oooooooo")
    job = start(proj, "20261119-100002-jjjjjjjj")
    tree = Path(job["path"])
    commit(Path(other["path"]), "f1.txt", "one from other\n")
    check("detached: another job lands first",
          wt.land(proj, other, "Land other")["state"], "landed")
    commit(tree, "f2.txt", "two from job\n")
    made: list[str] = []
    seen_detached: list[bool] = []

    def during(project, t):
        if not made:
            # The session, still working while its `--land` tests.
            seen_detached.append(not on_branch(t))
            made.append(commit(t, "f3.txt", "three, made while landing\n"))
        return real_run_tests(project, t)

    wt.run_tests = during
    mid = wt.land(proj, job, "Land mid-job", keep_tree=True)
    wt.run_tests = real_run_tests
    branch = job["branch"]
    check("...the tests ran on a detached HEAD", seen_detached, [True])
    check("...the mid-job landing landed", mid["state"], "landed")
    check("...the commit made meanwhile is on the branch",
          git(tree, "show", f"{branch}:f3.txt"), "three, made while landing")
    check("...the tree is on its branch, clean",
          (git(tree, "symbolic-ref", "--short", "HEAD"), git(tree, "status", "--porcelain")),
          (branch, ""))
    end = wt.land(proj, job, "Land at the end")
    check("...and nothing of it is left to land at the end",
          end["state"] in ("landed", "landed_earlier"), True)
    check("...main has it", git(proj.root, "show", "main:f3.txt"),
          "three, made while landing")

    # -- branch: a fast-forward tested on the branch ---------------------------
    proj2 = make_project("branch")
    job2 = start(proj2, "20261119-100003-bbbbbbbb")
    tree2 = Path(job2["path"])
    commit(tree2, "f1.txt", "one\n")
    made2: list[str] = []

    def during2(project, t):
        if not made2:
            made2.append(commit(t, "f4.txt", "four, made while landing\n"))
        return real_run_tests(project, t)

    wt.run_tests = during2
    mid2 = wt.land(proj2, job2, "Land mid-job", keep_tree=True)
    wt.run_tests = real_run_tests
    check("branch: landed", mid2["state"], "landed")
    check("...with the commit made meanwhile",
          git(proj2.root, "show", "main:f4.txt"), "four, made while landing")

    # -- locked: no outside tries, so nothing re-tests -------------------------
    proj3 = make_project("locked")
    job3 = start(proj3, "20261119-100004-llllllll")
    tree3 = Path(job3["path"])
    other3 = start(proj3, "20261119-100005-pppppppp")
    commit(Path(other3["path"]), "f1.txt", "one from other\n")
    wt.land(proj3, other3, "Land other")
    commit(tree3, "f2.txt", "two\n")
    made3: list[str] = []

    def during3(project, t):
        if not made3:
            made3.append(commit(t, "f5.txt", "five, made while landing\n"))
        return real_run_tests(project, t)

    tries = wt.OUTSIDE_TRIES
    wt.OUTSIDE_TRIES = 0
    wt.run_tests = during3
    mid3 = wt.land(proj3, job3, "Land mid-job", keep_tree=True)
    wt.run_tests = real_run_tests
    wt.OUTSIDE_TRIES = tries
    b3 = job3["branch"]
    check("locked: landed under the lock",
          (mid3["state"], mid3.get("tested_under_lock")), ("landed", True))
    check("...the commit is on the branch, on top of what landed",
          (git(tree3, "show", f"{b3}:f5.txt"), is_ancestor(tree3, mid3["sha"], b3)),
          ("five, made while landing", True))
    end3 = wt.land(proj3, job3, "Land at the end")
    check("...and lands at the end",
          (end3["state"], git(proj3.root, "show", "main:f5.txt")),
          ("landed", "five, made while landing"))
finally:
    wt.run_tests = real_run_tests
    shutil.rmtree(ROOT, ignore_errors=True)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
