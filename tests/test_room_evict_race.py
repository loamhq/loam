#!/usr/bin/env python3
"""Verify that ending a session on purpose (for room, idle, pressure or retire)
never costs an arriving owner message its reply or leaves a failed-turn mark.

Reproduces the 2026-11-23 20:06 PST race: a loaded idle thread session (idle
16 s) receives an owner message in the same instant two brand-new threads need
room and make_room() starts shutting that idle session down.
"""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))

import loam_broker as cfg  # noqa: E402
import loam_limits as limits  # noqa: E402
import loam_lock  # noqa: E402
import loam_thread_engine as te  # noqa: E402
import loam_thread_inputs as inputs  # noqa: E402
import loam_threads as threads  # noqa: E402
import loam_waiters as waiters  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results: list[bool] = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:68} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


# Stateful stub CLI whose SIGTERM teardown takes ~0.25 s (matching Claude
# Code's ~0.6 s MCP teardown where stdin stayed open while state == "running").
SLOW_EXIT_STUB = """#!/usr/bin/env python3
import json, os, signal, sys, time
from pathlib import Path

args = sys.argv[1:]
def flag(name):
    return args[args.index(name) + 1] if name in args else None

resumed = "--resume" in args
sid = flag("--resume") or flag("--session-id") or "unknown"
store_dir = Path(os.environ["LOAM_TEST_RACE_STORE"])
store_dir.mkdir(parents=True, exist_ok=True)
hist_file = store_dir / f"{sid}.json"
history = json.loads(hist_file.read_text()) if (resumed and hist_file.exists()) else []

dying = False

def on_term(signum, frame):
    global dying
    dying = True
    time.sleep(0.25)
    sys.exit(0)

signal.signal(signal.SIGTERM, on_term)

def emit(obj):
    print(json.dumps(obj), flush=True)

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    msg = json.loads(raw)
    if msg.get("type") == "control_request":
        emit({"type": "control_response",
              "response": {"subtype": "success", "request_id": msg["request_id"], "response": {}}})
        continue
    if msg.get("type") != "user":
        continue
    if dying:
        continue
    content = msg["message"]["content"]
    text = content if isinstance(content, str) else " ".join(
        b.get("text", "") for b in content if isinstance(b, dict))
    # Extract the owner's actual line if wrapped in thread prompt
    history.append(text)
    hist_file.write_text(json.dumps(history))
    reply = f"ok turns={len(history)} resumed={resumed}"
    emit({"type": "system", "subtype": "init", "session_id": sid, "model": "stub"})
    emit({"type": "assistant", "session_id": sid,
          "message": {"role": "assistant", "model": "stub",
                      "content": [{"type": "text", "text": reply}],
                      "usage": {"input_tokens": 50, "output_tokens": 10}}})
    emit({"type": "result", "subtype": "success", "is_error": False,
          "session_id": sid, "result": reply, "total_cost_usd": 0.001,
          "modelUsage": {"stub": {"contextWindow": 200000}}})
"""


