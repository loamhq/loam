#!/usr/bin/env python3
"""Loam's layer, the same on every provider (2026-11-13).

The owner, 11-13 (m247): what Loam builds -- the soul, the config, memory,
the persona, Loam's own tools (the engineer among them), the conversation
record -- is the same on every provider and must be used; above that each
provider uses what it has, and the differences are a feature, not gaps. So
this holds the providers to Loam's layer and to nothing above it. A
provider's own extras (Codex's bundled skills and web search, Claude Code's
workflows and built-in tools, Antigravity's own tools) are not checked and
are not failures.

On a throwaway persona built in a temp directory (its soul, config,
``_always.md``, ``_active.md``, ``_reflection.md`` and one cron; its
``.mcp.json``, ``.claude/settings.json``, ``.codex/config.toml`` and
``.agents/mcp_config.json`` rendered by bin/provision's own functions), never
a real persona's memory:

Offline, always:

  files      a conversation loads the same memory files on every provider, in
             Claude Code's order (CLAUDE.md, its imports, ``_active.md``); a
             wake and a cron load the same files on Claude Code and Codex
             (CLAUDE.md, its imports, the lane's own file; no ``_active.md``).
  tools      the same MCP servers, started the same way (command, arguments,
             environment), on all three; the engineer among them.
  record     a Codex or Antigravity subagent's words are never the persona's.
  lanes      a wake and a cron run on the persona's provider: Claude Code and
             Codex; Antigravity is on Claude Code and the profile says why.

``--live`` (costs one Haiku turn and three short Codex turns):

  Claude Code  a turn in the throwaway workspace: its MCP tool names from the
               CLI's own ``init``, and the soul's token in its answer.
  Codex        the MCP tool names a thread in the same workspace has (the
               app-server's ``mcpServerStatus/list``), equal to Claude's; then a
               real conversation through the broker's own ``CodexSession``:
               the soul's and ``_active.md``'s tokens in the reply, and a
               subagent's sentence never in the persona's message (read with
               the thread engine's own ``Reply.of``).
  Codex lanes  ``tests/test_codex_wake.py --live`` runs a real wake and cron.
  Antigravity  only the offline half, unless it is signed in: no turn has
               ever run, and it needs the owner's Google sign-in.

Transcripts of the live turns are quarantined (``LOAM_PROBE=1``), never filed
in the vault.

Run: python3 tests/test_provider_parity.py [--live]
"""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))

import loam_antigravity as agy  # noqa: E402
import loam_codex as codex  # noqa: E402
import loam_codex_wake as cw  # noqa: E402
import loam_config as lc  # noqa: E402
import loam_memory  # noqa: E402
import loam_profile  # noqa: E402
import loam_thread_engine  # noqa: E402

LIVE = "--live" in sys.argv
NAME = "parityprobe"
CLAUDE = str(lc.CLAUDE_BIN)
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CODEX_MODEL = "gpt-6-luna"
results: list[bool] = []


def check(label: str, ok, detail="") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok and detail != "":
        print(f"      {str(detail)[:900]}")


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


prov = load("provision_parity", AGENTS / "bin" / "provision")


def build(root: Path) -> Path:
    """The throwaway persona, its harness rendered as provision renders it."""
    ws = root / "ai-labs" / NAME
    (ws / "crons").mkdir(parents=True)
    (root / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)
    (ws / "agent.json").write_text(json.dumps(
        {"name": NAME, "workspace": str(ws), "vault": str(root / "vault")}))
    (ws / "soul.md").write_text("# Soul\n\nI am a parity probe, built for one test. "
                                "My token is SOUL-QUARTZ.\n")
    (ws / "config.md").write_text("# Config\n\nThe config token is CONFIG-BERYL.\n")
    (ws / "_always.md").write_text("# Always\n\nThe always token is ALWAYS-OPAL.\n")
    (ws / "_active.md").write_text(
        "---\nname: conversation\ndescription: probe's conversation lane.\n"
        "keep-coding-instructions: true\n---\nThe conversation token is ACTIVE-JADE.\n")
    (ws / "_reflection.md").write_text("The reflection token is REFL-RUBY.\n")
    (ws / "crons" / "probe.md").write_text(
        "---\nschedule: daily at 05:00 PT\nenabled: true\napproved: 2026-11-13\n---\n"
        "The cron token is CRON-TOPAZ.\n")
    (ws / "CLAUDE.md").write_text("# Parityprobe\n\n@soul.md\n\n@config.md\n\n@_always.md\n")
    (ws / ".mcp.json").write_text(json.dumps(prov.mcp_config(NAME, ws), indent=2))
    (ws / ".claude").mkdir()
    (ws / ".claude" / "settings.json").write_text(json.dumps(prov.settings(ws), indent=2))
    styles = ws / ".claude" / "output-styles"
    styles.mkdir()
    (styles / "conversation.md").symlink_to(Path("../..") / "_active.md")
    (styles / "none.md").write_text("---\nname: none\ndescription: none\n---\n")
    (ws / ".codex").mkdir()
    (ws / ".codex" / "config.toml").write_text(prov.codex_config(NAME, ws))
    (ws / ".agents").mkdir()
    (ws / ".agents" / "mcp_config.json").write_text(
        json.dumps(prov.antigravity_mcp(NAME, ws), indent=2))
    return ws


