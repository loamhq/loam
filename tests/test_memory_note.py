"""Verify held notes remain recoverable and cannot waive a failed test or publish a denied change."""

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
    # -- The work lands mid-job; the note after it skips the test suite and lands.
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
    check("note alone after mid-job land: skips tests and lands",
          (result["state"], result.get("notes_only"), "A note." in shown(proj)),
          ("landed", True, True))
    check("...the test suite did not run (zero runs)", runs(), 0)
    check("...the receipt says tests were skipped for memory only",
          "Test suite skipped (changes only `memory/`)" in text, True)
    check("...main moved past the mid-job commit",
          git(proj.root, "rev-parse", "main") != work_sha, True)

    # -- Fresh job with only a memory note (e.g. plugin work outside the repo):
    #    skips tests and lands immediately even when the test suite would fail.
    r = start(proj, "20261119-100000-00000004")
    commit(r, "memory/MEMORY.md", "# Memory\n\nA note.\n\nPlugin note.\n", "Engineer memory")
    runs()
    result, text = land(proj, r)
    check("note-only job: skips tests and lands",
          (result["state"], result.get("notes_only"), "Plugin note." in shown(proj)),
          ("landed", True, True))
    check("...zero test runs", runs(), 0)

    # -- Note-only job when main moved concurrently: merges and still skips tests.
    r = start(proj, "20261119-100000-00000005")
    commit(r, "memory/MEMORY.md", "# Memory\n\nA note.\n\nPlugin note.\n\nConcurrent note.\n",
           "Engineer memory")
    (proj.root / "side.txt").write_text("main moved\n")
    git(proj.root, "add", "side.txt")
    git(proj.root, "commit", "-qm", "main advances")
    git(proj.root, "push", "-q", "origin", "main")
    runs()
    result, text = land(proj, r)
    check("note-only job after main moved: merges, skips tests, and lands",
          (result["state"], result.get("notes_only"), "Concurrent note." in shown(proj)),
          ("landed", True, True))
    check("...zero test runs on merge", runs(), 0)

    # -- Uncommitted note alone: landing commits it, skips tests, and lands.
    r = start(proj, "20261119-100000-00000006")
    write(r, "memory/MEMORY.md",
          "# Memory\n\nA note.\n\nPlugin note.\n\nConcurrent note.\n\nUncommitted alone.\n")
    runs()
    result, text = land(proj, r)
    check("uncommitted note alone: commits, skips tests, and lands",
          (result["state"], result.get("memory_committed"), result.get("notes_only"),
           "Uncommitted alone." in shown(proj)),
          ("landed", True, True, True))
    check("...zero test runs for uncommitted note alone", runs(), 0)

    # -- A release tag on a note-only branch STILL requires tests (never skips).
    r = start(proj, "20261119-100000-00000007")
    commit(r, "memory/MEMORY.md", "# Memory\n\nRelease note.\n")
    runs()
    rel_res = wt.land(proj, r, f"Land {r['branch']}", release="0.1.0")
    check("release tag requested: note-only still runs tests (and fails)",
          (rel_res["state"], runs() >= 1), ("nothing", True))
    FAIL.unlink()

    # -- Uncommitted memory alongside real work: commits memory AND runs tests.
    r = start(proj, "20261119-100000-00000002")
    commit(r, "more.txt", "more work\n")
    write(r, "memory/MEMORY.md", "# Memory\n\nAn uncommitted note.\n")
    runs()
    result, text = land(proj, r)
    check("uncommitted note with work: commits, runs tests, and lands",
          (result["state"], result.get("memory_committed"), bool(result.get("notes_only")),
           runs(), "uncommitted note" in shown(proj)),
          ("landed", True, False, 1, True))
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

    # -- Non-test failure on a note-only branch after mid-job work: note is still held as patch.
    r = start(proj, "20261119-100000-00000008")
    commit(r, "early.txt", "early work\n")
    mid2, _ = land(proj, r, keep_tree=True)
    work_sha2 = mid2["sha"]
    commit(r, "memory/MEMORY.md", "# Memory\n\nHeld note.\n", "Engineer memory")
    git(proj.root, "checkout", "-q", "-b", "other")
    runs()
    result, text = land(proj, r)
    git(proj.root, "checkout", "-q", "main")
    git(proj.root, "branch", "-qD", "other")
    check("note alone, non-test landing failure: reports landed_earlier",
          (result["state"], result["sha"], result.get("reason"), runs()),
          ("landed_earlier", work_sha2, None, 0))
    patch = proj.jobs / r["job"] / wt.MEMORY_PATCH
    check("...the note is kept as a patch in the job's folder",
          patch.is_file() and "+Held note." in patch.read_text(), True)
    check("...the receipt says the note did not land, and where it is",
          "memory note did not land" in text and str(patch) in text
          and "no continuation" in text, True)

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
    runs()
    result, text = land(proj, r)
    check("gate, a note alone names it: nothing lands, the note is kept (0 tests)",
          (result["state"], result.get("memory_held", {}).get("reason"),
           git(proj.root, "rev-parse", "main"), "nothing landed" in text, runs()),
          ("nothing", "gate", before, True, 0))

    # -- Platform project (engineer/memory/) on a release install (loam.localMain):
    #    note alone skips the suite; any file outside engineer/memory/ still runs it.
    plat_origin = root / "plat.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(plat_origin)], check=True)
    plat_repo = root / "plat"
    subprocess.run(["git", "init", "-q", "-b", "main", str(plat_repo)], check=True)
    git(plat_repo, "config", "user.name", NAME)
    git(plat_repo, "config", "user.email", EMAIL)
    git(plat_repo, "config", wt.LOCAL_MAIN_KEY, "true")
    (plat_repo / "engineer" / "memory").mkdir(parents=True)
    (plat_repo / "engineer" / "memory" / "MEMORY.md").write_text("# Memory\n")
    (plat_repo / "engineer" / "role.md").write_text("# Role\n")
    git(plat_repo, "add", "-A")
    git(plat_repo, "commit", "-qm", "start")
    git(plat_repo, "remote", "add", "origin", str(plat_origin))
    git(plat_repo, "push", "-q", "origin", "main")
    plat = eng.Project("Loam", plat_repo / "engineer", root / "plat-jobs", platform=True,
                       record={"name": "Loam", "checkout": str(plat_repo)})
    plat.jobs.mkdir()
    plat_calls = []
    real_run_tests = wt.run_tests
    def fake_run_tests(project, tree):
        plat_calls.append(project.name)
        return False, "python3 tests/run-all", "simulated failure"
    wt.run_tests = fake_run_tests
    try:
        r = start(plat, "20261124-072426-00000021")
        commit(r, "engineer/memory/MEMORY.md", "# Memory\n\nPlugin built.\n", "Engineer memory")
        result, text = land(plat, r)
        check("platform release install, note alone: skips suite and lands locally",
              (result["state"], result.get("notes_only"), result.get("local_only"),
               len(plat_calls), "Plugin built." in git(plat_repo, "show", "main:engineer/memory/MEMORY.md")),
              ("landed", True, True, 0, True))
        check("...receipt names engineer/memory/",
              "Test suite skipped (changes only `engineer/memory/`)" in text, True)

        r = start(plat, "20261124-072426-00000022")
        commit(r, "engineer/memory/MEMORY.md", "# Memory\n\nPlugin built.\n\nRole change.\n")
        commit(r, "engineer/role.md", "# Role\n\nChanged.\n")
        result, text = land(plat, r)
        check("platform release install, non-memory file touched: runs suite and refuses",
              (result["state"], result.get("reason"), len(plat_calls)),
              ("not_landed", "branch_tests", 1))
    finally:
        wt.run_tests = real_run_tests
finally:
    wt.WORKTREES, loam_lock.LOCK_DIR = saved
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
