"""The release note section shared by the taggers and update delivery."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

DIRECTORY = "docs/releases"
AGENT_HEADING = "## For your agent"


def for_agent(text: str) -> str:
    """Read the agent section up to the next level-two heading."""
    sections = re.split(r"^##[ \t]+", text, flags=re.M)
    for section in sections[1:]:
        heading, _, body = section.partition("\n")
        if f"## {heading.strip()}" == AGENT_HEADING:
            return re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()
    return ""


def requirement(repo: Path, commit: str, version: str) -> str | None:
    """A tag refusal message, or None when the commit carries agent notes."""
    path = f"{DIRECTORY}/{version}.md"
    notes = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{path}"],
        capture_output=True, text=True, errors="replace", timeout=30,
    )
    if notes.returncode or not for_agent(notes.stdout):
        return f"add {path} with a nonempty '{AGENT_HEADING}' section (CONTRIBUTING.md)"
    return None
