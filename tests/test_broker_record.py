#!/usr/bin/env python3
"""What the designed screens read off a session record, and off the box.

The home screen draws a row per session: a title, one word for what it is
doing, the last thing the agent said, how long ago anything happened, a pin
and an archive. None of those existed on the record before 2026-09-18 -- the
record said `state: running | exited` and nothing else -- so the page had a
spinner and no way to say what it was spinning about.

Proved here against real Session objects with a stand-in process and the real
``Server.route``, because the point is what goes out on the wire and not what
a method returns. Seven things, in the brief's order:

  1. a title, from the titler the transcript writer uses, standing in on the
     first message's opening words until it answers
  2. status, last_message and last_event_at
  3. archived records are listed (the classic list's archive filter and
     the pin/archive/rename PATCH retired with the classic view, 2026-11-12)
  4. the live list: one stream of every record that changes
  5. `provider` on the record and on start, and a 400 that says why
  6. GET /v1/providers, including the usage read off a real rate_limit_event
  7. a record whose broker restarted mid-turn does not still say `working`

**No model call is spent here.** The titler is the real one, pointed at a
stub binary through ``LOAM_TITLER_BIN``'s own override point -- which is what
that override exists for -- so the argv, the timeout, the validation and the
fail-soft paths are all exercised and the quota is not.

Run: python3 tests/test_broker_record.py
"""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
os.environ["LOAM_USAGE_READS"] = "off"  # no real CLI reads; see loam_provider_usage
import loam_broker as cfg  # noqa: E402
# Admission reads no real box here: a fixture session must not be refused
# because the machine running the suite is short of memory (a landing
# got 429 for one, 2026-11-09). test_broker_seats is where admission is tested.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_adopt as adoption  # noqa: E402
import loam_transcript  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


# -- stand-ins for the process and the socket ------------------------------


class FakeStdin:
    def __init__(self) -> None:
        self.lines: list[dict] = []

    def write(self, data: bytes) -> None:
        self.lines.append(json.loads(data.decode()))

    async def drain(self) -> None:
        return None


class FakeProc:
    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.returncode = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


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
        """The JSON body of a plain response."""
        head, _, payload = self.text().partition("\r\n\r\n")
        return int(head.split()[1]), json.loads(payload)

    def items(self) -> list[dict]:
        return [json.loads(line[len("data: "):])
                for line in self.text().splitlines()
                if line.startswith("data: ")]

    def comments(self) -> list[str]:
        return [line for line in self.text().splitlines()
                if line.startswith(":")]


def envelope(total=0.01, subtype="success", is_error=False, session_id="s"):
    return {"type": "result", "subtype": subtype, "is_error": is_error,
            "session_id": session_id, "total_cost_usd": total,
            "duration_ms": 2249, "num_turns": 1,
            "usage": {"input_tokens": 4, "output_tokens": 76},
            "permission_denials": []}


def assistant(text: str, parent: str | None = None) -> dict:
    return {"type": "assistant", "parent_tool_use_id": parent,
            "message": {"model": "claude-fable-5-1",
                        "content": [{"type": "text", "text": text}]}}


def permission_request(request_id="req-1") -> dict:
    return {"type": "control_request", "request_id": request_id,
            "request": {"subtype": "can_use_tool", "tool_name": "Read",
                        "input": {"file_path": "/etc/hostname"}}}


# A rate_limit_event copied verbatim off this box's own session log
# (state/broker/sessions/9bea9b02-….jsonl, seq 41, 2026-09-18). Read, not
# guessed -- including the fourth window, which was absent from the same
# session's earlier events and appeared mid-session.
REAL_RATE_LIMIT_EVENT = {
    "type": "rate_limit_event",
    "rate_limit_info": {
        "status": "allowed",
        "resetsAt": 1789690200,
        "rateLimitType": "five_hour",
        "overageStatus": "allowed",
        "overageResetsAt": 1790812800,
        "isUsingOverage": False,
        "unifiedWindows": {
            "five_hour": {"utilization": 0.25, "resetsAt": 1789690200},
            "seven_day": {"utilization": 0.24, "resetsAt": 1790136000},
            "seven_day_overage_included": {"utilization": 0.39,
                                           "resetsAt": 1790136000},
        },
    },
    "uuid": "b0f237a8-fe07-459b-b60e-d532c35545e3",
    "session_id": "9bea9b02-e059-417f-9c70-c27e6eaa7795",
}


