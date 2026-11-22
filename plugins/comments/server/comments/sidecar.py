"""The unified sidecar: one readable JSON document per commented file.

One comment model serves every file type in the vault:

- On Markdown files, comments and suggestions carry a passage ``anchor``
  (quote + surrounding context) and ``where`` (current offsets in the text),
  plus the doc's save history (``base`` + ``saves``).
- On every other file type (PDF, images, HTML, audio, video, code, archives),
  a whole-file comment is the exact same thread record with ``anchor: null``
  and ``where: null``, and no ``base``/``saves``. A future anchor kind (a PDF
  page or an image coordinate) is an addition to ``anchor``, not a rewrite.

Shape (``version`` 1)::

    {"format": "loam-comments", "version": 1, "id": "f-…",
     "file": {"root": "vault", "path": "…/Spec.pdf",
              "dev": 2049, "ino": 123456, "sha256": "sha256:…", "size": 42000},
     "created": "…Z",
     "base": null | {"text": "…", "revision": "sha256:…", "at": "…Z", "author": {…}},
     "saves": [],
     "threads": [{"id": "n-…", "kind": "comment" | "suggestion",
                  "author": {…}, "created": "…Z", "text": "…",
                  "anchor": null | {"quote": "…", "prefix": "…", "suffix": "…", "pos": 0},
                  "state": "open" | "resolved" | "accepted" | "rejected",
                  "closed": null | {"by": {…}, "at": "…Z"},
                  "replies": [{"id": "r-…", "author": {…}, "created": "…Z", "text": "…"}],
                  "where": null | {"state": "attached" | "changed" | "detached", "start": …, "end": …}}]}
"""

from __future__ import annotations

import copy
import hashlib
import secrets
from datetime import datetime, timezone

from . import anchor as anchors
from . import textdiff

FORMAT = "loam-comments"
VERSION = 1
KINDS = ("comment", "suggestion")
OPEN = "open"
CLOSED = ("resolved", "accepted", "rejected")
KEEP_SAVES = 400


