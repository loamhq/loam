"""Serve the app's vault, workspace and runtime document roots. See Files in broker/README.md.

Validate each decoded path component, then walk from an open root directory
without following symlinks. Private entries must look absent on direct
access as well as in listings. Uploads and renames cannot overwrite an
existing destination."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import fnmatch
import mimetypes
import os
import re
import secrets
import stat
import struct
import time
import unicodedata
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import loam_config as lc
from loam_file_revision import revision
from urllib.parse import parse_qs, quote, unquote, urlparse

import loam_broker as cfg
import loam_inbox  # the failure log's place, for RENAME_PINNED

LIST_DEFAULT = 200
LIST_MAX = 1000
# Report truncation when a directory exceeds the scan limit.
SCAN_MAX = 50_000
TEXT_READ_MAX = 2 * 1024 * 1024
# The JSON body carrying it is capped at 4 MiB by the broker, and JSON escaping
# can double a text, so 1 MiB is what a save can be sure to carry.
TEXT_WRITE_MAX = 1024 * 1024
DOWNLOAD_MAX = 50 * 1024 * 1024
ATTACHMENT_PARTS = Path(lc.vault_folder("attachments")).parts
CHUNK = 64 * 1024

USER_LINK = lc.OWNER_LINK

# HTML is editable text. Downloads use attachment headers and sandboxing.
TEXT_TYPES = {
    "md": "text/markdown", "markdown": "text/markdown", "txt": "text/plain",
    "csv": "text/csv", "tsv": "text/tab-separated-values",
    "json": "application/json", "jsonl": "application/x-ndjson",
    "yaml": "application/yaml", "yml": "application/yaml",
    "toml": "application/toml", "base": "application/yaml",
    "canvas": "application/json",
    "html": "text/html", "htm": "text/html",
}

# Hide credentials at every depth in every root.
SECRET_NAME = re.compile(
    r"(?i)(^\.env|\.env$|\.pem$|\.key$|\.p12$|\.pfx$|\.keystore$"
    r"|^id_(rsa|dsa|ecdsa|ed25519)|^(auth|credentials|\.credentials)\.json$"
    r"|^token$|\.token$|^\.netrc$|^\.git-credentials$)")
# Omit generated workspace files from resource listings.
SPACES_PRIVATE = {"logs", "cache", "scratch", "__pycache__", "venv",
                  "node_modules"}
SPACES_PRIVATE_SUFFIX = (".pyc", ".log", ".swp", ".swo")
# The three dotted names inside a persona's workspace that are its rendered
# configuration, shown read-only so the owner can see what a persona runs on.
SPACES_DOTTED = {".claude", ".codex", ".mcp.json"}
# Rendered into every workspace by bin/provision: an edit here is drift that
# the next provision reverts, so they are the runtime's and read-only.
RUNTIME_RENDERED = {"CLAUDE.md", "agent.json", ".mcp.json", ".claude", ".codex"}
# Operator-authored, per the role file: the owner's to edit, never the persona's.
USER_AUTHORED = {"soul.md", "config.md"}
# Schedule saves record their author through loam_schedule.
SCHEDULES_DIR = "crons"

# Use the configured folder for the transcript writer’s read-only records.
TRANSCRIPTS = lc.vault_folder("conversations").strip("/")

# The System root: the runtime's own Markdown, read from the live checkout.
# Each entry is a virtual path and the one file it is; nothing under the
# checkout is reachable that is not listed here.
SYSTEM_BASE = cfg.AGENTS_ROOT
SYSTEM_FILES = {
    "Session broker.md": "broker/README.md",
    "Deployment.md": "deploy/README.md",
    "Logs.md": "docs/logs.md",
    "Engineer/Role.md": "engineer/role.md",
    "Engineer/Platform project.md": "engineer/project.md",
    "Engineer/Runtime memory.md": "engineer/memory/MEMORY.md",
    "Engineer/Project template/README.md": "engineer-template/README.md",
    "Engineer/Project template/CLAUDE.md": "engineer-template/repo/CLAUDE.md",
    "Engineer/Project template/project.md": "engineer-template/repo/project.md",
    "Engineer/Project template/MEMORY.md":
        "engineer-template/repo/memory/MEMORY.md",
    "Reviewer/CLAUDE.md": "reviewer/CLAUDE.md",
}
# Generated System documents link back to the editable vault sources copied by provision.
SYSTEM_VAULT_SOURCES = {
    "Engineer/Role.md": f"{lc.vault_folder('projects')}/Loam/engineer/role.md",
    "Engineer/Platform project.md":
        f"{lc.vault_folder('projects')}/Loam/engineer/projects/loam.md",
}

# Vault machinery folders hidden from the main Your files listing and gathered
# behind one quiet Loam files row. Names come from [vault] in loam.toml.
LOAM_FOLDER_DESCRIPTIONS = (
    ("conversations", "The record of every chat, written as you talk."),
    ("attachments", "Images and files added to chats."),
    ("boards", "Each chat’s board."),
    ("system", "How your agents work: skills, conventions, tools."),
)

ROOTS = {
    "vault": {"name": "Your files",
              "description": "Written by you or your agents, for you."},
    "spaces": {"name": "Agent files",
               "description": "What your agents keep for themselves."},
    "system": {"name": "System",
               "description": "The runtime's own documents. Read-only."},
}


class FilesError(Exception):
    def __init__(self, status: int, message: str, **extra) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra

    def body(self) -> dict:
        return {"error": self.message, **self.extra}


def root_path(root: str) -> Path:
    """The host directory of a root, read at call time so a test can point
    ``cfg.VAULT`` or ``cfg.AI_LABS`` elsewhere."""
    if root == "vault":
        return cfg.VAULT
    if root == "spaces":
        return cfg.AI_LABS
    raise FilesError(404, f"no root called {root!r}; GET /v1/files/roots "
                          f"names them")


def parse_path(raw: str | None) -> list[str]:
    """A relative path, already decoded once, as its parts.

    Leading and trailing slashes are forgiven; anything else that is not a
    plain name is refused here, before a single system call."""
    if raw is None:
        return []
    if "\x00" in raw:
        raise FilesError(400, "a path cannot contain a NUL")
    text = raw.strip("/")
    if not text:
        return []
    parts = text.split("/")
    for part in parts:
        if part in ("", ".", "..") or "\\" in part:
            raise FilesError(400, "a path is names joined by '/', with no "
                                  "'.', '..', empty part or backslash")
        if len(part.encode("utf-8", "surrogatepass")) > 255:
            raise FilesError(400, "a name in the path is longer than 255 bytes")
    return parts


def _agents() -> set[str]:
    try:
        # Archived workspaces still own their files.
        import loam_agents  # noqa: PLC0415
        return set(loam_agents.every(cfg.AI_LABS))
    except OSError:
        return set()


def visible(root: str, parent: list[str], name: str,
            agents: set[str] | None = None) -> bool:
    """Whether ``name``, inside the folder ``parent`` of ``root``, exists as
    far as Files is concerned. False is the same as absent, everywhere."""
    if SECRET_NAME.search(name):
        return False
    if root == "spaces":
        if not parent:
            agents = _agents() if agents is None else agents
            return name in agents or name in ("commons", "README.md")
        if name in SPACES_PRIVATE or name.endswith(SPACES_PRIVATE_SUFFIX):
            return False
        if name.startswith("."):
            return (len(parent) == 1 and parent[0] != "commons"
                    and name in SPACES_DOTTED)
        return True
    return not name.startswith(".")


def check_visible(root: str, parts: list[str]) -> None:
    agents = _agents() if root == "spaces" else None
    for i, name in enumerate(parts):
        if not visible(root, parts[:i], name, agents):
            raise _missing(root, parts)


def _missing(root: str, parts: list[str]) -> FilesError:
    return FilesError(404, f"nothing at {root}/{'/'.join(parts)}")


def _walk(root: str, parts: list[str]) -> int:
    """A descriptor on the folder ``parts`` names, opened one component at a
    time without following a link. The caller closes it."""
    try:
        fd = _open_directory(root_path(root))
    except OSError:
        raise FilesError(404, f"the {root} root is not on this box") from None
    try:
        return _walk_from(fd, parts)
    except OSError:
        # ENOENT, ENOTDIR, ELOOP (a link) and EACCES all answer alike: what
        # Files cannot reach does not exist for the asker.
        raise _missing(root, parts) from None


def _open_directory(base: Path) -> int:
    return os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)


def _walk_from(fd: int, parts: list[str]) -> int:
    try:
        for part in parts:
            nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                          | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = nfd
    except OSError:
        os.close(fd)
        raise
    return fd


def _open_file(root: str, parts: list[str]) -> tuple[int, os.stat_result]:
    """A read descriptor on the regular file ``parts`` names, and its stat."""
    if not parts:
        raise FilesError(400, "that is a folder; read a file in it")
    dfd = _walk(root, parts[:-1])
    try:
        return _open_regular(dfd, parts[-1])
    except IsADirectoryError:
        raise FilesError(400, f"{root}/{'/'.join(parts)} is a folder; list it") from None
    except OSError:
        raise _missing(root, parts) from None
    finally:
        os.close(dfd)


def open_regular_at(base: Path, parts: list[str]) -> tuple[int, os.stat_result]:
    """Open a regular file beneath a trusted directory, following no links."""
    dfd = _walk_from(_open_directory(base), parts[:-1])
    try:
        return _open_regular(dfd, parts[-1])
    finally:
        os.close(dfd)


def _open_regular(dfd: int, name: str) -> tuple[int, os.stat_result]:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=dfd)
    try:
        st = os.fstat(fd)
        if stat.S_ISDIR(st.st_mode):
            raise IsADirectoryError(name)
        if not stat.S_ISREG(st.st_mode):
            raise OSError("expected a regular file")
    except OSError:
        os.close(fd)
        raise
    return fd, st


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ext(name: str) -> str:
    _, dot, ext = name.rpartition(".")
    return ext.lower() if dot else ""


def media_type(name: str) -> str:
    ext = _ext(name)
    if ext in TEXT_TYPES:
        return TEXT_TYPES[ext]
    guessed, _ = mimetypes.guess_type("x." + ext) if ext else (None, None)
    return guessed or "application/octet-stream"


def authority(root: str, parts: list[str]) -> str:
    """Who a file belongs to, in the words of the capability table.

    ``user``: the owner's to edit here. ``schedule``: a persona's schedule
    record, its and the owner's both; their save goes through the schedule log.
    ``persona``: a persona's own, edited by it through its own tools.
    ``runtime``: rendered or kept by the runtime. ``transcripts``: the
    transcript writer's."""
    if root == "system":
        return "runtime"
    if root == "vault":
        return ("transcripts" if list(parts[:len(Path(TRANSCRIPTS).parts)])
                == list(Path(TRANSCRIPTS).parts) else "user")
    if len(parts) < 2 or parts[0] == "commons":
        return "runtime" if not parts or parts == ["README.md"] else "persona"
    if parts[1] in RUNTIME_RENDERED:
        return "runtime"
    if len(parts) == 2 and parts[1] in USER_AUTHORED:
        return "user"
    if schedule_record(root, parts):
        return "schedule"
    return "persona"


