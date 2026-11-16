#!/usr/bin/env python3
"""Adopting the sessions the owner had in the Claude Code app, as records.

Those conversations are Claude Code sessions on this box -- the Remote
Control servers run them as the same login in the same persona workspace --
so adopting one is reading a file that is already here, and the three things
that can go wrong are all things this proves cannot:

  1. **Adopting something that was never a conversation.** A wake, a cron, a
     probe and a broker-driven turn all leave a session file in the same
     folder. What separates them is the session file's own word for it:
     `origin: {"kind": "human"}` on a prompt a person typed, and nothing at
     all on the rest.

  2. **Adopting a conversation that is two weeks stale and putting it at the
     top of the home screen.** The filesystem's mtime is not when a session
     was last spoken to: measured on this box 2026-09-20, three sessions
     whose last entry is from 09-04, 09-05 and 08-28 carry mtimes of 09-20
     01:31, 09-08 01:30 and 09-09 01:30. The window is judged on the last
     entry's own timestamp, and mtime is only a cheap first cut because it
     always runs late.

  3. **Adopting a session twice**, which is what would make the route unsafe
     to run at every broker start. A session the broker already holds is
     left as it is or refreshed in place, and never made a second time.

The scan, which is what this file is about. What the record then replays out
of that session file is `test_broker_replay_app.py`, beside it; the two meet
in one place only, and it is asserted here: the count the scan writes into
`record_seq` is the number of events the replay produces. The fourth thing
that can go wrong -- resuming an app session in place, on top of a Remote
Control server that may still be running -- is `test_broker_fork.py`, which
is where the code that forks is; the launch line is checked here too,
because a scan that produced a record something would resume in place is a
scan that did harm.

Run: python3 tests/test_broker_adopt.py
"""

import asyncio
import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_adopt as adoption  # noqa: E402
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

    def text(self) -> str:
        return b"".join(self.chunks).decode()

    def body(self):
        head, _, payload = self.text().partition("\r\n\r\n")
        return int(head.split()[1]), json.loads(payload)

    def items(self) -> list[dict]:
        return [json.loads(line[len("data: "):])
                for line in self.text().splitlines()
                if line.startswith("data: ")]


async def route(server, method, path, params=None, payload=None):
    writer = FakeWriter()
    try:
        await server.route(method, path, params or {}, payload or {}, writer)
    except broker_mod.BrokerError as exc:
        return exc.status, writer, {"error": exc.message}
    if writer.text().startswith("HTTP/1.1 200 OK\r\nContent-Type: text/event"):
        return 200, writer, None
    status, body = writer.body()
    return status, writer, body


# -- session files, in the shapes this box's own really carry -------------
#
# Read off real files on 2026-09-20 and not invented: a person's prompt has
# `promptSource` and `origin: {"kind": "human"}`, a wake's has
# `promptSource: "sdk"` and no origin at all, a tool result is a `user`
# entry with no prompt source, and the bookkeeping entries (`ai-title`,
# `last-prompt`, `attachment`) carry no timestamp worth reading.

def person(text, ts):
    return {"type": "user", "promptSource": "sdk", "origin": {"kind": "human"},
            "isSidechain": False, "timestamp": ts,
            "message": {"role": "user", "content": text}}


def machine(text, ts):
    """A wake, a cron or a probe: a prompt nobody typed."""
    return {"type": "user", "promptSource": "sdk", "isSidechain": False,
            "timestamp": ts, "message": {"role": "user", "content": text}}


def spoke(blocks, ts, model="claude-fable-5-1", sidechain=False):
    return {"type": "assistant", "isSidechain": sidechain, "timestamp": ts,
            "message": {"role": "assistant", "model": model,
                        "content": blocks}}


def tool_result(text, ts):
    return {"type": "user", "isSidechain": False, "timestamp": ts,
            "message": {"role": "user",
                        "content": [{"type": "tool_result",
                                     "tool_use_id": "toolu_01",
                                     "content": text}]}}


def stamped(entries: list, prefix: str) -> list:
    """The entries with the uuid the CLI puts on each of them.

    The prefix is the CONVERSATION's and not the file's, deliberately: a
    fork's session file carries its parent's uuids unchanged (measured on
    this box 2026-09-22 -- only `sessionId` is rewritten), and that is the
    whole of how a scan recognises a copy of something it already holds.
    """
    return [dict(entry, uuid=f"{prefix}-{i:04d}")
            for i, entry in enumerate(entries)]


