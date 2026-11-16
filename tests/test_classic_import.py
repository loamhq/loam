#!/usr/bin/env python3
"""Classic chats imported as archived threads (2026-11-11).

``bin/import-classic-chats`` on fixtures, then through the broker's routes:

  - a chat the broker ran: the owner's messages (clock envelope off, time from
    the transcript, an attachment kept), one persona message per harness
    turn, the full record from the session log, `segments` for words said
    before the tools
  - an adopted chat whose app file is gone: its transcript is the record,
    and a logged reply's steps go to the turn whose words contain it
  - a broker log from before `broker.user_message`: the transcript wins
  - a thread's own engine session and an empty session are not imported
  - the record: same title, the persona the only member and default,
    archived, `imported_from` and `source`, read up to its end
  - the original transcript and session files untouched
  - a second run imports nothing
  - GET /v1/threads?archived=1, /v1/threads/imported, /v1/threads/<id>,
    /v1/threads/<id>/steps
  - the owner writing in one: out of the archive, answered by its persona;
    a thread archived by hand stays archived when written in

Run: python3 tests/test_classic_import.py
"""

import asyncio
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
for who in ("ada", "iris"):
    (TMP / "ai-labs" / who).mkdir(parents=True)
    (TMP / "ai-labs" / who / "agent.json").write_text(json.dumps({"name": who.capitalize()}))
CONV = TMP / "vault" / "15_CONVERSATIONS"
CONV.mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents" / "comms.md").write_text("# From agents\n")

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_adopt as adoption  # noqa: E402
import loam_classic_import as importer  # noqa: E402
import loam_thread_steps as steps  # noqa: E402
import loam_threads as threads  # noqa: E402
OWNER, OWNER_NAME = cfg.lc.OWNER_ID, cfg.lc.OWNER_NAME

cfg.SESSION_DIR = TMP / "sessions"
cfg.SESSION_DIR.mkdir()
adoption.TRANSCRIPT_STATE = TMP / "transcripts"
adoption.TRANSCRIPT_STATE.mkdir()
cfg.roster = threads.roster
cfg.roster_cards = lambda: [{"id": p} for p in threads.roster()]
cfg.VAULT = TMP / "vault"

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


# -- fixtures ---------------------------------------------------------------------

def record(sid, agent, title, **extra):
    meta = {"session_id": sid, "agent": agent, "provider": "claude-code",
            "origin": "loam-ui", "title": title, "created": "2026-10-01T10:00:00Z",
            "state": "exited", "archived": False, **extra}
    (cfg.SESSION_DIR / f"{sid}.json").write_text(json.dumps(meta))


def log(sid, events):
    with (cfg.SESSION_DIR / f"{sid}.jsonl").open("w") as fh:
        for i, e in enumerate(events, 1):
            fh.write(json.dumps({**e, "seq": i, "session_id": sid}) + "\n")


def transcript(sid, title, turns):
    path = CONV / f"{title}.md"
    body = "\n\n".join(f"<!-- turn speaker={who} timestamp={at} -->\n"
                       f"### {'User' if who == 'user' else who.capitalize()}\n\n{text}"
                       for who, at, text in turns)
    path.write_text(f"---\ntitle: \"{title}\"\nsession_id: {sid}\n---\n\n{body}\n")
    (adoption.TRANSCRIPT_STATE / f"{sid}.json").write_text(json.dumps({"path": str(path)}))
    return path


def user(text, **extra):
    return {"type": "broker.user_message", "text": text, **extra}


def say(text, ts):
    return {"type": "assistant", "parent_tool_use_id": None, "timestamp": ts,
            "message": {"model": "claude-test", "content": [{"type": "text", "text": text}]}}


def tool(name, ts, tid):
    return {"type": "assistant", "parent_tool_use_id": None, "timestamp": ts,
            "message": {"content": [{"type": "tool_use", "id": tid, "name": name,
                                     "input": {"file_path": "/x"}}]}}


