#!/usr/bin/env python3
"""hooks/loading_size.py: the persona's post-write warning, which never refuses.

Run as a subprocess on real PostToolUse payloads against a throwaway
workspace, as the CLI runs it. Checks: an oversized loading file warns with
its count and the limit; a small one and a file that does not load are
silent; `_active.md` losing its frontmatter warns; an Android-illegal name
warns; Codex's apply_patch is read for the same paths; and the exit code is
0 every time, because the write has already landed.

Run: python3 tests/test_loading_size.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = str(Path(__file__).resolve().parent.parent / "hooks" / "loading_size.py")
FAILURES = 0


def run(payload: dict, project: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, HOOK], input=json.dumps(payload), text=True,
        capture_output=True, env={"CLAUDE_PROJECT_DIR": str(project),
                                  "PATH": "/usr/bin:/bin"})
    ctx = ""
    if proc.stdout.strip():
        ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    return proc.returncode, ctx


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILURES  # noqa: PLW0603
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  | {detail[:160]}" if detail else ""))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "probe"
        ws.mkdir()
        (ws / "agent.json").write_text(json.dumps({
            "name": "probe", "workspace": str(ws), "vault": str(Path(tmp) / "vault")}))
        big = ws / "_always.md"
        big.write_text("x" * 10_500)
        code, ctx = run({"tool_name": "Edit", "tool_input": {"file_path": str(big)}}, ws)
        check("an oversized loading file warns with its count and the limit",
              code == 0 and "10,500" in ctx and "10,000" in ctx and "landed" in ctx, ctx)

        big.write_text("x" * 500)
        code, ctx = run({"tool_name": "Write", "tool_input": {"file_path": str(big)}}, ws)
        check("a loading file under the limit is silent", code == 0 and not ctx, ctx)

        other = ws / "notes.md"
        other.write_text("x" * 20_000)
        code, ctx = run({"tool_name": "Write", "tool_input": {"file_path": str(other)}}, ws)
        check("a file that does not load is never capped", code == 0 and not ctx, ctx)

        active = ws / "_active.md"
        active.write_text("no frontmatter here\n")
        code, ctx = run({"tool_name": "Edit", "tool_input": {"file_path": str(active)}}, ws)
        check("_active.md without its frontmatter warns", code == 0 and "frontmatter" in ctx, ctx)

        code, ctx = run({"tool_name": "Write",
                         "tool_input": {"file_path": str(ws / "a?b.md")}}, ws)
        check("an Android-illegal filename warns", code == 0 and "Android" in ctx, ctx)

        lane = ws / "crons" / "sweep.md"
        lane.parent.mkdir()
        lane.write_text("y" * 12_000)
        patch = "*** Begin Patch\n*** Update File: crons/sweep.md\n@@\n-y\n+z\n*** End Patch\n"
        code, ctx = run({"tool_name": "apply_patch", "cwd": str(ws),
                         "tool_input": {"command": ["apply_patch", patch]}}, ws)
        check("Codex's apply_patch is read for the same paths",
              code == 0 and "12,000" in ctx, ctx)

        code, ctx = run({"tool_name": "Write",
                         "tool_input": {"file_path": "/etc/anything"}}, ws)
        check("a write anywhere on the box passes silently", code == 0 and not ctx, ctx)
    print(f"\nFAILURES: {FAILURES}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
