#!/usr/bin/env python3
"""Check the ordering of turn completion, background-task notifications and context accounting."""

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
# Use fixture memory to isolate the broker from machine load.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_adopt as adoption  # noqa: E402

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


# -- stand-ins for the process ---------------------------------------------


class FakeStdin:
    def write(self, data: bytes) -> None:
        return None

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


def running(session_id: str | None = None):
    session = broker_mod.Session("probe", session_id)
    session.state = "running"
    session.proc = FakeProc()
    return session


# Provider events for a task that outlives its parent turn.

TASKS_ONE = {                                              # seq 1376
    "type": "system", "subtype": "background_tasks_changed",
    "tasks": [{"task_id": "a2225e8aa387690af",
               "task_type": "local_agent",
               "description": "Four briefs: broker tail/seen, page x2, deploy"}],
    "uuid": "4a974fe8-a0d1-4150-8014-6492aba35c4d",
    "session_id": "940380fd-0d52-5ae4-a399-9dc7d059b650"}

TASKS_TWO = {                                              # seq 8688
    "type": "system", "subtype": "background_tasks_changed",
    "tasks": [{"task_id": "a2225e8aa387690af",
               "task_type": "local_agent",
               "description": "Four briefs: broker tail/seen, page x2, deploy"},
              {"task_id": "a7914659c9d784ff0",
               "task_type": "local_agent",
               "description": "Page audit: two capped jobs' points"}],
    "uuid": "2cd3fc11-34f0-4055-9031-81ca139ceb1f",
    "session_id": "940380fd-0d52-5ae4-a399-9dc7d059b650"}

TASKS_NONE = {                                             # seq 10155
    "type": "system", "subtype": "background_tasks_changed", "tasks": [],
    "uuid": "224eaf16-bf8d-4345-b74c-716b65c2d9b3",
    "session_id": "940380fd-0d52-5ae4-a399-9dc7d059b650"}

NOTIFICATION = {                                           # seq 10157
    "type": "system", "subtype": "task_notification",
    "task_id": "a2225e8aa387690af", "status": "completed",
    "summary": "The job completed. Nothing further to request.",
    "uuid": "b1f0a9c0-0000-4000-8000-000000000000",
    "session_id": "940380fd-0d52-5ae4-a399-9dc7d059b650"}

NOTIFICATION_TWO = {                                       # seq 9242, retyped
    "type": "system", "subtype": "task_notification",
    "task_id": "a7914659c9d784ff0", "status": "completed",
    "summary": "The call returned; here is the report.",
    "uuid": "b1f0a9c0-0000-4000-8000-000000000001",
    "session_id": "940380fd-0d52-5ae4-a399-9dc7d059b650"}

# The shape a permission takes on this channel, as `--permission-prompt-tool
# stdio` emits it. Same as tests/test_broker_record.py's, which is where the
# first reading of it was caused.
PERMISSION_REQUEST = {
    "type": "control_request", "request_id": "req-1",
    "request": {"subtype": "can_use_tool", "tool_name": "Read",
                "input": {"file_path": "/etc/hostname"}}}

INIT = {                                                   # seq 10158
    "type": "system", "subtype": "init",
    "cwd": "/home/loam/agents/ada", "model": "claude-fable-5-1",
    "session_id": "940380fd-0d52-5ae4-a399-9dc7d059b650",
    "uuid": "c0a1b2c3-0000-4000-8000-000000000000"}

# Usage belongs to the top-level assistant message.
ASSISTANT = {
    "type": "assistant", "parent_tool_use_id": None,
    "message": {"model": "claude-fable-5-1",
                "content": [{"type": "text", "text": "Logged."}],
                "usage": {"input_tokens": 32,
                          "cache_creation_input_tokens": 548,
                          "cache_read_input_tokens": 34691,
                          "output_tokens": 89,
                          "service_tier": "standard"}},
    "session_id": "49db688c-3111-4b67-b512-804c5ef7e34c"}

SUBAGENT_ASSISTANT = {
    "type": "assistant", "parent_tool_use_id": "toolu_01NBV8qNLqgjG95VWGA8r8nB",
    "message": {"model": "claude-haiku-4-5-20251001",
                "content": [{"type": "text", "text": "I'll load the tool."}],
                "usage": {"input_tokens": 4, "cache_read_input_tokens": 900000,
                          "output_tokens": 12}},
    "session_id": "49db688c-3111-4b67-b512-804c5ef7e34c"}

