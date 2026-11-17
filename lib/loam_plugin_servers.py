"""Server plugins, supervised beside Loam and never inside the broker
(2026-11-13; the owner, m259: "its own program beside Loam ... a crash, install
or update touches only that plugin, never a conversation").

**One plugin, one transient user unit**, ``loam-plugin-<id>.service``, made
by ``systemd-run --user`` exactly as an engineer job is (bin/engineer-job),
so it is the box's existing pattern: its own cgroup under the user manager,
never the broker's (loam-session-broker.service), so a plugin's crash, its
memory and its restart are its own. Started when the plugin is on and has a
server; stopped when it is turned off; stopped before its folder is removed;
started fresh after an update. ``loam_plugins`` calls this module at each of
those; ``reconcile()`` (``bin/plugin-servers reconcile``, on
``loam-plugins-reconcile.timer`` at boot and every five minutes) starts what
should run and is not loaded (after a reboot or a user-manager restart),
and stops what runs and should not. It never restarts a unit that failed:
a crash loop stays visible until someone acts.

**What a unit gets**: its command (the manifest's ``server.run``, or
``python3 server/main.py`` / ``node server/main.js``) run by ``/bin/sh -c``
in the plugin's folder; a loopback port of its own, kept across restarts
(``state/plugins/ports.json``); and this environment:

  PORT, LOAM_PLUGIN_PORT   the port to listen on, on 127.0.0.1
  LOAM_PLUGIN_ID           its id
  LOAM_PLUGIN_DIR          its folder
  LOAM_PLUGIN_BASE         /x/<id>/, where the app reaches it
  LOAM_PLUGIN_DATA         Yours/_data/<id>/, its own files (made here)
  LOAM_PLUGIN_SETTINGS     Yours/_settings/<id>/<its settings file>
  LOAM_BROKER_URL          the broker, as the app reaches it
  LOAM_BROKER_TOKEN_FILE   the file holding the broker's token (a path, so
                           the token is never a unit property anyone can
                           read with `systemctl show`)
  LOAM_API                 the plugin interface version

**Limits**: ``MemoryMax=512M`` and no swap each (``MEMORY_MAX``; the box is
32 GB and a plugin server is a small program; a wake is 1.5 GB), 256 tasks,
``NoNewPrivileges`` (this user has sudo; a plugin never does), and an OOM
score between the agents' and the core's, so the kernel takes a plugin
after any agent run and before the broker.
``Restart=always`` after 2 s; five starts within 60 s and systemd gives up,
which is ``failing`` with the last log lines on the plugin's row.

**The address**: Caddy sends ``/x/*`` on the app's host, behind the same
login, to ``bin/plugin-gateway`` (127.0.0.1:8933, its own unit), which reads
the port table and passes the request to the plugin's port with the prefix
taken off. The broker never carries a plugin's traffic.

``LOAM_PLUGIN_UNITS=1`` turns the units on wherever Yours is; by default
they are managed only when Yours is the box's own folder, so a test run with
``LOAM_PLUGINS_DIR`` in a temporary folder never starts a unit.
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
import subprocess
from contextlib import contextmanager
from pathlib import Path

import loam_config as lc

import loam_broker as cfg
import loam_plugins as plugins

UNIT_PREFIX = "loam-plugin-"
GATEWAY_PORT = 8933                 # bin/plugin-gateway, on 127.0.0.1
PORTS = range(9100, 9300)           # one per plugin, kept across restarts
MEMORY_MAX = "512M"
TASKS_MAX = 256
OOM_SCORE = 700                     # after agents (800), before core (100); lib/loam_config.py
RESTART_SEC = 2
BURST, BURST_WINDOW = 5, 60         # starts within seconds, then `failing`
LOG_LINES = 20
PROPS = ("LoadState", "ActiveState", "SubState", "MainPID", "NRestarts",
         "Result", "ActiveEnterTimestamp", "ExecMainStatus")


class ServerError(Exception):
    pass


def managed() -> bool:
    flag = os.environ.get("LOAM_PLUGIN_UNITS")
    if flag is not None:
        return flag == "1"
    return plugins.yours_root() == plugins.YOURS_DEFAULT


def unit(pid: str) -> str:
    return f"{UNIT_PREFIX}{pid}.service"


def state_dir() -> Path:
    env = os.environ.get("LOAM_PLUGIN_STATE")
    return Path(env) if env else lc.STATE / "plugins"


def ports_path() -> Path:
    return state_dir() / "ports.json"


@contextmanager
def _locked():
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "ports.lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def read_ports() -> dict[str, int]:
    """``{id: port}``; what bin/plugin-gateway routes by."""
    try:
        data = json.loads(ports_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    ports = data.get("ports") if isinstance(data, dict) else None
    return {k: v for k, v in (ports or {}).items()
            if isinstance(k, str) and isinstance(v, int)}


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def port_for(pid: str) -> int:
    """The plugin's port: the one it had, or the first free one."""
    with _locked():
        ports = read_ports()
        if pid in ports:
            return ports[pid]
        taken = set(ports.values())
        for port in PORTS:
            if port not in taken and _free(port):
                ports[pid] = port
                tmp = ports_path().with_suffix(".tmp")
                tmp.write_text(json.dumps({"ports": ports}, indent=2, sort_keys=True)
                               + "\n", encoding="utf-8")
                os.replace(tmp, ports_path())
                return port
    raise ServerError(f"no free port in {PORTS.start}-{PORTS.stop - 1}")


