"""A thread's full record (2026-11-10): every step of every reply, kept.

The readable record in ``15_CONVERSATIONS/<title>.md`` keeps a reply's text
and its ``did`` labels; it is the human record. This is the machine one: one
append-only JSONL file per thread, ``state/threads/steps/<thread id>.jsonl``,
one line per message a member's session produced::

    {"message": "m4", "thread": "t-6daf5a6f", "author": "ada",
     "engine": "claude-code", "model": null, "at": "2026-11-10T17:47:55Z",
     "session_id": "ede89dc4-…", "seq": [12, 4310],
     "steps": [{"type": "text", "text": "…", "parent": null}, …]}

**Why not beside the record in the vault.** Tool outputs go in untrimmed --
a ``cat`` of a config, an ``env`` listing, a whole file -- and the vault is
pushed to GitHub and served at the vault's own host; its history already weighs
4.6 GB. So this lives with the runtime's thread state, which is gitignored
and never served. Unlike the rest of ``state/threads/`` it is **not a
cache**: nothing else in the thread holds tool inputs and outputs. The
broker's session event log (``state/broker/sessions/<session id>.jsonl``)
holds the same events and more, and ``seq`` points into it.

``steps`` is every event the harness emitted for the reply, in order, each
in one of these shapes:

- ``{"type": "text", "text", "parent"}`` -- what was said; ``parent`` is the
  ``Agent`` tool call a subagent said it under, null at the top.
- ``{"type": "thinking", "thinking", "parent"}``
- ``{"type": "tool", "id", "name", "input", "parent", "output", "is_error",
  "result"}`` -- a call and, once it came back, its output as the harness
  returned it (a string or a list of content blocks), untrimmed. ``result``
  is the rest of the tool result event (``tool_use_result`` and the like).
- ``{"type": "tool", "id", "name": "Agent", "input": {"description"},
  "parent": null, "carried": true, "output", "is_error"}`` -- the stub of an
  ``Agent`` call made in an earlier reply, ahead of that subagent's steps
  in this one (``nest_carried``, 2026-11-12).
- ``{"type": "permission", "event"}`` / ``{"type": "permission_resolved",
  "event"}`` -- a question or permission card and its answer, the broker's
  own ``broker.permission_request`` / ``…_resolved`` events whole.
- ``{"type": "end", "event"}`` -- a ``result``: the end of a harness turn,
  with its usage and cost.
- ``{"type": "event", "event"}`` -- anything else, whole (``system`` lines,
  task notifications, rate limits, ``broker.*``).

The one thing left out is ``stream_event``: the token deltas that the
``assistant`` events repeat whole. They stay in the session log. No size cap.

Stdlib only, like ``loam_threads``.
"""

from __future__ import annotations

import fcntl
import json
import sys
import time
from pathlib import Path

import loam_threads as threads

SKIPPED = ("stream_event",)

# The writer's own counts, served on GET /v1/threads/writer: a reply that
# was dropped or merged is loud now, never silent (2026-11-10).
# `late_prompt` (2026-11-10): replies posted by the watch kept on a session
# after its turn's `result`, the moment they finished; `fanout`: turns made
# by an `@all` beyond the ones its message's own mentions made.
# `delivered_while_waiting` (2026-11-10): messages handed to a member whose
# turn had said its words and was only waiting on background work, at once,
# rather than queued behind that wait.
# `rotated` (2026-11-11): turns that started a fresh session because the
# member's session for the thread had passed the context threshold.
# `steered` (2026-11-14): messages sent into a member's turn in flight, at
# its next safe boundary; `steer_refused`: a steer the engine declined or
# the harness refused, whose messages kept their place in the queue;
# `already_heard`: queued turns not run because the message that made them
# had already been shown to a turn that went on to answer.
COUNTS: dict = {"since": None, "replies": 0, "late_recovered": 0,
                "steered": 0, "steer_refused": 0, "already_heard": 0,
                "late_prompt": 0, "fanout": 0, "parallel": 0,
                "delivered_while_waiting": 0, "reply_to": 0,
                "carried": 0, "rotated": 0, "steps_written": 0, "steps_failed": 0,
                "last_failure": None}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


COUNTS["since"] = _now()


