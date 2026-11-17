#!/usr/bin/env python3
"""Where a note sits after the text changes (2026-11-17, round 4 of the
walk): a note's place is carried through every change, never searched for,
on a doc that repeats itself on purpose (the same sentences in each section,
tables with equal cells). What must hold:

- a change outside a note's passage never changes which words it is on;
- a change inside leaves it there (marked changed) or detaches it, never
  moves it to other words;
- Accept writes into the passage the person saw marked, or refuses.

The walk's four cases are named tests; then seeded runs of known changes
(saves typing and deleting, accepts, rejects, restores) and changes on disk,
near, inside and far from the notes, checking after every step that each
note is on the very occurrence it was put on.

Run: python3 plugins/doc-comments/test/test_places.py
"""

import random
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))

from doccomments.service import Service  # noqa: E402
from doccomments.store import MemoryStore  # noqa: E402

OWNER = {"id": "sam", "name": "Sam", "kind": "owner"}
P = ("vault", "Walk.md")


def section(i):
    return (f"## Section {i}\n\n"
            f"Paragraph {i}a. The river kept its own time, and the town learned to keep it too.\n\n"
            f"> [!note] A callout in section {i}\n> The note in the callout runs to two lines.\n> A second line of the callout.\n\n"
            f"| Year | Where | Why |\n| --- | --- | --- |\n| 201{i} | Lisbon | the light |\n| 202{i} | Porto | the river |\n\n"
            f"Paragraph {i}b, after the blocks. Nothing here is remarkable except where it sits.\n\n"
            f"Paragraph {i}c closes the section with a sentence.\n")


DOC = "---\nloam_doc_id: d-abcdefghij\n---\n\n# Walk\n\n" + "\n".join(section(i) for i in range(1, 7))


def nth(text, words, n):
    at = -1
    for _ in range(n):
        at = text.index(words, at + 1)
    return at


class World:
    def __init__(self):
        self.store = MemoryStore({"vault:Walk.md": DOC}, stamp=False)
        self.svc = Service(self.store)

    def text(self):
        return self.store.files["vault:Walk.md"]

    def get(self):
        return self.svc.get(OWNER, *P)

    def note(self, nid):
        return [n for n in self.get()["notes"] if n["id"] == nid][0]

    def add(self, kind, words, n, **kw):
        t = self.text()
        s = nth(t, words, n)
        got = self.svc.add(OWNER, *P, {"kind": kind, "start": s, "end": s + len(words), "quote": words,
                                       "revision": self.get()["revision"], "text": kw.pop("text", "x"), **kw})
        return got["note"]

    def save(self, at, delete=0, insert=""):
        d = self.get()
        t = d["text"]
        new = t[:at] + insert + t[at + delete:]
        # as the editor saves: with its own record of the edit
        self.svc.save(OWNER, *P, {"base_revision": d["revision"], "base_text": t, "text": new,
                                  "changes": [[at, at + delete, insert]]})

    def disk(self, at, delete=0, insert=""):
        t = self.text()
        self.store.outside_edit(*P, t[:at] + insert + t[at + delete:])