def running(broker, agent: str = "probe", session_id: str | None = None,
            provider: str | None = None):
    session = broker.adopt(broker_mod.Session(agent, session_id,
                                              provider=provider))
    session.state = "running"
    session.proc = FakeProc()
    broker.sessions[session.id] = session
    return session


def file_transcript(session_id: str, title: str) -> Path:
    """The transcript writer, as far as the broker can see it.

    What the broker reads is the sidecar the writer keeps under
    ``state/transcripts/<session id>.json`` and the ``title:`` in the
    frontmatter of the file it points at. Both are written here for real --
    a fixture transcript rendered by the writer's own ``render_file`` -- so
    this exercises the reading and not a stub of it.
    """
    turns = [loam_transcript.Turn("user", "surface check",
                                  "2026-09-22T19:00:00Z"),
             loam_transcript.Turn("probe", "checked.", "2026-09-22T19:00:04Z")]
    path = VAULT / f"{title}.md"
    path.write_text(
        loam_transcript.render_file(title, turns, "probe",
                                    session_id=session_id,
                                    surface=loam_transcript.SURFACE_LOAM_UI),
        encoding="utf-8")
    (adoption.TRANSCRIPT_STATE / f"{session_id}.json").write_text(
        json.dumps({"path": str(path), "turns_written": 2, "titled": True}),
        encoding="utf-8")
    return path


async def settled(session) -> None:
    """Wait out the titling task, which polls in a thread."""
    for _ in range(100):
        if not session._titling:
            return
        await asyncio.sleep(0.02)


async def route(server, method, path, params=None, payload=None):
    writer = FakeWriter()
    try:
        await server.route(method, path, params or {}, payload or {}, writer)
    except broker_mod.BrokerError as exc:
        return exc.status, {"error": exc.message}
    return writer.body()


