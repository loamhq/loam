"""Daily discovery for installed plugins. Only nightly housekeeping applies updates.

Both callers run inside the broker so installs and preference changes share
the installer's lock. The daily snapshot survives broker restarts. It names
the exact installed source, candidate commit and two-step install token.
"""

import json
import os
import shutil
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import loam_attention
import loam_marketplace as market
import loam_plugin_review as review
import loam_plugin_servers
import loam_plugins as plugins

DAY = 86400
REVIEW_FORMAT = 4
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


def market_tag(pid, line, entry):
    """Prove the active tree against a tag in the listed repository."""
    source = line["source"]
    built = source["kind"] == "built"
    if not entry or (not built and (source["kind"] != "link" or source.get("via") == "market"
                                    or not review.market_origin(pid, source, entry))):
        return None
    folder = review.target(pid)
    files = plugins.fingerprint(folder)
    if not files or not built and files != line.get("files"):
        return None
    found = plugins.read_folder(folder, pid)
    manifest = found["manifest"]
    version = manifest["version"]
    if not version_key(version) or version_key(version) > version_key(entry["version"]):
        return None
    if set(manifest["kind"]) & set(plugins.LISTED_ONLY) or built and version == entry["version"]:
        return None  # The ordinary candidate already checks for an exact current built release.
    src = plugins.parse_source({"url": entry["repo"], "path": (entry.get("pin") or entry)["path"]})
    url, env = plugins._remote(src)
    tags = plugins._refs(env.pop("_refs"))["tags"]
    tried = set()
    for tag in sorted(tags, key=lambda name: (name != source.get("ref"), not name.endswith(version), name)):
        commit = tags[tag]
        if not built and commit != source.get("commit"):
            continue
        tagged = {**src, "kind": "link", "via": "market", "ref": tag,
                  "ref_kind": "tag", "commit": commit}
        if version == entry["version"] and not review.market_pin(pid, tagged, entry):
            continue
        if commit in tried:
            continue
        tried.add(commit)
        with TemporaryDirectory(dir=plugins._staging()) as tmp:
            repo = Path(tmp) / "repo"
            if plugins._checkout(url, env, commit, repo) != commit:
                continue
            candidate = repo / src["path"]
            if candidate.resolve() != candidate or not candidate.is_dir():
                continue
            shutil.rmtree(repo / ".git")
            if plugins.fingerprint(candidate) == files:
                tagged["url"] = plugins.pinned_link({**tagged, "ref_kind": "commit"})
                return {"id": pid, "version": version, "source": tagged, "files": files,
                        "declaration": plugins.declaration(manifest, found["has"])}
    return None


def inspect(pid, line, entry, market_state):
    source = line["source"]
    if source["kind"] == "loam":
        result = {"base": source, "via": "loam"}
        latest = review.shipped(pid)
        if latest and version_key(latest["version"]) > version_key(line["version"]):
            result.update(candidate=latest, version=latest["version"], commit=latest["source"]["commit"],
                          held=True)
        return result
    built = source["kind"] == "built"
    # Untracked local code can join only its same-id, immutable Market release.
    if built and not entry:
        return {"base": source, "via": "market"}
    same_origin = not built and review.market_origin(pid, source, entry)
    legacy_match = same_origin and not source.get("via")
    via = "market" if (built or source.get("via") == "market" or line.get("update_via") == "market"
                       or legacy_match) else "link"
    result = {"base": source, "via": via}
    if via == "market":
        if not entry or (not built and not same_origin):
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
    preview = plugins.install({"url": link, "via": via})
    if preview.get("id") != pid or preview.get("installable") is False:
        return result
    candidate = preview["source"]
    if via == "market" and candidate["ref_kind"] != "tag" and not plugins.SHA.fullmatch(
            (entry.get("pin") or {}).get("ref") or entry["ref"]):
        return result
    if via == "link" and candidate["ref_kind"] != "tag":
        return result
    held = bool(built or preview["added"] or not line.get("declaration")
                or source["repo"].lower() != candidate["repo"].lower())
    result["candidate"] = {"id": pid, "version": preview["version"],
                           "source": {**candidate, "via": via}, "files": preview["files"],
                           "declaration": preview["declaration"]}
    if not built and (not version_key(line.get("version"))
                      or version_key(preview["version"]) <= version_key(line["version"])):
        result["match_only"] = True
    result.update(commit=candidate["commit"], version=preview["version"],
                  link=candidate["url"], confirm=preview["confirm"], held=held,
                  added=preview["added"])
    return result


