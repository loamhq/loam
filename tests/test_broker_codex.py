#!/usr/bin/env python3
"""Check translation, login, model choices and approval responses against offline fixtures.

With --live, start a signed-out app-server in a temporary home, request a
device code and cancel it. This mode contacts the provider."""

import asyncio
import base64
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
os.environ["LOAM_USAGE_READS"] = "off"  # no real CLI reads; see loam_provider_usage
import loam_broker as cfg  # noqa: E402
# Use fixture memory to isolate the broker from machine load.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_codex as codex  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

LIVE = "--live" in sys.argv
results = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} got={got!r}"[:300])
    if not ok:
        print(f"      wanted {want!r}")


class FakeWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None

    def body(self):
        head, _, payload = b"".join(self.chunks).decode().partition("\r\n\r\n")
        return int(head.split()[1]), json.loads(payload)


async def route(server, method, path, payload=None):
    writer = FakeWriter()
    try:
        await server.route(method, path, {}, payload or {}, writer)
    except broker_mod.BrokerError as exc:
        return exc.status, {"error": exc.message}
    return writer.body()


def translator_cases() -> None:
    t = codex.Translator("rec-1")
    t.model = "gpt-6-luna"
    out = t.feed("turn/started", {"threadId": "th", "turn": {"id": "tu"}})
    check("turn/started is the system/init that opens a turn",
          [(e["type"], e["subtype"], e["turn_id"]) for e in out],
          [("system", "init", "tu")])
    out = t.feed("item/started", {"item": {"type": "agentMessage", "id": "m1",
                                           "text": ""}})
    check("an agent message opens as message_start + content_block_start",
          [e["event"]["type"] for e in out],
          ["message_start", "content_block_start"])
    out = t.feed("item/agentMessage/delta", {"itemId": "m1", "delta": "Hel"})
    out += t.feed("item/agentMessage/delta", {"itemId": "m1", "delta": "lo"})
    check("each delta is a text_delta on a stream_event",
          [e["event"]["delta"] for e in out],
          [{"type": "text_delta", "text": "Hel"},
           {"type": "text_delta", "text": "lo"}])
    t.feed("thread/tokenUsage/updated", {"tokenUsage": {
        "last": {"inputTokens": 1200, "cachedInputTokens": 1000,
                 "outputTokens": 30, "reasoningOutputTokens": 0,
                 "totalTokens": 1230},
        "total": {"inputTokens": 1200, "cachedInputTokens": 1000,
                  "outputTokens": 30, "reasoningOutputTokens": 0,
                  "totalTokens": 1230},
        "modelContextWindow": 272000}})
    out = t.feed("item/completed", {"item": {"type": "agentMessage", "id": "m1",
                                             "text": "Hello"}})
    check("...and closes as block stop, message stop, then the assistant",
          [e.get("event", {}).get("type") or e["type"] for e in out],
          ["content_block_stop", "message_stop", "assistant"])
    check("the assistant message carries the text and the model",
          (out[-1]["message"]["content"], out[-1]["message"]["model"]),
          ([{"type": "text", "text": "Hello"}], "gpt-6-luna"))
    check("its usage sums to the request's input under the CLI's keys",
          cfg.context_used(out[-1]["message"]["usage"]), 1200)
    out = t.feed("item/started", {"item": {
        "type": "commandExecution", "id": "c1", "command": "ls -la",
        "cwd": "/w", "status": "inProgress"}})
    check("a command is a Bash tool_use with its command line",
          out[0]["message"]["content"][0],
          {"type": "tool_use", "id": "c1", "name": "Bash",
           "input": {"command": "ls -la", "cwd": "/w"}})
    out = t.feed("item/completed", {"item": {
        "type": "commandExecution", "id": "c1", "command": "ls -la",
        "aggregatedOutput": "nope", "exitCode": 2, "status": "failed"}})
    check("...and its completion a tool_result, an error on a non-zero exit",
          out[0]["message"]["content"][0],
          {"type": "tool_result", "tool_use_id": "c1",
           "content": "nope\n(exit 2)", "is_error": True})
    out = t.feed("item/started", {"item": {
        "type": "mcpToolCall", "id": "p1", "server": "history",
        "tool": "search", "arguments": {"q": "x"}, "status": "inProgress"}})
    check("an MCP call is named the way the CLI names one",
          out[0]["message"]["content"][0]["name"], "mcp__history__search")
    out = t.feed("item/completed", {"item": {
        "type": "mcpToolCall", "id": "p1", "server": "history",
        "tool": "search", "status": "completed",
        "result": {"content": [{"type": "text", "text": "found"}]}}})
    check("...and answered with its content blocks",
          out[0]["message"]["content"][0]["content"],
          [{"type": "text", "text": "found"}])
    # The search query arrives on completion.
    search = json.loads((AGENTS / "fixtures" / "codex-websearch.json")
                        .read_text(encoding="utf-8"))
    sid = search["item/started"]["item"]["id"]
    check("a web search that starts empty draws nothing yet",
          t.feed("item/started", search["item/started"]), [])
    out = t.feed("item/completed", search["item/completed"])
    check("...and at completion is the tool_use, then its tool_result",
          [(e["type"], e["message"]["content"][0]["type"]) for e in out],
          [("assistant", "tool_use"), ("user", "tool_result")])
    check("...the call's input is the query and each query sent",
          out[0]["message"]["content"][0],
          {"type": "tool_use", "id": sid, "name": "webSearch",
           "input": {"query": "site.plotbook.example.org allotment API plot "
                              "booking integrations garden ...",
                     "queries": ["site.plotbook.example.org allotment API plot "
                                 "booking integrations garden",
                                 "site.docs.plotbook.example.org plots availability "
                                 "waiting list booking allotment"]}})
    lines = out[1]["message"]["content"][0]["content"].split("\n")
    check("...the result counts the results and lists the first titles and URLs",
          (lines[0], lines[1], lines[-1], len(lines),
           out[1]["message"]["content"][0]["is_error"]),
          ("19 results", "Allotment Society APIs | Plot booking by Plotbook — "
           "https://plotbook.example.org/products", "… and 11 more",
           1 + codex.SHOWN_RESULTS + 1, False))
    check("...and no field of either is null",
          "null" in json.dumps(out[0]["message"]["content"])
          or "None" in out[1]["message"]["content"][0]["content"],
          False)
    out = t.feed("item/started", {"item": {"type": "webSearch", "id": "w2",
                                           "query": "loam", "action": None}})
    check("a search that starts with its query is drawn at start",
          out[0]["message"]["content"][0]["input"], {"query": "loam"})
    out = t.feed("item/completed", {"item": {"type": "webSearch", "id": "w2",
                                             "query": "loam", "results": []}})
    check("...and is not drawn twice",
          [(e["type"], e["message"]["content"][0]["content"]) for e in out],
          [("user", "no results")])
    t.feed("item/started", {"item": {"type": "webSearch", "id": "w3", "query": ""}})
    out = t.feed("item/completed", {"item": {
        "type": "webSearch", "id": "w3", "query": "x",
        "results": [{"type": "error", "message": "search unavailable"}]}})
    check("a search that failed is a failed step with Codex's words",
          (out[1]["message"]["content"][0]["content"],
           out[1]["message"]["content"][0]["is_error"]),
          ("search unavailable", True))
    t.feed("item/started", {"item": {"type": "imageGeneration", "id": "g1",
                                     "status": "inProgress", "result": ""}})
    out = t.feed("item/completed", {"item": {
        "type": "imageGeneration", "id": "g1", "status": "failed", "result": "",
        "failure": {"type": "usageLimitExceeded", "limitId": "images"}}})
    check("an image generation that failed says why, after its call",
          (out[0]["message"]["content"][0]["input"],
           out[1]["message"]["content"][0]["content"],
           out[1]["message"]["content"][0]["is_error"]),
          ({}, "usageLimitExceeded images", True))
    out = t.feed("item/started", {"item": {
        "type": "dynamicToolCall", "id": "d1", "tool": "lookup",
        "arguments": {"q": "x"}, "status": "inProgress", "contentItems": None,
        "success": None}})
    check("a dynamic tool call is named by its tool, input its arguments",
          (out[0]["message"]["content"][0]["name"],
           out[0]["message"]["content"][0]["input"]), ("lookup", {"q": "x"}))
    out = t.feed("item/completed", {"item": {
        "type": "dynamicToolCall", "id": "d1", "tool": "lookup",
        "status": "failed", "success": False,
        "contentItems": [{"type": "inputText", "text": "no such thing"}]}})
    check("...and one that failed is an error with its text",
          (out[0]["message"]["content"][0]["content"],
           out[0]["message"]["content"][0]["is_error"]), ("no such thing", True))
    out = t.feed("item/started", {"item": {"type": "imageView", "id": "v1",
                                           "path": "/w/a.png", "note": None}})
    check("any other item draws only the fields it has",
          out[0]["message"]["content"][0]["input"], {"path": "/w/a.png"})
    check("reasoning is not drawn",
          t.feed("item/completed", {"item": {"type": "reasoning", "id": "r"}}),
          [])
    out = t.feed("error", {"error": {"message": "Reconnecting... 1/5"},
                           "willRetry": True})
    check("a retry Codex will make is api_retry", out[0]["subtype"], "api_retry")
    out = t.feed("turn/completed", {"threadId": "th", "turn": {
        "id": "tu", "status": "completed", "durationMs": 900, "items": []}})
    result = out[0]
    check("turn/completed is a successful result",
          (result["type"], result["subtype"], result["is_error"]),
          ("result", "success", False))
    check("...whose window is under the model's name, as context_window reads it",
          broker_mod.context_window(result, "gpt-6-luna"), 272000)
    out = t.feed("turn/completed", {"threadId": "th", "turn": {
        "id": "tu2", "status": "failed", "items": [],
        "error": {"message": "401 Unauthorized"}}})
    check("a failed turn is an error result with Codex's sentence",
          (out[0]["is_error"], out[0]["result"]), (True, "401 Unauthorized"))


