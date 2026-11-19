#!/usr/bin/env python3
"""A project engineer on Antigravity (since 2026-11-15), against a stubbed `agy`.

What the launcher promises an Antigravity job, held here without a model:

  the setting       `antigravity` is a runtime a project engineer and the
                    default can take, and the platform engineer by its own
                    setting (2026-11-19; without one it is Claude Code
                    whatever its record says); nothing defaults to it.
  the models        `agy models`' tab-separated lines are read as ids; an id
                    that ends in its level carries it, and `--effort` is not
                    passed beside it (agy refuses the pair).
  the command line  print mode, stream-json, no approvals, the job directory
                    added, `--conversation` for a continuation.
  the instructions  the Claude engineer's CLAUDE.md and its imports, under
                    Antigravity's line, at the head of the one message.
  the stream        written line by line to the job directory; the
                    conversation id reported the moment it arrives; an ERROR
                    result, a missing result and a resume onto another
                    conversation are each a failure.
  the cap, clock    as Codex's: ended by process group.
  the wall          the harness held read-only by the mount namespace.
  a continuation    refused before anything starts when the conversation's
                    file is gone, because agy itself would start a new one.

Run: python3 tests/test_engineer_antigravity.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_agy_job as aj  # noqa: E402
import loam_antigravity as agy  # noqa: E402
import loam_engineer as eng  # noqa: E402
import loam_worktree as wt  # noqa: E402

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


job = load("engineer_job_under_test_agy", AGENTS / "bin" / "engineer-job")
drift = load("check_drift_under_test_agy", AGENTS / "bin" / "check-drift")
root = Path(tempfile.mkdtemp(prefix="loam-engineer-agy-"))


def project(record: dict) -> eng.Project:
    return eng.Project(record.get("name", "p"), root, root / "jobs",
                       platform=False, record=record)


def stub(body: str) -> Path:
    path = root / f"agy-stub-{len(list(root.glob('agy-stub-*')))}"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def event(obj: dict) -> str:
    return "echo '" + json.dumps(obj) + "'\n"


# `agy models` as 1.5.9 printed it on 2026-11-15 (a cut of its 22 lines).
MODELS_TEXT = ("Fetching available models...\n"
               "gemini-4-hydrogen-high\tGemini 4 Hydrogen (High)\n"
               "gemini-4-hydrogen-xhigh\tGemini 4 Hydrogen (XHigh)\n"
               "gemini-pro-orchestrator\tOrchestrator (Carbon)\n"
               "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
               "claude-opus-5-5-high\tClaude Opus 5.5 (High)\n"
               "gpt-oss-120b-medium\tGPT-OSS 120B (Medium)\n")

# One step as the wire carries it (1.5.9): under its own key.
def step(index, stype, state, **rest) -> dict:
    return {"event": "step_update", "step_update": {
        "conversation_id": "C-1", "step_index": index, "state": state,
        "step_type": stype, **rest}}


try:
    # -- the setting -------------------------------------------------------
    check("record runtime antigravity", project({"runtime": "antigravity"}).runtime,
          "antigravity")
    loam = eng.Project("Loam", root, root / "jobs", platform=True,
                       record={"runtime": "antigravity"})
    check("platform engineer: Claude Code with no setting, whatever its record says",
          loam.runtime, "claude")
    check("nothing defaults to it", eng.DEFAULT_RUNTIME, "codex")
    check("built-in: the trial's model, no separate effort",
          eng.builtin_choice("antigravity"), ("gemini-4-hydrogen-high", None))
    check("the page's id", eng.PROVIDER_OF["antigravity"], "antigravity")
    choice = eng.engineer_choice(project({"runtime": "codex"}),
                                 {"projects": {"p": {"provider": "antigravity",
                                                     "model": "gemini-4-hydrogen-xhigh"}}})
    check("a setting moves a Codex record to agy, with its model",
          (choice["runtime"], choice["model"], choice["source"]),
          ("antigravity", "gemini-4-hydrogen-xhigh", "own"))
    # Loam's own setting is a provider like the others' (2026-11-19); the
    # default for every other project is never Loam's.
    choice = eng.engineer_choice(loam, {"projects": {"Loam": {"provider": "antigravity"}}})
    check("Loam's own setting: antigravity", (choice["runtime"], choice["model"]),
          ("antigravity", eng.AGY_ENGINEER_MODEL))
    choice = eng.engineer_choice(loam, {"default": {"provider": "antigravity"}})
    check("...the default is never Loam's", (choice["runtime"], choice["source"]),
          ("claude", "automatic"))

    # -- the models --------------------------------------------------------
    models = agy.parse_models(MODELS_TEXT)
    check("models: every tab-separated id, none of the header",
          [m["id"] for m in models],
          ["gemini-4-hydrogen-high", "gemini-4-hydrogen-xhigh",
           "gemini-pro-orchestrator", "gemini-3.8-flash-low",
           "claude-opus-5-5-high", "gpt-oss-120b-medium"])
    check("models: the name after the tab", models[0]["name"], "Gemini 4 Hydrogen (High)")
    check("level: carried by -high, -xhigh",
          (agy.carries_level("gemini-4-hydrogen-high"),
           agy.carries_level("gemini-4-hydrogen-xhigh"),
           agy.carries_level("gemini-pro-orchestrator"),
           agy.carries_level("gemini-4-hydrogen"), agy.carries_level(None)),
          (True, True, False, False, False))
    persona = agy.argv("hi", None, "gemini-4-hydrogen-low", "xhigh")
    check("persona argv: no --effort beside an id that carries one",
          "--effort" in persona, False)
    persona = agy.argv("hi", None, "gemini-4-hydrogen", "high")
    check("persona argv: --effort beside a family id",
          persona[-2:], ["--effort", "high"])

    check("drift: antigravity with the built-in listed is clean",
          drift.runtime_findings(project({"runtime": "antigravity"}), models)
          if Path(agy.AGY).exists() else [], [])
    found = drift.runtime_findings(project({"runtime": "antigravity"}),
                                   [{"id": "gemini-0"}])
    check("drift: an unlisted built-in is named",
          any("AGY_ENGINEER_MODEL" in f for f in found), True)

    # -- the persona translation, on the wire's shape ----------------------
    t = agy.Translator("s1")
    events = []
    for line in ({"event": "init", "conversation_id": "C-1", "init": {"model": "m"}},
                 step(1, "tool", "ACTIVE", tool_name="run_command",
                      tool_info={"name": "run_command",
                                 "parameters": {"CommandLine": "ls"}}),
                 step(1, "tool", "DONE", tool_name="run_command",
                      tool_info={"name": "run_command",
                                 "parameters": {"CommandLine": "ls"}, "output": "a"}),
                 step(2, "agent_response", "ACTIVE", text_delta="He"),
                 step(2, "agent_response", "DONE", text_delta="y")):
        events += t.feed(line)
    calls = [b for e in events if e["type"] == "assistant"
             for b in e["message"]["content"] if b["type"] == "tool_use"]
    check("translator: a nested step's tool call, with its parameters",
          calls and calls[0]["input"], {"CommandLine": "ls"})
    texts = [b["text"] for e in events if e["type"] == "assistant"
             for b in e["message"]["content"] if b["type"] == "text"]
    check("translator: a nested step's text", texts, ["Hey"])
    check("translator: the conversation", t.conversation_id, "C-1")

    # -- the instructions --------------------------------------------------
    ws = root / "tree"
    (ws / ".git").mkdir(parents=True)
    (ws / "CLAUDE.md").write_text("@role.md\n\n@project.md\n")
    (ws / "role.md").write_text("ROLE TEXT\n")
    (ws / "project.md").write_text("PROJECT TEXT\n")
    text = aj.instructions(ws)
    check("instructions: Antigravity's line first", text.startswith(aj.PROVIDER_LINE), True)
    check("instructions: the role's and project's text",
          ("ROLE TEXT" in text, "PROJECT TEXT" in text), (True, True))
    message = aj.compose(text, "THE PROMPT")
    check("message: instructions, then the prompt",
          message.index("ROLE TEXT") < message.index("THE PROMPT"), True)

    # -- the command line --------------------------------------------------
    jd = root / "job"
    jd.mkdir()
    argv = aj.argv(prompt="P", job_dir=jd, conversation=None)
    check("argv: print mode, stream-json, no approvals",
          (argv[0], argv[1], "stream-json" in argv,
           "--dangerously-skip-permissions" in argv), (agy.AGY, "--print=P", True, True))
    check("argv: the built-in model", argv[argv.index("--model") + 1],
          eng.AGY_ENGINEER_MODEL)
    check("argv: no --effort beside it", "--effort" in argv, False)
    check("argv: the job directory added", argv[argv.index("--add-dir") + 1], str(jd))
    check("argv: a fresh job names no conversation", "--conversation" in argv, False)
    cont = aj.argv(prompt="P", job_dir=jd, conversation="C-9",
                   model="gemini-4-hydrogen", effort="high")
    check("argv: continuation resumes its conversation",
          cont[cont.index("--conversation") + 1], "C-9")
    check("argv: a family id takes the effort", cont[cont.index("--effort") + 1], "high")
    check("launched_model reads it back",
          job.launched_model(argv), (eng.AGY_ENGINEER_MODEL, None))
    short = aj.prompt_arg(jd, "short")
    check("prompt: short is inline, and kept", (short, (jd / aj.PROMPT_FILE).read_text()),
          ("short", "short"))
    long = aj.prompt_arg(jd, "x" * (agy.ARG_MAX_CHARS + 1))
    check("prompt: long is a pointer to the job's copy",
          str(jd / aj.PROMPT_FILE) in long and len(long) < 1000, True)

    env = aj.env({"GEMINI_API_KEY": "k", "CLAUDE_CODE_OAUTH_TOKEN": "t", "A": "1"})
    check("env: API key and Claude login out",
          ("GEMINI_API_KEY" in env, "CLAUDE_CODE_OAUTH_TOKEN" in env, env["A"]),
          (False, False, "1"))

    # -- the stream --------------------------------------------------------
    usage = {"input_tokens": 100, "output_tokens": 7, "thinking_tokens": 3,
             "cache_read_tokens": 40, "total_tokens": 107}
    good = stub(
        event({"event": "init", "conversation_id": "C-1",
               "init": {"model": "gemini-4-hydrogen-high"}})
        + event(step(0, "user_input", "DONE"))
        + event(step(1, "tool", "ACTIVE", tool_name="run_command"))
        + event(step(1, "tool", "DONE", tool_name="run_command"))
        + event(step(2, "agent_response", "ACTIVE", text_delta="o"))
        + event(step(2, "agent_response", "DONE", text_delta="k",
                     usage={"input_tokens": 60, "output_tokens": 5,
                            "thinking_tokens": 2, "cache_read_tokens": 30}))
        + event({"event": "result", "result": {
            "conversation_id": "C-1", "status": "SUCCESS", "response": "ok",
            "num_turns": 1, "usage": usage}}))
    seen = []
    rc, run, timed_out = aj.run([str(good)], str(ws), 30, jd, jd / "err",
                                on_thread=seen.append)
    check("run: exit 0, not timed out, no failure", (rc, timed_out, run.failed),
          (0, False, None))
    check("run: conversation reported once", seen, ["C-1"])
    check("run: round trips = tool steps + replies",
          (run.tool_calls, run.responses, run.round_trips), (1, 1, 2))
    check("run: usage is this run's steps, not the conversation's result",
          (run.usage, run.conversation_usage["input_tokens"]),
          ({"input_tokens": 60, "cache_read_tokens": 30,
            "output_tokens": 5, "thinking_tokens": 2}, 100))
    check("run: model from init", run.model, "gemini-4-hydrogen-high")
    check("run: stream on disk, every line",
          len((jd / aj.STREAM).read_text().splitlines()), 7)
    check("usage text: tokens, no dollars", "no dollar cost" in aj.usage_text(run), True)

    bad = stub(event({"event": "result", "result": {
        "conversation_id": "", "status": "ERROR", "response": "",
        "error": "invalid model selection"}}) + "exit 1\n")
    rc, run, _ = aj.run([str(bad)], str(ws), 30, jd, jd / "err")
    check("an ERROR result: exit 1 and its error", (rc, run.failed),
          (1, "invalid model selection"))
    check("an ERROR result that is not quota is not called quota", run.quota, False)
    quota = stub(event({"event": "result", "result": {
        "status": "ERROR", "error": "RESOURCE_EXHAUSTED: You have exhausted your capacity"}})
        + "exit 1\n")
    rc, run, _ = aj.run([str(quota)], str(ws), 30, jd, jd / "err")
    check("a quota error is named as one", run.quota, True)
    # A side call's 503 on another model (2026-11-19, the job that ran on a
    # Claude model and was reported ERROR for Gemini's capacity).
    side = ("API error (attempt 1): UNAVAILABLE (code 503): No capacity available "
            "for model gemini-4-hydrogen-high on the server")
    check("side call: a 503 naming another model is the side call's",
          aj.side_call_failure(side, "claude-opus-5-5-high"), "gemini-4-hydrogen-high")
    check("side call: a 503 naming the job's own model is the job's",
          aj.side_call_failure(side, "gemini-4-hydrogen-high"), None)
    check("side call: the same model without its level is the job's",
          aj.side_call_failure(side.replace("-high on", " on"), "gemini-4-hydrogen-high"),
          None)
    check("side call: a 503 naming no model is the job's",
          aj.side_call_failure("UNAVAILABLE (code 503)", "claude-opus-5-5-high"), None)
    check("side call: a non-transient error is the job's",
          aj.side_call_failure("invalid model selection for model gemini-x",
                               "claude-opus-5-5-high"), None)
    silent = stub(event({"event": "init", "conversation_id": "C-2", "init": {}}))
    rc, run, _ = aj.run([str(silent)], str(ws), 30, jd, jd / "err")
    check("no result event: a failure even on exit 0",
          (rc, run.failed is not None), (0, True))
    other = stub(event({"event": "init", "conversation_id": "C-new", "init": {}})
                 + event({"event": "result", "result": {
                     "conversation_id": "C-new", "status": "SUCCESS"}}))
    rc, run, _ = aj.run([str(other)], str(ws), 30, jd, jd / "err", resumed="C-old")
    check("a resume onto a new conversation: a failure",
          (run.resumed_other, "C-old" in (run.failed or "")), (True, True))

    # -- the cap and the clock ---------------------------------------------
    old_cap = eng.MAX_TURNS
    eng.MAX_TURNS = 3
    try:
        loop = stub(event({"event": "init", "conversation_id": "C-cap", "init": {}})
                    + "while true; do\n  "
                    + event(step(1, "tool", "DONE", tool_name="run_command"))
                    + "  sleep 0.05\ndone\n")
        started = time.monotonic()
        rc, run, _ = aj.run([str(loop)], str(ws), 60, jd, jd / "err")
        check("cap: ended at MAX_TURNS", (run.capped, run.round_trips), (True, 3))
        check("cap: well before the clock", time.monotonic() - started < 20, True)
    finally:
        eng.MAX_TURNS = old_cap
    hang = stub(event({"event": "init", "conversation_id": "C-hang", "init": {}})
                + "sleep 300\n")
    started = time.monotonic()
    rc, run, timed_out = aj.run([str(hang)], str(ws), 2, jd, jd / "err")
    check("clock: timed out within seconds",
          (timed_out, time.monotonic() - started < 15, run.conversation_id),
          (True, True, "C-hang"))

    # -- the wall ----------------------------------------------------------
    tree = root / "wall-tree"
    (tree / ".git").mkdir(parents=True)
    (tree / "role.md").write_text("r")
    wproj = eng.Project("w", root / "wall-canon", root / "wall-jobs",
                        platform=False, record={})
    (root / "wall-canon").mkdir()
    script = wt.wall_script(wproj, {"path": str(tree)}, jd,
                            [tree / "role.md", tree / "absent.md"])
    check("wall: the harness bound read-only",
          f"mount -o remount,bind,ro {tree / 'role.md'}" in script.replace("'", ""), True)
    check("wall: an absent path skipped", "absent.md" in script, False)
    check("wall: nothing protected by default",
          "role.md" in wt.wall_script(wproj, {"path": str(tree)}, jd), False)

    # -- a continuation ----------------------------------------------------
    cproj = eng.Project("p", root / "cp", root / "cp-jobs", platform=False,
                        record={"runtime": "antigravity"})
    parent_dir = root / "cp-jobs" / "20261115-000000-aaaaaaaa"
    parent_dir.mkdir(parents=True)
    (parent_dir / "session-id").write_text("C-cont\n")
    (parent_dir / "runtime").write_text("antigravity\n")
    (parent_dir / "result.json").write_text("{}")
    check("continuation: parent's runtime",
          job.job_runtime({"runtime": parent_dir / "runtime"}), "antigravity")
    old = aj.CONVERSATIONS
    aj.CONVERSATIONS = root / "convs"
    try:
        sid, refusal = job.resolve_continuation(cproj, "20261115-000000-aaaaaaaa")
        check("continuation: a missing conversation is refused, and where",
              (sid, refusal is not None and str(root / "convs") in refusal), (None, True))
        aj.CONVERSATIONS.mkdir()
        (aj.CONVERSATIONS / "C-cont.db").write_text("")
        check("continuation: an Antigravity parent resumes by its conversation",
              job.resolve_continuation(cproj, "20261115-000000-aaaaaaaa"),
              ("C-cont", None))
    finally:
        aj.CONVERSATIONS = old
finally:
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