def recognise_copies(listed):
    folders = {f.name: f for f in plugins._folders(plugins.yours_root())}
    shipped = {f.name for root in plugins.shipped_roots() for f in plugins._folders(root)}
    for pid, folder in folders.items():
        if (pid in listed["plugins"] or pid in listed.get("copies", {})
                or not plugins.ID.fullmatch(pid)):
            continue
        try:
            manifest = plugins.read_folder(folder, pid)["manifest"]
            parent = manifest.get("forked_from")
            if not parent and pid not in shipped:
                continue
            origin, version = parent.split("@") if parent else (pid, manifest["version"])
            base = review.original(listed, origin) or review.shipped(origin)
            if base and base["version"] == version:
                review.track(listed, pid, origin)
        except (plugins.PluginError, plugins.ManifestError, OSError):
            continue  # Existing unidentified copies remain protected.


def check(nightly=False):
    with _lock:
        path = loam_plugin_servers.state_dir() / "updates.json"
        try:
            cache = json.loads(path.read_text())
        except (OSError, ValueError):
            cache = {}
        local_errors = {}
        adopted = []
        with plugins.locked():
            listed = plugins.load_list()
            recognise_copies(listed)
            for pid, line in list(listed["plugins"].items()):
                if (isinstance(line, dict) and plugins.ID.fullmatch(pid)
                        and (line.get("source") or {}).get("kind") == "link" and line.get("declaration")
                        and (pid in listed.get("copies", {}) or
                             "underlay" not in line and os.path.lexists(plugins.copy_folder(pid)))):
                    try:
                        if review.adopt(listed, pid, {**line, "id": pid}):
                            adopted.append(pid)
                    except (plugins.PluginError, OSError) as exc:
                        local_errors[pid] = str(exc)
        lines = {pid: line for pid, line in listed["plugins"].items()
                 if isinstance(line, dict) and plugins.ID.fullmatch(pid)
                 and (line.get("source") or {}).get("kind") == "link"}
        for pid, record in listed.get("copies", {}).items():
            if plugins.ID.fullmatch(pid) and record.get("base") and review.target(pid).is_dir():
                lines[pid] = record["base"]
        for folder in plugins._folders(plugins.yours_root()) + plugins._folders(plugins.yours_root() / "_copies"):
            if plugins.ID.fullmatch(folder.name) and folder.name not in lines:
                lines[folder.name] = {"source": {"kind": "built"}}
        old_held = {
            (pid, rec["commit"])
            for pid, rec in (cache.get("plugins") or {}).items()
            if isinstance(rec, dict) and rec.get("held") and rec.get("commit")
        }
        new_install = lines.keys() - cache.get("plugins", {}).keys()
        if cache.get("review_format") != REVIEW_FORMAT or new_install or time.time() - cache.get("checked", 0) >= DAY:
            catalogue = market.read(refresh=True) if lines else {"plugins": [], "state": "empty"}
            entries = {entry["id"]: entry for entry in catalogue["plugins"]}
            cache = {"checked": time.time(), "market_state": catalogue["state"],
                     "entries": {}, "plugins": {}, "errors": {}, "review_format": REVIEW_FORMAT}
            for pid, line in lines.items():
                upstream_id = line.get("id", pid)
                entry = entries.get(upstream_id)
                if entry:
                    cache["entries"][pid] = {k: entry[k] for k in
                                              ("id", "author", "repo", "ref", "path", "pin",
                                               "version", "link", "pinned_link")
                                              if k in entry}
                try:
                    record = inspect(upstream_id, line, entry, catalogue["state"])
                    cache["plugins"][pid] = record
                    if (line["source"]["kind"] == "link" and record["via"] == "market"
                            and not (line["source"].get("via") or line.get("update_via"))):
                        with plugins.locked():
                            current = plugins.load_list()
                            same = current["plugins"].get(pid) or {}
                            if same.get("source") == line["source"]:
                                same["update_via"] = "market"
                                plugins._save_list(current)
                except (plugins.PluginError, plugins.ManifestError, OSError, ValueError) as exc:
                    cache["errors"][pid] = str(exc)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache))
            os.replace(tmp, path)
        updates = {}
        cache_changed = False
        errors = {**cache.get("errors", {}), **local_errors}
        for pid, record in cache["plugins"].items():
            line = lines.get(pid)
            if pid in local_errors or not line or line["source"] != record["base"]:
                continue
            built = line["source"]["kind"] == "built"
            candidate = record.get("candidate")
            entry = cache["entries"].get(pid)
            if candidate and candidate.get("files") and candidate["id"] == pid:
                with plugins.locked():
                    current = plugins.load_list()
                    base = review.original(current, pid)
                    if (base or {"source": {"kind": "built"}})["source"] != record["base"]:
                        continue
                    try:
                        if review.adopt(current, pid, candidate, entry=entry):
                            adopted.append(pid)
                            continue
                    except (plugins.PluginError, OSError) as exc:
                        errors[pid] = str(exc)
                        continue
            if nightly and line.get("id", pid) == pid:
                try:
                    release = market_tag(pid, line, entry)
                    if release:
                        with plugins.locked():
                            current = plugins.load_list()
                            base = review.original(current, pid)
                            if (base or {"source": {"kind": "built"}})["source"] != record["base"]:
                                continue
                            if not review.adopt(current, pid, release, entry=entry, verified_tag=True):
                                continue
                            adopted.append(pid)
                            listed = current
                            line = current["plugins"][pid]
                        cache_changed = True
                        record = inspect(pid, line, entry, cache["market_state"])
                        cache["plugins"][pid] = record
                        built = False
                        candidate = record.get("candidate")
                except (plugins.PluginError, plugins.ManifestError, OSError, ValueError) as exc:
                    errors[pid] = str(exc)
                    if pid in adopted:
                        cache["checked"] = 0  # Retry discovery using the new baseline.
                    continue
            if built or record.get("match_only"):
                continue
            updates[pid] = {k: record[k] for k in ("via", "version", "link", "held") if k in record}
            if not candidate:
                continue
            pref = plugins.update_preference(pid, line)
            if pid in listed.get("copies", {}) or pref["edited"] or pref["custom_copy"]:
                try:
                    answer = review.offer(pid, review.original(listed, pid), candidate)
                    if "skipped" in answer or "adopted" in answer:
                        updates.pop(pid)
                    else:
                        updates[pid].update(personal=True, held=True)
                except plugins.PluginError as exc:
                    errors[pid] = str(exc)
                continue
            if record.get("held"):
                if not record.get("notified"):
                    told = loam_attention.tell(
                        f"{pid} {record['version']} needs your yes. Review the update in Settings › Plugins.",
                        by="plugin-updates", key=f"{pid}-{record['commit']}", evidence=record["link"],
                        detail=json.dumps(record["added"]) if record["added"] else None)
                    if told in ("told", "repeat"):
                        record["notified"] = True
                        cache_changed = True
            elif record.get("commit"):
                loam_attention.withdraw({f"plugin-updates:{pid}-{record['commit']}"})
            if not record.get("link"):
                continue
            if nightly and not record["held"] and pref["auto_update"]:
                try:
                    plugins.install({"url": record["link"], "via": record["via"],
                                     "confirm": record["confirm"]}, by="loam",
                                    automatic={**record, "id": pid})
                    updates.pop(pid)
                except (plugins.PluginError, OSError) as exc:
                    errors[pid] = str(exc)
        stale_held = {
            f"plugin-updates:{pid}-{commit}"
            for pid, commit in old_held
            if pid not in errors and not (
                isinstance(cache["plugins"].get(pid), dict)
                and cache["plugins"][pid].get("held")
                and cache["plugins"][pid].get("commit") == commit
            )
        }
        if stale_held:
            loam_attention.withdraw(stale_held)
        if cache_changed:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache))
            os.replace(tmp, path)
        return {"plugins": updates, "adopted": adopted, "entries": cache["entries"], "market_state": cache["market_state"],
                "checked": cache["checked"], "errors": errors}
