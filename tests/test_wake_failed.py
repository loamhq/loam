"""The mid-run death reporter, asserted end to end.

Runs the real bin/wake and the real bin/wake-failed as subprocesses against
a throwaway agent whose vault is a temp directory, so every path exercised
is the one production uses and nothing fabricated reaches the real vault.

The two cases are the whole contract, and they pull in opposite directions:

  wrapper recorded it     -> wake-failed must stay SILENT. bin/wake exits
                             non-zero on its own recorded refusals, so
                             OnFailure= fires on the good path too; a
                             reporter that always wrote would attribute
                             every handled failure twice.
  wrapper died mid-run    -> wake-failed must write. This is 2026-08-30,
                             when the kernel killed Wren's wake and the
                             night went missing rather than recorded.

What separates them is the run marker, and case three is what makes that
separation trustworthy. That a SIGKILL cannot run `finally` is an OS
guarantee, not something worth asserting about Python. What IS ours, and is
asserted here, is that the clear lives *only* in that `finally`: a run that
exits by a path which skips it must leave the marker behind. If a later
edit moved the clear anywhere that survives an abrupt exit -- an atexit
hook, a signal handler, a second call on the error paths -- case two would
silently degrade into case one, the mid-run death would go unreported
again, and every assertion above would still pass.

os._exit is used as the stand-in for the kill: it is the one in-process
exit that skips `finally` exactly as SIGKILL does, and unlike killing a
real wake it cannot race. The wake being tested dies in milliseconds, so
signalling it is a coin toss, not a test.

Run: python3 tests/test_wake_failed.py
"""

import json, os, shutil, signal, subprocess, sys, tempfile, time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
AGENT = "wakefailedselftest"
# Where bin/wake writes: the runtime's state and log folders as
# lib/loam_config names them, outside the checkout since 2026-11-14.
sys.path.insert(0, str(AGENTS / "lib"))
import loam_config as lc  # noqa: E402
STATE = lc.STATE / "wake" / f"{AGENT}.running"
LOG = lc.LOGS / f"wake-{AGENT}.jsonl"
# The schedule log is not: bin/wake and bin/wake-failed write their fire
# lines where LOAM_SCHEDULE_STATE says (lib/loam_schedule.py), and until
# 2026-10-14 this test's fires landed in the box's own log, read by Activity.
os.environ["LOAM_SCHEDULE_STATE"] = tempfile.mkdtemp(prefix="loam-wake-failed-sched-")

fails = 0


def check(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'' if cond else '  | ' + detail}")


def read_log():
    return [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()] if LOG.exists() else []


def note_bullets(vault):
    note = vault / "00_INBOX" / "from-agents" / "loam-wake-failures.md"
    if not note.exists():
        return []
    return [l for l in note.read_text().splitlines() if l.startswith("- `")]


def make_project(tmp, with_reflection):
    """A minimal agent. No _reflection.md means bin/wake refuses at preflight
    -- a failure it records itself, which is exactly the silence case."""
    proj, vault = tmp / "proj", tmp / "vault"
    proj.mkdir(); (vault / "00_INBOX" / "from-agents").mkdir(parents=True)
    (proj / "agent.json").write_text(json.dumps({
        "name": AGENT, "display": "Test", "emoji": "x",
        "workspace": str(proj), "vault": str(vault),
        "wake_schedule": "23:59", "wake_timezone": "America/Los_Angeles",
    }))
    if with_reflection:
        (proj / "_reflection.md").write_text("a self-model\n")
    return proj, vault


def cleanup():
    STATE.unlink(missing_ok=True)
    LOG.unlink(missing_ok=True)


cleanup()
try:
    # --- 1. wrapper records its own failure, then wake-failed stays silent ---
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        proj, vault = make_project(tmp, with_reflection=False)
        r = subprocess.run([str(AGENTS / "bin" / "wake"), str(proj)],
                           capture_output=True, text=True)
        check("bin/wake refuses at preflight (exit 3)", r.returncode == 3, f"exit={r.returncode}")
        check("wrapper wrote one log line", len(read_log()) == 1, str(read_log()))
        check("wrapper wrote one vault bullet", len(note_bullets(vault)) == 1)
        check("finally cleared the run marker", not STATE.exists(),
              "marker survived a recorded failure; wake-failed would double-report")

        r = subprocess.run([str(AGENTS / "bin" / "wake-failed"), f"loam-wake-{AGENT}"],
                           capture_output=True, text=True)
        check("wake-failed exits 0 with no marker", r.returncode == 0, r.stderr[:120])
        check("wake-failed added NO second log line", len(read_log()) == 1, str(read_log()))
        check("wake-failed added NO second bullet", len(note_bullets(vault)) == 1)
    cleanup()

    # --- 2. wrapper dies mid-run: the marker survives and is reported ---
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        proj, vault = make_project(tmp, with_reflection=True)
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({
            "ts": "2026-08-30T10:00:11Z", "pid": 3827846, "vault": str(vault)}))

        r = subprocess.run([str(AGENTS / "bin" / "wake-failed"), f"loam-wake-{AGENT}"],
                           capture_output=True, text=True)
        check("wake-failed exits 0", r.returncode == 0, r.stderr[:120])
        log = read_log()
        check("recorded one died_mid_run entry",
              len(log) == 1 and log[0].get("stage") == "died_mid_run", str(log))
        check("kept the dead run's own start time",
              log and log[0].get("ts") == "2026-08-30T10:00:11Z", str(log))
        check("reached the vault the dead run was using", len(note_bullets(vault)) == 1)
        check("consumed the marker", not STATE.exists(),
              "a surviving marker makes the next failure look unrecorded too")
    cleanup()

    # --- 3. the clear lives only in the finally ---
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        proj, vault = make_project(tmp, with_reflection=True)
        probe = tmp / "probe.py"
        probe.write_text(
            "import importlib.machinery, importlib.util, os, sys\n"
            "l = importlib.machinery.SourceFileLoader('wake', %r)\n"
            "sp = importlib.util.spec_from_loader('wake', l)\n"
            "m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)\n"
            "def killed(*a, **k):\n"
            "    assert (m.STATE_DIR / (%r + '.running')).exists(), 'no marker in flight'\n"
            "    os._exit(137)\n"
            "m.run = killed\n"
            "sys.argv = ['wake', %r]\n"
            "m.main()\n" % (str(AGENTS / "bin" / "wake"), AGENT, str(proj))
        )
        r = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True)
        check("marker exists while the run is in flight", r.returncode == 137,
              f"exit={r.returncode} {r.stderr.strip()[:140]}")
        check("an exit that skips finally leaves the marker", STATE.exists(),
              "the clear is reachable from somewhere other than the finally; "
              "a killed wake would report as already handled")

        # And the ordinary error path must still clear it -- otherwise case 1
        # passes for the wrong reason and every handled failure double-reports.
        STATE.unlink(missing_ok=True)
        probe.write_text(
            "import importlib.machinery, importlib.util, sys\n"
            "l = importlib.machinery.SourceFileLoader('wake', %r)\n"
            "sp = importlib.util.spec_from_loader('wake', l)\n"
            "m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)\n"
            "def boom(*a, **k):\n"
            "    raise RuntimeError('unhandled')\n"
            "m.run = boom\n"
            "sys.argv = ['wake', %r]\n"
            "try:\n"
            "    m.main()\n"
            "except RuntimeError:\n"
            "    pass\n" % (str(AGENTS / "bin" / "wake"), str(proj))
        )
        r = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True)
        check("an unhandled exception still clears the marker", not STATE.exists(),
              r.stderr.strip()[:140])
finally:
    cleanup()

print()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
