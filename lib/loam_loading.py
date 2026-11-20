"""Shared locations and seed text for persona instructions."""

from __future__ import annotations

import os
from pathlib import Path

import loam_config as lc
from loam_memory import MAP_LAYER as MAP

# From this file's own tree, as bin/provision finds its lib (2026-11-09):
# live, that is the checkout; in a job's tree or a landing's, that tree, so
# a test there renders and resolves the text the tree carries.
LOAM_TEXT = Path(__file__).resolve().parent.parent / "docs" / "persona" / "loam.md"
OWNER_PAGE = lc.WORKSPACES / "_shared" / "owner.md"


def owner_page() -> Path:
    """The shared owner's page, under the workspaces a test may point
    elsewhere (``LOAM_AI_LABS``, as lib/loam_agents.py reads it), so no test
    writes the install's own."""
    root = os.environ.get("LOAM_AI_LABS")
    return Path(root) / "_shared" / "owner.md" if root else OWNER_PAGE

# What the shared page holds when Loam makes it: a heading and one line, so
# the import resolves and the page says what it is. The words under it are
# the owner's, and nothing here writes them.
OWNER_PAGE_SEED = (
    "# The owner's words\n\n"
    "Rules the owner gave every agent, in their words, each with its date.\n"
)

# The one line a new agent's map starts with (2026-11-18): Loam's only words
# in it, and the agent's to keep or cut.
MAP_SEED = "# Map\n\nWhat loads with me on every run. It points to things; it does not hold them.\n"

def ensure_owner_page() -> bool:
    """Make the shared owner's page if there is none. Never rewrites one.
    Returns whether it was made."""
    page = owner_page()
    if page.exists():
        return False
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(OWNER_PAGE_SEED, encoding="utf-8")
    return True
