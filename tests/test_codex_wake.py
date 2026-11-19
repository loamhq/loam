#!/usr/bin/env python3
"""A persona's wake and cron on Codex (2026-11-13): lib/loam_codex_wake.py
and bin/wake's Codex half.

Offline, always:

  instructions   a Codex wake loads what a Claude wake loads: CLAUDE.md and
                 its imports in order, then the lane's file; no _active.md.
  answers        every approval granted, a question answered with nothing,
                 anything else refused.
  drive          a replayed run: the root's reply is the root's words, never
                 a subagent's; a subagent ending after the root's turn is
                 handed back in a second root turn; usage summed over every
                 thread; a failed turn is a failure with Codex's sentence.
  wake           bin/wake end to end with the app-server stubbed: a persona
                 on Codex takes the Codex lane, the line says provider,
                 model, thread and usage, the marker advances, and a
                 persona on Claude Code never touches it.

``--live``: real bin/wake, real Codex, on a throwaway persona built in a
temp directory (its own soul, _always.md, _reflection.md and cron, its
.codex/config.toml rendered by bin/provision's own codex_config), with the
log, the marker, the locks and the schedule log pointed at the temp
directory. Not a real persona's memory. Runs one reflection wake and one
cron, each of which writes a file only its lane's instructions name, uses a
subagent and calls Loam's MCP tools; then reads what the run left behind.
Costs two short turns on the ChatGPT login.

Run: python3 tests/test_codex_wake.py [--live]
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
# A lock table and a schedule log of this run's own, before anything reads
# them at import (tests/run-all does the same for the suite): bin/wake takes
# a lock and records a fire, and neither may touch the box's.
SCRATCH = Path(tempfile.mkdtemp(prefix="loam-codex-wake-env-"))
for var, sub in (("LOAM_LOCK_DIR", "locks"), ("LOAM_SCHEDULE_STATE", "schedules")):
    if not os.environ.get(var):
        (SCRATCH / sub).mkdir()
        os.environ[var] = str(SCRATCH / sub)
os.environ.pop("LOAM_LIVE_LOCKS", None)

import loam_codex as codex  # noqa: E402
import loam_codex_wake as cw  # noqa: E402
import loam_identity  # noqa: E402
import loam_profile  # noqa: E402

results: list[bool] = []
LIVE = "--live" in sys.argv   # read now: the wake tests set sys.argv


def check(label: str, ok: bool, detail="") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok and detail != "":
        print(f"      {str(detail)[:800]}")


def persona(root: Path, name: str, provider: str | None = "codex",
            model: str | None = None) -> Path:
    """A throwaway persona: the files a wake reads, nothing of anybody's."""
    ws = root / "ai-labs" / name
    (ws / "crons").mkdir(parents=True)
    vault = root / "vault"
    (vault / "00_INBOX" / "from-agents").mkdir(parents=True, exist_ok=True)
    card = {"name": name, "workspace": str(ws), "vault": str(vault)}
    if provider:
        card["provider"] = provider
    if model:
        card["model"] = model
    (ws / "agent.json").write_text(json.dumps(card))
    (ws / "soul.md").write_text(
        "# Soul\n\nI am a probe persona built for one test and then thrown "
        "away. The kestrel over the dry field is the image I keep.\n")
    (ws / "config.md").write_text("# Config\n\nKeep every answer short.\n")
    (ws / "_always.md").write_text("# Always\n\nThe always-layer token is ALWAYS-OPAL.\n")
    (ws / "_active.md").write_text("---\nname: c\ndescription: c\n---\n"
                                   "The conversation-layer token is ACTIVE-JADE.\n")
    (ws / "CLAUDE.md").write_text(f"# {name}\n\n@soul.md\n\n@config.md\n\n@_always.md\n")
    return ws


# --- instructions -----------------------------------------------------------

def test_instructions(root: Path) -> None:
    ws = persona(root, "cwinstr")
    (ws / "_reflection.md").write_text("---\nx: y\n---\nThe reflection token is REFL-RUBY.\n")
    import loam_memory  # noqa: PLC0415
    _, text, path = loam_memory.resolve_lane(ws, loam_memory.REFLECTION_LAYER)
    got = cw.instructions(ws, "reflection wake", path, text)
    names = [p.name for p in cw.instruction_sources(ws)]
    check("instructions: Claude Code's wake sources, in order",
          names == ["CLAUDE.md", "soul.md", "config.md", "_always.md"], names)
    order = [got.find(t) for t in ("@soul.md", "kestrel", "Keep every answer",
                                   "ALWAYS-OPAL", "REFL-RUBY")]
    check("instructions: each source verbatim, the lane's file last",
          all(i >= 0 for i in order) and order == sorted(order), order)
    check("instructions: no conversation layer, no lane frontmatter",
          "ACTIVE-JADE" not in got and "x: y" not in got)
    check("instructions: the wake's provider line opens them, saying unattended",
          got.startswith(cw.PROVIDER_LINE) and "unattended" in cw.PROVIDER_LINE
          and "spawn_agent" in cw.PROVIDER_LINE)
    check("instructions: Claude Code's Agent is no longer listed as missing on Codex",
          "Agent" not in codex.CLAUDE_ONLY_TOOLS and "Agent," not in codex.PROVIDER_LINE)


