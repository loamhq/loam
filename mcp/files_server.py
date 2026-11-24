#!/usr/bin/env python3
"""Listing and search of the reviewer's reach, as an MCP server.

Built for the reviewer on Antigravity (2026-11-15, bin/review). The
reviewer reads a workspace whole, so it must be able to list a folder and
search across files. On Claude Code that is Glob and Grep. On Antigravity
(1.5.9) the custom agent the reviewer runs as is given `view_file` and the
edit tools, but `list_dir`, `grep_search` and `find_by_name` are accepted
in its `tools:` list and then not offered: caused 2026-11-15, the session
listed its tools as read_file, write_file, edit_file and MCP. There, listing
and searching go through `run_command`, the shell, which the reviewer must
not have (reviewer/CLAUDE.md, "Your reach": a shell cannot be made read-only).
So listing and search come through this server, as history comes through
mcp/history_server.py: fixed code, pure Python, no subprocess, nothing here
writes on Antigravity. Codex has no native file tools in this lane, so the
launcher also enables text reads and Markdown writes to its memory and outbox.

    list    the entries under a folder, or the files matching a glob, with
            each one's size
    grep    the lines matching a regular expression in the files under a
            path, with file and line number

The reach is `LOAM_FILES_ROOTS` (paths joined by `:`), with
`LOAM_FILES_EXCLUDE` held out of it (the vault's `15_CONVERSATIONS/`, which
the reviewer does not read on either provider). Every path is resolved
before it is used, so a symlink or `..` cannot point outside. Output is
bounded by CHAR_BUDGET with an `offset` to continue.
"""

from __future__ import annotations

import fnmatch
import os
import re
import uuid
from pathlib import Path
from typing import Any

import sys as _sys
_sys.path.append(str(Path(__file__).resolve().parent.parent / "lib"))
from loam_mcp import MCPServer  # noqa: E402

CHAR_BUDGET = 60_000
LIST_MAX = 5_000
GREP_FILE_MAX = 2_000_000     # bytes; a larger file is skipped and said
SKIP_DIRS = {".git"}


def _paths(var: str) -> list[Path]:
    return [Path(p).resolve() for p in (os.environ.get(var) or "").split(":") if p]


ROOTS = _paths("LOAM_FILES_ROOTS")
EXCLUDE = _paths("LOAM_FILES_EXCLUDE")

mcp = MCPServer("loam-files")


def _error(error: str) -> dict[str, Any]:
    return {"ok": False, "error": error}


def _excluded(path: Path) -> bool:
    return any(path == e or path.is_relative_to(e) for e in EXCLUDE)


def _inside(path: Path) -> bool:
    return any(path == r or path.is_relative_to(r) for r in ROOTS) and not _excluded(path)


def _resolve(path: str) -> Path | None:
    if not path:
        return None
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return None
    return resolved if _inside(resolved) else None


def _reach() -> str:
    text = ", ".join(str(r) for r in ROOTS)
    if EXCLUDE:
        text += " (not " + ", ".join(str(e) for e in EXCLUDE) + ")"
    return text


def _page(text: str, offset: int) -> dict[str, Any]:
    offset = max(0, int(offset or 0))
    chunk = text[offset:offset + CHAR_BUDGET]
    nxt = offset + len(chunk)
    return {"ok": True, "text": chunk, "offset": offset,
            "next_offset": nxt if nxt < len(text) else None,
            "total_chars": len(text)}


def _walk(base: Path):
    """Every file under ``base``, sorted, ``.git`` and the excluded paths
    left out; symlinks are not followed out of the reach."""
    if base.is_file():
        yield base
        return
    for dirpath, dirnames, filenames in os.walk(base):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS
                             and _inside((here / d).resolve()))
        for name in sorted(filenames):
            path = here / name
            try:
                real = path.resolve()
            except OSError:
                continue
            if _inside(real) and real.is_file():
                yield path


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


@mcp.tool(
    name="list",
    description=(
        "List a folder, or find files by name. `path` is an absolute folder "
        "inside your reach. Without `pattern`, its entries one level down "
        "(folders end in /), with each file's size. With `pattern` (a glob on "
        "the path below `path`, like `**/*.md` or `skills/*/SKILL.md`), every "
        "file under it that matches, recursively. Hidden files and folders "
        "are included; `.git` is not."
    ),
    annotations={"readOnlyHint": True},
)
def list_(path: str, pattern: str | None = None, offset: int = 0) -> dict[str, Any]:
    base = _resolve(path)
    if base is None:
        return _error(f"{path!r} is not inside your reach: {_reach()}")
    if not base.exists():
        return _error(f"{base} does not exist")
    lines: list[str] = []
    if not pattern:
        if not base.is_dir():
            return _error(f"{base} is a file; read it with your file tool")
        for entry in sorted(base.iterdir(), key=lambda p: p.name):
            if entry.name in SKIP_DIRS:
                continue
            try:
                real = entry.resolve()
            except OSError:
                continue
            if not _inside(real):
                continue
            lines.append(f"{entry.name}/" if real.is_dir()
                         else f"{entry.name}\t{_size(entry):,} B")
    else:
        for file in _walk(base):
            rel = str(file.relative_to(base))
            if fnmatch.fnmatchcase(rel, pattern) or (
                    pattern.startswith("**/") and fnmatch.fnmatchcase(rel, pattern[3:])):
                lines.append(f"{rel}\t{_size(file):,} B")
                if len(lines) >= LIST_MAX:
                    lines.append(f"(stopped at {LIST_MAX} files; narrow the pattern)")
                    break
    text = "\n".join(lines) + ("\n" if lines else "")
    return {**_page(text or "(nothing)\n", offset), "path": str(base),
            "entries": len(lines)}


