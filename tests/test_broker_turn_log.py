#!/usr/bin/env python3
"""Check that replay keeps both sides of a turn and charges only its incremental cost."""

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
import loam_broker as cfg
# Use fixture memory to isolate the broker from machine load.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_clock  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:58} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


class FakeStdin:
    """The CLI's stdin, kept so the test can assert what actually went out."""

    def __init__(self) -> None:
        self.lines: list[dict] = []

    def write(self, data: bytes) -> None:
        self.lines.append(json.loads(data.decode()))

    async def drain(self) -> None:
        return None


class FakeProc:
    returncode = None

    def __init__(self) -> None:
        self.stdin = FakeStdin()


def running(agent: str = "probe", session_id: str | None = None):
    session = broker_mod.Session(agent, session_id)
    session.state = "running"
    session.proc = FakeProc()
    return session


def log_of(session) -> list[dict]:
    if not session.log_path.exists():
        return []
    return [json.loads(line) for line
            in session.log_path.read_text(encoding="utf-8").splitlines() if line]


def envelope(total, session_id="s"):
    """A result envelope's shape, with only what the turn record reads."""
    return {"type": "result", "subtype": "success", "session_id": session_id,
            "total_cost_usd": total, "duration_ms": 2249, "num_turns": 1,
            "usage": {"input_tokens": 4, "output_tokens": 76},
            "permission_denials": []}


