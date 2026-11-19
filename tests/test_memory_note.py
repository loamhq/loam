"""The engineer's memory note never blocks a landing (since 2026-11-19;
lib/loam_worktree.py `land`, "The engineer's memory note never blocks a
landing").

Asserted against real git repositories in a temp directory (a bare
`origin`, a canonical checkout, job trees made by lib/loam_worktree.py as
bin/engineer-job makes them), with a test command that fails when told to:

  - the work landed mid-job, then a note alone meets failing tests: the run
    is tried once more, the result is `landed_earlier` (no continuation is
    sent for it), the note is kept as a patch in the job's folder, and the
    receipt says so;
  - a tree whose only uncommitted change is the memory file has it
    committed by the landing, and lands;
  - a failing test with work on the branch is still refused (the one hard
    gate: `main` moves only by a merge that passed);
  - with the push gate on: a note that names a denied word is held and the
    work lands as one commit without it; `main` and `origin` never have the
    word; a denied word in the work itself is still refused; a commit
    message naming one lands as one commit under the landing's message;
    a note alone that names one is `nothing` with the note kept.

Run: python3 tests/test_memory_note.py
"""

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
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} {got!r}"
          + ("" if ok else f"\n      wanted {want!r}"))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


root = Path(tempfile.mkdtemp(prefix="loam-note-test-"))
saved = (wt.WORKTREES, loam_lock.LOCK_DIR)
wt.WORKTREES = root / "worktrees"
loam_lock.LOCK_DIR = root / "locks"
os.environ.pop(loam_lock.LIVE_HELD_VAR, None)
NAME, EMAIL = "Demo engineer", "engineer+demo@noreply.local"
FAIL = root / "fail-now"
RUNS = root / "runs"


def make(name: str) -> tuple[eng.Project, Path]:
    origin = root / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    checkout = root / name
    subprocess.run(["git", "init", "-q", "-b", "main", str(checkout)], check=True)
    git(checkout, "config", "user.name", NAME)
    git(checkout, "config", "user.email", EMAIL)
    (checkout / "a.txt").write_text("a\n")
    (checkout / "memory").mkdir()
    (checkout / "memory" / "MEMORY.md").write_text("# Memory\n")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-qm", "start")
    git(checkout, "remote", "add", "origin", str(origin))
    git(checkout, "push", "-q", "origin", "main")
    git(checkout, "fetch", "-q", "origin")
    command = f"echo run >> {RUNS}; test ! -e {FAIL}"
    project = eng.Project(name, checkout, root / f"{name}-jobs", platform=False,
                          record={"name": name, "checkout": str(checkout),
                                  "test_command": command})
    project.jobs.mkdir()
    return project, origin


def start(project: eng.Project, job_id: str) -> dict:
    job_dir = project.jobs / job_id
    job_dir.mkdir()
    return wt.setup(project, job_id, job_dir, None)


def write(record: dict, name: str, text: str) -> None:
    path = Path(record["path"]) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def commit(record: dict, name: str, text: str, msg: str | None = None) -> None:
    write(record, name, text)
    git(Path(record["path"]), "add", "-A")
    git(Path(record["path"]), "commit", "-qm", msg or f"add {name}")


def land(project, record, keep_tree=False):
    result = wt.land(project, record, f"Land {record['branch']}", keep_tree=keep_tree)
    return result, wt.receipt_section(project, record["job"], result)


def runs() -> int:
    n = len(RUNS.read_text().splitlines()) if RUNS.exists() else 0
    RUNS.unlink(missing_ok=True)
    return n


def shown(project, ref="main") -> str:
    return git(project.root, "show", f"{ref}:memory/MEMORY.md")


