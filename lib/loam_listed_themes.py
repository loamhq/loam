"""The first-party looks that left the shipped set, kept for whoever chose one.

Since 0.2.5 (2026-11-18) Loam ships two themes, Latte and Plain. The five
other first-party looks (Autumn, Deep Forest, Foundry, Mist Trail, Yosemite)
are plugins from the Market, published from ``plugins/_listed/<id>/`` in this
repository to ``loamhq/plugins`` at the tag ``<id>-v<version>``.

An install that had chosen one of them must not lose it on the update. So
once, at the broker's first start on this code, :func:`keep` installs each
moved look that the person was using or had chosen:

- **using**: a device kind's ``appearance.<kind>.theme`` names it;
- **had chosen**: a device kind holds saved option choices for it
  (``appearance.<kind>.options.<id>``), which only a look someone has worn
  and changed has.

Each is installed from its Market link through the normal two calls
(``loam_plugins.install``: read, then confirm what was read), so it is an
installed plugin with its link, exactly as if the person had pressed Get. If
GitHub cannot be reached, the release's own copy in ``plugins/_listed/`` is
put in the plugins folder instead, a plugin built here, which Appearance
wears the same way; nothing is lost, and the Market can update it later.

A look already in force in either layer is left alone, and each id is tried
once (``<state>/plugins/kept-themes.json``): a look the person removes later
stays removed.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import loam_config as lc

# id -> version at its listing's tag. The link is LINK.format(id=, version=).
MOVED = {
    "autumn": "1.0.1",
    "deep-forest": "1.0.1",
    "foundry": "1.0.1",
    "mist-trail": "1.0.1",
    "yosemite": "1.0.1",
}
LINK = "https://github.com/loamhq/plugins/tree/{id}-v{version}/{id}"
BY = "loam-update"


def link(pid: str) -> str:
    return LINK.format(id=pid, version=MOVED[pid])


def record_path() -> Path:
    import loam_plugin_servers  # noqa: PLC0415
    return loam_plugin_servers.state_dir() / "kept-themes.json"


def _read_record() -> dict:
    try:
        data = json.loads(record_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_record(data: dict) -> None:
    path = record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".kept-themes-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def wanted() -> dict[str, str]:
    """The moved looks this install was using or had chosen: id -> why."""
    import loam_appearance  # noqa: PLC0415
    try:
        stored = loam_appearance._load_all().get("appearance")
    except loam_appearance.AppearanceError:
        return {}
    if not isinstance(stored, dict):
        return {}
    out: dict[str, str] = {}
    for kind in loam_appearance.KINDS:
        row = stored.get(kind)
        if not isinstance(row, dict):
            continue
        if row.get("theme") in MOVED:
            out[row["theme"]] = f"in use on {kind}"
        opts = row.get("options")
        if isinstance(opts, dict):
            for pid, chosen in opts.items():
                if pid in MOVED and isinstance(chosen, dict) and chosen:
                    out.setdefault(pid, f"options chosen on {kind}")
    return out


def _in_force(pid: str) -> bool:
    import loam_plugins  # noqa: PLC0415
    return any(r["id"] == pid and r.get("effective") and not r.get("broken")
               for r in loam_plugins._rows())


def _from_release(pid: str) -> None:
    """The release's own copy, as a plugin built here."""
    import loam_plugins  # noqa: PLC0415
    source = lc.ROOT / "plugins" / "_listed" / pid
    if not (source / "PLUGIN.md").is_file():
        raise FileNotFoundError(f"{source} is not in this release")
    target = loam_plugins.yours_root() / pid
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"kept-{pid}-", dir=target.parent))
    try:
        shutil.copytree(source, staging / pid)
        os.replace(staging / pid, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def keep(install=None, from_release=None) -> dict:
    """Install every wanted moved look that is not in force, once each.
    Answers ``{id: {"how": "link"|"release"|"present"|"failed", ...}}`` for
    the ids it looked at this time. ``install`` and ``from_release`` are for
    tests."""
    import loam_plugins  # noqa: PLC0415
    install = install or loam_plugins.install
    from_release = from_release or _from_release
    record = _read_record()
    done = record.setdefault("kept", {})
    out: dict[str, dict] = {}
    for pid, why in sorted(wanted().items()):
        if pid in done:
            continue
        entry = {"why": why, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if _in_force(pid):
            entry["how"] = "present"
        else:
            try:
                first = install({"url": link(pid)}, by=BY)
                if first.get("id") != pid:
                    raise loam_plugins.PluginError(
                        f"{link(pid)} is {first.get('id')!r}, not {pid}")
                install({"url": link(pid), "confirm": first["confirm"]}, by=BY)
                entry.update(how="link", link=link(pid))
            except Exception as exc:  # noqa: BLE001 - the release's copy is the fallback
                entry["link_error"] = str(exc)[:300]
                try:
                    from_release(pid)
                    entry["how"] = "release"
                except Exception as exc2:  # noqa: BLE001 - reported, tried again next start
                    entry.update(how="failed", error=str(exc2)[:300])
        out[pid] = entry
        if entry["how"] != "failed":
            done[pid] = entry
    if out:
        _write_record(record)
    return out
