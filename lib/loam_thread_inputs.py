"""Persist ordered inputs per thread member before dispatch. The broker rewrites
each member's file atomically on its event loop. On startup, recover queued
inputs in order and inspect previously dispatched inputs against their journal
positions before deciding whether another turn is needed."""

from __future__ import annotations

import itertools
import json
import os
import re
import time
from pathlib import Path

import loam_threads as threads

SAFE = re.compile(r"[^A-Za-z0-9._-]")
_counter = itertools.count(1)


def root() -> Path:
    return threads.STATE / "inputs"


def _path(thread_id: str, persona: str) -> Path:
    return root() / SAFE.sub("_", thread_id) / f"{SAFE.sub('_', persona)}.json"


def _read(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return [i for i in items or [] if isinstance(i, dict) and i.get("id")]


def _write(path: Path, items: list[dict]) -> None:
    if not items:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        try:
            path.parent.rmdir()
        except OSError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"items": items}, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def new_id() -> str:
    return f"i{time.time_ns()}-{next(_counter)}"


def add(thread_id: str, persona: str, entry: dict) -> str:
    """Append one input to the member's list; its id."""
    entry = {**entry, "id": entry.get("id") or new_id()}
    entry.setdefault("at", threads.now_iso())
    path = _path(thread_id, persona)
    items = _read(path)
    items.append(entry)
    _write(path, items)
    return entry["id"]


def _change(thread_id: str, persona: str, ids, fn) -> None:
    ids = {i for i in ids if i}
    if not ids:
        return
    path = _path(thread_id, persona)
    items = _read(path)
    out = []
    for item in items:
        if item["id"] in ids:
            item = fn(item)
            if item is None:
                continue
        out.append(item)
    if out != items:
        _write(path, out)


def bind(thread_id: str, persona: str, input_id: str, dispatch: dict) -> None:
    """Write the exact dispatch before any provider IO. Never recreate a lost input."""
    path = _path(thread_id, persona)
    entries = items(thread_id, persona)
    for entry in entries:
        if entry["id"] == input_id:
            entry["dispatch"] = dispatch
            _write(path, entries)
            return
    raise OSError(f"missing input {input_id} for {thread_id}/{persona}")


def mark_sent(thread_id: str, persona: str, ids) -> None:
    """The session has been given these inputs."""
    now = threads.now_iso()
    _change(thread_id, persona, ids, lambda i: {**i, "sent": now})


def keep(thread_id: str, persona: str, ids) -> None:
    """These inputs stay to be given again: their turn was cut by a stop
    before it said anything."""
    def unsend(item):
        item = dict(item)
        item.pop("sent", None)
        item.pop("dispatch", None)
        return item
    _change(thread_id, persona, ids, unsend)


def remove(thread_id: str, persona: str, ids) -> None:
    """These inputs are dealt with."""
    _change(thread_id, persona, ids, lambda i: None)


def items(thread_id: str, persona: str) -> list[dict]:
    return _read(_path(thread_id, persona))


def drop_member(thread_id: str, persona: str) -> None:
    _write(_path(thread_id, persona), [])


def pending() -> list[tuple[str, str, list[dict]]]:
    """Every member's list on disk: (thread id, persona, items oldest first),
    the members in a stable order. The ids are read back from the files'
    own names; a thread or persona id holds only ``SAFE`` characters."""
    out = []
    base = root()
    if not base.is_dir():
        return out
    for folder in sorted(p for p in base.iterdir() if p.is_dir()):
        for path in sorted(folder.glob("*.json")):
            got = _read(path)
            if got:
                out.append((folder.name, path.stem, got))
    return out
