#!/usr/bin/env python3
"""Resolve pending permission requests on the stream and keep waiting sessions alive."""

import asyncio
import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import loam_broker as cfg  # noqa: E402
# Use fixture memory to isolate the broker from machine load.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(ROOT / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:62} got={got!r}")
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
    """A CLI that ends when it is signalled, as the real one does on SIGTERM."""

    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.returncode = None
        self.pid = 0

    def terminate(self) -> None:
        self.returncode = 143

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode


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


def closings(session) -> list[tuple]:
    return [(e["request_id"], e["behavior"]) for e in events(session)
            if e["type"] == "broker.permission_resolved"]


SUGGESTION = {"type": "addRules", "behavior": "allow", "destination": "session",
              "rules": [{"toolName": "Read",
                         "ruleContent": "//home/loam/agents/**"}]}


def server_with(*sessions) -> "broker_mod.Server":
    broker = broker_mod.Broker()
    for session in sessions:
        broker.sessions[session.id] = session
    return broker_mod.Server(broker)


async def asking(request_id: str = "req-1",
                 tool: str = "Read") -> "broker_mod.Session":
    """A running session whose CLI has just asked, through `_handle` as the
    real request arrives."""
    session = broker_mod.Session("probe")
    session.state = "running"
    session.proc = FakeProc()
    session._turn_active = True
    await session._handle({"type": "control_request", "request_id": request_id,
                           "request": {"subtype": "can_use_tool",
                                       "tool_name": tool,
                                       "input": {"file_path": "/elsewhere/x"},
                                       "permission_suggestions": [SUGGESTION],
                                       "tool_use_id": "toolu_1"}})
    return session


