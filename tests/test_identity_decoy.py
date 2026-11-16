#!/usr/bin/env python3
"""The identity preflight's decoy is sampled, not deterministic.

Until 2026-09-05 ``pick_decoy`` returned the first usable sentence of the
first sibling in sorted order: Cleo's soul for six of seven agents, every
night, and five souls that were never anyone's decoy. This file asserts the
replacement against a throwaway ``ai-labs`` of fake souls, by pointing the
module's ``AI_LABS`` at it. Nothing here reads a real agent's files or spends
a model call; the probe in ``assert_soul_in_context`` is not exercised.

Asserted, each by drawing:

  spread          with a fixed seed, repeated draws name more than one owner;
                  in fact every sibling with an eligible sentence is drawn.
  the filter      no draw is a sentence present in the project's own files,
                  and a sibling whose every sentence is in those files never
                  appears as an owner. It drops out of the draw.
  attribution     the sentence drawn really is in the soul of the owner named,
                  which is what makes ``identity_decoy_from`` diagnostic.
  reproducible    the same seed gives the same sequence; a different seed
                  gives a different one.
  the refusal     when no sibling has an eligible sentence, ``IdentityError``.
  the contract    ``preflight_identity``'s signature is unchanged, so the two
                  existing stubs of it keep fitting, and ``pick_decoy`` still
                  returns ``(sentence, owner)`` with no ``rng`` passed.

Run: python3 tests/test_identity_decoy.py
"""

from __future__ import annotations

import inspect
import random
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_identity as li  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"      got  {got!r}\n      want {want!r}")


def sentence(tag: str, n: int) -> str:
    # 45 to 110 characters, no newline: the shape `_sentences` accepts.
    return f"The {tag} soul says a distinct thing number {n} that nobody else says here."


SHARED = "This sentence sits in the project's own files, so it can never be a decoy."
assert 45 <= len(SHARED) <= 110

# Sibling souls. `stray` has one eligible sentence; `hollow` only the shared
# one, so it must drop out; `verbose` has many, to prove a long soul does not
# crowd out the short ones. Each soul opens with a heading, as the real ones
# do. Until 2026-09-05 `_sentences` split only after sentence punctuation, so
# the heading swallowed the first sentence after it, and the shared sentence
# was put first to be the one swallowed. A line break is a boundary now
# (tests/test_identity_sentences.py), so the order no longer matters; the
# shared sentence stays first because nothing here depends on it moving.
SIBLINGS = {
    "stray": [SHARED, sentence("stray", 1)],
    "hollow": [SHARED, SHARED],
    "verbose": [sentence("verbose", i) for i in range(1, 30)],
    "brief": [sentence("brief", 1), sentence("brief", 2)],
}
ELIGIBLE = {
    owner: {s for s in ss if s != SHARED} for owner, ss in SIBLINGS.items()
}


def build(root: Path, siblings: dict[str, list[str]]) -> Path:
    labs = root / "ai-labs"
    labs.mkdir()
    project = labs / "me"
    project.mkdir()
    (project / "soul.md").write_text(
        f"# Me\n\n{sentence('me', 1)} {SHARED}\n", encoding="utf-8"
    )
    for name in li.SIBLING_FILES:
        (project / name).write_text(f"# {name}\n\n{SHARED}\n", encoding="utf-8")
    for owner, ss in siblings.items():
        d = labs / owner
        d.mkdir()
        (d / "soul.md").write_text(
            f"# {owner}\n\n" + " ".join(ss) + "\n", encoding="utf-8"
        )
    # A directory with no soul.md, as `commons` is on the box: not a sibling.
    (labs / "commons").mkdir()
    return project


def draws(project: Path, seed: int, n: int = 200) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    return [li.pick_decoy(project, rng=rng) for _ in range(n)]


real_labs = li.AI_LABS
root = Path(tempfile.mkdtemp(prefix="loam-decoy-test-"))
try:
    li.AI_LABS = root / "ai-labs"
    project = build(root, SIBLINGS)
    mine = "".join(
        (project / n).read_text(encoding="utf-8")
        for n in ("soul.md",) + li.SIBLING_FILES
    )

    first = draws(project, seed=7)
    owners = {owner for _, owner in first}

    check("more than one owner across 200 draws", len(owners) > 1, True)
    check("every sibling with an eligible sentence is drawn",
          owners, {"stray", "verbose", "brief"})
    check("a sibling with no eligible sentence drops out",
          "hollow" in owners, False)
    check("no draw is present in the project's own files",
          [s for s, _ in first if s in mine], [])
    check("every sentence belongs to the owner it is attributed to",
          all(s in ELIGIBLE[o] for s, o in first), True)

    # Owner first, then sentence: `verbose` has 28 eligible sentences to
    # `stray`'s one, and still must not dominate. With three owners drawn
    # uniformly, 200 draws put each near 67; pooled sampling would put
    # `verbose` near 180 and `stray` near 6.
    counts = {o: sum(1 for _, x in first if x == o) for o in owners}
    check("owners drawn uniformly, not by soul length (stray >= 40 of 200)",
          counts["stray"] >= 40, True)
    check("owners drawn uniformly, not by soul length (verbose <= 100 of 200)",
          counts["verbose"] <= 100, True)

    check("the same seed gives the same sequence", draws(project, seed=7), first)
    check("a different seed gives a different sequence",
          draws(project, seed=8) == first, False)

    # No rng: the system RNG, and the same return shape.
    s, o = li.pick_decoy(project)
    check("default rng returns (sentence, owner)",
          (isinstance(s, str), o in ELIGIBLE, s in ELIGIBLE[o]),
          (True, True, True))

    # `ask` joined it on 2026-11-13, keyword with a default, for a Codex
    # wake's gate; the Claude path still calls (project, model=...), so the
    # stubs of that shape keep fitting.
    check("preflight_identity signature is unchanged but for the Codex ask",
          str(inspect.signature(li.preflight_identity)),
          "(project: 'Path', model: 'str' = 'fable', ask=None) -> 'tuple[str, str]'")
finally:
    li.AI_LABS = real_labs
    shutil.rmtree(root, ignore_errors=True)

# The refusal: every sibling sentence is in the project's own files.
root = Path(tempfile.mkdtemp(prefix="loam-decoy-test-"))
try:
    li.AI_LABS = root / "ai-labs"
    project = build(root, {"a": [SHARED], "b": [SHARED, "Too short."]})
    try:
        li.pick_decoy(project, rng=random.Random(1))
        raised = None
    except li.IdentityError as exc:
        raised = str(exc)
    check("no eligible sentence anywhere raises IdentityError",
          raised, "no decoy could be drawn from a sibling soul")
finally:
    li.AI_LABS = real_labs
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
