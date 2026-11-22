"""Built-in plugins that left Loam for the Market, kept for whoever had them on.

Since 0.2.9 (2026-11-18) Dictation and Read aloud are no longer part of
Loam. Each is a Market plugin in its maker's own repository, listed in the
marketplace in their name; a fresh install has neither until it installs
them from Settings › Market. The release keeps a copy of each in
``plugins/_listed/<id>/`` for its tests and as the fallback below; a folder
there is never read as a plugin.

An install that had one on must not lose it on the update. So once, at the
broker's first start on this code, :func:`keep` installs each moved plugin
that was **on** (``installed.json``'s ``on`` says ``true``: they shipped
off, so only a person's yes turned one on):

- from **its Market listing**: the marketplace this install reads is asked
  for the listing of that id, and its link is installed through the normal
  two calls (``loam_plugins.install``: read, then confirm what was read), so
  it is an installed plugin with its link, as if the person had pressed Get.
  The link is read from the listing, not written here, so it follows the
  listing to the maker's repository and version;
- if the list or GitHub cannot be reached, from **the release's own copy**,
  put in the plugins folder as a plugin built here. Its switch is the id's
  and already says on, so it stays on; the Market can update it later.

What the person set is kept because nothing that holds it moves: the switch
(``installed.json``'s ``on``), each agent's voice (``_settings/<id>/``),
the plugin's own key and the shared keys are all kept by the plugin's id,
not by its layer, and the browser's own preferences are kept by the page
code's storage keys (``loam-ui.dictation``, ``loam-ui.speech``,
``loam-ui.read-aloud``), which the Market copies keep unchanged.

A plugin already in force in Yours (a copy built here, installed from a
link, or installed by hand before the update) is left alone, and each id is
settled once (``<state>/plugins/to-market.json``): one the person removes
later stays removed. A failure is reported and tried again at the next start.

**Since 0.2.12 each carries its own server half** (Dictation and Read aloud
1.1.0): ``/api/transcribe``, ``/api/speech`` and the broker's dictation
terms left Loam, and each plugin's own server answers at ``/x/<id>/``. A copy
older than that has nothing to talk to on this release, so once, at the same
first start, :func:`bring_up` installs the listing's version over any copy
below ``SERVER_SINCE`` that came from the Market (or from the release's own
copy, for one built here by the step above), keeping everything kept by id
as before. A copy installed from elsewhere, or edited since, is left alone
and said so. And :func:`seed_voices` copies the install's
``app.speech_voices`` (which the app used to read) into Read aloud's own
``voices`` setting, once, when that is still unset.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import loam_config as lc

# id -> the release it left Loam's core in.
MOVED = {
    "dictation": "0.2.9",
    "read-aloud": "0.2.9",
}
BY = "loam-update"
# The first version of each that carries its own server half (0.2.12).
SERVER_SINCE = {
    "dictation": (1, 1, 0),
    "read-aloud": (1, 1, 0),
}
# What the app read aloud in before 0.2.12 when the install named no voices
# (bin/deploy's default): the starter agent's.
DEFAULT_VOICES = "iris=Aoede:warm, natural and unhurried"


def record_path() -> Path:
    import loam_plugin_servers  # noqa: PLC0415
    return loam_plugin_servers.state_dir() / "to-market.json"


def _read_record() -> dict:
    try:
        data = json.loads(record_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_record(data: dict) -> None:
    path = record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".to-market-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def wanted() -> list[str]:
    """The moved plugins this install had switched on."""
    import loam_plugins  # noqa: PLC0415
    try:
        on = loam_plugins.load_list().get("on") or {}
    except Exception:  # noqa: BLE001 - an unreadable list wants nothing now
        return []
    return sorted(pid for pid in MOVED if on.get(pid) is True)


def _in_yours(pid: str) -> bool:
    import loam_plugins  # noqa: PLC0415
    return any(r["id"] == pid and r["layer"] == "yours" and r.get("effective")
               and not r.get("broken") for r in loam_plugins._rows())


def market_link(pid: str) -> str:
    """The link the marketplace this install reads lists for ``pid``."""
    import loam_marketplace  # noqa: PLC0415
    got = loam_marketplace.read(refresh=True)
    if got.get("state") != "listed":
        raise LookupError(got.get("message") or f"the marketplace is {got.get('state')}")
    for entry in got.get("plugins", []):
        if entry.get("id") == pid and entry.get("link"):
            return entry.get("pinned_link") or entry["link"]
    raise LookupError(f"the marketplace lists no {pid!r}")


def release_copy(pid: str) -> Path:
    return lc.ROOT / "plugins" / "_listed" / pid


def _from_release(pid: str) -> None:
    """The release's own copy, as a plugin built here."""
    import loam_plugins  # noqa: PLC0415
    source = release_copy(pid)
    if not (source / "PLUGIN.md").is_file():
        raise FileNotFoundError(f"{source} is not in this release")
    contributions = loam_plugins.read_folder(source, pid)["manifest"]["contributes"]
    loam_plugins.seed_release_copy(source, pid)
    if contributions.get("agent.tool") or contributions.get("agent.prompt"):
        loam_plugins._contributions_follow(f"{pid} kept from this release")


