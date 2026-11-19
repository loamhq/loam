#!/usr/bin/env python3
"""Input to a thread member is durable and ordered (2026-11-19, batch B step 2).

Each (thread, member) has an ordered list of input on disk
(lib/loam_thread_inputs.py): the owner's messages and returned results. A
broker restart loses none of it, and a job's result can reach a turn that
is generating about the same work. Proved against the thread engine with
the fake harness of tests/test_thread_steer.py:

  1. restart: a turn cut before a word, one owner message queued behind it
     and one job result queued: nothing is posted at the stop, the list on
     disk holds all three in order, and a new engine's ``resume_inputs``
     gives each once and in order -- the first replayed turn reads both
     messages (so the second runs no turn of its own), the result is its
     own quiet turn after -- each prompt carries when it was queued, and
     the list is empty after
  2. once-only after a crash: a result left marked as given is not given
     twice; a message left marked as given whose ``through`` the member
     has passed runs no turn; input for a thread that is gone is dropped
  3. a result steered into the Claude Code turn that briefed it: one
     prompt, the steer carries the receipt note, no second turn, nothing
     posted for it, the list empty once the turn ends
  4. a result for a turn about something else (under way since after the
     brief) waits behind it and has its own turn
  5. a result for a turn parked on background work takes the session over
     at once (the hand-over), on Claude Code
  6. Antigravity, which cannot steer: the result waits for the turn to end
  7. the handoff note says when the job finished

Run: python3 tests/test_thread_inputs.py
"""

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
# One member per engine: Ada on Claude Code, Iris on Codex, Cleo on
# Antigravity.
for who, provider in (("ada", "claude-code"), ("iris", "codex"),
                      ("cleo", "antigravity")):
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


async def call(server, method, path, payload=None, params=None):
    w = FakeWriter()
    try:
        await server.route(method, path, params or {}, payload or {}, w)
    except (broker_mod.BrokerError, threads.ThreadError) as exc:
        return exc.status, {"error": exc.message}
    return w.json()


async def until(cond, rounds=400):
    for _ in range(rounds):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


def said(tid):
    return [(m["author"], m["text"]) for m in threads.read(tid)[1]
            if m["author"] != "system"]


def system_lines(tid):
    return [m["text"] for m in threads.read(tid)[1] if m["author"] == "system"
            and m.get("kind") == "system" and "brought in" not in m["text"]
            and "default" not in m["text"]]


def drain(queue):
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out



inputs = engine_mod.inputs
fake = Broker()


def quiet_note(jid):
    return (f"[Loam: the engineer job {jid} you briefed from this thread has "
            f"finished. Receipt: /tmp/{jid}/receipt.md.]")