def out(tid, ts):
    return {"type": "user", "parent_tool_use_id": None, "timestamp": ts,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
                                     "content": "file text"}]}}


RESULT = {"type": "result", "parent_tool_use_id": None, "subtype": "success"}
NOISE = {"type": "stream_event", "event": {"delta": "x"}}

# 1. A chat the broker ran. Two prompts; the second turn has a background
# notification after its result, which is a second harness turn.
A = "aaaaaaaa-0000-0000-0000-000000000001"
record(A, "ada", "Planning the garden")
ATTACH = [{"path": "/v/98_ATTACHMENTS/loam-ui/x.png", "name": "x.png", "type": "image"}]
log(A, [
    {"type": "system", "subtype": "init", "model": "claude-test"},
    user("<loam-clock>Mon 09:00 PDT</loam-clock>\nWhat should I plant?", attachments=ATTACH),
    NOISE,
    say("Let me look at your notes.", "2026-10-01T10:00:05.123Z"),
    tool("Read", "2026-10-01T10:00:06Z", "tu1"),
    out("tu1", "2026-10-01T10:00:07Z"),
    say("Tomatoes and basil.", "2026-10-01T10:00:09Z"),
    RESULT,
    user("And when?"),
    say("In May.", "2026-10-01T10:05:03Z"),
    RESULT,
    say("The frost check came back: mid-May.", "2026-10-01T10:07:00Z"),
    RESULT,
])
A_TRANSCRIPT = transcript(A, "Planning the garden", [
    ("user", "2026-10-01T10:00:01Z", "What should I plant?"),
    ("ada", "2026-10-01T10:00:09Z", "Let me look at your notes.\n\nTomatoes and basil."),
    ("user", "2026-10-01T10:05:00Z", "And when?"),
    ("ada", "2026-10-01T10:07:00Z", "In May.\n\nThe frost check came back: mid-May."),
])

# 2. Adopted from the app; its app file is gone. The broker ran one turn
# on it after adoption, which its log holds.
B = "bbbbbbbb-0000-0000-0000-000000000002"
record(B, "iris", "Books on Washington", origin="claude-code-app",
       source=str(TMP / "gone" / f"{B}.jsonl"), replayed=12, archived=True)
log(B, [user("One more?"), tool("WebSearch", "2026-09-09T12:00:01Z", "tu9"),
        out("tu9", "2026-09-09T12:00:02Z"),
        say("Try *His Excellency*.", "2026-09-09T12:00:03Z"), RESULT])
transcript(B, "Books on Washington", [
    ("user", "2026-09-08T18:27:03Z", "A biography of Washington?"),
    ("iris", "2026-09-08T18:27:30Z", "Chernow's *Washington: A Life*."),
    ("user", "2026-09-09T12:00:00Z", "One more?"),
    ("iris", "2026-09-09T12:00:03Z", "Searching. Try *His Excellency*."),
])

# 3. A September broker log: replies, no `broker.user_message`.
C = "cccccccc-0000-0000-0000-000000000003"
record(C, "ada", "Runtime check")
log(C, [say("Checked: all green.", "2026-09-07T01:00:05Z"), RESULT])
transcript(C, "Runtime check", [
    ("user", "2026-09-07T01:00:00Z", "Is the runtime fine?"),
    ("ada", "2026-09-07T01:00:05Z", "Checked: all green."),
])

# 4. Not classic chats: a thread's engine session, and an empty one.
record("dddddddd-0000-0000-0000-000000000004", "ada", "Thread · Something")
record("eeeeeeee-0000-0000-0000-000000000005", "ada", "Joined", thread_id="t-x")
record("ffffffff-0000-0000-0000-000000000006", "ada", None)
log("ffffffff-0000-0000-0000-000000000006", [{"type": "broker.exit", "exit_code": 0}])


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


ORIGINALS = {p: digest(p) for p in list(CONV.glob("*.md")) + list(cfg.SESSION_DIR.iterdir())}

