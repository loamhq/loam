#!/usr/bin/env python3
"""The turn cap's unit, read back from a session file shaped like the CLI's.

``--max-turns`` counts API round trips; the envelope's ``num_turns`` on a
finished run counts user messages, one per tool result. lib/loam_session.py
is where the launchers get the first number, and this proves it counts what
the module says it counts against a fixture with the real transcript shape:
one entry per content block, all blocks of one response sharing a message
id, one user entry per tool result.

Run: python3 tests/test_session_turns.py
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_session  # noqa: E402

results = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:52} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


def entry(kind: str, **fields) -> str:
    return json.dumps({"type": kind, **fields})


# Three responses. The first carries six parallel tool calls, so it is seven
# entries (thinking + six tool_use) on one id and six tool_result user
# entries after it. The last is text only. 3 round trips, 13 user entries.
SID = "11111111-2222-3333-4444-555555555555"
lines = [entry("queue-operation", operation="enqueue"),
         entry("user", message={"role": "user", "content": "go"})]
for i, (mid, calls) in enumerate([("msg_A", 6), ("msg_B", 6), ("msg_C", 0)]):
    lines.append(entry("assistant", message={"id": mid, "content": [
        {"type": "thinking", "thinking": "..."}]}))
    for n in range(calls):
        lines.append(entry("assistant", message={"id": mid, "content": [
            {"type": "tool_use", "name": "Read", "id": f"tu_{i}_{n}"}]}))
    for n in range(calls):
        lines.append(entry("user", message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"tu_{i}_{n}"}]}))
lines.append(entry("assistant", message={"id": "msg_C", "content": [
    {"type": "text", "text": "done"}]}))
lines.append("this line is not json")
lines.append(entry("assistant", message={"content": []}))   # no id: not a trip

root = Path(tempfile.mkdtemp(prefix="loam-session-test-"))
saved = loam_session.SESSIONS_DIR
try:
    project = root / "-home-somebody-scratch"
    project.mkdir()
    (project / f"{SID}.jsonl").write_text("\n".join(lines) + "\n")
    loam_session.SESSIONS_DIR = root

    check("finds the file under any project directory",
          loam_session.session_file(SID), project / f"{SID}.jsonl")
    check("round trips are distinct assistant message ids",
          loam_session.round_trips(SID), 3)
    check("user entries in the fixture (what num_turns would say)",
          sum(1 for l in lines if l.startswith('{"type": "user"')), 13)
    check("unknown session gives None, not an error",
          loam_session.round_trips("no-such-session"), None)
    check("missing session id gives None",
          loam_session.round_trips(None), None)

    # Forked transcripts begin with copied history. Count only the new turns.
    FORK = "99999999-8888-7777-6666-555555555555"
    forked = lines + [
        entry("user", message={"role": "user", "content": "carry on"}),
        entry("assistant", message={"id": "msg_D", "content": [
            {"type": "text", "text": "reading"}]}),
        entry("assistant", message={"id": "msg_E", "content": [
            {"type": "text", "text": "done"}]}),
    ]
    (project / f"{FORK}.jsonl").write_text("\n".join(forked) + "\n")
    check("a fork's own round trips exclude the parent's",
          loam_session.round_trips(FORK, SID), 2)
    check("counted whole, a fork reports the parent's too (the defect)",
          loam_session.round_trips(FORK), 5)
    check("an unreadable parent does not zero the count",
          loam_session.round_trips(FORK, "no-such-session"), 5)
    check("a parent with no transcript entries subtracts nothing",
          loam_session.round_trips(FORK, None), 5)

    check("subtype error_max_turns is a cap hit",
          loam_session.turn_cap_hit({"subtype": "error_max_turns"}), True)
    check("terminal_reason max_turns is a cap hit",
          loam_session.turn_cap_hit({"subtype": "success",
                                     "terminal_reason": "max_turns"}), True)
    check("a success envelope is not",
          loam_session.turn_cap_hit({"subtype": "success", "num_turns": 129,
                                     "terminal_reason": "completed"}), False)
    check("an empty envelope is not", loam_session.turn_cap_hit({}), False)
finally:
    loam_session.SESSIONS_DIR = saved
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
