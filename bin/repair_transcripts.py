#!/usr/bin/env python3
"""Take the harness's own content out of transcripts written before the rule.

The transcript writer filed every user-role entry the CLI gave it, and the CLI
uses that role for two different things: what somebody typed, and whatever the
harness needed to hand the model next. So 14 files in ``15_CONVERSATIONS/``
carry task notifications -- whole engineer receipts pasted in, filed as
``### User`` turns -- and two of them also carry peer messages from another
Claude session. ``loam_transcript.person_text`` is the rule now and
``loam_transcript.repair_transcript`` applies it to a written file; this walks
the folder with it.

Reads the same rule as the writer, deliberately: a repair that decided for
itself what harness content is would drift from what the writer excludes, and
then a repaired file would be rewritten differently the next time its session
fired a Stop hook.

Turns only. Frontmatter is untouched -- the writer is these files' author and
this is the writer correcting itself, not a later edit by somebody else.

Idempotent, because it is a function of the text: a file with nothing to remove
is not written at all, and running it twice changes nothing the second time.

  python3 bin/repair_transcripts.py            # dry run, says what it would do
  python3 bin/repair_transcripts.py --apply
  python3 bin/repair_transcripts.py --show     # with the first line of each cut
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_config as lc  # noqa: E402

import loam_transcript as lt  # noqa: E402

CONVERSATIONS = lc.VAULT / lc.vault_folder("conversations")
APPLY = "--apply" in sys.argv
SHOW = "--show" in sys.argv


def main() -> None:
    directory = CONVERSATIONS
    for arg in sys.argv[1:]:
        if not arg.startswith("--"):
            directory = Path(arg)

    repaired: list[str] = []
    left: list[str] = []
    files = 0
    clean = 0
    turns_removed = 0
    turns_trimmed = 0

    for path in sorted(directory.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            left.append(f"{path.name}: unreadable ({exc})")
            continue
        result = lt.repair_transcript(text)
        if result.text is None:
            if result.dropped or result.trimmed:
                left.append(f"{path.name}: {result.note}")
            else:
                clean += 1
            continue

        files += 1
        turns_removed += len(result.dropped)
        turns_trimmed += len(result.trimmed)
        repaired.append(
            f"{path.name}: {result.turns_before} -> {result.turns_after} turns "
            f"({result.note})"
        )
        if SHOW:
            for first in result.dropped:
                repaired.append(f"      cut: {first[:100]}")
            for first in result.trimmed:
                repaired.append(f"      trimmed: {first[:100]}")
        if APPLY:
            path.write_text(result.text, encoding="utf-8")

    print(f"{'APPLIED' if APPLY else 'DRY RUN'}  {directory}")
    print(f"  files repaired      {files}")
    print(f"  turns removed       {turns_removed}")
    print(f"  turns trimmed       {turns_trimmed}")
    print(f"  files already clean {clean}")
    for line in repaired:
        print(f"    {line}")
    if left:
        print(f"\n  LEFT ALONE, needs a look ({len(left)}):")
        for line in left:
            print(f"    {line}")


if __name__ == "__main__":
    main()
