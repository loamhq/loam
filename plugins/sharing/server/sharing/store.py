"""Read-only broker store for Sharing (and in-memory equivalent for tests).

Sharing never modifies files in the vault. Every file read, download, stat,
fingerprint, locate and vault event query goes through Loam's session broker so
the broker's path-containment and symlink rules always apply.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlencode


class ShareError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip_frontmatter_id(text: str) -> str | None:
    m = re.match(r"\A---(\r?\n)(.*?)\1---(?:\r?\n|\Z)", text or "", re.S)
    if not m:
        return None
    for line in m.group(2).splitlines():
        mm = re.match(r"^\s*loam_doc_id\s*:\s*[\"']?([df]-[a-z0-9]{10})[\"']?\s*$", line)
        if mm:
            return mm.group(1)
    return None


class BaseStore:
    def __init__(self):
        self._people = None
        self._people_at = 0.0

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

    def _load_people(self) -> dict:
        raise NotImplementedError

    def stat_file(self, root: str, path: str) -> dict:
        raise NotImplementedError

    def read_text(self, root: str, path: str) -> dict:
        raise NotImplementedError

    def download_bytes(self, root: str, path: str, *, range_header: str | None = None) -> tuple[int, bytes, dict]:
        """Return ``(status, body, headers_lower)`` where status is 200 or 206."""
        raise NotImplementedError

    def fingerprint(self, root: str, path: str) -> dict:
        raise NotImplementedError

    def locate(self, root: str, *, dev=None, ino=None, sha256=None, size=None, doc_id=None) -> dict | None:
        raise NotImplementedError

    def get_events(self, since: int) -> dict:
        return {"events": [], "seq": since}

    def plugin_services(self, interface: str) -> list[dict]:
        return []


class BrokerStore(BaseStore):
    def __init__(self, url: str, token_file: str, timeout: float = 25):
        super().__init__()
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.timeout = timeout

    def _token(self) -> str:
        with open(self.token_file, encoding="utf-8") as f:
            return f.read().strip()

    def _call(self, method: str, path: str, query: dict | None = None):
        url = self.url + path + ("?" + urlencode(query) if query else "")
        req = urllib.request.Request(
            url, method=method,
            headers={"Authorization": f"Bearer {self._token()}", "Accept": "application/json"},
        )
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
            raise ShareError(502, f"the session broker did not answer ({exc})") from None

    def stat_file(self, root: str, path: str) -> dict:
        status, body = self._call("GET", "/v1/files/stat", {"root": root, "path": path})
        if status != 200:
            raise ShareError(status, body.get("error") or f"the broker answered {status}")
        return body

    def read_text(self, root: str, path: str) -> dict:
        status, body = self._call("GET", "/v1/files/read", {"root": root, "path": path})
        if status != 200:
            raise ShareError(status, body.get("error") or f"the broker answered {status}")
        return body

    def download_bytes(self, root: str, path: str, *, range_header: str | None = None) -> tuple[int, bytes, dict]:
        url = self.url + "/v1/files/download?" + urlencode({"root": root, "path": path})
        hdrs = {"Authorization": f"Bearer {self._token()}"}
        if range_header:
            hdrs["Range"] = range_header
        req = urllib.request.Request(url, method="GET", headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                raw = res.read()
                rh = {k.lower(): v for k, v in res.headers.items()}
                return res.status, raw, rh
        except urllib.error.HTTPError as exc:
            if exc.code == 416:
                rh = {k.lower(): v for k, v in exc.headers.items()}
                return 416, b"", rh
            try:
                msg = json.loads(exc.read() or b"{}").get("error")
            except ValueError:
                msg = None
            raise ShareError(exc.code, msg or f"the broker answered {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ShareError(502, f"the session broker did not answer ({exc})") from None

    def fingerprint(self, root: str, path: str) -> dict:
        status, body = self._call("GET", "/v1/files/fingerprint", {"root": root, "path": path})
        if status != 200:
            raise ShareError(status, body.get("error") or f"the broker answered {status}")
        return body

    def locate(self, root: str, *, dev=None, ino=None, sha256=None, size=None, doc_id=None) -> dict | None:
        q = {"root": root}
        if dev is not None:
            q["dev"] = str(dev)
        if ino is not None:
            q["ino"] = str(ino)
        if sha256:
            q["sha256"] = str(sha256)
        if size is not None:
            q["size"] = str(size)
        if doc_id:
            q["doc_id"] = str(doc_id)
        status, body = self._call("GET", "/v1/files/locate", q)
        if status == 200 and body.get("found"):
            res = body.get("resource")
            return res if isinstance(res, dict) else body
        return None

    def get_events(self, since: int) -> dict:
        status, body = self._call("GET", "/v1/files/events", {"since": str(since)})
        return body if status == 200 else {"events": [], "seq": since}

    def plugin_services(self, interface: str) -> list[dict]:
        try:
            status, body = self._call("GET", "/v1/plugin-services", {"interface": interface})
            return body.get("services") or [] if status == 200 else []
        except ShareError:
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
    """In-memory broker store supporting text & binary files, Range requests,
    simulated inodes, vault mutation events, delete/restore trash, and plugin services."""

    def __init__(self, files: dict | None = None, people: dict | None = None,
                 services: list[dict] | None = None):
        super().__init__()
        self.files: dict[str, str | bytes] = dict(files or {})
        self.folders: set[str] = set()
        self._inodes: dict[str, int] = {}
        self._next_ino = 2000
        for k in self.files:
            self._inodes[k] = self._alloc_ino()
        self._events: list[dict] = []
        self._trash: dict[str, tuple[str, str | bytes, int]] = {}
        self._fixed_people = people or {
            "sam": {"id": "sam", "name": "Sam", "kind": "owner"},
            "ada": {"id": "ada", "name": "Ada", "kind": "agent"},
            "nova": {"id": "nova", "name": "Nova", "kind": "agent"},
        }
        self._services = list(services or [])

    def _alloc_ino(self) -> int:
        self._next_ino += 1
        return self._next_ino

    def _key(self, root: str, path: str) -> str:
        return f"{root}:{path}"

    def _raw(self, val: str | bytes) -> bytes:
        return val if isinstance(val, bytes) else val.encode("utf-8", "surrogatepass")

    def _load_people(self) -> dict:
        return dict(self._fixed_people)

    def stat_file(self, root: str, path: str) -> dict:
        k = self._key(root, path)
        if k in self.folders or any(x.startswith(f"{root}:{path}/") for x in self.files):
            return {"root": root, "path": path, "name": path.rsplit("/", 1)[-1], "kind": "folder"}
        if k not in self.files:
            raise ShareError(404, f"{root}/{path} is not there")
        raw = self._raw(self.files[k])
        return {
            "root": root, "path": path, "name": path.rsplit("/", 1)[-1],
            "kind": "file", "size": len(raw), "modified": now_iso(),
            "capabilities": {"read": True, "download": True, "write": isinstance(self.files[k], str)},
        }

    def read_text(self, root: str, path: str) -> dict:
        k = self._key(root, path)
        if k not in self.files:
            raise ShareError(404, f"{root}/{path} is not there")
        val = self.files[k]
        if isinstance(val, bytes):
            try:
                text = val.decode("utf-8")
            except UnicodeDecodeError:
                raise ShareError(415, f"{root}/{path} is not UTF-8 text") from None
        else:
            text = val
        rev = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        return {"root": root, "path": path, "name": path.rsplit("/", 1)[-1],
                "kind": "file", "text": text, "revision": rev, "size": len(text.encode("utf-8"))}

    def download_bytes(self, root: str, path: str, *, range_header: str | None = None) -> tuple[int, bytes, dict]:
        k = self._key(root, path)
        if k not in self.files:
            raise ShareError(404, f"{root}/{path} is not there")
        raw = self._raw(self.files[k])
        total = len(raw)
        if range_header and range_header.startswith("bytes="):
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if m and (m.group(1) or m.group(2)):
                s = int(m.group(1)) if m.group(1) else max(0, total - int(m.group(2)))
                e = int(m.group(2)) if m.group(2) else total - 1
                if s >= total or s > e:
                    return 416, b"", {"content-range": f"bytes */{total}", "accept-ranges": "bytes"}
                e = min(e, total - 1)
                return 206, raw[s:e + 1], {
                    "content-range": f"bytes {s}-{e}/{total}",
                    "accept-ranges": "bytes",
                }
        return 200, raw, {"accept-ranges": "bytes"}

    def fingerprint(self, root: str, path: str) -> dict:
        k = self._key(root, path)
        if k in self.folders or any(x.startswith(f"{root}:{path}/") for x in self.files):
            raise ShareError(400, f"{root}/{path} is a folder, not a file")
        if k not in self.files:
            raise ShareError(404, f"{root}/{path} is not there")
        val = self.files[k]
        raw = self._raw(val)
        ino = self._inodes.setdefault(k, self._alloc_ino())
        out = {
            "root": root, "path": path, "name": path.rsplit("/", 1)[-1],
            "kind": "file", "size": len(raw), "dev": 1, "ino": ino,
            "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        }
        if isinstance(val, str) and path.lower().endswith((".md", ".markdown")):
            did = _strip_frontmatter_id(val)
            if did:
                out["doc_id"] = did
        return out

    def locate(self, root: str, *, dev=None, ino=None, sha256=None, size=None, doc_id=None) -> dict | None:
        by_ino, by_doc, by_sha = [], [], []
        prefix = f"{root}:"
        for k, val in self.files.items():
            if not k.startswith(prefix):
                continue
            p = k[len(prefix):]
            raw = self._raw(val)
            if dev is not None and ino is not None and self._inodes.get(k) == ino:
                by_ino.append(p)
            if doc_id and isinstance(val, str) and p.lower().endswith((".md", ".markdown")):
                if _strip_frontmatter_id(val) == doc_id:
                    by_doc.append(p)
            if sha256 and isinstance(size, int) and len(raw) == size:
                if "sha256:" + hashlib.sha256(raw).hexdigest() == sha256:
                    by_sha.append(p)
        for hits in (by_ino, by_doc, by_sha):
            if len(hits) == 1:
                return {"found": True, **self.fingerprint(root, hits[0])}
        return None

    def get_events(self, since: int) -> dict:
        latest = self._events[-1]["seq"] if self._events else 0
        return {"events": [e for e in self._events if e["seq"] > since], "seq": latest}

    def plugin_services(self, interface: str) -> list[dict]:
        return [s for s in self._services if s.get("interface") == interface]

    # Test helpers
    def rename(self, root: str, path: str, new_path: str, *, emit_event: bool = True):
        old_k, new_k = self._key(root, path), self._key(root, new_path)
        self.files[new_k] = self.files.pop(old_k)
        ino = self._inodes.pop(old_k, self._alloc_ino())
        self._inodes[new_k] = ino
        if emit_event and root == "vault":
            seq = (self._events[-1]["seq"] + 1) if self._events else 1
            self._events.append({"seq": seq, "op": "move", "root": "vault",
                                 "from": path, "to": new_path, "kind": "file", "dev": 1, "ino": ino})

    def rename_folder(self, root: str, old_dir: str, new_dir: str, *, emit_event: bool = True):
        prefix = f"{root}:{old_dir}/"
        for k in list(self.files):
            if k.startswith(prefix):
                rest = k[len(prefix):]
                nk = f"{root}:{new_dir}/{rest}"
                self.files[nk] = self.files.pop(k)
                self._inodes[nk] = self._inodes.pop(k, self._alloc_ino())
        if emit_event and root == "vault":
            seq = (self._events[-1]["seq"] + 1) if self._events else 1
            self._events.append({"seq": seq, "op": "move", "root": "vault",
                                 "from": old_dir, "to": new_dir, "kind": "folder"})

    def delete(self, root: str, path: str) -> str:
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

    def restore(self, root: str, restore_id: str) -> str:
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
    return BrokerStore(os.environ["LOAM_BROKER_URL"], os.environ["LOAM_BROKER_TOKEN_FILE"])