def jwt(claims: dict) -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


def login_cases(root: Path) -> None:
    codex.AUTH_PATH = root / "auth.json"
    check("no file is no login", codex.login_state()["reason"],
          "no login on the box")
    codex.AUTH_PATH.write_text(json.dumps({"OPENAI_API_KEY": None, "tokens": {
        "id_token": jwt({"email": "a@example.com",
                         "https://api.openai.com/auth": {
                             "chatgpt_plan_type": "pro"}}),
        "access_token": "SECRET-ACCESS", "refresh_token": "SECRET-REFRESH",
        "account_id": "acc"}}))
    state = codex.login_state()
    check("a ChatGPT login is connected, with whose and which plan",
          (state["connected"], state["source"], state["account"], state["plan"]),
          (True, "chatgpt", "a@example.com", "pro"))
    check("...no expiry, because the file states none",
          (state["expires_at"], state["days_left"]), (None, None))
    check("...and no token or piece of one is answered",
          "SECRET" in json.dumps(state), False)
    codex.AUTH_PATH.write_text("{not json")
    check("an unreadable file is not a login", codex.login_state()["connected"],
          False)


# Device-login terminal output with a fixture code.
DEVICE_TEXT = (
    "\nWelcome to Codex [v\x1b[90m0.159.3\x1b[0m]\n\x1b[90mOpenAI's "
    "command-line coding agent\x1b[0m\n\nFollow these steps to sign in with "
    "ChatGPT using device code authorization:\n\n1. Open this link in your "
    "browser and sign in to your account\n   \x1b[94mhttps://auth.openai.com/"
    "codex/device\x1b[0m\n\n2. Enter this one-time code \x1b[90m(expires in "
    "15 minutes)\x1b[0m\n   \x1b[94mAB12-CD34E\x1b[0m\n\n\x1b[90mContinue only "
    "if you started this login in Codex.\x1b[0m\n\n")


