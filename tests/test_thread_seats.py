#!/usr/bin/env python3
"""Check default routing, local time, idle resumption and visible status while a member waits for capacity."""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
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


class FakeSession:
    n = 0

    def __init__(self, agent, provider, sid):
        self.agent, self.provider, self.id = agent, provider, sid
        self.subs = set()
        self.prompts = []
        self.idle_timeout_s = None
        self.gate = None

    def save(self):
        pass

    def watch(self, q):
        self.subs.add(q)

    def unwatch(self, q):
        self.subs.discard(q)

    async def send_message(self, text):
        self.prompts.append(text)

        async def answer():
            if self.gate is not None:
                await self.gate.wait()
            for ev in ({"type": "assistant", "parent_tool_use_id": None,
                        "message": {"content": [{"type": "text",
                                                 "text": f"{self.agent} here"}]}},
                       {"type": "result", "parent_tool_use_id": None}):
                for q in list(self.subs):
                    q.put_nowait(ev)
        asyncio.get_running_loop().create_task(answer())
        return 1


class Full(Exception):
    status = 429

    def __init__(self, busy):
        super().__init__("4 sessions are already running and the cap is 4 ... "
                         "End one with DELETE /v1/sessions/<id>")
        self.detail = {"reason": "cap", "cap": 4, "live_count": 4, "busy": busy}


class FakeBroker:
    def __init__(self):
        self.sessions = {}
        self.starts = []
        self.full_for = 0          # how many starts to refuse at the cap
        self.busy = []

    async def start_session(self, agent, session_id=None, resume=False, provider=None):
        self.starts.append((agent, session_id, resume))
        if self.full_for:
            self.full_for -= 1
            raise Full(self.busy)
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        FakeSession.n += 1
        s = FakeSession(agent, provider, f"s{FakeSession.n}")
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


async def say(server, tid, text):
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": text})
    await settle(server, tid)
    return (await call(server, "GET", f"/v1/threads/{tid}"))[1]


