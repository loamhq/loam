"""Shared locations and seed text for persona instructions."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import loam_config as lc
from loam_memory import MAP_LAYER as MAP, UNDERSTANDING_LAYER as UNDERSTANDING

# From this file's own tree, as bin/provision finds its lib (2026-11-09):
# live, that is the checkout; in a job's tree or a landing's, that tree, so
# a test there renders and resolves the text the tree carries.
LOAM_TEXT = Path(__file__).resolve().parent.parent / "docs" / "persona" / "loam.md"
OWNER_PAGE = lc.WORKSPACES / "_shared" / "owner.md"


def owner_page() -> Path:
    """Legacy shared owner page under workspaces (overridable by ``LOAM_AI_LABS``).
    Fresh installs never create it; existing installs keep loading it while it exists."""
    root = os.environ.get("LOAM_AI_LABS")
    return Path(root) / "_shared" / "owner.md" if root else OWNER_PAGE


# The one line a new agent's map starts with (2026-11-18): Loam's only words
# in it, and the agent's to keep or cut.
MAP_SEED = "# Map\n\nWhat loads with me on every run. It points to things; it does not hold them.\n"


def display_name_line(workspace: Path) -> str:
    """Give a run the current name even if its files predate a rename."""
    from loam_agents import card
    from loam_profile import display_name
    agent = workspace.name
    name = display_name(agent, card(agent, workspace.parent))
    return f"Your current name in Loam is “{name}”."


def reach_view() -> Path:
    """Runtime-owned import for installs with the former factory capability text."""
    return lc.STATE / "instructions" / "reach.md"


def _current_reach(text: str) -> str:
    text = text.replace("\nthreads and boards, settings, the Market, the reviewer) is the shared skill",
                        "\nthreads, settings, the Market, the reviewer) is the shared skill")
    return re.sub(r"(?m)^(  edit one\)\.) Boards are in `[^`\n]+`; General's is\n"
                  r"  `[^`\n]+`\. (Post with `mcp__engineer__thread`)$", r"\1 \2", text)


def reach_import(source: Path) -> Path:
    """Keep direct loading for current templates and individually written guidance."""
    try:
        original = source.read_text("utf-8")
    except FileNotFoundError:
        return source
    return source if _current_reach(original) == original else reach_view()


def refresh_reach_import(project: Path) -> None:
    """Refresh only when the generated project instructions import the view.

    The owner's shared source stays editable. Re-read it at provider startup so
    personal additions and removals take effect without a provisioning cycle.
    """
    view = reach_view()
    wrapper = project / "CLAUDE.md"
    if not wrapper.is_file() or f"@{view}" not in wrapper.read_text("utf-8").splitlines():
        return
    source = lc.WORKSPACES / "_shared" / "reach.md"
    content = _current_reach(source.read_text("utf-8"))
    # Nested relative imports keep their original base in the shared workspace.
    content = re.sub(r"(?m)^@([^\s]+)$",
                     lambda m: "@" + str(source.parent / Path(m[1]).expanduser()), content)
    content = f"Shared guidance from `{source}`. Make edits there.\n\n" + content
    if view.is_file() and view.read_text("utf-8") == content:
        return
    view.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=view.parent,
                                     prefix=".reach-", delete=False) as tmp:
        tmp.write(content)
    try:
        os.replace(tmp.name, view)
    finally:
        Path(tmp.name).unlink(missing_ok=True)