def in_order(text: str, bodies: list[str]) -> bool:
    cursor = 0
    for body in bodies:
        at = text.find(body.strip(), cursor)
        if at < 0:
            return False
        cursor = at
    return True


# --- files ------------------------------------------------------------------

def files_cases(ws: Path) -> None:
    claude_conv = codex.instruction_sources(ws)
    names = [str(p.relative_to(ws)) for p in claude_conv]
    check("files: Claude Code's conversation sources are CLAUDE.md, its imports, _active.md",
          names == ["CLAUDE.md", "soul.md", "config.md", "_always.md", "_active.md"], names)
    settings = json.loads((ws / ".claude" / "settings.json").read_text())
    check("files: ...and _active.md reaches Claude Code as its conversation style",
          settings.get("outputStyle") == "conversation"
          and (ws / ".claude" / "output-styles" / "conversation.md").resolve()
          == (ws / "_active.md").resolve(), settings.get("outputStyle"))
    bodies = [p.read_text() if p.name != "_active.md"
              else loam_memory.strip_frontmatter(p.read_text()) for p in claude_conv]
    for label, text in (("Codex", codex.instructions(ws)),
                        ("Antigravity", agy.instructions(ws))):
        check(f"files: {label}: a conversation is given every one, verbatim, in that order",
              in_order(text, bodies) and "keep-coding-instructions" not in text)

    wake = load("wake_parity", AGENTS / "bin" / "wake")
    for lane, lane_file in (("reflection", loam_memory.REFLECTION_LAYER),
                            ("cron:probe", "crons/probe.md")):
        _, lane_text, lane_path = loam_memory.resolve_lane(ws, lane_file)
        argv = wake.claude_argv(NAME, lane, "p", lane_path, model="m")
        claude_wake = ([p.name for p in cw.instruction_sources(ws)]
                       + [Path(argv[argv.index("--append-system-prompt-file") + 1]).name])
        style = json.loads(argv[argv.index("--settings") + 1]).get("outputStyle")
        text = cw.instructions(ws, lane, lane_path, lane_text)
        wake_bodies = [p.read_text() for p in cw.instruction_sources(ws)] + [lane_text]
        check(f"files: a {lane} on Claude Code loads CLAUDE.md, its imports, its lane file "
              f"and no _active.md",
              claude_wake[:4] == ["CLAUDE.md", "soul.md", "config.md", "_always.md"]
              and style == "none", (claude_wake, style))
        check(f"files: ...and on Codex the same files, verbatim, in that order, "
              f"no _active.md",
              in_order(text, wake_bodies) and "ACTIVE-JADE" not in text)


# --- tools ------------------------------------------------------------------

def tools_cases(ws: Path) -> None:
    import tomllib  # noqa: PLC0415
    claude = json.loads((ws / ".mcp.json").read_text())["mcpServers"]
    cdx = tomllib.loads((ws / ".codex" / "config.toml").read_text())["mcp_servers"]
    ag = json.loads((ws / ".agents" / "mcp_config.json").read_text())["mcpServers"]
    check("tools: the same MCP servers on all three providers, the engineer among them",
          sorted(claude) == sorted(cdx) == sorted(ag) and "engineer" in claude,
          (sorted(claude), sorted(cdx), sorted(ag)))

    def started(spec: dict, url_key: str) -> tuple:
        if spec.get(url_key) or spec.get("url"):
            return ("url", spec.get(url_key) or spec.get("url"))
        return (spec.get("command"), tuple(spec.get("args") or []),
                tuple(sorted((k, v) for k, v in (spec.get("env") or {}).items()
                             if k not in cw.codex.STRIPPED_ENV)))
    same = {s: (started(claude[s], "url"), started(cdx[s], "url"),
                started(ag[s], "serverUrl")) for s in claude}
    differ = {s: v for s, v in same.items()
              if not (v[0] == v[1] and v[0][:2] == v[2][:2])}
    check("tools: each server started the same way: command, arguments, environment",
          not differ, differ)
    check("tools: every Codex server's tools run unasked, as under bypassPermissions",
          all(spec.get("default_tools_approval_mode") == "approve" for spec in cdx.values()))
    check("tools: Codex hands a wake's lane to the engineer server (LOAM_WAKE)",
          "LOAM_WAKE" in (cdx["engineer"].get("env_vars") or []))