async def main():
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = FakeBroker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    engine_mod.CAP_RETRY_S = 0.01

    # -- 1. the default ----------------------------------------------------
    status, row = await call(server, "POST", "/v1/threads", {"persona": "ada"})
    tid = row["id"]
    got = await say(server, tid, "Hey, started a new thread")
    got = await say(server, tid, "@iris adding you to the thread for fun")
    check("a tag brings Iris in and Iris answers",
          [m["author"] for m in got["messages"]][-3:], [OWNER, "system", "iris"])
    check("...and the default is still Ada", got["thread"]["default"], "ada")
    got = await say(server, tid, "Can you please end all active sessions")
    check("an untagged message after Iris spoke goes to the default, Ada",
          got["messages"][-1]["author"], "ada")

    status, summary = await call(server, "PATCH", f"/v1/threads/{tid}",
                                 {"default": "iris"})
    got = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    check("a PATCH moves the default, and the thread says so",
          (summary["default"], got["messages"][-1]["kind"], got["messages"][-1]["text"]),
          ("iris", "system", f"{OWNER_NAME} made Iris the default"))
    got = await say(server, tid, "and now?")
    check("...and the next untagged message goes to the new default",
          got["messages"][-1]["author"], "iris")
    status, _ = await call(server, "PATCH", f"/v1/threads/{tid}", {"seen": True})
    got = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    check("a PATCH that leaves the default alone says nothing about it",
          got["messages"][-1]["author"], "iris")

    status, before = await call(server, "PATCH", f"/v1/threads/{tid}",
                                {"add": ["cleo"]})
    got = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    check("a PATCH add writes its join line (the members list was shared "
          "with the cache, so `before` already had Cleo)",
          got["messages"][-1]["text"], f"{OWNER_NAME} brought in Cleo")

    # -- 2. the clock --------------------------------------------------------
    monday = datetime(2026, 11, 9, 17, 25, 51, tzinfo=timezone.utc)
    check("the clock line: weekday, date and time in Los Angeles",
          threads.clock_line(monday),
          "It is now Monday, 2026-11-09, 9:25 AM PST (America/Los_Angeles). "
          "Message times below are in the same zone.")
    check("a stamp in the transcript is local, with its weekday",
          threads.local_stamp("2026-11-09T17:25:51Z"), "Mon 2026-11-09 09:25 PST")
    check("...and in summer, PDT",
          threads.local_stamp("2026-07-04T19:00:00Z"), "Sat 2026-07-04 12:00 PDT")
    ada = [s for s in fake.sessions.values() if s.agent == "ada"][0]
    fresh, resumed = ada.prompts[0], ada.prompts[-1]
    today = datetime.now(timezone.utc).astimezone(threads.PACIFIC)
    check("a fresh turn's prompt carries today's weekday and date",
          f"It is now {today.strftime('%A')}, {today.strftime('%Y-%m-%d')}" in fresh,
          True)
    check("...and so does a resumed one", "It is now " in resumed and
          "America/Los_Angeles" in resumed, True)
    check("...with the messages stamped in the same zone, not UTC",
          (f"Z] {OWNER_NAME}" in fresh, f" PST] {OWNER_NAME}" in fresh or f" PDT] {OWNER_NAME}" in fresh),
          (False, True))

    # -- 3. the idle stop and the resume ------------------------------------
    check("a member's session is marked for the thread idle stop",
          ada.idle_timeout_s, cfg.THREAD_IDLE_S)
    clay_starts = [s for s in fake.starts if s[0] == "ada"]
    check("...and its next turn resumes it by id",
          (clay_starts[-1][1], clay_starts[-1][2]), (ada.id, True))

    # -- 4. the wait at a busy cap ------------------------------------------
    fake.busy = [{"agent": "ada", "status": "working"},
                 {"agent": "iris", "status": "working"},
                 {"agent": "cleo", "status": "needs_input"},
                 {"agent": "ada", "status": "working"}]
    fake.full_for = 3
    stream = server.threads.listen(tid)
    got = await say(server, tid, "@cleo how are you?")
    texts = [(m["author"], m["text"]) for m in got["messages"]][-2:]
    check("a turn at a busy cap answers, with no waiting line in the record",
          texts, [(OWNER, "@cleo how are you?"), ("cleo", "cleo here")])
    waits = []
    while not stream.empty():
        e = stream.get_nowait()
        if e.get("type") == "turn.waiting":
            waits.append(e["text"])
    check("...the wait is state on the turn, said once",
          waits, ["Cleo is waiting for a free seat: three others are "
                  "mid-turn and Cleo is waiting on you. I'll post as soon "
                  "as one frees up."])

    engine_mod.CAP_WAIT_S = 0.05
    fake.full_for = 10 ** 6
    fake.busy = [{"agent": "iris", "status": "working"}] * 4
    got = await say(server, tid, "@cleo again?")
    check("past the wait nothing is added to the record but his message",
          got["messages"][-1]["text"], "@cleo again?")
    failed = next((m["text"] for m in server.threads.marks_for(tid)
                   if m["persona"] == "cleo"), "")
    check("past the wait the turn fails in a plain sentence, a mark on it",
          failed.startswith("Cleo couldn't get a seat: the box stayed full"),
          True)
    check("...naming who held the seats, with no route and no DELETE",
          ("four others are mid-turn" in failed, "DELETE" in failed,
           "/v1/" in failed), (True, False, False))
    fake.full_for = 0
    engine_mod.CAP_WAIT_S = 300

    # -- 5. who is in the room ----------------------------------------------
    status, got = await call(server, "GET", f"/v1/threads/{tid}")
    members = {m["persona"]: m for m in got["thread"]["members"]}
    spoke = {a: m["at"] for m in got["messages"] if m["kind"] == "message"
             for a in [m["author"]]}
    check("each member says when it last spoke, from the record",
          {p: m["last_spoke_at"] for p, m in members.items()},
          {p: spoke.get(p) for p in members})
    check("...and nobody is running between turns",
          {p: m["running"] for p, m in members.items()},
          {p: False for p in members})

    gate = asyncio.Event()
    cleo = [s for s in fake.sessions.values() if s.agent == "cleo"][0]
    cleo.gate = gate
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "@cleo hold on"})
    for _ in range(100):
        await asyncio.sleep(0.01)
        if server.threads.active.get(tid):
            break
    status, got = await call(server, "GET", f"/v1/threads/{tid}")
    check("mid-turn, the member whose turn it is says `running`",
          [m["persona"] for m in got["thread"]["members"] if m["running"]], ["cleo"])
    status, listing = await call(server, "GET", "/v1/threads")
    row = [t for t in listing["threads"] if t["id"] == tid][0]
    check("...on the list's row too",
          [m["persona"] for m in row["members"] if m["running"]], ["cleo"])
    gate.set()
    await settle(server, tid)

    # General: a member who never spoke says so.
    status, listing = await call(server, "GET", "/v1/threads")
    general = [t for t in listing["threads"] if t["id"] == "general"][0]
    check("a member who never spoke has a null last_spoke_at",
          {m["persona"]: m["last_spoke_at"] for m in general["members"]},
          {"cleo": None, "ada": None, "iris": None})


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
