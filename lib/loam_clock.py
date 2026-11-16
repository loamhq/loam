"""The time as a persona is told it (2026-11-09), in one place.

The owner's clock is the box's, America/Los_Angeles. A harness gives a turn
less than that: Claude Code's context says ``Today's date is 2026-11-09.``
with no weekday and no time, Codex's ``<environment_context>`` gives the
date and the zone. On Monday 11-09 two personas' first thread replies said
"Sunday afternoon". So every turn the broker sends -- a classic chat's
message and a thread's prompt -- carries the weekday, date and time here.

A classic chat's line goes to the harness inside a ``<loam-clock>`` envelope
at the head of the person's text. The broker's own log
(``broker.user_message``) keeps what the person typed; the transcript writer
and the adoption replay strip the envelope (``strip``) where they read the
harness's own file. Stdlib only.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import loam_config as lc

PACIFIC = ZoneInfo(lc.TIMEZONE)

TAG = "loam-clock"
ENVELOPE_RE = re.compile(rf"<{TAG}>.*?</{TAG}>[ \t]*\n*", re.S)


def sentence(now: datetime | None = None) -> str:
    """``It is now Monday, 2026-11-09, 9:25 AM PST (America/Los_Angeles).``"""
    now = (now or datetime.now(timezone.utc)).astimezone(PACIFIC)
    hour = now.strftime("%I:%M %p").lstrip("0")
    return (f"It is now {now.strftime('%A')}, {now.strftime('%Y-%m-%d')}, "
            f"{hour} {now.strftime('%Z')} ({lc.TIMEZONE}).")


def envelope(now: datetime | None = None) -> str:
    return f"<{TAG}>{sentence(now)}</{TAG}>"


def stamp(text: str, now: datetime | None = None) -> str:
    """A person's text as the harness receives it: the clock, then the text."""
    return f"{envelope(now)}\n\n{text}" if text else envelope(now)


def strip(text: str) -> str:
    """The text with any clock envelope taken out."""
    if not isinstance(text, str) or f"<{TAG}>" not in text:
        return text
    return ENVELOPE_RE.sub("", text).strip()


def local_stamp(at: str) -> str:
    """``2026-11-09T17:25:51Z`` as ``Mon 2026-11-09 09:25 PST``; anything
    unparsable as it came."""
    try:
        when = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return str(at)
    return when.astimezone(PACIFIC).strftime("%a %Y-%m-%d %H:%M %Z")
