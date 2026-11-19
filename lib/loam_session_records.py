"""Read, repair and migrate the session host's on-disk records."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import loam_adopt as adoption
import loam_broker as cfg
import loam_transcript
from loam_provider_session import first_words, history_bytes, recovered_status, with_context


def normalise(meta: dict) -> dict:
    """A record read off disk, answered with every field the interface has.

    A record is only rewritten when its session is touched, so the box is
    full of files written before these fields existed. A client must not have
    to ask whether a key is missing because the session has no title or
    because the session predates titles: every record the interface answers
    carries every key, and the value says the rest.
    """
    meta["status"] = recovered_status(meta.get("status"), meta.get("turns") or [])
    meta.setdefault("provider", cfg.DEFAULT_PROVIDER)
    meta.setdefault("title", None)
    # What named it, added 2026-09-22 when the broker stopped titling and
    # started reading the vault's title instead. Null on a record written
    # before it, which says only that nothing recorded where its name came
    # from -- see `sync_titles`, which is what such a record gets once.
    meta.setdefault("title_source", None)
    meta.setdefault("last_message", None)
    meta.setdefault("last_event_at", None)
    # Where the session came from, added 2026-09-20 with adoption. A record
    # written before it is one the broker started itself, which is the only
    # kind there was.
    meta.setdefault("origin", adoption.ORIGIN_UI)
    meta.setdefault("source", None)
    # How many of the session's events are translated from that file rather
    # than logged here. Null for everything the broker started itself.
    meta.setdefault("replayed", None)
    # The reading of that file the record was made from, added 2026-09-21 so
    # that a later read can tell whether the conversation has moved on since.
    # Null on a record written before it, which reads as "never measured".
    meta.setdefault("source_size", None)
    meta.setdefault("source_mtime", None)
    # The fork, added 2026-09-20 with the first message into an adopted
    # session. Null on every other record: a session the broker started runs
    # under its own id, and the two fields exist to say when it does not.
    meta.setdefault("cli_session_id", None)
    meta.setdefault("forked_from", None)
    # The provider's own thread, added 2026-10-01 with Codex: the id
    # `thread/resume` takes. Null on every Claude Code record, whose
    # conversation is resumed by the record's own id.
    meta.setdefault("thread_id", None)
    # This chat's model and effort, added 2026-09-21 with the + menu. Null on
    # a record written before it, which is what every session was: the
    # persona's own setting, nothing overridden.
    meta.setdefault("model", None)
    meta.setdefault("effort", None)
    meta["pinned"] = bool(meta.get("pinned"))
    meta["archived"] = bool(meta.get("archived"))
    # How far this session has been read, added 2026-09-22. A record written
    # before it reads as 0 -- unread to the end -- which is what a chat
    # nobody has opened on this interface actually is.
    meta["seen_seq"] = int(meta.get("seen_seq") or 0)
    # How many background tasks are running behind this session's turn, added
    # 2026-09-22. Zero here without looking at what the file says, and that is
    # not a default but a fact: a record read off disk is a record no process
    # of this broker's is behind, and a background task dies with the process
    # that launched it. A record left saying 2 by a broker that was killed
    # would otherwise draw a badge for two subagents that stopped days ago.
    meta["background_tasks"] = 0
    # Whether a process is behind this session, added 2026-09-25. False here
    # for the same reason the count above is zero, and it is a fact and not a
    # default: a record read off disk is a record this broker has no process
    # for, so nothing in the file can make it live. A record left saying
    # `true` by a broker that was killed would otherwise put a row in the "in
    # RAM" filter for a process that died days ago.
    meta["live"] = False
    # What a first message into this record would re-send, in bytes. Derived
    # rather than stored, so it cannot disagree with the reading it comes from.
    meta["history_bytes"] = history_bytes(meta.get("origin"),
                                          meta.get("cli_session_id"),
                                          meta.get("source_size"))
    meta["turns"] = [with_context(turn) for turn in (meta.get("turns") or [])
                     if isinstance(turn, dict)]
    return meta


def from_log(path: Path) -> dict:
    """What a record written before these fields existed can still be told by
    its own event log.

    Added 2026-09-18. ``normalise`` fills the keys and cannot fill the
    content, so every session on the box from before this morning drew as
    "New chat" with no status box -- caused in a real browser. The log is
    the durable copy of the conversation and it holds all three answers, so
    they are read from it rather than left null forever.

    The rules, and each is the same rule the live path already uses:

    - ``title`` is the opening words of the first thing a PERSON said. That
      is ``broker.user_message``, the broker's own event, which is why it
      exists; a ``user`` event is a tool result unless its content is prose,
      and a session logged before ``broker.user_message`` (2026-09-16) has
      only tool results, so its title stays null rather than becoming the
      first page of a file the agent happened to read.
    - ``last_message`` is the last TOP-LEVEL assistant text, cut to
      ``LAST_MESSAGE_CHARS``. A subagent's sentence is machinery.
    - ``result_ok`` is the last ``result`` envelope's verdict, judged the
      way ``_handle`` judges a live one, and ``None`` when the log holds no
      result at all.
    - ``last_seq`` is the highest number in the log, which is what the
      record's position field means.
    """
    found = {"title": None, "last_message": None, "result_ok": None,
             "last_seq": 0}
    if not path.exists():
        return found
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            seq = event.get("seq")
            if isinstance(seq, int) and seq > found["last_seq"]:
                found["last_seq"] = seq
            if found["title"] is None and kind == "broker.user_message":
                found["title"] = first_words(event.get("text") or "") or None
            elif found["title"] is None and kind == "user":
                content = (event.get("message") or {}).get("content")
                # A list holding a tool_result is the CLI feeding itself, not
                # a person speaking -- three real sessions were read end to
                # end on 2026-09-16 and every `user` event in all three was
                # one of those. Prose here is a person only if nothing in the
                # message is machinery.
                if not (isinstance(content, list) and any(
                        isinstance(block, dict)
                        and block.get("type") == "tool_result"
                        for block in content)):
                    found["title"] = first_words(
                        loam_transcript._text_of(content)) or None
            elif kind == "assistant" and event.get("parent_tool_use_id") is None:
                said = loam_transcript._text_of(
                    (event.get("message") or {}).get("content"))
                if said:
                    found["last_message"] = said[:cfg.LAST_MESSAGE_CHARS]
            elif kind == "result":
                found["result_ok"] = not (
                    bool(event.get("is_error"))
                    or (event.get("subtype") or "success") != "success")
    return found


def backfill(meta: dict, log_path: Path) -> bool:
    """Fill a record from its log, in place. True if anything changed.

    Only what the record does not already say: a title a person wrote, a
    last message this broker recorded and a status it stored are all facts
    about the session and outrank anything re-derived from the log.

    The position field is the exception and is corrected upward, never down.
    ``record_seq`` is defined as the number of the last event in the log, and
    a record that says less than its own log is a record that will renumber
    from there when the session is resumed -- which is the duplicate-seq
    defect of 2026-09-16, and one session on this box (a probe killed
    mid-turn that day) still carries it: the file says 1 and the log ends at
    51. Raising it costs nothing and makes a resume of that session
    replayable; lowering it could never be right.
    """
    found = from_log(log_path)
    before = dict(meta)
    if not meta.get("title") and found["title"]:
        meta["title"] = found["title"]
    if not meta.get("last_message") and found["last_message"]:
        meta["last_message"] = found["last_message"]
    # A record with no stored status is answered by recovered_status, which
    # can only say "it has turns or it does not". The log says more: whether
    # the last turn actually succeeded. A record that stored one keeps it --
    # the rules in recovered_status apply to it on the way out.
    if not meta.get("status") and found["result_ok"] is not None:
        meta["status"] = "done" if found["result_ok"] else "failed"
    position = int(meta.get("record_seq") or meta.get("seq") or 0)
    meta.pop("seq", None)
    meta["record_seq"] = max(position, found["last_seq"])
    return meta != before


def backfill_records() -> tuple[int, int]:
    """Every record on the box, filled from its log once, at start.

    Once rather than on every read: the answer cannot change (a log this
    broker is not writing to does not grow), and the listing reads thirteen
    files today and several hundred one day -- one of them a 725-line log --
    on a route a phone hits on every pull-to-refresh. Written back, so the
    work is paid once per record and not once per boot.

    Returns (records seen, records changed) for the line at startup. A file
    that cannot be read or written is reported and skipped: a backfill is a
    convenience and must never be the reason a broker will not start.
    """
    seen = changed = 0
    for path in sorted(cfg.SESSION_DIR.glob("*.json")):
        seen += 1
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"backfill: {path.name}: unreadable ({exc})",
                  file=sys.stderr, flush=True)
            continue
        session_id = meta.get("session_id") or path.stem
        if not backfill(meta, cfg.SESSION_DIR / f"{session_id}.jsonl"):
            continue
        try:
            path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"backfill: {path.name}: not written ({exc})",
                  file=sys.stderr, flush=True)
            continue
        changed += 1
    return seen, changed


def sync_titles() -> tuple[int, list[tuple[str, str, str]]]:
    """Every record's title made the vault's title, once, at start.

    The broker used to name a session with its own model call and the
    transcript writer named the same conversation with another, so the home
    screen and the vault disagreed about what a conversation was called --
    "Household note and image attachment" on one and "Attachment check" on
    the other, 2026-09-22. The broker's call is gone; this is what the records
    written by it get.

    What is never touched: a title a person typed. It is marked
    ``title_source: "person"`` on the record from the rename onward. A record
    written before that field existed carries no mark at all, and the honest
    reading is that nothing on it records a rename -- every title on the box
    when this landed came from one of the two titlers. So they are synced, and
    each change is printed with both names, because a title the machine got
    wrong is recoverable from the line and a silent overwrite is not.

    Returns (records seen, [(id, was, now)]). Fail-soft per record: a file
    that will not read or write is reported and skipped, because a title is a
    convenience and must never be the reason the broker will not start.
    """
    seen = 0
    changed: list[tuple[str, str, str]] = []
    for path in sorted(cfg.SESSION_DIR.glob("*.json")):
        seen += 1
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"titles: {path.name}: unreadable ({exc})",
                  file=sys.stderr, flush=True)
            continue
        if meta.get("title_source") == "person":
            continue
        session_id = meta.get("session_id") or path.stem
        # The fork's id when there is one: that is the CLI session whose turns
        # are being filed, and so the id the writer's sidecar is under. A
        # Codex thread's turns are filed under its thread id.
        filed = adoption.transcript_title(
            meta.get("cli_session_id") or meta.get("thread_id") or session_id)
        if not filed or filed == meta.get("title"):
            if filed and meta.get("title_source") != "vault":
                meta["title_source"] = "vault"
            else:
                continue
        else:
            changed.append((session_id, meta.get("title") or "", filed))
            meta["title"] = filed
            meta["title_source"] = "vault"
        try:
            path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"titles: {path.name}: not written ({exc})",
                  file=sys.stderr, flush=True)
    return seen, changed


def prune_fork_duplicates() -> list[tuple[str, str]]:
    """The records a scan made of the broker's own forks, removed once.

    Between the fork landing (2026-09-20) and the scan learning about it
    (2026-09-22), replying to an adopted session left a second record of the
    same conversation: the CLI wrote the fork into its own session file, and
    the next scan found a file with a person in it, today's date on it and no
    record against its id. Four such records on this box, and the home screen
    drew one of them beside the conversation it was a copy of -- "Loam phase
    six planning", pinned and in Recent at once.

    A record is one of these when all four hold:

    - another record names it as the fork it drives (``cli_session_id``)
    - it says it came from the app (``origin``) -- what the scan writes
    - nothing has been said into it through this broker: no log of its own
    - it has never been forked itself (``cli_session_id`` of its own), which
      would make it a conversation somebody has gone on having here

    **Only the record goes.** The session file it points at is the CLI's, and
    this module does not write near one; it is also the fork's own history,
    which the record that survives now replays. Returns [(id, title)], both
    printed at start, because a record removed without its name is a thing
    nobody can check afterwards.
    """
    records: dict[str, dict] = {}
    for path in sorted(cfg.SESSION_DIR.glob("*.json")):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"prune: {path.name}: unreadable ({exc})",
                  file=sys.stderr, flush=True)
            continue
        records[meta.get("session_id") or path.stem] = meta
    driven = {meta.get("cli_session_id") for meta in records.values()
              if meta.get("cli_session_id")}
    removed: list[tuple[str, str]] = []
    for session_id in sorted(driven & set(records)):
        meta = records[session_id]
        if meta.get("origin") != adoption.ORIGIN_APP:
            continue
        if meta.get("cli_session_id"):
            continue
        log = cfg.SESSION_DIR / f"{session_id}.jsonl"
        try:
            if log.exists() and log.stat().st_size:
                continue                # spoken into here; not a bare copy
        except OSError:
            continue
        try:
            (cfg.SESSION_DIR / f"{session_id}.json").unlink()
            # An empty log of our own beside it is the broker's, not the
            # CLI's, and a log with no record is reachable by nothing.
            log.unlink(missing_ok=True)
        except OSError as exc:
            print(f"prune: {session_id}: not removed ({exc})",
                  file=sys.stderr, flush=True)
            continue
        removed.append((session_id, meta.get("title") or ""))
    return removed