def schedule_record(root: str, parts: list[str]) -> tuple[str, str] | None:
    """(agent, record name) when this is a persona's schedule record."""
    if (root == "spaces" and len(parts) == 3 and parts[0] != "commons"
            and parts[1] == SCHEDULES_DIR and parts[2].endswith(".md")
            and len(parts[2]) > 3):
        return parts[0], parts[2][:-3]
    return None


def capabilities(root: str, parts: list[str], kind: str) -> dict:
    """What the owner may do with this through Files. Never what a persona may:
    a persona's writes go through its own tools and its own guard."""
    owner = authority(root, parts)
    text = kind == "file" and _ext(parts[-1] if parts else "") in TEXT_TYPES
    if root == "vault":
        writable = owner == "user"
        renameable = writable and bool(parts) and pinned(root, parts, kind) is None
        caps = {"read": True, "download": kind == "file",
                "write": writable and text,
                "create": writable and kind == "folder", "rename": renameable}
        if renameable and kind == "file":
            caps["delete"] = True
        return caps
    if root == "spaces":
        return {"read": True, "download": kind == "file",
                "write": owner in ("user", "schedule") and text,
                "create": False, "rename": False}
    return {"read": True, "download": kind == "file", "write": False,
            "create": False, "rename": False}


# Pin paths the runtime addresses by name, the folders containing them and all top-level folders.
# A wildcard matches one whole path component.
_INBOX = lc.vault_folder("inbox")
_PROJECTS, _SYSTEM = lc.vault_folder("projects"), lc.vault_folder("system")
RENAME_PINNED = (
    loam_inbox.FAILURE_LOG,
    f"{_INBOX}/archive",
    f"{_PROJECTS}/*/receipts",
    f"{_PROJECTS}/Loam/engineer/role.md",
    f"{_PROJECTS}/Loam/engineer/projects/loam.md",
    f"{_PROJECTS}/Loam/infrastructure/schedule.md",
    f"{_PROJECTS}/Loam/architecture",
    f"{_PROJECTS}/Loam/decisions",
    f"{_PROJECTS}/Loam/reports",
    f"{_SYSTEM}/Dictation terms.md",
    f"{_SYSTEM}/conventions",
)


def pinned(root: str, parts: list[str], kind: str) -> str | None:
    """Why ``parts`` may not be renamed, or None. Vault only."""
    if root != "vault" or not parts:
        return None
    if len(parts) == 1 and kind == "folder":
        return "a top-level vault folder is the vault's structure"
    from loam_thread_files import owned_paths
    rel = "/".join(parts)
    if any(p == rel or p.startswith(rel + "/") for p in owned_paths()):
        return "an extension manages a thread file here"
    for pattern in RENAME_PINNED:
        pat = pattern.split("/")
        if len(parts) > len(pat) or not all(
                fnmatch.fnmatchcase(a, b) for a, b in zip(parts, pat)):
            continue
        rest = pat[len(parts):]
        if not rest:
            return "the runtime reads or writes it by this name"
        concrete = []
        for name in rest:
            if "*" in name:
                break
            concrete.append(name)
        below = Path(root_path(root), *parts, *concrete)
        if not concrete or os.path.lexists(below):
            return (f"the runtime reads or writes "
                    f"{'/'.join(parts + concrete) if concrete else 'what is in it'}"
                    f" by name")
    return None


def _workspace_name(agent: str) -> str:
    from loam_agents import card
    from loam_profile import display_name
    return display_name(agent, card(agent, cfg.AI_LABS))


def _entry(root: str, parts: list[str], st: os.stat_result,
           *, include_workspace: bool = False) -> dict:
    kind = "folder" if stat.S_ISDIR(st.st_mode) else "file"
    workspace_name = None
    if (root == "spaces" and parts and parts[0] not in ("commons", "README.md")
            and (include_workspace or len(parts) == 1)):
        workspace_name = _workspace_name(parts[0])
    name = parts[-1] if parts else ROOTS[root]["name"]
    return {
        "root": root,
        "path": "/".join(parts),
        "name": workspace_name if workspace_name and len(parts) == 1 else name,
        "kind": kind,
        "size": st.st_size if kind == "file" else None,
        "modified": _iso(st.st_mtime),
        "media_type": media_type(parts[-1]) if kind == "file" and parts else None,
        "authority": authority(root, parts),
        "capabilities": capabilities(root, parts, kind),
        "link": link(root, parts),
        **({"workspace_name": workspace_name} if workspace_name and len(parts) > 1 else {}),
        **_source(root, parts),
    }


def _source(root: str, parts: list[str]) -> dict:
    vault = SYSTEM_VAULT_SOURCES.get("/".join(parts)) if root == "system" else None
    if vault is None:
        return {}
    return {"source": {"root": "vault", "path": vault,
                       "link": link("vault", vault.split("/"))}}


def link(root: str, parts: list[str]) -> str:
    """The app's deep link to a resource: ``/files/<root>/<path>``, each name
    percent-encoded on its own so a ``/`` in the result is always a separator."""
    return "/files/" + root + "".join("/" + quote(p, safe="") for p in parts)


# --- System ------------------------------------------------------------------

def _system_tree() -> dict:
    """The System root as nested dicts: a folder is a dict, a file its host
    path. Only files that exist today are in it."""
    tree: dict = {}
    for virtual, rel in SYSTEM_FILES.items():
        host = Path(SYSTEM_BASE) / rel
        if not host.is_file() or host.is_symlink():
            continue
        node = tree
        *folders, leaf = virtual.split("/")
        for folder in folders:
            node = node.setdefault(folder, {})
        node[leaf] = host
    return tree


def _system_node(parts: list[str]):
    node = _system_tree()
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            raise _missing("system", parts)
        node = node[part]
    return node


def _system_stat(node) -> os.stat_result:
    if isinstance(node, dict):
        return os.stat(SYSTEM_BASE)
    return os.stat(node, follow_symlinks=False)


