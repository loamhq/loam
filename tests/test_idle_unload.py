#!/usr/bin/env python3
"""Verify RAM-scaled idle unload timeouts, config overrides, waiting-run relief,
non-idle protection (turns, tool calls, subagents, pending job results, queued
inputs), private-handoff debouncing, and lossless unload/resume."""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))

import loam_broker as cfg  # noqa: E402
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


# Stateful stub CLI: stores conversation history by session_id on disk so
# --resume proves the reloaded process sees prior turns, model, effort, and
# receives no repeated messages.
STATEFUL_CLAUDE_STUB = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
def flag(name):
    return args[args.index(name) + 1] if name in args else None

resumed = "--resume" in args
sid = flag("--resume") or flag("--session-id") or "unknown"
model = flag("--model") or "default-model"
effort = flag("--effort") or "default-effort"

store_dir = Path(os.environ["LOAM_TEST_CLI_STORE"])
store_dir.mkdir(parents=True, exist_ok=True)
hist_file = store_dir / f"{sid}.json"
history = json.loads(hist_file.read_text()) if (resumed and hist_file.exists()) else []

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
    content = msg["message"]["content"]
    text = content if isinstance(content, str) else " ".join(
        b.get("text", "") for b in content if isinstance(b, dict))
    history.append(text)
    hist_file.write_text(json.dumps(history))
    reply = f"turns={len(history)} resumed={resumed} model={model} effort={effort} history={' | '.join(history)}"
    emit({"type": "system", "subtype": "init", "session_id": sid, "model": model})
    emit({"type": "assistant", "session_id": sid,
          "message": {"role": "assistant", "model": model,
                      "content": [{"type": "text", "text": reply}],
                      "usage": {"input_tokens": 100, "output_tokens": 20}}})
    emit({"type": "result", "subtype": "success", "is_error": False,
          "session_id": sid, "result": reply, "total_cost_usd": 0.001,
          "modelUsage": {model: {"contextWindow": 200000}}})
