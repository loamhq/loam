"""Adopt local Claude Code app sessions from their workspace transcripts. Keep a broker
record pointing at the source and translate its events on replay. A resumed
conversation forks the source, preserving the original history.
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
    """Read the latest timestamp from the session entries. A file touch can advance mtime
    without a new turn, so mtime serves only as a cheap exclusion for old files.
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
    """Read the first entry UUID. A CLI fork rewrites sessionId but copies entry UUIDs,
    allowing a scan to recognize a shared history.
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
    """Return the source's (size, mtime), or (None, None) when missing. A changed mtime
    alone can mean a file touch, while growth signals appended history.
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
    """Translate typed prompts, assistant blocks, tool results and turn boundaries into
    live-stream event shapes. Omit private reasoning, sidechains and harness messages.
    Preserve source timestamps and set adopted on events. Replay assigns the sequence
    numbers.
    """
    model: str | None = None
    first: str | None = None
    last: str | None = None
    spoke = 0                  # assistant messages since the last boundary
    failed = False
    usage: dict | None = None  # the last assistant message's, for the context

    def boundary() -> dict:
        """Build a turn boundary from the source. Report used context from the last request
        when available. Leave per-turn cost and the context window unknown because the
        transcript supplies neither.
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
    """Stream the translated events once to build the record. Use the same translation as
    replay so record_seq equals the event count without buffering the session.
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
    """Find adoptable files for this persona and report those skipped. Exclude held
    records, known CLI forks and copies that share an original's first entry UUID. A
    full rescan can still refresh the original source.
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
