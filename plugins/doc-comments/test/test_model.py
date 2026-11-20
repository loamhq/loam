#!/usr/bin/env python3
"""The doc-comments model: anchors, the three-way merge, the sidecar and the
operations, against an in-memory copy of the broker's file contract.

  - anchors re-find their passage after edits elsewhere, mark a slightly
    changed passage "changed", send a vanished one to detached (never
    deleting it), and re-attach to a new selection
  - the merge lands changes to different passages from both saves; where
    both changed one passage the first save stands and the second becomes a
    suggestion in its author's name, with the rest of the second save landing
  - accepting a suggestion produces exactly the expected file text;
    rejecting it leaves the text byte for byte
  - the sidecar is found by the id line and follows the doc when it is
    renamed or moved; a copy of a commented doc does not take its notes
  - a doc nobody commented on is never written; the first note adds exactly
    one frontmatter line and nothing else changes
  - edits made outside the editor are recorded under the persona the
    frontmatter's last_modified_by names, else the owner
  - history replays to every earlier text, and restore writes it back

Run: python3 plugins/doc-comments/test/test_model.py
"""

import json
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from doccomments import anchor, frontmatter as fm, merge, sidecar, textdiff  # noqa: E402
from doccomments.service import Service  # noqa: E402
from doccomments.sidecar import NoteError  # noqa: E402
from doccomments.store import MemoryStore  # noqa: E402

OWNER = {"id": "sam", "name": "Sam", "kind": "owner"}
ADA = {"id": "ada", "name": "Ada", "kind": "agent"}
IRIS = {"id": "iris", "name": "Iris", "kind": "agent"}

ESSAY = """---
created_by: "[[Sam]]"
tags: [essay]
---

# On staying in one place

For ten years I moved every eighteen months, and I told myself it was curiosity. It was closer to a habit of leaving before anything could ask something of me.

The first winter I stayed, nothing happened, and that was the point. These are small things, but small things are most of a life.

## What staying costs

Staying is not free. You trade the clean slate for a record: people remember the version of you that was careless in March.
"""


class Tokens(unittest.TestCase):
    def test_tokens_cover_text(self):
        for t in [ESSAY, "a\r\nb", "", "  x\t\ty\n\n", "émigré — naïve"]:
            self.assertEqual("".join(textdiff.tokens(t)), t)

    def test_delta_roundtrip(self):
        a = ESSAY
        b = ESSAY.replace("eighteen", "twelve").replace("## What", "## Why") + "More.\n"
        ops = textdiff.delta(a, b)
        self.assertEqual(textdiff.apply(a, ops), b)
        self.assertEqual(textdiff.apply(b, textdiff.delta(b, a)), a)

    def test_map_pos(self):
        a = "one two three"
        b = "one 2 two three"
        ops = textdiff.delta(a, b)
        self.assertEqual(textdiff.map_pos(ops, a.index("three")), b.index("three"))


class Anchors(unittest.TestCase):
    def test_an_anchor_is_its_words_and_a_little_either_side(self):
        q = "a habit of leaving"
        s = ESSAY.index(q)
        a = anchor.make(ESSAY, s, s + len(q))
        self.assertEqual((a["quote"], a["pos"]), (q, s))
        self.assertEqual(a["prefix"], ESSAY[s - anchor.CONTEXT:s])
        self.assertEqual(a["suffix"], ESSAY[s + len(q):s + len(q) + anchor.CONTEXT])

    def test_nothing_searches_for_a_quote(self):
        self.assertFalse(hasattr(anchor, "locate"))


class Merge(unittest.TestCase):
    def test_different_passages_both_land(self):
        base = "Alpha one.\n\nBeta two.\n\nGamma three.\n"
        theirs = base.replace("Alpha one", "Alpha ONE")
        ours = base.replace("Gamma three", "Gamma THREE")
        merged, conflicts = merge.merge3(base, ours, theirs)
        self.assertEqual(merged, "Alpha ONE.\n\nBeta two.\n\nGamma THREE.\n")
        self.assertEqual(conflicts, [])

    def test_same_paragraph_different_sentences_both_land(self):
        base = "The cat sat on the mat. The dog lay by the door. The bird sang.\n"
        theirs = base.replace("cat sat", "cat slept")
        ours = base.replace("bird sang", "bird was silent")
        merged, conflicts = merge.merge3(base, ours, theirs)
        self.assertEqual(merged, "The cat slept on the mat. The dog lay by the door. The bird was silent.\n")
        self.assertEqual(conflicts, [])

    def test_same_passage_first_save_stands(self):
        base = "One. The weather was fine today. Three.\n"
        theirs = base.replace("fine", "glorious")
        ours = base.replace("fine", "awful").replace("Three", "THREE")
        merged, conflicts = merge.merge3(base, ours, theirs)
        self.assertEqual(merged, "One. The weather was glorious today. THREE.\n")
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual(merged[c["start"]:c["end"]], "glorious")
        self.assertEqual(c["ours"], "awful")

    def test_identical_changes_are_not_a_conflict(self):
        base = "a b c"
        merged, conflicts = merge.merge3(base, "a X c", "a X c")
        self.assertEqual((merged, conflicts), ("a X c", []))