def _system_open(parts: list[str]) -> tuple[int, os.stat_result]:
    node = _system_node(parts)
    if isinstance(node, dict):
        raise FilesError(400, f"system/{'/'.join(parts)} is a folder; list it")
    try:
        fd = os.open(node, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise _missing("system", parts) from None
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise _missing("system", parts)
    return fd, st


# --- Reading -----------------------------------------------------------------

def _check_root(root: str) -> None:
    if root not in ROOTS:
        raise FilesError(404, f"no root called {root!r}; GET /v1/files/roots "
                              f"names them")


def roots() -> dict:
    out = []
    for key, meta in ROOTS.items():
        kind_caps = capabilities(key, [], "folder")
        out.append({"root": key, **meta, "link": link(key, []),
                    "capabilities": kind_caps})
    return {"roots": out, "shortcuts": shortcuts(), "loam_files": loam_files(),
            "workspace_names": {agent: _workspace_name(agent) for agent in sorted(_agents())
                                if not (cfg.AI_LABS / agent).is_symlink()}}


def shortcuts() -> list[dict]:
    """Host-offered shortcuts are retired: Pinned starts empty and holds only
    items the owner pinned themselves."""
    return []


def loam_files() -> list[dict]:
    """The configured vault machinery folders (conversations, attachments,
    boards, system) that sit behind the quiet Loam files entry."""
    out: list[dict] = []
    seen: set[str] = set()
    for key, description in LOAM_FOLDER_DESCRIPTIONS:
        rel = (lc.vault_folder(key) or "").strip("/")
        if not rel or rel in seen:
            continue
        row = _exists("vault", rel.split("/"))
        if row and row["kind"] == "folder":
            seen.add(rel)
            out.append({"key": key, "path": rel, "name": row["name"],
                        "description": description, "resource": row})
    return out


def _bounded(raw: str | None, name: str, default: int, top: int) -> int:
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise FilesError(400, f"`{name}` is a number") from None
    if value < 0 or (name == "limit" and value < 1):
        raise FilesError(400, f"`{name}` is out of range")
    return min(value, top)


def listing(root: str, raw_path: str | None, cursor: str | None = None,
            limit: str | None = None) -> dict:
    """One page of a folder: folders first, then files, each by name."""
    _check_root(root)
    parts = parse_path(raw_path)
    start = _bounded(cursor, "cursor", 0, SCAN_MAX)
    count = _bounded(limit, "limit", LIST_DEFAULT, LIST_MAX)
    truncated = False
    rows: list[dict] = []
    if root == "system":
        node = _system_node(parts)
        if not isinstance(node, dict):
            raise FilesError(400, f"system/{'/'.join(parts)} is a file; read it")
        here = _system_stat(node)
        for name in node:
            rows.append(_entry("system", parts + [name], _system_stat(node[name])))
    else:
        check_visible(root, parts)
        fd = _walk(root, parts)
        try:
            here = os.fstat(fd)
            agents = _agents() if root == "spaces" else None
            names = []
            with os.scandir(fd) as it:
                for item in it:
                    if len(names) >= SCAN_MAX:
                        truncated = True
                        break
                    names.append(item.name)
            for name in names:
                if not visible(root, parts, name, agents):
                    continue
                try:
                    st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except OSError:
                    continue      # gone between the scan and the stat
                if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
                    continue      # links, sockets, devices: not followed, not shown
                rows.append(_entry(root, parts + [name], st))
        finally:
            os.close(fd)
    rows.sort(key=lambda r: (r["kind"] != "folder", r["name"].casefold(),
                             r["name"]))
    page = rows[start:start + count]
    nxt = start + count if start + count < len(rows) else None
    folder = _entry(root, parts, here, include_workspace=True)
    folder["kind"] = "folder"
    folder["size"] = None
    return {"folder": folder, "entries": page, "total": len(rows),
            "cursor": start, "next_cursor": None if nxt is None else str(nxt),
            "truncated": truncated}


def open_file(root: str, raw_path: str | None) -> tuple[int, os.stat_result, list[str]]:
    _check_root(root)
    parts = parse_path(raw_path)
    if root == "system":
        fd, st = _system_open(parts)
    else:
        check_visible(root, parts)
        fd, st = _open_file(root, parts)
    return fd, st, parts


def _read_all(fd: int, limit: int) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = os.read(fd, CHUNK)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            break
    return b"".join(chunks)


def stat_resource(root: str, raw_path: str | None) -> dict:
    """A file's or folder's row, without its content."""
    _check_root(root)
    parts = parse_path(raw_path)
    if root == "system":
        return _entry("system", parts, _system_stat(_system_node(parts)))
    check_visible(root, parts)
    if not parts:
        fd = _walk(root, parts)
    else:
        dfd = _walk(root, parts[:-1])
        try:
            st = os.stat(parts[-1], dir_fd=dfd, follow_symlinks=False)
        except OSError:
            raise _missing(root, parts) from None
        finally:
            os.close(dfd)
        if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
            raise _missing(root, parts)
        return _entry(root, parts, st, include_workspace=True)
    try:
        return _entry(root, parts, os.fstat(fd))
    finally:
        os.close(fd)


def read_text(root: str, raw_path: str | None) -> dict:
    """A text file whole, with the revision a save must name."""
    fd, st, parts = open_file(root, raw_path)
    try:
        if st.st_size > TEXT_READ_MAX:
            raise FilesError(413, f"{root}/{'/'.join(parts)} is "
                                  f"{st.st_size} bytes; text is read up to "
                                  f"{TEXT_READ_MAX}. Download it instead.",
                             size=st.st_size, max=TEXT_READ_MAX)
        data = _read_all(fd, TEXT_READ_MAX)
    finally:
        os.close(fd)
    if len(data) > TEXT_READ_MAX:
        raise FilesError(413, "the file grew past the text limit while it was "
                              "read. Download it instead.", max=TEXT_READ_MAX)
    try:
        if b"\x00" in data:
            raise UnicodeDecodeError("utf-8", data, 0, 1, "NUL")
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise FilesError(415, f"{root}/{'/'.join(parts)} is not UTF-8 text. "
                              f"Download it instead.",
                         media_type=media_type(parts[-1])) from None
    out = _entry(root, parts, st, include_workspace=True)
    out["size"] = len(data)
    out.update({"text": text, "revision": revision(data)})
    return out


def download(root: str, raw_path: str | None) -> tuple[int, int, dict]:
    """A descriptor to stream, its size, and the headers it goes with. The
    caller closes the descriptor."""
    fd, st, parts = open_file(root, raw_path)
    name = parts[-1]
    attachment_type = cfg.attachment_type(name) or ""
    attachment_video = (root == "vault" and attachment_type.startswith("video/")
                        and tuple(parts[:len(ATTACHMENT_PARTS)]) == ATTACHMENT_PARTS)
    mtype = attachment_type if attachment_video else media_type(name)
    limit = cfg.VIDEO_ATTACHMENT_MAX_BYTES if attachment_video else DOWNLOAD_MAX
    if st.st_size > limit:
        os.close(fd)
        raise FilesError(413, f"{root}/{'/'.join(parts)} is {st.st_size} "
                              f"bytes; Files downloads up to {limit}.",
                         size=st.st_size, max=limit)
    headers = {
        "Content-Type": mtype + ("; charset=utf-8" if mtype.startswith("text/")
                                 else ""),
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name, safe=""),
        "X-Content-Type-Options": "nosniff",
        # Whatever the app does with the bytes, the broker's answer is never
        # a document that may run: HTML from the vault is untrusted content.
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "Cache-Control": "no-store",
        "Last-Modified": datetime.fromtimestamp(st.st_mtime, timezone.utc)
        .strftime("%a, %d %b %Y %H:%M:%S GMT"),
    }
    return fd, st.st_size, headers


# Stream complete exports directly from the filesystem. Listing and download size limits do not apply.
# Include user dotfiles in exports while excluding credentials and runtime-private files.

# Exclude repository history and synchronization metadata at any depth.
ARCHIVE_NEVER = {".git", ".stversions", ".stfolder"}
# Secret-shaped names the browsing rule does not already name; the app's
# fallback left these out too.
ARCHIVE_SECRET = re.compile(r"(?i)^secrets?\.(json|ya?ml|toml|env|txt)$")
# Also omit workspace lock files and virtual environments.
ARCHIVE_SPACES_CHURN = {".locks", ".venv"}
# DEFLATE what compresses (the text types Files edits, and a few more text
# kinds); STORE the rest: images, audio, PDFs and archives are compressed
# already, and deflating gigabytes of them costs minutes for nothing.
ARCHIVE_DEFLATE = set(TEXT_TYPES) | {"js", "mjs", "ts", "css", "py", "sh",
                                     "xml", "svg", "ics", "log", "rtf", "tex"}
# DOS time cannot say before 1980 or after 2107; the extended-timestamp field
# beside it carries the exact second in UTC for every unzip that reads it.
_DOS_MIN = (1980, 1, 1, 0, 0, 0)
_DOS_MAX = (2107, 12, 31, 23, 59, 58)


