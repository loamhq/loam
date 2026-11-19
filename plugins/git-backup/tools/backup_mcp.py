#!/usr/bin/env python3
"""The Git backup tool for agents: a stdio MCP server with no dependencies
(JSON-RPC 2.0, one message per line). The work is backup_core.py's."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_core as core  # noqa: E402

PROTOCOL = "2025-06-18"
TREE = {"type": "string", "enum": ["vault", "spaces"],
        "description": "vault: the owner's vault. spaces: AI Spaces, every agent's workspace."}
TOOLS = [
    {"name": "backup_status",
     "description": ("Whether the vault and AI Spaces are backed up off this machine: for "
                     "each, its repository, when it was last pushed, a failure in plain words "
                     "if pushes are failing, files kept out for being too large, and how often "
                     "it backs up. Read it before telling the owner anything about backups."),
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "backup_connect",
     "description": ("Connect a tree to an empty private repository the owner made, on their "
                     "word, with the address they gave: SSH or the repository's web address "
                     "on any git host or a server of theirs, or the path of a folder on this "
                     "machine. Refuses a public repository. For an SSH remote, makes a key for "
                     "that one repository and answers with the public key and the steps for "
                     "the owner to add it with write access; a folder needs no key (a new or "
                     "empty one is made a repository). Then call backup_test."),
     "inputSchema": {"type": "object", "properties": {
         "tree": TREE, "remote": {"type": "string", "description": "The repository's address."}},
         "required": ["tree", "remote"]}},
    {"name": "backup_test",
     "description": ("Check that the key works for the tree's repository, after the owner "
                     "added it. On yes, the first backup starts at once."),
     "inputSchema": {"type": "object", "properties": {"tree": TREE}, "required": ["tree"]}},
    {"name": "backup_now",
     "description": "Back a tree (or both) up now, whatever the schedule says.",
     "inputSchema": {"type": "object", "properties": {"tree": TREE}}},
    {"name": "backup_disconnect",
     "description": ("Stop backing a tree up, on the owner's word. The repository and "
                     "what is in it are left alone."),
     "inputSchema": {"type": "object", "properties": {"tree": TREE}, "required": ["tree"]}},
    {"name": "backup_restore",
     "description": ("Bring a tree back from its repository onto this machine, on the owner's "
                     "word only: on a new install, to get the vault and the agents back. What "
                     "is here is kept aside first. On a tree with history of its own it refuses "
                     "unless replace is true, which needs the owner to have said so knowing it."),
     "inputSchema": {"type": "object", "properties": {
         "tree": TREE, "remote": {"type": "string", "description": "The repository, if the tree is not connected yet."},
         "replace": {"type": "boolean"}}, "required": ["tree"]}},
]


def run(name: str, args: dict) -> dict:
    tree = args.get("tree")
    try:
        if name == "backup_status":
            return core.status()
        if name == "backup_connect":
            return core.connect(tree, args.get("remote") or "")
        if name == "backup_test":
            return core.test(tree)
        if name == "backup_now":
            out = core.run(force=True, trees=[tree] if tree else None)
            return {"ok": all(r.get("ok") for r in out), "results": out}
        if name == "backup_disconnect":
            return core.disconnect(tree)
        if name == "backup_restore":
            return core.restore(tree, args.get("remote"), replace=bool(args.get("replace")))
        return {"ok": False, "error": f"no tool {name!r}"}
    except core.BackupError as exc:
        return {"ok": False, "error": str(exc)}
    except KeyError:
        return {"ok": False, "error": "tree is vault or spaces"}


def handle(msg: dict):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": (msg.get("params") or {}).get("protocolVersion") or PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "git-backup", "version": "1.0.0"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        out = run(params.get("name"), params.get("arguments") or {})
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}],
            **({"isError": True} if not out.get("ok") else {})}}
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
