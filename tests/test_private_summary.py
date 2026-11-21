"""Exercise the private provider pipes, including cancellation of an actual child."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import loam_private_summary as summaries


async def check(root):
    program = root / "provider"
    program.write_text("""#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
if "block" in args:
    Path(os.environ["SUMMARY_FIXTURE_PID"]).write_text(str(os.getpid()))
    time.sleep(60)
elif "--fork-session" in args:
    assert args[args.index("--resume")+1] == "claude-parent"
    assert sys.stdin.read() == "private prompt"
    print(json.dumps({"subtype":"success", "result":"Claude handoff"}))
elif "fork" in args:
    assert args[args.index("fork")+1] == "codex-parent"
    assert sys.stdin.read() == "private prompt"
    Path(args[args.index("--output-last-message")+1]).write_text("Codex handoff")
else:
    assert "--print=private prompt" in args and "--conversation" not in args
    print(json.dumps({"status":"SUCCESS","response":"Antigravity handoff"}))
""")
    program.chmod(0o700)
    s = SimpleNamespace(id="session", cli_session_id="claude-parent", thread_id="codex-parent",
                        model=None, workspace=root)
    with (patch.object(summaries.cfg, "CLAUDE", str(program)),
          patch.object(summaries.codex, "CODEX", str(program)),
          patch.object(summaries.codex, "env", return_value=dict(os.environ)),
          patch.object(summaries.agy, "AGY", str(program))):
        for provider, name in (("claude-code", "Claude"), ("codex", "Codex"),
                               ("antigravity", "Antigravity")):
            s.provider = provider
            assert await summaries.ask(s, "private prompt") == name + " handoff"
        s.provider, s.model = "claude-code", "block"
        pidfile = root / "pid"
        with patch.dict(os.environ, SUMMARY_FIXTURE_PID=str(pidfile)):
            task = asyncio.create_task(summaries.ask(s, "private prompt"))
            while not pidfile.exists() or not pidfile.read_text():
                await asyncio.sleep(.01)
            pid = int(pidfile.read_text())
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert task.cancelled() and not Path(f"/proc/{pid}").exists()
    print("PASS all three private provider pipes and cancellation reaps the provider PID")


with tempfile.TemporaryDirectory() as folder:
    asyncio.run(asyncio.wait_for(check(Path(folder)), 10))