@mcp.tool(
    name="grep",
    description=(
        "Search file contents. `pattern` is a regular expression (Python "
        "syntax); `path` an absolute folder or file inside your reach; `glob` "
        "optionally limits to files whose path below `path` matches (like "
        "`*.md`); `ignore_case` for a case-blind match. Answers "
        "`file:line: text` for each matching line."
    ),
    annotations={"readOnlyHint": True},
)
def grep(pattern: str, path: str, glob: str | None = None,
         ignore_case: bool = False, offset: int = 0) -> dict[str, Any]:
    base = _resolve(path)
    if base is None:
        return _error(f"{path!r} is not inside your reach: {_reach()}")
    if not base.exists():
        return _error(f"{base} does not exist")
    try:
        rx = re.compile(pattern, re.I if ignore_case else 0)
    except re.error as exc:
        return _error(f"pattern is not a regular expression: {exc}")
    out: list[str] = []
    skipped: list[str] = []
    files = 0
    for file in _walk(base):
        rel = str(file.relative_to(base)) if base.is_dir() else file.name
        if glob and not (fnmatch.fnmatchcase(rel, glob)
                         or fnmatch.fnmatchcase(file.name, glob)):
            continue
        if _size(file) > GREP_FILE_MAX:
            skipped.append(rel)
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        files += 1
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                out.append(f"{file}:{n}: {line[:400]}")
    if skipped:
        out.append(f"(skipped {len(skipped)} file(s) over {GREP_FILE_MAX:,} B: "
                   + ", ".join(skipped[:20]) + ")")
    text = "\n".join(out) + ("\n" if out else "")
    return {**_page(text or "(no match)\n", offset), "path": str(base),
            "files_searched": files}


def read(path: str, offset: int = 0) -> dict[str, Any]:
    """Read a UTF-8 text file in the reviewer's reach. Use next_offset to continue."""
    source = _resolve(path)
    if source is None or not source.is_file():
        return _error("Choose a file inside the reviewer's reach.")
    try:
        with source.open(encoding="utf-8") as handle:
            text = handle.read(GREP_FILE_MAX + 1)
    except (OSError, UnicodeError) as exc:
        return _error(str(exc))
    if len(text) > GREP_FILE_MAX:
        return _error("This file exceeds the text read limit.")
    return _page(text, offset)


def write(path: str, text: str) -> dict[str, Any]:
    """Write or replace a Markdown file in memory or outbox; supply its full text."""
    target = Path(path)
    memory, outbox = (Path(os.environ[key]).resolve()
                      for key in ("LOAM_REVIEWER_MEMORY", "LOAM_REVIEWER_OUTBOX"))
    if ".." in target.parts or target.suffix != ".md":
        return _error("Choose a Markdown file in memory or outbox/<persona>/.")
    if target.parent == memory:
        root, persona = memory, None
    elif target.parent.parent == outbox:
        root, persona = outbox, target.parent.name
    else:
        return _error("Choose a Markdown file in memory or outbox/<persona>/.")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        root_fd = os.open(root, flags)
        try:
            folder_fd = root_fd
            if persona:
                try:
                    os.mkdir(persona, dir_fd=root_fd)
                except FileExistsError:
                    pass
                folder_fd = os.open(persona, flags, dir_fd=root_fd)
            try:
                # A replacement never writes through an existing symlink or hard link.
                temp = f".{uuid.uuid4().hex}.tmp"
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=folder_fd)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(text)
                    os.replace(temp, target.name, src_dir_fd=folder_fd, dst_dir_fd=folder_fd)
                finally:
                    try:
                        os.unlink(temp, dir_fd=folder_fd)
                    except FileNotFoundError:
                        pass
            finally:
                if folder_fd != root_fd:
                    os.close(folder_fd)
        finally:
            os.close(root_fd)
    except (OSError, UnicodeError) as exc:
        return _error(str(exc))
    return {"ok": True, "path": path}


if os.environ.get("LOAM_REVIEWER_MEMORY") and os.environ.get("LOAM_REVIEWER_OUTBOX"):
    mcp.tool(name="read", description=read.__doc__, annotations={"readOnlyHint": True})(read)
    mcp.tool(name="write", description=write.__doc__)(write)


if __name__ == "__main__":
    mcp.run()
