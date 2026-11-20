"""Sessions the owner had in the Claude Code app, as sessions the broker holds.

Every conversation he had on claude.ai/code or on his phone, until Remote
Control was retired on 2026-11-19, is a Claude Code session **on this box**:
the Remote Control servers ran them as the same login, in the same persona
workspace, and they left the same session file under ``~/.claude/projects/``
that a session started at a terminal here leaves. So they are not somewhere else to be imported from -- they are
already here, and the broker simply did not know about them.

**A record, and the conversation behind it** (2026-09-20). It finds those
session files and writes a broker record for each (``origin:
"claude-code-app"``, against ``"loam-ui"`` for a session the broker started
itself): who it belongs to, when it happened, what it is called, what was
last said in it. Nothing is copied -- the record points at the CLI's own
file and that file stays the only copy of the conversation. ``iter_events``
below translates that file into the broker's own event shape, which is what
lets an adopted session open in the app with its history in it rather than
as an empty thread wearing a title; the translation is read at every replay
and written nowhere. Forking one so it can be written into is the job after
this one.

**A record is a reading with a date on it** (2026-09-21). The owner goes on
talking in the app, so the file behind an adopted record grows after the
record was written, and the record then shows a conversation as it was hours
ago -- which is what he found. ``source_stat`` is the two numbers that say
whether the reading still holds, and ``summarise(after=...)`` hands back the
events past what a reader has already been given, so the broker can take the
reading again on a read and stream the difference to a thread somebody has
open. Nothing about that writes either: it is the same file, read again.

**Nothing here ever writes.** Not to the session file and not near it. The
Remote Control server that owned an app session could still have been
running when this was written, and two processes appending to one session file is a corrupted conversation of
the owner's; every path in this module is a read, and the module that comes to
resume one of these will have to fork rather than resume in place.

**One parser.** The session JSONL schema is internal and version-unstable
upstream, so every reader of it on this box goes through
``loam_transcript.iter_entries`` and the two predicates beside it. A second
parser would be a second understanding of the schema, and the day it moved
only one of them would be fixed.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import loam_config as lc

import loam_broker as cfg
import loam_clock
import loam_transcript as lt

CLAUDE_HOME = lc.CLAUDE_HOME
PROJECTS = CLAUDE_HOME / "projects"

# Where the transcript writer keeps what it filed for a session id. An adopted
# session wears the name its transcript wears, for the same reason a
# broker-started one does: two names for one conversation is two conversations
# as far as anybody reading is concerned.
TRANSCRIPT_STATE = lc.STATE / "transcripts"

# What a record says about where the session came from. A record written
# before this existed has neither and is answered as `loam-ui`, which is the
# only thing the broker had started until now.
ORIGIN_APP = "claude-code-app"
ORIGIN_UI = "loam-ui"

# How far back a scan looks. The owner asked for "the past two weeks"
# (2026-09-20); it is one number and it is here.
WINDOW_DAYS = 14

# Match the CLI's working-directory slug so the existing session directory is found.
_SLUG = re.compile(r"[^A-Za-z0-9]")

# A cheap pre-filter before anything is parsed. A persona has up to 230
# session files and one of them is 25 MB; JSON-parsing all of them at every
# broker start would cost minutes and most of a gigabyte. A line that could
# name a human origin contains this; a line that does not, cannot.
_HUMAN_HINT = b'"human"'

# How much of the end of a file to read looking for the last entry that
# carries a timestamp. The last line is often a `last-prompt` or `ai-title`
# entry with none, so this walks backwards; 64 KiB covers every real case on
# this box and the loop doubles it up to the limit rather than giving up.
_TAIL_BYTES = 64 * 1024
_TAIL_LIMIT = 4 * 1024 * 1024

# How many lines at the head of a file to walk looking for the first entry
# that carries a uuid (`first_uuid`). The CLI opens a session file with a few
# bookkeeping entries that have none -- four on the files measured here --
# and a conversation's first turn is well inside this.
_HEAD_LINES = 32


def project_dir(workspace: Path) -> Path:
    """The CLI's session folder for a working directory."""
    return PROJECTS / _SLUG.sub("-", str(workspace))


