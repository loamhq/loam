"""Check parsing against the provider schema captured in fixtures/golden-session.cli-version."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import loam_transcript as lt

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "golden-session.jsonl"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        failures.append(label)


def main() -> None:
    print(f"fixture: {FIXTURE}")
    turns = lt.parse_session(FIXTURE, "pax")

    check("two turns parsed", len(turns) == 2, f"got {len(turns)}")
    check("first turn is the user", turns[0].speaker == "user")
    check("second turn is the agent", turns[1].speaker == "pax")
    check(
        "user turn carries the prompt",
        "about six hours of sun" in turns[0].content,
    )
    check(
        "agent turn carries the reply",
        "direct sun" in turns[1].content,
    )
    check(
        "timestamps normalised to vault form",
        all(t.timestamp.endswith("Z") and len(t.timestamp) == 20 for t in turns),
        str([t.timestamp for t in turns]),
    )

    # Machinery must never reach the vault.
    joined = "\n".join(t.content for t in turns)
    check("no tool_use blocks leaked", "tool_use" not in joined)
    check("no tool_result blocks leaked", "tool_result" not in joined)

    rendered = lt.render_file(
        "A title", turns, "pax", session_id="abc-123"
    )

    check("frontmatter opens the file", rendered.startswith("---\ntitle: "))
    check(
        "field order matches the chassis writer",
        [line.split(":")[0] for line in rendered.split("---\n")[1].strip().split("\n")]
        == [
            "title",
            "created_at",
            "last_activity",
            "default_agent",
            "agents",
            "created_by",
            "last_modified_by",
            "surface",
            "session_id",
        ],
    )
    check("agent wiki-linked", 'default_agent: "[[Pax]]"' in rendered)
    check(
        "user turn marker",
        f"<!-- turn speaker=user timestamp={turns[0].timestamp} -->\n### User\n\n"
        in rendered,
    )
    check(
        "agent turn marker with the plain name as its header (no emoji, 2026-11-17)",
        f"<!-- turn speaker=pax timestamp={turns[1].timestamp} -->\n"
        f"### Pax\n\n" in rendered,
    )
    check("no telegram fields", "telegram_" not in rendered)
    check("surface is a positive fact", "surface: claude-code" in rendered)
    check("session id is traceable", "session_id: abc-123" in rendered)
    check(
        "session id omitted when absent (chassis files get no empty field)",
        "session_id" not in lt.render_file("T", turns, "pax", session_id=None),
    )

    # A session after /clear can open with an assistant acknowledgement.
    import json as _json
    import tempfile

    rows = [
        {"type": "assistant", "isSidechain": False,
         "timestamp": "2026-08-26T00:00:00Z",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "No response requested."}]}},
        {"type": "user", "isSidechain": False, "promptSource": "user",
         "timestamp": "2026-08-26T00:00:05Z",
         "message": {"role": "user", "content": "actual first question"}},
        {"type": "assistant", "isSidechain": False,
         "timestamp": "2026-08-26T00:00:07Z",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "answer"}]}},
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        for row in rows:
            fh.write(_json.dumps(row) + "\n")
        tmp = Path(fh.name)
    cleared = lt.parse_session(tmp, "pax")
    tmp.unlink(missing_ok=True)
    check(
        "leading assistant turn dropped after /clear",
        [t.speaker for t in cleared] == ["user", "pax"],
        str([t.speaker for t in cleared]),
    )
    check("file ends with a single newline", rendered.endswith("\n") and not rendered.endswith("\n\n"))

    # Filenames must also sync to Android filesystems.
    check(
        "android-illegal characters stripped from titles",
        lt.sanitize_title('a?b:c*d"e<f>g|h') == "abcdefgh",
        lt.sanitize_title('a?b:c*d"e<f>g|h'),
    )
    check("path separators neutralised", "/" not in lt.sanitize_title("a/b"))

    # Tools can split one reply across several top-level text entries.
    split_reply = [
        {"type": "user", "isSidechain": False, "promptSource": "user",
         "timestamp": "2026-09-17T00:00:00Z",
         "message": {"role": "user", "content": "why the tortoise?"}},
        {"type": "assistant", "isSidechain": False,
         "timestamp": "2026-09-17T00:00:01Z",
         "message": {"role": "assistant",
                     "content": [{"type": "thinking", "thinking": "private"}]}},
        {"type": "assistant", "isSidechain": False,
         "timestamp": "2026-09-17T00:00:02Z",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "Let me look first."}]}},
        {"type": "assistant", "isSidechain": False,
         "timestamp": "2026-09-17T00:00:03Z",
         "message": {"role": "assistant",
                     "content": [{"type": "tool_use", "name": "Read", "input": {}}]}},
        {"type": "user", "isSidechain": False,
         "timestamp": "2026-09-17T00:00:04Z",
         "message": {"role": "user",
                     "content": [{"type": "tool_result", "content": "a file"}]}},
        {"type": "assistant", "isSidechain": True,
         "timestamp": "2026-09-17T00:00:05Z",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "subagent chatter"}]}},
        {"type": "assistant", "isSidechain": False,
         "parent_tool_use_id": "toolu_01",
         "timestamp": "2026-09-17T00:00:06Z",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "stream-shaped subagent"}]}},
        {"type": "assistant", "isSidechain": False,
         "timestamp": "2026-09-17T00:00:07Z",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "The answer, at last."}]}},
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        for row in split_reply:
            fh.write(_json.dumps(row) + "\n")
        tmp = Path(fh.name)
    reply = lt.parse_session(tmp, "iris")
    tmp.unlink(missing_ok=True)

    check("a reply split by tool calls is one turn", len(reply) == 2, str(len(reply)))
    check(
        "the sentence before the tools is kept",
        "Let me look first." in reply[1].content,
    )
    check(
        "the answer after the tools is kept",
        "The answer, at last." in reply[1].content,
        reply[1].content,
    )
    check("sidechain text stays out", "subagent chatter" not in reply[1].content)
    check(
        "parent_tool_use_id text stays out",
        "stream-shaped subagent" not in reply[1].content,
    )
    check("thinking stays out", "private" not in reply[1].content)

    # Stop can arrive before the final text is flushed.
    filed = lt.render_file("T", reply, "iris", session_id="s-1")
    unchanged, why = lt.plan_update(filed, reply, "iris")
    check("a file that matches its session is not rewritten",
          unchanged is None and why == "unchanged", why)

    truncated_turn = lt.Turn("iris", "Let me look first.", reply[1].timestamp)
    truncated = lt.render_file(
        "T", [reply[0], truncated_turn], "iris", session_id="s-1"
    )
    repaired, why = lt.plan_update(truncated, reply, "iris")
    check("a turn that grew after it was written is repaired",
          repaired is not None and "The answer, at last." in repaired, why)
    check("repairing does not duplicate the turn",
          repaired is not None and repaired.count("<!-- turn speaker=iris") == 1,
          why)
    check("repairing keeps the frontmatter and the title",
          repaired is not None and repaired.startswith('---\ntitle: "T"'))
    check("repairing bumps last_activity",
          repaired is not None
          and f"last_activity: {reply[-1].timestamp}" in repaired)

    later = reply + [
        lt.Turn("user", "and the shell?", "2026-09-17T00:01:00Z"),
        lt.Turn("iris", "Stoic skin.", "2026-09-17T00:01:02Z"),
    ]
    grown, why = lt.plan_update(filed, later, "iris")
    check("new turns are appended",
          grown is not None and grown.count("<!-- turn speaker=") == 4, why)
    check("appending leaves the earlier turns byte-identical",
          grown is not None
          and grown.startswith(
              lt.bump_last_activity(filed.rstrip("\n"), later[-1].timestamp)))

    # Keep filed content if the session no longer contains it.
    hand_edited = filed.replace("The answer, at last.", "something else entirely")
    left, why = lt.plan_update(hand_edited, reply, "iris")
    check("a diverged turn is never overwritten",
          left is None and "diverged" in why, why)
    diverged_then_grown, why = lt.plan_update(hand_edited, later, "iris")
    check("but later turns still land on a diverged file",
          diverged_then_grown is not None
          and "something else entirely" in diverged_then_grown
          and "Stoic skin." in diverged_then_grown, why)

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
