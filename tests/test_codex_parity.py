#!/usr/bin/env python3
"""Check that a persona keeps its identity and configured capabilities when it changes provider."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Isolate the plugin directories so the comparison sees only declared fixtures.
import tempfile  # noqa: E402
os.environ["LOAM_PLUGINS_DIR"] = tempfile.mkdtemp(prefix="loam-parity-yours-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import loam_codex as codex  # noqa: E402
import loam_config as lc  # noqa: E402

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
        # List capabilities in a workspace thread to exercise the same environment as a
        # persona.
        started = rpc(1, "thread/start", {"cwd": str(ws)})
        thread_id = (started.get("thread") or {}).get("id")
        # Wait for every server handshake before comparing the advertised tools.
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
    # Verify equivalent tool approval behavior on both providers.
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
    # Check that every registered server receives its declared tool policy.
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

    # Optional per-agent Codex `verbosity` -> `model_verbosity` (2026-11-26):
    # present when set to low/medium/high, absent when unset or invalid,
    # ignored by non-Codex providers, and preserved by `loam_profile.kept`.
    import loam_profile  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as tmp:
        fx = Path(tmp) / "fixture"
        fx.mkdir()
        base_card = {
            "name": "fixture",
            "display": "Fixture",
            "workspace": str(fx),
            "vault": str(lc.VAULT),
            "provider": "codex",
            "effort": "high",
        }
        (fx / "agent.json").write_text(loam_profile.render_agent(base_card), encoding="utf-8")
        without_text = provision.codex_config("fixture", fx)
        without_toml = tomllib.loads(without_text)
        claude_without = provision.settings(fx)
        agy_without = provision.antigravity_mcp("fixture", fx)
        check("codex_config without verbosity omits model_verbosity",
              "model_verbosity" not in without_text and "model_verbosity" not in without_toml)

        for level in ("low", "medium", "high"):
            card = {**base_card, "verbosity": level}
            (fx / "agent.json").write_text(loam_profile.render_agent(card), encoding="utf-8")
            with_text = provision.codex_config("fixture", fx)
            with_toml = tomllib.loads(with_text)
            check(f"codex_config with verbosity={level!r} renders model_verbosity = {level!r}",
                  f'model_verbosity = "{level}"' in with_text
                  and with_toml.get("model_verbosity") == level)

        check("non-Codex provider configs ignore verbosity",
              provision.settings(fx) == claude_without
              and provision.antigravity_mcp("fixture", fx) == agy_without)

        (fx / "agent.json").write_text(
            json.dumps({**base_card, "verbosity": "verbose"}), encoding="utf-8")
        bad_text = provision.codex_config("fixture", fx)
        check("codex_config ignores an invalid verbosity value",
              "model_verbosity" not in bad_text and "model_verbosity" not in tomllib.loads(bad_text))

        kept = loam_profile.kept({**base_card, "verbosity": "high"})
        rendered_card = json.loads(loam_profile.render_agent({**base_card, "verbosity": "high"}))
        keys = list(rendered_card)
        check("agent.json keeps verbosity over provision and places it beside effort",
              kept.get("verbosity") == "high"
              and keys.index("verbosity") == keys.index("effort") + 1, str(keys))


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
    check("Claude Code's sources include soul, config and map in order",
          names[0] == "CLAUDE.md" and all(n in names for n in ("soul.md", "config.md", "map.md"))
          and names.index("soul.md") < names.index("config.md") < names.index("map.md"), str(names))
    check("the provider line opens the Codex file", text.startswith(codex.PROVIDER_LINE))
    cursor = 0
    for path in sources:
        body = path.read_text(encoding="utf-8").strip()
        pos = text.find(body, cursor)
        check(f"same text reaches Codex in order: {path.name}", pos >= cursor)
        if pos >= 0:
            cursor = pos + len(body)

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

    # Allow the documented differences in provider built-ins, including the subagent tool
    # name.
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
