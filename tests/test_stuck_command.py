#!/usr/bin/env python3
"""Verify that the watchdog can stop an unresponsive command while its enclosing job continues."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import time
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import loam_agy_job as agy_job  # noqa: E402
import loam_codex_job as codex_job  # noqa: E402
import loam_stuck  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:62} {got!r}"[:240]
          + ("" if ok else f"   want {want!r}"))


# A stub engine. argv: shape (agy|codex), helper pidfile, mode
# (own-group|same-group|background). It prints its events line by line.
STUB = r'''
import json, os, subprocess, sys, time
shape, pidfile, mode = sys.argv[1], sys.argv[2], sys.argv[3]
def say(obj):
    print(json.dumps(obj), flush=True)
helper = subprocess.Popen(["sleep", "300"])
open(pidfile, "w").write(str(helper.pid))
if shape == "agy":
    say({"event": "init", "conversation_id": "c1", "init": {"model": "m"}})
    time.sleep(1.5)
    say({"event": "step_update", "step_update": {"conversation_id": "c1", "step_index": 1, "state": "DONE", "step_type": "agent_response"}})
    time.sleep(1.5)
    say({"event": "step_update", "step_update": {"conversation_id": "c1", "step_index": 2, "state": "ACTIVE", "step_type": "tool", "tool_name": "run_command", "tool_info": {"name": "run_command", "parameters": {"CommandLine": "sleep 600"}}}})
else:
    say({"type": "thread.started", "thread_id": "th1"})
    time.sleep(1.5)
    say({"type": "turn.started"})
    time.sleep(1.5)
    say({"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "sleep 600", "status": "in_progress"}})
cmd = subprocess.Popen(["bash", "-c", "sleep 600; echo slept"], start_new_session=(mode != "same-group"))
if mode == "background":
    # The command returns at once and leaves a server behind it.
    if shape == "agy":
        say({"event": "step_update", "step_update": {"conversation_id": "c1", "step_index": 2, "state": "DONE", "step_type": "tool", "tool_name": "run_command"}})
    else:
        say({"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "exit_code": 0}})
    time.sleep(8)
    os.killpg(cmd.pid, 9)
    cmd.wait()
    code = "background"
else:
    code = cmd.wait()
if shape == "agy":
    say({"event": "step_update", "step_update": {"conversation_id": "c1", "step_index": 3, "state": "DONE", "step_type": "tool", "tool_name": "run_command", "tool_info": {"output": f"exit {code}"}}})
    say({"event": "result", "result": {"conversation_id": "c1", "status": "SUCCESS", "response": f"command ended: {code}", "num_turns": 1}})
else:
    say({"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "exit_code": code}})
    say({"type": "turn.completed", "usage": {"input_tokens": 1}})
helper.kill()
'''


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()[0]
    except OSError:
        return False
    return state != "Z"


def run_stub(tmp: Path, shape: str, mode: str):
    job = tmp / f"{shape}-{mode}"
    job.mkdir()
    stub = tmp / "stub.py"
    stub.write_text(STUB, encoding="utf-8")
    pidfile = job / "helper.pid"
    argv = [sys.executable, str(stub), shape, str(pidfile), mode]
    began = time.monotonic()
    if shape == "agy":
        code, run, timed_out = agy_job.run(
            argv, str(tmp), 60, job, job / "stderr.log",
            stop_quiet_commands=True)
    else:
        code, run, timed_out = codex_job.run(
            argv, str(tmp), 60, job, job / "stderr.log",
            stop_quiet_commands=True)
    took = time.monotonic() - began
    helper = int(pidfile.read_text())
    return job, code, run, timed_out, took, helper


def main() -> int:
    loam_stuck.CHILD_STOP_MINUTES = 4 / 60   # four seconds of silence
    loam_stuck.CHECK_SECONDS = 0.5
    with tempfile.TemporaryDirectory(prefix="stuck-") as name:
        tmp = Path(name)

        # New engineer calls use resumable shells. Even an active command
        # event must never arm the old process-tree kill timer.
        with patch.object(loam_stuck, "Watch", side_effect=AssertionError("watch armed")):
            for runner in (agy_job, codex_job):
                rc, _, _ = runner.run(["/bin/true"], str(tmp), 10, tmp, tmp / "err")
                check(f"{runner.__name__}: engineer runs without a kill timer", rc, 0)

        job, code, run, timed_out, took, helper = run_stub(tmp, "agy", "own-group")
        stops = loam_stuck.read_stops(job)
        check("agy: the engine finished on its own", (code, timed_out), (0, False))
        check("agy: well inside its 60 s ceiling", took < 30, True)
        check("agy: the engine was told the command died",
              (run.status, (run.failed or "")), ("SUCCESS", ""))
        check("agy: one stop recorded", len(stops), 1)
        check("agy: the stop is the command, by its group",
              (stops[0]["argv"], stops[0]["how"]) if stops else None,
              ("bash -c sleep 600; echo slept", "SIGTERM (group)"))
        check("agy: the stop names what the engine said it ran",
              stops[0]["command"] if stops else None, "sleep 600")
        check("agy: the helper started before it was not touched (killed by the stub at its end only)",
              "helper" not in json.dumps(stops), True)
        stream = (job / agy_job.STREAM).read_text(encoding="utf-8")
        check("agy: the stream went on past the stop", '"status": "SUCCESS"' in stream, True)

        job, code, run, timed_out, took, helper = run_stub(tmp, "codex", "same-group")
        stops = loam_stuck.read_stops(job)
        check("codex: the engine finished on its own", (code, timed_out), (0, False))
        check("codex: one stop, by subtree (it shares the engine's group)",
              [s["how"] for s in stops], ["SIGTERM (subtree)"])
        check("codex: the stream records the command's exit after the stop",
              '"exit_code": -15' in (job / codex_job.STREAM).read_text(encoding="utf-8")
              or '"exit_code": 143' in (job / codex_job.STREAM).read_text(encoding="utf-8"), True)

        job, code, run, timed_out, took, helper = run_stub(tmp, "agy", "background")
        check("background: a silence after the command returned stops nothing",
              loam_stuck.read_stops(job), [])

        check("receipt: no stops, no paragraph", loam_stuck.receipt_text(job), "")
        sample = tmp / "agy-own-group"
        text = loam_stuck.receipt_text(sample)
        check("receipt: a stop is named with its command", "`bash -c sleep 600; echo slept`" in text
              and "stopped 1 command" in text, True)

        # The busy readers, on the shapes the real engines write.
        check("agy_busy: ACTIVE tool", loam_stuck.agy_busy(
            {"event": "step_update", "step_update": {"state": "ACTIVE", "step_type": "tool",
             "tool_name": "run_command", "tool_info": {"parameters": {"CommandLine": "node --test"}}}}),
            "node --test")
        check("agy_busy: DONE tool", loam_stuck.agy_busy(
            {"event": "step_update", "step_update": {"state": "DONE", "step_type": "tool"}}), None)
        check("codex_busy: file_change is not a command", loam_stuck.codex_busy(
            {"type": "item.started", "item": {"type": "file_change"}}), None)

        # The status, from the launcher itself.
        loader = importlib.machinery.SourceFileLoader("engineer_job_t", str(ROOT / "bin" / "engineer-job"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        job_mod = importlib.util.module_from_spec(spec)
        loader.exec_module(job_mod)
        for platform in (False, True):
            project = job_mod.eng.Project(
                "sample", tmp, tmp / "jobs", platform=platform, record={})
            settings = job_mod.claude_job_settings(project, "20261117-120000-abcdef01")
            groups = settings["hooks"]["PreToolUse"]
            check("Claude job keeps its original guard source",
                  job_mod.GUARD_SETTINGS["hooks"]["PreToolUse"][0] in groups, not platform)
            check("Claude job sets its Bash foreground adapter", groups[-1]["matcher"], "Bash")

        now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
        check("status: a working job says its last event",
              job_mod.quiet_words({"last_activity": "2026-11-17T11:57:00Z"}, now),
              "last activity 2026-11-17T11:57:00Z (3 min ago)")
        words = job_mod.quiet_words({"last_activity": "2026-11-16T22:51:00Z",
                                     "quiet_since": "2026-11-16T22:51:00Z",
                                     "child_stops": 1}, now)
        check("status: a stuck one says so, with its stops",
              words, "NO ACTIVITY FOR 13 h 9 min (last engine event 2026-11-16T22:51:00Z); "
                     "it may be stuck; the launcher stopped 1 command that never returned")

        sessions = tmp / "sessions"
        (sessions / "sid" / "subagents").mkdir(parents=True)
        main_t = sessions / "sid.jsonl"
        main_t.write_text("{}\n")
        sub = sessions / "sid" / "subagents" / "agent-1.jsonl"
        sub.write_text("{}\n")
        old = time.time() - 3600
        os.utime(main_t, (old, old))
        saved = job_mod.loam_session.session_file
        job_mod.loam_session.session_file = lambda sid: main_t
        try:
            last = job_mod.transcript_last("sid")
        finally:
            job_mod.loam_session.session_file = saved
        check("transcript_last: a Claude subagent's line is the session's activity",
              abs(last.timestamp() - sub.stat().st_mtime) < 1, True)

    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