def archive_keeps(root: str, parent: list[str], name: str,
                  agents: set[str] | None = None) -> bool:
    """Whether ``name``, inside ``parent`` (a path from the top of ``root``),
    goes into an archive."""
    if (name in ARCHIVE_NEVER or SECRET_NAME.search(name)
            or ARCHIVE_SECRET.search(name)):
        return False
    if root == "spaces":
        if not parent:
            agents = _agents() if agents is None else agents
            return name in agents or name in ("commons", "README.md")
        if (name in SPACES_PRIVATE or name in ARCHIVE_SPACES_CHURN
                or name.endswith(SPACES_PRIVATE_SUFFIX)):
            return False
    return True


def archive(root: str, raw_path: str | None) -> tuple[int, list[str], dict]:
    """A descriptor on the folder to archive, its parts, and the headers the
    answer goes with. Every refusal is raised here, before a byte is sent;
    the caller hands the descriptor to ``archive_stream``, which closes it."""
    if root not in ("vault", "spaces"):
        _check_root(root)
        raise FilesError(404, f"the {root} root is not archived; download "
                              f"its files one by one")
    parts = parse_path(raw_path)
    check_visible(root, parts)
    if not parts:
        fd = _walk(root, parts)
    else:
        dfd = _walk(root, parts[:-1])
        try:
            st = os.stat(parts[-1], dir_fd=dfd, follow_symlinks=False)
            if stat.S_ISREG(st.st_mode):
                raise FilesError(400, f"{root}/{'/'.join(parts)} is a file; "
                                      f"download it, or archive its folder")
            if not stat.S_ISDIR(st.st_mode):
                raise _missing(root, parts)
            fd = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                         | os.O_CLOEXEC, dir_fd=dfd)
        except OSError:
            raise _missing(root, parts) from None
        finally:
            os.close(dfd)
    name = parts[-1] if parts else {"vault": "vault", "spaces": "ai-spaces"}[root]
    headers = {
        "Content-Type": "application/zip",
        "Content-Disposition": "attachment; filename*=UTF-8''"
                               + quote(name + ".zip", safe=""),
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "Cache-Control": "no-store",
    }
    return fd, parts, headers


class ArchiveError(Exception):
    """A failure partway through an archive: the zip is abandoned unfinished."""


class _Sink:
    """The zip's output: bytes held until the stream takes them. It has
    ``tell`` and no ``seek``, so zipfile writes data descriptors and never
    goes back. Once dead it swallows writes: a failed zip's cleanup is not
    sent."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.pos = 0
        self.dead = False

    def write(self, data) -> int:
        if not self.dead:
            self.buf += data
            self.pos += len(data)
        return len(data)

    def tell(self) -> int:
        return self.pos

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        out = bytes(self.buf)
        self.buf.clear()
        return out


def _zipinfo(arcname: str, st: os.stat_result, folder: bool) -> zipfile.ZipInfo:
    try:
        when = tuple(time.localtime(st.st_mtime)[:6])
    except (OverflowError, OSError, ValueError):
        when = _DOS_MIN
    when = min(max(when, _DOS_MIN), _DOS_MAX)
    info = zipfile.ZipInfo(arcname + ("/" if folder else ""), when)
    info.create_system = 3
    info.external_attr = (st.st_mode & 0xFFFF) << 16 | (0x10 if folder else 0)
    secs = max(-2**31, min(int(st.st_mtime), 2**31 - 1))
    info.extra = struct.pack("<HHBl", 0x5455, 5, 1, secs)
    if folder:
        info.CRC = info.compress_size = info.file_size = 0
    else:
        # The size zipfile judges Zip64 by: over ~4 GiB the entry is Zip64
        # from its first byte. A file that grows past that while it is read
        # fails the archive (zipfile refuses) rather than writing a wrong size.
        info.file_size = st.st_size
        info.compress_type = (zipfile.ZIP_DEFLATED
                              if _ext(arcname.rpartition("/")[2]) in ARCHIVE_DEFLATE
                              else zipfile.ZIP_STORED)
    return info


def archive_stream(root: str, parts: list[str], fd: int,
                   stats: dict | None = None):
    """The zip of the folder ``fd`` is open on, as chunks of bytes; it takes
    ownership of ``fd``. Anything that cannot be read whole raises partway
    (``ArchiveError``), and the zip's closing record is then never produced,
    so what was sent is not a valid zip.

    A symbolic link is never followed and never stored; a socket, a device
    or a FIFO is not a file and is left out; a name that vanishes between the
    scan and its open was not there to export. Everything else goes in or
    fails the archive. ``stats``, when given, counts files, folders and bytes
    as they go."""
    stats = {} if stats is None else stats
    stats.update(files=0, folders=0, bytes=0)
    sink = _Sink()
    zf = zipfile.ZipFile(sink, "w", allowZip64=True)
    agents = _agents() if root == "spaces" else None
    gone = (errno.ENOENT, errno.ELOOP, errno.ENOTDIR)

    def where(rel: list[str]) -> str:
        return f"{root}/{'/'.join(parts + rel)}"

    def arcname(rel: list[str]) -> str:
        name = "/".join(rel)
        try:
            name.encode("utf-8")
        except UnicodeEncodeError:
            raise ArchiveError(f"{where(rel)!r} has a name that is not UTF-8; "
                               f"rename it and download again") from None
        return name

    def opened(dfd: int, name: str, here: list[str], flags: int) -> int | None:
        try:
            return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
                           | flags, dir_fd=dfd)
        except OSError as exc:
            if exc.errno in gone:
                return None       # gone, or swapped for a link
            raise ArchiveError(f"could not open {where(here)}: "
                               f"{exc.strerror}") from None

    def folder(dfd: int, rel: list[str]):
        with os.scandir(dfd) as it:
            names = sorted(item.name for item in it)
        for name in names:
            if not archive_keeps(root, parts + rel, name, agents):
                continue
            here = rel + [name]
            try:
                st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISDIR(st.st_mode):
                cfd = opened(dfd, name, here, os.O_DIRECTORY)
                if cfd is None:
                    continue
                try:
                    zf.mkdir(_zipinfo(arcname(here), os.fstat(cfd), True))
                    stats["folders"] += 1
                    yield from folder(cfd, here)
                finally:
                    os.close(cfd)
            elif stat.S_ISREG(st.st_mode):
                ffd = opened(dfd, name, here, os.O_NONBLOCK)
                if ffd is None:
                    continue
                try:
                    fst = os.fstat(ffd)
                    if not stat.S_ISREG(fst.st_mode):
                        continue
                    with zf.open(_zipinfo(arcname(here), fst, False), "w") as out:
                        while True:
                            try:
                                chunk = os.read(ffd, CHUNK)
                            except OSError as exc:
                                raise ArchiveError(
                                    f"could not read {where(here)}: "
                                    f"{exc.strerror}") from None
                            if not chunk:
                                break
                            out.write(chunk)
                            stats["bytes"] += len(chunk)
                            if len(sink.buf) >= CHUNK:
                                yield sink.take()
                    stats["files"] += 1
                finally:
                    os.close(ffd)
            # links, sockets, devices, FIFOs: not files, never followed
            if len(sink.buf) >= CHUNK:
                yield sink.take()

    try:
        yield from folder(fd, [])
        zf.close()
        if sink.buf:
            yield sink.take()
    except BaseException:
        # Abandon the zip: nothing more reaches the stream, and zipfile's own
        # cleanup (an open entry's close, the central directory) has nowhere
        # to write.
        sink.dead = True
        zf.fp = None
        raise
    finally:
        os.close(fd)


# --- Writing -----------------------------------------------------------------

FRONTMATTER = re.compile(r"---(\r?\n)(?:(.*?)\r?\n)?---[ \t]*(?=\r?\n|$)", re.S)


def _frontmatter_set(text: str, fields: dict[str, str]) -> str:
    """``text`` with each field set in its YAML frontmatter, which is made if
    there is none. Only a whole single-line ``key: value`` is replaced."""
    match = FRONTMATTER.match(text)
    if match:
        nl = match.group(1)
        lines = match.group(2).split(nl) if match.group(2) else []
        for key, value in fields.items():
            pattern = re.compile(rf"^{re.escape(key)}\s*:")
            hit = [i for i, line in enumerate(lines) if pattern.match(line)]
            if hit:
                lines[hit[0]] = f"{key}: {value}"
            else:
                lines.append(f"{key}: {value}")
        return "---" + nl + nl.join(lines) + nl + "---" + text[match.end():]
    block = "".join(f"{key}: {value}\n" for key, value in fields.items())
    return f"---\n{block}---\n" + ("\n" if text and not text.startswith("\n")
                                    else "") + text


def author_link(by) -> str:
    """Return the provenance link for the owner or a known persona. Reject unknown authors."""
    if by is None or lc.is_owner(by):
        return USER_LINK
    if not isinstance(by, str) or by not in cfg.roster():
        raise FilesError(400, f"`by` names a persona of this Loam; {by!r} is not one")
    return f'"[[{cfg.agent_card(by)["name"]}]]"'


def with_provenance(root: str, parts: list[str], text: str, created: bool,
                    link: str = USER_LINK) -> str:
    """The owner's identity on a vault note they wrote through Files: the
    provenance convention's two fields, with their link. The vault only: a
    schedule record in particular is never stamped, because its header has
    no such key (loam_cron.HEADER_KEYS) and the schedule log already names
    who saved it. ``link`` is a persona's when the save was made for one."""
    if root != "vault" or _ext(parts[-1]) not in ("md", "markdown"):
        return text
    fields = {"created_by": link} if created else {}
    fields["last_modified_by"] = link
    match = FRONTMATTER.match(text)
    if created and match and re.search(r"(?m)^created_by\s*:", match.group(2) or ""):
        fields.pop("created_by")      # a created_by the person typed is theirs
    return _frontmatter_set(text, fields)


