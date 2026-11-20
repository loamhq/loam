"""Daily discovery for installed plugins. Only nightly housekeeping applies updates.

Both callers run inside the broker so installs and preference changes share
the installer's lock. The daily snapshot survives broker restarts. It names
the exact installed source, candidate commit and two-step install token.
"""

import json
import os
import threading
import time

import loam_attention
import loam_marketplace as market
import loam_plugin_servers
import loam_plugins as plugins

DAY = 86400
_lock = threading.Lock()


def version_key(value):
    if not isinstance(value, str) or not plugins.VERSION.fullmatch(value):
        return ()
    version, _, pre = value.partition("+")[0].partition("-")
    identifiers = tuple((0, int(p)) if p.isdecimal() else (1, p) for p in pre.split("."))
    return tuple(map(int, version.split("."))), not bool(pre), identifiers


def tag_link(pid, source):
    src = plugins.parse_source({"url": source["repo"], "path": source.get("path", "")})
    _, env = plugins._remote(src)
    tags = plugins._refs(env["_refs"])["tags"]
    tag = plugins.newest_tag(tags, [pid, src["path"].split("/")[-1], src["repo"].split("/")[-1]])
    if not tag or tags[tag] == source.get("commit"):
        return None
    return plugins.pinned_link({**src, "ref": tag, "ref_kind": "tag"})


def inspect(pid, line, entry, market_state):
    source = line["source"]
    # Older installs did not record the door they came through. A matching
    # package in the same repository follows the listing even after it moves.
    legacy_match = (not source.get("via") and entry and
                    entry["repo"].lower() == source["repo"].lower() and
                    (entry.get("pin", {}).get("path") or entry["path"]) == source.get("path", ""))
    via = "market" if (source.get("via") == "market" or line.get("update_via") == "market"
                       or legacy_match) else "link"
    result = {"base": source, "via": via}
    if via == "market":
        if not entry:
            return result
        pin = entry.get("pin") or {}
        if (pin.get("ref") and pin["ref"] == source.get("commit")
                or not pin and entry["ref"] in (source.get("commit"), source.get("ref"))):
            return result
        link = entry.get("pinned_link") or entry["link"]
    else:
        if not source.get("via") and market_state == "unreachable":
            return result
        link = tag_link(pid, source)
    if not link:
        return result
    preview = plugins.install({"url": link})
    if (preview.get("id") != pid or preview.get("installable") is False
            or not version_key(line.get("version"))
            or version_key(preview["version"]) <= version_key(line["version"])):
        return result
    candidate = preview["source"]
    if via == "market" and candidate["ref_kind"] != "tag" and not plugins.SHA.fullmatch(
            (entry.get("pin") or {}).get("ref") or entry["ref"]):
        return result
    if via == "link" and candidate["ref_kind"] != "tag":
        return result
    held = bool(preview["added"] or not line.get("declaration")
                or source["repo"].lower() != candidate["repo"].lower())
    result.update(commit=candidate["commit"], version=preview["version"],
                  link=candidate["url"], confirm=preview["confirm"], held=held,
                  added=preview["added"])
    return result


def check(nightly=False):
    with _lock:
        path = loam_plugin_servers.state_dir() / "updates.json"
        try:
            cache = json.loads(path.read_text())
        except (OSError, ValueError):
            cache = {}
        with plugins._lock:
            listed = plugins.load_list()
            plugins._remember_pauses(listed)
        lines = {pid: line for pid, line in listed["plugins"].items()
                 if isinstance(line, dict) and plugins.ID.fullmatch(pid)
                 and (line.get("source") or {}).get("kind") == "link"}
        if not cache or time.time() - cache.get("checked", 0) >= DAY:
            catalogue = market.read(refresh=True) if lines else {"plugins": [], "state": "empty"}
            entries = {entry["id"]: entry for entry in catalogue["plugins"]}
            cache = {"checked": time.time(), "market_state": catalogue["state"],
                     "entries": {}, "plugins": {}, "errors": {}}
            for pid, line in lines.items():
                entry = entries.get(pid)
                if entry:
                    cache["entries"][pid] = {k: entry[k] for k in
                                              ("id", "author", "repo", "ref", "path", "pin")
                                              if k in entry}
                try:
                    record = inspect(pid, line, entry, catalogue["state"])
                    cache["plugins"][pid] = record
                    if record.get("held"):
                        loam_attention.tell(
                            f"{pid} {record['version']} needs your yes. Review the update in Settings › Plugins.",
                            by="plugin-updates", key=f"{pid}-{record['commit']}", evidence=record["link"],
                            detail=json.dumps(record["added"]) if record["added"] else None)
                    if record["via"] == "market" and not (line["source"].get("via") or line.get("update_via")):
                        with plugins._lock:
                            current = plugins.load_list()
                            same = current["plugins"].get(pid) or {}
                            if same.get("source") == line["source"]:
                                same["update_via"] = "market"
                                plugins._save_list(current)
                except (plugins.PluginError, OSError, ValueError) as exc:
                    cache["errors"][pid] = str(exc)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache))
            os.replace(tmp, path)
        updates = {}
        errors = dict(cache.get("errors", {}))
        for pid, record in cache["plugins"].items():
            line = lines.get(pid)
            if not line or line["source"] != record["base"]:
                continue
            updates[pid] = {k: record[k] for k in ("via", "version", "link", "held") if k in record}
            if not record.get("link"):
                continue
            if nightly and not record["held"] and plugins.update_preference(pid, line)["auto_update"]:
                try:
                    plugins.install({"url": record["link"], "via": record["via"],
                                     "confirm": record["confirm"]}, by="loam",
                                    automatic={**record, "id": pid})
                    updates.pop(pid)
                except (plugins.PluginError, OSError) as exc:
                    errors[pid] = str(exc)
        return {"plugins": updates, "entries": cache["entries"], "market_state": cache["market_state"],
                "checked": cache["checked"], "errors": errors}
