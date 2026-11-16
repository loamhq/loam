#!/usr/bin/env python3
"""read_epub as an MCP server -- the one custom tool that survives the move.

Claude Code brings Read/Write/Edit/Glob/Grep, web and subagents natively,
and skills are already the same on-disk format. Of the chassis's custom
tools only this one has no native equivalent: an epub is a zip of XHTML,
and the Read tool sees a binary blob. ``say`` and ``takes`` are
chassis-specific and do not move; ``search_vault`` is deliberately dropped.

Behaviour is ported from chassis/epub_reader.py so the agents read the same
tool on both runtimes:

* TOC mode (no ``chapter``) -- metadata plus the chapter list. Cheap, and
  the thing to call first.
* Read mode (``chapter`` as a 1-based index or a case-insensitive title
  substring) -- that chapter's plain text from ``offset``, bounded by
  CHAR_BUDGET, with ``next_offset`` when there is more.

Every failure path returns a structured result rather than raising: a
malformed archive or an ambiguous chapter should tell the agent what to do
next, not surface as a tool crash.

Read boundary: the roots in LOAM_EPUB_ROOTS (colon-separated). A path
outside them is refused before the archive is opened.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
from pathlib import Path
from typing import Any

import sys as _sys
_sys.path.append(str(Path(__file__).resolve().parent.parent / "lib"))
import loam_config as lc  # noqa: E402  constants only; nothing here holds state

from bs4 import BeautifulSoup
from ebooklib import ITEM_DOCUMENT, epub
from mcp.server import MCPServer

MAX_EPUB_BYTES = 50 * 1024 * 1024
CHAR_BUDGET = 80_000
EPUB_EXTENSION = ".epub"

ROOTS = [
    Path(p).resolve()
    for p in (os.environ.get("LOAM_EPUB_ROOTS") or str(lc.VAULT)).split(":")
    if p
]

# Who the server runs for. The write boundary these once fed retired on
# 2026-11-08; the names stay for the env the generator passes.
AGENT = os.environ.get("LOAM_AGENT", "")
WORKSPACE = Path(os.environ.get("LOAM_AGENT_WORKSPACE", "/nonexistent")).resolve()
VAULT = Path(os.environ.get("LOAM_VAULT") or lc.VAULT).resolve()
AI_LABS = Path(os.environ.get("LOAM_AI_LABS") or lc.WORKSPACES).resolve()
COMMONS = AI_LABS / "commons"
CONVERSATIONS = VAULT / "15_CONVERSATIONS"

# Illegal on Android at the filesystem layer: a file carrying one of
# these never syncs to the phone. Checked on names this server creates,
# never on names it reads or removes.
ANDROID_ILLEGAL = set('?:*"<>|')

mcp = MCPServer("loam-epub")


def _error(error: str, detail: str | None = None) -> dict[str, Any]:
    return {"ok": False, "error": error, "detail": detail}


def _resolve_under_roots(path: str) -> Path | None:
    try:
        resolved = Path(path).resolve()
    except OSError:
        return None
    return resolved if any(resolved.is_relative_to(r) for r in ROOTS) else None


def _meta_values(book: epub.EpubBook, name: str) -> list[str]:
    try:
        raw = book.get_metadata("DC", name)
    except Exception:
        return []
    return [v for v, _attrs in raw if v and v.strip()]


def _metadata(book: epub.EpubBook) -> dict[str, Any]:
    titles = _meta_values(book, "title")
    languages = _meta_values(book, "language")
    return {
        "title": titles[0] if titles else None,
        "authors": _meta_values(book, "creator"),
        "language": languages[0] if languages else None,
    }


def _title_by_href(book: epub.EpubBook) -> dict[str, str]:
    """Map each TOC target document to its title, first-seen wins."""
    mapping: dict[str, str] = {}

    def walk(entries: Any) -> None:
        for entry in entries:
            if isinstance(entry, tuple):
                section, children = entry
                href = getattr(section, "href", None)
                title = getattr(section, "title", None)
                if href and title:
                    mapping.setdefault(href.split("#")[0], title)
                walk(children)
            else:
                href = getattr(entry, "href", None)
                title = getattr(entry, "title", None)
                if href and title:
                    mapping.setdefault(href.split("#")[0], title)

    try:
        walk(book.toc)
    except Exception:
        pass
    return mapping


def _extract_text(item) -> str:
    soup = BeautifulSoup(item.get_content(), "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = "\n".join(line.strip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _build_chapters(book: epub.EpubBook) -> list[dict[str, Any]]:
    """One chapter per spine document, in reading order, 1-based.

    Spine order is the book's own reading order and is the only ordering
    that survives every epub layout. Chapter-merging heuristics are
    deliberately not attempted -- epubs split text wildly and a wrong
    merge is worse than a faithful split.
    """
    titles = _title_by_href(book)
    chapters: list[dict[str, Any]] = []
    for spine_id, _linear in book.spine:
        item = book.get_item_with_id(spine_id)
        if item is None or item.get_type() != ITEM_DOCUMENT:
            continue
        text = _extract_text(item)
        if not text:
            continue
        index = len(chapters) + 1
        title = titles.get(item.get_name(), f"Section {index}")
        chapters.append({"index": index, "title": title, "_text": text})
    return chapters


def _normalize_title(value: str) -> str:
    """Fold quotes/dashes/whitespace so a plain query matches a typeset title."""
    folded = (
        value.replace("’", "'").replace("‘", "'")
        .replace("“", '"').replace("”", '"')
        .replace("—", "-").replace("–", "-")
    )
    return re.sub(r"\s+", " ", folded).strip().lower()


def _resolve_chapter(chapters, chapter_arg):
    valid = "Valid chapters:\n" + "\n".join(
        f"{c['index']}. {c['title']}" for c in chapters
    )
    as_index = None
    if isinstance(chapter_arg, bool):
        return None, _error(f"chapter must be an index or title; got {chapter_arg!r}. {valid}")
    if isinstance(chapter_arg, int):
        as_index = chapter_arg
    elif isinstance(chapter_arg, str) and chapter_arg.strip().lstrip("-").isdigit():
        as_index = int(chapter_arg.strip())
    if as_index is not None:
        if 1 <= as_index <= len(chapters):
            return chapters[as_index - 1], None
        return None, _error(
            f"chapter index {as_index} is out of range (1-{len(chapters)}). {valid}"
        )

    needle = _normalize_title(str(chapter_arg))
    if not needle:
        return None, _error(f"chapter is empty. {valid}")
    matches = [c for c in chapters if needle in _normalize_title(c["title"])]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, _error(f"no chapter title matches {chapter_arg!r}. {valid}")
    matched = ", ".join(f"{c['index']}. {c['title']}" for c in matches)
    return None, _error(
        f"chapter {chapter_arg!r} is ambiguous - it matches {len(matches)} "
        f"chapters ({matched}). Use a 1-based index or a more specific title. {valid}"
    )


@mcp.tool(
    name="read_epub",
    description=(
        "Read an EPUB book. Pass an absolute `path` to a `.epub` file. Called "
        "without a chapter, it returns the book's metadata and chapter list "
        "(table of contents) - cheap and safe to call first. Called with "
        "`chapter` (a 1-based index from the chapter list, or a chapter title "
        "- case-insensitive substring match), it returns that chapter's text. "
        "Long chapters are bounded; pass `offset` to continue reading from "
        "where a truncated chapter left off. To read a whole book "
        "efficiently, list the chapters first, then read them one at a time "
        "(or dispatch a sub-agent per chapter)."
    ),
)
def read_epub(
    path: str, chapter: str | int | None = None, offset: int = 0
) -> dict[str, Any]:
    if not path or not Path(path).is_absolute():
        return _error(f"path must be an absolute path; got {path!r}")
    resolved = _resolve_under_roots(path)
    if resolved is None:
        roots = ", ".join(str(r) for r in ROOTS)
        return _error(f"path {path!r} is outside the readable roots ({roots}).")
    if not resolved.is_file() or not os.access(resolved, os.R_OK):
        return _error(f"path {path!r} does not exist or is not readable.")
    if resolved.suffix.lower() != EPUB_EXTENSION:
        return _error(
            f"path must end in {EPUB_EXTENSION!r}; got {resolved.suffix or '(none)'!r}. "
            f"read_epub reads EPUB files only - PDFs can be opened with the Read tool."
        )
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        return _error(f"could not stat {path!r}", detail=str(exc))
    if size > MAX_EPUB_BYTES:
        return _error(
            f"{resolved.name} is {size / 1024 / 1024:.1f} MB, over the "
            f"{MAX_EPUB_BYTES // (1024 * 1024)} MB epub size limit."
        )

    try:
        book = epub.read_epub(str(resolved))
    except Exception as exc:
        return _error(
            f"could not parse {resolved.name!r} as an EPUB - the file may be "
            f"malformed or not a real epub.",
            detail=f"{type(exc).__name__}: {exc}",
        )
    try:
        chapters = _build_chapters(book)
    except Exception as exc:
        return _error(
            f"failed to extract chapters from {resolved.name!r}.",
            detail=f"{type(exc).__name__}: {exc}",
        )
    if not chapters:
        return _error(
            f"{resolved.name!r} parsed but has no readable chapters "
            f"(empty or image-only spine)."
        )

    metadata = _metadata(book)

    chapter_given = chapter is not None and not (
        isinstance(chapter, str) and not chapter.strip()
    )
    if not chapter_given:
        return {
            "ok": True,
            "mode": "toc",
            "metadata": metadata,
            "chapters": [
                {"index": c["index"], "title": c["title"], "approx_chars": len(c["_text"])}
                for c in chapters
            ],
            "chapter_count": len(chapters),
            "total_approx_chars": sum(len(c["_text"]) for c in chapters),
        }

    selected, error = _resolve_chapter(chapters, chapter)
    if error is not None:
        return error

    text = selected["_text"]
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        return _error(f"offset must be a non-negative integer; got {offset!r}")
    start = min(offset, len(text))
    slice_ = text[start : start + CHAR_BUDGET]
    truncated = start + len(slice_) < len(text)
    return {
        "ok": True,
        "mode": "chapter",
        "chapter": {"index": selected["index"], "title": selected["title"]},
        "text": slice_,
        "offset": start,
        "returned_chars": len(slice_),
        "truncated": truncated,
        "next_offset": (start + len(slice_)) if truncated else None,
    }


def _mutable(path: str, verb: str) -> tuple[Path | None, str | None]:
    """Resolve a mutation target. Shared by delete_file and move_file.

    No path boundary since 2026-11-08 ("the authority move"): a persona has
    the box's shell and sudo, and what it asks the owner first is in its
    config. The symlink refusals in the tools below stay -- they protect
    against the 10-02 bug, not a permission.
    """
    try:
        return Path(os.path.abspath(path)).resolve(), None
    except OSError:
        return None, f"could not resolve {path!r}"


def _link_on_path(path: str) -> Path | None:
    """The first symlink on an absolute path, from the path itself upward.

    Only components below the vault or ai-labs root are looked at: the
    roots are where the box puts them, and a link above them is not a
    persona's to answer for. None when no component is a link.
    """
    p = Path(os.path.abspath(path))
    while p != p.parent and p not in (VAULT, AI_LABS):
        if p.is_symlink():
            return p
        p = p.parent
    return None


@mcp.tool(
    name="delete_file",
    description=(
        "Delete a single file, or an EMPTY directory, at an absolute path. "
        "The target must exist. A directory with anything in it is refused, "
        "and a symlink is removed itself, never its target. There is no "
        "recursive delete: "
        "to remove a populated directory, delete its contents first and then "
        "the directory itself, each removal its own decision. Use it the way "
        "you would use Write: deliberately, when removing "
        "something is the honest action rather than leaving a stub — or an "
        "empty folder — behind."
    ),
)
async def delete_file(path: str) -> dict[str, Any]:
    if not path or not Path(path).is_absolute():
        return _error("validation", f"path must be absolute; got {path!r}")
    link = Path(os.path.abspath(path))
    if link.is_symlink():
        # The link is the target, never its referent: resolving it first is
        # how a vault-root README.md -> overview.md deleted overview.md on
        # 2026-10-02. The boundary is checked on the directory holding the
        # link, so a link pointing anywhere at all -- outside every writable
        # tree, or nowhere -- is removable where it stands and its referent
        # is never touched.
        parent, why = _mutable(str(link.parent), "Delete")
        if parent is None:
            return _error("boundary", why or "target is out of bounds")
        try:
            (parent / link.name).unlink()
        except OSError as exc:
            return _error("os_error", f"{type(exc).__name__}: {exc}")
        return {"ok": True, "deleted": str(parent / link.name), "kind": "symlink"}
    resolved, why = _mutable(path, "Delete")
    if resolved is None:
        return _error("boundary", why or "target is out of bounds")
    if not resolved.exists():
        # Surfaced rather than treated as a no-op: better the agent learns it
        # had the path wrong than that a delete silently did nothing.
        return _error("not_found", f"{path!r} does not exist.")
    if resolved.is_dir():
        # An empty directory has no blast radius -- nothing is lost that was
        # not already nothing -- so removing it is allowed, and the files-only
        # rule that used to leave un-removable residue behind is lifted that
        # far and no further. A populated directory stays refused: recursion
        # is the part with the blast radius, and its absence is the design,
        # not a gap. iterdir() counts dotfiles too, which is the honest count
        # -- a directory holding only a .DS_Store is not empty, and rmdir
        # would refuse it anyway.
        try:
            entries = sorted(p.name for p in resolved.iterdir())
        except OSError as exc:
            return _error("os_error", f"{type(exc).__name__}: {exc}")
        if entries:
            shown = ", ".join(entries[:5])
            if len(entries) > 5:
                shown += ", ..."
            return _error(
                "directory_not_empty",
                f"{path!r} is a directory holding {len(entries)} "
                f"{'entry' if len(entries) == 1 else 'entries'} ({shown}); "
                f"only empty directories can be deleted. Remove what is "
                f"inside it first — there is no recursive delete.",
            )
        try:
            resolved.rmdir()
        except OSError as exc:
            return _error("os_error", f"{type(exc).__name__}: {exc}")
        return {"ok": True, "deleted": str(resolved), "kind": "directory"}
    try:
        resolved.unlink()
    except OSError as exc:
        return _error("os_error", f"{type(exc).__name__}: {exc}")
    return {"ok": True, "deleted": str(resolved), "kind": "file"}


@mcp.tool(
    name="move_file",
    description=(
        "Move or rename a single file. Both `source` and `destination` are "
        "absolute paths. Missing parent directories under the destination are created. "
        "Directories cannot be moved, and neither can a symlink: a link is "
        "refused at either end, and so is a path that passes through one. "
        "The destination must not already exist: overwriting is not renaming, so if you mean to replace a "
        "file, delete it first and let that be its own decision. This is "
        "also how you fix a filename Android cannot store — rename it to "
        "one it can and the file starts reaching the phone."
    ),
)
async def move_file(source: str, destination: str) -> dict[str, Any]:
    for label, raw in (("source", source), ("destination", destination)):
        if not raw or not Path(raw).is_absolute():
            return _error(
                "validation", f"{label} must be an absolute path; got {raw!r}"
            )

    # A link is never followed. Resolving first is how delete_file unlinked
    # overview.md through the vault's README.md link on 2026-10-02; a move
    # would carry the referent away and leave the link dangling, or land the
    # file wherever a linked directory happens to point. Refused rather than
    # moved as a link, because a renamed link keeps its relative target and
    # can come to point somewhere else entirely.
    for label, raw in (("source", source), ("destination", destination)):
        link = _link_on_path(raw)
        if link is not None:
            where = ("is a symlink" if link == Path(os.path.abspath(raw))
                     else f"passes through the symlink {str(link)!r}")
            return _error(
                "symlink",
                f"{label} {raw!r} {where}; "
                f"move_file does not follow or move links. Move the file at "
                f"the path the link points to, or delete the link with "
                f"delete_file.",
            )

    src, why = _mutable(source, "Move")
    if src is None:
        return _error("boundary", why or "source is out of bounds")
    dst, why = _mutable(destination, "Move")
    if dst is None:
        return _error("boundary", why or "destination is out of bounds")

    # Destination only. A source carrying one of these is the *reason* to
    # call this tool — checking it here would refuse the one operation that
    # fixes a file already stranded off the phone.
    bad = sorted(set(dst.name) & ANDROID_ILLEGAL)
    if bad:
        return _error(
            "illegal_filename",
            f"destination name {dst.name!r} contains {''.join(bad)!r}, which "
            f"Android cannot store — the file would be permanently invisible "
            f"on the phone. Choose a name without it.",
        )

    if src == dst:
        return _error(
            "validation", "source and destination resolve to the same path."
        )
    if not src.exists():
        return _error("not_found", f"{source!r} does not exist.")
    if src.is_dir():
        return _error(
            "is_a_directory",
            f"{source!r} is a directory; move_file is for files only.",
        )
    if dst.exists():
        return _error(
            "destination_exists",
            f"{destination!r} already exists; move_file will not overwrite it. "
            f"Delete it first if replacing it is what you mean.",
        )

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            src.rename(dst)
        except OSError as exc:
            # The two roots share a mount today, but rename() raises EXDEV
            # across devices rather than falling back to a copy, and nothing
            # guarantees they stay on one filesystem.
            if exc.errno != errno.EXDEV:
                raise
            shutil.move(str(src), str(dst))
    except OSError as exc:
        return _error("os_error", f"{type(exc).__name__}: {exc}")
    return {"ok": True, "moved": str(src), "to": str(dst)}


if __name__ == "__main__":
    mcp.run()