def _text_body(payload: dict) -> bytes:
    text = payload.get("text")
    if not isinstance(text, str):
        raise FilesError(400, "`text` is the file's whole new content, a string")
    data = text.encode("utf-8", "surrogatepass")
    if len(data) > TEXT_WRITE_MAX:
        raise FilesError(413, f"that is {len(data)} bytes; Files saves text up "
                              f"to {TEXT_WRITE_MAX}", max=TEXT_WRITE_MAX)
    return data


def _write_target(payload: dict) -> tuple[str, list[str]]:
    root = payload.get("root")
    if not isinstance(root, str):
        raise FilesError(400, "`root` names the root")
    _check_root(root)
    raw = payload.get("path")
    if not isinstance(raw, str):
        raise FilesError(400, "`path` is the file's path in its root")
    parts = parse_path(raw)
    if not parts:
        raise FilesError(400, "`path` names a file, not the root")
    return root, parts


def _readonly(root: str, parts: list[str]) -> FilesError:
    owner = authority(root, parts)
    why = {"transcripts": "transcripts are the transcript writer's",
           "runtime": "it is the runtime's, rendered or kept by it",
           "persona": "it is a persona's own; the persona edits it"}.get(
        owner, "Files does not edit this kind of file")
    return FilesError(403, f"{root}/{'/'.join(parts)} is read-only here: {why}",
                      authority=owner)


def _tmp_in(dfd: int, data: bytes, mode: int) -> str:
    """A new file in the folder ``dfd`` holding ``data``, flushed to disk."""
    for _ in range(8):
        name = f".loam-files-{secrets.token_hex(6)}.tmp"
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=dfd)
        except FileExistsError:
            continue
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fchmod(fd, mode)
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            os.unlink(name, dir_fd=dfd)
            raise
        os.close(fd)
        return name
    raise FilesError(500, "could not make a temporary file beside the target")


def _settle(dfd: int, root: str, parts: list[str]) -> dict:
    try:
        os.fsync(dfd)
    except OSError:
        pass
    st = os.stat(parts[-1], dir_fd=dfd, follow_symlinks=False)
    return _entry(root, parts, st)


def _vault_mutation(operation):
    """Serialize vault saves and renames with trash moves across API workers."""
    @wraps(operation)
    def run(payload: dict) -> dict:
        if payload.get("root") != "vault":
            return operation(payload)
        fd = _walk("vault", [])
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            return operation(payload)
        finally:
            os.close(fd)
    return run


@_vault_mutation
def save_text(payload: dict) -> dict:
    """Replace a text file the owner may edit, only if it is still the revision
    they read. ``{root, path, text, base_revision, by?}``: ``by`` names the
    persona a plugin saved it for, whose link the provenance stamp then
    carries (``author_link``)."""
    root, parts = _write_target(payload)
    link = author_link(payload.get("by"))
    base = payload.get("base_revision")
    if not isinstance(base, str) or not base.startswith("sha256:"):
        raise FilesError(400, "`base_revision` is the revision the edit began "
                              "from, as a read answered it")
    if root == "system":
        _system_node(parts)
        raise _readonly(root, parts)
    check_visible(root, parts)
    data = _text_body(payload)
    dfd = _walk(root, parts[:-1])
    try:
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
                         | os.O_NONBLOCK, dir_fd=dfd)
        except OSError:
            raise _missing(root, parts) from None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise _missing(root, parts)
            if not capabilities(root, parts, "file")["write"]:
                raise _readonly(root, parts)
            current = _read_all(fd, TEXT_WRITE_MAX * 4)
        finally:
            os.close(fd)
        if revision(current) != base:
            raise FilesError(409, f"{root}/{'/'.join(parts)} changed since "
                                  f"that revision; read it again and redo the "
                                  f"edit on top", current_revision=revision(current))
        text = with_provenance(root, parts, data.decode("utf-8", "surrogatepass"),
                               created=False, link=link)
        data = text.encode("utf-8", "surrogatepass")
        tmp = _tmp_in(dfd, data, stat.S_IMODE(st.st_mode))
        try:
            # The check again, as close to the rename as Python gets: an agent
            # writing in the instant between the two is the one window left,
            # and it is microseconds, not the minutes an open editor holds.
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
                         | os.O_NONBLOCK, dir_fd=dfd)
            try:
                again = _read_all(fd, TEXT_WRITE_MAX * 4)
            finally:
                os.close(fd)
            if revision(again) != base:
                raise FilesError(409, f"{root}/{'/'.join(parts)} changed while "
                                      f"it was being saved; read it again",
                                 current_revision=revision(again))
            record = schedule_record(root, parts)
            if record:
                # Before the rename, so the reconciler can never see these
                # bytes without knowing they are the owner's.
                import loam_schedule  # noqa: PLC0415
                loam_schedule.claim(*record, text, actor=lc.OWNER_ID, lane="app")
            os.rename(tmp, parts[-1], src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
        out = _settle(dfd, root, parts)
    finally:
        os.close(dfd)
    out.update({"revision": revision(data), "text": text,
                "provenance": text != payload["text"]})
    if root == "vault":
        import loam_recent  # noqa: PLC0415
        by = payload.get("by")
        if by is not None and not lc.is_owner(by):
            loam_recent.record_write("/".join(parts), kind="agent", actor=str(by),
                                     source="app", vault=root_path("vault"))
        else:
            loam_recent.record_write("/".join(parts), kind="owner", actor=lc.OWNER_ID,
                                     source="app", vault=root_path("vault"))
    record = schedule_record(root, parts)
    if record:
        import loam_activity  # noqa: PLC0415
        out["schedule"] = loam_activity.after_file_save(*record)
    return out


def _creatable(root: str, parent: list[str], name: str) -> None:
    if root == "system":
        _system_node(parent)
        raise _readonly(root, parent + [name])
    check_visible(root, parent)
    # Read-only first: a folder nobody may create in says so, whatever the name.
    if not capabilities(root, parent, "folder")["create"]:
        raise _readonly(root, parent)
    if not visible(root, parent, name) or name.startswith("."):
        raise FilesError(400, f"{name!r} is not a name Files creates "
                              f"(hidden, secret-shaped or runtime-private)")


def create_file(payload: dict) -> dict:
    """A new text file, which must not exist yet. ``{root, path, text?}``."""
    root, parts = _write_target(payload)
    payload = {**payload, "text": payload.get("text", "")}
    _creatable(root, parts[:-1], parts[-1])
    if _ext(parts[-1]) not in TEXT_TYPES:
        raise FilesError(415, f"Files creates text files ("
                              f"{', '.join(sorted(TEXT_TYPES))}); "
                              f"{parts[-1]!r} is not one")
    data = _text_body(payload)
    text = with_provenance(root, parts, data.decode("utf-8", "surrogatepass"),
                           created=True)
    data = text.encode("utf-8", "surrogatepass")
    dfd = _walk(root, parts[:-1])
    try:
        tmp = _tmp_in(dfd, data, 0o644)
        try:
            # link(2) never replaces: an existing name of any kind, link
            # included, is EEXIST, decided by the kernel at the instant of
            # creation.
            os.link(tmp, parts[-1], src_dir_fd=dfd, dst_dir_fd=dfd,
                    follow_symlinks=False)
        except FileExistsError:
            raise FilesError(409, f"{root}/{'/'.join(parts)} already exists; "
                                  f"read it and save over its revision") from None
        finally:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
        out = _settle(dfd, root, parts)
    finally:
        os.close(dfd)
    out.update({"revision": revision(data), "text": text,
                "provenance": text != payload["text"]})
    if root == "vault":
        import loam_recent  # noqa: PLC0415
        loam_recent.record_write("/".join(parts), kind="owner", actor=lc.OWNER_ID,
                                 source="app", vault=root_path("vault"))
    return out


def create_folder(payload: dict) -> dict:
    """A new, empty folder. ``{root, path}``."""
    root, parts = _write_target(payload)
    _creatable(root, parts[:-1], parts[-1])
    dfd = _walk(root, parts[:-1])
    try:
        try:
            os.mkdir(parts[-1], 0o755, dir_fd=dfd)
        except FileExistsError:
            raise FilesError(409, f"{root}/{'/'.join(parts)} already "
                                  f"exists") from None
        out = _settle(dfd, root, parts)
    finally:
        os.close(dfd)
    return out


# --- Renaming and moving ------------------------------------------------------

# A rename preserves file contents, provenance and modification time.
RENAME_NOREPLACE = 1
_libc = None


def _renameat2(src_dfd: int, src: str, dst_dfd: int, dst: str) -> None:
    """``renameat2(..., RENAME_NOREPLACE)``: an existing destination of any
    kind, a dangling link included, is EEXIST, decided by the kernel at the
    instant of the rename."""
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
    fn = getattr(_libc, "renameat2", None)
    if fn is None:
        raise FilesError(500, "this box's C library has no renameat2, so "
                              "Files cannot rename without risking a replace")
    fn.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                   ctypes.c_char_p, ctypes.c_uint)
    if fn(src_dfd, os.fsencode(src), dst_dfd, os.fsencode(dst),
          RENAME_NOREPLACE) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def rename_name(raw, what: str = "`name`") -> str:
    """A new name as the person typed it, NFC and trimmed, as an upload's
    name is; refused (never rewritten) when it is not a plain name."""
    if not isinstance(raw, str):
        raise FilesError(400, f"{what} is the new name, a string")
    name = unicodedata.normalize("NFC", raw).strip()
    if not name:
        raise FilesError(400, "a name cannot be empty")
    if "/" in name or "\\" in name:
        raise FilesError(400, "a name cannot hold '/' or '\\'; to move it "
                              "to another folder, send `to`")
    if name in (".", ".."):
        raise FilesError(400, f"{name!r} is not a name")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise FilesError(400, "a name cannot hold control characters")
    if len(name.encode("utf-8", "surrogatepass")) > 255:
        raise FilesError(400, "a name is at most 255 bytes")
    return name