class NoteError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def revision(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _id(prefix: str) -> str:
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return prefix + "".join(secrets.choice(alphabet) for _ in range(8))


def new(fid: str, root: str, path: str, *, text: str | None = None,
        author: dict | None = None, fingerprint: dict | None = None,
        now: str | None = None) -> dict:
    now = now or now_iso()
    fmeta = {"root": root, "path": path}
    if fingerprint:
        for k in ("dev", "ino", "sha256", "size"):
            if fingerprint.get(k) is not None:
                fmeta[k] = fingerprint[k]
    base = ({"text": text, "revision": revision(text), "at": now, "author": author}
            if text is not None else None)
    return {"format": FORMAT, "version": VERSION, "id": fid,
            "file": fmeta, "created": now,
            "base": base, "saves": [], "threads": []}


def check(sc: dict) -> dict:
    if not isinstance(sc, dict) or sc.get("format") != FORMAT:
        raise NoteError(422, "that file is not a comments sidecar")
    if sc.get("version") != VERSION:
        raise NoteError(422, f"sidecar version {sc.get('version')!r} is not one this plugin reads")
    sc.setdefault("saves", [])
    sc.setdefault("threads", [])
    sc.setdefault("file", {})
    return sc


# -- the save record (Markdown files) ------------------------------------------

def head_text(sc: dict) -> str:
    if not sc.get("base"):
        return ""
    text = sc["base"]["text"]
    for s in sc["saves"]:
        text = textdiff.apply(text, s["delta"])
    return text


def head_revision(sc: dict) -> str | None:
    if not sc.get("base"):
        return None
    return sc["saves"][-1]["revision"] if sc["saves"] else sc["base"]["revision"]


def text_at(sc: dict, save_id: str) -> str:
    if not sc.get("base"):
        raise NoteError(404, "this file has no text history")
    text = sc["base"]["text"]
    if save_id == "base":
        return text
    for s in sc["saves"]:
        text = textdiff.apply(text, s["delta"])
        if s["id"] == save_id:
            return text
    raise NoteError(404, f"no save {save_id!r} in this doc's history")


def record_save(sc: dict, old: str, new_text: str, author: dict, kind: str,
                now: str | None = None, **extra) -> dict | None:
    if not sc.get("base") or old == new_text:
        return None
    ops = textdiff.delta(old, new_text)
    save = {"id": _id("s-"), "at": now or now_iso(), "author": author,
            "kind": kind, "revision": revision(new_text), "delta": ops,
            "summary": textdiff.summary(old, new_text, ops), **extra}
    sc["saves"].append(save)
    if len(sc["saves"]) > KEEP_SAVES:
        compact(sc, KEEP_SAVES // 2)
    return save


def compact(sc: dict, keep: int) -> None:
    drop = len(sc["saves"]) - keep
    if drop <= 0 or not sc.get("base"):
        return
    text = sc["base"]["text"]
    for s in sc["saves"][:drop]:
        text = textdiff.apply(text, s["delta"])
    last = sc["saves"][drop - 1]
    sc["base"] = {"text": text, "revision": last["revision"], "at": last["at"],
                  "author": last["author"], "folded": sc["base"].get("folded", 0) + drop}
    sc["saves"] = sc["saves"][drop:]


def authorship(sc: dict) -> list[dict]:
    if not sc.get("base"):
        return []
    runs: list[list] = []
    base = sc["base"]
    if base["text"]:
        runs.append([len(base["text"]), base.get("author", {}).get("id", "owner")])
    for s in sc["saves"]:
        who = s["author"]["id"]
        out: list[list] = []
        i, used = 0, 0

        def take(n: int, keep: bool):
            nonlocal i, used
            while n > 0 and i < len(runs):
                room = runs[i][0] - used
                step = min(room, n)
                if keep:
                    if out and out[-1][1] == runs[i][1]:
                        out[-1][0] += step
                    else:
                        out.append([step, runs[i][1]])
                used += step
                n -= step
                if used == runs[i][0]:
                    i, used = i + 1, 0

        for op, val in s["delta"]:
            if op == "=":
                take(val, True)
            elif op == "-":
                take(val, False)
            else:
                if out and out[-1][1] == who:
                    out[-1][0] += len(val)
                else:
                    out.append([len(val), who])
        runs = out
    spans, at = [], 0
    for n, who in runs:
        spans.append({"start": at, "end": at + n, "author": who})
        at += n
    return spans


# -- notes (anchored and whole-file) -------------------------------------------

def find(sc: dict, tid: str) -> dict:
    for t in sc["threads"]:
        if t["id"] == tid:
            return t
    raise NoteError(404, f"no note {tid!r} on this file")


def _body(text, what="the note", allow_empty=False) -> str:
    if not isinstance(text, str):
        raise NoteError(400, f"{what} is text")
    text = text.strip()
    if not text and not allow_empty:
        raise NoteError(400, f"{what} is empty")
    if len(text) > 20_000:
        raise NoteError(413, f"{what} is longer than 20,000 characters")
    return text


def add(sc: dict, text: str | None, kind: str, start: int | None, end: int | None,
        body: str, author: dict, replacement: str | None = None,
        now: str | None = None, **extra) -> dict:
    """Add an anchored note (Markdown) or a whole-file comment (``start`` and
    ``end`` both None, ``anchor: null``)."""
    if kind not in KINDS:
        raise NoteError(400, "a note is a comment or a suggestion")
    whole_file = start is None and end is None
    if whole_file:
        if kind != "comment":
            raise NoteError(400, "suggestions are for Markdown files; leave a comment instead")
        note = {"id": _id("n-"), "kind": "comment", "author": author,
                "created": now or now_iso(),
                "text": _body(body, allow_empty=False),
                "anchor": None,
                "state": OPEN, "closed": None, "replies": [],
                "where": None, **extra}
        sc["threads"].append(note)
        return note
    if not isinstance(text, str):
        raise NoteError(400, "anchored notes require document text")
    if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text)):
        raise NoteError(400, "a note points at a non-empty passage of the doc")
    if kind == "suggestion":
        if not isinstance(replacement, str):
            raise NoteError(400, "a suggestion says what the passage becomes")
        if replacement == text[start:end]:
            raise NoteError(400, "that suggestion changes nothing")
    note = {"id": _id("n-"), "kind": kind, "author": author,
            "created": now or now_iso(),
            "text": _body(body, allow_empty=kind == "suggestion"),
            "anchor": anchors.make(text, start, end),
            "state": OPEN, "closed": None, "replies": [],
            "where": {"state": "attached", "start": start, "end": end}, **extra}
    if kind == "suggestion":
        note["replacement"] = replacement
    _remember(note, text, start, end)
    sc["threads"].append(note)
    return note


