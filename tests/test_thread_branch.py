#!/usr/bin/env python3
"""Exercise branch replies from the broker API through the first agent turn."""

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
        answer = ("I saw that the thyme needs shade."
                  if "> The thyme needs shade." in prompt else "ok")
        script, self.script = self.script or [text(answer), result(0)], []

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
    threads.append(tid, OWNER, "Earlier gardening context")
    system = threads.append(tid, "system", "A system line", kind="system")
    earlier = "The thyme needs shade.  Two spaces stay.\n\nMarkdown: **thyme**."
    closing = "Water at dawn.\n" + "Full source text. " * 260 + "Keep the final line. -->"
    source = threads.append(tid, "iris", closing, segments=[{"text": earlier}])
    threads.append(tid, "ada", "A later aside")
    await call(server, "GET", f"/v1/threads/{tid}")
    path = threads.record_path(tid)
    before = path.read_bytes()
    branch_route = f"/v1/threads/{tid}/branch"
    for mid, persona, expected in (("m999", "ada", 404), (system["id"], "ada", 400),
                                   (source["id"], "missing", 404)):
        status, _ = await call(server, "POST", branch_route, {"message": mid, "persona": persona})
        check(f"invalid branch {mid}, {persona} is refused", status, expected)

    status, new = await call(server, "POST", branch_route,
                             {"message": source["id"], "persona": "ada"})
    nid = new["id"]
    first_route = f"/v1/threads/{nid}/messages"
    quote = new["draft_reply"]
    check("picker creates an empty composer for the chosen member",
          (status, new["default"], [m["persona"] for m in new["members"]], new["draft"],
           new["branched_from"], threads.read(nid)[1], list(fake.sessions)),
          (201, "ada", ["ada"], None, None, [], []))
    check("draft quote is read from the source record",
          (quote["thread"], quote["id"], quote["text"], quote["at"]),
          (tid, source["id"], earlier + "\n\n" + closing, source["at"]))
    opened = (await call(server, "GET", f"/v1/threads/{nid}"))[1]
    check("reopening an unsent branch restores the quote", opened["draft_reply"], quote)

    cfg.ATTACHMENT_ROOT.mkdir()
    attachment = cfg.ATTACHMENT_ROOT / "notes.txt"
    attachment.write_text("a file alone cannot open a branch")
    payload = {"reply_to": source["id"], "reply_thread": tid, "submission_id": "first"}
    for method, fields in (("PUT", {}), ("PUT", {"attachments": [str(attachment)]}), ("POST", {})):
        status, _ = await call(server, method, first_route, {**payload, **fields, "text": "  "})
        check(f"{method} rejects an empty branch with fields {list(fields)}", status, 400)
    check("refused sends leave no transcript or agent turn",
          (threads.read(nid)[1], list(fake.sessions)), ([], []))

    opening = "How should we water the thyme?"
    payload["text"] = opening
    status, response = await call(server, "PUT", first_route, payload)
    check("send stores the owner's first message and the exact source quote",
          (status, response["message"]["id"], response["message"]["author"],
           response["message"]["reply_to"], response["message"]["reply_source"],
           response["message"]["text"]), (200, "m1", OWNER, source["id"], quote, opening))
    retry = await call(server, "PUT", first_route, payload)
    check("retry returns the same accepted delivery", retry, (status, response))
    status, _ = await call(server, "PUT", first_route, {**payload, "reply_thread": nid})
    check("the idempotency key includes the source thread", status, 409)
    status, _ = await call(server, "PUT", first_route, {**payload, "submission_id": "later"})
    check("a cross-thread quote goes on the first owner message", status, 409)

    await settle(server, nid)
    ada = next(s for s in fake.sessions.values() if s.agent == "ada")
    prompt = ada.prompts[0]
    quoted = "\n".join("> " + line if line else ">" for line in quote["text"].split("\n"))
    check("the answering agent receives the full quote, author, time, title, IDs and path",
          all(part in prompt for part in (quoted, f"Iris's message {source['id']}",
              threads.local_stamp(source["at"]), f'"Garden plans" (id {tid})',
              str(path), "You may read earlier messages", opening)), True)
    check("only the chosen agent wakes, and no source window enters its turn",
          (len(fake.sessions), len(ada.prompts), "Earlier gardening context" in prompt,
           "A later aside" in prompt), (1, 1, False, False))
    check("the provider's answer about the quoted detail is filed in the branch",
          threads.read(nid)[1][-1]["text"], "I saw that the thyme needs shade.")
    check("the thread takes its title from the owner's opening",
          threads.summary(nid)["title"], opening)
    threads._CACHE.clear()
    server.threads = engine_mod.ThreadEngine(fake)
    reloaded = (await call(server, "GET", f"/v1/threads/{nid}"))[1]
    check("the quote and first words survive a new engine and record read",
          (reloaded["messages"][0]["reply_source"], reloaded["messages"][0]["text"],
           reloaded["draft_reply"]), (quote, opening, None))
    check("the Markdown record includes a readable source quote",
          '· in "Garden plans"' in threads.record_path(nid).read_text("utf-8"), True)

    def links():
        return {row["id"]: (row["title"], row["branched_from"])
                for row in threads.listing(archived=True) if row["branched_from"]}

    origin = {"thread": tid, "message": source["id"], "reply": True}
    check("the thread listing supplies an outgoing link for the source message",
          links(), {nid: (opening, origin)})
    _, second = await call(server, "POST", branch_route, {"message": source["id"]})
    sid = second["id"]
    check("opening another composer does not create its back-link", set(links()), {nid})
    await call(server, "PUT", f"/v1/threads/{sid}/messages",
               {**payload, "text": "Another question", "submission_id": "second"})
    await settle(server, sid)
    await call(server, "PATCH", f"/v1/threads/{sid}", {"title": "Second branch renamed", "archived": True})
    check("sibling back-links follow destination renames and survive archiving",
          links(), {nid: (opening, origin), sid: ("Second branch renamed", origin)})
    await call(server, "DELETE", f"/v1/threads/{sid}")
    check("deleting the destination removes just its back-link", links(), {nid: (opening, origin)})

    _, unquoted = await call(server, "POST", branch_route, {"message": source["id"]})
    pid = unquoted["id"]
    await call(server, "PATCH", f"/v1/threads/{pid}", {"draft_reply": None})
    detail = (await call(server, "GET", f"/v1/threads/{pid}"))[1]
    check("removing the quote persists before send", detail["draft_reply"], None)
    await call(server, "PUT", f"/v1/threads/{pid}/messages",
               {"text": "An ordinary new thread", "submission_id": "plain"})
    await settle(server, pid)
    check("sending with the quote removed creates a plain thread",
          (threads.summary(pid)["branched_from"], threads.read(pid)[1][0].get("reply_to"), set(links())),
          (None, None, {nid}))
    check("the source record and read state are untouched by all branches",
          (path.read_bytes() == before, threads.summary(tid)["unread"]), (True, 0))

    # A record written by the previous branch format still provides its original context.
    legacy = threads.create("ada", "Old branch", extra={"branched_from": tid,
        "branched_message": source["id"], "branched_window": [source["id"]]})
    legacy_id = legacy["id"]
    back = {"thread": tid, "message": source["id"], "title": "Garden plans"}
    threads.append(legacy_id, "system", "Branched from Garden plans", kind="system", link=back)
    check("legacy branch metadata, link and first-turn window remain readable",
          (links()[legacy_id][1], threads.read(legacy_id)[1][0]["link"],
           closing in threads.branch_context(legacy)),
          ({"thread": tid, "message": source["id"]}, back, True))

    for words in ("", "An explicitly supplied opening"):
        args = ["--text", words] if words else []
        out = subprocess.run([sys.executable, str(AGENTS / "bin" / "thread"), "branch",
                              tid, source["id"], "--persona", "ada", *args],
                             capture_output=True, text=True, env={**os.environ, "LOAM_AGENT": "iris"})
        got = json.loads(out.stdout)
        handoff = (await call(server, "GET", f"/v1/threads/{got['thread']}"))[1]
        check(f"agent tool opens a quoted composer with explicit text {bool(words)}",
              (out.returncode, handoff["thread"]["draft"], handoff["draft_reply"], handoff["messages"]),
              (0, words or None, quote, []))

    server.threads.stop_all_tails()


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