def _env() -> dict:
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def run(args: list[str]) -> subprocess.CompletedProcess:
    """One systemctl, systemd-run or journalctl call. A test replaces it."""
    return subprocess.run(args, capture_output=True, text=True, timeout=60,
                          env=_env())


def environment(pid: str, want: dict, port: int) -> dict[str, str]:
    yours = plugins.yours_root()
    data = yours / plugins.DATA_DIR / pid
    settings_dir = yours / plugins.SETTINGS_DIR / pid
    data.mkdir(parents=True, exist_ok=True)
    settings_dir.mkdir(parents=True, exist_ok=True)
    import loam_contributions  # noqa: PLC0415
    private = loam_contributions.private_dir(pid)
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = Path(want.get("settings") or "settings.md").name
    return {
        "PORT": str(port), "LOAM_PLUGIN_PORT": str(port),
        "LOAM_PLUGIN_ID": pid, "LOAM_PLUGIN_DIR": str(want["folder"]),
        "LOAM_PLUGIN_BASE": f"/x/{pid}/",
        "LOAM_PLUGIN_DATA": str(data),
        "LOAM_PLUGIN_SETTINGS": str(settings_dir / name),
        # Since 2026-11-14 (first release L5), as a contributed process has:
        # the settings folder, the machine-local private folder, Loam's lib.
        "LOAM_PLUGIN_SETTINGS_DIR": str(settings_dir),
        "LOAM_PLUGIN_PRIVATE": str(private),
        "LOAM_LIB": str(lc.ROOT / "lib"),
        "LOAM_BROKER_URL": f"http://{cfg.BIND_ADDR}:{cfg.PORT}",
        "LOAM_BROKER_TOKEN_FILE": str(cfg.TOKEN_PATH),
        "LOAM_API": str(plugins.API),
        # Since 2026-11-16 (doc-comments): the plugin's own folder in the
        # vault, `<system>/Loam/data/<id>`, which an update keeps
        # (lib/loam_skills.py, KEPT_DIR). Vault-relative, for the broker's
        # Files routes, and as a host path. Not made until the plugin writes.
        "LOAM_VAULT_DATA": vault_data(pid),
        "LOAM_VAULT_DATA_DIR": str(lc.VAULT / vault_data(pid)),
        "PATH": lc.UNIT_PATH,
        "PYTHONUNBUFFERED": "1",
    }