def keep(install=None, from_release=None, link_of=None) -> dict:
    """Install every wanted moved plugin not already in Yours, once each.
    Answers ``{id: {"how": "link"|"release"|"present"|"failed", ...}}`` for
    the ids it looked at this time. The arguments are for tests."""
    import loam_plugins  # noqa: PLC0415
    install = install or loam_plugins.install
    from_release = from_release or _from_release
    link_of = link_of or market_link
    record = _read_record()
    done = record.setdefault("kept", {})
    out: dict[str, dict] = {}
    for pid in wanted():
        if pid in done:
            continue
        entry: dict = {"why": "on", "at": _now()}
        if _in_yours(pid):
            entry["how"] = "present"
        else:
            try:
                link = link_of(pid)
                first = install({"via": "market", "url": link}, by=BY)
                if first.get("id") != pid:
                    raise loam_plugins.PluginError(f"{link} is {first.get('id')!r}, not {pid}")
                install({"via": "market", "url": link, "confirm": first["confirm"]}, by=BY)
                entry.update(how="link", link=link)
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


def _version(text) -> tuple[int, ...]:
    import re  # noqa: PLC0415
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(text or ""))
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def _yours_row(pid: str) -> dict | None:
    import loam_plugins  # noqa: PLC0415
    return next((r for r in loam_plugins._rows() if r["id"] == pid and r["layer"] == "yours"
                 and not r.get("broken")), None)


def bring_up(install=None, from_release=None, link_of=None) -> dict:
    """Install the listing's version over a moved plugin's copy that predates
    its own server, once each. Answers ``{id: {"how": ...}}`` for the ids it
    settled or failed this time: ``link`` (from the listing), ``release``
    (an absent plugin seeded from this release), ``current``, ``kept``
    (installed from elsewhere, or edited: left as it is, with ``why``),
    ``absent`` or ``failed`` (tried again at the next start)."""
    import loam_plugins  # noqa: PLC0415
    install = install or loam_plugins.install
    link_of = link_of or market_link
    record = _read_record()
    done = record.setdefault("servers", {})
    out: dict[str, dict] = {}
    for pid, since in SERVER_SINCE.items():
        if pid in done:
            continue
        entry: dict = {"at": _now()}
        row = _yours_row(pid)
        source = (row or {}).get("source") or {}
        if row is None:
            entry["how"] = "absent"
        elif _version(row.get("version")) >= since:
            entry["how"] = "current"
        elif row.get("edited"):
            entry.update(how="kept", why="edited since it was installed")
        elif source.get("kind") == "link":
            try:
                link = link_of(pid)
                first = install({"via": "market", "url": link}, by=BY)
                if first.get("id") != pid:
                    raise loam_plugins.PluginError(f"{link} is {first.get('id')!r}, not {pid}")
                if _version((first.get("plugin") or first).get("version")) < since:
                    raise loam_plugins.PluginError(f"the listing is still {first.get('version')}")
                install({"via": "market", "url": link, "confirm": first["confirm"]}, by=BY)
                entry.update(how="link", link=link, was=row.get("version"))
            except Exception as exc:  # noqa: BLE001 - reported, tried again next start
                entry.update(how="failed", error=str(exc)[:300])
        elif source.get("kind") in (None, "built"):
            # Older release copies had no fingerprint. A home-built plugin can
            # have the same id and version, so they all go through review.
            try:
                import loam_plugin_review as review
                first = install({"via": "market", "url": link_of(pid)}, by=BY)
                if first.get("id") != pid or _version(first.get("version")) < since:
                    raise loam_plugins.PluginError("The listing does not name the expected update.")
                base = review.original(loam_plugins.load_list(), pid)
                review.offer(pid, base, {"id": pid, "version": first["version"],
                             "source": {**first["source"], "via": "market"}})
                entry.update(how="kept", why="personal copy awaiting review")
            except (loam_plugins.PluginError, OSError, LookupError) as exc:
                entry.update(how="failed", error=str(exc)[:300])
        else:
            entry.update(how="kept", why=f"installed from {source.get('kind')}, not the Market")
        out[pid] = entry
        if entry["how"] != "failed":
            done[pid] = entry
    if out:
        _write_record(record)
    return out


def seed_voices(spec=None) -> dict | None:
    """Read aloud's ``voices`` setting from the install's ``app.speech_voices``
    (or the old default), once, when the setting is unset. Answers what it
    did, or None when it had already settled."""
    import loam_contributions  # noqa: PLC0415
    record = _read_record()
    if "voices" in record:
        return None
    if spec is None:
        got = lc.get("app.speech_voices")
        spec = got.strip() if isinstance(got, str) and got.strip() else DEFAULT_VOICES
    path = loam_contributions.settings_path("read-aloud")
    values = loam_contributions.setting_values("read-aloud")
    if isinstance(values.get("voices"), str) and values["voices"].strip():
        entry = {"how": "present", "at": _now()}
    else:
        values["voices"] = spec[:500]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        entry = {"how": "seeded", "at": _now()}
    record["voices"] = entry
    _write_record(record)
    return entry