async def main():
    engine_mod.PARKED_CHECK_S = 0.02
    engine_mod.STEER_FOLLOW_S = {"claude-code": 0.3}
    logged = []
    real_log = steps_mod.log
    steps_mod.log = lambda line: (logged.append(line), real_log(line))
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake.stopping = False
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)

    async def new_thread(persona):
        status, row = await call(server, "POST", "/v1/threads", {"persona": persona})
        return row["id"]

    async def post(tid, text_):
        status, body = await call(server, "POST", f"/v1/threads/{tid}/messages",
                                  {"text": text_})
        return body.get("message", body)

    def session(persona):
        return [s for s in fake.sessions.values() if s.agent == persona][-1]

    def deliver(tid, persona, jid, since):
        stamp = {"since_ts": since} if isinstance(since, float) else {"since": since}
        return server.threads.deliver_job(
            {"job": jid, "thread": tid, "persona": persona, **stamp},
            quiet_note(jid))

    # -- 1. restart: one queued owner message, one queued result -------------
    tid = await new_thread("ada")
    gate = Gate()
    fake.next_scripts["ada"] = [[init(), tool(), gate,
                                 lambda s: setattr(fake, "stopping", True),
                                 {"type": "broker.exit", "exit_code": 143}]]
    m1 = await post(tid, "start the long thing")
    await until(lambda: gate.reached)
    ada = session("ada")
    ada.refuse = True             # no steer: the message waits in the queue
    m2 = await post(tid, "and then tell me when it's done")
    check("1. deliver a job's result while the turn is under way",
          deliver(tid, "ada", "job-r1", "2026-11-19T00:00:00Z"), "delivered")
    lines0 = len(system_lines(tid))
    gate.open()
    await until(lambda: server.threads.settled(tid))
    await asyncio.sleep(0.05)
    check("...the stop posts nothing: no 'ended', no 'didn't start'",
          system_lines(tid)[lines0:], [])
    kept = inputs.items(tid, "ada")
    check("...the list on disk holds all three, in order, none marked given",
          [(i["reason"], "sent" in i) for i in kept],
          [(m1["id"], False), (m2["id"], False), ("job:job-r1", False)])
    # The broker starts again.
    fake.stopping = False
    server.threads = engine_mod.ThreadEngine(fake)
    ada.refuse = False
    ada.prompts.clear()
    ada.scripts = [[init(), text("Started it again; I'll tell you when done."), result(1)],
                   [init(), result(1)]]
    mark = len(logged)
    counts = server.threads.resume_inputs()
    await until(lambda: server.threads.settled(tid) and len(ada.prompts) >= 2)
    await asyncio.sleep(0.1)
    check("...resumed: three scheduled again", counts["scheduled"], 3)
    check("...two turns: the messages together, then the result",
          (len(ada.prompts), m1["id"] in ada.prompts[0], m2["id"] in ada.prompts[0],
           "job-r1" in ada.prompts[1], "job-r1" in ada.prompts[0]),
          (2, True, True, True, False))
    check("...each prompt says when it was queued",
          all("was queued for you at" in p for p in ada.prompts), True)
    check("...the second message ran no turn of its own (already heard)",
          any(m2["id"] in l and "already" in l for l in logged[mark:]), True)
    check("...one reply posted, nothing for the result, no system line",
          (said(tid)[-1], system_lines(tid)[lines0:]),
          (("ada", "Started it again; I'll tell you when done."), []))
    check("...and the list is empty", inputs.items(tid, "ada"), [])

    # -- 2. once-only after a crash ------------------------------------------
    tid2 = await new_thread("ada")
    await post(tid2, "hello")
    await until(lambda: server.threads.settled(tid2))
    heard = threads.session_for(tid2, "ada")["through"]
    inputs.add(tid2, "ada", {"reason": "job:job-crash", "chain": 0,
                             "note": quiet_note("job-crash"), "sent": "2026-11-19T01:00:00Z"})
    inputs.add(tid2, "ada", {"reason": heard, "chain": 0, "sent": "2026-11-19T01:00:00Z"})
    inputs.add("t-gone0000", "ada", {"reason": "m3", "chain": 0})
    ada = session("ada")
    before = len(ada.prompts)
    server.threads = engine_mod.ThreadEngine(fake)
    counts = server.threads.resume_inputs()
    await until(lambda: server.threads.settled(tid2))
    await asyncio.sleep(0.05)
    check("2. a crash: the given result is not given twice; the heard message "
          "runs no turn; the gone thread's input is dropped",
          (counts["dropped"], len(ada.prompts) - before, inputs.items(tid2, "ada"),
           inputs.pending()), (2, 0, [], []))

    # -- 3. a result steered into the turn that briefed it --------------------
    tid3 = await new_thread("ada")
    gate = Gate()
    fake.next_scripts["ada"] = [[init(), tool(), gate,
                                 text("Briefed it and it landed; done."), result(2)]]
    await post(tid3, "brief the engineer and keep going")
    await until(lambda: gate.reached)
    ada = session("ada")
    since = time.time()         # the brief is accepted during this turn
    steered0 = steps_mod.COUNTS["steered"]
    deliver(tid3, "ada", "job-s1", since)
    ok = await until(lambda: ada.steers)
    check("3. Claude Code: the result is steered into the turn that briefed it",
          (ok, len(ada.prompts), "job-s1" in (ada.steers or [""])[0],
           "not from" in (ada.steers or [""])[0]), (True, 1, True, True))
    check("...counted, and it is the turn's own on disk until the turn ends",
          (steps_mod.COUNTS["steered"] - steered0,
           [(i["reason"][:3], "sent" in i) for i in inputs.items(tid3, "ada")]),
          (1, [("m1", True), ("job", True)]))
    gate.open()
    await until(lambda: server.threads.settled(tid3))
    await asyncio.sleep(0.4)
    check("...no second turn, one reply, no system line, the list empty",
          (len(ada.prompts), said(tid3)[-1], system_lines(tid3),
           inputs.items(tid3, "ada")),
          (1, ("ada", "Briefed it and it landed; done."), [], []))

    # -- 4. a result for a turn about something else waits -------------------
    tid4 = await new_thread("ada")
    since = time.time()         # briefed before this turn
    gate = Gate()
    fake.next_scripts["ada"] = [[init(), tool(), gate, text("About the other thing."),
                                 result(2)], [init(), result(1)]]
    await post(tid4, "something else entirely")
    await until(lambda: gate.reached)
    ada = session("ada")
    deliver(tid4, "ada", "job-w1", since)
    await asyncio.sleep(0.2)
    check("4. a turn about something else: the result is not steered in",
          (ada.steers, len(ada.prompts)), ([], 1))
    gate.open()
    await until(lambda: server.threads.settled(tid4) and len(ada.prompts) >= 2)
    await asyncio.sleep(0.1)
    check("...it is its own quiet turn after, nothing posted for it",
          (len(ada.prompts), "job-w1" in ada.prompts[1], said(tid4)[-1],
           system_lines(tid4)),
          (2, True, ("ada", "About the other thing."), []))

    # -- 5. a result for a parked turn takes the session over ----------------
    tid5 = await new_thread("ada")
    since = time.time()
    fake.next_scripts["ada"] = [[init(), lambda s: s.tasks("Brief engineer"),
                                 text("Briefed; waiting on it."), result(1)],
                                [init(), result(1)]]
    await post(tid5, "brief it in the background")
    await until(lambda: (server.threads.turns.get((tid5, "ada")) or {}).get("on_background"))
    ada = session("ada")
    handed0 = steps_mod.COUNTS["delivered_while_waiting"]
    deliver(tid5, "ada", "job-p1", since)
    await until(lambda: len(ada.prompts) >= 2)
    check("5. parked on background work: the result is taken at once",
          (len(ada.prompts), "job-p1" in ada.prompts[-1],
           steps_mod.COUNTS["delivered_while_waiting"] - handed0), (2, True, 1))
    ada.tasks()
    await until(lambda: server.threads.settled(tid5))

    # -- 6. Antigravity waits for the turn to end ----------------------------
    tid6 = await new_thread("cleo")
    gate = Gate()
    fake.next_scripts["cleo"] = [[init(), tool(), gate, text("Cleo done."), result(2)],
                                 [init(), result(1)]]
    await post(tid6, "go, Cleo")
    await until(lambda: gate.reached)
    cleo = session("cleo")
    deliver(tid6, "cleo", "job-a1", "2030-01-01T00:00:00Z")
    await asyncio.sleep(0.2)
    check("6. Antigravity: not steered, waiting", (cleo.steers, len(cleo.prompts)),
          ([], 1))
    gate.open()
    await until(lambda: server.threads.settled(tid6) and len(cleo.prompts) >= 2)
    check("...its own turn once the turn ends", "job-a1" in cleo.prompts[-1], True)

    # -- 7. the handoff note says when the job finished ----------------------
    import loam_waiters  # noqa: PLC0415
    note = loam_waiters.handoff_text({"job": "j", "job_dir": "/tmp/j", "result": {
        "outcome": "completed", "stage": "done", "finished": "2026-11-19T10:00:00Z"}})
    check("7. the note carries the finish time", "finished at Thu 2026-11-19 02:00" in note,
          True)


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if results and all(results) else 1)
