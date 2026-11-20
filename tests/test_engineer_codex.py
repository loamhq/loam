#!/usr/bin/env python3
"""Exercise the project engineer's Codex invocation and event handling with a stub CLI.

Run: python3 tests/test_engineer_codex.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_codex  # noqa: E402
import loam_codex_job as cj  # noqa: E402
import loam_engineer as eng  # noqa: E402

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
drift = load("check_drift_under_test", AGENTS / "bin" / "check-drift")
root = Path(tempfile.mkdtemp(prefix="loam-engineer-codex-"))


def project(record: dict) -> eng.Project:
    return eng.Project(record.get("name", "p"), root, root / "jobs",
                       platform=False, record=record)


def stub(body: str) -> Path:
    """A `codex` that prints `body` (a shell script's lines) as its run."""
    path = root / f"codex-stub-{len(list(root.glob('codex-stub-*')))}"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def event(obj: dict) -> str:
    return "echo '" + json.dumps(obj) + "'\n"


try:
    # -- the setting -------------------------------------------------------
    check("record runtime codex", project({"runtime": "codex"}).runtime, "codex")
    check("record runtime claude", project({"runtime": "claude"}).runtime, "claude")
    import loam_provider_defaults as providers
    original_available = providers.available
    providers.available = lambda provider: (provider in ("claude-code", "codex"), None)
    check("no runtime: automatic prefers a connected Codex", project({}).runtime, "codex")
    check("unknown runtime: automatic", project({"runtime": "gpt"}).runtime, "codex")
    loam = eng.Project("Loam", root, root / "jobs", platform=True,
                       record={"runtime": "codex"})
    check("platform engineer: Claude Code with no setting, whatever its record says",
          loam.runtime, "claude")
    providers.available = original_available

    models = [{"id": eng.CODEX_ENGINEER_MODEL, "efforts": ["low", "high"]}]
    check("drift: codex with a listed model is clean",
          drift.runtime_findings(project({"runtime": "codex"}), models), [])
    check("drift: claude is clean",
          drift.runtime_findings(project({"runtime": "claude"}), models), [])
    check("drift: missing runtime selects automatically",
          drift.runtime_findings(project({}), models), [])
    check("drift: unknown runtime is named",
          "none of" in drift.runtime_findings(project({"runtime": "x"}), models)[0], True)
    check("drift: an unlisted model is named",
          "not in Codex's model list" in drift.runtime_findings(
              project({"runtime": "codex"}), [{"id": "gpt-0", "efforts": []}])[0], True)
    check("drift: an untaken effort is named",
          "effort" in drift.runtime_findings(
              project({"runtime": "codex"}),
              [{"id": eng.CODEX_ENGINEER_MODEL, "efforts": ["low"]}])[0], True)

    # Exercise registered project settings with the Codex runtime.
    for path in sorted((AGENTS / "projects").glob("*.json")):
        check(f"registry {path.stem}: runtime",
              json.loads(path.read_text()).get("runtime"), "codex")

    # -- the instructions --------------------------------------------------
    ws = root / "tree"
    (ws / ".git").mkdir(parents=True)
    (ws / "CLAUDE.md").write_text("@role.md\n\n@project.md\n")
    (ws / "role.md").write_text("ROLE TEXT\n")
    (ws / "project.md").write_text("PROJECT TEXT\n")
    text = cj.instructions(ws)
    check("instructions: provider line first", text.startswith(cj.PROVIDER_LINE), True)
    check("instructions: CLAUDE.md, role, project in order",
          [text.index(f"Contents of {ws / n}") for n in
           ("CLAUDE.md", "role.md", "project.md")]
          == sorted(text.index(f"Contents of {ws / n}") for n in
                    ("CLAUDE.md", "role.md", "project.md")), True)
    check("instructions: the role's text is in", "ROLE TEXT" in text, True)

    # -- the command line --------------------------------------------------
    jd = root / "job"
    jd.mkdir()
    argv = cj.argv(workdir=ws, tree=ws, job_dir=jd, prompt="P", extra_dirs=[],
                   fork_from=None, instructions_text='say "hi"\n')
    joined = " ".join(argv)
    check("argv: exec", argv[:2], [loam_codex.CODEX, "exec"])
    check("argv: model from its one home",
          argv[argv.index("-m") + 1], eng.CODEX_ENGINEER_MODEL)
    check("argv: effort high", 'model_reasoning_effort="high"' in argv, True)
    check("argv: workspace-write sandbox", 'sandbox_mode="workspace-write"' in argv, True)
    check("argv: never asks", 'approval_policy="never"' in argv, True)
    check("argv: .git and job dir writable",
          f"sandbox_workspace_write.writable_roots={json.dumps([str(ws / '.git'), str(jd)])}"
          in argv, True)
    disabled = {argv[i + 1] for i, a in enumerate(argv) if a == "--disable"}
    check("argv: no connector apps, browser, desktop, plugins",
          {"apps", "browser_use", "computer_use", "plugins"} <= disabled,
          True)
    check("argv: user config ignored", "--ignore-user-config" in argv, True)
    check("argv: no bypass of the sandbox", "dangerously" in joined, False)
    check("argv: instructions as a TOML string",
          'developer_instructions="say \\"hi\\"\\n"' in argv, True)
    check("argv: prompt last", argv[-1], "P")
    prot = cj.argv(workdir=ws, tree=ws, job_dir=jd, prompt="P", extra_dirs=[],
                   fork_from=None, instructions_text="x",
                   protected=[ws / "role.md"])
    table = next(a for a in prot if a.startswith("permissions.engineer.filesystem="))
    check("profile: the tree and .git write, role.md read",
          all(f'"{k}"="{v}"' in table for k, v in (
              (ws, "write"), (ws / ".git", "write"), (jd, "write"),
              ("/", "read"), (ws / "role.md", "read"))), True)
    check("profile: selected", 'default_permissions="engineer"' in prot, True)
    fork = cj.argv(workdir=ws, tree=ws, job_dir=jd, prompt="P", extra_dirs=[],
                   fork_from="T-1", instructions_text="x")
    check("argv: continuation forks", (fork[2], fork[-2], fork[-1]), ("fork", "T-1", "P"))

    env = cj.env({"OPENAI_API_KEY": "k", "CLAUDE_CODE_OAUTH_TOKEN": "t", "A": "1"})
    check("env: API key and Claude login out",
          ("OPENAI_API_KEY" in env, "CLAUDE_CODE_OAUTH_TOKEN" in env, env["A"]),
          (False, False, "1"))

    # -- the stream, the thread, the usage ---------------------------------
    usage = {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 7,
             "reasoning_output_tokens": 3}
    good = stub(
        event({"type": "thread.started", "thread_id": "T-good"})
        + event({"type": "turn.started"})
        + event({"type": "item.completed", "item": {"type": "command_execution"}})
        + event({"type": "item.completed", "item": {"type": "agent_message"}})
        + event({"type": "turn.completed", "usage": usage}))
    seen = []
    rc, run, timed_out = cj.run([str(good)], str(ws), 30, jd, jd / "err",
                                on_thread=seen.append)
    check("run: exit 0, not timed out", (rc, timed_out), (0, False))
    check("run: thread id reported once", seen, ["T-good"])
    check("run: round trips = tool calls + turn ends",
          (run.tool_calls, run.turns, run.round_trips), (1, 1, 2))
    check("run: usage summed", run.usage, usage)
    check("run: stream on disk, every line",
          len((jd / cj.STREAM).read_text().splitlines()), 5)

    # -- the cap -----------------------------------------------------------
    old_cap = eng.MAX_TURNS
    eng.MAX_TURNS = 3
    try:
        loop = stub(event({"type": "thread.started", "thread_id": "T-cap"})
                    + "while true; do\n"
                    + "  " + event({"type": "item.completed",
                                    "item": {"type": "command_execution"}})
                    + "  sleep 0.05\ndone\n")
        started = time.monotonic()
        rc, run, timed_out = cj.run([str(loop)], str(ws), 60, jd, jd / "err")
        check("cap: ended at MAX_TURNS", (run.capped, run.round_trips), (True, 3))
        check("cap: well before the clock", time.monotonic() - started < 20, True)
    finally:
        eng.MAX_TURNS = old_cap

    # -- the wall clock ----------------------------------------------------
    hang = stub(event({"type": "thread.started", "thread_id": "T-hang"})
                + "sleep 300\n")
    started = time.monotonic()
    rc, run, timed_out = cj.run([str(hang)], str(ws), 2, jd, jd / "err")
    check("clock: timed out", timed_out, True)
    check("clock: within seconds of it", time.monotonic() - started < 15, True)
    check("clock: thread still known", run.thread_id, "T-hang")

    # A failed turn is an outcome, not a success.
    bad = stub(event({"type": "thread.started", "thread_id": "T-bad"})
               + event({"type": "turn.failed", "error": {"message": "401"}}))
    rc, run, _ = cj.run([str(bad)], str(ws), 30, jd, jd / "err")
    check("failed turn: recorded", run.failed, "401")

    # -- the stall ---------------------------------------------------------
    p = {"heartbeat": jd / "heartbeat"}
    stream = jd / cj.STREAM
    old = eng.now_utc() - timedelta(minutes=job.STALL_MINUTES + 5)
    os.utime(stream, (old.timestamp(), old.timestamp()))
    beat = job.beat_once(p, "unused", old, stream=stream)
    check("stall: read off the stream", beat["stalled_since"] is not None, True)
    os.utime(stream, None)
    beat = job.beat_once(p, "unused", old, stream=stream)
    check("stall: clears on a new line", beat["stalled_since"], None)

    # -- a continuation's CLI ----------------------------------------------
    parent = {"runtime": jd / "runtime"}
    check("continuation: no runtime file is claude", job.job_runtime(parent), "claude")
    (jd / "runtime").write_text("codex\n")
    check("continuation: parent's runtime", job.job_runtime(parent), "codex")

    # -- a continuation of a Codex job finds its rollout ---------------------
    cont = eng.Project("p", root / "cp", root / "cp-jobs", platform=False,
                       record={"runtime": "codex"})
    parent_dir = root / "cp-jobs" / "20261007-000000-aaaaaaaa"
    parent_dir.mkdir(parents=True)
    (parent_dir / "session-id").write_text("T-cont\n")
    (parent_dir / "runtime").write_text("codex\n")
    (parent_dir / "result.json").write_text("{}")
    old_home = loam_codex.CODEX_HOME
    loam_codex.CODEX_HOME = root / "home-c"
    try:
        sid, refusal = job.resolve_continuation(cont, "20261007-000000-aaaaaaaa")
        check("continuation: a missing rollout is refused, under CODEX_HOME",
              (sid, refusal is not None and str(root / "home-c") in refusal), (None, True))
        day = loam_codex.CODEX_HOME / "sessions" / "2026" / "10" / "07"
        day.mkdir(parents=True)
        (day / "rollout-2026-10-07T00-00-00-T-cont.jsonl").write_text("{}\n")
        check("continuation: a Codex parent resumes by its thread",
              job.resolve_continuation(cont, "20261007-000000-aaaaaaaa"), ("T-cont", None))
    finally:
        loam_codex.CODEX_HOME = old_home

    # -- the login's meters, from a rollout ---------------------------------
    old_home = loam_codex.CODEX_HOME
    loam_codex.CODEX_HOME = root / "home"
    try:
        day = loam_codex.CODEX_HOME / "sessions" / "2026" / "10" / "07"
        day.mkdir(parents=True)
        (day / "rollout-2026-10-07T00-00-00-T-m.jsonl").write_text(json.dumps(
            {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
                "primary": {"used_percent": 4.0, "window_minutes": 300, "resets_at": 1},
                "secondary": {"used_percent": 1.0, "window_minutes": 10080,
                              "resets_at": 2}}}}) + "\n")
        check("meters: five-hour and weekly",
              cj.meters("T-m"), {"five_hour": 4.0, "five_hour_resets_at": 1,
                                 "seven_day": 1.0, "seven_day_resets_at": 2})
        check("meters: none for an unknown thread", cj.meters("T-x"), None)
    finally:
        loam_codex.CODEX_HOME = old_home
finally:
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
