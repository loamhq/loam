"""Text differences at the size people edit in: words, spaces and marks.

A text is cut into tokens -- a run of word characters, a run of spaces, or
one other character -- whose concatenation is the text exactly, so nothing
here can change a byte that was not changed. Two texts are compared token by
token (difflib, without its junk heuristic), and the result is either

- **hunks**: ``(b1, b2, tokens)`` in the first text's token positions, the
  form the three-way merge needs; or
- **a delta**: ``[["=", n], ["-", n], ["+", "text"]]`` in characters, the
  form the save record keeps. It is readable in the JSON and is applied to
  the previous text to give the next one.
"""

from __future__ import annotations

import difflib
import re

TOKEN = re.compile(r"\w+|[^\S\n]+|\n|[^\w\s]", re.UNICODE)


def tokens(text: str) -> list[str]:
    out = TOKEN.findall(text)
    # Every character is in exactly one token; this is the promise the merge
    # and the deltas rest on.
    assert sum(map(len, out)) == len(text)
    return out


def _opcodes(a: list[str], b: list[str]):
    """difflib's opcodes, with the common head and tail taken off first:
    the usual edit touches a little of a long document, and SequenceMatcher
    is quadratic in what it is given."""
    head = 0
    n = min(len(a), len(b))
    while head < n and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < n - head and a[len(a) - 1 - tail] == b[len(b) - 1 - tail]:
        tail += 1
    mid_a, mid_b = a[head:len(a) - tail], b[head:len(b) - tail]
    ops = []
    if head:
        ops.append(("equal", 0, head, 0, head))
    if mid_a or mid_b:
        sm = difflib.SequenceMatcher(None, mid_a, mid_b, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            ops.append((tag, i1 + head, i2 + head, j1 + head, j2 + head))
    if tail:
        ops.append(("equal", len(a) - tail, len(a), len(b) - tail, len(b)))
    return ops


def hunks(a: list[str], b: list[str]) -> list[tuple[int, int, list[str]]]:
    """What changed from ``a`` to ``b``: each a range of ``a``'s tokens and
    the tokens of ``b`` that replace it, in order."""
    return [(i1, i2, b[j1:j2]) for tag, i1, i2, j1, j2 in _opcodes(a, b)
            if tag != "equal"]


def delta(old: str, new: str) -> list[list]:
    """The character delta that turns ``old`` into ``new``."""
    if old == new:
        return [["=", len(old)]] if old else []
    a, b = tokens(old), tokens(new)
    out: list[list] = []

    def put(op, val):
        if out and out[-1][0] == op:
            out[-1][1] += val
        else:
            out.append([op, val])

    for tag, i1, i2, j1, j2 in _opcodes(a, b):
        if tag == "equal":
            put("=", sum(map(len, a[i1:i2])))
            continue
        if i2 > i1:
            put("-", sum(map(len, a[i1:i2])))
        if j2 > j1:
            put("+", "".join(b[j1:j2]))
    return out


def apply(old: str, ops: list[list]) -> str:
    """``old`` with ``ops`` applied. Refuses a delta that does not fit."""
    out, at = [], 0
    for op, val in ops:
        if op == "=":
            out.append(old[at:at + val])
            at += val
        elif op == "-":
            at += val
        elif op == "+":
            out.append(val)
        else:
            raise ValueError(f"unknown delta op {op!r}")
        if at > len(old):
            raise ValueError("the delta runs past the end of the text")
    if at != len(old):
        raise ValueError("the delta does not cover the whole text")
    return "".join(out)


def map_pos(ops: list[list], pos: int, after: bool = False) -> int:
    """Where position ``pos`` of the old text is in the new one. Inside a
    deleted run it lands where the run was; at an insertion, before it unless
    ``after``."""
    old = new = 0
    for op, val in ops:
        if op == "=":
            if pos < old + val or (pos == old + val and not after):
                return new + (pos - old)
            old += val
            new += val
        elif op == "-":
            if pos < old + val:
                return new
            old += val
        elif op == "+":
            if pos == old and not after:
                return new
            new += len(val)
    return new + max(0, pos - old)


_FM = re.compile(r"---\r?\n(?:.*?\r?\n)?---[ \t]*(?:\r?\n|$)", re.S)
_HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t#]*$")


def summary(old: str, new: str, ops: list[list] | None = None) -> dict:
    """What a History row says of a save: ``{"added", "removed", "first",
    "section"}``. ``first`` is the words of the first change in the body (a
    change to the frontmatter alone, like a provenance stamp, is not what a
    person made); ``section`` is the heading above that change in the new
    text, or the start of its paragraph when there is none."""
    ops = delta(old, new) if ops is None else ops
    added = sum(len(v) for o, v in ops if o == "+")
    removed = sum(v for o, v in ops if o == "-")
    fm_old = _FM.match(old)
    body_from = fm_old.end() if fm_old else 0
    first, at_new, at = "", None, 0
    pos_new = 0
    for op, val in ops:
        if op == "=":
            at += val
            pos_new += val
        elif op == "+":
            if at >= body_from:
                first, at_new = val, pos_new
                break
            pos_new += len(val)
        elif op == "-":
            if at + val > body_from:
                first, at_new = old[max(at, body_from):at + val], pos_new
                break
            at += val
    if at_new is None:
        fm_new = _FM.match(new)
        return {"added": added, "removed": removed, "first": "", "section": "",
                "frontmatter": bool(ops) and not (added == removed == 0) and bool(fm_new or fm_old)}
    first = " ".join(first.split())[:60]
    section = ""
    lines = new[:at_new].splitlines()
    # A `#` inside a fenced code block is a comment, not a heading.
    fenced, in_code = [], False
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            in_code = not in_code
            fenced.append(True)
        else:
            fenced.append(in_code)
    for line, code in zip(reversed(lines), reversed(fenced)):
        m = None if code else _HEADING.match(line.strip())
        if m:
            section = m.group(1).strip()
            break
    if not section:
        start = new.rfind("\n\n", 0, at_new)
        para = new[start + 2 if start >= 0 else 0:].lstrip()
        section = " ".join(para.split()[:6])
    return {"added": added, "removed": removed, "first": first, "section": section[:60]}