def log(line: str) -> None:
    print(f"threads: {line}", file=sys.stderr, flush=True)


def steps_dir() -> Path:
    return threads.STATE / "steps"


def steps_path(thread_id: str) -> Path:
    return steps_dir() / f"{thread_id}.jsonl"


def marker_path(session_id: str) -> Path:
    """Says a session's thread turns have this writer behind them; the
    transcript hook reads it (see ``hooks/transcript_capture.py``)."""
    return steps_dir() / "sessions" / session_id


def mark_session(session_id: str, thread_id: str) -> None:
    try:
        path = marker_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(thread_id + "\n", encoding="utf-8")
    except OSError as exc:
        log(f"session marker for {session_id[:8]} not written ({exc})")


def keep(event: dict) -> bool:
    return isinstance(event, dict) and event.get("type") not in SKIPPED


def build(events: list[dict]) -> list[dict]:
    """The ordered steps of one reply, from its events."""
    steps: list[dict] = []
    calls: dict[str, dict] = {}
    for event in events:
        if not keep(event):
            continue
        kind = event.get("type")
        parent = event.get("parent_tool_use_id")
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content")
        if kind == "assistant" and isinstance(content, list):
            meta = {k: v for k, v in message.items() if k != "content"}
            first = True
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    step = {"type": "text", "text": block.get("text") or "",
                            "parent": parent}
                elif btype == "thinking":
                    step = {"type": "thinking", "thinking": block.get("thinking") or "",
                            "parent": parent}
                elif btype == "tool_use":
                    step = {"type": "tool", "id": block.get("id"),
                            "name": block.get("name"), "input": block.get("input"),
                            "parent": parent, "output": None, "is_error": None}
                    if block.get("id"):
                        calls[block["id"]] = step
                else:
                    step = {"type": "block", "block": block, "parent": parent}
                if first and meta:
                    step["message"] = meta
                first = False
                steps.append(step)
            continue
        if kind == "user" and isinstance(content, list):
            rest = []
            for block in content:
                if (isinstance(block, dict) and block.get("type") == "tool_result"
                        and block.get("tool_use_id") in calls):
                    call = calls[block["tool_use_id"]]
                    call["output"] = block.get("content")
                    call["is_error"] = bool(block.get("is_error"))
                    extra = {k: v for k, v in event.items() if k != "message"}
                    if extra:
                        call["result"] = extra
                else:
                    rest.append(block)
            if rest:
                steps.append({"type": "event", "event": {
                    **event, "message": {**message, "content": rest}}})
            continue
        if kind == "broker.permission_request":
            steps.append({"type": "permission", "event": event})
        elif kind == "broker.permission_resolved":
            steps.append({"type": "permission_resolved", "event": event})
        elif kind == "result":
            steps.append({"type": "end", "event": event})
        else:
            steps.append({"type": "event", "event": event})
    return nest_carried(steps)


def nest_carried(steps: list[dict]) -> list[dict]:
    """A subagent's steps under a call this reply did not make get that call
    back, as a ``carried`` stub ahead of the first of them (2026-11-12).

    A background subagent launched in one reply often finishes in the next:
    its last words, its ``task_notification`` and the persona's follow-up
    turn all land in the reply after the one holding its ``Agent`` call. Its
    steps keep their ``parent``, but nothing in this record has that id, and
    a reader that nests by parent (the page's transcript) falls back to the
    top level -- where the subagent's last sentence reads as the persona's
    own words. Seen in thread t-6daf5a6f on 11-12: "The Show more fix is
    ready for Ada to check." drawn as Ada's, and before it two raw
    engineer receipts. The stub names the call from the task's own events
    (``description``) and, once the task has reported, carries its
    ``summary`` as the output, so the subagent's work is drawn folded under
    its call as a step and never as speech. The message's text was never
    affected: only top-level text is what a persona said.

    Idempotent, so a record written before this is mended as it is read."""
    known = {s.get("id") for s in steps
             if isinstance(s, dict) and s.get("type") == "tool" and s.get("id")}
    about: dict[str, dict] = {}
    for s in steps:
        event = s.get("event") if isinstance(s, dict) else None
        if not (isinstance(event, dict) and event.get("type") == "system"
                and event.get("tool_use_id")):
            continue
        info = about.setdefault(event["tool_use_id"], {})
        if event.get("subtype") in ("task_started", "task_progress") \
                and event.get("description") and "description" not in info:
            info["description"] = event["description"]
        if event.get("subtype") == "task_notification":
            info["summary"] = event.get("summary")
            info["status"] = event.get("status")
    out: list[dict] = []
    for s in steps:
        parent = s.get("parent") if isinstance(s, dict) else None
        if isinstance(parent, str) and parent and parent not in known:
            known.add(parent)
            info = about.get(parent, {})
            out.append({"type": "tool", "id": parent, "name": "Agent",
                        "input": {"description": info.get("description")
                                  or "a background task"},
                        "parent": None, "carried": True,
                        "output": info.get("summary"),
                        "is_error": (info.get("status") not in (None, "completed"))
                        if "status" in info else None})
        out.append(s)
    return out


