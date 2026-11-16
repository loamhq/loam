#!/usr/bin/env python3
"""What an older record can be told by its own log, and who the agents are.

Two things, both found in a real browser at the deployed app on 2026-09-18:

  1. **Every row read "New chat" and none had a status box.** A record
     written before that morning never had `title` or `last_message`
     written, and `normalise` fills the keys without filling the content.
     The conversation is on disk -- the durable event log is the whole point
     of the log -- so the three fields are derived from it once, at start,
     and written back. Proved here against real logs in a real state
     directory, through the real `Server.route`.

  2. **`GET /v1/agents` answered bare strings**, so a page drawing an
     agent's name and mark had to invent both. It answers objects now:
     `{"id", "name", "emoji"}`, read from the persona's own `agent.json`,
     which is where the runtime states them. A breaking change to that
     route's shape, and the one route on this interface that has had one.

No model call is spent: the titler does not run here. A backfilled title is
the opening words of the first message by rule -- the titler names a session
after a turn it watched, and nothing can watch a turn that ended on Tuesday.

Run: python3 tests/test_broker_backfill.py
"""

import asyncio
import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
# Admission reads no real box here: a fixture session must not be refused
# because the machine running the suite is short of memory (a landing
# got 429 for one, 2026-11-09). test_broker_seats is where admission is tested.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


class FakeWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None

    def body(self):
        head, _, payload = b"".join(self.chunks).decode().partition("\r\n\r\n")
        return int(head.split()[1]), json.loads(payload)


async def route(server, method, path, params=None, payload=None):
    writer = FakeWriter()
    try:
        await server.route(method, path, params or {}, payload or {}, writer)
    except broker_mod.BrokerError as exc:
        return exc.status, {"error": exc.message}
    return writer.body()


# -- the events, in the shapes the CLI and the broker really write ---------


def said(text, parent=None, seq=0):
    return {"type": "assistant", "parent_tool_use_id": parent, "seq": seq,
            "message": {"model": "claude-fable-5-1",
                        "content": [{"type": "text", "text": text}]}}


def asked(text, seq=0):
    return {"type": "broker.user_message", "session_id": "x", "text": text,
            "seq": seq}


def tool_result(content, seq=0):
    """A `user` event, which is what the CLI calls a tool result. Every one
    of them in three real sessions read end to end on 2026-09-16."""
    return {"type": "user", "seq": seq,
            "message": {"role": "user",
                        "content": [{"tool_use_id": "toolu_01",
                                     "type": "tool_result",
                                     "content": content}]}}


def result(seq=0, is_error=False, subtype="success"):
    return {"type": "result", "subtype": subtype, "is_error": is_error,
            "total_cost_usd": 0.02, "seq": seq}


def write_session(session_id: str, record: dict, events: list) -> None:
    """A record and its log, as an older broker left them."""
    meta = {"session_id": session_id, "agent": "ada", "state": "exited",
            "workspace": "/home/loam/ai-labs/ada",
            "created": "2026-09-16T16:00:00Z",
            "last_activity": "2026-09-16T16:05:00Z",
            "record_seq": len(events),
            "turns": [], "pending_permissions": []}
    meta.update(record)
    if "seq" in record:      # a record written before the 09-16 rename
        meta.pop("record_seq")
    (cfg.SESSION_DIR / f"{session_id}.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")
    with (cfg.SESSION_DIR / f"{session_id}.jsonl").open("w",
                                                        encoding="utf-8") as fh:
        for n, event in enumerate(events, start=1):
            event = dict(event, seq=n)
            fh.write(json.dumps(event) + "\n")


FIRST = ("Could you look at the deployment layer and tell me whether the "
         "wall is still where we left it on Tuesday?")
OPENING = "Could you look at the deployment layer and tell me whether…"


