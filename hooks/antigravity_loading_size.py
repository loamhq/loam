#!/usr/bin/env python3
"""Antigravity lifecycle hook: post-write loading-size notices for personas.

Antigravity's PostToolUse hook cannot inject context directly (it expects ``{}``),
while PostInvocation runs immediately after tool calls finish and can inject a
``systemMessage`` step before the next model invocation. This hook pairs the
two: PostToolUse inspects written files via ``hooks/loading_size.py`` and stores
any one-shot notice under ``conversationId``; PostInvocation pops it once and
injects it, never refusing, truncating or repeating.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

_HOOKS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOKS_DIR.parent / "lib"))
sys.path.insert(0, str(_HOOKS_DIR))

import loam_config as lc  # noqa: E402
from loading_size import warnings_for  # noqa: E402
from loam_memory import LayerError, load_config  # noqa: E402

WRITE_FIELDS = {
    "write_file": ("Path",),
    "edit_file": ("Path",),
    "write_to_file": ("TargetFile",),
    "replace_file_content": ("TargetFile",),
    "multi_replace_file_content": ("TargetFile",),
}
STALE_SECONDS = 3600


def pending_dir() -> Path:
    override = os.environ.get("LOAM_AGY_HOOK_STATE")
    return Path(override) if override else lc.STATE / "hooks" / "agy-pending"


def _pending_file(conversation_id: str) -> Path | None:
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        return None
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", conversation_id.strip())[:120]
    return pending_dir() / f"{safe}.json" if safe else None


def _sweep_stale(folder: Path) -> None:
    cutoff = time.time() - STALE_SECONDS
    try:
        for item in folder.glob("*.json"):
            try:
                if item.stat().st_mtime < cutoff:
                    item.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        pass


def _written_paths(payload: dict) -> list[str]:
    tc = payload.get("toolCall") or {}
    name = str(tc.get("name") or "")
    args = tc.get("args") or {}
    if not isinstance(args, dict):
        return []
    fields = WRITE_FIELDS.get(name)
    if fields is None and name.endswith("move_file"):
        fields = ("destination",)
    if not fields:
        return []
    cwd = payload.get("cwd") or ""
    out: list[str] = []
    for field in fields:
        raw = args.get(field)
        if not isinstance(raw, str) or not raw.strip():
            continue
        p = raw.strip()
        if cwd and not os.path.isabs(p):
            p = os.path.join(cwd, p)
        out.append(p)
    return out


def _persona_config(payload: dict) -> tuple[dict, Path | None]:
    candidates: list[str] = []
    for ws in payload.get("workspacePaths") or []:
        if isinstance(ws, str) and ws:
            candidates.append(ws)
    if isinstance(payload.get("cwd"), str) and payload["cwd"]:
        candidates.append(payload["cwd"])
    for raw in candidates:
        try:
            cfg = load_config(Path(raw))
            ws = cfg.get("workspace")
            if ws:
                return cfg, Path(ws)
        except (LayerError, KeyError, OSError, ValueError):
            continue
    return {}, None


def handle_post_tool(payload: dict) -> dict:
    if payload.get("error"):
        return {}
    paths = _written_paths(payload)
    if not paths:
        return {}
    config, workspace = _persona_config(payload)
    if workspace is not None:
        agent_id = str(config.get("id") or config.get("name") or workspace.name).strip().lower()
        vault = Path(config["vault"]) if config.get("vault") else None
        if agent_id:
            try:
                import loam_recent  # noqa: PLC0415
                for raw in paths:
                    loam_recent.record_write(raw, kind="agent", actor=agent_id,
                                             source="hook", vault=vault)
            except Exception:  # noqa: BLE001
                pass
    found = [w for raw in paths for w in warnings_for(raw, workspace)]
    if not found:
        return {}
    target = _pending_file(payload.get("conversationId") or "")
    if target is None:
        return {}
    target.parent.mkdir(parents=True, exist_ok=True)
    _sweep_stale(target.parent)
    existing: list[str] = []
    if target.is_file():
        try:
            prev = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(prev, list):
                existing = [str(x) for x in prev if isinstance(x, str)]
        except (OSError, ValueError):
            existing = []
    merged = list(dict.fromkeys([*existing, *found]))
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(merged), encoding="utf-8")
    tmp.replace(target)
    return {}


def handle_post_invocation(payload: dict) -> dict:
    target = _pending_file(payload.get("conversationId") or "")
    if target is None or not target.is_file():
        return {}
    try:
        items = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        items = []
    finally:
        target.unlink(missing_ok=True)
    if not isinstance(items, list):
        return {}
    messages = [str(x) for x in items if isinstance(x, str) and x]
    if not messages:
        return {}
    return {
        "injectSteps": [{
            "systemMessage": {
                "systemMessage": "Loam: " + " ".join(messages),
            },
        }],
    }


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print("{}")
        sys.exit(0)
    event = payload.get("hookEventName")
    if event == "PostToolUse":
        out = handle_post_tool(payload)
    elif event == "PostInvocation":
        out = handle_post_invocation(payload)
    else:
        out = {}
    print(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