async def main() -> None:
    # -- a person's message is an event ------------------------------------
    session = running()
    sent_seq = await session.send_message("what did you do yesterday?")

    # Skip the injected clock.
    check("the text reached the CLI",
          loam_clock.strip(session.proc.stdin.lines[0]["message"]["content"]),
          "what did you do yesterday?")
    log = log_of(session)
    check("one event was logged", len(log), 1)
    event = log[0]
    check("its type", event["type"], "broker.user_message")
    check("the text is flat", event["text"], "what did you do yesterday?")
    check("session_id is on it", event["session_id"], session.id)
    check("it is numbered like any event", event["seq"], 1)
    check("nothing is nested under a payload key",
          sorted(event), ["seq", "session_id", "text", "type"])
    # The route answers with this number, and it is the event's own rather
    # than the session's counter read afterwards -- which is what lets a
    # sender recognise its own line when the event comes round on its stream.
    check("the send answers with the event's own seq", sent_seq, event["seq"])

    # -- and it is ordered BEFORE what the CLI says about it ---------------
    #
    # The order is the whole value of the event: a replay that puts the
    # question after the answer is not a conversation.
    await session._handle({"type": "assistant",
                           "message": {"model": "claude-fable-5-1",
                                       "content": [{"type": "text",
                                                    "text": "I built a broker."}]}})
    await session._handle(envelope(0.25))
    await session.send_message("and today?")
    await session._handle({"type": "assistant",
                           "message": {"model": "claude-fable-5-1",
                                       "content": [{"type": "text",
                                                    "text": "This."}]}})
    await session._handle(envelope(0.30))

    check("a client replaying from 0 reads both sides, in order",
          [e["type"] for e in session.replay(0)],
          ["broker.user_message", "assistant", "result",
           "broker.user_message", "assistant", "result"])
    check("...and the log is what it read from",
          [e["type"] for e in log_of(session)],
          [e["type"] for e in session.replay(0)])
    check("replaying from a seq skips what was already seen",
          [e["type"] for e in session.replay(3)],
          ["broker.user_message", "assistant", "result"])
    check("the person's words survive the round trip to disk",
          [e["text"] for e in session.replay(0)
           if e["type"] == "broker.user_message"],
          ["what did you do yesterday?", "and today?"])

    # -- a live subscriber sees it too -------------------------------------
    session = running()
    queue: asyncio.Queue = asyncio.Queue()
    session.subscribers.add(queue)
    await session.send_message("hello")
    live = queue.get_nowait()
    check("a client watching live gets it", live["type"], "broker.user_message")
    check("...the same one that is on disk", live["seq"], log_of(session)[0]["seq"])

    # -- a message the CLI never received is not in the log ----------------
    session = running()
    session.state = "exited"
    try:
        await session.send_message("into the void")
        check("sending to an ended session raises", False, True)
    except broker_mod.BrokerError as exc:
        check("sending to an ended session is a 409", exc.status, 409)
    check("and nothing was logged as sent", log_of(session), [])

    # Compute the cost delta inside one provider process.
    session = running()
    await session._handle(envelope(0.34253275))
    await session._handle(envelope(0.35690825))
    turns = session.turns
    check("the first turn of a process costs its own total",
          turns[0]["cost_delta_usd"], 0.34253275)
    check("the second costs the difference, not the total",
          turns[1]["cost_delta_usd"], 0.0143755)
    check("...and the running total is kept as reported beside it",
          turns[1]["total_cost_usd"], 0.35690825)
    check("the arithmetic carries no floating-point noise",
          repr(turns[1]["cost_delta_usd"]), "0.0143755")
    check("a turn the CLI priced at nothing is nothing, not None",
          running()._cost_delta(0), 0)

    # -- and the reset at a resume -----------------------------------------
    #
    # A resume is a new process, so the CLI's total starts over. The turn
    # records do NOT start over -- they are the session's -- which is exactly
    # why a client cannot do this subtraction for itself: nothing in the list
    # says where the second process began.
    session.save()
    resumed = running(session_id=session.id)
    check("the resumed session carried the turns across", len(resumed.turns), 2)
    check("...and the seq", resumed.seq, session.seq)
    await resumed._handle(envelope(0.01428225))
    check("the first turn after a resume costs its own total, not a negative",
          resumed.turns[2]["cost_delta_usd"], 0.01428225)
    check("...and the earlier turns are untouched",
          [t["cost_delta_usd"] for t in resumed.turns],
          [0.34253275, 0.0143755, 0.01428225])
    await resumed._handle(envelope(0.02))
    check("the process continues from there",
          resumed.turns[3]["cost_delta_usd"], 0.00571775)
    check("a turn says whether it was its process's first",
          [t["process_start"] for t in resumed.turns], [True, False, True, False])

    # A resumed process can restore the cumulative total.
    session = running()
    for total in (1.25, 2.0):
        await session._handle(envelope(total))
    session.save()
    resumed = running(session_id=session.id)
    for total in (2.5, 3.1):                    # restored from 2.0
        await resumed._handle(envelope(total))
    resumed.save()
    again = running(session_id=session.id)
    await again._handle(envelope(3.6))          # restored from 3.1
    deltas = [t["cost_delta_usd"] for t in again.turns]
    check("restored resume: the first turn costs the difference",
          deltas, [1.25, 0.75, 0.5, 0.6, 0.5])
    check("restored resume: the deltas sum to the final total",
          round(sum(deltas), 10), 3.6)

    # A stale restored total can predate a process that reset its counter.
    session = running()
    await session._handle(envelope(15.76))
    session.save()
    second = running(session_id=session.id)
    for total in (0.4, 12.65):                  # reset: starts over
        await second._handle(envelope(total))
    second.save()
    third = running(session_id=session.id)
    await third._handle(envelope(16.46))        # restored from 15.76
    deltas = [t["cost_delta_usd"] for t in third.turns]
    check("stale restore: each turn its own cost",
          deltas, [15.76, 0.4, 12.25, 0.7])
    check("stale restore: the deltas sum to what the three processes spent",
          round(sum(deltas), 10), round(15.76 + 12.65 + (16.46 - 15.76), 10))

    # -- a record written before process_start existed -------------------
    #
    # The old rule wrote delta == total on exactly a process's first turn, so
    # those turns still mark where the earlier processes ended.
    session = running()
    session.turns = [
        {"total_cost_usd": 5.0, "cost_delta_usd": 5.0},
        {"total_cost_usd": 6.0, "cost_delta_usd": 1.0},     # that process ends at 6
        {"total_cost_usd": 0.5, "cost_delta_usd": 0.5},     # a reset
        {"total_cost_usd": 0.9, "cost_delta_usd": 0.4},
    ]
    check("legacy turns: the bases are zero and each process's end",
          session._cost_bases(), {0.0, 6.0, 0.9})
    check("legacy turns: a restore from the older end costs the difference",
          session._cost_delta(6.3), 0.3)

    # -- an envelope with no cost in it ------------------------------------
    session = running()
    await session._handle(envelope(None))
    check("no total reported is no delta invented",
          session.turns[0]["cost_delta_usd"], None)
    await session._handle(envelope(0.5))
    check("...and the next turn is still the first priced one",
          session.turns[1]["cost_delta_usd"], 0.5)

    # -- the record the listing reads -------------------------------------
    check("cost_delta_usd is in the saved record",
          "cost_delta_usd" in json.loads(
              session.meta_path.read_text(encoding="utf-8"))["turns"][1], True)


root = Path(tempfile.mkdtemp(prefix="loam-broker-test-"))
saved_session_dir = cfg.SESSION_DIR
saved_ai_labs = cfg.AI_LABS
try:
    # Under a temporary directory: this test must never touch the box's real
    # session logs, and Session.__init__ reads any record it finds for its id.
    cfg.SESSION_DIR = root / "sessions"
    cfg.AI_LABS = root / "agents"
    (cfg.AI_LABS / "probe").mkdir(parents=True)
    (cfg.AI_LABS / "probe" / "agent.json").write_text("{}", encoding="utf-8")
    asyncio.run(main())
finally:
    cfg.SESSION_DIR = saved_session_dir
    cfg.AI_LABS = saved_ai_labs
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
