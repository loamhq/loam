"""Track who wrote each vault file and answer Recent for Files.

Vault auto-commit runs on a timer under one bot identity, and Markdown
frontmatter is neither required nor trustworthy on its own (engineer writes
often carry none, and launcher receipts carry the briefing persona's name).
So Loam records write authorship positively in a small SQLite WAL ledger
under ``STATE/files/attribution.sqlite``:

- App Files API saves, creates and uploads -> ``owner`` (or the named persona
  when a plugin passes ``by``).
- Syncthing deliveries from paired devices (``ItemFinished`` events in real
  time, and ``/rest/db/file`` ``local.modifiedBy != myID`` on catch-up/backfill)
  -> ``owner``.
- Persona tool hooks (``hooks/loading_size.py``) and turn/wake start-end vault
  snapshots -> that persona (``agent``).
- Engineer jobs and launcher receipt/artifact writes -> ``engineer`` (always
  excluded from Recent).
- Anything written on disk without a positive owner or agent record (a running
  engineer job before its end-diff, a script, a background helper) ->
  unattributed, and **left out of Recent** rather than credited to the owner.

Recent lists only files a person reads: Markdown notes, HTML pages, PDFs and
images (not JSON, code or data files). Reads never block on git:
``recent_files`` walks the vault outside Loam files and structural exclusions
with ``os.scandir`` (tens of milliseconds even on a large vault) and resolves
authorship against the ledger.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import sqlite3
import ssl
import stat
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import loam_broker as cfg
import loam_config as lc

# Grace window for matching a recorded write to the file's mtime on disk
# (covers filesystem timestamp granularity and hook-vs-rename ordering).
MTIME_TOLERANCE_NS = 2_000_000_000

DEFAULT_WINDOW_HOURS = 48
DEFAULT_MAX_ROWS = 10
WEEK_DAYS = 7
WEEK_MAX_ROWS = 50
GROUP_THRESHOLD = 3

VALID_KINDS = ("owner", "agent", "engineer", "runtime")
VALID_WHO = ("all", "you", "agents")
VALID_WINDOWS = ("default", "week")

# Files a person reads: Markdown notes, HTML pages, PDFs and images.
# Excludes JSON, code, logs and other data files.
RECENT_EXTENSIONS = frozenset({
    ".md", ".markdown",
    ".html", ".htm",
    ".pdf",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".heic", ".heif",
})

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SYNCTHING_ADDR_RE = re.compile(r"^(?:(https?)://)?(\[?[^\]:/]+\]?):([0-9]{1,5})/?$")

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)
_FM_AUTHOR_RE = re.compile(r"(?m)^(last_modified_by|created_by)\s*:\s*(.+?)\s*$")
_HTML_COMMENT_AUTHOR_RE = re.compile(
    r"<!--[^\n]*?(?:last_modified_by|created_by)\s*:\s*([^\n·>]+?)(?:\s*(?:·|-->))", re.I)
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


def is_readable_file(name_or_path: str | Path) -> bool:
    """True when ``name_or_path`` is a readable note, HTML page, PDF or image."""
    return Path(name_or_path).suffix.lower() in RECENT_EXTENSIONS


def db_path() -> Path:
    return lc.STATE / "files" / "attribution.sqlite"


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS writes (
            rel_path TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            actor TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            recorded_at REAL NOT NULL,
            source TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    return conn


def _get_meta(key: str) -> str | None:
    path = db_path()
    if not path.is_file():
        return None
    try:
        conn = _connect()
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return None


def _set_meta(key: str, value: str) -> None:
    try:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        pass


def _vault_root(vault: Path | None = None) -> Path:
    return (vault or cfg.VAULT).resolve()


def _loam_folder_parts() -> list[tuple[str, ...]]:
    """Configured Loam files folders as path-part tuples."""
    out: list[tuple[str, ...]] = []
    for key in ("conversations", "attachments", "system", "boards"):
        raw = lc.vault_folder(key)
        if not raw:
            continue
        parts = tuple(p for p in Path(raw).parts if p not in ("", "."))
        if parts:
            out.append(parts)
    return out


def _structural_exclusions() -> tuple[tuple[str, ...], list[tuple[str, ...]]]:
    """Return ``(projects_parts, loam_folder_parts)`` for fast path filtering."""
    proj_raw = lc.vault_folder("projects") or "Projects"
    proj_parts = tuple(p for p in Path(proj_raw).parts if p not in ("", "."))
    return proj_parts, _loam_folder_parts()


def _board_paths() -> frozenset[str]:
    """Vault-relative POSIX paths of every known board:
    - All thread boards in the boards plugin's thread-files record
      (``thread-files.json`` / legacy ``file-bindings.json``).
    - The General board's configured path (``vault.general_board``).
    """
    paths: set[str] = set()
    general = lc.get("vault.general_board", "")
    if isinstance(general, str) and general.strip():
        rel = to_rel_path(general.strip())
        if rel:
            paths.add(rel)
    try:
        import loam_thread_files as ltf  # noqa: PLC0415
        src = ltf._source()
        if src.is_file():
            data = json.loads(src.read_text("utf-8"))
            boards = data.get("boards") if isinstance(data, dict) else None
            if isinstance(boards, dict):
                for row in boards.values():
                    if not isinstance(row, dict):
                        continue
                    for key in ("path", "moving_to"):
                        raw = row.get(key)
                        if isinstance(raw, str) and raw.strip():
                            rel = to_rel_path(raw.strip())
                            if rel:
                                paths.add(rel)
    except Exception:
        pass
    return frozenset(paths)


def excluded_parts(parts: tuple[str, ...] | list[str], *,
                   board_paths: frozenset[str] | None = None) -> bool:
    """True when a vault-relative path belongs to Loam files, dotfiles,
    boards, launcher receipts, or runtime/engineer infrastructure files."""
    if not parts:
        return True
    if any(p.startswith(".") or p in ("..",) for p in parts):
        return True
    proj_parts, loam_parts = _structural_exclusions()
    for lp in loam_parts:
        if len(parts) >= len(lp) and tuple(parts[:len(lp)]) == lp:
            return True
    if proj_parts and len(parts) >= len(proj_parts) + 2 and tuple(parts[:len(proj_parts)]) == proj_parts:
        sub = parts[len(proj_parts):]
        # <projects>/<project>/receipts/...
        if len(sub) >= 2 and sub[1] == "receipts":
            return True
        # <projects>/Loam/engineer/...
        if len(sub) >= 2 and sub[0] == "Loam" and sub[1] == "engineer":
            return True
        # <projects>/Loam/infrastructure/schedule.md
        if tuple(sub) == ("Loam", "infrastructure", "schedule.md"):
            return True
    bp = _board_paths() if board_paths is None else board_paths
    if bp and "/".join(parts) in bp:
        return True
    return False


def excluded_path(rel_path: str) -> bool:
    parts = [p for p in rel_path.strip("/").split("/") if p]
    return excluded_parts(parts)


def to_rel_path(path: str | Path, vault: Path | None = None) -> str | None:
    """Normalise an absolute or vault-relative path to a POSIX vault-relative
    string, or None if outside the vault or hidden."""
    vroot = _vault_root(vault)
    p = Path(path)
    if p.is_absolute():
        try:
            rel = p.resolve().relative_to(vroot)
        except (OSError, ValueError):
            try:
                rel = Path(os.path.abspath(p)).relative_to(vroot)
            except ValueError:
                return None
    else:
        rel = Path(str(path).lstrip("/"))
    parts = [seg for seg in rel.parts if seg not in ("", ".")]
    if not parts or any(seg == ".." or seg.startswith(".") for seg in parts):
        return None
    return "/".join(parts)


# -- writing attribution -------------------------------------------------------

def record_write(path: str | Path, *, kind: str, actor: str, source: str,
                 vault: Path | None = None, mtime_ns: int | None = None,
                 recorded_at: float | None = None) -> bool:
    """Record who wrote ``path``. Best-effort: returns False on invalid/outside
    paths or storage errors and never raises into callers."""
    if kind not in VALID_KINDS or not actor:
        return False
    rel = to_rel_path(path, vault)
    if not rel:
        return False
    vroot = _vault_root(vault)
    if mtime_ns is None:
        try:
            mtime_ns = os.stat(vroot / rel, follow_symlinks=False).st_mtime_ns
        except OSError:
            mtime_ns = time.time_ns()
    now = time.time() if recorded_at is None else float(recorded_at)
    try:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO writes (rel_path, kind, actor, mtime_ns, recorded_at, source)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(rel_path) DO UPDATE SET
                    kind = excluded.kind,
                    actor = excluded.actor,
                    mtime_ns = excluded.mtime_ns,
                    recorded_at = excluded.recorded_at,
                    source = excluded.source
                """,
                (rel, kind, str(actor), int(mtime_ns), now, source),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except (OSError, sqlite3.Error):
        return False


