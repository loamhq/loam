#!/usr/bin/env python3
"""The chats a broker restart ended, as a job's receipt lists them.

Since 2026-10-13. A Loam job that restarts the broker ends every resident
chat, and until now the receipt named them only if the engineer chose to
write them out (the 10-07 22:13Z deploy, job 20261007-151253-50a6668a, did
by hand). `lib/loam_broker.ended_by_restart` reads the unit's stop out of
the journal and each chat's `broker.exit` out of its event log; bin/engineer-
job renders one line per chat in the launcher's half.

The fixture is that restart, copied in shape: the journal's Stopping /
Stopped / Started stamps to the microsecond, the two chats it ended with
their titles, persona, provider, exit stamps and turns -- and a third chat
the idle reaper ended eight minutes earlier, which is the same event and
must not be listed. Each record carries turns AFTER the restart too, as the
live ones do (both chats were resumed that evening), so "last turn" has to
be the last one before the exit and not the record's last.

Run: python3 tests/test_ended_chats.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_broker  # noqa: E402

AGENTS = Path(__file__).resolve().parent.parent

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:60} {got!r}"[:240]
          + ("" if ok else f"   want {want!r}"))


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def utc(*parts) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc)


def entry(message: str, moment: datetime) -> dict:
    return {"MESSAGE": message,
            "__REALTIME_TIMESTAMP": str(int(moment.timestamp() * 1_000_000))}


UNIT = "loam-session-broker.service - Loam session broker: persona sessions for the web half"
JOURNAL = [
    entry(f"Started {UNIT}.", utc(2026, 10, 7, 20, 57, 1)),
    entry(f"Stopping {UNIT}...", utc(2026, 10, 7, 22, 13, 50, 955847)),
    entry(f"Stopped {UNIT}.", utc(2026, 10, 7, 22, 13, 52, 146954)),
    entry("loam-session-broker.service: Consumed 3min 30.875s CPU time.",
          utc(2026, 10, 7, 22, 13, 52, 147004)),
    entry(f"Started {UNIT}.", utc(2026, 10, 7, 22, 13, 52, 172327)),
]

CHATS = {
    "e8810a52-52ea-4441-8d6e-5871951fbf3f": {
        "title": "Calendar integration research for Acme", "agent": "ada",
        "provider": "claude-code", "exit": "2026-10-07T22:13:51Z",
        "turns": ["2026-10-07T22:01:01Z", "2026-10-07T22:12:50Z",
                  "2026-10-07T22:20:51Z", "2026-10-07T22:26:35Z"],
    },
    "fac1b2dc-a0e7-4409-a63f-eee6f08f8d36": {
        "title": "Wren Phase 6 continuation and tech debt review", "agent": "ada",
        "provider": "claude-code", "exit": "2026-10-07T22:13:52Z",
        "turns": ["2026-10-07T21:38:30Z", "2026-10-08T01:15:15Z"],
    },
    # Reaped for idleness before the restart: a broker.exit, not the restart's.
    "0a0a0a0a-0000-4000-8000-000000000000": {
        "title": "An idle chat", "agent": "iris", "provider": "codex",
        "exit": "2026-10-07T22:05:00Z", "turns": ["2026-10-07T21:05:00Z"],
    },
}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="loam-ended-chats-"))
    try:
        for sid, chat in CHATS.items():
            events = [
                {"type": "result", "session_id": sid, "seq": 1},
                {"type": "broker.exit", "session_id": sid, "exit_code": 143,
                 "ts": chat["exit"], "seq": 2},
            ]
            (tmp / f"{sid}.jsonl").write_text(
                "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
            (tmp / f"{sid}.json").write_text(json.dumps({
                "session_id": sid, "agent": chat["agent"], "title": chat["title"],
                "provider": chat["provider"], "state": "exited",
                "turns": [{"ts": t, "subtype": "success"} for t in chat["turns"]],
            }), encoding="utf-8")

        started, finished = utc(2026, 10, 7, 22, 12, 54), utc(2026, 10, 7, 22, 15, 29)
        stops = loam_broker.broker_stops(started, finished, entries=JOURNAL)
        check("one stop, Stopping to the Started after it", stops,
              [(utc(2026, 10, 7, 22, 13, 50, 955847), utc(2026, 10, 7, 22, 13, 52, 172327))])

        unfinished = loam_broker.broker_stops(started, finished, entries=JOURNAL[:3])
        check("a stop with no Started yet runs to the job's end",
              unfinished[0][1], finished)

        ended = loam_broker.ended_by_restart(started, finished, stops=stops,
                                             session_dir=tmp)
        check("the restart's two chats, in the order they ended",
              [e["session_id"][:8] for e in ended], ["e8810a52", "fac1b2dc"])
        check("the reaped chat is not one of them",
              any(e["agent"] == "iris" for e in ended), False)
        check("the first: title, persona, provider, last turn before the exit",
              {k: ended[0][k] for k in ("title", "agent", "provider", "ended", "last_turn")},
              {"title": "Calendar integration research for Acme", "agent": "ada",
               "provider": "claude-code", "ended": "2026-10-07T22:13:51Z",
               "last_turn": "2026-10-07T22:12:50Z"})
        check("the second's last turn is 21:38:30Z, not the record's 01:15Z",
              ended[1]["last_turn"], "2026-10-07T21:38:30Z")
        check("no stop, no chats",
              loam_broker.ended_by_restart(started, finished, stops=[], session_dir=tmp), [])

        job = load("engineer_job_under_test", AGENTS / "bin" / "engineer-job")
        text = job.ended_chats(None, started, finished, chats=ended)
        print(text)
        check("the receipt carries one line per ended chat",
              [line for line in text.splitlines() if line.startswith("- ")], [
                  '- "Calendar integration research for Acme" — ada, claude-code, '
                  'last turn 2026-10-07T22:12:50Z; ended 2026-10-07T22:13:51Z (`e8810a52`)',
                  '- "Wren Phase 6 continuation and tech debt review" — ada, claude-code, '
                  'last turn 2026-10-07T21:38:30Z; ended 2026-10-07T22:13:52Z (`fac1b2dc`)',
              ])
        check("and says what it read them from",
              "ended 2 chats**, read from the unit's journal" in text, True)
        check("a job with no restart adds nothing",
              job.ended_chats(None, started, finished, chats=[]), "")

        class NotLoam:
            platform = False
        check("a project engineer's job is not looked at",
              job.ended_chats(NotLoam(), started, finished), "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