# --- answers ----------------------------------------------------------------

def test_answers() -> None:
    got = {m: cw.unattended_answer(m, {"permissions": {"network": True}})
           for m in ("item/commandExecution/requestApproval",
                     "item/fileChange/requestApproval", "execCommandApproval",
                     "item/permissions/requestApproval",
                     "item/tool/requestUserInput", "item/tool/call")}
    check("answers: approvals granted, a question answered with nothing, the rest refused",
          got == {"item/commandExecution/requestApproval": {"decision": "accept"},
                  "item/fileChange/requestApproval": {"decision": "accept"},
                  "execCommandApproval": {"decision": "approved"},
                  "item/permissions/requestApproval":
                      {"permissions": {"network": True}, "scope": "turn"},
                  "item/tool/requestUserInput": {"answers": {}},
                  "item/tool/call": None}, got)
    approve = cw.unattended_answer("mcpServer/elicitation/request",
                                   {"message": "Allow engineer.status?", "mode": "form",
                                    "requestedSchema": {"type": "object", "properties": {}}})
    check("answers: an MCP call asking leave is accepted",
          approve == {"action": "accept", "content": {}}, approve)


# --- drive ------------------------------------------------------------------

class FakeServer:
    """Answers requests at once and plays one script per turn/start."""

    def __init__(self, scripts: list[list[dict]]):
        self.scripts = scripts
        self.sent: list[dict] = []
        self.queue: list = []

    def request(self, method, params, timeout=0):
        self.sent.append({"method": method, "params": params})
        if method == "turn/start":
            self.queue += self.scripts.pop(0)
        return {"turn": {"id": f"t{len(self.sent)}"}}

    def send(self, obj):
        self.sent.append(obj)

    def next(self, timeout=0):
        return self.queue.pop(0) if self.queue else None


def n(method, **params):
    return {"method": method, "params": params}


def usage(i, o):
    return {"total": {"inputTokens": i, "cachedInputTokens": i // 2,
                      "outputTokens": o, "reasoningOutputTokens": 1}}


