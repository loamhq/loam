"""What a note points at, and finding it again after the text has changed.

An anchor is the passage's own words (``quote``), a little of the text on
either side (``prefix``, ``suffix``) and where it last was (``pos``). Nothing
is written into the document. To find it again:

1. **Exactly**: every place the quote occurs is scored by how much of its
   context still matches and how near it is to where it was; the best wins.
   The state is ``attached``.
2. **Changed**: no exact occurrence, but the context still brackets a
   passage like the quote, or a window of the text is close enough to it.
   The state is ``changed`` ("text changed since"), and the note keeps its
   original quote to show what it was written about.
3. **Detached**: neither. The note keeps its quote, is never deleted, and
   can be attached again to a new selection.
"""

from __future__ import annotations

import difflib

CONTEXT = 32          # characters of context kept on each side
MIN_RATIO = 0.6       # how like the quote a changed passage must be
BRACKET_RATIO = 0.45  # ... when its context on both sides still matches


def make(text: str, start: int, end: int) -> dict:
    if not 0 <= start < end <= len(text):
        raise ValueError("an anchor is a non-empty range inside the text")
    return {"quote": text[start:end],
            "prefix": text[max(0, start - CONTEXT):start],
            "suffix": text[end:end + CONTEXT],
            "pos": start}


def _common_tail(a: str, b: str) -> int:
    n = 0
    while n < len(a) and n < len(b) and a[-1 - n] == b[-1 - n]:
        n += 1
    return n


def _common_head(a: str, b: str) -> int:
    n = 0
    while n < len(a) and n < len(b) and a[n] == b[n]:
        n += 1
    return n


def _score(text: str, anchor: dict, start: int, end: int, hint: int) -> float:
    pre = anchor.get("prefix", "")
    suf = anchor.get("suffix", "")
    ctx = (_common_tail(text[max(0, start - len(pre)):start], pre)
           + _common_head(text[end:end + len(suf)], suf))
    distance = abs(start - hint)
    # Context dominates; distance only separates otherwise equal candidates.
    return ctx * 1000 - min(distance, 999_999) / 1000


def _occurrences(text: str, quote: str) -> list[int]:
    out, at = [], text.find(quote)
    while at >= 0:
        out.append(at)
        at = text.find(quote, at + 1)
    return out


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _bracketed(text: str, anchor: dict, hint: int):
    """The text between where the prefix ends and the suffix begins, when
    both are still there and what is between is like the quote."""
    quote, pre, suf = anchor["quote"], anchor.get("prefix", ""), anchor.get("suffix", "")
    pre_key = pre[-16:]
    suf_key = suf[:16]
    if len(pre_key) < 6 or len(suf_key) < 6:
        return None
    best = None
    for p in _occurrences(text, pre_key):
        start = p + len(pre_key)
        limit = start + max(len(quote) * 3, len(quote) + 200)
        s = text.find(suf_key, start, limit + len(suf_key))
        if s < 0 or s == start:
            continue
        between = text[start:s]
        r = _ratio(quote, between)
        if r >= BRACKET_RATIO:
            key = (r, -abs(start - hint))
            if best is None or key > best[0]:
                best = (key, start, s)
    return None if best is None else (best[1], best[2])


def _window(text: str, quote: str, hint: int):
    """The window of the text most like the quote, found from the longest
    run the two share, when it is like enough."""
    if len(quote) < 8:
        return None
    sm = difflib.SequenceMatcher(None, text, quote, autojunk=False)
    m = sm.find_longest_match(0, len(text), 0, len(quote))
    if m.size < max(6, min(12, len(quote) // 3)):
        return None
    base = m.a - m.b
    best = None
    slack = max(4, len(quote) // 4)
    for ds in range(-slack, slack + 1, max(1, slack // 4)):
        for de in range(-slack, slack + 1, max(1, slack // 4)):
            s = max(0, base + ds)
            e = min(len(text), base + len(quote) + de)
            if e <= s:
                continue
            r = _ratio(quote, text[s:e])
            key = (r, -abs(s - hint))
            if best is None or key > best[0]:
                best = (key, s, e)
    if best is None or best[0][0] < MIN_RATIO:
        return None
    s, e = _snap(text, best[1], best[2])
    return s, e


def _snap(text: str, s: int, e: int):
    """A found window, widened to whole words and trimmed of edge spaces."""
    while s > 0 and text[s - 1].isalnum() and text[s].isalnum():
        s -= 1
    while e < len(text) and text[e - 1].isalnum() and text[e].isalnum():
        e += 1
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e


def locate(text: str, anchor: dict, hint: int | None = None) -> dict:
    """``{"start", "end", "state"}``: where the anchor is in ``text`` now.
    ``hint`` is where it is expected (its old position mapped through a known
    edit); without one, its last position."""
    quote = anchor.get("quote") or ""
    hint = anchor.get("pos", 0) if hint is None else hint
    if not quote:
        return {"start": None, "end": None, "state": "detached"}
    found = _occurrences(text, quote)
    if found:
        best = max(found, key=lambda s: _score(text, anchor, s, s + len(quote), hint))
        return {"start": best, "end": best + len(quote), "state": "attached"}
    hit = _bracketed(text, anchor, hint) or _window(text, quote, hint)
    if hit:
        return {"start": hit[0], "end": hit[1], "state": "changed"}
    return {"start": None, "end": None, "state": "detached"}
