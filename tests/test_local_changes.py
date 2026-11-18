#!/usr/bin/env python3
"""A change to Loam's own parts on a release install, and what an update
does to it (2026-11-17).

Real git repositories in a temp directory, made the way an install is made:
a bare `origin` (Loam's public repository) whose `main` has moved past the
release, a clone of it at the release tag (install.sh's), and a clone of that
as the checkout (bin/loam-install's), detached at the tag with no branch at
all. Then, through lib/loam_worktree.py exactly as bin/engineer-job uses it:

  - before: a job's tree cannot be made from such a checkout (the premise
    this fixes: "Remote branch main not found");
  - the first Loam job makes the install's own `main` at the release, with
    nothing in the tree moved, and marks it local; a checkout that has a
    `main` already is left alone and never marked;
  - a job's change lands on that `main`, is never pushed, and the canonical
    is never fast-forwarded to `origin/main`'s unreleased development; the
    receipt says the change stayed on the install;
  - `loam update`'s rules on that checkout: a release that touches other
    files merges, keeping the local change; one that changes the same lines
    is refused before anything moves, naming the file, and the note for the
    agent says to offer sending it upstream and a revert, never a discard.

Run: python3 tests/test_local_changes.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-local-changes-"))
INSTALL = TMP / "install"
CONFIG = TMP / "loam.toml"
CONFIG.write_text(f'''
[owner]
id = "sam"
name = "Sam"
[paths]
home = "{TMP}/home"
root = "{INSTALL}"
vault = "{TMP}/vault"
workspaces = "{TMP}/agents"
state = "{TMP}/state"
''')
os.environ["LOAM_CONFIG"] = str(CONFIG)
for var in ("LOAM_VAULT", "LOAM_AI_LABS", "LOAM_UPDATE_RESTART", "LOAM_SESSION_ID"):
    os.environ.pop(var, None)
os.environ["LOAM_UPDATE_STATE"] = str(TMP / "state" / "update")
os.environ["LOAM_LOCK_DIR"] = str(TMP / "locks")
os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test",
                   "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})

sys.path.insert(0, str(ROOT / "lib"))
import loam_engineer as eng  # noqa: E402
import loam_lock  # noqa: E402
import loam_update as up  # noqa: E402
import loam_worktree as wt  # noqa: E402

wt.WORKTREES = TMP / "worktrees"
loam_lock.LOCK_DIR = TMP / "locks"
os.environ.pop(loam_lock.LIVE_HELD_VAR, None)

FAILURES = 0


def check(label: str, got, want) -> None:
    global FAILURES  # noqa: PLW0603
    ok = got == want
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def run(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True)


def git(repo: Path, *args: str) -> str:
    out = run("git", "-C", str(repo), *args)
    if out.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr}")
    return out.stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


ORIGIN, HQ = TMP / "origin.git", TMP / "hq"


def release(version: str, changes: dict[str, str]) -> None:
    for rel, text in changes.items():
        write(HQ / rel, text)
    write(HQ / "manifest.json", json.dumps({"version": version}) + "\n")
    git(HQ, "add", "-A")
    git(HQ, "commit", "-qm", f"Release {version}")
    git(HQ, "tag", "-a", version, "-m", f"Loam {version}")
    git(HQ, "push", "-q", "origin", "main", "--tags")


try:
    run("git", "init", "-q", "--bare", "-b", "main", str(ORIGIN))
    run("git", "init", "-q", "-b", "main", str(HQ))
    git(HQ, "remote", "add", "origin", str(ORIGIN))
    write(HQ / ".gitignore", "*.pyc\n")
    write(HQ / "lines.txt", "one\ntwo\nthree\n")
    write(HQ / "engineer" / "memory" / "MEMORY.md", "# Memory\n")
    write(HQ / "tests" / "run-all", "import sys\nsys.exit(0)\n")
    write(HQ / "docs" / "library" / "README.md", "# Library\n")
    release("1.0.0", {})
    # Development goes on past the release on the public repository's main.
    write(HQ / "unreleased.txt", "not in any release\n")
    git(HQ, "add", "-A")
    git(HQ, "commit", "-qm", "Unreleased work")
    git(HQ, "push", "-q", "origin", "main")
    dev_tip = git(HQ, "rev-parse", "HEAD")

    # -- the install, as install.sh and bin/loam-install make it --------------
    release_dir = TMP / "release"
    run("git", "clone", "-q", "--branch", "1.0.0", str(ORIGIN), str(release_dir))
    run("git", "clone", "-q", "--no-hardlinks", str(release_dir), str(INSTALL))
    git(INSTALL, "remote", "set-url", "origin", str(ORIGIN))
    tag_commit = git(INSTALL, "rev-parse", "1.0.0^{commit}")
    check("the install is detached at the release",
          run("git", "-C", str(INSTALL), "symbolic-ref", "-q", "HEAD").returncode, 1)
    probe = run("git", "clone", "-q", "--shared", "--no-checkout", "--branch", "main",
                str(INSTALL), str(TMP / "probe"))
    check("before: a job's clone cannot be made from it (the premise)",
          "Remote branch main not found" in probe.stderr, True)

    project = eng.Project("Loam", INSTALL / "engineer", TMP / "jobs", platform=True)

    def start(job_id: str) -> dict:
        d = project.jobs / job_id
        d.mkdir(parents=True, exist_ok=True)
        return wt.setup(project, job_id, d)

    rec = start("20261117-000001-aaaaaaaa")
    check("the first job makes the install's own main", "local_main" in rec, True)
    check("main is the release it runs", git(INSTALL, "rev-parse", "main"), tag_commit)
    check("HEAD is on it", git(INSTALL, "symbolic-ref", "HEAD"), "refs/heads/main")
    check("it is marked local", wt.local_main(INSTALL), True)
    check("nothing in the tree moved", git(INSTALL, "status", "--porcelain"), "")
    check("a second call does nothing", wt.ensure_local_main(INSTALL), None)

    tree = Path(rec["path"])
    write(tree / "lines.txt", "one\nTWO, fixed here\nthree\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", "Fix the second line")
    result = wt.land(project, rec, "Land the fix")
    check("the change lands", result["state"], "landed")
    check("it is local only", result.get("local_only"), True)
    check("nothing was pushed", git(ORIGIN, "rev-parse", "main"), dev_tip)
    main_now = git(INSTALL, "rev-parse", "main")
    check("main never took origin's unreleased work",
          run("git", "-C", str(INSTALL), "merge-base", "--is-ancestor", dev_tip,
              main_now).returncode, 1)
    check("the live checkout has the change",
          (INSTALL / "lines.txt").read_text(), "one\nTWO, fixed here\nthree\n")
    words = wt.receipt_section(project, "20261117-000001-aaaaaaaa", result)
    check("the receipt says it stayed on the install", "Kept on this install" in words, True)

    rec2 = start("20261117-000002-bbbbbbbb")
    check("a later job starts from the local main, not origin's",
          git(Path(rec2["path"]), "rev-parse", "main"), main_now)
    check("and makes no main again", "local_main" in rec2, False)
    wt.remove_tree(project, rec2, delete_branch=True)

    # A checkout that has a main (a box that develops Loam) is left alone.
    dev_box = TMP / "devbox"
    run("git", "clone", "-q", str(ORIGIN), str(dev_box))
    check("a development checkout gets no local main",
          (wt.ensure_local_main(dev_box), wt.local_main(dev_box)), (None, False))

    # -- loam update on that checkout --------------------------------------------
    cur = up.current()
    check("the update reads it as a development install",
          (cur["kind"], cur["local_commits"], cur["branch"]), ("development", 1, "main"))

    release("1.1.0", {"other.txt": "a new file\n"})
    up.fetch_tag("1.1.0")
    check("a release that touches other files is not blocked",
          up.blockers("development", "1.1.0"), None)
    up.move_to("1.1.0", "development")
    check("the update merged it", json.loads((INSTALL / "manifest.json").read_text())["version"],
          "1.1.0")
    check("the local change is kept", (INSTALL / "lines.txt").read_text(),
          "one\nTWO, fixed here\nthree\n")
    check("and is still the install's own commit", up.current()["local_commits"], 1)

    release("1.2.0", {"lines.txt": "one\n2 (the release's own fix)\nthree\n"})
    up.fetch_tag("1.2.0")
    before = git(INSTALL, "rev-parse", "HEAD")
    refusal = up.blockers("development", "1.2.0")
    check("a release that changes the same lines is refused",
          (refusal.code, refusal.detail.get("files")), ("conflict", ["lines.txt"]))
    check("nothing moved", git(INSTALL, "rev-parse", "HEAD"), before)
    check("the change is still there", (INSTALL / "lines.txt").read_text(),
          "one\nTWO, fixed here\nthree\n")
    up.tell_refused("1.2.0", refusal)
    notes = [json.loads(p.read_text()) for p in
             sorted((TMP / "state" / "update" / "notices").glob("*.json"))]
    note = notes[-1]["note"] if notes else ""
    check("the agent is told to offer sending it upstream", "report bug --patch" in note, True)
    check("and a revert that keeps the history", "git revert" in note, True)
    check("and a plugin for the change made again", "plugin" in note, True)
    check("and never to discard it", "Never discard a change of theirs" in note, True)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'FAILED' if FAILURES else 'OK'}: {FAILURES} failure(s)")
sys.exit(1 if FAILURES else 0)
