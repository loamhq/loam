#!/usr/bin/env python3
"""A question a provider asks, in one shape out and the vendor's shape back.

Three formats ask the person something (lib/loam_questions.py): Claude
Code's ``AskUserQuestion`` (CLI 2.1.281), Codex's ``item/tool/requestUserInput``
and a Codex form ``mcpServer/elicitation/request`` (app-server 0.159.3). Each
reaches the page as ``broker.permission_request`` with ``kind: "question"``
and the questions in one shape, and the page's ``answers`` go back as the
vendor's own message. What is asserted is the exact message put on the wire,
against a session with a stand-in process, not that a mapper exists:

  - each format in: the event's ``kind``, ``message`` and ``questions``;
  - each format out: the control_response / JSON-RPC result, byte for byte,
    including a multi-select with a typed value, a secret, an elicitation
    with an enum, a titled oneOf, a boolean, a number and an integer;
  - the deny path of each, in that format's words;
  - an allow with no ``answers`` is a 400 through the route, nothing is sent,
    and the card is still answerable;
  - ``broker.permission_resolved`` carries ``kind`` and the answers;
  - Codex's own MCP tool-call approval (an empty form) stays an approval.

Run: python3 tests/test_broker_questions.py
"""

import asyncio
import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
# Admission reads no real box here: a fixture session must not be refused
# because the machine running the suite is short of memory (a landing
# got 429 for one, 2026-11-09). test_broker_seats is where admission is tested.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} got={got!r}"[:300])
    if not ok:
        print(f"      wanted {want!r}")


class FakeStdin:
    def __init__(self) -> None:
        self.lines: list[dict] = []

    def write(self, data: bytes) -> None:
        self.lines.append(json.loads(data.decode()))

    async def drain(self) -> None:
        return None


class FakeProc:
    returncode = None

    def __init__(self) -> None:
        self.stdin = FakeStdin()


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


def events(session) -> list[dict]:
    return [json.loads(line) for line in
            session.log_path.read_text(encoding="utf-8").splitlines() if line]


# The input as the model writes it (CLI 2.1.281's schema): questions keyed
# by their text, `multiSelect`, an option `preview`.
ASK = {"questions": [
    {"question": "What should the plugin be called?", "header": "Name",
     "options": [{"label": "Parlance (Recommended)", "description": "Short.",
                  "preview": "parlance/"},
                 {"label": "Lexicon", "description": "Plain."}],
     "multiSelect": False},
    {"question": "Which features do you want?", "header": "Features",
     "options": [{"label": "Search", "description": "Find."},
                 {"label": "Sync, both ways", "description": "Mirror."}],
     "multiSelect": True}]}
NAME, FEATURES = (q["question"] for q in ASK["questions"])


def claude_session() -> "broker_mod.Session":
    session = broker_mod.Session("probe")
    session.state = "running"
    session.proc = FakeProc()
    return session


async def ask(session, request_id: str = "req-q") -> dict:
    await session._handle({"type": "control_request", "request_id": request_id,
                           "request": {"subtype": "can_use_tool",
                                       "tool_name": "AskUserQuestion",
                                       "input": ASK, "tool_use_id": "toolu_1"}})
    return events(session)[-1]


