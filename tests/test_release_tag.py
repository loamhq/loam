"""Tag only the tested, pushed commit whose version matches the release request."""

import json
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

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} {got!r}"
          + ("" if ok else f"\n      wanted {want!r}"))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True, text=True, check=True).stdout.strip()


root = Path(tempfile.mkdtemp(prefix="loam-release-test-"))
saved = (wt.WORKTREES, loam_lock.LOCK_DIR)
wt.WORKTREES = root / "worktrees"
loam_lock.LOCK_DIR = root / "locks"
os.environ.pop(loam_lock.LIVE_HELD_VAR, None)


def make_project(name: str) -> eng.Project:
    origin = root / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = root / f"{name}-seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "manifest.json").write_text(json.dumps({"version": "1.0.0"}))
    (seed / "package.json").write_text(json.dumps({"version": "1.0.0"}))
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "1.0.0")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "main")
    checkout = root / name
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
    git(checkout, "config", "user.name", f"{name} engineer")
    git(checkout, "config", "user.email", f"engineer+{name}@noreply.local")
    return eng.Project(name, checkout, root / f"{name}-jobs", platform=False,
                       record={"name": name, "checkout": str(checkout),
                               "test_command": "true"})


def start(project: eng.Project, job_id: str) -> dict:
    job_dir = project.jobs / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return wt.setup(project, job_id, job_dir, None)


def bump(record: dict, manifest: str, package: str) -> str:
    tree = Path(record["path"])
    (tree / "manifest.json").write_text(json.dumps({"version": manifest}))
    (tree / "package.json").write_text(json.dumps({"version": package}))
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", f"bump {manifest}")
    return git(tree, "rev-parse", "HEAD")


def origin_tags(project: eng.Project) -> dict[str, str]:
    out = git(project.root, "ls-remote", "--tags", "origin")
    return {ref.rsplit("/", 1)[1]: sha for sha, ref in
            (line.split() for line in out.splitlines())}


def origin_main(project: eng.Project) -> str:
    return git(project.root, "ls-remote", "origin", "refs/heads/main").split()[0]


def land(project: eng.Project, record: dict, job_dir: Path):
    result = wt.land(project, record, f"Land {record['branch']}",
                     release=wt.read_release(job_dir))
    return result, wt.receipt_section(project, record["job"], result)


try:
    proj = make_project("plugin")

    # A tag that matches: pushed, at the landed commit, after main.
    a = start(proj, "20261002-140000-aaaaaaaa")
    a_dir = proj.jobs / a["job"]
    tip = bump(a, "1.1.0", "1.1.0")
    (a_dir / wt.RELEASE_FILE).write_text("1.1.0\n")
    result, text = land(proj, a, a_dir)
    check("matching tag: the branch landed and main was pushed",
          (result["state"], result["pushed"], origin_main(proj)),
          ("landed", True, tip))
    check("matching tag: pushed to origin at the landed commit",
          (result["release"]["state"], origin_tags(proj).get("1.1.0")),
          ("pushed", tip))
    check("matching tag: the receipt says so",
          "Release tag `1.1.0` pushed" in text, True)

    # A tag that does not match: refused, the merge intact, no tag anywhere.
    b = start(proj, "20261002-140001-bbbbbbbb")
    b_dir = proj.jobs / b["job"]
    tip_b = bump(b, "1.2.0", "1.2.0")
    (b_dir / wt.RELEASE_FILE).write_text("1.3.0\n")
    result, text = land(proj, b, b_dir)
    check("mismatched tag: the merge still landed and was pushed",
          (result["state"], result["pushed"], origin_main(proj)),
          ("landed", True, tip_b))
    check("mismatched tag: refused, and no tag on origin or here",
          (result["release"]["state"], "1.3.0" in origin_tags(proj),
           git(proj.root, "tag", "-l", "1.3.0")),
          ("refused", False, ""))
    check("mismatched tag: the receipt names the versions",
          ("Release tag `1.3.0` refused" in text,
           "manifest.json says 1.2.0" in text), (True, True))

    # The two version files disagreeing refuses a tag matching only one.
    c = start(proj, "20261002-140002-cccccccc")
    c_dir = proj.jobs / c["job"]
    bump(c, "1.4.0", "1.3.9")
    (c_dir / wt.RELEASE_FILE).write_text("1.4.0")
    result, _ = land(proj, c, c_dir)
    check("a tag matching manifest.json but not package.json is refused",
          (result["state"], result["release"]["state"],
           "1.4.0" in origin_tags(proj)), ("landed", "refused", False))

    # A branch that does not land cuts no tag.
    d = start(proj, "20261002-140003-dddddddd")
    d_dir = proj.jobs / d["job"]
    bump(d, "1.5.0", "1.5.0")
    (Path(d["path"]) / "stray.txt").write_text("uncommitted\n")
    (d_dir / wt.RELEASE_FILE).write_text("1.5.0")
    result, text = land(proj, d, d_dir)
    check("a branch that did not land: no tag",
          (result["state"], result["release"]["state"],
           "1.5.0" in origin_tags(proj)), ("not_landed", "refused", False))

    # No tag asked for: the landing is what it always was.
    e = start(proj, "20261002-140004-eeeeeeee")
    bump(e, "1.6.0", "1.6.0")
    result, text = land(proj, e, proj.jobs / e["job"])
    check("no release-tag file: no release key, no tag",
          ("release" in result, "Release tag" in text,
           "1.6.0" in origin_tags(proj)), (False, False, False))
finally:
    wt.WORKTREES, loam_lock.LOCK_DIR = saved
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
