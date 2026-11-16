#!/usr/bin/env python3
"""A background helper's result is not a persona's message (2026-11-12).

Seen in thread t-6daf5a6f on 11-12: Ada launched a background subagent,
its turn ended, and the subagent's last sentence ("The Show more fix is
ready for Ada to check.") was drawn in the thread as Ada's words, as
two raw engineer receipts had been before it. The readable record and the
message's text were already right -- only top-level text is a persona's --
but the reply's full record carried the subagent's last words under a
``parent`` that named an ``Agent`` call made in the *previous* reply, so a
reader that nests by parent (the page) drew them at the top level, as
speech. ``loam_thread_steps.nest_carried`` gives such steps a ``carried``
stub of that call. Driven through the broker's routes with a fake harness:

  - a turn a task notification hands the persona, with its subagent's
    last words ahead of it, posts only the persona's own text
  - its full record has no top-level text but the persona's, and the
    subagent's steps sit under a carried stub named from the task, with the
    task's summary as its output
  - a notification turn that writes nothing posts nothing
  - a background shell command's completion: only the persona's text
  - a record written before the fix is mended as it is read
  - the readable thread file never holds the subagent's sentence

Run: python3 tests/test_thread_notification_text.py
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



AGENT_CALL = "toolu_agent_1"
SUB_SAID = "The fix is ready for Ada to check."


def agent_call(tid, description):
    return {"type": "assistant", "parent_tool_use_id": None, "message": {
        "content": [{"type": "tool_use", "id": tid, "name": "Agent",
                     "input": {"description": description, "prompt": "..."}}]}}


def child(event):
    return {**event, "parent_tool_use_id": AGENT_CALL}


def top_texts(entry):
    return [s["text"] for s in entry["steps"]
            if s.get("type") == "text" and not s.get("parent")]


async def wait_for(pred, seconds=2.0):
    at = time.monotonic()
    while time.monotonic() - at < seconds:
        await asyncio.sleep(0.01)
        if pred():
            return True
    return False


async def main():
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = Broker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    status, row = await call(server, "POST", "/v1/threads", {"persona": "ada"})
    tid = row["id"]
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "hello"})
    await settle(server, tid)
    ada = session_of(fake, "ada")

    # -- the subagent outlives the turn that launched it ----------------------
    ada.script = [init(), agent_call(AGENT_CALL, "Send Show more back"),
                   text("I've sent it back for a fix."),
                   lambda s: s.tasks("Send Show more back"), result(1)]
    before = len(threads.read(tid)[1])
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "fix it"})
    await asyncio.sleep(0.2)
    # The subagent works and finishes after the persona's turn ended; the
    # CLI hands the persona its notification as a turn of its own.
    ada.emit({"type": "system", "subtype": "task_started", "task_id": "a1",
               "tool_use_id": AGENT_CALL, "description": "Send Show more back"})
    ada.emit(child({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "toolu_child_1", "name": "ToolSearch",
         "input": {"query": "select:mcp__engineer__brief"}}]}}))
    ada.emit(child({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_child_1", "content": "ok"}]}}))
    ada.emit(child(text(SUB_SAID)))
    ada.emit({"type": "system", "subtype": "task_notification", "task_id": "a1",
               "tool_use_id": AGENT_CALL, "status": "completed",
               "summary": SUB_SAID})
    ada.tasks()
    ada.emit(init())
    ada.emit(text("Show more is live and right now."))
    ada.emit(result(2))
    await wait_for(lambda: any(m["text"] == "Show more is live and right now."
                               for m in threads.read(tid)[1]))
    await settle(server, tid)
    mine = [m for m in threads.read(tid)[1][before:] if m["author"] == "ada"]
    check("only the persona's own words are posted, one message per turn",
          [m["text"] for m in mine],
          ["I've sent it back for a fix.", "Show more is live and right now."])
    entry = (await call(server, "GET", f"/v1/threads/{tid}/steps",
                        params={"message": mine[-1]["id"]}))[1]["steps"][0]
    check("the follow-up's full record: no top-level text but the persona's",
          top_texts(entry), ["Show more is live and right now."])
    stub = [s for s in entry["steps"] if s.get("carried")]
    check("...the subagent's call is carried in as a stub, named from its task",
          [(s["id"], s["name"], s["input"], s["output"], s["parent"]) for s in stub],
          [(AGENT_CALL, "Agent", {"description": "Send Show more back"}, SUB_SAID, None)])
    ids = [s.get("id") for s in entry["steps"]]
    said_at = next(i for i, s in enumerate(entry["steps"])
                   if s.get("type") == "text" and s.get("parent") == AGENT_CALL)
    check("...ahead of the subagent's own steps, which stay under it",
          ids.index(AGENT_CALL) < said_at, True)
    launched = (await call(server, "GET", f"/v1/threads/{tid}/steps",
                           params={"message": mine[0]["id"]}))[1]["steps"][0]
    check("the reply that made the call has no stub of its own",
          [s.get("carried") for s in launched["steps"] if s.get("id") == AGENT_CALL],
          [None])

    # -- a notification turn that says nothing posts nothing -----------------
    await asyncio.sleep(0.05)
    before = len(threads.read(tid)[1])
    ada.emit({"type": "system", "subtype": "task_notification", "task_id": "a2",
               "tool_use_id": "toolu_agent_2", "status": "completed",
               "summary": "Receipt: job 2026… landed at abc1234"})
    ada.emit(init())
    ada.emit({"type": "assistant", "parent_tool_use_id": None, "message": {
        "content": [{"type": "tool_use", "id": "toolu_b1", "name": "Bash",
                     "input": {"command": "true", "description": "Note it"}}]}})
    ada.emit(result(3))
    await asyncio.sleep(0.2)
    check("a notification turn that writes nothing: nothing in the thread",
          threads.read(tid)[1][before:], [])

    # -- a background shell command's completion ------------------------------
    before = len(threads.read(tid)[1])
    ada.emit({"type": "system", "subtype": "task_notification", "task_id": "b9",
               "tool_use_id": "toolu_bash_9", "status": "completed",
               "summary": "Background command \"make\" completed (exit code 0)"})
    ada.emit(init())
    ada.emit(text("The build passed."))
    ada.emit(result(4))
    await wait_for(lambda: len(threads.read(tid)[1]) > before)
    check("a background command's completion: only the persona's sentence",
          [(m["author"], m["text"]) for m in threads.read(tid)[1][before:]],
          [("ada", "The build passed.")])

    # -- a record written before the fix ---------------------------------------
    old = {"message": "m-old", "thread": tid, "author": "ada", "steps": [
        {"type": "text", "text": SUB_SAID, "parent": "toolu_old"},
        {"type": "event", "event": {"type": "system", "subtype": "task_notification",
                                    "tool_use_id": "toolu_old", "status": "completed",
                                    "summary": SUB_SAID}},
        {"type": "text", "text": "Mine.", "parent": None}]}
    steps_mod.write(tid, old)
    mended = steps_mod.read(tid, "m-old")["m-old"]
    check("an old record is mended as it is read: stub first, then the steps",
          [(s["type"], s.get("id"), s.get("parent")) for s in mended["steps"]][:2],
          [("tool", "toolu_old", None), ("text", None, "toolu_old")])
    check("...and reading it twice adds nothing",
          len(steps_mod.nest_carried(mended["steps"])), len(mended["steps"]))

    # -- the human record ------------------------------------------------------
    files = list((TMP / "vault" / "15_CONVERSATIONS").glob("*.md"))
    check("the readable thread file never holds the subagent's sentence",
          any(SUB_SAID in f.read_text() for f in files), False)
    server.threads.stop_all_tails()


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
