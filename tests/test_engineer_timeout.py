#!/usr/bin/env python3
"""Exercise timeouts and silent sessions with a stub CLI. Preserve partial output and
distinguish a stall warning from a timeout verdict.

Run: python3 tests/test_engineer_timeout.py
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import json
import subprocess
import os
import shutil
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_engineer as eng  # noqa: E402
import loam_session  # noqa: E402

AGENTS = Path(__file__).resolve().parent.parent

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:60} {got!r}"[:220]
          + ("" if ok else f"   want {want!r}"))


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


job = load("engineer_job_under_test", AGENTS / "bin" / "engineer-job")
failed = load("wake_failed_under_test", AGENTS / "bin" / "wake-failed")

root = Path(tempfile.mkdtemp(prefix="loam-engineer-timeout-"))


def job_paths(name: str) -> dict[str, Path]:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    return {
        "dir": d, "brief": d / "brief.md", "receipt": d / "receipt.md",
        "result": d / "result.json", "heartbeat": d / "heartbeat",
        "stderr": d / "session-stderr.log", "stdout": d / "session-stdout.log",
        "session": d / "session-id",
    }


def stub(name: str, body: str) -> Path:
    path = root / name
    path.write_text(f"#!{sys.executable}\nimport sys, time\n{body}\n")
    path.chmod(0o755)
    return path


HANGS_AFTER_STDERR = stub("claude-hangs", (
    "sys.stderr.write('stub: connected\\nstub: waiting on the API\\n')\n"
    "sys.stderr.flush()\n"
    "time.sleep(60)"))
HANGS_SILENT = stub("claude-silent", "time.sleep(60)")
EXITS_CLEAN = stub("claude-clean", "print('{\"subtype\": \"success\"}')")

try:
    # -----------------------------------------------------------------------
    # 1. The timeout keeps what the CLI printed.
    # -----------------------------------------------------------------------
    p = job_paths("timeout-with-stderr")
    t0 = time.monotonic()
    code, envelope, timed_out = job.run_session(
        [str(HANGS_AFTER_STDERR), "-p", "x"], str(root), 2, p)
    check("timeout: run_session reports it timed out", timed_out, True)
    check("timeout: it returned at the clock, not the stub's 60 s",
          time.monotonic() - t0 < 20, True)
    check("timeout: the stderr the stub wrote is saved",
          p["stderr"].read_text(encoding="utf-8"),
          "stub: connected\nstub: waiting on the API\n")
    check("timeout: no envelope, and nothing on stdout to keep",
          (envelope, p["stdout"].exists()), ({}, False))

    text = job.no_receipt_body(p, None, True, job.read_beat(p))
    print("\n--- the no-receipt text for a timed-out job ---\n" + text + "\n---\n")
    check("timeout text: says the run timed out and quotes stderr",
          "The run timed out; stderr as captured:\n\n```\n"
          "stub: connected\nstub: waiting on the API\n```" in text, True)
    check("timeout text: never claims the CLI wrote nothing",
          "wrote nothing" in text, False)

    p = job_paths("timeout-silent")
    _, _, timed_out = job.run_session([str(HANGS_SILENT)], str(root), 2, p)
    text = job.no_receipt_body(p, None, True, job.read_beat(p))
    check("silent timeout: timed out", timed_out, True)
    check("silent timeout: says stderr not captured",
          "The run timed out; stderr not captured" in text, True)
    check("silent timeout: never claims the CLI wrote nothing",
          "wrote nothing" in text, False)

    # The normal path is unchanged: a run that returned had its stderr read
    # whole, so "wrote nothing" is what the launcher actually knows there.
    p = job_paths("clean")
    code, envelope, timed_out = job.run_session([str(EXITS_CLEAN)], str(root), 20, p)
    check("clean exit: envelope parsed, not timed out",
          (code, envelope, timed_out), (0, {"subtype": "success"}, False))
    check("clean exit with empty stderr: the old sentence stands",
          "the CLI wrote nothing to stderr" in
          job.no_receipt_body(p, None, False, job.read_beat(p)), True)

    # Recover a receipt returned as the session's closing message.
    said = ("The deploy and restart are done.\n\n**Evidence**\n"
            "- `bin/deploy loam-ui up c0730da` answered 0.")
    p = job_paths("said-not-written")
    code, envelope, _ = job.run_session([str(stub("claude-says", (
        "import json\nprint(json.dumps({'subtype': 'success', "
        f"'result': {said!r}}}))")))], str(root), 20, p)
    check("said, not written: no receipt.md", p["receipt"].exists(), False)
    text = job.no_receipt_body(p, None, False, job.read_beat(p),
                               envelope.get("result"))
    print("\n--- the no-receipt text for a receipt said, not written ---\n"
          + text + "\n---\n")
    check("said, not written: still says no receipt was written",
          text.startswith("**The engineer wrote no receipt.**"), True)
    check("said, not written: quotes the last message whole, labelled",
          "not written to `receipt.md`:\n\n> The deploy and restart are "
          "done.\n>\n> **Evidence**\n> - `bin/deploy loam-ui up c0730da` "
          "answered 0." in text, True)
    check("an envelope with no message adds no heading",
          "last message" in job.no_receipt_body(
              p, None, False, job.read_beat(p), None), False)

    # Preserve the provider's error detail when its envelope contains no result.
    killed = {"oom_kill": 1}
    text = job.no_receipt_body(p, None, False, job.read_beat(p), None, killed)
    print("\n--- the no-receipt text for a job killed for memory ---\n"
          + text + "\n---\n")
    check("killed for memory: said plainly, straight after the opening",
          text.split("\n\n")[1].startswith("**Killed for memory.** The kernel "
                                           "killed 1 process(es) in this job"),
          True)
    check("killed for memory: names the box and the order, no ceiling",
          ("no memory ceiling of its own" in text,
           "OOM order" in text, "MemoryMax" in text), (True, True, False))
    check("no kill: no memory paragraph",
          "Killed for memory" in job.no_receipt_body(
              p, None, False, job.read_beat(p), None, {}), False)
    check("record row: the kill",
          job.memory_events_note(killed),
          "; **1 OOM kill(s), the box out of memory**")
    check("record row: a quiet job's row is unchanged",
          job.memory_events_note({"oom_kill": 0}), "")

    # -----------------------------------------------------------------------
    # 2. The stall label, on a fixture transcript and a fixture heartbeat.
    # -----------------------------------------------------------------------
    real_sessions = loam_session.SESSIONS_DIR
    loam_session.SESSIONS_DIR = root / "sessions"
    (loam_session.SESSIONS_DIR / "-fixture-project").mkdir(parents=True)
    sid = "11111111-2222-3333-4444-555555555555"
    transcript = loam_session.SESSIONS_DIR / "-fixture-project" / f"{sid}.jsonl"
    transcript.write_text('{"type": "assistant"}\n')
    now = eng.now_utc().replace(microsecond=0)
    started = now - timedelta(minutes=40)
    quiet_since = now - timedelta(minutes=job.STALL_MINUTES + 5)
    os.utime(transcript, (quiet_since.timestamp(), quiet_since.timestamp()))

    p = job_paths("stall")
    beat = job.beat_once(p, sid, started, now)
    check("stall: 20 min of silence is labelled", beat["stalled_since"],
          eng.iso(quiet_since))
    check("stall: the heartbeat file says so, first line still the tick",
          p["heartbeat"].read_text(encoding="utf-8"),
          f"{eng.iso(now)}\ntranscript_last: {eng.iso(quiet_since)}\n"
          f"stalled_since: {eng.iso(quiet_since)}\n")
    check("stall: last_beat still reads only the tick", job.last_beat(p), eng.iso(now))
    check("stall: read_beat round-trips it", job.read_beat(p), beat)

    text = job.no_receipt_body(p, sid, True, beat)
    check("stall: the receipt-less record names it",
          f"**Stalled since {eng.iso(quiet_since)}.**" in text, True)

    fresh = now - timedelta(minutes=job.STALL_MINUTES - 1)
    os.utime(transcript, (fresh.timestamp(), fresh.timestamp()))
    check("stall: a line inside the window clears it",
          job.beat_once(p, sid, started, now)["stalled_since"], None)
    check("stall: ...and the heartbeat file drops the line",
          "stalled_since" in p["heartbeat"].read_text(encoding="utf-8"), False)

    unwritten = "99999999-2222-3333-4444-555555555555"
    check("stall: no transcript ever, past the window: stalled since start",
          job.beat_once(p, unwritten, started, now)["stalled_since"], eng.iso(started))
    check("stall: no transcript yet, inside the window: not stalled",
          job.beat_once(p, unwritten, now - timedelta(minutes=2), now)["stalled_since"],
          None)
    check("stall: a probe (no session) is never stalled",
          job.beat_once(p, None, started, now)["stalled_since"], None)

    # Accept the older timestamp-only heartbeat.
    fixture = job_paths("fixture-heartbeat")
    fixture["heartbeat"].write_text("2026-09-25T22:47:50Z")
    check("fixture heartbeat, old one-line form",
          job.read_beat(fixture),
          {"tick": "2026-09-25T22:47:50Z", "transcript_last": None,
           "stalled_since": None})
    fixture["heartbeat"].write_text(
        "2026-09-25T22:47:50Z\ntranscript_last: 2026-09-25T22:06:31Z\n"
        "stalled_since: 2026-09-25T22:06:31Z\n")
    check("fixture heartbeat, stalled form",
          job.read_beat(fixture)["stalled_since"], "2026-09-25T22:06:31Z")
    loam_session.SESSIONS_DIR = real_sessions

    # Drive the full inner launcher against the stub CLI.
    repo = root / "agents"
    (repo / "engineer").mkdir(parents=True)
    (repo / "engineer" / "role.md").write_text("role\n")
    for argv in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "start"]):
        subprocess.run(["git", "-C", str(repo), *argv], check=True)
    project = eng.Project("Loam", repo / "engineer", root / "jobs", platform=True)
    job_id = f"20260930-120000-{os.getpid() & 0xffffff:06x}fe"
    inner = job_paths(f"jobs/{job_id}")
    inner["brief"].write_text("---\ntitle: Stub timeout\nbriefed_by: ada\n---\n\nx\n")

    saved = (job.CLAUDE, job.STATE_DIR, job.STALL_MINUTES, job.read_brief_meta,
             eng.VAULT, eng.log_line, eng.job_dir, job.loam_lock.hold_slot,
             job.wt.WORKTREES, job.wt.wall_check, job.wt.walled, job.resume_queued)
    real_meta = job.read_brief_meta
    logged: list[dict] = []
    job.CLAUDE = str(HANGS_AFTER_STDERR)
    job.STATE_DIR = root / "state"
    job.STALL_MINUTES = 0.02  # 1.2 s, so a 3 s run stalls
    job.read_brief_meta = lambda paths: {**real_meta(paths), "max_minutes": 0.05}
    eng.VAULT = root / "vault"
    eng.log_line = logged.append
    eng.job_dir = lambda engineer, jid: root / "jobs" / jid
    job.loam_lock.hold_slot = lambda *a, **k: contextlib.nullcontext()
    job.wt.WORKTREES = root / "worktrees"
    # The wall is tests/test_worktree.py's; this stub CLI runs without it.
    job.wt.wall_check = lambda *a: None
    job.wt.walled = lambda project, record, job_dir, argv: argv
    job.resume_queued = lambda: []
    try:
        exit_code = job.run_inner(project, job_id)
    finally:
        (job.CLAUDE, job.STATE_DIR, job.STALL_MINUTES, job.read_brief_meta,
         eng.VAULT, eng.log_line, eng.job_dir, job.loam_lock.hold_slot,
         job.wt.WORKTREES, job.wt.wall_check, job.wt.walled,
         job.resume_queued) = saved

    result = json.loads(inner["result"].read_text(encoding="utf-8"))
    print("\n--- result.json of a stalled, timed-out job (receipt_text elided) ---")
    print(json.dumps({k: v for k, v in result.items() if k != "receipt_text"},
                     indent=2)[:1400] + "\n---\n")
    check("inner: exits TIMEOUT_EXIT on a wall-clock cut", exit_code, job.TIMEOUT_EXIT)
    check("inner: outcome is the brief's ceiling", result["outcome"].startswith("cut off at the 0.05-minute ceiling"),
          True)
    check("inner: result.json carries stalled_since",
          bool(result.get("stalled_since")), True)
    check("inner: stalled since the job's start (no transcript was ever written)",
          result["stalled_since"], result["started"])
    check("inner: result.json carries transcript_last (none here)",
          "transcript_last" in result and result["transcript_last"] is None, True)
    check("inner: the record quotes the captured stderr",
          "stderr as captured:\n\n```\nstub: connected\nstub: waiting on the API\n```"
          in result["receipt_text"], True)
    check("inner: the record names the stall",
          f"**Stalled since {result['stalled_since']}.**" in result["receipt_text"], True)
    check("inner: the log line carries stalled_since",
          logged and logged[0].get("stalled_since") == result["stalled_since"], True)
    check("inner: a timed-out job is not landed",
          (result.get("landing_state"), result.get("landing_reason")),
          ("not_landed", "incomplete"))
    check("inner: ...its tree is kept, under the test's root, not agents/",
          (root / "worktrees" / "Loam" / job_id).is_dir(), True)
    check("inner: ...and the receipt says so, with the continuation",
          "**Landing: not landed**" in result["receipt_text"]
          and f"continue_job: {job_id}" in result["receipt_text"], True)
    check("inner: the stderr file is on disk",
          inner["stderr"].read_text(encoding="utf-8").startswith("stub: connected"), True)

    # -----------------------------------------------------------------------
    # 4. The failure handler: the same number, and the right marker.
    # -----------------------------------------------------------------------
    check("wake-failed and the launcher agree on the timeout code",
          failed.ENGINEER_TIMEOUT_EXIT, job.TIMEOUT_EXIT)
    # The argv `launch` hands systemd-run, captured rather than run.
    launched: list[list[str]] = []
    real_run = job.subprocess.run
    job.subprocess.run = lambda argv, **kw: launched.append(argv)
    try:
        job.launch(eng.platform_project(), job_id, inner, 1)
    finally:
        job.subprocess.run = real_run
    check("launch declares the timeout code a success to systemd",
          f"--property=SuccessExitStatus={job.TIMEOUT_EXIT}" in launched[0], True)

    state = root / "markers"
    state.mkdir()
    (state / "engineer.running").write_text(json.dumps(
        {"lane": "job:20260930-110000-aaaaaaaa", "project": "Loam"}))
    (state / "engineer-loam-ui.running").write_text(json.dumps(
        {"lane": "job:20260930-110500-bbbbbbbb", "project": "loam-ui"}))
    (state / "ada.running").write_text(json.dumps({"lane": "job:20260930-110500-bbbbbbbb"}))
    check("handler: a project engineer's death finds its own marker",
          failed.engineer_marker(state, "job:20260930-110500-bbbbbbbb"),
          state / "engineer-loam-ui.running")
    check("handler: the platform engineer's finds its own",
          failed.engineer_marker(state, "job:20260930-110000-aaaaaaaa"),
          state / "engineer.running")
    check("handler: a job with no marker finds none (not another job's)",
          failed.engineer_marker(state, "job:20260930-120000-cccccccc"), None)
finally:
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
