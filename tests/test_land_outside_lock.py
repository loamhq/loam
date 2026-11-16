"""A landing tests the merged tree outside the live lock (since 2026-11-16).

Against real git repositories in a temp directory, made the way
tests/test_worktree.py makes them, and real landings (lib/loam_worktree.land).
Every check reads what happened: where `main` points here and on the remote,
which commit each test run saw, who held the live lock while it ran, and the
landing's own telemetry.

  moved      `main` moves while the branch's merge is being tested -- another
             job lands in the gap, through its own landing -- so the first
             tested merge is stale. The landing lets go of the lock, merges
             again on the new `main`, tests again, and lands exactly the
             commit it tested last. `retests` is 1.
  starved    `main` moves under every attempt. After OUTSIDE_TRIES lost
             races the landing tests under the lock, which another process
             then cannot take, and lands. Nothing it did not test is pushed.
  pair       two landings, two processes, started together, each with a
             suite that takes SLOW seconds. Neither waits for the other's
             suite: both test at once, and the lock is waited on for the
             other's final step only, not its run.
  telemetry  the receipt's cell names the new phases.

Run: python3 tests/test_land_outside_lock.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="loam-land-outside-test-"))
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


def make_project(name: str, test_command: str) -> eng.Project:
    origin = ROOT / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = ROOT / f"{name}-seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    for i in range(1, 9):
        (seed / f"f{i}.txt").write_text(f"{i}\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "start")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "main")
    checkout = ROOT / name
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
    git(checkout, "config", "user.name", f"{name} engineer")
    git(checkout, "config", "user.email", f"engineer+{name}@noreply.local")
    record = {"name": name, "checkout": str(checkout), "test_command": test_command}
    return eng.Project(name, checkout, ROOT / f"{name}-jobs", platform=False,
                       record=record)


def start(project: eng.Project, job_id: str) -> dict:
    job_dir = project.jobs / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return wt.setup(project, job_id, job_dir, None)


def commit(record: dict, path: str, text: str) -> str:
    tree = Path(record["path"])
    (tree / path).write_text(text)
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", f"edit {path}")
    return git(tree, "rev-parse", "HEAD")


def main_of(p: eng.Project) -> str:
    return git(p.root, "rev-parse", "main")


def origin_of(p: eng.Project) -> str:
    return git(ROOT / f"{p.name}.git", "rev-parse", "main")


def lock_free(p: eng.Project) -> bool:
    """Whether ANOTHER process could take the live lock now."""
    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); import loam_lock\n"
         "try:\n"
         "    with loam_lock.live(sys.argv[2], 'probe', wait_seconds=0, "
         "on_wait=lambda *a: None):\n"
         "        print('free')\n"
         "except loam_lock.Busy:\n"
         "    print('held')",
         str(Path(wt.__file__).parent), p.live_lock],
        capture_output=True, text=True, check=False,
        env={k: v for k, v in os.environ.items() if k != loam_lock.LIVE_HELD_VAR})
    return probe.stdout.strip() == "free"


real_run_tests = wt.run_tests
try:
    # -- moved: another landing lands while this one tests ---------------------
    proj = make_project("moved", "true")
    a = start(proj, "20261116-120001-aaaaaaaa")
    b = start(proj, "20261116-120002-bbbbbbbb")
    commit(a, "f1.txt", "one from a\n")
    tip_b = commit(b, "f2.txt", "two from b\n")
    tip_a = commit(a, "f3.txt", "three from a\n")
    seen: list[tuple[str, bool]] = []

    def a_tests(project, tree):
        seen.append((git(tree, "rev-parse", "HEAD"), lock_free(project)))
        if len(seen) == 1:
            # B lands in the gap, with its own landing, in its own process'
            # shoes: this process holds no live lock while A tests.
            other = real_run_tests
            wt.run_tests = other
            try:
                landed_b = wt.land(proj, b, "Land b")
            finally:
                wt.run_tests = a_tests
            check("moved: B lands while A's merge is being tested",
                  (landed_b["state"], main_of(proj)), ("landed", tip_b))
        return real_run_tests(project, tree)

    wt.run_tests = a_tests
    main_before = main_of(proj)
    result = wt.land(proj, a, "Land a")
    wt.run_tests = real_run_tests
    check("moved: A landed", result["state"], "landed")
    check("...after one re-test", (result["retests"], len(seen)), (1, 2))
    check("...both test runs had the live lock free (outside it)",
          [free for _, free in seen], [True, True])
    check("...the first run tested A's tip, a fast-forward of the old main",
          seen[0][0], tip_a)
    check("...the second a merge of A onto B's main",
          git(Path(a["path"]) if Path(a["path"]).exists() else proj.root,
              "rev-parse", f"{seen[1][0]}^1"), tip_b)
    check("...main and origin are exactly the commit tested last",
          (main_of(proj), origin_of(proj)), (seen[-1][0], seen[-1][0]))
    check("...and it holds B's change and A's",
          (git(proj.root, "show", "main:f1.txt"), git(proj.root, "show", "main:f2.txt"),
           git(proj.root, "show", "main:f3.txt")),
          ("one from a", "two from b", "three from a"))
    check("...not tested under the lock", result["tested_under_lock"], False)
    held = result["timing"]["lock_held"]["wall_s"]
    check("...the lock was held only for the final step (< 5 s)", held < 5, True)
    check("...one merge commit on main, the tested one",
          git(proj.root, "rev-list", "--count", "--merges", "main"), "1")

    # -- starved: main moves under every attempt; the fallback -----------------
    proj2 = make_project("starved", "true")
    s = start(proj2, "20261116-120003-ssssssss")
    commit(s, "f1.txt", "one from s\n")
    pushers = []
    for n in range(wt.OUTSIDE_TRIES):
        o = start(proj2, f"20261116-12001{n}-oooooo0{n}")
        commit(o, f"f{n + 4}.txt", f"from other {n}\n")
        pushers.append(o)
    runs: list[tuple[str, bool]] = []

    def s_tests(project, tree):
        runs.append((git(tree, "rev-parse", "HEAD"), lock_free(project)))
        if len(runs) <= wt.OUTSIDE_TRIES:
            wt.run_tests = real_run_tests
            try:
                wt.land(proj2, pushers[len(runs) - 1], "Land other")
            finally:
                wt.run_tests = s_tests
        return real_run_tests(project, tree)

    wt.run_tests = s_tests
    starved = wt.land(proj2, s, "Land s")
    wt.run_tests = real_run_tests
    check("starved: landed in the end", starved["state"], "landed")
    check("...after OUTSIDE_TRIES re-tests, then under the lock",
          (starved["retests"], starved["tested_under_lock"], len(runs)),
          (wt.OUTSIDE_TRIES, True, wt.OUTSIDE_TRIES + 1))
    check("...the first runs outside the lock, the last one under it",
          [free for _, free in runs], [True] * wt.OUTSIDE_TRIES + [False])
    check("...main and origin are exactly the commit tested last",
          (main_of(proj2), origin_of(proj2)), (runs[-1][0], runs[-1][0]))
    check("...with every other landing's change in it",
          [git(proj2.root, "show", f"main:f{n + 4}.txt")
           for n in range(wt.OUTSIDE_TRIES)],
          [f"from other {n}" for n in range(wt.OUTSIDE_TRIES)])

    # -- a failing merge still says which tree is broken -----------------------
    proj3 = make_project("broken", "! grep -q BAD f1.txt f2.txt")
    x = start(proj3, "20261116-120021-xxxxxxxx")
    y = start(proj3, "20261116-120022-yyyyyyyy")
    commit(x, "f2.txt", "fine\n")
    wt.land(proj3, x, "Land x")
    commit(y, "f1.txt", "BAD\n")
    before = main_of(proj3)
    bad = wt.land(proj3, y, "Land y")
    check("broken branch on a moved main: branch_tests, main unmoved",
          (bad["state"], bad["reason"], bad["test"]["tree"], main_of(proj3)),
          ("not_landed", "branch_tests", "branch", before))

    # -- pair: two landings finishing together --------------------------------
    SLOW = 6
    proj4 = make_project("pair", f"sleep {SLOW}")
    jobs = [start(proj4, "20261116-120031-pppppppp"),
            start(proj4, "20261116-120032-qqqqqqqq")]
    commit(jobs[0], "f1.txt", "one from p\n")
    commit(jobs[1], "f2.txt", "two from q\n")
    helper = ROOT / "land_one.py"
    helper.write_text(f"""