class TheWalksCases(unittest.TestCase):
    def test_1_a_suggestion_stays_on_its_paragraph_when_words_are_typed_before_it(self):
        w = World()
        nid = w.add("suggestion", "remarkable", 6, replacement="notable")
        at = nth(w.text(), "Nothing here is", 6)
        w.save(at + len("Nothing here"), insert=" truly")
        n = w.note(nid)
        self.assertEqual(n["where"]["state"], "attached")
        self.assertEqual(n["where"]["start"], nth(w.text(), "remarkable", 6))
        w.svc.accept(OWNER, *P, nid)
        t = w.text()
        self.assertIn("Paragraph 6b, after the blocks. Nothing here truly is notable", t)
        self.assertEqual(t.count("notable"), 1)

    def test_2_a_comment_stays_when_a_word_is_typed_just_before_it(self):
        w = World()
        nid = w.add("comment", "line of the callout", 5)
        s = nth(w.text(), "line of the callout", 5)
        w.save(s, insert="fine ")
        n = w.note(nid)
        self.assertEqual((n["where"]["state"], n["where"]["start"]), ("attached", nth(w.text(), "line of the callout", 5)))

    def test_3_a_comment_stays_when_an_overlapping_suggestion_is_accepted(self):
        w = World()
        cid = w.add("comment", "line of the callout", 4)
        sid = w.add("suggestion", "callout.", 4, replacement="callout and three.")   # inside the comment's words
        self.assertTrue(nth(w.text(), "line of the callout", 4) < nth(w.text(), "callout.", 4) < nth(w.text(), "## Section 5", 1))
        w.svc.accept(OWNER, *P, sid)
        n = w.note(cid)
        self.assertIn(n["where"]["state"], ("attached", "changed"))
        self.assertTrue(nth(w.text(), "## Section 4", 1) < n["where"]["start"] < nth(w.text(), "## Section 5", 1))

    def test_4_a_suggestion_stays_on_an_edit_inside_its_passage_here_or_on_disk(self):
        w = World()
        nid = w.add("suggestion", "light", 3, replacement="sun")
        s = nth(w.text(), "light", 3)
        w.save(s + 2, insert="x")                       # "lixght", in the editor
        n = w.note(nid)
        self.assertEqual((n["where"]["state"], n["where"]["start"]), ("changed", s))
        with self.assertRaises(Exception):
            w.svc.accept(OWNER, *P, nid)                 # refuses, never writes elsewhere
        self.assertEqual(w.text().count("sun"), 0)
        # on disk, inside another suggestion's passage
        rid = w.add("suggestion", "the river", 4, replacement="the stream")
        r = nth(w.text(), "the river", 4)
        w.disk(r + 4, delete=5, insert="brook")
        n = w.note(rid)
        self.assertEqual(n["where"]["start"], r)
        self.assertIn(n["where"]["state"], ("changed", "detached"))


# -- seeded runs ---------------------------------------------------------------

PHRASES = ["remarkable", "line of the callout", "the light", "the river", "Paragraph", "Nothing here is",
           "closes the section", "callout"]


