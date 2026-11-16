#!/usr/bin/env python3
"""A message can reply to one earlier message (2026-11-11).

Driven through the broker's routes with a fake harness:

  - POST .../messages with `reply_to` stores it: the record's `loam` comment,
    the one-line quote in the readable markdown, the parsed `text` without it
  - served on the read, on `message.done` and in the POST's reply
  - refused for another thread's id (404) and a malformed one (400), and
    nothing is written or started
  - the persona's prompt shows the referenced message, who, when, whole,
    above the owner's words -- when it is far outside the new window, and when
    it is inside it; a long one is cut with where the rest is
  - a persona's `↩ m2` opening becomes `reply_to`, marker gone, also from its
    first text segment; an id the thread lacks stays as text
  - the full record's entry carries it; `bin/thread post --reply-to`

Run: python3 tests/test_thread_reply_to.py
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
OWNER_NAME = cfg.lc.OWNER_NAME

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


async def main():
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = Broker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    tid = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    other = (await call(server, "POST", "/v1/threads", {"persona": "iris"}))[1]["id"]

    # m1 the owner, m2 Ada's long-ago answer, then 60 more exchanges.
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "what's the plan?"})
    await settle(server, tid)
    ada = next(s for s in fake.sessions.values() if s.agent == "ada")
    ada.script = [text("The plan: move the garden beds to the south wall."), result(1)]
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "and?"})
    await settle(server, tid)
    plan = next(m for m in threads.read(tid)[1]
                if m["author"] == "ada" and "south wall" in m["text"])
    for i in range(30):
        ada.script = [text(f"filler {i}"), result(1)]
        await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": f"more {i}"})
        await settle(server, tid)
    await call(server, "POST", f"/v1/threads/{other}/messages", {"text": "hi iris"})
    await settle(server, other)
    iris_msg = threads.read(other)[1][-1]["id"]

    # -- stored and served ------------------------------------------------------
    stream_w = FakeWriter()
    stream = asyncio.create_task(server.thread_stream(tid, None, stream_w))
    await asyncio.sleep(0.05)
    ada.script = [text("Yes -- the south wall gets the most sun."), result(1)]
    before = len(ada.prompts)
    status, got = await call(server, "POST", f"/v1/threads/{tid}/messages",
                             {"text": "Why that wall?", "reply_to": plan["id"]})
    await settle(server, tid)
    await asyncio.sleep(0.05)
    stream.cancel()
    mine = got["message"]
    check("POST with reply_to: 200, and the reply carries it",
          (status, mine["reply_to"], mine["text"]), (200, plan["id"], "Why that wall?"))
    raw = threads.record_path(tid).read_text("utf-8")
    lines = raw.split("\n")
    at = next(i for i, line in enumerate(lines)
              if line.startswith("<!-- loam ") and f'"id": "{mine["id"]}"' in line)
    block = lines[at - 1:]
    check("the record's loam comment carries reply_to",
          json.loads(lines[at][10:-4]).get("reply_to"), plan["id"])
    quote = (f'> ↩ Ada, {plan["at"].replace("T", " ")[:16]} ({plan["id"]}): '
             f'"The plan: move the garden beds to the south wall."')
    check("the markdown opens the message with the one-line quote",
          (block[0].startswith(f"### {OWNER_NAME} · "), block[3], block[5]),
          (True, quote, "Why that wall?"))
    stored = threads.find(threads.read(tid)[1], mine["id"])
    check("parsed back: reply_to kept, text without the quote line",
          (stored["reply_to"], stored["text"]), (plan["id"], "Why that wall?"))
    status, page = await call(server, "GET", f"/v1/threads/{tid}", params={"seen": "0"})
    check("the thread read serves reply_to",
          threads.find(page["messages"], mine["id"]).get("reply_to"), plan["id"])
    done = [e["message"] for e in stream_w.items() if e.get("type") == "message.done"
            and e["message"]["id"] == mine["id"]]
    check("message.done serves reply_to", [m.get("reply_to") for m in done], [plan["id"]])

    # -- the persona's prompt -----------------------------------------------------
    prompt = ada.prompts[before]
    check("the referenced message is far outside the new window",
          f"({plan['id']}):" in prompt.split(f"{OWNER_NAME} is replying")[0], False)
    want = (f"{OWNER_NAME} is replying to this earlier message, Ada's {plan['id']} of "
            f"[{threads.local_stamp(plan['at'])}]:\n"
            f"> The plan: move the garden beds to the south wall.\n"
            f"{OWNER_NAME}'s words in reply:\nWhy that wall?")
    check("the prompt shows it whole, who and when, right above his words",
          want in prompt, True)
    check("...and every message in the prompt carries its id",
          f"{OWNER_NAME} ({mine['id']}):" in prompt, True)
    check("...and the tail says how a persona replies to one",
          "begin with ↩ and its id" in prompt, True)

    # Inside the window too: reply to the message just before.
    last_clay = [m for m in threads.read(tid)[1] if m["author"] == "ada"][-1]
    before = len(ada.prompts)
    await call(server, "POST", f"/v1/threads/{tid}/messages",
               {"text": "say more", "reply_to": last_clay["id"]})
    await settle(server, tid)
    check("a reply to a message inside the window is quoted all the same",
          f"Ada's {last_clay['id']} of" in ada.prompts[before], True)

    # A long one is cut, with where the rest is.
    long = threads.append(tid, "iris", "word " * 2000)
    window = [threads.append(tid, threads.OWNER, "this", reply_to=long["id"])]
    shown = threads.transcript(window, threads.read(tid)[1],
                               str(threads.record_path(tid)))
    check("a long referenced message: its first part and where the rest is",
          (f"cut at {threads.PROMPT_QUOTE_CHARS} of" in shown,
           f"{threads.record_path(tid)} at id {long['id']}" in shown,
           len(shown) < threads.PROMPT_QUOTE_CHARS + 600), (True, True, True))

    # -- refused --------------------------------------------------------------
    count = len(threads.read(tid)[1])
    turns = len(ada.prompts)
    status, err = await call(server, "POST", f"/v1/threads/{tid}/messages",
                             {"text": "wrong one", "reply_to": "m9999"})
    check("reply_to an id this thread lacks: 404", status, 404)
    status, err = await call(server, "POST", f"/v1/threads/{tid}/messages",
                             {"text": "wrong one", "reply_to": "nope"})
    check("a malformed reply_to: 400", status, 400)
    await settle(server, tid)
    check("...nothing written and no turn started",
          (len(threads.read(tid)[1]), len(ada.prompts)), (count, turns))
    # Ids are per thread: the owner's reply above is an id only this thread
    # holds, so naming it in Iris's thread is a foreign id.
    foreign = mine["id"]
    check("(the id is this thread's and not Iris's)",
          (threads.find(threads.read(other)[1], foreign) is None,
           threads.find(threads.read(tid)[1], foreign) is not None), (True, True))
    held = len(threads.read(other)[1])
    status, _ = await call(server, "POST", f"/v1/threads/{other}/messages",
                           {"text": "x", "reply_to": foreign})
    check("reply_to a message of another thread: 404, nothing written",
          (status, len(threads.read(other)[1])), (404, held))
    check("(Iris's own id is valid in Iris's thread)",
          threads.check_reply_to(threads.read(other)[1], iris_msg, other)["id"], iris_msg)

    # -- a persona replies to a message -----------------------------------------
    ada.script = [text(f"↩ {plan['id']} Still the south wall."), result(1)]
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "still sure?"})
    await settle(server, tid)
    answer = threads.read(tid)[1][-1]
    check("a persona's `↩ m2` opening becomes reply_to, marker gone",
          (answer["author"], answer.get("reply_to"), answer["text"]),
          ("ada", plan["id"], "Still the south wall."))
    entry = steps_mod.read(tid, answer["id"])[answer["id"]]
    check("...and the full record's entry carries it", entry.get("reply_to"), plan["id"])

    ada.script = [text(f"<- {plan['id']}\nLooking."),
                   tool("t1", "Read", {"file_path": "plan.md"}), output("t1", "x"),
                   text("Confirmed."), result(1)]
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "check"})
    await settle(server, tid)
    answer = threads.read(tid)[1][-1]
    said = [s.get("text") for s in answer.get("segments") or [] if "text" in s]
    check("...from its first text segment when it spoke before working",
          (answer.get("reply_to"), said[:1], answer["text"]),
          (plan["id"], ["Looking."], "Confirmed."))

    ada.script = [text("↩ m9999 hello"), result(1)]
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "bad"})
    await settle(server, tid)
    answer = threads.read(tid)[1][-1]
    check("a marker naming an id the thread lacks stays as text",
          (answer.get("reply_to"), answer["text"]), (None, "↩ m9999 hello"))
    status, counts = await call(server, "GET", "/v1/threads/writer")
    check("the writer counts persona replies-to", counts.get("reply_to"), 2)

    # -- bin/thread ------------------------------------------------------------
    env = {**os.environ, "LOAM_AGENT": "iris"}
    out = subprocess.run([sys.executable, str(AGENTS / "bin" / "thread"), "post",
                          tid, "From a wake.", "--reply-to", plan["id"]],
                         capture_output=True, text=True, env=env)
    got = json.loads(out.stdout)
    check("bin/thread post --reply-to", (got["ok"], got["message"].get("reply_to")),
          (True, plan["id"]))
    out = subprocess.run([sys.executable, str(AGENTS / "bin" / "thread"), "post",
                          tid, f"↩ {plan['id']} marked"],
                         capture_output=True, text=True, env=env)
    got = json.loads(out.stdout)
    check("bin/thread post with a leading marker",
          (got["message"].get("reply_to"), got["message"]["text"]), (plan["id"], "marked"))
    out = subprocess.run([sys.executable, str(AGENTS / "bin" / "thread"), "post",
                          tid, "x", "--reply-to", "m9999"],
                         capture_output=True, text=True, env=env)
    check("bin/thread refuses an id the thread lacks",
          json.loads(out.stdout)["ok"], False)

    server.threads.stop_all_tails()


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
