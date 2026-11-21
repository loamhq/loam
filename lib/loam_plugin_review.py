"""A private, three-way working copy for an owner's plugin update.

installed.json owns the upstream pin and decisions for each effective copy.
Saved originals and recovery folders live alongside it in the plugins repo.
Only the broker changes those records. Agents edit the draft in staging.
"""
import ctypes
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path

import loam_attention
import loam_plugins as p


def refuse(message):
    raise p.PluginError(message, 409)


def digest(folder):
    result = p.fingerprint(folder)
    if not result:
        refuse("Loam could not verify every file in this plugin.")
    return result


def snapshot(folder, pid):
    key = digest(folder)
    target = version_folder(pid, key)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=p._staging()) as tmp:
            copy = Path(tmp) / pid
            shutil.copytree(folder, copy, symlinks=True)
            if digest(copy) != key:
                refuse("The plugin changed while Loam was saving it.")
            os.replace(copy, target)
    if digest(target) != key:
        refuse("The saved original changed. Restore it from the plugins history.")
    return key


def version_folder(pid, files):
    if not re.fullmatch(r"v2:[a-f0-9]{64}", files or ""):
        refuse("The saved original has no verified fingerprint.")
    return p.yours_root() / "_versions" / files[3:] / p._need_id(pid)


def target(pid):
    folder = p.copy_folder(pid)
    if folder.parent.is_symlink():
        refuse("The copies directory must be inside the owner’s plugins folder.")
    return folder if os.path.lexists(folder) else p.yours_root() / pid


def original(data, pid):
    saved = data.get("copies", {}).get(pid, {}).get("base")
    if saved:
        return saved
    line = data["plugins"].get(pid, {})
    if line.get("source", {}).get("kind") == "link":
        return {"id": pid, **{k: line.get(k) for k in ("source", "version", "files")}}


def shipped(pid):
    for root in p.shipped_roots():
        folder = root / pid
        if folder.is_dir():
            found = p.read_folder(folder, pid)["manifest"]
            files = snapshot(folder, pid)
            return {"id": pid, "source": {"kind": "loam", "commit": files},
                    "version": found["version"], "files": files}


def remote_version(source, pid):
    with tempfile.TemporaryDirectory(dir=p._staging()) as tmp:
        request = {"url": source["repo"], "ref": source["commit"], "path": source["path"]}
        got = p.fetch(p.parse_source(request), Path(tmp))
        folder = got["folder"]
        if folder.is_symlink() or not folder.resolve().is_relative_to(Path(tmp) / "repo"):
            refuse("The source folder leaves its repository.")
        shutil.rmtree(Path(tmp) / "repo" / ".git", ignore_errors=True)
        manifest = p.read_folder(folder, None)["manifest"]
        if manifest["id"] != pid or got["source"]["commit"] != source["commit"]:
            refuse("This version belongs to another plugin.")
        return {"id": pid, "source": source, "version": manifest["version"],
                "files": snapshot(folder, pid)}


def materialize(base):
    files = base.get("files") or ""
    if files.startswith("v2:") and version_folder(base["id"], files).is_dir():
        folder = version_folder(base["id"], files)
        if digest(folder) != files:
            refuse("The saved original changed. Restore it from the plugins history.")
        return folder
    if base["source"]["kind"] != "link":
        refuse("Loam needs the original copy this version started from.")
    found = remote_version(base["source"], base["id"])
    return version_folder(base["id"], found["files"])


def track(data, pid, origin, link=None):
    p._need_id(origin)
    folder = target(pid)
    if not folder.is_dir() or folder.is_symlink():
        refuse("Make the local copy before connecting its original.")
    if data.get("copies", {}).get(pid, {}).get("base"):
        refuse("This copy already has an original.")
    base = original(data, origin)
    if link:
        preview = p.install({"url": link})
        if preview.get("id") != origin:
            refuse("The starting link must name the original plugin.")
        source = preview["source"]
        if base and source["repo"] == base["source"].get("repo"):
            source = {**source, "via": base["source"].get("via")}
        base = remote_version(source, origin)
    elif not base:
        base = shipped(origin)
    if not base:
        refuse("Give the exact link this copy started from.")
    base = {**base, "files": digest(materialize(base))}
    data.setdefault("copies", {}).setdefault(pid, {})["base"] = base
    p._save_list(data)
    return {"original": base, "folder": str(folder)}


