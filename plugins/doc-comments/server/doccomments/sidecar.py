"""The sidecar: one readable JSON document per commented doc.

It holds the doc's notes (comments and suggestions, their replies and
state, and what each points at), the record of its saves (who, when, what
changed, as deltas from the text the record began with), and, from step 2,
its share links. Everything here is a pure function of the sidecar and the
document's text; reading and writing them is the service's.

Shape (``version`` 1)::

    {"format": "loam-doc-notes", "version": 1, "id": "d-…",
     "doc": {"root": "vault", "path": "…/Essay.md"},
     "created": "…Z",
     "base": {"text": "…", "revision": "sha256:…", "at": "…Z", "author": {…}},
     "saves": [{"id": "s-…", "at": "…Z", "author": {…}, "kind": "editor",
                "revision": "sha256:…", "delta": [["=", 120], ["+", "new "]],
                "summary": {"added": 4, "removed": 0, "first": "new"}}],
     "threads": [{"id": "n-…", "kind": "comment" | "suggestion", …}],
     "links": []}

An author is ``{"id": "owner" | "agent:<name>", "name": "…"}``; step 2 adds
link visitors and their agents with ids of their own.
"""

from __future__ import annotations

import copy
import hashlib
import secrets
from datetime import datetime, timezone

from . import anchor as anchors
from . import textdiff

FORMAT = "loam-doc-notes"
VERSION = 1
KINDS = ("comment", "suggestion")
OPEN = "open"
CLOSED = ("resolved", "accepted", "rejected")
KEEP_SAVES = 400          # past this, the oldest saves fold into the base


class NoteError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def revision(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _id(prefix: str) -> str:
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return prefix + "".join(secrets.choice(alphabet) for _ in range(8))


def new(did: str, root: str, path: str, text: str, author: dict,
        now: str | None = None) -> dict:
    now = now or now_iso()
    return {"format": FORMAT, "version": VERSION, "id": did,
            "doc": {"root": root, "path": path}, "created": now,
            "base": {"text": text, "revision": revision(text), "at": now,
                     "author": author},
            "saves": [], "threads": [], "links": []}


def check(sc: dict) -> dict:
    if not isinstance(sc, dict) or sc.get("format") != FORMAT:
        raise NoteError(422, "that file is not a doc-notes sidecar")
    if sc.get("version") != VERSION:
        raise NoteError(422, f"sidecar version {sc.get('version')!r} is not one this plugin reads")
    sc.setdefault("saves", [])
    sc.setdefault("threads", [])
    sc.setdefault("links", [])
    return sc


# -- the save record -----------------------------------------------------------

def head_text(sc: dict) -> str:
    text = sc["base"]["text"]
    for s in sc["saves"]:
        text = textdiff.apply(text, s["delta"])
    return text


def head_revision(sc: dict) -> str:
    return sc["saves"][-1]["revision"] if sc["saves"] else sc["base"]["revision"]


def text_at(sc: dict, save_id: str) -> str:
    """The text as it was right after save ``save_id`` (``"base"`` for the
    text the record began with)."""
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
    """Append a save turning ``old`` (which must be the head) into
    ``new_text``. Nothing is recorded when nothing changed."""
    if old == new_text:
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
    """Fold all but the newest ``keep`` saves into the base."""
    drop = len(sc["saves"]) - keep
    if drop <= 0:
        return
    text = sc["base"]["text"]
    for s in sc["saves"][:drop]:
        text = textdiff.apply(text, s["delta"])
    last = sc["saves"][drop - 1]
    sc["base"] = {"text": text, "revision": last["revision"], "at": last["at"],
                  "author": last["author"], "folded": sc["base"].get("folded", 0) + drop}
    sc["saves"] = sc["saves"][drop:]


def authorship(sc: dict) -> list[dict]:
    """Who wrote each run of the head text: ``[{"start", "end", "author"}]``,
    worked out by replaying the record. The base text is its author's."""
    runs: list[list] = []   # [length, author id]
    base = sc["base"]
    if base["text"]:
        runs.append([len(base["text"]), base.get("author", {}).get("id", "owner")])
    for s in sc["saves"]:
        who = s["author"]["id"]
        out: list[list] = []
        i, used = 0, 0      # index into runs, chars of runs[i] consumed

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


# -- notes ----------------------------------------------------------------------

def find(sc: dict, tid: str) -> dict:
    for t in sc["threads"]:
        if t["id"] == tid:
            return t
    raise NoteError(404, f"no note {tid!r} on this doc")


def _body(text, what="the note", allow_empty=False) -> str:
    if not isinstance(text, str):
        raise NoteError(400, f"{what} is text")
    text = text.strip()
    if not text and not allow_empty:
        raise NoteError(400, f"{what} is empty")
    if len(text) > 20_000:
        raise NoteError(413, f"{what} is longer than 20,000 characters")
    return text


def add(sc: dict, text: str, kind: str, start: int, end: int, body: str,
        author: dict, replacement: str | None = None, now: str | None = None,
        **extra) -> dict:
    if kind not in KINDS:
        raise NoteError(400, "a note is a comment or a suggestion")
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
    sc["threads"].append(note)
    return note


def reply(sc: dict, tid: str, body: str, author: dict, now: str | None = None) -> dict:
    note = find(sc, tid)
    r = {"id": _id("r-"), "author": author, "created": now or now_iso(),
         "text": _body(body, "the reply")}
    note["replies"].append(r)
    return r


def edit_reply(sc: dict, tid: str, rid: str | None, body: str, author: dict) -> dict:
    """Change the words of one's own note (``rid`` None) or reply."""
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
    """Typing next to one's own open suggestion grows it: the new one (which
    covers it) takes its place, with its replies and its words if it has
    none of its own. Anyone else's suggestion, or a closed one, stays."""
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
        # Someone answered it: it closes (kept with its replies) rather than vanish.
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
    """Whether a note is one's own: written by oneself, or, for a visitor,
    by their own agent (``guest-agent:<link>:<name>`` beside ``guest:<link>:<name>``)."""
    if author_id == who_id:
        return True
    return who_id.startswith("guest:") and author_id == "guest-agent:" + who_id[len("guest:"):]


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
    """Undo a resolve or a reject (an accept is undone by History)."""
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
    """The doc's text with the suggestion applied. The caller saves it and
    records the save; the suggestion is marked accepted here."""
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
    # A file that ended with a line break keeps one (a suggestion typed on
    # the empty last line is anchored on the words before that break).
    if text.endswith("\n") and not new_text.endswith("\n"):
        new_text += "\r\n" if text.endswith("\r\n") else "\n"
    note["state"] = "accepted"
    note["closed"] = {"by": author, "at": now or now_iso()}
    note["where"] = {"state": "attached", "start": where["start"],
                     "end": where["start"] + len(note["replacement"])}
    return new_text


def reattach(sc: dict, text: str, tid: str, start: int, end: int) -> dict:
    note = find(sc, tid)
    if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text)):
        raise NoteError(400, "attach a note to a non-empty passage")
    note.setdefault("first_anchor", note["anchor"])
    note["anchor"] = anchors.make(text, start, end)
    note.pop("found", None)
    note["where"] = {"state": "attached", "start": start, "end": end}
    return note


