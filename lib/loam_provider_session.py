"""Interactive provider sessions and their numbered event journals.

A session owns its provider I/O, records, subscriptions and pending questions.
The caller owns admission, thread policy and the app's HTTP interface.
"""

from __future__ import annotations

import asyncio
import base64
import errno
import json
import os
import shlex
import signal
import sys
import tempfile
import time
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path

import loam_adopt as adoption
import loam_antigravity as agy
import loam_broker as cfg
import loam_clock
import loam_codex as codex
import loam_loading
import loam_questions as questions_fmt
from loam_session import local_command_output
import loam_skills
import loam_transcript
import loam_waiters

now_iso = cfg.now_iso

# What a status means, and it is the page's whole vocabulary for a session:
#
#   working      a turn is in flight and the model is generating
#   waiting      the turn's result has arrived and the session is parked on
#                background work it launched -- nothing is being generated
#   needs_input  a permission is pending and the turn is paused on it
#   done         idle after a result that succeeded
#   failed       the last result was an error, or the process ended other
#                than by request
#   idle         started, no turn yet
STATUSES = ("working", "waiting", "needs_input", "done", "failed", "idle")

# The record was written while something was happening and the process is now
# gone, so that something did not finish. A page that read `working` off a
# record whose broker restarted three days ago would draw a spinner forever.
UNFINISHED = ("working", "needs_input")


def recovered_status(stored: str | None, turns: list) -> str:
    """The status of a session whose process is gone, from its stored one."""
    if stored in UNFINISHED:
        return "failed"
    if stored == "waiting":
        # A stored waiting turn already received its result. Its background work ended with
        # the process.
        return "done"
    if stored in STATUSES:
        return stored
    # Without a stored status, only the presence of turns distinguishes a completed session.
    return "done" if turns else "idle"


# The one sum, in lib/loam_broker.py because the adoption module needs the
# same arithmetic over an adopted session's own assistant messages -- two
# copies of a sum is two answers to one question the day somebody changes
# one. Named here so the rest of this file reads as though it were local,
# which it was until the second reader appeared.
context_used = cfg.context_used


def context_window(envelope: dict, model: str | None) -> int | None:
    """Look up contextWindow for the model that answered this turn. One envelope may bill
    both a helper with a 200k window and the answering model with a 1M window. Return
    None if that model or its row is missing.
    """
    rows = envelope.get("modelUsage")
    if not model or not isinstance(rows, dict):
        return None
    row = rows.get(model)
    if not isinstance(row, dict):
        return None
    window = row.get("contextWindow")
    if isinstance(window, (int, float)) and not isinstance(window, bool):
        return int(window)
    return None


def compaction_event(session_id: str, boundary: dict) -> dict:
    """Translate a provider compact_boundary event into a divider using its reported token
    counts.
    """
    meta = boundary.get("compact_metadata")
    meta = meta if isinstance(meta, dict) else {}

    def count(name: str) -> int | None:
        value = meta.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
        return None

    trigger = meta.get("trigger")
    return {"type": "broker.compaction",
            "session_id": session_id,
            "at": now_iso(),
            "trigger": trigger if isinstance(trigger, str) else None,
            "tokens_before": count("pre_tokens"),
            "tokens_after": count("post_tokens"),
            "duration_ms": count("duration_ms")}


def history_bytes(origin: str | None, cli_session_id: str | None,
                  source_size: int | None) -> int | None:
    """How many bytes of history a first message into this session re-sends.

    An adopted record that has never been spoken to here is a reading of the
    Claude Code app's own session file, and the first message into it forks
    that file -- so the whole conversation is read back into a model's context
    before a word of the new turn is answered. The size of that file is what
    says how much. The page turns it into money; this says only the bytes.

    ``None`` for everything else, and that is the honest answer rather than a
    zero: a session the broker started has its history in this broker's log and
    resumes without re-sending it, and a session already forked runs on a file
    of the CLI's own.
    """
    if origin != adoption.ORIGIN_APP or cli_session_id:
        return None
    return source_size if isinstance(source_size, int) else None


def with_context(turn: dict) -> dict:
    """Supply the context keys on older turn records too. Missing readings remain unknown."""
    context = turn.get("context")
    if not isinstance(context, dict):
        context = {}
    turn["context"] = {"used": context.get("used"),
                       "window": context.get("window")}
    return turn

def parse_iso(value, fallback: float) -> float:
    """A record's UTC string back to an epoch, or the fallback."""
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return fallback


def first_words(text: str) -> str:
    """The stand-in title a session wears until the titler has answered.

    The first message's opening words, on one line. A session must never be
    nameless on the page -- the design draws a title on every row, and the
    first thing a person recognises their own session by is what they said to
    start it.
    """
    line = " ".join((text or "").split())
    if len(line) <= cfg.TITLE_FALLBACK_CHARS:
        return line
    cut = line[:cfg.TITLE_FALLBACK_CHARS].rsplit(" ", 1)[0]
    return (cut or line[:cfg.TITLE_FALLBACK_CHARS]) + "…"

def human_size(count: int) -> str:
    """A size as a refusal says it, which is a sentence a person reads."""
    if count < 1024:
        return f"{count} B"
    if count < 1024 * 1024:
        return f"{round(count / 1024)} KB"
    return f"{round(count / (1024 * 1024), 1)} MB"

CHOOM = "/usr/bin/choom"


def first_to_go(argv: list[str]) -> list[str]:
    """Launch the provider with the child OOM score and preserve its PID across exec so the
    ordering also applies to its descendants.
    """
    if not os.access(CHOOM, os.X_OK):
        return list(argv)
    return [CHOOM, "-n", str(cfg.CHILD_OOM_SCORE_ADJ), "--", *argv]

# Reasons a session is ended on purpose by Loam (not by the owner and not by a
# crash/OOM). A thread turn whose session exits with one of these before any
# reply or tool step recovers automatically on a fresh/resumed session.
RECLAIM_REASONS = ("room", "idle", "pressure", "retire")