async def claude_cases() -> None:
    session = claude_session()
    event = await ask(session)
    check("AskUserQuestion is a question, not an approval", event["kind"], "question")
    # A thread's opening snapshot reads these (2026-11-08): the event exactly
    # as it went out, seq and all.
    check("...kept as pending_events(), the event as the log has it",
          session.pending_events(), [event])
    check("...its questions in the one shape", event["questions"], [
        {"id": NAME, "header": "Name", "question": NAME,
         "options": [{"label": "Parlance (Recommended)", "description": "Short.",
                      "preview": "parlance/"},
                     {"label": "Lexicon", "description": "Plain.", "preview": None}],
         "multi": False, "other": True, "secret": False, "required": True},
        {"id": FEATURES, "header": "Features", "question": FEATURES,
         "options": [{"label": "Search", "description": "Find.", "preview": None},
                     {"label": "Sync, both ways", "description": "Mirror.",
                      "preview": None}],
         "multi": True, "other": True, "secret": False, "required": True}])
    check("...and the old fields are still there for an older page",
          (event["tool_name"], event["input"]), ("AskUserQuestion", ASK))

    # An older page: allow with no answers. A 400 naming the field, nothing
    # sent, and the card still pending.
    server = broker_mod.Server(broker_mod.Broker())
    server.broker.sessions[session.id] = session
    status, body = await route(server, "POST",
                               f"/v1/sessions/{session.id}/permissions/req-q",
                               {"behavior": "allow"})
    check("allow with no answers is a 400", status, 400)
    check("...naming the missing field", "`answers`" in body["error"], True)
    check("...nothing reached the CLI", session.proc.stdin.lines, [])
    check("...and the card is still answerable", list(session.pending), ["req-q"])
    status, body = await route(server, "POST",
                               f"/v1/sessions/{session.id}/permissions/req-q",
                               {"behavior": "allow",
                                "answers": {NAME: ["Lexicon", "Parlance (Recommended)"]}})
    check("two answers to a single-select is a 400", status, 400)

    # A multi-select with a typed value, through the route.
    status, body = await route(server, "POST",
                               f"/v1/sessions/{session.id}/permissions/req-q",
                               {"behavior": "allow",
                                "answers": {NAME: ["Parlance (Recommended)"],
                                            FEATURES: ["Sync, both ways",
                                                       "an offline cache"]}})
    check("answered, the route says so", (status, body),
          (200, {"answered": "req-q", "behavior": "allow"}))
    check("the exact control_response the CLI is sent",
          session.proc.stdin.lines[-1],
          {"type": "control_response",
           "response": {"subtype": "success", "request_id": "req-q",
                        "response": {"behavior": "allow", "updatedInput": {
                            **ASK, "answers": {
                                NAME: "Parlance (Recommended)",
                                FEATURES: ["Sync, both ways", "an offline cache"]}}}}})
    resolved = events(session)[-1]
    check("resolved carries kind and the answers",
          (resolved["type"], resolved["kind"], resolved["answers"]),
          ("broker.permission_resolved", "question",
           {NAME: ["Parlance (Recommended)"],
            FEATURES: ["Sync, both ways", "an offline cache"]}))

    # Deny: declined in the CLI's words, not "Denied from the client."
    session = claude_session()
    await ask(session, "req-d")
    await session.answer_permission("req-d", "deny")
    check("a declined question, in the CLI's words",
          session.proc.stdin.lines[-1]["response"]["response"],
          {"behavior": "deny", "message": "User declined to answer questions"})
    check("...resolved as a declined question",
          (events(session)[-1]["kind"], "answers" in events(session)[-1]),
          ("question", False))

    # An ordinary tool call is untouched.
    session = claude_session()
    await session._handle({"type": "control_request", "request_id": "req-r",
                           "request": {"subtype": "can_use_tool", "tool_name": "Read",
                                       "input": {"file_path": "/x"}}})
    event = events(session)[-1]
    check("a tool call is an approval with no questions",
          (event["kind"], "questions" in event), ("approval", False))
    await session.answer_permission("req-r", "allow")
    check("...and allowed exactly as before",
          session.proc.stdin.lines[-1]["response"]["response"],
          {"behavior": "allow", "updatedInput": {"file_path": "/x"}})


def codex_session():
    session = broker_mod.CodexSession("probe")
    session.state = "running"
    sent: list[dict] = []

    async def send_raw(obj):
        sent.append(obj)
    session.send_raw = send_raw
    return session, sent


async def codex_input_cases() -> None:
    session, sent = codex_session()
    params = {"threadId": "th", "turnId": "tu", "itemId": "i1", "isBlocking": True,
              "questions": [
                  {"id": "db", "header": "Database", "question": "Which one?",
                   "options": [{"label": "Postgres", "description": "Rows."},
                               {"label": "SQLite", "description": "A file."}],
                   "isOther": True, "isSecret": False},
                  {"id": "key", "header": "API key", "question": "Paste the key.",
                   "options": None, "isOther": False, "isSecret": True}]}
    await session._handle({"jsonrpc": "2.0", "id": 21,
                           "method": "item/tool/requestUserInput", "params": params})
    event = events(session)[-1]
    check("requestUserInput is a question", event["kind"], "question")
    check("...kept as pending_events() on Codex too", session.pending_events(), [event])
    check("...mapped field for field; no options is free text, a secret is secret",
          event["questions"], [
              {"id": "db", "header": "Database", "question": "Which one?",
               "options": [{"label": "Postgres", "description": "Rows.", "preview": None},
                           {"label": "SQLite", "description": "A file.", "preview": None}],
               "multi": False, "other": True, "secret": False, "required": True},
              {"id": "key", "header": "API key", "question": "Paste the key.",
               "options": [], "multi": False, "other": True, "secret": True,
               "required": True}])
    try:
        await session.answer_permission("codex-21", "allow", answers={"db": ["SQLite"]})
        got = None
    except broker_mod.BrokerError as exc:
        got = exc.status
    check("an unanswered question is a 400, and nothing is sent",
          (got, sent, list(session.pending)), (400, [], ["codex-21"]))
    await session.answer_permission("codex-21", "allow",
                                    answers={"db": ["CockroachDB"], "key": ["sk-123"]})
    check("...and gone from pending_events() once answered", session.pending_events(), [])
    check("the exact JSON-RPC result Codex is sent (a typed value, a secret)",
          sent[-1], {"jsonrpc": "2.0", "id": 21, "result": {"answers": {
              "db": {"answers": ["CockroachDB"]},
              "key": {"answers": ["sk-123"]}}}})
    check("the secret's answer is not on the logged resolved event",
          (events(session)[-1]["answers"],
           "sk-123" in session.log_path.read_text(encoding="utf-8")),
          ({"db": ["CockroachDB"], "key": None}, False))
    await session._handle({"jsonrpc": "2.0", "id": 22,
                           "method": "item/tool/requestUserInput", "params": params})
    await session.answer_permission("codex-22", "deny")
    check("a deny is no answers (the schema has no decline)",
          sent[-1], {"jsonrpc": "2.0", "id": 22, "result": {"answers": {}}})