"""


class FakeStdin:
    def write(self, data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None


class FakeProc:
    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.returncode = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


def running(broker, agent: str, session_id: str, idle_s: float = 0.0,
            idle_timeout_s: float | None = None):
    session = broker.adopt(broker_mod.Session(agent, session_id))
    session.state = "running"
    session.proc = FakeProc()
    session.last_activity = time.time() - idle_s
    session.idle_timeout_s = idle_timeout_s
    broker.sessions[session.id] = session
    return session


async def stop_all(broker) -> None:
    for session in list(broker.sessions.values()):
        if session.is_live():
            await session.stop()


async def main() -> None:
    # -- 1. Box-size curve & this box's exact values -------------------------
    no_cfg = lambda key, default=None: default  # noqa: E731
    check("this box (31335 MB) yields today's exact (600, 1800)",
          cfg.idle_timeouts(31335, config_get=no_cfg), (600, 1800))
    check("...and this box's reap interval stays 60 s",
          cfg.reap_interval_s(*cfg.idle_timeouts(31335, config_get=no_cfg)), 60)
    if cfg.BOX_MB > 12288:
        check("module defaults on this host match (600, 1800, 60)",
              (cfg.THREAD_IDLE_S, cfg.CLASSIC_IDLE_S, cfg.REAP_INTERVAL_S),
              (600, 1800, 60))

    for box_mb, want, want_tick in (
        (1900, (90, 180), 22),     # 2 GB VPS (usable)
        (2048, (90, 180), 22),     # 2 GiB
        (3072, (90, 180), 22),     # upper 2 GB boundary
        (3700, (120, 300), 30),    # 4 GB decimal VPS
        (3950, (120, 300), 30),    # 4 GiB usable
        (6144, (120, 300), 30),    # upper 4 GB boundary
        (7750, (300, 900), 60),    # 8 GB usable
        (12288, (300, 900), 60),   # upper 8 GB boundary
        (15600, (600, 1800), 60),  # 16 GB usable
        (16384, (600, 1800), 60),  # 16 GiB
        (31335, (600, 1800), 60),  # 32 GB (this box)
    ):
        got = cfg.idle_timeouts(box_mb, config_get=no_cfg)
        check(f"{box_mb} MB box yields {want} (tick {want_tick}s)",
              (got, cfg.reap_interval_s(*got)), (want, want_tick))

    # Admission reserve & start cost match loam_limits scaling (and stay
    # identical to today's max(2048, 12%) / 450 on 16 GB+ boxes).
    import loam_limits  # noqa: PLC0415
    check("admission reserve matches loam_limits",
          cfg.MEMORY_RESERVE_MB, loam_limits.memory_reserve_mb(cfg.BOX_MB))
    check("session/run start cost matches loam_limits",
          (cfg.SESSION_START_MB, loam_lock.RUN_START_MB),
          (loam_limits.start_allowance_mb(cfg.BOX_MB),
           loam_limits.start_allowance_mb(cfg.BOX_MB)))

    # -- 2. Config overrides (unit + real loam.toml subprocess load) ---------
    overrides = {"sessions.thread_idle_s": 75, "sessions.classic_idle_s": 240}
    check("config overrides both thread and classic idle timeouts",
          cfg.idle_timeouts(31335, config_get=lambda k, d=None: overrides.get(k, d)),
          (75, 240))
    check("partial override keeps RAM-derived default for the other",
          cfg.idle_timeouts(3950, config_get=lambda k, d=None: 45 if k == "sessions.thread_idle_s" else d),
          (45, 300))
    for bad in (0, -10, True, False, "120", 12.5):
        check(f"invalid override {bad!r} safely falls back to RAM default",
              cfg.idle_timeouts(3950, config_get=lambda k, d=None, b=bad: b),
              (120, 300))

    # Verify loading overrides directly from a real LOAM_CONFIG TOML file.
    with tempfile.TemporaryDirectory(prefix="loam-idle-cfg-") as cfg_tmp:
        root = Path(cfg_tmp)
        toml_path = root / "loam.toml"
        toml_path.write_text(
            f'[paths]\nhome = "{root / "home"}"\n\n'
            f'[sessions]\nthread_idle_s = 77\nclassic_idle_s = 222\n',
            encoding="utf-8",
        )
        env = dict(os.environ, LOAM_CONFIG=str(toml_path), LOAM_TEST_INSTALL_CONFIG="1")
        out = subprocess.check_output(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, {str(AGENTS / 'lib')!r}); "
             f"import loam_broker as b; print(b.THREAD_IDLE_S, b.CLASSIC_IDLE_S, b.REAP_INTERVAL_S)"],
            env=env, text=True,
        ).strip()
        check("loam.toml [sessions] overrides loaded at import time", out, "77 222 19")

    # -- 3. Non-idle guards: turn, tool call, subagent, dispatch, job result, queued input --
    real_memory = cfg.memory
    cfg.memory = lambda: {"available": 25000, "cgroup_room": None, "reserve": 2048}

    broker = broker_mod.Broker()
    now = time.time()
    s_idle = running(broker, "probe1", "s-idle", idle_s=200, idle_timeout_s=120)
    s_turn = running(broker, "probe1", "s-turn", idle_s=5000, idle_timeout_s=120)
    s_turn._turn_active = True
    s_perm = running(broker, "probe1", "s-perm", idle_s=5000, idle_timeout_s=120)
    s_perm.pending["req-1"] = {"raised_at": now - 10, "event": {"type": "broker.permission_request"}}
    s_sub = running(broker, "probe1", "s-sub", idle_s=5000, idle_timeout_s=120)
    s_sub.background_tasks = [{"task_id": "sub-1", "task_type": "agent", "description": "research"}]
    s_dispatch = running(broker, "probe1", "s-dispatch", idle_s=5000, idle_timeout_s=120)
    gate = asyncio.Event()
    s_dispatch._dispatches["d-1"] = asyncio.create_task(gate.wait())

    # Finished engineer job result owed to caller_session s-job-caller
    s_job_caller = running(broker, "probe1", "s-job-caller", idle_s=5000, idle_timeout_s=120)
    job_done_dir = Path(waiters.STATE).parent / "fake-job-done"
    job_done_dir.mkdir(parents=True, exist_ok=True)
    (job_done_dir / "result.json").write_text(json.dumps({
        "ok": True, "stage": "done", "finished": "2026-11-23T19:00:00Z",
    }), encoding="utf-8")
    waiters.register("20261123-190001-aaaa", "Loam", job_done_dir, "probe1",
                     {"caller": "conversation", "caller_session": s_job_caller.id,
                      "thread": "t-job1"})

    # Finished engineer job result owed to a thread whose session rotated to s-job-bound
    s_job_bound = running(broker, "probe2", "s-job-bound", idle_s=5000, idle_timeout_s=120)
    threads.remember_session("t-job2", "probe2", s_job_bound.id, "claude-code", "m1")
    job_done2_dir = Path(waiters.STATE).parent / "fake-job-done2"
    job_done2_dir.mkdir(parents=True, exist_ok=True)
    (job_done2_dir / "result.json").write_text(json.dumps({
        "ok": True, "stage": "done", "finished": "2026-11-23T19:00:02Z",
    }), encoding="utf-8")
    waiters.register("20261123-190002-bbbb", "Loam", job_done2_dir, "probe2",
                     {"caller": "conversation", "caller_session": "older-rotated-sid",
                      "thread": "t-job2"})

    # Unfinished (running/queued) engineer job MUST NOT block unloading idle chat
    s_job_queued = running(broker, "probe3", "s-job-queued", idle_s=200, idle_timeout_s=120)
    job_queued_dir = Path(waiters.STATE).parent / "fake-job-queued"
    job_queued_dir.mkdir(parents=True, exist_ok=True)
    (job_queued_dir / "result.json").write_text(json.dumps({
        "ok": False, "stage": "queued",
    }), encoding="utf-8")
    waiters.register("20261123-190003-cccc", "Loam", job_queued_dir, "probe3",
                     {"caller": "conversation", "caller_session": s_job_queued.id,
                      "thread": "t-job3"})

    # Queued thread input on disk (e.g., job result already picked up by deliver_job)
    s_queued_in = running(broker, "probe4", "s-queued-in", idle_s=5000, idle_timeout_s=120)
    threads.remember_session("t-in1", "probe4", s_queued_in.id, "claude-code", "m1")
    inp_id = inputs.add("t-in1", "probe4", {"note": "Job finished"})

    await broker.reap_once(now)
    check("plain idle session past small-box timeout is unloaded", s_idle.is_live(), False)
    check("session with queued/running (unfinished) job IS unloaded to free room",
          s_job_queued.is_live(), False)
    check("session with turn in progress is NOT unloaded", s_turn.is_live(), True)
    check("session with tool call waiting on approval is NOT unloaded", s_perm.is_live(), True)
    check("session with background subagent is NOT unloaded", s_sub.is_live(), True)
    check("session with dispatch in flight is NOT unloaded", s_dispatch.is_live(), True)
    check("session with finished job result owed (caller_session) is NOT unloaded",
          s_job_caller.is_live(), True)
    check("session with finished job result owed (thread binding) is NOT unloaded",
          s_job_bound.is_live(), True)
    check("session with queued thread input is NOT unloaded", s_queued_in.is_live(), True)

    # Also verify make_room() refuses to evict a session with a pending job result.
    cfg.memory = lambda: {"available": 2100, "cgroup_room": None, "reserve": 2048}
    try:
        await broker.make_room("probe1")
        evicted_protected = True
    except broker_mod.BrokerError as exc:
        evicted_protected = exc.status != 429
    check("make_room never evicts sessions with active work or owed job results",
          (evicted_protected, s_job_caller.is_live(), s_job_bound.is_live(), s_queued_in.is_live()),
          (False, True, True, True))

    # Also verify end_idle() preserves sessions with owed job results / queued inputs
    # while ending truly idle ones.
    s_endable = running(broker, "probe1", "s-endable", idle_s=40, idle_timeout_s=120)
    srv = broker_mod.Server(broker)
    end_res = await srv.end_idle()
    ended_ids = [r["id"] for r in end_res["ended"]]
    kept_map = {r["id"]: r["why"] for r in end_res["kept"]}
    check("end_idle ends truly idle session and keeps owed-job & queued-input sessions",
          (ended_ids == ["s-endable"],
           kept_map.get(s_job_caller.id),
           kept_map.get(s_job_bound.id),
           kept_map.get(s_queued_in.id)),
          (True, "job_result", "job_result", "queued_input"))
    await srv.threads.handoffs.close()
    srv.threads.stop_all_tails()

    gate.set()
    await s_dispatch._dispatches["d-1"]
    waiters.settle("20261123-190001-aaaa")
    waiters.settle("20261123-190002-bbbb")
    waiters.settle("20261123-190003-cccc")
    inputs.remove("t-in1", "probe4", [inp_id])
    await stop_all(broker)

    # -- 4. Waiting-run relief (loam_lock.room_wanted + SessionHost.relieve) --
    broker = broker_mod.Broker()
    old_idle = running(broker, "probe1", "wait-old", idle_s=45, idle_timeout_s=600)
    new_idle = running(broker, "probe2", "wait-new", idle_s=20, idle_timeout_s=600)
    busy_s = running(broker, "probe3", "wait-busy", idle_s=90, idle_timeout_s=600)
    busy_s._turn_active = True

    # Available sits strictly between the box's reserve and reserve + start allowance.
    reserve_mb = loam_lock.MEMORY_RESERVE_MB
    start_mb = loam_lock.RUN_START_MB
    mid_avail_mb = reserve_mb + start_mb // 2
    cfg.memory = lambda: {"available": mid_avail_mb, "cgroup_room": None, "reserve": reserve_mb}
    (loam_lock.LOCK_DIR / loam_lock.ROOM_WANTED_FILE).unlink(missing_ok=True)
    check("between reserve and reserve+start with no waiting run, relieve does nothing",
          await broker.relieve(time.time()), None)

    # Now simulate a wake/job failing memory admission in loam_lock._try_admit.
    fake_meminfo = Path(loam_lock.LOCK_DIR) / "fake-meminfo"
    fake_meminfo.write_text(
        f"MemTotal: {(reserve_mb + start_mb + 2048) * 1024} kB\n"
        f"MemAvailable: {mid_avail_mb * 1024} kB\n", encoding="utf-8")
    old_meminfo = loam_lock.MEMINFO
    loam_lock.MEMINFO = str(fake_meminfo)
    try:
        handle, r = loam_lock._try_admit({"agent": "probe1", "lane": "wake", "pid": os.getpid()})
        check("loam_lock refuses admission when MemAvailable < reserve + start",
              (handle is None, r["fits"], loam_lock.room_wanted()), (True, False, True))
    finally:
        loam_lock.MEMINFO = old_meminfo

    evicted = await broker.relieve(time.time())
    check("when a wake/job is waiting for room, relieve unloads oldest idle chat",
          (evicted is old_idle, old_idle.is_live(), new_idle.is_live(), busy_s.is_live()),
          (True, False, True, True))
    (loam_lock.LOCK_DIR / loam_lock.ROOM_WANTED_FILE).unlink(missing_ok=True)
    await stop_all(broker)

    # -- 5. Private handoff debounce floor on small boxes --------------------
    old_thread_idle = cfg.THREAD_IDLE_S
    try:
        cfg.THREAD_IDLE_S = 120
        dummy_engine = te.ThreadEngine(broker_mod.Broker())
        dummy_session = broker_mod.Session("probe1", "handoff-floor")
        dummy_session.last_activity = time.time()
        due = dummy_engine.summary_due(te.Turn("t-floor", "probe1", 0, "m1"), dummy_session)
        check("small-box THREAD_IDLE_S=120 keeps private handoff floor at 600 s",
              round(due - dummy_session.last_activity), cfg.DEFAULT_THREAD_IDLE_S)
        await dummy_engine.handoffs.close()
    finally:
        cfg.THREAD_IDLE_S = old_thread_idle

    # -- 6. Lossless unload + resume (classic AND thread) --------------------
    cfg.memory = lambda: {"available": 25000, "cgroup_room": None, "reserve": 2048}
    broker = broker_mod.Broker()
    # Classic session: send turn 1 with model/effort/title/pin, unload via reap_once,
    # resume and send turn 2, confirm conversation history + settings intact.
    c_sid = "classic-continuity"
    c1 = await broker.start_session("probe1", c_sid, model="custom-model", effort="high")
    c1.title = "Shed Paint Plan"
    c1.title_source = "person"
    c1.pinned = True
    c1.seen_seq = 2
    c1.save()

    async def wait_result(session):
        q = asyncio.Queue()
        session.watch(q)
        try:
            while True:
                ev = await asyncio.wait_for(q.get(), 10)
                if ev is None:
                    raise RuntimeError("session exited before result")
                if ev.get("type") == "result":
                    return ev["result"]
        finally:
            session.unwatch(q)

    waiter = asyncio.create_task(wait_result(c1))
    await c1.send_message("code word amber-pine-9")
    res1 = await waiter
    check("classic turn 1 recorded", "turns=1" in res1 and "amber-pine-9" in res1, True)

    # Unload via small-box classic timeout
    c1.idle_timeout_s = 180
    c1.last_activity = time.time() - 200
    await broker.reap_once(time.time())
    check("classic session unloaded cleanly (live=False, status=done)",
          (c1.is_live(), c1.status()), (False, "done"))

    # Resume and send turn 2
    c2 = await broker.start_session("probe1", c_sid, resume=True)
    waiter2 = asyncio.create_task(wait_result(c2))
    await c2.send_message("follow up question")
    res2 = await waiter2
    check("resumed classic session carries conversation with no missed or repeated message",
          ("turns=2" in res2 and "resumed=True" in res2
           and "amber-pine-9" in res2 and "follow up question" in res2
           and res2.count("amber-pine-9") == 1), True)
    check("resumed classic session preserves model, effort, title, pin, seen_seq, turns",
          (c2.model, c2.effort, c2.title, c2.pinned, c2.seen_seq, len(c2.turns),
           "model=custom-model" in res2, "effort=high" in res2),
          ("custom-model", "high", "Shed Paint Plan", True, 2, 2, True, True))
    await stop_all(broker)

    # Thread session: unload after Turn 1 at small-box THREAD_IDLE_S=120,
    # send Turn 2 in thread, confirm resumed session keeps exact conversation,
    # model, and no missed/repeated message.
    old_thread_idle = cfg.THREAD_IDLE_S
    cfg.THREAD_IDLE_S = 120
    try:
        broker = broker_mod.Broker()
        engine = te.ThreadEngine(broker)
        meta = threads.create("probe1", title="Unload Resume Thread")
        tid = meta["id"]
        threads.update(tid, engines={"probe1": "claude-code"},
                       models={"probe1": "thread-model-x"})

        async def post_and_wait(text: str):
            msg = threads.append(tid, threads.OWNER, text)
            engine.schedule(te.Turn(tid, "probe1", 0, msg["id"]))
            for _ in range(200):
                await asyncio.sleep(0.05)
                if (tid, "probe1") not in engine.turns and (
                    (tid, "probe1") not in engine.workers or engine.workers[(tid, "probe1")].done()
                ):
                    _, msgs, _ = threads.read(tid)
                    if msgs and msgs[-1]["author"] == "probe1":
                        return msgs[-1]["text"]
            raise TimeoutError("thread turn did not finish")

        t_res1 = await post_and_wait("remember blue-cedar-42")
        kept1 = threads.session_for(tid, "probe1")
        live1 = broker.sessions[kept1["session_id"]]
        check("thread turn 1 uses small-box THREAD_IDLE_S=120",
              (live1.idle_timeout_s, "turns=1" in t_res1, "blue-cedar-42" in t_res1),
              (120, True, True))

        # Wait past small-box idle window -> unload
        live1.last_activity = time.time() - 150
        await broker.reap_once(time.time())
        check("idle thread session unloaded at 150 s (> 120 s)", live1.is_live(), False)

        # Turn 2 resumes the exact same session ID and model with no duplicate turn 1
        t_res2 = await post_and_wait("confirm what we remembered")
        kept2 = threads.session_for(tid, "probe1")
        check("thread turn 2 resumes same session id, model, and conversation without repeats",
              (kept2["session_id"] == kept1["session_id"],
               "turns=2" in t_res2,
               "resumed=True" in t_res2,
               "model=thread-model-x" in t_res2,
               t_res2.count("blue-cedar-42") == 1,
               "confirm what we remembered" in t_res2),
              (True, True, True, True, True, True))

        await engine.handoffs.close()
        engine.stop_all_tails()
        await stop_all(broker)
    finally:
        cfg.THREAD_IDLE_S = old_thread_idle
        cfg.memory = real_memory

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="loam-idle-unload-") as tmp:
        root = Path(tmp)
        stub = root / "claude"
        stub.write_text(STATEFUL_CLAUDE_STUB, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        os.environ["LOAM_TEST_CLI_STORE"] = str(root / "cli-store")

        labs = root / "ai-labs"
        for i in range(1, 5):
            ws = labs / f"probe{i}"
            ws.mkdir(parents=True)
            (ws / "agent.json").write_text(json.dumps({
                "name": f"Probe {i}", "handle": f"probe{i}",
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
