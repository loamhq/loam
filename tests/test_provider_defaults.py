#!/usr/bin/env python3
"""Seed empty installs and deliver replies through the real provider adapters.

The three CLI fixtures implement the respective wire protocols and enforce
their local login. All credentials and files are synthetic. No network or
systemd is used. Run: python3 tests/run-all test_provider_defaults.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROVIDERS = ("claude-code", "codex", "antigravity")
MODELS = dict(zip(PROVIDERS, ("claude-fixture", "gpt-fixture", "gemini-fixture"), strict=True))


def emit(event):
    print(json.dumps(event), flush=True)


def cli(provider, marker):
    args = sys.argv[4:]
    ready = Path(marker).exists()
    if provider == "antigravity" and args == ["models"]:
        print("gemini-fixture\tGemini Fixture" if ready else "Sign in to continue")
        return 0 if ready else 1
    if args == ["--version"]:
        print("1.0.0")
        return 0
    if provider == "codex" and args == ["login", "status"]:
        print("Logged in" if ready else "Not logged in")
        return 0 if ready else 1
    model = MODELS[provider]
    reply = f"Reply from {provider}"
    # Log actual CLI invocations for turns, even when the login is absent.
    if provider == "antigravity":
        with Path("turns.txt").open("a") as log:
            print(provider, file=log)
        assert ready and "--model" not in args and "--effort" not in args, args
        emit({"event": "init", "init": {"model": model}})
        emit({"event": "step_update", "conversation_id": "fixture", "step_index": 1,
              "state": "DONE", "step_type": "agent_response", "text_delta": reply})
        emit({"event": "result", "result": {"status": "SUCCESS", "num_turns": 1,
                                            "response": reply, "conversation_id": "fixture"}})
        return 0
    for line in sys.stdin:
        event = json.loads(line)
        if provider == "claude-code":
            if event["type"] != "user":
                continue
            # Loam's best-effort /rename is not a turn.
            if "/rename " in json.dumps(event["message"]["content"]):
                continue
            with Path("turns.txt").open("a") as log:
                print(provider, file=log)
            if not ready:
                emit({"type": "result", "is_error": True, "result": "Not logged in",
                      "num_turns": 1})
                continue
            assert "--effort" not in args, args
            emit({"type": "system", "subtype": "init", "model": model})
            emit({"type": "assistant", "message": {"model": model,
                                                   "content": [{"type": "text", "text": reply}]}})
            emit({"type": "result", "num_turns": 1, "duration_ms": 1})
            continue
        method = event.get("method")
        if "id" not in event:
            continue
        params = event.get("params", {})
        result = {}
        if method == "model/list":
            result = {"data": [{"id": model, "model": model, "displayName": "GPT Fixture",
                                "isDefault": True}]}
        if method in ("thread/start", "thread/resume"):
            assert params.get("model") is None, params
            result = {"thread": {"id": "fixture"}, "model": model}
        if method == "turn/start":
            with Path("turns.txt").open("a") as log:
                print(provider, file=log)
            assert ready and params.get("effort") is None and params.get("model") is None, params
            result = {"turn": {"id": "fixture-turn"}}
        emit({"id": event["id"], "result": result})
        if method == "turn/start":
            for verb, data in (
                ("turn/started", {"turn": {"id": "fixture-turn"}}),
                ("item/completed", {"item": {"type": "agentMessage", "id": "answer", "text": reply}}),
                ("turn/completed", {"turn": {"id": "fixture-turn", "status": "completed"}}),
            ):
                emit({"method": verb, "params": {"threadId": "fixture", **data}})
    return 0


def fixture(root):
    home, labs, vault, state = (root / name for name in ("home", "agents", "vault", "state"))
    for path in (home, labs, vault, state):
        path.mkdir()
    login = {
        "claude-code": home / ".claude" / ".credentials.json",
        "codex": state / "broker" / "codex" / "auth.json",
        "antigravity": home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token",
    }
    config = root / "loam.toml"
    bodies = {"claude-code": {"claudeAiOauth": {"accessToken": "fixture"}},
              "codex": {"tokens": {"refresh_token": "fixture"}}, "antigravity": {}}
    for path in login.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    binaries = {}
    for key, provider in zip(("claude", "codex", "agy"), PROVIDERS, strict=True):
        binary = root / key
        binary.write_text(
            f"#!/bin/sh\nexec '{sys.executable}' '{__file__}' --cli {provider} '{login[provider]}' \"$@\"\n")
        binary.chmod(0o700)
        binaries[key] = binary
    lines = ["[owner]", 'name = "Test Owner"', "[paths]",
             *(f'{key} = "{value}"' for key, value in
               (("home", home), ("root", ROOT), ("workspaces", labs),
                ("vault", vault), ("state", state))),
             "[bin]", *(f'{key} = "{value}"' for key, value in binaries.items())]
    config.write_text("\n".join(lines) + "\n")

    def connect(provider):
        login[provider].write_text(json.dumps(bodies[provider]))

    return config, home, connect, login


async def exercise(case, root, baseline=False):
    import importlib.machinery
    import importlib.util
    from unittest.mock import AsyncMock

    config, home, connect, credentials = fixture(root)
    for key in list(os.environ):
        if (key.startswith("LOAM_") and key not in ("LOAM_OFFLINE",)
                or key in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")):
            os.environ.pop(key)
    os.environ.update(LOAM_CONFIG=str(config), HOME=str(home), LOAM_USAGE_READS="off")
    sys.path.insert(0, str(ROOT / "lib"))
    import loam_agents as agents
    import loam_broker as cfg
    import loam_codex as codex
    import loam_config as lc
    import loam_skills as library
    import loam_starter as starter
    import loam_threads as threads
    import loam_engineer as eng
    import loam_profile as profile
    from loam_session_socket import listen

    assert credentials["codex"] == codex.AUTH_PATH, codex.AUTH_PATH

    def provision(agent, wake):
        workspace = lc.WORKSPACES / agent
        for folder, filename, value in ((".codex", "config.toml", ""),
                                        (".agents", "mcp_config.json", "{}"),
                                        (".claude", "settings.json", '{"model": "fable"}')):
            (workspace / folder).mkdir()
            (workspace / folder / filename).write_text(value)
        return "fixture provision"

    library.seed_vault(lc.VAULT)
    seeded = starter.seed(provision=provision)
    agents._provision = provision
    agent, hello = seeded["agent"], seeded["thread"]
    assert not agents.card(agent).get("provider")
    if case == "legacy":
        threads.update(hello, engines={agent: "claude-code"})
        marker = starter.marker_path()
        stamp = threads.record_path(hello).stat().st_mtime_ns + 1
        os.utime(marker, ns=(stamp, stamp))
        assert starter.inherit_legacy_hello() and not starter.inherit_legacy_hello()
        assert threads.read(hello)[0]["engines"] == {}

    # Configure credentials only after seeding. The binaries all existed at seed time.
    if case in PROVIDERS:
        connect(case)
        expected = case
    elif case in ("several", "explicit"):
        for provider in PROVIDERS:
            connect(provider)
        expected = "codex" if case == "explicit" else "claude-code"
    elif case == "legacy":
        connect("antigravity")
        expected = "antigravity"
    else:
        expected = None

    cfg.memory = lambda: {"available": None, "cgroup_room": None,
                          "reserve": cfg.MEMORY_RESERVE_MB}
    loader = importlib.machinery.SourceFileLoader("broker", str(ROOT / "bin" / "session-broker"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    host = module.Broker()
    # A relative path keeps the private Unix socket short under nested test directories.
    os.chdir(root)
    async with listen(host, Path("host.sock")):
        server = module.Server(module.SocketBroker(Path("host.sock")))
        server.threads._memory_pins = AsyncMock()

        class Writer:
            def __init__(self):
                self.data = b""

            def write(self, data):
                self.data += data

            async def drain(self):
                pass

        async def route(method, path, payload=None, code=200):
            writer = Writer()
            await server.route(method, f"/v1{path}", {}, payload or {}, writer)
            head, body = writer.data.split(b"\r\n\r\n", 1)
            assert int(head.split()[1]) == code, (method, path, writer.data)
            return json.loads(body)

        async def say(tid, who):
            await route("PUT", f"/threads/{tid}/messages", {
                "text": "Hello from the test", "submission_id": "hello-test"})
            await asyncio.wait_for(server.threads.workers[(tid, who)], 15)
            last = threads.read(tid)[1][-1]
            turns = (lc.WORKSPACES / who / "turns.txt")
            return last, turns.read_text().splitlines() if turns.exists() else []

        if baseline:
            last, turns = await say(hello, agent)
            assert not last.get("engine") == "antigravity" and turns == ["claude-code"], (last, turns)
            print("BASELINE Antigravity only: first hello launched signed-out Claude Code", flush=True)
            return

        if case == "explicit":
            await route("PATCH", f"/agents/{agent}", {"provider": "codex"})
            assert agents.card(agent)["provider"] == "codex"
        if case == "antigravity":
            # Catch a legacy model leaking from the old implicit Claude default.
            agents._write_agent(agent, {**agents.card(agent), "model": "opus", "effort": "high"})

        rows = {r["id"]: r for r in (await route("GET", "/providers"))["providers"]}
        assert {p for p in PROVIDERS if rows[p]["connected"]} == {
            p for p in PROVIDERS if credentials[p].exists()}
        settings = await route("GET", f"/agents/{agent}")
        assert settings["defaults"]["effective"]["provider"] == expected, settings["defaults"]
        assert settings["defaults"]["wakes"]["provider"] == (
            "codex" if expected == "codex" else
            "claude-code" if expected == "claude-code" else None)
        assert profile.reviewer_provider() == (expected if expected in profile.REVIEWER_PROVIDERS
                                               else "claude-code" if case == "explicit" else None)
        assert eng.platform_project().runtime == (
            "claude" if case in ("several", "explicit", "claude-code") else expected)
        project = eng.Project("Example", root, root / "jobs", platform=False)
        assert project.runtime == ("codex" if case in ("several", "explicit", "codex")
                                   else "claude" if case == "claude-code" else expected)

        for target in (hello, (await route("POST", "/agents/new", code=201))["id"]):
            view = (await route("GET", f"/threads/{target}"))["thread"]
            assert view["members"][0]["engine"] == expected, view
            last, turns = await say(target, agent)
            if expected:
                assert (last["engine"], last["text"], last["model"]) == (
                    expected, f"Reply from {expected}", MODELS[expected]), last
                assert set(turns) == {expected}, turns
            else:
                assert turns == [] and last["author"] == threads.OWNER, (turns, last)
                assert "Connect a provider" in server.threads.marks_for(target)[0]["text"]
        print(f"PASS {case}: hello and New agent turn -> {expected or 'setup'}", flush=True)

        draft = await route("POST", "/agents/drafts", {
            "name": "Scout", "soul": "A test agent.", "config": "Test only."}, code=201)
        made = await route("POST", f"/agents/drafts/{draft['draft']}/confirm", {"rev": draft["rev"]}, code=201)
        first = made["first_thread"]
        assert not agents.card("scout").get("provider")

        # The no-login broker has already cached a negative Antigravity reading.
        # A newly connected account must take effect in the same running broker.
        if case == "none":
            assert threads.default_engine("scout") is None
            job = {"thread": hello, "persona": agent, "job": "fixture-job"}
            assert server.threads.deliver_job(job, "A fixture result.") == "waiting for a provider"
            connect("claude-code")
            lc.CLAUDE_BIN.chmod(0o600)
            assert threads.default_engine("scout") is None
            rows = {r["id"]: r for r in (await route("GET", "/providers"))["providers"]}
            assert not rows["claude-code"]["connected"]
            connect("antigravity")
            expected = "antigravity"
            assert server.threads.deliver_job(job, "A fixture result.") == "delivered"
            await asyncio.wait_for(server.threads.workers[(hello, agent)], 15)
            assert threads.read(hello)[1][-1]["engine"] == expected
        elif case == "explicit":
            # A thread override remains explicit even when the picker chose its current engine.
            await route("PATCH", f"/threads/{first}", {"engine": {"scout": "claude-code"}})
            expected = "claude-code"
        last, turns = await say(first, "scout")
        assert last["engine"] == expected and turns == [expected], (last, turns)
        print(f"PASS {case}: newly created agent's first reply -> {expected}", flush=True)
        if case == "explicit":
            credentials["codex"].unlink()
            retained = (await route("GET", f"/threads/{hello}"))["thread"]["members"][0]
            assert retained["engine"] == "codex" and retained["engine_override"] is None, retained
            assert agents.card(agent)["provider"] == "codex"
            print("PASS explicit: the saved agent choice survives the loss of its login", flush=True)
    await asyncio.gather(*(session.stop() for session in host.sessions.values()))


def main():
    if sys.argv[1:2] == ["--cli"]:
        return cli(*sys.argv[2:4])
    if sys.argv[1:2] == ["--case"]:
        with tempfile.TemporaryDirectory(prefix="loam-default-") as root:
            asyncio.run(exercise(sys.argv[2], Path(root), "--baseline" in sys.argv))
        return 0
    for case in (*PROVIDERS, "none", "several", "explicit", "legacy"):
        subprocess.run([sys.executable, __file__, "--case", case], check=True, timeout=75)
    return 0


if __name__ == "__main__":
    sys.exit(main())
