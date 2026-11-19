#!/usr/bin/env python3
"""Check that the event stream and stored thread order replies by completion when input arrives mid-turn."""

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
for who, provider in (("ada", "claude-code"),):
    (TMP / "ai-labs" / who).mkdir(parents=True)
    (TMP / "ai-labs" / who / "agent.json").write_text(
        json.dumps({"name": who.capitalize(), "provider": provider}))
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents" / "comms.md").write_text("# From agents\n")

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_threads as threads  # noqa: E402

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


def tool(name="Bash"):
    return {"type": "assistant", "parent_tool_use_id": None,
            "message": {"content": [{"type": "tool_use", "name": name,
                                     "input": {"command": "sleep 60"}}]}}


class Gate:
    """A point in a script the test opens."""

    def __init__(self):
        self.event = asyncio.Event()
        self.reached = False

    async def wait(self):
        self.reached = True
        await self.event.wait()

    def open(self):
        self.event.set()


class Session:
    n = 0

    def __init__(self, agent, provider):
        Session.n += 1
        self.agent, self.provider, self.id = agent, provider, f"sess-{Session.n}"
        self.model = None
        self.subs = set()
        self.scripts = []        # one script per send_message, in order
        self.background_tasks = []
        self.turn_active = False
        self.alive = True
        self.thread_id = "019a-codex-thread" if provider == "codex" else None
        self.log = []
        self.seq = 0
        self.prompts = []
        self.steers = []
        self.STEERS = provider != "antigravity"
        self.refuse = False
        self.after_steer = []    # events played when a steer is taken

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
        return bool(self.background_tasks)

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
        return ev

    def tasks(self, *names):
        self.background_tasks = [{"task_id": n, "description": n} for n in names]
        self.emit({"type": "broker.background_tasks", "count": len(names),
                   "tasks": self.background_tasks})

    def task_started_at(self, task_id):
        return "2026-11-14T21:49:25Z"

    async def play(self, script):
        for ev in script:
            await asyncio.sleep(0.003)
            if isinstance(ev, Gate):
                await ev.wait()
            elif callable(ev):
                ev(self)
            else:
                self.emit(ev)

    async def send_message(self, prompt, attachments=None):
        self.prompts.append(prompt)
        script = self.scripts.pop(0) if self.scripts else [
            init(), text(f"{self.agent} here"), result(1)]
        asyncio.get_running_loop().create_task(self.play(script))
        return 1

    async def steer(self, prompt, attachments=None):
        await asyncio.sleep(0.002)
        if not self.STEERS or self.refuse or not self.turn_active:
            return None
        self.steers.append(prompt)
        ev = self.emit({"type": "broker.user_message", "text": prompt,
                        "steered": True})
        if self.after_steer:
            script, self.after_steer = self.after_steer, []
            asyncio.get_running_loop().create_task(self.play(script))
        return ev["seq"]


class Broker:
    def __init__(self):
        self.sessions = {}
        self.next_scripts = {}   # persona -> scripts for its next new session

    async def start_session(self, agent, session_id=None, resume=False,
                            provider=None, model=None, effort=None):
        if session_id in self.sessions and self.sessions[session_id].alive:
            return self.sessions[session_id]
        s = Session(agent, provider)
        if session_id:
            s.id = session_id
        s.scripts = self.next_scripts.pop(agent, [])
        self.sessions[s.id] = s
        return s


def delta(t):
    return {"type": "stream_event", "parent_tool_use_id": None,
            "event": {"type": "content_block_delta",
                      "delta": {"type": "text_delta", "text": t}}}


def done_ids(events):
    return [e["message"]["id"] for e in events if e.get("type") == "message.done"]


def at_of(msgs, mid):
    return next(m["at"] for m in msgs if m["id"] == mid)


async def until(cond, rounds=600):
    for _ in range(rounds):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


async def call(server, method, path, payload=None, params=None):
    w = FakeWriter()
    await server.route(method, path, params or {}, payload or {}, w)
    return w.json()


class Stream:
    """``GET /v1/threads/<id>/events`` as a browser holds it open."""

    def __init__(self, server, tid, after=None):
        self.w = FakeWriter()
        self.task = asyncio.get_running_loop().create_task(
            server.thread_stream(tid, after, self.w))

    def events(self):
        return self.w.items()

    async def close(self):
        self.task.cancel()
        try:
            await self.task
        except (asyncio.CancelledError, Exception):
            pass


