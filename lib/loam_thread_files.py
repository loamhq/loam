"""Markdown files contributed to threads. Bind names to thread IDs before creating files.

The registry is derived from enabled manifests at install time. Bindings live outside
thread transcripts and survive a switch off. All callers share the thread writer lock.
"""

from __future__ import annotations

import json
import os
import stat
from functools import lru_cache
from pathlib import Path

import loam_config as lc
import loam_file_revision as revisions
import loam_threads as threads


REGISTRY = "file-types.json"
BINDINGS = "file-bindings.json"


def configure(plugins):
    types = {p["id"]: spec for p in plugins
             for spec in p["contributes"].get("thread.file", [])}
    with threads.locked(threads.STATE):
        if not _binding_file().exists() and (threads.STATE / BINDINGS).is_file():
            _save_bindings(_bindings())
        if types or (threads.STATE / REGISTRY).exists():
            threads.save_state(REGISTRY, types)


def _binding_file():
    # The Yours tree travels with a restored vault. Keep ownership outside notes
    # and outside individual extension versions, which the installer replaces.
    import loam_plugins
    return loam_plugins.yours_root() / "_data" / "thread-files.json"


def _source():
    path = _binding_file()
    return path if path.exists() else threads.STATE / BINDINGS


def _bindings():
    # Propagate malformed ownership records. An empty fallback could authorize
    # moving or deleting the wrong note.
    try:
        return json.loads(_source().read_text("utf-8"))
    except FileNotFoundError:
        return {}


def _save_bindings(rows):
    threads._write_atomic(_binding_file(), json.dumps(rows, ensure_ascii=False) + "\n")


def _boards_folder() -> str:
    """Resolve the vault folder for boards, matching vault cleanup when an
    older install's loam.toml still points boards under the inbox."""
    inbox = lc.vault_folder("inbox").strip("/")
    boards = lc.vault_folder("boards").strip("/")
    if not boards or boards == f"{inbox}/boards":
        return lc._VAULT_DEFAULTS["boards"]
    return boards


def _allowed_roots() -> tuple[Path, ...]:
    """Vault folders that may hold thread files: Boards, System (Loam files),
    and the configured inbox for legacy bindings."""
    roots = []
    for folder in (_boards_folder(), lc.vault_folder("boards"),
                   lc.vault_folder("system"), lc.vault_folder("inbox")):
        if folder:
            root = threads.VAULT / folder
            if root not in roots:
                roots.append(root)
    return tuple(roots)


def _folder_for(pid: str) -> Path:
    if pid == "boards":
        return Path(_boards_folder())
    return Path(lc.vault_folder("system")) / pid


def _path(rel):
    roots = _allowed_roots()
    path = threads.VAULT / rel
    if (not any(path.is_relative_to(r) and path != r for r in roots)
            or path.suffix != ".md" or path.resolve() != path):
        raise threads.ThreadError(409, "Thread file is outside its configured folder")
    return path


def _available(pid, title, spec, bindings):
    folder = _folder_for(pid)
    used = {row[key] for group in bindings.values() for row in group.values()
            for key in ("path", "moving_to") if key in row}
    base = threads.safe_title(title)
    n = 1
    while True:
        name = base if n == 1 else f"{base} ({n})"
        rel = str(folder / f"{name}{spec['suffix']}.md")
        if rel not in used and not os.path.lexists(_path(rel)):
            return rel
        n += 1


def adopt(pid, entries, *, spec=None):
    """Import an operator-verified set of (id, title, vault-relative path) tuples.
    A shared existing file needs an explicit owner. Missing duplicates get separate
    names, in stable ID order. Import only creates reservations in runtime state.
    """
    with threads.locked(threads.STATE):
        spec = spec or _spec(pid)
        bindings = _bindings()
        group = bindings.setdefault(pid, {})
        claimed = {row["path"]: (plugin, tid) for plugin, rows in bindings.items()
                   for tid, row in rows.items()}
        entries = sorted(entries)
        # Reserve explicit paths first, including names with a numeric suffix.
        pending = []
        for tid, title, rel in entries:
            path = _path(rel)
            if path.exists() and not path.is_file():
                raise threads.ThreadError(409, "Thread file is not a regular file")
            if tid in group:
                continue
            if rel in claimed:
                if path.exists():
                    raise threads.ThreadError(409, f"Two threads claim {rel}")
                pending.append((tid, title))
                continue
            group[tid] = {"path": rel, "title": title}
            claimed[rel] = (pid, tid)
        for tid, title in pending:
            group[tid] = {"path": _available(pid, title, spec, bindings), "title": title}
        _save_bindings(bindings)


def _spec(pid):
    spec = threads.load_state(REGISTRY).get(pid)
    if not spec:
        raise threads.ThreadError(404, "This extension has no active thread file")
    return spec


