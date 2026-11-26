#!/usr/bin/env python3
"""hooks/loading_size.py and hooks/antigravity_loading_size.py: post-write size notices.

Run as subprocesses on real hook payloads against a throwaway workspace, as
each provider CLI runs them. Checks: an oversized map/cron warns at 10,000;
understanding.md gives a one-shot notice past 4,000 (and stays silent at 4,000);
Antigravity's PostToolUse + PostInvocation delivers the notice once and does
not repeat it; Codex's apply_patch is read for the same paths; Android-illegal
names warn; and nothing is ever refused (exit 0).

Run: python3 tests/run-all test_loading_size.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import loam_memory  # noqa: E402

HOOK = str(ROOT / "hooks" / "loading_size.py")
AGY_HOOK = str(ROOT / "hooks" / "antigravity_loading_size.py")
LANE_CHECK = str(ROOT / "bin" / "lane-check")
FAILURES = 0


def run(payload: dict, project: Path) -> tuple[int, str]:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project)}
    proc = subprocess.run(
        [sys.executable, HOOK], input=json.dumps(payload), text=True,
        capture_output=True, env=env)
    ctx = ""
    if proc.stdout.strip():
        ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    return proc.returncode, ctx


def run_agy(payload: dict, state_dir: Path) -> tuple[int, dict]:
    env = {**os.environ, "LOAM_AGY_HOOK_STATE": str(state_dir)}
    proc = subprocess.run(
        [sys.executable, AGY_HOOK], input=json.dumps(payload), text=True,
        capture_output=True, env=env)
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES  # noqa: PLW0603
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  | {detail[:180]}" if detail else ""))


def main() -> int:
    mark = loam_memory.UNDERSTANDING_MARK_CHARS
    check("understanding size mark is 4,000 in one place", mark == 4_000, str(mark))

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "probe"
        ws.mkdir()
        (ws / "agent.json").write_text(json.dumps({
            "name": "probe", "workspace": str(ws), "vault": str(Path(tmp) / "vault")}))

        # map.md and crons/*.md (10,000 limit)
        big = ws / "map.md"
        big.write_text("x" * 10_500)
        code, ctx = run({"tool_name": "Edit", "tool_input": {"file_path": str(big)}}, ws)
        check("an oversized map warns with its count and the limit",
              code == 0 and "10,500" in ctx and "10,000" in ctx and "landed" in ctx, ctx)

        big.write_text("x" * 500)
        code, ctx = run({"tool_name": "Write", "tool_input": {"file_path": str(big)}}, ws)
        check("a map under the limit is silent", code == 0 and not ctx, ctx)

        # understanding.md (4,000 soft mark) on Claude Code
        u = ws / "understanding.md"
        u.write_text("u" * mark)
        code, ctx = run({"tool_name": "Write", "tool_input": {"file_path": str(u)}}, ws)
        check("understanding.md right at the mark (4,000) is silent", code == 0 and not ctx, ctx)

        u.write_text("u" * (mark + 1))
        code, ctx = run({"tool_name": "Edit", "tool_input": {"file_path": str(u)}}, ws)
        check("Claude Code: understanding.md over the mark (4,001) notices size and mark",
              code == 0 and f"{mark + 1:,}" in ctx and f"{mark:,}" in ctx and "landed" in ctx, ctx)

        # Codex apply_patch on understanding.md (Add File and Update File)
        u.write_text("u" * (mark + 250))
        patch = "*** Begin Patch\n*** Update File: understanding.md\n@@\n-u\n+v\n*** End Patch\n"
        code, ctx = run({"tool_name": "apply_patch", "cwd": str(ws),
                         "tool_input": {"command": ["apply_patch", patch]}}, ws)
        check("Codex: apply_patch on understanding.md over the mark notices size and mark",
              code == 0 and f"{mark + 250:,}" in ctx and f"{mark:,}" in ctx, ctx)

        # Antigravity PostToolUse + PostInvocation (one-shot, never repeated)
        agy_state = Path(tmp) / "agy-pending"
        conv = "conv-test-1"
        code, out = run_agy({
            "hookEventName": "PostToolUse",
            "conversationId": conv,
            "cwd": str(ws),
            "workspacePaths": [str(ws)],
            "error": "",
            "toolCall": {"name": "write_file", "args": {"Path": str(u)}},
        }, agy_state)
        check("Antigravity PostToolUse returns empty object and queues notice",
              code == 0 and out == {}, str(out))

        code, out = run_agy({
            "hookEventName": "PostInvocation",
            "conversationId": conv,
            "cwd": str(ws),
            "workspacePaths": [str(ws)],
        }, agy_state)
        injected = (((out.get("injectSteps") or [{}])[0]
                     .get("systemMessage") or {}).get("systemMessage") or "")
        check("Antigravity PostInvocation injects notice with size and mark",
              code == 0 and f"{mark + 250:,}" in injected and f"{mark:,}" in injected
              and injected.startswith("Loam: "), injected)

        code, out = run_agy({
            "hookEventName": "PostInvocation",
            "conversationId": conv,
            "cwd": str(ws),
            "workspacePaths": [str(ws)],
        }, agy_state)
        check("Antigravity next PostInvocation does not repeat the notice",
              code == 0 and out == {}, str(out))

        # lane-check does NOT repeat warnings for oversized understanding.md
        lc_proc = subprocess.run([sys.executable, LANE_CHECK, "--json", str(ws)],
                                 capture_output=True, text=True)
        lc_data = json.loads(lc_proc.stdout) if lc_proc.stdout.strip() else {}
        check("lane-check does not repeat warnings on oversized understanding.md",
              lc_proc.returncode == 0 and not lc_data.get("problems")
              and not lc_data.get("warnings"), str(lc_data))

        # Unrelated files and Android checks still behave as expected
        other = ws / "notes.md"
        other.write_text("x" * 20_000)
        code, ctx = run({"tool_name": "Write", "tool_input": {"file_path": str(other)}}, ws)
        check("a file that does not load is never capped", code == 0 and not ctx, ctx)

        code, ctx = run({"tool_name": "Write",
                         "tool_input": {"file_path": str(ws / "a?b.md")}}, ws)
        check("an Android-illegal filename warns", code == 0 and "Android" in ctx, ctx)

        lane = ws / "crons" / "sweep.md"
        lane.parent.mkdir()
        lane.write_text("y" * 12_000)
        patch = "*** Begin Patch\n*** Update File: crons/sweep.md\n@@\n-y\n+z\n*** End Patch\n"
        code, ctx = run({"tool_name": "apply_patch", "cwd": str(ws),
                         "tool_input": {"command": ["apply_patch", patch]}}, ws)
        check("Codex's apply_patch is read for cron lane files",
              code == 0 and "12,000" in ctx, ctx)

        code, ctx = run({"tool_name": "Write",
                         "tool_input": {"file_path": "/etc/anything"}}, ws)
        check("a write anywhere on the box passes silently", code == 0 and not ctx, ctx)
    print(f"\nFAILURES: {FAILURES}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
