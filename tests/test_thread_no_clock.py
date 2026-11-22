#!/usr/bin/env python3
"""Advance the clock during active and background work and check that both can still finish."""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
for who in ("ada", "iris", "cleo"):
    (TMP / "ai-labs" / who).mkdir(parents=True)
    (TMP / "ai-labs" / who / "agent.json").write_text(
        json.dumps({"name": who.capitalize()}))
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_threads as threads  # noqa: E402
OWNER, OWNER_NAME = cfg.lc.OWNER_ID, cfg.lc.OWNER_NAME

cfg.roster = threads.roster
cfg.roster_cards = lambda: [{"id": p} for p in threads.roster()]
cfg.VAULT = TMP / "vault"
cfg.ATTACHMENT_ROOT = cfg.VAULT / "98_ATTACHMENTS"

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)
engine_mod = broker_mod.loam_thread_engine
steps_mod = broker_mod.thread_steps

results = []


def check(label, got, want):
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


class FakeWriter:
    def __init__(self):
        self.chunks = []

    def write(self, data):
        self.chunks.append(data)

    async def drain(self):
        return None

    def json(self):
        text = b"".join(self.chunks).decode()
        return int(text.split()[1]), json.loads(text.split("\r\n\r\n", 1)[1])

    def items(self):
        text = b"".join(self.chunks).decode()
        return [json.loads(line[6:]) for line in text.splitlines()
                if line.startswith("data: ")]


def text(t, model=None):
    message = {"content": [{"type": "text", "text": t}]}
    if model:
        message["model"] = model
    return {"type": "assistant", "parent_tool_use_id": None, "message": message}


def init(model=None):
    return {"type": "system", "subtype": "init", **({"model": model} if model else {})}


def result(n):
    return {"type": "result", "parent_tool_use_id": None, "num_turns": n}


class Session:
    n = 0
    first = None   # the script the next new session's first message plays

    def __init__(self, agent, provider):
        Session.n += 1
        self.agent, self.provider, self.id = agent, provider, f"sess-{Session.n}"
        self.model = None
        self.subs = set()
        self.script = []
        self.background_tasks = []
        self.behind = False
        self.turn_active = False
        self.alive = True
        self.thread_id = "019a-codex-thread" if provider == "codex" else None
        self.log = []
        self.seq = 0

    def save(self):
        pass

    def is_live(self):
        return self.alive

    def status(self):
        return "working" if self.turn_active else (
            "waiting" if self.working_behind() else "done")

    def watch(self, q):
        self.subs.add(q)

    def unwatch(self, q):
        self.subs.discard(q)

    def working_behind(self):
        return self.behind or bool(self.background_tasks)

    def replay(self, since):
        return [e for e in self.log if e["seq"] > since]

    def emit(self, ev):
        self.seq += 1
        ev = {**ev, "seq": self.seq}
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            self.turn_active = True
        if ev.get("type") == "result":
            self.turn_active = False
        self.log.append(ev)
        for q in list(self.subs):
            q.put_nowait(ev)

    def tasks(self, *names):
        """The harness's list of background tasks, whole, as it says it."""
        self.background_tasks = [{"task_id": n, "description": n} for n in names]
        self.emit({"type": "broker.background_tasks", "count": len(names),
                   "tasks": self.background_tasks})

    async def send_message(self, prompt, attachments=None):
        script, self.script = self.script or Session.first or [
            init(), text("hi"), result(1)], []
        Session.first = None

        async def play():
            for ev in script:
                await asyncio.sleep(0.003)
                if callable(ev):
                    ev(self)
                else:
                    self.emit(ev)
        asyncio.get_running_loop().create_task(play())
        return 1


class Broker:
    def __init__(self):
        self.sessions = {}

    async def start_session(self, agent, session_id=None, resume=False,
                            provider=None, model=None):
        if session_id in self.sessions:
            old = self.sessions[session_id]
            if old.alive:
                return old
            # Stepped out by the idle stop: the same id comes back resumed.
            s = Session(agent, provider)
            s.id, s.resumed = session_id, resume
            self.sessions[s.id] = s
            return s
        s = Session(agent, provider)
        self.sessions[s.id] = s
        return s


async def call(server, method, path, payload=None, params=None):
    w = FakeWriter()
    try:
        await server.route(method, path, params or {}, payload or {}, w)
    except (broker_mod.BrokerError, threads.ThreadError) as exc:
        return exc.status, {"error": exc.message}
    return w.json()