async def main() -> None:
    broker = broker_mod.Broker()
    server = broker_mod.Server(broker)

    # -- 2. a title, and the opening words until the titler answers --------
    session = running(broker)
    check("a session with nothing said yet has no title", session.title, None)
    await session.send_message(
        "Could you look at the deployment layer and tell me whether the wall "
        "is still where we left it on Tuesday?")
    check("the first message's opening words stand in",
          session.title,
          "Could you look at the deployment layer and tell me whether…")
    check("...cut at a word, never mid-word",
          session.title.rstrip("…").split()[-1], "whether")

    stand_in = session.title
    await session._handle(assistant("The wall is where it was."))
    await session._handle(envelope(0.02))
    # The turn ended before the writer had filed anything, which is the normal
    # case: the writer runs on the same Stop hook and its titler is a model
    # call. Nothing to read means the stand-in stays and this is NOT finished.
    await settled(session)
    check("nothing filed yet: the opening words stay", session.title, stand_in)
    check("...and the session is not done being named", session._titled, False)

    # Now the writer files it, which is the fixture transcript.
    file_transcript(session.id, "Attachment check")
    await session.send_message("and the attachment?")
    await session._handle(assistant("It is on the note."))
    await session._handle(envelope(0.02))
    await settled(session)
    check("the vault's title is the session's title",
          session.title, "Attachment check")
    check("...marked as the vault's, so a sync knows not to ask again",
          session.title_source, "vault")
    check("...and it is done being named", session._titled, True)
    check("...having spent no model call of its own on a name",
          STUB_LOG.exists(), False)
    check("...which is what the record carries to another device",
          json.loads(session.meta_path.read_text())["title"],
          "Attachment check")

    # -- 2b. the one-time sync of records the broker's own titler named -----
    #
    # Every record on the box was named by a second model call until
    # 2026-09-22, so the home screen and the vault disagreed. The sync is what
    # those records get, and a rename is what it must never touch.
    renamed = running(broker)
    await renamed.send_message("a conversation somebody named themselves")
    file_transcript(renamed.id, "What the vault calls it")
    # A person's rename, as PATCH /v1/sessions/<id> wrote it until the
    # classic view retired (2026-11-12); records carrying one remain.
    renamed.title, renamed.title_source = "What the owner calls it", "person"
    renamed.save()
    stale = running(broker)
    await stale.send_message("a conversation the old titler named")
    stale.title = "Household note and image attachment"
    stale.save()
    file_transcript(stale.id, "Attachment check")
    seen, changes = broker_mod.sync_titles()
    changed = {session_id: (was, now) for session_id, was, now in changes}
    check("the sync takes the vault's title for a record that disagrees",
          changed.get(stale.id),
          ("Household note and image attachment", "Attachment check"))
    check("...on the record itself",
          json.loads(stale.meta_path.read_text())["title"], "Attachment check")
    check("...and never for a title a person typed",
          json.loads(renamed.meta_path.read_text())["title"],
          "What the owner calls it")
    check("...which stays marked as theirs",
          json.loads(renamed.meta_path.read_text())["title_source"], "person")
    check("...and it reports every record it read", seen >= 2, True)

    # -- 3. status, last_message, last_event_at ----------------------------
    session = running(broker)
    check("started, nothing said: idle", session.status(), "idle")
    await session.send_message("count to three")
    check("a message in flight: working", session.status(), "working")
    check("...and last_event_at is stamped",
          session.meta()["last_event_at"] is not None, True)

    await session._handle(permission_request())
    check("a pending permission outranks the turn: needs_input",
          session.status(), "needs_input")
    await session.answer_permission("req-1", "allow")
    check("...answered, it is working again", session.status(), "working")

    await session._handle(assistant("one two three"))
    check("last_message is the top-level assistant text",
          session.last_message, "one two three")
    await session._handle(assistant("from a subagent", parent="toolu_01"))
    check("...a subagent's sentence is not it",
          session.last_message, "one two three")
    await session._handle(envelope(0.03))
    check("a result that succeeded: done", session.status(), "done")

    await session.send_message("again")
    await session._handle(envelope(0.04, subtype="error_during_execution",
                                   is_error=True))
    check("a result that errored: failed", session.status(), "failed")

    long_answer = "x" * (cfg.LAST_MESSAGE_CHARS + 500)
    await session._handle(assistant(long_answer))
    check("last_message is cut to one length the app never guesses at",
          len(session.last_message), cfg.LAST_MESSAGE_CHARS)

    # The process ends, and by whose hand is the whole question.
    asked = running(broker)
    await asked.send_message("hello")
    await asked._handle(envelope(0.01))
    await asked.stop()
    check("ended by request after a good turn: done", asked.status(), "done")

    # A process that ends from outside while the session is idle with a good
    # answer on it is NOT a failure. It was until 2026-09-22, and that is the
    # red ring the owner found on Juno's "Course registration approval":
    # the answer was complete at 19:27Z, the broker was restarted at 19:49Z,
    # and systemd's SIGTERM reached the CLI child before `stop()` had recorded
    # that the end was asked for. The session has an answer to read and its id
    # resumes, which is the definition of `done`.
    crashed = running(broker)
    await crashed.send_message("hello")
    await crashed._handle(envelope(0.01))
    crashed.state = "exited"          # nobody asked
    check("ended from outside with a good answer on it: done",
          crashed.status(), "done")

    # But a process that ended having never answered anything did fail, and
    # nothing else it could be.
    stillborn = running(broker)
    stillborn.state = "exited"
    check("ended before it ever answered: failed", stillborn.status(), "failed")

    cut = running(broker)
    await cut.send_message("hello")
    cut.proc.returncode = 0
    await cut.stop()
    await cut._reap()
    check("a turn that never produced a result is not done",
          cut.status(), "failed")

    # -- 4. archived records are listed -----------------------------------
    # The list's archive filter retired with the classic view (2026-11-12):
    # every record is listed, with or without ?archived=1, which is what
    # Running now always asked for.
    pinned = running(broker, session_id="pinned-one")
    await pinned.send_message("pin me")
    pinned.archived = True
    pinned.save()
    listed = (await route(server, "GET", "/v1/sessions"))[1]["sessions"]
    check("an archived session is on the listing",
          "pinned-one" in [r["session_id"] for r in listed], True)
    listed = (await route(server, "GET", "/v1/sessions",
                          params={"archived": "1"}))[1]["sessions"]
    check("...and on it with ?archived=1, which changes nothing",
          "pinned-one" in [r["session_id"] for r in listed], True)

    # A record rebuilt off disk keeps its own time, and a start makes it
    # active. The idle reaper ends anything quiet for an hour, and a session
    # carrying a day-old time into it would be killed inside the minute.
    ended = running(broker, session_id="ended-one")
    await ended.send_message("something")
    await ended._handle(envelope(0.01))
    await ended.stop()
    del broker.sessions[ended.id]          # a restart forgets the object
    was = json.loads(ended.meta_path.read_text())["last_activity"]
    stale_clock = broker.adopt(broker_mod.Session("probe", "ended-one"))
    check("a record's own time is what it is rebuilt with",
          stale_clock.meta()["last_activity"], was)
    stale_clock._restored_status = None
    before_resume = stale_clock.last_activity
    stale_clock.last_activity = broker_mod.time.time()   # what start() does
    check("...while a process that has just started is active",
          stale_clock.last_activity > before_resume, True)

    # -- 5. the live list --------------------------------------------------
    expected = [r["session_id"] for r in broker.known()]
    resident = len(broker.live())
    watcher = FakeWriter()
    stream = asyncio.create_task(server.stream_records(watcher))
    await asyncio.sleep(0.05)
    check("the live list subscribes", len(broker.record_subscribers), 1)
    check("it opens by stating its own keepalive interval",
          watcher.comments()[0],
          f": open ping={broker_mod.KEEPALIVE_S * 1000}")
    opening = watcher.items()
    records = [r for r in opening if r["type"] == "broker.session"]
    check("...then the listing as it stands, newest first, archived or not",
          [r["session_id"] for r in records][:len(expected)], expected)
    check("...every row of it the snapshot, so there is one shape to read",
          {r["type"] for r in records}, {"broker.session"})
    # And one item that is not a row: the count against the cap (2026-09-25),
    # last, so a client has applied the rows before it is told the total. It
    # is here as well as on the list route because reopening this stream is
    # meant to re-send the whole screen.
    check("...and the resident count and the cap, once, at the end",
          opening[-1], {"type": "broker.residency", "live_count": resident,
                        "cap": cfg.MAX_SESSIONS})

    before = len(watcher.items())
    fresh = running(broker, session_id="live-one")
    await fresh.send_message("a new session says something")
    await asyncio.sleep(0.05)
    arrived = watcher.items()[before:]
    rows = [r for r in arrived if r["type"] == "broker.session"]
    check("a new session reaches the live list",
          rows[-1]["session_id"], "live-one")
    check("...as a snapshot, which carries no seq",
          (rows[-1]["type"], "seq" in rows[-1]),
          ("broker.session", False))
    check("...carrying what the row is drawn from",
          (rows[-1]["status"], rows[-1]["title"]),
          ("working", "a new session says something"))
    check("...saying it is resident, which is what the count counts",
          rows[-1]["live"], True)
    check("...and the count moves with it, unasked",
          [r for r in arrived if r["type"] == "broker.residency"][-1],
          {"type": "broker.residency", "live_count": resident + 1,
           "cap": cfg.MAX_SESSIONS})

    before = len(watcher.items())
    fresh.archived = True
    fresh.save()
    await asyncio.sleep(0.05)
    arrived = watcher.items()[before:]
    check("a record change with nothing said reaches it too",
          (arrived[-1]["session_id"], arrived[-1]["archived"]),
          ("live-one", True))
    check("...and says nothing about residency, which did not change",
          [r["type"] for r in arrived], ["broker.session"])

    before = len(watcher.items())
    await fresh._handle(assistant("and answers"))
    await asyncio.sleep(0.05)
    arrived = watcher.items()[before:]
    check("a last message reaches it",
          arrived[-1]["last_message"], "and answers")
    stream.cancel()

    # -- 6. provider -------------------------------------------------------
    check("a record carries its provider",
          session.meta()["provider"], "claude-code")
    async def start_on(provider):
        try:
            await broker.start_session("probe", provider=provider)
        except broker_mod.BrokerError as exc:
            return exc.status, exc.message
        return None, ""
    status, error = await start_on("grok")
    check("a start on a provider that is not wired is a 400", status, 400)
    check("...whose message says which one is", "claude-code" in error, True)
    check("...and so is one nobody has heard of",
          (await start_on("not-a-provider"))[0], 400)

    # -- 7. GET /v1/providers ----------------------------------------------
    status, answer = await route(server, "GET", "/v1/providers")
    rows = {row["id"]: row for row in answer["providers"]}
    check("every provider the page draws a row for",
          [row["id"] for row in answer["providers"]], list(cfg.PROVIDERS))
    check("...each with the name the owner uses",
          rows["claude-code"]["name"], "Claude Code")
    check("the two that are not wired say so",
          {p: (rows[p]["connected"], rows[p]["reason"])
           for p in ("grok", "opencode")},
          {p: (False, "not wired") for p in ("grok", "opencode")})
    check("...and carry no usage object to be read as zero",
          [("usage" in rows[p]) for p in ("grok", "opencode")],
          [False, False])
    check("no reading seen yet is null, not a zeroed page",
          rows["claude-code"]["usage"], None)

    # A real event off a real stream, through the real handler.
    await session._handle(REAL_RATE_LIMIT_EVENT)
    status, answer = await route(server, "GET", "/v1/providers")
    usage = {row["id"]: row for row in answer["providers"]}["claude-code"]["usage"]
    check("a rate_limit_event on any session's stream is the usage",
          (usage["five_hour"]["utilization"], usage["seven_day"]["utilization"]),
          (0.25, 0.24))
    check("...with the reset times as instants, not epochs to be guessed at",
          (usage["five_hour"]["resets_at"], usage["seven_day"]["resets_at"]),
          ("2026-09-18T00:10:00Z", "2026-09-23T04:00:00Z"))
    check("...the epoch beside them for a countdown",
          usage["five_hour"]["resets_at_epoch"], 1789690200)
    check("...and when it was seen", usage["seen_at"] is not None, True)
    check("a window nobody listed is carried anyway",
          usage["seven_day_overage_included"]["utilization"], 0.39)
    check("it survives a restart, so the page is not blank",
          cfg.read_usage()["five_hour"]["utilization"], 0.25)

    # -- 8. a record the broker did not write this time --------------------
    #
    # The failure this prevents: a broker restart while a turn was in flight
    # leaves `working` on disk, and a page reading it draws a spinner for a
    # process that has not existed for three days.
    check("a record caught mid-turn is not still working",
          broker_mod.recovered_status("working", [{}]), "failed")
    check("...nor is one caught waiting on a permission",
          broker_mod.recovered_status("needs_input", [{}]), "failed")
    # `waiting` is the other side of that rule (2026-09-25): its turn ENDED
    # with a good result and the only thing outstanding was a background child
    # of a process that no longer exists. Nothing more is coming, so `done`.
    check("a record caught waiting on background work is not a failure",
          broker_mod.recovered_status("waiting", [{}]), "done")
    check("a record that had finished keeps its answer",
          broker_mod.recovered_status("done", [{}]), "done")
    check("a record from before statuses existed, with turns",
          broker_mod.recovered_status(None, [{}]), "done")
    check("...and without", broker_mod.recovered_status(None, []), "idle")

    stale = running(broker, session_id="stale-one")
    await stale.send_message("mid-turn when the broker went")
    check("the record on disk says working",
          json.loads(stale.meta_path.read_text())["status"], "working")
    del broker.sessions[stale.id]
    listed = {r["session_id"]: r
              for r in broker.known()}["stale-one"]
    check("...and the listing, after a restart, does not",
          (listed["status"], listed["state"]), ("failed", "exited"))
    check("...while keeping what it can honestly keep",
          listed["title"], "mid-turn when the broker went")

    old = {"session_id": "old-one", "agent": "probe", "state": "exited",
           "turns": [{}], "record_seq": 4}
    filled = broker_mod.normalise(dict(old))
    check("a record older than these fields answers all of them",
          sorted(k for k in ("status", "provider", "title", "last_message",
                             "last_event_at", "pinned", "archived")
                 if k in filled),
          ["archived", "last_event_at", "last_message", "pinned", "provider",
           "status", "title"])
    check("...with the provider that was the only one there has ever been",
          filled["provider"], "claude-code")


