#!/usr/bin/env python3
"""Check incremental synchronization of adopted sessions from the provider log."""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_adopt as adoption  # noqa: E402
import loam_broker as cfg  # noqa: E402
# Use fixture memory to isolate the broker from machine load.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

FIXTURE = AGENTS / "fixtures" / "app-session.jsonl"
# Where the fixture is cut. Line 55 is its second human turn, so the record is
# adopted mid-conversation exactly as one is when the owner is still talking.
CUT = 55
APP_ID = "5e55107a-0000-4000-8000-000000000001"
LATER_ID = "5e55107a-0000-4000-8000-000000000002"
NEWER_ID = "5e55107a-0000-4000-8000-000000000003"

results = []
parses = []          # every file `summarise` was asked to read, in order


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} got={got!r:.120}")
    if not ok:
        print(f"      wanted {want!r:.200}")


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
    """A request that answers and ends. Not for a stream."""
    writer = FakeWriter()
    try:
        await server.route(method, path, params or {}, payload or {}, writer)
    except broker_mod.BrokerError as exc:
        return exc.status, {"error": exc.message}
    return (*writer.body(),)[0], writer.body()[1]


async def until(predicate, seconds: float = 3.0) -> bool:
    """Wait for something a task elsewhere is doing, or give up and let the
    check fail with what actually happened."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


def lines(tag: str = "") -> list[str]:
    """Give the conversation a stable event prefix so fixtures can model shared and
    unrelated histories.
    """
    out = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if tag:
            entry = json.loads(line)
            for field in ("uuid", "parentUuid"):
                if isinstance(entry.get(field), str):
                    entry[field] = f"{tag}-{entry[field]}"
            line = json.dumps(entry)
        out.append(line)
    return out


def write_part(path: Path, upto: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines(path.stem)[:upto]) + "\n",
                    encoding="utf-8")


def append_rest(path: Path, after: int) -> None:
    """The turn the owner took in the app after the record was made."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines(path.stem)[after:]) + "\n")


