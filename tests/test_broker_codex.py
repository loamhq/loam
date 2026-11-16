#!/usr/bin/env python3
"""Codex as a provider: the translation, the login reading, the device-code
text, the model choices, the permission answers -- and, with ``--live``, the
real ``codex app-server`` and ``codex login --device-auth``.

What is proved offline:

  1. ``loam_codex.Translator``: each app-server notification becomes the
     Claude CLI event the page already draws (see the table in
     lib/loam_codex.py), and the ``result`` carries the context figures
  2. ``login_state`` from an ``auth.json``: absent, a ChatGPT login (email
     and plan out of the id token, no token answered), and an unreadable one
  3. the URL and the one-time code out of what the CLI prints
  4. ``choices`` for a Codex chat refuses what ``model/list`` did not offer
  5. a Codex approval becomes a ``broker.permission_request`` and the
     existing route answers it as JSON-RPC

``--live`` starts real processes against a throwaway ``CODEX_HOME`` and a
throwaway agent directory, and spends nothing: with no login, a turn fails at
OpenAI with a 401 and that failure is what is checked. It also mints one
device code and cancels it, which is a request to OpenAI and why it is not
the default.

Run: python3 tests/test_broker_codex.py [--live]
"""

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
# Admission reads no real box here: a fixture session must not be refused
# because the machine running the suite is short of memory (a landing
# got 429 for one, 2026-11-09). test_broker_seats is where admission is tested.
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
    # A web search fills in at completion: the real one from 2026-10-02,
    # which the page drew as `webSearch {"query":"","action":null,...}`.
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


