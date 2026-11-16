"""Classic chats become archived threads (2026-11-11).

Classic chats -- the session-based view, one broker session per chat -- are
retired; threads are the one way to talk to a persona. Nothing a classic
chat said is dropped on the way: each becomes an archived thread with the
same title, its persona as the only member and the default, its messages in
order with their authors and the times they were said, and, where the
broker's session log has them, every step of every reply in the thread's
full record (``state/threads/steps/``). The original transcript in
``15_CONVERSATIONS/`` and the session's own files stay where they are; the
thread's frontmatter names both (``imported_from``, ``source``).

**What a classic chat is.** A broker session record
(``state/broker/sessions/<id>.json``) that is not a thread's engine: no
``thread_id`` and a title not starting ``Thread ·``, which is how a thread
turn marks the session it runs on (``ThreadEngine._session``).

**Where its messages come from**, in this order:

1. The session's own event stream, as the classic view replayed it
   (``Session.replay``): the Claude Code app's file for an adopted chat, cut
   at ``replayed``, then the broker's log. A turn starts at each
   ``broker.user_message``; a persona's replies are cut at each top-level
   ``result``, one message per harness turn, by the same ``Reply`` the live
   thread writer uses, so an imported reply reads as a thread reply does.
   The broker's log does not stamp a person's message, so its time is the
   transcript's for the same words, else the first event after it.
2. The transcript, when the stream is not whole: an adopted chat whose app
   file is gone (Claude Code prunes them) holds its history nowhere else.
   Its replies get full-record entries where the broker's log has a reply
   with the same words -- proved by the text, never by position.

**Idempotent.** A session already imported (its id is some record's
``imported_from``) is not imported again, so a second run imports nothing
twice. What a chat said after its import is appended to its thread on the
next run, but only while the thread holds exactly what the import wrote
(``catch_up``): once the owner writes in it, it is a thread like any other.

Stdlib only, like ``loam_threads``; ``bin/import-classic-chats`` runs it.
"""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path

import loam_adopt as adoption
import loam_broker as cfg
import loam_clock
import loam_thread_engine as engine
import loam_thread_steps as steps
import loam_threads as threads
import loam_transcript as lt

THREAD_TITLE_PREFIX = "Thread ·"


def records(session_dir: Path | None = None) -> list[dict]:
    """Every classic chat's session record, oldest first."""
    out = []
    for path in sorted((session_dir or cfg.SESSION_DIR).glob("*.json")):
        try:
            meta = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict) or not meta.get("session_id"):
            continue
        if meta.get("thread_id") or (meta.get("title") or "").startswith(THREAD_TITLE_PREFIX):
            continue
        out.append(meta)
    out.sort(key=lambda m: m.get("created") or "")
    return out


# -- the transcript ------------------------------------------------------------

