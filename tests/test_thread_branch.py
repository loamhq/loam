#!/usr/bin/env python3
"""Check branch context boundaries, preview, draft summary and the first message in the new thread."""

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



def at(minutes):
    return f"2026-11-11T{10 + minutes // 60:02d}:{minutes % 60:02d}:00Z"


def msg(n, minutes, author=OWNER, kind="message"):
    return {"id": f"m{n}", "author": author, "at": at(minutes), "kind": kind,
            "text": f"message {n}", "did": []}


def ids(window):
    return [m["id"] for m in window]


def heuristic():
    # Ten messages a minute apart, a 45-minute pause, then five more.
    msgs = [msg(i, i) for i in range(1, 11)] + [msg(10 + i, 55 + i) for i in range(1, 6)]
    check("window: back to the last pause", ids(threads.branch_window(msgs, "m15")),
          ["m11", "m12", "m13", "m14", "m15"])
    check("window: at least three, across a pause",
          ids(threads.branch_window(msgs, "m12")), ["m10", "m11", "m12"])
    long = [msg(i, i) for i in range(1, 31)]
    check("window: at most twelve with no pause", ids(threads.branch_window(long, "m30")),
          [f"m{i}" for i in range(19, 31)])
    check("window: ends at the chosen message, not the last",
          ids(threads.branch_window(long, "m5")), ["m1", "m2", "m3", "m4", "m5"])
    check("window: a thread of two gives two", ids(threads.branch_window(long[:2], "m2")),
          ["m1", "m2"])
    gap = [msg(1, 0), msg(2, 40), msg(3, 80), msg(4, 120), msg(5, 121)]
    check("window: pauses everywhere still give three",
          ids(threads.branch_window(gap, "m5")), ["m3", "m4", "m5"])
    sys_mix = [msg(1, 1), msg(2, 2, "system", "system"), msg(3, 3), msg(4, 4)]
    check("window: system lines are left out",
          ids(threads.branch_window(sys_mix, "m4")), ["m1", "m3", "m4"])
    exactly = [msg(1, 0), msg(2, 30), msg(3, 60), msg(4, 90)]
    check("window: a gap of exactly 30 minutes is not a pause",
          ids(threads.branch_window(exactly, "m4")), ["m1", "m2", "m3", "m4"])
    for bad, status in (("m99", 404), ("nope", 400), ("m2", 400)):
        try:
            threads.branch_window(sys_mix, bad)
            got = None
        except threads.ThreadError as exc:
            got = exc.status
        check(f"window: {bad!r} refused", got, status)