async def main() -> None:
    workspace = cfg.workspace("iris")
    (workspace).mkdir(parents=True, exist_ok=True)
    (workspace / "agent.json").write_text(
        json.dumps({"name": "iris", "display": "Iris", "emoji": "🪟"}),
        encoding="utf-8")
    folder = adoption.project_dir(workspace)
    source = folder / f"{APP_ID}.jsonl"
    write_part(source, CUT)

    broker = broker_mod.Broker()
    server = broker_mod.Server(broker)
    broker.adopt_app_sessions()
    record_path = cfg.SESSION_DIR / f"{APP_ID}.json"
    stored = json.loads(record_path.read_text(encoding="utf-8"))
    check("the record says what the file measured when it was read",
          (stored["replayed"], stored["source_size"],
           stored["source_mtime"] == int(source.stat().st_mtime)),
          (18, source.stat().st_size, True))

    # -- 1. a read of the record, and what it does and does not cost -------
    parses.clear()
    status, record = await route(server, "GET", f"/v1/sessions/{APP_ID}")
    check("a read of an unchanged record parses nothing",
          (status, parses), (200, []))

    # An mtime change alone does not establish a new turn.
    touched = int(source.stat().st_mtime) + 90
    os.utime(source, (touched, touched))
    _, record = await route(server, "GET", f"/v1/sessions/{APP_ID}")
    check("a file touched but not written into is not re-read",
          (parses, record["replayed"], record["source_mtime"]),
          ([], 18, touched))

    append_rest(source, CUT)
    _, record = await route(server, "GET", f"/v1/sessions/{APP_ID}")
    check("a read of a record whose file GREW re-reads it, once",
          (len(parses), record["replayed"], record["record_seq"]), (1, 21, 21))
    check("...and answers what the app has said since",
          (record["last_message"].startswith(
              "So it wasn't dictation eating a name"),
           record["last_event_at"]),
          (True, "2026-09-11T22:50:52Z"))
    check("...and writes the new reading to the record",
          json.loads(record_path.read_text(encoding="utf-8"))["source_size"],
          source.stat().st_size)
    check("...and the next read of it parses nothing again",
          (await route(server, "GET", f"/v1/sessions/{APP_ID}"))[1]["replayed"],
          21)
    check("...which is one parse in four reads", len(parses), 1)

    # The listing, which is where the owner actually reads a stale row. A queue
    # on `record_subscribers` is exactly what `GET /v1/sessions/events` holds,
    # so this is the live list stream's own path to the changed snapshot.
    write_part(folder / f"{LATER_ID}.jsonl", CUT)
    broker.adopt_app_sessions()
    append_rest(folder / f"{LATER_ID}.jsonl", CUT)
    watching: asyncio.Queue = asyncio.Queue()
    broker.record_subscribers.add(watching)
    parses.clear()
    status, listing = await route(server, "GET", "/v1/sessions")
    rows = {row["session_id"]: row for row in listing["sessions"]}
    check("the list re-reads the record whose file grew, and only that one",
          (len(parses), rows[LATER_ID]["replayed"], rows[APP_ID]["replayed"]),
          (1, 21, 21))
    snapshots = []
    while not watching.empty():
        snapshots.append(watching.get_nowait())
    check("...and the live list is sent the changed snapshot",
          [(s["type"], s["session_id"], s["replayed"]) for s in snapshots],
          [("broker.session", LATER_ID, 21)])
    broker.record_subscribers.discard(watching)

    # -- the two records that must NOT be refreshed --------------------------
    #
    # A forked record: the fork read that conversation and went on in a file of
    # its own, so what the owner says in the app on that session afterwards is a
    # branch this record does not follow. Written by hand here, because forking
    # is a CLI launch and test_broker_fork.py is where that lives.
    forked = json.loads(record_path.read_text(encoding="utf-8"))
    forked.update({"cli_session_id": "f0f0f0f0-0000-4000-8000-000000000001",
                   "forked_from": APP_ID, "source_size": 1})
    record_path.write_text(json.dumps(forked), encoding="utf-8")
    parses.clear()
    _, record = await route(server, "GET", f"/v1/sessions/{APP_ID}")
    check("a forked record is not re-read from the app's file",
          (parses, record["replayed"], record["source_size"]), ([], 21, 1))
    session = broker.load(APP_ID)
    check("...and is not followable", session.followable(), False)

    # A record with a log of this broker's own, which is the same rule the
    # scan settles on: the numbering above `replayed` is already spoken for.
    logged = json.loads(record_path.read_text(encoding="utf-8"))
    logged.update({"cli_session_id": None, "forked_from": None,
                   "source_size": 2})
    record_path.write_text(json.dumps(logged), encoding="utf-8")
    (cfg.SESSION_DIR / f"{APP_ID}.jsonl").write_text(
        json.dumps({"type": "broker.user_message", "seq": 22,
                    "session_id": APP_ID, "text": "a turn taken here"}) + "\n",
        encoding="utf-8")
    parses.clear()
    _, record = await route(server, "GET", f"/v1/sessions/{APP_ID}")
    check("a record with a log of our own is not re-read either",
          (parses, record["source_size"]), ([], 2))

    # -- 2. the scan, on a read of the list, at most once a minute ---------
    fourth_id = "5e55107a-0000-4000-8000-000000000004"
    write_part(folder / f"{fourth_id}.jsonl", CUT)
    broker._scanned -= cfg.SCAN_INTERVAL_S + 1
    _, listing = await route(server, "GET", "/v1/sessions")
    check("a conversation started in the app appears on a read of the list",
          fourth_id in {row["session_id"] for row in listing["sessions"]},
          True)

    fifth_id = "5e55107a-0000-4000-8000-000000000005"
    write_part(folder / f"{fifth_id}.jsonl", CUT)
    _, listing = await route(server, "GET", "/v1/sessions")
    check("...but not twice inside a minute, however often the list is read",
          fifth_id in {row["session_id"] for row in listing["sessions"]},
          False)
    broker._scanned -= cfg.SCAN_INTERVAL_S + 1
    _, listing = await route(server, "GET", "/v1/sessions")
    check("...and on the first read after the minute is up",
          fifth_id in {row["session_id"] for row in listing["sessions"]},
          True)


root = Path(tempfile.mkdtemp(prefix="broker-follow-"))
saved = {name: getattr(cfg, name)
         for name in ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "USAGE_PATH",
                      "AI_LABS")}
saved_adopt = {name: getattr(adoption, name)
               for name in ("PROJECTS", "TRANSCRIPT_STATE", "summarise",
                            "candidates")}
try:
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.USAGE_PATH = cfg.STATE_DIR / "provider-usage.json"
    cfg.SESSION_DIR.mkdir(parents=True)
    cfg.AI_LABS = root / "agents"
    adoption.PROJECTS = root / "projects"
    adoption.TRANSCRIPT_STATE = root / "transcripts"

    # Every parse of a session file, recorded. It is the one thing about the
    # cost of this that a test can hold: the stat is free and the parse is not.
    counted = adoption.summarise

    def summarise(path, session_id, agent, after=None):
        parses.append(Path(path).name)
        return counted(path, session_id, agent, after)

    adoption.summarise = summarise

    # Keep the scan clock inside the fixture’s discovery window.
    scanned = adoption.candidates
    fixture_now = adoption.epoch_of(adoption.last_timestamp(FIXTURE)) + 3600

    def candidates(*args, **kwargs):
        kwargs.setdefault("now", fixture_now)
        return scanned(*args, **kwargs)

    adoption.candidates = candidates

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
