#!/usr/bin/env python3
"""Migrate existing vaults for the Files redesign and clean up unused seeded folders.

Runs on update (from ``bin/library install``) and is safe to run idempotently by
hand (``python3 lib/loam_vault_cleanup.py [--dry-run | --apply]``):

1. Move thread boards out of ``<inbox>/boards/`` into ``<boards>/`` (with
   ``git mv`` when tracked) and update ``thread-files.json``.
2. Move ``loam-wake-failures.md`` out of ``<inbox>/`` or
   ``<inbox>/from-agents/`` into ``<system>/loam-wake-failures.md``.
3. Move unused seeded folders (``Inbox``, ``Daily``, ``Images``, plus any
   configured inbox/daily folder) to ``<vault>/.trash/loam-cleanup-<stamp>/``
   when they hold nothing of substance (empty, or only Markdown/text notes with
   frontmatter and at most a few words). Anything with real writing stays.
4. Migrate the retired ``daily-notes`` plugin and legacy inbox action into the
   unified ``quick-actions`` plugin, keeping active folders on and leaving
   cleaned-up installs off.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loam_config as lc  # noqa: E402

FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---[ \t]*(?:\r?\n|$)", re.S)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
OBSIDIAN_COMMENT_RE = re.compile(r"%%.*?%%", re.S)
WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

# A note with this many words or fewer (after stripping YAML frontmatter and
# comments) counts as empty / a throwaway test and does not keep a seeded
# folder alive.
INSUBSTANTIAL_MAX_WORDS = 5
TEXT_EXTS = {".md", ".markdown", ".txt"}
SEEDED_DEFAULTS = ("Inbox", "Daily", "Images")


def is_insubstantial_text(text: str) -> bool:
    """True when a note's body is empty or holds only a few words after
    stripping YAML frontmatter and comments."""
    body = FRONTMATTER_RE.sub("", text, count=1)
    body = HTML_COMMENT_RE.sub(" ", body)
    body = OBSIDIAN_COMMENT_RE.sub(" ", body)
    # Strip leading markdown heading markers and horizontal rules; keep words.
    lines = []
    for line in body.splitlines():
        s = line.strip()
        if not s or re.fullmatch(r"[-*_]{3,}", s):
            continue
        lines.append(re.sub(r"^#{1,6}\s+", "", s))
    words = WORD_RE.findall(" ".join(lines))
    return len(words) <= INSUBSTANTIAL_MAX_WORDS


def folder_has_substance(folder: Path) -> bool:
    """Return True if ``folder`` holds any file of substance (any non-text file
    or any Markdown/text note with more than a few words of body text)."""
    if not folder.is_dir():
        return False
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue
            path = Path(root) / name
            if path.is_symlink():
                return True
            if path.suffix.lower() not in TEXT_EXTS:
                return True
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return True
            if not is_insubstantial_text(text):
                return True
    return False


def _is_git_repo(vault: Path) -> bool:
    return (vault / ".git").exists()


def _git_tracked(vault: Path, path: Path) -> bool:
    if not _is_git_repo(vault):
        return False
    try:
        rel = str(path.resolve().relative_to(vault.resolve()))
    except (OSError, ValueError):
        return False
    res = subprocess.run(
        ["git", "-C", str(vault), "ls-files", "--error-unmatch", "--", rel],
        capture_output=True, text=True, check=False,
    )
    return res.returncode == 0


def _move_preserving_history(vault: Path, src: Path, dst: Path) -> str:
    """Move ``src`` to ``dst``, using ``git mv`` when tracked inside ``vault``."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if _git_tracked(vault, src):
        src_rel = str(src.resolve().relative_to(vault.resolve()))
        dst_rel = str(dst.resolve().relative_to(vault.resolve()))
        res = subprocess.run(
            ["git", "-C", str(vault), "mv", "--", src_rel, dst_rel],
            capture_output=True, text=True, check=False,
        )
        if res.returncode == 0:
            return "git-mv"
    shutil.move(str(src), str(dst))
    return "move"