root = Path(tempfile.mkdtemp(prefix="loam-broker-record-test-"))
saved = {name: getattr(cfg, name) for name in
         ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "USAGE_PATH", "AI_LABS",
          "CREDENTIALS_PATH")}
saved_titler = (loam_transcript.CLAUDE_BIN, loam_transcript.WAKE_ENV,
                loam_transcript.TITLER_CWD)
saved_vault = (adoption.TRANSCRIPT_STATE, cfg.TITLE_POLL_WAITS)

# Where the transcript writer's sidecars and files go for this test. The
# broker reads them; nothing here writes into the real vault.
VAULT = root / "15_CONVERSATIONS"

# The titler, for real, against a stub binary. LOAM_TITLER_BIN exists on
# loam_transcript precisely so the argv and the failure paths can be exercised
# with a binary that is not the model; this test is what that override is for,
# and it is why running this costs nothing.
STUB_LOG = root / "titler-call.json"
STUB = root / "claude-stub"
STUB.write_text(
    "#!/usr/bin/env python3\n"
    "import json, sys\n"
    "argv = sys.argv[1:]\n"
    "json.dump({'prompt': argv[argv.index('-p') + 1],\n"
    "           'model': argv[argv.index('--model') + 1],\n"
    "           'argv': argv},\n"
    f"          open({str(STUB_LOG)!r}, 'w'))\n"
    "print('The wall on Tuesday')\n",
    encoding="utf-8")
