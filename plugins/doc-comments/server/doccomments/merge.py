"""Three-way merge of saves, by words.

``base`` is the text an editor began from, ``theirs`` the text on disk now
(someone saved first), ``ours`` the save arriving. Changes to different
passages both land. Where both changed the same passage, **the first save
stands** -- the passage keeps ``theirs`` -- and ours becomes a conflict: the
passage as it now reads and what we would have made it, which the caller
turns into a suggestion in our author's name. The rest of our save lands.

Hunks are compared in the base's token positions. Two hunks touch when
their ranges overlap, or when one is an insertion at a point inside or at
the edge of the other: an insertion right beside someone's change is about
the same passage.
"""

from __future__ import annotations

from . import textdiff


def _touch(a, b) -> bool:
    (a1, a2), (b1, b2) = a, b
    if a1 == a2 or b1 == b2:
        return a1 <= b2 and b1 <= a2
    return a1 < b2 and b1 < a2


def _apply(base: list[str], lo: int, hi: int, hs) -> list[str]:
    """``base[lo:hi]`` with the hunks ``hs`` (all inside it) applied."""
    out, at = [], lo
    for b1, b2, toks in sorted(hs, key=lambda h: (h[0], h[1])):
        out.extend(base[at:b1])
        out.extend(toks)
        at = b2
    out.extend(base[at:hi])
    return out


def merge3(base: str, ours: str, theirs: str) -> tuple[str, list[dict]]:
    """``(merged, conflicts)``. Each conflict is ``{"start", "end", "theirs",
    "ours"}``: the range in ``merged`` that holds the first save's version of
    the passage, and our version of it."""
    if ours == base or ours == theirs:
        return theirs, []
    if theirs == base:
        return ours, []
    B = textdiff.tokens(base)
    ho = [("o",) + h for h in textdiff.hunks(B, textdiff.tokens(ours))]
    ht = [("t",) + h for h in textdiff.hunks(B, textdiff.tokens(theirs))]
    allh = sorted(ho + ht, key=lambda h: (h[1], h[2]))
    clusters: list[list] = []
    for h in allh:
        if clusters and any(_touch((h[1], h[2]), (c[1], c[2])) for c in clusters[-1]):
            clusters[-1].append(h)
        else:
            clusters.append([h])
    # A later hunk can touch an earlier cluster's widened range only through
    # one of its members, which the loop above checks; ranges are sorted, so
    # one pass is enough.
    out: list[str] = []
    conflicts: list[dict] = []
    at = 0
    length = 0
    for cl in clusters:
        lo = min(h[1] for h in cl)
        hi = max(h[2] for h in cl)
        gap = B[at:lo]
        out.extend(gap)
        length += sum(map(len, gap))
        mine = [h[1:] for h in cl if h[0] == "o"]
        first = [h[1:] for h in cl if h[0] == "t"]
        t_ver = _apply(B, lo, hi, first)
        if not mine:
            piece = t_ver
        elif not first:
            piece = _apply(B, lo, hi, mine)
        else:
            o_ver = _apply(B, lo, hi, mine)
            piece = t_ver
            if o_ver != t_ver:
                t_text, o_text = "".join(t_ver), "".join(o_ver)
                conflicts.append({"start": length, "end": length + len(t_text),
                                  "theirs": t_text, "ours": o_text})
        out.extend(piece)
        length += sum(map(len, piece))
        at = hi
    out.extend(B[at:])
    return "".join(out), conflicts


def widen(text: str, start: int, end: int, ours: str) -> tuple[int, int, str]:
    """A conflict whose first-save version is empty (they deleted the
    passage) has nothing to point at. Widen it to the word on each side, in
    the text and in our version alike, so the suggestion has a quote."""
    if end > start:
        return start, end, ours
    s = start
    while s > 0 and text[s - 1].isspace():
        s -= 1
    while s > 0 and not text[s - 1].isspace():
        s -= 1
    e = end
    while e < len(text) and text[e].isspace():
        e += 1
    while e < len(text) and not text[e].isspace():
        e += 1
    if e == s:
        return start, end, ours
    return s, e, text[s:start] + ours + text[end:e]