# -- finding passages again ------------------------------------------------------

def _touched(ops: list, start: int, end: int) -> bool:
    """Whether the change ``ops`` alters anything strictly inside the old
    range [start, end): a deletion overlapping it, or an insertion inside it."""
    old = 0
    for op, val in ops:
        if op == "=":
            old += val
        elif op == "-":
            if old < end and old + val > start:
                return True
            old += val
        elif op == "+":
            if start < old < end:
                return True
    return False


SURVIVE = 0.5        # below this share of its own letters left, a passage is gone


def _survived(ops: list, start: int, end: int) -> int:
    """How many letters of the old range [start, end) the change kept."""
    old, kept = 0, 0
    for op, val in ops:
        if op == "=":
            kept += max(0, min(end, old + val) - max(start, old))
            old += val
        elif op == "-":
            old += val
    return kept


def _churn(ops: list, start: int, end: int) -> int:
    """How many letters a change put in or took out at or inside [start, end]."""
    old, n = 0, 0
    for op, val in ops:
        if op == "=":
            old += val
        elif op == "-":
            if old <= end and old + val >= start:
                n += val
            old += val
        elif op == "+":
            if start <= old <= end:
                n += len(val)
    return n


def locate(note: dict, text: str, ops: list | None = None, guessed: bool = False) -> dict:
    """Where a note's passage is in ``text``. Its place is **carried**, never
    searched for: ``ops`` is the change from the text its place was last
    known in (every change the server makes is known; a file changed on
    disk is worked out from the old and new stored text first), and the
    place moves through it exactly, the way the editor's drafts move
    through an edit. Then: the quote still there is attached; other words
    there are "changed since", there; nothing left (or almost nothing like
    it) is detached, remembering the carried point, so the note comes back
    if those exact words return there. A like passage elsewhere is never the
    note's (2026-11-17: a suggestion on one of several "remarkable"s moved
    to another on a word typed beside it, and Accept wrote there).

    Only a note that has never been placed (a sidecar from before places
    were kept) is found by searching."""
    a = note["anchor"]
    quote = a.get("quote") or ""
    w = note.get("where")
    if not w or (w.get("start") is None and w.get("pos") is None):
        if w and w.get("state") == "detached":
            return {"start": None, "end": None, "state": "detached"}
        return anchors.locate(text, a)
    if w.get("start") is None:
        # detached: its carried point; back when the exact words return there
        p = w["pos"] if not ops else textdiff.map_pos(ops, w["pos"], after=True)
        p = max(0, min(len(text), p))
        if quote and text[p:p + len(quote)] == quote:
            return {"start": p, "end": p + len(quote), "state": "attached"}
        return {"start": None, "end": None, "state": "detached", "pos": p}
    s, e = w["start"], w["end"]
    if ops:
        s2 = textdiff.map_pos(ops, s, after=True)
        e2 = max(s2, textdiff.map_pos(ops, e, after=False))
    else:
        s2, e2 = s, e
    s2, e2 = max(0, min(len(text), s2)), max(0, min(len(text), e2))
    now = text[s2:e2]
    if now == quote:
        return {"start": s2, "end": e2, "state": "attached"}
    if guessed and ops and quote:
        # A change nobody described (the file, edited on disk) is worked out
        # by comparing, which cannot tell two equal runs of words apart: the
        # note's exact words within the size of the change around its
        # carried place are still its own place.
        reach = min(400, _churn(ops, s, e))
        lo, hi = max(0, s2 - reach), min(len(text), e2 + reach)
        hits, at = [], text.find(quote, lo)
        while 0 <= at and at + len(quote) <= hi:
            hits.append(at)
            at = text.find(quote, at + 1)
        if hits:
            best = min(hits, key=lambda h: abs(h - s2))
            return {"start": best, "end": best + len(quote), "state": "attached"}
    # Most of the passage's own letters gone (a diff may keep a "the" or a
    # "Paragraph" of a deleted paragraph by aligning it with the next): gone.
    kept = _survived(ops, s, e) if ops else len(now)
    if not now.strip() or kept < SURVIVE * max(1, e - s):
        return {"start": None, "end": None, "state": "detached", "pos": s2}
    return {"start": s2, "end": e2, "state": "changed"}


