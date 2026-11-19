"""Verify backup refspecs, pending retries and independence from the release remote."""

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_engineer as eng  # noqa: E402
import loam_lock  # noqa: E402
import loam_worktree as wt  # noqa: E402

LAUNCHER = str(Path(__file__).resolve().parent.parent / "bin" / "engineer-job")
loader = importlib.machinery.SourceFileLoader("engineer_job_backup", LAUNCHER)
spec = importlib.util.spec_from_loader("engineer_job_backup", loader)
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} {got!r}"
          + ("" if ok else f"\n      wanted {want!r}"))


def git(repo: Path, *args: str, ok: bool = True) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True, text=True, check=ok).stdout.strip()


def heads(bare: Path) -> dict[str, str]:
    out = git(bare, "for-each-ref", "--format=%(refname:short) %(objectname)",
              "refs/heads/", "refs/tags/")
    return dict(line.split() for line in out.splitlines())


root = Path(tempfile.mkdtemp(prefix="loam-backup-test-"))
saved = (wt.WORKTREES, loam_lock.LOCK_DIR, eng.projects)
wt.WORKTREES = root / "worktrees"
loam_lock.LOCK_DIR = root / "locks"
os.environ.pop(loam_lock.LIVE_HELD_VAR, None)

try:
    # The off-box repository: older, unrelated history on its default branch.
    backup = root / "old.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(backup)], check=True)
    old = root / "old"
    subprocess.run(["git", "init", "-q", "-b", "main", str(old)], check=True)
    (old / "old.txt").write_text("old\n")
    git(old, "add", "-A")
    git(old, "commit", "-qm", "old history")
    git(old, "push", "-q", str(backup), "main")
    old_main = git(old, "rev-parse", "HEAD")

    # The canonical checkout: no origin, a local tag, an excluded file.
    checkout = root / "loam"
    subprocess.run(["git", "init", "-q", "-b", "main", str(checkout)], check=True)
    (checkout / "a.txt").write_text("a\n")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-qm", "start")
    git(checkout, "tag", "0.1.0")
    (checkout / ".git" / "info" / "exclude").write_text("/private\n")
    (checkout / "private").mkdir()
    (checkout / "private" / "secret.txt").write_text("not for off the box\n")
    git(checkout, "config", "user.name", "loam engineer")
    git(checkout, "config", "user.email", "engineer@noreply.local")
    project = eng.Project("Loam", checkout, root / "jobs", platform=False,
                          record={"name": "Loam", "checkout": str(checkout),
                                  "test_command": "true"})
    project.jobs.mkdir()
    eng.projects = lambda: {"Loam": project}

    def land_one(job_id: str, name: str) -> tuple[dict, str]:
        job_dir = project.jobs / job_id
        job_dir.mkdir()
        record = wt.setup(project, job_id, job_dir, None)
        tree = Path(record["path"])
        (tree / name).write_text(name + "\n")
        git(tree, "add", "-A")
        git(tree, "commit", "-qm", f"add {name}")
        result = wt.land(project, record, f"Land {record['branch']}")
        return result, wt.receipt_section(project, job_id, result)

    # With neither remote: lands locally, as before.
    result, text = land_one("20261116-090000-aaaaaaa1", "b.txt")
    check("no remote at all: landed, nothing pushed, no backup key",
          (result["state"], result["pushed"], "backup" in result), ("landed", None, False))
    check("no remote at all: the receipt says so",
          "No remote to push to." in text and "Backed up" not in text, True)
    check("no backup remote: the sweep has nothing pending",
          (wt.backup_pending(checkout), job.push_pending(project)), (False, None))

    # The backup remote, mapped to its own branch.
    git(checkout, "remote", "add", wt.BACKUP, str(backup))
    git(checkout, "config", f"remote.{wt.BACKUP}.push", "refs/heads/main:refs/heads/loam")
    check("a backup remote alone is not an origin",
          (wt.has_remote(checkout), wt.has_remote(checkout, wt.BACKUP)), (False, True))
    check("main not yet backed up: the sweep sees it pending",
          wt.backup_pending(checkout), True)
    swept = job.push_pending(project)
    main_now = git(checkout, "rev-parse", "main")
    check("the sweep pushes main to the mapped branch",
          (swept["pushed"], swept["remote"], heads(backup).get("loam")),
          (True, wt.BACKUP, main_now))
    check("a second sweep: nothing pending, nothing reported",
          (wt.backup_pending(checkout), job.push_pending(project)), (False, None))

    result, text = land_one("20261116-090000-aaaaaaa2", "c.txt")
    main_now = git(checkout, "rev-parse", "main")
    check("a landing pushes main to the backup's branch",
          (result["state"], result["pushed"], result["backup"],
           heads(backup).get("loam")), ("landed", None, True, main_now))
    check("the backup's own main, its old history, is untouched",
          heads(backup).get("main"), old_main)
    check("no tag and no other branch went: only main and loam there",
          sorted(heads(backup)), ["loam", "main"])
    check("the excluded file did not go",
          git(backup, "ls-tree", "-r", "--name-only", "loam").split(),
          ["a.txt", "b.txt", "c.txt"])
    check("the receipt says it was backed up",
          "No remote to push to. Backed up off the box to `backup`." in text, True)
    check("after the landing the sweep has nothing pending",
          job.push_pending(project), None)

    # A release still needs origin: the backup is not one.
    rel = wt.cut_release(checkout, result, "0.2.0")
    check("a release tag is refused with no origin, the backup unasked",
          (rel["state"], "0.2.0" in heads(backup)), ("refused", False))

    # A failed backup push: named in the receipt, retried by the sweep.
    git(checkout, "remote", "set-url", wt.BACKUP, str(root / "gone.git"))
    result, text = land_one("20261116-090000-aaaaaaa3", "d.txt")
    check("an unreachable backup: landed all the same, failure kept",
          (result["state"], isinstance(result["backup"], str)), ("landed", True))
    check("the receipt names the failed backup push",
          "**The backup push failed**" in text, True)
    check("the sweep sees it pending", wt.backup_pending(checkout), True)
    failed = job.push_pending(project)
    check("the sweep's retry fails and says so",
          (failed["pushed"], bool(failed["error"])), (False, True))
    git(checkout, "remote", "set-url", wt.BACKUP, str(backup))
    retried = job.push_pending(project)
    check("once reachable, the sweep's retry pushes what landed",
          (retried["pushed"], heads(backup).get("loam")),
          (True, git(checkout, "rev-parse", "main")))
finally:
    wt.WORKTREES, loam_lock.LOCK_DIR, eng.projects = saved
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'all passed' if not fails else f'{fails} failed'}")
sys.exit(1 if fails else 0)
