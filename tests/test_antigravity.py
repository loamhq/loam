#!/usr/bin/env python3
"""Antigravity, the third provider (2026-11-09), held to the other two.

No login and no turn: the translation is fed the documented stream-json
shapes (https://antigravity.google/docs/cli/headless) plus the one result
this box has actually seen, the unauthenticated one; the persona's
instructions are held to the same sources Codex is given; its MCP servers to
the ones ``.mcp.json`` names; and threads accept the engine.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import loam_antigravity as agy  # noqa: E402
import loam_broker as cfg  # noqa: E402
import loam_codex as codex  # noqa: E402
import loam_config as lc  # noqa: E402
import loam_threads as threads  # noqa: E402

# Seen on this box, 1.5.3, with no login (stdin piped).
UNAUTH = {"event": "result", "result": {
    "conversation_id": "", "status": "ERROR", "response": "",
    "error": "authentication failed or timed out", "duration_seconds": 0,
    "num_turns": 0, "usage": {"input_tokens": 0, "output_tokens": 0,
                              "thinking_tokens": 0, "cache_read_tokens": 0,
                              "total_tokens": 0},
    "finished_reason": "error"}}


def test_translation() -> None:
    t = agy.Translator("s1")
    events = []
    for line in [
        {"event": "init", "init": {"cwd": "/w", "tools": ["run_command"],
                                   "permission_mode": "always-proceed",
                                   "model": "gemini-x"}},
        {"event": "step_update", "conversation_id": "c9", "step_index": 0,
         "state": "DONE", "step_type": "user_input"},
        {"event": "step_update", "conversation_id": "c9", "step_index": 1,
         "state": "ACTIVE", "step_type": "tool", "tool_name": "run_command",
         "tool_info": {"args": {"command": "ls"}}},
        {"event": "step_update", "conversation_id": "c9", "step_index": 1,
         "state": "DONE", "step_type": "tool", "tool_name": "run_command",
         "tool_info": {"args": {"command": "ls"}, "output": "a\nb"}},
        {"event": "step_update", "conversation_id": "c9", "step_index": 2,
         "state": "ACTIVE", "step_type": "agent_response", "text_delta": "Hel"},
        {"event": "step_update", "conversation_id": "c9", "step_index": 2,
         "state": "DONE", "step_type": "agent_response", "text_delta": "lo"},
        {"event": "result", "result": {"conversation_id": "c9",
                                       "status": "SUCCESS", "response": "Hello",
                                       "duration_seconds": 1.5, "num_turns": 1,
                                       "usage": {"input_tokens": 10}}},
    ]:
        events += t.feed(line)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "system" and events[0]["subtype"] == "init", kinds
    calls = [b for e in events if e["type"] == "assistant"
             for b in e["message"]["content"] if b["type"] == "tool_use"]
    assert calls == [{"type": "tool_use", "id": "agy-1", "name": "run_command",
                      "input": {"command": "ls"}}], calls
    results = [b for e in events if e["type"] == "user"
               for b in e["message"]["content"]]
    assert results[0]["content"] == "a\nb", results
    deltas = "".join(e["event"]["delta"]["text"] for e in events
                     if e["type"] == "stream_event"
                     and e["event"]["type"] == "content_block_delta")
    assert deltas == "Hello", deltas
    texts = [b["text"] for e in events if e["type"] == "assistant"
             for b in e["message"]["content"] if b["type"] == "text"]
    assert texts == ["Hello"], texts
    last = events[-1]
    assert last["type"] == "result" and last["subtype"] == "success"
    assert last["is_error"] is False and last["duration_ms"] == 1500
    assert t.conversation_id == "c9"
    assert all(e["session_id"] == "s1" for e in events)


def test_unauthenticated_result() -> None:
    [res] = agy.Translator("s2").feed(UNAUTH)
    assert res["is_error"] and res["subtype"] == "error_during_execution"
    assert res["result"] == "authentication failed or timed out"


def test_argv() -> None:
    line = agy.argv("hi there", None, None, None)
    assert line[1] == "--print=hi there"
    assert "--dangerously-skip-permissions" in line
    assert "--conversation" not in line
    line = agy.argv("-p looks like a flag", "c9", "m", "high")
    assert line[1] == "--print=-p looks like a flag"
    assert line[line.index("--conversation") + 1] == "c9"
    assert line[line.index("--model") + 1] == "m"
    assert line[line.index("--effort") + 1] == "high"


def test_instructions_same_sources() -> None:
    workspace = cfg.workspace(lc.LEAD_AGENT)
    if not (workspace / "CLAUDE.md").is_file():
        print(f"SKIP instructions: no {lc.LEAD_AGENT} workspace")
        return
    mine = agy.instructions(workspace)
    theirs = codex.instructions(workspace)
    assert mine.startswith(agy.PROVIDER_LINE)
    assert mine[len(agy.PROVIDER_LINE):] == theirs[len(codex.PROVIDER_LINE):]


def test_mcp_matches_claude() -> None:
    loader = importlib.machinery.SourceFileLoader(
        "provision", str(ROOT / "bin" / "provision"))
    spec = importlib.util.spec_from_loader("provision", loader)
    provision = importlib.util.module_from_spec(spec)
    loader.exec_module(provision)
    workspace = provision.AI_LABS / lc.LEAD_AGENT
    if not (workspace / "agent.json").is_file():
        print(f"SKIP mcp: no {lc.LEAD_AGENT} workspace")
        return
    claude = provision.mcp_config(lc.LEAD_AGENT, workspace)["mcpServers"]
    mine = provision.antigravity_mcp(lc.LEAD_AGENT, workspace)["mcpServers"]
    assert set(mine) == set(claude), (set(mine), set(claude))
    for name, spec in claude.items():
        if spec.get("type") == "http":
            assert mine[name] == {"serverUrl": spec["url"]}
        else:
            assert mine[name]["command"] == spec["command"]
            assert mine[name]["args"] == spec.get("args", [])
            for key, value in (spec.get("env") or {}).items():
                assert mine[name]["env"][key] == value


def test_wired_and_engines() -> None:
    assert "antigravity" in cfg.WIRED_PROVIDERS
    assert cfg.PROVIDER_NAMES["antigravity"] == "Antigravity"
    assert "antigravity" in threads.ENGINES


def test_prompt_arg_spills_long_text() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        before = agy.PROMPTS_DIR
        agy.PROMPTS_DIR = Path(tmp)
        try:
            assert agy.prompt_arg("s", "short") == "short"
            said = agy.prompt_arg("s", "x" * (agy.ARG_MAX_CHARS + 1))
            [path] = list(Path(tmp).iterdir())
            assert str(path) in said and path.read_text() == "x" * (agy.ARG_MAX_CHARS + 1)
        finally:
            agy.PROMPTS_DIR = before


def test_login_cache_serves_and_reasks_behind() -> None:
    """Past the minute the kept login is served at once and the CLI asked
    behind the call; the next call has the new answer (2026-11-09)."""
    import threading
    import time
    saved = (agy._probe, agy._login_cache)
    answers = iter([{"connected": False}, {"connected": True}])
    gate = threading.Event()

    def probe():
        out = next(answers)
        if out["connected"]:
            gate.wait(5)
        return out
    try:
        agy._probe, agy._login_cache = probe, None
        assert agy.login_state() == {"connected": False}       # first: in line
        agy._login_cache = (time.monotonic() - agy.LOGIN_CACHE_S - 1, *agy._login_cache[1:])
        started = time.monotonic()
        assert agy.login_state() == {"connected": False}       # stale, served now
        assert time.monotonic() - started < 1
        gate.set()
        for _ in range(50):
            if agy._login_cache[2] == {"connected": True}:
                break
            time.sleep(0.05)
        assert agy.login_state() == {"connected": True}        # the re-ask landed
    finally:
        agy._probe, agy._login_cache = saved


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok {test.__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