def transcript_path(record: dict) -> Path | None:
    """The transcript the writer filed this chat under: its state file says,
    by the session's id and then by the CLI's own id; else the vault file
    whose frontmatter names the session."""
    for sid in (record.get("session_id"), record.get("cli_session_id")):
        if not sid:
            continue
        try:
            state = json.loads((adoption.TRANSCRIPT_STATE / f"{sid}.json").read_text("utf-8"))
            path = Path(state["path"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            continue
        if path.is_file():
            return path
    return _by_session().get(record.get("session_id"))


_SESSION_FILES: dict | None = None


def _by_session() -> dict:
    global _SESSION_FILES
    if _SESSION_FILES is None:
        _SESSION_FILES = {}
        for path in (threads.VAULT / threads.CONVERSATIONS).glob("*.md"):
            try:
                with open(path, encoding="utf-8") as fh:
                    head = fh.read(1500)
            except OSError:
                continue
            if "kind: thread" in head:
                continue
            match = re.search(r"^session_id: (\S+)$", head, re.M)
            if match:
                _SESSION_FILES.setdefault(match.group(1), path)
    return _SESSION_FILES


TURN_RE = re.compile(r"^<!-- turn speaker=(\S+) timestamp=(\S+) -->$")


def transcript_turns(path: Path) -> list[dict]:
    """``{speaker, at, text}`` per turn the transcript holds, in order."""
    try:
        text = path.read_text("utf-8")
    except OSError:
        return []
    _, blocks = lt.split_transcript(text)
    out = []
    for block in blocks:
        match = TURN_RE.match(block.split("\n", 1)[0])
        if not match:
            continue
        body = lt.turn_body(block)
        out.append({"speaker": match.group(1), "at": lt.format_timestamp(match.group(2)),
                    "text": body})
    return out


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def vault_rel(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(threads.VAULT.resolve()))
    except ValueError:
        return str(path)


# -- the stream ----------------------------------------------------------------

def stream(record: dict) -> tuple[list[dict], bool]:
    """The chat's events as the classic view replayed them, and whether that
    stream is whole: False for an adopted chat whose app file is gone."""
    sid = record["session_id"]
    out: list[dict] = []
    whole = True
    if record.get("origin") == adoption.ORIGIN_APP and record.get("source"):
        source = Path(record["source"])
        if source.is_file():
            limit = record.get("replayed")
            for n, event in enumerate(adoption.iter_events(source, sid), 1):
                if limit is not None and n > limit:
                    break
                out.append(event)
        else:
            whole = False
    log = cfg.SESSION_DIR / f"{sid}.jsonl"
    if log.exists():
        with log.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and steps.keep(event):
                    out.append(event)
    return out, whole


def _stamp(event: dict) -> str | None:
    raw = event.get("timestamp") or event.get("ts")
    if isinstance(raw, str) and len(raw) >= 19:
        return lt.format_timestamp(raw)
    return None


def _last_stamp(events: list[dict]) -> str | None:
    for event in reversed(events):
        if _stamp(event):
            return _stamp(event)
    return None


def _first_stamp(events: list[dict]) -> str | None:
    for event in events:
        if _stamp(event):
            return _stamp(event)
    return None


def replies_of(events: list[dict]) -> list[tuple["engine.Reply", list[dict]]]:
    """A turn's replies as the live writer cuts them: one per harness turn
    that said something; a silent one's events carry on to the next."""
    out, carry = [], []
    for chunk in steps.split_replies(events):
        reply = engine.Reply.of(carry + chunk)
        if reply.spoken():
            out.append((reply, carry + chunk))
            carry = []
        else:
            carry = carry + chunk
    if carry and out:
        reply, evs = out[-1]
        out[-1] = (reply, evs + carry)
    return out


def _person_times(turns: list[dict]) -> list[tuple[str, str]]:
    return [(t["at"], _norm(t["text"])) for t in turns if t["speaker"] == "user"]


def _match_time(said: str, people: list[tuple[str, str]], start: int) -> tuple[str | None, int]:
    """The transcript's time for what the owner said, searched forward from
    ``start`` so two identical messages take two times."""
    key = _norm(said)[:80]
    if not key:
        return None, start
    for i in range(start, len(people)):
        at, text = people[i]
        if text.startswith(key) or key.startswith(text[:80] or "\0"):
            return at, i + 1
    return None, start


def from_stream(record: dict, events: list[dict], turns: list[dict]) -> list[dict]:
    """Messages, each with its full-record events under ``_events``."""
    persona = record["agent"]
    eng = record.get("provider") or "claude-code"
    people = _person_times(turns)
    cursor = 0
    blocks: list[tuple[dict | None, list[dict]]] = [(None, [])]
    for event in events:
        if event.get("type") == "broker.user_message":
            blocks.append((event, []))
        else:
            blocks[-1][1].append(event)
    msgs: list[dict] = []
    last_at = record.get("created")
    for prompt, evs in blocks:
        if prompt is not None:
            text = lt.person_text(loam_clock.strip(prompt.get("text") or ""))
            attachments = prompt.get("attachments") or []
            if text or attachments:
                at = prompt.get("ts") if isinstance(prompt.get("ts"), str) else None
                if not at:
                    at, cursor = _match_time(text, people, cursor)
                at = at or _first_stamp(evs) or last_at
                # Never before what came above it: a short message can match
                # a later turn of the same words in the transcript.
                at = max(at, last_at) if at and last_at else at
                msg = {"author": threads.OWNER, "at": at, "kind": "message",
                       "text": text, "did": []}
                if attachments:
                    msg["attachments"] = attachments
                msgs.append(msg)
                last_at = at
        for reply, chunk in replies_of(evs):
            at = _last_stamp(chunk) or last_at
            at = max(at, last_at) if at and last_at else at
            msg = {"author": persona, "at": at, "kind": "message",
                   "text": reply.spoken(), "did": reply.did, "engine": eng,
                   "_events": chunk}
            before = reply.before()
            if before and any("text" in seg for seg in before):
                msg["segments"] = before
            if reply.model:
                msg["model"] = reply.model
            msgs.append(msg)
            last_at = at
    return msgs


def from_transcript(record: dict, turns: list[dict], events: list[dict]) -> list[dict]:
    """Messages from the transcript. A transcript turn holds everything the
    persona said in it, before its tools as well as after, so a reply from
    the broker's log belongs to the turn whose words contain the reply's
    own; each logged reply is given to one turn at most, in order."""
    persona = record["agent"]
    eng = record.get("provider") or "claude-code"
    blocks: list[list[dict]] = [[]]
    for event in events:
        if event.get("type") == "broker.user_message":
            blocks.append([])
        else:
            blocks[-1].append(event)
    logged = [(_norm(reply.spoken()), chunk) for evs in blocks
              for reply, chunk in replies_of(evs)]
    cursor = 0
    msgs = []
    for turn in turns:
        if not turn["text"]:
            continue
        if turn["speaker"] == "user":
            msgs.append({"author": threads.OWNER, "at": turn["at"],
                         "kind": "message", "text": turn["text"], "did": []})
            continue
        if turn["speaker"] == "system":
            msgs.append({"author": "system", "at": turn["at"], "kind": "system",
                         "text": turn["text"], "did": []})
            continue
        msg = {"author": persona, "at": turn["at"], "kind": "message",
               "text": turn["text"], "did": [], "engine": eng}
        said = _norm(turn["text"])
        mine: list[dict] = []
        i = cursor
        while i < len(logged):
            words, chunk = logged[i]
            if words and words in said:
                mine.extend(chunk)
                cursor = i + 1
            elif mine:
                break
            i += 1
        if mine:
            reply = engine.Reply.of(mine)
            msg["did"] = reply.did
            msg["_events"] = mine
            if reply.model:
                msg["model"] = reply.model
        msgs.append(msg)
    return msgs


# -- one chat --------------------------------------------------------------------

def plan(record: dict) -> dict:
    """What importing one chat would write: ``{record, source, how,
    messages}``, or ``{record, skipped: why}``."""
    persona = record.get("agent")
    if persona not in threads.roster():
        return {"record": record, "skipped": f"no persona named {persona!r}"}
    tpath = transcript_path(record)
    turns = transcript_turns(tpath) if tpath else []
    events, whole = stream(record)
    if whole:
        msgs = from_stream(record, events, turns)
        how = "session"
        heard = sum(1 for m in msgs if threads.is_owner(m["author"]))
        typed = sum(1 for t in turns if t["speaker"] == "user")
        if turns and (typed > heard or not any(m["author"] == persona for m in msgs)):
            # The transcript holds more of what the owner said than the stream
            # does: a broker log from before `broker.user_message` existed
            # (September) has the replies and none of the questions. The
            # transcript is then the better record, with the log's steps
            # given to the replies whose words they are.
            msgs, how = from_transcript(record, turns, events), "transcript"
    elif turns:
        msgs, how = from_transcript(record, turns, events), "transcript"
    else:
        return {"record": record,
                "skipped": "its app file is gone and no transcript names it"}
    if not msgs:
        return {"record": record, "skipped": "nothing was said in it"}
    for i, msg in enumerate(msgs, 1):
        msg["id"] = f"m{i}"
    return {"record": record, "source": vault_rel(tpath), "how": how, "messages": msgs}


def write(planned: dict) -> dict:
    """Write one planned import: the record, then its full-record entries."""
    record = planned["record"]
    persona = record["agent"]
    eng = record.get("provider") or "claude-code"
    thread_id = "t-" + secrets.token_hex(4)
    title = threads.clean_title(record.get("title") or f"Chat with {threads.display(persona)}")
    meta = {"id": thread_id, "title": title, "default": persona, "icon": None,
            "archived": True, "models": {}, "members": [persona],
            "engines": {persona: eng}, "pinned": False,
            "created": record.get("created") or threads.now_iso(),
            "imported_from": record["session_id"], "source": planned.get("source")}
    msgs = [{k: v for k, v in m.items() if k != "_events"} for m in planned["messages"]]
    path = threads.write_imported(meta, msgs)
    entries = 0
    for full, msg in zip(planned["messages"], msgs):
        if not full.get("_events"):
            continue
        if steps.write(thread_id, steps.entry(msg, thread_id, record["session_id"],
                                              full["_events"], backfilled=True,
                                              imported=True, ended="imported")):
            entries += 1
    return {"thread": thread_id, "path": path, "entries": entries}


def catch_up(record: dict, thread_id: str) -> int:
    """Messages the chat gained after its import, appended to its thread
    while nobody has written in the thread (``threads.append_imported``).
    The thread's messages must be the start of what the chat says now --
    the same authors and words -- or nothing is added."""
    try:
        _, have, _ = threads.read(thread_id)
    except threads.ThreadError:
        return 0
    row = threads.load_state("threads.json").get(thread_id, {})
    if row.get("imported") != len(have):
        return 0
    planned = plan(record)
    if "skipped" in planned or len(planned["messages"]) <= len(have):
        return 0
    head = planned["messages"][:len(have)]
    if any(a["author"] != b["author"] or _norm(a["text"]) != _norm(b.get("text"))
           for a, b in zip(head, have)):
        return 0
    later = planned["messages"][len(have):]
    out = threads.append_imported(
        thread_id, [{k: v for k, v in m.items() if k not in ("_events", "id")}
                    for m in later])
    if not out:
        return 0
    for full, msg in zip(later, out):
        if full.get("_events"):
            steps.write(thread_id, steps.entry(msg, thread_id, record["session_id"],
                                               full["_events"], backfilled=True,
                                               imported=True, ended="imported"))
    return len(out)


def run(dry: bool = False, only: list[str] | None = None, say=print) -> dict:
    """Import every classic chat not yet imported. Returns the counts."""
    done = threads.imported()
    counts = {"chats": 0, "already": 0, "imported": 0, "skipped": [], "entries": 0,
              "messages": 0, "live": [], "caught_up": []}
    for record in records():
        sid = record["session_id"]
        if only and not any(sid.startswith(o) for o in only):
            continue
        counts["chats"] += 1
        if sid in done:
            counts["already"] += 1
            if not dry:
                added = catch_up(record, done[sid])
                if added:
                    counts["caught_up"].append((sid, done[sid], added))
                    say(f"{sid[:8]} {record.get('agent')} {record.get('title')!r}: "
                        f"{added} later message(s) added to {done[sid]}")
            continue
        planned = plan(record)
        label = f"{sid[:8]} {record.get('agent')} {record.get('title')!r}"
        if "skipped" in planned:
            counts["skipped"].append((sid, record.get("title"), planned["skipped"]))
            say(f"{label}: skipped, {planned['skipped']}")
            continue
        if record.get("state") == "running" or record.get("live"):
            counts["live"].append(sid)
        n = len(planned["messages"])
        steps_n = sum(1 for m in planned["messages"] if m.get("_events"))
        if dry:
            say(f"{label}: {n} messages from its {planned['how']}, "
                f"{steps_n} with full steps (dry run)")
            counts["imported"] += 1
            counts["messages"] += n
            counts["entries"] += steps_n
            continue
        out = write(planned)
        counts["imported"] += 1
        counts["messages"] += n
        counts["entries"] += out["entries"]
        say(f"{label}: {out['thread']} ({out['path'].name}), {n} messages from its "
            f"{planned['how']}, {out['entries']} with full steps")
    return counts
