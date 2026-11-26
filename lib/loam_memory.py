"""Identify automatically loaded Markdown files and their size limits. The boundary
guard enforces the limit in guarded lanes. Wake and lane checks report oversized
files and warn near the limit.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# ONE authority for the limit. bin/provision renders it into CLAUDE.md,
# boundary_guard.py refuses past it, bin/wake and bin/lane-check report it
# and never refuse a run.
# Characters, not bytes: it is what an agent can reason about, and what the
# CLAUDE.md sentence promises.
LIMIT_CHARS = 10_000

# The earlier warning, kept beside the limit so the two cannot drift apart.
# A file between this and LIMIT_CHARS is reported -- one line in the failure
# log, at most one per night -- and the run proceeds exactly
# as it would have. Nothing refuses at this number; the edit-time guard in
# hooks/boundary_guard.py knows only LIMIT_CHARS.
#
# Why it exists: the two files closest to the limit on 2026-09-07 (9,957 and
# 9,750 characters) are rewritten by their agents on every wake, and grew on
# two of the three nights before. A warning that first arrives at 10,001 is
# a warning with no room left to act on; this one arrives with a thousand
# characters still to spend.
NEAR_LIMIT_CHARS = 9_000

# Nothing refuses a run over the limit (2026-11-18). From 2026-09-11 to
# 2026-11-18 bin/wake refused an over-limit lane file (OVER_LIMIT_REFUSED_FROM,
# gone); the owner's word of 2026-11-08 had made the limit a warning, and the
# refusal outlived it by ten days, keeping one persona's wake dark two nights
# over 9 characters. Every reporter now warns and the run goes ahead.

MAP_LAYER = "map.md"
UNDERSTANDING_LAYER = "understanding.md"
# ONE authority for the understanding page's soft size mark (characters).
# Post-write hooks notice when a write leaves the page past this mark; nothing
# refuses, truncates or repeats it.
UNDERSTANDING_MARK_CHARS = 4_000
CRON_LANE_DIR = "crons"

FRONTMATTER_RE = re.compile(r"\A---\r?\n(?:.*?\r?\n)?---\r?\n*", re.DOTALL)


class LayerError(Exception):
    """An auto-loaded file could not be established. Always fatal on a wake."""


def load_config(project_dir: Path) -> dict:
    path = project_dir / "agent.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LayerError(f"unreadable agent config at {path}: {exc}") from exc


def project_dir_from(payload: dict) -> Path:
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
    if not raw:
        raise LayerError("cannot locate the agent project directory")
    return Path(raw)


def strip_frontmatter(text: str) -> str:
    return FRONTMATTER_RE.sub("", text, count=1)


def is_auto_loaded(target: Path, workspace: Path) -> bool:
    """Identify automatic context by its position in the workspace. Include the universal
    loading files and Markdown instructions in the cron lane folder.
    """
    try:
        target = target.resolve()
        workspace = workspace.resolve()
    except OSError:
        return False
    if target == workspace / MAP_LAYER:
        return True
    lanes = workspace / CRON_LANE_DIR
    return target.is_relative_to(lanes) and target.suffix == ".md"


def is_understanding(target: Path, workspace: Path) -> bool:
    """Whether ``target`` is this persona's understanding page."""
    try:
        return target.resolve() == (workspace / UNDERSTANDING_LAYER).resolve()
    except OSError:
        return False


def understanding_notice(raw: str, size: int) -> str:
    """The one-shot post-write notice when an understanding page passes its mark."""
    return (
        f"{raw!r} is now {size:,} characters, over the "
        f"{UNDERSTANDING_MARK_CHARS:,}-character mark for your understanding "
        f"page. The edit landed. This page loads on every run, so keep it "
        f"to what matters."
    )


def over_limit(text: str) -> bool:
    return len(text) > LIMIT_CHARS


def near_limit(size: int) -> bool:
    """Over the warning threshold but not over the limit. Sizes in characters."""
    return NEAR_LIMIT_CHARS < size <= LIMIT_CHARS


def near_limit_reason(name: str, size: int, path: Path) -> str:
    """The one sentence both reporters write, so the log reads the same from
    a wake and from the nightly lane check."""
    return (
        f"`{name}` is {size:,} characters, within "
        f"{LIMIT_CHARS - NEAR_LIMIT_CHARS:,} characters of the "
        f"{LIMIT_CHARS:,}-character limit on files that load automatically, "
        f"with {LIMIT_CHARS - size:,} to spare ({path}). A warning, not a "
        f"refusal: the file still loads and the run went ahead."
    )


def over_limit_reason(name: str, size: int, path: Path,
                      run: str = "the run went ahead") -> str:
    """The sentence both reporters write for a file over the limit. It says
    the run went ahead, because it did: nothing refuses a run over it."""
    return (
        f"`{name}` is {size:,} characters, over the {LIMIT_CHARS:,}-character "
        f"limit on files that load automatically, by {size - LIMIT_CHARS:,} "
        f"({path}). Reported, not refused: the file still loads whole and "
        f"{run}. Trim it when you next can."
    )


def resolve_lane(project_dir: Path, lane: str) -> tuple[str, str, Path]:
    """Return ``(agent_name, lane_text, lane_path)`` or raise LayerError.

    The text comes back with leading frontmatter stripped, because the caller
    is going to hand it to ``--append-system-prompt-file`` and that route
    delivers exactly what the file holds.
    """
    config = load_config(project_dir)
    agent = config["name"]
    workspace = Path(config["workspace"])
    path = workspace / lane

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LayerError(
            f"{agent} cannot read its lane file at {path}: {exc}"
        ) from exc

    stripped = strip_frontmatter(text).strip()
    if not stripped:
        raise LayerError(f"{agent}'s lane file at {path} is empty")
    return agent, stripped, path


def front_matter(text: str) -> dict[str, str]:
    """The leading frontmatter block's flat ``key: value`` pairs, as written.

    A hand-rolled two-line parser rather than a YAML dependency: these blocks
    are written by bin/provision and by the agents themselves and only ever
    hold flat lines, and the guard that calls this runs on every single tool
    call.

    ``partition`` splits on the FIRST colon only, so a value may contain one --
    which ``schedule: daily at 05:00 PT`` relies on entirely.
    """
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    body = match.group(0).split("---", 2)[1] if "---" in match.group(0) else ""
    out: dict[str, str] = {}
    for line in body.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            out[key.strip()] = value.strip().strip("\"'")
    return out
