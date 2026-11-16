#!/usr/bin/env python3
"""What a persona carries into a thread turn (2026-11-11).

Driven through the broker's routes with a fake harness:

  - the window heuristic: back to the last pause of more than 30 minutes, at
    most 20 messages, the newest always in, an unreadable gap no pause
  - a fresh session's first turn with no summary: told it has none and
    where to write it, the recent exchange, the thread file's path, and
    nothing older
  - reply_to: a message in the window that replies to one outside it shows
    that one whole, and nothing else from outside the window
  - a resumed session is told only what in the window is new, and how many
    new messages fell outside it
  - rotation at the threshold: a session past it gets a fresh one seeded
    with the persona's own summary; a reply the old one made unheard is
    posted first; no system line; below it, or with work out, it resumes
  - the broker never writes into a persona's workspace

Run: python3 tests/test_thread_context.py
"""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
for who in ("ada", "iris"):
    (TMP / "ai-labs" / who).mkdir(parents=True)
    (TMP / "ai-labs" / who / "agent.json").write_text(
        json.dumps({"name": who, "display": who.capitalize()}))
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


def text(t):
    return {"type": "assistant", "parent_tool_use_id": None,
            "message": {"content": [{"type": "text", "text": t}]}}


def tool(tid, name, inp):
    return {"type": "assistant", "parent_tool_use_id": None,
            "message": {"content": [{"type": "tool_use", "id": tid, "name": name,
                                     "input": inp}]}}


