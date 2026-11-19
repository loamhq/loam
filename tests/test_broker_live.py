#!/usr/bin/env python3
"""The live sessions the app lists and ends (2026-11-10), through the
broker's routes with a fake broker and a fake harness.

  1. **`GET /v1/sessions/live`** answers every live session, thread member
     and classic chat alike, with its kind, thread, state, times, memory and
     what ending it loses; and the box's totals beside them.
  2. **`POST /v1/sessions/end-idle`** ends the idle ones and never one that
     is working, asking, waiting on background work, or carrying a thread's
     turn, or that was active in the last seconds.
  3. **`DELETE /v1/sessions/:id` mid-turn on a thread member** stops the turn
     with one quiet system line ("Sam ended Iris's turn"), keeps what it
     had said, writes no "(no reply)", and the next mention resumes the same
     session by id. The thread survives.
  4. **The stream** opens with `sessions.live` and sends again after a
     record change.
  5. **lib/loam_live.py** reads a process tree's RSS, a unit slice and the
     admission count from fake roots.

Run: python3 tests/test_broker_live.py
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
for who in ("ada", "iris"):
    (TMP / "ai-labs" / who).mkdir(parents=True)
    (TMP / "ai-labs" / who / "agent.json").write_text(json.dumps({"name": who.capitalize()}))
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents" / "comms.md").write_text("# From agents\n")

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_live  # noqa: E402
import loam_threads as threads  # noqa: E402
OWNER, OWNER_NAME = cfg.lc.OWNER_ID, cfg.lc.OWNER_NAME

cfg.roster = threads.roster
cfg.roster_cards = lambda: [{"id": p} for p in threads.roster()]

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)
engine_mod = broker_mod.loam_thread_engine

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

    def events(self):
        text = b"".join(self.chunks).decode()
        return [json.loads(line[6:]) for line in text.splitlines()
                if line.startswith("data: ")]


class FakeSession:
    n = 0

    def __init__(self, agent, provider, sid, busy=None, title=None):
        self.agent, self.provider, self.id = agent, provider or "claude", sid
        self.model = None
        self.title = title
        self._busy = busy
        self.subs = set()
        self.prompts = []
        self.idle_timeout_s = None
        self.answer = True        # answer each message, or hang mid-turn
        self.stopped = 0
        self.live = True
        self.last_activity = time.time() - 600
        self.proc = None

    def busy(self):
        return self._busy

    def save(self):
        pass

    def watch(self, q):
        self.subs.add(q)

    def unwatch(self, q):
        self.subs.discard(q)

    def meta(self):
        return {"session_id": self.id, "agent": self.agent}

    async def send_message(self, text):
        self.prompts.append(text)
        self._busy = "working"
        self.last_activity = time.time()

        def put(ev):
            for q in list(self.subs):
                q.put_nowait(ev)

        async def go():
            text = f"{self.agent} on it"
            put({"type": "stream_event", "event": {"type": "content_block_delta",
                                                  "delta": {"type": "text_delta", "text": text}}})
            put({"type": "assistant", "parent_tool_use_id": None,
                 "message": {"content": [{"type": "text", "text": text}]}})
            if self.answer:
                put({"type": "result", "parent_tool_use_id": None})
                self._busy = None
        asyncio.get_running_loop().create_task(go())
        return 1

    async def stop(self):
        self.stopped += 1
        self.live = False
        self._busy = None
        for q in list(self.subs):
            q.put_nowait({"type": "broker.exit", "session_id": self.id})
            q.put_nowait(None)


class FakeBroker:
    def __init__(self):
        self.sessions = {}
        self.starts = []
        self.record_subscribers = set()
        self.hang_next = False

    def live(self):
        return [s for s in self.sessions.values() if s.live]

    def seats_taken(self):
        return len(self.live())

    def room(self):
        return {"ok": True, "available_mb": 20_000, "cgroup_room_mb": 26_000,
                "reserve_mb": 3_792, "need_mb": 450, "starting": 0}

    async def start_session(self, agent, session_id=None, resume=False, provider=None,
                            **kw):
        self.starts.append((agent, session_id, resume))
        if session_id and session_id in self.sessions:
            s = self.sessions[session_id]
            s.live = True
            return s
        FakeSession.n += 1
        s = FakeSession(agent, provider, f"t{FakeSession.n}")
        s.answer = not self.hang_next
        self.sessions[s.id] = s
        return s


async def call(server, method, path, payload=None, params=None):
    w = FakeWriter()
    try:
        await server.route(method, path, params or {}, payload or {}, w)
    except (broker_mod.BrokerError, threads.ThreadError) as exc:
        return exc.status, {"error": exc.message}
    return w.json()


async def settle(server, tid, rounds=300):
    for _ in range(rounds):
        await asyncio.sleep(0.01)
        if server.threads.settled(tid):
            return


def pure_checks():
    root = TMP / "proc"
    for pid, ppid, kb in ((100, 1, 204800), (101, 100, 102400), (102, 101, 51200),
                          (200, 1, 999999)):
        d = root / str(pid)
        d.mkdir(parents=True)
        (d / "stat").write_text(f"{pid} (node (mcp) x) S {ppid} 0 0" + " 0" * 30 + "\n")
        (d / "status").write_text(f"Name:\tx\nVmRSS:\t{kb} kB\n")
    kids = loam_live.children(root)
    check("a process tree's RSS: the CLI and its MCP servers, in MB",
          loam_live.tree_rss_mb(100, kids, root), 350)
    check("no process, no memory", loam_live.tree_rss_mb(None, kids, root), None)
    sl = TMP / "slice"
    for name, mb in (("loam-engineer-20261110-1.service", 900), ("loam-wake-iris.service", 300),
                     ("loam-cron-ada-infra-watch.service", 120), ("loam-rc-ada.service", 380),
                     ("loam-session-broker.service", 5000), ("dbus.socket", 1)):
        (sl / name).mkdir(parents=True)
        (sl / name / "memory.current").write_text(str(mb * 1024 * 1024))
    u = loam_live.units(sl)
    check("units by kind, the broker and a leftover RC unit not among them",
          {k: (v["count"], v["mb"]) for k, v in u.items()},
          {"engineer_jobs": (1, 900), "wakes": (1, 300), "crons": (1, 120)})
    # 32 GB box: MemTotal ~31,600, reserve 12% = 3,792, a start 450.
    check("admission at 32 GB with 20 GB available: (20000-3792)//450",
          loam_live.can_start(20_000, 3_792, 450, 0, None, 100), 36)
    check("...the cgroup's room bounds it", loam_live.can_start(20_000, 3_792, 450, 0, 900, 100), 2)
    check("...and the backstop's seats", loam_live.can_start(20_000, 3_792, 450, 0, None, 3), 3)
    check("...unreadable memory says so", loam_live.can_start(None, 3_792, 450, 0, None, 3), None)
    real = loam_live.started_ts(os.getpid())
    check("this process's real start time, from /proc", real is not None and
          0 <= time.time() - real < 600, True)
    check("this process's real RSS, from /proc",
          (loam_live.tree_rss_mb(os.getpid(), loam_live.children()) or 0) > 5, True)
    check("the state words", [loam_live.state_of(b) for b in ("working", "needs_input", "waiting", None)],
          ["working", "asking", "background", "idle"])


async def main():
    pure_checks()
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = FakeBroker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)

    # A thread with Iris in it, who answers once and goes idle.
    status, row = await call(server, "POST", "/v1/threads", {"persona": "iris",
                                                              "title": "Garden plan"})
    tid = row["id"]
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "hello"})
    await settle(server, tid)
    iris = next(s for s in fake.sessions.values() if s.agent == "iris")
    # Classic chats: one idle, one asking, one working, one with background work.
    for sid, busy, title in (("c-idle", None, "Idle chat"), ("c-ask", "needs_input", "Asking chat"),
                             ("c-work", "working", "Working chat"), ("c-bg", "waiting", "Bg chat")):
        fake.sessions[sid] = FakeSession("ada", "codex" if sid == "c-bg" else None, sid,
                                         busy=busy, title=title)

    # -- 1. the view ---------------------------------------------------------
    status, view = await call(server, "GET", "/v1/sessions/live")
    rows = {r["id"]: r for r in view["sessions"]}
    check("GET /v1/sessions/live answers 200 with every live session",
          (status, sorted(rows)), (200, sorted(["c-idle", "c-ask", "c-work", "c-bg", iris.id])))
    r = rows[iris.id]
    check("a thread member: kind, thread id and title, no classic title",
          (r["kind"], r["thread_id"], r["thread_title"], r["title"], r["agent"], r["engine"]),
          ("thread", tid, "Garden plan", None, "iris", "claude-code"))
    check("...idle, with idle_since and last_active, and ending it loses nothing",
          (r["state"], r["idle_since"] == r["last_active"] is not None, r["ending"]),
          ("idle", True, {"loses_work": False, "text": "resumes later exactly where it was"}))
    check("a classic chat: kind and its title",
          (rows["c-ask"]["kind"], rows["c-ask"]["title"], rows["c-ask"]["thread_id"]),
          ("classic", "Asking chat", None))
    check("states: asking, working, background",
          [rows[k]["state"] for k in ("c-ask", "c-work", "c-bg")], ["asking", "working", "background"])
    check("...and what ending each loses", [rows[k]["ending"]["loses_work"] for k in ("c-ask", "c-work", "c-bg")],
          [True, True, True])
    check("...a busy one has no idle_since", rows["c-work"]["idle_since"], None)
    check("each row has started_at, rss_mb and model (None with no process)",
          all(k in rows["c-idle"] for k in ("started_at", "rss_mb", "model", "engine")), True)
    box = view["box"]
    check("the box: reserve, start size, how many more could start",
          (box["reserve_mb"], box["start_mb"], box["can_start"]),
          (3_792, 450, min(36, cfg.MAX_SESSIONS - 5)))
    check("...RAM used/total, the sessions' count, and the units by kind",
          (isinstance(box["ram_total_mb"], int), box["sessions"]["live"], sorted(box["units"])),
          (True, 5, ["crons", "engineer_jobs", "wakes"]))
    check("no thread turn in flight: `turns` is empty (2026-11-17)", view["turns"], [])

    # -- 2. end-idle ---------------------------------------------------------
    iris.last_activity = time.time() - 600     # its answer was ten minutes ago
    fake.sessions["c-fresh"] = FakeSession("ada", None, "c-fresh", title="Just answered")
    fake.sessions["c-fresh"].last_activity = time.time()
    status, out = await call(server, "POST", "/v1/sessions/end-idle")
    check("end-idle ends the idle ones only",
          (status, sorted(e["id"] for e in out["ended"])), (200, sorted(["c-idle", iris.id])))
    check("...and keeps working, asking, background and the just-active",
          sorted((k["id"], k["why"]) for k in out["kept"]),
          sorted([("c-ask", "asking"), ("c-work", "working"), ("c-bg", "background"),
                  ("c-fresh", "just active")]))
    check("...no busy session was stopped",
          [fake.sessions[k].stopped for k in ("c-ask", "c-work", "c-bg", "c-fresh")], [0, 0, 0, 0])
    got = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    check("ending an idle member writes nothing into the thread",
          [m["author"] for m in got["messages"]], [OWNER, "iris"])

    # -- 3. DELETE mid-turn on a thread member ------------------------------
    iris.answer = False
    events = server.threads.listen(tid)
    try:
        await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "@iris one more"})
        # End the turn after its words reached the thread, even if the
        # harness reported "working" before producing any text.
        async with asyncio.timeout(5):
            while (await events.get()).get("type") != "message.delta":
                pass
    finally:
        server.threads.unlisten(tid, events)
    check("the turn is in flight on Iris's resumed session",
          (server.threads.turn_of(iris.id), fake.starts[-1]), (tid, ("iris", iris.id, True)))
    status, view = await call(server, "GET", "/v1/sessions/live")
    check("...and the view shows it working, ending a turn in progress",
          [(r["state"], r["ending"]["text"]) for r in view["sessions"] if r["id"] == iris.id],
          [("working", "ends a turn in progress")])
    check("...and names the turn in flight, for a planned restart",
          view["turns"], [{"thread_id": tid, "persona": "iris", "queued": False}])
    check("...which that turn holds back (loam_live.restart_blockers)",
          loam_live.restart_blockers(view["sessions"], view["turns"])[-1:],
          [f"iris's turn in {tid} in flight"])
    status, out = await call(server, "POST", "/v1/sessions/end-idle")
    check("end-idle leaves the turn alone", iris.id in [k["id"] for k in out["kept"]], True)
    status, rec = await call(server, "DELETE", f"/v1/sessions/{iris.id}")
    await settle(server, tid)
    check("DELETE answers 200 and names the thread whose turn it ended",
          (status, rec.get("ended_turn_in")), (200, tid))
    got = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    tail = [(m["author"], m["kind"], m["text"]) for m in got["messages"]][-3:]
    check("one quiet system line; what it said is kept; no '(no reply'",
          tail, [(OWNER, "message", "@iris one more"), ("iris", "message", "iris on it"),
                 ("system", "system", f"{OWNER_NAME} ended Iris's turn")])
    check("the thread survives, Iris still a member",
          [m["persona"] for m in got["thread"]["members"]], ["iris"])
    iris.answer = True
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "@iris back?"})
    await settle(server, tid)
    got = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    check("the next mention resumes the same session by id and Iris answers",
          (fake.starts[-1], got["messages"][-1]["author"]), (("iris", iris.id, True), "iris"))
    check("...and its prompt carries what was new since",
          "back?" in iris.prompts[-1], True)

    # -- 4. the stream -------------------------------------------------------
    broker_mod.LIVE_DEBOUNCE_S = 0
    w = FakeWriter()
    task = asyncio.get_running_loop().create_task(server.live_stream(w))
    await asyncio.sleep(0.05)
    fake.sessions["c-new"] = FakeSession("iris", None, "c-new", title="New one")
    for q in list(fake.record_subscribers):
        q.put_nowait({"session_id": "c-new"})
    await asyncio.sleep(0.05)
    for q in list(fake.record_subscribers):
        q.put_nowait(None)
    await asyncio.wait_for(task, 2)
    evs = w.events()
    check("the stream opens with sessions.live and sends again on a change",
          ([e["type"] for e in evs], "c-new" in [r["id"] for r in evs[-1]["sessions"]]),
          (["sessions.live", "sessions.live"], True))
    check("...and lets go of its subscription", fake.record_subscribers, set())


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
