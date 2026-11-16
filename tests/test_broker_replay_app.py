#!/usr/bin/env python3
"""A conversation the owner had in the Claude Code app, replayed by the broker.

The translation (`loam_adopt.iter_events`) is a pure function of the CLI's
own session file, so it is tested against a real one:
`fixtures/app-session.jsonl` is an Iris session from 2026-09-11 copied off
this box with every id in it replaced -- session id, entry uuids, prompt and
request ids, tool-use ids -- and, on 2026-11-15, every word a person or an
agent wrote in it replaced with neutral text of the same shape. The entries,
their order and their fields are the CLI's own. A hand-written file would
only ever prove that the translation matches the shape I imagined the
CLI writes, and the schema is internal and version-unstable upstream; this
fixture is what will tell us when it moves, the way
`fixtures/golden-session.jsonl` does for the transcript writer.

What has to hold, and each of these is a way a replayed chat goes wrong:

  1. **Both sides of the conversation.** The CLI does not echo what a person
     typed, so a replay built only out of what the file calls messages shows
     answers with no questions -- the 2026-09-16 defect, in a new place.
  2. **One event per content block.** It is what the CLI streams and what the
     page reads: a sentence said before the tools and each tool call are
     separate events sharing a message.
  3. **Nothing private, nothing machine.** Thinking blocks and subagent
     traffic never reach the stream.
  4. **A turn boundary per turn**, carrying what the file knows and null
     where it knows nothing -- a cost the box never saw is not one to invent.
  5. **The numbering is continuous with what comes next.** The replayed
     events are 1..N and the broker's own log begins at N+1, so a turn taken
     in an adopted session tomorrow appends to its history rather than
     renumbering on top of it. That is the whole reason `record_seq` is set
     from the same count.

Run: python3 tests/test_broker_replay_app.py
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

FIXTURE = AGENTS / "fixtures" / "app-session.jsonl"
# The id the fixture's own entries carry, and the id the record wears.
APP_ID = "5e55107a-0000-4000-8000-000000000001"

results = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} got={got!r:.140}")
    if not ok:
        print(f"      wanted {want!r:.200}")


def blocks_of(event: dict) -> list:
    content = (event.get("message") or {}).get("content")
    return content if isinstance(content, list) else []


def kinds(events: list[dict]) -> list[str]:
    """One word per event, the way a page would sort them: the event's type,
    and for a message the type of the single block it carries."""
    out = []
    for event in events:
        block = blocks_of(event)
        out.append(f"{event['type']}/{block[0]['type']}" if block
                   else event["type"])
    return out


# -- the translation, on the real file -------------------------------------

events = list(adoption.iter_events(FIXTURE, APP_ID))

check("the real session file translates into events", len(events), 21)
check("...the conversation reads as it happened",
      kinds(events),
      ["broker.user_message", "assistant/text", "assistant/tool_use",
       "user/tool_result", "assistant/tool_use", "user/tool_result",
       "assistant/tool_use", "user/tool_result", "assistant/tool_use",
       "user/tool_result", "assistant/tool_use", "user/tool_result",
       "assistant/tool_use", "user/tool_result", "assistant/tool_use",
       "user/tool_result", "assistant/text", "result",
       "broker.user_message", "assistant/text", "result"])
check("...a person's turn is the broker's own event, with their words in it",
      (events[0]["type"], events[0]["text"][:51]),
      ("broker.user_message",
       "Something that I don't know if I've told you before"))
check("...every message carries exactly one block",
      sorted({len(blocks_of(e)) for e in events if blocks_of(e)}), [1])
check("...no thinking reaches the stream",
      [b["type"] for e in events for b in blocks_of(e)
       if b["type"] in adoption.PRIVATE_BLOCKS], [])
check("...and the file does hold thinking that had to be dropped",
      sum(1 for entry in adoption.lt.iter_entries(FIXTURE)
          for block in ((entry.get("message") or {}).get("content") or [])
          if isinstance(block, dict) and block.get("type") == "thinking"), 5)
check("...an assistant event is top-level, as a page tests for",
      {e.get("parent_tool_use_id") for e in events
       if e["type"] == "assistant"}, {None})
check("...each event says which session it belongs to",
      {e["session_id"] for e in events}, {APP_ID})
check("...and says it was adopted rather than watched happening",
      {e["adopted"] for e in events}, {True})

turns = [e for e in events if e["type"] == "result"]
check("a turn boundary per turn the person took", len(turns), 2)
check("...carrying the model the file states, and no cost it does not",
      [(t["model"], t["total_cost_usd"], t["usage"]) for t in turns],
      [("claude-fable-5-1", None, None), ("claude-fable-5-1", None, None)])
check("...and the turn's own span, from the file's timestamps",
      [(t["started_at"], t["ts"], t["duration_ms"]) for t in turns],
      [("2026-09-11T22:25:32Z", "2026-09-11T22:26:35Z", 63000),
       ("2026-09-11T22:50:33Z", "2026-09-11T22:50:52Z", 19000)])
check("...a turn that did not fail says so in both places the CLI says it",
      [(t["is_error"], t["subtype"]) for t in turns],
      [(False, "success"), (False, "success")])

check("the same file twice is the same events",
      [json.dumps(e, sort_keys=True) for e in
       adoption.iter_events(FIXTURE, APP_ID)],
      [json.dumps(e, sort_keys=True) for e in events])

summary = adoption.summarise(FIXTURE, APP_ID, "iris")
check("what the record is made of is counted off the same translation",
      (summary["events"], summary["created"], summary["last_event_at"]),
      (21, "2026-09-11T22:25:32Z", "2026-09-11T22:50:52Z"))
check("...and the last thing the persona said is the last thing it said",
      summary["last_message"][:44],
      "So it wasn't dictation eating a name. You sa")

# The person's second turn in this real conversation was a photograph with a
# line of text under it. `broker.user_message` has one field for what a
# person sent and it is `text`, so the photograph is not replayed -- stated
# here because it is a limit of the event shape and not an accident of the
# translation, and because the file proves a real conversation hits it.
pictures = [entry for entry in adoption.lt.iter_entries(FIXTURE)
            if any(isinstance(b, dict) and b.get("type") == "image"
                   for b in ((entry.get("message") or {}).get("content") or []))]
check("a photograph a person sent is in the file and not in the replay",
      (len(pictures), [b["type"] for e in events for b in blocks_of(e)
                       if b["type"] == "image"]), (1, []))


# -- the shapes the real file does not hold --------------------------------
#
# Each was read off this box's session files on 2026-09-20 and each is rare:
# a `fallback` block (3 across 27 files), an assistant message the CLI wrote
# itself when the spend limit was reached, and the CLI's own self-talk.

def write(entries: list, folder: Path, session_id: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
    return path


ODD_ID = "5e55107a-0000-4000-8000-000000000002"
ODD = [
    {"type": "user", "promptSource": "sdk", "origin": {"kind": "human"},
     "timestamp": "2026-09-19T10:00:00Z",
     "message": {"role": "user", "content": "carry on"}},
    # The CLI swapped the model mid-turn and said so in a block of its own.
    {"type": "assistant", "timestamp": "2026-09-19T10:00:01Z",
     "message": {"role": "assistant", "model": "claude-fable-5-1",
                 "content": [{"type": "fallback",
                              "from": {"model": "claude-fable-5-1"},
                              "to": {"model": "claude-opus-4-8"}}]}},
    # A message the CLI wrote in the model's place. `<synthetic>` is not a
    # model and the turn it ends did not succeed.
    {"type": "assistant", "timestamp": "2026-09-19T10:00:02Z",
     "isApiErrorMessage": True, "apiErrorStatus": None,
     "message": {"role": "assistant", "model": "<synthetic>",
                 "content": [{"type": "text",
                              "text": "You've hit your monthly spend limit."}]}},
    # The CLI talking to itself: a slash command, the local-command caveat,
    # and a subagent reporting back. None of it is a person and none of it is
    # a tool result.
    {"type": "user", "timestamp": "2026-09-19T10:00:03Z",
     "message": {"role": "user",
                 "content": "<command-name>/clear</command-name>"}},
    {"type": "user", "promptSource": "sdk",
     "origin": {"kind": "task-notification"},
     "timestamp": "2026-09-19T10:00:04Z",
     "message": {"role": "user", "content": "<task-notification>done"}},
    # A subagent's own sentence, which is never the persona's.
    {"type": "assistant", "isSidechain": True,
     "timestamp": "2026-09-19T10:00:05Z",
     "message": {"role": "assistant", "model": "claude-fable-5-1",
                 "content": [{"type": "text", "text": "a subagent's line"}]}},
]


async def main(root: Path) -> None:
    odd = write(ODD, root / "projects" / "odd", ODD_ID)
    events = list(adoption.iter_events(odd, ODD_ID))
    check("a block the translation has no name for is passed through, not lost",
          kinds(events),
          ["broker.user_message", "assistant/fallback", "assistant/text",
           "result"])
    boundary = events[-1]
    check("...the CLI's own failure message ends the turn as a failure",
          (boundary["is_error"], boundary["subtype"]),
          (True, adoption.ERROR_SUBTYPE))
    check("...and `<synthetic>` is not recorded as the model it ran on",
          boundary["model"], "claude-fable-5-1")
    check("...the CLI's self-talk and a subagent's line are not the thread",
          [e for e in events if e["type"] == "user"], [])

    # -- through the broker's record and replay ---------------------------
    workspace = cfg.workspace("iris")
    (workspace / "agent.json").parent.mkdir(parents=True, exist_ok=True)
    (workspace / "agent.json").write_text(
        json.dumps({"name": "iris", "display": "Iris", "emoji": "🪟"}),
        encoding="utf-8")
    folder = adoption.project_dir(workspace)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, folder / f"{APP_ID}.jsonl")

    broker = broker_mod.Broker()
    report = broker.adopt_app_sessions()
    check("the scan adopts the session the fixture is",
          [r["session_id"] for r in report["adopted"]], [APP_ID])

    # What the record says and what its replay reads (a session's events
    # stream, which carried both to a page, retired with the classic chat
    # view on 2026-11-12; `replay` is still the one reading of its history).
    record = broker.load(APP_ID).meta()
    check("the record counts the conversation it adopted",
          (record["record_seq"], record["replayed"]), (21, 21))
    history = broker.load(APP_ID).replay(0)
    check("...and the events are numbered from 1 with no gaps",
          [e["seq"] for e in history], list(range(1, 22)))
    check("a read from mid-history gets the rest and no more",
          [e["seq"] for e in broker.load(APP_ID).replay(17)], [18, 19, 20, 21])

    # -- and the next job's numbering -------------------------------------
    #
    # Nothing forks an adopted session yet. What must hold when something
    # does is that its events land after the replayed ones, so this writes
    # one where a process would and reads the stream back whole.
    session = broker.load(APP_ID)
    check("the session numbers its next event after the history",
          session.seq, 21)
    await session.emit({"type": "broker.user_message", "session_id": APP_ID,
                        "text": "and since then?"})
    session.save()
    replayed = broker.load(APP_ID).replay(0)
    check("...a turn taken here appends to the conversation, in one stream",
          ([e["seq"] for e in replayed][-3:],
           [e.get("adopted") for e in replayed][-2:]),
          ([20, 21, 22], [True, None]))
    check("...and the record's position is the whole of it now",
          (broker.load(APP_ID).replayed, broker.load(APP_ID).seq), (21, 22))

    # The app kept writing to its own file after we counted it. The record
    # holds 21, so the 22nd translated event must NOT appear -- the broker's
    # own log has that number already.
    with (folder / f"{APP_ID}.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(
            {"type": "user", "promptSource": "sdk",
             "origin": {"kind": "human"},
             "timestamp": "2026-09-21T09:00:00Z",
             "message": {"role": "user", "content": "said in the app since"}})
            + "\n")
    after = broker.load(APP_ID).replay(0)
    check("what the app said after the scan does not take a number twice",
          ([e["seq"] for e in after], len(after)),
          (list(range(1, 23)), 22))
    check("...and the scan leaves a session with a log of our own alone",
          APP_ID in broker._settled(), True)


root = Path(tempfile.mkdtemp(prefix="broker-replay-app-"))
saved = {name: getattr(cfg, name)
         for name in ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "USAGE_PATH",
                      "AI_LABS")}
saved_adopt = {name: getattr(adoption, name)
               for name in ("PROJECTS", "TRANSCRIPT_STATE", "candidates")}
try:
    # The scan's clock, stood an hour after the fixture's last line: the
    # fixture was last spoken to on 2026-09-11, and on the wall clock the scan
    # has passed it over as older than WINDOW_DAYS since 09-25.
    scanned = adoption.candidates
    fixture_now = adoption.epoch_of(adoption.last_timestamp(FIXTURE)) + 3600

    def candidates(*args, **kwargs):
        kwargs.setdefault("now", fixture_now)
        return scanned(*args, **kwargs)

    adoption.candidates = candidates
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.USAGE_PATH = cfg.STATE_DIR / "provider-usage.json"
    cfg.SESSION_DIR.mkdir(parents=True)
    cfg.AI_LABS = root / "agents"
    adoption.PROJECTS = root / "projects"
    adoption.TRANSCRIPT_STATE = root / "transcripts"
    asyncio.run(main(root))
finally:
    for name, value in saved.items():
        setattr(cfg, name, value)
    for name, value in saved_adopt.items():
        setattr(adoption, name, value)
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