ELICIT = {"serverName": "deployer", "threadId": "th", "turnId": "tu",
          "mode": "form", "message": "Configure the release.",
          "requestedSchema": {"type": "object", "required": ["env", "confirm"],
                              "properties": {
    "env": {"type": "string", "title": "Environment",
            "description": "Where to?", "enum": ["staging", "prod"]},
    "region": {"type": "string", "description": "Which region?",
               "oneOf": [{"const": "eu-1", "title": "Europe"},
                         {"const": "us-1", "title": "America"}]},
    "confirm": {"type": "boolean", "title": "Confirm",
                "description": "Really ship?"},
    "replicas": {"type": "integer", "title": "Replicas"},
    "ratio": {"type": "number", "title": "Ratio"},
    "tags": {"type": "array", "title": "Tags",
             "items": {"type": "string", "enum": ["a", "b", "c"]}},
    "note": {"type": "string", "title": "Note"}}}}


async def elicitation_cases() -> None:
    session, sent = codex_session()
    await session._handle({"jsonrpc": "2.0", "id": 31,
                           "method": "mcpServer/elicitation/request", "params": ELICIT})
    event = events(session)[-1]
    check("a form elicitation with fields is a question",
          (event["kind"], event["message"]), ("question", "Configure the release."))
    qs = {q["id"]: q for q in event["questions"]}
    check("an enum is options, title as header, description as question",
          (qs["env"]["header"], qs["env"]["question"],
           [o["label"] for o in qs["env"]["options"]], qs["env"]["other"],
           qs["env"]["required"]),
          ("Environment", "Where to?", ["staging", "prod"], False, True))
    check("a oneOf is its titles; no title is the property name",
          (qs["region"]["header"], [o["label"] for o in qs["region"]["options"]],
           qs["region"]["required"]),
          ("region", ["Europe", "America"], False))
    check("a boolean is two options",
          [o["label"] for o in qs["confirm"]["options"]], ["Yes", "No"])
    check("a number is free text",
          (qs["replicas"]["options"], qs["replicas"]["other"]), ([], True))
    check("an array of enum is a multi-select", qs["tags"]["multi"], True)

    await session.answer_permission("codex-31", "allow", answers={
        "env": ["prod"], "region": ["Europe"], "confirm": ["No"],
        "replicas": ["3"], "ratio": ["0.5"], "tags": ["a", "c"]})
    check("the exact result: each value cast back to its schema type", sent[-1],
          {"jsonrpc": "2.0", "id": 31, "result": {"action": "accept", "content": {
              "env": "prod", "region": "eu-1", "confirm": False,
              "replicas": 3, "ratio": 0.5, "tags": ["a", "c"]}}})

    await session._handle({"jsonrpc": "2.0", "id": 32,
                           "method": "mcpServer/elicitation/request", "params": ELICIT})
    before = len(sent)
    for label, answers in (
            ("an integer that does not parse is a 400",
             {"env": ["prod"], "confirm": ["Yes"], "replicas": ["three"]}),
            ("a value outside an enum with no other is a 400",
             {"env": ["dev"], "confirm": ["Yes"]}),
            ("a required field left out is a 400", {"env": ["prod"]})):
        try:
            await session.answer_permission("codex-32", "allow", answers=answers)
            got = None
        except broker_mod.BrokerError as exc:
            got = exc.status
        check(label, got, 400)
    check("...and none of them sent anything", len(sent), before)
    await session.answer_permission("codex-32", "deny")
    check("a deny is decline", sent[-1],
          {"jsonrpc": "2.0", "id": 32, "result": {"action": "decline"}})

    # Codex's own approval before an MCP call: an empty form, an approval.
    approval = {"serverName": "engineer", "threadId": "th", "turnId": "tu",
                "mode": "form", "message": "Allow the engineer server to run brief?",
                "requestedSchema": {"type": "object", "properties": {}},
                "_meta": {"codex_approval_kind": "mcp_tool_call"}}
    await session._handle({"jsonrpc": "2.0", "id": 33,
                           "method": "mcpServer/elicitation/request", "params": approval})
    check("Codex's MCP tool-call approval stays an approval",
          (events(session)[-1]["kind"], "questions" in events(session)[-1]),
          ("approval", False))
    await session.answer_permission("codex-33", "allow")
    check("...accepted as before", sent[-1]["result"], {"action": "accept", "content": {}})


root = Path(tempfile.mkdtemp(prefix="loam-broker-questions-test-"))
saved = {name: getattr(cfg, name) for name in ("STATE_DIR", "SESSION_DIR", "AI_LABS")}
try:
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.AI_LABS = root / "agents"
    (cfg.AI_LABS / "probe").mkdir(parents=True)
    (cfg.AI_LABS / "probe" / "agent.json").write_text("{}", encoding="utf-8")
    asyncio.run(claude_cases())
    asyncio.run(codex_input_cases())
    asyncio.run(elicitation_cases())
finally:
    for name, value in saved.items():
        setattr(cfg, name, value)
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
