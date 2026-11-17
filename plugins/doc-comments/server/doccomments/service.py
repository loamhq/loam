"""The operations on a doc's notes, independent of how they are called.

The page calls them over HTTP through the plugin's address, a persona's
tools call them over the same HTTP on loopback, and step 2's share links
will call them for a visitor. Each takes ``who`` (an author: ``{"id",
"name", "kind"}``, kind ``owner`` or ``agent``) and the doc's ``root`` and
``path``, and answers a plain dict. Errors are ``NoteError`` with an HTTP
status and a sentence.

Storage is a ``Store`` (``store.py``): the doc's text and revision, and the
sidecar by id. Every write names the revision it was made against, so two
writers never overwrite each other silently; a lost race is read again and
redone, up to a few times.
"""

from __future__ import annotations

import threading

from . import frontmatter as fm
from . import merge as merging
from . import sidecar as sc_mod
from . import textdiff
from .sidecar import NoteError

from .store import Conflict

RETRIES = 4


class Service:
    def __init__(self, store):
        self.store = store
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # -- locking: one operation at a time per doc ---------------------------------

    def _lock(self, root: str, path: str) -> threading.Lock:
        key = f"{root}\0{path}"
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    # -- permissions ----------------------------------------------------------------
    # One place, so step 2 can add a link's mode (view / comment / edit).

    # What a share link's visitor (kind `guest`) and their agent
    # (`guest-agent`) may do, by the link's mode. Restoring a version and
    # anything about links are the owner's and the personas' alone.
    GUEST = {"view": {"view"},
             "comment": {"view", "comment", "suggest"},
             "edit": {"view", "comment", "suggest", "edit", "accept", "resolve_any"}}

    def may(self, who: dict, action: str, note: dict | None = None) -> bool:
        kind = who.get("kind")
        if kind in ("owner", "agent"):
            return True
        if kind in ("guest", "guest-agent"):
            return action in self.GUEST.get(who.get("mode"), set())
        return False

    def _need(self, who, action, note=None):
        if not self.may(who, action, note):
            raise NoteError(403, f"you may not {action} here")

    # -- reading ----------------------------------------------------------------------

    def _author_of_outside_edit(self, text: str) -> dict:
        """Who made an edit outside the editor: the persona its frontmatter's
        ``last_modified_by`` names, else the owner."""
        name = fm.last_modified_by(text)
        return self.store.person_named(name) or self.store.owner()

    def _load(self, root: str, path: str):
        """``(doc, sidecar, sidecar_revision, dirty)`` with outside edits
        recorded and every note's passage found in the current text. The
        sidecar is None for a doc with no notes."""
        doc = self.store.read_doc(root, path)
        did = fm.doc_id(doc["text"])
        if not did:
            return doc, None, None, False
        got = self.store.read_sidecar(did)
        if got is None:
            return doc, None, None, False
        sc, sc_rev = got
        sc_mod.check(sc)
        dirty = False
        where = sc.get("doc") or {}
        if (where.get("root"), where.get("path")) != (root, path):
            if self._still_there(where, did):
                # A copy of a commented doc carries its id but not its notes.
                doc["copy_of"] = where
                return doc, None, None, False
            sc["doc"] = {"root": root, "path": path}
            dirty = True
        if sc_mod.head_revision(sc) != doc["revision"]:
            old = sc_mod.head_text(sc)
            sc_mod.record_save(sc, old, doc["text"],
                               self._author_of_outside_edit(doc["text"]), "outside",
                               at_modified=doc.get("modified"))
            sc_mod.relocate(sc, doc["text"], textdiff.delta(old, doc["text"]))
            dirty = True
        elif any("where" not in t for t in sc["threads"]):
            sc_mod.relocate(sc, doc["text"])
            dirty = True
        return doc, sc, sc_rev, dirty

    def _still_there(self, where: dict, did: str) -> bool:
        if not where.get("root") or not where.get("path"):
            return False
        try:
            other = self.store.read_doc(where["root"], where["path"])
        except NoteError:
            return False
        return fm.doc_id(other["text"]) == did

    def _answer(self, who, doc, sc) -> dict:
        out = {"root": doc["root"], "path": doc["path"], "text": doc["text"],
               "revision": doc["revision"], "me": who,
               "can": {a: self.may(who, a) for a in ("comment", "suggest", "edit", "accept", "resolve_any", "restore")},
               "id": None, "notes": []}
        if doc.get("row"):
            out["row"] = doc["row"]
        if doc.get("copy_of"):
            out["copy_of"] = doc["copy_of"]
        if sc is not None:
            out.update(sc_mod.view(sc, doc["text"]))
            out["resolved"] = sum(1 for t in sc["threads"] if t["state"] != "open")
        return out

    def get(self, who: dict, root: str, path: str) -> dict:
        self._need(who, "view")
        with self._lock(root, path):
            doc, sc, sc_rev, dirty = self._load(root, path)
            if sc is not None and dirty:
                self._write_sidecar(sc, sc_rev)
            return self._answer(who, doc, sc)

    def revision(self, who: dict, root: str, path: str) -> dict:
        """Only the doc's revision and its notes' (the sidecar's): what an
        open editor asks every second or two, to take in a change made on
        disk, or a note, reply or close from anyone, without reading it all."""
        self._need(who, "view")
        doc = self.store.read_doc(root, path)
        notes = None
        did = fm.doc_id(doc["text"])
        if did:
            try:
                notes = self.store.read_doc(self.store.root, self.store.sidecar_path(did))["revision"]
            except NoteError as exc:
                if exc.status != 404:
                    raise
        return {"revision": doc["revision"], "notes": notes}

    # -- writing ----------------------------------------------------------------------

    def _write_sidecar(self, sc: dict, rev):
        return self.store.write_sidecar(sc["id"], sc, rev)

    def _write_doc(self, who: dict, root: str, path: str, text: str, base: str) -> dict:
        return self.store.write_doc(root, path, text, base,
                                    by=who["id"] if who.get("kind") == "agent" else None)

    def _with_retry(self, fn):
        last = None
        for _ in range(RETRIES):
            try:
                return fn()
            except Conflict as exc:
                last = exc
        raise NoteError(409, "the doc kept changing while this was saved; try again") from last

    def _ensure_sidecar(self, who, root, path, doc, sc, sc_rev):
        """A doc's first note: its id line is written into the doc (one
        write, against the revision read), and its sidecar made. Returns the
        doc as now stored, the sidecar, its revision and the delta the id
        line made."""
        if sc is not None:
            return doc, sc, sc_rev, None
        did = fm.new_id()
        text = doc["text"]
        if doc.get("copy_of"):
            text = fm.without_id(text)
        with_id = fm.with_id(text, did)
        stored = self._write_doc(who, root, path, with_id, doc["revision"])
        ops = textdiff.delta(doc["text"], stored["text"])
        new_doc = {**doc, **stored}
        new_doc.pop("copy_of", None)
        sc = sc_mod.new(did, root, path, stored["text"],
                        self._author_of_outside_edit(doc["text"]))
        return new_doc, sc, None, ops

    def _place(self, text: str, revision: str, args: dict) -> tuple[int, int]:
        """Where a new note goes: ``start``/``end`` when the client read this
        revision; else the ``quote`` found again (near ``start`` if given,
        or the ``occurrence``-th, counting from 1, or the one whose
        ``prefix`` matches)."""
        start, end = args.get("start"), args.get("end")
        quote = args.get("quote")
        if args.get("revision") == revision and isinstance(start, int) and isinstance(end, int):
            if quote is not None and text[start:end] != quote:
                raise NoteError(409, "that passage is not where the note says it is")
            return start, end
        if not isinstance(quote, str) or not quote:
            raise NoteError(409, "the doc has changed since you read it; "
                                 "read it again or name the passage by its words")
        hits, at = [], text.find(quote)
        while at >= 0:
            hits.append(at)
            at = text.find(quote, at + 1)
        if not hits:
            raise NoteError(404, "those words are not in the doc as it is now")
        occ = args.get("occurrence")
        prefix = args.get("prefix")
        if isinstance(occ, int):
            if not 1 <= occ <= len(hits):
                raise NoteError(404, f"those words occur {len(hits)} time(s)")
            s = hits[occ - 1]
        elif isinstance(prefix, str) and prefix:
            match = [h for h in hits if text[:h].endswith(prefix)]
            if len(match) != 1:
                raise NoteError(409, f"{len(match)} places have those words after that prefix")
            s = match[0]
        elif len(hits) == 1:
            s = hits[0]
        elif isinstance(start, int):
            s = min(hits, key=lambda h: abs(h - start))
        else:
            raise NoteError(409, f"those words occur {len(hits)} times; say which "
                                 "(occurrence, counting from 1, or the words just before them)")
        return s, s + len(quote)

    def add(self, who: dict, root: str, path: str, args: dict) -> dict:
        kind = args.get("kind", "comment")
        self._need(who, "suggest" if kind == "suggestion" else "comment")

        def go():
            with self._lock(root, path):
                doc, sc, sc_rev, _ = self._load(root, path)
                start, end = self._place(doc["text"], doc["revision"], args)
                doc2, sc, sc_rev, ops = self._ensure_sidecar(who, root, path, doc, sc, sc_rev)
                if ops:
                    # A passage at the very top stays after the id line a
                    # doc's first note puts there, never takes it in.
                    start = textdiff.map_pos(ops, start, after=True)
                    end = max(start, textdiff.map_pos(ops, end, after=False))
                    sc_mod.relocate(sc, doc2["text"])
                note = sc_mod.add(sc, doc2["text"], kind, start, end, args.get("text", ""),
                                  who, replacement=args.get("replacement"))
                if kind == "suggestion" and args.get("supersedes"):
                    sc_mod.supersede(sc, args["supersedes"], note, who)
                self._write_sidecar(sc, sc_rev)
                out = self._answer(who, doc2, sc)
                out["note"] = note["id"]
                return out
        return self._with_retry(go)

    def _note_op(self, who, root, path, fn) -> dict:
        def go():
            with self._lock(root, path):
                doc, sc, sc_rev, _ = self._load(root, path)
                if sc is None:
                    raise NoteError(404, "this doc has no notes")
                fn(doc, sc)
                self._write_sidecar(sc, sc_rev)
                return self._answer(who, doc, sc)
        return self._with_retry(go)

    def reply(self, who, root, path, tid, body) -> dict:
        self._need(who, "comment")
        return self._note_op(who, root, path, lambda d, sc: sc_mod.reply(sc, tid, body, who))

    def edit(self, who, root, path, tid, rid, body) -> dict:
        return self._note_op(who, root, path, lambda d, sc: sc_mod.edit_reply(sc, tid, rid, body, who))

    def delete_reply(self, who, root, path, tid, rid) -> dict:
        return self._note_op(who, root, path, lambda d, sc: sc_mod.delete_reply(sc, tid, rid, who))

    def resolve(self, who, root, path, tid) -> dict:
        return self._note_op(who, root, path, lambda d, sc: sc_mod.resolve(
            sc, tid, who, self.may(who, "resolve_any")))

    def reopen(self, who, root, path, tid) -> dict:
        def go_unaccept():
            with self._lock(root, path):
                doc, sc, sc_rev, _ = self._load(root, path)
                if sc is None:
                    raise NoteError(404, "this doc has no notes")
                if sc_mod.find(sc, tid)["state"] != "accepted":
                    return None
                return self._unaccept(who, root, path, doc, sc, sc_rev, tid)
        # Reopening an accepted suggestion is its Undo: only while its accept
        # is still the doc's last change (the editor's toast offers it then).
        self._need(who, "view")
        undone = self._with_retry(go_unaccept)
        if undone is not None:
            return undone

        def fn(d, sc):
            note = sc_mod.find(sc, tid)
            if note["kind"] == "comment" and not self.may(who, "resolve_any") \
                    and note["author"]["id"] != who["id"]:
                raise NoteError(403, "you may reopen your own notes")
            if note["kind"] == "suggestion":
                self._need(who, "accept")
            sc_mod.reopen(sc, tid, who)
        return self._note_op(who, root, path, fn)

    def _unaccept(self, who, root, path, doc, sc, sc_rev, tid) -> dict:
        """Undo an accept that is still the doc's last change: the text goes
        back to what it was just before, as a save of its own, and the
        suggestion is open again. Later than that, History restores."""
        self._need(who, "accept")
        last = sc["saves"][-1] if sc["saves"] else None
        if not last or last.get("kind") != "accept" or last.get("note") != tid \
                or last["revision"] != doc["revision"]:
            raise NoteError(409, "the doc has changed since that was accepted; "
                                 "History can restore the text from before it")
        before = sc_mod.text_at(sc, sc["saves"][-2]["id"]) if len(sc["saves"]) > 1 else sc["base"]["text"]
        stored = self._write_doc(who, root, path, before, doc["revision"])
        ops = textdiff.delta(doc["text"], stored["text"])
        sc_mod.record_save(sc, doc["text"], stored["text"], who, "restore", undid=last["id"])
        note = sc_mod.find(sc, tid)
        note["state"] = sc_mod.OPEN
        note["closed"] = None
        sc_mod.relocate(sc, stored["text"], ops)
        self._write_sidecar(sc, sc_rev)
        return self._answer(who, {**doc, **stored}, sc)

    def withdraw(self, who, root, path, tid) -> dict:
        """Take back one's own open suggestion entirely (Suggesting, when the
        words typed are deleted again): it leaves no trace in Resolved."""
        self._need(who, "suggest")
        return self._note_op(who, root, path, lambda d, sc: sc_mod.withdraw(sc, tid, who))

    def remove(self, who, root, path, tid) -> dict:
        """Delete one's own comment, while nobody else has replied to it."""
        self._need(who, "comment")
        return self._note_op(who, root, path, lambda d, sc: sc_mod.remove(sc, tid, who))

    def reject(self, who, root, path, tid) -> dict:
        self._need(who, "accept")
        return self._note_op(who, root, path, lambda d, sc: sc_mod.reject(sc, tid, who))

    def reattach(self, who, root, path, tid, args) -> dict:
        self._need(who, "comment")

        def fn(doc, sc):
            start, end = self._place(doc["text"], doc["revision"], args)
            sc_mod.reattach(sc, doc["text"], tid, start, end)
        return self._note_op(who, root, path, fn)

    def accept(self, who, root, path, tid) -> dict:
        self._need(who, "accept")

        def go():
            with self._lock(root, path):
                doc, sc, sc_rev, _ = self._load(root, path)
                if sc is None:
                    raise NoteError(404, "this doc has no notes")
                note = sc_mod.find(sc, tid)
                new_text = sc_mod.accept(sc, doc["text"], tid, who)
                stored = self._write_doc(who, root, path, new_text, doc["revision"])
                ops = textdiff.delta(doc["text"], stored["text"])
                sc_mod.record_save(sc, doc["text"], stored["text"], note["author"], "accept",
                                   by=who, note=tid)
                sc_mod.relocate(sc, stored["text"], ops)
                self._write_sidecar(sc, sc_rev)
                return self._answer(who, {**doc, **stored}, sc)
        return self._with_retry(go)

    # -- saving: three-way merge -----------------------------------------------------

    def save(self, who: dict, root: str, path: str, args: dict) -> dict:
        """``{base_revision, base_text, text}``: the editor's text, made from
        ``base_text`` at ``base_revision``. Lands whole when the doc has not
        moved; merged with what is there when it has, the first save
        standing where both changed one passage and ours becoming a
        suggestion there."""
        self._need(who, "edit")
        base_rev = args.get("base_revision")
        text = args.get("text")
        base_text = args.get("base_text")
        if not isinstance(text, str) or not isinstance(base_rev, str):
            raise NoteError(400, "a save is {base_revision, base_text, text}")

        def go():
            with self._lock(root, path):
                doc, sc, sc_rev, dirty = self._load(root, path)
                current = doc["text"]
                if doc["revision"] == base_rev:
                    merged, conflicts = text, []
                else:
                    btext = base_text
                    if not isinstance(btext, str) or sc_mod.revision(btext) != base_rev:
                        btext = self._text_at_revision(sc, base_rev)
                    if btext is None:
                        raise NoteError(409, "the doc changed and the text this edit began "
                                             "from is not known; read it again")
                    merged, conflicts = merging.merge3(btext, text, current)
                if merged == current and not conflicts:
                    if sc is not None and dirty:
                        self._write_sidecar(sc, sc_rev)
                    out = self._answer(who, doc, sc)
                    out.update(merged_with_others=doc["revision"] != base_rev, conflicts=0)
                    return out
                id_ops = None
                if conflicts and sc is None:
                    # Somewhere to keep the suggestions: the doc's first id line,
                    # added in the same write.
                    did = fm.new_id()
                    with_id = fm.with_id(merged, did)
                    id_ops = textdiff.delta(merged, with_id)
                    merged = with_id
                    sc = sc_mod.new(did, root, path, current, self._author_of_outside_edit(current))
                    sc_rev = None
                stored = self._write_doc(who, root, path, merged, doc["revision"])
                if sc is not None:
                    ops = textdiff.delta(current, stored["text"])
                    sc_mod.record_save(sc, current, stored["text"], who, "editor",
                                       merged_with=None if doc["revision"] == base_rev else doc["revision"])
                    sc_mod.relocate(sc, stored["text"], ops)
                    to_stored = textdiff.delta(merged, stored["text"])
                    for c in conflicts:
                        s, e = c["start"], c["end"]
                        if id_ops:
                            s, e = textdiff.map_pos(id_ops, s), textdiff.map_pos(id_ops, e)
                        s, e = textdiff.map_pos(to_stored, s), textdiff.map_pos(to_stored, e)
                        s, e, ours = merging.widen(stored["text"], s, e, c["ours"])
                        if e <= s or stored["text"][s:e] == ours:
                            continue
                        sc_mod.add(sc, stored["text"], "suggestion", s, e, "", who,
                                   replacement=ours, origin="conflict")
                    self._write_sidecar(sc, sc_rev)
                out = self._answer(who, {**doc, **stored}, sc)
                out.update(merged_with_others=doc["revision"] != base_rev,
                           conflicts=len(conflicts))
                return out
        return self._with_retry(go)

    def _text_at_revision(self, sc, rev):
        if sc is None:
            return None
        if sc["base"]["revision"] == rev:
            return sc["base"]["text"]
        text = sc["base"]["text"]
        for s in sc["saves"]:
            text = textdiff.apply(text, s["delta"])
            if s["revision"] == rev:
                return text
        return None

    # -- history -----------------------------------------------------------------------

    def history(self, who, root, path) -> dict:
        self._need(who, "view")
        with self._lock(root, path):
            doc, sc, sc_rev, dirty = self._load(root, path)
            if sc is None:
                return {"saves": [], "authors": [], "people": self.store.people()}
            if dirty:
                self._write_sidecar(sc, sc_rev)
            return {"saves": sc_mod.history(sc), "authors": sc_mod.authorship(sc),
                    "revision": doc["revision"], "people": self.store.people()}

    def version(self, who, root, path, save_id) -> dict:
        self._need(who, "view")
        with self._lock(root, path):
            doc, sc, _, _ = self._load(root, path)
            if sc is None:
                raise NoteError(404, "this doc has no history yet")
            return {"text": sc_mod.text_at(sc, save_id), "save": save_id}

    def restore(self, who, root, path, save_id) -> dict:
        self._need(who, "restore")

        def go():
            with self._lock(root, path):
                doc, sc, sc_rev, _ = self._load(root, path)
                if sc is None:
                    raise NoteError(404, "this doc has no history yet")
                old = sc_mod.text_at(sc, save_id)
                # The id line stays: a restore never orphans the notes.
                did = sc["id"]
                if fm.doc_id(old) != did:
                    old = fm.with_id(old, did)
                if old == doc["text"]:
                    return self._answer(who, doc, sc)
                stored = self._write_doc(who, root, path, old, doc["revision"])
                ops = textdiff.delta(doc["text"], stored["text"])
                sc_mod.record_save(sc, doc["text"], stored["text"], who, "restore",
                                   restored=save_id)
                sc_mod.relocate(sc, stored["text"], ops)
                self._write_sidecar(sc, sc_rev)
                return self._answer(who, {**doc, **stored}, sc)
        return self._with_retry(go)

    # -- share links: the sidecar's side ---------------------------------------------------

    def ensure_id(self, who: dict, root: str, path: str) -> str:
        """The doc's id, giving it one (and its sidecar) if it has none: a
        link points at a doc by its id, so it follows the doc's moves."""
        self._need(who, "restore")

        def go():
            with self._lock(root, path):
                doc, sc, sc_rev, dirty = self._load(root, path)
                if sc is not None:
                    if dirty:
                        self._write_sidecar(sc, sc_rev)
                    return sc["id"]
                doc2, sc, sc_rev, _ = self._ensure_sidecar(who, root, path, doc, sc, sc_rev)
                self._write_sidecar(sc, sc_rev)
                return sc["id"]
        return self._with_retry(go)

    def sync_links(self, did: str, public: list[dict]) -> None:
        """Write what anyone reading the vault may see of a doc's links into
        its sidecar (never a token, hash or key)."""
        def go():
            got = self.store.read_sidecar(did)
            if not got:
                return
            sc, rev = got
            if sc.get("links") == public:
                return
            sc["links"] = public
            self._write_sidecar(sc, rev)
        self._with_retry(go)

    def shared(self, dids: list[str], seen: dict) -> list[dict]:
        """Shared Docs: each doc with a link on, its title and path, and how
        much is new since the owner last opened it."""
        out = []
        owner_ids = {p["id"] for p in self.store.people().values()}
        for did in dids:
            got = self.store.read_sidecar(did)
            if not got:
                continue
            sc = got[0]
            where = sc.get("doc") or {}
            since = seen.get(did) or ""
            new = 0
            people = []
            last = sc.get("created", "")
            for t in sc["threads"]:
                for item in [t] + t.get("replies", []):
                    a = item.get("author") or {}
                    at = item.get("created", "")
                    last = max(last, at)
                    if a.get("id") not in owner_ids:
                        if a.get("name") and a.get("name") not in people:
                            people.append(a.get("name"))
                        if at > since:
                            new += 1
            for s in sc["saves"]:
                last = max(last, s.get("at", ""))
                a = s.get("author") or {}
                if a.get("id") not in owner_ids and s.get("at", "") > since:
                    new += 1
            out.append({"id": did, "root": where.get("root"), "path": where.get("path"),
                        "links": sc.get("links", []), "new": new, "people": people[:6], "last": last})
        out.sort(key=lambda d: d["last"], reverse=True)
        return out
