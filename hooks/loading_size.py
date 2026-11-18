#!/usr/bin/env python3
"""PostToolUse hook: tells a persona what a write did to a file that loads
on every run. It never refuses anything.

Since 2026-11-08 (the owner's word, "the authority move") a persona's reach is
the whole box -- shell, sudo, every path -- and what it should and should not
do lives in its config, which loads on every run. Runtime mechanisms are for
capacity and recovery, never for permission. This hook replaced
hooks/boundary_guard.py on the persona lanes. The guard refused writes by
path and refused an edit that took a loading file over the limit; this one
runs AFTER the write, so the write has landed, and puts three things in front
of the agent in the turn where acting on them is one more edit away:

- the size of a loading file past LIMIT_CHARS, with the limit;
- a filename Android cannot store (it would never reach the phone);
- `_active.md` losing the frontmatter that makes it load at all (the loader
  fails silently without it).

It reads the file as it now is on disk rather than modelling the edit, so
every tool that writes is covered the same way, on both providers: Claude
Code's Write/Edit/MultiEdit, the loam server's move_file, and Codex's
apply_patch. The engineer and reviewer lanes keep the guard.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from loam_memory import (  # noqa: E402
    LIMIT_CHARS,
    LayerError,
    is_auto_loaded,
    load_config,
    project_dir_from,
    style_frontmatter_intact,
)

# Each tool that leaves content at a path, and the field naming that path.
RESULT_FIELDS = {
    "Write": ("file_path",),
    "Edit": ("file_path",),
    "MultiEdit": ("file_path",),
    "NotebookEdit": ("notebook_path",),
    "mcp__loam__move_file": ("destination",),
    "mcp__images__generate_image": ("target_path",),
    "mcp__composition__generate_pdf": ("target_path",),
}

ANDROID_ILLEGAL = set('?:*"<>|')

# Codex's apply_patch: the files an Add, an Update or a Move leaves behind.
PATCH_HEADER = re.compile(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$")


def patch_paths(patch: str, cwd: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        match = PATCH_HEADER.match(line)
        if not match:
            continue
        kind, raw = match.group(1), match.group(2).strip()
        path = os.path.join(cwd, raw)
        if kind == "Move to" and paths:
            paths[-1] = path
        elif kind != "Delete File":
            paths.append(path)
    return paths


def written_paths(payload: dict) -> list[str]:
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if tool == "apply_patch":
        patch = tool_input.get("command")
        if isinstance(patch, list):  # an argv, the patch is its last element
            patch = patch[-1] if patch else ""
        return patch_paths(str(patch or ""), payload.get("cwd") or os.getcwd())
    return [str(tool_input[f]) for f in RESULT_FIELDS.get(tool, ())
            if tool_input.get(f)]


def warnings_for(raw: str, workspace: Path | None) -> list[str]:
    path = Path(os.path.abspath(raw))
    out = []
    bad = sorted(set(path.name) & ANDROID_ILLEGAL)
    if bad:
        out.append(
            f"{raw!r} has {''.join(bad)!r} in its filename, which Android "
            f"cannot store: the file will never reach the phone. Rename it "
            f"(mcp__loam__move_file) if it should.")
    if workspace is None or not is_auto_loaded(path, workspace):
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    if len(text) > LIMIT_CHARS:
        out.append(
            f"{raw!r} is now {len(text):,} characters, over the "
            f"{LIMIT_CHARS:,}-character limit on files that load into every "
            f"run. The edit landed. This file's size is paid on every run: "
            f"move the bulk to a file you read on demand and leave an index "
            f"line pointing at it, or cut.")
    if path.name == "_active.md" and not style_frontmatter_intact(text):
        out.append(
            f"{raw!r} no longer opens with the frontmatter block (name, "
            f"description, keep-coding-instructions) that makes it load into "
            f"a conversation. Without it the whole layer silently stops "
            f"loading. Put the `---` block back exactly as it was.")
    return out


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    paths = written_paths(payload)
    if not paths:
        sys.exit(0)
    try:
        workspace = Path(load_config(project_dir_from(payload))["workspace"])
    except (LayerError, KeyError, OSError, ValueError):
        workspace = None
    found = [w for raw in paths for w in warnings_for(raw, workspace)]
    if found:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "Loam: " + " ".join(found),
        }}))
    sys.exit(0)


if __name__ == "__main__":
    main()