# -- the import -------------------------------------------------------------------

with redirect_stdout(io.StringIO()):
    first = importer.run()
check("four classic chats counted, three imported, one could not be",
      (first["chats"], first["imported"], [s[0][:8] for s in first["skipped"]]),
      (4, 3, ["ffffffff"]))

mapping = threads.imported()
check("each imported chat is some thread's `imported_from`",
      sorted(s[:8] for s in mapping), ["aaaaaaaa", "bbbbbbbb", "cccccccc"])

meta, msgs, path = threads.read(mapping[A])
check("same title, the persona the only member and the default, archived",
      (meta["title"], meta["members"], meta["default"], meta["archived"],
       meta["engines"]),
      ("Planning the garden", ["ada"], "ada", True, {"ada": "claude-code"}))
check("...its record is beside the transcript, which keeps its own name",
      (path.name, A_TRANSCRIPT.exists()),
      ("Planning the garden (2).md", True))
check("...frontmatter names the session and the transcript",
      (meta["imported_from"], meta["source"]),
      (A, "15_CONVERSATIONS/Planning the garden.md"))
check("...created when the chat was",
      meta["created"], "2026-10-01T10:00:00Z")
check("messages in order with their authors",
      [(m["id"], m["author"]) for m in msgs],
      [("m1", OWNER), ("m2", "ada"), ("m3", OWNER), ("m4", "ada"),
       ("m5", "ada")])
check("the owner's words without the clock envelope, their attachment kept",
      (msgs[0]["text"], msgs[0].get("attachments")), ("What should I plant?", ATTACH))
check("the owner's times from the transcript (the log has none)",
      (msgs[0]["at"], msgs[2]["at"]), ("2026-10-01T10:00:01Z", "2026-10-01T10:05:00Z"))
check("a reply's time is its last event's",
      (msgs[1]["at"], msgs[3]["at"], msgs[4]["at"]),
      ("2026-10-01T10:00:09Z", "2026-10-01T10:05:03Z", "2026-10-01T10:07:00Z"))
check("a reply's text is its last words, its earlier words in `segments`",
      (msgs[1]["text"], msgs[1]["segments"][0], [d["label"][:5] for d in msgs[1]["did"]]),
      ("Tomatoes and basil.", {"text": "Let me look at your notes."}, ["Said:", "Read:"]))
check("one message per harness turn: the notification's reply is its own",
      (msgs[3]["text"], msgs[4]["text"]),
      ("In May.", "The frost check came back: mid-May."))
check("...each reply with the engine and the model it ran on",
      (msgs[1]["engine"], msgs[1]["model"]), ("claude-code", "claude-test"))

full = steps.read(mapping[A])
check("every reply has its full record, marked imported",
      (sorted(full), {e["imported"] for e in full.values()},
       {e["session_id"] for e in full.values()}),
      (["m2", "m4", "m5"], {True}, {A}))
tool_step = [s for s in full["m2"]["steps"] if s["type"] == "tool"][0]
check("...with the tool's input and output, and no stream events",
      (tool_step["name"], tool_step["output"],
       any(s.get("event", {}).get("type") == "stream_event" for s in full["m2"]["steps"])),
      ("Read", "file text", False))
check("read up to its end: an imported chat is not all unread",
      threads.unread(mapping[A], msgs), 0)

meta, msgs, _ = threads.read(mapping[B])
check("an adopted chat with its app file gone: the transcript is the record",
      [(m["author"], m["at"], m["text"]) for m in msgs],
      [(OWNER, "2026-09-08T18:27:03Z", "A biography of Washington?"),
       ("iris", "2026-09-08T18:27:30Z", "Chernow's *Washington: A Life*."),
       (OWNER, "2026-09-09T12:00:00Z", "One more?"),
       ("iris", "2026-09-09T12:00:03Z", "Searching. Try *His Excellency*.")])
