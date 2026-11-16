#!/usr/bin/env python3
"""Backfill `surface:` (and `session_id:` where known) on existing transcripts.

Surface is recorded as a positive fact, never inferred from an absence. Two
rules, each requiring affirmative evidence:

  telegram    — the file carries a telegram_chat_id, which only the chassis
                Telegram surface ever wrote.
  claude-code — a sidecar in agents/state/transcripts/ maps a Claude Code
                session id to this exact path, i.e. this runtime wrote it.

A file matching neither is listed and left alone. "No telegram_chat_id,
therefore Claude Code" is exactly the inference this exists to remove: it
would mislabel any conversation that was never on Telegram.

Frontmatter only: keys are appended after the existing ones, nothing is
reordered, no body is touched, no file is renamed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_config as lc  # noqa: E402

CONVERSATIONS = lc.VAULT / lc.vault_folder("conversations")
SIDECARS = lc.STATE / "transcripts"
DRY = "--apply" not in sys.argv


def sidecar_index() -> dict[str, str]:
    """path -> session_id, for transcripts this runtime is known to have written."""
    index: dict[str, str] = {}
    for f in SIDECARS.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if d.get("path"):
            index[d["path"]] = f.stem
    return index


def split_frontmatter(text: str):
    if not text.startswith("---\n"):
        return None, None, None
    end = text.find("\n---", 4)
    if end == -1:
        return None, None, None
    return text[:4], text[4:end], text[end:]


def main() -> None:
    index = sidecar_index()
    counts = {"telegram": 0, "claude-code": 0, "already": 0, "unmatched": 0}
    unmatched: list[str] = []

    for path in sorted(CONVERSATIONS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        head, fm, rest = split_frontmatter(text)
        if fm is None:
            unmatched.append(f"{path.name} (no parseable frontmatter)")
            counts["unmatched"] += 1
            continue
        if "\nsurface:" in "\n" + fm:
            counts["already"] += 1
            continue

        session_id = index.get(str(path))
        if "telegram_chat_id:" in fm:
            surface, sid = "telegram", None
        elif session_id:
            surface, sid = "claude-code", session_id
        else:
            unmatched.append(path.name)
            counts["unmatched"] += 1
            continue

        added = f"surface: {surface}"
        if sid:
            added += f"\nsession_id: {sid}"
        new_text = head + fm.rstrip("\n") + "\n" + added + rest
        counts[surface] += 1
        if not DRY:
            path.write_text(new_text, encoding="utf-8")

    print(f"{'DRY RUN' if DRY else 'APPLIED'}")
    for k, v in counts.items():
        print(f"  {k:<12} {v}")
    if unmatched:
        print(f"\n  MATCHED NO RULE — left untouched ({len(unmatched)}):")
        for name in unmatched:
            print(f"    {name}")


if __name__ == "__main__":
    main()
