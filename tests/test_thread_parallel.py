#!/usr/bin/env python3
"""Check concurrent replies across members and ordered input to the same member."""

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
    scripts = {}   # persona -> the script its next new message plays

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
        self.prompts = []

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

    def task_started_at(self, task_id):
        return "2026-11-10T21:49:25Z"

    async def send_message(self, prompt, attachments=None):
        self.prompts.append(prompt)
        script, self.script = self.script or Session.scripts.pop(self.agent, None) or [
            init(), text(f"{self.agent} here"), result(1)], []

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




def said(tid):
    return [(m["author"], m["text"]) for m in threads.read(tid)[1]
            if m["author"] != "system"]


async def until(cond, rounds=300):
    for _ in range(rounds):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


async def main():
    engine_mod.PARKED_CHECK_S = 0.02
    logged = []
    real_log = steps_mod.log
    steps_mod.log = lambda line: (logged.append(line), real_log(line))
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = Broker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    engine = server.threads

    # A second member can answer while the first is busy.
    status, row = await call(server, "POST", "/v1/threads", {"persona": "iris"})
    tid = row["id"]
    stream = engine.listen(tid)
    Session.scripts["ada"] = [init(), text("Starting the long build.")]  # no result yet
    await call(server, "POST", f"/v1/threads/{tid}/messages",
               {"text": "@ada start the long build"})
    await until(lambda: (tid, "ada") in engine.turn_sessions)
    ada = session_of(fake, "ada")
    await asyncio.sleep(0.05)
    parallel_before = steps_mod.COUNTS["parallel"]
    Session.scripts["iris"] = [init(), text("Lunch is soup.")]          # held too
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "what's for lunch?"})
    started = await until(lambda: (tid, "iris") in engine.turn_sessions)
    await asyncio.sleep(0.05)
    check("a message to Iris starts Iris while Ada's turn runs",
          (started, sorted(p for (t, p) in engine.turns if t == tid)), (True, ["ada", "iris"]))
    iris = session_of(fake, "iris")
    check("...Iris read the thread as it stood, Ada's tag in it",
          "@ada start the long build" in iris.prompts[0], True)
    check("...the writer counts it `parallel`",
          steps_mod.COUNTS["parallel"] - parallel_before, 1)
    check("...and logs a `threads:` line naming both",
          [l for l in logged if "iris: turn starts while ada is mid-turn" in l] != [], True)
    status, got = await call(server, "GET", f"/v1/threads/{tid}")
    check("the read: `turns[]` both, earliest first; `turn` the earliest",
          ([t["persona"] for t in got["turns"]], got["turn"]["persona"],
           all(t.get("started_at") for t in got["turns"])),
          (["ada", "iris"], "ada", True))
    check("...both members `busy` and `working`",
          {m["persona"]: (m["busy"], m["working"]) for m in got["thread"]["members"]},
          {"iris": (True, True), "ada": (True, True)})
    seen = []
    while not stream.empty():
        seen.append(stream.get_nowait())
    both = [e for e in seen if e["type"] == "members"
            and all(m["working"] for m in e["members"])]
    check("the stream's `members` said both working", len(both) >= 1, True)

    iris.emit(result(1))
    await until(lambda: (tid, "iris") not in engine.turns)
    check("Iris's reply lands while Ada is still mid-turn",
          (said(tid)[-1], (tid, "ada") in engine.turns),
          (("iris", "Lunch is soup."), True))

    # Input for the busy member waits behind its own turn.
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "@ada and also this"})
    await asyncio.sleep(0.08)
    check("a second message to Ada mid-turn does not start a second Ada turn",
          (len(ada.prompts), engine.queues[(tid, "ada")].qsize()), (1, 1))
    ada.script = [init(), text("Second answer."), result(1)]
    ada.emit(text("The build is done."))
    ada.emit(result(1))
    await settle(server, tid)
    check("Ada's first reply lands after Iris's, then the queued turn's",
          said(tid), [(OWNER, "@ada start the long build"),
                      (OWNER, "what's for lunch?"),
                      ("iris", "Lunch is soup."),
                      (OWNER, "@ada and also this"),
                      ("ada", "Starting the long build.\n\nThe build is done."),
                      ("ada", "Second answer.")])
    check("...and Ada's next turn was told what Iris said while Ada ran",
          (len(ada.prompts), "Lunch is soup." in ada.prompts[1],
           "@ada and also this" in ada.prompts[1]), (2, True, True))
    check("...`through` is the last id that turn read, not its own reply's",
          threads.session_for(tid, "ada")["through"],
          [m for m in threads.read(tid)[1]
           if m["text"].endswith("The build is done.")][0]["id"])
    engine.stop_all_tails()

    # -- 3. @all in a three-member thread: three turns at once ----------------
    status, row = await call(server, "POST", "/v1/threads", {"persona": "ada"})
    t3 = row["id"]
    await call(server, "PATCH", f"/v1/threads/{t3}", {"add": ["iris", "cleo"]})
    for p in ("ada", "iris", "cleo"):
        Session.scripts[p] = [init(), text(f"{p} answering")]           # held
    for s in fake.sessions.values():
        s.script = []
    # Fresh sessions for this thread: the scripts go to new sessions.
    before = set(fake.sessions)
    await call(server, "POST", f"/v1/threads/{t3}/messages", {"text": "@all status?"})
    await until(lambda: len(engine.live_turns(t3)) == 3)
    await asyncio.sleep(0.05)
    check("`@all` in a three-member thread: three turns live at once",
          sorted(t["persona"] for t in engine.live_turns(t3)), ["ada", "cleo", "iris"])
    news = {s.agent: s for sid, s in fake.sessions.items() if sid not in before}
    if len(news) < 3:   # a member's session from thread one is reused
        news.update({p: engine.turn_sessions[(t3, p)] for p in ("ada", "iris", "cleo")})
    for p in ("cleo", "ada", "iris"):
        news[p].emit(result(1))   # three results in the same tick
    await settle(server, t3)
    replies = [m for m in threads.read(t3)[1] if m["author"] in ("ada", "iris", "cleo")]
    check("replies landing at the same moment all recorded, in result order",
          [m["author"] for m in replies], ["cleo", "ada", "iris"])
    check("...with distinct ids", len({m["id"] for m in replies}), 3)
    full = steps_mod.read(t3)
    check("...each in the full record under its own id",
          all(m["id"] in full for m in replies), True)
    engine.stop_all_tails()

    # -- 4. words done, background work out: busy, not working ----------------
    Session.scripts.clear()
    clay3 = engine.live_session(t3, "ada") or news["ada"]
    clay3.alive = True
    clay3.script = [init(), text("Briefed; waiting on it."),
                    lambda s: s.tasks("Brief loam-ui: chip logo")]   # result held
    await call(server, "POST", f"/v1/threads/{t3}/messages", {"text": "@ada brief it"})
    await until(lambda: engine.turns.get((t3, "ada"), {}).get("on_background"))
    status, got = await call(server, "GET", f"/v1/threads/{t3}")
    member = [m for m in got["thread"]["members"] if m["persona"] == "ada"][0]
    launched_seq = clay3.log[-1]["seq"]
    check("...`background_tasks` names it, with when it started; its reply "
          "is not posted yet, so `message` is null",
          member["background_tasks"],
          [{"id": "Brief loam-ui: chip logo", "description": "Brief loam-ui: chip logo",
            "started_at": "2026-11-10T21:49:25Z", "message": None,
            "launched_at_seq": launched_seq}])
    check("...a member with nothing out has an empty list",
          [m["background_tasks"] for m in got["thread"]["members"] if m["persona"] == "iris"],
          [[]])
    clay3.emit(result(1))     # the reply that launched it is written now
    await until(lambda: any(m["text"] == "Briefed; waiting on it."
                            for m in threads.read(t3)[1]))
    await asyncio.sleep(0.05)
    reply_id = [m["id"] for m in threads.read(t3)[1]
                if m["text"] == "Briefed; waiting on it."][-1]
    status, got = await call(server, "GET", f"/v1/threads/{t3}")
    member = [m for m in got["thread"]["members"] if m["persona"] == "ada"][0]
    check("words done, a task out: `busy` true, `working` false",
          (member["busy"], member["working"], member["background"]), (True, False, 1))
    check("...once that reply is written, `message` is its id",
          [(t["message"], t["launched_at_seq"]) for t in member["background_tasks"]],
          [(reply_id, launched_seq)])
    clay3.tasks()
    clay3.emit(result(1))
    await settle(server, t3)
    engine.stop_all_tails()

    # The real Session stamps a task the first time the CLI lists it.
    real = broker_mod.Session.__new__(broker_mod.Session)
    real.background_tasks, real._awaiting, real._notified = [], {}, {}
    real._resuming = False
    real._arm_settle = lambda: None
    real._set_background_tasks([{"task_id": "a1", "task_type": "local_agent",
                                 "description": "Brief broker"}])
    first = real.task_started_at("a1")
    real._set_background_tasks([{"task_id": "a1", "description": "Brief broker"},
                                {"task_id": "a2", "description": "Deploy"}])
    check("the real Session: a task's started_at is set once and kept",
          (bool(first) and first.endswith("Z"), real.task_started_at("a1") == first,
           sorted(real._task_seen)), (True, True, ["a1", "a2"]))
    real._set_background_tasks([])
    check("...and dropped when the task leaves the list", real._task_seen, {})
    check("the engine's member list reads it",
          engine_mod.background_list(real), [])


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