import json, sys, time
sys.path.insert(0, {str(Path(wt.__file__).parent)!r})
import loam_engineer as eng, loam_lock, loam_worktree as wt
from pathlib import Path
wt.WORKTREES = Path({str(wt.WORKTREES)!r})
loam_lock.LOCK_DIR = Path({str(loam_lock.LOCK_DIR)!r})
spec = json.loads(Path(sys.argv[1]).read_text())
proj = eng.Project(spec["name"], Path(spec["checkout"]), Path(spec["jobs"]),
                   platform=False, record=spec["project"])
spans = []
real = wt.run_tests
def timed_tests(project, tree):
    t0 = time.time()
    out = real(project, tree)
    spans.append([t0, time.time()])
    return out
wt.run_tests = timed_tests
t0 = time.time()
result = wt.land(proj, spec["record"], "Land " + spec["record"]["job"])
Path(sys.argv[2]).write_text(json.dumps({{"result": result, "spans": spans,
    "start": t0, "end": time.time()}}, default=str))
""")
    procs, outs = [], []
    for n, job in enumerate(jobs):
        spec = ROOT / f"spec{n}.json"
        spec.write_text(json.dumps({"name": proj4.name, "checkout": str(proj4.root),
                                    "jobs": str(proj4.jobs), "project": proj4.record,
                                    "record": job}))
        outs.append(ROOT / f"out{n}.json")
        env = {k: v for k, v in os.environ.items() if k != loam_lock.LIVE_HELD_VAR}
        procs.append(subprocess.Popen([sys.executable, str(helper), str(spec),
                                       str(outs[-1])], env=env))
    t0 = time.time()
    for proc in procs:
        proc.wait(timeout=20 * SLOW)
    wall = time.time() - t0
    got = [json.loads(o.read_text()) for o in outs]
    first, second = sorted(got, key=lambda g: g["end"])
    check("pair: both landed",
          sorted(g["result"]["state"] for g in got), ["landed", "landed"])
    check("...their first test runs overlapped (both outside the lock)",
          first["spans"][0][0] < second["spans"][0][1]
          and second["spans"][0][0] < first["spans"][0][1], True)
    check("...the second's lock wait is shorter than one suite",
          second["result"]["timing"]["lock_wait"]["wall_s"] < SLOW,
          True)
    check("...the first's lock was held for its final step, not its suite",
          first["result"]["timing"]["lock_held"]["wall_s"] < SLOW, True)
    # Before: the second waited out the first's suite under the lock, then
    # ran its own two (branch, merge) -- 3 suites end to end. Now: the two
    # first runs overlap, and the second re-tests once on the moved main.
    bound = 2.5 * SLOW
    check(f"...both done in under {bound:.0f} s (old order: 3 suites, {3 * SLOW} s)",
          wall < bound, True)
    print(f"      pair: wall {wall:.1f} s; first {wt.timing_cell(first['result'])}")
    print(f"      pair: second {wt.timing_cell(second['result'])}")
    check("...main and origin agree, with both changes",
          (main_of(proj4) == origin_of(proj4), git(proj4.root, "show", "main:f1.txt"),
           git(proj4.root, "show", "main:f2.txt")),
          (True, "one from p", "two from q"))

    # -- telemetry -------------------------------------------------------------
    cell = wt.timing_cell(result)
    check("telemetry: the cell names tests outside the lock, lock wait, "
          "lock held and re-tests",
          all(word in cell for word in ("merge tests (outside the lock)",
                                        "lock wait", "lock held", "re-tests 1")),
          True)
    print(f"      {cell}")
    old = {"timing": {"lock_wait": {"wall_s": 1.0, "cpu_s": 0.0},
                      "branch_tests": {"wall_s": 444.0, "cpu_s": 300.0},
                      "merge_tests": {"wall_s": 419.0, "cpu_s": 290.0}}}
    check("...a landing of before still reads as it did",
          wt.timing_cell(old), "branch tests 444.0 s (300.0 s CPU); merge tests "
          "419.0 s (290.0 s CPU); lock wait 1.0 s (0.0 s CPU)")
finally:
    wt.run_tests = real_run_tests
    import shutil
    shutil.rmtree(ROOT, ignore_errors=True)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