def reply(sc: dict, tid: str, body: str, author: dict, now: str | None = None) -> dict:
    note = find(sc, tid)
    r = {"id": _id("r-"), "author": author, "created": now or now_iso(),
         "text": _body(body, "the reply")}
    note["replies"].append(r)
    return r


def edit_reply(sc: dict, tid: str, rid: str | None, body: str, author: dict) -> dict:
    note = find(sc, tid)
    item = note if rid is None else next((r for r in note["replies"] if r["id"] == rid), None)
    if item is None:
        raise NoteError(404, f"no reply {rid!r} on that note")
    if item["author"]["id"] != author["id"]:
        raise NoteError(403, "only its author changes a note's words")
    item["text"] = _body(body, allow_empty=note["kind"] == "suggestion" and rid is None)
    item["edited"] = now_iso()
    return item


def supersede(sc: dict, old_id: str, new: dict, author: dict) -> None:
    old = next((t for t in sc["threads"] if t["id"] == old_id), None)
    if not old or old is new or old["kind"] != "suggestion" or old["state"] != OPEN \
            or old["author"]["id"] != author["id"]:
        return
    new["replies"] = old.get("replies", []) + new.get("replies", [])
    if not new.get("text") and old.get("text"):
        new["text"] = old["text"]
    sc["threads"].remove(old)


def withdraw(sc: dict, tid: str, author: dict) -> None:
    note = find(sc, tid)
    if note["kind"] != "suggestion" or note["author"]["id"] != author["id"]:
        raise NoteError(403, "only its author takes back a suggestion")
    if note["state"] != OPEN:
        raise NoteError(409, f"that suggestion is already {note['state']}")
    if note.get("replies"):
        note["state"] = "rejected"
        note["closed"] = {"by": author, "at": now_iso()}
        return
    sc["threads"].remove(note)


def remove(sc: dict, tid: str, author: dict) -> None:
    note = find(sc, tid)
    if not own(note["author"]["id"], author["id"]):
        raise NoteError(403, "only its author deletes a note")
    if any(r["author"]["id"] != author["id"] for r in note.get("replies", [])):
        raise NoteError(409, "Others have replied to it; resolve it instead")
    sc["threads"].remove(note)


def delete_reply(sc: dict, tid: str, rid: str, author: dict) -> None:
    note = find(sc, tid)
    for i, r in enumerate(note["replies"]):
        if r["id"] == rid:
            if r["author"]["id"] != author["id"]:
                raise NoteError(403, "only its author removes a reply")
            del note["replies"][i]
            return
    raise NoteError(404, f"no reply {rid!r} on that note")


def own(author_id: str, who_id: str) -> bool:
    if author_id == who_id:
        return True
    if who_id.startswith("guest:") and author_id.startswith("guest-agent:" + who_id[len("guest:"):]):
        rest = author_id[len("guest-agent:" + who_id[len("guest:"):]):]
        return rest == "" or rest.startswith(":")
    return False


def resolve(sc: dict, tid: str, author: dict, may_any: bool, now: str | None = None) -> dict:
    note = find(sc, tid)
    if note["kind"] != "comment":
        raise NoteError(409, "a suggestion is accepted or rejected, not resolved")
    if note["state"] != OPEN:
        raise NoteError(409, f"that note is already {note['state']}")
    if not may_any and not own(note["author"]["id"], author["id"]):
        raise NoteError(403, "you may resolve your own notes; the owner resolves any")
    note["state"] = "resolved"
    note["closed"] = {"by": author, "at": now or now_iso()}
    return note


def reopen(sc: dict, tid: str, author: dict) -> dict:
    note = find(sc, tid)
    if note["state"] == OPEN:
        return note
    if note["state"] == "accepted":
        raise NoteError(409, "an accepted suggestion is part of the text now; "
                             "History can restore the text from before it")
    note["state"] = OPEN
    note["closed"] = None
    return note


def reject(sc: dict, tid: str, author: dict, now: str | None = None) -> dict:
    note = find(sc, tid)
    if note["kind"] != "suggestion":
        raise NoteError(409, "a comment is resolved, not rejected")
    if note["state"] != OPEN:
        raise NoteError(409, f"that suggestion is already {note['state']}")
    note["state"] = "rejected"
    note["closed"] = {"by": author, "at": now or now_iso()}
    return note