# What 0.159.3 printed on this box on 2026-10-01, with the code replaced.
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
    # The MCP approval, as a persona's thread 01a0f8a0 met it four times on
    # 2026-10-01: refused by this client then, and read by the model as
    # "user rejected MCP tool call". Now a card, and only the page answers.
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
    # Answered in the one shape since 2026-10-01 (tests/test_broker_questions.py
    # holds the rest); the broker puts it back in Codex's.
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

    # The order a signed-in turn really sends (2026-10-01, thread 01a0f895):
    # a request's tokenUsage arrives AFTER the message it produced closed.
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

    # A subagent is a background task (2026-10-02). The shape of a persona's chat
    # 7bcccd28: the root spawns one, the subagent opens an engineer call, the
    # root ends its turn, and the subagent is still on the call.
    session.thread_id = "th"
    queue: asyncio.Queue = asyncio.Queue()
    session.watch(queue)

    async def feed(method, params):
        await session._handle({"jsonrpc": "2.0", "method": method,
                               "params": params})
    await feed("turn/started", {"threadId": "th", "turn": {"id": "u2"}})
    await feed("item/completed", {"threadId": "th", "turnId": "u2", "item": {
        "type": "subAgentActivity", "id": "call_1", "kind": "started",
        "agentThreadId": "sub", "agentPath": "/root/worker"}})
    await feed("turn/started", {"threadId": "sub", "turn": {"id": "s1"}})
    check("a subagent's turn opening is a background task, the root's turn stays",
          (session.status(), session.meta()["background_tasks"],
           session._codex.turn_id), ("working", 1, "u2"))
    await feed("thread/tokenUsage/updated", {"threadId": "th", **usage(30000)})
    await feed("thread/tokenUsage/updated", {"threadId": "sub", **usage(90000)})
    await feed("item/started", {"threadId": "sub", "turnId": "s1", "item": {
        "type": "mcpToolCall", "id": "x1", "server": "engineer",
        "tool": "brief", "arguments": {}}})
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "u2", "status": "completed", "items": []}})
    check("the root's turn ending with the subagent on a call is waiting, 1",
          (session.status(), session.meta()["background_tasks"],
           session.turns[-1]["context"]["used"]), ("waiting", 1, 30000))
    turns = len(session.turns)
    await feed("item/completed", {"threadId": "sub", "turnId": "s1", "item": {
        "type": "mcpToolCall", "id": "x1", "server": "engineer",
        "tool": "brief", "status": "completed", "result": {"content": []}}})
    check("...its tool call closes and the session still waits",
          session.status(), "waiting")

    # A subagent ending after the root's turn wakes the root (2026-10-09),
    # as a Claude task notification wakes a Claude persona. Before, its
    # report sat unread until the person's next message (proved 10-02). The
    # order is the one measured on 0.159.3: its message, its idle, then its
    # `turn/completed`.
    session.WAKE_GATHER_S = 0

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

    before = len(sent)
    said: asyncio.Queue = asyncio.Queue()
    session.watch(said)
    last_before = session.last_message
    await feed("item/completed", {"threadId": "sub", "turnId": "s1", "item": {
        "type": "agentMessage", "id": "m-sub", "text": "The receipt says it landed."}})
    # The record is Loam's (2026-11-13): a subagent's words are nested under
    # the call that spawned it, never the persona's. Until today they went
    # out with a null parent, and the thread engine posts top-level text as
    # the persona's reply.
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
    session.unwatch(said)
    await feed("thread/status/changed", {"threadId": "sub",
                                         "status": {"type": "idle"}})
    check("WAKE the subagent ending after the root's turn: working, 0, no turn yet",
          (session.status(), session.meta()["background_tasks"],
           len(session.turns)), ("working", 0, turns))
    await settle()
    await feed("turn/completed", {"threadId": "sub", "turn": {
        "id": "s1", "status": "completed", "items": []}})
    wake = turn_starts(before)
    text = (wake[0]["params"]["input"][0]["text"] if wake else "")
    check("WAKE ...one root turn/start, carrying what the subagent last said",
          (len(wake), wake[0]["params"]["threadId"] if wake else None,
           text.startswith("<task-notification>"),
           "The receipt says it landed." in text, "/root/worker" in text),
          (1, "th", True, True, True))
    check("WAKE ...which the transcript writer does not file as the owner's words",
          broker_mod.loam_transcript.person_text(text), "")
    await answer(wake, "u-wake")
    await feed("turn/started", {"threadId": "th", "turn": {"id": "u-wake"}})
    await feed("item/completed", {"threadId": "th", "turnId": "u-wake", "item": {
        "type": "agentMessage", "id": "m-wake", "text": "The worker says it landed."}})
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "u-wake", "status": "completed", "items": []}})
    check("WAKE ...and the woken turn ends done, one turn on the record",
          (session.status(), len(session.turns), session.last_message),
          ("done", turns + 1, "The worker says it landed."))
    check("WAKE the page is told what a Claude task notification tells it",
          drawn_since(),
          [("system", "init"), ("broker.background_tasks", 1),
           ("result", "success"),
           ("broker.background_tasks", 0), ("system", "task_notification"),
           ("system", "init"), ("result", "success")])

    # The earlier shape (seq 1166 of 7bcccd28): the subagent finishes while
    # the root is still working, and the root's turn is not closed by it.
    # Nothing new starts: Codex hands the report to the turn in flight.
    before = len(sent)
    await feed("turn/started", {"threadId": "th", "turn": {"id": "u3"}})
    await feed("turn/started", {"threadId": "sub", "turn": {"id": "s2"}})
    await feed("turn/completed", {"threadId": "sub", "turn": {
        "id": "s2", "status": "completed", "items": []}})
    await settle()
    check("a subagent ending inside the root's turn leaves the root working",
          (session.status(), session.meta()["background_tasks"]), ("working", 0))
    await feed("turn/completed", {"threadId": "th", "turn": {
        "id": "u3", "status": "completed", "items": []}})
    await settle()
    check("...until the root's own turn ends, and no turn is started for it",
          (session.status(), len(turn_starts(before))), ("done", 0))
    drawn_since()

    # A root MCP call that outlives its turn (a persona's a9c8ec2c, 10-09: a
    # `brief` left running in a code cell when the turn ended, 23 minutes of
    # `done`/0, its receipt landing on a closed turn nobody read).
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

    # A root command that outlives its turn (probe f35cc554, 10-09: a code
    # cell yields after 30 s and leaves its `sleep 30` running; the row read
    # `done`/0 and the output reached nobody).
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