def device_cases() -> None:
    check("the URL out of the CLI's text", codex.device_url(DEVICE_TEXT),
          "https://auth.openai.com/codex/device")
    check("the code out of the CLI's text", codex.device_code(DEVICE_TEXT),
          "AB12-CD34E")
    check("no code before the URL is drawn",
          codex.device_code(DEVICE_TEXT.split("1. Open")[0]), None)


async def offline_broker_cases(root: Path) -> None:
    codex.MODELS_PATH = root / "codex-models.json"
    codex.write_models([
        {"id": "gpt-6-luna", "displayName": "GPT-6-Luna", "isDefault": False,
         "hidden": False, "defaultReasoningEffort": "medium",
         "supportedReasoningEfforts": [{"reasoningEffort": "low"},
                                       {"reasoningEffort": "max"}]},
        {"id": "secret-model", "hidden": True}])
    check("a hidden model is not on the menu",
          [m["id"] for m in codex.read_models()], ["gpt-6-luna"])
    check("a listed model and effort are taken",
          broker_mod.choices({"model": "gpt-6-luna", "effort": "max"}, "codex"),
          {"model": "gpt-6-luna", "effort": "max"})
    for label, payload in (("an unlisted Codex model is a 400", {"model": "opus"}),
                           ("an effort no model offers is a 400",
                            {"effort": "ultracode"})):
        try:
            broker_mod.choices(payload, "codex")
            got = None
        except broker_mod.BrokerError as exc:
            got = exc.status
        check(label, got, 400)

    session = broker_mod.CodexSession("probe")
    sent: list[dict] = []

    async def send_raw(obj):
        sent.append(obj)
    session.send_raw = send_raw
    session.state = "running"
    await session._handle({"jsonrpc": "2.0", "id": 7,
                           "method": "item/commandExecution/requestApproval",
                           "params": {"threadId": "th", "turnId": "tu",
                                      "itemId": "c1", "command": "rm -rf x",
                                      "cwd": "/w", "reason": "outside sandbox",
                                      "startedAtMs": 0}})
    check("an approval is a pending permission card",
          (list(session.pending), session.status()), (["codex-7"], "needs_input"))
    await session.answer_permission("codex-7", "deny", "no")
    check("...answered as JSON-RPC, decline for a deny",
          sent[-1], {"jsonrpc": "2.0", "id": 7, "result": {"decision": "decline"}})
    await session._handle({"jsonrpc": "2.0", "id": 8,
                           "method": "item/permissions/requestApproval",
                           "params": {"threadId": "th", "turnId": "tu",
                                      "itemId": "c2", "cwd": "/w",
                                      "permissions": {"network": {"enabled": True}},
                                      "startedAtMs": 0}})
    await session.answer_permission("codex-8", "allow")
    check("a permissions request allowed grants what it asked, for the turn",
          sent[-1]["result"], {"permissions": {"network": {"enabled": True}},
                               "scope": "turn"})
    # An MCP approval uses the same client permission route.
    elicit = {"serverName": "engineer", "threadId": "th", "turnId": "tu",
              "mode": "form", "message": "Allow the engineer server to run brief?",
              "requestedSchema": {"type": "object", "properties": {}},
              "_meta": {"codex_approval_kind": "mcp_tool_call"}}
    before = len(sent)
    await session._handle({"jsonrpc": "2.0", "id": 9,
                           "method": "mcpServer/elicitation/request",
                           "params": elicit})
    card = session.pending.get("codex-9") or {}
    check("an MCP elicitation is a card, and nothing is answered on its own",
          (card.get("tool_name"), card.get("input", {}).get("message"),
           session.status(), len(sent) - before),
          ("mcp__engineer", elicit["message"], "needs_input", 0))
    await session.answer_permission("codex-9", "allow")
    check("...allowed, it is accepted", sent[-1],
          {"jsonrpc": "2.0", "id": 9,
           "result": {"action": "accept", "content": {}}})
    await session._handle({"jsonrpc": "2.0", "id": 10,
                           "method": "mcpServer/elicitation/request",
                           "params": elicit})
    await session.answer_permission("codex-10", "deny", "no")
    check("...denied, it is declined", sent[-1]["result"], {"action": "decline"})
    question = {"threadId": "th", "turnId": "tu", "itemId": "q1",
                "isBlocking": True,
                "questions": [{"id": "pick", "header": "Pick",
                               "question": "Which?", "options": None}]}
    await session._handle({"jsonrpc": "2.0", "id": 11,
                           "method": "item/tool/requestUserInput",
                           "params": question})
    check("a question for the user is a card too",
          (session.pending["codex-11"]["tool_name"],
           session.pending["codex-11"]["input"]),
          ("request_user_input", {"questions": question["questions"]}))
    # Translate the client answer back to the provider schema.
    await session.answer_permission(
        "codex-11", "allow", answers={"pick": ["the first"]})
    check("...answered with the page's answers",
          sent[-1]["result"], {"answers": {"pick": {"answers": ["the first"]}}})
    await session._handle({"jsonrpc": "2.0", "id": 12,
                           "method": "execCommandApproval",
                           "params": {"command": ["rm", "x"], "cwd": "/w",
                                      "callId": "c", "conversationId": "th",
                                      "parsedCmd": []}})
    check("a v1 command approval is a card with the command joined",
          session.pending["codex-12"]["input"], {"command": "rm x", "cwd": "/w"})
    await session.answer_permission("codex-12", "deny")
    check("...and denied in the v1 words", sent[-1]["result"],
          {"decision": "denied"})
    await session._handle({"jsonrpc": "2.0", "id": 13,
                           "method": "item/tool/call", "params": {}})
    check("a request that asks no person is refused, not left waiting",
          (sent[-1]["error"]["code"], list(session.pending)), (-32601, []))

    # Usage can arrive after the final message.
    def usage(n: int) -> dict:
        last = {"inputTokens": n, "cachedInputTokens": n - 100,
                "outputTokens": 9}
        return {"tokenUsage": {"last": last, "total": last,
                               "modelContextWindow": 258400}}
    session._codex.model = "gpt-6-luna"
    session._codex.window = 258400
    for method, params in (
            ("turn/started", {"threadId": "th", "turn": {"id": "u1"}}),
            ("thread/tokenUsage/updated", usage(20000)),
            ("item/started", {"item": {"type": "agentMessage", "id": "m9",
                                       "text": ""}}),
            ("item/completed", {"item": {"type": "agentMessage", "id": "m9",
                                         "text": "hi"}}),
            ("thread/tokenUsage/updated", usage(22394)),
            ("turn/completed", {"threadId": "th", "turn": {
                "id": "u1", "status": "completed", "items": []}})):
        await session._handle({"jsonrpc": "2.0", "method": method,
                               "params": params})
    check("a Codex turn's context is its last request's, from the result",
          session.turns[-1]["context"], {"used": 22394, "window": 258400})

    # Direct-child helpers stay part of the open root turn, translate as
    # Claude-shaped Agent calls with plain helper names, and hold the reply
    # open until all helpers return rather than landing early or posting
    # background_tasks mid-turn.
    check("helper_name strips /root/ and formats underscores",
          (codex.helper_name("/root/critics"),
           codex.helper_name("/root/check_reviews"),
           codex.helper_name("/root"),
           codex.helper_name(None)),
          ("critics", "check reviews", "helper", "helper"))
    check("CHAT_SUBAGENT_ASK adds one wait sentence while SUBAGENT_ASK is untouched",
          ("wait_agent" in codex.CHAT_SUBAGENT_ASK,
           codex.CHAT_SUBAGENT_ASK.startswith(codex.SUBAGENT_ASK),
           "wait_agent" not in codex.SUBAGENT_ASK),
          (True, True, True))

    session.thread_id = "th"
    session.WAKE_GATHER_S = 0
    session.HOLD_GATHER_S = 0
    queue: asyncio.Queue = asyncio.Queue()
    session.watch(queue)

    async def feed(method, params):
        await session._handle({"jsonrpc": "2.0", "method": method,
                               "params": params})

    async def settle():
        for _ in range(10):
            await asyncio.sleep(0)

    def turn_starts(since):
        return [m for m in sent[since:] if m.get("method") == "turn/start"]

    def drawn_since():
        drawn = []
        while not queue.empty():
            event = queue.get_nowait()
            if event["type"] in ("broker.background_tasks", "result") or (
                    event["type"] == "system"
                    and event.get("subtype") in ("task_notification", "init")):
                drawn.append((event["type"],
                              event.get("subtype") or event.get("count")))
        return drawn

    async def answer(wake, turn_id):
        if wake:
            await session._handle({"jsonrpc": "2.0", "id": wake[0]["id"],
                                   "result": {"turn": {"id": turn_id}}})
        await settle()

    turns = len(session.turns)
    all_events: asyncio.Queue = asyncio.Queue()
    session.watch(all_events)
    await feed("turn/started", {"threadId": "th", "turn": {"id": "u2"}})
    await feed("item/started", {"threadId": "th", "turnId": "u2", "item": {
        "type": "subAgentActivity", "id": "call_1", "kind": "started",
        "agentThreadId": "sub", "agentPath": "/root/worker",
        "model": "gpt-6.2-sol", "reasoningEffort": "xhigh"}})
    await feed("item/completed", {"threadId": "th", "turnId": "u2", "item": {
        "type": "subAgentActivity", "id": "call_1", "kind": "started",
        "agentThreadId": "sub", "agentPath": "/root/worker"}})
    await feed("item/completed", {"threadId": "th", "turnId": "u2", "item": {
        "type": "subAgentActivity", "id": "call_1_ping", "kind": "interacted",
        "agentThreadId": "sub", "agentPath": "/root/worker"}})
    await feed("turn/started", {"threadId": "sub", "turn": {"id": "s1"}})
    spawn_events = []
    while not all_events.empty():
        spawn_events.append(all_events.get_nowait())
    tool_uses = [b for e in spawn_events if e.get("type") == "assistant"
                 for b in (e.get("message") or {}).get("content") or []
                 if b.get("type") == "tool_use"]
    tool_results = [b for e in spawn_events if e.get("type") == "user"
                    for b in (e.get("message") or {}).get("content") or []
                    if b.get("type") == "tool_result"]
    check("subAgentActivity started becomes one Agent tool_use with plain name and deferred result",
          (len(tool_uses), tool_uses[0]["name"], tool_uses[0]["input"]["description"],
           len(tool_results), "subAgentActivity" in json.dumps(spawn_events),
           "/root/worker" in json.dumps(spawn_events)),
          (1, "Agent", "worker", 0, False, False))
    check("an in-turn subagent stays part of the open turn (not background_tasks)",
          (session.status(), session.meta()["background_tasks"],
           session._codex.turn_id), ("working", 0, "u2"))
    await feed("thread/tokenUsage/updated", {"threadId": "th", **usage(30000)})
    await feed("thread/tokenUsage/updated", {"threadId": "sub", **usage(90000)})
    await feed("item/started", {"threadId": "sub", "turnId": "s1", "item": {
        "type": "mcpToolCall", "id": "x1", "server": "engineer",
        "tool": "brief", "arguments": {}}})
    # Root writes a premature answer while the helper is still running: buffered
    # and discarded when turn/completed fires while sub is still active.
    await feed("item/started", {"threadId": "th", "turnId": "u2", "item": {
        "type": "agentMessage", "id": "m-early", "phase": "final_answer"}})
    await feed("item/agentMessage/delta", {"threadId": "th", "turnId": "u2",
                                           "itemId": "m-early", "delta": "Premature answer."})
    await feed("item/completed", {"threadId": "th", "turnId": "u2", "item": {
        "type": "agentMessage", "id": "m-early", "phase": "final_answer",
        "text": "Premature answer."}})
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "u2", "status": "completed", "durationMs": 5000, "items": []}})
    held_events = []
    while not all_events.empty():
        held_events.append(all_events.get_nowait())
    check("premature root turn/completed holds turn open and emits neither draft nor result",
          (session.status(), session.meta()["background_tasks"],
           len(session.turns), session._codex.turn_id,
           any(e.get("type") == "result" for e in held_events),
           "Premature answer." in json.dumps(held_events)),
          ("working", 0, turns, None, False, False))
    await feed("item/completed", {"threadId": "sub", "turnId": "s1", "item": {
        "type": "mcpToolCall", "id": "x1", "server": "engineer",
        "tool": "brief", "status": "completed", "result": {"content": []}}})
    check("...its tool call closes and the root still holds open",
          session.status(), "working")

    before = len(sent)
    said: asyncio.Queue = asyncio.Queue()
    session.watch(said)
    last_before = session.last_message
    await feed("item/completed", {"threadId": "sub", "turnId": "s1", "item": {
        "type": "agentMessage", "id": "m-sub", "text": "The receipt says it landed."}})
    # Keep child output nested under its spawning call.
    sub_events = []
    while not said.empty():
        sub_events.append(said.get_nowait())
    parents = {e.get("parent_tool_use_id") for e in sub_events
               if "parent_tool_use_id" in e}
    import loam_thread_engine  # noqa: PLC0415
    check("RECORD a subagent's message is nested under its spawn call",
          (parents, loam_thread_engine.Reply.of(sub_events).segments,
           session.last_message == last_before),
          ({"call_1"}, [], True))
    await feed("item/completed", {"threadId": "orphan", "turnId": "o1", "item": {
        "type": "agentMessage", "id": "m-orphan", "text": "Not the persona."}})
    orphan = []
    while not said.empty():
        orphan.append(said.get_nowait())
    check("RECORD ...and one whose spawn was never seen still has a parent",
          ({e.get("parent_tool_use_id") for e in orphan
            if "parent_tool_use_id" in e},
           loam_thread_engine.Reply.of(orphan).segments),
          ({"codex-subagent:orphan"}, []))
    while not all_events.empty():
        all_events.get_nowait()
    await feed("thread/status/changed", {"threadId": "sub",
                                         "status": {"type": "idle"}})
    agent_close = []
    while not all_events.empty():
        agent_close.append(all_events.get_nowait())
    close_results = [b for e in agent_close if e.get("type") == "user"
                     and e.get("parent_tool_use_id") is None
                     for b in (e.get("message") or {}).get("content") or []
                     if b.get("type") == "tool_result"]
    check("HOLD finishing the helper emits the deferred Agent tool_result",
          (len(close_results), close_results[0]["tool_use_id"],
           "The receipt says it landed." in close_results[0]["content"],
           session.status(), session.meta()["background_tasks"], len(session.turns)),
          (1, "call_1", True, "working", 0, turns))
    session.unwatch(said)
    session.unwatch(all_events)
    await settle()
    await feed("turn/completed", {"threadId": "sub", "turn": {
        "id": "s1", "status": "completed", "items": []}})
    wake = turn_starts(before)
    text = (wake[0]["params"]["input"][0]["text"] if wake else "")
    check("HOLD ...one continuation turn/start, carrying what the helper said",
          (len(wake), wake[0]["params"]["threadId"] if wake else None,
           text.startswith("<task-notification>"),
           "The receipt says it landed." in text,
           "worker" in text, "held back" in text),
          (1, "th", True, True, True, True))
    check("HOLD ...which the transcript writer does not file as the owner's words",
          broker_mod.loam_transcript.person_text(text), "")
    await answer(wake, "u-cont")
    await feed("turn/started", {"threadId": "th", "turn": {"id": "u-cont"}})
    await feed("thread/tokenUsage/updated", {"threadId": "th", **usage(30000)})
    await feed("item/completed", {"threadId": "th", "turnId": "u-cont", "item": {
        "type": "agentMessage", "id": "m-cont", "text": "The worker says it landed."}})
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "u-cont", "status": "completed", "durationMs": 2500, "items": []}})
    check("HOLD ...continuation ends done, one turn on record, cumulative duration",
          (session.status(), len(session.turns), session.last_message,
           session.turns[-1]["context"]["used"], session.turns[-1]["duration_ms"]),
          ("done", turns + 1, "The worker says it landed.", 30000, 7500))
    check("HOLD stream saw one init, one result, and zero background_tasks",
          drawn_since(),
          [("system", "init"), ("result", "success")])

    # Two helpers: turn stays held until BOTH finish, then sends ONE
    # continuation carrying BOTH findings; if continuation says nothing, the
    # held draft is emitted as fallback so a reply is never dropped.
    before = len(sent)
    two_q: asyncio.Queue = asyncio.Queue()
    session.watch(two_q)
    await feed("turn/started", {"threadId": "th", "turn": {"id": "u2h"}})
    await feed("item/started", {"threadId": "th", "turnId": "u2h", "item": {
        "type": "subAgentActivity", "id": "c_critics", "kind": "started",
        "agentThreadId": "t_critics", "agentPath": "/root/critics"}})
    await feed("turn/started", {"threadId": "t_critics", "turn": {"id": "sc1"}})
    await feed("item/started", {"threadId": "th", "turnId": "u2h", "item": {
        "type": "subAgentActivity", "id": "c_readers", "kind": "started",
        "agentThreadId": "t_readers", "agentPath": "/root/readers"}})
    await feed("turn/started", {"threadId": "t_readers", "turn": {"id": "sr1"}})
    await feed("item/completed", {"threadId": "th", "turnId": "u2h", "item": {
        "type": "agentMessage", "id": "m-2h-draft", "phase": "final_answer",
        "text": "Fallback draft text."}})
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "u2h", "status": "completed", "durationMs": 1000, "items": []}})
    await feed("item/completed", {"threadId": "t_critics", "turnId": "sc1", "item": {
        "type": "agentMessage", "id": "mc1", "text": "Critics praised the worldbuilding."}})
    await feed("turn/completed", {"threadId": "t_critics", "turn": {
        "id": "sc1", "status": "completed", "items": []}})
    await settle()
    check("TWO-HELPER after 1st finishes: still held, zero continuation sent yet",
          (session.status(), len(turn_starts(before))), ("working", 0))
    await feed("item/completed", {"threadId": "t_readers", "turnId": "sr1", "item": {
        "type": "agentMessage", "id": "mr1", "text": "Readers loved the pacing."}})
    await feed("turn/completed", {"threadId": "t_readers", "turn": {
        "id": "sr1", "status": "completed", "items": []}})
    await settle()
    cont = turn_starts(before)
    cont_text = cont[0]["params"]["input"][0]["text"] if cont else ""
    check("TWO-HELPER after 2nd finishes: one continuation with BOTH helpers",
          (len(cont), "critics" in cont_text, "readers" in cont_text,
           "Critics praised the worldbuilding." in cont_text,
           "Readers loved the pacing." in cont_text,
           broker_mod.loam_transcript.person_text(cont_text)),
          (1, True, True, True, True, ""))
    await answer(cont, "u2h-cont")
    await feed("turn/started", {"threadId": "th", "turn": {"id": "u2h-cont"}})
    await feed("item/completed", {"threadId": "th", "turnId": "u2h-cont", "item": {
        "type": "agentMessage", "id": "m-empty", "text": ""}})
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "u2h-cont", "status": "completed", "durationMs": 500, "items": []}})
    two_events = []
    while not two_q.empty():
        two_events.append(two_q.get_nowait())
    session.unwatch(two_q)
    check("TWO-HELPER empty continuation falls back to held draft before result",
          (session.status(), session.last_message,
           any(e.get("type") == "broker.background_tasks" for e in two_events),
           sum(1 for e in two_events if e.get("type") == "result")),
          ("done", "Fallback draft text.", False, 1))
    drawn_since()

    # A buffered root message flushes immediately when helpers finish before
    # the root turn completes (or when the root emits a tool call).
    before = len(sent)
    flush_q: asyncio.Queue = asyncio.Queue()
    session.watch(flush_q)
    await feed("turn/started", {"threadId": "th", "turn": {"id": "u3"}})
    await feed("item/started", {"threadId": "th", "turnId": "u3", "item": {
        "type": "subAgentActivity", "id": "c3", "kind": "started",
        "agentThreadId": "sub3", "agentPath": "/root/compare"}})
    await feed("turn/started", {"threadId": "sub3", "turn": {"id": "s3"}})
    await feed("item/started", {"threadId": "th", "turnId": "u3", "item": {
        "type": "agentMessage", "id": "m3-mid"}})
    await feed("item/completed", {"threadId": "th", "turnId": "u3", "item": {
        "type": "agentMessage", "id": "m3-mid", "text": "Checking notes too."}})
    await feed("item/started", {"threadId": "th", "turnId": "u3", "item": {
        "type": "commandExecution", "id": "cmd3", "command": "ls"}})
    await feed("item/completed", {"threadId": "th", "turnId": "u3", "item": {
        "type": "commandExecution", "id": "cmd3", "status": "completed",
        "aggregatedOutput": "ok", "exitCode": 0}})
    await feed("turn/completed", {"threadId": "sub3", "turn": {
        "id": "s3", "status": "completed", "items": []}})
    await settle()
    check("a subagent ending inside the root's turn leaves the root working",
          (session.status(), session.meta()["background_tasks"]), ("working", 0))
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "u3", "status": "completed", "items": []}})
    await settle()
    flush_events = []
    while not flush_q.empty():
        flush_events.append(flush_q.get_nowait())
    session.unwatch(flush_q)
    check("...buffered text flushed before tool call and no continuation turn starts",
          (session.status(), len(turn_starts(before)),
           "Checking notes too." in json.dumps(flush_events)),
          ("done", 0, True))
    drawn_since()

    # After MAX_HOLD_CYCLES, still-running helpers are promoted to
    # background_tasks with clean helper names so the turn cannot loop forever.
    before = len(sent)
    prev_cap = session.MAX_HOLD_CYCLES
    session.MAX_HOLD_CYCLES = 0
    await feed("turn/started", {"threadId": "th", "turn": {"id": "ucap"}})
    await feed("item/started", {"threadId": "th", "turnId": "ucap", "item": {
        "type": "subAgentActivity", "id": "ccap", "kind": "started",
        "agentThreadId": "subcap", "agentPath": "/root/long_runner"}})
    await feed("turn/started", {"threadId": "subcap", "turn": {"id": "scap"}})
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "ucap", "status": "completed", "items": []}})
    check("MAX_HOLD_CYCLES promotes still-running helper to background_tasks with clean name",
          (session.status(), session.meta()["background_tasks"],
           session.background_tasks[0]["description"],
           session.task_started_at("subcap") is not None),
          ("waiting", 1, "long runner", True))
    await feed("item/completed", {"threadId": "subcap", "turnId": "scap", "item": {
        "type": "agentMessage", "id": "mcap", "text": "Finished late."}})
    await feed("turn/completed", {"threadId": "subcap", "turn": {
        "id": "scap", "status": "completed", "items": []}})
    await settle()
    wake_cap = turn_starts(before)
    await answer(wake_cap, "ucap-wake")
    await feed("turn/started", {"threadId": "th", "turn": {"id": "ucap-wake"}})
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "ucap-wake", "status": "completed", "items": []}})
    check("...promoted helper completion wakes root and returns to done",
          (len(wake_cap), session.status(), session.meta()["background_tasks"]),
          (1, "done", 0))
    session.MAX_HOLD_CYCLES = prev_cap
    drawn_since()

    # An MCP call can outlive its turn.
    before = len(sent)
    await feed("turn/started", {"threadId": "th", "turn": {"id": "u4"}})
    await feed("item/started", {"threadId": "th", "turnId": "u4", "item": {
        "type": "mcpToolCall", "id": "b1", "server": "engineer",
        "tool": "brief", "arguments": {}}})
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "u4", "status": "completed", "items": []}})
    check("LATE a root MCP call open at the turn's end is waiting, 1",
          (session.status(), session.meta()["background_tasks"]), ("waiting", 1))
    await feed("item/completed", {"threadId": "th", "turnId": "u4", "item": {
        "type": "mcpToolCall", "id": "b1", "server": "engineer", "tool": "brief",
        "status": "completed",
        "result": {"content": [{"type": "text", "text": "receipt: landed"}]}}})
    await settle()
    wake = turn_starts(before)
    check("LATE ...its result wakes the root, working, 0, carrying the result",
          (session.status(), session.meta()["background_tasks"], len(wake),
           "receipt: landed" in (wake[0]["params"]["input"][0]["text"]
                                 if wake else "")),
          ("working", 0, 1, True))
    await answer(wake, "u5")
    await feed("turn/started", {"threadId": "th", "turn": {"id": "u5"}})
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "u5", "status": "completed", "items": []}})
    check("LATE ...and that turn ends done", session.status(), "done")

    # A command can keep running after its turn returns.
    before = len(sent)
    drawn_since()
    await feed("turn/started", {"threadId": "th", "turn": {"id": "u6"}})
    await feed("item/started", {"threadId": "th", "turnId": "u6", "item": {
        "type": "commandExecution", "id": "exec-q", "cwd": "/w",
        "command": "/bin/bash -lc 'echo quick'"}})
    await feed("item/completed", {"threadId": "th", "turnId": "u6", "item": {
        "type": "commandExecution", "id": "exec-q", "cwd": "/w",
        "command": "/bin/bash -lc 'echo quick'", "status": "completed",
        "aggregatedOutput": "quick\n", "exitCode": 0}})
    await feed("item/started", {"threadId": "th", "turnId": "u6", "item": {
        "type": "commandExecution", "id": "exec-s", "cwd": "/w",
        "command": "/bin/bash -lc 'sleep 30 && echo the kestrel hovers'"}})
    await feed("item/started", {"threadId": "sub", "turnId": "s9", "item": {
        "type": "commandExecution", "id": "exec-sub", "cwd": "/w",
        "command": "/bin/bash -lc 'sleep 30'"}})
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "u6", "status": "completed", "items": []}})
    check("COMMAND a root command open at the turn's end is waiting, 1",
          (session.status(), session.meta()["background_tasks"]), ("waiting", 1))
    check("COMMAND ...named by its command, out of Codex's bash wrapper; "
          "one that closed inside the turn and a subagent's are not tasks",
          session.background_tasks,
          [{"task_id": "exec-s", "task_type": "local_bash",
            "description": "sleep 30 && echo the kestrel hovers"}])
    await feed("item/completed", {"threadId": "th", "turnId": "u6", "item": {
        "type": "commandExecution", "id": "exec-s", "cwd": "/w",
        "command": "/bin/bash -lc 'sleep 30 && echo the kestrel hovers'",
        "status": "completed", "aggregatedOutput": "the kestrel hovers\n",
        "exitCode": 0}})
    await settle()
    wake = turn_starts(before)
    text = wake[0]["params"]["input"][0]["text"] if wake else ""
    check("COMMAND ...its end wakes the root, working, 0, carrying its output",
          (session.status(), session.meta()["background_tasks"], len(wake),
           "the kestrel hovers" in text, "sleep 30 && echo" in text),
          ("working", 0, 1, True, True))
    await answer(wake, "u7")
    await feed("turn/started", {"threadId": "th", "turn": {"id": "u7"}})
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "u7", "status": "completed", "items": []}})
    check("COMMAND ...and that turn ends done, drawn as a Claude notification",
          (session.status(), drawn_since()),
          ("done", [("system", "init"), ("broker.background_tasks", 1),
                    ("result", "success"),
                    ("broker.background_tasks", 0),
                    ("system", "task_notification"),
                    ("system", "init"), ("result", "success")]))

    # A command a later turn finishes (the model polled it) wakes nothing.
    before = len(sent)
    await feed("turn/started", {"threadId": "th", "turn": {"id": "u8"}})
    await feed("item/started", {"threadId": "th", "turnId": "u8", "item": {
        "type": "commandExecution", "id": "exec-p", "command": "sleep 60"}})
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "u8", "status": "completed", "items": []}})
    await feed("turn/started", {"threadId": "th", "turn": {"id": "u9"}})
    await feed("item/completed", {"threadId": "th", "turnId": "u8", "item": {
        "type": "commandExecution", "id": "exec-p", "command": "sleep 60",
        "status": "completed", "aggregatedOutput": "", "exitCode": 0}})
    await settle()
    check("COMMAND one ending inside a later turn leaves the list, no wake",
          (session.status(), session.meta()["background_tasks"],
           len(turn_starts(before))), ("working", 0, 0))
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "u9", "status": "completed", "items": []}})
    check("COMMAND ...and that turn ends done", session.status(), "done")
    drawn_since()
    check("a command's description: the wrapper unwrapped, one line, cut",
          (broker_mod.command_description(["/bin/bash", "-lc", "ls\n  -la"]),
           broker_mod.command_description("python3 -c 'print(1)'"),
           len(broker_mod.command_description("echo " + "x" * 200))),
          ("ls -la", "python3 -c 'print(1)'", 60))

    # The writer files a Codex conversation under its thread id.
    state = root / "transcript-state"
    state.mkdir()
    filed = root / "Filed.md"
    filed.write_text('---\ntitle: "Filed by the writer"\n---\n', encoding="utf-8")
    (state / "th-77.json").write_text(json.dumps({"path": str(filed)}),
                                      encoding="utf-8")
    saved_state, broker_mod.adoption.TRANSCRIPT_STATE = (
        broker_mod.adoption.TRANSCRIPT_STATE, state)
    try:
        record = cfg.SESSION_DIR / "rec-77.json"
        record.write_text(json.dumps({"session_id": "rec-77", "agent": "probe",
                                      "provider": "codex", "thread_id": "th-77",
                                      "title": "Opening words…",
                                      "title_source": None}), encoding="utf-8")
        broker_mod.sync_titles()
        meta = json.loads(record.read_text(encoding="utf-8"))
        check("a Codex record takes the title filed under its thread id",
              (meta["title"], meta["title_source"]),
              ("Filed by the writer", "vault"))
    finally:
        broker_mod.adoption.TRANSCRIPT_STATE = saved_state
    status, body = await route(broker_mod.Server(broker_mod.Broker()), "POST",
                               "/v1/providers/codex/login/code", {"code": "x"})
    check("there is no code to paste back for a device login", status, 405)


