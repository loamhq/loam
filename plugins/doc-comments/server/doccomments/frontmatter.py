"""The one line this plugin writes into a document: its id, in the
frontmatter, added at the first note. Everything else in the file is left
byte for byte as it was, including its line endings."""

from __future__ import annotations

import re
import secrets

ID_KEY = "loam_doc_id"
ID_RE = re.compile(r"^d-[a-z0-9]{10}$")
FRONTMATTER = re.compile(r"---(\r?\n)(?:(.*?)\r?\n)?---[ \t]*(?=\r?\n|$)", re.S)
_LINE = re.compile(rf"^{ID_KEY}[ \t]*:[ \t]*['\"]?([^'\"\s]+)['\"]?[ \t]*(?=\r?$)", re.M)
_WHO = re.compile(r"^last_modified_by[ \t]*:[ \t]*(.*?)[ \t]*$", re.M)
_LINK = re.compile(r"\[\[([^\]|#]+)")


def new_id() -> str:
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return "d-" + "".join(secrets.choice(alphabet) for _ in range(10))


def block(text: str) -> str | None:
    m = FRONTMATTER.match(text)
    return None if not m else (m.group(2) or "")


def doc_id(text: str) -> str | None:
    fm = block(text)
    if fm is None:
        return None
    m = _LINE.search(fm)
    return m.group(1) if m and ID_RE.match(m.group(1)) else None


def last_modified_by(text: str) -> str | None:
    """The name in ``last_modified_by: "[[Name]]"``, or None."""
    fm = block(text)
    if fm is None:
        return None
    m = _WHO.search(fm)
    if not m:
        return None
    link = _LINK.search(m.group(1))
    return (link.group(1) if link else m.group(1).strip("'\" ")).strip() or None


def with_id(text: str, did: str) -> str:
    """``text`` carrying ``loam_doc_id: did``: replacing a line that is
    there, else added as the frontmatter's last line, else in a new
    frontmatter at the top. The file's own line ending is used."""
    m = FRONTMATTER.match(text)
    if m:
        nl = m.group(1)
        inner = m.group(2)
        if inner is not None and _LINE.search(inner):
            new_inner = _LINE.sub(f"{ID_KEY}: {did}", inner, count=1)
        elif inner is None or inner == "":
            new_inner = f"{ID_KEY}: {did}"
        else:
            new_inner = inner + nl + f"{ID_KEY}: {did}"
        return _rebuild(text, m, new_inner)
    nl = "\r\n" if "\r\n" in text else "\n"
    return f"---{nl}{ID_KEY}: {did}{nl}---{nl}" + text


def _rebuild(text: str, m: re.Match, new_inner: str) -> str:
    nl = m.group(1)
    closing = text[m.start(0):m.end(0)]
    # The closing fence and anything after it on its line stay as they were.
    fence_at = closing.rfind("---")
    return "---" + nl + new_inner + nl + closing[fence_at:] + text[m.end(0):]


def without_id(text: str) -> str:
    """``text`` with the id line taken out again (a copy that must not share
    its original's notes)."""
    m = FRONTMATTER.match(text)
    if not m or m.group(2) is None:
        return text
    nl = m.group(1)
    lines = [ln for ln in m.group(2).split(nl) if not _LINE.match(ln)]
    return _rebuild(text, m, nl.join(lines))