def move_to_trash(vault: Path, target: Path, stamp: str | None = None) -> Path:
    """Move ``target`` recoverably into ``<vault>/.trash/loam-cleanup-<stamp>/``."""
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rel = target.resolve().relative_to(vault.resolve())
    trash_base = vault / ".trash" / f"loam-cleanup-{stamp}"
    dst = trash_base / rel
    if dst.exists():
        for i in range(2, 1000):
            cand = dst.with_name(f"{dst.name}-{i}")
            if not cand.exists():
                dst = cand
                break
    dst.parent.mkdir(parents=True, exist_ok=True)
    # If tracked files live inside target, untrack cleanly with git rm --cached
    # after moving, or move directly into .trash (which is gitignored).
    shutil.move(str(target), str(dst))
    return dst


def _remove_empty_dir_chain(folder: Path, stop_at: Path) -> None:
    """Remove empty directories upwards from ``folder`` without touching ``stop_at``."""
    cur = folder
    while cur != stop_at and cur.is_relative_to(stop_at):
        try:
            entries = [e for e in cur.iterdir() if e.name != ".DS_Store"]
        except OSError:
            break
        if entries:
            break
        for ds in cur.glob(".DS_Store"):
            try:
                ds.unlink()
            except OSError:
                pass
        try:
            cur.rmdir()
        except OSError:
            break
        cur = cur.parent


