#!/usr/bin/env python3
"""Check immediate delivery to a member waiting on background work and preserve its outstanding task."""

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
# Exercise each supported provider with a separate member.
for who, provider in (("ada", "claude-code"), ("iris", "codex"),
                      ("cleo", "antigravity")):
    (TMP / "ai-labs" / who).mkdir(parents=True)
    (TMP / "ai-labs" / who / "agent.json").write_text(
        json.dumps({"name": who.capitalize(), "provider": provider}))
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



def seen_events(queue):
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


def task(name):
    return lambda s: s.tasks(name)


def done_tasks(s):
    s.tasks()


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

    async def post(tid, text):
        return await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": text})

    def member(tid, persona):
        row = engine.summary(tid)
        return next(m for m in row["members"] if m["persona"] == persona)

    def parked(tid, persona):
        return lambda: engine.turns.get((tid, persona), {}).get("on_background")

    # -- 1. Claude Code: parked on a task, a message is answered at once -----
    status, row = await call(server, "POST", "/v1/threads", {"persona": "ada"})
    tid = row["id"]
    stream1 = engine.listen(tid)
    Session.scripts["ada"] = [init(), text("Briefed the engineer; waiting on it."),
                               task("Brief engineer: 15 minutes"), result(1)]
    await post(tid, "brief it")
    ok = await until(parked(tid, "ada"))
    ada = session_of(fake, "ada")
    # Wait for both the recorded reply and the transition out of the active turn.
    await until(lambda: any("Briefed" in s for _, s in said(tid))
                and not member(tid, "ada")["working"])
    m = member(tid, "ada")
    check("Claude: words done, a task out -> parked, `working` false",
          (ok, m["busy"], m["working"], m["background"]), (True, True, False, 1))
    count = steps_mod.COUNTS["delivered_while_waiting"]
    ada.script = [init(), text("Yes, still on it -- here's the status."), result(1)]
    await post(tid, "are you there?")
    got = await until(lambda: len(ada.prompts) == 2, rounds=100)
    check("...a new message reaches the live session at once, task still out",
          (got, bool(ada.background_tasks), "are you there?" in ada.prompts[-1]),
          (True, True, True))
    check("...on the same session (no new process)",
          [s.id for s in fake.sessions.values() if s.agent == "ada"], [ada.id])
    await until(lambda: said(tid)[-1][0] == "ada"
                and "status" in said(tid)[-1][1])
    check("...answered as its own message while the task is still out",
          (said(tid)[-2:], bool(ada.background_tasks)),
          ([(OWNER, "are you there?"),
            ("ada", "Yes, still on it -- here's the status.")], True))
    check("...the writer counts `delivered_while_waiting`",
          steps_mod.COUNTS["delivered_while_waiting"] - count, 1)
    check("...and logs a line naming the task",
          [l for l in logged if "input arrived while its turn waited on "
           "background work (Brief engineer: 15 minutes)" in l] != [], True)
    check("...`delivered_while_waiting` is on GET /v1/threads/writer",
          (await call(server, "GET", "/v1/threads/writer"))[1]
          .get("delivered_while_waiting", None) is not None, True)
    await until(parked(tid, "ada"))
    # Complete the background task with a notification turn.
    for ev in (done_tasks, init(), text("The engineer's receipt is in: done."), result(1)):
        ev(ada) if callable(ev) else ada.emit(ev)
        await asyncio.sleep(0.005)
    await settle(server, tid)
    check("...the notification later lands as its own reply",
          said(tid)[-3:], [(OWNER, "are you there?"),
                           ("ada", "Yes, still on it -- here's the status."),
                           ("ada", "The engineer's receipt is in: done.")])
    sys_lines = [m["text"] for m in threads.read(tid)[1] if m["author"] == "system"]
    check("...and no system line about a turn ending", sys_lines, [])
    check("...the handed turn wrote no empty full-record entry",
          [l for l in logged if "(handed)" in l], [])
    notes = [m["note"] for e in seen_events(stream1) if e["type"] == "members"
             for m in e["members"] if m["persona"] == "ada"]
    check("...a Claude member never read 'will answer when its background "
          "task returns'", engine_mod.WILL_ANSWER_AFTER_BACKGROUND in notes, False)
    engine.stop_all_tails()

    # -- 2. generating: the message queues, then is delivered on park --------
    status, row = await call(server, "POST", "/v1/threads", {"persona": "ada"})
    t2 = row["id"]
    Session.scripts["ada"] = [init(), text("Working on the long thing.")]  # held
    clay2 = None
    await post(t2, "do the long thing")
    await until(lambda: (t2, "ada") in engine.turn_sessions)
    clay2 = engine.turn_sessions[(t2, "ada")]
    await asyncio.sleep(0.05)
    count = steps_mod.COUNTS["delivered_while_waiting"]
    clay2.script = [init(), text("Second answer, at once."), result(1)]
    await post(t2, "and this too")
    await asyncio.sleep(0.08)
    m = member(t2, "ada")
    check("generating: a second message queues behind the member's turn",
          (len(clay2.prompts), m["working"], m["queued"], m["note"]),
          (1, True, 1, "will answer when this turn ends"))
    # Its words are done and it parks on a task: the message goes in now.
    clay2.emit(text("Done; a check runs in the background."))
    clay2.tasks("Background check")
    clay2.emit(result(1))
    got = await until(lambda: len(clay2.prompts) == 2, rounds=100)
    check("...delivered the moment the turn parks, task still out",
          (got, bool(clay2.background_tasks)), (True, True))
    await until(lambda: said(t2)[-1] == ("ada", "Second answer, at once."))
    check("...both answers in the record, in order",
          said(t2), [(OWNER, "do the long thing"), (OWNER, "and this too"),
                     ("ada", "Working on the long thing.\n\nDone; a check runs in the background."),
                     ("ada", "Second answer, at once.")])
    check("...counted", steps_mod.COUNTS["delivered_while_waiting"] - count, 1)
    clay2.tasks()
    await settle(server, t2)
    engine.stop_all_tails()

    # -- 3. Codex: the same hand-over ----------------------------------------
    status, row = await call(server, "POST", "/v1/threads", {"persona": "iris"})
    t3 = row["id"]
    Session.scripts["iris"] = [init(), text("Sent a subagent."),
                               task("subagent: research"), result(1)]
    await post(t3, "research it")
    await until(parked(t3, "iris"))
    iris = session_of(fake, "iris")
    check("Codex member's session is on codex", iris.provider, "codex")
    iris.script = [init(), text("Iris here, meanwhile."), result(1)]
    await post(t3, "quick question")
    got = await until(lambda: len(iris.prompts) == 2, rounds=100)
    await until(lambda: said(t3)[-1] == ("iris", "Iris here, meanwhile."))
    check("Codex: delivered at once, answered while the subagent runs",
          (got, said(t3)[-1], bool(iris.background_tasks)),
          (True, ("iris", "Iris here, meanwhile."), True))
    for ev in (done_tasks, init(), text("The subagent reports."), result(1)):
        ev(iris) if callable(ev) else iris.emit(ev)
        await asyncio.sleep(0.005)
    await settle(server, t3)
    check("...the subagent's wake lands as its own reply",
          said(t3)[-1], ("iris", "The subagent reports."))
    engine.stop_all_tails()

    # -- 4. Antigravity: cannot take input; waits, and says so ---------------
    status, row = await call(server, "POST", "/v1/threads", {"persona": "cleo"})
    t4 = row["id"]
    Session.scripts["cleo"] = [init(), text("Started."), task("agy job"), result(1)]
    await post(t4, "start")
    await until(parked(t4, "cleo"))
    cleo = session_of(fake, "cleo")
    cleo.script = [init(), text("Now answering the second."), result(1)]
    count = steps_mod.COUNTS["delivered_while_waiting"]
    await post(t4, "second")
    await asyncio.sleep(0.08)
    m = member(t4, "cleo")
    check("Antigravity: the message waits while background work runs",
          (len(cleo.prompts), m["queued"]), (1, 1))
    check("...the member reads 'will answer when its background task returns'",
          m["note"], "will answer when its background task returns")
    check("...and a log line says why",
          [l for l in logged if "antigravity cannot take input while its "
           "background work runs" in l] != [], True)
    cleo.tasks()
    await settle(server, t4)
    check("...answered when the task returns",
          (len(cleo.prompts), said(t4)[-1],
           steps_mod.COUNTS["delivered_while_waiting"] - count),
          (2, ("cleo", "Now answering the second."), 0))
    engine.stop_all_tails()

    # -- 5. older pages in record order, on an out-of-order record -----------
    status, row = await call(server, "POST", "/v1/threads", {"persona": None})
    t5 = row["id"]
    for i in range(1, 13):
        threads.append(t5, OWNER, f"msg {i}")
    meta, msgs, path = threads.read(t5)
    # Two recovered replies, m13 and m14, appended where they were recovered:
    # between m10 and m11 in the record.
    late = [{"id": "m13", "author": "ada", "at": msgs[9]["at"], "kind": "message",
             "text": "late one", "did": []},
            {"id": "m14", "author": "ada", "at": msgs[9]["at"], "kind": "message",
             "text": "late two", "did": []}]
    threads.replace_message(t5, "m10", [msgs[9]] + late)
    order = [m["id"] for m in threads.read(t5)[1]]
    check("the record holds m13, m14 between m10 and m11",
          order[8:13], ["m9", "m10", "m13", "m14", "m11"])
    ids = lambda got: [m["id"] for m in got["messages"]]
    status, got = await call(server, "GET", f"/v1/threads/{t5}",
                             params={"before": "m11", "limit": "4"})
    check("before=m11: the four earlier in the record, in record order",
          (ids(got), got["has_more"]), (["m9", "m10", "m13", "m14"], True))
    status, got = await call(server, "GET", f"/v1/threads/{t5}",
                             params={"before": "m13", "limit": "3"})
    check("before=m13: nothing after it in the record (not m11, m12)",
          ids(got), ["m8", "m9", "m10"])
    status, got = await call(server, "GET", f"/v1/threads/{t5}",
                             params={"before": "m9", "limit": "50"})
    check("before=m9: m1..m8, never the late m13/m14",
          (ids(got), got["has_more"]), ([f"m{i}" for i in range(1, 9)], False))
    status, got = await call(server, "GET", f"/v1/threads/{t5}",
                             params={"before": "m99", "limit": "2"})
    check("an id the record does not hold: the number cut, record order",
          ids(got), ["m11", "m12"])

    # -- 6. ?seen=0 reads without marking seen -------------------------------
    seen = lambda: threads.load_state("threads.json").get(t5, {}).get("seen")
    threads.mark_seen(t5, "m1")
    await call(server, "GET", f"/v1/threads/{t5}", params={"seen": "0"})
    check("GET ?seen=0 leaves the seen mark where it was", seen(), "m1")
    await call(server, "GET", f"/v1/threads/{t5}", params={"seen": "false", "limit": "1"})
    check("...?seen=false too", seen(), "m1")
    await call(server, "GET", f"/v1/threads/{t5}", params={"limit": "1"})
    check("a plain GET of the newest page still marks seen",
          seen(), threads.read(t5)[1][-1]["id"])


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
