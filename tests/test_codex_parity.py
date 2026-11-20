#!/usr/bin/env python3
"""Check that a persona keeps its identity and configured capabilities when it changes provider."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# The box's own plugin layer stays out of the renders compared here (as in
# every other test): a plugin turned on live (the owner's Cloudflare, 2026-11-14)
# adds its MCP server to every persona and is not what this test is about.
import tempfile  # noqa: E402
os.environ["LOAM_PLUGINS_DIR"] = tempfile.mkdtemp(prefix="loam-parity-yours-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import loam_codex as codex  # noqa: E402
import loam_config as lc  # noqa: E402
from loam_memory import strip_frontmatter  # noqa: E402

QUESTION = ("List the instruction files you loaded, in order, and the names "
            "of every tool you have.")
# By id: the `haiku` alias moves with the CLI pin (2.1.294 made it Haiku 5.5),
# and a parity answer compared across a silent model change is not parity.
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE = str(lc.CLAUDE_BIN)

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  | {detail}" if detail else ""))


def claude_half(ws: Path, binary: str = CLAUDE) -> tuple[list[str], str]:
    """The Claude half, on ``binary`` (``bin.claude`` by default:
    ``bin/provider-pin test`` points it at the pinned build)."""
    env = {k: v for k, v in os.environ.items() if k != "LOAM_WAKE"}
    env.update({"CLAUDE_PROJECT_DIR": str(ws), "LOAM_PROBE": "1",
                "DISABLE_AUTOUPDATER": "1"})
    done = subprocess.run(
        [binary, "-p", QUESTION, "--model", CLAUDE_MODEL,
         "--output-format", "stream-json", "--verbose", "--max-turns", "1"],
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
    return tools, answer


def codex_mcp_tools(ws: Path, binary: str | None = None) -> list[str]:
    """Every MCP tool a Codex thread in this workspace would have, named the
    way Codex names them to the model and to hooks: ``mcp__<server>__<tool>``."""
    _, servers = codex_mcp_status(ws, binary)
    return sorted(f"mcp__{server['name']}__{tool}"
                  for server in servers
                  for tool in (server.get("tools") or {}))


def codex_mcp_status(ws: Path, binary: str | None = None) -> tuple[dict, list[dict]]:
    """``thread/start``'s answer and ``mcpServerStatus/list``'s servers for
    a thread in this workspace, from an app-server started the way the
    broker starts one, on ``binary`` (``bin.codex`` by default:
    ``bin/provider-pin test`` points it at the pinned build)."""
    codex.write_instructions(ws.name, ws)
    codex.write_trust(codex.persona_workspaces(), codex.read_trusted_hooks())
    proc = subprocess.Popen([binary or codex.CODEX, *codex.APP_SERVER_ARGV],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, cwd=str(ws),
                            env=codex.env(), start_new_session=True)

    def rpc(ident: int, method: str, params: dict) -> dict:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": ident,
                                     "method": method, "params": params}) + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            obj = json.loads(line)
            if obj.get("id") == ident:
                return obj.get("result") or {}
        return {}

    started, status = {}, {}
    try:
        rpc(0, "initialize", {"clientInfo": {"name": "parity", "version": "1"}})
        proc.stdin.write('{"jsonrpc":"2.0","method":"initialized"}\n')
        proc.stdin.flush()
        # Listed for a thread in the workspace, as the persona has them. With
        # no thread the list is the app-server's global config only, from
        # 0.163 on (#51872): `codex_apps` and none of the project's servers
        # (measured on 0.167.0, 2026-11-09). 0.159.3 read the launch cwd.
        started = rpc(1, "thread/start", {"cwd": str(ws)})
        thread_id = (started.get("thread") or {}).get("id")
        # Read until no server is still `starting`: a list taken at once can
        # catch a server mid-handshake with its tools already listed, on
        # either build (0.167.0 and 0.167.1, 2026-11-11), which reads as a
        # server that failed to connect when it had not finished.
        deadline, ident = time.monotonic() + 30, 2
        while True:
            status = rpc(ident, "mcpServerStatus/list", {"threadId": thread_id})
            starting = [s for s in status.get("data") or []
                        if s.get("runtimeStatus") == "starting"]
            if not starting or time.monotonic() > deadline:
                break
            ident += 1
            time.sleep(1)
    finally:
        codex.kill_group(proc.pid)
        proc.wait()
    return started, list(status.get("data") or [])


def config_cases() -> None:
    """Offline: the rendered config turns off the `codex_apps` server a
    ChatGPT login brings (Gmail, Drive, GitHub, Sites; measured 10-01)."""
    import importlib.machinery  # noqa: PLC0415
    import importlib.util  # noqa: PLC0415
    import tomllib  # noqa: PLC0415
    loader = importlib.machinery.SourceFileLoader(
        "provision", str(Path(__file__).resolve().parent.parent / "bin" / "provision"))
    spec = importlib.util.spec_from_loader("provision", loader)
    provision = importlib.util.module_from_spec(spec)
    loader.exec_module(provision)
    ws = lc.WORKSPACES / lc.LEAD_AGENT
    rendered = tomllib.loads(provision.codex_config(lc.LEAD_AGENT, ws))
    check("the login's connector apps are off in the rendered config",
          rendered.get("features", {}).get("apps") is False)
    # What Claude Code runs without a prompt, Codex runs without one
    # (2026-10-01: every MCP call in a persona's thread 01a0f8a0 was asked and
    # refused). Read back per tool, the way Codex resolves it.
    allow = provision.settings(ws)["permissions"]["allow"]
    servers = rendered.get("mcp_servers", {})

    def approved(server: str, tool: str) -> bool:
        spec = servers.get(server, {})
        mode = ((spec.get("tools") or {}).get(tool) or {}).get("approval_mode")
        return (mode or spec.get("default_tools_approval_mode")) == "approve"
    wanted = [a for a in allow if a.startswith("mcp__")]
    whole = [a[5:] for a in wanted if "__" not in a[5:]]
    tools = [tuple(a[5:].split("__", 1)) for a in wanted if "__" in a[5:]]
    check("every MCP server Claude allows whole is approved whole on Codex",
          bool(whole) and all(servers.get(s, {}).get("default_tools_approval_mode")
                              == "approve" for s in whole), str(whole))
    check("every MCP tool Claude allows by name is approved on Codex",
          bool(tools) and all(approved(s, t) for s, t in tools), str(tools))
    # The servers themselves, offline: the runtime's four, and whatever the
    # plugins that are on give this persona (lib/loam_contributions.py),
    # the same on both providers. The live half below compares the tool
    # names they serve.
    import loam_contributions  # noqa: PLC0415
    mcp = provision.mcp_config(lc.LEAD_AGENT, ws)["mcpServers"]
    plugin = set(loam_contributions.lane_servers("persona", lc.LEAD_AGENT))
    check("the same MCP servers on both providers: the runtime's four, and the plugins'",
          sorted(servers) == sorted(mcp)
          and set(servers) - plugin == {"browser", "composition", "engineer", "loam"},
          str(sorted(servers)))
    # Every server approved whole since 2026-11-12, as Claude Code runs
    # every tool unasked under bypassPermissions: a tool a server adds
    # later must not ask on Codex either.
    check("every server is approved whole",
          all(spec.get("default_tools_approval_mode") == "approve"
              for spec in servers.values()), str(sorted(servers)))
    import loam_broker  # noqa: PLC0415
    flags = list(loam_broker.CLAUDE_STREAM_FLAGS)
    mode = flags[flags.index("--permission-mode") + 1] if "--permission-mode" in flags else None
    check("Claude Code's conversation lane asks for nothing",
          mode == "bypassPermissions", str(mode))
    check("...and still denies what the scheduler owns",
          {"CronCreate", "CronDelete", "ScheduleWakeup", "RemoteTrigger"}
          <= set(provision.settings(ws)["permissions"]["deny"]))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    config_cases()
    if "--live" not in sys.argv:
        print("SKIP  live and paid; run with --live")
        return 1 if fails else 0
    agent = args[0] if args else lc.LEAD_AGENT
    ws = lc.WORKSPACES / agent

    # 1. Instruction sources.
    sources = codex.instruction_sources(ws)
    text = codex.instructions(ws)
    # A source outside the workspace (Loam's own persona text, under the
    # runtime's docs/persona/ since cf8cc3c) is named by its full path.
    names = [str(p.relative_to(ws)) if p.is_relative_to(ws) else str(p) for p in sources]
    check("Claude Code's sources: CLAUDE.md first, _active.md last",
          names[0] == "CLAUDE.md" and names[-1] == "_active.md", str(names))
    check("the provider line opens the Codex file",
          text.startswith(codex.PROVIDER_LINE))
    cursor, ordered = 0, True
    for path in sources:
        body = path.read_text(encoding="utf-8")
        if path.name == "_active.md":
            body = strip_frontmatter(body)
        at = text.find(body.strip(), cursor)
        ordered &= at >= cursor
        cursor = at if at >= 0 else cursor
    check("every source verbatim in the Codex file, in Claude Code's order",
          ordered, " > ".join(names))
    check("no frontmatter from _active.md reaches Codex",
          "keep-coding-instructions:" not in text)

    # 2. MCP tool names.
    claude_tools, claude_answer = claude_half(ws)
    claude_mcp = sorted(t for t in claude_tools if t.startswith("mcp__"))
    codex_mcp = codex_mcp_tools(ws)
    check(f"Claude Code ({CLAUDE_MODEL}) reported its tools",
          bool(claude_tools), f"{len(claude_tools)} tools")
    check("Codex app-server reported its MCP tools", bool(codex_mcp),
          f"{len(codex_mcp)} tools")
    only_claude = sorted(set(claude_mcp) - set(codex_mcp))
    only_codex = sorted(set(codex_mcp) - set(claude_mcp))
    check("MCP tool names identical on both providers",
          not only_claude and not only_codex,
          f"only Claude: {only_claude}; only Codex: {only_codex}")

    # 3. Built-ins differ only by the documented list.
    # The CLI's init event still names the subagent tool `Task`; the model is
    # shown it as `Agent` (measured 2026-10-01, Haiku listed "Agent").
    builtins = ["Agent" if t == "Task" else t
                for t in claude_tools if not t.startswith("mcp__")]
    missing = [t for t in codex.CLAUDE_ONLY_TOOLS if t not in builtins]
    check("every Claude-only tool named in the provider line is on Claude Code",
          not missing, f"missing: {missing}")
    check("the provider line names every one of them",
          all(t in codex.PROVIDER_LINE for t in codex.CLAUDE_ONLY_TOOLS))

    print(f"\n--- Claude Code ({CLAUDE_MODEL}) answered ---\n{claude_answer}\n")
    login = codex.login_state()
    if not login["connected"]:
        print(f"SKIP  the Codex half of the question: {login['reason']}. "
              f"Sign in from the app, then run this again.")
    else:
        print("NOTE  Codex is signed in: ask the same question in a broker "
              "session on the cheapest model and compare.")
    print(f"\nFAILURES: {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