try:
    # -- The work lands mid-job; the note after it meets failing tests.
    proj, origin = make("plain")
    r = start(proj, "20261119-100000-00000001")
    commit(r, "work.txt", "the work\n")
    mid, _ = land(proj, r, keep_tree=True)
    check("mid-job: the work lands", mid["state"], "landed")
    work_sha = mid["sha"]
    commit(r, "memory/MEMORY.md", "# Memory\n\nA note.\n", "Engineer memory")
    FAIL.write_text("x")
    runs()
    result, text = land(proj, r)
    check("note alone, failing tests: the result is what landed before",
          (result["state"], result["sha"], result.get("reason")),
          ("landed_earlier", work_sha, None))
    check("...the run was tried once more (two runs)", runs(), 2)
    patch = proj.jobs / r["job"] / wt.MEMORY_PATCH
    check("...the note is kept as a patch in the job's folder",
          patch.is_file() and "+A note." in patch.read_text(), True)
    check("...the receipt says the note did not land, and where it is",
          "memory note did not land" in text and str(patch) in text
          and "no continuation" in text, True)
    check("...main is where the work left it", git(proj.root, "rev-parse", "main"), work_sha)
    FAIL.unlink()

    # -- Uncommitted memory alone: the landing commits it.
    r = start(proj, "20261119-100000-00000002")
    commit(r, "more.txt", "more work\n")
    write(r, "memory/MEMORY.md", "# Memory\n\nAn uncommitted note.\n")
    result, text = land(proj, r)
    check("uncommitted note: the landing commits it and lands",
          (result["state"], result.get("memory_committed"), "uncommitted note" in shown(proj)),
          ("landed", True, True))
    check("...and the receipt says so", "the landing committed it" in text, True)

    # -- Failing tests with work on the branch: still refused.
    r = start(proj, "20261119-100000-00000003")
    commit(r, "broken.txt", "work that fails\n")
    commit(r, "memory/MEMORY.md", "# Memory\n\nNote with broken work.\n")
    before = git(proj.root, "rev-parse", "main")
    FAIL.write_text("x")
    result, text = land(proj, r)
    check("work with failing tests: refused, main unmoved",
          (result["state"], result["reason"], git(proj.root, "rev-parse", "main")),
          ("not_landed", "branch_tests", before))
    check("...and no note is set aside", "memory_held" in result, False)
    FAIL.unlink()
    runs()

    # -- The push gate.
    proj, origin = make("gated")
    deny = root / "deny"
    deny.write_text("\\bSecretname\\b\n")
    git(proj.root, "config", "loam.pushGate", "true")
    git(proj.root, "config", "loam.pushDeny", str(deny))

    r = start(proj, "20261119-100000-00000011")
    commit(r, "work.txt", "clean work\n")
    commit(r, "memory/MEMORY.md", "# Memory\n\nSecretname said so.\n", "Engineer memory")
    result, text = land(proj, r)
    check("gate, the note names a denied word: the work lands",
          (result["state"], result["pushed"], git(proj.root, "show", "main:work.txt")),
          ("landed", True, "clean work"))
    check("...without the note: main and origin never have the word",
          ("Secretname" in shown(proj),
           "Secretname" in git(origin, "log", "-p", "main"),
           git(origin, "rev-parse", "main") == git(proj.root, "rev-parse", "main")),
          (False, False, True))
    check("...the note is held, kept, and the receipt says why",
          (result.get("memory_held", {}).get("reason"),
           "Secretname" in Path(result["memory_held"]["patch"]).read_text(),
           "memory note did not land" in text and "one commit without them" in text),
          ("gate", True, True))

    r = start(proj, "20261119-100000-00000012")
    commit(r, "leak.txt", "Secretname in the work\n")
    commit(r, "memory/MEMORY.md", "# Memory\n\nfine note\n")
    before = git(proj.root, "rev-parse", "main")
    result, text = land(proj, r)
    check("gate, the work itself names a denied word: still refused",
          (result["state"], result["reason"], git(proj.root, "rev-parse", "main")),
          ("not_landed", "gate", before))
    check("...the branch is as the engineer left it",
          git(Path(r["path"]), "show", f"{r['branch']}:leak.txt"), "Secretname in the work")

    r = start(proj, "20261119-100000-00000013")
    commit(r, "plain.txt", "plain\n", "a message naming Secretname")
    result, text = land(proj, r)
    check("gate, only a commit message names it: lands as one commit",
          (result["state"], result.get("squashed") is not None, "memory_held" in result,
           "Secretname" in git(origin, "log", "--format=%B", "main")),
          ("landed", True, False, False))

    r = start(proj, "20261119-100000-00000014")
    commit(r, "memory/MEMORY.md", "# Memory\n\nSecretname, a note alone.\n")
    before = git(proj.root, "rev-parse", "main")
    result, text = land(proj, r)
    check("gate, a note alone names it: nothing lands, the note is kept",
          (result["state"], result.get("memory_held", {}).get("reason"),
           git(proj.root, "rev-parse", "main"), "nothing landed" in text),
          ("nothing", "gate", before, True))
finally:
    wt.WORKTREES, loam_lock.LOCK_DIR = saved
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
