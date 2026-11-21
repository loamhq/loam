"""Count Claude CLI round trips by distinct assistant message IDs. A response can
contain several tool calls, and num_turns in the result envelope also counts tool
results on successful runs. Subtract inherited IDs when counting a fork.
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
    """A model family accepts its point releases. An explicit model ID must match exactly
    so a downgrade is detectable.
    """
    if configured.startswith("claude-"):
        return seen == configured
    return model_family(seen) == model_family(configured)


class SessionModel(NamedTuple):
    """Keep model, session and refusal as the first three fields for positional callers.
    The latest record supplies last. A model selection carries its name and time, and
    refusal_to names the fallback target.
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


def local_command_output(event: dict) -> bool:
    """Claude marks both halves of a local command on the stream. Synthetic
    provider errors have no such markers and still belong to the turn."""
    if event.get("type") == "assistant":
        return bool(event.get("local_command_run") or event.get("local_command_source"))
    return event.get("type") == "result" and bool(event.get("local_command"))


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
    """Select the session with the newest dated record. Undated appends can change mtime
    without adding a turn. Scan newest files first and stop once older mtimes cannot
    beat the best timestamp.
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
    """Inspect the latest session's transcript for the model its last turn used. A /model
    choice supersedes a refusal, and a later fallback supersedes that choice. Return
    None when there is no transcript.
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
    """Return (status, reason), where status is ok, chosen or drift. Check both the
    broker's requested model and an in-session selection before flagging a discrepancy.
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
    """Read the last synthetic API error as "<error>: <text>", or return None. A terminal
    provider failure can still leave a success envelope and empty stderr.
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
    """Count distinct assistant response IDs, subtracting IDs inherited from a parent when
    the session is a fork. Return None if the log cannot be read.
    """
    ids = _assistant_ids(session_id)
    if ids is None:
        return None
    if forked_from:
        inherited = _assistant_ids(forked_from)
        if inherited:
            ids = ids - inherited
    return len(ids)
