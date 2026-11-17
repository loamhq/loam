"""Where docs and sidecars are read and written.

``BrokerStore`` is the real one: every read and write goes through the
session broker's Files interface (``/v1/files/...``), exactly as the page's
own editor does, so the broker's file guard, its revisions and its
provenance stamp apply to this plugin as to anything else. Nothing here
opens a path of the machine. ``MemoryStore`` is the same contract in a dict,
for the tests.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from .sidecar import NoteError, revision

DATA_DEFAULT = "99_SYSTEM/Loam/data/doc-comments"


class Conflict(Exception):
    """A write lost a race: the file is not at the revision it was made against."""


class BaseStore:
    root = "vault"           # where sidecars live

    def __init__(self, data_dir: str = DATA_DEFAULT):
        self.data_dir = data_dir.strip("/")
        self._people = None
        self._people_at = 0.0

    def sidecar_path(self, did: str) -> str:
        if not re.fullmatch(r"d-[a-z0-9]{10}", did or ""):
            raise NoteError(400, "not a doc id")
        return f"{self.data_dir}/{did}.json"

    def read_sidecar(self, did: str):
        try:
            got = self.read_doc(self.root, self.sidecar_path(did))
        except NoteError as exc:
            if exc.status == 404:
                return None
            raise
        try:
            return json.loads(got["text"]), got["revision"]
        except ValueError:
            raise NoteError(422, f"the sidecar {self.sidecar_path(did)} is not JSON; "
                                 "mend it by hand or move it aside") from None

    def write_sidecar(self, did: str, sc: dict, rev: str | None) -> str:
        text = json.dumps(sc, ensure_ascii=False, indent=1) + "\n"
        path = self.sidecar_path(did)
        if rev is None:
            return self.create_file(self.root, path, text)["revision"]
        return self.write_doc(self.root, path, text, rev)["revision"]

    # -- people --------------------------------------------------------------------

    def people(self) -> dict:
        """``{id: {"id", "name", "kind"}}`` for the owner and every persona."""
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


class BrokerStore(BaseStore):
    def __init__(self, url: str, token_file: str, data_dir: str = DATA_DEFAULT,
                 timeout: float = 20):
        super().__init__(data_dir)
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

    def read_doc(self, root: str, path: str) -> dict:
        status, body = self._call("GET", "/v1/files/read", query={"root": root, "path": path})
        if status != 200:
            raise NoteError(status, body.get("error") or f"the broker answered {status}")
        return {"root": root, "path": path, "text": body["text"],
                "revision": body["revision"], "modified": body.get("modified"),
                "row": {k: body.get(k) for k in ("name", "capabilities", "authority", "link", "modified", "size")}}

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
    """The broker's file contract in memory, provenance stamp included: a
    save of a Markdown file in the vault sets ``last_modified_by`` to the
    owner's link, or the named persona's."""

    def __init__(self, files: dict | None = None, people: dict | None = None,
                 data_dir: str = DATA_DEFAULT, stamp: bool = True):
        super().__init__(data_dir)
        self.files = dict(files or {})
        self._fixed_people = people or {
            "sam": {"id": "sam", "name": "Sam", "kind": "owner"},
            "ada": {"id": "ada", "name": "Ada", "kind": "agent"},
            "iris": {"id": "iris", "name": "Iris", "kind": "agent"}}
        self.stamp = stamp
        self.writes = []

    def _load_people(self):
        return dict(self._fixed_people)

    def _key(self, root, path):
        return f"{root}:{path}"

    def read_doc(self, root, path):
        k = self._key(root, path)
        if k not in self.files:
            raise NoteError(404, f"{root}/{path} is not there")
        t = self.files[k]
        return {"root": root, "path": path, "text": t, "revision": revision(t)}

    def _stamped(self, root, path, text, by):
        if not self.stamp or root != "vault" or not path.endswith(".md"):
            return text
        who = self.people().get(by) if by else self.owner()
        who = who or self.owner()
        line = f'last_modified_by: "[[{who["name"]}]]"'
        from .frontmatter import FRONTMATTER
        m = FRONTMATTER.match(text)
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
        self.writes.append((root, path, None))
        return {"text": text, "revision": revision(text)}

    # A test's stand-in for someone editing on disk (a persona, Obsidian).
    def outside_edit(self, root, path, text):
        self.files[self._key(root, path)] = text

    def rename(self, root, path, new_path):
        self.files[self._key(root, new_path)] = self.files.pop(self._key(root, path))


def from_env() -> BrokerStore:
    return BrokerStore(os.environ["LOAM_BROKER_URL"], os.environ["LOAM_BROKER_TOKEN_FILE"],
                       os.environ.get("LOAM_VAULT_DATA") or DATA_DEFAULT)