async def main():
    heuristic()
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = Broker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    tid = (await call(server, "POST", "/v1/threads",
                      {"persona": "iris", "title": "Garden plans"}))[1]["id"]
    await call(server, "PATCH", f"/v1/threads/{tid}", {"add": ["ada"]})
    # An early exchange the branch must not carry, then the stretch it must.
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "EARLY tulips?"})
    await settle(server, tid)
    for i in range(14):
        iris = next(s for s in fake.sessions.values() if s.agent == "iris")
        iris.script = [text(f"reply {i}" + (" -- the watering cron crashes on line 40"
                                           if i == 13 else "")), result(1)]
        await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": f"step {i}"})
        await settle(server, tid)
    msgs = threads.read(tid)[1]
    bug = msgs[-1]
    check("(the source ends with Iris naming the bug)",
          (bug["author"], "line 40" in bug["text"]), ("iris", True))
    # A later message, so the branch point is not the last one.
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "LATER aside"})
    await settle(server, tid)
    await call(server, "GET", f"/v1/threads/{tid}")       # the owner reads it all
    before = threads.record_path(tid).read_text("utf-8")

    # -- the sheet's read --------------------------------------------------------
    status, got = await call(server, "GET", f"/v1/threads/{tid}/branch",
                             params={"message": bug["id"]})
    check("GET branch: 200, ends at the message, twelve at most",
          (status, got["window"][-1]["id"], len(got["window"])), (200, bug["id"], 12))
    check("...members first, the default first of all",
          got["people"][:2], ["iris", "ada"])
    check("...the default preselected", got["persona"], "iris")
    s = got["summarizer"]
    check("...the summarizer's prompt: the owner's voice, JSON, the excerpt",
          (f"{OWNER_NAME}'s own voice" in s["system_instruction"],
           '{"title": "...", "draft": "..."}' in s["system_instruction"],
           "line 40" in s["input"], "EARLY" in s["input"], "Garden plans" in s["input"]),
          (True, True, True, False, True))
    check("...and a fallback with the message in it",
          ("line 40" in got["fallback"]["draft"], bool(got["fallback"]["title"])), (True, True))
    status, got = await call(server, "GET", f"/v1/threads/{tid}/branch",
                             params={"message": bug["id"], "persona": "ada"})
    check("...a persona chosen is the prompt's", "with Ada" in got["summarizer"]["input"], True)
    status, _ = await call(server, "GET", f"/v1/threads/{tid}/branch",
                           params={"message": "m999"})
    check("GET branch at a message the thread lacks: 404", status, 404)
    check("the read wrote nothing",
          threads.record_path(tid).read_text("utf-8") == before, True)

    # -- Start thread ----------------------------------------------------------
    draft = ("The watering cron crashes on line 40.\nIris found it while we planned "
             "the beds.\n\nWhat I want to know:")
    status, made = await call(server, "POST", f"/v1/threads/{tid}/branch",
                              {"message": bug["id"], "persona": "ada",
                               "title": "Watering cron crash", "draft": draft})
    new = made["thread"]
    nid = new["id"]
    check("POST branch: 201, Ada the default and only member, the title",
          (status, new["default"], [m["persona"] for m in new["members"]], new["title"]),
          (201, "ada", ["ada"], "Watering cron crash"))
    check("...its draft waits in the summary, unsent", new["draft"], draft)
    check("...and it says where it came from",
          new["branched_from"], {"thread": tid, "message": bug["id"]})
    nmeta, nmsgs, npath = threads.read(nid)
    check("...the record keeps the source, the message and the window",
          (nmeta["branched_from"], nmeta["branched_message"], len(nmeta["branched_window"]),
           nmeta["branched_window"][-1]), (tid, bug["id"], 12, bug["id"]))
    check("the new thread's first line links back",
          (nmsgs[0]["kind"], nmsgs[0]["text"], nmsgs[0].get("link")),
          ("system", "Branched from Garden plans",
           {"thread": tid, "message": bug["id"], "title": "Garden plans"}))
    check("...and it is the only message: nothing was sent", len(nmsgs), 1)
    smsgs = threads.read(tid)[1]
    at_bug = next(i for i, m in enumerate(smsgs) if m["id"] == bug["id"])
    line = smsgs[at_bug + 1]
    check("the source's line sits right under the chosen message",
          (line["kind"], line["text"], line.get("link"), line.get("after")),
          ("system", "Branched into Watering cron crash",
           {"thread": nid, "title": "Watering cron crash"}, bug["id"]))
    check("...with the next free id", line["id"], made["into"]["id"])
    check("...and the later message still after it", smsgs[at_bug + 2]["text"], "LATER aside")
    after = threads.record_path(tid).read_text("utf-8")
    check("...no other byte of the source changed",
          after.replace("\n" + threads.render_message(
              {**line, "text": line["text"]}).strip("\n") + "\n", "", 1) == before, True)
    check("the source stays read", threads.summary(tid)["unread"], 0)
    # A second branch from the same message lands under the first line.
    status, made2 = await call(server, "POST", f"/v1/threads/{tid}/branch",
                               {"message": bug["id"]})
    smsgs = threads.read(tid)[1]
    check("a second branch: its line under the first, the default by default",
          ([m["id"] for m in smsgs[at_bug + 1:at_bug + 3]], made2["thread"]["default"]),
          ([line["id"], made2["into"]["id"]], "iris"))
    status, _ = await call(server, "POST", f"/v1/threads/{tid}/branch",
                           {"message": bug["id"], "persona": "nobody"})
    check("a branch with a persona nobody is: 404", status, 404)

    # -- the persona's first turn ----------------------------------------------
    status, _ = await call(server, "POST", f"/v1/threads/{nid}/messages",
                           {"text": draft + " why line 40?"})
    await settle(server, nid)
    ada = next(s for s in fake.sessions.values() if s.agent == "ada")
    prompt = ada.prompts[0]
    check("Ada's first turn: where it came from and the source file",
          (f"branched from the thread \"Garden plans\" (id {tid})" in prompt,
           str(threads.record_path(tid)) in prompt), (True, True))
    check("...the window's messages, the bug among them", "line 40" in prompt, True)
    check("...and not the source's earlier messages",
          ("EARLY" in prompt, "LATER aside" in prompt, "step 0\n" in prompt), (False, False, False))
    check("...then the branch's own messages", "why line 40?" in prompt, True)
    check("the draft is spent once the owner writes", threads.summary(nid)["draft"], None)
    threads.set_draft(nid, "again")
    await call(server, "PATCH", f"/v1/threads/{nid}", {"draft": None})
    check("PATCH draft null clears it", threads.summary(nid)["draft"], None)
    plain = threads.summary(tid)
    check("a thread that is not a branch says so",
          (plain["branched_from"], plain["draft"]), (None, None))

    # -- bin/thread branch, as a persona -----------------------------------------
    env = {**os.environ, "LOAM_AGENT": "iris"}
    out = subprocess.run([sys.executable, str(AGENTS / "bin" / "thread"), "branch",
                          tid, bug["id"], "--persona", "ada",
                          "--text", "Ada, the cron bug:"],
                         capture_output=True, text=True, env=env)
    got = json.loads(out.stdout)
    check("bin/thread branch: ok, with Ada, titled by the draft",
          (got["ok"], got["persona"], got["title"]), (True, "ada", "Ada, the cron bug:"))
    pmsgs = threads.read(got["thread"])[1]
    check("...its lines name the persona who branched",
          (pmsgs[0]["text"], threads.find(threads.read(tid)[1], got["lines"]["into"])["text"]),
          ("Iris branched this from Garden plans", "Iris branched into Ada, the cron bug:"))
    check("...and the draft waits for the owner", threads.summary(got["thread"])["draft"],
          "Ada, the cron bug:")
    out = subprocess.run([sys.executable, str(AGENTS / "bin" / "thread"), "branch",
                          tid, bug["id"]], capture_output=True, text=True, env=env)
    got = json.loads(out.stdout)
    check("bin/thread branch with no text: the fallback opening",
          (got["ok"], "line 40" in (threads.summary(got["thread"])["draft"] or "")), (True, True))
    out = subprocess.run([sys.executable, str(AGENTS / "bin" / "thread"), "branch",
                          tid, "m999"], capture_output=True, text=True, env=env)
    check("bin/thread branch at a message the thread lacks is refused",
          json.loads(out.stdout)["ok"], False)

    server.threads.stop_all_tails()


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
