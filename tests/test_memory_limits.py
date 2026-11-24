#!/usr/bin/env python3
"""Verify scaled memory admission sizing, installer floor, every durable limit
event kind written once, privacy sanitization, bounded rotation, and the single
read path (loam_limits.query, bin/loam-limits, GET /v1/limit-events).

Run: python3 tests/test_memory_limits.py
"""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
import loam_limits as limits  # noqa: E402
import loam_lock  # noqa: E402
import loam_pressure as pressure  # noqa: E402
import loam_thread_engine as te  # noqa: E402
import loam_threads as threads  # noqa: E402

fails = 0


def check(label: str, got, want=True) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} {got!r}"[:220]
          + ("" if ok else f"\n      want {want!r}"))


def load_bin(name: str):
    loader = importlib.machinery.SourceFileLoader(
        name.replace("-", "_"), str(AGENTS / "bin" / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class FakeStdin:
    def write(self, data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None


class FakeProc:
    def __init__(self, pid: int = 424242, rc: int = 0) -> None:
        self.pid = pid
        self.stdin = FakeStdin()
        self.returncode = None
        self._rc = rc

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = self._rc
        return self.returncode


class FakeWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None

    def body(self):
        raw = b"".join(self.chunks).decode()
        head, _, payload = raw.partition("\r\n\r\n")
        return int(head.split()[1]), json.loads(payload)


async def run_tests() -> None:
    root = Path(tempfile.mkdtemp(prefix="loam-limits-test-"))
    ev_file = root / "limits" / "events.jsonl"
    saved_env = os.environ.get("LOAM_LIMIT_EVENTS")
    os.environ["LOAM_LIMIT_EVENTS"] = str(ev_file)

    try:
        # -- 1. Scaled admission across box sizes ----------------------------
        check("this box (31335 MB) yields exact 3760 MB reserve & 450 MB start",
              (limits.memory_reserve_mb(31335), limits.start_allowance_mb(31335)),
              (3760, 450))
        check("loam_broker & loam_lock agree with loam_limits on this host",
              (cfg.MEMORY_RESERVE_MB, cfg.SESSION_START_MB,
               loam_lock.MEMORY_RESERVE_MB, loam_lock.RUN_START_MB),
              (limits.memory_reserve_mb(cfg.BOX_MB), limits.start_allowance_mb(cfg.BOX_MB),
               limits.memory_reserve_mb(cfg.BOX_MB), limits.start_allowance_mb(cfg.BOX_MB)))

        for box_mb, want_res, want_start in (
            (1850, 478, 350),     # decimal 2 GB VPS
            (1930, 487, 350),     # 2 GiB VM
            (2048, 501, 350),     # 2048 MB
            (3700, 700, 350),     # decimal 4 GB VPS
            (3850, 718, 350),     # 4 GiB VM
            (7750, 1186, 350),    # 8 GB CX33
            (8192, 1239, 350),    # 8 GiB
            (12288, 1730, 350),   # upper 8 GB boundary
            (12289, 2048, 450),   # 16 GB+ boundary
            (15600, 2048, 450),   # 16 GiB usable (unchanged)
            (16384, 2048, 450),   # 16 GiB (unchanged)
            (31335, 3760, 450),   # 32 GB (this box, unchanged)
            (63500, 7620, 450),   # 64 GB (unchanged)
        ):
            check(f"{box_mb} MB box -> reserve={want_res}, start={want_start}",
                  (limits.memory_reserve_mb(box_mb), limits.start_allowance_mb(box_mb)),
                  (want_res, want_start))

        # Monotonicity across full range.
        prev_r, prev_s = 0, 0
        mono = True
        for mb in range(1024, 65537, 256):
            r, s = limits.memory_reserve_mb(mb), limits.start_allowance_mb(mb)
            if r < prev_r or s < prev_s:
                mono = False
                break
            prev_r, prev_s = r, s
        check("reserve and start allowance are monotonic across 1..64 GB", mono, True)

        # Installer floor constants.
        check("installer floor allows 2 GB (1850 MB) and rejects 1 GB (1024 MB)",
              (1024 < limits.MIN_INSTALL_MEM_MB,
               1850 >= limits.MIN_INSTALL_MEM_MB,
               1850 < limits.RECOMMENDED_MEM_MB,
               3850 >= limits.RECOMMENDED_MEM_MB),
              (True, True, True, True))

        # -- 2. Privacy sanitization & bounded rotation ----------------------
        rec = limits.record(
            "chat_wait",
            outcome="started",
            subject={"agent": "iris", "thread": "t-1", "message": "SECRET USER TEXT"},
            duration_s=12.34,
            detail={"reason": "memory", "retries": 3, "prompt": "SECRET PROMPT"},
            memory={"total_mb": 1930, "available_mb": 700, "reserve_mb": 487,
                    "start_mb": 350, "swap_total_mb": 2048, "swap_used_mb": 50,
                    "swap_free_mb": 1998},
            ts=1_700_000_000,
        )
        raw_text = ev_file.read_text(encoding="utf-8")
        check("private fields stripped from subject and detail",
              ("SECRET" not in raw_text, rec["subject"], rec["detail"], rec["duration_s"]),
              (True, {"agent": "iris", "thread": "t-1"},
               {"reason": "memory", "retries": 3}, 12.3))

        # Rotation at small max_bytes keeps both .1 and active readable with no duplicates.
        rot_file = root / "limits" / "rot.jsonl"
        for i in range(12):
            limits.record(
                "chat_evicted", outcome="evicted",
                subject={"agent": "iris", "session": f"s-{i}"},
                ts=1_700_000_000 + i * 60,
                path=rot_file, max_bytes=900,
            )
        rot_q = limits.query(days=0, path=rot_file, pressure_path=root / "no-p.json")
        check("rotation creates .1 file and stays bounded",
              (rot_file.with_name(rot_file.name + ".1").exists(),
               rot_file.stat().st_size < 1800,
               rot_q["total"] >= 4),
              (True, True, True))

        # Clear main ev_file before exercising every live emitter once each.
        ev_file.unlink(missing_ok=True)

        # -- 3. chat_wait & chat_timeout (loam_thread_engine) ----------------
        broker_mod = load_bin("session-broker")

        class StubBroker:
            remote = False
            stopping = False

            def __init__(self, fail_times: int):
                self.fail_times = fail_times
                self.calls = 0

            async def start_session(self, agent, sid=None, **kw):
                self.calls += 1
                if self.calls <= self.fail_times:
                    raise broker_mod.BrokerError(
                        429, "busy",
                        {"reason": "memory", "busy": [{"agent": "ada", "status": "working"}],
                         "memory": {"available_mb": 700, "reserve_mb": 487, "need_mb": 350}},
                    )
                return SimpleNamespace(
                    id="sess-waited", agent=agent,
                    configure_thread=None,
                    set_title=lambda *a, **k: None,
                    model=None, effort=None,
                )

        async def noop_cfg(*a, **k):
            return None

        saved_cap = (te.CAP_WAIT_S, te.CAP_RETRY_S, te.configure_thread)
        te.CAP_RETRY_S = 0.01
        te.configure_thread = noop_cfg
        try:
            # 3a. Waits twice, then starts -> exactly 1 chat_wait (started)
            eng_ok = te.ThreadEngine(StubBroker(fail_times=2))
            eng_ok._memory_pins = lambda *a, **k: asyncio.sleep(0)
            turn = te.Turn("t-wait-ok", "iris", 0, "mention")
            eng_ok.turns[turn.key] = {"persona": "iris"}
            with patch.object(threads, "session_for", return_value=None):
                sess, _ = await eng_ok._session(turn, {"title": "Test"}, "claude-code")
            rows = limits.read_events(ev_file)
            check("chat_wait written once when chat starts after retries",
                  [(r["kind"], r["outcome"], r["subject"].get("thread"), r["detail"].get("retries"))
                   for r in rows],
                  [("chat_wait", "started", "t-wait-ok", 2)])

            # 3b. Times out -> exactly 1 chat_timeout (NOT chat_wait)
            te.CAP_WAIT_S = 0.03
            eng_to = te.ThreadEngine(StubBroker(fail_times=99))
            turn2 = te.Turn("t-wait-to", "iris", 0, "mention")
            eng_to.turns[turn2.key] = {"persona": "iris"}
            got_noseat = False
            with patch.object(threads, "session_for", return_value=None):
                try:
                    await eng_to._session(turn2, {"title": "Test"}, "claude-code")
                except te.NoSeat:
                    got_noseat = True
            rows2 = limits.read_events(ev_file)
            check("chat_timeout written once on 5-min NoSeat (no duplicate chat_wait)",
                  (got_noseat, [(r["kind"], r["outcome"], r["subject"].get("thread")) for r in rows2]),
                  (True, [("chat_wait", "started", "t-wait-ok"),
                          ("chat_timeout", "timed_out", "t-wait-to")]))
        finally:
            te.CAP_WAIT_S, te.CAP_RETRY_S, te.configure_thread = saved_cap

        # -- 4. chat_evicted (make_room & relieve) vs ordinary idle timeout --
        ev_file.unlink(missing_ok=True)
        host = broker_mod.Broker()
        saved_mem = cfg.memory

        def make_sess(agent: str, sid: str, idle_s: float):
            s = host.adopt(broker_mod.Session(agent, sid))
            s.state = "running"
            s.proc = FakeProc()
            s.last_activity = time.time() - idle_s
            host.sessions[sid] = s
            return s

        try:
            # 4a. Ordinary idle timeout (ample memory) -> 0 chat_evicted events!
            cfg.memory = lambda: {"available": 10_000, "cgroup_room": None, "reserve": 487}
            s_idle = make_sess("iris", "s-normal-idle", cfg.CLASSIC_IDLE_S + 100)
            await host.reap_once(time.time())
            check("ordinary idle timeout writes ZERO chat_evicted events",
                  (s_idle.is_live(), limits.read_events(ev_file)), (False, []))

            # 4b. make_room evicts idle session for incoming chat -> 1 chat_evicted
            avail = [700]
            cfg.memory = lambda: {"available": avail[0], "cgroup_room": None, "reserve": 487}
            s_vic = make_sess("iris", "s-vic-chat", 120)
            orig_stop = s_vic.stop

            async def freeing_stop(reason=None):
                await orig_stop(reason=reason)
                avail[0] = 1200

            s_vic.stop = freeing_stop
            await host.make_room("ada")
            rows = limits.read_events(ev_file)
            check("make_room writes 1 chat_evicted (trigger=chat_start)",
                  [(r["kind"], r["outcome"], r["subject"].get("session"),
                    r["detail"].get("trigger"), r["detail"].get("for_agent")) for r in rows],
                  [("chat_evicted", "evicted", "s-vic-chat", "chat_start", "ada")])

            # 4c. relieve() for waiting run (room_wanted) -> 1 chat_evicted
            saved_lock_dir = loam_lock.LOCK_DIR
            loam_lock.LOCK_DIR = root / "locks"
            try:
                loam_lock.note_room_wanted()
                cfg.memory = lambda: {"available": 487 + cfg.SESSION_START_MB - 50,
                                      "cgroup_room": None, "reserve": 487}
                s_wait = make_sess("iris", "s-vic-run", 60)
                await host.relieve(time.time())
                rows = limits.read_events(ev_file)
                check("relieve for waiting run writes 1 chat_evicted (trigger=waiting_run)",
                      (s_wait.is_live(), rows[-1]["kind"], rows[-1]["detail"].get("trigger")),
                      (False, "chat_evicted", "waiting_run"))

                # 4d. relieve() under reserve -> 1 chat_evicted
                cfg.memory = lambda: {"available": 400, "cgroup_room": None, "reserve": 487}
                s_res = make_sess("iris", "s-vic-res", 60)
                await host.relieve(time.time())
                rows = limits.read_events(ev_file)
                check("relieve under reserve writes 1 chat_evicted (trigger=under_reserve)",
                      (s_res.is_live(), rows[-1]["kind"], rows[-1]["detail"].get("trigger")),
                      (False, "chat_evicted", "under_reserve"))
            finally:
                loam_lock.LOCK_DIR = saved_lock_dir

            # 4e. private_summary deferred for memory -> 1 summary_deferred
            cfg.memory = lambda: {"available": 400, "cgroup_room": None, "reserve": 487}
            s_sum = make_sess("iris", "s-sum", 10)
            got_429 = False
            try:
                await host.private_summary("s-sum", "summarize")
            except broker_mod.BrokerError as exc:
                got_429 = exc.status == 429
            rows = limits.read_events(ev_file)
            check("private_summary short of memory writes 1 summary_deferred",
                  (got_429, rows[-1]["kind"], rows[-1]["outcome"],
                   rows[-1]["subject"].get("session")),
                  (True, "summary_deferred", "deferred", "s-sum"))
            await s_sum.stop()

            # 4f. Chat OOM kill detected via cgroup memory.events delta -> 1 oom_kill
            ev_file.unlink(missing_ok=True)
            host._oom_kills = 2
            s_oom = host.adopt(broker_mod.Session("iris", "s-oom-chat"))
            s_oom.state = "running"
            s_oom.proc = FakeProc(pid=999991, rc=-9)
            with patch.object(limits, "cgroup_oom_kills", return_value=3), \
                 patch.object(limits, "journal_oom_pid", return_value=False):
                await s_oom._reap()
            rows = limits.read_events(ev_file)
            check("unexpected chat SIGKILL + cgroup oom_kill delta writes 1 oom_kill",
                  [(r["kind"], r["outcome"], r["subject"].get("session"),
                    r["detail"].get("source")) for r in rows],
                  [("oom_kill", "killed", "s-oom-chat", "cgroup_memory_events")])

            # Intentional stop() with -9 does NOT write oom_kill.
            s_stop = host.adopt(broker_mod.Session("iris", "s-clean-stop"))
            s_stop.state = "running"
            s_stop.proc = FakeProc(pid=999992, rc=-9)
            s_stop._stop_requested = True
            with patch.object(limits, "cgroup_oom_kills", return_value=4):
                await s_stop._reap()
            check("intentional stop() writes ZERO oom_kill events",
                  len(limits.read_events(ev_file)), 1)
        finally:
            cfg.memory = saved_mem

        # -- 5. run_wait & run_skipped (loam_lock.hold) ----------------------
        ev_file.unlink(missing_ok=True)
        saved_lock = (loam_lock.LOCK_DIR, loam_lock.MEMINFO)
        mi_path = root / "meminfo"
        loam_lock.LOCK_DIR = root / "locks2"
        loam_lock.MEMINFO = str(mi_path)
        try:
            # 5a. Low memory on 1st check, then enough on 2nd -> 1 run_wait
            calls = [0]
            orig_avail = loam_lock.available_mb

            def flip_avail():
                calls[0] += 1
                if calls[0] == 1:
                    return loam_lock.MEMORY_RESERVE_MB - 10
                return loam_lock.MEMORY_RESERVE_MB + loam_lock.RUN_START_MB + 200

            loam_lock.available_mb = flip_avail
            try:
                mi_path.write_text("MemTotal: 4096000 kB\nMemAvailable: 500000 kB\n"
                                   "SwapTotal: 2048000 kB\nSwapFree: 2000000 kB\n")
                with loam_lock.hold("iris", "reflection", wait_seconds=1.0, poll=0.01):
                    pass
            finally:
                loam_lock.available_mb = orig_avail
            rows = limits.read_events(ev_file)
            check("run_wait written once when wake waits for memory then starts",
                  [(r["kind"], r["outcome"], r["subject"].get("agent"), r["subject"].get("lane"))
                   for r in rows],
                  [("run_wait", "started", "iris", "reflection")])

            # 5b. Memory stays low -> 1 run_skipped (no duplicate run_wait)
            mi_path.write_text("MemTotal: 4096000 kB\nMemAvailable: 100000 kB\n"
                               "SwapTotal: 2048000 kB\nSwapFree: 2000000 kB\n")
            got_busy = False
            try:
                with loam_lock.hold("iris", "cron:nightly", wait_seconds=0.03, poll=0.01):
                    pass
            except loam_lock.Busy:
                got_busy = True
            rows = limits.read_events(ev_file)
            check("run_skipped written once on memory timeout",
                  (got_busy, [(r["kind"], r["outcome"], r["subject"].get("lane")) for r in rows]),
                  (True, [("run_wait", "started", "iris" and "reflection"),
                          ("run_skipped", "timed_out", "cron:nightly")]))

            # 5c. Per-agent lock contention (not memory) writes ZERO limit events
            mi_path.write_text(f"MemTotal: 65536000 kB\n"
                               f"MemAvailable: {(loam_lock.MEMORY_RESERVE_MB + 5000) * 1024} kB\n")
            with loam_lock.hold("iris", "reflection", wait_seconds=0.1, poll=0.01):
                try:
                    with loam_lock.hold("iris", "cron:overlap", wait_seconds=0.03, poll=0.01):
                        pass
                except loam_lock.Busy:
                    pass
            check("agent-lock contention writes ZERO memory limit events",
                  len(limits.read_events(ev_file)), 2)
        finally:
            loam_lock.LOCK_DIR, loam_lock.MEMINFO = saved_lock

        # -- 6. job_queued (bin/engineer-job:take_slot) ----------------------
        ev_file.unlink(missing_ok=True)
        ej = load_bin("engineer-job")
        jdir = root / "job-q"
        jdir.mkdir()
        (jdir / "brief.md").write_text("---\nbriefed_by: iris\nthread: t-job\ntitle: test\n---\n")
        jp = {"dir": jdir, "brief": jdir / "brief.md", "queued": jdir / "queued",
              "cancelled": jdir / "cancelled", "result": jdir / "result.json"}
        fake_proj = SimpleNamespace(name="Loam", lock_name="Loam", jobs=root)
        saved_ej = (ej.QUEUE_POLL_SECONDS, ej.queued_ahead, ej.loam_lock.hold_slot)
        ej.QUEUE_POLL_SECONDS = 0.01
        ej.queued_ahead = lambda jid: 0
        slot_calls = [0]

        class DummyLock:
            def __enter__(self): return self
            def __exit__(self, *a): return None

        def fake_hold_slot(*a, **k):
            slot_calls[0] += 1
            if slot_calls[0] <= 2:
                raise loam_lock.Busy("box short of memory")
            return DummyLock()

        ej.loam_lock.hold_slot = fake_hold_slot
        try:
            lk = ej.take_slot(fake_proj, "20261123-000001-qtest", "job:20261123-000001-qtest", jp)
            rows = limits.read_events(ev_file)
            check("job_queued written once when queued job starts",
                  (lk is not None,
                   [(r["kind"], r["outcome"], r["subject"].get("job"),
                     r["subject"].get("agent"), r["detail"].get("retries")) for r in rows]),
                  (True, [("job_queued", "started", "20261123-000001-qtest", "iris", 2)]))

            # 6b. Cancelled while waiting -> 1 job_queued (outcome=cancelled)
            jdir2 = root / "job-q-cancel"
            jdir2.mkdir()
            (jdir2 / "brief.md").write_text("---\nbriefed_by: iris\nthread: t-job2\ntitle: cancel\n---\n")
            jp2 = {"dir": jdir2, "brief": jdir2 / "brief.md", "queued": jdir2 / "queued",
                   "cancelled": jdir2 / "cancelled", "result": jdir2 / "result.json"}
            cancel_calls = [0]

            def cancel_hold_slot(*a, **k):
                cancel_calls[0] += 1
                if cancel_calls[0] >= 2:
                    jp2["cancelled"].write_text(json.dumps({"at": "2026-11-23T20:00:00Z", "by": "owner"}))
                raise loam_lock.Busy("box short of memory")

            ej.loam_lock.hold_slot = cancel_hold_slot
            saved_wc = ej.write_cancelled
            ej.write_cancelled = lambda *a, **k: None
            try:
                lk2 = ej.take_slot(fake_proj, "20261123-000002-qcancel",
                                   "job:20261123-000002-qcancel", jp2)
            finally:
                ej.write_cancelled = saved_wc
            rows = limits.read_events(ev_file)
            check("job_queued written once when cancelled while waiting",
                  (lk2 is None,
                   [(r["kind"], r["outcome"], r["subject"].get("job")) for r in rows]),
                  (True, [("job_queued", "started", "20261123-000001-qtest"),
                          ("job_queued", "cancelled", "20261123-000002-qcancel")]))

            # 6c. engineer-job cgroup OOM kill recording
            ev_file.unlink(missing_ok=True)
            check("record_job_oom ignores zero kills",
                  (ej.record_job_oom(fake_proj, "20261123-000001-qtest",
                                     "job:20261123-000001-qtest", jp, {"oom_kill": 0}, 0, "claude"),
                   len(limits.read_events(ev_file))),
                  (False, 0))
            check("record_job_oom records 1 oom_kill when cgroup oom_kill > 0",
                  (ej.record_job_oom(fake_proj, "20261123-000001-qtest",
                                     "job:20261123-000001-qtest", jp, {"oom_kill": 2}, -9, "claude"),
                   [(r["kind"], r["outcome"], r["subject"].get("job"),
                     r["detail"].get("oom_kills")) for r in limits.read_events(ev_file)]),
                  (True, [("oom_kill", "killed", "20261123-000001-qtest", 2)]))
        finally:
            ej.QUEUE_POLL_SECONDS, ej.queued_ahead, ej.loam_lock.hold_slot = saved_ej

        # -- 6d. wake-failed OnFailure oom-kill vs exit-code -----------------
        ev_file.unlink(missing_ok=True)
        wf = load_bin("wake-failed")
        st_dir = root / "wf-state"
        st_dir.mkdir()
        fake_wake = SimpleNamespace(
            STATE_DIR=st_dir,
            record_failure=lambda *a, **k: None,
            log_line=lambda *a, **k: None,
        )
        saved_wf = (wf.load_wake, wf.unit_property, sys.argv)
        wf.load_wake = lambda: fake_wake
        try:
            import loam_schedule as lsched
            with patch.object(lsched, "record_fire", return_value=None):
                # oom-kill -> 1 oom_kill event
                (st_dir / "iris.running").write_text(
                    json.dumps({"ts": "2026-11-23T20:00:00Z", "lane": "reflection", "vault": str(root)}))
                wf.unit_property = lambda u, n: {"Result": "oom-kill", "ExecMainCode": "killed",
                                                 "ExecMainStatus": "9"}.get(n, "")
                sys.argv = ["wake-failed", "loam-wake-iris.service"]
                rc_oom = wf.main()
                # exit-code -> 0 oom_kill events
                (st_dir / "iris.running").write_text(
                    json.dumps({"ts": "2026-11-23T20:01:00Z", "lane": "reflection", "vault": str(root)}))
                wf.unit_property = lambda u, n: {"Result": "exit-code", "ExecMainCode": "exited",
                                                 "ExecMainStatus": "1"}.get(n, "")
                rc_ex = wf.main()
            rows = limits.read_events(ev_file)
            check("wake-failed writes oom_kill on Result=oom-kill and NOT on exit-code",
                  (rc_oom, rc_ex,
                   [(r["kind"], r["subject"].get("agent"), r["subject"].get("unit"),
                     r["detail"].get("source")) for r in rows]),
                  (0, 0, [("oom_kill", "iris", "loam-wake-iris", "systemd_onfailure")]))
        finally:
            wf.load_wake, wf.unit_property, sys.argv = saved_wf

        # -- 7. pressure_episode & sustained_swap (lib/loam_pressure.py) -----
        ev_file.unlink(missing_ok=True)
        p_file = root / "pressure.json"
        p_log = root / "pressure.jsonl"
        saved_p = (pressure.PRESSURE, pressure.EVENTS, pressure.now, pressure.meminfo, pressure.heavy_units)
        clock = [1_700_000_000.0]
        cur_mi = [{}]
        pressure.PRESSURE = p_file
        pressure.EVENTS = p_log
        pressure.now = lambda: clock[0]
        pressure.meminfo = lambda: cur_mi[0]
        pressure.heavy_units = lambda top=3: ["loam-session-host 900 MB"]
        try:
            # 7a. Pressure episode: 4 ticks over 85% RAM, then recovery -> 1 pressure_episode
            cur_mi[0] = {"MemTotal": 2000, "MemAvailable": 200, "SwapTotal": 2048, "SwapFree": 2000}
            for _ in range(4):
                pressure.tick()
                clock[0] += 60
            # Active episode visible in query()["active"] before end, 0 finished yet!
            q_mid = limits.query(days=7, path=ev_file, pressure_path=p_file, now=clock[0])
            check("active pressure episode appears in query()['active'] before close",
                  (q_mid["total"], [a["kind"] for a in q_mid["active"]]),
                  (0, ["pressure_episode"]))
            # Recover
            cur_mi[0] = {"MemTotal": 2000, "MemAvailable": 1200, "SwapTotal": 2048, "SwapFree": 2000}
            pressure.tick()
            rows = limits.read_events(ev_file)
            check("pressure_episode written exactly once on episode end",
                  [(r["kind"], r["outcome"], r["detail"].get("ram_pct")) for r in rows],
                  [("pressure_episode", "ended", 90)])

            # 7b. Sustained swap (RAM healthy 55%, swap 600/2048 MB = 29% for 6 min) -> 1 sustained_swap
            clock[0] += 60
            cur_mi[0] = {"MemTotal": 2000, "MemAvailable": 900, "SwapTotal": 2048, "SwapFree": 1448}
            for _ in range(6):
                st = pressure.tick()
                clock[0] += 60
            check("sustained swap does NOT alarm pressure episode_since",
                  (st.get("episode_since"), bool(st.get("swap_episode_since"))),
                  (None, True))
            q_sw = limits.query(days=7, path=ev_file, pressure_path=p_file, now=clock[0])
            check("active sustained_swap appears in query()['active']",
                  [a["kind"] for a in q_sw["active"]], ["sustained_swap"])
            # Swap drops back down
            cur_mi[0] = {"MemTotal": 2000, "MemAvailable": 1100, "SwapTotal": 2048, "SwapFree": 1950}
            pressure.tick()
            rows = limits.read_events(ev_file)
            check("sustained_swap written exactly once on end",
                  [(r["kind"], r["outcome"]) for r in rows],
                  [("pressure_episode", "ended"), ("sustained_swap", "ended")])
        finally:
            pressure.PRESSURE, pressure.EVENTS, pressure.now, pressure.meminfo, pressure.heavy_units = saved_p

        # -- 8. Single read path: CLI (bin/loam-limits) & broker route -------
        # Add one old event (30 days ago) and one recent event (now).
        now_ts = time.time()
        ev_file.unlink(missing_ok=True)
        limits.record("oom_kill", outcome="killed", subject={"agent": "iris", "lane": "reflection"},
                      ts=now_ts - 20 * 86400, path=ev_file)
        limits.record("chat_wait", outcome="started", subject={"agent": "iris", "thread": "t-now"},
                      duration_s=15.0, ts=now_ts - 60, path=ev_file)
        limits.record("chat_evicted", outcome="evicted", subject={"agent": "ada", "session": "s-1"},
                      ts=now_ts - 30, path=ev_file)

        # CLI --json (7 days sees 2; 30 days sees 3; --kind filters).
        cli_env = {**os.environ, "LOAM_LIMIT_EVENTS": str(ev_file)}
        r7 = subprocess.run([sys.executable, str(AGENTS / "bin" / "loam-limits"),
                             "--days", "7", "--json"], capture_output=True, text=True, env=cli_env)
        d7 = json.loads(r7.stdout)
        check("loam-limits --days 7 --json returns counts & 2 recent events",
              (r7.returncode, d7["total"], d7["counts"]["chat_wait"],
               d7["counts"]["chat_evicted"], d7["counts"]["oom_kill"], len(d7["events"])),
              (0, 2, 1, 1, 0, 2))
        r30 = subprocess.run([sys.executable, str(AGENTS / "bin" / "loam-limits"),
                              "--days", "30", "--kind", "oom_kill", "--json"],
                             capture_output=True, text=True, env=cli_env)
        d30 = json.loads(r30.stdout)
        check("loam-limits --days 30 --kind oom_kill filters accurately",
              (r30.returncode, d30["total"], d30["counts"]["oom_kill"], len(d30["events"])),
              (0, 1, 1, 1))
        rtxt = subprocess.run([sys.executable, str(AGENTS / "bin" / "loam-limits"),
                               "--days", "7"], capture_output=True, text=True, env=cli_env)
        check("loam-limits plain text prints readable counts summary",
              (rtxt.returncode == 0, "Total events: 2" in rtxt.stdout,
               "chat_wait" in rtxt.stdout),
              (True, True, True))

        # Broker route GET /v1/limit-events.
        srv = broker_mod.Server(broker_mod.Broker())
        w = FakeWriter()
        await srv.route("GET", "/v1/limit-events", {"days": "7", "limit": "1"}, {}, w)
        st_code, body = w.body()
        check("GET /v1/limit-events?days=7&limit=1 returns full counts & truncated=True",
              (st_code, body["total"], body["counts"]["chat_wait"],
               len(body["events"]), body["truncated"]),
              (200, 2, 1, 1, True))

        w_bad = FakeWriter()
        await srv.route("GET", "/v1/limit-events", {"kind": "not_a_real_kind"}, {}, w_bad)
        st_bad, _ = w_bad.body()
        check("GET /v1/limit-events rejects unknown kind with 400", st_bad, 400)

    finally:
        if saved_env is None:
            os.environ.pop("LOAM_LIMIT_EVENTS", None)
        else:
            os.environ["LOAM_LIMIT_EVENTS"] = saved_env
        shutil.rmtree(root, ignore_errors=True)


asyncio.run(run_tests())
print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
