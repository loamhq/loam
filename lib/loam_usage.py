"""What a run spent and what it was refused, read out of the CLI's envelope.

Every lane already runs with ``--output-format json`` and every lane already
parses the envelope, but until 2026-09-30 nothing kept its usage: the wake
line carried ``total_cost_usd`` and no token counts, so how much of a lane's
input the cache served -- the number a model change is decided on -- was
recorded nowhere. ``bin/cache-report`` reconstructs it from session
transcripts by matching clocks; this reads it from the envelope the run
already returns.

The names on the log line are the envelope's own: ``usage`` with its snake
case, ``modelUsage`` with its camel case, ``permission_denials`` as a list.
Nothing here is priced. ``total_cost_usd`` and each model's ``costUSD`` are
the CLI's estimate at list price (``costBasis: "list"`` on 2.1.281), not a
bill; on a subscription there is no bill per run to compare it with.

What the envelope holds on 2.1.281, caused 2026-09-30 with the wake's own
flags: in a ``-p`` run the top-level ``usage`` is the whole run summed, and
equals ``modelUsage`` when one model ran. It is not the same in the broker's
long-lived stream-json sessions, where ``usage`` and ``modelUsage`` disagree
by an order of magnitude on one envelope -- which is why the two are both
kept rather than one derived from the other.

Each denial is ``{tool_name, tool_use_id, tool_input}``. The list is kept as
the CLI gave it, with one cut: a string inside ``tool_input`` longer than
``INPUT_CHARS`` is shortened, because a denied ``Write`` carries the whole
file it meant to write and the log is a line per run.

Stdlib only, and nothing here may fail a run: a missing or malformed field is
an absent field, never an exception.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

USAGE_FIELDS = (
    "input_tokens", "output_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens",
)
# The 1h/5m split of cache writes. Kept because the two are priced
# differently, and a lane that writes its cache for an hour and wakes once a
# night pays for an hour it never reads.
CACHE_CREATION = "cache_creation"
MODEL_FIELDS = (
    "inputTokens", "outputTokens",
    "cacheReadInputTokens", "cacheCreationInputTokens", "costUSD",
)
INPUT_CHARS = 500

# Three consecutive nights of refused vault writes send one actionable
# notice to the selected system persona. The failure log retains each run.
WRITE_TOOLS = ("Write", "Edit")
REPEAT_NIGHTS = 3


def _count(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _clip(value):
    if isinstance(value, str) and len(value) > INPUT_CHARS:
        return value[:INPUT_CHARS] + f"… [{len(value):,} chars]"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


def from_envelope(envelope: dict) -> dict:
    """The fields to merge into a log line. Absent in, absent out.

    An older CLI's envelope has no ``permission_denials`` and may have no
    ``modelUsage``; the line then carries only what it had before.
    """
    out: dict = {}
    usage = envelope.get("usage")
    if isinstance(usage, dict):
        kept = {k: usage[k] for k in USAGE_FIELDS if _count(usage.get(k))}
        split = usage.get(CACHE_CREATION)
        if isinstance(split, dict):
            kept[CACHE_CREATION] = {
                k: v for k, v in split.items() if _count(v)}
        if kept:
            out["usage"] = kept
    models = envelope.get("modelUsage")
    if isinstance(models, dict):
        kept_models = {
            name: {k: row[k] for k in MODEL_FIELDS if _count(row.get(k))}
            for name, row in models.items() if isinstance(row, dict)
        }
        if kept_models:
            out["modelUsage"] = kept_models
    denials = envelope.get("permission_denials")
    if isinstance(denials, list):
        out["permission_denials"] = [
            {**d, "tool_input": _clip(d.get("tool_input"))}
            if isinstance(d, dict) else d
            for d in denials
        ]
    return out


def denied_names(denials: list) -> str:
    """``Read ×2, Bash``: the tools, never their arguments. Commonest first."""
    names = Counter(
        d.get("tool_name", "?") if isinstance(d, dict) else "?"
        for d in denials)
    return ", ".join(
        name if n == 1 else f"{name} ×{n}" for name, n in names.most_common())


def denial_reason(lane: str, denials: list) -> str:
    n = len(denials)
    return (f"{lane} was refused {n} tool call{'' if n == 1 else 's'}: "
            f"{denied_names(denials)}. Recorded, not a failure: the run "
            f"finished, and the arguments are on its line in the wake log.")


def vault_writes(denials: list, vault: Path) -> bool:
    """Whether any denial is a write whose path lies inside the vault."""
    root = str(vault).rstrip("/") + "/"
    for d in denials or ():
        if not isinstance(d, dict) or d.get("tool_name") not in WRITE_TOOLS:
            continue
        path = (d.get("tool_input") or {}).get("file_path")
        if isinstance(path, str) and path.startswith(root):
            return True
    return False


def vault_write_nights(log: Path, vault: Path, today: date) -> bool:
    """True if each of the REPEAT_NIGHTS - 1 dates before ``today`` has a line
    in ``log`` with a denied vault write. Today's own is the caller's to know.
    """
    want = {(today - timedelta(days=i)).isoformat()
            for i in range(1, REPEAT_NIGHTS)}
    seen: set[str] = set()
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        day = str(row.get("ts", ""))[:10]
        if day in want and vault_writes(row.get("permission_denials"), vault):
            seen.add(day)
    return seen == want
