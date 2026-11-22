#!/usr/bin/env python3
"""PostToolUse hook: tells a persona what a write did to a file that loads
on every run. It never refuses anything.

Since 2026-11-08 (the owner's word, "the authority move") a persona's reach is
the whole box -- shell, sudo, every path -- and what it should and should not
do lives in its config, which loads on every run. Runtime mechanisms are for
capacity and recovery, never for permission. This hook replaced
hooks/boundary_guard.py on the persona lanes. It runs after the edit and
warns when a loading file exceeds the limit or Android cannot store its name.

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
)

# Each tool that leaves content at a path, and the field naming that path.
RESULT_FIELDS = {
    "Write": ("file_path",),
    "Edit": ("file_path",),
    "MultiEdit": ("file_path",),
    "NotebookEdit": ("notebook_path",),
    "mcp__loam__move_file": ("destination",),
    "mcp__images__generate_image": ("target_path",),
    "mcp__composition__generate_image": ("target_path",),   # a session from before 2026-11-18
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
    return out


def _response_paths(payload: dict) -> list[str]:
    """Extract output paths returned by image/document tools when target_path was omitted."""
    tool = payload.get("tool_name", "")
    if tool not in ("mcp__images__generate_image", "mcp__composition__generate_image",
                    "mcp__composition__generate_pdf"):
        return []
    resp = payload.get("tool_response")
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except ValueError:
            return []
    if isinstance(resp, list):
        for item in resp:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                try:
                    resp = json.loads(item["text"])
                    break
                except ValueError:
                    continue
    if not isinstance(resp, dict):
        return []
    out: list[str] = []
    for key in ("paths", "vault_paths"):
        vals = resp.get(key)
        if isinstance(vals, list):
            out.extend(str(v) for v in vals if isinstance(v, str) and v)
    if isinstance(resp.get("path"), str) and resp["path"]:
        out.append(resp["path"])
    return out


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    try:
        config = load_config(project_dir_from(payload))
        workspace = Path(config["workspace"])
    except (LayerError, KeyError, OSError, ValueError):
        config = {}
        workspace = None
    paths = written_paths(payload)
    extra = _response_paths(payload)
    # Record write attribution for Files › Recent before returning warnings.
    all_written = list(dict.fromkeys([*paths, *extra]))
    if all_written and workspace is not None:
        agent_id = str(config.get("id") or workspace.name).strip().lower()
        vault = Path(config["vault"]) if config.get("vault") else None
        if agent_id:
            try:
                import loam_recent  # noqa: PLC0415
                for raw in all_written:
                    loam_recent.record_write(raw, kind="agent", actor=agent_id,
                                             source="hook", vault=vault)
            except Exception:  # noqa: BLE001 -- attribution must never block a tool
                pass
    if not paths:
        sys.exit(0)
    found = [w for raw in paths for w in warnings_for(raw, workspace)]
    if found:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "Loam: " + " ".join(found),
        }}))
    sys.exit(0)


if __name__ == "__main__":
    main()
