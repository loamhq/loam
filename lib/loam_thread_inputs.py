"""Each thread member's input, on disk and in order (2026-11-19, batch B step 2).

What a member of a thread is owed -- the owner's messages, a job's result,
another agent's mention, an update's notice -- was an in-memory queue until
now: a broker restart dropped it, and even the owner's own message came back
as "didn't start: the broker restarted. Tag @Ada to ask again". Each input is
now written here before it is queued (``ThreadEngine.schedule``) and removed
once its turn has dealt with it; the broker reads the lists when it starts
and schedules what is still pending, once each and in order
(``ThreadEngine.resume_inputs``).

One file per (thread, member): ``<threads state>/inputs/<thread id>/<persona>.json``,
``{"items": [entry, ...]}`` oldest first. An entry is what a ``Turn`` needs
to be made again (``reason``, ``chain``, ``model``, ``attachments``,
``note``, ``briefed_at``) plus its own ``id``, the time it was queued
(``at``), and ``sent``: when the member's session was given it (its turn's
prompt went in, or it was steered into a turn in flight).

``sent`` is the once-only key's half that lives here. After an orderly stop
an input whose turn was cut before it said anything is kept with ``sent``
cleared, and is given again. After a crash an entry still marked ``sent``
was in front of the session when the broker died: a message's is given
again only if the member's ``through`` has not passed it
(``ThreadEngine.already_heard``); any other kind (a result, a notice) is
not given twice, and its drop is logged.

Only the broker writes these files, on its one event loop; a write is a
whole-file atomic replace.
"""

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
