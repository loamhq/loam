#!/usr/bin/env python3
"""Check background replies, member activity, model reporting and mention delivery after a turn ends."""

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
(TMP / "vault" / "00_INBOX" / "from-agents" / "comms.md").write_text("# From agents\n")

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
        script, self.script = self.script or [init(), text("hi"), result(1)], []

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
            return self.sessions[session_id]
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


async def main():
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = Broker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    status, row = await call(server, "POST", "/v1/threads", {"persona": "ada"})
    tid = row["id"]

    # -- model on every persona message ---------------------------------------
    w = FakeWriter()
    stream = asyncio.create_task(server.thread_stream(tid, None, w, full=True))
    await asyncio.sleep(0.02)
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "hello"})
    await settle(server, tid)
    ada = session_of(fake, "ada")
    msgs = threads.read(tid)[1]
    first = [m for m in msgs if m["author"] == "ada"][-1]
    check("a reply with no model said anywhere carries no `model`",
          ("model" in first, first.get("engine")), (False, "claude-code"))
    ada.script = [init("claude-opus-5-5"), text("On Opus.", "claude-opus-5-5"), result(1)]
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "which model?"})
    await settle(server, tid)
    await asyncio.sleep(0.03)
    said = threads.read(tid)[1][-1]
    check("the model the engine said it used is on the readable record",
          (said["text"], said.get("model"), said.get("engine")),
          ("On Opus.", "claude-opus-5-5", "claude-code"))
    entry = (await call(server, "GET", f"/v1/threads/{tid}/steps",
                        params={"message": said["id"]}))[1]["steps"][0]
    check("...on the full record entry", entry["model"], "claude-opus-5-5")
    done = [e["message"] for e in w.items() if e["type"] == "message.done"
            and e["message"]["id"] == said["id"]]
    check("...on message.done", [m.get("model") for m in done], ["claude-opus-5-5"])
    page = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    check("...and on the thread read, older messages left without one",
          [m.get("model") for m in page["messages"] if m["author"] == "ada"],
          [None, "claude-opus-5-5"])
    snap = [e for e in w.items() if e["type"] == "thread.snapshot"][0]
    check("the stream's snapshot carries the members array",
          [m["persona"] for m in snap["members"]], ["ada"])
    stream.cancel()

    # -- a reply after the result posts at once -------------------------------
    w = FakeWriter()
    stream = asyncio.create_task(server.thread_stream(tid, None, w))
    await asyncio.sleep(0.02)
    ada.script = [init(), text("I've briefed the job."), result(1)]
    before = len(threads.read(tid)[1])
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "brief it"})
    await settle(server, tid)
    check("the turn ended and a watch stays on the session",
          ((tid, "ada") in server.threads.tails, tid in server.threads.active),
          (True, False))
    # Later, with no new request: a background task reports and the CLI
    # hands the persona another turn.
    await asyncio.sleep(0.3)
    ada.emit({"type": "system", "subtype": "task_notification", "task_id": "a1"})
    ada.emit(init())
    ada.emit(text("The job landed at 1a2b3c4."))
    ada.emit(result(2))
    at = time.monotonic()
    posted = None
    while time.monotonic() - at < 2:
        await asyncio.sleep(0.01)
        posted = next((m for m in threads.read(tid)[1][before:]
                       if m["text"] == "The job landed at 1a2b3c4."), None)
        if posted:
            break
    check("a reply after the result, no new request: posted within 2 s",
          bool(posted) and time.monotonic() - at < 2, True)
    check("...as its own message, not late",
          [(m["author"], m["text"], m.get("late")) for m in threads.read(tid)[1][before:]],
          [(OWNER, "brief it", None), ("ada", "I've briefed the job.", None),
           ("ada", "The job landed at 1a2b3c4.", None)])
    await asyncio.sleep(0.03)
    done = [e["message"]["text"] for e in w.items() if e["type"] == "message.done"]
    check("...with its own message.done on the stream",
          done[-1], "The job landed at 1a2b3c4.")
    entry = (await call(server, "GET", f"/v1/threads/{tid}/steps",
                        params={"message": posted["id"]}))[1]["steps"][0]
    check("...and a full record entry that says why: notification",
          entry["ended"], "notification")
    counts = (await call(server, "GET", "/v1/threads/writer"))[1]
    check("GET /v1/threads/writer counts it as late_prompt, not late_recovered",
          (counts["late_prompt"], counts["late_recovered"]), (1, 0))
    # The next turn does not post it a second time.
    ada.script = [init(), text("Next."), result(3)]
    before = len(threads.read(tid)[1])
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "next"})
    await settle(server, tid)
    check("the next turn does not post it again",
          [m["text"] for m in threads.read(tid)[1][before:]], ["next", "Next."])

    # A turn asked from outside the thread is not the thread's.
    before = len(threads.read(tid)[1])
    ada.emit({"type": "broker.user_message", "text": "from classic"})
    ada.emit(init())
    ada.emit(text("Answer in the classic view."))
    ada.emit(result(4))
    await asyncio.sleep(0.05)
    check("a turn somebody asked outside the thread is not posted",
          threads.read(tid)[1][before:], [])
    stream.cancel()

    # -- a restart still recovers ---------------------------------------------
    server.threads.stop_all_tails()
    server.threads = engine_mod.ThreadEngine(fake)
    ada.emit(init())
    ada.emit(text("Written while the broker was down."))
    ada.emit(result(5))
    ada.script = [init(), text("Back."), result(6)]
    before = len(threads.read(tid)[1])
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "back?"})
    await settle(server, tid)
    check("after a restart the reply nobody watched comes in late, first",
          [(m["text"], m.get("late")) for m in threads.read(tid)[1][before:]
           if m["author"] == "ada"],
          [("Written while the broker was down.", True), ("Back.", None)])

    # The watch ends with the session.
    ada.emit({"type": "broker.exit", "exit_code": 0})
    await asyncio.sleep(0.02)
    check("the session exiting ends the watch",
          (tid, "ada") in server.threads.tails, False)
    ada.alive = False
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "again"})
    await settle(server, tid)
    check("no watch is kept on a session that is not alive",
          (tid, "ada") in server.threads.tails, False)
    ada.alive = True

    # -- background tasks per member and per thread ---------------------------
    w = FakeWriter()
    stream = asyncio.create_task(server.thread_stream(tid, None, w))
    lw = FakeWriter()
    lstream = asyncio.create_task(server.thread_list_stream(lw, False))
    await asyncio.sleep(0.02)

    def launch(s):
        s.tasks("brief the deploy", "read the logs")

    ada.script = [init(), text("Two subagents are out."), launch, result(7)]
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "go"})
    await asyncio.sleep(0.15)
    page = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    member = page["thread"]["members"][0]
    check("GET /v1/threads/:id: background 2; the turn in flight (busy), parked",
          (member["background"], member["busy"], member["working"]), (2, True, False))
    rows = (await call(server, "GET", "/v1/threads"))[1]["threads"]
    mine = next(r for r in rows if r["id"] == tid)
    check("GET /v1/threads: the row sums background, counts working",
          (mine["background"], mine["working"]), (2, 0))
    ada.tasks("read the logs")
    await asyncio.sleep(0.02)
    ada.tasks()
    await asyncio.sleep(0.02)
    ada.emit({"type": "system", "subtype": "task_notification", "task_id": "x"})
    ada.emit(init())
    await asyncio.sleep(0.02)
    mid = (await call(server, "GET", f"/v1/threads/{tid}"))[1]["thread"]
    check("the notification's turn: working again",
          (mid["members"][0]["working"], mid["working"], mid["members"][0]["busy"]),
          (True, 1, True))
    ada.emit(text("Both reported."))
    ada.emit(result(8))
    await settle(server, tid)
    await asyncio.sleep(0.03)
    end = (await call(server, "GET", f"/v1/threads/{tid}"))[1]["thread"]["members"][0]
    check("...and done: nothing out, nobody working, no turn",
          (end["background"], end["working"], end["busy"]), (0, False, False))
    stream.cancel()
    lstream.cancel()
    seen = [(e["members"][0]["background"], e["members"][0]["working"], e["background"])
            for e in w.items() if e["type"] == "members"]
    tail = seen[next(i for i, s in enumerate(seen) if s == (2, False, 2)):]
    # Another field of the array (`last_spoke_at`, `busy`) can change between
    # two that read the same here; each is still a whole `members`.
    tail = [s for i, s in enumerate(tail) if i == 0 or s != tail[i - 1]]
    check("`members` on the stream as they change: 2, 1, 0, working, done",
          tail, [(2, False, 2), (1, False, 1), (0, False, 0), (0, True, 0),
                 (0, False, 0)])
    check("...each one the full members array, persona and all",
          all(e["members"][0]["persona"] == "ada" and "engine" in e["members"][0]
              for e in w.items() if e["type"] == "members"), True)
    check("...and the thread list hears each change as thread.changed",
          any(e["type"] == "thread.changed" and e["summary"]["background"] == 2
              for e in lw.items()), True)
    check("the reply after the tasks reported was posted",
          threads.read(tid)[1][-1]["text"], "Both reported.")

    # -- a parked turn whose wait settles with no event ------------------------
    # The broker's settle backstop clears a stale awaited task and says
    # nothing on the stream; the turn must notice and end, not wait forever.
    engine_mod.PARKED_CHECK_S = 0.05

    def stale(s):
        s.behind = True

    ada.script = [init(), text("Done, one thing still settling."), stale, result(9)]
    before = len(threads.read(tid)[1])
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "and?"})
    await asyncio.sleep(0.15)
    check("a turn parked on a stale wait is still in flight", tid in server.threads.active,
          True)
    ada.behind = False          # settled, and no event says so
    await settle(server, tid, rounds=100)
    check("...ends within the check once nothing is out: the reply, no system line",
          (tid in server.threads.active,
           [(m["author"], m["text"]) for m in threads.read(tid)[1][before:]]),
          (False, [(OWNER, "and?"), ("ada", "Done, one thing still settling.")]))

    # -- the transcript marker for a Codex session ----------------------------
    status, row = await call(server, "POST", "/v1/threads", {"persona": "iris"})
    cid = row["id"]
    status, _ = await call(server, "PATCH", f"/v1/threads/{cid}",
                           {"engine": {"iris": "codex"}})
    await call(server, "POST", f"/v1/threads/{cid}/messages", {"text": "hi"})
    await settle(server, cid)
    marks = threads.STATE / "steps" / "sessions"
    iris = session_of(fake, "iris")
    check("a Codex member's session: marked under its thread_id as well",
          (iris.provider, (marks / iris.id).is_file(),
           (marks / "019a-codex-thread").read_text().strip()), ("codex", True, cid))

    # -- @all -----------------------------------------------------------------
    status, row = await call(server, "POST", "/v1/threads", {"persona": "ada"})
    aid = row["id"]
    await call(server, "PATCH", f"/v1/threads/{aid}", {"add": ["iris", "cleo"]})
    meta = threads.read(aid)[0]
    check("a three-member thread", sorted(meta["members"]), ["ada", "cleo", "iris"])
    started = []
    real = server.threads.run_turn

    async def counting(turn):
        started.append(turn.persona)
        return await real(turn)
    server.threads.run_turn = counting
    fan0 = (await call(server, "GET", "/v1/threads/writer"))[1]["fanout"]
    await call(server, "POST", f"/v1/threads/{aid}/messages", {"text": "@all status?"})
    await settle(server, aid)
    check("@all from the owner in a three-member thread: three turns",
          sorted(started), ["ada", "cleo", "iris"])
    check("...counted as fanout", (await call(server, "GET", "/v1/threads/writer"))[1]
          ["fanout"] - fan0, 3)
    started.clear()
    await call(server, "POST", f"/v1/threads/{aid}/messages",
               {"text": "@everyone and @ada especially"})
    await settle(server, aid)
    check("@everyone beside @ada: one turn each, Ada's not doubled",
          sorted(started), ["ada", "cleo", "iris"])
    started.clear()
    session_of(fake, "ada").script = [init(), text("Over to @all of you."), result(1)]
    await call(server, "POST", f"/v1/threads/{aid}/messages", {"text": "@ada say it"})
    await settle(server, aid)
    check("@all from a persona is plain text: only the turn it was given",
          started, ["ada"])
    status, row = await call(server, "POST", "/v1/threads", {})
    nid = row["id"]
    await call(server, "POST", f"/v1/threads/{nid}/messages", {"text": "@all anyone?"})
    await settle(server, nid)
    check("@all with no default and no members: the record holds only the message",
          [(m["author"], m["text"]) for m in threads.read(nid)[1]],
          [(OWNER, "@all anyone?")])
    check("...and the thread carries its own mark saying so",
          [(m["persona"], m["text"]) for m in server.threads.marks_for(nid)],
          [(None, "Nobody to reach: bring someone in first")])
    server.threads.stop_all_tails()


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
