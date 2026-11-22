#!/usr/bin/env python3
"""Check that each reply keeps its own text, ordered steps and completion event, including after recovery."""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
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
(TMP / "ai-labs" / "iris" / "agent.json").write_text(
    json.dumps({"name": "Iris", "provider": "codex"}))
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
# Supply fixture memory independently of the machine running the tests.
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
inputs_mod = engine_mod.inputs
broker_mod.codex.model_ok = lambda value: value in ("gpt-6", "gpt-6-luna")

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



def text(t):
    return {"type": "assistant", "parent_tool_use_id": None,
            "message": {"content": [{"type": "text", "text": t}]}}


def tool(tid, name, inp, parent=None):
    return {"type": "assistant", "parent_tool_use_id": parent,
            "message": {"content": [{"type": "tool_use", "id": tid, "name": name,
                                     "input": inp}]}}


def output(tid, out, parent=None):
    return {"type": "user", "parent_tool_use_id": parent,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
                                     "content": out}]},
            "tool_use_result": {"stdout": out}}


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
        self.behind = False
        self.background_tasks = []
        self.log = []
        self.seq = 0

    def save(self):
        pass

    def watch(self, q):
        self.subs.add(q)

    def unwatch(self, q):
        self.subs.discard(q)

    def working_behind(self):
        return self.behind

    def replay(self, since):
        return [e for e in self.log if e["seq"] > since]

    def emit(self, ev):
        self.seq += 1
        ev = {**ev, "seq": self.seq}
        self.log.append(ev)
        for q in list(self.subs):
            q.put_nowait(ev)

    async def send_message(self, prompt, attachments=None):
        script, self.script = self.script or [text("hi"), result(0)], []

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