async def live_cases(root: Path) -> None:
    codex.CODEX_HOME = root / "codex-home"
    codex.AUTH_PATH = codex.CODEX_HOME / "auth.json"
    server = broker_mod.Server(broker_mod.Broker())
    status, rec = await route(server, "POST", "/v1/sessions",
                              {"agent": "probe", "provider": "codex"})
    check("LIVE a Codex session starts: initialize, model/list, thread/start",
          (status, rec.get("provider"), bool(rec.get("thread_id"))),
          (201, "codex", True))
    session = server.broker.sessions[rec["session_id"]]
    queue: asyncio.Queue = asyncio.Queue()
    session.watch(queue)
    status, _ = await route(server, "POST",
                            f"/v1/sessions/{session.id}/messages",
                            {"text": "Say hi."})
    kinds = []
    while True:
        event = await asyncio.wait_for(queue.get(), 60)
        kinds.append((event["type"], event.get("subtype")))
        if event["type"] == "result":
            break
    check("LIVE with no login the turn opens and fails as a result",
          (kinds[0], kinds[1], kinds[-1]),
          (("broker.user_message", None), ("system", "init"),
           ("result", "error_during_execution")))
    check("LIVE ...and the row says failed", session.status(), "failed")
    await route(server, "DELETE", f"/v1/sessions/{session.id}")
    status, again = await route(server, "POST", "/v1/sessions",
                                {"agent": "probe", "session_id": session.id,
                                 "resume": True})
    check("LIVE a resume is thread/resume on the stored thread",
          (status, again.get("thread_id")), (201, rec["thread_id"]))
    await route(server, "DELETE", f"/v1/sessions/{session.id}")
    check("LIVE the persona's directory was not written",
          sorted(p.name for p in (cfg.AI_LABS / "probe").iterdir()),
          ["agent.json"])

    status, flow = await route(server, "POST", "/v1/providers/codex/login")
    check("LIVE the device login starts with a URL and a code",
          (status, flow["state"], flow["url"], bool(flow["code"])),
          (201, "pending", "https://auth.openai.com/codex/device", True))
    pid = server.broker.codex_login.proc.pid
    status, flow = await route(server, "DELETE", "/v1/providers/codex/login")
    await asyncio.sleep(1)
    left = subprocess.run(["pgrep", "-g", str(pid)], capture_output=True,
                          text=True).stdout.strip()
    check("LIVE ...and cancelling it leaves no process and no login",
          (flow["state"], left, codex.AUTH_PATH.exists()), ("idle", "", False))


root = Path(tempfile.mkdtemp(prefix="loam-broker-codex-test-"))
saved = {name: getattr(cfg, name) for name in
         ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "USAGE_PATH", "AI_LABS")}
saved_codex = {name: getattr(codex, name) for name in
               ("CODEX_HOME", "AUTH_PATH", "MODELS_PATH")}
try:
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.USAGE_PATH = cfg.STATE_DIR / "provider-usage.json"
    cfg.AI_LABS = root / "agents"
    (cfg.AI_LABS / "probe").mkdir(parents=True)
    (cfg.AI_LABS / "probe" / "agent.json").write_text("{}", encoding="utf-8")
    translator_cases()
    login_cases(root)
    device_cases()
    asyncio.run(offline_broker_cases(root))
    if LIVE:
        asyncio.run(live_cases(root))
finally:
    for name, value in saved.items():
        setattr(cfg, name, value)
    for name, value in saved_codex.items():
        setattr(codex, name, value)
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
