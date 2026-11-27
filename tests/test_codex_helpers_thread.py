#!/usr/bin/env python3
"""End-to-end thread test over the real SessionHost <-> SessionSocket <->
RemoteSession split for Codex in-turn helpers, live step hydration,
open-turn protection against premature PARKED_CHECK_S exit, and silent
empty notification turns.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-codex-helpers-"))
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
for who in ("henry", "ada"):
    ws = TMP / "ai-labs" / who
    ws.mkdir(parents=True)
    (ws / "agent.json").write_text(json.dumps({
        "name": who.capitalize(),
        "provider": "codex" if who == "henry" else "claude-code",
        "model": "gpt-6.2-sol" if who == "henry" else "sonnet",
    }))
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
cfg.SESSION_DIR = TMP / "sessions"
cfg.SESSION_DIR.mkdir(parents=True)
cfg.VAULT = TMP / "vault"
cfg.ATTACHMENT_ROOT = cfg.VAULT / "98_ATTACHMENTS"
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}

import loam_threads as threads  # noqa: E402
import loam_thread_engine as engine_mod  # noqa: E402
import loam_thread_steps as steps_mod  # noqa: E402
from loam_provider_session import CodexSession, Session  # noqa: E402
from loam_session_client import RemoteRegistry  # noqa: E402
from loam_session_host import SessionHost, own_session_store  # noqa: E402
from loam_session_socket import listen  # noqa: E402

cfg.roster = threads.roster
cfg.roster_cards = lambda: [{"id": p} for p in threads.roster()]

results = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:68} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


async def until(pred, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition timed out")


class ScriptedHost(SessionHost):
    """SessionHost that creates real CodexSession / Session objects backed
    by scripted fake pipes so the full Unix socket + RemoteSession +
    ThreadEngine pipeline runs without spawning external CLIs."""

    def __init__(self) -> None:
        super().__init__()
        self.created: dict[str, Session] = {}
        self.codex_sent: list[dict] = []
        self.claude_sent: list[dict] = []

    async def start_session(self, agent, session_id=None, resume=False,
                            provider=None, model=None, effort=None):
        sid = session_id or f"s-{agent}"
        if provider == "codex":
            sess = CodexSession(agent, session_id=sid, model=model, effort=effort)
            sess.HOLD_GATHER_S = 0.02
            sess.WAKE_GATHER_S = 0.02
            sess.thread_id = f"codex-th-{sid[:8]}"
            sess._codex.model = model or "gpt-6.2-sol"
            sess.state = "running"
            sess.proc = SimpleNamespace(pid=4242, returncode=None)

            async def fake_send_raw(obj):
                self.codex_sent.append(obj)
                if obj.get("method") == "turn/start":
                    turn_no = sum(1 for m in self.codex_sent
                                  if m.get("method") == "turn/start")
                    asyncio.get_running_loop().call_soon(
                        lambda rpc_id=obj["id"], n=turn_no: asyncio.create_task(
                            sess._handle({"jsonrpc": "2.0", "id": rpc_id,
                                          "result": {"turn": {"id": f"root-t{n}"}}})))
            sess.send_raw = fake_send_raw
        else:
            sess = Session(agent, session_id=sid, model=model, effort=effort)
            sess.state = "running"
            sess.proc = SimpleNamespace(pid=4343, returncode=None, stdin=True)

            async def fake_send_raw(obj):
                self.claude_sent.append(obj)
            sess.send_raw = fake_send_raw
        sess.save()
        self.sessions[sess.id] = sess
        self.created[agent] = sess
        return sess


async def run_all() -> None:
    engine_mod.PARKED_CHECK_S = 0.05
    host = ScriptedHost()
    sock = Path("host.sock")
    async with listen(host, sock):
        registry = RemoteRegistry(sock)
        await registry.synchronize()
        engine = engine_mod.ThreadEngine(registry)

        # ---- 1. Two-helper Codex turn over RemoteSession ----
        row = threads.create("henry", "City of Brass")
        tid = row["id"]
        stream_q = engine.listen(tid)
        engine.post_person(tid, "What do you think of City of Brass?")
        await until(lambda: "henry" in host.created and any(
            m.get("method") == "turn/start" for m in host.codex_sent))
        henry: CodexSession = host.created["henry"]

        async def feed(method: str, params: dict) -> None:
            await henry._handle({"jsonrpc": "2.0", "method": method, "params": params})

        th = henry.thread_id
        await feed("turn/started", {"threadId": th, "turn": {"id": "root-t1"}})
        await feed("item/started", {"threadId": th, "turnId": "root-t1", "item": {
            "type": "subAgentActivity", "id": "call_critics", "kind": "started",
            "agentThreadId": "sub_critics", "agentPath": "/root/critics",
            "model": "gpt-6.2-sol", "reasoningEffort": "xhigh"}})
        await feed("item/completed", {"threadId": th, "turnId": "root-t1", "item": {
            "type": "subAgentActivity", "id": "call_critics", "kind": "started",
            "agentThreadId": "sub_critics", "agentPath": "/root/critics"}})
        await feed("turn/started", {"threadId": "sub_critics", "turn": {"id": "sc1"}})
        await feed("item/started", {"threadId": th, "turnId": "root-t1", "item": {
            "type": "subAgentActivity", "id": "call_readers", "kind": "started",
            "agentThreadId": "sub_readers", "agentPath": "/root/readers",
            "model": "gpt-6.2-sol", "reasoningEffort": "xhigh"}})
        await feed("turn/started", {"threadId": "sub_readers", "turn": {"id": "sr1"}})
        await feed("item/started", {"threadId": "sub_critics", "turnId": "sc1", "item": {
            "type": "webSearch", "id": "ws1", "query": "", "action": None, "results": None}})
        await feed("item/completed", {"threadId": "sub_critics", "turnId": "sc1", "item": {
            "type": "webSearch", "id": "ws1", "status": "completed",
            "query": "City of Brass critics reviews",
            "results": [{"title": "Kirkus Review", "url": "https://example.test/kirkus"}]}})
        await feed("item/started", {"threadId": "sub_readers", "turnId": "sr1", "item": {
            "type": "commandExecution", "id": "cmd1",
            "command": "/bin/bash -lc \"cat notes.md\"", "cwd": "/tmp"}})
        await feed("item/completed", {"threadId": "sub_readers", "turnId": "sr1", "item": {
            "type": "commandExecution", "id": "cmd1", "status": "completed",
            "aggregatedOutput": "loves lush worldbuilding", "exitCode": 0}})
        # Root writes a premature reply and closes turn/completed while BOTH
        # helpers are still running.
        await feed("item/started", {"threadId": th, "turnId": "root-t1", "item": {
            "type": "agentMessage", "id": "m-premature", "phase": "final_answer"}})
        await feed("item/agentMessage/delta", {"threadId": th, "turnId": "root-t1",
                                               "itemId": "m-premature",
                                               "delta": "Premature reply."})
        await feed("item/completed", {"threadId": th, "turnId": "root-t1", "item": {
            "type": "agentMessage", "id": "m-premature", "phase": "final_answer",
            "text": "Premature reply."}})
        await feed("turn/completed", {"threadId": th, "turn": {
            "id": "root-t1", "status": "completed", "durationMs": 4000, "items": []}})

        # Wait well past PARKED_CHECK_S (0.05s) to prove the turn stays alive.
        await asyncio.sleep(0.15)
        mid_summary = engine.summary(tid)
        mid_member = mid_summary["members"][0]
        mid_turn = engine.live_turns(tid)[0]
        mid_msgs = threads.read(tid)[1]
        mid_did_labels = [d["label"] for d in mid_turn.get("did") or []]
        check("MID-RUN member stays working with 0 background_tasks past PARKED_CHECK_S",
              (mid_member["working"], mid_member["background"], mid_member["busy"],
               len(mid_msgs), engine.marks_for(tid)),
              (True, 0, True, 1, []))
        check("MID-RUN snapshot live turn hydrates clean Agent + child step labels",
              (mid_turn.get("step"),
               "Agent: critics" in mid_did_labels,
               "Agent: readers" in mid_did_labels,
               "↳ webSearch: City of Brass critics reviews" in mid_did_labels,
               "↳ Bash: cat notes.md" in mid_did_labels,
               any("subAgentActivity" in l or "/root/" in l or "/bin/bash" in l
                   for l in mid_did_labels)),
              ("↳ Bash: cat notes.md", True, True, True, True, False))

        # Helper 1 finishes: still held waiting for helper 2.
        await feed("item/completed", {"threadId": "sub_critics", "turnId": "sc1", "item": {
            "type": "agentMessage", "id": "mc1",
            "text": "Critics praise the 18th-century Cairo setting."}})
        await feed("turn/completed", {"threadId": "sub_critics", "turn": {
            "id": "sc1", "status": "completed", "items": []}})
        await asyncio.sleep(0.08)
        starts_after_one = [m for m in host.codex_sent if m.get("method") == "turn/start"]
        check("AFTER HELPER 1 still holding open, no continuation turn yet",
              (len(starts_after_one), len(threads.read(tid)[1]),
               engine.summary(tid)["members"][0]["working"]),
              (1, 1, True))

        # Helper 2 finishes: one continuation turn/start carries BOTH findings.
        await feed("item/completed", {"threadId": "sub_readers", "turnId": "sr1", "item": {
            "type": "agentMessage", "id": "mr1",
            "text": "Your notes say you love lush worldbuilding."}})
        await feed("turn/completed", {"threadId": "sub_readers", "turn": {
            "id": "sr1", "status": "completed", "items": []}})
        await until(lambda: sum(1 for m in host.codex_sent
                                if m.get("method") == "turn/start") == 2)
        cont_prompt = [m for m in host.codex_sent
                       if m.get("method") == "turn/start"][1]["params"]["input"][0]["text"]
        check("AFTER HELPER 2 one continuation carries BOTH helper reports",
              ("Critics praise the 18th-century Cairo setting." in cont_prompt,
               "Your notes say you love lush worldbuilding." in cont_prompt),
              (True, True))

        # Continuation produces the single complete reply.
        await feed("turn/started", {"threadId": th, "turn": {"id": "root-t2"}})
        await feed("item/started", {"threadId": th, "turnId": "root-t2", "item": {
            "type": "agentMessage", "id": "m-final", "phase": "final_answer"}})
        await feed("item/agentMessage/delta", {"threadId": th, "turnId": "root-t2",
                                               "itemId": "m-final",
                                               "delta": "Yes — critics praise Cairo and it fits your taste."})
        await feed("item/completed", {"threadId": th, "turnId": "root-t2", "item": {
            "type": "agentMessage", "id": "m-final", "phase": "final_answer",
            "text": "Yes — critics praise Cairo and it fits your taste."}})
        await feed("turn/completed", {"threadId": th, "turn": {
            "id": "root-t2", "status": "completed", "durationMs": 1500, "items": []}})

        await until(lambda: engine.settled(tid))
        end_msgs = threads.read(tid)[1]
        end_member = engine.summary(tid)["members"][0]
        stream_events = []
        while not stream_q.empty():
            stream_events.append(stream_q.get_nowait())
        engine.unlisten(tid, stream_q)
        step_labels = [e["label"] for e in stream_events if e.get("type") == "turn.step"]
        rec = steps_mod.read(tid, end_msgs[-1]["id"]).get(end_msgs[-1]["id"])
        rec_steps = (rec or {}).get("steps") or []
        agents = [s for s in rec_steps if s.get("type") == "tool" and s.get("name") == "Agent"]
        nested = [s for s in rec_steps if s.get("type") == "tool" and s.get("parent") in ("call_critics", "call_readers")]
        ends = [s for s in rec_steps if s.get("type") == "end"]
        check("AFTER TURN one reply posted, no marks, no premature draft, not working",
              (len(end_msgs), end_msgs[-1]["text"], engine.marks_for(tid),
               end_member["working"], end_member["background"],
               "Premature reply." in json.dumps(stream_events)),
              (2, "Yes — critics praise Cairo and it fits your taste.", [],
               False, 0, False))
        check("AFTER TURN live stream carried clean Agent + child labels, never raw internal ones",
              ("Agent: critics" in step_labels,
               "Agent: readers" in step_labels,
               "↳ webSearch: City of Brass critics reviews" in step_labels,
               "↳ Bash: cat notes.md" in step_labels,
               any("subAgentActivity" in s or "/root/" in s for s in step_labels)),
              (True, True, True, True, False))
        check("AFTER TURN full steps record nests child tools and closes both Agent calls",
              ([(a["input"]["description"], a["is_error"], bool(a["output"])) for a in agents],
               len(nested), len(ends)),
              ([("critics", False, True), ("readers", False, True)], 2, 1))

        # ---- 2. Empty notification turn in _tail over RemoteSession ----
        tail_q = engine.listen(tid)
        # Simulate a late notification turn after result with empty text.
        await henry._handle({"jsonrpc": "2.0", "method": "turn/started",
                             "params": {"threadId": th, "turn": {"id": "root-empty"}}})
        await henry._handle({"jsonrpc": "2.0", "method": "item/completed",
                             "params": {"threadId": th, "turnId": "root-empty",
                                        "item": {"type": "agentMessage", "id": "me", "text": ""}}})
        await henry._handle({"jsonrpc": "2.0", "method": "turn/completed",
                             "params": {"threadId": th, "turn": {
                                 "id": "root-empty", "status": "completed", "items": []}}})
        await asyncio.sleep(0.08)
        tail_events = []
        while not tail_q.empty():
            tail_events.append(tail_q.get_nowait())
        engine.unlisten(tid, tail_q)
        check("EMPTY TAIL NOTIFICATION posts nothing, leaves no mark, ends working=False",
              (len(threads.read(tid)[1]), engine.marks_for(tid),
               engine.summary(tid)["members"][0]["working"],
               any(e.get("type") == "message.done" for e in tail_events)),
              (2, [], False, False))

        # ---- 3. Claude run_in_background over RemoteSession ----
        row2 = threads.create("ada", "Claude background check")
        tid2 = row2["id"]
        engine.post_person(tid2, "Kick off a background task and tell me.")
        await until(lambda: "ada" in host.created and len(host.claude_sent) >= 1)
        ada: Session = host.created["ada"]
        await ada._handle({"type": "system", "subtype": "init", "session_id": ada.id,
                           "model": "claude-sonnet-4-6"})
        await ada._handle({"type": "system", "subtype": "background_tasks_changed",
                           "tasks": [{"task_id": "bg1", "task_type": "local_agent",
                                      "description": "Scan archive"}]})
        # Sleep well past PARKED_CHECK_S while turn is still open (no result yet).
        await asyncio.sleep(0.15)
        mid_ada = engine.summary(tid2)["members"][0]
        check("CLAUDE REMOTE mid-turn background task stays working and never times out at PARKED_CHECK_S",
              (mid_ada["working"], mid_ada["background"],
               mid_ada["background_tasks"][0]["description"],
               mid_ada["background_tasks"][0]["started_at"] is not None,
               engine.marks_for(tid2)),
              (True, 1, "Scan archive", True, []))
        await ada._handle({"type": "assistant", "parent_tool_use_id": None,
                           "message": {"id": "ma1", "role": "assistant",
                                       "model": "claude-sonnet-4-6",
                                       "content": [{"type": "text", "text": "Started the scan."}]}})
        await ada._handle({"type": "result", "subtype": "success", "is_error": False,
                           "num_turns": 1, "session_id": ada.id})
        await until(lambda: len(threads.read(tid2)[1]) == 2)
        await asyncio.sleep(0.05)
        parked_ada = engine.summary(tid2)["members"][0]
        check("CLAUDE REMOTE after result with bg still running: parked (working=False, bg=1)",
              (parked_ada["working"], parked_ada["background"], parked_ada["busy"]),
              (False, 1, True))
        # Background task completes and produces an empty notification turn -> posts nothing, no mark.
        await ada._handle({"type": "system", "subtype": "background_tasks_changed", "tasks": []})
        await ada._handle({"type": "system", "subtype": "task_notification", "task_id": "bg1"})
        await ada._handle({"type": "system", "subtype": "init", "session_id": ada.id,
                           "model": "claude-sonnet-4-6"})
        await ada._handle({"type": "assistant", "parent_tool_use_id": None,
                           "message": {"id": "ma2", "role": "assistant",
                                       "model": "claude-sonnet-4-6",
                                       "content": [{"type": "text", "text": ""}]}})
        await ada._handle({"type": "result", "subtype": "success", "is_error": False,
                           "num_turns": 1, "session_id": ada.id})
        await until(lambda: engine.settled(tid2))
        check("CLAUDE REMOTE empty notification turn posts nothing and writes no mark",
              (len(threads.read(tid2)[1]), threads.read(tid2)[1][-1]["text"],
               engine.marks_for(tid2), engine.summary(tid2)["members"][0]["working"]),
              (2, "Started the scan.", [], False))

        engine.stop_all_tails()


def main() -> int:
    os.chdir(TMP)
    with own_session_store():
        asyncio.run(asyncio.wait_for(run_all(), 20))
    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