class Seeded(unittest.TestCase):
    def test_seeded_runs(self):
        runs = 300
        checked = 0
        kinds = {}
        for seed in range(1, runs + 1):
            r = random.Random(seed)
            w = World()
            # notes on repeated phrases, each remembered with its true place
            truth = {}
            for _ in range(r.randint(2, 5)):
                ph = r.choice(PHRASES)
                n = r.randint(1, w.text().count(ph))
                kind = r.choice(["comment", "suggestion"])
                s = nth(w.text(), ph, n)
                if any(not (s + len(ph) <= a or b <= s) for a, b in truth.values()):
                    continue
                kw = {"replacement": ph.upper()} if kind == "suggestion" else {}
                nid = w.add(kind, ph, n, **kw)
                truth[nid] = (s, s + len(ph))
            last = None
            for step in range(12):
                t = w.text()
                k = r.random()
                # a change: near a note, inside one, or anywhere
                ids = [i for i in truth if truth[i] is not None]
                if ids and r.random() < 0.6:
                    a, b = truth[r.choice(ids)]
                    at = r.choice([max(0, a - r.randint(0, 12)), min(len(t), b + r.randint(0, 12)), r.randint(a, b)])
                else:
                    at = r.randint(0, len(t))
                at = max(len("---\nloam_doc_id: d-abcdefghij\n---\n"), min(at, len(t)))
                delete = r.choice([0, 0, 1, 3, 9]) if at < len(t) else 0
                delete = min(delete, len(t) - at)
                insert = r.choice(["", " truly", "x", "the river ", "remarkable ", "\n\n"])
                if not delete and not insert:
                    insert = "y"
                if k < 0.12 and ids:
                    # accept or reject a suggestion
                    nid = r.choice(ids)
                    note = w.note(nid)
                    last = None
                    if note["kind"] == "suggestion" and note["where"]["state"] == "attached":
                        kinds["accept/reject"] = kinds.get("accept/reject", 0) + 1
                        if r.random() < 0.5:
                            w.svc.reject(OWNER, *P, nid)
                            truth[nid] = None
                        else:
                            a, b = truth[nid]
                            self.assertEqual(w.text()[a:b], note["anchor"]["quote"], f"seed {seed}: accept writes where it was put")
                            w.svc.accept(OWNER, *P, nid)
                            rep = note["replacement"]
                            delta = len(rep) - (b - a)
                            truth = {i: (None if v is None else (v if v[1] <= a else (v[0] + delta, v[1] + delta) if v[0] >= b else v)) for i, v in truth.items()}
                            truth[nid] = None
                    continue
                if k > 0.93 and last:
                    # restore the version before the last change: its inverse
                    saves = w.svc.history(OWNER, *P)["saves"]
                    if len(saves) >= 2:
                        w.svc.restore(OWNER, *P, saves[1]["id"])
                        kinds["restore"] = kinds.get("restore", 0) + 1
                        at, delete, insert = last
                        how = w.svc.restore
                        last = None
                    else:
                        continue
                else:
                    how = w.save if k < 0.7 else w.disk
                    removed = t[at:at + delete]
                    how(at, delete, insert)
                    kinds[how.__name__] = kinds.get(how.__name__, 0) + 1
                    last = (at, len(insert), removed)
                # the truth, carried by hand: what each note was put on
                for i, v in list(truth.items()):
                    if v is None:
                        continue
                    a, b = v
                    end = at + delete
                    if end <= a:
                        truth[i] = (a + len(insert) - delete, b + len(insert) - delete)
                    elif at >= b:
                        pass
                    else:
                        truth[i] = ("inside", a if at > a else at)   # changed or gone, never elsewhere
                for i, v in truth.items():
                    if v is None:
                        continue
                    n = w.note(i)
                    st = n["where"]["state"]
                    if v[0] == "inside":
                        if st != "detached":
                            # changed (or the words came back): at the same place
                            self.assertLessEqual(abs(n["where"]["start"] - v[1]), len(n["anchor"]["quote"]) + 12, f"seed {seed} step {step}: a note on an edited passage moved")
                        continue
                    self.assertEqual(st, "attached", f"seed {seed} step {step}: a change outside a note changed its state: {how.__name__}({at}, {delete}, {insert!r}) note {v} {n['anchor']['quote']!r} now {w.text()[v[0]-15:v[1]+15]!r}")
                    self.assertEqual((n["where"]["start"], n["where"]["end"]), v, f"seed {seed} step {step}: a change outside a note moved it")
                    checked += 1
                # once inside, stop tracking exactly
                truth = {i: (None if (v is not None and v[0] == "inside") else v) for i, v in truth.items()}
        print(f"# {runs} seeded runs, {checked} note places checked after a change outside them; changes: {kinds}")



# -- round 5: whole blocks removed and put back ------------------------------------

def block_bounds(text, kind, r):
    """A block of the walk doc: a paragraph, a section, or several sections."""
    heads = [i for i in range(len(text)) if text.startswith("## Section", i)]
    if kind == "sections":
        a = r.randrange(len(heads) - 1)
        b = min(len(heads), a + r.randint(2, 3))
        return heads[a], heads[b] if b < len(heads) else len(text)
    if kind == "section":
        a = r.randrange(len(heads))
        return heads[a], heads[a + 1] if a + 1 < len(heads) else len(text)
    paras = [i for i in range(len(text)) if text.startswith("Paragraph ", i)]
    a = r.choice(paras)
    e = text.find("\n\n", a)
    return a, (e + 2 if e >= 0 else len(text))


