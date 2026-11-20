"""Format an install-local clock for each provider using the configured time zone. Keep
the clock envelope out of the person's stored message.
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
