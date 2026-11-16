"""The recovery sweep's pending push and a project that lands by pull request.

Found on 2026-10-13: the sweep fetched acme and acme-foundation, which land by
pull request, without the job's credential, and logged `fetch failed` for each
every five minutes from 10-07. Their `main` mirrors the remote and is never
pushed, so the sweep has nothing to do there; it skips them. A push project
whose fetch really fails still produces its line, with git's reason.

Loads this tree's bin/engineer-job and lib/, not the live ones, so it tests
the change it ships with.

Run: python3 tests/test_engineer_recover_pr.py
"""

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import loam_engineer as eng  # noqa: E402
import loam_lock  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "engineer_job_recover_pr", str(ROOT / "bin" / "engineer-job"))
spec = importlib.util.spec_from_loader("engineer_job_recover_pr", loader)
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


root = Path(tempfile.mkdtemp(prefix="loam-recover-pr-test-"))
saved_lock_dir = loam_lock.LOCK_DIR
os.environ.pop(loam_lock.LIVE_HELD_VAR, None)
try:
    loam_lock.LOCK_DIR = root / "locks"
    loam_lock.LOCK_DIR.mkdir()

    def clone_with_dead_remote(name: str) -> Path:
        """A checkout whose origin cannot be fetched, as an https remote with
        no credential cannot."""
        origin = root / f"{name}.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        seed = root / f"{name}-seed"
        subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
        (seed / "a.txt").write_text("a\n")
        git(seed, "add", "-A")
        git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "start")
        git(seed, "push", "-q", str(origin), "main")
        checkout = root / name
        subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
        shutil.rmtree(origin)
        return checkout

    pr = eng.Project("adopted", clone_with_dead_remote("adopted"), root / "adopted-jobs",
                     platform=False, record={"name": "adopted", "landing": "pull_request"})
    push = eng.Project("owned", clone_with_dead_remote("owned"), root / "owned-jobs",
                       platform=False, record={"name": "owned"})

    check("a pull-request project: no line, so no `fetch failed`",
          job.push_pending(pr), None)
    line = job.push_pending(push)
    check("a push project whose fetch fails still says so",
          (line or {}).get("push"), "fetch failed")
    check("and carries git's reason", bool((line or {}).get("error")), True)
finally:
    loam_lock.LOCK_DIR = saved_lock_dir
    shutil.rmtree(root, ignore_errors=True)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
