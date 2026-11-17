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
        gone, came = "".join(a[i1:i2]), "".join(b[j1:j2])
        if gone and came and len(gone) + len(came) <= 4000:
            # A run of words replaced is exact to the letter inside it: "x"
            # typed inside "light" keeps "l", "i", "ght" (so a note on the
            # word is carried as changed, there, not as gone).
            # Letters both share at its start stay first, then at its end:
            # "river |" becoming "riverthe river " keeps the old "river" as
            # the first one, where it was.
            p = 0
            while p < len(gone) and p < len(came) and gone[p] == came[p]:
                p += 1
            q = 0
            while q < len(gone) - p and q < len(came) - p and gone[-1 - q] == came[-1 - q]:
                q += 1
            if p:
                put("=", p)
            g2, c2 = gone[p:len(gone) - q], came[p:len(came) - q]
            for t2, x1, x2, y1, y2 in difflib.SequenceMatcher(None, g2, c2, autojunk=False).get_opcodes():
                if t2 == "equal":
                    put("=", x2 - x1)
                    continue
                if x2 > x1:
                    put("-", x2 - x1)
                if y2 > y1:
                    put("+", c2[y1:y2])
            if q:
                put("=", q)
            continue
        if gone:
            put("-", len(gone))
        if came:
            put("+", came)
    return _slide(old, out)


def _slide(old: str, ops: list[list]) -> list[list]:
    """Each insertion or deletion that could equally sit further right (the
    text after it repeats it) is moved as far right as it goes: of two equal
    runs side by side, the first is the one kept. A diff cannot tell which
    was typed; this way a note on the first is never taken for changed."""
    out = [list(o) for o in ops]
    i, old_at = 0, 0
    while i < len(out):
        op, val = out[i]
        if op == "=":
            old_at += val
            i += 1
            continue
        nxt = out[i + 1] if i + 1 < len(out) and out[i + 1][0] == "=" else None
        if nxt is not None and not (op == "-" and i + 1 < len(out) and out[i + 1][0] == "+"):
            room = nxt[1]
            k = 0
            if op == "+":
                x = val
                while k < room and x and old[old_at + k] == x[0]:
                    x = x[1:] + x[0]
                    k += 1
                if k:
                    out[i][1] = x
            elif op == "-":
                while k < room and old[old_at + k] == old[old_at + val + k]:
                    k += 1
            if k:
                prev = out[i - 1] if i and out[i - 1][0] == "=" else None
                if prev is not None:
                    prev[1] += k
                else:
                    out.insert(i, ["=", k])
                    i += 1
                nxt[1] -= k
                old_at += k
                if not nxt[1]:
                    out.pop(i + 1)
        if op == "-":
            old_at += val
        i += 1
    return out


def lean_left(old: str, ops: list[list]) -> list[list]:
    """The same change with each insertion or deletion moved as far left as
    it equally goes: the other reading of two equal runs side by side."""
    rev = [[op, val[::-1] if op == "+" else val] for op, val in reversed(ops)]
    back = _slide(old[::-1], rev)
    return [[op, val[::-1] if op == "+" else val] for op, val in reversed(back)]


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
    first, at_new, at, cut_at = "", None, 0, None
    pos_new = 0
    for k, (op, val) in enumerate(ops):
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
                # words taken out, not replaced: read where they were
                if not (k + 1 < len(ops) and ops[k + 1][0] == "+") and not (k and ops[k - 1][0] == "+"):
                    cut_at = max(at, body_from)
                break
            at += val
    if at_new is None:
        fm_new = _FM.match(new)
        return {"added": added, "removed": removed, "first": "", "section": "",
                "frontmatter": bool(ops) and not (added == removed == 0) and bool(fm_new or fm_old)}
    first = " ".join(first.split())[:60]
    section = ""
    # The section is read where the change is: for words taken out, in the
    # text they were taken from, their own line included (a comparison can
    # start a removed section after its heading's shared "## Section ", and
    # the heading is the whole line, number and all); for words put in, the
    # line they were put in, whole, when they start partway along it.
    if cut_at is not None:
        src, pos = old, cut_at
        end = src.find("\n", pos)
        pos = len(src) if end < 0 else end
    else:
        src, pos = new, at_new
        if pos > 0 and src[pos - 1] != "\n":
            end = src.find("\n", pos)
            pos = len(src) if end < 0 else end
    lines = src[:pos].splitlines()
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


def from_changes(old: str, changes) -> list[list] | None:
    """A delta from a client's own record of its edit: ``[[from, to, insert],
    …]`` in ``old``'s positions, in order and not overlapping. None when it
    does not fit ``old`` (then the caller works the change out instead)."""
    if not isinstance(changes, list):
        return None
    out, at = [], 0
    try:
        for c in changes:
            a, b, ins = int(c[0]), int(c[1]), c[2]
            if not isinstance(ins, str) or a < at or b < a or b > len(old):
                return None
            if a > at:
                out.append(["=", a - at])
            if b > a:
                out.append(["-", b - a])
            if ins:
                out.append(["+", ins])
            at = b
    except (TypeError, ValueError, IndexError):
        return None
    if at < len(old):
        out.append(["=", len(old) - at])
    return out


def then(text_a: str, ops_ab: list[list], text_c: str) -> list[list]:
    """The change from the text ``ops_ab`` makes of ``text_a`` on to
    ``text_c`` (a stamp the store added on its write), as one more delta to
    carry places through. Returns the second step only."""
    return delta(apply(text_a, ops_ab), text_c)