async def main():
    engine_mod.PARKED_CHECK_S = 0.02
    engine_mod.STEER_FOLLOW_S = {"claude-code": 0.3}
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = Broker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    engine = server.threads
    dump = {}

    async def new_thread(persona):
        status, row = await call(server, "POST", "/v1/threads", {"persona": persona})
        return row["id"]

    async def post(tid, text_):
        status, body = await call(server, "POST", f"/v1/threads/{tid}/messages",
                                  {"text": text_})
        return body.get("message", body)

    def session(persona):
        return [s for s in fake.sessions.values() if s.agent == persona][-1]

    async def read(tid):
        status, body = await call(server, "GET", f"/v1/threads/{tid}")
        return [m["id"] for m in body["messages"]]

    # -- 1. two messages during a long tool call, then the reply -------------
    tid = await new_thread("ada")
    live = Stream(server, tid)
    await asyncio.sleep(0.02)
    gate = Gate()
    fake.next_scripts["ada"] = [[init(), delta("Starting on it."),
                                  text("Starting on it."), tool(), gate,
                                  delta("Done. "), delta("Done. Blue, and quietly."),
                                  text("Done. Blue, and quietly."), result(2)]]
    m1 = await post(tid, "run the long thing")
    await until(lambda: gate.reached)
    ada = session("ada")
    m2 = await post(tid, "also: use the blue one")
    await until(lambda: len(ada.steers) == 1)
    m3 = await post(tid, "and keep it quiet")
    await until(lambda: len(ada.steers) == 2)
    gate.open()
    await until(lambda: engine.settled(tid))
    await asyncio.sleep(2.2)     # the stream's own two-second look
    events = live.events()
    await live.close()
    msgs = threads.read(tid)[1]
    reply = msgs[-1]
    kinds = [e["type"] for e in events]
    check("long tool: message.done in posting order, the reply last",
          done_ids(events)[-4:], [m1["id"], m2["id"], m3["id"], reply["id"]])
    check("...ids rise along the stream",
          done_ids(events) == sorted(done_ids(events), key=threads._num), True)
    check("...the reply is Ada's, after both, and names them as steered",
          (reply["author"], reply.get("steered"),
           reply["at"] >= at_of(msgs, m3["id"]) >= at_of(msgs, m2["id"])),
          ("ada", [m2["id"], m3["id"]], True))
    started = kinds.index("turn.started")
    m2_at = next(i for i, e in enumerate(events) if e.get("type") == "message.done"
                 and e["message"]["id"] == m2["id"])
    check("...turn.started and a draft delta come before the mid-turn messages",
          (started < m2_at, kinds.index("message.delta") < m2_at), (True, True))
    check("...one reply, no duplicate message.done for any id",
          (sum(1 for m in msgs if m["author"] == "ada"),
           len(done_ids(events)) == len(set(done_ids(events)))), (1, True))
    check("...a reload reads the same order the stream gave",
          await read(tid), done_ids(events))
    again = Stream(server, tid, after=m1["id"])
    await asyncio.sleep(0.1)
    replayed = again.events()
    await again.close()
    check("...a reconnect from m1 replays the rest in the same order",
          done_ids(replayed), done_ids(events)[done_ids(events).index(m1["id"]) + 1:])
    dump["long_tool"] = events

    # -- 2. reply, message, reply: the end-of-turn race ----------------------
    tid = await new_thread("ada")
    live = Stream(server, tid)
    await asyncio.sleep(0.02)
    gate = Gate()
    fake.next_scripts["ada"] = [[init(), delta("Here is the first answer."),
                                  text("Here is the first answer."), gate, result(1)]]
    m1 = await post(tid, "question one")
    await until(lambda: gate.reached)
    ada = session("ada")
    ada.after_steer = [gate, init(), delta("And the follow-up."),
                       text("And the follow-up."), result(1)]
    m2 = await post(tid, "follow-up, sent as it finished")
    await until(lambda: ada.steers)
    gate.open()
    await until(lambda: engine.settled(tid))
    await asyncio.sleep(2.2)
    events = live.events()
    await live.close()
    msgs = threads.read(tid)[1]
    order = [(m["author"], m["text"]) for m in msgs
             if threads._num(m["id"]) >= threads._num(m1["id"])]
    check("race: the first answer posts at its result, after the follow-up",
          order, [(threads.OWNER, "question one"),
                  (threads.OWNER, "follow-up, sent as it finished"),
                  ("ada", "Here is the first answer."),
                  ("ada", "And the follow-up.")])
    first = next(i for i, e in enumerate(events) if e.get("type") == "message.delta")
    m2_at = next(i for i, e in enumerate(events) if e.get("type") == "message.done"
                 and e["message"]["id"] == m2["id"])
    check("...though its words streamed before the follow-up was posted",
          first < m2_at, True)
    check("...the stream gives them in that order, ids rising",
          done_ids(events)[-4:], [m["id"] for m in msgs][-4:])
    check("...a reload agrees", await read(tid), done_ids(events))
    dump["race"] = events

    if os.environ.get("LOAM_ORDER_DUMP"):
        Path(os.environ["LOAM_ORDER_DUMP"]).write_text(json.dumps(dump, indent=1))


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