@_vault_mutation
def rename(payload: dict) -> dict:
    """Rename or move a vault file or folder, never over anything.
    ``{root, path, name}`` renames it in its folder; ``{root, path, to}``
    moves it to the path ``to`` in the same root."""
    root, parts = _write_target(payload)
    has_name, has_to = "name" in payload, "to" in payload
    if has_name == has_to:
        raise FilesError(400, "send `name` to rename in place, or `to` (a "
                              "path in the same root) to move; one of them")
    if has_name:
        dest = parts[:-1] + [rename_name(payload["name"])]
    else:
        if not isinstance(payload["to"], str):
            raise FilesError(400, "`to` is the new path in the same root")
        dest = parse_path(payload["to"])
        if not dest:
            raise FilesError(400, "`to` names the new path, not the root")
        dest = dest[:-1] + [rename_name(dest[-1], "the last name in `to`")]
        for name in dest[:-1]:
            rename_name(name, "a folder in `to`")
    if root == "system":
        _system_node(parts)
        raise _readonly(root, parts)
    check_visible(root, parts)
    where = f"{root}/{'/'.join(parts)}"
    if dest == parts:
        raise FilesError(400, f"{where} already has that name")
    if dest[:len(parts)] == parts:
        raise FilesError(400, "a folder cannot move into itself")
    dfd = _walk(root, parts[:-1])
    try:
        try:
            st = os.stat(parts[-1], dir_fd=dfd, follow_symlinks=False)
        except OSError:
            raise _missing(root, parts) from None
        # Treat links as absent for renames as for reads.
        if stat.S_ISDIR(st.st_mode):
            kind = "folder"
        elif stat.S_ISREG(st.st_mode):
            kind = "file"
        else:
            raise _missing(root, parts)
        if not capabilities(root, parts, kind)["rename"]:
            owner = authority(root, parts)
            if root == "spaces":
                raise FilesError(
                    403, f"{where} is not renamed here: a workspace's files "
                         f"are its persona's, and the runtime finds them by "
                         f"name", authority=owner)
            why = pinned(root, parts, kind)
            if owner != "user" or why is None:
                raise _readonly(root, parts)
            raise FilesError(403, f"{where} keeps its name: {why}",
                             authority=owner, pinned=True)
        _creatable(root, dest[:-1], dest[-1])
        ddfd = _walk(root, dest[:-1])
        try:
            try:
                _renameat2(dfd, parts[-1], ddfd, dest[-1])
            except FilesError:
                raise
            except FileExistsError:
                raise FilesError(409, f"{root}/{'/'.join(dest)} already "
                                      f"exists; choose another name") from None
            except FileNotFoundError:
                raise _missing(root, parts) from None
            except OSError as exc:
                raise FilesError(409, f"could not rename {where}: "
                                      f"{exc.strerror}") from None
            if dest[:-1] != parts[:-1]:
                try:
                    os.fsync(dfd)
                except OSError:
                    pass
            out = _settle(ddfd, root, dest)
        finally:
            os.close(ddfd)
    finally:
        os.close(dfd)
    out["from"] = {"root": root, "path": "/".join(parts),
                   "link": link(root, parts)}
    if root == "vault":
        import loam_recent  # noqa: PLC0415
        loam_recent.record_rename("/".join(parts), "/".join(dest),
                                  vault=root_path("vault"))
    return out


# --- Trash -------------------------------------------------------------------

TRASH_PARENT = ".trash"
TRASH = "loam-files-trash"
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _trash_target(payload: dict) -> tuple[str, list[str]]:
    root, parts = _write_target(payload)
    if root != "vault" or authority(root, parts) != "user":
        raise _readonly(root, parts)
    check_visible(root, parts)
    why = pinned(root, parts, "file")
    if why:
        raise FilesError(403, f"vault/{'/'.join(parts)} stays in place: {why}",
                         authority="user", pinned=True)
    return root, parts


@contextmanager
def _trash_root(create: bool = False):
    vault = _walk("vault", [])
    try:
        parent = _open_trash_directory(vault, TRASH_PARENT, create)
        try:
            trash = _open_trash_directory(parent, TRASH, create)
            try:
                yield trash
            finally:
                os.close(trash)
        finally:
            os.close(parent)
    finally:
        os.close(vault)


def _open_trash_directory(parent: int, name: str, create: bool) -> int:
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
        else:
            os.fsync(parent)
    return os.open(name, _DIRECTORY, dir_fd=parent)


def _sync_moved_file(*directories: int) -> None:
    for fd in directories:
        try:
            os.fsync(fd)
        except OSError:
            pass


def _discard_trash_entry(trash: int, entry: int, restore_id: str) -> None:
    """Best-effort cleanup once the entry no longer contains a file."""
    try:
        os.unlink("path", dir_fd=entry)
    except OSError:
        pass
    try:
        os.rmdir(restore_id, dir_fd=trash)
        os.fsync(trash)
    except OSError:
        pass


@_vault_mutation
def delete(payload: dict) -> dict:
    """Move a visible, unpinned vault file into trash by atomic rename."""
    root, parts = _trash_target(payload)
    where = "/".join(parts)
    name = parts[-1]
    source = _walk(root, parts[:-1])
    try:
        try:
            st = os.stat(name, dir_fd=source, follow_symlinks=False)
        except OSError:
            raise _missing(root, parts) from None
        if stat.S_ISDIR(st.st_mode):
            raise FilesError(400, "only files can be deleted here")
        if not stat.S_ISREG(st.st_mode):
            raise _missing(root, parts)
        with _trash_root(create=True) as trash:
            restore_id = secrets.token_hex(16)
            os.mkdir(restore_id, mode=0o700, dir_fd=trash)
            entry = os.open(restore_id, _DIRECTORY, dir_fd=trash)
            try:
                # Make the original pathname durable before the file moves.
                os.rename(_tmp_in(entry, os.fsencode(where), 0o600), "path",
                          src_dir_fd=entry, dst_dir_fd=entry)
                os.fsync(entry)
                os.fsync(trash)
                _renameat2(source, name, entry, "file")
                _sync_moved_file(source, entry)
            except BaseException:
                _discard_trash_entry(trash, entry, restore_id)
                raise
            finally:
                os.close(entry)
    except FileNotFoundError:
        raise _missing(root, parts) from None
    except OSError as exc:
        raise FilesError(409, f"could not move vault/{where} to trash: {exc.strerror}") from None
    finally:
        os.close(source)
    return {"ok": True, "restore_id": restore_id}


