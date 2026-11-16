#!/usr/bin/env python3
"""Admission by memory (2026-11-09), seats, idle stops, and the light list.

The owner, 11-09: "I want to make sure that I can have as many sessions as I
need ... and I'm not bottlenecked by the number of sessions or anything
else." Proved here, against the broker's own objects and a simulated box
(`Box`: a RAM size, what the rest of the box uses, and each live session's
own size, so ending one really frees what it held and one can grow):

  1. **Its own live session is reused before admission is asked.**
  2. **No count cap**: on a roomy box starts go far past the old four, and
     nothing is ended to make them.
  3. **Short of memory the least recently used idle session is ended**,
     thread or classic; one mid-turn, asking, waiting on its children or
     active within `cfg.EVICT_GRACE_S` never is.
  4. **Only an all-busy, short box refuses**, in a person's sentence, with
     the route text in `hint` and the box's numbers in `memory`; the count
     backstop refuses the same way.
  5. **Pressure**: a box whose sessions grew under the reserve gets one idle
     session ended per reaper pass.
  6. **Idle stops**: a thread session after `THREAD_IDLE_S`, a classic chat
     after `CLASSIC_IDLE_S`, never while busy, however long (no clock).
  7. **OOM order**: a chat runs at `CHILD_OOM_SCORE_ADJ`, read off its pid.
  8. **The admission measured** on simulated 8 GB and 32 GB boxes.
  9. **The list is light** (`list_row`).

Real processes against a stub binary, as tests/test_broker_residency.py
does: what is counted is a process.

Run: python3 tests/test_broker_seats.py
"""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results = []

STUB = """#!/usr/bin/env python3
import sys
for line in sys.stdin:
    pass
"""


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


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


class FakeWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None

    def text(self) -> str:
        return b"".join(self.chunks).decode()

    def body(self):
        head, _, payload = self.text().partition("\r\n\r\n")
        return int(head.split()[1]), json.loads(payload)

    def items(self) -> list[dict]:
        return [json.loads(line[len("data: "):])
                for line in self.text().splitlines()
                if line.startswith("data: ")]


async def start(broker, agent):
    """A start as a thread turn makes one (the classic POST /v1/sessions
    retired 2026-11-12), answered as a route would."""
    try:
        session = await broker.start_session(agent)
    except broker_mod.BrokerError as exc:
        return exc.status, {"error": exc.message, **exc.detail}
    return 201, session.meta()


async def route(server, method, path, params=None, payload=None):
    writer = FakeWriter()
    try:
        await server.route(method, path, params or {}, payload or {}, writer)
    except broker_mod.BrokerError as exc:
        return exc.status, {"error": exc.message, **exc.detail}
    return writer.body()


def running(broker, agent, session_id, idle_s=0.0):
    session = broker.adopt(broker_mod.Session(agent, session_id))
    session.state = "running"
    session.proc = FakeProc()
    session.last_activity = time.time() - idle_s
    broker.sessions[session.id] = session
    return session


def fill(broker, idles):
    """One running session per entry, idle that many seconds."""
    return [running(broker, f"probe{n + 1}", f"seat-{n + 1}", idle)
            for n, idle in enumerate(idles)]


async def stop_all(broker):
    for session in list(broker.sessions.values()):
        await session.stop()