# --- record -----------------------------------------------------------------

def record_cases() -> None:
    t = codex.Translator("s")
    events = []
    for method, params in (
            ("item/started", {"item": {"type": "agentMessage", "id": "a", "text": ""}}),
            ("item/completed", {"item": {"type": "agentMessage", "id": "a",
                                         "text": "A SUBAGENT'S SENTENCE"}})):
        events += t.feed(method, params, parent="call_spawn")
    for method, params in (
            ("item/started", {"item": {"type": "agentMessage", "id": "b", "text": ""}}),
            ("item/completed", {"item": {"type": "agentMessage", "id": "b",
                                         "text": "The persona's own words."}})):
        events += t.feed(method, params)
    reply = loam_thread_engine.Reply.of(events)
    check("record: on Codex, a subagent's message is not the persona's; its own is",
          reply.segments == ["The persona's own words."], reply.segments)
    a = agy.Translator("s") if hasattr(agy, "Translator") else None
    check("record: Antigravity's translator carries a parent for nested steps "
          "(its shapes unmeasured: never signed in)",
          a is not None and "parent_tool_use_id=parent" in
          Path(agy.__file__).read_text())


# --- lanes ------------------------------------------------------------------

def lanes_cases() -> None:
    got = {p: (loam_profile.lane_provider({"provider": p}),
               loam_profile.defaults("x", {"provider": p})["not_honoured"])
           for p in ("claude-code", "codex", "antigravity")}
    check("lanes: wakes and crons run on Claude Code and on Codex, nothing not honoured",
          got["claude-code"] == ("claude-code", []) and got["codex"] == ("codex", []), got)
    check("lanes: Antigravity's run on Claude Code, and the profile says why",
          got["antigravity"][0] == "claude-code" and got["antigravity"][1]
          and "sign-in" in got["antigravity"][1][0], got["antigravity"])
    wake = (AGENTS / "bin" / "wake").read_text()
    check("lanes: bin/wake takes the lane's provider for the lock and the log line",
          '"engine": LANE_PROVIDER' in wake and 'setdefault("provider", LANE_PROVIDER)' in wake)


# --- live -------------------------------------------------------------------

def claude_live(ws: Path) -> list[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("LOAM_WAKE", "LOAM_SESSION_ID")}
    env.update({"CLAUDE_PROJECT_DIR": str(ws), "LOAM_PROBE": "1", "DISABLE_AUTOUPDATER": "1"})
    done = subprocess.run(
        [CLAUDE, "-p", "Without using any tool, reply with the four tokens your "
         "instructions give (soul, config, always, conversation), space-separated.",
         "--model", CLAUDE_MODEL, "--output-format", "stream-json", "--verbose",
         "--max-turns", "1"],
        cwd=str(ws), env=env, capture_output=True, text=True, timeout=300)
    tools, answer = [], ""
    for line in done.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            tools = list(event.get("tools") or [])
        if event.get("type") == "result":
            answer = event.get("result") or ""
    print(f"      Claude Code answered: {answer[:200]!r}")
    check("LIVE Claude Code: soul, config, _always.md and _active.md loaded",
          all(t in answer for t in ("SOUL-QUARTZ", "CONFIG-BERYL", "ALWAYS-OPAL",
                                    "ACTIVE-JADE")), answer or done.stderr[-400:])
    return sorted(t for t in tools if t.startswith("mcp__"))


def codex_mcp_live(ws: Path) -> list[str]:
    parity = load("codex_parity_for_parity", AGENTS / "tests" / "test_codex_parity.py")
    real = codex.persona_workspaces
    codex.persona_workspaces = lambda: sorted(set(real()) | {ws})
    try:
        _, servers = parity.codex_mcp_status(ws)
    finally:
        codex.persona_workspaces = real
    return sorted(f"mcp__{s['name']}__{t}" for s in servers for t in (s.get("tools") or {}))