def offer(pid, base, candidate, *, skip=False):
    """Persist a decision by upstream version. Retry a failed attention write."""
    with p._lock:
        data = p.load_list()
        if original(data, pid) != base or not target(pid).is_dir():
            refuse("The local copy changed. Check for updates again.")
        record = data.setdefault("copies", {}).setdefault(pid, {"base": base})
        version = candidate["version"]
        if not skip and version in record.get("skipped", []):
            return {"skipped": version}
        if skip:
            if skip and version not in record.setdefault("skipped", []):
                record["skipped"].append(version)
            if record.get("offer", {}).get("version") == version:
                record.pop("offer", None)
                record.pop("draft", None)
            p._save_list(data)
            return {"skipped": version}
        if (record.get("offer", {}).get("source") != candidate["source"]
                or record.get("offer", {}).get("version") != version):
            record["offer"] = candidate
            record.pop("draft", None)
            p._save_list(data)
        if record.get("told") != candidate["source"]["commit"]:
            result = loam_attention.tell(
                f"{pid} has a new version, {version}. Your person's own changes are safe.",
                by="plugin-updates", key=f"{pid}-{candidate['source']['commit']}-copy",
                evidence=f"loam plugin prepare {pid}",
                detail="Use loam-building-a-plugin. The draft combines separate changes. "
                       "Explain the choices and any new access in chat. Apply on yes, or skip this version.")
            if result not in ("told", "repeat"):
                refuse("Loam could not notify your agent. It will try again.")
            record["told"] = candidate["source"]["commit"]
            p._save_list(data)
        return {"offered": version}


def node(path):
    if not os.path.lexists(path):
        return None
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
        return mode, os.readlink(path)
    if stat.S_ISDIR(mode):
        return mode, digest(path)
    if stat.S_ISREG(mode):
        return mode, hashlib.sha256(path.read_bytes()).digest()
    refuse("This plugin contains a file Loam cannot compare.")


def put(src, dst):
    if src.is_symlink():
        dst.symlink_to(os.readlink(src))
    elif src.is_dir():
        shutil.copytree(src, dst, symlinks=True)
    elif src.exists():
        shutil.copy2(src, dst)


def combine(base, local, incoming, draft, rel, changes):
    """Merge complete nodes, descending through directories and merging text by line."""
    old, mine, new = (node(f) for f in (base, local, incoming))
    dirs = all(n is None or stat.S_ISDIR(n[0]) for n in (old, mine, new))
    if dirs and mine and new:
        draft.mkdir()
        names = {f.name for d in (base, local, incoming) if d.is_dir() for f in d.iterdir()}
        for name in sorted(names):
            combine(base / name, local / name, incoming / name, draft / name, rel / name, changes)
        if old and mine[0] != old[0] and new[0] not in (old[0], mine[0]):
            changes["choices"].append(str(rel))
        draft.chmod(new[0] if old and mine[0] == old[0] else mine[0])
        return
    for group, changed in (("personal", mine != old), ("upstream", new != old)):
        if changed:
            changes[group].append(str(rel))
    if mine == old:
        put(incoming, draft)
    elif new == old or mine == new:
        put(local, draft)
    else:
        changes["overlap"].append(str(rel))
        if all(n and stat.S_ISREG(n[0]) for n in (old, mine, new)):
            result = subprocess.run(
                ["git", "merge-file", "-p", "-L", "your copy", "-L", "original",
                 "-L", "new version", str(local), str(base), str(incoming)], capture_output=True)
            if result.returncode in range(128):
                draft.write_bytes(result.stdout)
                draft.chmod(incoming.stat().st_mode if mine[0] == old[0] else mine[0])
                if result.returncode == 0 and (mine[0] == old[0] or new[0] in (mine[0], old[0])):
                    return
            else:
                put(local, draft)
        else:
            put(local, draft)
        changes["choices"].append(str(rel))


def prepare(data, pid, record):
    incoming = record["offer"]
    if not record.get("base"):
        refuse("Connect the starting version with loam plugin track and its exact release link.")
    base = materialize(record["base"])
    upstream = materialize(incoming)
    incoming["files"] = digest(upstream)
    folder = target(pid)
    files = digest(folder)
    existing = record.get("draft")
    if existing and existing["files"] == files and Path(existing["folder"]).is_dir():
        return existing
    work = Path(tempfile.mkdtemp(prefix="review-", dir=p._staging()))
    draft = work / "draft" / pid
    draft.parent.mkdir()
    try:
        local = work / "local" / pid
        shutil.copytree(folder, local, symlinks=True)
        if digest(local) != files:
            refuse("Your copy changed. Prepare the update again.")
        changes = {k: [] for k in ("personal", "upstream", "overlap", "choices")}
        combine(base, local, upstream, draft, Path(), changes)
        result = {"folder": str(draft), "local": str(local), "base": str(base),
                  "incoming": str(upstream), "version": incoming["version"], "files": files,
                  "target": str(folder), **changes}
        record["draft"] = result
        p._save_list(data)
        return result
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def validate(folder, pid, live, record):
    files = digest(folder)
    for f in folder.rglob("*"):
        if f.name == ".git" or f.is_symlink() and not f.resolve().is_relative_to(folder):
            refuse("The draft contains a repository or a link outside the plugin.")
        if f.is_file() and not f.is_symlink() and b"<<<<<<< your copy" in f.read_bytes():
            refuse("Finish the highlighted choices in the draft first.")
    found = p.read_folder(folder, pid)
    if found["manifest"]["id"] != pid or set(found["manifest"]["kind"]) & set(p.LISTED_ONLY):
        refuse("This draft cannot be installed as this plugin.")
    declaration = p.declaration(found["manifest"], found["has"])
    previous = {}
    try:
        previous = p.read_folder(live, pid)
        prior = p.declaration(previous["manifest"], previous["has"])
    except p.ManifestError:
        prior = None
    token = hashlib.sha256(json.dumps([pid, files, record["draft"]["files"],
                                       record["base"], record["offer"], declaration],
                                      sort_keys=True).encode()).hexdigest()
    return {"confirm": token, "added": p.added(prior, declaration),
            "warnings": found["warnings"], "declaration": declaration, "files": files,
            "prior_contributes": bool(previous.get("manifest", {}).get("contributes"))}