def vault_data(pid: str) -> str:
    """A plugin's folder in the vault, relative to it: under Loam's own
    folder, in the one place there an update does not replace."""
    import loam_skills  # noqa: PLC0415
    return f"{lc.vault_folder('system')}/{loam_skills.LOAM_DIR}/{loam_skills.KEPT_DIR}/{pid}"


def argv(pid: str, want: dict, port: int) -> list[str]:
    out = ["systemd-run", "--user", f"--unit={unit(pid)}",
           f"--description=Loam plugin server: {pid}",
           f"--property=WorkingDirectory={want['folder']}",
           f"--property=MemoryMax={MEMORY_MAX}",
           "--property=MemorySwapMax=0",
           f"--property=TasksMax={TASKS_MAX}",
           f"--property=OOMScoreAdjust={OOM_SCORE}",
           "--property=NoNewPrivileges=yes",
           "--property=Restart=always",
           f"--property=RestartSec={RESTART_SEC}",
           f"--property=StartLimitIntervalSec={BURST_WINDOW}",
           f"--property=StartLimitBurst={BURST}",
           "--property=StandardOutput=journal",
           "--property=StandardError=journal"]
    out += [f"--setenv={k}={v}" for k, v in environment(pid, want, port).items()]
    return out + ["--", "/bin/sh", "-c", "exec " + want["run"]]


def _show(pids: list[str]) -> dict[str, dict]:
    if not pids:
        return {}
    done = run(["systemctl", "--user", "show", "--no-pager",
                "-p", ",".join(PROPS), *[unit(p) for p in pids]])
    blocks = done.stdout.strip("\n").split("\n\n") if done.stdout.strip() else []
    out = {}
    for pid, block in zip(pids, blocks):
        props = {}
        for line in block.splitlines():
            k, _, v = line.partition("=")
            props[k] = v
        out[pid] = props
    return out


def _state(props: dict) -> str:
    load, active, sub = (props.get("LoadState"), props.get("ActiveState"),
                         props.get("SubState"))
    if load != "loaded":
        return "stopped"
    if active == "active":
        return "running"
    if active == "failed" or sub == "auto-restart":
        return "failing"
    if active in ("activating", "reloading"):
        return "starting"
    if active == "deactivating":
        return "stopping"
    return "stopped"


def logs(pid: str, lines: int = LOG_LINES) -> list[str]:
    done = run(["journalctl", "--user", "-u", unit(pid), "-n", str(lines),
                "-o", "cat", "--no-pager"])
    text = done.stdout if done.returncode == 0 else ""
    return [ln for ln in text.splitlines() if ln.strip()][-lines:]


def status(pid: str, props: dict | None = None, with_logs: bool = False) -> dict:
    props = props if props is not None else _show([pid]).get(pid, {})
    state = _state(props)
    out = {"state": state, "unit": unit(pid), "port": read_ports().get(pid),
           "url": f"/x/{pid}/", "pid": None, "restarts": 0, "since": None,
           "memory_max": MEMORY_MAX}
    try:
        out["pid"] = int(props.get("MainPID") or 0) or None
        out["restarts"] = int(props.get("NRestarts") or 0)
    except ValueError:
        pass
    if state != "stopped":
        out["since"] = props.get("ActiveEnterTimestamp") or None
    if with_logs or state == "failing":
        out["log"] = logs(pid)
    return out


