#!/usr/bin/env python3
"""The thread API loam-ui 7731c02 is built against (2026-11-09), through the
broker's routes with a fake harness.

  - a thread with no default: untagged messages are kept and nobody
    answers; a tag or a default brings someone who reads the history
  - PATCH: icon, pinned, archived, default null, per-member model (checked
    against the engine), and General refusing rename, archive and delete
  - GET /v1/threads?archived=1, and DELETE removing the record and board
  - messages with a per-turn model and attachments uploaded to the thread
  - a reply that speaks, works and speaks again kept as ordered segments
  - an explicit waiting-on-a-background-task step
  - the thread list's own stream
  - `bin/thread rename` (2026-11-10): record title, file name, board file and
    `board:` key moved together, the board's text kept, an open list told,
    no line in the thread, General refused, a taken board name not shared

Run: python3 tests/test_thread_api.py
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
for who in ("ada", "iris", "cleo"):
    (TMP / "ai-labs" / who).mkdir(parents=True)
    (TMP / "ai-labs" / who / "agent.json").write_text(
        json.dumps({"name": who.capitalize()}))
(TMP / "ai-labs" / "iris" / "agent.json").write_text(
    json.dumps({"name": "Iris", "provider": "codex"}))
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents" / "comms.md").write_text("# From agents\n")

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
# Admission reads no real box here: a fixture session must not be refused
# because the machine running the suite is short of memory (a landing
# got 429 for one, 2026-11-09). test_broker_seats is where admission is tested.
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


def ev_text(text):
    return {"type": "assistant", "parent_tool_use_id": None,
            "message": {"content": [{"type": "text", "text": text}]}}


def ev_tool(name, path):
    return {"type": "assistant", "parent_tool_use_id": None,
            "message": {"content": [{"type": "tool_use", "name": name,
                                     "input": {"file_path": path}}]}}


RESULT = {"type": "result", "parent_tool_use_id": None}


class FakeSession:
    n = 0

    def __init__(self, agent, provider, sid, model=None):
        self.agent, self.provider, self.id, self.model = agent, provider, sid, model
        self.subs = set()
        self.prompts = []
        self.sent_attachments = []
        self.choices = []
        self.script = None
        self.behind = False
        self.background_tasks = []

    def save(self):
        pass

    def watch(self, q):
        self.subs.add(q)

    def unwatch(self, q):
        self.subs.discard(q)

    def working_behind(self):
        return self.behind

    async def set_choices(self, changes):
        self.choices.append(changes)
        self.model = changes.get("model")
        return []

    async def send_message(self, text, attachments=None):
        self.prompts.append(text)
        self.sent_attachments.append(attachments or [])
        script = self.script or [ev_text(f"{self.agent} here"), RESULT]
        self.script = None

        async def play():
            for ev in script:
                if callable(ev):
                    ev(self)
                    continue
                await asyncio.sleep(0.005)
                for q in list(self.subs):
                    q.put_nowait(ev)
        asyncio.get_running_loop().create_task(play())
        return 1


class FakeBroker:
    def __init__(self):
        self.sessions = {}
        self.starts = []

    async def start_session(self, agent, session_id=None, resume=False,
                            provider=None, model=None):
        self.starts.append((agent, session_id, resume, provider, model))
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        FakeSession.n += 1
        s = FakeSession(agent, provider, f"s{FakeSession.n}", model)
        self.sessions[s.id] = s
        return s


async def call(server, method, path, payload=None, params=None, raw=b"", ctype=""):
    w = FakeWriter()
    try:
        await server.route(method, path, params or {}, payload or {}, w,
                           raw=raw, content_type=ctype)
    except (broker_mod.BrokerError, threads.ThreadError) as exc:
        return exc.status, {"error": exc.message}
    return w.json()


async def settle(server, tid, rounds=300):
    for _ in range(rounds):
        await asyncio.sleep(0.01)
        if server.threads.settled(tid):
            return


def sessions_of(fake, agent):
    return [s for s in fake.sessions.values() if s.agent == agent]


async def main():
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = FakeBroker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)

    # -- the list's stream, opened first so it sees everything ------------
    list_w = FakeWriter()
    list_stream = asyncio.create_task(server.thread_list_stream(list_w))
    await asyncio.sleep(0.05)
    check("the list stream opens with the rows and the agents",
          (list_w.items()[0]["type"], "threads" in list_w.items()[0],
           "agents" in list_w.items()[0]), ("threads.snapshot", True, True))

    # -- a thread with nobody default ---------------------------------------
    status, row = await call(server, "POST", "/v1/threads", {})
    tid = row["id"]
    check("POST /v1/threads with no persona: no default, no members",
          (status, row["default"], row["members"], row["title"]),
          (201, None, [], "New thread"))
    status, _ = await call(server, "POST", f"/v1/threads/{tid}/messages",
                           {"text": "Notes before anyone is here"})
    await settle(server, tid)
    got = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    check("an untagged message with no default is kept and nobody answers",
          ([m["author"] for m in got["messages"]], fake.starts), ([OWNER], []))
    status, _ = await call(server, "POST", f"/v1/threads/{tid}/messages",
                           {"text": "@ada what do you make of that?"})
    await settle(server, tid)
    got = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    ada = sessions_of(fake, "ada")[0]
    check("a tag brings Ada in and Ada answers",
          [m["author"] for m in got["messages"]][-2:], ["system", "ada"])
    check("...having read the history from before it was there",
          "Notes before anyone is here" in ada.prompts[0], True)
    check("...told nobody answers untagged messages",
          "nobody answers when nobody is tagged" in ada.prompts[0], True)
    status, _ = await call(server, "POST", f"/v1/threads/{tid}/messages",
                           {"text": "still nobody's default"})
    await settle(server, tid)
    got = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    check("...and the next untagged one is still answered by nobody",
          got["messages"][-1]["author"], OWNER)

    # -- PATCH: default, icon, pinned, archived, model ------------------
    status, summary = await call(server, "PATCH", f"/v1/threads/{tid}",
                                 {"default": "ada", "icon": "🌱", "pinned": True})
    check("PATCH sets the default, an icon and a pin",
          (status, summary["default"], summary["icon"], summary["pinned"]),
          (200, "ada", "🌱", True))
    status, summary = await call(server, "PATCH", f"/v1/threads/{tid}",
                                 {"default": None})
    got = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    check("`default: null` clears it, and the thread says so",
          (summary["default"], got["messages"][-1]["kind"],
           got["messages"][-1]["text"].startswith(f"{OWNER_NAME} set no default")),
          (None, "system", True))
    meta_text = threads.read(tid)[2].read_text()
    check("...the record's frontmatter keeps the icon, the pin and no default",
          ("default:\n" in meta_text, 'icon: "🌱"' in meta_text,
           "pinned: true" in meta_text), (True, True, True))

    status, summary = await call(server, "PATCH", f"/v1/threads/{tid}",
                                 {"default": "ada", "model": {"ada": "opus"}})
    check("a member's model is set, on its row",
          (status, [m["model"] for m in summary["members"] if m["persona"] == "ada"]),
          (200, ["opus"]))
    status, body = await call(server, "PATCH", f"/v1/threads/{tid}",
                              {"model": {"ada": "gpt-6"}})
    check("...a model Claude Code does not take is refused, naming the list",
          (status, "is not one this CLI takes" in body["error"]), (400, True))
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "and now?"})
    await settle(server, tid)
    check("the member's model is what its turn runs on",
          (ada.choices[-1], ada.model), ({"model": "opus"}, "opus"))
    got = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    check("...and the reply records it", got["messages"][-1].get("model"), "opus")

    # a per-turn model, on Codex for Iris
    status, _ = await call(server, "PATCH", f"/v1/threads/{tid}", {"add": ["iris"]})
    status, body = await call(server, "POST", f"/v1/threads/{tid}/messages",
                              {"text": "@iris quick one", "model": "claude-sonnet-5"})
    check("a per-turn model is checked against the tagged member's engine",
          (status, "Codex lists" in body.get("error", "")), (400, True))
    status, body = await call(server, "POST", f"/v1/threads/{tid}/messages",
                              {"text": "@iris quick one", "model": "gpt-6-luna"})
    await settle(server, tid)
    iris = sessions_of(fake, "iris")[0]
    starts = [s for s in fake.starts if s[0] == "iris"]
    check("...and the turn starts on it", (starts[-1][3], starts[-1][4]),
          ("codex", "gpt-6-luna"))
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "@iris and again"})
    await settle(server, tid)
    check("...for that turn only: the next goes back to the persona's own",
          iris.choices[-1], {"model": None})
    status, summary = await call(server, "PATCH", f"/v1/threads/{tid}",
                                 {"model": {"ada": None}})
    check("`model: {ada: null}` puts Ada back on the persona's default",
          [m["model"] for m in summary["members"] if m["persona"] == "ada"], [None])

    # -- attachments -----------------------------------------------------------
    starts_before = len(fake.starts)
    boundary = "XyZ"
    raw = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
           f"filename=\"plan.txt\"\r\nContent-Type: text/plain\r\n\r\n"
           f"beds by the fence\r\n--{boundary}--\r\n").encode()
    status, up = await call(server, "POST", f"/v1/threads/{tid}/attachments",
                            raw=raw, ctype=f"multipart/form-data; boundary={boundary}")
    stored = up["attachments"][0]
    check("a thread upload answers the classic attachment object",
          (status, stored["name"], sorted(stored)),
          (201, "plan.txt", ["name", "path", "size", "type"]))
    check("...and the bytes are in the vault, with no session started for it",
          (Path(stored["path"]).read_text(), len(fake.starts)),
          ("beds by the fence", starts_before))
    await call(server, "POST", f"/v1/threads/{tid}/messages",
               {"text": "see the plan", "attachments": [stored["path"]]})
    await settle(server, tid)
    got = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    asked = [m for m in got["messages"] if m["author"] == OWNER][-1]
    check("the message carries its attachment on the record",
          [a["name"] for a in asked.get("attachments", [])], ["plan.txt"])
    check("...and the turn it starts carries it to the harness",
          [a["path"] for a in ada.sent_attachments[-1]], [stored["path"]])
    check("...and names its path in the history a member reads",
          stored["path"] in ada.prompts[-1], True)
    status, body = await call(server, "POST", f"/v1/threads/{tid}/messages",
                              {"text": "x", "attachments": ["/etc/passwd"]})
    check("an attachment that is not a stored upload is refused", status, 400)

    # -- segments: speak, work, speak --------------------------------------
    ada.script = [ev_text("Let me look at the bed list first."),
                   ev_tool("Read", "beds.md"),
                   ev_text("The east bed is free."),
                   ev_tool("Edit", "beds.md"),
                   ev_text("Booked it for the beans."), RESULT]
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "which bed?"})
    await settle(server, tid)
    threads._CACHE.clear()
    got = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    reply = got["messages"][-1]
    check("a reply that speaks, works and speaks again keeps its order",
          reply.get("segments"),
          [{"text": "Let me look at the bed list first."},
           {"did": [{"label": "Read: beds.md"}]},
           {"text": "The east bed is free."},
           {"did": [{"label": "Edit: beds.md"}]}])
    check("...the text is what it said last, and the earlier speech is whole",
          reply["text"], "Booked it for the beans.")
    check("...and `did` is as it was, for a reader of the older field",
          [d["label"] for d in reply["did"]][:2],
          ["Said: Let me look at the bed list first.", "Read: beds.md"])
    check("the record stays readable: the last thing it said is the body",
          "\n\nBooked it for the beans.\n" in threads.read(tid)[2].read_text(), True)

    # -- waiting on a background task ----------------------------------------
    thread_w = FakeWriter()
    stream = asyncio.create_task(server.thread_stream(tid, None, thread_w))
    await asyncio.sleep(0.05)

    def launch(s):
        s.behind = True
        s.background_tasks = [{"task_id": "t1", "description": "count the seeds"}]

    def report(s):
        s.behind = False
        s.background_tasks = []

    ada.script = [ev_text("Starting a count."), launch, RESULT,
                   {"type": "system", "subtype": "init"}, report,
                   ev_text("Forty seeds."), RESULT]
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": "count them"})
    await settle(server, tid)
    stream.cancel()
    steps = [e for e in thread_w.items() if e["type"] == "turn.step"]
    check("a result with work still out is a waiting step, said on the stream",
          [(e.get("waiting"), e["label"]) for e in steps],
          [(True, "Waiting on a background task: count the seeds")])
    got = (await call(server, "GET", f"/v1/threads/{tid}"))[1]
    # Each harness turn is its own message since 2026-11-10: until then the
    # follow-up's answer replaced the first reply's text in one message.
    check("...and the follow-up's answer is a message of its own, after the first",
          [m["text"] for m in got["messages"][-2:]],
          ["Starting a count.", "Forty seeds."])
    check("the stream says when a turn finished",
          "turn.finished" in [e["type"] for e in thread_w.items()], True)

    # -- archive, the archived list, General's refusals ---------------------
    status, summary = await call(server, "PATCH", f"/v1/threads/{tid}",
                                 {"archived": True})
    status, listing = await call(server, "GET", "/v1/threads")
    check("an archived thread leaves the list",
          tid in [t["id"] for t in listing["threads"]], False)
    status, listing = await call(server, "GET", "/v1/threads",
                                 params={"archived": "1"})
    check("...and is on ?archived=1, saying so",
          [t["archived"] for t in listing["threads"] if t["id"] == tid], [True])
    check("archiving moved nothing on disk", threads.read(tid)[2].is_file(), True)
    for body, word in (({"title": "Renamed"}, "renamed"),
                       ({"archived": True}, "archived")):
        status, answer = await call(server, "PATCH", "/v1/threads/general", body)
        check(f"General can't be {word}", (status, word in answer["error"]), (400, True))
    status, answer = await call(server, "DELETE", "/v1/threads/general")
    check("General can't be deleted", (status, "deleted" in answer["error"]), (400, True))

    # -- delete -----------------------------------------------------------------
    meta, _, path = threads.read(tid)
    board = threads.VAULT / meta["board"]
    threads.board_text(meta)                 # make the board exist
    status, gone = await call(server, "DELETE", f"/v1/threads/{tid}")
    check("DELETE removes the record and its board",
          (status, path.exists(), board.exists(), sorted(gone["removed"])),
          (200, False, False, sorted([str(path.relative_to(threads.VAULT)),
                                      meta["board"]])))
    status, _ = await call(server, "GET", f"/v1/threads/{tid}")
    check("...and the thread is gone", status, 404)

    await asyncio.sleep(2.2)                 # one stat pass of the list stream
    list_stream.cancel()
    kinds = [(e["type"], e.get("thread")) for e in list_w.items()]
    check("the list stream told every change of that thread's row",
          ("thread.changed", tid) in kinds, True)
    check("...and its removal", ("thread.removed", tid) in kinds, True)
    archived_rows = [e["summary"]["archived"] for e in list_w.items()
                     if e["type"] == "thread.changed" and e.get("thread") == tid]
    check("...including the row saying it was archived", True in archived_rows, True)

    # an agent's post from outside the broker reaches an open list
    list_w = FakeWriter()
    list_stream = asyncio.create_task(server.thread_list_stream(list_w))
    await asyncio.sleep(0.05)
    threads.append("general", "cleo", "woke", kind="bulletin")
    await asyncio.sleep(2.3)
    list_stream.cancel()
    check("a post written outside the broker reaches an open list by its stat",
          [e["summary"]["last"]["text"] for e in list_w.items()
           if e["type"] == "thread.changed" and e.get("thread") == "general"][-1:],
          ["woke"])

    # -- a persona renames a thread (2026-11-10), as the details page does ----
    def rename(tid, title, by="iris"):
        out = subprocess.run([sys.executable, str(AGENTS / "bin" / "thread"),
                              "rename", tid, title, "--by", by],
                             capture_output=True, text=True, env=dict(os.environ))
        return json.loads(out.stdout)

    status, row = await call(server, "POST", "/v1/threads", {"persona": "iris"})
    tid = row["id"]
    meta, msgs_before, old_path = threads.read(tid)
    old_board = threads.VAULT / meta["board"]
    threads.write_board(meta, "# board\n\n- [ ] keep me <!-- Iris · new -->\n")
    # Another thread already holds the board name the new title would take.
    status, other = await call(server, "POST", "/v1/threads",
                               {"persona": "ada", "title": "What to pursue"})
    other_meta = threads.read(other["id"])[0]
    threads.write_board(other_meta, "# other\n")
    list_w = FakeWriter()
    list_stream = asyncio.create_task(server.thread_list_stream(list_w))
    await asyncio.sleep(0.05)
    answer = rename(tid, "What to pursue")
    await asyncio.sleep(2.3)
    list_stream.cancel()
    meta, msgs_after, new_path = threads.read(tid)
    front = threads.split_front(new_path.read_text("utf-8"))[0]
    check("bin/thread rename answers ok with the new title",
          (answer["ok"], answer["title"]), (True, "What to pursue"))
    check("...the record's title: and its file name both moved",
          (front["title"], new_path.name, old_path.exists()),
          ("What to pursue", "What to pursue (2).md", False))
    check("...the board went with it, under a name nobody else's board has",
          (front["board"], (threads.VAULT / front["board"]).is_file(),
           old_board.exists()),
          ("00_INBOX/boards/What to pursue (2) — board.md", True, False))
    check("...the board link resolves to its own text",
          "keep me" in (threads.VAULT / front["board"]).read_text(), True)
    check("...the other thread's board untouched",
          (threads.VAULT / threads.read(other["id"])[0]["board"]).read_text(),
          "# other\n")
    status, opened = await call(server, "GET", f"/v1/threads/{tid}")
    check("...the API opens it, title and board",
          (status, opened["thread"]["title"], "keep me" in opened["board"]["markdown"]),
          (200, "What to pursue", True))
    check("...no line was added to the thread, as the app's rename adds none",
          len(msgs_after), len(msgs_before))
    check("...an open list was told the new title by its stat",
          [e["summary"]["title"] for e in list_w.items()
           if e["type"] == "thread.changed" and e.get("thread") == tid][-1:],
          ["What to pursue"])
    again = rename(tid, "What to pursue.")       # same file name: stays put
    check("a rename to the same file name keeps the file",
          (again["ok"], threads.read(tid)[2].name), (True, "What to pursue (2).md"))
    refused = rename("general", "Not General")
    check("General can't be renamed by bin/thread",
          (refused["ok"], "General" in refused["error"]), (False, True))
    nobody = rename(tid, "x", by="")
    check("...nor by nobody", nobody["ok"], False)


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
