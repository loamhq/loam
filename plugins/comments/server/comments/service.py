"""Unified operations for comments and suggestions on any vault file.

- Markdown files (``.md``, ``.markdown``): anchored comments and suggestions,
  accept/reject/withdraw/reattach, three-way merge saves, save history and
  authorship tracking — identical to the calm Markdown editor.
- All other file types (PDF, images, HTML, audio, video, code, archives):
  whole-file comments (same thread record with ``anchor: null``), replies,
  edit/delete own words, resolve and reopen; no suggestions and no edits to
  the target file.
"""

from __future__ import annotations

import json
import threading

from . import frontmatter as fm
from . import merge as merging
from . import sidecar as sc_mod
from . import textdiff
from .sidecar import NoteError
from .store import Conflict

RETRIES = 4
SIDECAR_MAX_BYTES = 4 * 1024 * 1024


def is_markdown(path: str | None) -> bool:
    return isinstance(path, str) and path.lower().endswith((".md", ".markdown"))


class Service:
    def __init__(self, store):
        self.store = store
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, root: str, path: str) -> threading.Lock:
        key = f"{root}\0{path}"
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    # -- permissions -----------------------------------------------------------

    GUEST_MD = {
        "view": {"view"},
        "comment": {"view", "comment", "suggest"},
        "edit": {"view", "comment", "suggest", "edit", "accept", "resolve_any"},
    }
    GUEST_FILE = {
        "view": {"view"},
        "comment": {"view", "comment"},
        "edit": {"view", "comment"},  # non-Markdown stays view/comment only
    }

    def may(self, who: dict, action: str, *, markdown: bool = True) -> bool:
        kind = who.get("kind")
        if kind in ("owner", "agent"):
            if not markdown and action in ("suggest", "edit", "accept", "restore"):
                return False
            return True
        if kind in ("guest", "guest-agent"):
            table = self.GUEST_MD if markdown else self.GUEST_FILE
            return action in table.get(who.get("mode"), set())
        return False

    def _need(self, who: dict, action: str, *, markdown: bool = True):
        if not self.may(who, action, markdown=markdown):
            raise NoteError(403, f"you may not {action} here")

    # -- loading ---------------------------------------------------------------

    def _author_of_outside_edit(self, text: str) -> dict:
        name = fm.last_modified_by(text)
        return self.store.person_named(name) or self.store.owner()

    def _load_md(self, root: str, path: str):
        """``(doc, sidecar, sidecar_revision, dirty)`` for a Markdown file."""
        self.store.sync_events()
        doc = self.store.read_doc(root, path)
        did = fm.doc_id(doc["text"])
        if not did:
            # Also check index in case a file was tracked before frontmatter id
            did = self.store.find_file_id(root, path)
        if not did:
            return doc, None, None, False
        got = self.store.read_sidecar(did)
        if got is None:
            return doc, None, None, False
        sc, sc_rev = got
        sc_mod.check(sc)
        dirty = False
        where = sc.get("file") or {}
        if (where.get("root"), where.get("path")) != (root, path):
            if self.store.exists_with_doc_id(where.get("root"), where.get("path"), did):
                doc["copy_of"] = {"root": where.get("root"), "path": where.get("path")}
                return doc, None, None, False
            sc["file"] = {**where, "root": root, "path": path}
            dirty = True
        if sc.get("base") is None:
            sc["base"] = {"text": doc["text"], "revision": doc["revision"],
                          "at": sc_mod.now_iso(), "author": self._author_of_outside_edit(doc["text"])}
            dirty = True
        elif sc_mod.head_revision(sc) != doc["revision"]:
            old = sc_mod.head_text(sc)
            sc_mod.record_save(sc, old, doc["text"],
                               self._author_of_outside_edit(doc["text"]), "outside",
                               at_modified=doc.get("modified"))
            sc_mod.relocate(sc, doc["text"], textdiff.delta(old, doc["text"]), guessed=True, old=old)
            dirty = True
        elif any(t.get("anchor") is not None and "where" not in t for t in sc["threads"]):
            sc_mod.relocate(sc, doc["text"])
            dirty = True
        return doc, sc, sc_rev, dirty

    def _load_other(self, root: str, path: str):
        """``(fp, sidecar, sidecar_revision, dirty)`` for any non-Markdown file."""
        fp = self.store.fingerprint(root, path)
        fid = self.store.find_file_id(root, path, fp=fp)
        if not fid:
            return fp, None, None, False
        got = self.store.read_sidecar(fid)
        if got is None:
            return fp, None, None, False
        sc, sc_rev = got
        sc_mod.check(sc)
        dirty = False
        fmeta = sc.get("file") or {}
        want = {"root": root, "path": path,
                **{k: fp[k] for k in ("dev", "ino", "sha256", "size") if fp.get(k) is not None}}
        if fmeta != want:
            sc["file"] = want
            dirty = True
        return fp, sc, sc_rev, dirty

    def _answer_md(self, who, doc, sc) -> dict:
        out = {"root": doc["root"], "path": doc["path"], "markdown": True,
               "text": doc["text"], "revision": doc["revision"], "me": who,
               "can": {a: self.may(who, a, markdown=True)
                       for a in ("comment", "suggest", "edit", "accept", "resolve_any", "restore")},
               "id": None, "notes": []}
        if doc.get("row"):
            out["row"] = doc["row"]
        if doc.get("copy_of"):
            out["copy_of"] = doc["copy_of"]
        if sc is not None:
            out.update(sc_mod.view(sc, doc["text"]))
            out["resolved"] = sum(1 for t in sc["threads"] if t["state"] != "open")
            out["notes_revision"] = sc_mod.revision(json.dumps(sc, ensure_ascii=False, indent=1) + "\n")
        return out

    def _answer_other(self, who, fp, sc) -> dict:
        file_rev = fp.get("sha256") or f"size:{fp.get('size', 0)}:{fp.get('modified', '')}"
        out = {"root": fp["root"], "path": fp["path"], "markdown": False,
               "name": fp.get("name") or fp["path"].rsplit("/", 1)[-1],
               "size": fp.get("size"), "media_type": fp.get("media_type"),
               "revision": file_rev, "me": who,
               "can": {a: self.may(who, a, markdown=False)
                       for a in ("comment", "suggest", "edit", "accept", "resolve_any", "restore")},
               "id": None, "notes": [], "resolved": 0}
        if sc is not None:
            out.update(sc_mod.view(sc, None))
            out["resolved"] = sum(1 for t in sc["threads"] if t["state"] != "open")
            out["notes_revision"] = sc_mod.revision(json.dumps(sc, ensure_ascii=False, indent=1) + "\n")
        return out

    # -- public reads ----------------------------------------------------------

    def get(self, who: dict, root: str, path: str) -> dict:
        md = is_markdown(path)
        self._need(who, "view", markdown=md)
        with self._lock(root, path):
            if md:
                doc, sc, sc_rev, dirty = self._load_md(root, path)
                if sc is not None and dirty:
                    self._write_sidecar(sc, sc_rev)
                return self._answer_md(who, doc, sc)
            fp, sc, sc_rev, dirty = self._load_other(root, path)
            if sc is not None and dirty:
                self._write_sidecar(sc, sc_rev)
            return self._answer_other(who, fp, sc)

    def revision(self, who: dict, root: str, path: str) -> dict:
        md = is_markdown(path)
        self._need(who, "view", markdown=md)
        if md:
            self.store.sync_events()
            doc = self.store.read_doc(root, path)
            notes = None
            did = fm.doc_id(doc["text"]) or self.store.find_file_id(root, path)
            if did:
                try:
                    notes = self.store.read_doc(self.store.root, self.store.sidecar_path(did))["revision"]
                except NoteError as exc:
                    if exc.status != 404:
                        raise
            return {"revision": doc["revision"], "notes": notes}
        fp = self.store.fingerprint(root, path)
        fid = self.store.find_file_id(root, path, fp=fp)
        notes = None
        if fid:
            try:
                notes = self.store.read_doc(self.store.root, self.store.sidecar_path(fid))["revision"]
            except NoteError as exc:
                if exc.status != 404:
                    raise
        file_rev = fp.get("sha256") or f"size:{fp.get('size', 0)}"
        return {"revision": file_rev, "notes": notes}

    def file_id_for(self, root: str, path: str) -> str | None:
        if is_markdown(path):
            try:
                doc = self.store.read_doc(root, path)
                did = fm.doc_id(doc["text"])
                if did:
                    return did
            except NoteError:
                pass
        return self.store.find_file_id(root, path)

    def summary(self, items: list[dict]) -> dict[str, dict]:
        """For each ``{root, path, since?}``, summarize open/resolved counts
        and new outsider activity since ``since`` (used by Sharing's Shared list)."""
        self.store.sync_events()
        owner_ids = {p["id"] for p in self.store.people().values()}
        out = {}
        for item in items:
            root, path = item.get("root"), item.get("path")
            if not isinstance(root, str) or not isinstance(path, str) or not path:
                continue
            key = f"{root}:{path}"
            since = item.get("since") or ""
            fid = self.file_id_for(root, path)
            if not fid:
                out[key] = {"id": None, "open": 0, "resolved": 0, "new": 0, "last": "", "people": []}
                continue
            got = self.store.read_sidecar(fid)
            if not got:
                out[key] = {"id": fid, "open": 0, "resolved": 0, "new": 0, "last": "", "people": []}
                continue
            sc = got[0]
            open_n = sum(1 for t in sc["threads"] if t["state"] == "open")
            closed_n = len(sc["threads"]) - open_n
            new = 0
            people = []
            last = sc.get("created", "")
            for t in sc["threads"]:
                for entry in [t] + t.get("replies", []):
                    a = entry.get("author") or {}
                    at = entry.get("created", "")
                    last = max(last, at)
                    if a.get("id") not in owner_ids:
                        if a.get("name") and a.get("name") not in people:
                            people.append(a["name"])
                        if at > since:
                            new += 1
            for s in sc.get("saves") or []:
                last = max(last, s.get("at", ""))
                a = s.get("author") or {}
                if a.get("id") not in owner_ids and s.get("at", "") > since:
                    new += 1
            out[key] = {"id": fid, "open": open_n, "resolved": closed_n,
                        "new": new, "last": last, "people": people[:6]}
        return out

    def sidecar_too_large(self, root: str, path: str) -> bool:
        fid = self.file_id_for(root, path)
        if not fid:
            return False
        got = self.store.read_sidecar(fid)
        return bool(got) and len(json.dumps(got[0])) > SIDECAR_MAX_BYTES

    # -- writing ---------------------------------------------------------------

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
        raise NoteError(409, "the file kept changing while this was saved; try again") from last

    def _ensure_md_sidecar(self, who, root, path, doc, sc, sc_rev):
        if sc is not None:
            return doc, sc, sc_rev, None
        fid = fm.new_id("f")
        text = doc["text"]
        if doc.get("copy_of"):
            text = fm.without_id(text)
        with_id = fm.with_id(text, fid)
        stored = self._write_doc(who, root, path, with_id, doc["revision"])
        ops = textdiff.delta(doc["text"], stored["text"])
        new_doc = {**doc, **stored}
        new_doc.pop("copy_of", None)
        try:
            fp = self.store.fingerprint(root, path)
        except NoteError:
            fp = None
        sc = sc_mod.new(fid, root, path, text=stored["text"],
                        author=self._author_of_outside_edit(doc["text"]),
                        fingerprint=fp)
        return new_doc, sc, None, ops

    def _ensure_other_sidecar(self, root, path, fp, sc, sc_rev):
        if sc is not None:
            return sc, sc_rev
        fid = fm.new_id("f")
        sc = sc_mod.new(fid, root, path, text=None, author=None, fingerprint=fp)
        return sc, None

    def _place(self, text: str, revision: str, args: dict) -> tuple[int, int]:
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
        md = is_markdown(path)
        kind = args.get("kind", "comment")
        if not md and kind == "suggestion":
            raise NoteError(400, "suggestions are for Markdown files; leave a comment instead")
        self._need(who, "suggest" if kind == "suggestion" else "comment", markdown=md)

        if not md:
            if args.get("quote") not in (None, ""):
                raise NoteError(400, "comments on non-Markdown files are on the whole file (omit quote)")

            def go_other():
                with self._lock(root, path):
                    fp, sc, sc_rev, _ = self._load_other(root, path)
                    sc, sc_rev = self._ensure_other_sidecar(root, path, fp, sc, sc_rev)
                    note = sc_mod.add(sc, None, "comment", None, None, args.get("text", ""), who)
                    self._write_sidecar(sc, sc_rev)
                    out = self._answer_other(who, fp, sc)
                    out["note"] = note["id"]
                    return out
            return self._with_retry(go_other)

        def go_md():
            with self._lock(root, path):
                doc, sc, sc_rev, _ = self._load_md(root, path)
                start, end = self._place(doc["text"], doc["revision"], args)
                doc2, sc, sc_rev, ops = self._ensure_md_sidecar(who, root, path, doc, sc, sc_rev)
                if ops:
                    start = textdiff.map_pos(ops, start, after=True)
                    end = max(start, textdiff.map_pos(ops, end, after=False))
                    sc_mod.relocate(sc, doc2["text"], ops, old=doc["text"])
                note = sc_mod.add(sc, doc2["text"], kind, start, end, args.get("text", ""),
                                  who, replacement=args.get("replacement"))
                if kind == "suggestion" and args.get("supersedes"):
                    sc_mod.supersede(sc, args["supersedes"], note, who)
                self._write_sidecar(sc, sc_rev)
                out = self._answer_md(who, doc2, sc)
                out["note"] = note["id"]
                return out
        return self._with_retry(go_md)

    def _note_op(self, who, root, path, fn) -> dict:
        md = is_markdown(path)

        def go():
            with self._lock(root, path):
                if md:
                    doc, sc, sc_rev, _ = self._load_md(root, path)
                    if sc is None:
                        raise NoteError(404, "this file has no notes")
                    fn(doc, sc)
                    self._write_sidecar(sc, sc_rev)
                    return self._answer_md(who, doc, sc)
                fp, sc, sc_rev, _ = self._load_other(root, path)
                if sc is None:
                    raise NoteError(404, "this file has no notes")
                fn(fp, sc)
                self._write_sidecar(sc, sc_rev)
                return self._answer_other(who, fp, sc)
        return self._with_retry(go)

    def reply(self, who, root, path, tid, body) -> dict:
        self._need(who, "comment", markdown=is_markdown(path))
        made = {}
        out = self._note_op(who, root, path, lambda d, sc: made.update(r=sc_mod.reply(sc, tid, body, who)))
        out["note"] = tid
        out["reply"] = made["r"]["id"]
        return out

    def edit(self, who, root, path, tid, rid, body) -> dict:
        return self._note_op(who, root, path, lambda d, sc: sc_mod.edit_reply(sc, tid, rid, body, who))

    def delete_reply(self, who, root, path, tid, rid) -> dict:
        return self._note_op(who, root, path, lambda d, sc: sc_mod.delete_reply(sc, tid, rid, who))

    def resolve(self, who, root, path, tid) -> dict:
        md = is_markdown(path)
        return self._note_op(who, root, path, lambda d, sc: sc_mod.resolve(
            sc, tid, who, self.may(who, "resolve_any", markdown=md)))

    def reopen(self, who, root, path, tid) -> dict:
        md = is_markdown(path)
        self._need(who, "view", markdown=md)
        if md:
            def go_unaccept():
                with self._lock(root, path):
                    doc, sc, sc_rev, _ = self._load_md(root, path)
                    if sc is None:
                        raise NoteError(404, "this doc has no notes")
                    if sc_mod.find(sc, tid)["state"] != "accepted":
                        return None
                    return self._unaccept(who, root, path, doc, sc, sc_rev, tid)
            undone = self._with_retry(go_unaccept)
            if undone is not None:
                return undone

        def fn(d, sc):
            note = sc_mod.find(sc, tid)
            if note["kind"] == "comment" and not self.may(who, "resolve_any", markdown=md) \
                    and not sc_mod.own(note["author"]["id"], who["id"]):
                raise NoteError(403, "you may reopen your own notes")
            if note["kind"] == "suggestion":
                self._need(who, "accept", markdown=md)
            sc_mod.reopen(sc, tid, who)
        return self._note_op(who, root, path, fn)

    def _unaccept(self, who, root, path, doc, sc, sc_rev, tid) -> dict:
        self._need(who, "accept", markdown=True)
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
        sc_mod.relocate(sc, stored["text"], ops)
        w = note.get("where") or {}
        start = textdiff.map_pos(ops, w.get("start") or 0, after=False)
        quote = note["anchor"]["quote"]
        if stored["text"][start:start + len(quote)] == quote:
            note["where"] = {"state": "attached", "start": start, "end": start + len(quote)}
        else:
            note["where"] = sc_mod.locate({**note, "where": None}, stored["text"])
        note["state"] = sc_mod.OPEN
        note["closed"] = None
        self._write_sidecar(sc, sc_rev)
        return self._answer_md(who, {**doc, **stored}, sc)

    def withdraw(self, who, root, path, tid) -> dict:
        self._need(who, "suggest", markdown=is_markdown(path))
        return self._note_op(who, root, path, lambda d, sc: sc_mod.withdraw(sc, tid, who))

    def remove(self, who, root, path, tid) -> dict:
        self._need(who, "comment", markdown=is_markdown(path))
        return self._note_op(who, root, path, lambda d, sc: sc_mod.remove(sc, tid, who))

    def reject(self, who, root, path, tid) -> dict:
        self._need(who, "accept", markdown=is_markdown(path))
        return self._note_op(who, root, path, lambda d, sc: sc_mod.reject(sc, tid, who))

    def reattach(self, who, root, path, tid, args) -> dict:
        if not is_markdown(path):
            raise NoteError(400, "whole-file comments are not attached to a passage")
        self._need(who, "comment", markdown=True)

        def fn(doc, sc):
            start, end = self._place(doc["text"], doc["revision"], args)
            sc_mod.reattach(sc, doc["text"], tid, start, end)
        return self._note_op(who, root, path, fn)

    def accept(self, who, root, path, tid) -> dict:
        if not is_markdown(path):
            raise NoteError(400, "suggestions are for Markdown files only")
        self._need(who, "accept", markdown=True)

        def go():
            with self._lock(root, path):
                doc, sc, sc_rev, _ = self._load_md(root, path)
                if sc is None:
                    raise NoteError(404, "this doc has no notes")
                note = sc_mod.find(sc, tid)
                w = dict(note.get("where") or {})
                new_text = sc_mod.accept(sc, doc["text"], tid, who)
                stored = self._write_doc(who, root, path, new_text, doc["revision"])
                sc_mod.record_save(sc, doc["text"], stored["text"], note["author"], "accept",
                                   by=who, note=tid)
                done = sc_mod.find(sc, tid)["where"]
                exact = [["=", done["start"]], ["-", (w.get("end") or 0) - (w.get("start") or 0)],
                         ["+", note["replacement"]], ["=", len(doc["text"]) - (w.get("end") or 0)]]
                exact = [o for o in exact if o[1]]
                try:
                    if textdiff.apply(doc["text"], exact) != new_text:
                        raise ValueError
                    sc_mod.relocate(sc, new_text, exact, old=doc["text"])
                    if stored["text"] != new_text:
                        sc_mod.relocate(sc, stored["text"], textdiff.delta(new_text, stored["text"]))
                except ValueError:
                    sc_mod.relocate(sc, stored["text"], textdiff.delta(doc["text"], stored["text"]), guessed=True, old=doc["text"])
                self._write_sidecar(sc, sc_rev)
                return self._answer_md(who, {**doc, **stored}, sc)
        return self._with_retry(go)

    # -- saving: three-way merge (Markdown only) -------------------------------

    def save(self, who: dict, root: str, path: str, args: dict) -> dict:
        if not is_markdown(path):
            raise NoteError(400, "collaborative saves are for Markdown files only")
        self._need(who, "edit", markdown=True)
        base_rev = args.get("base_revision")
        text = args.get("text")
        base_text = args.get("base_text")
        if not isinstance(text, str) or not isinstance(base_rev, str):
            raise NoteError(400, "a save is {base_revision, base_text, text}")

        def go():
            with self._lock(root, path):
                doc, sc, sc_rev, dirty = self._load_md(root, path)
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
                    out = self._answer_md(who, doc, sc)
                    out.update(merged_with_others=doc["revision"] != base_rev, conflicts=0)
                    return out
                id_ops = None
                if conflicts and sc is None:
                    fid = fm.new_id("f")
                    with_id = fm.with_id(merged, fid)
                    id_ops = textdiff.delta(merged, with_id)
                    merged = with_id
                    sc = sc_mod.new(fid, root, path, text=current,
                                    author=self._author_of_outside_edit(current))
                    sc_rev = None
                stored = self._write_doc(who, root, path, merged, doc["revision"])
                if sc is not None:
                    sc_mod.record_save(sc, current, stored["text"], who, "editor",
                                       merged_with=None if doc["revision"] == base_rev else doc["revision"])
                    exact = textdiff.from_changes(current, args.get("changes")) \
                        if doc["revision"] == base_rev and not id_ops else None
                    if exact is not None and textdiff.apply(current, exact) == merged:
                        sc_mod.relocate(sc, merged, exact, old=current)
                        if stored["text"] != merged:
                            sc_mod.relocate(sc, stored["text"], textdiff.delta(merged, stored["text"]))
                    else:
                        sc_mod.relocate(sc, stored["text"], textdiff.delta(current, stored["text"]), guessed=True, old=current)
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
                out = self._answer_md(who, {**doc, **stored}, sc)
                out.update(merged_with_others=doc["revision"] != base_rev,
                           conflicts=len(conflicts))
                return out
        return self._with_retry(go)

    def _text_at_revision(self, sc, rev):
        if sc is None or not sc.get("base"):
            return None
        if sc["base"]["revision"] == rev:
            return sc["base"]["text"]
        text = sc["base"]["text"]
        for s in sc["saves"]:
            text = textdiff.apply(text, s["delta"])
            if s["revision"] == rev:
                return text
        return None

    # -- history (Markdown only) -----------------------------------------------

    def history(self, who, root, path) -> dict:
        if not is_markdown(path):
            return {"saves": [], "authors": [], "people": self.store.people()}
        self._need(who, "view", markdown=True)
        with self._lock(root, path):
            doc, sc, sc_rev, dirty = self._load_md(root, path)
            if sc is None:
                return {"saves": [], "authors": [], "people": self.store.people()}
            if dirty:
                self._write_sidecar(sc, sc_rev)
            return {"saves": sc_mod.history(sc), "authors": sc_mod.authorship(sc),
                    "revision": doc["revision"], "people": self.store.people()}

    def version(self, who, root, path, save_id) -> dict:
        if not is_markdown(path):
            raise NoteError(404, "this file has no text history")
        self._need(who, "view", markdown=True)
        with self._lock(root, path):
            doc, sc, _, _ = self._load_md(root, path)
            if sc is None:
                raise NoteError(404, "this doc has no history yet")
            return {"text": sc_mod.text_at(sc, save_id), "save": save_id}

    def restore(self, who, root, path, save_id) -> dict:
        if not is_markdown(path):
            raise NoteError(400, "history restore is for Markdown files only")
        self._need(who, "restore", markdown=True)

        def go():
            with self._lock(root, path):
                doc, sc, sc_rev, _ = self._load_md(root, path)
                if sc is None:
                    raise NoteError(404, "this doc has no history yet")
                old = sc_mod.text_at(sc, save_id)
                fid = sc["id"]
                if fm.doc_id(old) != fid:
                    old = fm.with_id(old, fid)
                if old == doc["text"]:
                    return self._answer_md(who, doc, sc)
                stored = self._write_doc(who, root, path, old, doc["revision"])
                ops = textdiff.delta(doc["text"], stored["text"])
                sc_mod.record_save(sc, doc["text"], stored["text"], who, "restore",
                                   restored=save_id)
                sc_mod.relocate(sc, stored["text"], ops, guessed=True, old=doc["text"])
                self._write_sidecar(sc, sc_rev)
                return self._answer_md(who, {**doc, **stored}, sc)
        return self._with_retry(go)