full = steps.read(mapping[B])
check("...the logged turn's steps go to the reply whose words hold it",
      (sorted(full), [s["name"] for s in full["m4"]["steps"] if s["type"] == "tool"],
       [d["label"][:9] for d in msgs[3]["did"]]),
      (["m4"], ["WebSearch"], ["WebSearch"]))

_, msgs, _ = threads.read(mapping[C])
check("a log from before `broker.user_message`: the transcript wins",
      ([(m["author"], m["text"]) for m in msgs], sorted(steps.read(mapping[C]))),
      ([(OWNER, "Is the runtime fine?"), ("ada", "Checked: all green.")], ["m2"]))

check("the transcripts and the session files are byte for byte as they were",
      all(digest(p) == d for p, d in ORIGINALS.items()), True)

with redirect_stdout(io.StringIO()):
    second = importer.run()
check("a second run imports nothing",
      (second["imported"], second["already"], len(threads.imported()),
       len(list(CONV.glob("*(2).md")))), (0, 3, 3, 3))


# A broker older than this code drops `imported_from` from a record it
# rewrites; the state file still says the chat is imported.
_p = threads.read(mapping[C])[2]
_whole = _p.read_text()
_p.write_text(_whole.replace(f"imported_from: {C}\n", ""))
threads._CACHE.clear()
with redirect_stdout(io.StringIO()):
    kept = importer.run()
check("a record whose frontmatter lost `imported_from` is still not imported twice",
      (kept["imported"], threads.imported().get(C)), (0, mapping[C]))
_p.write_text(_whole)

# What the chat says after its import arrives on the next run, once.
with (cfg.SESSION_DIR / f"{A}.jsonl").open("a") as fh:
    for i, e in enumerate([user("One more: shade?"),
                           say("Mint likes shade.", "2026-10-02T09:00:04Z"), RESULT], 100):
        fh.write(json.dumps({**e, "seq": i, "session_id": A}) + "\n")
ORIGINALS[cfg.SESSION_DIR / f"{A}.jsonl"] = digest(cfg.SESSION_DIR / f"{A}.jsonl")
with redirect_stdout(io.StringIO()):
    caught = importer.run()
_, msgs, _ = threads.read(mapping[A])
check("a later run adds what the chat said since, after what was imported",
      (caught["imported"], caught["caught_up"][0][2],
       [(m["id"], m["author"], m["text"]) for m in msgs[5:]]),
      (0, 2, [("m6", OWNER, "One more: shade?"), ("m7", "ada", "Mint likes shade.")]))
check("...with its full record, and still read to its end",
      ("m7" in steps.read(mapping[A]), threads.unread(mapping[A], msgs)), (True, 0))
with redirect_stdout(io.StringIO()):
    again = importer.run()
check("...and only once", (again["caught_up"], len(threads.read(mapping[A])[1])), ([], 7))


# -- through the broker ----------------------------------------------------------

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
    def __init__(self, agent, sid):
        self.agent, self.id, self.provider, self.model = agent, sid, "claude-code", None
        self.subs = set()
        self.prompts = []
        self.background_tasks = []

    def save(self):
        pass

    def watch(self, q):
        self.subs.add(q)

    def unwatch(self, q):
        self.subs.discard(q)

    def working_behind(self):
        return False

    async def send_message(self, text, attachments=None):
        self.prompts.append(text)

        async def play():
            for ev in ({"type": "assistant", "parent_tool_use_id": None,
                        "message": {"content": [{"type": "text",
                                                 "text": f"{self.agent} here"}]}},
                       {"type": "result", "parent_tool_use_id": None}):
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
        self.starts.append((agent, session_id, resume))
        s = FakeSession(agent, f"s{len(self.starts)}")
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
    for _ in range(300):
        await asyncio.sleep(0.01)
        if server.threads.settled(tid):
            return


