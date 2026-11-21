"""Retire the old starter board on an install that has yet to use it."""

from __future__ import annotations

import loam_agents
import loam_deploy
import loam_starter as starter
import loam_threads as threads
import loam_update as update


# The installer keeps Git history. Use the shipped templates for comparisons
# so even a one-byte change protects the person's copy. The last commit here
# is the first-week occasions starter, used by checkouts before the release.
LIST_STARTERS = (
    "012953416b90e08c808a184e1c4bce98b1b3c925",  # 0.2.16
    "1fc8914a00f3490e09128c8cf35976267bfc16af",  # 0.2.17
    "a51e4b24855b83f0a70dc96079a3ae7af0e0e160",  # 0.2.18
    "bc89663f785c9d99d5282aceeb536d617bc8c7eb",
)


def retire_hello_board() -> bool:
    """Use the original Hello ID and serialize the decision with chat and board edits."""
    with loam_agents.locked():
        seeded = starter.status()
        if (not isinstance(seeded, dict) or seeded.get("first_weeks") is not True
                or not seeded.get("thread") or not seeded.get("agent")):
            return False
        with threads.locked(threads.VAULT):
            try:
                meta, messages, _ = threads.read(seeded["thread"])
            except threads.ThreadError as exc:
                if exc.status != 404:
                    raise
                return False
            if not meta.get("hello") or (any(threads.is_owner(m["author"]) for m in messages)
                                         and not loam_deploy.unclaimed()):
                return False
            board = threads.VAULT / meta["board"]
            if board.is_symlink() or not board.is_file():
                return False
            original = board.read_bytes()
            prefix = update.prefix() + "docs/starter/"
            known_boards = (update.show(rev, prefix + "hello-board.md") for rev in LIST_STARTERS)
            if all(text is None or text.encode("utf-8") != original for text in known_boards):
                return False

            # The board was still stock. Move each unedited guidance file to
            # the current starter so the first reply won't point to a list.
            # Replace the board last so a retry can finish after a partial write.
            ws = loam_agents.ai_labs() / seeded["agent"]
            for name, replacement in starter.own_files().items():
                path = ws / name
                if path.is_symlink() or not path.is_file():
                    continue
                templates = (update.show(rev, prefix + "iris/" + name) for rev in LIST_STARTERS)
                for template in templates:
                    stock = starter.fill(template) if template is not None else None
                    if stock is not None and path.read_bytes() == stock.encode("utf-8"):
                        if stock != replacement:
                            threads._write_atomic(path, replacement)
                        break
            try:
                threads._replace_board(board, threads.empty_board(meta), threads._board_revision(original))
            except threads.ThreadError as exc:
                if exc.status != 409:
                    raise
                return False
            return True