def output(tid, out):
    return {"type": "user", "parent_tool_use_id": None,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
                                     "content": out}]}}


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
        self.prompts = []
        self.background_tasks = []
        self.log = []
        self.seq = 0
        self.turns = []
        self.live = True

    def is_live(self):
        return self.live

    def busy(self):
        return None

    def save(self):
        pass

    def watch(self, q):
        self.subs.add(q)

    def unwatch(self, q):
        self.subs.discard(q)

    def working_behind(self):
        return False

    def replay(self, since):
        return [e for e in self.log if e["seq"] > since]

    def emit(self, ev):
        self.seq += 1
        ev = {**ev, "seq": self.seq}
        self.log.append(ev)
        for q in list(self.subs):
            q.put_nowait(ev)

    async def send_message(self, prompt, attachments=None):
        self.prompts.append(prompt)
        script, self.script = self.script or [text("ok"), result(0)], []

        async def play():
            for ev in script:
                await asyncio.sleep(0.002)
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
    W = threads.recent_window

    # -- the window heuristic ---------------------------------------------------
    def at(minute):
        return f"2026-11-11T{10 + minute // 60:02d}:{minute % 60:02d}:00Z"
    msgs = [{"id": f"m{i}", "at": at(t)} for i, t in
            enumerate([0, 5, 10, 50, 55, 60, 61], start=1)]
    check("the window stops at a gap of more than 30 minutes",
          [m["id"] for m in W(msgs)], ["m4", "m5", "m6", "m7"])
    edge = [{"id": "m1", "at": at(0)}, {"id": "m2", "at": at(30)}]
    check("a gap of exactly 30 minutes is not a pause",
          [m["id"] for m in W(edge)], ["m1", "m2"])
    many = [{"id": f"m{i}", "at": at(i)} for i in range(1, 51)]
    check("at most 20 messages", [m["id"] for m in W(many)][::19], ["m31", "m50"])
    check("the newest is always in, even after a long pause",
          [m["id"] for m in W([{"id": "m1", "at": at(0)}, {"id": "m2", "at": at(200)}])],
          ["m2"])
    odd = [{"id": "m1", "at": at(0)}, {"id": "m2", "at": None},
           {"id": "m3", "at": at(100)}]
    check("a gap that cannot be read is not a pause",
          [m["id"] for m in W(odd)], ["m1", "m2", "m3"])
    check("deterministic: the same record, the same window",
          W(msgs) == W(list(msgs)), True)
    check("an empty record is an empty window", W([]), [])

    # -- a first turn with no summary ------------------------------------------
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = Broker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    labs = TMP / "ai-labs"
    before_tree = tree(labs)

    tid = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    for i in range(30):
        threads.append(tid, threads.OWNER if i % 2 == 0 else "iris", f"old {i}")
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "hello ada"})
    await settle(server, tid)
    ada = next(s for s in fake.sessions.values() if s.agent == "ada")
    first = ada.prompts[0]
    mine = threads.summary_path("ada", tid)
    check("no summary yet: the first turn says so and where it goes",
          ("You have no summary of this thread yet" in first, str(mine) in first),
          (True, True))
    check("...the recent exchange is in: the message and the 19 before it",
          ("hello ada" in first, "old 11" in first, "old 10" in first),
          (True, True, False))
    check("...with the thread file's path for the rest",
          f"Earlier history is in the thread file, {threads.record_path(tid)}" in first,
          True)
    check("...and the tail says the summary is theirs and to keep it",
          "It is yours" in first and "update it if something" in first, True)
    check("...and the broker wrote no summary", mine.exists(), False)

    # -- reply_to outside the window -------------------------------------------
    msgs = threads.read(tid)[1]
    far = next(m for m in msgs if m["text"] == "old 2")
    ada.script = [text("about that"), result(1)]
    n = len(ada.prompts)
    await call(server, "POST", f"/v1/threads/{tid}/messages",
               {"text": "back to this", "reply_to": far["id"]})
    await settle(server, tid)
    prompt = ada.prompts[n]
    check("a reply to a message outside the window shows it whole",
          (f"{OWNER_NAME}'s {far['id']} of" in prompt, "> old 2" in prompt), (True, True))
    check("...and nothing else from outside it", "old 3" in prompt, False)
    check("a resumed session is told only what is new",
          ("New in the thread since your last turn" in prompt,
           "hello ada" in prompt), (True, False))
    check("...and gets no summary block", "<your-summary>" in prompt, False)

    # Many new messages while away: the window, and how many it left out.
    for i in range(25):
        threads.append(tid, "iris", f"while away {i}")
    n = len(ada.prompts)
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "catch up"})
    await settle(server, tid)
    prompt = ada.prompts[n]
    check("new messages outside the window are counted, not shown",
          ("[6 earlier messages new to you are not shown here" in prompt,
           "while away 5" in prompt, "while away 6" in prompt), (True, False, True))

    # -- rotation ---------------------------------------------------------------
    check("the threshold: 40% of a 200k window, else 150k",
          (engine_mod.rotate_at({"used": 1, "window": 200_000}),
           engine_mod.rotate_at({"used": 1, "window": 1_000_000}),
           engine_mod.rotate_at({"used": 1, "window": None})),
          (80_000, 150_000, 150_000))
    # The persona writes its summary (in its own workspace, as itself).
    mine.parent.mkdir(parents=True, exist_ok=True)
    mine.write_text("Ada's view: we are planning the beds. Open: soil test.\n")
    persona_wrote = {str(mine.relative_to(labs)), str(mine.parent.relative_to(labs))}
    sessions_before = len(fake.sessions)

    ada.turns = [{"context": {"used": 149_999, "window": 1_000_000}}]
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "under"})
    await settle(server, tid)
    check("below the threshold: the session is resumed",
          (len(fake.sessions), threads.session_for(tid, "ada")["session_id"]),
          (sessions_before, ada.id))
    check("...and its context is remembered",
          threads.session_for(tid, "ada").get("context"),
          {"used": 149_999, "window": 1_000_000})

    ada.turns = [{"context": {"used": 150_000, "window": 1_000_000}}]
    ada.background_tasks = [{"id": "t1", "description": "a long build"}]
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "busy"})
    await settle(server, tid)
    check("past the threshold with work out: resumed, not rotated",
          threads.session_for(tid, "ada")["session_id"], ada.id)
    ada.background_tasks = []
    server.threads.stop_all_tails()

    # A reply the old session made with nobody watching.
    ada.emit(text("a late thought nobody heard"))
    ada.emit(result(1))
    count_before = len(threads.read(tid)[1])
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "over"})
    await settle(server, tid)
    new = threads.session_for(tid, "ada")["session_id"]
    fresh = fake.sessions[new]
    msgs = threads.read(tid)[1]
    check("past the threshold: the turn starts a fresh session",
          (new != ada.id, len(fresh.prompts)), (True, 1))
    check("...seeded with the persona's own summary",
          "<your-summary>\nAda's view: we are planning the beds. Open: soil test.\n"
          "</your-summary>" in fresh.prompts[0], True)
    check("...the recent exchange and the file",
          ("over" in fresh.prompts[0], str(threads.record_path(tid)) in fresh.prompts[0]),
          (True, True))
    added = [(m["author"], m["text"], bool(m.get("late"))) for m in msgs[count_before:]]
    check("no lost reply: the old session's unheard reply, then the turn's",
          added, [(OWNER, "over", False),
                  ("ada", "a late thought nobody heard", True), ("ada", "ok", False)])
    check("...and no system line", [m for m in msgs[count_before:]
                                   if m["author"] == "system"], [])
    status, counts = await call(server, "GET", "/v1/threads/writer")
    check("the writer counts the rotation", counts.get("rotated"), 1)
    n = len(fresh.prompts)
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "next"})
    await settle(server, tid)
    check("the fresh session is resumed at the next turn",
          (threads.session_for(tid, "ada")["session_id"], len(fresh.prompts)),
          (new, n + 1))

    # A not-resident session past the threshold rotates on the remembered size.
    fresh.live = False
    fresh.turns = []
    kept = threads.session_for(tid, "ada")
    threads.remember_session(tid, "ada", kept["session_id"], kept["engine"],
                             kept["through"], seq=kept.get("seq"),
                             context={"used": 90_000, "window": 200_000})
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "later"})
    await settle(server, tid)
    check("a session no longer resident rotates on its remembered context",
          threads.session_for(tid, "ada")["session_id"] not in (new, ada.id), True)

    # -- the broker never writes into a persona workspace ------------------------
    after_tree = tree(labs)
    check("the broker wrote nothing into a persona workspace",
          sorted(set(after_tree) - set(before_tree) - persona_wrote), [])
    check("...and changed no file there", [p for p in before_tree
                                          if (labs / p).is_file()
                                          and after_tree.get(p) != before_tree[p]], [])
    check("...and the summary is as the persona wrote it",
          mine.read_text(), "Ada's view: we are planning the beds. Open: soil test.\n")
    server.threads.stop_all_tails()


def tree(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.stat().st_size)
            for p in root.rglob("*")}


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
