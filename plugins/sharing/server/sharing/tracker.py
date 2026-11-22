"""Track shared vault files across rename, move, delete, and restore.

Sharing never modifies vault files. Each shared file receives a private stable
``fid`` (``s-<10-chars>``) stored in ``LOAM_PLUGIN_PRIVATE/files.json`` along
with its path, inode (``dev``, ``ino``), content digest (``sha256``, ``size``),
optional Markdown ``doc_id``, and ``deleted`` / ``restore_id`` state.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path

from .store import ShareError

ALPHA = "abcdefghijklmnopqrstuvwxyz0123456789"


def _new_fid() -> str:
    return "s-" + "".join(secrets.choice(ALPHA) for _ in range(10))


class FileTracker:
    def __init__(self, store, folder: str | Path):
        self.store = store
        self.folder = Path(folder)
        self.path = self.folder / "files.json"
        self._lock = threading.RLock()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("seq", 0)
                data.setdefault("files", {})
                return data
        except (OSError, ValueError):
            pass
        return {"seq": 0, "files": {}}

    def _write(self, data: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.folder, 0o700)
        except OSError:
            pass
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def sync_events(self) -> None:
        with self._lock:
            data = self._read()
            since = data.get("seq", 0)
            res = self.store.get_events(since)
            events = res.get("events") or []
            latest = res.get("seq", since)
            if not events and latest == since:
                return
            changed = latest != since
            data["seq"] = latest
            for ev in events:
                if ev.get("root") != "vault":
                    continue
                op = ev.get("op")
                if op == "move":
                    old_p, new_p = ev.get("from"), ev.get("to")
                    is_folder = ev.get("kind") == "folder"
                    if not old_p or not new_p:
                        continue
                    for entry in data["files"].values():
                        if entry.get("root") != "vault" or entry.get("deleted"):
                            continue
                        ep = entry.get("path") or ""
                        if not is_folder and ep == old_p:
                            entry["path"] = new_p
                            if ev.get("dev") is not None:
                                entry["dev"] = ev["dev"]
                            if ev.get("ino") is not None:
                                entry["ino"] = ev["ino"]
                            changed = True
                        elif is_folder and ep.startswith(old_p + "/"):
                            entry["path"] = new_p + ep[len(old_p):]
                            changed = True
                elif op == "delete":
                    dp, rid = ev.get("path"), ev.get("restore_id")
                    for entry in data["files"].values():
                        if entry.get("root") == "vault" and entry.get("path") == dp and not entry.get("deleted"):
                            entry["deleted"] = True
                            entry["restore_id"] = rid
                            if ev.get("dev") is not None:
                                entry["dev"] = ev["dev"]
                            if ev.get("ino") is not None:
                                entry["ino"] = ev["ino"]
                            changed = True
                elif op == "restore":
                    rp, rid = ev.get("path"), ev.get("restore_id")
                    rdev, rino = ev.get("dev"), ev.get("ino")
                    for entry in data["files"].values():
                        if not entry.get("deleted"):
                            continue
                        same_rid = rid and entry.get("restore_id") == rid
                        same_ino = (
                            rdev is not None and rino is not None
                            and entry.get("dev") == rdev and entry.get("ino") == rino
                        )
                        if same_rid or (same_ino and entry.get("path") == rp):
                            entry["deleted"] = False
                            entry["restore_id"] = None
                            if rp:
                                entry["path"] = rp
                            changed = True
            if changed:
                self._write(data)

    def find_by_path(self, root: str, path: str) -> str | None:
        self.sync_events()
        with self._lock:
            data = self._read()
            for fid, entry in data["files"].items():
                if not entry.get("deleted") and entry.get("root") == root and entry.get("path") == path:
                    return fid
        # Handle external disk move (e.g. `mv` without broker event): match by inode / doc_id / sha256
        try:
            fp = self.store.fingerprint(root, path)
        except ShareError:
            return None
        with self._lock:
            data = self._read()
            for fid, entry in data["files"].items():
                if entry.get("deleted") or entry.get("root") != root:
                    continue
                same_ino = (
                    fp.get("dev") is not None and fp.get("ino") is not None
                    and entry.get("dev") == fp["dev"] and entry.get("ino") == fp["ino"]
                )
                same_doc = fp.get("doc_id") and entry.get("doc_id") == fp["doc_id"]
                same_sha = fp.get("sha256") and fp.get("size") and entry.get("sha256") == fp["sha256"] and entry.get("size") == fp["size"]
                if same_ino or same_doc or same_sha:
                    # Only treat as a move if the old path no longer exists
                    if not self._exists(entry["root"], entry["path"]):
                        entry.update(
                            path=path,
                            dev=fp.get("dev"), ino=fp.get("ino"),
                            sha256=fp.get("sha256"), size=fp.get("size"),
                            doc_id=fp.get("doc_id") or entry.get("doc_id"),
                        )
                        self._write(data)
                        return fid
        return None

    def ensure(self, root: str, path: str) -> str:
        """Return existing or newly created ``fid`` for a vault file (refuses folders)."""
        if root != "vault":
            raise ShareError(400, "only vault files can be shared")
        fp = self.store.fingerprint(root, path)
        if fp.get("kind") != "file":
            raise ShareError(400, "folders cannot be shared; choose a file instead")
        existing = self.find_by_path(root, path)
        if existing:
            self._update_fp(existing, root, path, fp)
            return existing
        fid = _new_fid()
        with self._lock:
            data = self._read()
            data["files"][fid] = {
                "root": root, "path": path,
                "dev": fp.get("dev"), "ino": fp.get("ino"),
                "sha256": fp.get("sha256"), "size": fp.get("size"),
                "doc_id": fp.get("doc_id"),
                "deleted": False, "restore_id": None,
            }
            self._write(data)
        return fid

    def _update_fp(self, fid: str, root: str, path: str, fp: dict) -> None:
        with self._lock:
            data = self._read()
            entry = data["files"].get(fid)
            if not entry:
                return
            changed = False
            for k in ("dev", "ino", "sha256", "size"):
                if fp.get(k) is not None and entry.get(k) != fp[k]:
                    entry[k] = fp[k]
                    changed = True
            if fp.get("doc_id") and entry.get("doc_id") != fp["doc_id"]:
                entry["doc_id"] = fp["doc_id"]
                changed = True
            if entry.get("root") != root or entry.get("path") != path:
                entry["root"] = root
                entry["path"] = path
                changed = True
            if changed:
                self._write(data)

    def _exists(self, root: str, path: str) -> bool:
        try:
            row = self.store.stat_file(root, path)
            return row.get("kind") == "file"
        except ShareError:
            return False

    def entry(self, fid: str) -> dict | None:
        with self._lock:
            e = self._read()["files"].get(fid)
            return dict(e) if e else None

    def resolve(self, fid: str) -> tuple[str, str] | None:
        """Resolve ``fid`` to ``(root, path)`` if the file is live and not deleted.
        Returns ``None`` immediately if deleted or missing (so share links 404)."""
        self.sync_events()
        with self._lock:
            data = self._read()
            entry = data["files"].get(fid)
            if not entry or entry.get("deleted"):
                return None
            root, path = entry.get("root"), entry.get("path")
            snap = dict(entry)
        if root != "vault" or not path:
            return None
        # Fast path: file is at its recorded path
        try:
            fp = self.store.fingerprint(root, path)
            if fp.get("kind") == "file":
                self._update_fp(fid, root, path, fp)
                return root, path
        except ShareError:
            pass
        # External move fallback: ask broker to locate by inode -> doc_id -> sha256+size
        found = self.store.locate(
            root,
            dev=snap.get("dev"), ino=snap.get("ino"),
            sha256=snap.get("sha256"), size=snap.get("size"),
            doc_id=snap.get("doc_id"),
        )
        if found and found.get("path"):
            new_path = found["path"]
            self._update_fp(fid, root, new_path, found)
            return root, new_path
        return None