def accept(sc: dict, text: str, tid: str, author: dict, now: str | None = None) -> str:
    note = find(sc, tid)
    if note["kind"] != "suggestion":
        raise NoteError(409, "a comment is resolved, not accepted")
    if note["state"] != OPEN:
        raise NoteError(409, f"that suggestion is already {note['state']}")
    where = locate(note, text)
    if where["state"] == "detached":
        raise NoteError(409, "the passage this suggestion changes is gone; "
                             "attach it to a passage again first")
    if where["state"] == "changed":
        raise NoteError(409, "the passage has changed since this was suggested; "
                             "reply or reject it, or attach it again")
    new_text = text[:where["start"]] + note["replacement"] + text[where["end"]:]
    if text.endswith("\n") and not new_text.endswith("\n"):
        new_text += "\r\n" if text.endswith("\r\n") else "\n"
    note["state"] = "accepted"
    note["closed"] = {"by": author, "at": now or now_iso()}
    note["where"] = {"state": "attached", "start": where["start"],
                     "end": where["start"] + len(note["replacement"])}
    return new_text


def reattach(sc: dict, text: str, tid: str, start: int, end: int) -> dict:
    note = find(sc, tid)
    if note.get("anchor") is None:
        raise NoteError(400, "whole-file comments are not attached to a passage")
    if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text)):
        raise NoteError(400, "attach a note to a non-empty passage")
    note.setdefault("first_anchor", note["anchor"])
    note["anchor"] = anchors.make(text, start, end)
    _remember(note, text, start, end)
    note.pop("found", None)
    note["where"] = {"state": "attached", "start": start, "end": end}
    return note


# -- finding passages again (anchored Markdown notes) --------------------------

SURVIVE = 0.5


def _survived(ops: list, start: int, end: int) -> int:
    old, kept = 0, 0
    for op, val in ops:
        if op == "=":
            kept += max(0, min(end, old + val) - max(start, old))
            old += val
        elif op == "-":
            old += val
    return kept


def _deleted_run(ops: list, pos: int):
    old = 0
    for op, val in ops:
        if op == "=":
            old += val
        elif op == "-":
            if old <= pos < old + val:
                return old, old + val
            old += val
    return None


def _inside_deleted(ops: list, pos: int) -> bool:
    run = _deleted_run(ops, pos)
    return bool(run) and run[0] < pos < run[1]


def _replaced_runs(ops: list) -> list[tuple[int, int]]:
    out, old, i = [], 0, 0
    while i < len(ops):
        op, val = ops[i]
        if op == "=":
            old += val
            i += 1
            continue
        a, ins, j = old, False, i
        while j < len(ops) and ops[j][0] != "=":
            if ops[j][0] == "-":
                old += ops[j][1]
            else:
                ins = True
            j += 1
        if ins and old > a:
            out.append((a, old))
        i = j
    return out


def _ends(ops: list, s: int, e: int) -> tuple[bool, bool]:
    s_after, e_after = not _inside_deleted(ops, s), _inside_deleted(ops, e)
    for a, b in _replaced_runs(ops):
        if a == s and b <= e:
            s_after = False
        if b == e and a >= s:
            e_after = True
    return s_after, e_after


WIDE = 200
HOMES = 16


def _body_from(text: str) -> int:
    m = textdiff._FM.match(text or "")
    return m.end() if m else 0


def _text_key(text: str) -> str:
    return hashlib.sha1(text[_body_from(text):].encode("utf-8")).hexdigest()[:20]


def _remember(note: dict, text: str, s: int, e: int, key: str | None = None) -> None:
    b0 = _body_from(text)
    note["wide"] = {"b": text[max(b0, s - WIDE):s], "a": text[e:e + WIDE]}
    key = key or _text_key(text)
    homes = [h for h in note.get("homes") or [] if h.get("h") != key]
    homes.append({"h": key, "s": s - b0, "e": e - b0})
    note["homes"] = homes[-HOMES:]


def _holds(note: dict, text: str, s: int, e: int) -> bool:
    w = note.get("wide")
    if not w:
        a = note.get("anchor") or {}
        b, af = a.get("prefix", ""), a.get("suffix", "")
        return s - len(b) >= 0 and text[s - len(b):s] == b and text[e:e + len(af)] == af
    b, af = w.get("b", ""), w.get("a", "")
    b0 = _body_from(text)
    if s - len(b) < b0 or text[s - len(b):s] != b or text[e:e + len(af)] != af:
        return False
    if len(b) < WIDE and s - len(b) != b0:
        return False
    if len(af) < WIDE and e + len(af) != len(text):
        return False
    return True