def _binding(pid, meta, spec):
    """Caller holds the thread lock. Complete a pending rename before any file access."""
    bindings = _bindings()
    group = bindings.setdefault(pid, {})
    tid, title = meta["id"], meta["title"]
    if tid not in group:
        group[tid] = {"path": _available(pid, title, spec, bindings), "title": title}
        _save_bindings(bindings)
    row = group[tid]
    if row["title"] != title or "moving_to" in row:
        if "moving_to" not in row:
            row["moving_to"] = _available(pid, title, spec, bindings)
            row["new_title"] = title
            _save_bindings(bindings)
        old, new = _path(row["path"]), _path(row["moving_to"])
        # link+unlink refuses an occupied destination. A retry after linking can
        # identify the same inode and finish without overwriting someone else's note.
        if old.exists():
            if not old.is_file():
                raise threads.ThreadError(409, "Thread file is no longer a regular file")
            new.parent.mkdir(parents=True, exist_ok=True)
            try:
                new.hardlink_to(old)
            except FileExistsError:
                if not new.samefile(old):
                    raise threads.ThreadError(409, "The new thread file name is occupied")
            old.unlink()
        row.update(path=row.pop("moving_to"), title=row.pop("new_title"))
        _save_bindings(bindings)
        if row["title"] != title:
            return _binding(pid, meta, spec)
    return row["path"]


def locations(meta):
    """Resolve active files for a turn or tool without creating notes."""
    specs = threads.load_state(REGISTRY)
    if not specs:
        return {}
    with threads.locked(threads.STATE):
        meta, _, _ = threads.read(meta["id"])
        return {pid: {"label": spec["label"], "path": str(_path(_binding(pid, meta, spec)))}
                for pid, spec in specs.items()}


def renamed(meta):
    """Caller holds the thread lock after committing the new thread title."""
    for pid, spec in threads.load_state(REGISTRY).items():
        _binding(pid, meta, spec)


def has_content(meta):
    """Called under the writer lock before discarding a never-sent thread."""
    for pid, spec in threads.load_state(REGISTRY).items():
        row = _bindings().get(pid, {}).get(meta["id"])
        if row:
            path = _path(row["path"])
            if path.exists():
                initial = spec["initial"].replace("{title}", row["title"]) + "\n\n"
                if path.read_bytes() != initial.encode("utf-8"):
                    return True
    return False


def removed(thread_id):
    """Called under the thread lock for a confirmed delete. An inactive plugin's
    files remain ordinary notes. A missing file is an idempotent deletion."""
    specs = threads.load_state(REGISTRY)
    bindings = _bindings()
    gone = []
    for pid in list(bindings):
        row = bindings[pid].get(thread_id)
        if not row:
            continue
        if pid in specs:
            if "moving_to" in row:
                _binding(pid, {"id": thread_id, "title": row["new_title"]}, specs[pid])
                bindings = _bindings()
            rel = bindings[pid][thread_id]["path"]
            path = _path(rel)
            if path.is_file():
                path.unlink()
                gone.append(rel)
        del bindings[pid][thread_id]
        _save_bindings(bindings)
    return gone


@lru_cache(maxsize=1)
def _owned_paths(source, stamp, active):
    rows = json.loads(source.read_text("utf-8"))
    return frozenset(row[key] for pid in active for row in rows.get(pid, {}).values()
                     for key in ("path", "moving_to") if key in row)


def owned_paths():
    active = tuple(threads.load_state(REGISTRY))
    if active:
        source = _source()
        try:
            info = source.stat()
            return _owned_paths(source, (info.st_ino, info.st_mtime_ns), active)
        except FileNotFoundError:
            pass
    return frozenset()


def existing_documents(pid):
    """Yield materialized documents for an enabled extension's scheduled work."""
    with threads.locked(threads.STATE):
        _spec(pid)
        rows = list(_bindings().get(pid, {}).items())
    for tid, row in rows:
        path = _path(row["path"])
        try:
            if not stat.S_ISREG(path.lstat().st_mode):
                continue
            raw = path.read_bytes()
        except FileNotFoundError:
            continue
        yield tid, {"path": row["path"], "markdown": raw.decode("utf-8"),
                    "revision": revisions.revision(raw)}


def document(pid, thread_id, payload=None):
    with threads.locked(threads.STATE):
        spec = _spec(pid)
        meta, _, _ = threads.read(thread_id)
        rel = _binding(pid, meta, spec)
        path = _path(rel)
        if payload is not None:
            content, base = payload.get("markdown"), payload.get("base_revision")
            if not isinstance(content, str) or not isinstance(base, str):
                raise threads.ThreadError(400, "Supply markdown and its base revision")
            try:
                revisions.replace_text(path, content, base)
            except revisions.RevisionConflict as exc:
                raise threads.ThreadError(409, str(exc)) from None
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("x", encoding="utf-8") as f:
                    f.write(spec["initial"].replace("{title}", meta["title"]) + "\n\n")
            except FileExistsError:
                pass
        if not stat.S_ISREG(path.lstat().st_mode):
            raise threads.ThreadError(409, "Thread file is no longer a regular file")
        raw = path.read_bytes()
        return {"path": rel, "markdown": raw.decode("utf-8"), "revision": revisions.revision(raw)}
