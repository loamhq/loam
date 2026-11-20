"""Exercise wake and its failure reporter against a temporary vault. A failure report must reflect the actual process outcome."""

import json, os, shutil, signal, subprocess, sys, tempfile, time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
AGENT = "wakefailedselftest"
# Use the same runtime state and log layout as the wake launcher.
sys.path.insert(0, str(AGENTS / "lib"))
import loam_config as lc  # noqa: E402
STATE = lc.STATE / "wake" / f"{AGENT}.running"
LOG = lc.LOGS / f"wake-{AGENT}.jsonl"
# Put the schedule fire log in the fixture's schedule state directory.
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


def make_project(tmp):
    """A minimal agent with no cron file."""
    proj, vault = tmp / "proj", tmp / "vault"
    proj.mkdir(); (vault / "00_INBOX" / "from-agents").mkdir(parents=True)
    (proj / "agent.json").write_text(json.dumps({
        "name": AGENT, "display": "Test", "emoji": "x",
        "workspace": str(proj), "vault": str(vault),
        "wake_schedule": "23:59", "wake_timezone": "America/Los_Angeles",
    }))
    return proj, vault


def cleanup():
    STATE.unlink(missing_ok=True)
    LOG.unlink(missing_ok=True)


cleanup()
try:
    # --- 1. wrapper records its own failure, then wake-failed stays silent ---
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        proj, vault = make_project(tmp)
        r = subprocess.run([str(AGENTS / "bin" / "wake"), str(proj), "--cron", "missing"],
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
        proj, vault = make_project(tmp)
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
        proj, vault = make_project(tmp)
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
