#!/usr/bin/env python3
"""Which sessions are in RAM right now, and how many may be.

The owner, 2026-09-25: "since we have a ram limit on number of running
sessions, we need a filter in search on active sessions that are active in
Ram atm." One session's process costs the box a few hundred megabytes (see
"The turn shape" in broker/README.md for the cgroup measurements), the box
allows four, and the broker is the only party that knows which four -- so the
app has to be able to ask, and a page has to be able to say "2 of 4 live".

Three things are proved here:

  1. **`live` is residency and not turn state.** It is read off the process
     table (`Session.is_live`): a `done` session with a warm idle process is
     live, a session whose process has died is not, and a record read off
     disk never is, whatever the file says.
  2. **`live_count` and `cap` are on the listing**, and `live` -- the older
     spelling of that count, from before each session carried a `live` of
     its own -- still agrees with it.
  3. **The count moves by itself.** Starting processes to the cap and past
     it, and ending one, with the live list watched throughout: every
     arrival and departure reaches it as `broker.residency`, and a record
     write that changed no residency does not.

Point 3 runs real processes against a stub binary -- a pid, a returncode, a
line in `ps` -- because four sessions of the real CLI is 1.8 GB and the
question is whether a process is behind a session, not what it is running.
The real CLI is in the causing this was written from
(`engineer/jobs/20260925-141117-600e0f16/`).

Run: python3 tests/test_broker_residency.py
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
import loam_broker as cfg  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results = []

# A real process that holds its stdin open and answers nothing, in place of
# the CLI. What is being counted is the process, and this is one.
STUB = """#!/usr/bin/env python3
import sys
for line in sys.stdin:
    pass
