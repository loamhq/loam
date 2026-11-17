"""Files: the vault, the agents' spaces and the runtime's own documents, to an app.

Added 2026-10-02. The app runs in Docker and cannot see the host's trees; the
broker can, so this module is the bounded way an app reads them and, for the
files the owner owns, writes them. The routes are in ``bin/session-broker``
(``/v1/files/...``); the contract is ``broker/README.md``, "Files".

Three named roots and nothing else -- no route here takes a host path:

  ``vault``   the vault, whole, transcripts included (read-only there)
  ``spaces``  every persona's workspace and the commons
  ``system``  a curated list of the runtime's Markdown documents (read-only)

What keeps a request inside them is enforced here on the host, not in the
page:

- a path is names joined by ``/``; ``.``, ``..``, an empty part, a backslash
  or a NUL is refused before the disk is touched, and the query is decoded
  exactly once, so ``%2e%2e`` is ``..`` and refused and ``%252e%252e`` is a
  file literally named ``%2e%2e``;
- the walk is ``openat`` one component at a time with ``O_NOFOLLOW``, from a
  descriptor on the root, so a symlink anywhere on the way -- including one
  swapped in between a listing and a read -- is refused by the kernel, not by
  a string comparison done earlier;
- secrets and runtime-private files are not merely unlisted: every component
  of every path is judged, so naming one directly answers the same ``404`` as
  a file that does not exist.

Writes are the owner's, never a persona's: the app is their surface, and a
persona writes through its own tools under its own guard. Nothing here
deletes, an upload never replaces a file, and a rename (added 2026-11-13)
never replaces one either: it moves a vault file or folder to a name that is
free, decided by the kernel (``renameat2`` with ``RENAME_NOREPLACE``).
"""

from __future__ import annotations

import ctypes
import fnmatch
import hashlib
import mimetypes
import os
import re
import secrets
import stat
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import loam_config as lc
from urllib.parse import parse_qs, quote, unquote, urlparse

import loam_broker as cfg

LIST_DEFAULT = 200
LIST_MAX = 1000
# A folder this big is answered as far as this and says so; 15_CONVERSATIONS
# held ~3,000 files on 2026-10-02.
SCAN_MAX = 50_000
TEXT_READ_MAX = 2 * 1024 * 1024
# The JSON body carrying it is capped at 4 MiB by the broker, and JSON escaping
# can double a text, so 1 MiB is what a save can be sure to carry.
TEXT_WRITE_MAX = 1024 * 1024
DOWNLOAD_MAX = 50 * 1024 * 1024
CHUNK = 64 * 1024

# The owner's vault identity, as the provenance convention spells it
# (99_SYSTEM/conventions/provenance.md) and as their own files already carry it.
USER_LINK = lc.OWNER_LINK

# The files a person edits as text, by extension. Creating any other kind is
# refused; saving one in place is too, because a save is a text body. HTML is
# here as stored text: the broker only reads and writes its bytes, never
# serves it as a page (a download is `CSP: sandbox`, `attachment`), and
# rendering it is the app's, in an isolated origin.
TEXT_TYPES = {
    "md": "text/markdown", "markdown": "text/markdown", "txt": "text/plain",
    "csv": "text/csv", "tsv": "text/tab-separated-values",
    "json": "application/json", "jsonl": "application/x-ndjson",
    "yaml": "application/yaml", "yml": "application/yaml",
    "toml": "application/toml", "base": "application/yaml",
    "canvas": "application/json",
    "html": "text/html", "htm": "text/html",
}

# Never shown, never read, whatever root they are found in. The secrets on this
# box live outside the three roots (agents/wake.env, secrets.env, state/,
# ~/.claude); these are defence in depth against one landing inside.
SECRET_NAME = re.compile(
    r"(?i)(^\.env|\.env$|\.pem$|\.key$|\.p12$|\.pfx$|\.keystore$"
    r"|^id_(rsa|dsa|ecdsa|ed25519)|^(auth|credentials|\.credentials)\.json$"
    r"|^token$|\.token$|^\.netrc$|^\.git-credentials$)")
# Runtime churn in a workspace: the ai-labs .gitignore's list of what is not
# state (logs, caches, lock files, scratch, interpreter leftovers).
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
# A persona's schedule records, `<agent>/crons/<name>.md`: its own and
# the owner's alike (2026-10-15: a schedule's detail and editing are its file,
# opened here). A save is attributed through lib/loam_schedule.py's claim.
SCHEDULES_DIR = "crons"