async def main() -> None:
    hour = cfg.PERMISSION_WAIT_S

    # -- expiry: denied with the sentence, the process lives ---------------
    session = await asking()
    server = server_with(session)
    await server.broker.reap_once(time.time() + hour + 1)
    sent = session.proc.stdin.lines
    check("the reaper answered the CLI", len(sent), 1)
    response = sent[0]["response"]
    check("...for this request", response["request_id"], "req-1")
    check("...with a deny", response["response"]["behavior"], "deny")
    check("...carrying the sentence", response["response"]["message"],
          "Nobody answered this request within an hour. "
          f"Ask {cfg.lc.OWNER_NAME} in words, or go on without it.")
    check("the process was not ended", (session.state, session.proc.returncode),
          ("running", None))
    check("the log says expired", closings(session), [("req-1", "expired")])
    check("...with the same sentence as its message",
          events(session)[-1]["message"], cfg.PERMISSION_EXPIRED_MESSAGE)
    check("...and the request's kind", events(session)[-1]["kind"], "approval")
    check("nothing is pending", session.meta()["pending_permissions"], [])
    check("no broker.exit", [e for e in events(session)
                             if e["type"] == "broker.exit"], [])
    await session._handle({"type": "result", "subtype": "success",
                           "is_error": False, "result": "went on without it"})
    check("the turn then completes on its own", session.status(), "done")
    status, body = await route(server, "POST",
                               f"/v1/sessions/{session.id}/permissions/req-1",
                               {"behavior": "allow"})
    check("a late answer to it is a 404", status, 404)
    check("...with the sentence the page reads", body["error"],
          "no permission request req-1 is pending")
    # The idle clock runs from the deny: the next pass an hour on ends it,
    # the one before does not.
    await server.broker.reap_once(session.last_activity + 60)
    check("idle clock restarted at the deny: not reaped a minute later",
          session.state, "running")

    # -- a question expires the same way -----------------------------------
    question = await asking("req-q", "AskUserQuestion")
    question.pending["req-q"]["questions"] = [{"id": "q", "question": "q?"}]
    await server_with(question).broker.reap_once(time.time() + hour + 1)
    check("a question is denied with the sentence too",
          question.proc.stdin.lines[0]["response"]["response"]["message"],
          cfg.PERMISSION_EXPIRED_MESSAGE)
    check("...and closes as an expired question",
          (events(question)[-1]["behavior"], events(question)[-1]["kind"]),
          ("expired", "question"))

    # -- exempt: asking under the limit, idle far past the idle limit ------
    session = await asking()
    session.last_activity = time.time() - 10 * cfg.CLASSIC_IDLE_S
    session.pending["req-1"]["raised_at"] = time.time() - (hour - 120)
    await server_with(session).broker.reap_once(time.time())
    check("a session that is asking is not reaped", session.state, "running")
    check("...and nothing went to its CLI", session.proc.stdin.lines, [])
    check("...and the request is still pending", list(session.pending), ["req-1"])
    idle = broker_mod.Session("probe")
    idle.state = "running"
    idle.proc = FakeProc()
    idle.last_activity = time.time() - cfg.CLASSIC_IDLE_S - 1
    await server_with(idle).broker.reap_once(time.time())
    check("the same age with nothing pending is reaped",
          (idle.state, idle.proc.returncode), ("exited", 143))

    # -- ended: the reaper, when it does end a process ---------------------
    # A session asking past both limits has its request expired, not its
    # process ended; one that crashes with a request open is reaped by
    # `_reap` when the pipe closes.
    session = await asking()
    session.proc.returncode = 1
    await session._reap()
    kinds = [e["type"] for e in events(session)]
    check("a crash closes the request as ended", closings(session),
          [("req-1", "ended")])
    check("...before broker.exit", kinds[-2:],
          ["broker.permission_resolved", "broker.exit"])

    # -- ended: DELETE ------------------------------------------------------
    session = await asking()
    server = server_with(session)
    status, _ = await route(server, "DELETE", f"/v1/sessions/{session.id}")
    check("DELETE answers", status, 200)
    check("...and the request closes as ended", closings(session),
          [("req-1", "ended")])
    check("...and nothing was sent to a CLI that is going",
          session.proc.stdin.lines, [])
    await session._reap()   # the pump reads the closed pipe afterwards
    kinds = [e["type"] for e in events(session)]
    check("...once, before broker.exit",
          kinds[1:], ["broker.permission_resolved", "broker.exit"])
    status, body = await route(server, "POST",
                               f"/v1/sessions/{session.id}/permissions/req-1",
                               {"behavior": "allow"})
    check("an answer after DELETE is a 404 with the sentence",
          (status, body["error"]),
          (404, "no permission request req-1 is pending"))

    # -- ended: the broker's own shutdown ----------------------------------
    # `amain` stops every live session and returns; the pump that would have
    # run `_reap` may never be scheduled again, so `stop()` alone must leave
    # the log closed.
    sessions = [await asking("req-a"), await asking("req-b")]
    for live in broker_mod.Broker.live(server_with(*sessions).broker):
        await live.stop()
    check("shutdown closes every live session's request",
          [closings(s) for s in sessions],
          [[("req-a", "ended")], [("req-b", "ended")]])

    # -- cancelled: the CLI withdrew it ------------------------------------
    session = await asking()
    await session._handle({"type": "control_cancel_request",
                           "request_id": "req-1"})
    check("a cancel closes the request on the stream", closings(session),
          [("req-1", "cancelled")])
    resolved = [e for e in events(session)
                if e["type"] == "broker.permission_resolved"]
    check("...with the request's kind", resolved[0]["kind"], "approval")
    check("...and it is no longer pending", session.pending, {})
    await session._handle({"type": "control_cancel_request",
                           "request_id": "req-1"})
    check("a second cancel for it writes nothing", len(closings(session)), 1)

    # -- cancelled: Codex withdrew it --------------------------------------
    # `serverRequest/resolved` names the JSON-RPC id Codex asked with; the
    # card is keyed `codex-<id>`.
    codex_session = broker_mod.CodexSession("probe")
    sent: list[dict] = []

    async def send_raw(obj):
        sent.append(obj)
    codex_session.send_raw = send_raw
    codex_session.state = "running"
    await codex_session._handle({"jsonrpc": "2.0", "id": 7,
                                 "method": "item/commandExecution/requestApproval",
                                 "params": {"threadId": "th", "turnId": "tu",
                                            "itemId": "c1", "command": "ls",
                                            "cwd": "/w", "startedAtMs": 0}})
    check("a Codex approval is pending", list(codex_session.pending), ["codex-7"])
    await codex_session._handle({"jsonrpc": "2.0",
                                 "method": "serverRequest/resolved",
                                 "params": {"threadId": "th", "requestId": 7}})
    check("Codex's serverRequest/resolved closes it as cancelled",
          closings(codex_session), [("codex-7", "cancelled")])
    check("...with the request's kind",
          events(codex_session)[-1]["kind"], "approval")
    check("...and it is no longer pending", codex_session.pending, {})
    check("...and nothing was answered to Codex", sent, [])
    await codex_session._handle({"jsonrpc": "2.0",
                                 "method": "serverRequest/resolved",
                                 "params": {"threadId": "th", "requestId": 7}})
    check("a second serverRequest/resolved for it writes nothing",
          len(closings(codex_session)), 1)

    # -- updatedPermissions -------------------------------------------------
    session = await asking()
    server = server_with(session)
    path = f"/v1/sessions/{session.id}/permissions/req-1"
    local = dict(SUGGESTION, destination="localSettings")
    status, body = await route(server, "POST", path, {
        "behavior": "allow", "updatedPermissions": [SUGGESTION, local]})
    check("a localSettings suggestion is refused", status, 400)
    check("...nothing reached the CLI", session.proc.stdin.lines, [])
    status, _ = await route(server, "POST", path, {
        "behavior": "deny", "updatedPermissions": [SUGGESTION]})
    check("rules on a deny are refused", status, 400)
    status, _ = await route(server, "POST", path, {
        "behavior": "allow", "updatedPermissions": [SUGGESTION]})
    check("an allow with the session suggestion is accepted", status, 200)
    response = session.proc.stdin.lines[0]["response"]["response"]
    check("...the rules reach the CLI unaltered",
          response["updatedPermissions"], [SUGGESTION])
    check("...beside updatedInput", response["updatedInput"],
          {"file_path": "/elsewhere/x"})
    check("...and the event says the allow was for the chat",
          (events(session)[-1]["behavior"], events(session)[-1]["scope"]),
          ("allow", "session"))
    session = await asking()
    await route(server_with(session), "POST",
                f"/v1/sessions/{session.id}/permissions/req-1",
                {"behavior": "allow"})
    check("a plain allow carries no rules",
          "updatedPermissions" in session.proc.stdin.lines[0]["response"]["response"],
          False)
    check("...and claims no scope", "scope" in events(session)[-1], False)


root = Path(tempfile.mkdtemp(prefix="loam-broker-test-"))
saved = (cfg.SESSION_DIR, cfg.AI_LABS)
try:
    cfg.SESSION_DIR = root / "sessions"
    cfg.AI_LABS = root / "agents"
    (cfg.AI_LABS / "probe").mkdir(parents=True)
    (cfg.AI_LABS / "probe" / "agent.json").write_text("{}", encoding="utf-8")
    asyncio.run(main())
finally:
    cfg.SESSION_DIR, cfg.AI_LABS = saved
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