def _twin_elsewhere(text: str, quote: str, s: int) -> bool:
    at = text.find(quote)
    while at >= 0:
        if at != s:
            return True
        at = text.find(quote, at + 1)
    return False


def _home(note: dict, text: str, key: str | None) -> dict | None:
    if not key:
        return None
    quote = (note.get("anchor") or {}).get("quote") or ""
    b0 = _body_from(text)
    for h in reversed(note.get("homes") or []):
        if h.get("h") == key:
            s, e = h["s"] + b0, h["e"] + b0
            if quote and text[s:e] == quote:
                return {"start": s, "end": e, "state": "attached"}
    return None


def _returned(note: dict, text: str, ops: list, p: int) -> dict | None:
    quote = (note.get("anchor") or {}).get("quote") or ""
    if not quote or not note.get("wide"):
        return None
    lo = hi = None
    for a, b in _hunks(ops):
        if a <= p <= b:
            lo, hi = a, b
            break
    if lo is None:
        return None
    hits = []
    at = text.find(quote, lo)
    while 0 <= at and at + len(quote) <= hi:
        if _holds(note, text, at, at + len(quote)):
            hits.append(at)
        at = text.find(quote, at + 1)
    if len(hits) == 1:
        return {"start": hits[0], "end": hits[0] + len(quote), "state": "attached"}
    return None


def _hunks(ops: list, glue: int = 64) -> list[tuple[int, int]]:
    out, new = [], 0
    for op, val in ops:
        if op == "=":
            new += val
        elif op == "+":
            a, b = new, new + len(val)
            if out and a - out[-1][1] < glue:
                out[-1] = (out[-1][0], b)
            else:
                out.append((a, b))
            new = b
    return out


def locate(note: dict, text: str, ops: list | None = None, guessed: bool = False,
           old: str | None = None, key: str | None = None) -> dict:
    if note.get("anchor") is None:
        return None
    if guessed:
        home = _home(note, text, key)
        if home:
            return home
    where = _where(note, text, ops, guessed, old)
    if guessed and where.get("start") is not None:
        quote = (note.get("anchor") or {}).get("quote") or ""
        s2, e2 = where["start"], where["end"]
        twin = _twin_elsewhere(text, quote, s2) or (old is not None and old.count(quote) > 1)
        if quote and twin and not _holds(note, text, s2, e2):
            return {"start": None, "end": None, "state": "detached", "pos": s2, "off": 0}
    if guessed and where.get("start") is None and ops and where.get("pos") is not None:
        back = _returned(note, text, ops, where["pos"])
        if back:
            return back
    return where


def _where(note: dict, text: str, ops: list | None = None, guessed: bool = False,
           old: str | None = None) -> dict:
    where = _carry(note, text, ops, old)
    if guessed and ops and old is not None:
        other = _carry(note, text, textdiff.lean_left(old, ops), old)
        if other["state"] == "attached" and where["state"] != "attached":
            return other
        if other["state"] == "attached" and other["start"] != where["start"]:
            a = note["anchor"]
            suf = a.get("suffix", "")
            score = lambda w: (anchors._score(text, a, w["start"], w["end"], w["start"]),
                               anchors._common_head(text[w["end"]:w["end"] + len(suf)], suf))
            if score(other) > score(where):
                return other
    return where


def _inserted(ops: list) -> list[tuple[int, int]]:
    out, new = [], 0
    for op, val in ops:
        if op == "=":
            new += val
        elif op == "+":
            out.append((new, new + len(val)))
            new += len(val)
    return out