async def codex_conversation_live(ws: Path, root: Path) -> None:
    import loam_broker as cfg  # noqa: PLC0415
    broker = load("broker_parity", AGENTS / "bin" / "session-broker")
    saved = {n: getattr(cfg, n) for n in ("SESSION_DIR", "workspace")}
    real_ws = codex.persona_workspaces
    cfg.SESSION_DIR = root / "sessions"
    cfg.SESSION_DIR.mkdir()
    cfg.workspace = lambda agent: ws if agent == NAME else saved["workspace"](agent)
    codex.persona_workspaces = lambda: sorted(set(real_ws()) | {ws})
    session = None
    try:
        session = broker.CodexSession(NAME, provider="codex", model=CODEX_MODEL,
                                      effort="low")
        await session.start()
        queue: asyncio.Queue = asyncio.Queue()
        session.watch(queue)
        await session.send_message(
            "Two things, in order. First, use spawn_agent to start one sub-agent whose "
            "whole task is to reply with exactly the sentence 'SUBAGENT-SPEAKS-7.' and "
            "nothing else; wait for it with wait_agent. Then reply yourself with the "
            "tokens your instructions give for your soul and your conversation layer, "
            "and the word ROOT-DONE. Do not repeat the sub-agent's sentence.")
        events, deadline = [], time.monotonic() + 300
        while time.monotonic() < deadline:
            try:
                event = await asyncio.wait_for(queue.get(), 10)
            except TimeoutError:
                if session.status() in ("done", "failed", "idle"):
                    break
                continue
            events.append(event)
            if event.get("type") == "result" and not session.background_tasks:
                break
        reply = loam_thread_engine.Reply.of(events)
        said = " ".join(reply.segments)
        spoke = [e for e in events if e.get("type") == "assistant"
                 and "SUBAGENT-SPEAKS-7" in json.dumps(e.get("message") or {})]
        print(f"      Codex persona said: {said[:300]!r}")
        print(f"      subagent message events: {len(spoke)}, their parents: "
              f"{sorted({str(e.get('parent_tool_use_id')) for e in spoke})}")
        check("LIVE Codex conversation: soul and _active.md loaded (their tokens said)",
              "SOUL-QUARTZ" in said and "ACTIVE-JADE" in said, said)
        check("LIVE Codex conversation: a subagent spoke, nested under its call",
              spoke and all(e.get("parent_tool_use_id") for e in spoke),
              [e.get("parent_tool_use_id") for e in spoke])
        check("LIVE Codex conversation: the subagent's sentence is not the persona's message",
              "SUBAGENT-SPEAKS-7" not in said and "ROOT-DONE" in said, said)
    finally:
        if session is not None:
            try:
                await session.stop()
            except Exception:  # noqa: BLE001
                if session.proc and session.proc.returncode is None:
                    codex.kill_group(session.proc.pid)
        cfg.SESSION_DIR = saved["SESSION_DIR"]
        cfg.workspace = saved["workspace"]
        codex.persona_workspaces = real_ws


def live(ws: Path, root: Path) -> None:
    os.environ["LOAM_PROBE"] = "1"
    claude_mcp = claude_live(ws)
    codex_mcp = codex_mcp_live(ws)
    check("LIVE Claude Code listed its MCP tools", bool(claude_mcp), len(claude_mcp))
    only_claude = sorted(set(claude_mcp) - set(codex_mcp))
    only_codex = sorted(set(codex_mcp) - set(claude_mcp))
    print(f"      MCP tools: Claude Code {len(claude_mcp)}, Codex {len(codex_mcp)}")
    check("LIVE the same MCP tools by name on Claude Code and Codex (Loam's tools)",
          not only_claude and not only_codex,
          f"only Claude: {only_claude}; only Codex: {only_codex}")
    login = cw.login_refusal()
    if login:
        print(f"SKIP  LIVE Codex conversation: {login}")
    else:
        asyncio.run(codex_conversation_live(ws, root))
    state = agy.login_state()
    if not state.get("connected"):
        print(f"SKIP  LIVE Antigravity: {state.get('reason') or 'not signed in'} "
              f"(needs the owner's Google sign-in; only its offline half is held)")
    else:
        print("NOTE  Antigravity is signed in: its live half is not written yet; "
              "the first signed-in run should add it")


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="loam-provider-parity-"))
    try:
        ws = build(root)
        files_cases(ws)
        tools_cases(ws)
        record_cases()
        lanes_cases()
        if LIVE:
            live(ws, root)
        else:
            print("SKIP  live: run with --live")
    finally:
        shutil.rmtree(root, ignore_errors=True)
        if LIVE:   # the instructions file a Codex start writes for this persona
            codex.instructions_path(NAME).unlink(missing_ok=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