def write_file(folder: Path, session_id: str, entries: list,
               mtime: float | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
        # The bookkeeping the CLI leaves at the end of a real file, which is
        # why the last line often has no timestamp on it.
        handle.write(json.dumps({"type": "last-prompt",
                                 "lastPrompt": "..."}) + "\n")
    if mtime is not None:
        import os
        os.utime(path, (mtime, mtime))
    return path


DAY = 86400
NOW = time.time()


def ago(days: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(NOW - days * DAY))


# A conversation: two turns, a tool call and its result, a thinking block
# and a subagent's sentence that must not appear.
CONVERSATION = [
    person("what did the market watch turn up?", ago(3)),
    spoke([{"type": "thinking", "thinking": "private"},
           {"type": "text", "text": "Let me look."},
           {"type": "tool_use", "id": "toolu_01", "name": "Read",
            "input": {"file_path": "/notes.md"}}], ago(3)),
    tool_result("the notes", ago(3)),
    spoke([{"type": "text", "text": "Two things stood out."}], ago(3)),
    spoke([{"type": "text", "text": "a subagent's sentence"}], ago(3),
          sidechain=True),
    person("which one matters most?", ago(2)),
    spoke([{"type": "text", "text": "The second."}], ago(2),
          model="claude-opus-5"),
]

WAKE = [
    machine("You're waking. It's 2026-09-20T10:20:57Z. This time is yours.",
            ago(1)),
    spoke([{"type": "text", "text": "I read my notes."}], ago(1)),
]

# Last spoken to a month ago; touched by whatever touches these files today.
STALE = [
    person("a conversation from last month", ago(30)),
    spoke([{"type": "text", "text": "quite."}], ago(30)),
]


async def main() -> None:
    ada = cfg.workspace("ada")
    folder = adoption.project_dir(ada)
    app_id = "11111111-1111-4111-8111-111111111111"
    write_file(folder, app_id, stamped(CONVERSATION, "market"))
    write_file(folder, "22222222-2222-4222-8222-222222222222", WAKE)
    # Stale by its own last entry, fresh by mtime. This is the one the
    # filesystem lies about.
    write_file(folder, "33333333-3333-4333-8333-333333333333", STALE,
               mtime=NOW)
    # A session the broker started itself, which has a session file in the
    # same folder like everything else the CLI runs. It is already held, it
    # is not from the app, and the scan must leave it exactly as it is.
    held_id = "44444444-4444-4444-8444-444444444444"
    write_file(folder, held_id, CONVERSATION)
    held_record = cfg.SESSION_DIR / f"{held_id}.json"
    held_record.write_text(json.dumps({
        "session_id": held_id, "agent": "ada", "state": "exited",
        "workspace": str(ada), "status": "done", "record_seq": 2,
        "title": "a session of the broker's own", "turns": [],
        "pending_permissions": []}), encoding="utf-8")
    held_before = held_record.read_text(encoding="utf-8")

    # The transcript the writer filed for the adopted session, and its
    # sidecar -- the record wears the name the vault wears.
    conversations = Path(tempfile.mkdtemp()) / "15_CONVERSATIONS"
    conversations.mkdir(parents=True)
    filed = conversations / "What the market watch turned up.md"
    filed.write_text('---\ntitle: "What the market watch turned up"\n'
                     'created_at: 2026-09-17T00:00:00Z\n---\n\n',
                     encoding="utf-8")
    adoption.TRANSCRIPT_STATE.mkdir(parents=True, exist_ok=True)
    (adoption.TRANSCRIPT_STATE / f"{app_id}.json").write_text(
        json.dumps({"path": str(filed)}), encoding="utf-8")

    broker = broker_mod.Broker()
    server = broker_mod.Server(broker)

    # -- the scan ---------------------------------------------------------
    status, _, report = await route(server, "POST", "/v1/sessions/adopt")
    check("the adopt route answers", status, 200)
    check("...having adopted the conversation and nothing else",
          [r["session_id"] for r in report["adopted"]], [app_id])
    check("...counted per persona", report["agents"], {"ada": 1})
    check("...and says what it passed over, and why",
          sorted((s["session_id"][:8], s["why"]) for s in report["skipped"]),
          [("22222222", "no human turn (a wake, a cron or a probe)"),
           ("44444444", "already held")])
    # The stale one is not in `skipped` because it is not a near miss: it is
    # outside the window, like the hundreds of older files beside it.
    check("...a session stale by its last entry is not adopted on its mtime",
          "33333333-3333-4333-8333-333333333333" in
          [r["session_id"] for r in report["adopted"]], False)
    check("...and a record the broker already held is untouched",
          held_record.read_text(encoding="utf-8"), held_before)

    status, _, record = await route(server, "GET", f"/v1/sessions/{app_id}")
    check("the record says where it came from", record["origin"],
          "claude-code-app")
    check("...and which session file it was read out of",
          record["source"], str(folder / f"{app_id}.jsonl"))
    check("...is an ended conversation, not a failed one",
          (record["state"], record["status"]), ("exited", "done"))
    check("...belongs to the persona whose workspace it was found in",
          record["agent"], "ada")
    check("...wears the name the transcript writer filed it under",
          record["title"], "What the market watch turned up")
    check("...says the last thing the persona said", record["last_message"],
          "The second.")
    check("...is dated by the conversation and not by the scan",
          (record["created"], record["last_event_at"]),
          (ago(3), ago(2)))
    check("...invents no spend it never saw", record["turns"], [])
    # The position field is the count of what the file replays, so the first
    # turn taken here later is numbered after that history and not on top of
    # it. The translation itself is tested in test_broker_replay_app.py.
    check("...says how much of it comes out of the app's own file",
          (record["replayed"], record["record_seq"]), (9, 9))
    check("...and opens with the conversation in it",
          [e["seq"] for e in broker.load(app_id).replay(0)],
          list(range(1, 10)))

    # -- idempotence ------------------------------------------------------
    before = (cfg.SESSION_DIR / f"{app_id}.json").read_text(encoding="utf-8")
    status, _, again = await route(server, "POST", "/v1/sessions/adopt")
    check("adopting twice adopts nothing twice", again["adopted"], [])
    check("...it refreshes the record instead",
          [r["session_id"] for r in again["refreshed"]], [app_id])
    check("...and the record is byte-identical",
          (cfg.SESSION_DIR / f"{app_id}.json").read_text(encoding="utf-8"),
          before)

    # -- a person's rename outranks the scan ------------------------------
    # The rename route (PATCH /v1/sessions/<id>) retired with the classic
    # view on 2026-11-12, but records renamed before then carry
    # `title_source: person`, and the scan must still leave them alone.
    renaming = broker.load(app_id)
    renaming.title, renaming.title_source = "The owner's own name for it", "person"
    renaming._titled = True
    renaming.save()
    await route(server, "POST", "/v1/sessions/adopt")
    _, _, renamed = await route(server, "GET", f"/v1/sessions/{app_id}")
    check("a refresh does not rename a session a person renamed",
          renamed["title"], "The owner's own name for it")

    # What brings an adopted one back is a fork, never a resume in place:
    # `claude --resume <the app's id>` would be a second process on a session
    # file the app may still be writing. Asserted on the launch line rather
    # than by starting anything, because a scan test has no business
    # launching a CLI; the fork itself is test_broker_fork.py.
    check("the launch line for an adopted session forks it, never resumes it",
          broker.load(app_id).argv(resume=True)[-3:],
          ["--resume", app_id, "--fork-session"])
    check("...and the listing answers `loam-ui` for a record with no origin",
          {r["session_id"]: r["origin"] for r in broker.known()}[held_id],
          "loam-ui")
    # -- the fork of an adopted session is not a second conversation ------
    #
    # Replying to an adopted session forks it: the CLI copies the
    # conversation into a session file of its own, in this same folder, with
    # a person in it and today's date on it. Every test above passes, so
    # until 2026-09-22 the next scan adopted it -- and the home screen drew
    # "Loam phase six planning" twice, once pinned and once in Recent.
    fork_id = "55555555-5555-4555-8555-555555555555"
    fork_file = write_file(folder, fork_id, stamped(CONVERSATION, "market"))
    record_path = cfg.SESSION_DIR / f"{app_id}.json"
    meta = json.loads(record_path.read_text(encoding="utf-8"))
    meta["cli_session_id"] = fork_id
    meta["forked_from"] = app_id
    record_path.write_text(json.dumps(meta), encoding="utf-8")

    status, _, forked = await route(server, "POST", "/v1/sessions/adopt")
    check("the fork this broker drives is not adopted", forked["adopted"], [])
    check("...and the scan says so by name",
          sorted((s["session_id"][:8], s["why"]) for s in forked["skipped"]
                 if s["session_id"].startswith("55555555")),
          [("55555555", "the fork of a session already held")])
    check("...so the conversation is one record and not two",
          [r["session_id"] for r in broker.known()
           if r["session_id"] in (app_id, fork_id)], [app_id])

    # The second net: the same conversation copied into a file NO record
    # names -- a fork the CLI minted for a turn that died before the broker
    # read its id back. Nothing but the entries themselves says what it is,
    # and the entries are the parent's, uuids and all.
    orphan_id = "66666666-6666-4666-8666-666666666666"
    write_file(folder, orphan_id, stamped(CONVERSATION, "market"))
    status, _, orphaned = await route(server, "POST", "/v1/sessions/adopt")
    check("a copy of a held conversation is not adopted either",
          orphaned["adopted"], [])
    check("...and that one is named too",
          sorted((s["session_id"][:8], s["why"]) for s in orphaned["skipped"]
                 if s["session_id"].startswith("66666666")),
          [("66666666", "a copy of a conversation already held")])
    # A conversation the broker has never seen still is adopted: the nets are
    # under forks, not under every file that turns up after the first scan.
    other_id = "77777777-7777-4777-8777-777777777777"
    write_file(folder, other_id, stamped(CONVERSATION, "another"))
    status, _, fresh = await route(server, "POST", "/v1/sessions/adopt")
    check("a conversation that is nobody's copy is still adopted",
          [r["session_id"] for r in fresh["adopted"]], [other_id])

    # -- and the duplicates already made are removed, once -----------------
    #
    # Four of them on this box on 2026-09-22, made between the fork landing
    # on 09-20 and the scan learning about it. `prune_fork_duplicates` runs
    # at broker start, before the scan above.
    duplicate = cfg.SESSION_DIR / f"{fork_id}.json"
    duplicate.write_text(json.dumps({
        "session_id": fork_id, "agent": "ada", "state": "exited",
        "status": "done", "origin": adoption.ORIGIN_APP,
        "source": str(fork_file), "record_seq": 9, "replayed": 9,
        "cli_session_id": None, "forked_from": None,
        "title": "What the market watch turned up", "turns": [],
        "pending_permissions": []}), encoding="utf-8")
    # A record that IS a fork of another and has been spoken into here is not
    # a duplicate of anything: it is a conversation somebody went on having.
    spoken_id = "88888888-8888-4888-8888-888888888888"
    spoken_file = write_file(folder, spoken_id, stamped(CONVERSATION, "spoken"))
    (cfg.SESSION_DIR / f"{spoken_id}.json").write_text(json.dumps({
        "session_id": spoken_id, "agent": "ada", "state": "exited",
        "status": "done", "origin": adoption.ORIGIN_APP,
        "source": str(spoken_file), "record_seq": 9,
        "title": "a conversation with a log of our own", "turns": [],
        "pending_permissions": []}), encoding="utf-8")
    (cfg.SESSION_DIR / f"{spoken_id}.jsonl").write_text(
        json.dumps({"type": "broker.user_message", "seq": 10}) + "\n",
        encoding="utf-8")
    # Named as a fork by a record of its own, so that the only thing keeping
    # it is the log this broker wrote into it.
    (cfg.SESSION_DIR / f"{other_id}.json").write_text(
        json.dumps(dict(json.loads(
            (cfg.SESSION_DIR / f"{other_id}.json").read_text(encoding="utf-8")),
            cli_session_id=spoken_id)), encoding="utf-8")
    fork_bytes = fork_file.read_bytes()

    removed = broker_mod.prune_fork_duplicates()
    check("the duplicate record of a fork is removed, with its name",
          removed, [(fork_id, "What the market watch turned up")])
    check("...the record file is gone", duplicate.exists(), False)
    check("...the conversation it was a copy of stays",
          record_path.exists(), True)
    check("...the CLI's own file is untouched",
          fork_file.read_bytes(), fork_bytes)
    check("...a fork somebody has spoken into here is not removed",
          (cfg.SESSION_DIR / f"{spoken_id}.json").exists(), True)
    check("...and running it again removes nothing",
          broker_mod.prune_fork_duplicates(), [])
    status, _, after = await route(server, "POST", "/v1/sessions/adopt")
    check("...and the scan does not make it again",
          [r["session_id"] for r in after["adopted"]], [])



root = Path(tempfile.mkdtemp(prefix="broker-adopt-"))
saved = {name: getattr(cfg, name)
         for name in ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "USAGE_PATH",
                      "AI_LABS")}
saved_adopt = {name: getattr(adoption, name)
               for name in ("PROJECTS", "TRANSCRIPT_STATE")}
try:
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.USAGE_PATH = cfg.STATE_DIR / "provider-usage.json"
    cfg.SESSION_DIR.mkdir(parents=True)
    cfg.AI_LABS = root / "agents"
    adoption.PROJECTS = root / "projects"
    adoption.TRANSCRIPT_STATE = root / "transcripts"
    (cfg.AI_LABS / "ada").mkdir(parents=True)
    (cfg.AI_LABS / "ada" / "agent.json").write_text(
        json.dumps({"name": "ada", "display": "Ada", "emoji": "🧱"}),
        encoding="utf-8")

    asyncio.run(main())
finally:
    for name, value in saved.items():
        setattr(cfg, name, value)
    for name, value in saved_adopt.items():
        setattr(adoption, name, value)
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
