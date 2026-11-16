"""The account answering instead of the model, named as such.

On 2026-09-15 the account's monthly spend ceiling stopped the night. One persona's
wake was cut off mid-run at 09:22Z; every wake after it, the reviewer and
another's market-watch cron were refused. The provider's refusal arrives as the
model's text -- "You've hit your monthly spend limit. Switch to another
model, or manage usage credits at claude.ai/settings/usage..." -- so the
identity preflight, which tests that text for the soul, blamed the soul six
times. The fault was the account's, and fixing it is the owner's hands at
``claude.ai/settings/usage``, not anything in an agent's files.

This is the one matcher. A one-off OS-upgrade verifier named the shape first
(its CLI check, 2026-09-16; removed with it 2026-11-14); ``bin/wake`` and
``lib/loam_identity`` ask it before they judge anything else.

Two signals, the structured one first:

1. **The error category.** When a request fails terminally the CLI writes a
   synthetic assistant entry into the session transcript carrying
   ``error: "rate_limit"`` (read on 09-15 from a persona's session; see
   ``loam_session.api_error``). The stream-json ``system/api_retry`` event
   carries the same field with the categories in ``ACCOUNT_ERRORS``. The
   wake runs with ``--output-format json``, not stream-json, so the
   transcript is where it reads the category; ``kind_from_event`` exists so
   a stream-json caller reads it the same way.
2. **The text**, as the fallback. The preflight runs in plain-text mode and
   has no session id, so the refusal sentence is all it ever sees.
"""

from __future__ import annotations

import re

# Error categories that mean the account, not the run, is the problem. From
# the CLI's stream-json docs (read 2026-09-30) for the api_retry event;
# `rate_limit` is also what the transcript entry carried on 09-15.
ACCOUNT_ERRORS = frozenset({
    "billing_error", "rate_limit", "authentication_failed", "account_on_hold",
})

# The upgrade verifier's four phrases (09-16) and the provider's
# other refusals: a subscription usage limit, an API credit balance, a
# billing hold, an expired or invalid credential. Phrases, not words: this
# is run over a failed run's final text, and "billing" alone could be the
# model talking about something else.
REFUSAL_RE = re.compile(
    r"spend limit"
    r"|usage credits"
    r"|\brate_limit\b"
    r"|out of usage"
    r"|hit your (?:usage )?limit"
    r"|usage limit reached"
    r"|credit balance is too low"
    r"|\b(?:billing_error|account_on_hold|authentication_failed)\b"
    r"|account (?:is )?on hold"
    r"|invalid api key"
    r"|oauth token has expired"
    r"|please run /login",
    re.IGNORECASE,
)


class AccountRefused(Exception):
    """The provider refused the run. Not a finding about the agent.

    ``str(exc)`` is the provider's sentence, as the reason to record.
    """


def matches(text: str | None) -> bool:
    return bool(text) and bool(REFUSAL_RE.search(text))


def kind_from_event(event: dict) -> str | None:
    """The error category of a stream-json ``system/api_retry`` event."""
    if event.get("type") == "system" and event.get("subtype") == "api_retry":
        kind = event.get("error")
        return kind if isinstance(kind, str) else None
    return None


def refusal(text: str | None, kind: str | None = None) -> str | None:
    """The reason to record if this is the account refusing, else None.

    ``text`` is what the provider said in the model's place -- the
    preflight's answer, the transcript's error entry, the envelope's result
    -- and is passed whole, never a wrapper around it, because it becomes
    the reason on one line. ``kind`` is the structured category when one was
    seen; it wins over the text, and the text then only supplies the words.
    """
    flat = " ".join((text or "").split())[:300]
    if kind in ACCOUNT_ERRORS:
        return f"{kind}: {flat}" if flat else kind
    if matches(text):
        return flat
    return None
