#!/usr/bin/env python3
"""Verify one-time agent thread auto-naming after the owner's second message."""

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
for who in ("ada", "iris", "juno"):
    (TMP / "ai-labs" / who).mkdir(parents=True)
    (TMP / "ai-labs" / who / "agent.json").write_text(
        json.dumps({"name": who, "display": who.capitalize()}))
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)

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
    print(f"{'PASS' if ok else 'FAIL'}  {label:68} got={got!r}")
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


def delta(t):
    return {"type": "stream_event", "parent_tool_use_id": None,
            "event": {"type": "content_block_delta",
                      "delta": {"type": "text_delta", "text": t}}}


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


def result(n=1):
    return {"type": "result", "parent_tool_use_id": None, "num_turns": n}


class Session:
    STEERS = True
    n = 0

    def __init__(self, agent, provider):
        Session.n += 1
        self.agent, self.provider, self.id = agent, provider, f"sess-{Session.n}"
        self.model = None
        self.subs = set()
        self.scripts = []
        self.prompts = []
        self.steers = []
        self.background_tasks = []
        self.log = []
        self.seq = 0
        self._working = False
        self.gate = None

    def save(self):
        pass

    def status(self):
        return "working" if self._working else "idle"

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

    async def steer(self, prompt, attachments=None):
        self.steers.append(prompt)
        self.emit({"type": "broker.user_message", "text": prompt, "steered": True})
        return self.seq

    async def send_message(self, prompt, attachments=None):
        self.prompts.append(prompt)
        script = self.scripts.pop(0) if self.scripts else [text("ok"), result(1)]
        gate = self.gate
        self.gate = None

        async def play():
            self._working = True
            try:
                if gate is not None:
                    await gate.wait()
                for ev in script:
                    await asyncio.sleep(0.002)
                    self.emit(ev)
            finally:
                self._working = False

        asyncio.get_running_loop().create_task(play())
        return 1