async def main() -> None:
    # A session from this morning: a person spoke, the agent answered, the
    # turn succeeded -- and the record, written by the broker of the day,
    # says none of it.
    write_session("modern", {"turns": [{"ts": "2026-09-16T16:05:00Z"}]}, [
        asked(FIRST),
        said("The wall is where it was."),
        said("from a subagent", parent="toolu_01"),
        said("And the deploy layer renders it."),
        result(),
    ])
    # The same, ended by an error.
    write_session("errored", {}, [
        asked("do the thing"), said("trying"),
        result(is_error=True, subtype="error_during_execution"),
    ])
    # A probe session from 2026-09-16, before broker.user_message existed:
    # the log holds the agent's side and tool results, and nothing a person
    # said. Its title stays null rather than becoming a file the agent read.
    write_session("older", {"turns": [{}]}, [
        {"type": "system", "subtype": "init"},
        tool_result("1\tTHISTLE\n2\t"),
        said("THISTLE, and the canary is where you left it."),
        result(),
    ])
    # A log with no broker.user_message but a `user` event that is prose --
    # the shape a client replaying user messages would write. Prose from a
    # person is a title; a tool result is not.
    write_session("prose", {}, [
        {"type": "user", "message": {"role": "user", "content": FIRST}},
        said("It is."), result(),
    ])
    # Started and killed before anything was said. Nothing to backfill, and
    # the row says "nothing said yet" rather than inventing a name for it.
    write_session("silent", {"exit_code": 143},
                  [{"type": "broker.exit", "exit_code": 143}])
    # A record whose position is behind its own log: the duplicate-numbering
    # defect of 2026-09-16, still on this box as a probe session that says
    # `seq: 1` over a log ending at 4. Resumed as it stands, it renumbers
    # from 1 and the client's whole reconnect mechanism is a lie.
    write_session("behind", {"seq": 1}, [
        asked("hello"), said("hi"), result(), {"type": "broker.exit"},
    ])
    # What a person and a live broker already put on the record outranks
    # anything re-derived from a log.
    write_session("owned", {"title": "Tuesday's wall", "status": "failed",
                            "last_message": "what the broker recorded"}, [
        asked(FIRST), said("something else entirely"), result(),
    ])

    seen, changed = broker_mod.backfill_records()
    check("every record on the box is looked at", seen, 7)
    check("...and only the ones with something to fill are rewritten",
          changed, 5)

    def record(name):
        return json.loads(
            (cfg.SESSION_DIR / f"{name}.json").read_text(encoding="utf-8"))

    check("the title is the opening words of what the person said",
          record("modern")["title"], OPENING)
    check("...cut at a word, never mid-word",
          record("modern")["title"].rstrip("…").split()[-1], "whether")
    check("the last message is the last top-level assistant text",
          record("modern")["last_message"],
          "And the deploy layer renders it.")
    check("...and a subagent's sentence is not it",
          "subagent" in (record("modern")["last_message"] or ""), False)
    check("a log whose last result succeeded is done",
          record("modern")["status"], "done")
    check("...and one whose last result errored is failed",
          record("errored")["status"], "failed")

    check("a log from before broker.user_message has no person's words in it",
          record("older").get("title"), None)
    check("...but it does have the agent's, so the row is not empty",
          record("older")["last_message"],
          "THISTLE, and the canary is where you left it.")
    check("...and a tool result is never read as a person speaking",
          "THISTLE" in json.dumps(record("older").get("title")), False)
    check("a `user` event that is prose is a person speaking",
          record("prose")["title"], OPENING)

    check("a session where nothing was said stays nameless",
          (record("silent").get("title"),
           record("silent").get("last_message")),
          (None, None))
    check("...and the record is left alone rather than filled with nulls",
          broker_mod.backfill(record("silent"),
                              cfg.SESSION_DIR / "silent.jsonl"), False)
    check("...and is idle, not done", broker_mod.normalise(
        record("silent"))["status"], "idle")

    check("a record behind its own log is moved to the log's end",
          record("behind")["record_seq"], 4)
    check("...under the one name the position has had since 2026-09-16",
          "seq" in record("behind"), False)

    check("a name a person gave the session is not overwritten",
          record("owned")["title"], "Tuesday's wall")
    check("...nor a last message the broker recorded",
          record("owned")["last_message"], "what the broker recorded")
    check("...nor a status it stored", record("owned")["status"], "failed")

    # Paid once. A second start has nothing left to do, which is what makes
    # this affordable at every boot with several hundred sessions on disk.
    check("a second start finds nothing left to fill",
          broker_mod.backfill_records()[1], 0)

    # And it is what the listing answers, which is the point of all of it.
    broker = broker_mod.Broker()
    server = broker_mod.Server(broker)
    listed = {r["session_id"]: r
              for r in (await route(server, "GET", "/v1/sessions"))[1]["sessions"]}
    check("the listing carries the titles",
          listed["modern"]["title"], OPENING)
    check("...and the last messages",
          listed["errored"]["last_message"], "trying")
    check("...and the statuses",
          [listed[n]["status"] for n in ("modern", "errored", "silent")],
          ["done", "failed", "idle"])

    # -- 2. the agents, who have names -------------------------------------
    status, answer = await route(server, "GET", "/v1/agents")
    check("GET /v1/agents is an object with an `agents` key",
          (status, list(answer)), (200, ["agents"]))
    check("...holding objects, not the bare strings it answered before",
          answer["agents"][0],
          {"id": "ada", "name": "Ada", "emoji": "🧱", "face": None})
    check("...sorted by id, as the roster always was",
          [a["id"] for a in answer["agents"]],
          ["ada", "nameless", "plain"])
    check("an agent.json with no display falls back to the capitalised id",
          [a for a in answer["agents"] if a["id"] == "nameless"][0],
          {"id": "nameless", "name": "Nameless", "emoji": "🌳", "face": None})
    check("an agent.json with no emoji says null, rather than a guess",
          [a for a in answer["agents"] if a["id"] == "plain"][0],
          {"id": "plain", "name": "Plain", "emoji": None, "face": None})
    # `face` (2026-11-14) is an uploaded image's route and digest, null
    # without one: never the workspace's path.
    check("nothing else from agent.json leaves the box",
          sorted(answer["agents"][0]), ["emoji", "face", "id", "name"])
    check("the roster of ids is unchanged, and is what a session starts by",
          cfg.roster(), ["ada", "nameless", "plain"])


root = Path(tempfile.mkdtemp(prefix="loam-broker-backfill-test-"))
saved = {name: getattr(cfg, name) for name in
         ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "USAGE_PATH", "AI_LABS")}
try:
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.USAGE_PATH = cfg.STATE_DIR / "provider-usage.json"
    cfg.SESSION_DIR.mkdir(parents=True)
    cfg.AI_LABS = root / "agents"
    for name, config in (
            ("ada", {"name": "ada", "display": "Ada", "emoji": "🧱",
                      "wake_schedule": "03:20"}),
            ("nameless", {"name": "nameless", "emoji": "🌳"}),
            ("plain", {"name": "plain", "display": "Plain"})):
        (cfg.AI_LABS / name).mkdir(parents=True)
        (cfg.AI_LABS / name / "agent.json").write_text(
            json.dumps(config), encoding="utf-8")

    asyncio.run(main())
finally:
    for name, value in saved.items():
        setattr(cfg, name, value)
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