class Box:
    """A simulated box: `total` MB of RAM, `base` MB used by everything that
    is not a chat, and each live session's own size (`sizes`, default
    `cfg.SESSION_START_MB`). `memory()` answers what `cfg.memory` would."""

    def __init__(self, broker, total, base):
        self.broker, self.total, self.base = broker, total, base
        self.sizes: dict[str, int] = {}
        self.reserve = max(2048, total * 12 // 100)

    def used(self) -> int:
        return self.base + sum(self.sizes.get(s.id, cfg.SESSION_START_MB)
                               for s in self.broker.live())

    def memory(self) -> dict:
        return {"available": self.total - self.used(), "cgroup_room": None,
                "reserve": self.reserve}


def on(box):
    cfg.memory = box.memory
    return box


async def main() -> None:
    real_memory = cfg.memory
    real_max = cfg.MAX_SESSIONS

    # -- 1. its own live session first ------------------------------------
    broker = broker_mod.Broker()
    seats = fill(broker, [600 + n for n in range(4)])
    on(Box(broker, 8000, 8000))            # nothing left at all
    own = seats[1]
    got = await broker.start_session(own.agent, own.id, resume=True)
    check("on a full box, a start naming a live session gets that session",
          got is own, True)
    check("...and ends nobody to do it",
          sorted(s.id for s in broker.live()), sorted(s.id for s in seats))
    await stop_all(broker)

    # -- 2. no count cap -----------------------------------------------------
    broker = broker_mod.Broker()
    box = on(Box(broker, 32000, 6000))
    started = [await broker.start_session(f"probe{n % 9 + 1}") for n in range(12)]
    check("a roomy box takes twelve sessions, three times the old cap",
          (len(broker.live()), all(s.is_live() for s in started)), (12, True))
    check("...and the backstop is far above that",
          cfg.MAX_SESSIONS >= 30, True)
    await stop_all(broker)

    # -- 3. short of memory, the least recently used idle one goes ------
    broker = broker_mod.Broker()
    seats = fill(broker, [3000 - 100 * n for n in range(4)])
    # 8 GB with 4 GB used elsewhere: four 450s leave 2,200, under the 2,048
    # reserve plus a 450 start; one ended leaves 2,650, which fits.
    box = on(Box(broker, 8000, 4000))
    seats[0]._turn_active = True                     # mid-turn
    seats[1].pending["req"] = {"event": {"type": "broker.permission_request"}}
    victim = seats[2]
    new = await broker.start_session("probe9")
    check("short of memory a start is still accepted", new.is_live(), True)
    check("...the idle session used least recently was ended for it",
          (victim.is_live(), victim.state), (False, "exited"))
    check("...never the one mid-turn, nor the one asking, though both are "
          "older", (seats[0].is_live(), seats[1].is_live()), (True, True))
    check("the one ended keeps its id, and is not a failure: it resumes",
          (victim.id in broker.sessions, victim.status() != "failed"), (True, True))
    await stop_all(broker)

    # It ends as many as it takes, oldest first, and no more.
    broker = broker_mod.Broker()
    seats = fill(broker, [900 - 100 * n for n in range(6)])
    box = on(Box(broker, 8000, 3000))
    for s in seats:
        box.sizes[s.id] = 500            # 3,000 + 3,000 = 2,000 available
    await broker.start_session("probe9")
    check("it ends the oldest idle sessions until a start fits, and no more",
          [s.is_live() for s in seats], [False, True, True, True, True, True])
    await stop_all(broker)

    broker = broker_mod.Broker()
    seats = fill(broker, [900 - 100 * n for n in range(4)])
    box = on(Box(broker, 8000, 4500))
    seats[0].idle_timeout_s = cfg.THREAD_IDLE_S      # a thread member
    seats[0].last_activity = time.time() - 100       # but the freshest
    await broker.start_session("probe9")
    check("thread or classic, the least recently used idle one goes",
          (seats[0].is_live(), seats[1].is_live()), (True, False))
    await stop_all(broker)

    # -- 4. every session busy and the box short: refused, in words ----------
    broker = broker_mod.Broker()
    seats = fill(broker, [1000] * 4)
    box = on(Box(broker, 8000, 5000))
    for s in seats[:-1]:
        s._turn_active = True
    seats[-1].background_tasks = [{"task_id": "t", "task_type": "bash"}]
    status, body = 0, {}
    try:
        await broker.start_session("probe9")
    except broker_mod.BrokerError as exc:
        status, body = exc.status, {"error": exc.message, **exc.detail}
    check("a session waiting on work it launched is not ended: every session "
          "busy on a short box is a 429", status, 429)
    check("...naming who holds the box and doing what",
          sorted((b["agent"], b["status"]) for b in body.get("busy", [])),
          sorted([(f"probe{n + 1}", "working") for n in range(3)]
                 + [("probe4", "waiting")]))
    check("...in a person's sentence about memory, with no route in it",
          (body.get("error", "").startswith(
              "There's no room for Probe 9 right now: the box is short of memory"),
           "DELETE" in body.get("error", ""), "/v1/" in body.get("error", "")),
          (True, False, False))
    check("...the route-level advice kept for an API caller in `hint`",
          "DELETE /v1/sessions/<id>" in body.get("hint", ""), True)
    check("...and the box's numbers beside it",
          (body.get("reason"), body.get("memory", {}).get("available_mb"),
           body.get("memory", {}).get("reserve_mb"),
           body.get("memory", {}).get("need_mb")),
          ("memory", 8000 - 5000 - 1800, 2048, cfg.SESSION_START_MB))
    await stop_all(broker)

    broker = broker_mod.Broker()
    seats = fill(broker, [cfg.EVICT_GRACE_S - 5] * 4)
    on(Box(broker, 8000, 5000))
    try:
        await broker.start_session("probe9")
        status = 201
    except broker_mod.BrokerError as exc:
        status = exc.status
    check("a session active within the grace is not one to end",
          (status, all(s.is_live() for s in seats)), (429, True))
    await stop_all(broker)

    # The backstop: plenty of memory, the count at the sanity number.
    broker = broker_mod.Broker()
    cfg.MAX_SESSIONS = 3
    seats = fill(broker, [1000] * 3)
    on(Box(broker, 32000, 1000))
    for s in seats:
        s._turn_active = True
    status, body = await start(broker, "probe9")
    check("the count backstop refuses the same way, saying it is the backstop",
          (status, body.get("reason"), "backstop of 3 sessions" in body.get("error", "")),
          (429, "backstop", True))
    seats[0]._turn_active = False
    seats[0].last_activity = time.time() - 100
    status, record = await start(broker, "probe9")
    check("...and like memory, an idle session is ended before it refuses",
          (status, seats[0].is_live()), (201, False))
    cfg.MAX_SESSIONS = real_max
    await stop_all(broker)

    # -- 5. pressure: sessions that grew past the reserve -----------------
    broker = broker_mod.Broker()
    seats = fill(broker, [500, 400, 300])
    box = on(Box(broker, 8000, 4000))
    box.sizes[seats[2].id] = 2500       # one chat grew: 8000-4000-3400 = 600
    seats[2]._turn_active = True
    ended = await broker.relieve(time.time())
    check("under the reserve, one idle session leaves per pass, the oldest",
          (ended is seats[0], seats[0].is_live(), seats[1].is_live()),
          (True, False, True))
    check("...never the busy one that grew",
          seats[2].is_live(), True)
    box.base = 1000
    check("with room again, a pass ends nothing",
          await broker.relieve(time.time()), None)
    await stop_all(broker)

    # -- 6. the idle stops ---------------------------------------------------
    broker = broker_mod.Broker()
    on(Box(broker, 32000, 1000))
    thread_idle = running(broker, "probe1", "thread-idle", cfg.THREAD_IDLE_S + 30)
    thread_idle.idle_timeout_s = cfg.THREAD_IDLE_S
    thread_busy = running(broker, "probe2", "thread-busy", cfg.THREAD_IDLE_S + 30)
    thread_busy.idle_timeout_s = cfg.THREAD_IDLE_S
    thread_busy._turn_active = True
    thread_fresh = running(broker, "probe3", "thread-fresh", cfg.THREAD_IDLE_S - 30)
    thread_fresh.idle_timeout_s = cfg.THREAD_IDLE_S
    classic = running(broker, "probe4", "classic", cfg.THREAD_IDLE_S + 30)
    classic_old = running(broker, "probe5", "classic-old", cfg.CLASSIC_IDLE_S + 30)
    classic_busy = running(broker, "probe6", "classic-busy", cfg.CLASSIC_IDLE_S + 30)
    classic_busy._turn_active = True
    await broker.reap_once(time.time())
    check("a thread session idle past its short time is ended",
          thread_idle.is_live(), False)
    check("...one mid-turn is not, however long since its last event",
          thread_busy.is_live(), True)
    check("...nor one inside its time", thread_fresh.is_live(), True)
    check("a classic chat idle ten minutes is kept", classic.is_live(), True)
    check("...one idle past CLASSIC_IDLE_S leaves memory",
          classic_old.is_live(), False)
    check("...unless it is mid-turn", classic_busy.is_live(), True)
    # A turn has no clock (2026-11-10): two hours on, the busy ones are
    # still alive; only the idle one with nothing outstanding has gone.
    classic_waiting = running(broker, "probe7", "classic-waiting", 7200)
    classic_waiting.background_tasks = [{"task_id": "b1", "description": "long"}]
    await broker.reap_once(time.time() + 7200)
    check("two hours on: the idle one goes, the busy ones never",
          (classic.is_live(), thread_busy.is_live(), classic_busy.is_live(),
           classic_waiting.is_live()),
          (False, True, True, True))
    check("...the waiting chat with a background task out is kept",
          classic_waiting.busy(), "waiting")
    await stop_all(broker)

    broker = broker_mod.Broker()
    sid = "thread-resume"
    first = await broker.start_session("probe1", sid)
    first.idle_timeout_s = cfg.THREAD_IDLE_S
    first.last_activity = time.time() - cfg.THREAD_IDLE_S - 30
    await broker.reap_once(time.time())
    check("the idle stop ends the process", first.is_live(), False)
    again = await broker.start_session("probe1", sid, resume=True)
    check("the next turn resumes the same id", (again.id, again.is_live()),
          (sid, True))

    # -- 7. the OOM order ----------------------------------------------------
    if os.access(broker_mod.CHOOM, os.X_OK):
        # `choom` sets the score in the child and then execs: a read in the
        # first milliseconds can land before it has run.
        for _ in range(100):
            adj = Path(f"/proc/{again.proc.pid}/oom_score_adj").read_text().strip()
            if adj == str(cfg.CHILD_OOM_SCORE_ADJ):
                break
            await asyncio.sleep(0.01)
        check("a chat's process runs at CHILD_OOM_SCORE_ADJ, first to go",
              adj, str(cfg.CHILD_OOM_SCORE_ADJ))
        # The broker's unit runs at CORE_OOM_SCORE_ADJ; this test's own
        # process may not (inside an engineer job or a landing it runs at
        # AGENT_OOM_SCORE_ADJ since 2026-11-16, equal to the child's).
        check("...above the broker's own",
              int(adj) > cfg.lc.CORE_OOM_SCORE_ADJ, True)
    check("first_to_go keeps the command line whole behind choom",
          broker_mod.first_to_go(["claude", "-p"])[-2:], ["claude", "-p"])
    await stop_all(broker)

    # -- 8. the admission, measured ------------------------------------------
    for total, base, label in ((7750, 4100, "8 GB"), (31600, 6000, "32 GB")):
        broker = broker_mod.Broker()
        box = on(Box(broker, total, base))
        cfg.MAX_SESSIONS = max(16, total // 256)   # the backstop that box has
        n, refused = 0, None
        while n < 200:
            try:
                s = await broker.start_session(f"probe{n % 9 + 1}")
            except broker_mod.BrokerError as exc:
                refused = exc.detail.get("reason")
                break
            s._turn_active = True          # all busy: nothing to end
            n += 1
        print(f"      {label} box, {base} MB used elsewhere, every session "
              f"busy: {n} admitted, then {refused!r}; reserve {box.reserve} MB, "
              f"backstop {cfg.MAX_SESSIONS}")
        want = (total - base - box.reserve) // cfg.SESSION_START_MB
        check(f"the {label} box admits what its memory holds, and no count stops it",
              (n, refused), (want, "memory"))
        await stop_all(broker)
    cfg.memory = real_memory
    cfg.MAX_SESSIONS = real_max

    # -- 5. the light list --------------------------------------------------
    turns = ([{"model": "claude-fable-5-1", "context": {"used": 900, "window": 10}}]
             + [{"model": None, "context": {"used": 5, "window": 10},
                 "cost_delta_usd": 0.1}] * 30
             + [{"model": None, "context": {"used": None, "window": None}}])
    tail = broker_mod._tail_turns(turns)
    check("a row's tail ends at the latest turn", tail[-1] is turns[-1], True)
    check("...and is at most LIST_TURNS_MAX turns",
          len(tail) <= broker_mod.LIST_TURNS_MAX, True)
    model_turns = ([{"model": "m-old"}] + [{"model": "m-new", "context": {"used": 3, "window": 9}}]
                   + [{"model": None, "context": {"used": 4, "window": 9}}] * 3)
    tail = broker_mod._tail_turns(model_turns)
    check("...reaching back to the latest turn that names its model",
          (tail[0]["model"], len(tail)), ("m-new", 4))
    after = [{"model": "m", "context": {"used": 7, "window": 99}},
             {"model": None, "context": {"used": None, "window": None}}]
    check("...and past a compaction to the window before it",
          broker_mod._tail_turns(after), after)

    broker = broker_mod.Broker()
    server = broker_mod.Server(broker)
    heavy = running(broker, "probe1", "heavy")
    heavy.turns = [{"model": "m", "context": {"used": n, "window": 100},
                    "cost_delta_usd": 0.01, "text": "x" * 2000}
                   for n in range(60)]
    heavy.save()
    status, body = await route(server, "GET", "/v1/sessions")
    row = [r for r in body["sessions"] if r["session_id"] == "heavy"][0]
    check("GET /v1/sessions: the row carries the tail and the total",
          (len(row["turns"]), row["turns_total"], row["turns"][-1]["context"]["used"]),
          (1, 60, 59))
    status, full = await route(server, "GET", "/v1/sessions",
                               params={"turns": "all"})
    row = [r for r in full["sessions"] if r["session_id"] == "heavy"][0]
    check("...`?turns=all` answers them whole, for a client that read them",
          (len(row["turns"]), "turns_total" in row), (60, False))
    status, one = await route(server, "GET", "/v1/sessions/heavy")
    check("...and the per-session route always does", len(one["turns"]), 60)
    print(f"      list bytes, this fixture: slim {len(json.dumps(body))}, "
          f"whole {len(json.dumps(full))}")

    watcher = FakeWriter()
    stream = asyncio.create_task(server.stream_records(watcher))
    await asyncio.sleep(0.05)
    heavy.save()
    await asyncio.sleep(0.05)
    stream.cancel()
    rows = [e for e in watcher.items() if e.get("session_id") == "heavy"]
    check("the live list's opening burst and its changes are rows too",
          [(len(e["turns"]), e["turns_total"]) for e in rows], [(1, 60), (1, 60)])
    await stop_all(broker)


root = Path(tempfile.mkdtemp(prefix="loam-broker-seats-test-"))
saved = {name: getattr(cfg, name) for name in
         ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "AI_LABS", "CLAUDE")}
try:
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.AI_LABS = root / "agents"
    stub = root / "claude-stub"
    stub.write_text(STUB, encoding="utf-8")
    os.chmod(stub, stat.S_IRWXU)
    cfg.CLAUDE = str(stub)
    for n in range(1, 10):
        (cfg.AI_LABS / f"probe{n}").mkdir(parents=True)
        (cfg.AI_LABS / f"probe{n}" / "agent.json").write_text(
            json.dumps({"display": f"Probe {n}"}), encoding="utf-8")
    asyncio.run(main())
finally:
    for name, value in saved.items():
        setattr(cfg, name, value)
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
