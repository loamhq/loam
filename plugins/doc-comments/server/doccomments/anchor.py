"""What a note points at: an anchor is the passage's own words (``quote``),
a little of the text on either side (``prefix``, ``suffix``) and where it
was when made (``pos``). Nothing is written into the document.

An anchor is never searched for. Where a note's passage is after a change is
carried through that change (``sidecar.locate``); this module only makes
anchors and scores how well a place's surroundings match one (used to choose
between the two equal readings of one worked-out change). The searcher that
once re-found a quote anywhere in the text was removed on 2026-11-17 (round 6):
nothing called it any more, and nothing should.
"""

from __future__ import annotations

CONTEXT = 32          # characters of context kept on each side


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
