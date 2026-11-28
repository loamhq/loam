#!/usr/bin/env python3
"""Translate provider stream fixtures and check login state without starting a turn."""

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

# CLI 1.5.3 unauthenticated response with piped stdin.
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
    assert "--add-dir" not in line
    line = agy.argv("-p looks like a flag", "c9", "m", "high",
                    extra_dirs=[Path("/s/one"), Path("/s/two")])
    assert line[1] == "--print=-p looks like a flag"
    assert line[line.index("--conversation") + 1] == "c9"
    assert line[line.index("--model") + 1] == "m"
    assert line[line.index("--effort") + 1] == "high"
    add_dirs = [line[i + 1] for i, tok in enumerate(line) if tok == "--add-dir"]
    assert add_dirs == ["/s/one", "/s/two"], add_dirs


def test_split_rules_and_sync_instructions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "alpha"
        ws.mkdir()
        (ws / "agent.json").write_text('{"name": "alpha", "display": "Alpha"}\n')
        (ws / "soul.md").write_text("# Soul\n\nSTART_CANARY\n" + ("s" * 7000 + "\n\n") * 2)
        (ws / "map.md").write_text("# Map\n\n" + ("é" * 500 + "\n") * 25 + "TAIL_CANARY\n")
        (ws / "CLAUDE.md").write_text("You are Alpha.\n\n@soul.md\n\n@map.md\n")

        # Plant obsolete untriggered rule file, a stale extra part, and an unrelated rule file.
        rules_dir = ws / agy.RULES_DIR
        rules_dir.mkdir(parents=True)
        legacy = ws / agy.LEGACY_RULES_FILE
        legacy.write_text("old untriggered rule\n")
        stale = rules_dir / "loam-persona-99.md"
        stale.write_text("---\ntrigger: always_on\n---\nstale\n")
        custom = rules_dir / "custom.md"
        custom.write_text("---\ntrigger: always_on\n---\ncustom\n")

        issues_before = agy.rule_file_issues(ws)
        assert any("obsolete" in i for i in issues_before), issues_before

        paths, changed = agy.sync_instructions(ws)
        assert changed is True
        assert len(paths) >= 2, paths
        assert paths[0] == ws / agy.RULES_FILE
        assert not legacy.exists()
        assert not stale.exists()
        assert custom.is_file()
        assert agy.rule_file_issues(ws) == []

        combined = ""
        for p in paths:
            raw = p.read_bytes()
            text = raw.decode("utf-8")
            assert len(raw) <= agy.RULE_MAX_BYTES < agy.AGY_RULE_LIMIT_BYTES, (p, len(raw))
            assert agy.RULE_TRIGGER_RE.match(text), (p, text[:120])
            combined += text
        assert "You are Alpha." in combined
        assert "START_CANARY" in combined
        assert "TAIL_CANARY" in combined
        assert "Your current name in Loam is “Alpha”." in combined

        # Second sync is a no-op.
        paths2, changed2 = agy.sync_instructions(ws)
        assert paths2 == paths and changed2 is False

        # Shrinking back down removes excess parts.
        (ws / "soul.md").write_text("# Soul\n\nshort\n")
        (ws / "map.md").write_text("# Map\n\nshort\n")
        paths3, changed3 = agy.sync_instructions(ws)
        assert changed3 is True and len(paths3) == 1
        assert [p.name for p in agy.managed_rule_files(ws)] == ["loam-persona-01.md"]
        assert custom.is_file()

        # Oversized single line splits safely on UTF-8 boundaries.
        long_parts = agy.split_rules("€" * 1000, max_bytes=600)
        assert len(long_parts) > 1
        for _rel, body in long_parts:
            assert len(body.encode("utf-8")) <= 600
            assert agy.RULE_TRIGGER_RE.match(body)

        # rule_file_issues catches missing frontmatter and oversized files.
        paths3[0].write_text("no frontmatter\n")
        assert any("always_on" in i for i in agy.rule_file_issues(ws))
        paths3[0].write_text("---\ntrigger: always_on\n---\n" + ("x" * (agy.AGY_RULE_LIMIT_BYTES + 1)))
        assert any("per-file cap" in i for i in agy.rule_file_issues(ws))


def test_skill_dirs() -> None:
    import loam_skills as sk  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        vault = root / "vault"
        sys_dir = vault / lc.vault_folder("system")
        own_shelf = sys_dir / sk.OWN_DIR
        loam_shelf = sys_dir / sk.LOAM_DIR / "skills"
        external = root / "external-skill"
        ws = root / "ai-labs" / "alpha"
        for d in (own_shelf / "shared-one", loam_shelf / "loam-one",
                  external, ws / "skills" / "mine", ws / ".agents" / "skills"):
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"---\nname: {d.name}\ndescription: d\n---\n")
        (ws / ".agents" / "skills" / "mine").symlink_to("../../skills/mine")
        (ws / ".agents" / "skills" / "shared-one").symlink_to(str(own_shelf / "shared-one"))
        (ws / ".agents" / "skills" / "loam-one").symlink_to(str(loam_shelf / "loam-one"))
        (ws / ".agents" / "skills" / "ext").symlink_to(str(external))

        dirs = agy.skill_dirs(ws, vault=vault)
        assert dirs == [own_shelf.resolve(), loam_shelf.resolve(), external.resolve()], dirs

        # Missing shelves are omitted cleanly.
        empty_vault = root / "empty-vault"
        assert agy.skill_dirs(vault=empty_vault) == []


def test_provision_antigravity_and_files_authority() -> None:
    import loam_files  # noqa: PLC0415
    assert loam_files.visible("spaces", ["alpha"], ".agents") is True
    assert loam_files.authority("spaces", ["alpha", ".agents", "rules", "loam-persona-01.md"]) == "runtime"

    loader = importlib.machinery.SourceFileLoader(
        "provision_agy_test", str(ROOT / "bin" / "provision"))
    spec = importlib.util.spec_from_loader("provision_agy_test", loader)
    provision = importlib.util.module_from_spec(spec)
    loader.exec_module(provision)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        labs = root / "ai-labs"
        ws = labs / "beta"
        (ws / "skills").mkdir(parents=True)
        (ws / "agent.json").write_text(json.dumps({
            "name": "beta", "display": "Beta", "workspace": str(ws),
            "vault": str(root / "vault"),
        }))
        (ws / "soul.md").write_text("# Soul\n\n" + ("word " * 4000) + "\n")
        (ws / "map.md").write_text("# Map\n\n" + ("item " * 2500) + "\nEND_MARKER\n")
        (ws / "CLAUDE.md").write_text("You are Beta.\n\n@soul.md\n\n@map.md\n")
        (ws / ".agents" / "rules").mkdir(parents=True)
        legacy = ws / agy.LEGACY_RULES_FILE
        legacy.write_text("stale untriggered file\n")

        saved_labs = provision.AI_LABS
        provision.AI_LABS = labs
        try:
            provision.provision_antigravity(["beta"])
        finally:
            provision.AI_LABS = saved_labs

        assert not legacy.exists()
        parts = agy.managed_rule_files(ws)
        assert len(parts) >= 2, parts
        assert agy.rule_file_issues(ws) == []
        joined = "\n".join(p.read_text(encoding="utf-8") for p in parts)
        assert "You are Beta." in joined and "END_MARKER" in joined


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
    """After expiry, serve cached login state immediately and refresh it for the next read."""
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