def migrate_boards(vault: Path, *, inbox_folder: str | None = None,
                   boards_folder: str | None = None,
                   bindings_path: Path | None = None,
                   dry_run: bool = False) -> list[dict]:
    """Move board files out of ``<inbox>/boards/`` into ``<boards>/`` and update
    ``thread-files.json`` bindings. Leaves bindings outside ``<inbox>/boards/``
    (such as a legacy general comms note) untouched."""
    import loam_thread_files as tf  # noqa: PLC0415

    inbox = (inbox_folder or lc.vault_folder("inbox")).strip("/")
    boards = (boards_folder or lc.vault_folder("boards")).strip("/")
    if not boards or boards == f"{inbox}/boards":
        boards = "Boards"
    old_prefix = f"{inbox}/boards/"
    if old_prefix == f"{boards}/":
        return []

    bpath = bindings_path or tf._binding_file()
    bindings: dict = {}
    if bpath.is_file():
        try:
            bindings = json.loads(bpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            bindings = {}

    moved: list[dict] = []
    updated_bindings = False
    group = bindings.get("boards") if isinstance(bindings.get("boards"), dict) else {}

    def _free_dest(name: str) -> str:
        cand = f"{boards}/{name}"
        if not (vault / cand).exists():
            return cand
        stem, dot, ext = name.rpartition(".")
        if not dot:
            stem, ext = name, ""
        for i in range(2, 1000):
            alt = f"{boards}/{stem} ({i}).{ext}" if ext else f"{boards}/{stem} ({i})"
            if not (vault / alt).exists():
                return alt
        return cand

    seen_src: set[str] = set()
    for tid, row in sorted(group.items()):
        if not isinstance(row, dict):
            continue
        rel = row.get("path")
        if not isinstance(rel, str) or not rel.startswith(old_prefix):
            continue
        name = rel[len(old_prefix):]
        if "/" in name or not name:
            continue
        src = vault / rel
        target_rel = f"{boards}/{name}"
        if (vault / target_rel).exists() and src.exists() and not (vault / target_rel).samefile(src):
            target_rel = _free_dest(name)
        dst = vault / target_rel
        method = "binding-only"
        if src.is_file():
            seen_src.add(rel)
            if not dry_run:
                method = _move_preserving_history(vault, src, dst)
            else:
                method = "would-move"
        if not dry_run:
            row["path"] = target_rel
            if isinstance(row.get("moving_to"), str) and row["moving_to"].startswith(old_prefix):
                row["moving_to"] = f"{boards}/{row['moving_to'][len(old_prefix):]}"
            updated_bindings = True
        moved.append({"from": rel, "to": target_rel, "thread": tid, "how": method})

    # Move any remaining unreferenced .md files in <inbox>/boards/ too.
    old_dir = vault / inbox / "boards"
    if old_dir.is_dir():
        for entry in sorted(old_dir.iterdir()):
            if entry.name.startswith(".") or not entry.is_file():
                continue
            rel = f"{old_prefix}{entry.name}"
            if rel in seen_src:
                continue
            target_rel = _free_dest(entry.name)
            dst = vault / target_rel
            if not dry_run:
                method = _move_preserving_history(vault, entry, dst)
            else:
                method = "would-move"
            moved.append({"from": rel, "to": target_rel, "thread": None, "how": method})
        if not dry_run:
            _remove_empty_dir_chain(old_dir, vault / inbox)

    if updated_bindings and not dry_run:
        bpath.parent.mkdir(parents=True, exist_ok=True)
        tmp = bpath.with_suffix(".tmp")
        tmp.write_text(json.dumps(bindings, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, bpath)

    return moved


def migrate_failure_log(vault: Path, *, inbox_folder: str | None = None,
                        system_folder: str | None = None,
                        dry_run: bool = False,
                        stamp: str | None = None) -> list[dict]:
    """Move ``loam-wake-failures.md`` out of Inbox into ``<system>/``."""
    inbox = (inbox_folder or lc.vault_folder("inbox")).strip("/")
    system = (system_folder or lc.vault_folder("system")).strip("/")
    dst_rel = f"{system}/loam-wake-failures.md"
    dst = vault / dst_rel
    candidates = [
        f"{inbox}/from-agents/loam-wake-failures.md",
        f"{inbox}/loam-wake-failures.md",
    ]
    moved: list[dict] = []
    for rel in candidates:
        src = vault / rel
        if not src.is_file() or rel == dst_rel:
            continue
        if dst.is_file():
            if not dry_run:
                existing = dst.read_text(encoding="utf-8", errors="replace")
                extra_lines = [
                    line for line in src.read_text(encoding="utf-8", errors="replace").splitlines()
                    if line.startswith("- `") and line not in existing
                ]
                if extra_lines:
                    with dst.open("a", encoding="utf-8") as handle:
                        if existing and not existing.endswith("\n"):
                            handle.write("\n")
                        handle.write("\n".join(extra_lines) + "\n")
                trash_dst = move_to_trash(vault, src, stamp=stamp)
                _remove_empty_dir_chain(src.parent, vault / inbox)
                moved.append({"from": rel, "to": dst_rel, "how": "merged-and-trashed",
                              "trash": str(trash_dst.relative_to(vault))})
            else:
                moved.append({"from": rel, "to": dst_rel, "how": "would-merge"})
        else:
            if not dry_run:
                how = _move_preserving_history(vault, src, dst)
                _remove_empty_dir_chain(src.parent, vault / inbox)
                moved.append({"from": rel, "to": dst_rel, "how": how})
            else:
                moved.append({"from": rel, "to": dst_rel, "how": "would-move"})
    return moved


def cleanup_seeded_folders(vault: Path, *, extra_folders: tuple[str, ...] = (),
                           dry_run: bool = False,
                           stamp: str | None = None) -> dict:
    """Move empty or insubstantial seeded folders (Inbox, Daily, Images) into
    vault ``.trash/``. Folders holding real writing are left untouched."""
    protected = {
        Path(lc.vault_folder(k)).parts[0]
        for k in ("conversations", "attachments", "system", "boards", "projects")
        if lc.vault_folder(k)
    }
    candidates: list[str] = []
    for name in (*SEEDED_DEFAULTS, *extra_folders):
        clean = (name or "").strip("/")
        if not clean or "/" in clean or clean in protected or clean in candidates:
            continue
        candidates.append(clean)

    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    removed: list[dict] = []
    kept: list[dict] = []

    for name in candidates:
        folder = vault / name
        if not folder.is_dir() or folder.is_symlink():
            continue
        files = sorted(
            str(p.relative_to(vault))
            for p in folder.rglob("*")
            if p.is_file() and not any(part.startswith(".") for part in p.relative_to(folder).parts)
        )
        if folder_has_substance(folder):
            kept.append({"folder": name, "reason": "has substance", "files": len(files)})
            continue
        if dry_run:
            removed.append({
                "folder": name,
                "files": files,
                "trash": f".trash/loam-cleanup-{stamp}/{name}",
                "dry_run": True,
            })
        else:
            trash_dst = move_to_trash(vault, folder, stamp=stamp)
            removed.append({
                "folder": name,
                "files": files,
                "trash": str(trash_dst.relative_to(vault)),
            })

    return {"removed": removed, "kept": kept}


def migrate_quick_actions(vault: Path, *, dry_run: bool = False) -> dict:
    """Carry active inbox / daily-notes settings into the unified quick-actions
    plugin. Runs after seeded-folder cleanup so empty cleaned-up folders are
    not recreated."""
    import loam_contributions as contrib  # noqa: PLC0415
    import loam_plugins as lp  # noqa: PLC0415

    data = lp.load_list()
    on = data.setdefault("on", {})
    if "quick-actions" in on:
        # Already migrated or explicitly configured by the owner.
        if "daily-notes" in on and not dry_run:
            with lp.locked():
                data = lp.load_list(strict=False)
                data.setdefault("on", {}).pop("daily-notes", None)
                lp._save_list(data)
        return {"status": "already-configured", "on": bool(on["quick-actions"])}

    had_daily = bool(on.get("daily-notes"))
    daily_vals = contrib.setting_values("daily-notes")
    daily_dir = (daily_vals.get("folder") or "Daily").strip("/")
    inbox_dir = lc.vault_folder("inbox").strip("/")

    inbox_alive = (vault / inbox_dir).is_dir()
    daily_alive = had_daily and (vault / daily_dir).is_dir()

    if inbox_alive and daily_alive:
        actions = "both"
        want_on = True
    elif inbox_alive:
        actions = "new-note"
        want_on = True
    elif daily_alive:
        actions = "daily-note"
        want_on = True
    else:
        actions = "both"
        want_on = False

    plan = {
        "status": "migrated",
        "on": want_on,
        "actions": actions,
        "inbox_folder": inbox_dir,
        "daily_folder": daily_dir,
    }
    if dry_run:
        return plan

    vals = contrib.setting_values("quick-actions")
    vals["actions"] = actions
    if inbox_dir:
        vals["inbox_folder"] = inbox_dir
    if daily_dir:
        vals["daily_folder"] = daily_dir
    spath = contrib.settings_path("quick-actions")
    spath.parent.mkdir(parents=True, exist_ok=True)
    tmp = spath.with_suffix(".tmp")
    tmp.write_text(json.dumps(vals, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, spath)

    with lp.locked():
        data = lp.load_list(strict=False)
        on = data.setdefault("on", {})
        on["quick-actions"] = want_on
        on.pop("daily-notes", None)
        lp._save_list(data)
    return plan


def run_update_migration(vault: Path | None = None, *, dry_run: bool = False) -> dict:
    """Run all Files-redesign vault migrations in order and return a report."""
    import loam_contributions as contrib  # noqa: PLC0415

    vroot = (vault or lc.VAULT).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    daily_vals = contrib.setting_values("daily-notes")
    extra = tuple(x for x in (
        lc.vault_folder("inbox"),
        daily_vals.get("folder"),
    ) if x)

    boards = migrate_boards(vroot, dry_run=dry_run)
    failure_log = migrate_failure_log(vroot, dry_run=dry_run, stamp=stamp)
    cleanup = cleanup_seeded_folders(vroot, extra_folders=extra, dry_run=dry_run, stamp=stamp)
    quick = migrate_quick_actions(vroot, dry_run=dry_run)

    report = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vault": str(vroot),
        "dry_run": dry_run,
        "boards_moved": boards,
        "failure_log_moved": failure_log,
        "folders_removed": cleanup["removed"],
        "folders_kept": cleanup["kept"],
        "quick_actions": quick,
    }

    if not dry_run:
        try:
            out_path = lc.STATE / "files" / "last-cleanup.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
        except OSError:
            pass
    return report


def format_report(report: dict) -> str:
    lines = []
    if report["boards_moved"]:
        lines.append(f"Moved {len(report['boards_moved'])} board(s) out of Inbox into Boards:")
        for row in report["boards_moved"][:10]:
            lines.append(f"  {row['from']} -> {row['to']} ({row['how']})")
        if len(report["boards_moved"]) > 10:
            lines.append(f"  ... and {len(report['boards_moved']) - 10} more")
    for row in report["failure_log_moved"]:
        lines.append(f"Moved alert log: {row['from']} -> {row['to']} ({row['how']})")
    for row in report["folders_removed"]:
        detail = f" ({len(row['files'])} empty/test file(s))" if row["files"] else " (empty)"
        lines.append(f"Removed unused seeded folder: {row['folder']}/{detail} -> {row['trash']}")
    qa = report.get("quick_actions") or {}
    if qa.get("status") == "migrated":
        lines.append(f"Quick actions: {'on' if qa.get('on') else 'off'} (actions={qa.get('actions')})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    dry_run = "--dry-run" in args
    as_json = "--json" in args
    report = run_update_migration(dry_run=dry_run)
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        text = format_report(report)
        print(text or "Nothing to migrate or clean up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