@_vault_mutation
def restore(payload: dict) -> dict:
    """Restore a trash entry only to its original, still available name."""
    if payload.get("root") != "vault":
        raise FilesError(400, "only vault files can be restored")
    restore_id = payload.get("restore_id")
    if not isinstance(restore_id, str) or not re.fullmatch(r"[0-9a-f]{32}", restore_id):
        raise FilesError(400, "`restore_id` must come from a file deletion")
    try:
        with _trash_root() as trash:
            entry = os.open(restore_id, _DIRECTORY, dir_fd=trash)
            try:
                fd = os.open("path", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                             | os.O_CLOEXEC, dir_fd=entry)
                try:
                    path = os.fsdecode(_read_all(fd, 64 * 1024))
                finally:
                    os.close(fd)
                root, parts = _trash_target({"root": "vault", "path": path})
                dest = _walk(root, parts[:-1])
                try:
                    if not stat.S_ISREG(os.stat("file", dir_fd=entry, follow_symlinks=False).st_mode):
                        raise FilesError(404, "that deletion is no longer in the vault trash")
                    try:
                        _renameat2(entry, "file", dest, parts[-1])
                    except FileExistsError:
                        raise FilesError(409, f"vault/{path} already exists; "
                                              "the deleted file is still in trash") from None
                    row = _settle(dest, root, parts)
                    _sync_moved_file(entry)
                finally:
                    os.close(dest)
                _discard_trash_entry(trash, entry, restore_id)
            finally:
                os.close(entry)
    except FileNotFoundError:
        raise FilesError(404, "that deletion is no longer in the vault trash") from None
    except OSError as exc:
        raise FilesError(409, f"could not restore the vault file: {exc.strerror}") from None
    return {**row, "ok": True}


# --- Uploading ---------------------------------------------------------------

# Uploads share attachment limits and also accept editable text and phone-camera formats.
UPLOAD_MAX = cfg.ATTACHMENT_MAX_BYTES
UPLOAD_TYPES = {**cfg.ATTACHMENT_TYPES, **TEXT_TYPES,
                "heic": "image/heic", "heif": "image/heif"}
# How far the design's "IMG_2041 2.jpg" counts before it gives up.
UPLOAD_SUFFIX_MAX = 999
# What the first bytes of a binary type must be. The extension decides the
# type, and a file whose bytes are not that type is refused rather than
# stored under a name that lies about it: a preview trusts the name.
_ZIP = (b"PK\x03\x04",)
UPLOAD_MAGIC = {
    "png": (b"\x89PNG\r\n\x1a\n",), "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",), "gif": (b"GIF87a", b"GIF89a"),
    "pdf": (b"%PDF-",), "docx": _ZIP, "xlsx": _ZIP, "pptx": _ZIP,
    "odt": _ZIP, "epub": _ZIP,
    # A ZIP with no entries begins at its end record.
    "zip": _ZIP + (b"PK\x05\x06",),
}