def test_drive() -> None:
    root, sub = "R", "S"
    first = [
        n("turn/started", threadId=root, turn={"id": "u1"}),
        n("item/completed", threadId=root, item={
            "type": "subAgentActivity", "id": "call_1", "kind": "started",
            "agentThreadId": sub, "agentPath": "/root/notes"}),
        n("turn/started", threadId=sub, turn={"id": "s1"}),
        n("item/completed", threadId=root, item={
            "type": "agentMessage", "id": "m1", "text": "Sent a helper; resting."}),
        n("thread/tokenUsage/updated", threadId=root, tokenUsage=usage(1000, 10)),
        n("turn/completed", threadId=root, turn={"id": "u1", "status": "completed"}),
        n("item/completed", threadId=sub, item={
            "type": "commandExecution", "id": "c1", "status": "completed",
            "aggregatedOutput": "ok", "exitCode": 0}),
        n("item/completed", threadId=sub, item={
            "type": "agentMessage", "id": "m2", "text": "SUBAGENT WORDS"}),
        n("thread/tokenUsage/updated", threadId=sub, tokenUsage=usage(500, 5)),
        n("thread/status/changed", threadId=sub, status={"type": "idle"}),
        n("turn/completed", threadId=sub, turn={"id": "s1", "status": "completed"}),
    ]
    second = [
        n("turn/started", threadId=root, turn={"id": "u2"}),
        n("item/completed", threadId=root, item={
            "type": "agentMessage", "id": "m3", "text": "The helper is done."}),
        n("thread/tokenUsage/updated", threadId=root, tokenUsage=usage(1500, 20)),
        n("turn/completed", threadId=root, turn={"id": "u2", "status": "completed"}),
    ]
    server = FakeServer([first, second])
    out = cw.drive(server, root, "wake up", model="m", effort="low")
    starts = [s for s in server.sent if s.get("method") == "turn/start"]
    check("drive: a subagent ending after the root's turn is handed back in one more root turn",
          len(starts) == 2 and "<task-notification>" in starts[1]["params"]["input"][0]["text"]
          and "SUBAGENT WORDS" in starts[1]["params"]["input"][0]["text"]
          and "/root/notes" in starts[1]["params"]["input"][0]["text"],
          [s["params"]["input"][0]["text"][:200] for s in starts])
    check("drive: the reply is the root's last words, never the subagent's",
          out.ok and out.reply == "The helper is done.", out.reply)
    check("drive: turns, tool calls, subagents counted; model and effort on every turn",
          (out.turns, out.tool_calls, out.subagents) == (2, 1, 1)
          and all(s["params"].get("model") == "m" and s["params"].get("effort") == "low"
                  for s in starts), (out.turns, out.tool_calls, out.subagents))
    check("drive: usage is every thread's last total, summed",
          out.usage.get("inputTokens") == 2000 and out.usage.get("outputTokens") == 25
          and cw.claude_usage(out.usage) == {"input_tokens": 1000,
                                             "cache_read_input_tokens": 1000,
                                             "output_tokens": 25,
                                             "reasoning_output_tokens": 2},
          out.usage)

    # A subagent that ends inside the root's turn is Codex's to hand over.
    inside = [
        n("turn/started", threadId=root, turn={"id": "u1"}),
        n("item/completed", threadId=root, item={
            "type": "subAgentActivity", "id": "call_1", "kind": "started",
            "agentThreadId": sub, "agentPath": "/root/x"}),
        n("turn/started", threadId=sub, turn={"id": "s1"}),
        n("turn/completed", threadId=sub, turn={"id": "s1", "status": "completed"}),
        n("item/completed", threadId=root, item={"type": "agentMessage", "id": "m", "text": "done"}),
        n("turn/completed", threadId=root, turn={"id": "u1", "status": "completed"}),
    ]
    server = FakeServer([inside])
    out = cw.drive(server, root, "go")
    check("drive: a subagent ending inside the root's turn adds no turn",
          out.ok and out.turns == 1 and out.reply == "done")

    failed = [
        n("turn/started", threadId=root, turn={"id": "u1"}),
        n("error", threadId=root, willRetry=False, error={
            "message": "You've hit your usage limit.", "codexErrorInfo": "usageLimitExceeded"}),
        n("turn/completed", threadId=root, turn={"id": "u1", "status": "failed", "error": {
            "message": "You've hit your usage limit.", "codexErrorInfo": "usageLimitExceeded"}}),
    ]
    out = cw.drive(FakeServer([failed]), root, "go")
    check("drive: a failed turn is a failure, read as the account's",
          not out.ok and "usage limit" in (out.error or "")
          and cw.account_refusal(out) is not None, (out.ok, out.error, out.error_kind))

    # A request mid-run is answered and the run goes on.
    asking = [
        n("turn/started", threadId=root, turn={"id": "u1"}),
        {"id": 77, "method": "item/tool/requestUserInput", "params": {"questions": []}},
        n("item/completed", threadId=root, item={"type": "agentMessage", "id": "m", "text": "went on"}),
        n("turn/completed", threadId=root, turn={"id": "u1", "status": "completed"}),
    ]
    server = FakeServer([asking])
    out = cw.drive(server, root, "go")
    check("drive: a question for a person is answered with nothing, and the run goes on",
          out.ok and {"id": 77, "result": {"answers": {}}} in server.sent, server.sent)


# --- bin/wake ---------------------------------------------------------------

