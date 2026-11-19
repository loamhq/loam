"""Check upstream branch visibility, pruning and stale-mirror reporting through temporary Git remotes."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_engineer as eng  # noqa: E402
import loam_lock  # noqa: E402
import loam_worktree as wt  # noqa: E402

WITHOUT_REFRESH = "--without-refresh" in sys.argv[1:]
BRANCH = "docs/acme-hipaa-platform-blueprint"

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} {got!r}"
          + ("" if ok else f"\n      wanted {want!r}"))


def git(repo: Path, *args: str, check: bool = True) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True, text=True, check=check)
    return out.stdout.strip()


def ok(repo: Path, *args: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True).returncode == 0


root = Path(tempfile.mkdtemp(prefix="loam-mirror-test-"))
saved = (wt.WORKTREES, loam_lock.LOCK_DIR, wt.refresh_mirror)
wt.WORKTREES = root / "worktrees"
loam_lock.LOCK_DIR = root / "locks"
real_refresh = wt.refresh_mirror


def no_refresh(_repo, _url):
    return None


def make_project(name: str, mirror: bool = True) -> tuple[eng.Project, Path, Path]:
    """(project, the bare "GitHub", a clone of it that pushes as a person).
    The project's record sets `mirror_refresh` unless `mirror` is False."""
    github = root / f"{name}-github.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(github)], check=True)
    pusher = root / f"{name}-pusher"
    subprocess.run(["git", "init", "-q", "-b", "main", str(pusher)], check=True)
    (pusher / "README.md").write_text("start\n")
    git(pusher, "add", "-A")
    git(pusher, "commit", "-qm", "start")
    git(pusher, "remote", "add", "origin", str(github))
    git(pusher, "push", "-q", "origin", "main")
    checkout = root / name
    subprocess.run(["git", "clone", "-q", str(github), str(checkout)], check=True)
    git(checkout, "config", "user.name", f"{name} engineer")
    git(checkout, "config", "user.email", f"engineer+{name}@noreply.local")
    record = {"name": name, "checkout": str(checkout), "remote": str(github)}
    if mirror:
        record["mirror_refresh"] = True
    project = eng.Project(name, checkout, root / f"{name}-jobs", platform=False,
                          remote=str(github), record=record)
    return project, github, pusher


def push_branch(pusher: Path, branch: str, text: str) -> str:
    git(pusher, "checkout", "-q", "-B", branch, "main")
    (pusher / "blueprint.md").write_text(text)
    git(pusher, "add", "-A")
    git(pusher, "commit", "-qm", f"add {branch}")
    git(pusher, "push", "-q", "origin", branch)
    sha = git(pusher, "rev-parse", "HEAD")
    git(pusher, "checkout", "-q", "main")
    return sha


def start(project: eng.Project, job_id: str) -> dict:
    job_dir = project.jobs / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return wt.setup(project, job_id, job_dir)


def remote_branches(tree: Path) -> list[str]:
    return git(tree, "branch", "-r", "--format=%(refname:short)").split()