TRANSCRIPTS = "15_CONVERSATIONS"

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
# System files that are verbatim copies of a vault note (bin/provision's
# ENGINEER_ROLE_SRC and ENGINEER_PROJECT_SRC; check-drift holds them equal).
# Their rows name the note as `source`: the vault note is the one to edit,
# and the System copy follows it on the next provision.
SYSTEM_VAULT_SOURCES = {
    "Engineer/Role.md": "40_PROJECTS/Loam/engineer/role.md",
    "Engineer/Platform project.md": "40_PROJECTS/Loam/engineer/projects/loam.md",
}

# Where the vault keeps the system's own documents: its conventions and
# schemas, and the platform's design, architecture and decisions.
# The folder names are the install's (`vault.system`, `vault.projects`); on
# the first install they are 99_SYSTEM and 40_PROJECTS, as before. Only the
# ones that exist are offered (``shortcuts``).
_SYS, _PROJ = lc.vault_folder("system"), lc.vault_folder("projects")
VAULT_SHORTCUTS = (
    ("Vault system", _SYS),
    ("Conventions", f"{_SYS}/conventions"),
    ("Loam project", f"{_PROJ}/Loam"),
    ("Loam architecture", f"{_PROJ}/Loam/architecture"),
    ("Loam infrastructure", f"{_PROJ}/Loam/infrastructure"),
    ("Loam decisions", f"{_PROJ}/Loam/decisions"),
)