async def main():
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = Broker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    status, row = await call(server, "POST", "/v1/threads", {"persona": "ada"})
    tid = row["id"]
    # Open the session before attaching its scripted responses.
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "hello"})
    await settle(server, tid)
    ada = next(iter(fake.sessions.values()))

    # -- reply, then a second reply with no new request ---------------------
    stream_w = FakeWriter()
    stream = asyncio.create_task(server.thread_stream(tid, None, stream_w, full=True))
    await asyncio.sleep(0.05)

    def launch(s):
        s.behind = True
        s.background_tasks = [{"task_id": "a1", "description": "brief the job"}]

    def report(s):
        s.behind = False
        s.background_tasks = []

    big = "x" * 300_000
    ada.script = [
        text("Reading first."),
        tool("t1", "Bash", {"command": "cat big", "description": "Read the big file"}),
        output("t1", big),
        {"type": "broker.permission_request", "request_id": "r1",
         "tool_name": "AskUserQuestion", "kind": "question"},
        {"type": "broker.permission_resolved", "request_id": "r1", "answer": "yes"},
        text("Caught up. Here is the status."),
        tool("t2", "Agent", {"description": "Brief the job", "run_in_background": True}),
        output("t2", "launched"),
        text("I've briefed it."),
        launch, result(1),
        # the subagent still works after the persona spoke
        tool("t3", "Read", {"file_path": "brief.md"}, parent="t2"),
        output("t3", "the brief", parent="t2"),
        {"type": "system", "subtype": "task_notification", "task_id": "a1"},
        {"type": "system", "subtype": "init"}, report,
        text("The fixes are built and merged."),
        result(2),
    ]
    before = len(threads.read(tid)[1])
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "catch up"})
    await settle(server, tid)
    await asyncio.sleep(0.05)
    stream.cancel()
    msgs = threads.read(tid)[1][before:]
    replies = [m for m in msgs if m["author"] == "ada"]
    check("reply, then a second reply with no new request: two messages",
          [m["text"] for m in replies],
          ["I've briefed it.", "The fixes are built and merged."])
    check("...each with its own id, in the order said",
          [threads._num(m["id"]) for m in replies] == sorted(
              {threads._num(m["id"]) for m in replies}) and len(replies) == 2, True)
    check("...the first keeps its earlier speech whole as segments",
          [seg.get("text") for seg in replies[0].get("segments", []) if "text" in seg],
          ["Reading first.", "Caught up. Here is the status."])
    check("...the subagent's step is the second reply's, not a wiped text",
          [d["label"] for d in replies[1]["did"]][-1:], ["↳ Read: brief.md"])
    done = [e["message"] for e in stream_w.items() if e["type"] == "message.done"
            and e["message"]["author"] == "ada"]
    check("the live stream says them the same way: two message.done",
          [m["text"] for m in done], [m["text"] for m in replies])
    check("...and ?steps=full on the stream carries each one's steps",
          [bool(m.get("steps")) for m in done], [True, True])

    # -- the full record ------------------------------------------------------
    status, got = await call(server, "GET", f"/v1/threads/{tid}/steps",
                             params={"message": replies[0]["id"]})
    entry = got["steps"][0]
    kinds = [s["type"] for s in entry["steps"]]
    check("the full record: one entry for the message, from its session",
          (status, entry["message"], entry["author"], entry["session_id"],
           entry["engine"]), (200, replies[0]["id"], "ada", ada.id, "claude-code"))
    check("...every step in order: text, tool, cards, text, tool, text, end",
          kinds, ["text", "tool", "permission", "permission_resolved", "text",
                  "tool", "text", "end"])
    bash = entry["steps"][1]
    check("...a tool call with its input and its untrimmed output",
          (bash["name"], bash["input"]["command"], len(bash["output"]),
           bash["is_error"], bash["result"]["tool_use_result"]["stdout"] == big),
          ("Bash", "cat big", 300_000, False, True))
    check("...and the seq range it came from in the session log",
          entry["seq"][0] < entry["seq"][1], True)
    second = (await call(server, "GET", f"/v1/threads/{tid}/steps",
                         params={"message": replies[1]["id"]}))[1]["steps"][0]
    # Carry the parent call into the next reply so helper steps keep their nesting.
    check("the second reply's record holds the subagent's call and the notification",
          [(s["type"], s.get("name") or (s.get("event") or {}).get("subtype"),
            s.get("parent")) for s in second["steps"]][:4],
          [("tool", "Agent", None), ("tool", "Read", "t2"),
           ("event", "task_notification", None), ("event", "init", None)])
    check("...the stub is t2, marked carried",
          (second["steps"][0]["id"], second["steps"][0].get("carried")), ("t2", True))
    status, page = await call(server, "GET", f"/v1/threads/{tid}")
    check("the default read stays light: no steps on it",
          any("steps" in m for m in page["messages"]), False)
    status, page = await call(server, "GET", f"/v1/threads/{tid}",
                              params={"steps": "full"})
    by_id = {m["id"]: m for m in page["messages"]}
    check("?steps=full: replies carry theirs, the owner's message null",
          (bool(by_id[replies[0]["id"]]["steps"]),
           [m["steps"] for m in page["messages"] if m["author"] == OWNER][-1]),
          (True, None))
    status, missing = await call(server, "GET", f"/v1/threads/{tid}/steps",
                                 params={"message": "m999"})
    check("a message with no full record is a 404 on ?message=", status, 404)
    check("the file sits beside the thread state, not in the vault",
          (threads.STATE / "steps" / f"{tid}.jsonl").is_file()
          and not list((threads.VAULT).rglob("*.jsonl")), True)
    check("the transcript hook's marker names this session's thread",
          (threads.STATE / "steps" / "sessions" / ada.id).read_text().strip(), tid)

    # Resume a session that finishes a reply while the thread worker is offline.
    server.threads.stop_all_tails()
    server.threads = engine_mod.ThreadEngine(fake)
    ada.emit(text("Live at 9da1888."))
    ada.emit(result(3))
    ada.script = [text("Here."), result(4)]
    before = len(threads.read(tid)[1])
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "and now?"})
    await settle(server, tid)
    late = [m for m in threads.read(tid)[1][before:] if m["author"] == "ada"]
    check("a reply made while nobody watched is posted at the next turn, first",
          [(m["text"], m.get("late")) for m in late],
          [("Live at 9da1888.", True), ("Here.", None)])
    status, counts = await call(server, "GET", "/v1/threads/writer")
    check("GET /v1/threads/writer counts replies, the late one, and no failures",
          (status, counts["replies"] >= 4, counts["late_recovered"],
           counts["steps_failed"]), (200, True, 1, 0))

    def watch_stream():
        w = FakeWriter()
        return w, asyncio.create_task(server.thread_stream(tid, None, w))

    def no_placeholder(msgs, events):
        texts = [m.get("text") or "" for m in msgs]
        texts += [(e.get("message") or {}).get("text") or "" for e in events]
        return not any("(no reply" in t for t in texts)

    # 1. steps-only harness turn, then one that speaks: one message
    ada.script = [
        tool("s1", "Agent", {"description": "Brief the deploy",
                             "run_in_background": True}),
        output("s1", "launched"), launch, result(6),
        {"type": "system", "subtype": "task_notification", "task_id": "a1"},
        {"type": "system", "subtype": "init"}, report,
        text("Deployed."), result(7),
    ]
    w, task = watch_stream()
    await asyncio.sleep(0.02)
    before = len(threads.read(tid)[1])
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "deploy it"})
    await settle(server, tid)
    await asyncio.sleep(0.02)
    task.cancel()
    new = threads.read(tid)[1][before:]
    said = [m for m in new if m["author"] == "ada"]
    check("steps-only turn, then a spoken one: one message, the words",
          [m["text"] for m in said], ["Deployed."])
    check("...carrying the silent turn's steps",
          [d["label"] for d in said[0]["did"]][:1], ["Agent: Brief the deploy"])
    check("...no system line, and no placeholder on the record or the stream",
          ([m["kind"] for m in new], no_placeholder(new, w.items())),
          (["message", "message"], True))

    # 2. steps-only, then the session exits: no message, a mark on the turn
    ada.script = [
        tool("s2", "Bash", {"command": "make", "description": "Build it"}),
        output("s2", "ok"),
        {"type": "broker.exit", "exit_code": 1},
    ]
    w, task = watch_stream()
    await asyncio.sleep(0.02)
    before = len(threads.read(tid)[1])
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "build"})
    await settle(server, tid)
    await asyncio.sleep(0.02)
    task.cancel()
    new = threads.read(tid)[1][before:]
    check("steps-only, then the session exits: no Ada message, nothing but his in the record",
          [(m["author"], m["text"]) for m in new], [(OWNER, "build")])
    check("...a mark on Ada's turn says it ended",
          [(m["persona"], m["text"]) for m in server.threads.marks_for(tid)],
          [("ada", "Ada's turn ended: its session exited")])
    done = [e["message"] for e in w.items() if e["type"] == "message.done"]
    marked = [e.get("text") for e in w.items() if e["type"] == "turn.mark"]
    check("...the stream says the same, and no placeholder anywhere",
          ([(m["author"], m["kind"]) for m in done], "Ada's turn ended: its session exited" in marked,
           no_placeholder(new, w.items())),
          ([(OWNER, "message")], True, True))
    entries = (await call(server, "GET", f"/v1/threads/{tid}/steps"))[1]["steps"]
    unspoken = [e for e in entries if e.get("unspoken")]
    check("...its steps are in the full record, on an entry with no message",
          [(e["message"], e["after"], [s.get("name") for s in e["steps"]
                                       if s["type"] == "tool"])
           for e in unspoken], [(None, new[0]["id"], ["Bash"])])

    # 3. a message arriving mid-turn; the resumed session's empty result
    filler = [lambda s: None] * 12
    ada.script = [
        {"type": "system", "subtype": "task_notification", "status": "stopped"},
        {"type": "system", "subtype": "init"},
        {"type": "result", "parent_tool_use_id": None, "num_turns": 0},
        {"type": "system", "subtype": "init"},
        *filler, text("Here is where we are."), result(8),
    ]
    w, task = watch_stream()
    await asyncio.sleep(0.02)
    before = len(threads.read(tid)[1])
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "where are we?"})
    await asyncio.sleep(0.02)          # past the empty result, mid-turn
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "and one more"})
    await settle(server, tid)
    await asyncio.sleep(0.02)
    task.cancel()
    new = threads.read(tid)[1][before:]
    check("a message mid-turn: the empty result is not the end; no '(no reply)'",
          [(m["author"], m["text"]) for m in new],
          [(OWNER, "where are we?"), (OWNER, "and one more"),
           ("ada", "Here is where we are."), ("ada", "hi")])
    check("...on the record or the stream",
          no_placeholder(new, w.items()), True)

    # A waiting step can follow visible reply text.
    ada.script = [
        text("Status only: the job is landing."),
        {"type": "broker.background_tasks", "count": 1,
         "tasks": [{"description": "Brief the deploy"}]},
        {"type": "broker.exit", "exit_code": 143},
    ]
    before = len(threads.read(tid)[1])
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "status?"})
    await settle(server, tid)
    new = threads.read(tid)[1][before:]
    check("words, then the waiting step, then exit: the words are the message",
          [(m["author"], m["text"]) for m in new][1:],
          [("ada", "Status only: the job is landing.")])
    check("...and the ending is a mark, not a line",
          [m["text"] for m in server.threads.marks_for(tid)],
          ["Ada's turn ended: its session exited"])

    # Shutdown preserves partial replies and leaves unstarted input on disk.
    def stopping(s):
        fake.stopping = True
    ada.script = [text("Working on it."),
                   tool("s5", "Bash", {"command": "sleep 60"}), stopping,
                   {"type": "broker.exit", "exit_code": 143}]
    before = len(threads.read(tid)[1])
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "go"})
    await settle(server, tid)
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "still there?"})
    await settle(server, tid)
    new = threads.read(tid)[1][before:]
    check("a turn the broker's stop cuts after words, and one it never starts",
          [(m["author"], m["text"]) for m in new],
          [(OWNER, "go"), ("ada", "Working on it."), (OWNER, "still there?")])
    check("...the cut turn's ending is a mark on it",
          [m["text"] for m in server.threads.marks_for(tid)],
          ["Ada's turn ended: the broker restarted"])
    kept = inputs_mod.items(tid, "ada")
    check("...the cut turn's input is settled; the unstarted one is kept, unsent",
          [(i["reason"], "sent" in i) for i in kept], [(new[-1]["id"], False)])
    # The broker starts again: a new engine reads the list.
    fake.stopping = False
    server.threads = engine_mod.ThreadEngine(fake)
    prompts = []
    real_send = ada.send_message

    async def spy(prompt, attachments=None):
        prompts.append(prompt)
        return await real_send(prompt, attachments)
    ada.send_message = spy
    ada.script = [text("Yes, here."), result(1)]
    counts = server.threads.resume_inputs()
    await settle(server, tid)
    await asyncio.sleep(0.05)
    new = threads.read(tid)[1][before:]
    check("...at the next start it is scheduled once and answered, no line",
          (counts["scheduled"], [(m["author"], m["text"]) for m in new][-2:]),
          (1, [(OWNER, "still there?"), ("ada", "Yes, here.")]))
    check("...its prompt says when it was queued",
          len(prompts) == 1 and "was queued for you at" in prompts[0], True)
    check("...and the list is empty after", inputs_mod.items(tid, "ada"), [])
    del ada.send_message

    # -- a failed write is loud ---------------------------------------------
    real = steps_mod.steps_path
    steps_mod.steps_path = lambda t: TMP / "no-such-dir" / "\0bad"
    ada.script = [text("Again."), result(5)]
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "again"})
    await settle(server, tid)
    steps_mod.steps_path = real
    status, counts = await call(server, "GET", "/v1/threads/writer")
    check("a full record that fails to write is counted, the reply still posted",
          (counts["steps_failed"], threads.read(tid)[1][-1]["text"]), (1, "Again."))


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