def entry(msg: dict, thread_id: str, session_id: str | None,
          events: list[dict], **extra) -> dict:
    seqs = [e.get("seq") for e in events if isinstance(e, dict)
            and isinstance(e.get("seq"), int)]
    return {"message": msg.get("id"), "thread": thread_id,
            "author": msg.get("author"), "engine": msg.get("engine"),
            "model": msg.get("model"), "at": msg.get("at"),
            "session_id": session_id,
            "seq": [min(seqs), max(seqs)] if seqs else None,
            "steps": build(events),
            **({"reply_to": msg["reply_to"]} if msg.get("reply_to") else {}),
            **extra}


def write(thread_id: str, record: dict) -> bool:
    """Append one entry. A failure is logged and counted, never raised: the
    readable record is already written by then."""
    path = steps_path(thread_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        with path.open("ab") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                handle.write(data)
                handle.flush()
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    except (OSError, TypeError, ValueError) as exc:
        COUNTS["steps_failed"] += 1
        COUNTS["last_failure"] = {"at": _now(), "thread": thread_id,
                                  "message": record.get("message"), "error": str(exc)}
        log(f"{thread_id} {record.get('message')}: full record NOT written ({exc})")
        return False
    COUNTS["steps_written"] += 1
    return True


def read(thread_id: str, message: str | None = None) -> dict[str, dict]:
    """Message id -> its entry; the last line for an id wins (a backfill
    never overwrites, but a reader should not care).

    An entry with no message (``"unspoken": true``, 2026-11-10: a harness
    turn that worked and said nothing) is in the whole thread's read only,
    keyed ``unspoken:<first seq>``; no message carries it.

    A reply's ``↩ m12`` opening is read off its first words when the entry's
    ``reply_to`` is m12 (2026-11-17, ``threads.unmark_steps``): the page
    builds a reply's words from this record, and the id is not words."""
    out: dict[str, dict] = {}
    path = steps_path(thread_id)
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            mid = rec.get("message")
            if isinstance(rec.get("steps"), list):
                rec["steps"] = threads.unmark_steps(nest_carried(rec["steps"]),
                                                    rec.get("reply_to"))
            if mid and (message is None or mid == message):
                out[mid] = rec
            elif not mid and rec.get("unspoken") and message is None:
                out[f"unspoken:{(rec.get('seq') or [len(out)])[0]}"] = rec
    return out


def split_replies(events: list[dict]) -> list[list[dict]]:
    """Events cut into replies at each top-level ``result``: one harness turn,
    one reply. A trailing run with no result is the last element."""
    out: list[list[dict]] = [[]]
    for event in events:
        if not keep(event):
            continue
        out[-1].append(event)
        if event.get("type") == "result" and event.get("parent_tool_use_id") is None:
            out.append([])
    if not out[-1]:
        out.pop()
    return out


def said(events: list[dict]) -> str:
    """What a reply said after its last top-level tool call: the message's
    text, by the same rule the live writer uses."""
    segments: list[str] = []
    for event in events:
        if event.get("type") != "assistant" or event.get("parent_tool_use_id") is not None:
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                segments.append(block.get("text") or "")
            elif block.get("type") == "tool_use":
                segments = []
    return "\n\n".join(s.strip() for s in segments if s.strip()).strip()
