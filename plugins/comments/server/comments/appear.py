"""Choose the agent identity shown outside this install.

Per-file choices override the plugin default (`plugin.comments.appear`).
Mask local replies as they leave so a change also covers older notes while
preserving the true author in storage. Remote files retain the identity
they were sent with.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

CHOICES = ("names", "one")
DEFAULT = "names"
ALIAS_ID = "owner-agent"


def effective(choice: str | None, default: str | None) -> str:
    """The choice in force: the file's own, else the default, else names."""
    if choice in CHOICES:
        return choice
    return default if default in CHOICES else DEFAULT


def alias_name(owner_name: str) -> str:
    return f"{(owner_name or 'Owner').strip()}'s agent"


def alias(owner_name: str) -> dict:
    """The one identity every agent appears as."""
    return {"id": ALIAS_ID, "name": alias_name(owner_name), "kind": "agent"}


def mask(obj, agent_ids, one: dict):
    """``obj`` (an answer) with every agent of this Loam replaced by ``one``:
    an author or closer (a dict whose ``id`` is an agent's), an authorship
    span's ``author`` id, and a ``people`` map's entries. Nothing else is
    touched; the words people wrote are theirs."""
    ids = set(agent_ids)
    if not ids:
        return obj

    def walk(o):
        if isinstance(o, list):
            return [walk(x) for x in o]
        if not isinstance(o, dict):
            return o
        if o.get("id") in ids and ("name" in o or "kind" in o):
            return dict(one)
        out = {}
        for k, v in o.items():
            if k == "people" and isinstance(v, dict):
                out[k] = {}
                for pk, p in v.items():
                    if pk in ids:
                        out[k][one["id"]] = dict(one)
                    else:
                        out[k][pk] = walk(p)
            elif k in ("author", "by") and isinstance(v, str) and v in ids:
                out[k] = one["id"]
            else:
                out[k] = walk(v)
        return out
    return walk(obj)


class AppearStore:
    """Per-file appear overrides kept outside the vault in ``LOAM_PLUGIN_PRIVATE``."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.path = self.folder / "appear.json"
        self._lock = threading.RLock()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("files", {})
                return data
        except (OSError, ValueError):
            pass
        return {"files": {}}

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

    def get(self, *keys: str | None) -> str:
        with self._lock:
            files = self._read()["files"]
        for k in keys:
            if k and files.get(k) in CHOICES:
                return files[k]
        return "default"

    def set(self, key: str, value: str, *, clear_keys: tuple[str, ...] = ()) -> str:
        if value not in CHOICES + ("default",):
            raise ValueError("appear is names, one or default")
        with self._lock:
            data = self._read()
            for ck in clear_keys:
                if ck:
                    data["files"].pop(ck, None)
            if value == "default":
                data["files"].pop(key, None)
            else:
                data["files"][key] = value
            self._write(data)
        return value

    def move_path(self, root: str, old_path: str, new_path: str, *, folder: bool = False) -> None:
        with self._lock:
            data = self._read()
            files = data["files"]
            changed = False
            if not folder:
                old_k, new_k = f"path:{root}:{old_path}", f"path:{root}:{new_path}"
                if old_k in files:
                    files[new_k] = files.pop(old_k)
                    changed = True
            else:
                prefix = f"path:{root}:{old_path}/"
                for k in list(files):
                    if k.startswith(prefix):
                        rest = k[len(prefix):]
                        files[f"path:{root}:{new_path}/{rest}"] = files.pop(k)
                        changed = True
            if changed:
                self._write(data)