def load_wake():
    loader = importlib.machinery.SourceFileLoader(
        "wake_codex_test", str(AGENTS / "bin" / "wake"))
    spec = importlib.util.spec_from_loader("wake_codex_test", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def wake_env(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)


def test_wake_stubbed(root: Path) -> None:
    wake_env(root)
    ws = persona(root, "cwstub", model="gpt-x")
    (ws / "_reflection.md").write_text("Reflect.\n")
    wake = load_wake()
    wake.LOG_DIR, wake.STATE_DIR = root / "logs", root / "state"
    asked, ran = [], []
    real = (cw.login_refusal, cw.ask, cw.run, loam_identity.assert_soul_in_context)
    cw.login_refusal = lambda: None

    def ask(project, text, model, prompt, env=None):
        asked.append((text, model))
        return "YES NO"
    cw.ask = ask

    def run(project, text, prompt, *, model, effort, timeout=None, env=None):
        ran.append({"text": text, "prompt": prompt, "model": model,
                    "effort": effort, "wake": os.environ.get("LOAM_WAKE")})
        return cw.Outcome(ok=True, reply="rested", thread_id="th-1", model="gpt-x",
                          turns=2, tool_calls=3, subagents=1,
                          usage={"inputTokens": 100, "cachedInputTokens": 40,
                                 "outputTokens": 7})
    cw.run = run
    try:
        sys.argv = ["wake", str(ws)]
        code = wake.main()
    finally:
        cw.login_refusal, cw.ask, cw.run, loam_identity.assert_soul_in_context = real
    line = json.loads((root / "logs" / "wake-cwstub.jsonl").read_text().splitlines()[-1])
    check("wake: a Codex persona's reflection runs on Codex and exits 0",
          code == 0 and len(ran) == 1 and len(asked) == 1, (code, ran, asked))
    check("wake: the identity gate asks a thread composed as the run's",
          asked and asked[0][0] == ran[0]["text"] and "kestrel" in asked[0][0]
          and "Reflect." in asked[0][0])
    check("wake: the persona's model, the lane's prompt and LOAM_WAKE",
          ran and ran[0]["model"] == "gpt-x" and "You're waking" in ran[0]["prompt"]
          and ran[0]["wake"] == "reflection", ran)
    check("wake: the line names provider, model, thread, turns and usage",
          line.get("ok") is True and line.get("provider") == "codex"
          and line.get("model") == "gpt-x" and line.get("codex_thread") == "th-1"
          and line.get("api_turns") == 5 and line.get("subagents") == 1
          and line.get("usage") == {"input_tokens": 60, "cache_read_input_tokens": 40,
                                    "output_tokens": 7}
          and "provider_not_honoured" not in line, line)
    marker = json.loads((root / "state" / "cwstub.json").read_text())
    check("wake: reflection's marker advanced", "last_wake" in marker, marker)

    # The same persona on Claude Code never reaches the Codex half.
    ws2 = persona(root, "cwclaude", provider=None)
    (ws2 / "_reflection.md").write_text("Reflect.\n")
    wake = load_wake()
    wake.LOG_DIR, wake.STATE_DIR = root / "logs", root / "state"
    touched = []
    cw.run = lambda *a, **k: touched.append(1)
    real_preflight = loam_identity.preflight_identity
    loam_identity.preflight_identity = lambda project, model=None, ask=None: ("c", "o")

    class Fake:
        returncode, stderr = 0, ""
        stdout = json.dumps({"type": "result", "subtype": "success", "num_turns": 1,
                             "result": "ok"})
    real_sub = wake.subprocess.run
    wake.subprocess.run = lambda argv, **kw: Fake() if str(argv[0]).endswith("/claude") \
        else real_sub(argv, **kw)
    wake.loam_session.round_trips = lambda sid: None
    try:
        sys.argv = ["wake", str(ws2)]
        code = wake.main()
    finally:
        cw.run = real[2]
        wake.subprocess.run = real_sub
        # Put back: the live half runs the real gate.
        loam_identity.preflight_identity = real_preflight
    line = json.loads((root / "logs" / "wake-cwclaude.jsonl").read_text().splitlines()[-1])
    check("wake: a Claude Code persona's wake stays on Claude Code",
          code == 0 and not touched and line.get("provider") == "claude-code", line)

    # Antigravity: not a lane provider yet, and the line says so.
    check("wake: an explicit Antigravity choice pauses the unsupported lane",
          loam_profile.lane_choice({"provider": "antigravity"}, "fable")["provider"] is None
          and loam_profile.lane_choice({"provider": "antigravity"}, "fable")["note"])


# --- live -------------------------------------------------------------------

LIVE_MODEL = "gpt-6-luna"


def render_codex_config(ws: Path) -> None:
    loader = importlib.machinery.SourceFileLoader("provision_cw", str(AGENTS / "bin" / "provision"))
    spec = importlib.util.spec_from_loader("provision_cw", loader)
    prov = importlib.util.module_from_spec(spec)
    loader.exec_module(prov)
    (ws / ".codex").mkdir(exist_ok=True)
    (ws / ".codex" / "config.toml").write_text(prov.codex_config(ws.name, ws))


def live(root: Path) -> None:
    login = cw.login_refusal()
    if login:
        print(f"SKIP  live: {login}")
        return
    wake_env(root)
    name = "codexwakeprobe"
    ws = persona(root, name, model=LIVE_MODEL)
    card = json.loads((ws / "agent.json").read_text())
    card["effort"] = "low"
    (ws / "agent.json").write_text(json.dumps(card))
    render_codex_config(ws)
    vault = root / "vault"
    import subprocess  # noqa: PLC0415
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.name=probe", "-c", "user.email=probe@localhost",
                 "commit", "-q", "-m", "fixture", "--allow-empty"]):
        subprocess.run(["git", "-C", str(vault), *cmd], check=True)
    (ws / "_reflection.md").write_text(
        "# Reflection (probe)\n\nThis is a probe wake of a throwaway persona. Do exactly "
        "these steps and nothing else:\n"
        "1. Use spawn_agent to start one subagent whose whole task is to run the shell "
        "command `echo sub-$((6*7))` and report its output. Wait for it.\n"
        "2. Call the MCP tool `mcp__engineer__status` once (no arguments) and note how many "
        "jobs it lists.\n"
        "3. Write the file `wake-proof.txt` in your workspace with three lines: the token "
        "REFL-RUBY, the always-layer token from your instructions, and what the subagent "
        "reported.\n"
        "4. Reply with one line saying you did it.\n")
    (ws / "crons" / "probe.md").write_text(
        "---\nschedule: daily at 05:00 PT\nenabled: true\napproved: 2026-11-13\n---\n"
        "# Cron probe\n\nWrite the file `cron-proof.txt` in your workspace containing the "
        "token CRON-TOPAZ and the image your soul keeps, then reply with one line.\n")
    wake = load_wake()
    wake.LOG_DIR, wake.STATE_DIR = root / "logs", root / "state"
    for argv, label in (([str(ws)], "reflection"), ([str(ws), "--cron", "probe"], "cron:probe")):
        sys.argv = ["wake", *argv]
        code = wake.main()
        lines = (root / "logs" / f"wake-{name}.jsonl").read_text().splitlines()
        line = json.loads(lines[-1])
        print(f"      {label}: {json.dumps(line)[:900]}")
        check(f"LIVE {label}: exit 0, ok, on Codex, model {LIVE_MODEL}",
              code == 0 and line.get("ok") is True and line.get("provider") == "codex"
              and line.get("model") == LIVE_MODEL and line.get("lane") == label, line)
        check(f"LIVE {label}: the identity gate passed on Codex",
              bool(line.get("identity_canary")), line.get("identity_canary"))
    proof = (ws / "wake-proof.txt").read_text() if (ws / "wake-proof.txt").exists() else ""
    print(f"      wake-proof.txt: {proof!r}")
    check("LIVE reflection: the lane's file and _always.md loaded (their tokens written)",
          "REFL-RUBY" in proof and "ALWAYS-OPAL" in proof, proof)
    check("LIVE reflection: a subagent ran (its output written, a subagent counted)",
          "sub-42" in proof and json.loads((root / "logs" / f"wake-{name}.jsonl")
                                           .read_text().splitlines()[0]).get("subagents", 0) >= 1,
          proof)
    cron = (ws / "cron-proof.txt").read_text() if (ws / "cron-proof.txt").exists() else ""
    print(f"      cron-proof.txt: {cron!r}")
    check("LIVE cron: its own file loaded, and the soul (the kestrel)",
          "CRON-TOPAZ" in cron and "kestrel" in cron.lower(), cron)
    # What the rollout says the model was given and what it called.
    first = json.loads((root / "logs" / f"wake-{name}.jsonl").read_text().splitlines()[0])
    path = cw.rollout(first.get("codex_thread"))
    text = path.read_text() if path else ""
    check("LIVE reflection: Loam's MCP tool was called (engineer status) in the rollout",
          '"status"' in text and "engineer" in text, str(path))
    check("LIVE reflection: Loam's subagent ask reached the thread as developer instructions",
          codex.SUBAGENT_ASK[:80] in text, str(path))
    check("LIVE reflection: the wake's own instructions were the thread's (its provider line)",
          "through Loam's wake launcher" in text and "REFL-RUBY" in text)
    canaries = [json.loads(x).get("identity_canary") for x in
                (root / "logs" / f"wake-{name}.jsonl").read_text().splitlines()]
    soul = (ws / "soul.md").read_text()
    check("LIVE the identity gate ran for real: a canary from this soul, both runs",
          len(canaries) == 2 and all(c and c in soul for c in canaries), canaries)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="loam-codex-wake-"))
    saved = dict(os.environ)
    try:
        test_instructions(root / "a")
        test_answers()
        test_drive()
        test_wake_stubbed(root / "b")
        if LIVE:
            live(root / "live")
        else:
            print("SKIP  live: run with --live")
    finally:
        os.environ.clear()
        os.environ.update(saved)
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(SCRATCH, ignore_errors=True)
        if LIVE:   # the instructions file a Codex start writes for the probe
            codex.instructions_path("codexwakeprobe").unlink(missing_ok=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