async def main():
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = FakeBroker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    tid = mapping[A]

    _, body = await call(server, "GET", "/v1/threads")
    check("the thread list leaves the imported chats out",
          [r["id"] for r in body["threads"] if r.get("imported_from")], [])
    _, body = await call(server, "GET", "/v1/threads", params={"archived": "1"})
    rows = {r["id"]: r for r in body["threads"] if r.get("imported_from")}
    check("the archive view lists all three, archived, with their source",
          (len(rows), rows[tid]["archived"], rows[tid]["source"], rows[tid]["unread"]),
          (3, True, "15_CONVERSATIONS/Planning the garden.md", 0))

    status, body = await call(server, "GET", "/v1/threads/imported")
    check("GET /v1/threads/imported maps every session to its thread",
          (status, body["imported"]), (200, mapping))
    status, body = await call(server, "GET", "/v1/threads/imported",
                              params={"session": A})
    check("...?session= answers one", (status, body), (200, {"session": A, "thread": tid}))
    status, _ = await call(server, "GET", "/v1/threads/imported",
                           params={"session": "nope"})
    check("...and 404 for a session never imported", status, 404)

    status, body = await call(server, "GET", f"/v1/threads/{tid}")
    check("the thread opens on its history",
          (status, [m["author"] for m in body["messages"]]),
          (200, [OWNER, "ada", OWNER, "ada", "ada", OWNER, "ada"]))
    status, body = await call(server, "GET", f"/v1/threads/{tid}/steps",
                              params={"message": "m2"})
    check("...and its full record answers for a reply",
          (status, body["steps"][0]["message"]), (200, "m2"))

    status, _ = await call(server, "POST", f"/v1/threads/{tid}/messages",
                           {"text": "Picking this back up."})
    await settle(server, tid)
    meta, msgs, _ = threads.read(tid)
    check("the owner writing in it takes it out of the archive",
          (status, meta["archived"], meta["imported_from"]), (200, False, A))
    # A fresh session is handed the recent exchange and the file, not the
    # imported history (2026-11-11): the chat was long ago, so the window is
    # The owner's new message, and the rest is the file's.
    first = fake.sessions[fake.starts and "s1"].prompts[0]
    check("...and its persona answers, in a fresh session pointed at the history",
          ([m["author"] for m in msgs[-2:]], msgs[-1]["text"], fake.starts[-1][0],
           "Picking this back up." in first, "What should I plant?" in first,
           str(threads.record_path(tid)) in first),
          ([OWNER, "ada"], "ada here", "ada", True, False, True))
    _, body = await call(server, "GET", "/v1/threads")
    check("...and it is on the thread list again",
          tid in [r["id"] for r in body["threads"]], True)

    # Once the owner has written in it, the chat is no longer its record.
    with (cfg.SESSION_DIR / f"{A}.jsonl").open("a") as fh:
        for i, e in enumerate([user("Classic again"), say("Classic reply", "2026-10-03T09:00:00Z"),
                               RESULT], 200):
            fh.write(json.dumps({**e, "seq": i, "session_id": A}) + "\n")
    n = len(threads.read(tid)[1])
    with redirect_stdout(io.StringIO()):
        later = importer.run()
    check("a thread the owner has written in gains nothing from its old chat",
          (later["caught_up"], len(threads.read(tid)[1])), ([], n))

    # A thread archived by hand is not an imported chat: unchanged rule.
    status, row = await call(server, "POST", "/v1/threads", {"persona": "iris"})
    plain = row["id"]
    await call(server, "PATCH", f"/v1/threads/{plain}", {"archived": True})
    await call(server, "POST", f"/v1/threads/{plain}/messages", {"text": "note"})
    await settle(server, plain)
    check("a thread archived by hand stays archived when written in",
          threads.read(plain)[0]["archived"], True)

    # Re-archived, it stays imported: the next run still passes over it.
    await call(server, "PATCH", f"/v1/threads/{tid}", {"archived": True})
    with redirect_stdout(io.StringIO()):
        third = importer.run()
    check("re-archived after use, a third run still imports nothing",
          (third["imported"], threads.read(tid)[0]["imported_from"]), (0, A))


asyncio.run(main())

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