def _magic_ok(ext: str, data: bytes) -> bool:
    if ext == "webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if ext == "webm":
        return data.startswith(b"\x1a\x45\xdf\xa3")
    if ext == "mov":
        return data[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free")
    if ext in ("heic", "heif", "mp4"):
        return data[4:8] == b"ftyp"
    if ext == "pdf":
        # The header may follow a little junk; readers look in the first KiB.
        return b"%PDF-" in data[:1024]
    marks = UPLOAD_MAGIC.get(ext)
    return marks is None or data.startswith(marks)


def upload_name(raw) -> str:
    """The name an uploaded file asks for, as the person's disk called it.

    Only the last part of a path a browser sent (a Windows browser may send
    backslashes) is kept, NFC as the vault spells names. A name that would
    have to be rewritten to be safe -- control characters, hidden, ``.`` or
    ``..``, too long -- is refused, not rewritten: the file the owner sees must
    be the name they chose."""
    if not isinstance(raw, str):
        raise FilesError(400, "the file part has no filename")
    name = unicodedata.normalize(
        "NFC", raw.replace("\\", "/").rsplit("/", 1)[-1]).strip()
    if name in ("", ".", ".."):
        raise FilesError(400, "the file part has no filename")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise FilesError(400, "a file name cannot hold control characters")
    if name.startswith("."):
        raise FilesError(400, f"{name!r} is a hidden name; Files does not "
                              f"create hidden files")
    # Room for the " 999" a duplicate may need, inside the 255 a name may be.
    if len(name.encode("utf-8", "surrogatepass")) > 240:
        raise FilesError(400, "the file name is longer than 240 bytes")
    return name


def _numbered(name: str, n: int) -> str:
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    return f"{stem} {n}.{ext}" if ext else f"{stem} {n}"


def upload(root: str, raw_folder: str | None, part: dict) -> dict:
    """One uploaded file, written into a folder the owner may create in.

    ``part`` is a multipart file part: ``{"filename", "bytes"}`` (the type the
    browser claimed is not read). The name is never an overwrite: a taken one
    becomes ``name 2.ext``, ``name 3.ext``, decided by ``link(2)`` at the
    instant of creation. Bytes are stored exactly as sent, except a vault
    Markdown note, which gains the owner's provenance like any note they create
    here. Every refusal comes before a byte is written."""
    _check_root(root)
    parent = parse_path(raw_folder)
    name = upload_name(part.get("filename"))
    data = part.get("bytes", b"")
    if len(data) > UPLOAD_MAX:
        raise FilesError(413, f"{name} is {len(data)} bytes; Files uploads up "
                              f"to {UPLOAD_MAX}", size=len(data), max=UPLOAD_MAX)
    _creatable(root, parent, name)
    ext = _ext(name)
    if ext not in UPLOAD_TYPES:
        raise FilesError(415, (f"Files does not upload .{ext} files" if ext else
                               "a file needs a name with an extension"),
                         types=sorted(UPLOAD_TYPES))
    if not _magic_ok(ext, data):
        raise FilesError(415, f"{name} is not a {ext.upper()} file: its bytes "
                              f"do not begin as one does")
    sent = data
    if ext in ("md", "markdown"):
        try:
            if b"\x00" in data:
                raise UnicodeDecodeError("utf-8", data, 0, 1, "NUL")
            text = data.decode("utf-8").removeprefix("﻿")
        except UnicodeDecodeError:
            raise FilesError(415, f"{name} is not UTF-8 text; a Markdown note "
                                  f"is") from None
        data = with_provenance(root, parent + [name], text,
                               created=True).encode("utf-8")
    dfd = _walk(root, parent)
    try:
        tmp = _tmp_in(dfd, data, 0o644)
        try:
            for n in range(1, UPLOAD_SUFFIX_MAX + 1):
                final = name if n == 1 else _numbered(name, n)
                try:
                    # link(2) never replaces, so a name taken by anything --
                    # a file, a folder, a link, a racing upload -- is EEXIST
                    # and the next number is tried.
                    os.link(tmp, final, src_dir_fd=dfd, dst_dir_fd=dfd,
                            follow_symlinks=False)
                    break
                except FileExistsError:
                    continue
            else:
                raise FilesError(409, f"{root}/{'/'.join(parent + [name])} and "
                                      f"{UPLOAD_SUFFIX_MAX - 1} numbered copies "
                                      f"exist; rename one")
        finally:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
        out = _settle(dfd, root, parent + [final])
    finally:
        os.close(dfd)
    out.update({"revision": revision(data), "requested_name": name,
                "renamed": final != name, "provenance": data != sent})
    if root == "vault":
        import loam_recent  # noqa: PLC0415
        loam_recent.record_write("/".join(parent + [final]), kind="owner",
                                 actor=lc.OWNER_ID, source="app",
                                 vault=root_path("vault"))
    return out


# --- Links and references ----------------------------------------------------

def host_path(root: str, parts: list[str]) -> Path:
    if root == "system":
        node = _system_node(parts)
        if isinstance(node, dict):
            raise FilesError(400, "a System folder is not a file")
        return Path(node)
    return root_path(root).joinpath(*parts)


def _from_host(raw: str) -> tuple[str, list[str]] | None:
    """An absolute host path inside a root, as that root and its parts --
    by string, since the parts are then walked like any other request."""
    path = os.path.normpath(raw)
    for virtual, rel in SYSTEM_FILES.items():
        if path == os.path.normpath(str(Path(SYSTEM_BASE) / rel)):
            return "system", virtual.split("/")
    for root in ("vault", "spaces"):
        base = str(root_path(root))
        if path == base:
            return root, []
        if path.startswith(base + "/"):
            return root, path[len(base) + 1:].split("/")
    return None


# Bound suffix searches and report truncation when the walk stops early.
RESOLVE_SCAN_MAX = 50_000
RESOLVE_CANDIDATES_MAX = 50


def _vault_tree() -> tuple[list[list[str]], list[list[str]], bool]:
    """Every visible vault file and folder as its parts, and whether the walk
    stopped at ``RESOLVE_SCAN_MAX`` names. Links are neither followed nor
    listed, as files or as folders."""
    found: list[list[str]] = []
    folders: list[list[str]] = []
    seen = 0
    base = root_path("vault")
    for dirpath, dirnames, filenames in os.walk(base):
        rel = list(Path(dirpath).relative_to(base).parts)
        seen += len(dirnames) + len(filenames)
        if seen > RESOLVE_SCAN_MAX:
            return found, folders, True
        dirnames[:] = sorted(d for d in dirnames if visible("vault", rel, d)
                             and not os.path.islink(os.path.join(dirpath, d)))
        folders.extend(rel + [d] for d in dirnames)
        for name in filenames:
            if (visible("vault", rel, name)
                    and not os.path.islink(os.path.join(dirpath, name))):
                found.append(rel + [name])
    return found, folders, False


def _ends_with(paths: list[list[str]], tries: list[list[str]]) -> list[list[str]]:
    """Find vault paths by case-insensitive whole-component suffix for shortest-path links."""
    wanted = [[p.casefold() for p in t] for t in tries if t]
    hits = []
    for parts in paths:
        folded = [p.casefold() for p in parts]
        if any(len(folded) >= len(w) and folded[-len(w):] == w for w in wanted):
            hits.append(parts)
    return hits


def _exists(root: str, parts: list[str]) -> dict | None:
    try:
        return stat_resource(root, "/".join(parts))
    except FilesError:
        return None



def _is_app_host(netloc: str) -> bool:
    """Recognize configured app hosts and links using the legacy loam-ui prefix."""
    host = netloc.lower().partition(":")[0]
    if host.startswith("loam-ui."):
        return True
    try:
        import loam_deploy  # noqa: PLC0415
        return host in {h for a in loam_deploy.records() if a.app_shell
                        for h in a.hostnames}
    except Exception:  # noqa: BLE001 -- no deploy layer: only the old form
        return False

def resolve(ref: str | None, from_root: str | None = None,
            from_path: str | None = None) -> dict:
    """What a link in a chat or a note points at, as a Files resource.

    Answers ``status``: ``found`` with ``resource``; ``ambiguous`` with
    ``candidates`` (never a guess); ``missing``; ``external`` for a web link,
    which stays a web link."""
    if not isinstance(ref, str) or not ref.strip():
        raise FilesError(400, "`ref` is the link to resolve")
    ref = ref.strip()
    fragment = None
    out_of_scope = {"status": "missing", "reason": "outside Files' roots"}

    def found(root: str, parts: list[str]) -> dict:
        row = _exists(root, parts)
        if row is None:
            return {"status": "missing", "root": root, "path": "/".join(parts),
                    "fragment": fragment}
        return {"status": "found", "resource": row, "fragment": fragment}

    if ref.startswith("![[") and ref.endswith("]]"):
        ref = ref[1:]                 # an embed names its file the same way
    if ref.startswith("[[") and ref.endswith("]]"):
        # `\|` is the alias pipe escaped inside a Markdown table.
        target = ref[2:-2].split("|", 1)[0].removesuffix("\\")
        target, _, frag = target.partition("#")
        fragment = frag or None
        return _wiki(target.strip(), from_root, from_path, fragment)

    parsed = urlparse(ref)
    if parsed.scheme in ("http", "https", "mailto"):
        if parsed.path.startswith("/files/") and _is_app_host(parsed.netloc):
            ref = parsed.path + (("#" + parsed.fragment) if parsed.fragment else "")
            parsed = urlparse(ref)
        else:
            return {"status": "external", "url": ref}
    if parsed.scheme == "obsidian":
        query = parse_qs(parsed.query)
        file = (query.get("file") or [""])[0]
        if not file:
            return {"status": "missing", "reason": "no file in that link"}
        parts = parse_path(file)
        if parts and not _ext(parts[-1]):
            # A note by that name first; the folder only where there is none.
            note = _exists("vault", parts[:-1] + [parts[-1] + ".md"])
            folder = _exists("vault", parts)
            if not (note and note["kind"] == "file") and folder \
                    and folder["kind"] == "folder":
                return {"status": "found", "resource": folder,
                        "fragment": fragment}
            parts[-1] += ".md"
        return found("vault", parts)
    if parsed.scheme:
        return {"status": "external", "url": ref}

    fragment = unquote(parsed.fragment) or None
    path = parsed.path
    if path.startswith("/files/"):
        segments = path[len("/files/"):].split("/")
        root = segments[0]
        _check_root(root)
        parts = parse_path("/".join(unquote(s) for s in segments[1:]
                                    if s != ""))
        return found(root, parts)
    if path.startswith("/"):
        hit = _from_host(unquote(path))
        if hit is None:
            return out_of_scope
        root, parts = hit
        parts = parse_path("/".join(parts))
        return found(root, parts)
    # A path with no scheme: resolved as a wiki-link target is.
    return _wiki(unquote(path), from_root, from_path, fragment)


def _wiki(target: str, from_root: str | None, from_path: str | None,
          fragment: str | None) -> dict:
    """Resolve explicit relative links from their note. Otherwise try the note’s
    directory, the root and then a unique vault suffix. Prefer notes to folders
    unless the target ends in a slash."""
    root = from_root if from_root in ("vault", "spaces") else "vault"
    # The linking note's folder counts only in its own root: a System
    # document's folder is not a vault folder.
    here = (parse_path(from_path)[:-1]
            if from_path and (from_root or "vault") == root else None)
    if not target:
        if fragment and from_path and here is not None:
            # `[[#Heading]]`: a heading in the linking note itself.
            row = _exists(root, here + parse_path(from_path)[-1:])
            if row and row["kind"] == "file":
                return {"status": "found", "resource": row, "fragment": fragment}
        return {"status": "missing", "reason": "an empty link",
                "fragment": fragment}
    if "\x00" in target or "\\" in target:
        return {"status": "missing", "target": target, "fragment": fragment}
    folder_only = target.endswith("/")
    raw = target.split("/")

    def first(paths: list[list[str]], kind: str) -> dict | None:
        for candidate in paths:
            row = _exists(root, candidate)
            if row and row["kind"] == kind:
                return {"status": "found", "resource": row, "fragment": fragment}
        return None

    if any(part in (".", "..") for part in raw):
        # Relative to the linking note, climbing only inside its own root.
        stack = list(here or [])
        for part in raw:
            if part in ("", "."):
                continue
            if part == "..":
                if not stack:
                    return {"status": "missing", "target": target,
                            "fragment": fragment,
                            "reason": "the link climbs out of its root"}
                stack.pop()
            else:
                stack.append(part)
        parts = parse_path("/".join(stack))
        hit = ((None if folder_only else first(_with_md(parts), "file"))
               or first([parts], "folder"))
        if hit:
            return hit
        return {"status": "missing", "root": root, "path": "/".join(parts),
                "fragment": fragment}
    try:
        parts = parse_path(target)
    except FilesError:
        return {"status": "missing", "target": target, "fragment": fragment}
    if not parts:
        return {"status": "missing", "target": target, "fragment": fragment}
    tries = _with_md(parts)
    places = ([here] if here else []) + [[]]
    if not folder_only:
        hit = first([p + t for p in places for t in tries], "file")
        if hit:
            return hit
    files_, folders, truncated = _vault_tree()
    if not folder_only:
        rows = _rows(_ends_with(files_, tries), "file")
        if rows or truncated:
            return _choice(rows, truncated, target, fragment)
    hit = first([p + parts for p in places], "folder")
    if hit:
        return hit
    return _choice(_rows(_ends_with(folders, [parts]), "folder"), truncated,
                   target, fragment)


def _rows(hits: list[list[str]], kind: str) -> list[dict]:
    rows = []
    for hit in hits:
        row = _exists("vault", hit)        # walked again, by descriptor
        if row and row["kind"] == kind:
            rows.append(row)
    rows.sort(key=lambda r: (r["path"].count("/"), r["path"].casefold()))
    return rows


def _choice(rows: list[dict], truncated: bool, target: str,
            fragment: str | None) -> dict:
    """One row is ``found``; more are a choice; a cut-short walk never finds."""
    if len(rows) == 1 and not truncated:
        return {"status": "found", "resource": rows[0], "fragment": fragment}
    if rows:
        return {"status": "ambiguous", "fragment": fragment,
                "candidates": rows[:RESOLVE_CANDIDATES_MAX],
                "candidates_total": len(rows), "truncated": truncated}
    out = {"status": "missing", "target": target, "fragment": fragment}
    if truncated:
        out["truncated"] = True
    return out


def _with_md(parts: list[str]) -> list[list[str]]:
    """The path as written, then with ``.md`` when it names no Markdown file."""
    if parts and not parts[-1].lower().endswith(".md"):
        return [parts, parts[:-1] + [parts[-1] + ".md"]]
    return [parts] if parts else []
