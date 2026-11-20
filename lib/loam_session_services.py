"""Replace the API independently; hold admission closed across a host replacement."""
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import time

import loam_config as lc
import loam_live
import loam_session_fence as fence
import loam_session_source as source
import loam_units as units
import loam_update as update

API = units.API_UNIT
HOST = units.HOST_UNIT


class Deferred(RuntimeError):
    pass


def ctl(*args):
    return subprocess.run(["systemctl", "--user", *args], check=True,
                          text=True, stdout=subprocess.PIPE).stdout.strip()


def pid(unit):
    return int(ctl("show", "-p", "MainPID", "--value", unit) or 0)


def selected(view, target):
    """Compare each running process to the target, including imports deleted there."""
    chosen = set()
    for key, unit, entry in (("api", API, "bin/session-broker"), ("host", HOST, "bin/session-host")):
        old = view.get(key + "_revision")
        if not old:
            raise Deferred(f"the running {key} has no revision")
        changed = set(update.changed_files(old, target))
        if not changed:
            continue
        prefix = update.prefix()
        if (prefix + "systemd/session/" + unit in changed
                or (unit == API and any(p.startswith(prefix + "plugins/") for p in changed))):
            chosen.add(unit)
            continue
        if not any(p.startswith(prefix + "lib/") or p == prefix + entry for p in changed):
            continue
        try:
            closure = update.import_closure(old, entry) | update.import_closure(target, entry)
            if unit == HOST and prefix + "lib/loam_pins.py" in changed:
                closure.add(prefix + "lib/loam_pins.py")
            if any(not source.same_python(old, target, path, closure, prefix)
                   for path in sorted(closure & changed)):
                chosen.add(unit)
        except UnicodeError:
            chosen.add(unit)
    return chosen


def idle():
    view = loam_live.broker_live()
    if not view or not all(view.get(k) is True for k in ("admission_closed", "host_admission_closed")):
        raise Deferred("the running API and provider host have not both acknowledged the admission fence")
    if not isinstance(view.get("turns"), list) or view.get("opening") != 0:
        raise Deferred("a session is opening or thread work could not be read")
    reasons = loam_live.restart_blockers(view.get("sessions"), view["turns"])
    if reasons:
        raise Deferred(", ".join(reasons))


def healthy(split, old, replaced, timeout=75):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = loam_live.broker_live(timeout=2)
        if view:
            api = pid(API)
            host = pid(HOST) if split else api
            if (api and host and view.get("api_pid") == api and view.get("host_pid") == host
                    and (api != host) == split
                    and all((old.get(u) != p if u in replaced else old.get(u) == p)
                            for u, p in ((API, api), (HOST, host)))):
                return view
        time.sleep(.25)
    raise RuntimeError("the expected API and provider host did not answer on their new processes")


def install_units(split, chosen):
    rendered = units.rendered(split=split)
    for unit in chosen:
        dest = lc.USER_UNIT_DIR / unit
        if unit in rendered:
            tmp = dest.with_suffix(".new")
            tmp.write_text(rendered[unit])
            tmp.replace(dest)
        else:
            dest.unlink(missing_ok=True)
    ctl("daemon-reload")


def drain(unit):
    group = ctl("show", "-p", "ControlGroup", "--value", unit)
    if ctl("show", "-p", "LoadState", "--value", unit) == "not-found":
        return
    ctl("stop", unit)
    if pid(unit):
        raise RuntimeError(f"{unit} still has a main process")
    if group:
        path = Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.events"
        if path.exists() and "populated 1" in path.read_text().splitlines():
            raise RuntimeError(f"{unit} still has children in its cgroup")


