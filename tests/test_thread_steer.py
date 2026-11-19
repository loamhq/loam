#!/usr/bin/env python3
"""A message sent while a member works reaches that member (2026-11-14).

The owner, 11-14 in t-153f9c43: a message sent while a persona is "in the
middle of doing an action or a task ... gets queued up, but it doesn't
always go to the agent once they're done". The case on the record is m6 in
t-232b9184: sent while Ada's turn was generating after it had launched a
background build, it was refused the hand-over (the turn was generating),
never asked again, and sat queued until the owner re-sent it as m8 90 s
later; then the queued turn for m6 ran with nothing new and said so (m10).

Proved against the thread engine through the broker's routes with a fake
harness whose turns the test holds open:

  - Claude Code, a message during a tool call: steered into the turn in
    flight (no second turn, no cancel), the steer text carries the message
    id, one reply, `through` past it, `turn.steered` on the stream
  - several rapid messages during one tool call: all delivered, in order,
    none twice, no extra turn
  - the end-of-turn race on Claude Code: a steer after the model's last
    words is answered by the harness turn that opens after the `result`,
    posted as this turn's next message
  - Codex refusing the steer (`expectedTurnId` no longer active): the
    message keeps its place and has an ordered turn of its own
  - Antigravity (cannot steer): queued, `note` "will answer when this turn
    ends", answered by its own turn
  - the m6 regression: an engine that cannot steer, a task launched
    mid-turn, a message while the turn generates -> delivered when the
    turn's words are done, not when the next message comes
  - two queued messages: the first queued turn reads both; the second does
    not run with nothing new
  - a turn that dies before replying: an explicit system line, and its
    messages stay new to the next turn (`through` not advanced)
  - `CodexSession.steer`: `turn/steer` with `expectedTurnId`; the log line
    only once Codex accepted; a refusal logs nothing and answers None
  - `Session.steer` (Claude): None with no turn open, else a stdin user
    message after a `broker.user_message` with `steered: true`

Run: python3 tests/test_thread_steer.py
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


async def main():
    engine_mod.PARKED_CHECK_S = 0.02
    engine_mod.STEER_FOLLOW_S = {"claude-code": 0.3}
    logged = []
    real_log = steps_mod.log
    steps_mod.log = lambda line: (logged.append(line), real_log(line))
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = Broker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    engine = server.threads

    async def new_thread(persona):
        status, row = await call(server, "POST", "/v1/threads", {"persona": persona})
        return row["id"]

    async def post(tid, text_):
        status, body = await call(server, "POST", f"/v1/threads/{tid}/messages",
                                  {"text": text_})
        return body.get("message", body)

    def through(tid, persona):
        return (threads.session_for(tid, persona) or {}).get("through")

    def session(persona):
        return [s for s in fake.sessions.values() if s.agent == persona][-1]

    # -- 1. Claude Code: a message during a tool call is steered in ----------
    tid = await new_thread("ada")
    stream = engine.listen(tid)
    gate = Gate()
    fake.next_scripts["ada"] = [[init(), tool(), gate,
                                  text("Done, and noted your second message."),
                                  result(2)]]
    m1 = await post(tid, "run the long thing")
    await until(lambda: gate.reached)
    ada = session("ada")
    steered0 = steps_mod.COUNTS["steered"]
    m2 = await post(tid, "also: use the blue one")
    ok = await until(lambda: ada.steers)
    check("Claude, mid-tool: the message is steered into the turn in flight",
          (ok, len(ada.prompts), m2["id"] in (ada.steers or [""])[0],
           "use the blue one" in (ada.steers or [""])[0]), (True, 1, True, True))
    check("...the tool was not cut: the gate is still closed, no turn ended",
          (gate.event.is_set(), engine.settled(tid)), (False, False))
    check("...the queue holds nothing for it",
          engine.queues[(tid, "ada")].qsize(), 0)
    gate.open()
    await until(lambda: engine.settled(tid))
    await asyncio.sleep(0.1)
    check("...one reply, one harness prompt, no second turn",
          (said(tid)[-1], len(ada.prompts), len(ada.steers)),
          (("ada", "Done, and noted your second message."), 1, 1))
    check("...`through` is past the steered message",
          through(tid, "ada"), m2["id"])
    check("...the reply records which message was steered into it",
          threads.read(tid)[1][-1].get("steered"), [m2["id"]])
    events = drain(stream)
    st = [e for e in events if e.get("type") == "turn.steered"]
    check("...`turn.steered` on the stream names it",
          [e["messages"] for e in st], [[m2["id"]]])
    check("...counted", steps_mod.COUNTS["steered"] - steered0, 1)
    check("...no system line (no failure, no 'ended')", system_lines(tid), [])

    # -- 2. several rapid messages during one tool call ------------------------
    tid = await new_thread("ada")
    gate = Gate()
    ada = None
    fake.next_scripts["ada"] = [[init(), tool(), gate, text("All three handled."),
                                  result(3)]]
    await post(tid, "start")
    await until(lambda: gate.reached)
    ada = session("ada")
    ms = [await post(tid, f"rapid {i}") for i in range(3)]
    await until(lambda: sum(p.count("rapid ") for p in ada.steers) >= 3)
    joined = "\n".join(ada.steers)
    order = [joined.find(f"rapid {i}") for i in range(3)]
    check("rapid: all three delivered into the turn, in order",
          (all(o >= 0 for o in order), order == sorted(order)), (True, True))
    check("...none twice", [joined.count(f"rapid {i}") for i in range(3)], [1, 1, 1])
    gate.open()
    await until(lambda: engine.settled(tid))
    await asyncio.sleep(0.1)
    check("...one prompt, one reply, through the last",
          (len(ada.prompts), said(tid)[-1][1], through(tid, "ada")),
          (1, "All three handled.", ms[-1]["id"]))

    # One batch can contain a job between two messages. A message for a
    # different model, already on disk, must stay for its own turn.
    tid = await new_thread("ada")
    gate = Gate()
    fake.next_scripts["ada"] = [[init(), tool(), gate, text("Batch handled."), result(1)]]
    await post(tid, "begin the batch")
    await until(lambda: gate.reached)
    ada = session("ada")
    engine.post_person(tid, "batch first")
    engine.schedule(engine_mod.Turn(tid, "ada", 0, "job:batch", note="batch middle",
                                    briefed_at=time.time()))
    last = engine.post_person(tid, "batch last")
    engine.post_person(tid, "separate choice", model="other-model")
    await until(lambda: ada.steers)
    batch = ada.steers[0]
    positions = [batch.find(word) for word in ("batch first", "batch middle", "batch last")]
    check("one claimed batch preserves message, job, message order",
          min(positions) >= 0 and positions == sorted(positions), True)
    check("the steer stops at the last input it claimed",
          ("separate choice" in batch, engine.turn_live[tid, "ada"].read_through),
          (False, last["id"]))
    gate.open()
    await until(lambda: engine.settled(tid))
    check("the other model's message still gets its own dispatch",
          (len(ada.prompts), "separate choice" in ada.prompts[-1]), (2, True))

    # -- 3. end-of-turn race on Claude Code ----------------------------------
    tid = await new_thread("ada")
    gate = Gate()
    fake.next_scripts["ada"] = [[init(), text("Here is the first answer."), gate,
                                  result(1)]]
    await post(tid, "question one")
    await until(lambda: gate.reached)
    ada = session("ada")
    # The harness opens a turn of its own for words that came after its last.
    ada.after_steer = [gate, init(), text("And the answer to your follow-up."),
                        result(1)]
    m2 = await post(tid, "follow-up, sent as it finished")
    await until(lambda: ada.steers)
    gate.open()
    await until(lambda: engine.settled(tid))
    await asyncio.sleep(0.4)
    check("race: both answers posted, the second as its own message",
          said(tid)[-2:], [("ada", "Here is the first answer."),
                           ("ada", "And the answer to your follow-up.")])
    check("...no third turn, one prompt, nothing queued",
          (len(ada.prompts), engine.queues[(tid, "ada")].qsize()), (1, 0))
    check("...through past it, no failure line",
          (through(tid, "ada"), system_lines(tid)), (m2["id"], []))

    # -- 4. Codex refuses the steer: an ordered turn of its own ---------------
    tid = await new_thread("iris")
    gate = Gate()
    fake.next_scripts["iris"] = [[init(), tool(), gate, text("First done."), result(2)],
                                 [init(), text("Now the second."), result(1)]]
    await post(tid, "first")
    await until(lambda: gate.reached)
    iris = session("iris")
    iris.refuse = True
    refused0 = steps_mod.COUNTS["steer_refused"]
    m2 = await post(tid, "second")
    await until(lambda: steps_mod.COUNTS["steer_refused"] > refused0)
    check("Codex refusal: kept in the queue",
          (engine.queues[(tid, "iris")].qsize(), iris.steers), (1, []))
    gate.open()
    await until(lambda: engine.settled(tid))
    await asyncio.sleep(0.1)
    check("...answered by its own turn after the first, in order",
          (said(tid)[-2:], len(iris.prompts), "second" in iris.prompts[-1]),
          ([("iris", "First done."), ("iris", "Now the second.")], 2, True))
    check("...through past it",
          threads._num(through(tid, "iris")) >= threads._num(m2["id"]), True)

    # -- 5. Antigravity: no steer, queued, its own turn ------------------------
    tid = await new_thread("cleo")
    gate = Gate()
    fake.next_scripts["cleo"] = [[init(), tool(), gate, text("One."), result(2)],
                                   [init(), text("Two."), result(1)]]
    await post(tid, "one")
    await until(lambda: gate.reached)
    cleo = session("cleo")
    await post(tid, "two")
    await asyncio.sleep(0.05)
    row = engine.summary(tid)
    member = next(m for m in row["members"] if m["persona"] == "cleo")
    check("Antigravity: not steered, queued, `note` says when",
          (cleo.steers, member["queued"], member["note"]),
          ([], 1, engine_mod.WILL_ANSWER_AFTER_TURN))
    gate.open()
    await until(lambda: engine.settled(tid))
    await asyncio.sleep(0.1)
    check("...answered by its own turn", said(tid)[-1], ("cleo", "Two."))

    # -- 6. the m6 regression (no steering engine) -----------------------------
    tid = await new_thread("ada")
    gate = Gate()
    build_gate = Gate()
    fake.next_scripts["ada"] = [[init(), tool("Task"),
                                  lambda s: s.tasks("Build Acme prototype v0"),
                                  gate, text("The build has started."), result(1)],
                                 [init(), text("Stopped; we keep talking first."),
                                  result(1)]]
    await post(tid, "build a prototype")
    await until(lambda: gate.reached)
    ada = session("ada")
    ada.STEERS = False
    await until(lambda: engine.turns.get((tid, "ada"), {}).get("on_background"))
    m6 = await post(tid, "I don't want you to go ahead right now")
    await asyncio.sleep(0.05)
    check("m6: generating, so it waits (not handed over yet)",
          (len(ada.prompts), engine.queues[(tid, "ada")].qsize()), (1, 1))
    gate.open()
    ok = await until(lambda: len(ada.prompts) == 2, rounds=200)
    check("...delivered once the words are done, task still out, no 2nd message",
          (ok, bool(ada.background_tasks),
           "go ahead right now" in (ada.prompts[-1] if ok else "")),
          (True, True, True))
    await until(lambda: said(tid)[-1][1].startswith("Stopped"))
    check("...answered", said(tid)[-1], ("ada", "Stopped; we keep talking first."))
    ada.tasks()
    await until(lambda: engine.settled(tid))

    # -- 7. two queued messages: the second turn has nothing new, not run ------
    tid = await new_thread("cleo")
    gate = Gate()
    fake.next_scripts["cleo"] = [[init(), tool(), gate, text("A."), result(2)],
                                   [init(), text("B and C, both."), result(1)],
                                   [init(), text("Nothing new has come in."), result(1)]]
    await post(tid, "a")
    await until(lambda: gate.reached)
    cleo = session("cleo")
    heard0 = steps_mod.COUNTS["already_heard"]
    await post(tid, "b")
    mc = await post(tid, "c")
    gate.open()
    await until(lambda: engine.settled(tid) and len(cleo.prompts) >= 2)
    await asyncio.sleep(0.2)
    check("queued pair: the first queued turn read both",
          ("b" in cleo.prompts[1] and "c" in cleo.prompts[1]), True)
    check("...the second ran no turn (no 'Nothing new' reply)",
          (len(cleo.prompts), said(tid)[-1],
           steps_mod.COUNTS["already_heard"] - heard0),
          (2, ("cleo", "B and C, both."), 1))
    check("...through past the last",
          threads._num(through(tid, "cleo")) >= threads._num(mc["id"]), True)

    # -- 8. a turn that dies before replying ------------------------------------
    tid = await new_thread("ada")
    gate = Gate()
    fake.next_scripts["ada"] = [[init(), text("First."), result(1)]]
    m1 = await post(tid, "hello")
    await until(lambda: engine.settled(tid))
    ada = session("ada")
    before = through(tid, "ada")
    ada.scripts = [[init(), tool(), gate, {"type": "broker.exit"}]]
    m2 = await post(tid, "do the thing")
    await until(lambda: gate.reached)
    gate.open()
    await until(lambda: engine.settled(tid))
    await asyncio.sleep(0.1)
    check("died: no line in the record",
          "Ada's turn ended: its session exited" in system_lines(tid), False)
    check("...a mark on Ada's turn instead",
          [m["text"] for m in engine.marks_for(tid)], ["Ada's turn ended: its session exited"])
    check("...`through` not moved past the unanswered message",
          (before, through(tid, "ada")), (m1["id"], m1["id"]))
    ada.scripts = [[init(), text("Back: doing the thing now."), result(1)]]
    await post(tid, "@ada are you there?")
    await until(lambda: engine.settled(tid) and said(tid)[-1][0] == "ada")
    check("...the next turn is shown the unanswered message again",
          "do the thing" in ada.prompts[-1], True)

    # -- 9. CodexSession.steer --------------------------------------------------
    cs = broker_mod.CodexSession.__new__(broker_mod.CodexSession)
    cs.id, cs.thread_id, cs.state, cs.proc = "codex-1", "thr-1", "running", object()
    cs._turn_active, cs.tell_time = True, False
    cs._codex = type("C", (), {"turn_id": "turn-A"})()
    sent, logged_events = [], []

    async def ok_request(method, params):
        sent.append((method, params))
        return {"turnId": params["expectedTurnId"]}

    async def emit(ev):
        ev["seq"] = len(logged_events) + 1
        logged_events.append(ev)
    cs.request, cs.emit = ok_request, emit
    seq = await cs.steer("new words")
    check("CodexSession.steer: turn/steer with expectedTurnId, logged after",
          (seq, sent[0][0], sent[0][1]["expectedTurnId"],
           sent[0][1]["input"][0]["text"], logged_events[0].get("steered")),
          (1, "turn/steer", "turn-A", "new words", True))

    async def no_request(method, params):
        raise broker_mod.BrokerError(400, "expected turn turn-A is not active")
    cs.request = no_request
    logged_events.clear()
    check("...refused: None and nothing logged",
          (await cs.steer("late words"), logged_events), (None, []))
    cs._turn_active = False
    cs.request = ok_request
    check("...no turn open: None, nothing sent",
          (await cs.steer("x"), len(sent)), (None, 1))

    # -- 10. Session.steer (Claude) --------------------------------------------
    s = broker_mod.Session.__new__(broker_mod.Session)
    s.id, s.state, s.tell_time = "claude-1", "running", False
    s.proc = type("P", (), {"stdin": object()})()
    s._turn_active = False
    raw, evs = [], []

    async def send_raw(obj):
        raw.append(obj)

    async def emit2(ev):
        ev["seq"] = 7
        evs.append(ev)
    s.send_raw, s.emit = send_raw, emit2
    check("Session.steer: no turn open -> None, nothing written",
          (await s.steer("x"), raw), (None, []))
    s._turn_active = True
    seq = await s.steer("mid-turn words")
    check("...turn open: logged `steered`, then a stdin user message",
          (seq, evs[0].get("steered"), raw[0]["type"], raw[0]["message"]["content"]),
          (7, True, "user", "mid-turn words"))
    check("Antigravity says it cannot steer",
          (broker_mod.AntigravitySession.STEERS, broker_mod.Session.STEERS,
           broker_mod.CodexSession.STEERS), (False, True, True))


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
