"""Where files, sidecars and the file-tracking index are read and written.

``BrokerStore`` talks only to the session broker's Files routes
(``/v1/files/...``) and ``/v1/plugin-services``, so the broker's file guard,
revisions and provenance stamp always apply. Nothing here opens a vault path
directly. ``MemoryStore`` implements the same contract in memory for tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

from . import frontmatter as fm
from .sidecar import NoteError, revision

DATA_DEFAULT = "System/Loam/data/comments"
ID_RE = re.compile(r"^[df]-[a-z0-9]{10}$")


class Conflict(Exception):
    """A write lost a race: the file is not at the revision it was made against."""


class BaseStore:
    root = "vault"

    def __init__(self, data_dir: str = DATA_DEFAULT, private_dir: str | Path | None = None):
        self.data_dir = data_dir.strip("/")
        self.private_dir = Path(private_dir) if private_dir else None
        self._people = None
        self._people_at = 0.0
        self._idx_lock = threading.RLock()
        self._mem_index: dict | None = None
        self._bootstrapped = False
        self.on_move_callback = None

    def sidecar_path(self, fid: str) -> str:
        if not ID_RE.fullmatch(fid or ""):
            raise NoteError(400, "not a file id")
        return f"{self.data_dir}/{fid}.json"

    def read_sidecar(self, fid: str):
        try:
            got = self.read_doc(self.root, self.sidecar_path(fid))
        except NoteError as exc:
            if exc.status == 404:
                return None
            raise
        try:
            return json.loads(got["text"]), got["revision"]
        except ValueError:
            raise NoteError(422, f"the sidecar {self.sidecar_path(fid)} is not JSON; "
                                 "mend it by hand or move it aside") from None

    def write_sidecar(self, fid: str, sc: dict, rev: str | None) -> str:
        text = json.dumps(sc, ensure_ascii=False, indent=1) + "\n"
        path = self.sidecar_path(fid)
        if rev is None:
            out_rev = self.create_file(self.root, path, text)["revision"]
        else:
            out_rev = self.write_doc(self.root, path, text, rev)["revision"]
        fmeta = sc.get("file") or {}
        if fmeta.get("root") and fmeta.get("path"):
            self.remember_file(fid, fmeta["root"], fmeta["path"], fmeta)
        return out_rev

    # -- index: tracks files across rename, move, delete and restore -----------

    def _index_path(self) -> Path | None:
        return (self.private_dir / "index.json") if self.private_dir else None

    def _read_index(self) -> dict:
        p = self._index_path()
        if p is not None:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("seq", 0)
                    data.setdefault("files", {})
                    return data
            except (OSError, ValueError):
                pass
            return {"seq": 0, "files": {}}
        if self._mem_index is None:
            self._mem_index = {"seq": 0, "files": {}}
        return self._mem_index

    def _write_index(self, data: dict) -> None:
        p = self._index_path()
        if p is None:
            self._mem_index = data
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(p.parent, 0o700)
        except OSError:
            pass
        tmp = p.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.replace(tmp, p)

    def _bootstrap_index(self) -> None:
        """Rebuild the index from vault sidecars once if it is empty."""
        if self._bootstrapped:
            return
        self._bootstrapped = True
        with self._idx_lock:
            idx = self._read_index()
            if idx["files"]:
                return
            changed = False
            for name in self.list_sidecars():
                if not name.endswith(".json"):
                    continue
                fid = name[:-5]
                if not ID_RE.fullmatch(fid):
                    continue
                got = self.read_sidecar(fid)
                if not got:
                    continue
                sc = got[0]
                fmeta = sc.get("file") or {}
                if fmeta.get("root") and fmeta.get("path"):
                    idx["files"][fid] = {
                        "root": fmeta["root"], "path": fmeta["path"],
                        "dev": fmeta.get("dev"), "ino": fmeta.get("ino"),
                        "sha256": fmeta.get("sha256"), "size": fmeta.get("size"),
                        "deleted": False, "restore_id": None,
                    }
                    changed = True
            if changed:
                self._write_index(idx)

    def remember_file(self, fid: str, root: str, path: str, fp: dict | None = None) -> None:
        self._bootstrap_index()
        with self._idx_lock:
            idx = self._read_index()
            prev = idx["files"].get(fid) or {}
            entry = {
                "root": root, "path": path,
                "dev": fp.get("dev") if fp and fp.get("dev") is not None else prev.get("dev"),
                "ino": fp.get("ino") if fp and fp.get("ino") is not None else prev.get("ino"),
                "sha256": fp.get("sha256") if fp and fp.get("sha256") is not None else prev.get("sha256"),
                "size": fp.get("size") if fp and fp.get("size") is not None else prev.get("size"),
                "deleted": False, "restore_id": None,
            }
            if idx["files"].get(fid) != entry:
                idx["files"][fid] = entry
                self._write_index(idx)

    def sync_events(self) -> list[tuple[str, str, str]]:
        """Apply new vault move/delete/restore events to the index. Returns
        ``[(fid, old_path, new_path), ...]`` for moved entries."""
        self._bootstrap_index()
        moved_sidecars: list[tuple[str, str, str]] = []
        with self._idx_lock:
            idx = self._read_index()
            since = idx.get("seq", 0)
            res = self.get_events(since)
            events = res.get("events") or []
            latest = res.get("seq", since)
            if not events and latest == since:
                return moved_sidecars
            changed = latest != since
            idx["seq"] = latest
            for ev in events:
                op = ev.get("op")
                if ev.get("root") != "vault":
                    continue
                if op == "move":
                    old_p, new_p = ev.get("from"), ev.get("to")
                    is_folder = ev.get("kind") == "folder"
                    if not old_p or not new_p:
                        continue
                    if self.on_move_callback:
                        try:
                            self.on_move_callback("vault", old_p, new_p, is_folder)
                        except Exception:
                            pass
                    for fid, entry in idx["files"].items():
                        if entry.get("root") != "vault" or entry.get("deleted"):
                            continue
                        ep = entry.get("path") or ""
                        if not is_folder and ep == old_p:
                            entry["path"] = new_p
                            if ev.get("dev") is not None:
                                entry["dev"] = ev["dev"]
                            if ev.get("ino") is not None:
                                entry["ino"] = ev["ino"]
                            moved_sidecars.append((fid, old_p, new_p))
                            changed = True
                        elif is_folder and ep.startswith(old_p + "/"):
                            np = new_p + ep[len(old_p):]
                            entry["path"] = np
                            moved_sidecars.append((fid, ep, np))
                            changed = True
                elif op == "delete":
                    dp, rid = ev.get("path"), ev.get("restore_id")
                    for fid, entry in idx["files"].items():
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
                    for fid, entry in idx["files"].items():
                        if not entry.get("deleted"):
                            continue
                        same_rid = rid and entry.get("restore_id") == rid
                        same_ino = (rdev is not None and rino is not None
                                    and entry.get("dev") == rdev and entry.get("ino") == rino)
                        if same_rid or (same_ino and entry.get("path") == rp):
                            entry["deleted"] = False
                            entry["restore_id"] = None
                            if rp:
                                entry["path"] = rp
                            changed = True
            if changed:
                self._write_index(idx)
        for fid, _old_p, new_p in moved_sidecars:
            self._sync_sidecar_path(fid, "vault", new_p)
        return moved_sidecars

    def _sync_sidecar_path(self, fid: str, root: str, new_path: str) -> None:
        try:
            got = self.read_sidecar(fid)
            if not got:
                return
            sc, rev = got
            fmeta = sc.setdefault("file", {})
            if fmeta.get("root") == root and fmeta.get("path") == new_path:
                return
            fmeta["root"] = root
            fmeta["path"] = new_path
            self.write_sidecar(fid, sc, rev)
        except (NoteError, Conflict):
            pass

    def find_file_id(self, root: str, path: str, fp: dict | None = None) -> str | None:
        """Resolve the existing sidecar id for ``(root, path)`` across renames,
        moves (including external moves), and delete/restore. Returns None if
        this file has no notes yet (or is a copy of a commented file)."""
        self.sync_events()
        with self._idx_lock:
            idx = self._read_index()
            # 1. Active direct path match
            for fid, entry in idx["files"].items():
                if not entry.get("deleted") and entry.get("root") == root and entry.get("path") == path:
                    if fp:
                        self.remember_file(fid, root, path, fp)
                    return fid
        if fp is None:
            try:
                fp = self.fingerprint(root, path)
            except NoteError:
                return None
        # 2. Markdown frontmatter id (if sidecar exists and original path moved)
        did = fp.get("doc_id")
        if did and ID_RE.fullmatch(did):
            got = self.read_sidecar(did)
            if got:
                sc = got[0]
                where = sc.get("file") or {}
                if (where.get("root"), where.get("path")) == (root, path):
                    self.remember_file(did, root, path, fp)
                    return did
                if not self.exists_with_doc_id(where.get("root"), where.get("path"), did):
                    self.remember_file(did, root, path, fp)
                    return did
                return None  # copy of an existing commented Markdown doc
        # 3. Inode match (handles external `mv` / Obsidian rename / unlogged restore)
        dev, ino = fp.get("dev"), fp.get("ino")
        if dev is not None and ino is not None:
            with self._idx_lock:
                idx = self._read_index()
                candidates = [(fid, dict(e)) for fid, e in idx["files"].items()
                              if e.get("root") == root and e.get("dev") == dev and e.get("ino") == ino]
            if len(candidates) == 1:
                fid, entry = candidates[0]
                if entry.get("deleted") or not self.exists(entry["root"], entry["path"]):
                    self.remember_file(fid, root, path, fp)
                    return fid
        # 4. Content hash + size match (only when old path is gone and match is unique)
        sha, size = fp.get("sha256"), fp.get("size")
        if sha and isinstance(size, int) and size > 0:
            with self._idx_lock:
                idx = self._read_index()
                candidates = [(fid, dict(e)) for fid, e in idx["files"].items()
                              if not e.get("deleted") and e.get("root") == root
                              and e.get("sha256") == sha and e.get("size") == size]
            gone = [(fid, e) for fid, e in candidates if not self.exists(e["root"], e["path"])]
            if len(gone) == 1 and len(candidates) == 1:
                fid, _ = gone[0]
                self.remember_file(fid, root, path, fp)
                return fid
        return None

    def exists_with_doc_id(self, root: str | None, path: str | None, did: str) -> bool:
        if not root or not path:
            return False
        try:
            other = self.read_doc(root, path)
        except NoteError:
            return False
        return fm.doc_id(other["text"]) == did

    def exists(self, root: str | None, path: str | None) -> bool:
        if not root or not path:
            return False
        try:
            self.stat_file(root, path)
            return True
        except NoteError:
            return False

    # -- people ----------------------------------------------------------------

    def people(self) -> dict:
        if self._people is None or time.monotonic() - self._people_at > 60:
            self._people = self._load_people()
            self._people_at = time.monotonic()
        return self._people

    def owner(self) -> dict:
        for p in self.people().values():
            if p["kind"] == "owner":
                return p
        return {"id": "owner", "name": "Owner", "kind": "owner"}

    def agent(self, aid: str) -> dict | None:
        p = self.people().get(aid)
        return p if p and p["kind"] == "agent" else None

    def person_named(self, name: str | None) -> dict | None:
        if not name:
            return None
        low = name.strip().lower()
        for p in self.people().values():
            if p["name"].lower() == low or p["id"].lower() == low:
                return p
        return None

    # -- abstract methods ------------------------------------------------------

    def list_sidecars(self) -> list[str]:
        return []

    def get_events(self, since: int) -> dict:
        return {"events": [], "seq": since}

    def stat_file(self, root: str, path: str) -> dict:
        raise NotImplementedError

    def fingerprint(self, root: str, path: str) -> dict:
        raise NotImplementedError

    def read_doc(self, root: str, path: str) -> dict:
        raise NotImplementedError

    def write_doc(self, root: str, path: str, text: str, base: str, by: str | None = None) -> dict:
        raise NotImplementedError

    def create_file(self, root: str, path: str, text: str) -> dict:
        raise NotImplementedError

    def plugin_services(self, interface: str) -> list[dict]:
        return []

    def _load_people(self) -> dict:
        raise NotImplementedError


class BrokerStore(BaseStore):
    def __init__(self, url: str, token_file: str, data_dir: str = DATA_DEFAULT,
                 private_dir: str | Path | None = None, timeout: float = 20):
        super().__init__(data_dir, private_dir=private_dir)
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.timeout = timeout

    def _token(self) -> str:
        with open(self.token_file, encoding="utf-8") as f:
            return f.read().strip()

    def _call(self, method: str, path: str, body: dict | None = None, query: dict | None = None):
        url = self.url + path + ("?" + urlencode(query) if query else "")
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return res.status, json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read() or b"{}")
            except ValueError:
                payload = {}
            return exc.code, payload
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise NoteError(502, f"the session broker did not answer ({exc})") from None

    def list_sidecars(self) -> list[str]:
        status, body = self._call("GET", "/v1/files/list",
                                  query={"root": self.root, "path": self.data_dir, "limit": 1000})
        if status != 200:
            return []
        return [e["name"] for e in body.get("entries") or [] if e.get("kind") == "file"]

    def get_events(self, since: int) -> dict:
        status, body = self._call("GET", "/v1/files/events", query={"since": str(since)})
        if status != 200:
            return {"events": [], "seq": since}
        return body

    def stat_file(self, root: str, path: str) -> dict:
        status, body = self._call("GET", "/v1/files/stat", query={"root": root, "path": path})
        if status != 200:
            raise NoteError(status, body.get("error") or f"the broker answered {status}")
        if body.get("kind") != "file":
            raise NoteError(400, f"{root}/{path} is a folder, not a file")
        return body

    def fingerprint(self, root: str, path: str) -> dict:
        status, body = self._call("GET", "/v1/files/fingerprint", query={"root": root, "path": path})
        if status != 200:
            raise NoteError(status, body.get("error") or f"the broker answered {status}")
        return body

    def read_doc(self, root: str, path: str) -> dict:
        status, body = self._call("GET", "/v1/files/read", query={"root": root, "path": path})
        if status != 200:
            raise NoteError(status, body.get("error") or f"the broker answered {status}")
        return {"root": root, "path": path, "text": body["text"],
                "revision": body["revision"], "modified": body.get("modified"),
                "row": {k: body.get(k) for k in ("name", "capabilities", "authority", "link", "modified", "size", "media_type")}}

    def write_doc(self, root: str, path: str, text: str, base: str, by: str | None = None) -> dict:
        payload = {"root": root, "path": path, "text": text, "base_revision": base}
        if by:
            payload["by"] = by
        status, body = self._call("PUT", "/v1/files/text", payload)
        if status == 409:
            raise Conflict(body.get("current_revision"))
        if status != 200:
            raise NoteError(status, body.get("error") or f"the broker answered {status}")
        return {"text": body["text"], "revision": body["revision"], "modified": body.get("modified")}

    def create_file(self, root: str, path: str, text: str) -> dict:
        parts = path.split("/")
        for i in range(1, len(parts)):
            folder = "/".join(parts[:i])
            status, body = self._call("POST", "/v1/files/folder", {"root": root, "path": folder})
            if status not in (201, 409):
                raise NoteError(status, body.get("error") or f"could not make {folder}")
        status, body = self._call("POST", "/v1/files/file", {"root": root, "path": path, "text": text})
        if status == 409:
            raise Conflict(None)
        if status != 201:
            raise NoteError(status, body.get("error") or f"the broker answered {status}")
        return {"text": body["text"], "revision": body["revision"]}

    def plugin_services(self, interface: str) -> list[dict]:
        try:
            status, body = self._call("GET", "/v1/plugin-services", query={"interface": interface})
            return body.get("services") or [] if status == 200 else []
        except NoteError:
            return []

    def _load_people(self) -> dict:
        out = {}
        status, owner = self._call("GET", "/v1/owner")
        if status == 200 and owner.get("id"):
            out[owner["id"]] = {"id": owner["id"], "name": owner.get("name") or owner["id"], "kind": "owner"}
        status, agents = self._call("GET", "/v1/agents")
        if status == 200:
            for a in agents.get("agents", []):
                if a.get("id") and a["id"] not in out:
                    out[a["id"]] = {"id": a["id"], "name": a.get("name") or a["id"], "kind": "agent"}
        return out


class MemoryStore(BaseStore):
    """The broker's file contract in memory, for both Markdown and binary files,
    with simulated inodes, vault mutation events, delete/restore trash, and
    provenance stamping."""

    def __init__(self, files: dict | None = None, people: dict | None = None,
                 data_dir: str = DATA_DEFAULT, stamp: bool = True,
                 services: list[dict] | None = None):
        super().__init__(data_dir)
        self.files: dict[str, str | bytes] = dict(files or {})
        self._inodes: dict[str, int] = {}
        self._next_ino = 1000
        for k in self.files:
            self._inodes[k] = self._alloc_ino()
        self._events: list[dict] = []
        self._trash: dict[str, tuple[str, str | bytes, int]] = {}
        self._fixed_people = people or {
            "sam": {"id": "sam", "name": "Sam", "kind": "owner"},
            "ada": {"id": "ada", "name": "Ada", "kind": "agent"},
            "nova": {"id": "nova", "name": "Nova", "kind": "agent"}}
        self.stamp = stamp
        self.writes = []
        self._services = list(services or [])

    def _alloc_ino(self) -> int:
        self._next_ino += 1
        return self._next_ino

    def _load_people(self):
        return dict(self._fixed_people)

    def _key(self, root, path):
        return f"{root}:{path}"

    def _raw_bytes(self, val) -> bytes:
        return val if isinstance(val, bytes) else val.encode("utf-8", "surrogatepass")

    def list_sidecars(self) -> list[str]:
        prefix = f"{self.root}:{self.data_dir}/"
        return [k[len(prefix):] for k in self.files if k.startswith(prefix) and "/" not in k[len(prefix):]]

    def get_events(self, since: int) -> dict:
        latest = self._events[-1]["seq"] if self._events else 0
        return {"events": [e for e in self._events if e["seq"] > since], "seq": latest}

    def stat_file(self, root: str, path: str) -> dict:
        k = self._key(root, path)
        if k not in self.files:
            raise NoteError(404, f"{root}/{path} is not there")
        raw = self._raw_bytes(self.files[k])
        return {"root": root, "path": path, "name": path.rsplit("/", 1)[-1],
                "kind": "file", "size": len(raw), "capabilities": {"read": True, "download": True, "write": isinstance(self.files[k], str)}}

    def fingerprint(self, root: str, path: str) -> dict:
        k = self._key(root, path)
        if k not in self.files:
            raise NoteError(404, f"{root}/{path} is not there")
        val = self.files[k]
        raw = self._raw_bytes(val)
        ino = self._inodes.setdefault(k, self._alloc_ino())
        out = {"root": root, "path": path, "name": path.rsplit("/", 1)[-1],
               "kind": "file", "size": len(raw), "dev": 1, "ino": ino,
               "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()}
        if isinstance(val, str) and path.lower().endswith((".md", ".markdown")):
            did = fm.doc_id(val)
            if did:
                out["doc_id"] = did
        return out

    def read_doc(self, root, path):
        k = self._key(root, path)
        if k not in self.files:
            raise NoteError(404, f"{root}/{path} is not there")
        t = self.files[k]
        if isinstance(t, bytes):
            raise NoteError(415, f"{root}/{path} is not UTF-8 text")
        return {"root": root, "path": path, "text": t, "revision": revision(t)}

    def _stamped(self, root, path, text, by):
        if not self.stamp or root != "vault" or not path.endswith(".md"):
            return text
        who = self.people().get(by) if by else self.owner()
        who = who or self.owner()
        line = f'last_modified_by: "[[{who["name"]}]]"'
        m = fm.FRONTMATTER.match(text)
        if not m:
            return f"---\n{line}\n---\n" + ("\n" if text and not text.startswith("\n") else "") + text
        nl = m.group(1)
        lines = m.group(2).split(nl) if m.group(2) else []
        hit = [i for i, ln in enumerate(lines) if ln.startswith("last_modified_by")]
        if hit:
            lines[hit[0]] = line
        else:
            lines.append(line)
        return "---" + nl + nl.join(lines) + nl + "---" + text[m.end():]

    def write_doc(self, root, path, text, base, by=None):
        k = self._key(root, path)
        if k not in self.files:
            raise NoteError(404, f"{root}/{path} is not there")
        if revision(self.files[k]) != base:
            raise Conflict(revision(self.files[k]))
        text = self._stamped(root, path, text, by)
        self.files[k] = text
        self.writes.append((root, path, by))
        return {"text": text, "revision": revision(text)}

    def create_file(self, root, path, text):
        k = self._key(root, path)
        if k in self.files:
            raise Conflict(None)
        self.files[k] = text
        self._inodes[k] = self._alloc_ino()
        self.writes.append((root, path, None))
        return {"text": text, "revision": revision(text)}

    def plugin_services(self, interface: str) -> list[dict]:
        return [s for s in self._services if s.get("interface") == interface]

    # Test helpers
    def outside_edit(self, root, path, text):
        k = self._key(root, path)
        self.files[k] = text
        self._inodes.setdefault(k, self._alloc_ino())

    def rename(self, root, path, new_path, *, emit_event: bool = True):
        old_k, new_k = self._key(root, path), self._key(root, new_path)
        self.files[new_k] = self.files.pop(old_k)
        ino = self._inodes.pop(old_k, self._alloc_ino())
        self._inodes[new_k] = ino
        if emit_event and root == "vault":
            seq = (self._events[-1]["seq"] + 1) if self._events else 1
            self._events.append({"seq": seq, "op": "move", "root": "vault",
                                 "from": path, "to": new_path, "kind": "file", "dev": 1, "ino": ino})

    def delete(self, root, path) -> str:
        k = self._key(root, path)
        val = self.files.pop(k)
        ino = self._inodes.pop(k)
        rid = f"{len(self._trash) + 1:032x}"
        self._trash[rid] = (path, val, ino)
        if root == "vault":
            seq = (self._events[-1]["seq"] + 1) if self._events else 1
            self._events.append({"seq": seq, "op": "delete", "root": "vault",
                                 "path": path, "restore_id": rid, "dev": 1, "ino": ino})
        return rid

    def restore(self, root, restore_id: str) -> str:
        path, val, ino = self._trash.pop(restore_id)
        k = self._key(root, path)
        self.files[k] = val
        self._inodes[k] = ino
        if root == "vault":
            seq = (self._events[-1]["seq"] + 1) if self._events else 1
            self._events.append({"seq": seq, "op": "restore", "root": "vault",
                                 "path": path, "restore_id": restore_id, "dev": 1, "ino": ino})
        return path


def from_env() -> BrokerStore:
    private = os.environ.get("LOAM_PLUGIN_PRIVATE") or (
        str(Path(os.environ["LOAM_PLUGIN_DATA"]) / "private") if os.environ.get("LOAM_PLUGIN_DATA") else None)
    return BrokerStore(os.environ["LOAM_BROKER_URL"], os.environ["LOAM_BROKER_TOKEN_FILE"],
                       os.environ.get("LOAM_VAULT_DATA") or DATA_DEFAULT, private_dir=private)