def relocate(sc: dict, text: str, ops: list | None = None, guessed: bool = False) -> bool:
    """Find every note's passage in ``text``; ``ops`` is the delta from the
    text they were last found in, when it is known. Returns whether anything
    in the sidecar changed. An attached note's context is refreshed so it
    keeps up with the doc; a changed one keeps its original quote and
    remembers where its passage is now (``found``)."""
    changed = False
    for note in sc["threads"]:
        if note["state"] == "accepted":
            continue
        before = copy.deepcopy((note.get("anchor"), note.get("found"), note.get("where")))
        where = locate(note, text, ops, guessed)
        if where["state"] == "attached":
            note["anchor"] = anchors.make(text, where["start"], where["end"])
            note.pop("found", None)
        elif where["state"] == "changed":
            note["found"] = anchors.make(text, where["start"], where["end"])
        note["where"] = where
        if before != (note.get("anchor"), note.get("found"), note.get("where")):
            changed = True
    return changed


# -- what a client is shown --------------------------------------------------------

def view(sc: dict, text: str) -> dict:
    """The notes as a client draws them: each with where its passage is in
    ``text`` now. Assumes ``relocate`` has run against ``text``."""
    notes = []
    for t in sc["threads"]:
        w = dict(t.get("where") or {"state": "detached", "start": None, "end": None})
        # A closed note's place is where it was when it closed; the text may
        # have grown shorter since. Never past the end.
        for k in ("start", "end"):
            if isinstance(w.get(k), int):
                w[k] = max(0, min(len(text), w[k]))
        n = {k: v for k, v in t.items() if k not in ("where", "found")}
        n["where"] = w
        if w["state"] == "changed" and t.get("found"):
            n["now"] = t["found"]["quote"]
        notes.append(n)
    return {"id": sc["id"], "notes": notes}


def history(sc: dict) -> list[dict]:
    """The saves, newest first, without their deltas."""
    rows = [{k: v for k, v in s.items() if k != "delta"} for s in reversed(sc["saves"])]
    base = sc["base"]
    rows.append({"id": "base", "at": base["at"], "author": base.get("author"),
                 "kind": "base", "revision": base["revision"],
                 "summary": {"added": 0, "removed": 0, "first": ""},
                 "folded": base.get("folded", 0)})
    return rows