class WholeBlocks(unittest.TestCase):
    def test_case_1_a_suggestion_in_removed_sections_does_not_move_to_section_1(self):
        w = World()
        nid = w.add("suggestion", "remarkable", 3, replacement="dazzling")
        t = w.text()
        a, b = t.index("## Section 3"), t.index("## Section 5")
        w.store.outside_edit(*P, t[:a] + t[b:])
        n = w.note(nid)
        self.assertEqual(n["where"]["state"], "detached")
        with self.assertRaises(Exception):
            w.svc.accept(OWNER, *P, nid)
        self.assertNotIn("dazzling", w.text())
        # put back byte for byte: home again (case 6)
        w.store.outside_edit(*P, t)
        n = w.note(nid)
        self.assertEqual((n["where"]["state"], n["where"]["start"]), ("attached", nth(t, "remarkable", 3)))

    def test_case_2_cut_paste_suggest_and_restore_the_cut(self):
        w = World()
        w.add("comment", "Walk", 1)                  # the doc has notes, so a history
        t = w.text()
        a, b = t.index("## Section 3"), t.index("## Section 4")
        block = t[a:b]
        w.save(a, delete=b - a)                       # Ctrl+X
        cut_rev = w.svc.history(OWNER, *P)["saves"][0]["id"]
        w.save(a, insert=block)                       # Ctrl+V
        nid = w.add("suggestion", "remarkable", 3, replacement="bright")
        w.svc.restore(OWNER, *P, cut_rev)              # History: the version from the cut
        n = w.note(nid)
        self.assertEqual(n["where"]["state"], "detached")
        with self.assertRaises(Exception):
            w.svc.accept(OWNER, *P, nid)
        self.assertNotIn("bright", w.text())

    def test_case_3_comments_in_a_removed_section_stay_detached_and_come_home(self):
        w = World()
        c4 = w.add("comment", "line of the callout", 4)
        c2 = w.add("comment", "The river kept its own time", 2)
        t = w.text()
        a, b = t.index("## Section 4"), t.index("## Section 5")
        w.store.outside_edit(*P, t[:a] + t[b:])
        self.assertEqual(w.note(c4)["where"]["state"], "detached")
        self.assertEqual(w.note(c2)["where"]["start"], nth(w.text(), "The river kept its own time", 2))
        w.store.outside_edit(*P, t)
        self.assertEqual((w.note(c4)["where"]["state"], w.note(c4)["where"]["start"]), ("attached", nth(t, "line of the callout", 4)))

    def test_case_6_a_line_deleted_and_put_back_reattaches(self):
        w = World()
        nid = w.add("comment", "Paragraph 4c closes the section", 1)
        t = w.text()
        a = t.index("Paragraph 4c")
        b = t.index("\n", a) + 1
        w.store.outside_edit(*P, t[:a] + t[b:])
        self.assertEqual(w.note(nid)["where"]["state"], "detached")
        w.store.outside_edit(*P, t)
        self.assertEqual((w.note(nid)["where"]["state"], w.note(nid)["where"]["start"]), ("attached", a))
        # cut and undo in the editor, the same
        w.save(a, delete=b - a)
        w.save(a, insert=t[a:b])
        self.assertEqual((w.note(nid)["where"]["state"], w.note(nid)["where"]["start"]), ("attached", a))

    def test_seeded_whole_blocks(self):
        runs, checked, back = 250, 0, 0
        ambiguous_total = 0
        import os
        only = int(os.environ.get("DC_SEED") or 0)
        for seed in (range(only, only + 1) if only else range(1, runs + 1)):
            r = random.Random(seed * 7919)
            w = World()
            truth = {}
            ambiguous = set()
            for _ in range(r.randint(3, 6)):
                ph = r.choice(PHRASES)
                n = r.randint(1, w.text().count(ph))
                s = nth(w.text(), ph, n)
                if any(not (s + len(ph) <= x or y <= s) for x, y in truth.values()):
                    continue
                kind = r.choice(["comment", "suggestion"])
                nid = w.add(kind, ph, n, **({"replacement": ph.upper()} if kind == "suggestion" else {}))
                truth[nid] = (s, s + len(ph))
            removed = None                  # (at, text, notes inside with their offsets)
            since = False                   # anything saved since the removal
            for step in range(8):
                t = w.text()
                if removed and r.random() < 0.6:
                    at, block, inside, how = removed
                    # put it back, the way it went, or by restoring the version before
                    if how == "save" and not since and r.random() < 0.5:
                        before = w.svc.history(OWNER, *P)["saves"]
                        w.svc.restore(OWNER, *P, before[1]["id"])
                        if only: print("by restore")
                    elif how == "disk":
                        w.store.outside_edit(*P, t[:at] + block + t[at:])
                    else:
                        w.save(at, insert=block)
                    for i, v in list(truth.items()):
                        if v and v[0] >= at:
                            truth[i] = (v[0] + len(block), v[1] + len(block))
                    for i, off in inside.items():
                        truth[i] = (at + off[0], at + off[1])
                    removed = None
                    back += 1
                    if only: print("put back", how, at, len(block))
                elif not removed:
                    a, b = block_bounds(t, r.choice(["paragraph", "section", "sections"]), r)
                    inside = {i: (v[0] - a, v[1] - a) for i, v in truth.items() if v and a <= v[0] and v[1] <= b}
                    if any(v and v[0] < b and a < v[1] and i not in inside for i, v in truth.items()):
                        continue
                    how = r.choice(["disk", "save"])
                    if how == "disk":
                        w.store.outside_edit(*P, t[:a] + t[b:])
                    else:
                        w.save(a, delete=b - a)
                    for i in inside:
                        truth[i] = None
                    for i, v in list(truth.items()):
                        if v and v[0] >= b:
                            truth[i] = (v[0] - (b - a), v[1] - (b - a))
                    removed = (a, t[a:b], inside, how)
                    since = False
                    if only: print("removed", how, a, b, list(inside))
                else:
                    # an Accept, on any suggestion: it writes in its own passage or refuses
                    sug = [i for i in truth if w.note(i)["kind"] == "suggestion" and w.note(i)["state"] == "open"]
                    if not sug:
                        continue
                    i = r.choice(sug)
                    before = w.text()
                    if only: print("accept", w.note(i)["anchor"]["quote"], truth[i], w.note(i)["where"], i in ambiguous, repr(w.text()[truth[i][0]:truth[i][1]] if truth[i] else None))
                    since = True
                    at_note = dict(w.note(i)["where"])
                    if i in ambiguous:
                        # kept on an equal run: an accept writes exactly at its own
                        # stored place, or refuses and writes nothing
                        try:
                            w.svc.accept(OWNER, *P, i)
                        except Exception:
                            self.assertEqual(w.text(), before, f"seed {seed}: a refused accept wrote")
                            continue
                        a2, b2 = at_note["start"], at_note["end"]
                        self.assertEqual(w.text(), before[:a2] + w.note(i)["replacement"] + before[b2:], f"seed {seed}: an accept wrote outside its stored place")
                        truth = {k: (None if k == i else v) for k, v in truth.items()}
                        removed = None
                        continue
                    try:
                        w.svc.accept(OWNER, *P, i)
                    except Exception:
                        self.assertEqual(w.text(), before, f"seed {seed}: a refused accept wrote")
                        self.assertIsNone(truth[i], f"seed {seed}: an accept on a passage that is there was refused")
                        continue
                    self.assertIsNotNone(truth[i], f"seed {seed} step {step}: an accept wrote though its passage was removed")
                    a, b = truth[i]
                    rep = w.note(i)["replacement"]
                    self.assertEqual(w.text(), before[:a] + rep + before[b:], f"seed {seed} step {step}: an accept wrote outside its passage")
                    d = len(rep) - (b - a)
                    truth = {k: (None if v is None else (v if v[1] <= a else (v[0] + d, v[1] + d) if v[0] >= b else v)) for k, v in truth.items()}
                    truth[i] = None
                    if removed:
                        at0, blk, ins, how0 = removed
                        if at0 >= b:
                            removed = (at0 + d, blk, ins, how0)
                    continue
                for i, v in truth.items():
                    n = w.note(i)
                    if n["state"] != "open" or i in ambiguous:
                        continue
                    if v is None:
                        # Removed by a change nobody described: of two equal runs side
                        # by side, the one the comparison kept may keep the note (the
                        # text is the same either way) -- at the point of removal only.
                        kept_equal = (removed and removed[3] == "disk" and i in removed[2]
                                      and n["where"]["state"] == "attached" and abs(n["where"]["start"] - removed[0]) <= 120
                                      and w.text()[n["where"]["start"]:n["where"]["end"]] == n["anchor"]["quote"])
                        if kept_equal:
                            ambiguous.add(i)
                        if not kept_equal:
                            self.assertNotEqual(n["where"]["state"], "attached", f"seed {seed} step {step}: a note whose words were removed is attached elsewhere ({n['where']}, {n['anchor']['quote']!r})")
                    else:
                        if only and (n["where"]["state"], n["where"]["start"]) != ("attached", v[0]):
                            print("ANCHOR", n["anchor"], n["where"], repr(w.text()[v[0]-40:v[0]+40]))
                        self.assertEqual((n["where"]["state"], n["where"]["start"]), ("attached", v[0]), f"seed {seed} step {step}: a note is not on its own occurrence ({n['anchor']['quote']!r})")
                        checked += 1
            ambiguous_total += len(ambiguous)
        print(f"# {runs} seeded runs of whole blocks removed and put back ({back} put back), {checked} places checked; "
              f"{ambiguous_total} notes kept on an equal run the comparison kept")



