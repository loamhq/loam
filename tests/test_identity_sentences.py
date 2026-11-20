#!/usr/bin/env python3
"""A line break separates sentences for the identity preflight, even without terminal punctuation."""

from __future__ import annotations

import sys
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


OPENING = "She keeps the whole room in view and notices who has gone quiet."
assert 45 <= len(OPENING) <= 110
SHORT = "She keeps the room in view."
assert len(SHORT) < 45
HALF_A = "a line that is cut off part way through and"
HALF_B = "carries on down here with no full stop"
assert len(HALF_A) < 45 and len(HALF_B) < 45
assert 45 <= len(HALF_A) + 1 + len(HALF_B) <= 110

# After a heading: the first sentence of the identity section is a candidate.
check("sentence right after a heading is returned",
      li._sentences(f"# Name\n\n{OPENING}\n"), [OPENING])
check("sentence right after a heading with no blank line between",
      li._sentences(f"## Name\n{OPENING}\n"), [OPENING])

# Too short: the heading is a boundary, not padding.
check("too-short sentence after a heading is not returned",
      li._sentences(f"# Name\n\n{SHORT}\n"), [])

# No newline: a fragment spanning two lines is never a candidate.
two_lines = f"{HALF_A}\n{HALF_B}\n"
check("piece spanning two lines without punctuation is rejected",
      li._sentences(two_lines), [])
check("the joined two-line piece is not returned either",
      f"{HALF_A} {HALF_B}" in li._sentences(two_lines), False)

# End of file with no trailing newline.
check("sentence ending the file with no trailing newline is returned",
      li._sentences(f"# Name\n\n{OPENING}"), [OPENING])

# Within a line: punctuation still splits, order is document order, and the
# band still applies to each piece.
second = "Then she says the one thing that lets the next thing happen."
assert 45 <= len(second) <= 110
long = "x" * 111 + "."
check("sentences on one line still split on punctuation, in order",
      li._sentences(f"# Name\n\n{OPENING} {SHORT} {second} {long}\n"),
      [OPENING, second])

# Hard wrap: each line of a wrapped paragraph is its own candidate.
wrapped = (
    "# Name\n\n"
    "Juno is a steward of capital who believes wealth should compound into\n"
    "freedom, resilience, and possibility, and he does not mistake the money\n"
    "for it.\n"
)
check("a hard-wrapped paragraph yields its in-band lines as candidates",
      li._sentences(wrapped),
      ["Juno is a steward of capital who believes wealth should compound into",
       "freedom, resilience, and possibility, and he does not mistake the money"])

# The invariant the probe relies on, across every fixture above.
every = (
    li._sentences(f"# Name\n\n{OPENING}\n") + li._sentences(two_lines)
    + li._sentences(wrapped) + li._sentences(f"# Name\n\n{OPENING} {second}\n")
)
check("no returned piece contains a newline",
      [s for s in every if "\n" in s], [])
check("every returned piece is within the 45 to 110 band",
      [s for s in every if not 45 <= len(s) <= 110], [])

print()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
