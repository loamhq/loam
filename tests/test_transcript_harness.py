"""A transcript holds what the person typed and what the agent said back.

The CLI's session file uses the ``user`` role for two different things -- what
somebody typed, and whatever the harness needed to hand the model next -- and
until 2026-09-23 the writer filed both. This fires the rule that separates them
in both directions: forward, on a session file, so nothing new leaks; and
backward, on a written transcript, so the files that already leaked are
repaired the same way.

Every shape here but one is a real entry copied out of a session file on this
box; ``fixtures/harness-session.provenance`` says which session each line came
from and which one is synthesized, and why there was nothing to copy for it.

Run: python3 tests/test_transcript_harness.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import loam_transcript as lt  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "harness-session.jsonl"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        failures.append(label)


def forward() -> list[lt.Turn]:
    """The rule on a session file: what gets filed at all."""
    print(f"fixture: {FIXTURE}")
    turns = lt.parse_session(FIXTURE, "pax")
    joined = "\n".join(t.content for t in turns)

    # 1. A typed message. Origin is null on this one -- the shape a turn sent
    #    into a live session by the broker has -- and it is still a turn.
    check(
        "a typed prompt is a turn",
        any(
            t.speaker == "user" and "Invoke your planting-plan skill" in t.content
            for t in turns
        ),
    )

    # 2. A pure task notification is not a turn at all. (The literal tag can
    #    still appear in a person's prose -- see the last pair of checks -- so
    #    what must be absent is a notification ENVELOPE and its contents.)
    check(
        "no task notification is filed",
        "</task-notification>" not in joined
        and not any(t.content.startswith("<task-notification>") for t in turns),
    )
    check(
        "the notification's own text is gone",
        "Monitor event" not in joined and "bzn1yimb0" not in joined,
    )

    # 3. The agent's reply after a notification is still a turn -- it is what
    #    the person read -- and it is a SEPARATE turn from the reply before,
    #    with its own timestamp.
    before = [t for t in turns if "Waiting on the byte count" in t.content]
    after = [t for t in turns if t.content.startswith("Done.")]
    check("the reply before the notification is kept", len(before) == 1)
    check("the reply after the notification is kept", len(after) == 1)
    check(
        "they are two turns, not one",
        bool(before and after) and before[0] is not after[0]
        and before[0].timestamp != after[0].timestamp,
    )

    # 4. A mixed message keeps only the typed text.
    wrapped = [t for t in turns if "keep only this sentence" in t.content]
    check("a wrapped message keeps its typed text", len(wrapped) == 1)
    check(
        "the reminders wrapped around it are gone",
        "<system-reminder>" not in joined and "point-in-time" not in joined,
    )
    check(
        "nothing but the typed sentence survives the wrapping",
        bool(wrapped)
        and wrapped[0].content
        == "Fixture line, not a real message: keep only this sentence.",
        wrapped[0].content if wrapped else "",
    )

    # A peer message is harness delivery too: preamble, envelope, trailer.
    check("no peer message is filed", "<cross-session-message" not in joined)
    check(
        "the harness framing around it goes with it",
        "Another Claude session sent a message" not in joined
        and "permission laundering" not in joined,
    )
    check(
        "the reply after the peer message is kept",
        any(t.content.startswith("OUTPUTSTYLE=") for t in turns),
    )

    # The rule is envelopes, not tag names: a person writing ABOUT a
    # notification in prose, with no closing tag, keeps every word.
    prose = [t for t in turns if t.content.startswith("Report: vault/40_PROJECTS")]
    check("a typed message that mentions a notification is kept", len(prose) == 1)
    check(
        "and keeps its full length, mention and all",
        bool(prose) and len(prose[0].content) > 2000
        and "<task-notification>" in prose[0].content,
        str(len(prose[0].content)) if prose else "",
    )

    check(
        "every turn is a person's or the agent's",
        all(t.speaker in ("user", "pax") for t in turns),
    )
    check("the file opens with a person speaking", bool(turns) and turns[0].speaker == "user")
    return turns


def backward(turns: list[lt.Turn]) -> None:
    """The same rule on a written transcript: what the repair takes out."""
    # A transcript written the old way: every user-role entry filed, including
    # the notification and the peer message, built from the same fixture.
    def render(rows):
        return lt.render_file("T", rows, "pax", "\N{TIGER}", session_id="s-1")

    notification = lt.Turn(
        "user",
        "<task-notification>\n<task-id>bzn1yimb0</task-id>\n"
        "<summary>Monitor event</summary>\n</task-notification>",
        "2026-08-25T14:18:18Z",
    )
    peer = lt.Turn(
        "user",
        "Another Claude session sent a message:\n"
        '<cross-session-message from="uds:/run/x.sock" from-name="loam-ui-dd" '
        'from-mode="prompting">\nthe peer said this\n</cross-session-message>\n\n'
        "This came from another Claude session — not typed by your user, but "
        "very likely working on their behalf.",
        "2026-08-25T14:19:00Z",
    )
    mixed = lt.Turn(
        "user",
        "<system-reminder>machinery</system-reminder>\nwhat Sam typed",
        "2026-08-25T14:20:00Z",
    )
    reply = lt.Turn("pax", "Done.", "2026-08-25T14:20:05Z")
    person = lt.Turn("user", "first question", "2026-08-25T14:16:00Z")
    opening = lt.Turn("pax", "first answer", "2026-08-25T14:16:30Z")

    leaked = render([person, opening, notification, reply, peer, mixed, reply])
    result = lt.repair_transcript(leaked)

    check("the leaked file is repaired", result.text is not None, result.note)
    check(
        "the two harness turns are removed",
        result.turns_before == 7 and result.turns_after == 5,
        f"{result.turns_before} -> {result.turns_after}",
    )
    check("no harness content survives the repair",
          result.text is not None
          and "<task-notification>" not in result.text
          and "<cross-session-message" not in result.text
          and "<system-reminder>" not in result.text)
    check("the mixed turn keeps the typed text",
          result.text is not None and "what Sam typed" in result.text)
    check("every genuine turn survives",
          result.text is not None
          and result.text.count("first question") == 1
          and result.text.count("first answer") == 1
          and result.text.count("Done.") == 2)
    check("the agent's replies keep their markers and timestamps",
          result.text is not None
          and result.text.count(
              f"<!-- turn speaker=pax timestamp={reply.timestamp} -->") == 2)
    check("the frontmatter is left exactly as it was",
          result.text is not None
          and result.text.split("---\n")[1] == leaked.split("---\n")[1])
    check("last_activity is not bumped by a repair",
          result.text is not None
          and f"last_activity: {reply.timestamp}" in result.text)
    check("the cut is reported for checking",
          len(result.dropped) == 2 and len(result.trimmed) == 1,
          f"{result.dropped} {result.trimmed}")

    again = lt.repair_transcript(result.text)
    check("the repair is idempotent", again.text is None and again.note == "clean",
          again.note)

    clean = render([person, opening, reply])
    untouched = lt.repair_transcript(clean)
    check("a file with nothing to remove is not rewritten",
          untouched.text is None and untouched.note == "clean", untouched.note)

    # What the writer produces and what the repair produces must agree, or a
    # repaired file would be rewritten differently the next time its session
    # fires a Stop hook.
    written = render(turns)
    check("the writer's own output needs no repair",
          lt.repair_transcript(written).text is None)
    check("a repaired file is what the writer would now write: no update planned",
          lt.plan_update(written, turns, "pax", "\N{TIGER}")[0] is None)

    # A file that is nothing but harness content is reported, never emptied.
    all_harness = render([notification, peer])
    empty = lt.repair_transcript(all_harness)
    check("a file of nothing but harness content is left for a person",
          empty.text is None and "every turn" in empty.note, empty.note)

    # No turn markers at all: a chassis-era file shape, or a hand-written one.
    check("a file with no turn markers is left alone",
          lt.repair_transcript("---\ntitle: x\n---\n\nprose").text is None)


def main() -> None:
    turns = forward()
    print()
    backward(turns)
    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