def record_rename(old_path: str, new_path: str, *, vault: Path | None = None) -> None:
    """Carry attribution across a file or folder rename inside the vault."""
    old_rel = to_rel_path(old_path, vault)
    new_rel = to_rel_path(new_path, vault)
    if not old_rel or not new_rel or old_rel == new_rel:
        return
    prefix = old_rel + "/"
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT rel_path, kind, actor, mtime_ns, recorded_at, source "
                "FROM writes WHERE rel_path = ? OR rel_path LIKE ?",
                (old_rel, prefix + "%"),
            ).fetchall()
            if not rows:
                return
            for rel, kind, actor, mtime_ns, recorded_at, source in rows:
                target = new_rel if rel == old_rel else new_rel + rel[len(old_rel):]
                conn.execute("DELETE FROM writes WHERE rel_path = ?", (rel,))
                conn.execute(
                    """
                    INSERT OR REPLACE INTO writes
                        (rel_path, kind, actor, mtime_ns, recorded_at, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (target, kind, actor, mtime_ns, recorded_at, source),
                )
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        pass


def record_delete(path: str, *, vault: Path | None = None) -> None:
    rel = to_rel_path(path, vault)
    if not rel:
        return
    try:
        conn = _connect()
        try:
            conn.execute(
                "DELETE FROM writes WHERE rel_path = ? OR rel_path LIKE ?",
                (rel, rel + "/%"),
            )
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        pass


def snapshot_vault(vault: Path | None = None) -> dict[str, int]:
    """Fast ``{rel_path: st_mtime_ns}`` snapshot of non-excluded regular files."""
    vroot = _vault_root(vault)
    if not vroot.is_dir():
        return {}
    proj_parts, loam_parts = _structural_exclusions()
    out: dict[str, int] = {}

    def walk(directory: Path, rel_parts: tuple[str, ...]) -> None:
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            return
        for entry in entries:
            name = entry.name
            if name.startswith("."):
                continue
            here = rel_parts + (name,)
            # Skip Loam files folders and structural exclusions early.
            if any(here == lp for lp in loam_parts):
                continue
            if proj_parts and len(here) == len(proj_parts) + 2 and here[:len(proj_parts)] == proj_parts:
                if here[-1] == "receipts" or (here[-2] == "Loam" and here[-1] == "engineer"):
                    continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                walk(Path(entry.path), here)
            elif stat.S_ISREG(st.st_mode):
                if proj_parts and here == proj_parts + ("Loam", "infrastructure", "schedule.md"):
                    continue
                out["/".join(here)] = st.st_mtime_ns

    walk(vroot, ())
    return out


def record_diff(before: dict[str, int], *, kind: str, actor: str, source: str,
                vault: Path | None = None, started_at: float | None = None) -> list[str]:
    """Compare current vault state against ``before`` and record attribution
    for every newly created or modified file.

    Preserves a more specific record written during the same window (e.g., an
    owner save through the app or a hook record at matching mtime) so concurrent
    runs do not clobber explicit attribution. Returns recorded relative paths.
    """
    if kind not in VALID_KINDS or not actor:
        return []
    after = snapshot_vault(vault)
    changed = [(rel, mtime_ns) for rel, mtime_ns in after.items()
               if rel not in before or mtime_ns > before[rel]]
    if not changed:
        return []
    now = time.time()
    recorded: list[str] = []
    try:
        conn = _connect()
        try:
            for rel, mtime_ns in changed:
                row = conn.execute(
                    "SELECT kind, actor, mtime_ns, recorded_at, source "
                    "FROM writes WHERE rel_path = ?",
                    (rel,),
                ).fetchone()
                if row is not None:
                    r_kind, _r_actor, r_mtime, r_at, r_src = row
                    fresh = started_at is not None and r_at >= started_at - 0.5
                    same_write = abs(r_mtime - mtime_ns) <= MTIME_TOLERANCE_NS
                    if fresh and same_write:
                        # Keep app saves and Syncthing deliveries unconditionally;
                        # for non-engineer diffs, also keep an explicit hook or engineer record.
                        if r_src in ("app", "syncthing"):
                            continue
                        if kind != "engineer" and r_src in ("hook", "engineer-job", "launcher"):
                            continue
                conn.execute(
                    """
                    INSERT INTO writes (rel_path, kind, actor, mtime_ns, recorded_at, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(rel_path) DO UPDATE SET
                        kind = excluded.kind,
                        actor = excluded.actor,
                        mtime_ns = excluded.mtime_ns,
                        recorded_at = excluded.recorded_at,
                        source = excluded.source
                    """,
                    (rel, kind, str(actor), int(mtime_ns), now, source),
                )
                recorded.append(rel)
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return []
    return recorded


# -- reading & querying Recent -------------------------------------------------

def _load_ledger() -> dict[str, dict]:
    path = db_path()
    if not path.is_file():
        return {}
    try:
        conn = sqlite3.connect(str(path), timeout=5.0)
        try:
            rows = conn.execute(
                "SELECT rel_path, kind, actor, mtime_ns, recorded_at, source FROM writes"
            ).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return {}
    return {
        rel: {"kind": kind, "actor": actor, "mtime_ns": mtime_ns,
              "recorded_at": recorded_at, "source": source}
        for rel, kind, actor, mtime_ns, recorded_at, source in rows
    }


def _persona_names() -> dict[str, str]:
    names: dict[str, str] = {}
    try:
        for pid in cfg.roster():
            try:
                card = cfg.agent_card(pid)
                names[pid] = str(card.get("name") or pid.title())
            except Exception:  # noqa: BLE001
                names[pid] = pid.title()
    except Exception:  # noqa: BLE001
        pass
    return names


def _resolve_author(rel: str, mtime_ns: int, ledger: dict[str, dict]) -> tuple[str | None, str]:
    """Return ``(kind, actor)`` when a positive attribution matches ``rel`` on
    disk, or ``(None, "")`` when unattributed or modified after the last
    recorded write. Unattributed writes are excluded from Recent rather than
    credited to the owner."""
    rec = ledger.get(rel)
    if rec is not None and mtime_ns <= rec["mtime_ns"] + MTIME_TOLERANCE_NS:
        kind, actor = rec["kind"], rec["actor"]
        if kind == "agent" and actor:
            try:
                import loam_agents  # noqa: PLC0415
                actor = loam_agents.resolve_id(actor, cfg.AI_LABS)
            except Exception:  # noqa: BLE001
                pass
        return kind, actor
    return None, ""


def _day_label(day_date, today_date) -> str:
    delta = (today_date - day_date).days
    if delta <= 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    return day_date.strftime("%A")


def _folder_label(folder: str) -> str:
    if not folder:
        return "Your files"
    return folder.rsplit("/", 1)[-1]


def recent_files(who: str = "all", window: str = "default", *,
                 vault: Path | None = None, now: float | None = None) -> dict:
    """Return Recent files & grouped folders for the Files home.

    - Only files in Your files outside Loam files and structural exclusions.
    - Engineer and runtime writes never appear.
    - Filtered by ``who``: ``all`` | ``you`` | ``agents``.
    - 3+ files by the same author in one folder on one local day collapse into
      one grouped row (``count`` files in ``folder_name``).
    - ``window == 'default'``: past 48h, max 10 rows (a group counts as 1); if
      past 48h is empty, fallback to 10 most recent within the past 7 days.
    - ``window == 'week'``: past 7 days, max 50 rows. Nothing older appears.
    """
    import loam_files as files_api  # noqa: PLC0415

    who = (who or "all").strip().lower()
    if who not in VALID_WHO:
        raise files_api.FilesError(400, f"`who` is one of {', '.join(VALID_WHO)}")
    window = (window or "default").strip().lower()
    if window not in VALID_WINDOWS:
        raise files_api.FilesError(400, f"`window` is one of {', '.join(VALID_WINDOWS)}")

    now_ts = time.time() if now is None else float(now)
    week_cutoff = now_ts - WEEK_DAYS * 86400
    h48_cutoff = now_ts - DEFAULT_WINDOW_HOURS * 3600
    zone = ZoneInfo(lc.TIMEZONE)
    today_date = datetime.fromtimestamp(now_ts, tz=zone).date()

    vroot = _vault_root(vault)
    ledger = _load_ledger()
    names = _persona_names()
    proj_parts, loam_parts = _structural_exclusions()
    board_paths = _board_paths()

    candidates: list[dict] = []

    def walk(directory: Path, rel_parts: tuple[str, ...]) -> None:
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            return
        for entry in entries:
            name = entry.name
            if name.startswith("."):
                continue
            here = rel_parts + (name,)
            if any(here == lp for lp in loam_parts):
                continue
            if proj_parts and len(here) == len(proj_parts) + 2 and here[:len(proj_parts)] == proj_parts:
                if here[-1] == "receipts" or (here[-2] == "Loam" and here[-1] == "engineer"):
                    continue
            if not files_api.visible("vault", list(rel_parts), name):
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                walk(Path(entry.path), here)
            elif stat.S_ISREG(st.st_mode):
                if proj_parts and here == proj_parts + ("Loam", "infrastructure", "schedule.md"):
                    continue
                if not is_readable_file(name):
                    continue
                if st.st_mtime < week_cutoff:
                    continue
                rel = "/".join(here)
                if rel in board_paths:
                    continue
                kind, actor = _resolve_author(rel, st.st_mtime_ns, ledger)
                if kind not in ("owner", "agent"):
                    continue
                if who == "you" and kind != "owner":
                    continue
                if who == "agents" and kind != "agent":
                    continue
                local_dt = datetime.fromtimestamp(st.st_mtime, tz=zone)
                day_date = local_dt.date()
                folder = "/".join(rel_parts)
                author = (
                    {"kind": "owner", "id": lc.OWNER_ID, "name": lc.OWNER_NAME}
                    if kind == "owner"
                    else {"kind": "agent", "id": actor, "name": names.get(actor, actor.title())}
                )
                row = files_api._entry("vault", list(here), st)
                candidates.append({
                    "kind": "file",
                    "root": "vault",
                    "path": rel,
                    "name": name,
                    "folder": folder,
                    "folder_name": _folder_label(folder),
                    "author": author,
                    "author_key": f"{kind}:{actor}",
                    "day": _day_label(day_date, today_date),
                    "day_key": day_date.isoformat(),
                    "modified": row["modified"],
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                    "row": row,
                })

    if vroot.is_dir():
        walk(vroot, ())

    # Group 3+ files by the same author in one folder on one local day.
    buckets: dict[tuple[str, str, str], list[dict]] = {}
    for item in candidates:
        bkey = (item["day_key"], item["author_key"], item["folder"])
        buckets.setdefault(bkey, []).append(item)

    entries: list[dict] = []
    for (day_key, author_key, folder), items in buckets.items():
        items.sort(key=lambda x: (x["mtime"], x["path"]), reverse=True)
        if len(items) >= GROUP_THRESHOLD:
            newest = items[0]
            folder_name = _folder_label(folder)
            entries.append({
                "kind": "group",
                "id": f"{day_key}:{author_key}:{folder}",
                "author": newest["author"],
                "folder": folder,
                "folder_name": folder_name,
                "label": f"{len(items)} files in {folder_name}",
                "count": len(items),
                "day": newest["day"],
                "day_key": day_key,
                "modified": newest["modified"],
                "mtime": newest["mtime"],
                "items": [
                    {
                        "root": "vault",
                        "path": it["path"],
                        "name": it["name"],
                        "folder": it["folder"],
                        "modified": it["modified"],
                        "mtime": it["mtime"],
                        "size": it["size"],
                        "row": it["row"],
                    }
                    for it in items
                ],
            })
        else:
            for it in items:
                clean = {k: v for k, v in it.items() if k != "author_key"}
                entries.append(clean)

    # Sort newest first across all rows (grouped and single).
    entries.sort(key=lambda x: (x["day_key"], x["mtime"], x.get("path") or x.get("id", "")),
                 reverse=True)

    within_week = entries[:WEEK_MAX_ROWS]
    within_48h = [e for e in entries if e["mtime"] >= h48_cutoff]

    if window == "week":
        shown = within_week
        used_fallback = False
        has_more = False
    else:
        if within_48h:
            shown = within_48h[:DEFAULT_MAX_ROWS]
            used_fallback = False
        else:
            shown = within_week[:DEFAULT_MAX_ROWS]
            used_fallback = bool(shown)
        has_more = len(within_week) > len(shown)

    days: list[dict] = []
    by_day: dict[str, dict] = {}
    for entry in shown:
        dk = entry["day_key"]
        group = by_day.get(dk)
        if group is None:
            group = {"day": entry["day"], "day_key": dk, "items": []}
            by_day[dk] = group
            days.append(group)
        group["items"].append(entry)

    return {
        "who": who,
        "window": window,
        "used_fallback": used_fallback,
        "has_more": has_more,
        "total_shown": len(shown),
        "total_week": len(within_week),
        "days": days,
    }


# -- Syncthing positive owner attribution --------------------------------------

def _parse_loopback_address(raw: str, *, tls_default: bool = False) -> tuple[str, int, bool] | None:
    text = (raw or "").strip()
    if not text:
        return None
    m = _SYNCTHING_ADDR_RE.match(text)
    if not m:
        return None
    scheme, raw_host, raw_port = m.group(1), m.group(2), m.group(3)
    host = raw_host.strip("[]").lower()
    if host not in _LOOPBACK_HOSTS:
        return None
    port = int(raw_port)
    if not 1 <= port <= 65535:
        return None
    tls = (scheme.lower() == "https") if scheme else bool(tls_default)
    return host, port, tls


def _syncthing_config_candidates() -> list[Path]:
    out: list[Path] = []
    st_home = os.environ.get("SYNCTHING_HOME")
    if st_home:
        out.append(Path(st_home).expanduser() / "config.xml")
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        out.append(Path(xdg_state).expanduser() / "syncthing" / "config.xml")
    out.append(Path.home() / ".local" / "state" / "syncthing" / "config.xml")
    xdg_cfg = os.environ.get("XDG_CONFIG_HOME")
    if xdg_cfg:
        out.append(Path(xdg_cfg).expanduser() / "syncthing" / "config.xml")
    out.append(Path.home() / ".config" / "syncthing" / "config.xml")
    out.append(Path.home() / "Library" / "Application Support" / "Syncthing" / "config.xml")
    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def discover_syncthing() -> dict | None:
    """Discover local loopback Syncthing REST credentials, or None if absent.

    Checks (in order): explicit test/env overrides, Loam's saved ``syncthing``
    provider key, and standard local Syncthing ``config.xml`` paths. Never
    connects to a non-loopback address.
    """
    if os.environ.get("LOAM_SYNCTHING_DISABLE") == "1":
        return None

    env_url = os.environ.get("LOAM_SYNCTHING_URL")
    env_key = os.environ.get("LOAM_SYNCTHING_API_KEY")
    if env_url and env_key:
        parsed = _parse_loopback_address(env_url)
        if parsed:
            host, port, tls = parsed
            return {"host": host, "port": port, "tls": tls, "apikey": env_key.strip()}
        return None

    # Check Loam's saved provider keys if configured.
    try:
        import loam_keys  # noqa: PLC0415
        saved = loam_keys._read_env(loam_keys._private("syncthing"))
        key = (saved.get("SYNCTHING_API_KEY") or "").strip()
        if key:
            url = (saved.get("SYNCTHING_GUI_URL") or "http://127.0.0.1:8384").strip()
            parsed = _parse_loopback_address(url)
            if parsed:
                host, port, tls = parsed
                return {"host": host, "port": port, "tls": tls, "apikey": key}
    except Exception:  # noqa: BLE001
        pass

    # Fall back to standard local Syncthing config.xml.
    for cfg_path in _syncthing_config_candidates():
        if not cfg_path.is_file():
            continue
        try:
            root = ET.parse(cfg_path).getroot()
        except (OSError, ET.ParseError):
            continue
        gui = root.find("gui")
        if gui is None:
            continue
        if (gui.attrib.get("enabled") or "true").lower() == "false":
            continue
        apikey = (gui.findtext("apikey") or "").strip()
        if not apikey:
            continue
        addr = (gui.findtext("address") or "127.0.0.1:8384").strip()
        tls_default = (gui.attrib.get("tls") or "false").lower() == "true"
        parsed = _parse_loopback_address(addr, tls_default=tls_default)
        if parsed:
            host, port, tls = parsed
            return {"host": host, "port": port, "tls": tls, "apikey": apikey}
    return None


def _syncthing_get(st_cfg: dict, path: str, *, http_timeout: float = 5.0):
    host = st_cfg["host"]
    port = int(st_cfg["port"])
    if st_cfg.get("tls"):
        conn = http.client.HTTPSConnection(
            host, port, timeout=http_timeout, context=ssl._create_unverified_context())  # noqa: S323
    else:
        conn = http.client.HTTPConnection(host, port, timeout=http_timeout)
    try:
        conn.request("GET", path, headers={"X-API-Key": st_cfg["apikey"]})
        with conn.getresponse() as resp:
            if not 200 <= resp.status < 300:
                raise OSError(f"Syncthing HTTP {resp.status} for {path}")
            raw = resp.read()
    finally:
        conn.close()
    return json.loads(raw.decode("utf-8")) if raw.strip() else None


def _syncthing_vault_info(st_cfg: dict, vroot: Path, *, http_timeout: float = 3.0) -> dict | None:
    """Resolve Syncthing's device ID, start time, and folder IDs covering ``vroot``."""
    try:
        status = _syncthing_get(st_cfg, "/rest/system/status", http_timeout=http_timeout) or {}
        folders = _syncthing_get(st_cfg, "/rest/config/folders", http_timeout=http_timeout) or []
    except (OSError, ValueError, http.client.HTTPException):
        return None
    my_id = str(status.get("myID") or "").strip()
    if not my_id:
        return None
    start_time = str(status.get("startTime") or "").strip()
    mapped: dict[str, str] = {}
    for f in folders:
        if not isinstance(f, dict):
            continue
        fid = str(f.get("id") or "").strip()
        raw_path = str(f.get("path") or "").strip()
        if not fid or not raw_path:
            continue
        try:
            fpath = Path(raw_path).expanduser().resolve()
        except OSError:
            continue
        if fpath == vroot:
            mapped[fid] = ""
        else:
            try:
                rel = fpath.relative_to(vroot)
                mapped[fid] = rel.as_posix()
            except ValueError:
                continue
    if not mapped:
        return None
    return {"my_short": my_id[:7], "start_time": start_time, "folders": mapped}


def sync_syncthing_events(*, vault: Path | None = None, st_cfg: dict | None = None,
                          since: int | None = None, timeout: int = 0,
                          http_timeout: float | None = None) -> dict:
    """Ingest Syncthing ``ItemFinished`` events for the vault as positive owner writes.

    Syncthing emits ``ItemFinished`` only when pulling items from a remote paired
    device (never for local filesystem edits on this box). Persists the last
    seen event ID and Syncthing ``startTime`` in the ledger's ``meta`` table so
    restarts of either Syncthing or Loam do not miss or replay stale cursors.
    """
    vroot = _vault_root(vault)
    cfg_st = st_cfg if st_cfg is not None else discover_syncthing()
    if not cfg_st:
        return {"ok": False, "reason": "unconfigured", "recorded": [], "deleted": [], "last_id": 0}
    info = _syncthing_vault_info(cfg_st, vroot, http_timeout=min(http_timeout or 3.0, 5.0))
    if not info:
        return {"ok": False, "reason": "no-vault-folder", "recorded": [], "deleted": [], "last_id": 0}

    saved_start = _get_meta("syncthing_start_time") or ""
    saved_id = 0
    try:
        saved_id = int(_get_meta("syncthing_last_event_id") or "0")
    except ValueError:
        saved_id = 0
    if info["start_time"] and saved_start and info["start_time"] != saved_start:
        saved_id = 0

    cursor = saved_id if since is None else int(since)
    req_timeout = float(http_timeout) if http_timeout is not None else max(5.0, float(timeout) + 5.0)
    qs = urllib.parse.urlencode({
        "events": "ItemFinished",
        "since": str(cursor),
        "timeout": str(max(0, int(timeout))),
    })
    try:
        events = _syncthing_get(cfg_st, f"/rest/events?{qs}", http_timeout=req_timeout) or []
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return {"ok": False, "reason": str(exc), "recorded": [], "deleted": [], "last_id": cursor}

    recorded: list[str] = []
    deleted: list[str] = []
    last_id = cursor
    folders = info["folders"]
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ev_id = ev.get("id")
        if isinstance(ev_id, int) and ev_id > last_id:
            last_id = ev_id
        if ev.get("type") != "ItemFinished":
            continue
        data = ev.get("data") or {}
        if data.get("error"):
            continue
        if data.get("type") != "file":
            continue
        fid = str(data.get("folder") or "")
        if fid not in folders:
            continue
        item = str(data.get("item") or "").replace("\\", "/").lstrip("/")
        if not item:
            continue
        prefix = folders[fid]
        rel = to_rel_path(f"{prefix}/{item}" if prefix else item, vroot)
        if not rel or excluded_path(rel):
            continue
        action = str(data.get("action") or "").lower()
        if action == "update":
            if (vroot / rel).is_file():
                if record_write(rel, kind="owner", actor=lc.OWNER_ID, source="syncthing", vault=vroot):
                    recorded.append(rel)
        elif action == "delete":
            record_delete(rel, vault=vroot)
            deleted.append(rel)

    if info["start_time"]:
        _set_meta("syncthing_start_time", info["start_time"])
    if last_id != saved_id or (since is None and _get_meta("syncthing_last_event_id") is None):
        _set_meta("syncthing_last_event_id", str(last_id))

    return {
        "ok": True,
        "recorded": recorded,
        "deleted": deleted,
        "last_id": last_id,
        "events": len(events),
    }


def _syncthing_remote_owner(rel: str, info: dict, st_cfg: dict, *, http_timeout: float = 2.0) -> bool:
    """True when Syncthing's persistent index says ``rel`` was last modified by a
    remote paired device (``local.modifiedBy != myID[:7]``)."""
    folder_id = None
    folder_rel = None
    for fid, prefix in info["folders"].items():
        if not prefix:
            folder_id, folder_rel = fid, rel
            break
        if rel.startswith(prefix + "/"):
            folder_id, folder_rel = fid, rel[len(prefix) + 1:]
            break
    if not folder_id or not folder_rel:
        return False
    qs = urllib.parse.urlencode({"folder": folder_id, "file": folder_rel})
    try:
        data = _syncthing_get(st_cfg, f"/rest/db/file?{qs}", http_timeout=http_timeout) or {}
    except (OSError, ValueError, http.client.HTTPException):
        return False
    loc = data.get("local") or {}
    mby = str(loc.get("modifiedBy") or "").strip()
    return bool(mby) and mby != info["my_short"]


# -- backfill for existing installs --------------------------------------------

def _parse_author_hint(path: Path) -> str | None:
    """Best-effort author name from Markdown frontmatter or HTML comment."""
    ext = path.suffix.lower()
    if ext not in (".md", ".markdown", ".html", ".htm"):
        return None
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:4000]
    except OSError:
        return None
    if ext in (".md", ".markdown"):
        fm = _FRONTMATTER_RE.match(head)
        if not fm:
            return None
        found: dict[str, str] = {}
        for key, val in _FM_AUTHOR_RE.findall(fm.group(1)):
            found[key] = val.strip().strip('"').strip("'")
        raw = found.get("last_modified_by") or found.get("created_by")
        if not raw:
            return None
        wiki = _WIKILINK_RE.search(raw)
        return (wiki.group(1) if wiki else raw).strip()
    m = _HTML_COMMENT_AUTHOR_RE.search(head)
    if not m:
        return None
    raw = m.group(1).strip()
    wiki = _WIKILINK_RE.search(raw)
    return (wiki.group(1) if wiki else raw).strip()


def _classify_hint(hint: str | None, persona_lookup: dict[str, str]) -> tuple[str | None, str]:
    """Positively classify an author hint, or return ``(None, "")`` when absent
    or unrecognized so unattributed files stay out of Recent."""
    if not hint:
        return None, ""
    low = hint.strip().lower()
    if not low:
        return None, ""
    if low in ("engineer", "platform engineer", "loam", "reviewer", "loam-vps") or low.endswith(" engineer"):
        return "engineer", "engineer"
    if low in persona_lookup:
        return "agent", persona_lookup[low]
    owner_tokens = {
        lc.OWNER_ID.lower(),
        lc.OWNER_NAME.lower(),
        *(x.lower() for x in lc.LEGACY_OWNER_IDS),
    }
    wiki = _WIKILINK_RE.search(lc.OWNER_LINK)
    if wiki:
        owner_tokens.add(wiki.group(1).strip().lower())
    first = low.split()[0] if low.split() else ""
    if low in owner_tokens or (first and first in owner_tokens):
        return "owner", lc.OWNER_ID
    return None, ""


def _purge_stale_backfill(vroot: Path) -> int:
    """Remove prior ``source='backfill'`` rows and entries for deleted files."""
    path = db_path()
    if not path.is_file():
        return 0
    removed = 0
    try:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM writes WHERE source = 'backfill'")
            removed += cur.rowcount or 0
            rows = conn.execute("SELECT rel_path FROM writes").fetchall()
            missing = [rel for (rel,) in rows if not (vroot / rel).is_file()]
            for rel in missing:
                conn.execute("DELETE FROM writes WHERE rel_path = ?", (rel,))
                removed += 1
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return removed
    return removed


def backfill_recent(vault: Path | None = None, *, days: int = WEEK_DAYS,
                    now: float | None = None, reset_backfill: bool = True,
                    st_cfg: dict | None = None) -> dict:
    """Backfill positive attribution for readable files changed within the past
    ``days`` days.

    Sources (positive only — unattributed files are left out of Recent):
    1. Syncthing buffered ``ItemFinished`` events + persistent ``/rest/db/file``
       remote device ``modifiedBy`` -> ``owner`` (``source='syncthing'``).
    2. Explicit frontmatter / HTML-comment author hints matching a known
       persona, owner, or engineer -> ``source='backfill'``.
    3. Files with no positive signal are skipped (unattributed).
    """
    vroot = _vault_root(vault)
    now_ts = time.time() if now is None else float(now)
    cutoff = now_ts - days * 86400

    purged = _purge_stale_backfill(vroot) if reset_backfill else 0

    cfg_st = st_cfg if st_cfg is not None else discover_syncthing()
    st_info = _syncthing_vault_info(cfg_st, vroot) if cfg_st else None
    st_events = 0
    if cfg_st and st_info:
        ev_res = sync_syncthing_events(vault=vroot, st_cfg=cfg_st, since=0, timeout=0)
        st_events = len(ev_res.get("recorded") or [])

    existing = _load_ledger()
    names = _persona_names()
    persona_lookup: dict[str, str] = {}
    for pid, display in names.items():
        persona_lookup[pid.lower()] = pid
        persona_lookup[display.lower()] = pid

    snap = snapshot_vault(vroot)
    counts = {
        "owner": 0,
        "syncthing": st_events,
        "agent": 0,
        "engineer": 0,
        "unattributed": 0,
        "skipped": 0,
        "purged": purged,
    }
    for rel, mtime_ns in sorted(snap.items()):
        if mtime_ns / 1e9 < cutoff:
            continue
        if not is_readable_file(rel):
            continue
        prev = existing.get(rel)
        if prev and prev["source"] != "backfill" and mtime_ns <= prev["mtime_ns"] + MTIME_TOLERANCE_NS:
            counts["skipped"] += 1
            continue
        if cfg_st and st_info and _syncthing_remote_owner(rel, st_info, cfg_st):
            if record_write(rel, kind="owner", actor=lc.OWNER_ID, source="syncthing",
                            vault=vroot, mtime_ns=mtime_ns, recorded_at=now_ts):
                counts["owner"] += 1
                counts["syncthing"] += 1
            continue
        hint = _parse_author_hint(vroot / rel)
        kind, actor = _classify_hint(hint, persona_lookup)
        if not kind:
            counts["unattributed"] += 1
            continue
        if record_write(rel, kind=kind, actor=actor, source="backfill",
                        vault=vroot, mtime_ns=mtime_ns, recorded_at=now_ts):
            counts[kind] = counts.get(kind, 0) + 1
    return counts