class Broker:
    def __init__(self):
        self.sessions = {}
        self.next_scripts = {}
        self.next_gates = {}

    async def start_session(self, agent, session_id=None, resume=False,
                            provider=None, model=None, effort=None):
        if session_id in self.sessions:
            s = self.sessions[session_id]
        else:
            s = Session(agent, provider or "claude-code")
            self.sessions[s.id] = s
        if self.next_scripts.get(agent):
            s.scripts.extend(self.next_scripts.pop(agent))
        if agent in self.next_gates:
            s.gate = self.next_gates.pop(agent)
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

    # -- 1. Happy path: 2nd owner message names the thread once, hidden everywhere --
    tid = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    check("new thread starts with placeholder title_source",
          threads.title_source(tid), "placeholder")

    fake.next_scripts["ada"] = [
        [text("Tell me more about what you want to grow."), result(1)],
    ]
    await call(server, "POST", f"/v1/threads/{tid}/messages",
               {"text": "I want to figure out what we should plant along the south fence this spring"})
    await settle(server, tid)
    ada_sess = next(s for s in fake.sessions.values() if s.agent == "ada")
    first_title = threads.read(tid)[0]["title"]
    check("1st owner message sets truncated auto title",
          (first_title.endswith("…"), threads.title_source(tid)),
          (True, "first_message"))
    check("1st turn prompt does NOT carry auto-title instruction",
          "<thread-title>" in ada_sess.prompts[-1], False)

    # Watch both thread stream and list stream on the 2nd owner message.
    stream_w = FakeWriter()
    list_q = server.threads.listen_list()
    stream_task = asyncio.create_task(server.thread_stream(tid, None, stream_w))
    await asyncio.sleep(0.05)

    ada_sess.scripts.append([
        delta("<thr"),
        delta("ead-title>"),
        delta("South Fence"),
        delta(" Spring Planting</thread-title>\n\n"),
        delta("Tomatoes"),
        delta(" and basil will thrive there."),
        text("<thread-title>South Fence Spring Planting</thread-title>\n\nTomatoes and basil will thrive there."),
        result(1),
    ])
    await call(server, "POST", f"/v1/threads/{tid}/messages",
               {"text": "Mostly tomatoes and herbs, full sun all afternoon."})
    await settle(server, tid)
    await asyncio.sleep(0.05)
    stream_task.cancel()
    server.threads.unlisten_list(list_q)

    check("2nd turn prompt carries the auto-title instruction",
          threads.AUTO_TITLE_INSTRUCTION in ada_sess.prompts[-1], True)

    after_meta, after_msgs, after_path = threads.read(tid)
    check("thread renamed to agent's 3-5 word title",
          (after_meta["title"], threads.title_source(tid), after_path.name),
          ("South Fence Spring Planting", "agent", "South Fence Spring Planting.md"))

    reply_msg = after_msgs[-1]
    check("saved message text never contains the title tag",
          (reply_msg["text"], "<thread-title>" in reply_msg["text"]),
          ("Tomatoes and basil will thrive there.", False))

    events = stream_w.items()
    deltas = [e for e in events if e.get("type") == "message.delta"]
    check("streaming deltas never show the title tag or title text",
          ([d["draft"] for d in deltas if "<thread-title" in d["draft"] or "South Fence" in d["draft"]],
           [d["draft"] for d in deltas]),
          ([], ["Tomatoes", "Tomatoes and basil will thrive there."]))

    changed_on_thread = [e["summary"]["title"] for e in events
                         if e.get("type") == "thread.changed" and e.get("summary")]
    list_events = []
    while not list_q.empty():
        list_events.append(list_q.get_nowait())
    changed_on_list = [e["summary"]["title"] for e in list_events
                       if e.get("type") == "thread.changed" and e.get("thread") == tid]
    check("thread.changed broadcasts the new title on thread and list streams",
          ("South Fence Spring Planting" in changed_on_thread,
           "South Fence Spring Planting" in changed_on_list),
          (True, True))

    _, served_steps = await call(server, "GET", f"/v1/threads/{tid}/steps",
                                 params={"message": reply_msg["id"]})
    step_texts = [s["text"] for s in served_steps["steps"][0]["steps"] if s.get("type") == "text"]
    _, full_page = await call(server, "GET", f"/v1/threads/{tid}",
                              params={"seen": "0", "steps": "full"})
    full_msg = threads.find(full_page["messages"], reply_msg["id"])
    full_texts = [s["text"] for s in full_msg["steps"] if s.get("type") == "text"]
    raw_steps = steps_mod.steps_path(tid).read_text("utf-8")
    check("served steps and ?steps=full hide the tag while raw journal stays intact",
          (step_texts, full_texts, "<thread-title>South Fence Spring Planting</thread-title>" in raw_steps),
          (["Tomatoes and basil will thrive there."],
           ["Tomatoes and basil will thrive there."], True))

    # 3rd owner message: never asks again, and an unsolicited tag is not applied.
    ada_sess.scripts.append([
        text("<thread-title>Should Not Overwrite</thread-title>\n\nSounds great."),
        result(1),
    ])
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "Great, thanks!"})
    await settle(server, tid)
    check("3rd turn prompt does NOT ask for a title, and title stays unchanged",
          (threads.AUTO_TITLE_INSTRUCTION in ada_sess.prompts[-1],
           threads.read(tid)[0]["title"]),
          (False, "South Fence Spring Planting"))

    # -- 2. Manual-title guards ---------------------------------------------------
    # 2a. Explicit title at creation.
    tid_explicit = (await call(server, "POST", "/v1/threads",
                               {"persona": "ada", "title": "Explicitly Named"}))[1]["id"]
    check("explicit creation title is manual",
          (threads.title_source(tid_explicit), threads.can_auto_title(tid_explicit)),
          ("manual", False))
    before_p = len(ada_sess.prompts)
    await call(server, "POST", f"/v1/threads/{tid_explicit}/messages", {"text": "one"})
    await settle(server, tid_explicit)
    await call(server, "POST", f"/v1/threads/{tid_explicit}/messages", {"text": "two"})
    await settle(server, tid_explicit)
    new_prompts = [s.prompts for s in fake.sessions.values() if s.agent == "ada"]
    latest_explicit_prompt = next(
        s.prompts[-1] for s in fake.sessions.values()
        if s.id == threads.session_for(tid_explicit, "ada")["session_id"])
    check("explicit title thread never receives auto-title instruction",
          (threads.AUTO_TITLE_INSTRUCTION in latest_explicit_prompt,
           threads.read(tid_explicit)[0]["title"]),
          (False, "Explicitly Named"))

    # 2b. Owner renames manually between message 1 and message 2.
    tid_manual = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    await call(server, "POST", f"/v1/threads/{tid_manual}/messages",
               {"text": "First message about auto naming"})
    await settle(server, tid_manual)
    await call(server, "PATCH", f"/v1/threads/{tid_manual}", {"title": "Thread auto naming"})
    check("PATCH rename marks title_source manual",
          (threads.read(tid_manual)[0]["title"], threads.title_source(tid_manual),
           threads.can_auto_title(tid_manual)),
          ("Thread auto naming", "manual", False))
    await call(server, "POST", f"/v1/threads/{tid_manual}/messages",
               {"text": "Second message after manual rename"})
    await settle(server, tid_manual)
    manual_sess = fake.sessions[threads.session_for(tid_manual, "ada")["session_id"]]
    check("manually renamed thread gets no title instruction and keeps manual title",
          (threads.AUTO_TITLE_INSTRUCTION in manual_sess.prompts[-1],
           threads.read(tid_manual)[0]["title"]),
          (False, "Thread auto naming"))

    # 2c. Race: owner renames WHILE turn 2 is already in flight.
    tid_race = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    await call(server, "POST", f"/v1/threads/{tid_race}/messages", {"text": "First msg"})
    await settle(server, tid_race)
    race_sess = fake.sessions[threads.session_for(tid_race, "ada")["session_id"]]
    gate = asyncio.Event()
    race_sess.gate = gate
    race_sess.scripts.append([
        text("<thread-title>Agent Should Lose Race</thread-title>\n\nHere is my reply."),
        result(1),
    ])
    await call(server, "POST", f"/v1/threads/{tid_race}/messages", {"text": "Second msg"})
    await asyncio.sleep(0.05)
    # Owner renames while turn 2 is mid-flight!
    await call(server, "PATCH", f"/v1/threads/{tid_race}", {"title": "Owner Won Race"})
    gate.set()
    await settle(server, tid_race)
    race_meta, race_msgs, _ = threads.read(tid_race)
    check("mid-turn manual rename is never overwritten, yet tag is still stripped",
          (race_meta["title"], threads.title_source(tid_race), race_msgs[-1]["text"]),
          ("Owner Won Race", "manual", "Here is my reply."))

    # 2d. Legacy record on disk with placeholder_title=False and no title_source (like t-1072ddd2).
    tid_legacy = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    await call(server, "POST", f"/v1/threads/{tid_legacy}/messages", {"text": "Legacy open"})
    await settle(server, tid_legacy)
    ts = threads.load_state("threads.json")
    ts[tid_legacy] = {"placeholder_title": False, "seen": "m2"}
    threads.save_state("threads.json", ts)
    check("legacy threads.json entry without title_source is protected as manual",
          (threads.title_source(tid_legacy), threads.can_auto_title(tid_legacy)),
          ("manual", False))

    # 2e. General and hello threads are never auto-named.
    threads.ensure_general()
    hello_meta = threads.create("ada", "Hello", extra={"hello": True})
    check("General and hello threads cannot be auto-named",
          (threads.can_auto_title(threads.GENERAL_ID), threads.can_auto_title(hello_meta["id"])),
          (False, False))

    # -- 3. Missing-title & empty-title graceful degradation ----------------------
    tid_miss = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    await call(server, "POST", f"/v1/threads/{tid_miss}/messages", {"text": "First topic words"})
    await settle(server, tid_miss)
    miss_sess = fake.sessions[threads.session_for(tid_miss, "ada")["session_id"]]
    miss_sess.scripts.append([text("Plain answer without any title tag."), result(1)])
    await call(server, "POST", f"/v1/threads/{tid_miss}/messages", {"text": "Second message"})
    await settle(server, tid_miss)
    miss_meta, miss_msgs, _ = threads.read(tid_miss)
    check("missing title tag keeps existing title and clean reply",
          (miss_meta["title"], miss_msgs[-1]["text"]),
          ("First topic words", "Plain answer without any title tag."))
    # 3rd message still does NOT re-ask.
    await call(server, "POST", f"/v1/threads/{tid_miss}/messages", {"text": "Third message"})
    await settle(server, tid_miss)
    check("missing title on turn 2 does not ask again on turn 3",
          threads.AUTO_TITLE_INSTRUCTION in miss_sess.prompts[-1], False)

    # Empty tag `<thread-title></thread-title>` is stripped and keeps existing title.
    tid_empty = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    await call(server, "POST", f"/v1/threads/{tid_empty}/messages", {"text": "Keep me"})
    await settle(server, tid_empty)
    empty_sess = fake.sessions[threads.session_for(tid_empty, "ada")["session_id"]]
    empty_sess.scripts.append([text("<thread-title>   </thread-title>\n\nStill answered."), result(1)])
    await call(server, "POST", f"/v1/threads/{tid_empty}/messages", {"text": "Second"})
    await settle(server, tid_empty)
    check("empty title tag stripped cleanly and current title kept",
          (threads.read(tid_empty)[0]["title"], threads.read(tid_empty)[1][-1]["text"]),
          ("Keep me", "Still answered."))

    # -- 4. Multi-member thread selection -----------------------------------------
    # 4a. @all when default (ada) is among recipients: only ada gets the instruction.
    tid_multi = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    await call(server, "PATCH", f"/v1/threads/{tid_multi}", {"add": ["iris"]})
    await call(server, "POST", f"/v1/threads/{tid_multi}/messages", {"text": "Opening question"})
    await settle(server, tid_multi)
    m_ada = fake.sessions[threads.session_for(tid_multi, "ada")["session_id"]]
    m_ada.scripts.append([text("<thread-title>Team Sync Notes</thread-title>\n\nAda here."), result(1)])
    fake.next_scripts["iris"] = [[text("Iris here."), result(1)]]
    await call(server, "POST", f"/v1/threads/{tid_multi}/messages", {"text": "@all thoughts?"})
    await settle(server, tid_multi)
    m_iris = fake.sessions[threads.session_for(tid_multi, "iris")["session_id"]]
    check("multi-member @all: default (ada) gets instruction, iris does not",
          (threads.AUTO_TITLE_INSTRUCTION in m_ada.prompts[-1],
           threads.AUTO_TITLE_INSTRUCTION in m_iris.prompts[-1],
           threads.read(tid_multi)[0]["title"]),
          (True, False, "Team Sync Notes"))

    # 4b. Default (ada) NOT among recipients (@iris @juno): first tagged (iris) gets it.
    tid_tagged = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    await call(server, "POST", f"/v1/threads/{tid_tagged}/messages", {"text": "First"})
    await settle(server, tid_tagged)
    fake.next_scripts["iris"] = [[text("<thread-title>Iris And Juno Chat</thread-title>\n\nIris."), result(1)]]
    fake.next_scripts["juno"] = [[text("Juno."), result(1)]]
    await call(server, "POST", f"/v1/threads/{tid_tagged}/messages", {"text": "@iris @juno weigh in"})
    await settle(server, tid_tagged)
    t_iris = fake.sessions[threads.session_for(tid_tagged, "iris")["session_id"]]
    t_juno = fake.sessions[threads.session_for(tid_tagged, "juno")["session_id"]]
    check("multi-member without default: first tagged (iris) gets instruction, juno does not",
          (threads.AUTO_TITLE_INSTRUCTION in t_iris.prompts[-1],
           threads.AUTO_TITLE_INSTRUCTION in t_juno.prompts[-1],
           threads.read(tid_tagged)[0]["title"]),
          (True, False, "Iris And Juno Chat"))

    # -- 5. Tool call after title tag, reply_to combo, trailing tag, & engines ----
    # 5a. Tag-only text block before tool call, then real answer.
    tid_tool = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    await call(server, "POST", f"/v1/threads/{tid_tool}/messages", {"text": "Check config"})
    await settle(server, tid_tool)
    tool_sess = fake.sessions[threads.session_for(tid_tool, "ada")["session_id"]]
    tool_sess.scripts.append([
        text("<thread-title>Nginx Config Review</thread-title>"),
        tool("t1", "Read", {"file_path": "nginx.conf"}),
        output("t1", "server {}"),
        text("The config looks clean."),
        result(1),
    ])
    await call(server, "POST", f"/v1/threads/{tid_tool}/messages", {"text": "Please verify port 80"})
    await settle(server, tid_tool)
    tool_msg = threads.read(tid_tool)[1][-1]
    _, tool_steps = await call(server, "GET", f"/v1/threads/{tid_tool}/steps",
                               params={"message": tool_msg["id"]})
    tool_step_texts = [s["text"] for s in tool_steps["steps"][0]["steps"] if s.get("type") == "text"]
    check("tag-only segment before tool call: clean did, segments, steps and title",
          (threads.read(tid_tool)[0]["title"],
           tool_msg["text"],
           any("thread-title" in d.get("label", "") for d in tool_msg.get("did", [])),
           tool_msg.get("segments"),
           tool_step_texts),
          ("Nginx Config Review", "The config looks clean.", False, None, ["The config looks clean."]))

    # 5b. Combined `<thread-title>` + `↩ m1` + quotes cleaned + Codex engine.
    tid_combo = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    await call(server, "PATCH", f"/v1/threads/{tid_combo}", {"engine": {"ada": "codex"}})
    await call(server, "POST", f"/v1/threads/{tid_combo}/messages", {"text": "Initial idea"})
    await settle(server, tid_combo)
    combo_sess = fake.sessions[threads.session_for(tid_combo, "ada")["session_id"]]
    combo_w = FakeWriter()
    combo_stream = asyncio.create_task(server.thread_stream(tid_combo, None, combo_w))
    await asyncio.sleep(0.05)
    combo_sess.scripts.append([
        delta("<thread-title>\"Initial Idea"),
        delta(" Follow Up.\"</thread-title>\n↩ m1\n\n"),
        delta("Building on"),
        delta(" your first point."),
        text("<thread-title>\"Initial Idea Follow Up.\"</thread-title>\n↩ m1\n\nBuilding on your first point."),
        result(1),
    ])
    await call(server, "POST", f"/v1/threads/{tid_combo}/messages", {"text": "Let's refine it"})
    await settle(server, tid_combo)
    await asyncio.sleep(0.05)
    combo_stream.cancel()
    combo_msg = threads.read(tid_combo)[1][-1]
    combo_deltas = [e for e in combo_w.items() if e.get("type") == "message.delta"]
    check("combo title+reply_to on codex: strips quotes/period, streams reply_to cleanly",
          (threads.read(tid_combo)[0]["title"],
           combo_msg.get("reply_to"),
           combo_msg["text"],
           [d["draft"] for d in combo_deltas],
           {d.get("reply_to") for d in combo_deltas}),
          ("Initial Idea Follow Up", "m1", "Building on your first point.",
           ["Building on", "Building on your first point."], {"m1"}))

    # 5c. Trailing tag on antigravity engine.
    tid_agy = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    await call(server, "PATCH", f"/v1/threads/{tid_agy}", {"engine": {"ada": "antigravity"}})
    await call(server, "POST", f"/v1/threads/{tid_agy}/messages", {"text": "First agy message"})
    await settle(server, tid_agy)
    agy_sess = fake.sessions[threads.session_for(tid_agy, "ada")["session_id"]]
    agy_w = FakeWriter()
    agy_stream = asyncio.create_task(server.thread_stream(tid_agy, None, agy_w))
    await asyncio.sleep(0.05)
    agy_sess.scripts.append([
        delta("Done with the check."),
        delta("\n\n<thread-"),
        delta("title>Antigravity Smoke Check</thread-title>"),
        text("Done with the check.\n\n<thread-title>Antigravity Smoke Check</thread-title>"),
        result(1),
    ])
    await call(server, "POST", f"/v1/threads/{tid_agy}/messages", {"text": "Second agy message"})
    await settle(server, tid_agy)
    await asyncio.sleep(0.05)
    agy_stream.cancel()
    agy_deltas = [e for e in agy_w.items() if e.get("type") == "message.delta"]
    check("trailing title tag on antigravity: withheld in stream, stripped and applied",
          (threads.read(tid_agy)[0]["title"],
           threads.read(tid_agy)[1][-1]["text"],
           [d["draft"] for d in agy_deltas if "<thread" in d["draft"]]),
          ("Antigravity Smoke Check", "Done with the check.", []))

    # 5d. Code span containing `<thread-title>` is untouched.
    cleaned_code, raw_code = threads.take_thread_title("Mention `<thread-title>Foo</thread-title>` in docs.")
    check("inline code span with <thread-title> is untouched",
          (cleaned_code, raw_code),
          ("Mention `<thread-title>Foo</thread-title>` in docs.", None))

    # 5e. Steered 2nd message while turn 1 is still running carries auto-title instruction.
    tid_steer = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    steer_gate = asyncio.Event()
    fake.next_gates["ada"] = steer_gate
    fake.next_scripts["ada"] = [[
        text("<thread-title>Steered Second Message</thread-title>\n\nGot both messages."),
        result(1),
    ]]
    await call(server, "POST", f"/v1/threads/{tid_steer}/messages", {"text": "First quick note"})
    await asyncio.sleep(0.05)
    # Send 2nd message while turn 1 is still working -> gets steered!
    await call(server, "POST", f"/v1/threads/{tid_steer}/messages", {"text": "And one more detail"})
    await asyncio.sleep(0.05)
    steer_gate.set()
    await settle(server, tid_steer)
    steer_sess = fake.sessions[threads.session_for(tid_steer, "ada")["session_id"]]
    check("steered 2nd owner message carries auto-title instruction and renames thread",
          (any(threads.AUTO_TITLE_INSTRUCTION in s for s in steer_sess.steers),
           threads.read(tid_steer)[0]["title"],
           threads.read(tid_steer)[1][-1]["text"]),
          (True, "Steered Second Message", "Got both messages."))

    server.threads.stop_all_tails()


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