"""


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


class FakeStdin:
    def write(self, data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None


class FakeProc:
    """A process object, which is all `is_live` reads."""

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
        head, _, payload = self.text().partition("\r\n\r\n")
        return int(head.split()[1]), json.loads(payload)

    def items(self) -> list[dict]:
        return [json.loads(line[len("data: "):])
                for line in self.text().splitlines()
                if line.startswith("data: ")]


async def start(broker, agent):
    """A start as a thread turn makes one (POST /v1/sessions, which the
    classic view used, retired 2026-11-12), answered as a route would."""
    try:
        session = await broker.start_session(agent)
    except broker_mod.BrokerError as exc:
        return exc.status, {"error": exc.message, **exc.detail}
    return 201, session.meta()


async def route(server, method, path, params=None, payload=None):
    writer = FakeWriter()
    try:
        await server.route(method, path, params or {}, payload or {}, writer)
    except broker_mod.BrokerError as exc:
        return exc.status, {"error": exc.message, **exc.detail}
    return writer.body()


def envelope(total=0.01):
    return {"type": "result", "subtype": "success", "is_error": False,
            "session_id": "s", "total_cost_usd": total, "duration_ms": 2249,
            "num_turns": 1, "usage": {"input_tokens": 4, "output_tokens": 76},
            "permission_denials": []}


def running(broker, agent="probe1", session_id=None):
    session = broker.adopt(broker_mod.Session(agent, session_id))
    session.state = "running"
    session.proc = FakeProc()
    broker.sessions[session.id] = session
    return session


async def main() -> None:
    # -- 1. residency is the process table, not the status -----------------
    broker = broker_mod.Broker()
    server = broker_mod.Server(broker)

    warm = running(broker, session_id="warm-one")
    await warm.send_message("hello")
    check("a session mid-turn is live", (warm.meta()["status"],
                                         warm.meta()["live"]),
          ("working", True))
    await warm._handle(envelope())
    check("...and is STILL live once its turn is done: a warm idle process "
          "still holds its memory",
          (warm.meta()["status"], warm.meta()["live"]), ("done", True))

    died = running(broker, session_id="died-one")
    died.proc.returncode = 1
    check("a session whose process has died is not live, before `_reap` has "
          "had the line",
          (died.state, died.meta()["live"]), ("running", False))

    asked = running(broker, session_id="asked-one")
    await asked.send_message("hello")
    await asked._handle(envelope())
    await asked.stop()
    check("a session that was ended is not live, and keeps its answer",
          (asked.meta()["status"], asked.meta()["live"]), ("done", False))

    check("the resident set is the sessions with a process behind them",
          sorted(s.id for s in broker.live()), ["warm-one"])

    # -- and a record off disk is never live -------------------------------
    stored = json.loads(warm.meta_path.read_text(encoding="utf-8"))
    check("the record written while the process was up says so",
          (stored["state"], stored["live"]), ("running", True))
    restarted = broker_mod.Broker()
    check("...and a broker with no process table lists it as neither "
          "running nor live",
          [(r["session_id"], r["state"], r["live"]) for r in restarted.known()
           if r["session_id"] == "warm-one"],
          [("warm-one", "exited", False)])
    check("a record that predates the field reads as not live, which is what "
          "it is",
          broker_mod.normalise({"session_id": "x", "agent": "probe1",
                                "live": True, "state": "running"})["live"],
          False)
    check("and `live` is on every record the interface answers, never absent",
          all("live" in r for r in restarted.known()), True)

    # -- 2. the count and the cap, on the listing --------------------------
    status, body = await route(server, "GET", "/v1/sessions")
    check("the listing carries the count and the cap", status, 200)
    check("...as `live_count` and `cap`",
          (body["live_count"], body["cap"]), (1, cfg.MAX_SESSIONS))
    check("...and `live`, the older spelling, agrees with the count",
          body["live"], body["live_count"])
    status, narrowed = await route(server, "GET", "/v1/sessions",
                                   params={"agent": "probe9"})
    check("a narrowed list leaves them alone: the cap is the box's, which is "
          "why a page cannot count rows for this",
          (narrowed["sessions"], narrowed["live_count"], narrowed["cap"]),
          ([], 1, cfg.MAX_SESSIONS))

    for session in list(broker.sessions.values()):
        await session.stop()

    # -- 3. to the cap and past it, with the live list watching ------------
    broker = broker_mod.Broker()
    server = broker_mod.Server(broker)
    watcher = FakeWriter()
    stream = asyncio.create_task(server.stream_records(watcher))
    await asyncio.sleep(0.05)
    check("the live list opens by stating the count and the cap",
          watcher.items()[-1],
          {"type": "broker.residency", "live_count": 0,
           "cap": cfg.MAX_SESSIONS})

    counts = []
    for n in range(cfg.MAX_SESSIONS):
        status, record = await start(broker, f"probe{n + 1}")
        counts.append((status, record["live"]))
    await asyncio.sleep(0.1)
    check("every start is a real process, and each says it is live",
          counts, [(201, True)] * cfg.MAX_SESSIONS)
    check("...and the live list was told the count each time, in step and "
          "never one behind",
          [e["live_count"] for e in watcher.items()
           if e["type"] == "broker.residency"],
          list(range(cfg.MAX_SESSIONS + 1)))

    status, refused = await start(broker, "probe5")
    check("the fifth is refused, because the cap is what the count counts "
          "against", status, 429)
    # Every seat was taken a moment ago, so none is idle long enough to be
    # ended for it (cfg.EVICT_GRACE_S): this is the "truly no room" case.
    check("...and the refusal names the backstop and the count, for an API "
          "caller, in `hint`",
          (f"the backstop is {cfg.MAX_SESSIONS} sessions" in refused["hint"],
           refused["reason"], refused["cap"], refused["live_count"],
           len(refused["busy"])),
          (True, "backstop", cfg.MAX_SESSIONS, cfg.MAX_SESSIONS, cfg.MAX_SESSIONS))
    check("...while `error`, what a page shows, is a person's sentence with "
          "no route in it",
          (refused["error"].startswith("There's no room for Probe 5"),
           "DELETE" in refused["error"], "/v1/" in refused["error"]),
          (True, False, False))
    status, body = await route(server, "GET", "/v1/sessions")
    check("...and the listing says the box is full",
          (body["live_count"], body["cap"]),
          (cfg.MAX_SESSIONS, cfg.MAX_SESSIONS))

    # -- one leaves, and the room it makes is on the list ------------------
    leaving = [s for s in broker.live()][0]
    before = len(watcher.items())
    status, ended = await route(server, "DELETE",
                                f"/v1/sessions/{leaving.id}")
    await asyncio.sleep(0.1)
    check("a session ended says it is no longer resident",
          (status, ended["live"], ended["state"]), (200, False, "exited"))
    arrived = watcher.items()[before:]
    check("...the row reaches the live list saying the same",
          [(e["session_id"], e["live"]) for e in arrived
           if e["type"] == "broker.session"][0], (leaving.id, False))
    check("...and so does the count, unasked",
          [e for e in arrived if e["type"] == "broker.residency"],
          [{"type": "broker.residency", "live_count": cfg.MAX_SESSIONS - 1,
            "cap": cfg.MAX_SESSIONS}])

    # -- a write that changed no residency says nothing about it -----------
    before = len(watcher.items())
    stored = broker.load(leaving.id)
    stored.pinned = True
    stored.save()
    await asyncio.sleep(0.05)
    check("a record write is not an arrival",
          [e["type"] for e in watcher.items()[before:]], ["broker.session"])

    # -- and the one that was refused starts now ---------------------------
    status, record = await start(broker, "probe5")
    await asyncio.sleep(0.1)
    check("the start that was refused at the cap is accepted under it",
          (status, record["live"]), (201, True))
    status, body = await route(server, "GET", "/v1/sessions")
    check("...and the box is full again",
          body["live_count"], cfg.MAX_SESSIONS)
    check("...with the rows that are not live beside the ones that are, "
          "which is the filter's whole point",
          (sum(r["live"] for r in body["sessions"]),
           sum(not r["live"] for r in body["sessions"]) > 0),
          (cfg.MAX_SESSIONS, True))

    stream.cancel()
    for session in list(broker.sessions.values()):
        await session.stop()


root = Path(tempfile.mkdtemp(prefix="loam-broker-residency-test-"))
saved = {name: getattr(cfg, name) for name in
         ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "AI_LABS", "CLAUDE",
          "MAX_SESSIONS", "memory")}
try:
    # The count is what is proved here, so memory is plentiful and the
    # backstop is the four this file was written against; admission by
    # memory is tests/test_broker_seats.py's (2026-11-09).
    cfg.MAX_SESSIONS = 4
    cfg.memory = lambda: {"available": 10 ** 6, "cgroup_room": None,
                          "reserve": cfg.MEMORY_RESERVE_MB}
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.AI_LABS = root / "agents"
    stub = root / "claude-stub"
    stub.write_text(STUB, encoding="utf-8")
    os.chmod(stub, stat.S_IRWXU)
    cfg.CLAUDE = str(stub)
    for n in range(1, 6):
        (cfg.AI_LABS / f"probe{n}").mkdir(parents=True)
        (cfg.AI_LABS / f"probe{n}" / "agent.json").write_text(
            json.dumps({"display": f"Probe {n}"}), encoding="utf-8")
    asyncio.run(main())
finally:
    for name, value in saved.items():
        setattr(cfg, name, value)
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