async def wait_settled(engine, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        if (not engine.turns
                and all(w.done() for w in engine.workers.values())
                and all(q.empty() for q in engine.queues.values())):
            return
    raise TimeoutError("threads did not settle in time")


async def stop_all(broker) -> None:
    for s in list(broker.sessions.values()):
        if (s.is_live()
                or (s.proc is not None and s.proc.returncode is None)
                or (s._stopping_event is not None and not s._stopping_event.is_set())):
            await s.stop()
        if s.stdout_task is not None and not s.stdout_task.done():
            await s.stdout_task


def mark_of(engine, tid: str, persona: str) -> str | None:
    for row in engine.marks_for(tid):
        if row.get("persona") == persona:
            return row.get("text")
    return None


async def main() -> None:
    real_memory = cfg.memory
    real_grace = cfg.EVICT_GRACE_S
    real_retry = te.CAP_RETRY_S
    try:
        # -- 1. Exact reproduction of 2026-11-23 20:06 PST --------------------
        # Tight 3,910 MB box: room for 1 session at a time (2,360 MB left with 1
        # live session against 2,048 reserve + 450 start). Loaded idle session
        # on t1 (idle 16 s); same instant two brand-new threads t2 & t3 need
        # room, and 30 ms later (during s1's 250 ms SIGTERM teardown) an owner
        # message arrives on t1.
        broker = broker_mod.Broker()
        cfg.EVICT_GRACE_S = 0.4
        te.CAP_RETRY_S = 0.15

        def tight_mem():
            live_n = len(broker.live())
            return {"available": 3910 - 1100 - live_n * 450,
                    "cgroup_room": None, "reserve": 2048}

        cfg.memory = tight_mem
        engine = te.ThreadEngine(broker)
        t1 = threads.create("probe1", title="Loaded Idle Thread")["id"]
        t2 = threads.create("probe1", title="Brand New Thread 1")["id"]
        t3 = threads.create("probe1", title="Brand New Thread 2")["id"]
        for tid in (t1, t2, t3):
            threads.update(tid, engines={"probe1": "claude-code"})

        # Warm up t1 so it has a loaded idle Claude session.
        m0 = threads.append(t1, threads.OWNER, "warmup turn")
        engine.schedule(te.Turn(t1, "probe1", 0, m0["id"]))
        await wait_settled(engine)
        s1_id = threads.session_for(t1, "probe1")["session_id"]
        s1 = broker.sessions[s1_id]
        s1.last_activity = time.time() - 16.0  # idle 16 s

        q1 = engine.listen(t1)
        # Same instant: two new chats need room, and 30 ms later owner posts to t1
        m_new1 = threads.append(t2, threads.OWNER, "new chat one")
        m_new2 = threads.append(t3, threads.OWNER, "new chat two")
        engine.schedule(te.Turn(t2, "probe1", 0, m_new1["id"]))
        engine.schedule(te.Turn(t3, "probe1", 0, m_new2["id"]))
        await asyncio.sleep(0.03)
        m_follow = threads.append(t1, threads.OWNER, "important follow up")
        engine.schedule(te.Turn(t1, "probe1", 0, m_follow["id"]))

        await wait_settled(engine, timeout_s=15.0)
        t1_events = []
        while not q1.empty():
            t1_events.append(q1.get_nowait())
        engine.unlisten(t1, q1)

        _, msgs1, _ = threads.read(t1)
        _, msgs2, _ = threads.read(t2)
        _, msgs3, _ = threads.read(t3)

        check("exact race: t1 answered its follow-up message",
              (len(msgs1), msgs1[-1]["author"], "turns=2" in msgs1[-1]["text"],
               "resumed=True" in msgs1[-1]["text"]),
              (4, "probe1", True, True))
        check("exact race: t1 kept the same session id and left no failure mark",
              (threads.session_for(t1, "probe1")["session_id"],
               mark_of(engine, t1, "probe1"),
               inputs.items(t1, "probe1")),
              (s1_id, None, []))
        check("exact race: t1 showed ordinary waiting state while box was full",
              any(e.get("type") == "turn.waiting" for e in t1_events), True)
        check("exact race: both new threads answered cleanly with no marks",
              (msgs2[-1]["author"], msgs3[-1]["author"],
               mark_of(engine, t2, "probe1"), mark_of(engine, t3, "probe1")),
              ("probe1", "probe1", None, None))

        await engine.handoffs.close()
        engine.stop_all_tails()
        await stop_all(broker)

        # -- 2. Classic session reuse refreshes last_activity -----------------
        cfg.EVICT_GRACE_S = 15
        broker = broker_mod.Broker()
        cfg.memory = lambda: {"available": 25000, "cgroup_room": None, "reserve": 2048}
        c = await broker.start_session("probe1", "classic-reuse")
        c.last_activity = time.time() - 25.0
        reused = await broker.start_session("probe1", "classic-reuse", resume=True)
        check("reusing a warm classic session refreshes last_activity inside grace",
              (reused is c, time.time() - c.last_activity < cfg.EVICT_GRACE_S),
              (True, True))
        # Now a full box cannot immediately evict it before its message arrives.
        cfg.memory = lambda: {"available": 2100, "cgroup_room": None, "reserve": 2048}
        try:
            await broker.make_room("probe1")
            evicted = True
        except broker_mod.BrokerError as exc:
            evicted = exc.status != 429
        check("...protecting it from immediate make_room eviction",
              (evicted, c.is_live()), (False, True))
        await stop_all(broker)

        # -- 3. Layer 2A: intentional unload between start_session and send ---
        cfg.memory = lambda: {"available": 25000, "cgroup_room": None, "reserve": 2048}
        broker = broker_mod.Broker()
        engine = te.ThreadEngine(broker)
        t_reclaim = threads.create("probe1", title="Mid-flight Reclaim")["id"]
        threads.update(t_reclaim, engines={"probe1": "claude-code"})
        m_init = threads.append(t_reclaim, threads.OWNER, "initial message")
        engine.schedule(te.Turn(t_reclaim, "probe1", 0, m_init["id"]))
        await wait_settled(engine)
        r_sid = threads.session_for(t_reclaim, "probe1")["session_id"]

        # Intercept _prepare_turn ONCE so the session is unloaded for room right
        # after start_session() returns and before send_message() runs.
        orig_prepare = engine._prepare_turn
        fired = False

        async def prepare_then_reclaim(turn, meta, eng, model, effort):
            nonlocal fired
            sess, kept, prompt, through = await orig_prepare(turn, meta, eng, model, effort)
            if not fired:
                fired = True
                await sess.stop(reason="room")
            return sess, kept, prompt, through

        engine._prepare_turn = prepare_then_reclaim
        m_after = threads.append(t_reclaim, threads.OWNER, "message during reclaim")
        engine.schedule(te.Turn(t_reclaim, "probe1", 0, m_after["id"]))
        await wait_settled(engine)
        _, msgs_r, _ = threads.read(t_reclaim)
        check("unload between start_session and send auto-retries and answers",
              (len(msgs_r), msgs_r[-1]["author"], "turns=2" in msgs_r[-1]["text"],
               mark_of(engine, t_reclaim, "probe1"),
               threads.session_for(t_reclaim, "probe1")["session_id"] == r_sid),
              (4, "probe1", True, None, True))
        await engine.handoffs.close()
        engine.stop_all_tails()
        await stop_all(broker)

        # -- 4. Layer 2B: intentional unload broker.exit before any reply -----
        broker = broker_mod.Broker()
        engine = te.ThreadEngine(broker)
        t_exit = threads.create("probe1", title="Reclaim Exit Before Reply")["id"]
        threads.update(t_exit, engines={"probe1": "claude-code"})
        m_e1 = threads.append(t_exit, threads.OWNER, "first message")
        engine.schedule(te.Turn(t_exit, "probe1", 0, m_e1["id"]))
        await wait_settled(engine)

        # Simulate a session receiving broker.exit(reason="pressure") right after
        # send_message without producing any words or steps.
        orig_prepare2 = engine._prepare_turn
        injected = False

        async def prepare_with_reclaim_exit(turn, meta, eng, model, effort):
            nonlocal injected
            sess, kept, prompt, through = await orig_prepare2(turn, meta, eng, model, effort)
            if not injected:
                injected = True
                real_send = sess.send_message

                async def send_then_pressure_stop(text, attachments=None, *, dispatch_id=None):
                    # Emit user message, then immediately stop for pressure before reply.
                    sess._stop_reason = "pressure"
                    sess._stop_requested = True
                    await sess.emit({"type": "broker.exit", "session_id": sess.id,
                                     "exit_code": 0, "ts": cfg.now_iso(), "reason": "pressure"})
                    sess.state = "exited"
                    return sess.seq

                sess.send_message = send_then_pressure_stop
                _ = real_send
            return sess, kept, prompt, through

        engine._prepare_turn = prepare_with_reclaim_exit
        m_e2 = threads.append(t_exit, threads.OWNER, "second message")
        engine.schedule(te.Turn(t_exit, "probe1", 0, m_e2["id"]))
        await wait_settled(engine)
        _, msgs_e, _ = threads.read(t_exit)
        check("broker.exit(reason='pressure') before reply auto-retries cleanly",
              (len(msgs_e), msgs_e[-1]["author"], "turns=2" in msgs_e[-1]["text"],
               mark_of(engine, t_exit, "probe1")),
              (4, "probe1", True, None))
        ev_exit = [e for e in limits.read_events()
                   if e["kind"] == "turn_retried"
                   and (e.get("subject") or {}).get("thread") == t_exit]
        check("retry succeeding on attempt 2 writes 1 turn_retried limit event",
              (len(ev_exit),
               ev_exit[0]["outcome"] if ev_exit else None,
               (ev_exit[0].get("detail") or {}).get("reason") if ev_exit else None,
               (ev_exit[0].get("detail") or {}).get("retries") if ev_exit else None,
               (ev_exit[0].get("detail") or {}).get("attempts") if ev_exit else None,
               (ev_exit[0].get("detail") or {}).get("provider") if ev_exit else None,
               isinstance(ev_exit[0].get("duration_s"), (int, float)) if ev_exit else False,
               bool((ev_exit[0].get("subject") or {}).get("session")) if ev_exit else False,
               isinstance((ev_exit[0].get("memory") or {}).get("total_mb"), int) if ev_exit else False,
               "second message" not in json.dumps(ev_exit)),
              (1, "replied", "pressure", 1, 2, "claude-code", True, True, True, True))
        await engine.handoffs.close()
        engine.stop_all_tails()
        await stop_all(broker)

        # -- 5. Negative control: unexpected crash (no reason) still marks ----
        broker = broker_mod.Broker()
        engine = te.ThreadEngine(broker)
        t_crash = threads.create("probe1", title="Unexpected Crash")["id"]
        threads.update(t_crash, engines={"probe1": "claude-code"})
        orig_prepare3 = engine._prepare_turn

        async def prepare_with_crash(turn, meta, eng, model, effort):
            sess, kept, prompt, through = await orig_prepare3(turn, meta, eng, model, effort)

            async def send_then_crash(text, attachments=None, *, dispatch_id=None):
                await sess.emit({"type": "broker.exit", "session_id": sess.id,
                                 "exit_code": -9, "ts": cfg.now_iso()})
                sess.state = "exited"
                return sess.seq

            sess.send_message = send_then_crash
            return sess, kept, prompt, through

        engine._prepare_turn = prepare_with_crash
        m_c = threads.append(t_crash, threads.OWNER, "will crash")
        engine.schedule(te.Turn(t_crash, "probe1", 0, m_c["id"]))
        await wait_settled(engine)
        _, msgs_c, _ = threads.read(t_crash)
        ev_crash = [e for e in limits.read_events()
                    if e["kind"] == "turn_retried"
                    and (e.get("subject") or {}).get("thread") == t_crash]
        check("unexpected crash (no reclaim reason) still marks and does not retry",
              (len(msgs_c), "exited" in (mark_of(engine, t_crash, "probe1") or ""),
               len(ev_crash)),
              (1, True, 0))
        await engine.handoffs.close()
        engine.stop_all_tails()
        await stop_all(broker)

        # -- 6. Exhausting all 3 retries writes 1 turn_retried(exhausted) -----
        broker = broker_mod.Broker()
        engine = te.ThreadEngine(broker)
        t_exhaust = threads.create("probe1", title="Exhaust Retries")["id"]
        threads.update(t_exhaust, engines={"probe1": "claude-code"})
        orig_prepare4 = engine._prepare_turn
        exhaust_attempts = 0

        async def prepare_always_reclaim(turn, meta, eng, model, effort):
            nonlocal exhaust_attempts
            exhaust_attempts += 1
            sess, kept, prompt, through = await orig_prepare4(turn, meta, eng, model, effort)

            async def send_then_room_stop(text, attachments=None, *, dispatch_id=None):
                sess._stop_reason = "room"
                sess._stop_requested = True
                await sess.emit({"type": "broker.exit", "session_id": sess.id,
                                 "exit_code": 0, "ts": cfg.now_iso(), "reason": "room"})
                sess.state = "exited"
                return sess.seq

            sess.send_message = send_then_room_stop
            return sess, kept, prompt, through

        engine._prepare_turn = prepare_always_reclaim
        m_ex = threads.append(t_exhaust, threads.OWNER, "secret exhaust message")
        engine.schedule(te.Turn(t_exhaust, "probe1", 0, m_ex["id"]))
        await wait_settled(engine)
        ev_exhaust = [e for e in limits.read_events()
                      if e["kind"] == "turn_retried"
                      and (e.get("subject") or {}).get("thread") == t_exhaust]
        q_all = limits.query(days=7)
        check("exhausting 3 retries writes 1 turn_retried(exhausted) & counts in query",
              (exhaust_attempts,
               len(ev_exhaust),
               ev_exhaust[0]["outcome"] if ev_exhaust else None,
               (ev_exhaust[0].get("detail") or {}).get("reason") if ev_exhaust else None,
               (ev_exhaust[0].get("detail") or {}).get("retries") if ev_exhaust else None,
               (ev_exhaust[0].get("detail") or {}).get("attempts") if ev_exhaust else None,
               isinstance(ev_exhaust[0].get("duration_s"), (int, float)) if ev_exhaust else False,
               isinstance((ev_exhaust[0].get("memory") or {}).get("total_mb"), int) if ev_exhaust else False,
               "secret exhaust message" not in json.dumps(ev_exhaust),
               q_all["counts"]["turn_retried"] >= 2),
              (4, 1, "exhausted", "room", 3, 4, True, True, True, True))
        await engine.handoffs.close()
        engine.stop_all_tails()
        await stop_all(broker)
    finally:
        cfg.memory = real_memory
        cfg.EVICT_GRACE_S = real_grace
        te.CAP_RETRY_S = real_retry

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="loam-room-race-") as tmp:
        root = Path(tmp)
        stub = root / "claude"
        stub.write_text(SLOW_EXIT_STUB, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        os.environ["LOAM_TEST_RACE_STORE"] = str(root / "cli-store")
        os.environ["LOAM_LIMIT_EVENTS"] = str(root / "limits" / "events.jsonl")

        labs = root / "ai-labs"
        ws = labs / "probe1"
        ws.mkdir(parents=True)
        (ws / "agent.json").write_text(json.dumps({
            "name": "Probe 1", "handle": "probe1",
        }), encoding="utf-8")

        cfg.CLAUDE = str(stub)
        cfg.AI_LABS = labs
        cfg.SESSION_DIR = root / "sessions"
        cfg.SESSION_DIR.mkdir()
        threads.VAULT = root / "vault"
        threads.VAULT.mkdir()
        threads.AI_LABS = labs
        threads.STATE = root / "threads-state"
        threads.STATE.mkdir()
        waiters.STATE = root / "waiters"
        waiters.RUNS = waiters.STATE / "runs"
        waiters.THREADS_STATE = threads.STATE
        loam_lock.LOCK_DIR = root / "locks"
        loam_lock.LOCK_DIR.mkdir()
        asyncio.run(main())


if __name__ == "__main__":
    run()
