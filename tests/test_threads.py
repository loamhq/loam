#!/usr/bin/env python3
"""Drive thread creation, routing, snapshots, mentions and board updates through the broker with a fake harness."""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
for who in ("ada", "iris", "juno"):
    (TMP / "ai-labs" / who).mkdir(parents=True)
    (TMP / "ai-labs" / who / "agent.json").write_text(
        json.dumps({"name": who.capitalize()}))
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents" / "comms.md").write_text(
    "# From agents\n\n## Urgent\n\n- 2026-11-01 · **ada** · old line\n"
    "- [ ] expired item <!-- Ada · new · until 2026-11-01 -->\n"
    "- [ ] lasting item <!-- Iris · new · until 2099-01-01 -->\n")

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
# Supply fixture memory independently of the machine running the tests.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_threads as threads  # noqa: E402
OWNER, OWNER_NAME = cfg.lc.OWNER_ID, cfg.lc.OWNER_NAME

cfg.roster = threads.roster
cfg.roster_cards = lambda: [{"id": p} for p in threads.roster()]

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

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


class FakeSession:
    n = 0

    def __init__(self, agent, provider, sid):
        self.agent, self.provider = agent, provider
        self.id = sid
        self.subs = set()
        self.prompts = []

    def save(self):
        pass

    def watch(self, q):
        self.subs.add(q)

    def unwatch(self, q):
        self.subs.discard(q)

    async def send_message(self, text):
        self.prompts.append(text)
        reply = self.replies.pop(0) if self.replies else f"{self.agent} here"
        for ev in (
            {"type": "stream_event", "parent_tool_use_id": None,
             "event": {"type": "content_block_delta",
                       "delta": {"type": "text_delta", "text": "Looking"}}},
            {"type": "assistant", "parent_tool_use_id": None,
             "message": {"content": [{"type": "text", "text": "Looking."},
                                     {"type": "tool_use", "name": "Read",
                                      "input": {"file_path": "soul.md"}}]}},
            {"type": "assistant", "parent_tool_use_id": None,
             "message": {"content": [{"type": "text", "text": reply}]}},
            {"type": "result", "parent_tool_use_id": None},
        ):
            for q in list(self.subs):
                q.put_nowait(ev)
        return 1


class AskingSession(FakeSession):
    """A turn that asks a question and waits: the request is raised, and the
    result comes only when the test answers."""

    def __init__(self, *args):
        super().__init__(*args)
        self.pending = {}
        self.answered = asyncio.Event()

    def pending_events(self):
        return [dict(e) for e in self.pending.values()]

    async def send_message(self, text):
        event = {"type": "broker.permission_request", "session_id": self.id,
                 "request_id": "req-1", "tool_name": "AskUserQuestion",
                 "kind": "question", "questions": [{"id": "q", "question": "Which bed?"}],
                 "seq": 7}
        self.pending["req-1"] = event
        for q in list(self.subs):
            q.put_nowait(dict(event))

        async def later():
            await self.answered.wait()
            self.pending.clear()
            for q in list(self.subs):
                q.put_nowait({"type": "assistant", "parent_tool_use_id": None,
                              "message": {"content": [{"type": "text", "text": "ok"}]}})
                q.put_nowait({"type": "result", "parent_tool_use_id": None})
        asyncio.get_running_loop().create_task(later())
        return 1


class FakeBroker:
    def __init__(self):
        self.sessions = {}
        self.starts = []
        self.replies = {}
        self.kind = FakeSession

    async def start_session(self, agent, session_id=None, resume=False, provider=None):
        self.starts.append((agent, session_id, resume, provider))
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        FakeSession.n += 1
        s = self.kind(agent, provider, f"s{FakeSession.n}")
        s.replies = self.replies.setdefault(agent, [])
        self.sessions[s.id] = s
        return s


async def call(server, method, path, payload=None, params=None):
    w = FakeWriter()
    try:
        await server.route(method, path, params or {}, payload or {}, w)
    except (broker_mod.BrokerError, threads.ThreadError) as exc:
        return exc.status, {"error": exc.message}
    return w.json()


async def settle(server, tid):
    for _ in range(100):
        await asyncio.sleep(0.01)
        if server.threads.settled(tid):
            return