async def settle(server, tid, rounds=400):
    for _ in range(rounds):
        await asyncio.sleep(0.01)
        if server.threads.settled(tid):
            return


def session_of(fake, persona):
    return next(s for s in fake.sessions.values() if s.agent == persona)


async def until(predicate, timeout=20.0):
    """Wait for an observable engine transition so scheduler delays cannot change the
    result.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except StopIteration:
            pass
        await asyncio.sleep(0.01)
    return False


def texts(tid):
    return [(m["author"], m["text"]) for m in threads.read(tid)[1]]



class Clock:
    """The engine's `time`, with `monotonic` moved on by `skip`."""
    def __init__(self):
        self.offset = 0.0

    def monotonic(self):
        return time.monotonic() + self.offset

    def time(self):
        return time.time() + self.offset

    def skip(self, seconds):
        self.offset += seconds


async def main():
    clock = Clock()
    engine_mod.time = clock
    engine_mod.PARKED_CHECK_S = 0.02
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = Broker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    status, row = await call(server, "POST", "/v1/threads", {"persona": "ada"})
    tid = row["id"]

    check("ENDED_WHY: real causes only, no time-based one",
          sorted(engine_mod.ENDED_WHY), ["closed", "exit", "stopping"])
    sources = [AGENTS / "lib" / "loam_thread_engine.py", AGENTS / "lib" / "loam_broker.py",
               AGENTS / "bin" / "session-broker"]
    check("no turn clock or hour backstop left in the broker's code",
          [p.name for p in sources
           if any(w in p.read_text() for w in ("ran past", "TURN_TIMEOUT_S =",
                                                 "IDLE_TIMEOUT_S ="))], [])

    # -- a harness turn that runs two hours before its result -----------------
    Session.first = [init(), text("Starting the long one.")]   # and no result
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "go long"})
    # The script played (init, then the words) and the engine is watching.
    await until(lambda: len(session_of(fake, "ada").log) >= 2 and tid in server.threads.active)
    ada = session_of(fake, "ada")
    for _ in range(8):           # two hours in quarter-hour steps
        clock.skip(900)
        await asyncio.sleep(0.03)
    check("two hours mid-turn: still watched", tid in server.threads.active, True)
    check("...no system line in the thread",
          [m["text"] for m in threads.read(tid)[1] if m["author"] == "system"], [])

    # -- then parked on background work for two hours more --------------------
    ada.tasks("a two-hour build")
    ada.emit(result(1))
    await until(lambda: ("ada", "Starting the long one.") in texts(tid)
                and server.threads.active.get(tid, {}).get("on_background"))
    for _ in range(8):
        clock.skip(900)
        await asyncio.sleep(0.03)
    check("four hours, parked with a task out: still watched",
          (tid in server.threads.active, server.threads.active.get(tid, {})
           .get("on_background")), (True, True))
    check("...the first reply posted, still no system line",
          [(m["author"], m["text"]) for m in threads.read(tid)[1]],
          [(OWNER, "go long"), ("ada", "Starting the long one.")])
    ada.tasks()
    ada.emit({"type": "system", "subtype": "task_notification", "task_id": "a"})
    ada.emit(init())
    ada.emit(text("The build finished after two hours."))
    ada.emit(result(2))
    await settle(server, tid)
    await until(lambda: ("ada", "The build finished after two hours.") in texts(tid)
                and tid not in server.threads.active)
    check("the reply the notification brings posts as the turn's own",
          [(m["author"], m["text"]) for m in threads.read(tid)[1]],
          [(OWNER, "go long"), ("ada", "Starting the long one."),
           ("ada", "The build finished after two hours.")])
    check("...and the turn ended on its result, watched after it",
          (tid in server.threads.active, (tid, "ada") in server.threads.tails),
          (False, True))

    # -- idle with nothing out: stepped out, and resumed ----------------------
    check("idle, nothing out, not mid-turn: what the idle stop may end",
          (ada.status(), ada.working_behind()), ("done", False))
    ada.emit({"type": "broker.exit", "exit_code": 143})
    ada.alive = False
    await asyncio.sleep(0.02)
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "still there?"})
    await settle(server, tid)
    await until(lambda: fake.sessions[ada.id] is not ada and texts(tid)[-1] == ("ada", "hi"))
    again = fake.sessions[ada.id]
    check("the next message resumes the same id and answers",
          (again is not ada, again.id, getattr(again, "resumed", None),
           threads.read(tid)[1][-1]["text"]),
          (True, ada.id, True, "hi"))
    server.threads.stop_all_tails()


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