def exchange(one, other):
    """Linux renameat2 with RENAME_EXCHANGE on the plugins filesystem."""
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    if rename(-100, os.fsencode(one), -100, os.fsencode(other), 2):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def finish(data, pid, record, confirm):
    draft = record.get("draft")
    if not draft:
        refuse("Prepare this update first.")
    folder, live = Path(draft["folder"]), target(pid)
    if str(live) != draft["target"] or digest(live) != draft["files"]:
        refuse("Your copy changed. Prepare the update again.")
    reviewed = validate(folder, pid, live, record)
    if confirm is None:
        return reviewed
    if confirm != reviewed["confirm"]:
        refuse("The draft changed. Review it with the owner again.")
    incoming = materialize(record["offer"])
    baseline = digest(incoming)
    if incoming != Path(draft["incoming"]):
        refuse("The saved update changed. Prepare it again.")
    recovery = p.yours_root() / "_history" / pid / uuid.uuid4().hex
    recovery.mkdir(parents=True)
    previous = recovery / pid
    shutil.copytree(folder, previous, symlinks=True)
    if digest(previous) != reviewed["files"]:
        refuse("The draft changed during approval.")
    (recovery / "before.json").write_text(json.dumps({
        "copy": record, "installed": data["plugins"].get(pid), "target": str(live)}, indent=2))
    # Both names always exist, including if the process stops mid-approval.
    # After the exchange the recovery path contains the actual prior inode.
    exchange(live, previous)
    try:
        if digest(previous) != draft["files"] or target(pid) != live:
            refuse("Your copy changed during approval.")
        data["copies"][pid] = {"base": {**record["offer"], "files": baseline},
                               "confirmed": reviewed["declaration"],
                               "skipped": record.get("skipped", [])}
        line = data["plugins"].get(pid)
        if line and live == p.yours_root() / pid:
            line.update(source=record["offer"]["source"], version=record["offer"]["version"],
                        files=baseline, declaration=reviewed["declaration"], confirmed=confirm,
                        auto_update=False, updates_paused=True)
        p._save_list(data)
    except BaseException:
        exchange(live, previous)
        raise
    plugin = p.one(pid)["plugin"]
    if any(k.get("source") for k in plugin.get("keys", [])):
        import loam_keys
        loam_keys.sync(refresh_sources=True)
    p._servers_follow(pid, restart=True)
    if plugin.get("contributes") or reviewed.get("prior_contributes"):
        p._contributions_follow(f"{pid} updated with owner approval")
    return {"applied": record["offer"]["version"], "recovery": str(recovery),
            "plugin": p.one(pid)["plugin"]}


def action(pid, fields):
    p._need_id(pid)
    op = fields.get("review")
    with p._lock:
        data = p.load_list()
        try:
            if op == "track":
                return track(data, pid, fields.get("original"), fields.get("link"))
            record = data.get("copies", {}).get(pid, {})
            base = original(data, pid)
            if op in ("ask", "skip") and fields.get("link"):
                preview = p.install({"url": fields["link"]})
                origin_id = base["id"] if base else pid
                if preview.get("id") != origin_id or preview.get("installable") is False:
                    refuse("This version belongs to another plugin.")
                candidate = {"id": origin_id, "version": preview["version"],
                             "source": {**preview["source"], "via": fields.get("via", "link")}}
                return offer(pid, base, candidate, skip=op == "skip")
            candidate = record.get("offer")
            if not candidate:
                refuse("There is no update waiting for this copy.")
            if op in ("ask", "skip"):
                if candidate["version"] != fields.get("version"):
                    refuse("The available version changed. Check again.")
                return offer(pid, base, candidate, skip=op == "skip")
            if op == "prepare":
                return prepare(data, pid, record)
            if op == "preview":
                return finish(data, pid, record, None)
            if op == "apply" and fields.get("confirm"):
                return finish(data, pid, record, fields["confirm"])
            refuse("Choose prepare, preview, apply, ask or skip.")
        except (OSError, p.ManifestError) as exc:
            refuse(str(exc))
