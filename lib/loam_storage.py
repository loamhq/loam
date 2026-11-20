"""Space on the vault filesystem. Only the low-priority sampler walks directories."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import loam_config as lc

CACHE = lc.LOGS / "activity-storage.json"
SURVEY_SECONDS = 30 * 60
CONTAINER_ROOTS = (Path("/var/lib/docker"), Path("/var/lib/containerd"))


def disk(path):
    try:
        fs = os.statvfs(path)
        return {"total_bytes": fs.f_blocks * fs.f_frsize,
                "used_bytes": (fs.f_blocks - fs.f_bfree) * fs.f_frsize,
                "free_bytes": fs.f_bavail * fs.f_frsize}
    except OSError:
        return None


def storage(vault, cache=None):
    usage = disk(vault)
    if usage is None:
        return None
    breakdown = None
    try:
        saved = json.loads((cache or CACHE).read_text())
        if saved["device"] == vault.stat().st_dev:
            breakdown = {"sampled_at": saved["sampled_at"], "groups": saved["groups"]}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {**usage, "breakdown": breakdown}


def allocated(roots, device, *, privileged=False):
    """GNU du accounts for shared inodes once. Visit nested roots before parents."""
    sizes = dict.fromkeys(roots, 0)
    paths = []
    try:
        for path in sorted(roots, key=lambda p: len(p.parts), reverse=True):
            try:
                if path.stat().st_dev == device:
                    paths.append(path)
            except FileNotFoundError:
                continue
        if paths:
            cmd = ["du", "-Hsx", "-B1", "--null", "--", *paths]
            if privileged:
                cmd = ["sudo", "-n", *cmd]
            raw = subprocess.run(cmd, capture_output=True, check=True, timeout=60).stdout
            for row in raw.rstrip(b"\0").split(b"\0"):
                count, path = row.split(b"\t", 1)
                sizes[Path(os.fsdecode(path))] = int(count)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return sizes


def survey(vault, workspaces, records, device):
    roots = {vault: "vault", workspaces: "workspaces", **dict.fromkeys(records, "records")}
    sizes = allocated(roots, device)
    groups = {name: None for name in ("vault", "workspaces", "records")}
    if sizes is not None:
        groups = dict.fromkeys(groups, 0)
        for path, count in sizes.items():
            groups[roots[path]] += count
    containers = allocated(CONTAINER_ROOTS, device, privileged=True)
    groups["containers"] = sum(containers.values()) if containers is not None else None
    usage = disk(vault)
    groups["other"] = (max(0, usage["used_bytes"] - sum(groups.values()))
                       if usage and None not in groups.values() else None)
    return groups


def refresh(at, cache=CACHE):
    """Publish a complete survey in one rename, at most twice an hour."""
    try:
        if 0 <= at.timestamp() - cache.stat().st_mtime < SURVEY_SECONDS:
            return
    except FileNotFoundError:
        pass
    device = lc.VAULT.stat().st_dev
    records = [lc.VAULT / lc.vault_folder("conversations"), lc.STATE / "threads",
               lc.STATE / "broker" / "sessions"]
    snapshot = {"device": device, "sampled_at": at.isoformat(timespec="seconds"),
                "groups": survey(lc.VAULT, lc.WORKSPACES, records, device)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=cache.parent, delete=False) as out:
        tmp = Path(out.name)
        try:
            json.dump(snapshot, out)
            out.flush()
            tmp.replace(cache)
        finally:
            tmp.unlink(missing_ok=True)