class Frontmatter(unittest.TestCase):
    def test_id_line_added_alone(self):
        t = fm.with_id(ESSAY, "d-abcdefghij")
        self.assertEqual(fm.doc_id(t), "d-abcdefghij")
        self.assertEqual(t.replace("loam_doc_id: d-abcdefghij\n", ""), ESSAY)

    def test_no_frontmatter_crlf(self):
        t = "Hello\r\nworld\r\n"
        out = fm.with_id(t, "d-abcdefghij")
        self.assertTrue(out.startswith("---\r\nloam_doc_id: d-abcdefghij\r\n---\r\n"))
        self.assertTrue(out.endswith(t))
        self.assertEqual(fm.doc_id(out), "d-abcdefghij")
        self.assertEqual(fm.with_id(out, "d-bbbbbbbbbb").count("loam_doc_id"), 1)

    def test_last_modified_by(self):
        self.assertEqual(fm.last_modified_by('---\nlast_modified_by: "[[Ada]]"\n---\n'), "Ada")
        self.assertIsNone(fm.last_modified_by("no frontmatter"))


def make(files=None):
    store = MemoryStore(files or {"vault:Essays/staying.md": ESSAY})
    return store, Service(store)


def sidecars(store):
    return {k: json.loads(v) for k, v in store.files.items() if k.endswith(".json")}