class WhatItsWordsBecame(unittest.TestCase):
    """Round 5, B8: a comment whose last (or first) words were rewritten by
    an accepted suggestion beside it covers what those words became."""

    def add(self, w, kind, words, **kw):
        t = w.text()
        s = t.index(words, t.index("Paragraph 2a."))
        return w.svc.add(OWNER, *P, {"kind": kind, "start": s, "end": s + len(words), "quote": words,
                                     "revision": w.get()["revision"], "text": "x", **kw})["note"]

    def test_a_comment_covers_what_its_words_became(self):
        for cq, sq, rep, want in (
                ("river kept its own", "own time", "proper time", "river kept its proper time"),
                ("river kept its own", "its own", "its", "river kept its"),
                ("river kept its own", "The river", "A river", "A river kept its own"),
                ("kept its own time", "own", "own good", "kept its own good time"),
                ("river kept its own", "kept its", "kept all its", "river kept all its own")):
            w = World()
            c = self.add(w, "comment", cq)
            s = self.add(w, "suggestion", sq, replacement=rep)
            w.svc.accept(OWNER, *P, s)
            wh = w.note(c)["where"]
            self.assertEqual(w.text()[wh["start"]:wh["end"]], want, (cq, sq, rep))


class HistoryRows(unittest.TestCase):
    """Round 5, B9: a History row names the whole heading, number and all."""

    def test_a_cut_section_is_named_with_its_number(self):
        from doccomments import textdiff
        t = DOC
        a, b = t.index("## Section 3"), t.index("## Section 4")
        self.assertEqual(textdiff.summary(t, t[:a] + t[b:])["section"], "Section 3")
        c = t.index("Paragraph 2a")
        self.assertEqual(textdiff.summary(t, t[:c] + "New words. " + t[c:])["section"], "Section 2")
        d = t.index("## Section 5") + len("## Section ")
        self.assertEqual(textdiff.summary(t, t[:d] + "Five" + t[d + 1:])["section"], "Section Five")

if __name__ == "__main__":
    unittest.main(verbosity=1)
