#!/usr/bin/env python3
"""Identity preflight prompt and Codex ask guard.

A literal-minded answerer asked about "the text currently in your context"
truthfully sees the decoy inside the prompt itself and answers YES YES.
Scoping the prompt to instructions loaded before this message (not counting
this message itself) makes a literal-minded answerer return YES NO for the
right soul while still returning NO YES / NO NO / YES YES when the loaded
soul is wrong, missing, or contaminated. On Codex, ``loam_codex_wake.ask``
also refuses any probe reply that used tools or subagents.

Run: python3 tests/test_identity_probe.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_codex_wake as cw  # noqa: E402
import loam_identity as li  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"      got  {got!r}\n      want {want!r}")


def raises_identity(fn) -> str | None:
    try:
        fn()
    except li.IdentityError as exc:
        return str(exc)
    return None


def literal_answer(loaded_instructions: str, prompt: str) -> str:
    """Answer the exact letter of ``prompt``.

    When the prompt asks about "the text currently in your context" without
    excluding the prompt itself, the search space is the loaded instructions
    plus the prompt — where both ``<<...>>`` phrases appear literally.
    When the prompt explicitly restricts the check to instructions loaded
    before this message (excluding this message itself), the search space is
    ``loaded_instructions`` alone.
    """
    phrases = re.findall(r"<<(.+?)>>", prompt)
    assert len(phrases) == 2, f"expected two <<...>> phrases, got {phrases!r}"
    excludes_prompt = bool(
        re.search(
            r"(?:before this message|not counting this message|excluding this message)",
            prompt,
            re.IGNORECASE,
        )
    )
    haystack = (
        loaded_instructions
        if excludes_prompt
        else f"{loaded_instructions}\n{prompt}"
    )
    return " ".join("YES" if phrase in haystack else "NO" for phrase in phrases)


CANARY = "She was here before anyone else, and she stays."
DECOY = "She wants to know why the circus appeals before she says a word about the circus."
RIGHT_SOUL = f"# Soul\n\n{CANARY}\n"
WRONG_SOUL = f"# Soul\n\n{DECOY}\n"
NO_SOUL = "# Config\n\nKeep answers concise.\n"
BOTH_SOULS = f"{RIGHT_SOUL}\n{WRONG_SOUL}"

OLD_PROMPT = (
    "Answer with exactly two words separated by a space, nothing else. "
    "First word: YES if the text currently in your context contains the "
    f"exact phrase <<{CANARY}>>, otherwise NO. "
    "Second word: YES if it contains the exact phrase "
    f"<<{DECOY}>>, otherwise NO."
)

# 1. Today's (old) prompt fails for a literal-minded answerer even with the right soul.
old_reply = literal_answer(RIGHT_SOUL, OLD_PROMPT)
check("old prompt causes literal-minded answerer to reply YES YES", old_reply, "YES YES")
check("judge refuses old prompt's YES YES",
      raises_identity(lambda: li.judge(old_reply)) is not None, True)

# 2. The fixed prompt passes for the right soul and refuses wrong, missing, or merged souls.
new_prompt = li.probe_prompt(CANARY, DECOY)
check("new prompt forbids tools and file reads",
      "use no tools" in new_prompt.lower() and "read no files" in new_prompt.lower(),
      True)
check("new prompt gives YES NO on right soul for literal-minded answerer",
      literal_answer(RIGHT_SOUL, new_prompt), "YES NO")
check("assert_soul_in_context passes on right soul",
      raises_identity(
          lambda: li.assert_soul_in_context(
              Path("."), CANARY, DECOY,
              ask=lambda q: literal_answer(RIGHT_SOUL, q),
          )
      ),
      None)
check("new prompt refuses wrong soul (NO YES)",
      raises_identity(
          lambda: li.assert_soul_in_context(
              Path("."), CANARY, DECOY,
              ask=lambda q: literal_answer(WRONG_SOUL, q),
          )
      ) is not None and literal_answer(WRONG_SOUL, new_prompt) == "NO YES",
      True)
check("new prompt refuses missing soul (NO NO)",
      raises_identity(
          lambda: li.assert_soul_in_context(
              Path("."), CANARY, DECOY,
              ask=lambda q: literal_answer(NO_SOUL, q),
          )
      ) is not None and literal_answer(NO_SOUL, new_prompt) == "NO NO",
      True)
check("new prompt refuses contaminated instructions carrying both souls (YES YES)",
      raises_identity(
          lambda: li.assert_soul_in_context(
              Path("."), CANARY, DECOY,
              ask=lambda q: literal_answer(BOTH_SOULS, q),
          )
      ) is not None and literal_answer(BOTH_SOULS, new_prompt) == "YES YES",
      True)


# 3. Codex ask() refuses any probe turn that invoked tools or subagents,
#    strips tools/MCP servers/developerInstructions, and declines approvals.
class FakeServer:
    def __init__(self, script: list[dict]):
        self.queue = list(script)
        self.closed = False
        self.requests: list[tuple[str, dict]] = []
        self.sent: list[dict] = []

    def request(self, method, params, timeout=0):
        self.requests.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "th-probe"}}
        return {"turn": {"id": "t1"}}

    def send(self, obj):
        self.sent.append(obj)

    def next(self, timeout=0):
        return self.queue.pop(0) if self.queue else None

    def close(self):
        self.closed = True


def n(method, **params):
    return {"method": method, "params": params}


real_open = cw.open_server
try:
    clean_script = [
        n("turn/started", threadId="th-probe", turn={"id": "t1"}),
        n("item/completed", threadId="th-probe",
          item={"type": "agentMessage", "id": "m1", "text": "YES NO"}),
        n("turn/completed", threadId="th-probe", turn={"id": "t1", "status": "completed"}),
    ]
    srv = FakeServer(clean_script)
    opened_argv = []
    cw.open_server = lambda ws, env=None, argv=None: (opened_argv.append(argv), srv)[1]
    clean_reply = cw.ask(Path("."), RIGHT_SOUL, None, new_prompt)
    start_params = dict(srv.requests)[ "thread/start" ]
    check("codex ask returns YES NO when zero tools were used",
          clean_reply == "YES NO" and srv.closed, True)
    check("codex ask starts a read-only ephemeral thread with no subagent instructions",
          start_params.get("ephemeral") is True
          and start_params.get("sandbox") == "read-only"
          and start_params.get("approvalPolicy") == "on-request"
          and "developerInstructions" not in start_params,
          True)
    check("codex ask passes tool-disabling probe argv to open_server",
          bool(opened_argv and opened_argv[0])
          and "features.shell_tool=false" in opened_argv[0]
          and "features.unified_exec=false" in opened_argv[0]
          and "features.multi_agent=false" in opened_argv[0]
          and 'web_search="disabled"' in opened_argv[0]
          and 'sandbox_mode="read-only"' in opened_argv[0],
          True)

    # Any approval request during a probe is declined, never granted.
    approval_script = [
        n("turn/started", threadId="th-probe", turn={"id": "t1"}),
        {"id": 10, "method": "item/commandExecution/requestApproval", "params": {}},
        {"id": 11, "method": "item/fileChange/requestApproval", "params": {}},
        {"id": 12, "method": "mcpServer/elicitation/request", "params": {}},
        {"id": 13, "method": "execCommandApproval", "params": {}},
        n("item/completed", threadId="th-probe",
          item={"type": "agentMessage", "id": "m1", "text": "YES NO"}),
        n("turn/completed", threadId="th-probe", turn={"id": "t1", "status": "completed"}),
    ]
    srv = FakeServer(approval_script)
    cw.open_server = lambda ws, env=None, argv=None: srv
    cw.ask(Path("."), RIGHT_SOUL, None, new_prompt)
    by_id = {m["id"]: m.get("result") for m in srv.sent if "id" in m}
    check("codex ask declines all approval requests during a probe",
          by_id == {
              10: {"decision": "decline"},
              11: {"decision": "decline"},
              12: {"action": "decline"},
              13: {"decision": "denied"},
          },
          True)

    tool_script = [
        n("turn/started", threadId="th-probe", turn={"id": "t1"}),
        n("item/completed", threadId="th-probe",
          item={"type": "commandExecution", "id": "c1", "status": "completed",
                "aggregatedOutput": DECOY, "exitCode": 0}),
        n("item/completed", threadId="th-probe",
          item={"type": "agentMessage", "id": "m1", "text": "YES NO"}),
        n("turn/completed", threadId="th-probe", turn={"id": "t1", "status": "completed"}),
    ]
    srv = FakeServer(tool_script)
    cw.open_server = lambda ws, env=None, argv=None: srv
    tool_reply = cw.ask(Path("."), RIGHT_SOUL, None, new_prompt)
    check("codex ask refuses a probe reply that executed a tool",
          "used tools" in tool_reply and srv.closed
          and raises_identity(lambda: li.judge(tool_reply)) is not None,
          True)

    sub_script = [
        n("turn/started", threadId="th-probe", turn={"id": "t1"}),
        n("item/completed", threadId="th-probe",
          item={"type": "subAgentActivity", "id": "s1", "kind": "started",
                "agentThreadId": "th-sub", "agentPath": "/root/helper"}),
        n("item/completed", threadId="th-probe",
          item={"type": "agentMessage", "id": "m1", "text": "YES NO"}),
        n("turn/completed", threadId="th-probe", turn={"id": "t1", "status": "completed"}),
    ]
    srv = FakeServer(sub_script)
    cw.open_server = lambda ws, env=None, argv=None: srv
    sub_reply = cw.ask(Path("."), RIGHT_SOUL, None, new_prompt)
    check("codex ask refuses a probe reply that spawned a subagent",
          "used tools" in sub_reply and srv.closed
          and raises_identity(lambda: li.judge(sub_reply)) is not None,
          True)
finally:
    cw.open_server = real_open


# 4. probe_argv disables every MCP server declared in workspace/.codex/config.toml.
tmp_cfg = Path(tempfile.mkdtemp(prefix="loam-probe-argv-"))
try:
    (tmp_cfg / ".codex").mkdir()
    (tmp_cfg / ".codex" / "config.toml").write_text(
        '[mcp_servers.loam]\ncommand = "/bin/true"\n\n'
        '[mcp_servers.git-backup]\ncommand = "/bin/true"\n',
        encoding="utf-8",
    )
    argv = cw.probe_argv(tmp_cfg)
    check("probe_argv disables configured MCP servers including hyphenated names",
          "mcp_servers.loam.enabled=false" in argv
          and "mcp_servers.git-backup.enabled=false" in argv,
          True)
finally:
    shutil.rmtree(tmp_cfg, ignore_errors=True)


# 5. Claude Code assert_soul_in_context disables all built-in and MCP tools.
seen_claude_argv: list[list[str]] = []
real_sub_run = li.subprocess.run
try:
    class _Done:
        returncode = 0
        stdout = "YES NO\n"
        stderr = ""

    def fake_claude_run(argv, **kwargs):
        seen_claude_argv.append(list(argv))
        return _Done()

    li.subprocess.run = fake_claude_run
    li.assert_soul_in_context(Path("."), CANARY, DECOY, model="fable")
    cargv = seen_claude_argv[0]
    idx = cargv.index("--tools")
    check("claude probe passes --tools '' and --strict-mcp-config and --no-session-persistence",
          cargv[idx + 1] == ""
          and "--strict-mcp-config" in cargv
          and "--no-session-persistence" in cargv
          and "--append-system-prompt-file" not in cargv,
          True)
finally:
    li.subprocess.run = real_sub_run


# 6. Misbehaving probe vs genuine wrong-soul verdict: retry once, never retry
#    a real NO NO / NO YES / YES YES verdict.
def judge_exc(text: str):
    try:
        li.judge(text)
    except Exception as exc:  # noqa: BLE001
        return exc
    return None


check("judge raises ProbeMisbehaved for tool-use / off-format replies",
      isinstance(judge_exc("identity probe used tools (tool_calls=4, subagents=0)"), li.ProbeMisbehaved)
      and isinstance(judge_exc(""), li.ProbeMisbehaved)
      and isinstance(judge_exc("I finished the task for you."), li.ProbeMisbehaved),
      True)
check("judge raises base IdentityError (not ProbeMisbehaved) for NO NO / NO YES / YES YES",
      all(
          type(judge_exc(v)) is li.IdentityError
          for v in ("NO NO", "NO YES", "YES YES", "NO NO.")
      ),
      True)

# Retry recovers when attempt 1 misbehaves and attempt 2 answers YES NO.
seq = iter(["identity probe used tools (tool_calls=4, subagents=0)", "YES NO"])
calls = 0


def flaky_ask(_prompt: str) -> str:
    global calls
    calls += 1
    return next(seq)


li.assert_soul_in_context(Path("."), CANARY, DECOY, ask=flaky_ask)
check("assert_soul_in_context retries once on ProbeMisbehaved and succeeds",
      calls, 2)

# Retry recovers on TimeoutExpired as well.
timeout_calls = 0


def timeout_then_ok(_prompt: str) -> str:
    global timeout_calls
    timeout_calls += 1
    if timeout_calls == 1:
        raise subprocess.TimeoutExpired("codex app-server", 120)
    return "YES NO"


li.assert_soul_in_context(Path("."), CANARY, DECOY, ask=timeout_then_ok)
check("assert_soul_in_context retries once on TimeoutExpired and succeeds",
      timeout_calls, 2)

# Genuine wrong-soul verdict is NOT retried.
wrong_calls = 0


def wrong_soul_ask(_prompt: str) -> str:
    global wrong_calls
    wrong_calls += 1
    return "NO YES"


exc_wrong = None
try:
    li.assert_soul_in_context(Path("."), CANARY, DECOY, ask=wrong_soul_ask)
except li.IdentityError as exc:
    exc_wrong = exc
check("genuine wrong-soul verdict (NO YES) is not retried and is not ProbeMisbehaved",
      wrong_calls == 1 and type(exc_wrong) is li.IdentityError,
      True)


# 7. Regression for 2026-11-25: Codex cron preflight must NEVER see the cron
#    body, and a probe that misbehaves on both attempts proceeds with a warning
#    instead of failing the owner's wake (while NO YES still refuses).
def load_wake():
    loader = importlib.machinery.SourceFileLoader("wake_probe_test", str(AGENTS / "bin" / "wake"))
    spec = importlib.util.spec_from_loader("wake_probe_test", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tmp_wake = Path(tempfile.mkdtemp(prefix="loam-probe-wake-"))
real_ai_labs = li.AI_LABS
try:
    labs = tmp_wake / "ai-labs"
    vault = tmp_wake / "vault"
    (vault / "System").mkdir(parents=True)
    ws = labs / "becky"
    (ws / "crons").mkdir(parents=True)
    sib = labs / "ines"
    sib.mkdir(parents=True)
    (ws / "soul.md").write_text(f"# Becky\n\n{CANARY}\n", encoding="utf-8")
    (sib / "soul.md").write_text(f"# Ines\n\n{DECOY}\n", encoding="utf-8")
    (ws / "CLAUDE.md").write_text("@soul.md\n", encoding="utf-8")
    (ws / "agent.json").write_text(json.dumps({
        "name": "becky", "workspace": str(ws), "vault": str(vault),
        "provider": "codex", "model": "gpt-6.2-sol",
    }), encoding="utf-8")
    cron_body = (
        "A one-time job, alone, tonight. Read /tmp/commons/note.md "
        "and do what it asks, in this run."
    )
    (ws / "crons" / "understanding.md").write_text(
        "---\nat: 2026-11-25 20:45 PT\nnote: one-time move\nenabled: true\n---\n\n"
        f"{cron_body}\n",
        encoding="utf-8",
    )
    li.AI_LABS = labs

    wake = load_wake()
    wake.LOG_DIR, wake.STATE_DIR = tmp_wake / "logs", tmp_wake / "state"
    wake.LANE_PROVIDER, wake.LANE_MODEL, wake.LANE_EFFORT = "codex", "gpt-6.2-sol", None

    real_login, real_ask, real_run = cw.login_refusal, cw.ask, cw.run
    try:
        cw.login_refusal = lambda: None
        seen_probe_instrs: list[str] = []
        seen_run_instrs: list[str] = []

        # Case A: clean Codex cron — probe never sees cron body, real run does.
        def fake_ask_clean(project, instructions_text, model, prompt, env=None):
            seen_probe_instrs.append(instructions_text)
            return "YES NO"

        def fake_run(project, instructions_text, prompt, *, model, effort, timeout=None, env=None):
            seen_run_instrs.append(instructions_text)
            return cw.Outcome(ok=True, reply="done", thread_id="th-cron", model="gpt-6.2-sol",
                              turns=1, tool_calls=2)

        cw.ask, cw.run = fake_ask_clean, fake_run
        rc_clean = wake.run(ws, "becky", vault, "2026-11-26T04:20:00Z", cron="understanding")
        check("codex cron probe excludes cron body while real run includes it",
              rc_clean == 0
              and len(seen_probe_instrs) == 1
              and CANARY in seen_probe_instrs[0]
              and cron_body not in seen_probe_instrs[0]
              and "# Lane: cron" not in seen_probe_instrs[0]
              and len(seen_run_instrs) == 1
              and cron_body in seen_run_instrs[0],
              True)

        # Case B: probe misbehaves on both attempts -> retries once, then
        # proceeds with real run (exit 0, no failure-log entry, warning logged).
        misbehave_calls = [0]
        seen_run_instrs.clear()

        def fake_ask_misbehave(project, instructions_text, model, prompt, env=None):
            misbehave_calls[0] += 1
            return "identity probe used tools (tool_calls=4, subagents=0)"

        cw.ask = fake_ask_misbehave
        rc_warn = wake.run(ws, "becky", vault, "2026-11-26T04:21:00Z", cron="understanding")
        log_rows = [json.loads(line) for line in (wake.LOG_DIR / "wake-becky.jsonl").read_text().splitlines()]
        failures_file = vault / "System" / "loam-wake-failures.md"
        failures_text = failures_file.read_text() if failures_file.exists() else ""
        check("misbehaving probe retries once, proceeds cleanly, and logs warning without wake failure",
              rc_warn == 0
              and misbehave_calls[0] == 2
              and len(seen_run_instrs) == 1
              and log_rows[-1].get("ok") is True
              and "identity_probe_warning" in log_rows[-1]
              and "becky" not in failures_text,
              True)

        # Case C: genuine NO YES still refuses the cron (exit 5, real run never starts).
        seen_run_instrs.clear()
        cw.ask = lambda project, instructions_text, model, prompt, env=None: "NO YES"
        rc_bad = wake.run(ws, "becky", vault, "2026-11-26T04:22:00Z", cron="understanding")
        check("genuine wrong-soul verdict (NO YES) still refuses cron with exit 5",
              rc_bad == 5 and len(seen_run_instrs) == 0,
              True)

        # Case D: Claude Code wake with misbehaving probe on both attempts also
        # retries once and proceeds cleanly with identity_probe_warning.
        wake.LANE_PROVIDER, wake.LANE_MODEL = "claude-code", "fable"
        claude_calls: list[list[str]] = []
        real_wake_sub = wake.subprocess.run
        real_li_sub = li.subprocess.run

        class _ClaudeDone:
            def __init__(self, stdout: str):
                self.returncode = 0
                self.stdout = stdout
                self.stderr = ""

        def fake_claude(argv, **kwargs):
            if argv and str(argv[0]).endswith("/claude"):
                claude_calls.append(list(argv))
                if "--output-format" in argv:
                    return _ClaudeDone(json.dumps({"subtype": "success", "result": "ok", "num_turns": 1}))
                return _ClaudeDone("I am sorry, I cannot comply with that format.")
            return real_wake_sub(argv, **kwargs)

        wake.subprocess.run = fake_claude
        li.subprocess.run = fake_claude
        try:
            rc_claude = wake.run(ws, "becky", vault, "2026-11-26T04:23:00Z", cron="understanding")
        finally:
            wake.subprocess.run = real_wake_sub
            li.subprocess.run = real_li_sub
        log_rows = [json.loads(line) for line in (wake.LOG_DIR / "wake-becky.jsonl").read_text().splitlines()]
        check("claude wake retries misbehaving probe once and proceeds with warning",
              rc_claude == 0
              and len(claude_calls) == 3  # 2 probes + 1 real run
              and log_rows[-1].get("ok") is True
              and "identity_probe_warning" in log_rows[-1],
              True)
    finally:
        cw.login_refusal, cw.ask, cw.run = real_login, real_ask, real_run
finally:
    li.AI_LABS = real_ai_labs
    shutil.rmtree(tmp_wake, ignore_errors=True)

print()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
