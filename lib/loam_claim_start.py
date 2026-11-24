"""The boundary between an install's setup history and its owner's first day."""

from __future__ import annotations

import fcntl
import json
import subprocess

import loam_config as lc
import loam_deploy


def unclaimed() -> bool:
    if not lc.get("edge.app"):
        return False
    return loam_deploy.unclaimed()


def claimed_at() -> str:
    """UTC timestamp, normalized to the precision of runtime event records."""
    if not lc.get("edge.app"):
        return ""
    try:
        return json.loads(loam_deploy.CLAIMED_FILE.read_text())["at"][:19]
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def prepare() -> None:
    """Run as the install user before committing the claim, also on a retry."""
    import loam_agents
    import loam_attention
    import loam_check
    import loam_inbox
    import loam_starter
    import loam_threads as threads
    import loam_update

    if not unclaimed():
        raise RuntimeError("The install has already been claimed")
    # Setup failures on retired wakes can stay latched in the user manager.
    # Only acknowledge Loam's failed units while the install is still unclaimed.
    units = subprocess.check_output(["systemctl", "--user", "list-units", "--failed", "--plain",
                                     "--no-legend", "--no-pager", "loam-*.service"], text=True)
    failed = [line.split()[0] for line in units.splitlines() if line.strip()]
    if failed:
        subprocess.run(["systemctl", "--user", "reset-failed", *failed], check=True)
    threads.ensure_general()
    with threads.locked(threads.VAULT):
        meta, _, path = threads.read(threads.GENERAL_ID)
        # Old releases pinned every member to a provider before anyone signed in.
        meta["engines"] = {}
        meta["models"] = {}
        threads._write_atomic(path, threads.render_front(meta))
        for name in ("threads.json", "sessions.json", "handoffs.json"):
            state = threads.load_state(name)
            if state.pop(threads.GENERAL_ID, None) is not None:
                threads.save_state(name, state)
    (lc.VAULT / loam_inbox.FAILURE_LOG).unlink(missing_ok=True)
    with loam_attention._queue_lock():
        for record in loam_attention.STATE.glob("*.json"):
            record.unlink()
    try:
        with loam_update.run_lock():
            for name in ("check.json", "apply.json"):
                (loam_update.STATE / name).unlink(missing_ok=True)
    except loam_update.Refusal:
        pass  # An update in progress owns its record.
    for path in (loam_update.STATE / "notices").glob("*.json"):
        path.unlink()
    with loam_agents.locked():
        seeded = loam_starter.status()
        if seeded and seeded.get("thread"):
            seeded["at"] = loam_agents.now_iso()
            loam_agents._write_json(loam_starter.marker_path(), seeded)
    with loam_check.STATE_FILE.with_name(".check.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        loam_check.STATE_FILE.unlink(missing_ok=True)