def annotate(rows: list[dict], logs: bool = False) -> list[dict]:
    """Each row that has a server gets ``service``: whether it should run
    (``wanted``), its state (running, starting, failing, stopping,
    stopped), port, address, process, restarts and, when failing or asked,
    its last log lines. One systemctl call for the whole list."""
    have = [r for r in rows if plugins.server_spec(r)]
    if not have:
        return rows
    if not managed():
        for r in have:
            spec = plugins.server_spec(r)
            r["service"] = {"state": "unmanaged", "wanted": False,
                            "run": spec["run"], "url": f"/x/{r['id']}/",
                            "why": "server plugins are run on the box's own "
                                   "plugin folder only"}
        return rows
    effective = [r for r in have if r["effective"]]
    shown = _show([r["id"] for r in effective]) if effective else {}
    for r in have:
        spec = plugins.server_spec(r)
        if not r["effective"]:
            r["service"] = {"state": "stopped", "wanted": False, "run": spec["run"],
                            "url": None, "why": "another copy of this id is in force"}
            continue
        svc = status(r["id"], shown.get(r["id"], {}), with_logs=logs)
        svc["wanted"] = bool(r.get("on") and r.get("switch") == "plugin")
        svc["run"] = spec["run"]
        r["service"] = svc
    return rows


def stop(pid: str) -> dict:
    """Stop its unit and forget a failed one; quiet when there is none."""
    if not managed():
        return {"state": "unmanaged"}
    run(["systemctl", "--user", "stop", unit(pid)])
    run(["systemctl", "--user", "reset-failed", unit(pid)])
    return status(pid)


def start(pid: str, want: dict, fresh: bool = False) -> dict:
    """Start it if it is not running; with ``fresh``, stop it first (an
    update, a restart, a failing unit given another go)."""
    if not managed():
        return {"state": "unmanaged"}
    props = _show([pid]).get(pid, {})
    state = _state(props)
    if state in ("running", "starting") and not fresh:
        return status(pid, props)
    if props.get("LoadState") == "loaded":
        run(["systemctl", "--user", "stop", unit(pid)])
        run(["systemctl", "--user", "reset-failed", unit(pid)])
    port = port_for(pid)
    done = run(argv(pid, want, port))
    if done.returncode != 0:
        raise ServerError(f"systemd-run refused {unit(pid)}: "
                          f"{(done.stderr or done.stdout).strip()[-400:]}")
    return status(pid)


def loaded_units() -> list[str]:
    """The plugin ids whose units are loaded now, whatever their state."""
    done = run(["systemctl", "--user", "list-units", "--all", "--plain",
                "--no-legend", "--no-pager", f"{UNIT_PREFIX}*.service"])
    out = []
    for line in done.stdout.splitlines():
        name = line.split()[0] if line.split() else ""
        if name.startswith(UNIT_PREFIX) and name.endswith(".service"):
            pid = name[len(UNIT_PREFIX):-len(".service")]
            # A contributed run (`loam-plugin-<id>--<name>`, an id never
            # holds `--`) is lib/loam_contributions.py's, not a server: left
            # alone here (2026-11-14: the reconcile stopped them as unwanted).
            if "--" not in pid:
                out.append(pid)
    return out


def reconcile() -> dict:
    """Start every wanted server whose unit is not loaded; stop every loaded
    unit no longer wanted. A failed unit is loaded and is left alone."""
    if not managed():
        return {"managed": False, "started": [], "stopped": []}
    # The contributed timers too (2026-11-14, first release L4): a plugin
    # edited in place, or a timer someone removed by hand, comes back into
    # line within five minutes. Never stops a standing timer to do it.
    try:
        import loam_contributions  # noqa: PLC0415
        loam_contributions.apply_units()
    except Exception:  # noqa: BLE001 -- the servers' reconcile never fails for it
        pass
    want = plugins.wanted_servers()
    loaded = set(loaded_units())
    started, stopped, errors = [], [], {}
    for pid, spec in sorted(want.items()):
        if pid not in loaded:
            try:
                start(pid, spec)
                started.append(pid)
            except Exception as exc:  # noqa: BLE001 -- one plugin never stops the rest
                errors[pid] = str(exc)
    for pid in sorted(loaded - set(want)):
        stop(pid)
        stopped.append(pid)
    return {"managed": True, "started": started, "stopped": stopped,
            "errors": errors, "running": sorted(set(want) & (loaded | set(started)))}
