"""What the CLI's turn cap counts, read back from the session it wrote.

``claude -p --max-turns N`` stops a session after N **API round trips**: N
assistant messages, however many tool calls each one carries. That is not
what the result envelope's ``num_turns`` reports on a run that finished on
its own. Measured on CLI 2.1.260, 2026-09-07, by causing both outcomes on a
scratch project (the write-up is in engineer/memory/MEMORY.md):

    cap 3, one tool call per message .... 3 round trips ran, num_turns 4
    cap 8, one tool call per message .... 8 round trips ran, num_turns 9
    cap 3, six parallel calls per message  3 round trips ran, num_turns 4
    no cap hit, 2 x six parallel calls ... 3 round trips ran, num_turns 13

So on the cap path ``num_turns`` is the loop counter (round trips + 1: it is
incremented before the check that aborts), and on the success path it is the
number of user messages in the finished conversation -- the prompt plus one
per tool result, because the CLI stores every tool result as its own user
message. A session with 21 round trips and 127 tool calls therefore reports
``num_turns`` 129 against a cap of 100 and is nowhere near it. Both launchers
log both numbers so the log is never read that way again.

The envelope carries no round-trip count, so it is read from the session
file: distinct assistant ``message.id`` values, since the CLI writes one
entry per content block and every block of one response shares its id.

**A forked session's transcript is not only its own** (2026-09-22). A job
continued with ``--resume <id> --fork-session`` writes a new file that opens
with a copy of the parent's entries, so counting them all reports the
parent's round trips as the child's. ``round_trips`` takes the parent and
subtracts its ids for that reason.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import loam_config as lc
from typing import NamedTuple

# Where the CLI writes session transcripts: one directory per working
# directory, one <session_id>.jsonl per session. A test points this at a
# fixture tree and restores it.
SESSIONS_DIR = lc.CLAUDE_HOME / "projects"


def model_family(value: str) -> str:
    """``fable`` and ``claude-fable-5-1`` are one family; ``claude-opus-4-8``
    is another. A context suffix (``fable[1m]``) names a window, not a model.
    """
    v = re.sub(r"\[\d+m\]$", "", value.strip().lower())
    return re.sub(r"^claude-", "", v).split("-")[0]


def model_matches(configured: str, seen: str) -> bool:
    """Whether a lane running ``seen`` is running what ``configured`` asked
    for. Two strictnesses, and the difference is the point.

    A config naming a FAMILY (``fable``) is satisfied by any model in it: a
    point release is not drift, and pinning a persona to an exact id would
    make every Fable release a false alarm.

    A config naming an EXPLICIT ID (the engineers' ``claude-opus-5``) is
    satisfied only by that id. This is what makes the owner's 2026-09-25 rule
    -- wherever the system runs Opus it runs the LATEST Opus -- checkable at
    all: a drop from Opus 5 to Opus 4.8 passes a family test and fails this.
    """
    if configured.startswith("claude-"):
        return seen == configured
    return model_family(seen) == model_family(configured)


class SessionModel(NamedTuple):
    """What one session ran, and what its own records say about why.

    The first three fields are the ``(model, session, refusal)`` triple this
    function returned before 2026-09-28, in the same order, so a caller that
    indexes it reads what it always read.

    ``last`` is the session's newest dated record, which is what made it the
    latest (see ``latest_session``) and what the nightly alarm remembers.
    ``chosen`` is set when the model the last turn ran on was PICKED inside
    the session -- a ``/model`` command, whether the owner typed it, the Claude
    Code app's model menu sent it, or the broker sent it for the + menu --
    and holds the model named and when. ``refusal_to`` is the model the
    refusal record switched to, so a report can tell whether that refusal
    explains the last turn or is history the session has since moved past.
    """
    model: str
    session: str
    refusal: str
    last: str
    chosen: str | None
    refusal_to: str | None


# What the CLI writes when a model is set inside a session. Seen on this box
# in three shapes, 2026-09-03 to 09-22: "Set model to claude-fable-5",
# "Set model to `claude-opus-5`", and "Set model to `Opus 5` for this session
# only" -- as a user record or a system `local_command` record.
_SET_MODEL = re.compile(r"<local-command-stdout>Set model to `?([^`\n<]+?)`?(?: for |\n|<)")

# The broker's acknowledgement of a /model is a turn record of its own whose
# model is this; it is not a model anything ran on.
_SYNTHETIC = "<synthetic>"


def chosen_name(value: str) -> str:
    """A model as ``/model`` announced it, in the form ``model_matches``
    takes: ``Opus 5`` -> ``opus-5`` (the family ``opus``), and an id stays an
    id."""
    return re.sub(r"\s+", "-", value.strip().strip("`").lower())


def _last_dated(path: Path) -> str:
    """The newest ``timestamp`` in the file, or "" if it has none.

    Timestamps are the CLI's own ISO-8601 UTC strings, all with a ``Z`` and
    milliseconds, so they order as strings.
    """
    newest = ""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.search(r'"timestamp":\s*"([^"]+)"', line)
                if m and m.group(1) > newest:
                    newest = m.group(1)
    except OSError:
        return ""
    return newest


def latest_session(d: Path) -> tuple[Path, str] | None:
    """The session in ``d`` with the newest DATED record, and that record's
    timestamp.

    **Not the newest file by mtime** (until 2026-09-28 it was). The Remote
    Control server (retired 2026-11-19) kept a session file of its own,
    named with a UUID v5, and touched it about nine seconds after every
    start by appending an ``atis-latch`` record that carries no timestamp.
    So at every nightly restart a file whose last turn was weeks old became the newest on disk,
    outranked the previous night's real wake sessions, and the model alarm
    re-read the same stale session every night -- four identical false lines
    in comms on 09-26, 09-27 and 09-28. A turn carries its time; a touch does
    not, so the turn is what is ranked.

    Files are read newest-mtime first and the scan stops once a file's mtime
    is older than the best record already found: a record cannot be written
    after the file was last modified, so nothing older can win.
    """
    try:
        files = sorted(((p.stat().st_mtime, p) for p in d.glob("*.jsonl")),
                       reverse=True)
    except OSError:
        return None
    best: tuple[Path, str] | None = None
    for mtime, path in files:
        if best is not None:
            cutoff = datetime.fromtimestamp(mtime + 1, timezone.utc)
            if cutoff.strftime("%Y-%m-%dT%H:%M:%S") < best[1][:19]:
                break
        last = _last_dated(path)
        if last and (best is None or last > best[1]):
            best = (path, last)
    return best


def effective_model(workspace: Path) -> SessionModel | None:
    """The model a lane's most recent session actually ran its last turn on.

    Read back from the lane's own transcript rather than asked of anything
    else, because the whole point is to catch a model the configuration does
    not name: a value derived from the config could never disagree with it.

    None when the lane has no transcript yet. Only the MOST RECENT session is
    read -- the one with the newest dated record, see ``latest_session`` --
    so a finding clears by itself once the lane next starts a session on the
    right model, which is the only way one CAN clear while the offending
    session is still live and still being typed into.

    The session's records are read in order, and the last word on the model
    wins: a ``/model`` sets ``chosen``, a ``model_refusal_fallback`` record
    sets the refusal note and clears ``chosen``, and a later ``/model``
    clears the refusal in turn. One persona's 09-03 session is why the order
    matters: a refusal moved it to Opus 4.8 at 01:40, and then on 09-11 a
    ``/model`` chose Opus 5 -- so its last turns were chosen, and the alarm
    that reported them as the refusal's doing was wrong twice over.
    """
    found = latest_session(SESSIONS_DIR / str(workspace).replace("/", "-"))
    if found is None:
        return None
    latest, last = found
    model, refusal, refusal_to, chosen = None, "", None, None
    try:
        with latest.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if ('"model"' not in line and "refusal" not in line
                        and "Set model to" not in line):
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = rec.get("message")
                if isinstance(msg, dict) and msg.get("model") not in (None, "", _SYNTHETIC):
                    model = msg["model"]
                if rec.get("type") in ("user", "system"):
                    text = msg.get("content") if isinstance(msg, dict) else rec.get("content")
                    m = _SET_MODEL.search(text) if isinstance(text, str) else None
                    if m:
                        chosen = f"{chosen_name(m.group(1))} at {rec.get('timestamp')}"
                        refusal, refusal_to = "", None
                if rec.get("subtype") == "model_refusal_fallback":
                    chosen = None
                    refusal_to = rec.get("fallbackModel")
                    refusal = (
                        f"the CLI switched {rec.get('originalModel')} -> "
                        f"{rec.get('fallbackModel')} at {rec.get('timestamp')} "
                        f"after the API refused a turn (category "
                        f"{rec.get('apiRefusalCategory')!r}, scope "
                        f"{rec.get('scope')!r})"
                    )
    except OSError:
        return None
    if not model:
        return None
    return SessionModel(model, latest.name, refusal, last, chosen, refusal_to)


def model_verdict(configured: str, seen: SessionModel,
                  asked: str | None) -> tuple[str, str]:
    """``("ok" | "chosen" | "drift", why)`` for a lane configured
    ``configured`` whose latest session is ``seen``; ``asked`` is the model
    the broker's record says the chat asked for, if any.

    Drift means the lane ran a model that neither its config nor any record
    of a choice explains. A choice is either on the broker's record (the +
    menu, 2026-09-21) or in the session itself (a ``/model``, which is also
    what the Claude Code app's model menu sends). One function, so
    that ``bin/check-drift`` and anything that asks after it cannot answer
    it differently.
    """
    if model_matches(configured, seen.model):
        return "ok", ""
    if asked and model_matches(chosen_name(asked), seen.model):
        return "chosen", f"this chat asked for {asked!r} on the broker's record"
    if seen.chosen:
        name, _, when = seen.chosen.partition(" at ")
        if model_matches(name, seen.model):
            return "chosen", f"`/model {name}` in the session at {when}"
    return "drift", ""


def turn_cap_hit(envelope: dict) -> bool:
    """Whether the run was cut off by ``--max-turns``.

    ``subtype == "error_max_turns"`` is the signal the CLI has carried since
    the flag existed and is what the launchers were already derived from;
    ``terminal_reason == "max_turns"`` arrived beside it (present on 2.1.260)
    and is accepted too, so a rename of one does not turn a capped run into
    a short one.
    """
    return (envelope.get("subtype") == "error_max_turns"
            or envelope.get("terminal_reason") == "max_turns")


def session_file(session_id: str | None) -> Path | None:
    """The session's transcript, or None if it cannot be found."""
    if not session_id:
        return None
    try:
        return next(SESSIONS_DIR.glob(f"*/{session_id}.jsonl"), None)
    except OSError:
        return None


def api_error(session_id: str | None) -> str | None:
    """The last API error the CLI recorded in the session, or None.

    When a request fails terminally (a 429 "out of usage credits", a 5xx
    the retries gave up on) the CLI appends a synthetic assistant entry --
    `isApiErrorMessage: true`, `error: "rate_limit"` and the like, and the
    user-facing sentence as its text -- and exits 1 with a `success`
    envelope and nothing on stderr. Read from the transcript on 2026-09-08
    after a job died that way with an empty session-stderr.log. Returned as
    "<error>: <text>" so a receipt names the cause.
    """
    path = session_file(session_id)
    if path is None:
        return None
    last: str | None = None
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "assistant" or not entry.get("isApiErrorMessage"):
                    continue
                message = entry.get("message") or {}
                text = " ".join(
                    (c.get("text") or "").strip()
                    for c in (message.get("content") or [])
                    if isinstance(c, dict) and c.get("type") == "text"
                ).strip()
                kind = entry.get("error") or "api_error"
                last = f"{kind}: {text}" if text else str(kind)
    except OSError:
        return None
    return last


def _assistant_ids(session_id: str | None) -> set[str] | None:
    """Distinct assistant ``message.id`` values in the session, or None if the
    transcript cannot be read."""
    path = session_file(session_id)
    if path is None:
        return None
    ids: set[str] = set()
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "assistant":
                    continue
                message = entry.get("message") or {}
                if message.get("id"):
                    ids.add(message["id"])
    except OSError:
        return None
    return ids


def round_trips(session_id: str | None, forked_from: str | None = None) -> int | None:
    """API round trips in the session: what ``--max-turns`` is compared to.

    None when the transcript cannot be read. Never raises: this is a number
    on a log line, not a gate.

    ``forked_from`` is the session this one was forked out of, and it is not
    an optional nicety. Caused 2026-09-22 on the first continued job: ``claude
    --resume <id> --fork-session`` writes a NEW transcript that opens with a
    copy of the parent's entries, so a continued job whose own run was three
    round trips reported seven -- the parent's four plus its own -- against a
    cap that counts only this run's. The inherited ids are subtracted rather
    than the counts, so an entry the fork trimmed or reordered cannot make the
    number negative or wrong.
    """
    ids = _assistant_ids(session_id)
    if ids is None:
        return None
    if forked_from:
        inherited = _assistant_ids(forked_from)
        if inherited:
            ids = ids - inherited
    return len(ids)