class BrokerError(Exception):
    """Returned to the client as a 4xx with this text. Never a stack trace:
    the client is a web app and its user is a person."""

    def __init__(self, status: int, message: str,
                 detail: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        # Fields answered beside `error`, for a client that wants more than
        # the sentence (admission's 429: `reason`, `memory`, `busy`, `hint`).
        self.detail = detail or {}


# Pending requests are approvals or questions. Keep the original provider message alongside
# structured questions.

async def configure_thread(session, title, model, effort):
    # Thread sessions resume by ID after the shorter thread idle timeout.
    session.idle_timeout_s = cfg.THREAD_IDLE_S
    # The prompt opens with its own clock line; the broker's would be a
    # second copy of the same sentence.
    session.tell_time = False
    # Apply a new model to the running process. Clearing the override restores the persona
    # default on its next launch.
    if getattr(session, "model", None) != model and hasattr(session, "set_choices"):
        await session.set_choices({"model": model})
    # A cleared effort override also takes effect on the next launch.
    if effort and getattr(session, "effort", None) != effort \
            and hasattr(session, "set_choices"):
        await session.set_choices({"effort": effort})
    # Out of the Classic chats list, which stays the list it was.
    session.archived = True
    session.title = title
    session.title_source = "person"
    session.save()


def permission_decision(payload: dict) -> tuple[str, list | None]:
    behavior = payload.get("behavior")
    if behavior not in ("allow", "deny"):
        raise BrokerError(400, "`behavior` is 'allow' or 'deny'")
    updates = payload.get("updatedPermissions")
    if updates is not None:
        # The CLI's own suggestions, echoed back: a list of its
        # `PermissionUpdate` objects, each for this process only.
        # A suggestion can name `localSettings`, which would have
        # the CLI write the persona's own settings file from a
        # tap on a card; persona configs are operator-authored,
        # so only `session` is accepted and nothing is trimmed.
        if (behavior != "allow" or not isinstance(updates, list)
                or not all(isinstance(u, dict) for u in updates)):
            raise BrokerError(
                400, "`updatedPermissions` is a list of the "
                     "request's own permission_suggestions, "
                     "on an allow")
        if any(u.get("destination") != "session" for u in updates):
            raise BrokerError(
                400, "`updatedPermissions` may only carry "
                     "suggestions whose destination is 'session'")
    return behavior, updates


def _questions_of(req: dict) -> list[dict] | None:
    asked = req.get("questions")
    return asked if isinstance(asked, list) and asked else None


def _request_event(event: dict, req: dict) -> dict:
    asked = _questions_of(req)
    event["kind"] = "question" if asked is not None else "approval"
    if asked is not None:
        event["message"] = req.get("question_message")
        event["questions"] = asked
    return event


# Journal every unanswered close so replay cannot leave a live prompt. Expiration keeps the
# process running, cancellation comes from the provider, and an exit ends all open requests.
CLOSED_UNANSWERED = ("expired", "cancelled", "ended")


def _resolved_event(session_id: str, request_id: str, behavior: str,
                    req: dict, message: str | None,
                    answers: dict | None, scope: str | None = None) -> dict:
    event = {"type": "broker.permission_resolved",
             "session_id": session_id,
             "request_id": request_id,
             "behavior": behavior,
             "tool_name": req.get("tool_name"),
             "message": message if behavior in ("deny", "expired") else None,
             "kind": "question" if _questions_of(req) is not None else "approval"}
    if scope is not None:
        # The allow carried rules for the rest of this process
        # (`updatedPermissions`), so a page can say "Allowed for this chat".
        event["scope"] = scope
    if answers is not None:
        # The event is written to the session's log, so a secret's answer is
        # not on it: it went to the provider and nowhere else.
        secret = {q["id"] for q in _questions_of(req) or [] if q.get("secret")}
        event["answers"] = {qid: (None if qid in secret else values)
                            for qid, values in answers.items()}
    return event


def _not_saved_reason(exc: OSError) -> str:
    """The sentence a page shows for a turn the broker could not write down."""
    if exc.errno == errno.ENOSPC:
        return "couldn't save this reply: the server's disk is full"
    return f"couldn't save this reply: {exc.strerror or exc}"


class Session:
    """Own one session and its provider process. Choose its ID before launch so
    clients can address it immediately and use the same ID to resume it.
    """

    # Class defaults so tests constructing instances via __new__ still see them.
    _stop_requested = False
    _stop_reason: str | None = None
    _stopping_event: asyncio.Event | None = None

    def __init__(self, agent: str, session_id: str | None = None,
                 provider: str | None = None, model: str | None = None,
                 effort: str | None = None) -> None:
        self.agent = agent
        self.id = session_id or str(uuid.uuid4())
        self.provider = provider or cfg.DEFAULT_PROVIDER
        # This chat's model and effort, per the design's + menu. Null is not
        # "unknown" and not "fable": it is "the persona's own setting", which
        # is the honest state, because nothing goes on the launch line and the
        # CLI reads the persona's settings.json itself. Writing a default in
        # here would pin every session to whatever this file said on the day
        # it was started, and the owner changing a persona's model would then
        # change nothing they could see.
        self.model = model or None
        self.effort = effort or None
        self.workspace = cfg.workspace(agent)
        self.proc: asyncio.subprocess.Process | None = None
        self.stdout_task: asyncio.Task | None = None
        self.images_tool = False
        self.seq = 0
        # A session id survives resumption. This identity belongs to the
        # particular host object which owns outstanding provider writes.
        self.incarnation = str(uuid.uuid4())
        self._dispatches: dict[str, asyncio.Task] = {}
        self.subscribers: set[asyncio.Queue] = set()
        self.pending: dict[str, dict] = {}     # request_id -> can_use_tool request
        self.turns: list[dict] = []            # one record per result envelope
        self.created = now_iso()
        self.last_activity = time.time()
        self.state = "starting"
        self.exit_code: int | None = None
        self.resumed = False
        # What the page draws a row from, and all of it follows the session
        # across devices because it is on the record and not in a browser.
        self.title: str | None = None
        # Where that title came from, which is what decides whether anything
        # may replace it. `None` is the stand-in -- the opening words of the
        # first message -- `"vault"` is the name the transcript writer filed
        # this conversation under, and `"person"` is a rename, which outranks
        # both and is never overwritten by anything here.
        self.title_source: str | None = None
        self.pinned = False
        self.archived = False
        # How far down this conversation has been read. It is on the record
        # and not in a browser for the same reason the title is: one person,
        # one box, and a chat read on the phone must not come back unread on
        # the laptop. 0 is "nothing read", which is what every session was
        # before this field existed and is the honest state for one nobody
        # has opened.
        self.seen_seq = 0
        self.last_message: str | None = None
        self.last_event_at: str | None = None
        # Where this session came from, and the file its history is in.
        #
        # For a session the broker started, both are the defaults: it was
        # born here and its history is this broker's own log. For a session
        # adopted from the Claude Code app, `source` is the CLI's own session
        # file -- read to make the record and not read again, until the job
        # that replays it. See lib/loam_adopt.py.
        self.origin = adoption.ORIGIN_UI
        self.source: str | None = None
        # How many of this session's events come out of that file. It is the
        # first `replayed` numbers of the stream, and the broker's own log
        # begins at `replayed + 1` -- which is what makes a turn taken here
        # later append to the history rather than renumber on top of it.
        # `None` means nothing has been translated for this session.
        self.replayed: int | None = None
        # The reading of that file the record was made from: how big it was
        # and when it was last touched. A file the broker does not write is a
        # file that grows behind the record's back -- the owner goes on talking
        # in the app -- so every read of this record asks these two numbers
        # whether the reading still holds. Null for a session the broker
        # started, whose history is its own log. See `refresh_from_source`.
        self.source_size: int | None = None
        self.source_mtime: int | None = None
        # The CLI session this broker actually drives, when it is not this
        # record's own id. It is set for one kind of session only: an adopted
        # one, which is forked rather than resumed the first time anybody
        # sends into it, because its own id names a file the app may still be
        # writing. `forked_from` is the app session the fork came out of --
        # the same id this record wears, said where a reader of the record
        # will look for it rather than left to be inferred.
        #
        # Null on everything else, and that is the test the launch line asks:
        # a session with no `cli_session_id` that came from the app has not
        # been forked yet. See `argv`.
        self.cli_session_id: str | None = None
        self.forked_from: str | None = None
        # Codex resumes by provider thread ID. Claude Code normally uses the record ID.
        self.thread_id: str | None = None
        # What status() reads. A turn is in flight from the moment a person's
        # message goes in -- or the CLI writes the `system`/`init` that opens
        # one -- until its result comes back out.
        self._turn_active = False
        # Thread sessions override the classic idle timeout for the lifetime of the process.
        self.idle_timeout_s: float | None = None
        # Thread prompts supply their own clock line.
        self.tell_time = True
        # The background tasks the CLI says are live behind this session right
        # now, as it states them (`system`/`background_tasks_changed`): a list
        # of `{task_id, task_type, description}`. The record answers the count;
        # the stream carries both. Never restored from the file: a background
        # task is a child of a process, and this object has no process yet.
        self.background_tasks: list[dict] = []
        # Time each task from its first event because the provider omits the start timestamp.
        self._task_seen: dict[str, str] = {}
        # Task ids that have left that list without their notification having
        # arrived yet, against the moment each left. The session is still
        # waiting while one of these is outstanding -- the notification is what
        # wakes the persona for another turn -- and `_settle` is the backstop
        # that stops one that never comes from pinning the row on `waiting`.
        self._awaiting: dict[str, float] = {}
        # A completion notification can precede removal from the task list. Remember it so
        # the turn does not wait for finished work.
        self._notified: dict[str, float] = {}
        # A task notification has fired and no result has closed a turn since,
        # which means another turn is on its way whether or not it has started.
        self._resuming = False
        self._settle = None
        # The usage of the last top-level assistant message of the turn being
        # read, which is the last request that turn made and so the reading of
        # how full the context was when it ended. See `context_used`.
        self._turn_usage: dict | None = None
        # What a compaction inside the turn being read left behind: the
        # marker's `post_tokens`, and the model the `init` after it named.
        # See the result branch of `_handle`.
        self._compacted_to: int | None = None
        self._init_model: str | None = None
        self._last_result_ok: bool | None = None
        # Whether the log is known to end on a newline. Not known until the
        # first write looks: a log a failed write tore, under this broker or
        # an earlier one, ends mid-line, and the next event appended to it
        # would be glued onto the fragment and lost to every replay. See
        # `_append`.
        self._log_ends_clean = False
        # Why something in this turn could not be written down, if it could
        # not. Read at the turn's `result`, which then counts as failed.
        self._unsaved: str | None = None
        self._stop_requested = False
        self._stop_reason: str | None = None
        self._stopping_event: asyncio.Event | None = None
        self._restored_status: str | None = None
        # Whether this session has the name it is going to keep, and whether
        # somebody is currently looking for one. The second exists because the
        # look is now a poll spanning a minute and a turn can end inside it:
        # two tasks reading the same file would only race to agree.
        self._titled = False
        self._titling = False
        # Set by the Broker so that a record written here reaches the live
        # list. A Session built without one (a test, a probe) simply has no
        # listeners, which is not an error.
        self.on_record_change = None
        self.on_unexpected_exit = None
        self._write_lock = asyncio.Lock()
        cfg.SESSION_DIR.mkdir(parents=True, exist_ok=True)
        # Resume the existing journal sequence and turn list so replay cursors remain valid
        # across processes.
        if self.meta_path.exists():
            try:
                prior = json.loads(self.meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                prior = {}
            # Older records store the last journal position as seq.
            self.seq = int(prior.get("record_seq") or prior.get("seq") or 0)
            self.turns = list(prior.get("turns") or [])
            self.created = prior.get("created") or self.created
            # Everything a person put on the record stays on it. A title they
            # renamed, a pin, an archive and the provider the session was
            # started on all survive an end and a resume, because they are
            # facts about the session and not about the process.
            self.title = prior.get("title") or None
            self.title_source = prior.get("title_source") or None
            self.pinned = bool(prior.get("pinned"))
            self.archived = bool(prior.get("archived"))
            self.seen_seq = int(prior.get("seen_seq") or 0)
            self.provider = provider or prior.get("provider") or self.provider
            # Explicit launch choices override stored model and effort. Unset choices inherit
            # the persona defaults.
            self.model = model or prior.get("model") or None
            self.effort = effort or prior.get("effort") or None
            self.last_message = prior.get("last_message") or None
            self.last_event_at = prior.get("last_event_at") or None
            # An adopted session is adopted for good: where it came from and
            # which file its history is in are facts about the session and
            # outlive any one process.
            self.origin = prior.get("origin") or adoption.ORIGIN_UI
            self.source = prior.get("source") or None
            replayed = prior.get("replayed")
            self.replayed = int(replayed) if replayed else None
            # Missing source measurements require a fresh read.
            size, mtime = prior.get("source_size"), prior.get("source_mtime")
            self.source_size = int(size) if isinstance(size, int) else None
            self.source_mtime = int(mtime) if isinstance(mtime, int) else None
            # Which CLI session this record drives outlives the process that
            # forked it. Without this a broker restart would fork the app's
            # session a second time and the turns taken here would be in a
            # file nothing points at.
            self.cli_session_id = prior.get("cli_session_id") or None
            self.forked_from = prior.get("forked_from") or None
            self.thread_id = prior.get("thread_id") or None
            # Restore activity time so a metadata edit cannot move an old session to the top
            # of the list.
            self.last_activity = parse_iso(prior.get("last_activity"),
                                           self.last_activity)
            # Opening words are provisional until a person or the transcript gives the
            # session a title.
            self._titled = self.title_source in ("vault", "person")
            # No process of ours is behind this record until start() says so,
            # so the session is not running whatever the file says.
            self.state = "exited"
            self._restored_status = recovered_status(
                prior.get("status"), self.turns)

    # -- persistence -------------------------------------------------------
    #
    # Two files per session. The .jsonl is the event log and is what a
    # reconnecting client is replayed from; the .json is the record the
    # listing reads, so listing sessions never has to parse an event log.

    @property
    def log_path(self) -> Path:
        return cfg.SESSION_DIR / f"{self.id}.jsonl"

    @property
    def meta_path(self) -> Path:
        return cfg.SESSION_DIR / f"{self.id}.json"

    def is_live(self) -> bool:
        """Is a process of this broker's behind this session right now.

        Residency and nothing else: whether this session's ~450 MB are in RAM
        at this moment. Read off the process object -- the broker's own
        process table -- and never inferred from ``status``, which is about
        the turn: a ``done`` session with a warm idle process IS live, and a
        record left saying ``working`` by a broker that has since restarted
        is NOT.

        Both halves, and each one catches what the other cannot. A process
        object whose ``returncode`` is set is gone, however recently, and
        ``state`` does not say so until ``_reap`` gets the line -- so a
        session whose CLI has just died is not live. And ``stop()`` sets
        ``state`` before the kill it may have to fall back on, so a session
        the broker has finished with is not live either, whatever its
        ``returncode`` reads for the next few milliseconds. Neither direction
        may claim residency: 450 MB the box cannot use is not 450 MB in use.
        """
        return (self.state == "running" and not self._stop_requested
                and self.proc is not None and self.proc.returncode is None)

    def meta(self) -> dict:
        """Build the session record for a list row or broker.session snapshot. Its
        record_seq is the last journal position. Only an actual event carries seq,
        so receiving a snapshot cannot consume the next event.
        """
        return {
            "session_id": self.id,
            "agent": self.agent,
            "provider": self.provider,
            # Null on both means the persona's own setting, which is what a
            # client draws as the unticked menu. GET /v1/providers says what
            # the other rows are.
            "model": self.model,
            "effort": self.effort,
            "origin": self.origin,
            "source": self.source,
            "replayed": self.replayed,
            "source_size": self.source_size,
            "source_mtime": self.source_mtime,
            # What a first message into this session would re-send to the
            # model, in bytes: the app's own session file, for an adopted
            # record nobody has spoken to here yet. Null for every other kind.
            "history_bytes": history_bytes(self.origin, self.cli_session_id,
                                           self.source_size),
            "cli_session_id": self.cli_session_id,
            "forked_from": self.forked_from,
            "thread_id": self.thread_id,
            "workspace": str(self.workspace),
            "created": self.created,
            "last_activity": datetime.fromtimestamp(
                self.last_activity, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_event_at": self.last_event_at,
            "state": self.state,
            # Whether this session is resident in RAM right now, read off the
            # process table (`is_live`). `state` is the lifecycle word the
            # record was written with and goes stale in a file; this is the
            # answer to "is it holding 450 MB", which is the question the cap
            # is about and the one a page filters on. Never absent and never
            # null: false is the answer for every session no process is
            # behind, which is most of a home screen.
            "live": self.is_live(),
            "status": self.status(),
            "title": self.title,
            # What named it: null for the stand-in opening words, "vault" for
            # the name the transcript writer filed, "person" for a rename.
            "title_source": self.title_source,
            "last_message": self.last_message,
            "pinned": self.pinned,
            "archived": self.archived,
            "exit_code": self.exit_code,
            "record_seq": self.seq,
            # How far this one has been read, against `record_seq`, which is
            # how far it goes. Unread is `seen_seq < record_seq`, and the
            # page does that subtraction rather than being handed a boolean
            # that would be wrong the moment the next event landed.
            "seen_seq": self.seen_seq,
            # How many background tasks are running behind this session right
            # now. An integer and never absent: 0 is the answer for a session
            # that has none, and a page reads one number rather than asking
            # whether a key is missing because there are none or because the
            # record predates the field.
            "background_tasks": len(self.background_tasks),
            "turns": [with_context(turn) for turn in self.turns],
            "pending_permissions": list(self.pending),
        }

    def task_started_at(self, task_id: str) -> str:
        """When the broker first saw a background task (UTC ISO). Set as the
        CLI's list names it (`_set_background_tasks`); a task added another
        way (Codex's root calls) is stamped the first time it is asked for,
        which is when the engine hears its `broker.background_tasks`."""
        seen = getattr(self, "_task_seen", None)
        if seen is None:
            seen = self._task_seen = {}
        return seen.setdefault(task_id, datetime.now(timezone.utc)
                               .strftime("%Y-%m-%dT%H:%M:%SZ"))

    def working_behind(self) -> bool:
        """Count unfinished background work even between a task leaving the provider's list and
        its notification arriving. The notification starts another turn; its result clears
        the wait. Expire stale entries when reading status.
        """
        if self._awaiting:
            cutoff = time.monotonic() - cfg.BACKGROUND_SETTLE_S
            self._awaiting = {task: seen for task, seen
                              in self._awaiting.items() if seen > cutoff}
        return bool(self.background_tasks or self._awaiting or self._resuming)

    def busy(self) -> str | None:
        """Return the reason this resident session cannot be reclaimed, or None when it can."""
        if self.pending:
            return "needs_input"
        if self._turn_active:
            return "working"
        if self.working_behind():
            return "waiting"
        return None

    def status(self) -> str:
        """Give a pending user decision priority over an open turn. Once the turn ends, report
        waiting while its background work remains. Process residency is separate from status:
        a successful exited session can still be done.
        """
        if self.pending:
            return "needs_input"
        if self.state == "running":
            if self._turn_active:
                return "working"
            if self.working_behind():
                return "waiting"
        elif self._restored_status is not None:
            # A record this broker did not start. What it was doing is over.
            return self._restored_status
        elif (self.state == "exited" and not self._stop_requested
                and self._last_result_ok is None):
            # An unexpected exit before a reply fails the turn. An idle process can exit
            # after a successful reply without changing its result.
            return "failed"
        if self._last_result_ok is None:
            return "idle"
        return "done" if self._last_result_ok else "failed"

    def save(self) -> None:
        """Write the record, and tell the live list it changed.

        Every change to a record goes through here, so this is the one place
        ``GET /v1/sessions/events`` has to be fed from: a status, a title, a
        last message, a pin, an archive and a brand-new session are all a
        record being written. Hanging the fan-out off the write rather than
        off each caller is what keeps the live list from silently missing the
        next field somebody adds.
        """
        # Publish in-memory state even if persistence fails, then propagate the write error.
        try:
            self.meta_path.write_text(json.dumps(self.meta(), indent=2),
                                      encoding="utf-8")
        finally:
            if self.on_record_change is not None:
                self.on_record_change(self)

    # -- the process -------------------------------------------------------

    def has_images_tool(self) -> bool:
        """Read the generated tool list that the next provider process will load."""
        try:
            return "images" in json.loads((self.workspace / ".mcp.json").read_text())["mcpServers"]
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def argv(self, resume: bool) -> list[str]:
        """Build the conversation invocation using the persona's own workspace settings. Pass
        model and effort overrides only when the session requests them. Keep the provider's
        prompt-response channel open and stream numbered events from the same process across
        turns.
        """
        # The shared stream flags send permission prompts over stdio so the client can
        # answer.
        argv = [cfg.CLAUDE, *cfg.CLAUDE_STREAM_FLAGS, "--append-system-prompt",
                loam_loading.display_name_line(self.workspace)]
        if self.model:
            argv += ["--model", self.model]
        if self.effort:
            argv += ["--effort", self.effort]
        if self.origin == adoption.ORIGIN_APP:
            if self.cli_session_id is None:
                argv += ["--resume", self.id, "--fork-session"]
            else:
                argv += ["--resume", self.cli_session_id]
        elif resume:
            argv += ["--resume", self.id]
        else:
            argv += ["--session-id", self.id]
        return argv

    async def start(self, resume: bool = False) -> None:
        if not (self.workspace / "agent.json").is_file():
            raise BrokerError(404, f"no agent named {self.agent!r}")
        loam_loading.refresh_reach_import(self.workspace)
        if (self.origin == adoption.ORIGIN_APP
                and self.cli_session_id is None
                and not (self.source and Path(self.source).is_file())):
            # An adopted record with nothing behind it to fork. `argv` would
            # ask the CLI to resume a conversation it cannot find, and the
            # process would start and die -- which reads on the page as a
            # session that failed a turn rather than one that was never
            # there. Asked only before the fork: afterwards this session runs
            # on a file of the CLI's own and the app's is history it already
            # holds.
            raise BrokerError(
                409,
                f"session {self.id} was adopted from the Claude Code app and "
                f"the session file it was read out of is gone, so there is "
                f"nothing to fork from. Start a new session with the same "
                f"agent instead.")
        # The shared skills, current for this start (first release L7): the
        # Claude shelf above the workspaces and this persona's view. Never
        # fails a start (lib/loam_skills.py).
        await asyncio.to_thread(loam_skills.quiet_refresh, self.workspace)
        env = dict(os.environ)
        env["DISABLE_AUTOUPDATER"] = "1"
        env["CLAUDE_PROJECT_DIR"] = str(self.workspace)
        # Use the provider’s automatic compaction policy. Leave LOAM_WAKE unset so capture
        # treats this as a conversation.
        env.pop("LOAM_WAKE", None)
        # Tools use the broker session ID to associate their work with the initiating thread.
        env[loam_waiters.SESSION_ENV] = self.id
        # Which surface this conversation is being held on, for the transcript
        # the writer files. Nothing in the session file says: a broker session
        # is the same CLI in the same workspace under the same hooks as a
        # conversation the owner has in the Claude Code app, which is why the
        # first 157 transcripts all read `surface: claude-code` and a Loam
        # conversation could not be told from a Remote Control one. This is
        # the only party that knows, so this is where it is said.
        env[loam_transcript.SURFACE_ENV_VAR] = loam_transcript.SURFACE_LOAM_UI
        if self.origin == adoption.ORIGIN_APP:
            # A conversation that began in the app and is now being held here.
            # The surface is where it is happening; the origin is where it
            # came from, and only this kind has one to state.
            env[loam_transcript.ORIGIN_ENV_VAR] = adoption.ORIGIN_APP
        else:
            env.pop(loam_transcript.ORIGIN_ENV_VAR, None)
        self.resumed = resume
        # A process of ours exists again, so the status recovered from the
        # file is no longer what this session is; it is what it was.
        self._restored_status = None
        self._stop_requested = False
        self._stop_reason = None
        self._stopping_event = None
        self._turn_active = False
        # A new process has no children yet, whatever the last one left behind.
        self._clear_background()
        # A process that has just started is active. Without this a session
        # resumed after a day carries a day-old last_activity into a reaper
        # that ends anything idle for an hour, and it would be killed inside
        # the minute.
        self.last_activity = time.time()
        # This is the process boundary, and it is the only thing that knows
        # where the CLI's running cost total starts over. See _cost_delta.
        self._process_cost = None
        self.images_tool = self.has_images_tool()
        self.proc = await asyncio.create_subprocess_exec(
            *first_to_go(self.argv(resume)),
            cwd=str(self.workspace),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            # The CLI writes one JSON object per line and some of those lines
            # are a whole tool result. asyncio's default 64 KiB stream limit
            # raises on the first big one and there is no way to recover the
            # line, so the limit is set where it can be set at all.
            limit=16 * 1024 * 1024,
        )
        self.state = "running"
        self.save()
        self.stdout_task = asyncio.create_task(self._pump_stdout())
        asyncio.create_task(self._pump_stderr())
        # The SDK handshake. The CLI answers with its commands and output
        # styles; what matters is that the channel is open in both directions
        # before the first user message arrives.
        await self.send_raw({"type": "control_request",
                             "request_id": f"init-{self.id}",
                             "request": {"subtype": "initialize", "hooks": None}})

    async def send_raw(self, obj: dict) -> None:
        if self.proc is None or self.proc.stdin is None or self.state != "running":
            raise BrokerError(409, f"session {self.id} is not running")
        async with self._write_lock:
            self.proc.stdin.write((json.dumps(obj) + "\n").encode())
            await self.proc.stdin.drain()

    async def dispatch(self, dispatch_id: str, text: str, attachments=None,
                       *, steer: bool = False) -> int | None:
        """Repeat callers join the same write, even if its first caller left."""
        task = self._dispatches.get(dispatch_id)
        if task is None:
            send = self.steer if steer else self.send_message
            task = asyncio.create_task(send(text, attachments, dispatch_id=dispatch_id))
            self._dispatches[dispatch_id] = task
        return await asyncio.shield(task)

    def _not_running_error(self) -> BrokerError:
        if self._stop_reason in RECLAIM_REASONS:
            return BrokerError(
                409, f"session {self.id} was unloaded ({self._stop_reason})",
                {"reclaimed": self._stop_reason})
        return BrokerError(409, f"session {self.id} is not running")

    async def send_message(self, text: str,
                           attachments: list[dict] | None = None, *,
                           dispatch_id: str | None = None) -> int:
        """Validate that the session can receive input, then journal the user's words before
        sending them so replies follow their question during replay. Return the sequence of
        that specific event; another event may have advanced the session cursor. Name
        attachments by path and include image blocks for the provider.
        """
        if (self.proc is None or self.proc.stdin is None
                or self.state != "running" or self._stop_requested):
            raise self._not_running_error()
        attachments = attachments or []
        event = {"type": "broker.user_message",
                 "session_id": self.id,
                 "text": text}
        if dispatch_id:
            event["dispatch_id"] = dispatch_id
        if attachments:
            event["attachments"] = attachments
        await self.emit(event)
        await self.send_raw({"type": "user",
                             "message": {"role": "user",
                                         "content": self._content(
                                             self._timed(text), attachments)}})
        self.last_activity = time.time()
        # The turn is in flight from here until its result comes back, which
        # is what makes the row say `working` the instant the message lands
        # rather than when the model gets round to answering.
        self._turn_active = True
        if self.title is None:
            self.title = first_words(
                text or (f"Attached {attachments[0]['name']}"
                         if attachments else ""))
        self.save()
        return event["seq"]

    # Whether this provider accepts steering during a turn.
    STEERS = True

    async def steer(self, text: str,
                    attachments: list[dict] | None = None, *,
                    dispatch_id: str | None = None) -> int | None:
        """Send input to the current turn at its next safe boundary. Return the journal sequence
        of accepted input or None if the session cannot steer.
        """
        if (not self._turn_active or self.proc is None or self.proc.stdin is None
                or self.state != "running" or self._stop_requested):
            return None
        attachments = attachments or []
        event = {"type": "broker.user_message", "session_id": self.id,
                 "text": text, "steered": True}
        if dispatch_id:
            event["dispatch_id"] = dispatch_id
        if attachments:
            event["attachments"] = attachments
        await self.emit(event)
        await self.send_raw({"type": "user",
                             "message": {"role": "user",
                                         "content": self._content(
                                             self._timed(text), attachments)}})
        self.last_activity = time.time()
        return event["seq"]

    def _content(self, text: str, attachments: list[dict]) -> str | list[dict]:
        """Send plain text when there are no attachments. Otherwise include their paths and any
        supported image content in the provider's message blocks.
        """
        if not attachments:
            return text
        lines = [text] if text else []
        lines.append("Attached to this message"
                     " (files on disk, readable with Read):")
        blocks: list[dict] = []
        for item in attachments:
            note = f"- {item['path']} ({item['type']}, {human_size(item['size'])})"
            inline = (item["type"] in cfg.INLINE_IMAGE_TYPES
                      and item["size"] <= cfg.INLINE_IMAGE_MAX_BYTES)
            if inline:
                note += " -- the image itself is attached below"
                blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": item["type"],
                               "data": base64.b64encode(
                                   Path(item["path"]).read_bytes()).decode()},
                })
            elif item["type"] in cfg.INLINE_IMAGE_TYPES:
                note += (f" -- too big to show inline "
                         f"(over {human_size(cfg.INLINE_IMAGE_MAX_BYTES)})")
            lines.append(note)
        return [{"type": "text", "text": "\n".join(lines)}] + blocks

    async def set_choices(self, changes: dict) -> list[str]:
        """Persist model and effort overrides and send supported changes to a running CLI as
        control commands. Keep these commands out of the user's message history. Clearing a
        launch override restores the default on a later launch.
        """
        sent = []
        for field, command in (("model", "/model"), ("effort", "/effort")):
            if field not in changes:
                continue
            value = changes[field]
            setattr(self, field, value)
            if value and self.state == "running" and not self._stop_requested:
                await self.send_raw({
                    "type": "user",
                    "message": {"role": "user",
                                "content": f"{command} {value}"}})
                sent.append(f"{command} {value}")
        # Model changes leave activity time alone so the session keeps its position in the
        # list.
        self.save()
        return sent

    async def answer_permission(self, request_id: str, behavior: str,
                                message: str | None = None,
                                updated_input: dict | None = None,
                                answers: dict | None = None,
                                updated_permissions: list | None = None,
                                resolved_as: str | None = None) -> None:
        """Answer a pending request. ``resolved_as`` is the broker's own
        name for an answer nobody gave (``expired``), which goes to the CLI
        as the deny it is and onto the stream under that name.

        ``updated_permissions`` is the CLI's own ``PermissionUpdate`` list, as
        it proposed it in ``permission_suggestions``, put on the
        ``control_response`` unaltered beside ``updatedInput``. Never made up
        here; the route has already refused any destination but ``session``."""
        req = self.pending.get(request_id)
        if req is None:
            raise BrokerError(404, f"no permission request {request_id} is pending")
        asked = _questions_of(req)
        scope = None
        if behavior == "allow" and asked is not None:
            # A question is answered with its answers, keyed by question
            # text as the CLI's own prompt keys them. Checked before the
            # request leaves `pending`, so a 400 leaves the card answerable.
            try:
                checked = questions_fmt.check(asked, answers)
            except questions_fmt.AnswerError as exc:
                raise BrokerError(400, str(exc)) from None
            response = {"behavior": "allow",
                        "updatedInput": questions_fmt.to_claude(
                            req.get("input") or {}, asked, checked)}
        elif behavior == "allow":
            checked = None
            response = {"behavior": "allow",
                        "updatedInput": updated_input
                        if updated_input is not None else req.get("input", {})}
            if updated_permissions:
                response["updatedPermissions"] = updated_permissions
                scope = "session"
        else:
            checked = None
            # A declined question is told to the model in the CLI's words,
            # not as a refused permission.
            response = {"behavior": "deny",
                        "message": message or (questions_fmt.CLAUDE_DECLINED
                                               if asked is not None
                                               else "Denied from the client.")}
        self.pending.pop(request_id, None)
        await self.send_raw({"type": "control_response",
                             "response": {"subtype": "success",
                                          "request_id": request_id,
                                          "response": response}})
        self.last_activity = time.time()
        # The answer is on the wire, so every client watching this session is
        # told the card is gone -- including the one that did not answer it.
        # Without this, a second device holds a card for a request that no
        # longer exists until the next broker.session event happens to
        # reconcile it, and there is no reason a second device should have to
        # guess. Emitted AFTER the control_response, so it says what happened
        # rather than what is about to; the CLI's own resumed events may
        # interleave, and a client orders by seq anyway.
        await self.emit(_resolved_event(self.id, request_id,
                                        resolved_as or behavior, req,
                                        message, checked, scope))
        self.save()

    async def expire_permissions(self, now: float) -> list[str]:
        """Deny every request that has waited ``PERMISSION_WAIT_S``, with a
        sentence that tells the model what happened and what to do. The
        process lives and the turn finishes on its own; this is the reaper's
        answer to a session that is asking, where ending it is the answer to
        one that is idle. Returns the ids it expired."""
        expired = []
        for request_id, req in list(self.pending.items()):
            asked_at = req.get("raised_at", self.last_activity)
            if now - asked_at < cfg.PERMISSION_WAIT_S:
                continue
            await self.answer_permission(
                request_id, "deny", cfg.PERMISSION_EXPIRED_MESSAGE,
                resolved_as="expired")
            expired.append(request_id)
        return expired

    async def close_pending(self, behavior: str) -> None:
        """Close every request still pending with ``behavior`` on the stream
        and nothing sent to the provider: the process that asked is gone or
        going. Taken off ``pending`` before anything is emitted, so a second
        caller -- `stop` and `_reap` both get here when a process is ended --
        finds nothing and writes nothing twice."""
        closing = list(self.pending.items())
        self.pending.clear()
        for request_id, req in closing:
            await self.emit(_resolved_event(self.id, request_id, behavior,
                                            req, None, None))
        if closing:
            self.save()

    # -- the stream --------------------------------------------------------

    async def _raise_request(self, req: dict, event: dict) -> None:
        """Retain the emitted request on the pending entry so the same request can be replayed
        to a reconnecting client.
        """
        req["event"] = event
        await self.emit(event)

    def _timed(self, text: str) -> str:
        """What the harness is sent for a person's text: the clock first,
        unless this session's prompts carry their own. The log keeps the
        text as typed."""
        if not self.tell_time or (text or "").lstrip().startswith("/"):
            # A slash command typed into a chat is the harness's to read
            # first; a line in front of it would make it prose.
            return text
        return loam_clock.stamp(text)

    def pending_events(self) -> list[dict]:
        """Every request still open, as the ``broker.permission_request``
        that raised it, oldest first."""
        return [dict(req["event"]) for req in self.pending.values()
                if isinstance(req.get("event"), dict)]

    async def emit(self, event: dict) -> None:
        """Number it, write it down, hand it to whoever is listening.

        Written to disk BEFORE it is fanned out, because the disk copy is what
        a client that was not connected gets replayed from. A client that saw
        an event the log does not have would be a client that cannot recover
        by reconnecting, which is the one property this whole file is for.

        **One conversation wears one id on the wire.** A forked session's
        process states its own id on every envelope it writes, and that id is
        not this record's: a client keying events by ``session_id`` would read
        the history it was replayed and the turn it just took as two
        different threads. So an event naming the CLI's fork is rewritten to
        name the record. Nothing is hidden by it -- the fork's id is on the
        record as ``cli_session_id``, and each turn record keeps the id the
        process reported for the turn that ran.
        """
        if (self.cli_session_id is not None
                and event.get("session_id") == self.cli_session_id):
            event["session_id"] = self.id
        self.seq += 1
        event["seq"] = self.seq
        # When this session last showed any sign of life. `last_activity` is
        # not the same thing and never was: it is bumped by a client answering
        # a permission and by the reaper's arithmetic, so a page asking "it
        # says working -- when did it last actually do something?" has to ask
        # this one. A working session whose last event was twenty minutes ago
        # is a different thing from one that is mid-sentence, and a spinner
        # cannot say which.
        self.last_event_at = now_iso()
        try:
            self._append(event)
        except OSError as exc:
            self._not_saved(event, exc)
            if event.get("type") == "broker.user_message" and event.get("dispatch_id"):
                raise
        for queue in list(self.subscribers):
            queue.put_nowait(event)

    def _append(self, event: dict) -> None:
        """Append one JSON event per line. After startup or a failed write, check the final
        byte: a partial line must never consume the next valid event.
        """
        data = (json.dumps(event) + "\n").encode("utf-8")
        with self.log_path.open("ab") as handle:
            if not self._log_ends_clean and handle.tell() > 0:
                with self.log_path.open("rb") as tail:
                    tail.seek(-1, os.SEEK_END)
                    if tail.read(1) != b"\n":
                        data = b"\n" + data
            self._log_ends_clean = False
            handle.write(data)
            handle.flush()
        self._log_ends_clean = True

    def _not_saved(self, event: dict, exc: OSError) -> None:
        """Keep reading provider output after a journal failure. Notify live subscribers once
        per turn with the first missed sequence, and mark the turn failed when its result
        arrives. Later replay can recover only what reached disk.
        """
        reason = _not_saved_reason(exc)
        print(f"log: {self.id[:8]}: seq {event.get('seq')} not written ({exc})",
              file=sys.stderr, flush=True)
        if self._unsaved is None:
            self._unsaved = reason
            notice = {"type": "broker.write_failed", "session_id": self.id,
                      "reason": reason, "from_seq": event.get("seq")}
            for queue in list(self.subscribers):
                queue.put_nowait(notice)
        self._log_ends_clean = False

    def snapshot(self) -> dict:
        """The record as a stream item, and the only item here that is not an
        event: no number, no line in the log, nothing to dedupe against.

        A client applies it every time it arrives and keeps the latest.
        """
        return {"type": "broker.session", **self.meta()}

    def broadcast_snapshot(self) -> None:
        """Send subscribers the current session record without logging a new event. Its
        record_seq is a position; reusing it as a new event's seq would hide a real journal
        entry.
        """
        event = self.snapshot()
        for queue in list(self.subscribers):
            queue.put_nowait(event)

    async def _pump_stdout(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                await self.emit({"type": "broker.exit",
                                 "error": "unparsable line from the CLI",
                                 "raw": text[:2000]})
                continue
            try:
                await self._handle(obj)
            except OSError as exc:
                # Keep draining the provider even if a journal or record write fails.
                print(f"pump: {self.id[:8]}: {exc}", file=sys.stderr,
                      flush=True)
                if self._unsaved is None:
                    self._unsaved = _not_saved_reason(exc)
        await self._reap()

    async def _handle(self, obj: dict) -> None:
        if local_command_output(obj):
            # Preserve the CLI diagnostic and its journal position. It can arrive
            # after dispatch, while an unrelated model reply is still in flight.
            await self.emit({**obj, "type": "broker.local_command"})
            return
        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "init":
            model = obj.get("model")
            if isinstance(model, str) and model:
                self._init_model = model
            # **Where a fork says what it is called.** The CLI mints the
            # forked session's id and states it here, in the first thing it
            # says; nothing else on the stream is authoritative about it and
            # the launch line never asked for a particular one (see `argv`).
            #
            # Only an adopted session can reach the body of this: every other
            # session was started under an id chosen here, and an init
            # message naming a different one would be a surprise to report
            # rather than a fact to record.
            said = obj.get("session_id")
            if (self.origin == adoption.ORIGIN_APP and isinstance(said, str)
                    and said and said != self.id
                    and said != self.cli_session_id):
                self.cli_session_id = said
                self.forked_from = self.id
                # The system event is saved after it reaches the stream.
            # Stream init opens every turn, including a turn the provider starts to deliver a
            # task notification.
            self._turn_active = True
        # The background tasks behind this turn, and the two events that end
        # one. See `working_behind` for what each means to a row on a screen.
        background_changed = False
        if kind == "system" and obj.get("subtype") == "background_tasks_changed":
            background_changed = self._set_background_tasks(obj.get("tasks"))
        if kind == "system" and obj.get("subtype") == "task_notification":
            # A task has reported, and the CLI's own note on the notification
            # says what it is for: it fires each time a background agent stops,
            # and the persona is given it as a turn of its own. So the session
            # is working from here until that turn's result.
            self._awaiting.pop(obj.get("task_id") or "", None)
            if obj.get("task_id"):
                self._notified[obj["task_id"]] = time.monotonic()
            self._resuming = True
        if kind == "control_request":
            request = obj.get("request") or {}
            if request.get("subtype") == "can_use_tool":
                request_id = obj["request_id"]
                # AskUserQuestion reaches this channel as a permission
                # prompt for that tool; it is a question, and the card that
                # answers it needs the questions, not Allow/Deny on the JSON.
                if request.get("tool_name") == questions_fmt.CLAUDE_TOOL:
                    asked = questions_fmt.from_claude(request.get("input") or {})
                    if asked is not None:
                        request = {**request, "questions": asked}
                # When it was asked, which is what PERMISSION_WAIT_S counts
                # from. On the broker's copy only; never on the event.
                request = {**request, "raised_at": time.time()}
                self.pending[request_id] = request
                self.last_activity = time.time()
                await self._raise_request(request, _request_event({
                    "type": "broker.permission_request",
                    "session_id": self.id,
                    "request_id": request_id,
                    "tool_name": request.get("tool_name"),
                    "display_name": request.get("display_name"),
                    "input": request.get("input"),
                    "description": request.get("description"),
                    "decision_reason": request.get("decision_reason"),
                    "decision_reason_type": request.get("decision_reason_type"),
                    "permission_suggestions": request.get("permission_suggestions"),
                    "tool_use_id": request.get("tool_use_id"),
                }, request))
                self.save()
            return
        if kind == "control_cancel_request":
            # Close a withdrawn prompt on the stream so the client cannot answer it later.
            request_id = obj.get("request_id", "")
            req = self.pending.pop(request_id, None)
            if req is not None:
                await self.emit(_resolved_event(self.id, request_id,
                                                "cancelled", req, None, None))
            self.save()
        if kind == "control_response":
            return  # the initialize handshake's answer; not the client's business
        if kind == "rate_limit_event" and self.provider == cfg.DEFAULT_PROVIDER:
            # The provider's own reading of the quota, which arrives here and
            # nowhere else: no route asks for it, the CLI volunteers it
            # mid-turn. Kept on disk so a restart -- or a day when nobody
            # takes a turn -- does not blank the providers page.
            try:
                cfg.keep_usage_event(obj, self.id)
            except OSError as exc:
                print(f"usage: could not record ({exc})", file=sys.stderr,
                      flush=True)
        if kind == "assistant":
            # The only place the model is stated. Read back, not assumed --
            # and only from a TOP-LEVEL message: one with a parent_tool_use_id
            # came from a subagent, which the persona's own settings may run
            # on a different model entirely, and the turn's model is the
            # turn's own.
            if obj.get("parent_tool_use_id") is None:
                model = (obj.get("message") or {}).get("model")
                if isinstance(model, str) and model:
                    self._turn_model = model
                # Each top-level assistant message reports an individual request. Use the
                # latest reading instead of summing a multi-round turn.
                usage = (obj.get("message") or {}).get("usage")
                if isinstance(usage, dict):
                    self._turn_usage = usage
                # What the status box on the home screen shows. Top-level for
                # the same reason: a subagent's sentence is machinery, and
                # what the person is owed is what the persona said to them.
                said = loam_transcript._text_of(
                    (obj.get("message") or {}).get("content"))
                if said:
                    self.last_message = said[:cfg.LAST_MESSAGE_CHARS]
                    self.save()
        if kind == "result":
            self._turn_active = False
            # A notification's turn is this turn, and it is over. Whatever the
            # persona was resuming for has been answered; if another task
            # reports there will be another notification.
            self._resuming = False
            # An envelope says how it went in two places and they disagree on
            # different days: `is_error` is the CLI's own flag and `subtype`
            # is "success" or one of the error subtypes. Either saying so is
            # enough to call the turn failed.
            self._last_result_ok = not (
                bool(obj.get("is_error"))
                or (obj.get("subtype") or "success") != "success")
            # Record the provider's latest context reading on the turn and on its closing event.
            if obj.get("provider") == "codex" and obj.get("usage"):
                self._turn_usage = obj["usage"]
            context = {"used": context_used(self._turn_usage),
                       "window": context_window(obj, self._turn_model)}
            if self._turn_usage is None and self._compacted_to is not None:
                # Compaction has no assistant usage reading. Use its boundary so the record
                # reflects the smaller context.
                context = {"used": self._compacted_to,
                           "window": context_window(obj, self._init_model)}
            obj["context"] = dict(context)
            # Point 5 of the brief: what the envelope reports, per turn, kept
            # where the listing can read it without parsing the event log.
            self.turns.append({
                "ts": now_iso(),
                "session_id": obj.get("session_id"),
                "subtype": obj.get("subtype"),
                "model": self._turn_model,
                "duration_ms": obj.get("duration_ms"),
                "num_turns": obj.get("num_turns"),
                "total_cost_usd": obj.get("total_cost_usd"),
                "process_start": self._process_cost is None,
                "cost_delta_usd": self._cost_delta(obj.get("total_cost_usd")),
                "context": context,
                "usage": obj.get("usage"),
                "permission_denials": obj.get("permission_denials"),
            })
            # The next turn's model and the next turn's context are the next
            # turn's to state. A turn whose top-level messages named neither
            # records null rather than inheriting the last turn's, because
            # "this turn ran on fable" is a claim about this turn.
            self._turn_model = None
            self._turn_usage = None
            self._compacted_to = None
        self.last_activity = time.time()
        if kind == "result":
            obj["working_behind"] = self.working_behind()
        await self.emit(obj)
        if kind == "result" and self._unsaved is not None:
            # A missing journal event makes the turn incomplete even when the provider
            # reports success.
            self._last_result_ok = False
            self.turns[-1]["not_saved"] = self._unsaved
            self._unsaved = None
        if kind == "system" and obj.get("subtype") == "compact_boundary":
            # After the CLI's own marker, so the log holds both. See
            # compaction_event for the shape and where it came from.
            entry = compaction_event(self.id, obj)
            self._compacted_to = entry["tokens_after"]
            await self.emit(entry)
        if background_changed:
            # After the CLI's own event, so a client reads what changed and
            # then the count the broker keeps of it.
            await self.emit(self.background_event())
        try:
            if kind in ("result", "system"):
                self.save()
        finally:
            if kind == "result":
                # After the result event, so a client reads the turn's end and
                # then the record that prices it. See broadcast_snapshot. Sent
                # when the save failed too: the record is what says why.
                self.broadcast_snapshot()
        if kind == "result":
            if not self._titled and not self._titling:
                self._titling = True
                asyncio.create_task(self._title())

    # Track provider background tasks that can outlive the turn that launched them.

    def _set_background_tasks(self, tasks) -> bool:
        """The CLI's live list of background tasks, taken as stated. True if
        the count changed.

        ``system``/``background_tasks_changed`` carries the WHOLE list every
        time -- `[]` when the last one ends -- so it is copied rather than
        added to and subtracted from: a broker keeping its own tally would
        drift from the CLI's the first time an event was missed, and the CLI is
        the only party that knows.

        A task that has left the list goes into `_awaiting` until its
        notification arrives, because the list empties one event BEFORE the
        notification fires (seq 10155 then 10157 of session 940380fd) and a
        status computed in between would say `done` and take it back inside the
        same millisecond.
        """
        if not isinstance(tasks, list):
            return False
        live = [task for task in tasks if isinstance(task, dict)]
        was = {task.get("task_id") for task in self.background_tasks}
        now = {task.get("task_id") for task in live}
        cutoff = time.monotonic() - cfg.BACKGROUND_SETTLE_S
        self._notified = {t: at for t, at in self._notified.items() if at > cutoff}
        for task_id in was - now:
            if task_id and task_id not in self._notified:
                self._awaiting[task_id] = time.monotonic()
        for task_id in now - was:
            if task_id:
                self.task_started_at(task_id)
        self._task_seen = {t: at for t, at in getattr(self, "_task_seen", {}).items()
                           if t in now}
        before, self.background_tasks = len(self.background_tasks), live
        if self._awaiting or self._resuming:
            self._arm_settle()
        return before != len(live)

    def background_event(self) -> dict:
        """What a client is told when the count changes.

        The count is the field the record carries and the one a page draws. The
        tasks are beside it because the CLI names them -- a description a
        person wrote in the brief that launched them -- and a client that wants
        to say *which* subagent is running should not have to parse the CLI's
        own event to find out. A client that only wants the badge reads
        `count` and ignores the rest.
        """
        return {"type": "broker.background_tasks",
                "session_id": self.id,
                "count": len(self.background_tasks),
                "tasks": [{"task_id": task.get("task_id"),
                           "task_type": task.get("task_type"),
                           "description": task.get("description")}
                          for task in self.background_tasks]}

    def _arm_settle(self) -> None:
        """The backstop under `_awaiting` and `_resuming`.

        Both are held open by an event that has always arrived in the same
        breath, and neither may hold a row on `waiting` for a session that is
        in fact finished -- which is the defect this whole point is about, with
        its sign flipped. So a timer: when it fires, whatever is still
        outstanding is dropped and the record is written if that changed what
        the session is. Re-armed on every change, cancelled when the process
        ends, and never a reason anything fails -- a Session built outside a
        running loop (a test, a probe) simply has no timer.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._settle is not None:
            self._settle.cancel()
        self._settle = loop.call_later(cfg.BACKGROUND_SETTLE_S, self._settled)

    def _settled(self) -> None:
        self._settle = None
        if not (self._awaiting or self._resuming):
            return
        self._awaiting.clear()
        self._resuming = False
        # Persist the timeout transition even if checking pending work has already updated
        # the in-memory state.
        self.save()

    def _clear_background(self) -> None:
        """A process is gone, so its children are. Nothing outlives it: a
        background task is a child of the CLI process that launched it, and a
        record left saying two would draw a badge for two subagents that
        stopped when the process did."""
        if self._settle is not None:
            self._settle.cancel()
            self._settle = None
        self.background_tasks = []
        self._awaiting.clear()
        self._resuming = False

    async def _title(self) -> None:
        """Adopt the transcript writer's title when available. Preserve an explicit human title."""
        try:
            for wait in cfg.TITLE_POLL_WAITS:
                await asyncio.sleep(wait)
                if self._titled:        # a person renamed it while we waited
                    return
                # Look up the transcript under the fork or provider thread ID when it differs
                # from the record ID.
                title = await asyncio.to_thread(
                    adoption.transcript_title,
                    self.cli_session_id or self.thread_id or self.id)
                if title:
                    self.title = title
                    self.title_source = "vault"
                    self._titled = True
                    self.save()
                    return
        except Exception as exc:  # noqa: BLE001 - a title must never cost a session
            print(f"title: {self.agent}/{self.id[:8]}: {exc!r}",
                  file=sys.stderr, flush=True)
        finally:
            self._titling = False

    def _cost_delta(self, total: float | None) -> float | None:
        """Convert the provider's cumulative process cost to a per-turn delta. On the first
        result after restart, use the largest compatible saved process total as the baseline.
        A new process may start at zero or resume an earlier total.
        """
        if not isinstance(total, (int, float)) or isinstance(total, bool):
            return None
        previous, self._process_cost = self._process_cost, float(total)
        if previous is None:
            base = max((b for b in self._cost_bases() if b <= total), default=0.0)
            return round(float(total) - base, 10)
        return round(float(total) - previous, 10)

    _process_cost: float | None = None   # the previous turn's total, THIS process

    def _cost_bases(self) -> set[float]:
        """Return zero and the final total from each earlier process. Older records mark a
        process start implicitly when that turn's delta equals its cumulative total.
        """
        def total(t: dict) -> float | None:
            v = t.get("total_cost_usd")
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

        def starts(t: dict) -> bool:
            if "process_start" in t:
                return bool(t["process_start"])
            return t.get("cost_delta_usd") is not None and t.get("cost_delta_usd") == t.get("total_cost_usd")

        priced = [t for t in self.turns if total(t) is not None]
        bases = {0.0}
        bases.update(total(a) for a, b in zip(priced, priced[1:]) if starts(b))
        if priced:
            bases.add(total(priced[-1]))   # the process before this one
        return bases

    # Attribute a turn to the model reported for its top-level reply. Settings and per-model
    # usage can include models that did not answer.
    _turn_model: str | None = None

    async def _pump_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                print(f"[{self.agent}/{self.id[:8]}] {text}", file=sys.stderr,
                      flush=True)

    async def _reap(self) -> None:
        assert self.proc is not None
        pid = getattr(self.proc, "pid", None)
        self.exit_code = await self.proc.wait()
        self.state = "exited"
        if not self._stop_requested and self.on_unexpected_exit is not None:
            try:
                self.on_unexpected_exit(self, self.exit_code, pid)
            except Exception:  # noqa: BLE001
                pass
        # A turn cut off here is failed below on its own account; what it
        # could not write down is not carried into the next process.
        self._unsaved = None
        # Close pending requests before publishing exit so replay shows their final state.
        await self.close_pending("ended")
        had_tasks = bool(self.background_tasks)
        self._clear_background()
        if had_tasks:
            # Said rather than left to be inferred from broker.exit: a client
            # drawing a badge from the count is told the count is zero by the
            # same event that has always told it, and not by a different one.
            await self.emit(self.background_event())
        if self._turn_active:
            # A turn that never produced a result did not finish, even if
            # somebody asked the process to end. The person sent a message
            # and got no answer, and `done` would be a lie about that.
            self._turn_active = False
            self._last_result_ok = False
        exit_event = {"type": "broker.exit", "session_id": self.id,
                      "exit_code": self.exit_code, "ts": now_iso()}
        if self._stop_reason:
            exit_event["reason"] = self._stop_reason
        await self.emit(exit_event)
        self.save()
        for queue in list(self.subscribers):
            queue.put_nowait(None)

    async def stop(self, reason: str | None = None) -> None:
        """End the process, keep the session. The id resumes: the CLI's own
        session file under ~/.claude holds the context, which is the whole
        reason `--session-id` is chosen up front."""
        if self._stopping_event is not None:
            if reason and not self._stop_reason:
                self._stop_reason = reason
            await self._stopping_event.wait()
            return
        # Set before the signal, because _reap runs as soon as the pipe
        # closes and asks this question: a process that ended because
        # somebody asked it to has not failed.
        self._stop_requested = True
        self._stop_reason = reason
        self._stopping_event = asyncio.Event()
        try:
            if self.proc is not None and self.proc.returncode is None:
                self.proc.terminate()
                try:
                    async with asyncio.timeout(10):   # not wait_for: gh-86296
                        await self.proc.wait()
                except TimeoutError:
                    self.proc.kill()
            # Shutdown may finish before the pump reads EOF. Journal request closures here as
            # well.
            await self.close_pending("ended")
            self.state = "exited"
            # Whatever it was running behind the turn went with it. `_reap` says
            # the same thing on the stream when the pipe closes; this is what the
            # record says, and the two must not disagree for even one write.
            self._clear_background()
            self.save()
        finally:
            self._stopping_event.set()

    # Refresh adopted records from the provider file on reads, while watching an open thread,
    # and when scanning for new sessions.

    def followable(self) -> bool:
        """Is this record's history a file somebody else may still be writing.

        Four tests, and each one of them is a way this would do harm:

        - **adopted, with a file behind it.** A session the broker started has
          its history in its own log and there is nothing to follow.
        - **never forked** (`cli_session_id`). The fork read that conversation
          and went on in a file of its own; what the owner says in the app on
          that session after it is a branch this record does not follow, and
          the README says so. Following both would interleave two futures of
          one conversation.
        - **no log of this broker's own**, which is the same question
          `Broker._settled` asks and the same answer: the numbering above
          `replayed` belongs to events a client has already been handed, and
          re-counting the file under them would renumber history.
        - **no process of ours running it.** A fork's first event and a
          translated entry would both be numbered from `replayed + 1`, and one
          of the two would wear a number the other already had.
        """
        if self.origin != adoption.ORIGIN_APP or not self.source:
            return False
        if self.cli_session_id or self.proc is not None or self.state == "running":
            return False
        try:
            return not self.log_path.stat().st_size
        except OSError:
            return True

    def refresh_from_source(self) -> int:
        """Return the number of new events read from the adopted source. A timestamp-only touch
        updates the stored mtime. When size changes, refresh the record and send newly
        numbered events to listeners. Keep the external file as the sole source for this
        prefix so replay cannot double-count it.
        """
        if not self.followable():
            return 0
        path = Path(self.source)
        size, mtime = adoption.source_stat(path)
        if size is None:
            # The file is gone -- a login cleared out, a session deleted in the
            # app. The record and everything already handed out still stand.
            return 0
        if (size, mtime) == (self.source_size, self.source_mtime):
            return 0
        if size == self.source_size:
            self.source_mtime = mtime
            self.save()
            return 0
        already = self.replayed or 0
        summary = adoption.summarise(path, self.id, self.agent,
                                     after=already if self.subscribers else None)
        self.replayed = summary["events"]
        self.seq = summary["events"]
        self.last_event_at = summary["last_event_at"] or self.last_event_at
        self.last_activity = parse_iso(summary["last_event_at"],
                                       self.last_activity)
        if summary["last_message"]:
            self.last_message = summary["last_message"][:cfg.LAST_MESSAGE_CHARS]
        # A title somebody chose stays. The stand-in does not: the transcript
        # writer may have filed this conversation since the record was made,
        # and the opening words move if the file the record was read from was
        # replaced. So the stand-in is re-derived and anything that is not the
        # stand-in is left exactly as it is -- which is the same rule the scan
        # follows, said in terms of what can be recomputed.
        stand_in = first_words(summary["opening"] or "") or None
        if self.title_source != "person" and (not self.title
                                              or self.title == stand_in):
            filed = adoption.transcript_title(self.id)
            self.title = filed or stand_in
            self.title_source = "vault" if filed else None
        self.source_size, self.source_mtime = size, mtime
        self.save()
        fresh = summary["appended"]
        for number, event in enumerate(fresh, start=already + 1):
            event["seq"] = number
            self.fan_out(event)
        if fresh:
            # The record again, after the events rather than before them: it
            # now says `record_seq` for the last event that has just gone out,
            # and a snapshot promising events a client has not been handed is
            # a client that thinks it is behind.
            self.broadcast_snapshot()
        return len(fresh)

    def fan_out(self, event: dict) -> None:
        """Hand an event to this session's listeners without logging it.

        For events that are not this broker's to log: an adopted session's
        conversation lives in the app's own file and the file stays the only
        copy of it. `emit` is the other door and it writes first.
        """
        for queue in list(self.subscribers):
            queue.put_nowait(event)

    def watch(self, queue: asyncio.Queue) -> None:
        """Register a listener for this session's live events."""
        self.subscribers.add(queue)

    def unwatch(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def replay(self, since: int) -> list[dict]:
        return list(self.iter_replay(since))

    def iter_replay(self, since: int):
        """Replay events above since, with zero starting at the beginning. An adopted session
        has one sequence space across the provider file and the broker journal. Cap the
        translated prefix at replayed so later writes to the source cannot reuse numbers
        already assigned in the local journal.
        """
        if self.origin == adoption.ORIGIN_APP and self.source:
            limit = self.replayed
            seq = 0
            try:
                for event in adoption.iter_events(Path(self.source), self.id):
                    seq += 1
                    if limit is not None and seq > limit:
                        break
                    event["seq"] = seq
                    if seq > since:
                        yield event
            except OSError as exc:
                # The file moved or the login was cleared out. The record and
                # the broker's own log are still readable, and a thread that
                # opens short is better than a chat that will not open.
                print(f"replay: {self.id[:8]}: {self.source}: {exc}",
                      file=sys.stderr, flush=True)
        if not self.log_path.exists():
            return
        with self.log_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("seq", 0) > since:
                    # Older journals stored command output as assistant/result.
                    # Socket readers require every sequence, including these.
                    if local_command_output(event):
                        event["type"] = "broker.local_command"
                    yield event
        return


def wake_envelope(task: dict) -> str:
    """What a woken Codex root is told a background task finished with: the
    tag Claude's CLI hands a persona its task notification in, so the model
    reads it as the same thing and the transcript writer drops it as the same
    thing (`loam_transcript._HARNESS_ENVELOPES`), not as the owner's words."""
    name = task.get("description") or task.get("task_type") or "A background task"
    said = (task.get("said") or "").strip() or "(it said nothing)"
    return ("<task-notification>\n"
            f"<task-id>{task.get('task_id')}</task-id>\n"
            "<status>completed</status>\n"
            f"<summary>{name} finished after your turn had ended. "
            "What it finished with is below.</summary>\n"
            f"<result>\n{said}\n</result>\n"
            "</task-notification>")


def command_description(command, limit: int = 60) -> str:
    """A root command left running, as its background task names it: the
    script inside the ``/bin/bash -lc '…'`` Codex wraps every command in,
    on one line, cut at ``limit`` characters."""
    if isinstance(command, list):
        argv = [str(part) for part in command]
        text = " ".join(argv)
    else:
        text = str(command or "")
        try:
            argv = shlex.split(text)
        except ValueError:
            argv = []
    if (len(argv) == 3 and os.path.basename(argv[0]) in ("bash", "sh", "zsh")
            and argv[1] in ("-c", "-lc")):
        text = argv[2]
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


class CodexSession(Session):
    """Run a Codex app-server in the persona workspace and translate its RPC events into the
    common session journal. The root thread owns the turn; child threads remain
    background work, with their items nested under the spawning call. Deliver late child
    results to the root so a finished root turn cannot strand a report.
    """

    REQUEST_TIMEOUT_S = 60
    # How long a wake waits for others before it goes out, so two subagents
    # ending together give the root one turn; long enough for the root's own
    # `subagent-completed` item, which lands between a subagent's idle and its
    # `turn/completed`, to arrive first.
    WAKE_GATHER_S = 1.0
    # Route supported approval and elicitation requests to the client, including legacy
    # protocol names. Refuse unknown requests so the provider does not wait indefinitely.
    APPROVALS = ("item/commandExecution/requestApproval",
                 "item/fileChange/requestApproval",
                 "item/permissions/requestApproval",
                 "mcpServer/elicitation/request",
                 "item/tool/requestUserInput",
                 "execCommandApproval",
                 "applyPatchApproval")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.provider = "codex"
        self._rpc_next = 0
        self._waiters: dict[int, asyncio.Future] = {}
        self._codex = codex.Translator(self.id)
        # What each subagent thread is called, from the root's own
        # `subAgentActivity` item that started it (`/root/<task name>`): the
        # description a background task carries.
        self._agent_paths: dict[str, str] = {}
        # Which thread spawned each subagent, from the thread that item came
        # on: only the root's own children wake the root (see `_wake`).
        self._agent_parents: dict[str, str] = {}
        # The last thing each subagent said, which is what its wake carries.
        self._agent_said: dict[str, str] = {}
        # The id of the call that spawned each subagent thread (the
        # `subAgentActivity` item it started under): every event of that
        # thread is nested under it (`subagent_parent`).
        self._agent_calls: dict[str, str] = {}
        # The root's MCP calls and commands in flight (item id -> the
        # background task it would be), so one still open when the root's
        # turn ends can be a background task.
        self._root_calls: dict[str, dict] = {}
        # Wakes gathered in the `WAKE_GATHER_S` before one goes out.
        self._wakes: list[dict] = []
        self._wake_task: asyncio.Task | None = None

    def argv(self, resume: bool) -> list[str]:
        return [codex.CODEX, *codex.APP_SERVER_ARGV]

    def has_images_tool(self) -> bool:
        try:
            with (self.workspace / ".codex" / "config.toml").open("rb") as handle:
                return "images" in tomllib.load(handle).get("mcp_servers", {})
        except (OSError, ValueError, TypeError):
            return False

    async def start(self, resume: bool = False) -> None:
        if not (self.workspace / "agent.json").is_file():
            raise BrokerError(404, f"no agent named {self.agent!r}")
        env = codex.env()
        env.pop("LOAM_WAKE", None)
        env[loam_waiters.SESSION_ENV] = self.id
        # What the Claude session's hooks are given, for the same hooks: the
        # guard and the transcript writer find the persona by the project
        # directory, and the writer files the surface the broker states.
        env["CLAUDE_PROJECT_DIR"] = str(self.workspace)
        env[loam_transcript.SURFACE_ENV_VAR] = loam_transcript.SURFACE_LOAM_UI
        # Refresh the persona instructions and trust its workspace so the provider loads its
        # project configuration.
        if not (self.workspace / ".codex" / "config.toml").is_file():
            raise BrokerError(409, f"{self.agent} has no .codex/config.toml; "
                                   f"run bin/provision --codex {self.agent}")
        await asyncio.to_thread(codex.write_instructions, self.agent,
                                self.workspace)
        await asyncio.to_thread(loam_skills.quiet_refresh, self.workspace)
        await asyncio.to_thread(codex.write_trust, codex.persona_workspaces(),
                                codex.read_trusted_hooks())
        self.resumed = resume
        self._restored_status = None
        self._stop_requested = False
        self._stop_reason = None
        self._stopping_event = None
        self._turn_active = False
        self._clear_background()
        self._root_calls.clear()
        self._wakes.clear()
        self.last_activity = time.time()
        self._process_cost = None
        self.images_tool = self.has_images_tool()
        self.proc = await asyncio.create_subprocess_exec(
            *first_to_go(self.argv(resume)), cwd=str(self.workspace),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env, limit=16 * 1024 * 1024,
            start_new_session=True)
        self.state = "running"
        self.stdout_task = asyncio.create_task(self._pump_stdout())
        asyncio.create_task(self._pump_stderr())
        try:
            await self.request("initialize", {
                "clientInfo": {"name": "loam-broker", "title": "Loam",
                               "version": "1"}})
            await self.notify("initialized")
            listed = await self.request("model/list", {})
            if isinstance(listed.get("data"), list):
                await asyncio.to_thread(codex.write_models, listed["data"])
            ws = str(self.workspace)
            # The runtime's hooks, trusted by the hash Codex computes for
            # them. A project hook Codex has not seen trusted does not run,
            # and an untrusted guard is no guard. Config is re-read per
            # request, so trust written here holds for the thread below.
            hooks = await self.request("hooks/list", {"cwds": [ws]})
            mine = codex.runtime_hooks(hooks.get("data"), self.workspace)
            prefix = str(self.workspace / ".codex" / "config.toml") + ":"
            trusted = {k: v for k, v in codex.read_trusted_hooks().items()
                       if not k.startswith(prefix)}
            await asyncio.to_thread(codex.write_trust,
                                    codex.persona_workspaces(),
                                    {**trusted, **mine})
            if self.thread_id:
                # A resumed thread keeps the instructions it started with
                # unless it is handed new ones; these are this start's.
                opened = await self.request("thread/resume", {
                    "threadId": self.thread_id, "cwd": ws,
                    "model": self.model, "excludeTurns": True,
                    "baseInstructions": codex.instructions(self.workspace),
                    "developerInstructions": codex.SUBAGENT_ASK})
            else:
                # Use the shared explicit request for provider subagents.
                opened = await self.request("thread/start", {
                    "cwd": ws, "model": self.model,
                    "developerInstructions": codex.SUBAGENT_ASK})
                self.thread_id = (opened.get("thread") or {}).get("id")
        except Exception:
            self._stop_requested = True
            if self.proc.returncode is None:
                codex.kill_group(self.proc.pid)
            self.state = "exited"
            raise
        self._codex.model = opened.get("model") or self.model
        self.save()

    # -- JSON-RPC ----------------------------------------------------------

    async def request(self, method: str, params: dict) -> dict:
        """One request, and its result. An error answer is a BrokerError
        carrying Codex's own message, because that sentence is the one a
        person can act on ("not logged in", "model not found")."""
        self._rpc_next += 1
        rpc_id = self._rpc_next
        future = asyncio.get_running_loop().create_future()
        self._waiters[rpc_id] = future
        try:
            await self.send_raw({"jsonrpc": "2.0", "id": rpc_id,
                                 "method": method, "params": params})
            async with asyncio.timeout(self.REQUEST_TIMEOUT_S):   # gh-86296
                answer = await future
        except TimeoutError:
            raise BrokerError(504, f"codex did not answer {method} in "
                                   f"{self.REQUEST_TIMEOUT_S}s") from None
        finally:
            self._waiters.pop(rpc_id, None)
        if "error" in answer:
            error = answer.get("error") or {}
            raise BrokerError(502, f"codex {method}: "
                                   f"{error.get('message') or error}")
        result = answer.get("result")
        return result if isinstance(result, dict) else {}

    async def notify(self, method: str, params: dict | None = None) -> None:
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        await self.send_raw(message)

    async def _handle(self, obj: dict) -> None:
        method = obj.get("method")
        if method is None:
            # An answer to one of ours.
            future = self._waiters.get(obj.get("id"))
            if future is not None and not future.done():
                future.set_result(obj)
            return
        params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
        if "id" in obj:
            await self._server_request(method, obj["id"], params)
            return
        if method == "serverRequest/resolved":
            # Codex withdrew or settled a request -- an interrupted turn takes
            # its approvals with it. Dropped, as a `control_cancel_request`
            # is for the CLI, and said on the stream the same way. One this
            # broker answered has already left `pending` and says nothing.
            for request_id, req in list(self.pending.items()):
                if req.get("rpc_id") == params.get("requestId"):
                    self.pending.pop(request_id, None)
                    await self.emit(_resolved_event(
                        self.id, request_id, "cancelled", req, None, None))
                    self.save()
            return
        if method == "account/rateLimits/updated":
            # The quota, an account reading rather than this thread's: kept
            # on disk for the providers row, and put on this chat's stream as
            # the `rate_limit_event` a Claude chat carries, so the page moves
            # the ring the way it already does for Claude.
            try:
                usage = codex.keep_rate_limits(params, self.id)
            except OSError as exc:
                print(f"usage: could not record codex ({exc})",
                      file=sys.stderr, flush=True)
                usage = None
            if usage is not None:
                await Session._handle(self, codex.rate_limit_event(usage, self.id))
            return
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        if (item.get("type") == "subAgentActivity" and item.get("agentThreadId")
                and item.get("agentPath")):
            self._agent_paths[item["agentThreadId"]] = item["agentPath"]
            if params.get("threadId"):
                self._agent_parents.setdefault(item["agentThreadId"],
                                               params["threadId"])
            # Nest subagent events under the call that spawned them.
            if item.get("id") and item.get("kind") in (None, "started"):
                self._agent_calls.setdefault(item["agentThreadId"], item["id"])
        thread = params.get("threadId")
        if self.thread_id and thread and thread != self.thread_id:
            if (method == "item/completed" and item.get("type") in codex.MESSAGE_ITEMS
                    and isinstance(item.get("text"), str) and item["text"].strip()):
                self._agent_said[thread] = item["text"]
            # A subagent's thread. Its turn is a background task of this
            # session, and opening or closing it is not the root's turn
            # opening or closing; its token usage is its own context, not the
            # one the row's ring draws. Everything else -- its messages, its
            # tool calls and their results -- is translated as the root's
            # are, nested under the call that spawned it (`subagent_parent`),
            # so none of it is the persona's own words.
            if method == "turn/started":
                await self._subagent_turn(thread, True)
                return
            if method == "turn/completed":
                await self._subagent_turn(thread, False)
                return
            if method == "thread/status/changed":
                kind = (params.get("status") or {}).get("type")
                if kind in ("idle", "systemError", "notLoaded"):
                    await self._subagent_turn(thread, False)
                return
            if method == "thread/closed":
                await self._subagent_turn(thread, False)
                return
            if method == "thread/tokenUsage/updated":
                return
        # The root's MCP calls and commands. One still open when the root's
        # turn completes is a background task until its own `item/completed`,
        # which is then a wake: a9c8ec2c's `brief` ran 23 minutes past the
        # turn that made it, behind a row that said `done`, and probe
        # f35cc554's `sleep 30` 20 s past its turn the same way (a code cell
        # yields after 30 s and leaves its command running). Only these two:
        # a subagent's `subAgentActivity` and a `collabAgentToolCall`
        # straddle the root's turn end by a few events as a matter of course.
        late_call = None
        if (thread == self.thread_id and item.get("id")
                and item.get("type") in ("mcpToolCall", "commandExecution")):
            if method == "item/started":
                if item["type"] == "mcpToolCall":
                    task = {"task_type": "mcp_call",
                            "description": codex.tool_use(item).get("name")}
                else:
                    task = {"task_type": "local_bash",
                            "description": command_description(item.get("command"))}
                self._root_calls[item["id"]] = task
            elif method == "item/completed":
                self._root_calls.pop(item["id"], None)
                if any(task.get("task_id") == item["id"]
                       for task in self.background_tasks):
                    late_call = item
        if method == "turn/completed" and thread == self.thread_id:
            if (self._root_calls
                    and (params.get("turn") or {}).get("status") == "completed"):
                self.background_tasks = self.background_tasks + [
                    {"task_id": item_id, **task}
                    for item_id, task in self._root_calls.items()]
                await self.emit(self.background_event())
            self._root_calls.clear()
        parent = (self.subagent_parent(thread)
                  if self.thread_id and thread and thread != self.thread_id else None)
        for event in self._codex.feed(method, params, parent=parent):
            await Session._handle(self, event)
        if late_call is not None:
            content = codex.tool_result(late_call).get("content")
            if not isinstance(content, str):
                content = "\n".join(block.get("text") or "" for block in content or []
                                    if isinstance(block, dict))
            await self._task_ended(late_call["id"], content)

    def subagent_parent(self, thread: str) -> str:
        """Return the spawning call's ID for nesting, or a stable synthetic ID until the
        provider supplies the call.
        """
        return self._agent_calls.get(thread) or f"codex-subagent:{thread}"

    async def _subagent_turn(self, thread: str, running: bool) -> None:
        """A subagent thread's turn opened or closed: the background list
        gains or loses it, and a client is told as it is for a Claude task.

        The list is the whole truth, kept here because Codex states no list of
        its own: a thread is in it from its ``turn/started`` to its
        ``turn/completed``. Nothing goes through `_awaiting`: that holds the
        gap before a Claude task's notification, and here the broker sends
        the notification itself, in the same breath (`_wake`)."""
        if not running:
            await self._task_ended(thread, self._agent_said.pop(thread, None))
            return
        live = [task for task in self.background_tasks
                if task.get("task_id") != thread]
        live.append({"task_id": thread, "task_type": "subagent",
                     "description": self._agent_paths.get(thread)})
        changed = len(live) != len(self.background_tasks)
        self.background_tasks = live
        self.last_activity = time.time()
        if changed:
            await self.emit(self.background_event())
            self.save()

    async def _task_ended(self, task_id: str, said: str | None) -> None:
        """A background task left the list: a subagent's turn, or a root MCP
        call or command that outlived its turn. Said to a client, and the root woken for
        it if it is the root's to hear. A subagent's end is signalled two or
        three times (idle, ``turn/completed``, ``thread/closed``); only the
        first finds it in the list."""
        task = next((task for task in self.background_tasks
                     if task.get("task_id") == task_id), None)
        self.last_activity = time.time()
        if task is None:
            return
        self.background_tasks = [other for other in self.background_tasks
                                 if other is not task]
        await self.emit(self.background_event())
        # A grandchild reports to the subagent that spawned it, not here.
        if self._agent_parents.get(task_id, self.thread_id) == self.thread_id:
            await self._wake(task, said)
        self.save()

    async def _wake(self, task: dict, said: str | None) -> None:
        """Hand the root what a finished task said, as Claude's CLI hands a
        persona its task notification: ``system``/``task_notification`` on the
        stream now, then a root turn whose input is the ``<task-notification>``
        envelope. The page draws the woken turn as it draws a Claude one --
        no ``broker.user_message``, an ``init``, the work, a ``result`` -- and
        the row goes ``waiting``/1 to ``working``/0 here, not via ``done``.

        A root mid-turn is not woken: Codex hands that turn the report itself.
        Tasks ending within ``WAKE_GATHER_S`` of each other share one turn;
        one ending after the wake's ``turn/start`` has gone joins that turn
        the same way, as Codex's own business. The ``turn/start`` is sent
        from a task of its own, because this runs on the stdout pump and the
        answer to it arrives on that pump."""
        if self.state != "running" or self._stop_requested or not self.thread_id:
            return
        if self._turn_active and self._wake_task is None:
            return
        self._turn_active = True
        await Session._handle(self, {
            "type": "system", "subtype": "task_notification",
            "session_id": self.id, "provider": "codex",
            "task_id": task.get("task_id"), "task_type": task.get("task_type"),
            "description": task.get("description"), "status": "completed",
            "summary": said})
        self._wakes.append({**task, "said": said})
        if self._wake_task is None:
            self._wake_task = asyncio.create_task(self._send_wake())

    async def _send_wake(self) -> None:
        await asyncio.sleep(self.WAKE_GATHER_S)
        wakes, self._wakes = self._wakes, []
        self._wake_task = None
        if not wakes or self.state != "running" or self._stop_requested:
            self._turn_active = False
            return
        params = {"threadId": self.thread_id, "input": [
            {"type": "text",
             "text": "\n\n".join(wake_envelope(wake) for wake in wakes)}]}
        if self.model:
            params["model"] = self.model
            self._codex.model = self.model
        if self.effort:
            params["effort"] = self.effort
        self.last_activity = time.time()
        try:
            started = await self.request("turn/start", params)
            turn_id = (started.get("turn") or {}).get("id")
            if turn_id:
                self._codex.turn_id = turn_id
        except BrokerError as exc:
            await Session._handle(self, self._codex.result(
                {"status": "failed", "error": {"message": exc.message}}))

    async def _server_request(self, method: str, rpc_id, params: dict) -> None:
        """Codex asking the client something. Each of ``APPROVALS`` is a
        permission card, answered on the existing permission route; a card
        nobody answers waits as a Claude one does, until the reaper declines
        it after ``PERMISSION_WAIT_S``. Anything else is
        refused so a turn never waits on a question nobody sees."""
        if method not in self.APPROVALS:
            print(f"[{self.agent}/{self.id[:8]}] codex asked {method}; refused",
                  file=sys.stderr, flush=True)
            await self.send_raw({"jsonrpc": "2.0", "id": rpc_id, "error": {
                "code": -32601, "message": f"{method} is not handled by this client"}})
            return
        request_id = f"codex-{rpc_id}"
        if method in ("item/commandExecution/requestApproval",
                      "execCommandApproval"):
            command = params.get("command")
            if isinstance(command, list):
                command = " ".join(str(c) for c in command)
            tool, payload = "Bash", {"command": command, "cwd": params.get("cwd")}
        elif method in ("item/fileChange/requestApproval", "applyPatchApproval"):
            tool, payload = "apply_patch", {"grantRoot": params.get("grantRoot")}
        elif method == "mcpServer/elicitation/request":
            # An MCP server asking, or Codex asking before an MCP call
            # (`_meta.codex_approval_kind`). The card shows Codex's own
            # sentence and whatever it says about the call.
            meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
            server = params.get("serverName")
            tool = f"mcp__{server}"
            payload = {"server": server, "message": params.get("message"),
                       "mode": params.get("mode"), "meta": meta}
            for key in ("url", "requestedSchema"):
                if params.get(key) is not None:
                    payload[key] = params[key]
        elif method == "item/tool/requestUserInput":
            tool, payload = "request_user_input", {"questions": params.get("questions")}
        else:
            tool, payload = "permissions", {"permissions": params.get("permissions"),
                                            "cwd": params.get("cwd")}
        req = {"codex_method": method, "rpc_id": rpc_id, "params": params,
               "tool_name": tool, "input": payload, "raised_at": time.time()}
        # A question for the person, in the one shape the page draws. An
        # elicitation is one only when it is a form with fields; Codex's own
        # MCP tool-call approval is an empty form and stays an approval.
        if method == "item/tool/requestUserInput":
            asked = questions_fmt.from_codex_input(params)
        elif method == "mcpServer/elicitation/request":
            asked = questions_fmt.from_elicitation(params)
        else:
            asked = None
        if asked is not None:
            req["questions"] = asked
            req["question_message"] = params.get("message")
        self.pending[request_id] = req
        self.last_activity = time.time()
        await self._raise_request(req, _request_event({
            "type": "broker.permission_request", "session_id": self.id,
            "request_id": request_id, "tool_name": tool, "display_name": None,
            "input": payload,
            "description": params.get("reason") or params.get("message"),
            "decision_reason": params.get("reason"),
            "decision_reason_type": None, "permission_suggestions": None,
            "tool_use_id": params.get("itemId"), "provider": "codex",
            "codex_method": method,
        }, req))
        self.save()

    async def answer_permission(self, request_id: str, behavior: str,
                                message: str | None = None,
                                updated_input: dict | None = None,
                                answers: dict | None = None,
                                updated_permissions: list | None = None,
                                resolved_as: str | None = None) -> None:
        # `updated_permissions` is the CLI's shape and Codex offers no
        # suggestions to echo, so it is not passed on and no scope is
        # claimed; `resolved_as` names an answer nobody gave, as for Claude.
        req = self.pending.get(request_id)
        if req is None:
            raise BrokerError(404, f"no permission request {request_id} is pending")
        allow = behavior == "allow"
        method = req["codex_method"]
        asked = _questions_of(req)
        checked = None
        if asked is not None:
            # A question: the page's answers, checked and put back in the
            # vendor's shape before the request leaves `pending`, so a 400
            # leaves the card answerable. requestUserInput has no way to
            # decline in its schema, so a deny is no answers.
            try:
                if allow:
                    checked = questions_fmt.check(asked, answers)
                if method == "item/tool/requestUserInput":
                    result = questions_fmt.to_codex_input(checked or {})
                elif allow:
                    result = questions_fmt.to_elicitation(req["params"], checked)
                else:
                    result = {"action": "decline"}
            except questions_fmt.AnswerError as exc:
                raise BrokerError(400, str(exc)) from None
        elif method == "item/permissions/requestApproval":
            # Allowing grants what was asked for, for this turn; denying
            # grants nothing. Codex's own scopes beyond `turn` are not offered
            # because the card has no control for them.
            result = {"permissions": (req["params"].get("permissions") or {})
                      if allow else {}, "scope": "turn"}
        elif method == "mcpServer/elicitation/request":
            # A form's fields come back as the card's edited input, if the
            # page sent one; an approval has none to fill.
            result = ({"action": "accept",
                       "content": updated_input if isinstance(updated_input, dict)
                       else {}} if allow else {"action": "decline"})
        elif method == "item/tool/requestUserInput":
            # The answers are the page's to give, in Codex's shape
            # (`{question_id: {"answers": [...]}}`); a deny answers nothing.
            answers = (updated_input or {}).get("answers") if allow else None
            result = {"answers": answers if isinstance(answers, dict) else {}}
        elif method in ("execCommandApproval", "applyPatchApproval"):
            result = {"decision": "approved" if allow else "denied"}
        else:
            result = {"decision": "accept" if allow else "decline"}
        self.pending.pop(request_id, None)
        await self.send_raw({"jsonrpc": "2.0", "id": req["rpc_id"],
                             "result": result})
        self.last_activity = time.time()
        await self.emit(_resolved_event(self.id, request_id,
                                        resolved_as or behavior, req,
                                        message, checked))
        self.save()

    # -- turns -------------------------------------------------------------

    async def send_message(self, text: str,
                           attachments: list[dict] | None = None, *,
                           dispatch_id: str | None = None) -> int:
        """``turn/start`` with the person's words, after the same
        ``broker.user_message`` a Claude turn logs. Attachments are named by
        path in the text, as for Claude; an image small enough is also given
        as ``localImage``, which is Codex's way of showing one.

        A ``turn/start`` Codex refuses -- no login, a model it does not
        know -- closes the turn as a failed ``result`` carrying Codex's
        sentence, so the page shows the refusal where the answer would be."""
        if self.proc is None or self.state != "running" or self._stop_requested:
            raise self._not_running_error()
        attachments = attachments or []
        event = {"type": "broker.user_message", "session_id": self.id,
                 "text": text}
        if dispatch_id:
            event["dispatch_id"] = dispatch_id
        if attachments:
            event["attachments"] = attachments
        await self.emit(event)
        lines = [self._timed(text)] if text or self.tell_time else []
        images = []
        if attachments:
            lines.append("Attached to this message (files on disk):")
            for item in attachments:
                lines.append(f"- {item['path']} ({item['type']}, "
                             f"{human_size(item['size'])})")
                if (item["type"] in cfg.INLINE_IMAGE_TYPES
                        and item["size"] <= cfg.INLINE_IMAGE_MAX_BYTES):
                    images.append({"type": "localImage", "path": item["path"]})
        params = {"threadId": self.thread_id,
                  "input": [{"type": "text", "text": "\n".join(lines)}] + images}
        if self.model:
            params["model"] = self.model
            self._codex.model = self.model
        if self.effort:
            params["effort"] = self.effort
        self.last_activity = time.time()
        self._turn_active = True
        if self.title is None:
            self.title = first_words(
                text or (f"Attached {attachments[0]['name']}"
                         if attachments else ""))
        self.save()
        try:
            started = await self.request("turn/start", params)
            turn_id = (started.get("turn") or {}).get("id")
            if turn_id:
                self._codex.turn_id = turn_id
        except BrokerError as exc:
            await Session._handle(self, self._codex.result(
                {"status": "failed", "error": {"message": exc.message}}))
        return event["seq"]

    async def steer(self, text: str,
                    attachments: list[dict] | None = None, *,
                    dispatch_id: str | None = None) -> int | None:
        """Steer only the expected root turn. Journal the input once the provider accepts it. A
        racing turn completion returns None so the caller can send the input as a new turn.
        """
        turn_id = self._codex.turn_id
        if (not self._turn_active or not turn_id or not self.thread_id
                or self.proc is None or self.state != "running"
                or self._stop_requested):
            return None
        attachments = attachments or []
        lines = [self._timed(text)] if text or self.tell_time else []
        images = []
        if attachments:
            lines.append("Attached to this message (files on disk):")
            for item in attachments:
                lines.append(f"- {item['path']} ({item['type']}, "
                             f"{human_size(item['size'])})")
                if (item["type"] in cfg.INLINE_IMAGE_TYPES
                        and item["size"] <= cfg.INLINE_IMAGE_MAX_BYTES):
                    images.append({"type": "localImage", "path": item["path"]})
        try:
            await self.request("turn/steer", {
                "threadId": self.thread_id, "expectedTurnId": turn_id,
                "input": [{"type": "text", "text": "\n".join(lines)}] + images})
        except BrokerError as exc:
            print(f"codex: {self.id[:8]}: turn/steer refused on turn "
                  f"{turn_id[:8]} ({exc.message}); a turn of its own follows",
                  file=sys.stderr, flush=True)
            return None
        event = {"type": "broker.user_message", "session_id": self.id,
                 "text": text, "steered": True, "turn_id": turn_id}
        if dispatch_id:
            event["dispatch_id"] = dispatch_id
        if attachments:
            event["attachments"] = attachments
        await self.emit(event)
        self.last_activity = time.time()
        return event["seq"]

    async def set_choices(self, changes: dict) -> list[str]:
        """On the record only: ``turn/start`` carries the model and the
        effort, so the next message is the next turn on them, process or no
        process. Nothing is sent now."""
        for field in ("model", "effort"):
            if field in changes:
                setattr(self, field, changes[field])
        self.save()
        return []

    async def stop(self, reason: str | None = None) -> None:
        """``turn/interrupt`` for a turn in flight, then the process ends.
        DELETE on the session is the one stop route there is; interrupting
        first lets Codex close the turn and write its rollout rather than
        lose it to a signal."""
        if self._stopping_event is not None:
            if reason and not self._stop_reason:
                self._stop_reason = reason
            await self._stopping_event.wait()
            return
        self._stop_requested = True
        self._stop_reason = reason
        self._stopping_event = asyncio.Event()
        try:
            if (self.state == "running" and self._turn_active
                    and self.thread_id and self._codex.turn_id):
                try:
                    await self.request("turn/interrupt", {
                        "threadId": self.thread_id, "turnId": self._codex.turn_id})
                    deadline = time.monotonic() + 5
                    while self._turn_active and time.monotonic() < deadline:
                        await asyncio.sleep(0.1)
                except BrokerError as exc:
                    print(f"[{self.agent}/{self.id[:8]}] interrupt: {exc.message}",
                          file=sys.stderr, flush=True)
            # `Session.stop`, by process group: see `loam_codex.kill_group`.
            if self.proc is not None and self.proc.returncode is None:
                codex.kill_group(self.proc.pid, signal.SIGTERM)
                try:
                    async with asyncio.timeout(10):   # not wait_for: gh-86296
                        await self.proc.wait()
                except TimeoutError:
                    codex.kill_group(self.proc.pid)
            await self.close_pending("ended")
            self.state = "exited"
            self._clear_background()
            self._root_calls.clear()
            self._wakes.clear()
            self.save()
        finally:
            self._stopping_event.set()

    async def _reap(self) -> None:
        for future in self._waiters.values():
            if not future.done():
                future.set_result({"error": {"message": "codex exited"}})
        await super()._reap()

    async def _title(self) -> None:
        # The vault's title comes from the transcript writer, which is a
        # Claude Code hook and does not run here; the stand-in stays.
        self._titling = False


class AntigravitySession(Session):
    """Launch one Antigravity process per turn. Resume the provider's conversation ID on
    later turns and translate the stream into session events. Between turns the session
    has no resident process.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.provider = "antigravity"
        self._agy = agy.Translator(self.id)

    def is_live(self) -> bool:
        return (self.state == "running" and not self._stop_requested
                and self.proc is not None and self.proc.returncode is None)

    async def start(self, resume: bool = False) -> None:
        if not (self.workspace / "agent.json").is_file():
            raise BrokerError(404, f"no agent named {self.agent!r}")
        if not (self.workspace / agy.MCP_FILE).is_file():
            raise BrokerError(409, f"{self.agent} has no {agy.MCP_FILE}; "
                                   f"run bin/provision --antigravity {self.agent}")
        login = await asyncio.to_thread(agy.login_state)
        if not login["connected"]:
            raise BrokerError(409, f"Antigravity is not connected: "
                                   f"{login['reason']}")
        await asyncio.to_thread(loam_skills.quiet_refresh, self.workspace)
        self.resumed = resume
        self._restored_status = None
        self._stop_requested = False
        self._stop_reason = None
        self._stopping_event = None
        self._turn_active = False
        self._clear_background()
        self.last_activity = time.time()
        self._process_cost = None
        self.state = "running"
        self.save()

    # Each turn runs in a separate process without an interactive stdin. Queue incoming
    # messages for the next turn.
    STEERS = False
    _video_preparers = asyncio.Semaphore(2)

    async def steer(self, text: str,
                    attachments: list[dict] | None = None, *,
                    dispatch_id: str | None = None) -> int | None:
        return None

    async def send_message(self, text: str,
                           attachments: list[dict] | None = None, *,
                           dispatch_id: str | None = None) -> int:
        if self.state != "running" or self._stop_requested:
            raise self._not_running_error()
        if self._turn_active or (self.proc is not None and self.proc.returncode is None):
            raise BrokerError(409, f"session {self.id} has a turn in flight")
        self._turn_active = True
        attachments = attachments or []
        event = {"type": "broker.user_message", "session_id": self.id,
                 "text": text}
        if dispatch_id:
            event["dispatch_id"] = dispatch_id
        if attachments:
            event["attachments"] = attachments
        try:
            await self.emit(event)
        except (OSError, asyncio.CancelledError):
            self._turn_active = False
            raise
        previews = None
        lines = [self._timed(text)] if text or self.tell_time else []
        if attachments:
            lines.append("Attached to this message (files on disk):")
            for position, item in enumerate(attachments, 1):
                lines.append(f"- {item['path']} ({item['type']}, "
                             f"{human_size(item['size'])})")
                if not (item.get("type") or "").startswith("video/"):
                    continue
                if item['size'] <= agy.VIDEO_INPUT_MAX_BYTES:
                    lines.append("Use read_file on this video to see its contents.")
                    continue
                try:
                    if previews is None:
                        previews = tempfile.TemporaryDirectory(prefix="loam-video-")
                    preview = Path(previews.name) / f"attachment-{position}.mp4"
                    async with self._video_preparers:
                        ready = await asyncio.to_thread(agy.video_preview, item['path'], preview)
                except OSError:
                    ready = False
                if self._stop_requested:
                    if previews is not None:
                        previews.cleanup()
                    self._turn_active = False
                    raise BrokerError(409, "This turn was stopped while preparing the video.")
                if ready:
                    lines.append(f"Use read_file on {preview} to watch a smaller copy "
                                 "covering the full duration of the video above.")
                else:
                    lines.append("Loam could not make a smaller copy for read_file. "
                                 "Keep the original path available and tell the user if you cannot watch it.")
        prompt = agy.prompt_arg(self.id, "\n".join(lines))
        self.last_activity = time.time()
        if self.title is None:
            self.title = first_words(
                text or (f"Attached {attachments[0]['name']}"
                         if attachments else ""))
        self.save()
        env = agy.env()
        env["CLAUDE_PROJECT_DIR"] = str(self.workspace)
        env.pop("LOAM_WAKE", None)
        env[loam_waiters.SESSION_ENV] = self.id
        env[loam_transcript.SURFACE_ENV_VAR] = loam_transcript.SURFACE_LOAM_UI
        self._agy = agy.Translator(self.id)
        self._agy.model = self.model
        try:
            await asyncio.to_thread(agy.write_instructions, self.workspace)
            self.proc = await asyncio.create_subprocess_exec(
                *first_to_go(agy.argv(prompt, self.thread_id, self.model,
                                      self.effort)),
                cwd=str(self.workspace), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=env, start_new_session=True, limit=16 * 1024 * 1024)
        except OSError as exc:
            if previews is not None:
                previews.cleanup()
            await Session._handle(self, self._agy.result(
                {"status": "ERROR", "error": f"Antigravity could not start its turn: {exc}"}))
            return event["seq"]
        asyncio.create_task(self._pump_turn(previews))
        asyncio.create_task(self._pump_stderr())
        return event["seq"]

    async def _pump_turn(self, previews) -> None:
        try:
            await self._pump_stdout()
        finally:
            if previews is not None:
                previews.cleanup()

    async def _handle(self, obj: dict) -> None:
        if obj.get("type") == "broker.exit":   # an unparsable line, as is
            await Session._handle(self, obj)
            return
        for event in self._agy.feed(obj):
            if event.get("type") == "result" and self._agy.conversation_id \
                    and self.thread_id != self._agy.conversation_id:
                self.thread_id = self._agy.conversation_id
            await Session._handle(self, event)

    async def _reap(self) -> None:
        """A turn's process ended. The session does not: the next message
        is the next process. A process that ended with no ``result`` closes
        the turn as a failed one."""
        assert self.proc is not None
        pid = getattr(self.proc, "pid", None)
        code = await self.proc.wait()
        if not self._stop_requested and self.on_unexpected_exit is not None:
            try:
                self.on_unexpected_exit(self, code, pid)
            except Exception:  # noqa: BLE001
                pass
        if not self._agy.done:
            await Session._handle(self, self._agy.result(
                {"status": "INTERRUPTED" if self._stop_requested else "ERROR",
                 "error": f"agy exited {code} before its result"}))
        self._turn_active = False
        self.save()

    async def stop(self, reason: str | None = None) -> None:
        if self._stopping_event is not None:
            if reason and not self._stop_reason:
                self._stop_reason = reason
            await self._stopping_event.wait()
            return
        self._stop_requested = True
        self._stop_reason = reason
        self._stopping_event = asyncio.Event()
        try:
            if self.proc is not None and self.proc.returncode is None:
                codex.kill_group(self.proc.pid, signal.SIGTERM)
                try:
                    async with asyncio.timeout(10):   # not wait_for: gh-86296
                        await self.proc.wait()
                except TimeoutError:
                    codex.kill_group(self.proc.pid)
            await self.close_pending("ended")
            self.state = "exited"
            self._clear_background()
            self.save()
            exit_event = {"type": "broker.exit", "session_id": self.id,
                          "exit_code": None, "ts": now_iso()}
            if self._stop_reason:
                exit_event["reason"] = self._stop_reason
            await self.emit(exit_event)
            for queue in list(self.subscribers):
                queue.put_nowait(None)
        finally:
            self._stopping_event.set()

    async def set_choices(self, changes: dict) -> list[str]:
        for field in ("model", "effort"):
            if field in changes:
                setattr(self, field, changes[field])
        self.save()
        return []

    async def _title(self) -> None:
        self._titling = False


def session_class(provider: str | None) -> type[Session]:
    if provider == "antigravity":
        return AntigravitySession
    return CodexSession if provider == "codex" else Session