ROOTS = {
    "vault": {"name": "Vault",
              "description": "The vault, whole. Transcripts are read-only."},
    "spaces": {"name": "AI Spaces",
               "description": "Each persona's workspace, and the commons."},
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
        # Every agent, archived ones too (2026-11-14): their files stay.
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
        fd = os.open(root_path(root), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        raise FilesError(404, f"the {root} root is not on this box") from None
    try:
        for part in parts:
            nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                          | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = nfd
    except OSError:
        os.close(fd)
        # ENOENT, ENOTDIR, ELOOP (a link) and EACCES all answer alike: what
        # Files cannot reach does not exist for the asker.
        raise _missing(root, parts) from None
    return fd


def _open_file(root: str, parts: list[str]) -> tuple[int, os.stat_result]:
    """A read descriptor on the regular file ``parts`` names, and its stat."""
    if not parts:
        raise FilesError(400, "that is a folder; read a file in it")
    dfd = _walk(root, parts[:-1])
    try:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
                     | os.O_NONBLOCK, dir_fd=dfd)
    except OSError:
        raise _missing(root, parts) from None
    finally:
        os.close(dfd)
    st = os.fstat(fd)
    if stat.S_ISDIR(st.st_mode):
        os.close(fd)
        raise FilesError(400, f"{root}/{'/'.join(parts)} is a folder; list it")
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise _missing(root, parts)
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
        return "transcripts" if parts[:1] == [TRANSCRIPTS] else "user"
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
        return {"read": True, "download": kind == "file",
                "write": writable and text,
                "create": writable and kind == "folder",
                "rename": writable and bool(parts)
                          and pinned(root, parts, kind) is None}
    if root == "spaces":
        return {"read": True, "download": kind == "file",
                "write": owner in ("user", "schedule") and text,
                "create": False, "rename": False}
    return {"read": True, "download": kind == "file", "write": False,
            "create": False, "rename": False}


# What a rename may not move (2026-11-13): vault paths the runtime reads or
# writes by name, so that renaming one would quietly break it -- a thread's
# board, the comms board, the receipts an engineer job files, the notes
# `bin/provision` copies, the attachments folder chat records name. A path is
# pinned when it matches one of these (``*`` is one whole name), or when it
# is a folder holding one that exists. Every top-level vault folder is pinned
# too: they are the vault's structure. Not exhaustive of every note a persona
# cites by path; a persona's link to a renamed note breaks as it would in
# Obsidian with link-updating off, and is mended the same way.
RENAME_PINNED = (
    "00_INBOX/boards/*",
    "00_INBOX/from-agents/comms.md",
    "00_INBOX/from-agents/loam-wake-failures.md",
    "00_INBOX/archive",
    "40_PROJECTS/*/receipts",
    "40_PROJECTS/Loam/engineer/role.md",
    "40_PROJECTS/Loam/engineer/projects/loam.md",
    "40_PROJECTS/Loam/infrastructure/schedule.md",
    "40_PROJECTS/Loam/architecture",
    "40_PROJECTS/Loam/decisions",
    "40_PROJECTS/Loam/reports",
    "99_SYSTEM/Dictation terms.md",
    "99_SYSTEM/conventions",
)


def pinned(root: str, parts: list[str], kind: str) -> str | None:
    """Why ``parts`` may not be renamed, or None. Vault only."""
    if root != "vault" or not parts:
        return None
    if len(parts) == 1 and kind == "folder":
        return "a top-level vault folder is the vault's structure"
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


def _entry(root: str, parts: list[str], st: os.stat_result) -> dict:
    kind = "folder" if stat.S_ISDIR(st.st_mode) else "file"
    return {
        "root": root,
        "path": "/".join(parts),
        "name": parts[-1] if parts else ROOTS[root]["name"],
        "kind": kind,
        "size": st.st_size if kind == "file" else None,
        "modified": _iso(st.st_mtime),
        "media_type": media_type(parts[-1]) if kind == "file" and parts else None,
        "authority": authority(root, parts),
        "capabilities": capabilities(root, parts, kind),
        "link": link(root, parts),
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


def revision(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


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


def system_inventory() -> list[dict]:
    """Every System entry and the host file it is, for the report and the
    README; ``present`` is whether it is on the box today."""
    return [{"path": virtual, "source": str(Path(SYSTEM_BASE) / rel),
             "present": (Path(SYSTEM_BASE) / rel).is_file()}
            for virtual, rel in SYSTEM_FILES.items()]


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
    return {"roots": out, "shortcuts": shortcuts()}


def shortcuts() -> list[dict]:
    """The vault folders that document the system, offered beside System as
    plain vault rows -- their capabilities are the vault's, so a shortcut is
    a way in, never a second copy or a second authority. Only those that
    exist and are visible today."""
    out = []
    for label, path in VAULT_SHORTCUTS:
        row = _exists("vault", path.split("/"))
        if row and row["kind"] == "folder":
            out.append({"label": label, "resource": row})
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
    folder = _entry(root, parts, here)
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
        return _entry(root, parts, st)
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
    out = _entry(root, parts, st)
    out["size"] = len(data)
    out.update({"text": text, "revision": revision(data)})
    return out


def download(root: str, raw_path: str | None) -> tuple[int, int, dict]:
    """A descriptor to stream, its size, and the headers it goes with. The
    caller closes the descriptor."""
    fd, st, parts = open_file(root, raw_path)
    if st.st_size > DOWNLOAD_MAX:
        os.close(fd)
        raise FilesError(413, f"{root}/{'/'.join(parts)} is {st.st_size} "
                              f"bytes; Files downloads up to {DOWNLOAD_MAX}.",
                         size=st.st_size, max=DOWNLOAD_MAX)
    name = parts[-1]
    mtype = media_type(name)
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
    """The provenance link a save is stamped with: the owner's, or, when a
    save names a live persona as ``by`` (2026-11-16, for a plugin acting for
    one: doc-comments), that persona's, as its transcripts spell it. Anything
    else ``by`` might say is refused rather than stamped."""
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

# Added 2026-11-13 (the owner: "I couldn't find a way to easily edit the name of
# the file"). A vault file or folder, to a new name in its folder or a new
# path in the vault. The content is not touched: no provenance is stamped,
# because a rename is not an edit, and the mtime is the file's own.
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
        # A link is absent to Files, here as everywhere: never listed, never
        # followed, never renamed (move_file and delete_file refuse links
        # since 2026-10-13; on 10-02 a link's target was deleted through one).
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
    return out


# --- Uploading ---------------------------------------------------------------

# A file from the owner's device into a vault folder (added 2026-10-02, the
# design's fourth Add action). The cap and the types are a chat attachment's
# (cfg.ATTACHMENT_MAX_BYTES, cfg.ATTACHMENT_TYPES) -- one convention for "a
# file this box accepts from a browser" -- plus the text types Files already
# edits and the two formats a phone's camera writes.
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
}


def _magic_ok(ext: str, data: bytes) -> bool:
    if ext == "webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if ext in ("heic", "heif"):
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


# How far a by-name or partial-path lookup reads the vault before it stops and
# says so (``truncated``), and how many candidates an ambiguous answer carries.
# The vault held ~3,600 visible files on 2026-10-02; a lookup took 0.04 s.
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
    """The paths that end with one of ``tries``, whole names only and
    case-insensitively: Obsidian's shortest-path linking, where ``[[state]]``
    and ``[[Loam/state]]`` both name ``40_PROJECTS/Loam/state.md``."""
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
    """Whether a link's host is Loam's page: the app's own name today (the
    domain itself since 2026-11-15, first release L14), or any `loam-ui.`
    name, which is what every link written before that day carries."""
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
    """A wiki-link's target or a relative Markdown link's path, as Obsidian
    reads it: relative to the linking note when it says so (``./``, ``../``);
    otherwise from the linking note's folder, then from the root's top, then
    by the one vault path that ends with it.

    A folder is an answer only where no note is: every step looks for a file
    first, and a folder is looked for only once no file was found anywhere.
    A trailing ``/`` (``[[40_PROJECTS/Loam/]]``) asks for a folder alone."""
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
