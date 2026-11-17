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


if __name__ == "__main__":
    unittest.main(verbosity=1)