try:
    # -----------------------------------------------------------------------
    print("\n# A branch pushed to GitHub after the mirror was cloned")
    # -----------------------------------------------------------------------
    acme, github, pusher = make_project("acme")
    sha = push_branch(pusher, BRANCH, "the blueprint\n")
    check("the mirror does not have the branch before any job starts",
          ok(acme.root, "rev-parse", "--verify", f"origin/{BRANCH}"), False)
    if WITHOUT_REFRESH:
        wt.refresh_mirror = no_refresh
    record = start(acme, "20261009-132136-6d5239b2")
    wt.refresh_mirror = real_refresh
    tree = Path(record["path"])
    check("from inside the job's tree, `git branch -r` lists it",
          f"origin/{BRANCH}" in remote_branches(tree), True)
    check("and it resolves to the commit GitHub has",
          git(tree, "rev-parse", f"origin/{BRANCH}", check=False), sha)
    # `--track`, because git's bare-name guess only follows a remote whose
    # source refs are `refs/heads/*`, and the mirror's copies are not.
    check("`git checkout --track origin/<branch>` in the tree checks it out",
          ok(tree, "checkout", "-q", "--track", f"origin/{BRANCH}")
          and (tree / "blueprint.md").read_text() == "the blueprint\n", True)
    git(tree, "checkout", "-q", record["branch"])
    check("the job is still on its own branch, cut from main",
          (git(tree, "symbolic-ref", "--short", "HEAD"),
           git(tree, "rev-parse", "HEAD") == git(acme.root, "rev-parse", "main")),
          (record["branch"], True))
    check("the job's own `git fetch` keeps the branch (it reads the mirror)",
          ok(tree, "fetch", "--prune", "origin")
          and f"origin/{BRANCH}" in remote_branches(tree), True)
    if not WITHOUT_REFRESH:
        cell = wt.mirror_row(record)
        print(f"      receipt row: {cell.strip()}")
        check("the receipt's mirror row names the remote and the count",
              (cell.startswith(f"\n| mirror | refreshed from `{github}` at "),
               cell.endswith("the mirror holds 2 branches |")), (True, True))

    # -----------------------------------------------------------------------
    print("\n# The control: the same setup on a project without `mirror_refresh`")
    # -----------------------------------------------------------------------
    other, _github2, pusher2 = make_project("loam-ui", mirror=False)
    push_branch(pusher2, BRANCH, "the blueprint\n")
    blind = start(other, "20261009-000000-control")
    blind_tree = Path(blind["path"])
    check("without the key the job cannot see the branch",
          f"origin/{BRANCH}" in remote_branches(blind_tree), False)
    check("its clone keeps git's default view of the canonical",
          (remote_branches(blind_tree),
           git(blind_tree, "config", "--get-all", "remote.origin.fetch")),
          (["origin/HEAD", "origin/main"], "+refs/heads/*:refs/remotes/origin/*"))
    check("nothing was fetched from GitHub into the canonical",
          (ok(other.root, "rev-parse", "--verify", f"origin/{BRANCH}"),
           (wt.common_dir(other.root) / wt.MIRROR_STATE).exists()), (False, False))
    check("the record has no mirror and the receipt no mirror row",
          ("mirror" in blind, wt.mirror_row(blind)), (False, ""))

    if not WITHOUT_REFRESH:
        # -------------------------------------------------------------------
        print("\n# Tags, a deleted branch, a failed refresh")
        # -------------------------------------------------------------------
        git(pusher, "tag", "v1.0.0", "main")
        git(pusher, "push", "-q", "origin", "v1.0.0")
        git(pusher, "push", "-q", "origin", "--delete", BRANCH)
        second = start(acme, "20261009-134034-a41d47a0")
        tree2 = Path(second["path"])
        check("a tag pushed after the clone is in the next job's tree",
              ok(tree2, "rev-parse", "--verify", "refs/tags/v1.0.0"), True)
        check("a branch deleted on GitHub is gone from the next job's view",
              f"origin/{BRANCH}" in remote_branches(tree2), False)
        check("and from the mirror itself (pruned)",
              ok(acme.root, "rev-parse", "--verify", f"origin/{BRANCH}"), False)
        first_ok = second["mirror"]["last_ok"]

        git(acme.root, "remote", "set-url", "origin", str(root / "gone.git"))
        failed = start(acme, "20261009-150000-offline00")
        cell = wt.mirror_row(failed)
        print(f"      receipt row: {cell.strip()}")
        check("a failed refresh still makes the tree (the job runs)",
              Path(failed["path"]).is_dir(), True)
        check("and the row says it failed and when the last one succeeded",
              (cell.startswith("\n| mirror | **refresh failed**"),
               f"its last successful refresh was {first_ok}" in cell), (True, True))
        check("the record keeps the last success",
              (failed["mirror"]["ok"], failed["mirror"]["last_ok"]), (False, first_ok))
finally:
    wt.WORKTREES, loam_lock.LOCK_DIR, wt.refresh_mirror = saved
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'all passed' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
