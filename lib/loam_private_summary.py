"""Ask the member for a private handoff. No process here owns a thread journal."""
import asyncio
import json
import os
from pathlib import Path
import tempfile

import loam_antigravity as agy
import loam_broker as cfg
import loam_codex as codex
from loam_provider_session import first_to_go


def command(session, prompt, scratch):
    """Fork durable provider history where supported. The foreground keeps its own cursor."""
    model = session.model or getattr(session, "_init_model", None)
    choice = ["--model", model] if model else []
    if session.provider == "claude-code":
        return ([cfg.CLAUDE, "--print", "--safe-mode", "--tools", "",
                 "--strict-mcp-config", "--no-session-persistence", "--output-format", "json",
                 "--resume", session.cli_session_id or session.id, "--fork-session", *choice],
                session.workspace, dict(os.environ, DISABLE_AUTOUPDATER="1"), prompt)
    if session.provider == "codex":
        argv = [codex.CODEX, "exec", "--skip-git-repo-check", "--ignore-user-config",
                "--ephemeral", "--sandbox", "read-only", "--disable", "hooks",
                "--disable", "shell_tool", "--disable", "unified_exec", "--cd", str(scratch),
                "--output-last-message", str(scratch / "answer"), *choice]
        if session.thread_id:
            argv.extend(["fork", session.thread_id])
        return [*argv, "-"], scratch, codex.env(), prompt
    if session.provider == "antigravity":
        # agy can resume in place but cannot fork. Give the same member its
        # prior handoff and exchange in an isolated print turn without project MCPs.
        denied = ["command(*)", "mcp(*)", "write_file(*)", "read_file(*)",
                  "read_url(*)", "execute_url(*)"]
        return ([agy.AGY, "--print=" + prompt, "--output-format", "json", "--mode", "plan", "--sandbox",
                 "--settings", json.dumps({"permissions": {"deny": denied}}),
                 "--disable-slash-commands",
                 *choice], scratch, agy.env(), None)
    raise ValueError("provider has no private summary adapter")


async def ask(session, prompt):
    with tempfile.TemporaryDirectory(prefix="thread-summary-") as tmp:
        scratch = Path(tmp)
        argv, cwd, env, stdin = command(session, prompt, scratch)
        # A fork has no delivery address. Its only output consumer is this call.
        launch = asyncio.create_task(asyncio.create_subprocess_exec(
            *first_to_go(argv), cwd=cwd, env=env, start_new_session=True,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL))
        try:
            proc = await asyncio.shield(launch)
            stdout, _ = await proc.communicate(stdin.encode() if stdin else b"")
        finally:
            # Even if foreground input arrived during spawn, reap this exact child.
            proc = await launch
            if proc.returncode is None:
                codex.kill_group(proc.pid)
                await proc.wait()
        if proc.returncode:
            raise ValueError(f"private {session.provider} turn exited {proc.returncode}")
        if session.provider == "codex":
            return (scratch / "answer").read_text().strip()
        if session.provider == "claude-code":
            result = json.loads(stdout)
            if result.get("is_error") or result.get("subtype") != "success":
                raise ValueError("private Claude turn failed")
            return (result.get("result") or "").strip()
        result = json.loads(stdout)
        if result.get("status") != "SUCCESS":
            raise ValueError("private Antigravity turn failed")
        return (result.get("response") or "").strip()