async def main():
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = FakeBroker()
    server.broker = fake
    server.threads = broker_mod.loam_thread_engine.ThreadEngine(fake)

    status, body = await call(server, "GET", "/v1/threads")
    check("the list answers, General pinned first", (status, body["threads"][0]["id"],
          body["threads"][0]["pinned"]), (200, "general", True))
    check("General has every persona", sorted(m["persona"] for m in
          body["threads"][0]["members"]), ["ada", "iris", "juno"])

    status, row = await call(server, "POST", "/v1/threads", {"persona": "ada"})
    tid = row["id"]
    check("a thread is created with its default", (status, row["default"]), (201, "ada"))

    status, _ = await call(server, "POST", f"/v1/threads/{tid}/messages",
                           {"text": "What should we plant this spring?"})
    await settle(server, tid)
    status, got = await call(server, "GET", f"/v1/threads/{tid}")
    msgs = got["messages"]
    check("untagged: the default answered", [m["author"] for m in msgs],
          [OWNER, "ada"])
    check("the title came from the first message", got["thread"]["title"],
          "What should we plant this spring?")
    check("What I did carries the step and the text before it",
          [d["label"] for d in msgs[-1]["did"]], ["Said: Looking.", "Read: soul.md"])
    check("the reply is the text after the last step", msgs[-1]["text"], "ada here")
    check("the board was made on first use", got["board"]["path"],
          "00_INBOX/boards/What should we plant this spring — board.md")

    status, _ = await call(server, "POST", f"/v1/threads/{tid}/messages",
                           {"text": "@Iris what do you think?"})
    await settle(server, tid)
    msgs = (await call(server, "GET", f"/v1/threads/{tid}"))[1]["messages"]
    check("a tag brings Iris in, with the join line, and Iris answers",
          [(m["author"], m["kind"]) for m in msgs[2:]],
          [(OWNER, "message"), ("system", "system"), ("iris", "message")])
    check("the join line", msgs[3]["text"], f"{OWNER_NAME} brought in Iris")

    status, _ = await call(server, "POST", f"/v1/threads/{tid}/messages",
                           {"text": "Ada, again"})
    await settle(server, tid)
    clay_starts = [s for s in fake.starts if s[0] == "ada"]
    check("same engine: Ada's session is resumed", clay_starts[-1][1:3], ("s1", True))
    resumed = fake.sessions["s1"].prompts[-1]
    check("a resumed turn is told only what is new",
          (f"{OWNER_NAME}:\nWhat should we plant" in resumed, "Ada, again" in resumed), (False, True))

    status, _ = await call(server, "PATCH", f"/v1/threads/{tid}",
                           {"engine": {"ada": "codex"}})
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "and now?"})
    await settle(server, tid)
    last_start = [s for s in fake.starts if s[0] == "ada"][-1]
    check("engine switched: a fresh session on codex", (last_start[1], last_start[3]),
          (None, "codex"))
    fresh = fake.sessions[f"s{FakeSession.n}"].prompts[-1]
    check("a fresh session is handed the record", "What should we plant" in fresh, True)
    msgs = (await call(server, "GET", f"/v1/threads/{tid}"))[1]["messages"]
    check("the reply records its engine", msgs[-1].get("engine"), "codex")

    # A chain of agent mentions stops at the cap.
    fake.replies["ada"][:] = ["@Iris over to you"] * 5
    fake.replies["iris"][:] = ["@Ada back to you"] * 5
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "@ada start"})
    await settle(server, tid)
    await settle(server, tid)
    msgs = (await call(server, "GET", f"/v1/threads/{tid}"))[1]["messages"]
    tail = [m["author"] for m in msgs if threads._num(m["id"]) > 0][-5:]
    check("the chain stops after the cap, with no line in the record",
          tail, [OWNER, "ada", "iris", "ada", "iris"])
    marks = (await call(server, "GET", f"/v1/threads/{tid}"))[1]["marks"]
    check("...and a mark on the thread saying it paused",
          [(m["persona"], m["text"].startswith("Paused after")) for m in marks], [(None, True)])

    meta, _, path = threads.read(tid)
    text = path.read_text()
    check("the record's frontmatter", text.split("---")[1].strip().splitlines()[:3],
          ["kind: thread", f"id: {tid}", 'title: "What should we plant this spring?"'])
    print(text[:900])

    # Open time on a long record.
    for i in range(2000):
        threads.append(tid, OWNER, f"line {i} " + "word " * 40)
    threads._CACHE.clear()
    t0 = time.perf_counter()
    status, got = await call(server, "GET", f"/v1/threads/{tid}")
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    await call(server, "GET", f"/v1/threads/{tid}")
    warm = time.perf_counter() - t0
    print(f"      open a {len(threads.read(tid)[1])}-message thread: "
          f"cold {cold*1000:.1f} ms, warm {warm*1000:.1f} ms")
    check("a long thread opens with one page", (len(got["messages"]), got["has_more"]),
          (50, True))
    check("and opens under a quarter second cold", cold < 0.25, True)

    # Board: PUT whole, swept by until.
    status, board = await call(server, "PUT", f"/v1/threads/{tid}/board",
                               {"markdown": "# b\n\n- [ ] keep\n"
                                            "- [ ] old <!-- Iris · new · until 2026-11-07 -->\n"
                                            "  - child of old\n"})
    check("PUT board counts the new item", board["board_new"], 1)
    out = subprocess.run([sys.executable, str(AGENTS / "bin" / "sweep-boards"),
                          "--today", "2026-11-08"], capture_output=True, text=True,
                         env=os.environ)
    after = (threads.VAULT / meta["board"]).read_text()
    check("the sweep removed the expired item and its child", after, "# b\n\n- [ ] keep\n")
    comms = (threads.VAULT / threads.GENERAL_BOARD).read_text()
    check("General's board: the expired item gone, the rest kept",
          ("expired item" in comms, "lasting item" in comms, "old line" in comms),
          (False, True, True))

    # An agent posts into General.
    out = subprocess.run([sys.executable, str(AGENTS / "bin" / "thread"), "post",
                          "general", "Woke: the garden note is filed.", "--bulletin"],
                         capture_output=True, text=True,
                         env={**os.environ, "LOAM_AGENT": "juno"})
    posted = json.loads(out.stdout)
    check("bin/thread post answers ok", posted["ok"], True)
    msgs = (await call(server, "GET", "/v1/threads/general"))[1]["messages"]
    check("the bulletin is in General", (msgs[-1]["author"], msgs[-1]["kind"]),
          ("juno", "bulletin"))

    # A new subscriber gets pending questions in its opening snapshot.
    fake.kind = AskingSession
    status, row = await call(server, "POST", "/v1/threads", {"persona": "iris"})
    asking = row["id"]
    await call(server, "POST", f"/v1/threads/{asking}/messages", {"text": "Which bed?"})
    for _ in range(100):
        await asyncio.sleep(0.01)
        if server.threads.pending(asking):
            break
    sid = server.threads.sessions[asking].id
    w = FakeWriter()
    stream = asyncio.create_task(server.thread_stream(asking, None, w))
    await asyncio.sleep(0.05)
    stream.cancel()
    text = b"".join(w.chunks).decode()
    snap = json.loads(text.split("data: ", 1)[1].split("\n\n", 1)[0])
    check("thread.snapshot opens the stream", snap["type"], "thread.snapshot")
    check("...with the pending request, persona and session_id on it",
          [(e["type"], e["request_id"], e["persona"], e["session_id"], e["thread"],
            e["kind"]) for e in snap["pending"]],
          [("broker.permission_request", "req-1", "iris", sid, asking, "question")])
    check("...the request's own fields kept", snap["pending"][0]["questions"],
          [{"id": "q", "question": "Which bed?"}])
    check("...beside the turn in flight", snap["turn"]["persona"], "iris")
    server.threads.sessions[asking].answered.set()
    await settle(server, asking)
    w = FakeWriter()
    stream = asyncio.create_task(server.thread_stream(asking, None, w))
    await asyncio.sleep(0.05)
    stream.cancel()
    text = b"".join(w.chunks).decode()
    snap = json.loads(text.split("data: ", 1)[1].split("\n\n", 1)[0])
    check("once answered and the turn done, the snapshot has none",
          (snap["pending"], snap["turn"]), ([], None))
    fake.kind = FakeSession

    # Only the pinned General board gets precedence. Its title alone confers none.
    def ordered(rows):
        saved = threads.index, threads.summary
        by_id = {r["id"]: r for r in rows}
        threads.index = lambda: dict.fromkeys(by_id)
        threads.summary = lambda tid, running=None: by_id[tid]
        try:
            return [r["id"] for r in threads.listing()]
        finally:
            threads.index, threads.summary = saved

    def row(tid, at, pinned, title=None):
        return {"id": tid, "title": title or tid, "pinned": pinned, "archived": False,
                "last": {"at": at}, "created": at}
    rows = [row("general", "2026-11-01T00:00:00Z", True, "General"),
            row("newest-pin", "2026-11-18T00:00:00Z", True),
            row("pin-b", "2026-11-10T00:00:00Z", True),
            row("pin-c", "2026-11-05T00:00:00Z", True),
            row("plain-general", "2026-11-17T00:00:00Z", False, "General"),
            row("plain", "2026-11-12T00:00:00Z", False)]
    check("General first among pinned, the rest by newest",
          ordered(rows), ["general", "newest-pin", "pin-b", "pin-c", "plain-general", "plain"])
    unpinned = [dict(r, pinned=False) if r["id"] == "general" else r for r in rows]
    check("an unpinned General takes no special place",
          ordered(unpinned), ["newest-pin", "pin-b", "pin-c", "plain-general", "plain", "general"])
    check("with General absent the order is unchanged",
          ordered(rows[1:]), ["newest-pin", "pin-b", "pin-c", "plain-general", "plain"])

    check("mentions match id or name, once each",
          threads.mentions("@Iris and @ada and @iris, not me@x"), ["iris", "ada"])


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