def _carry(note: dict, text: str, ops: list | None = None, old: str | None = None) -> dict:
    a = note["anchor"]
    quote = a.get("quote") or ""
    w = note.get("where")
    if not w or (w.get("start") is None and w.get("pos") is None):
        p = a.get("pos")
        if quote and isinstance(p, int) and text[p:p + len(quote)] == quote:
            return {"start": p, "end": p + len(quote), "state": "attached"}
        return {"start": None, "end": None, "state": "detached"}
    if w.get("start") is None:
        off = w.get("off") or 0
        pos = w["pos"]
        gone = w.get("gone") or ""
        keep = {"start": None, "end": None, "state": "detached", "off": off, **({"gone": gone} if gone else {})}
        if not ops:
            return {**keep, "pos": pos}
        p = max(0, min(len(text), textdiff.map_pos(ops, pos, after=False)))
        ins = _inserted(ops)
        if ins and gone and quote:
            lo = max(0, p - len(gone))
            at = text.find(gone, lo, p + 2 * len(gone))
            while at >= 0 and at <= p + len(gone):
                b0, b1 = at, at + len(gone)
                st = at + off
                if any(x < b1 and b0 < y for x, y in ins) and text[st:st + len(quote)] == quote:
                    return {"start": st, "end": st + len(quote), "state": "attached"}
                at = text.find(gone, at + 1, p + 2 * len(gone))
        return {**keep, "pos": p}
    s, e = w["start"], w["end"]
    if ops:
        s_after, e_after = _ends(ops, s, e)
        rs, re_ = _deleted_run(ops, s), _deleted_run(ops, e)
        s_at = rs[0] if rs and rs[0] < s < rs[1] else s
        e_at = re_[1] if re_ and re_[0] < e < re_[1] else e
        s2 = textdiff.map_pos(ops, s_at, after=s_after if s_at == s else False)
        e2 = max(s2, textdiff.map_pos(ops, e_at, after=e_after or e_at != e))
    else:
        s2, e2 = s, e
    s2, e2 = max(0, min(len(text), s2)), max(0, min(len(text), e2))
    now = text[s2:e2]
    if now == quote:
        return {"start": s2, "end": e2, "state": "attached"}
    kept = _survived(ops, s, e) if ops else len(now)
    if not now.strip() or kept < SURVIVE * max(1, e - s):
        run = _deleted_run(ops, s) if ops else None
        at, off = (run[0], s - run[0]) if run else (s, 0)
        gone = old[run[0]:run[1]] if run and old is not None and run[1] - run[0] <= 50000 else ""
        return {"start": None, "end": None, "state": "detached",
                "pos": textdiff.map_pos(ops, at, after=False) if ops else s2, "off": off,
                **({"gone": gone} if gone else {})}
    return {"start": s2, "end": e2, "state": "changed"}


def relocate(sc: dict, text: str, ops: list | None = None, guessed: bool = False,
             old: str | None = None) -> bool:
    changed = False
    key = _text_key(text)
    for note in sc["threads"]:
        if note.get("anchor") is None or note["state"] == "accepted":
            continue
        before = copy.deepcopy((note.get("anchor"), note.get("found"), note.get("where"),
                                note.get("wide"), note.get("homes")))
        where = locate(note, text, ops, guessed, old, key=key)
        if where["state"] == "attached":
            note["anchor"] = anchors.make(text, where["start"], where["end"])
            _remember(note, text, where["start"], where["end"], key)
            note.pop("found", None)
        elif where["state"] == "changed":
            note["found"] = anchors.make(text, where["start"], where["end"])
        note["where"] = where
        if before != (note.get("anchor"), note.get("found"), note.get("where"),
                      note.get("wide"), note.get("homes")):
            changed = True
    return changed


# -- what a client is shown ----------------------------------------------------

def view(sc: dict, text: str | None = None) -> dict:
    notes = []
    for t in sc["threads"]:
        if t.get("anchor") is None:
            n = {k: v for k, v in t.items() if k not in ("found", "wide", "homes")}
            n["anchor"] = None
            n["where"] = None
            notes.append(n)
            continue
        w = dict(t.get("where") or {"state": "detached", "start": None, "end": None})
        if text is not None:
            for k in ("start", "end"):
                if isinstance(w.get(k), int):
                    w[k] = max(0, min(len(text), w[k]))
        n = {k: v for k, v in t.items() if k not in ("where", "found", "wide", "homes")}
        n["where"] = w
        if w["state"] == "changed" and t.get("found"):
            n["now"] = t["found"]["quote"]
        notes.append(n)
    return {"id": sc["id"], "notes": notes}


def history(sc: dict) -> list[dict]:
    if not sc.get("base"):
        return []
    rows = [{k: v for k, v in s.items() if k != "delta"} for s in reversed(sc["saves"])]
    base = sc["base"]
    rows.append({"id": "base", "at": base["at"], "author": base.get("author"),
                 "kind": "base", "revision": base["revision"],
                 "summary": {"added": 0, "removed": 0, "first": ""},
                 "folded": base.get("folded", 0)})
    return rows
