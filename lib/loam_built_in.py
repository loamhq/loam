"""Market plugins that became part of Loam: the installed copy steps aside.

Since 0.2.9 (2026-11-18) Images ships inside Loam (``plugins/images/``),
off until it is turned on in Settings › Plugins, and is no longer listed in
the Market. An install that had it from the Market has a copy in Yours, and a
copy in Yours wins over a shipped one: left alone, it would go on running the
Market version forever and never take a Loam update.

So once, at the broker's first start on this code, :func:`take_over` removes
each such copy through the normal removal (``loam_plugins.remove``), which
keeps the id's switch because a shipped copy takes over. Nothing else moves,
because nothing else is the copy's: a plugin's settings (``_settings/<id>/``),
data (``_data/<id>/``), keys and key choices (its private folder and the
shared keys) are all kept by its id, not by its layer. What was on stays on.

A Yours copy is replaced only when it is exactly what the Market installed:
installed from one of the repositories it was published from, and not edited
since. A copy built here, edited, or installed from anywhere else is the
person's own and is left in force; the record says why.

Each id is settled once (``<state>/plugins/built-in.json``). A failure is
reported and tried again at the next start.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

# id -> where its Market copies came from, and the release it came in.
GRADUATED = {
    "images": {"repos": ("loamhq/plugins",), "since": "0.2.9"},
}
BY = "loam-update"


def record_path() -> Path:
    import loam_plugin_servers  # noqa: PLC0415
    return loam_plugin_servers.state_dir() / "built-in.json"


def _read_record() -> dict:
    try:
        data = json.loads(record_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_record(data: dict) -> None:
    path = record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".built-in-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def look(pid: str) -> dict:
    """What :func:`take_over` would do for one id, without doing it:
    ``{"how": "absent"|"replace"|"kept"|"waiting", ...}``."""
    import loam_plugins  # noqa: PLC0415
    rows = [r for r in loam_plugins._rows() if r["id"] == pid]
    shipped = next((r for r in rows if r["layer"] == "shipped" and not r["broken"]), None)
    yours = next((r for r in rows if r["layer"] == "yours"), None)
    if shipped is None:
        # This release does not ship it (or its copy is broken): nothing to
        # take over with, so the Yours copy stays and the id is not settled.
        return {"how": "waiting", "why": "no shipped copy in this release"}
    if yours is None:
        return {"how": "absent"}
    src = yours.get("source") or {}
    if src.get("kind") != "link":
        return {"how": "kept", "why": "built here"}
    if src.get("repo") not in GRADUATED[pid]["repos"]:
        return {"how": "kept", "why": f"installed from {src.get('repo')}"}
    if yours.get("edited"):
        return {"how": "kept", "why": "edited since it was installed"}
    return {"how": "replace", "was": {"version": yours.get("version"), "source": src}}


def take_over(remove=None) -> dict:
    """Settle every graduated id not yet settled. Answers
    ``{id: {"how": "replaced"|"absent"|"kept"|"failed", ...}}`` for the ids
    it looked at this time. ``remove`` is for tests."""
    import loam_plugins  # noqa: PLC0415
    remove = remove or loam_plugins.remove
    record = _read_record()
    done = record.setdefault("settled", {})
    out: dict[str, dict] = {}
    for pid in sorted(GRADUATED):
        if pid in done:
            continue
        try:
            entry = look(pid)
            if entry["how"] == "waiting":
                continue
            if entry["how"] == "replace":
                remove(pid, by=BY)
                entry["how"] = "replaced"
        except Exception as exc:  # noqa: BLE001 - reported, tried again next start
            entry = {"how": "failed", "error": str(exc)[:300]}
        entry["at"] = _now()
        out[pid] = entry
        if entry["how"] != "failed":
            done[pid] = entry
    if out:
        _write_record(record)
    return out