def epoch_of(iso: str | None) -> float | None:
    """A session file's ISO timestamp as an epoch, or None if it is not one."""
    if not iso:
        return None
    try:
        return datetime.strptime(
            lt.format_timestamp(iso), "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def last_timestamp(path: Path) -> str | None:
    """When the session file last had something written into it, by its own
    reckoning rather than the filesystem's.

    **The filesystem's mtime is not this, and using it was a real mistake
    waiting to happen.** Something on this box touches old session files --
    measured 2026-09-20, a persona's session whose last entry is 2026-09-05 has an
    mtime of 2026-09-08 01:30, and several dozen files share that same 01:30
    minute on days after their last turn. So mtime runs LATE, which makes it
    a safe cheap filter (a file too old by mtime is certainly too old) and a
    false answer to "when did this conversation last happen".
    """
    window = _TAIL_BYTES
    size = path.stat().st_size
    while True:
        with path.open("rb") as handle:
            handle.seek(max(0, size - window))
            chunk = handle.read()
        lines = chunk.split(b"\n")
        if window < size:
            lines = lines[1:]          # the first is a fragment of a line
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(entry, dict) and entry.get("timestamp"):
                return lt.format_timestamp(entry["timestamp"])
        if window >= size or window >= _TAIL_LIMIT:
            return None
        window *= 2


def first_uuid(path: Path) -> str | None:
    """The uuid of the first entry in a session file that carries one.

    **The nearest thing the CLI writes to a fork mark** (measured on this box
    2026-09-22). ``--fork-session`` copies the resumed conversation into a new
    file and rewrites ``sessionId`` on every entry to the fork's own, so
    nothing in a fork's file names the session it came out of -- the id, the
    path and the folder are all the fork's. What it does NOT rewrite is the
    entries themselves: the first eight uuids of a fork read here are the
    first eight uuids of its parent, byte for byte. So two files whose first
    entry wears one uuid are one conversation written down twice, and a scan
    that knows the parent can pass the copy over.

    Read off the head of the file and nothing more: the bookkeeping the CLI
    opens a file with (``mode``, ``atis-latch``, ``queue-operation``) carries
    no uuid, so this walks a few lines rather than assuming the first one.
    None when the file will not open or holds no such entry, which is a file
    this says nothing about.
    """
    try:
        with path.open("rb") as handle:
            for _ in range(_HEAD_LINES):
                line = handle.readline()
                if not line:
                    break
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(entry, dict) and isinstance(
                        entry.get("uuid"), str) and entry["uuid"]:
                    return entry["uuid"]
    except OSError:
        return None
    return None


def source_stat(path: Path) -> tuple[int | None, int | None]:
    """The two numbers a record keeps about the file its history is in.

    ``(size, mtime)``, and ``(None, None)`` when the file is not there. A
    record written from a file another process is still appending to is a
    reading with a date on it, and these are how the next reader asks whether
    the reading still holds: the size says whether anything was *appended*,
    the mtime whether anything touched the file at all.

    Both, and not the mtime alone. mtime moves on this box for reasons that
    are not conversations -- see ``last_timestamp`` -- so a file whose mtime
    changed and whose size did not has nothing new in it, and re-reading it
    would cost a parse of up to 8.5 MB to learn that. The mtime is truncated
    to the second because that is the resolution the comparison needs and a
    float in a record file invites a mismatch that is only a rounding.
    """
    try:
        stat = path.stat()
    except OSError:
        return None, None
    return stat.st_size, int(stat.st_mtime)


def has_person(path: Path) -> bool:
    """Did a person type into this session, ever.

    The whole of what separates a conversation from a wake, a cron, a probe
    or a session the broker itself drove: the session file states the origin
    of every prompt, and only a person's says ``human``. See
    ``loam_transcript.is_person``.

    Byte-scanned first and parsed second. The marker is cheap to look for and
    a line that cannot contain it does not need to become a dict.
    """
    try:
        with path.open("rb") as handle:
            for line in handle:
                if _HUMAN_HINT not in line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if lt.is_person(entry):
                    return True
    except OSError:
        return False
    return False


def transcript_title(session_id: str) -> str | None:
    """The name the transcript writer filed this session under, if it did.

    The record and the vault then say the same thing about one conversation,
    which is the same rule a broker-started session follows -- it calls the
    writer's own titler. Here the titler has already run, days ago, and its
    answer is in the file's frontmatter; calling it again would spend a model
    call to maybe disagree with the record.
    """
    try:
        state = json.loads(
            (TRANSCRIPT_STATE / f"{session_id}.json").read_text(
                encoding="utf-8"))
        path = Path(state["path"])
        head = path.read_text(encoding="utf-8")[:400]
    except (OSError, json.JSONDecodeError, KeyError):
        return None
    for line in head.splitlines():
        if line.startswith("title:"):
            value = line[len("title:"):].strip()
            if value[:1] == '"' and value[-1:] == '"':
                try:
                    return json.loads(value) or None
                except json.JSONDecodeError:
                    return value.strip('"') or None
            return value or None
    return None


# -- the conversation, as this interface's events ---------------------------
#
# The translation, and it is a pure function of the file: same bytes in, same
# events out, no clock and no state of the broker's in it. That is what lets
# the scan count the events a session will replay and the replay produce
# exactly that many, without either one writing the other's answer down.

# Private by contract, in the session file and on the wire alike. A thinking
# block is not something the persona said to anybody, and a conversation
# reopened in the app must not show reasoning the app itself hides.
PRIVATE_BLOCKS = ("thinking", "redacted_thinking")

# The CLI's own word for a message it wrote itself rather than one a model
# produced -- a spend-limit notice, an interrupt. It appears where a model
# name goes, and a turn must not be labelled with it: "this turn ran on
# <synthetic>" is a claim about nothing.
SYNTHETIC_MODEL = "<synthetic>"

# The subtype a replayed turn wears when the file says the turn ended badly.
# The CLI's own vocabulary for the same thing, so a client has one set of
# words to read; `is_error` says it too, because the live envelope says it in
# both places and a reader may be looking at either.
ERROR_SUBTYPE = "error_during_execution"


def _blocks(content) -> list:
    """A message body as a list of blocks, whatever shape it arrived in."""
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _has_tool_result(content) -> bool:
    return any(b.get("type") == "tool_result" for b in _blocks(content))


def iter_events(path: Path, session_id: str):
    """The conversation in a CLI session file, in the broker's event shape.

    One event per thing the interface draws, in the order it happened, and
    the shapes are the ones a live session already streams:

    - ``broker.user_message`` for each turn a PERSON typed. The CLI does not
      echo a person's words on its own stream, which is why this event exists
      at all (see ``Session.send_message``), and a replay that left it out
      would show answers with no questions.
    - ``assistant``, **one per content block**, because that is what the CLI
      streams and therefore what a client reads: a sentence said before the
      tools and each tool call are separate events with the same message
      around them.
    - ``user`` for a tool result, which is the CLI feeding itself and is
      machinery a page folds away.
    - ``result`` at each turn boundary, carrying what the file knows.

    What is deliberately not here: thinking (private), subagent traffic
    (``isSidechain`` -- machinery, and never the persona's own words), and
    the CLI's self-talk -- ``/clear``, the local-command caveat, a
    subagent's ``task-notification`` -- which are ``user`` entries a person
    did not type and that carry no tool result either.

    Every event carries ``adopted: true`` and the file's own ``ts``. Neither
    is on a live event: a client needs no special case for them, and the
    engineer looking at a stream needs to know which half of it came out of
    a file written by somebody else's process.

    ``adopted`` and not ``replayed``, which is the word the RECORD uses for
    the count of these. One stream carries both -- the snapshot says
    ``replayed: 22`` and each of those 22 events says ``adopted: true`` --
    and a field that means "how many" on one object and "yes" on the next is
    the ``seq``/``record_seq`` mistake of 2026-09-16 wearing a new hat.

    Unnumbered on purpose. ``seq`` is the broker's to assign, and it is
    assigned in ``Session.replay`` so that the numbering of a session and the
    translation of a file stay two separate ideas.
    """
    model: str | None = None
    first: str | None = None
    last: str | None = None
    spoke = 0                  # assistant messages since the last boundary
    failed = False
    usage: dict | None = None  # the last assistant message's, for the context

    def boundary() -> dict:
        """The turn that has just ended, as the envelope a turn ends with.

        Cost is ``null`` and stays ``null``: the session file records no
        per-turn spend, and the only total it ever carries (a ``cost-state``
        entry, in 1 of the 27 files on this box) is the whole session's. A
        number the file does not state is not one to invent -- the record's
        `turns` stays empty for the same reason.

        ``context`` is the exception, and only half of it. The file DOES state
        each request's usage on its assistant entries, so ``used`` is the same
        sum a live turn's is, over the last of them (2026-09-22). ``window``
        stays null: a live turn reads it from the result envelope's
        ``modelUsage``, and nothing in a session file says what window the
        model had. Both keys are present either way, so a page reading a
        replayed turn and a page reading a live one read one shape.
        """
        return {"type": "result",
                "subtype": ERROR_SUBTYPE if failed else "success",
                "is_error": failed,
                "session_id": session_id,
                "model": model,
                "num_turns": spoke,
                "duration_ms": _span_ms(first, last),
                "started_at": first,
                "total_cost_usd": None,
                "usage": None,
                "context": {"used": cfg.context_used(usage), "window": None},
                "ts": last,
                "adopted": True}

    for entry in lt.iter_entries(path):
        if lt.is_subagent(entry):
            continue
        kind = entry.get("type")
        if kind not in ("user", "assistant"):
            # `attachment`, `ai-title`, `last-prompt`, `queue-operation`,
            # `system`, `mode`, `atis-latch`, `custom-title`, `agent-name`,
            # `cost-state` -- the CLI's bookkeeping, not a conversation, and
            # most of it carries no timestamp anyway.
            continue
        at = lt.format_timestamp(entry.get("timestamp", "")) or None
        message = entry.get("message") or {}

        if lt.is_person(entry):
            if spoke:
                yield boundary()
            model, first, last, spoke, failed = None, at, at, 0, False
            usage = None
            yield {"type": "broker.user_message",
                   "session_id": session_id,
                   "text": loam_clock.strip(lt._text_of(message.get("content"))),
                   "ts": at,
                   "adopted": True}
            continue

        if kind == "assistant":
            first = first or at
            last = at or last
            spoke += 1
            said = message.get("model")
            if isinstance(said, str) and said and said != SYNTHETIC_MODEL:
                model = said
            # The context this request held, kept so the turn's boundary can
            # state the last one. Subagent entries never reach here (they are
            # skipped at the top of the loop), which is the same "top-level
            # only" rule a live turn's reading follows.
            if isinstance(message.get("usage"), dict):
                usage = message["usage"]
            # An error the CLI wrote in the model's place -- a spend limit
            # reached mid-conversation is the real one on this box. The turn
            # it ends did not succeed, and a replay that called it `success`
            # would be the record quietly disagreeing with the transcript.
            if entry.get("isApiErrorMessage") or entry.get("apiErrorStatus"):
                failed = True
            for block in _blocks(message.get("content")):
                if block.get("type") in PRIVATE_BLOCKS:
                    continue
                # Every other block passes through in the message it came in,
                # including one this translation has no name for -- `fallback`
                # (the CLI's note that a model was swapped mid-turn) is the
                # one such on this box today. A client that does not know a
                # block ignores it; a translation that dropped it would lose
                # something the file states and nobody would know.
                yield {"type": "assistant",
                       "session_id": session_id,
                       "parent_tool_use_id": None,
                       "message": dict(message, content=[block]),
                       "ts": at,
                       "adopted": True}
            continue

        # A `user` entry nobody typed. A tool result is the conversation's
        # machinery and belongs on the stream; the rest is the CLI talking to
        # itself and belongs nowhere.
        if _has_tool_result(message.get("content")):
            last = at or last
            yield {"type": "user",
                   "session_id": session_id,
                   "parent_tool_use_id": None,
                   "message": {"role": "user",
                               "content": message.get("content")},
                   "ts": at,
                   "adopted": True}

    if spoke:
        yield boundary()


def _span_ms(first: str | None, last: str | None) -> int | None:
    """How long a turn took, by the file's own two timestamps."""
    start, end = epoch_of(first), epoch_of(last)
    if start is None or end is None:
        return None
    return int(round((end - start) * 1000))


# -- what a record says ----------------------------------------------------

def summarise(path: Path, session_id: str, agent: str,
              after: int | None = None) -> dict:
    """The facts a record is made of, in one streamed pass over the file.

    Read off the translation above rather than off the entries, so that the
    number the record carries as ``record_seq`` is by construction the number
    of events the replay will produce. Two passes with two ideas of what
    counts as an event is how a record comes to promise 41 and a stream to
    deliver 43, and the next process then numbers on top of the wrong one.

    Streamed and counted rather than collected: the largest session file on
    this box is 25 MB, and holding its events to look at the last one would
    be most of a gigabyte across a scan. Nothing of the conversation is kept
    beyond the two sentences the record shows -- the opening words, which
    stand in for a title, and the last thing the persona said, which is what
    a row on the home screen carries.

    ``messages`` is how many turns and replies the file holds, and is here so
    that a file with no thread in it can be passed over rather than adopted
    as an empty row. ``events`` is what the replay will be.

    ``after`` asks for the events past a count the caller has already handed
    out, and they come back in ``appended`` -- which is how a thread somebody
    is reading follows a file the owner is still talking into (2026-09-21).
    They are **unnumbered**, like everything ``iter_events`` produces: ``seq``
    is the broker's to assign, and it assigns it from where the record's
    numbering had reached. Without ``after`` nothing is collected, because the
    counting pass runs over every adopted record on a read of the list and the
    largest file here is 8.5 MB.
    """
    events = 0
    messages = 0
    created: str | None = None
    last_event_at: str | None = None
    opening: str | None = None
    last_message: str | None = None
    appended: list[dict] = []
    for event in iter_events(path, session_id):
        events += 1
        if after is not None and events > after:
            appended.append(event)
        kind = event.get("type")
        stamp = event.get("ts")
        if stamp:
            created = created or stamp
            last_event_at = stamp
        if kind == "broker.user_message":
            messages += 1
            opening = opening or (event.get("text") or None)
        elif kind == "assistant":
            messages += 1
            said = lt._text_of((event.get("message") or {}).get("content"))
            if said:
                last_message = said
    return {"session_id": session_id, "agent": agent, "messages": messages,
            "events": events, "created": created,
            "last_event_at": last_event_at, "opening": opening,
            "last_message": last_message, "source": str(path),
            "appended": appended}


def candidates(agent: str, workspace: Path, held, now: float | None = None,
               window_days: int = WINDOW_DAYS, forks=(),
               heads=None) -> tuple[list[Path], list[dict]]:
    """This persona's app sessions worth adopting, and what was passed over.

    ``held`` is every session id the broker already has a record for, so
    running this twice adopts nothing the second time -- which is what makes
    the route idempotent and the start-up scan cheap after the first one.

    ``forks`` and ``heads`` are the two nets under **a conversation adopted
    twice** (2026-09-22). Replying to an adopted session forks it, and the
    fork is a session file of the CLI's own, in the same folder, with a person
    in it and today's date on it -- so the next scan met every test above and
    adopted it as a second conversation with the same title and the same
    history. A persona saw "Loam phase six planning" drawn twice on the home
    screen, once pinned and once in Recent, and the page was right: they were
    two records.

    - ``forks`` is every record's ``cli_session_id``: the fork this broker
      itself drives, known by id, and the net that catches the ordinary case.
    - ``heads`` maps the first uuid of the file behind each record the broker
      holds (``first_uuid``) to the id of the record it belongs to, and
      catches a fork whose id the broker never wrote down -- one the CLI
      minted before a crash, say. It is the copied conversation itself, not a
      mark the CLI makes; see ``first_uuid``. **It maps to an id rather than
      being a set of uuids** because the commonest file with a record's head
      in it is that record's own file, which a full rescan re-reads on
      purpose: a set here would make every adopted record a copy of itself.

    The second return value is the skipped files with a reason each, because
    a scan that silently drops a conversation is a scan nobody can check.
    """
    now = time.time() if now is None else now
    cutoff = now - window_days * 86400
    forks = set(forks)
    heads = dict(heads or {})
    folder = project_dir(workspace)
    if not folder.is_dir():
        return [], [{"path": str(folder), "why": "no session folder"}]
    found: list[Path] = []
    passed: list[dict] = []
    for path in sorted(folder.glob("*.jsonl")):
        session_id = path.stem
        try:
            if path.stat().st_mtime < cutoff:
                continue               # certainly older; mtime runs late
        except OSError:
            continue
        if session_id in held:
            passed.append({"session_id": session_id, "why": "already held"})
            continue
        if session_id in forks:
            # The fork of an adopted session, which the record it belongs to
            # already is. Adopting it makes the same conversation twice.
            passed.append({"session_id": session_id,
                           "why": "the fork of a session already held"})
            continue
        stamp = last_timestamp(path)
        epoch = epoch_of(stamp)
        if epoch is not None and epoch < cutoff:
            continue                   # touched lately, last spoken to long ago
        if not has_person(path):
            passed.append({"session_id": session_id,
                           "why": "no human turn (a wake, a cron or a probe)"})
            continue
        if heads:
            # Every other test passed. If the conversation in it is one the
            # broker already holds under ANOTHER id, this file is a fork
            # nobody wrote down; under its own id it is simply itself.
            owner = heads.get(first_uuid(path))
            if owner is not None and owner != session_id:
                passed.append({"session_id": session_id,
                               "why": "a copy of a conversation already held"})
                continue
        found.append(path)
    return found, passed