STUB.chmod(STUB.stat().st_mode | stat.S_IXUSR)

try:
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.USAGE_PATH = cfg.STATE_DIR / "provider-usage.json"
    cfg.AI_LABS = root / "agents"
    cfg.CREDENTIALS_PATH = root / "credentials.json"
    (cfg.AI_LABS / "probe").mkdir(parents=True)
    (cfg.AI_LABS / "probe" / "agent.json").write_text("{}", encoding="utf-8")

    VAULT.mkdir(parents=True)
    adoption.TRANSCRIPT_STATE = root / "transcripts"
    adoption.TRANSCRIPT_STATE.mkdir(parents=True)
    # The real poll, with the waiting taken out: what is being tested is that
    # it looks again and what it does with each answer, not the clock.
    cfg.TITLE_POLL_WAITS = (0, 0, 0)

    loam_transcript.CLAUDE_BIN = str(STUB)
    loam_transcript.TITLER_CWD = root / "titler"
    loam_transcript.WAKE_ENV = root / "wake.env"
    loam_transcript.WAKE_ENV.write_text(
        "CLAUDE_CODE_OAUTH_TOKEN=not-a-real-token\n", encoding="utf-8")

    asyncio.run(main())
finally:
    for name, value in saved.items():
        setattr(cfg, name, value)
    (loam_transcript.CLAUDE_BIN, loam_transcript.WAKE_ENV,
     loam_transcript.TITLER_CWD) = saved_titler
    (adoption.TRANSCRIPT_STATE, cfg.TITLE_POLL_WAITS) = saved_vault
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