def backups(chosen, view):
    """Preserve each actual executable and its imports, plus its installed unit."""
    import tarfile
    import io
    saved = {}
    for unit in chosen:
        rev = view["api_revision" if unit == API else "host_revision"]
        folder = lc.STATE / "broker" / "service-backups" / rev
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = lc.USER_UNIT_DIR / unit
        text = path.read_text()
        # gen-units can already have installed the target rendering during
        # an update. Preserve the template the old process actually used.
        prefix = update.prefix()
        template = "systemd/" + ("session/" if view["api_pid"] != view["host_pid"] else "") + unit
        source = update.show(rev, prefix + template)
        if source:
            text = units.render(source)
        if not (folder / "source.ready").exists():
            raw = update.git("archive", rev, "--", "bin", "lib", text=False).stdout
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                archive.extractall(folder, members=[m for m in archive if m.isfile() or m.isdir()])
            (folder / "source.ready").write_text(rev + "\n")
        (folder / unit).write_text(text)
        saved[unit] = text.replace(f"{lc.ROOT}/bin/session-", f"{folder}/bin/session-")
    return saved


def restore(old_split, attempted_split, old, chosen, saved):
    if API in chosen:
        drain(API)
    if (attempted_split or old_split) and HOST in chosen:
        drain(HOST)
    install_units(old_split, chosen)
    for unit, text in saved.items():
        dest = lc.USER_UNIT_DIR / unit
        tmp = dest.with_suffix(".restore")
        tmp.write_text(text)
        tmp.replace(dest)
    ctl("daemon-reload")
    if old_split and HOST in chosen:
        ctl("start", HOST)
    if API in chosen:
        ctl("start", API)
    healthy(old_split, old, chosen)
    marker = units.split_marker()
    if old_split:
        marker.touch()
    else:
        marker.unlink(missing_ok=True)


def record_change(path, record):
    tmp = path.with_suffix(".new")
    tmp.write_text(json.dumps(record))
    tmp.replace(path)


def replace(mode):
    """Called under the live checkout lock. Return the units that actually moved."""
    marker = units.split_marker()
    for guarded, name in ((True, "replacing-host.json"), (False, "replacing-api.json")):
        pending = marker.parent / name
        if pending.exists():
            with fence.admission(exclusive=True) if guarded else nullcontext(True) as acquired:
                if not acquired:
                    raise Deferred("another session operation owns admission")
                previous = json.loads(pending.read_text())
                restore(previous["was_split"], previous["split"], previous["old"],
                        set(previous["chosen"]), previous["saved"])
                pending.unlink()
    was_split = marker.exists()
    split = was_split if mode != "combined" else False
    if mode == "split":
        split = True
    view = loam_live.broker_live()
    if not view or not view.get("api_pid"):
        raise Deferred("the running API cannot identify its session owner")
    old = {API: view["api_pid"], HOST: view["host_pid"]}
    if was_split != (old[API] != old[HOST]):
        raise Deferred("the installed and running session topology disagree")
    changing = split != was_split
    if changing or mode == "host":
        chosen = {API, HOST} if changing else {HOST}
    elif mode == "api":
        chosen = {API}
    else:
        chosen = selected(view, update.head())
    if not chosen:
        return "no session service reads the change"
    guard = HOST in chosen or not was_split
    if not split:
        chosen = {API, HOST}
    saved_units = chosen if was_split else {API}
    with fence.admission(exclusive=True) if guard else nullcontext(True) as acquired:
        if not acquired:
            raise Deferred("an admission or a thread turn is in flight")
        if guard:
            idle()
        saved = backups(saved_units, view)
        pending = marker.parent / ("replacing-host.json" if guard else "replacing-api.json")
        record_change(pending, {"was_split": was_split, "split": split, "old": old,
                                "chosen": sorted(chosen), "saved": saved})
        try:
            if changing:
                drain(API)
                if was_split:
                    drain(HOST)
            install_units(split, chosen)
            if split and HOST in chosen:
                ctl("restart", HOST)
            if API in chosen:
                ctl("restart", API)
            after = healthy(split, old, chosen)
        except Exception:
            # Keep the fence through rollback. An API-only failure cannot stop the host.
            restore(was_split, split, old, chosen, saved)
            pending.unlink()
            raise
        if split:
            marker.touch()
        else:
            marker.unlink(missing_ok=True)
        pending.unlink()
        return (f"restarted {', '.join(sorted(chosen if split else {API}))}, "
                f"API PID {after['api_pid']}, host PID {after['host_pid']}")
