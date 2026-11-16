#!/usr/bin/env python3
"""Read-only git history of the ai-labs repository, as an MCP server.

Built for the reviewer (Move 11, 2026-09-09). The reviewer judges each
persona's workspace against the commits that changed it -- "a fact stated
with a date can be checked against the commit that changed it" -- and it has
no shell, because a shell cannot be made read-only: `git log --output=<file>`
writes a file, and so does `git show`. So history arrives through four tools
that each run one fixed git command with the caller's arguments passed as
list elements, never through a shell, and every path is resolved under the
repository before git sees it. Nothing here writes.

The repository auto-commits every fifteen minutes with a timestamp for a
message, so a commit's date is its provenance and its message says nothing
else. Tool output is bounded by CHAR_BUDGET with an `offset` to continue, the
way read_epub bounds a chapter.

    log       commits that touched a path, newest first, with the files each
              one changed (A/M/D)
    diff      what changed in a path between two commits, or since a date
    blame     which commit and date last touched each line of a file
    file_at   a file's content as it was at a commit
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import sys as _sys
_sys.path.append(str(Path(__file__).resolve().parent.parent / "lib"))
import loam_config as lc  # noqa: E402  constants only; nothing here holds state

from mcp.server import MCPServer

REPO = Path(os.environ.get("LOAM_HISTORY_REPO") or lc.WORKSPACES).resolve()
CHAR_BUDGET = 60_000
LOG_LIMIT_MAX = 200

COMMIT_RE = re.compile(r"^[0-9a-fA-F]{4,40}$|^HEAD$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?$")

mcp = MCPServer("loam-history")


def _error(error: str, detail: str | None = None) -> dict[str, Any]:
    return {"ok": False, "error": error, "detail": detail}


def _rel(path: str) -> str | None:
    """The path relative to the repository, or None if it is not inside it.

    Resolved so a symlink or `..` cannot point outside; refused if it starts
    with `-` after that, so it can never be read by git as an option. The
    `--` separator below is the second wall for the same hole.
    """
    if not path:
        return "."
    raw = Path(path)
    candidate = raw if raw.is_absolute() else REPO / raw
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(REPO):
        return None
    rel = str(resolved.relative_to(REPO)) or "."
    return None if rel.startswith("-") else rel


def _git(*args: str) -> tuple[int, str, str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, check=False, timeout=60,
    )
    return out.returncode, out.stdout, out.stderr


def _page(text: str, offset: int) -> dict[str, Any]:
    offset = max(0, int(offset or 0))
    chunk = text[offset:offset + CHAR_BUDGET]
    nxt = offset + len(chunk)
    return {
        "ok": True,
        "text": chunk,
        "offset": offset,
        "next_offset": nxt if nxt < len(text) else None,
        "total_chars": len(text),
    }


def _commit_ok(value: str | None) -> bool:
    return value is None or bool(COMMIT_RE.match(value))


@mcp.tool(
    name="log",
    description=(
        "Commits that touched a path in the ai-labs repository, newest first, "
        "each with its date and the files it added (A), modified (M) or "
        "deleted (D). `path` is a workspace, folder or file under ai-labs "
        "(absolute or repository-relative). `since` is a date (YYYY-MM-DD, "
        "optionally with a time) and limits to commits after it. `limit` "
        "caps the count (default 50). Commit messages are timestamps: the "
        "date is the provenance."
    ),
    annotations={"readOnlyHint": True},
)
def log(path: str, since: str | None = None, limit: int = 50,
        offset: int = 0) -> dict[str, Any]:
    rel = _rel(path)
    if rel is None:
        return _error(f"{path!r} is not inside {REPO}")
    if since and not DATE_RE.match(since):
        return _error(f"since must be a date like 2026-09-01; got {since!r}")
    limit = max(1, min(int(limit or 50), LOG_LIMIT_MAX))
    args = ["log", f"--max-count={limit}", "--date=iso-strict",
            "--format=%h %ad", "--name-status"]
    if since:
        args.append(f"--since={since}")
    args += ["--", rel]
    code, out, err = _git(*args)
    if code != 0:
        return _error("git log failed", err.strip()[:500])
    return {**_page(out, offset), "path": rel, "commits_returned":
            sum(1 for line in out.splitlines() if re.match(r"^[0-9a-f]{7,} ", line))}


@mcp.tool(
    name="diff",
    description=(
        "What changed in a path. Either `since` (a date: everything from the "
        "last commit at or before that date up to HEAD) or `from_commit` and "
        "optionally `to_commit` (default HEAD). `stat_only` returns the "
        "per-file summary instead of the full patch. Long output is paged: "
        "pass `offset` from `next_offset` to continue."
    ),
    annotations={"readOnlyHint": True},
)
def diff(path: str, since: str | None = None, from_commit: str | None = None,
         to_commit: str | None = None, stat_only: bool = False,
         offset: int = 0) -> dict[str, Any]:
    rel = _rel(path)
    if rel is None:
        return _error(f"{path!r} is not inside {REPO}")
    if not _commit_ok(from_commit) or not _commit_ok(to_commit):
        return _error("commits are hex hashes (or HEAD)")
    if since:
        if not DATE_RE.match(since):
            return _error(f"since must be a date like 2026-09-01; got {since!r}")
        code, out, err = _git("rev-list", "-1", f"--before={since}", "HEAD")
        base = out.strip()
        if code != 0 or not base:
            return _error(f"no commit at or before {since}", err.strip()[:300])
    elif from_commit:
        base = from_commit
    else:
        return _error("pass `since` (a date) or `from_commit` (a hash)")
    args = ["diff", "--no-color", "--no-ext-diff"]
    if stat_only:
        args.append("--stat=120")
    args += [base, to_commit or "HEAD", "--", rel]
    code, out, err = _git(*args)
    if code != 0:
        return _error("git diff failed", err.strip()[:500])
    return {**_page(out, offset), "path": rel, "base": base,
            "head": to_commit or "HEAD"}


@mcp.tool(
    name="blame",
    description=(
        "For each line of a file, the commit and date that last changed it, "
        "then the line. `start` and `end` are 1-based line numbers to bound "
        "the range (default: the whole file)."
    ),
    annotations={"readOnlyHint": True},
)
def blame(path: str, start: int | None = None, end: int | None = None,
          offset: int = 0) -> dict[str, Any]:
    rel = _rel(path)
    if rel is None:
        return _error(f"{path!r} is not inside {REPO}")
    if not (REPO / rel).is_file():
        return _error(f"{rel} is not a file in the working tree")
    args = ["blame", "--date=short", "-w"]
    if start or end:
        args.append(f"-L{start or 1},{end or ''}")
    args += ["--", rel]
    code, out, err = _git(*args)
    if code != 0:
        return _error("git blame failed", err.strip()[:500])
    lines = []
    for line in out.splitlines():
        m = re.match(r"^\^?([0-9a-f]+)\s+(?:\(.*?)?(\d{4}-\d{2}-\d{2})\s+(\d+)\)\s?(.*)$", line)
        lines.append(f"{m.group(1)[:7]} {m.group(2)} {m.group(3):>5}| {m.group(4)}"
                     if m else line)
    return {**_page("\n".join(lines), offset), "path": rel}


@mcp.tool(
    name="file_at",
    description=(
        "A file's content as it was at a commit (a hash from `log`, or a "
        "date via `since`: the last commit at or before that date). Use it "
        "to see what a file said before a change."
    ),
    annotations={"readOnlyHint": True},
)
def file_at(path: str, commit: str | None = None, since: str | None = None,
            offset: int = 0) -> dict[str, Any]:
    rel = _rel(path)
    if rel is None:
        return _error(f"{path!r} is not inside {REPO}")
    if not _commit_ok(commit):
        return _error("commit is a hex hash")
    if since:
        if not DATE_RE.match(since):
            return _error(f"since must be a date like 2026-09-01; got {since!r}")
        code, out, err = _git("rev-list", "-1", f"--before={since}", "HEAD")
        commit = out.strip()
        if code != 0 or not commit:
            return _error(f"no commit at or before {since}", err.strip()[:300])
    if not commit:
        return _error("pass `commit` (a hash) or `since` (a date)")
    code, out, err = _git("show", f"{commit}:{rel}")
    if code != 0:
        return _error(f"{rel} at {commit} could not be read", err.strip()[:500])
    return {**_page(out, offset), "path": rel, "commit": commit}


if __name__ == "__main__":
    mcp.run()
