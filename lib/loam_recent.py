"""Track who wrote each vault file and answer Recent for Files.

Vault auto-commit runs on a timer under one bot identity, and Markdown
frontmatter is neither required nor trustworthy on its own (engineer writes
often carry none, and launcher receipts carry the briefing persona's name).
So Loam records write authorship as writes happen in a small SQLite WAL ledger
under ``STATE/files/attribution.sqlite``:

- App Files API saves, creates and uploads -> ``owner`` (or the named persona
  when a plugin passes ``by``).
- Persona tool hooks (``hooks/loading_size.py``) and turn/wake start-end vault
  snapshots -> that persona (``agent``).
- Engineer jobs and launcher receipt/artifact writes -> ``engineer`` (always
  excluded from Recent).
- Anything modified on disk without a matching ledger entry (Syncthing,
  Obsidian, manual edits) -> ``owner``.

Reads never block on git: ``recent_files`` walks the vault outside Loam files
and structural exclusions with ``os.scandir`` (tens of milliseconds even on a
large vault) and resolves authorship against the ledger.
"""

from __future__ import annotations

import os
import re
import sqlite3
import stat
import time
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

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)
_FM_AUTHOR_RE = re.compile(r"(?m)^(last_modified_by|created_by)\s*:\s*(.+?)\s*$")
_HTML_COMMENT_AUTHOR_RE = re.compile(
    r"<!--[^\n]*?(?:last_modified_by|created_by)\s*:\s*([^\n·>]+?)(?:\s*(?:·|-->))", re.I)
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


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
    return conn


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


def excluded_parts(parts: tuple[str, ...] | list[str]) -> bool:
    """True when a vault-relative path belongs to Loam files, dotfiles,
    launcher receipts, or runtime/engineer infrastructure files."""
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
                        # Keep app saves unconditionally; for non-engineer diffs,
                        # also keep an explicit hook or engineer record.
                        if r_src == "app":
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


def _resolve_author(rel: str, mtime_ns: int, ledger: dict[str, dict]) -> tuple[str, str]:
    """Return ``(kind, actor)`` for ``rel`` as it currently stands on disk."""
    rec = ledger.get(rel)
    if rec is not None and mtime_ns <= rec["mtime_ns"] + MTIME_TOLERANCE_NS:
        return rec["kind"], rec["actor"]
    # Unrecorded write, or file was modified on disk after the last recorded
    # write (Syncthing, Obsidian, manual edit) -> owner.
    return "owner", lc.OWNER_ID


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
                if st.st_mtime < week_cutoff:
                    continue
                rel = "/".join(here)
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


def _classify_hint(hint: str | None, persona_lookup: dict[str, str]) -> tuple[str, str]:
    if not hint:
        return "owner", lc.OWNER_ID
    low = hint.strip().lower()
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
    if low in owner_tokens or low.split()[0] in owner_tokens:
        return "owner", lc.OWNER_ID
    return "owner", lc.OWNER_ID


def backfill_recent(vault: Path | None = None, *, days: int = WEEK_DAYS,
                    now: float | None = None) -> dict:
    """Backfill attribution for files changed within the past ``days`` days
    using frontmatter/HTML-comment hints (falling back to owner), skipping
    existing non-backfill ledger entries and structural exclusions."""
    vroot = _vault_root(vault)
    now_ts = time.time() if now is None else float(now)
    cutoff = now_ts - days * 86400
    existing = _load_ledger()
    names = _persona_names()
    persona_lookup: dict[str, str] = {}
    for pid, display in names.items():
        persona_lookup[pid.lower()] = pid
        persona_lookup[display.lower()] = pid

    snap = snapshot_vault(vroot)
    counts = {"owner": 0, "agent": 0, "engineer": 0, "skipped": 0}
    for rel, mtime_ns in sorted(snap.items()):
        if mtime_ns / 1e9 < cutoff:
            continue
        prev = existing.get(rel)
        if prev and prev["source"] != "backfill" and mtime_ns <= prev["mtime_ns"] + MTIME_TOLERANCE_NS:
            counts["skipped"] += 1
            continue
        hint = _parse_author_hint(vroot / rel)
        kind, actor = _classify_hint(hint, persona_lookup)
        if record_write(rel, kind=kind, actor=actor, source="backfill",
                        vault=vroot, mtime_ns=mtime_ns, recorded_at=now_ts):
            counts[kind] = counts.get(kind, 0) + 1
    return counts