RESULT = {
    "type": "result", "subtype": "success", "is_error": False,
    "duration_ms": 11689, "duration_api_ms": 44574, "num_turns": 3,
    "session_id": "49db688c-3111-4b67-b512-804c5ef7e34c",
    "total_cost_usd": 0.5802115,
    # The turn's requests ADDED UP, which is what makes it the wrong number for
    # a context reading: three requests, and the last one held 35,271.
    "usage": {"input_tokens": 66, "cache_creation_input_tokens": 3840,
              "cache_read_input_tokens": 97999, "output_tokens": 683},
    "modelUsage": {
        "claude-haiku-4-5-20251001": {
            "inputTokens": 1187, "outputTokens": 12, "costUSD": 0.001247,
            "contextWindow": 200000, "maxOutputTokens": 32000},
        "claude-fable-5-1": {
            "inputTokens": 100, "outputTokens": 2634, "costUSD": 0.5789645,
            "contextWindow": 1000000, "maxOutputTokens": 64000}},
    "permission_denials": []}


def envelope(**over):
    out = json.loads(json.dumps(RESULT))
    out.update(over)
    return out


async def main(root: Path) -> None:
    # -- 1. working until the turn is truly over ---------------------------
    session = running()
    await session.send_message("read the phase six plan and brief the engineers")
    check("a message in flight: working", session.status(), "working")

    await session._handle(TASKS_ONE)
    check("a background task is on the record as a count",
          session.meta()["background_tasks"], 1)
    await session._handle(TASKS_TWO)
    check("two of them", session.meta()["background_tasks"], 2)

    # The turn result can arrive while its subagents are running.
    await session._handle(envelope())
    check("the CLI's turn is over and the tasks are not: waiting",
          session.status(), "waiting")
    check("...and the count is still the CLI's",
          session.meta()["background_tasks"], 2)
    check("...and the record on disk says so, which is what a page reads",
          json.loads(session.meta_path.read_text())["status"], "waiting")

    # One finishes: the list is re-stated whole, and the count follows it.
    await session._handle(TASKS_ONE)
    check("a task that ended leaves the count",
          session.meta()["background_tasks"], 1)
    check("...and the one still running keeps it waiting",
          session.status(), "waiting")
    await session._handle(NOTIFICATION_TWO)               # the one that ended

    # The last one leaves the list. Its notification has not arrived yet --
    # one event later in the real log -- and `done` in between would be a
    # flicker on every background task this box ever runs.
    await session._handle(TASKS_NONE)
    check("the list is empty and the notification has not come: still waiting",
          session.status(), "waiting")
    check("...with nothing running, so the badge is gone",
          session.meta()["background_tasks"], 0)
    check("...which is why a page must not read the state off the count",
          (session.status(), session.meta()["background_tasks"]),
          ("waiting", 0))

    await session._handle(NOTIFICATION)
    check("the notification is the CLI handing it another turn: still waiting",
          session.status(), "waiting")
    await session._handle(INIT)
    check("...and the turn it opens is a turn", session._turn_active, True)
    check("...which is the model generating again: working",
          session.status(), "working")

    await session._handle(envelope())
    check("the turn that answered the notification ends: done",
          session.status(), "done")
    check("...and nothing is left behind it",
          (session.meta()["background_tasks"], session._resuming), (0, False))

    # Distinguish generating, waiting on background tasks and done.
    three = running()
    seen = []
    await three.send_message("launch a background job and finish your turn")
    seen.append(three.status())
    await three._handle(TASKS_ONE)
    seen.append(three.status())                  # the turn is still open
    await three._handle(envelope())
    seen.append(three.status())                  # the turn closed; the job runs
    await three._handle(TASKS_NONE)
    await three._handle(NOTIFICATION)
    seen.append(three.status())                  # still parked, no turn open
    await three._handle(INIT)
    seen.append(three.status())                  # generating again
    await three._handle(envelope())
    seen.append(three.status())
    check("working -> waiting -> done across one turn with a background job",
          seen,
          ["working", "working", "waiting", "waiting", "working", "done"])

    # A completion notification can precede the empty task list.
    first = running()
    await first.send_message("launch a background sleep and finish your turn")
    await first._handle(TASKS_ONE)
    await first._handle(envelope())
    await first._handle(NOTIFICATION)
    await first._handle(TASKS_NONE)
    await first._handle(INIT)
    check("notified before the list emptied: the follow-up turn is working",
          first.status(), "working")
    await first._handle(envelope())
    check("...and its result is the end: done, nothing behind it",
          (first.status(), first.working_behind(), first._awaiting), ("done", False, {}))

    # A permission still outranks both: a paused turn is the one thing the
    # person has to act on, and `waiting` would hide it.
    outranked = running()
    await outranked.send_message("launch one, then ask me something")
    await outranked._handle(TASKS_ONE)
    await outranked._handle(envelope())
    check("parked on a background job", outranked.status(), "waiting")
    await outranked._handle(PERMISSION_REQUEST)
    check("...and a permission from that job outranks it",
          outranked.status(), "needs_input")
    await outranked.answer_permission("req-1", "allow")
    check("...and it is parked again once answered",
          outranked.status(), "waiting")

    # An init after a result opens another turn.
    reopened = running()
    await reopened.send_message("what happened to the four briefs?")
    await reopened._handle(NOTIFICATION)
    await reopened._handle(INIT)
    await reopened._handle(envelope())
    check("a result that closed the notification's turn: done so far",
          reopened.status(), "done")
    await reopened._handle(INIT)
    check("...and the init that opens the real one puts it back to working",
          reopened.status(), "working")

    # -- 1c. the backstop under a task that never reports ------------------
    #
    # A task that leaves the CLI's list and never notifies must not pin a row
    # on `working` forever -- that is this defect with its sign flipped.
    settle = cfg.BACKGROUND_SETTLE_S
    cfg.BACKGROUND_SETTLE_S = 0.05
    try:
        stuck = running()
        await stuck.send_message("launch one and lose it")
        await stuck._handle(TASKS_ONE)
        await stuck._handle(TASKS_NONE)
        await stuck._handle(envelope())
        check("a task gone without a notification holds it waiting",
              stuck.status(), "waiting")
        await asyncio.sleep(0.2)
        check("...but not forever: the backstop settles it",
              stuck.status(), "done")
        check("...and writes the record, so a page that is watching is told",
              json.loads(stuck.meta_path.read_text())["status"], "done")
    finally:
        cfg.BACKGROUND_SETTLE_S = settle

    # -- 1d. the process going takes its children with it ------------------
    ended = running()
    await ended.send_message("launch one and end the process")
    await ended._handle(TASKS_TWO)
    check("two running", ended.meta()["background_tasks"], 2)
    ended.proc.returncode = 0
    await ended.stop()
    await ended._reap()
    check("a process that ended has no children left",
          ended.meta()["background_tasks"], 0)
    check("...and a turn that never produced a result is not done",
          ended.status(), "failed")

    # A record read back off disk by a broker that did not start it says zero
    # whatever the file says: a background task is a child of a process.
    lying = json.loads(ended.meta_path.read_text())
    lying["background_tasks"] = 2
    lying["status"] = "working"
    check("a record left claiming tasks by a broker that was killed",
          (broker_mod.normalise(dict(lying))["background_tasks"],
           broker_mod.normalise(dict(lying))["status"]),
          (0, "failed"))

    # And the same record stored `waiting` rather than `working`: its turn had
    # ENDED with a good result, so nothing more is coming and it reads `done`.
    # The count still goes to zero -- the children went with the process.
    parked = json.loads(ended.meta_path.read_text())
    parked["background_tasks"] = 2
    parked["status"] = "waiting"
    check("a record left waiting on children of a process that is gone",
          (broker_mod.normalise(dict(parked))["background_tasks"],
           broker_mod.normalise(dict(parked))["status"]),
          (0, "done"))

    # The child can receive SIGTERM before the parent records an idle shutdown.
    killed = running()
    await killed.send_message("what do I owe on the garden club renewal?")
    await killed._handle(envelope())
    check("a session that has answered: done", killed.status(), "done")
    killed.proc.returncode = 143              # systemd, not the broker
    await killed._reap()
    check("...whose process is killed from outside while it is idle: still done",
          killed.status(), "done")
    check("...and the record a page reads says so",
          json.loads(killed.meta_path.read_text())["status"], "done")

    unanswered = running()
    await unanswered.send_message("and this one is cut off mid-turn")
    unanswered.proc.returncode = 143
    await unanswered._reap()
    check("a turn that was in flight when the process went: failed",
          unanswered.status(), "failed")

    # -- 2. the count is on the stream as well as on the record ------------
    watched = running()
    queue: asyncio.Queue = asyncio.Queue()
    watched.watch(queue)
    await watched.send_message("say when the count changes")
    await watched._handle(TASKS_ONE)
    await watched._handle(TASKS_TWO)
    await watched._handle(TASKS_TWO)          # re-stated, unchanged
    await watched._handle(TASKS_NONE)
    told = []
    while not queue.empty():
        event = queue.get_nowait()
        if event.get("type") == "broker.background_tasks":
            told.append(event)
    check("one event per change, and none for a list re-stated unchanged",
          [event["count"] for event in told], [1, 2, 0])
    check("...flat, like every other broker event",
          sorted(k for k in told[0] if k != "seq"),
          ["count", "session_id", "tasks", "type"])
    check("...naming the tasks the CLI named",
          [task["description"] for task in told[1]["tasks"]],
          ["Four briefs: broker tail/seen, page x2, deploy",
           "Page audit: two capped jobs' points"])
    check("...and numbered, so a reconnecting client is replayed it",
          told[0]["seq"] > 0, True)
    check("...which means it is in the log",
          [json.loads(line)["count"]
           for line in watched.log_path.read_text().splitlines()
           if json.loads(line).get("type") == "broker.background_tasks"],
          [1, 2, 0])

    # -- 3. the context figures --------------------------------------------
    counted = running()
    await counted.send_message("what did that cost?")
    await counted._handle(SUBAGENT_ASSISTANT)
    await counted._handle(ASSISTANT)
    closing = envelope()
    await counted._handle(closing)
    turn = counted.meta()["turns"][-1]
    check("the turn's context is the LAST top-level request's, added up",
          turn["context"]["used"], 32 + 548 + 34691)
    check("...not the result envelope's own usage, which is the turn summed",
          turn["context"]["used"] == (66 + 3840 + 97999), False)
    check("...and not a subagent's, which ran on another model entirely",
          turn["context"]["used"] == (4 + 900000), False)
    check("the window is the one the CLI states for the model it ran on",
          turn["context"]["window"], 1000000)
    check("...and not the other model in the same envelope",
          turn["context"]["window"] == 200000, False)
    check("the event that closes the turn carries both",
          closing["context"], {"used": 35271, "window": 1000000})
    check("...the same numbers the record carries",
          closing["context"], turn["context"])

    # A turn whose assistant messages named no model states neither figure
    # rather than inheriting the last turn's.
    await counted.send_message("and again")
    await counted._handle(envelope(modelUsage={}))
    check("a turn with nothing to read states null, never the last turn's",
          counted.meta()["turns"][-1]["context"], {"used": None, "window": None})

    # A turn record written before these figures existed answers them anyway.
    old = broker_mod.normalise(
        {"session_id": "old-one", "agent": "probe", "state": "exited",
         "turns": [{"ts": "2026-09-18T10:00:00Z", "total_cost_usd": 0.4}],
         "record_seq": 4})
    check("a turn from before the figures existed answers both as null",
          old["turns"][0]["context"], {"used": None, "window": None})

    # A turn replayed out of the Claude Code app's own session file carries
    # the same key, with the half of it that file can answer. One stream, two
    # halves, one shape: a page must not branch on where a turn came from.
    app_file = root / "app-session.jsonl"
    app_file.write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "promptSource": "sdk", "origin": {"kind": "human"},
         "timestamp": "2026-09-19T10:00:00Z",
         "message": {"role": "user", "content": "what did this cost?"}},
        {"type": "assistant", "timestamp": "2026-09-19T10:00:01Z",
         "message": {"role": "assistant", "model": "claude-fable-5-1",
                     "content": [{"type": "text", "text": "one moment"}],
                     "usage": {"input_tokens": 9,
                               "cache_read_input_tokens": 1000,
                               "cache_creation_input_tokens": 100}}},
        {"type": "assistant", "timestamp": "2026-09-19T10:00:02Z",
         "message": {"role": "assistant", "model": "claude-fable-5-1",
                     "content": [{"type": "text", "text": "done"}],
                     "usage": {"input_tokens": 11,
                               "cache_read_input_tokens": 1150,
                               "cache_creation_input_tokens": 40}}},
    ]) + "\n", encoding="utf-8")
    closings = [e for e in adoption.iter_events(app_file, "app-one")
                if e.get("type") == "result"]
    check("a replayed turn's boundary states the context too",
          closings[-1]["context"], {"used": 11 + 1150 + 40, "window": None})
    check("...the LAST request's, like a live turn's",
          closings[-1]["context"]["used"] == (9 + 1000 + 100), False)

    # -- 3b. history_bytes, on an adopted record that has not been forked ---
    adopted = broker_mod.Session("probe", "adopted-one")
    adopted.origin = adoption.ORIGIN_APP
    adopted.source = "/home/loam/.claude/projects/probe/adopted-one.jsonl"
    adopted.source_size = 4_812_733
    check("an adopted record says what a first message would re-send",
          adopted.meta()["history_bytes"], 4_812_733)
    adopted.cli_session_id = "the-fork"
    check("...and once it is forked it does not: it runs on its own file",
          adopted.meta()["history_bytes"], None)
    check("a session the broker started never has one",
          running().meta()["history_bytes"], None)
    check("...and a record off disk answers it the same way",
          broker_mod.normalise({"session_id": "x", "agent": "probe",
                                "origin": adoption.ORIGIN_APP,
                                "source_size": 12345})["history_bytes"],
          12345)


root = Path(tempfile.mkdtemp(prefix="loam-broker-background-test-"))
saved = {name: getattr(cfg, name) for name in
         ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "AI_LABS")}
try:
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.AI_LABS = root / "agents"
    (cfg.AI_LABS / "probe").mkdir(parents=True)
    (cfg.AI_LABS / "probe" / "agent.json").write_text("{}", encoding="utf-8")
    asyncio.run(main(root))
finally:
    for name, value in saved.items():
        setattr(cfg, name, value)
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