class Service_(unittest.TestCase):
    P = ("vault", "Essays/staying.md")

    def test_untouched_until_first_note(self):
        store, svc = make()
        got = svc.get(OWNER, *self.P)
        self.assertIsNone(got["id"])
        self.assertEqual(got["notes"], [])
        self.assertEqual(store.writes, [])
        self.assertEqual(store.files["vault:Essays/staying.md"], ESSAY)

    def comment(self, svc, quote, who=OWNER, text="A note"):
        got = svc.get(who, *self.P)
        s = got["text"].index(quote)
        return svc.add(who, *self.P, {"kind": "comment", "start": s, "end": s + len(quote),
                                      "quote": quote, "revision": got["revision"], "text": text})

    def test_first_note_adds_one_line_and_a_sidecar(self):
        store, svc = make()
        out = self.comment(svc, "a habit of leaving", who=ADA)
        doc = store.files["vault:Essays/staying.md"]
        did = fm.doc_id(doc)
        self.assertTrue(did)
        # The id line, and the broker's stamp naming the persona who made the
        # write: nothing else in the body changed.
        body = doc.replace(f"loam_doc_id: {did}\n", "").replace('last_modified_by: "[[Ada]]"\n', "")
        self.assertEqual(body, ESSAY)
        scs = sidecars(store)
        self.assertEqual(list(scs), [f"vault:99_SYSTEM/Loam/data/doc-comments/{did}.json"])
        note = out["notes"][0]
        self.assertEqual(note["author"]["id"], "ada")
        self.assertEqual(doc[note["where"]["start"]:note["where"]["end"]], "a habit of leaving")

    def test_agent_names_quote_with_occurrence(self):
        store, svc = make()
        out = svc.add(ADA, *self.P, {"kind": "comment", "quote": "small things", "occurrence": 2, "text": "x"})
        n = out["notes"][0]
        t = out["text"]
        self.assertEqual(n["where"]["start"], t.index("small things", t.index("small things") + 1))
        with self.assertRaises(NoteError) as e:
            svc.add(ADA, *self.P, {"kind": "comment", "quote": "small things", "text": "x"})
        self.assertEqual(e.exception.status, 409)

    def test_outside_edit_refind_changed_detached_reattach(self):
        store, svc = make()
        self.comment(svc, "a habit of leaving")
        self.comment(svc, "The first winter I stayed, nothing happened")
        self.comment(svc, "These are small things, but small things are most of a life.")
        doc = store.files["vault:Essays/staying.md"]
        # A persona edits on disk: a new opening line, one passage reworded,
        # one cut, and its stamp.
        edited = (doc.replace("# On staying", "# On staying\n\nA new first line.\n\n# On staying", 1)
                  .replace("nothing happened", "nothing much happened")
                  .replace("These are small things, but small things are most of a life.", "")
                  .replace('last_modified_by: "[[Sam]]"', 'last_modified_by: "[[Ada]]"'))
        store.outside_edit(*self.P, edited)
        got = svc.get(OWNER, *self.P)
        states = [n["where"]["state"] for n in got["notes"]]
        self.assertEqual(states, ["attached", "changed", "detached"])
        n0 = got["notes"][0]
        self.assertEqual(edited[n0["where"]["start"]:n0["where"]["end"]], "a habit of leaving")
        self.assertIn("nothing much happened", got["notes"][1]["now"])
        # Detached keeps its quote and stays in the sidecar.
        self.assertEqual(got["notes"][2]["anchor"]["quote"],
                         "These are small things, but small things are most of a life.")
        # Keep the outside editor's identity in the revision history.
        hist = svc.history(OWNER, *self.P)
        self.assertEqual(hist["saves"][0]["kind"], "outside")
        self.assertEqual(hist["saves"][0]["author"]["id"], "ada")
        # Re-attach the detached one to a new passage.
        q = "Staying is not free."
        got = svc.reattach(OWNER, *self.P, got["notes"][2]["id"],
                           {"quote": q, "start": edited.index(q), "end": edited.index(q) + len(q),
                            "revision": got["revision"]})
        n2 = got["notes"][2]
        self.assertEqual(n2["where"]["state"], "attached")
        self.assertEqual(got["text"][n2["where"]["start"]:n2["where"]["end"]], q)

    def test_outside_edit_by_unstamped_tool_is_the_owner(self):
        store, svc = make()
        self.comment(svc, "a habit of leaving", who=ADA)
        doc = store.files["vault:Essays/staying.md"]
        store.outside_edit(*self.P, doc.replace('last_modified_by: "[[Ada]]"\n', "") + "\nA line.\n")
        hist = svc.history(OWNER, *self.P)
        self.assertEqual(hist["saves"][0]["author"]["id"], "sam")

    def suggest(self, svc, quote, replacement, who=ADA):
        got = svc.get(who, *self.P)
        s = got["text"].index(quote)
        out = svc.add(who, *self.P, {"kind": "suggestion", "start": s, "end": s + len(quote), "quote": quote,
                                     "revision": got["revision"], "replacement": replacement, "text": ""})
        return out, out["note"]

    def test_accept_gives_exact_text(self):
        store, svc = make()
        out, nid = self.suggest(svc, "These are small things, but small things are most of a life.",
                                "Small things, but a life is mostly small things.")
        before = store.files["vault:Essays/staying.md"]
        got = svc.accept(OWNER, *self.P, nid)
        after = store.files["vault:Essays/staying.md"]
        expected = before.replace("These are small things, but small things are most of a life.",
                                  "Small things, but a life is mostly small things.")
        # The broker stamps the owner, who made the write.
        expected = expected.replace('last_modified_by: "[[Ada]]"', 'last_modified_by: "[[Sam]]"')
        self.assertEqual(after, expected)
        n = [x for x in got["notes"] if x["id"] == nid][0]
        self.assertEqual(n["state"], "accepted")
        hist = svc.history(OWNER, *self.P)["saves"][0]
        self.assertEqual((hist["kind"], hist["author"]["id"], hist["by"]["id"]), ("accept", "ada", "sam"))

    def test_accept_undone_while_it_is_the_last_change(self):
        # Accept's Undo (reopen on an accepted suggestion): the text as it
        # was, the suggestion open again, a save of its own in History.
        store, svc = make()
        _, nid = self.suggest(svc, "eighteen months", "two years")
        before = store.files["vault:Essays/staying.md"]
        svc.accept(OWNER, *self.P, nid)
        self.assertIn("every two years", store.files["vault:Essays/staying.md"])
        got = svc.reopen(OWNER, *self.P, nid)
        after = store.files["vault:Essays/staying.md"]
        unstamped = lambda t: "\n".join(ln for ln in t.split("\n") if not ln.startswith("last_modified_by"))
        self.assertEqual(unstamped(after), unstamped(before))
        n = [x for x in got["notes"] if x["id"] == nid][0]
        self.assertEqual((n["state"], n["where"]["state"]), ("open", "attached"))
        self.assertEqual(after[n["where"]["start"]:n["where"]["end"]], "eighteen months")
        self.assertEqual(svc.history(OWNER, *self.P)["saves"][0]["kind"], "restore")

    def test_accept_not_undone_after_another_change(self):
        store, svc = make()
        _, nid = self.suggest(svc, "eighteen months", "two years")
        svc.accept(OWNER, *self.P, nid)
        doc = store.files["vault:Essays/staying.md"]
        store.outside_edit(*self.P, doc.replace("curiosity", "wanderlust"))
        with self.assertRaises(NoteError) as e:
            svc.reopen(OWNER, *self.P, nid)
        self.assertEqual(e.exception.status, 409)
        self.assertIn("wanderlust", store.files["vault:Essays/staying.md"])

    def test_growing_my_suggestion_replaces_it(self):
        # Typing next to my own open suggestion sends the grown one with
        # `supersedes`: one suggestion is left, not two.
        store, svc = make()
        out, first = self.suggest(svc, "eighteen months", "two years", who=OWNER)
        got = svc.get(OWNER, *self.P)
        s = got["text"].index("eighteen months")
        grown = svc.add(OWNER, *self.P, {"kind": "suggestion", "start": s, "end": s + len("eighteen months,"),
                                         "quote": "eighteen months,", "replacement": "two years, maybe three,",
                                         "revision": got["revision"], "supersedes": first})
        open_ = [n for n in grown["notes"] if n["state"] == "open" and n["kind"] == "suggestion"]
        self.assertEqual([n["id"] for n in open_], [grown["note"]])
        # Someone else's is never replaced.
        got = svc.get(OWNER, *self.P)
        s = got["text"].index("I told myself")
        theirs = svc.add(store.agent("ada"), *self.P, {"kind": "suggestion", "quote": "I told myself", "replacement": "I said"})
        mine = svc.add(OWNER, *self.P, {"kind": "suggestion", "quote": "I told myself it", "replacement": "I said it",
                                        "supersedes": theirs["note"]})
        ids = {n["id"] for n in mine["notes"] if n["state"] == "open"}
        self.assertIn(theirs["note"], ids)

    def test_first_note_at_the_very_top_does_not_take_in_the_id_line(self):
        store = MemoryStore({"vault:Top.md": "# Walk\n\nWords.\n"})
        svc = Service(store)
        got = svc.add(OWNER, "vault", "Top.md", {"kind": "suggestion", "start": 0, "end": 1, "quote": "#",
                                                 "revision": svc.get(OWNER, "vault", "Top.md")["revision"],
                                                 "replacement": "Hello #", "text": ""})
        n = got["notes"][0]
        self.assertEqual(got["text"][n["where"]["start"]:n["where"]["end"]], "#")
        self.assertTrue(got["text"].startswith("---\nloam_doc_id: "))

    def test_a_deleted_passage_detaches_instead_of_moving_onto_a_like_one(self):
        sections = "\n\n".join(
            f"## Section {i}\n\nParagraph {i}a. The river kept its own time.\n\n"
            f"Paragraph {i}b, after the blocks. Nothing here is remarkable except where it sits.\n\n"
            f"Paragraph {i}c closes the section with words of its own." for i in range(1, 7))
        text = "# Long\n\n" + sections + "\n\nEnd words here.\n"
        store = MemoryStore({"vault:Long.md": text})
        svc = Service(store)
        p = ("vault", "Long.md")
        a = svc.add(OWNER, *p, {"kind": "comment", "quote": "Paragraph 6b, after the blocks", "text": "On 6b"})
        b = svc.add(OWNER, *p, {"kind": "comment", "quote": "End words here.", "text": "On the end"})
        doc = store.files["vault:Long.md"]
        start = doc.index("Paragraph 6b")
        end = doc.index("Paragraph 6c")
        doc = doc[:start] + doc[end:]
        doc = doc.replace("\n\nEnd words here.\n", "\n")
        store.outside_edit(*p, doc)
        notes = {n["text"]: n for n in svc.get(OWNER, *p)["notes"]}
        self.assertEqual(notes["On 6b"]["where"]["state"], "detached")
        self.assertEqual(notes["On the end"]["where"]["state"], "detached")
        # and a passage merely edited where it was is still found, changed
        a2 = svc.add(OWNER, *p, {"kind": "comment", "quote": "Paragraph 3b, after the blocks", "text": "On 3b"})
        doc = store.files["vault:Long.md"].replace("Paragraph 3b, after the blocks", "Paragraph 3b, after all the blocks")
        store.outside_edit(*p, doc)
        notes = {n["text"]: n for n in svc.get(OWNER, *p)["notes"]}
        self.assertEqual(notes["On 3b"]["where"]["state"], "changed")

    def test_revision_alone(self):
        store, svc = make()
        got = svc.revision(OWNER, *self.P)
        self.assertEqual(got, {"revision": svc.get(OWNER, *self.P)["revision"], "notes": None})
        # a note from anyone moves the notes' revision, not the doc's
        self.comment(svc, "eighteen months")
        first = svc.revision(OWNER, *self.P)
        self.assertIsNotNone(first["notes"])
        nid = svc.get(OWNER, *self.P)["notes"][0]["id"]
        svc.reply(store.agent("ada"), *self.P, nid, "A reply")
        second = svc.revision(OWNER, *self.P)
        self.assertEqual(second["revision"], first["revision"])
        self.assertNotEqual(second["notes"], first["notes"])

    def test_reject_leaves_text(self):
        store, svc = make()
        _, nid = self.suggest(svc, "eighteen months", "two years")
        before = store.files["vault:Essays/staying.md"]
        got = svc.reject(OWNER, *self.P, nid)
        self.assertEqual(store.files["vault:Essays/staying.md"], before)
        self.assertEqual([n["state"] for n in got["notes"]], ["rejected"])
        self.assertEqual(got["resolved"], 1)
        got = svc.reopen(OWNER, *self.P, nid)
        self.assertEqual([n["state"] for n in got["notes"]], ["open"])

    def test_accept_refused_when_passage_changed(self):
        store, svc = make()
        _, nid = self.suggest(svc, "It was closer to a habit of leaving before anything could ask something of me.",
                              "It was a habit of leaving.")
        doc = store.files["vault:Essays/staying.md"]
        store.outside_edit(*self.P, doc.replace("a habit of leaving", "a habit of going"))
        with self.assertRaises(NoteError) as e:
            svc.accept(OWNER, *self.P, nid)
        self.assertEqual(e.exception.status, 409)

    def test_resolve_undo(self):
        store, svc = make()
        out = self.comment(svc, "eighteen months")
        nid = out["note"]
        got = svc.resolve(OWNER, *self.P, nid)
        self.assertEqual(got["notes"][0]["state"], "resolved")
        got = svc.reopen(OWNER, *self.P, nid)
        self.assertEqual(got["notes"][0]["state"], "open")

    def test_save_merge_and_conflict_becomes_suggestion(self):
        store, svc = make()
        self.comment(svc, "eighteen months")
        opened = svc.get(OWNER, *self.P)              # the owner opens the editor
        base_text, base_rev = opened["text"], opened["revision"]
        # The other editor saves changes to the opening verb and the last line first.
        theirs = (base_text.replace("I told myself", "I promised myself")
                  .replace("careless in March", "careless in April"))
        store.outside_edit(*self.P, theirs)
        # The owner's save: the same verb differently, and a later sentence.
        ours = (base_text.replace("I told myself", "I assured myself")
                .replace("Staying is not free.", "Staying costs."))
        got = svc.save(OWNER, *self.P, {"base_revision": base_rev, "base_text": base_text, "text": ours})
        stored = store.files["vault:Essays/staying.md"]
        self.assertIn("I promised myself", stored)        # the first save stands
        self.assertIn("careless in April", stored)        # theirs elsewhere
        self.assertIn("Staying costs.", stored)           # the rest of ours landed
        self.assertEqual(got["conflicts"], 1)
        sug = [n for n in got["notes"] if n["kind"] == "suggestion"]
        self.assertEqual(len(sug), 1)
        self.assertEqual(sug[0]["author"]["id"], "sam")
        self.assertEqual(sug[0]["origin"], "conflict")
        self.assertEqual(sug[0]["replacement"], "assured")
        w = sug[0]["where"]
        self.assertEqual(stored[w["start"]:w["end"]], "promised")
        # Accepting it gives the owner's word in place, exactly.
        svc.accept(OWNER, *self.P, sug[0]["id"])
        self.assertEqual(store.files["vault:Essays/staying.md"], stored.replace("I promised myself", "I assured myself"))

    def test_save_conflict_on_doc_without_notes_makes_its_sidecar(self):
        store, svc = make()
        opened = svc.get(OWNER, *self.P)
        store.outside_edit(*self.P, opened["text"].replace("Staying is not free", "Staying is dear"))
        ours = opened["text"].replace("Staying is not free", "Staying is cheap")
        got = svc.save(OWNER, *self.P, {"base_revision": opened["revision"], "base_text": opened["text"], "text": ours})
        self.assertTrue(got["id"])
        self.assertEqual(len(got["notes"]), 1)
        n = got["notes"][0]
        self.assertEqual(got["text"][n["where"]["start"]:n["where"]["end"]], "dear")
        self.assertEqual(n["replacement"], "cheap")

    def test_plain_save_without_notes_records_nothing(self):
        store, svc = make()
        opened = svc.get(OWNER, *self.P)
        svc.save(OWNER, *self.P, {"base_revision": opened["revision"], "base_text": opened["text"],
                                  "text": opened["text"] + "\nMore.\n"})
        self.assertEqual(sidecars(store), {})
        self.assertIsNone(fm.doc_id(store.files["vault:Essays/staying.md"]))

    def test_unchanged_save_writes_nothing(self):
        store, svc = make()
        opened = svc.get(OWNER, *self.P)
        svc.save(OWNER, *self.P, {"base_revision": opened["revision"], "base_text": opened["text"],
                                  "text": opened["text"]})
        self.assertEqual(store.writes, [])

    def test_sidecar_follows_rename(self):
        store, svc = make()
        self.comment(svc, "eighteen months")
        store.rename("vault", "Essays/staying.md", "Published/On staying.md")
        got = svc.get(OWNER, "vault", "Published/On staying.md")
        self.assertEqual(len(got["notes"]), 1)
        self.assertEqual(got["notes"][0]["where"]["state"], "attached")
        sc = list(sidecars(store).values())[0]
        self.assertEqual(sc["doc"], {"root": "vault", "path": "Published/On staying.md"})

    def test_copy_does_not_take_the_notes(self):
        store, svc = make()
        self.comment(svc, "eighteen months")
        store.files["vault:Essays/copy.md"] = store.files["vault:Essays/staying.md"]
        got = svc.get(OWNER, "vault", "Essays/copy.md")
        self.assertEqual(got["notes"], [])
        self.assertTrue(got["copy_of"])
        # Its first note gives it an id of its own.
        s = got["text"].index("Staying")
        out = svc.add(OWNER, "vault", "Essays/copy.md", {"kind": "comment", "start": s, "end": s + 8,
                                                         "revision": got["revision"], "text": "x"})
        self.assertNotEqual(out["id"], svc.get(OWNER, *self.P)["id"])
        self.assertEqual(len(svc.get(OWNER, *self.P)["notes"]), 1)

    def test_history_restore_and_authors(self):
        store, svc = make()
        self.comment(svc, "eighteen months")
        o = svc.get(OWNER, *self.P)
        svc.save(OWNER, *self.P, {"base_revision": o["revision"], "base_text": o["text"],
                                  "text": o["text"].replace("Staying is not free.", "Staying costs.")})
        mid = store.files["vault:Essays/staying.md"]
        doc = store.files["vault:Essays/staying.md"]
        store.outside_edit(*self.P, doc.replace('last_modified_by: "[[Sam]]"', 'last_modified_by: "[[Iris]]"')
                           + "\nIris was here.\n")
        hist = svc.history(OWNER, *self.P)
        kinds = [s["kind"] for s in hist["saves"]]
        self.assertEqual(kinds, ["outside", "editor", "base"])
        text = store.files["vault:Essays/staying.md"]
        spans = hist["authors"]
        iris = [s for s in spans if s["author"] == "iris"]
        self.assertTrue(any("Iris was here" in text[s["start"]:s["end"]] for s in iris))
        editor_save = hist["saves"][1]["id"]
        self.assertEqual(svc.version(OWNER, *self.P, editor_save)["text"], mid)
        svc.restore(OWNER, *self.P, editor_save)
        restored = store.files["vault:Essays/staying.md"]
        self.assertNotIn("Iris was here", restored)
        self.assertIn("Staying costs.", restored)
        self.assertEqual(fm.doc_id(restored), fm.doc_id(mid))


if __name__ == "__main__":
    unittest.main(verbosity=1)
