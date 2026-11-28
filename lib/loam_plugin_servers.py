"""Supervise each enabled server plugin in its own transient user unit. Give it a
loopback port, plugin-specific state and resource limits. The authenticated gateway
routes requests to that unit, waking on-demand plugins on first request and
putting them back to sleep after a quiet period. Confine fixture reconciliation
to its isolated environment.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import loam_config as lc

import loam_broker as cfg
import loam_plugin_env as plugin_env

UNIT_PREFIX = "loam-plugin-"
GATEWAY_UNIT = "loam-plugins-gateway.service"
GATEWAY_PORT = 8933                 # bin/plugin-gateway, on 127.0.0.1
DOOR_PORT = 8936                    # its share door, /s/*, without the login (2026-11-16)
# Files whose content determines what the running plugin-gateway executes or reads.
GATEWAY_CODE_FILES = (
    "bin/plugin-gateway",
    "bin/plugin-servers",
    "lib/loam_plugin_env.py",
    "lib/loam_plugin_servers.py",
    "lib/loam_plugins.py",
    "lib/loam_contributions.py",
    "lib/loam_config.py",
    "lib/loam_broker.py",
    "systemd/loam-plugins-gateway.service",
)
PORTS = range(9100, 9300)           # front ports: stable across restarts (LOAM_PLUGIN_URL)
BACKEND_PORTS = range(9300, 9500)   # backend ports for on-demand servers behind plugin-gateway
BACKEND_PORT_OFFSET = 200
MEMORY_MAX = "512M"
TASKS_MAX = 256
OOM_SCORE = 700                     # after agents (800), before core (100); lib/loam_config.py
RESTART_SEC = 2
BURST, BURST_WINDOW = 5, 60         # starts within seconds, then `failing`
LOG_LINES = 20
PROPS = ("LoadState", "ActiveState", "SubState", "MainPID", "NRestarts",
         "Result", "ActiveEnterTimestamp", "ExecMainStatus")
_API = 0
_DATA_DIR = "_data"
_SETTINGS_DIR = "_settings"


class ServerError(Exception):
    pass


def _yours_root() -> Path:
    env = os.environ.get("LOAM_PLUGINS_DIR")
    return Path(env) if env else lc.PLUGINS


def managed() -> bool:
    flag = os.environ.get("LOAM_PLUGIN_UNITS")
    if flag is not None:
        return flag == "1"
    import loam_offline  # noqa: PLC0415
    if loam_offline.offline():
        return False
    return (lc.is_install_config()
            and _yours_root().resolve() == lc.PLUGINS.resolve())


def unit(pid: str) -> str:
    return f"{UNIT_PREFIX}{pid}.service"


def state_dir() -> Path:
    env = os.environ.get("LOAM_PLUGIN_STATE")
    return Path(env) if env else lc.STATE / "plugins"


def ports_path() -> Path:
    return state_dir() / "ports.json"


def gateway_pid_path() -> Path:
    return state_dir() / "gateway.pid"


def gateway_running_path() -> Path:
    return state_dir() / "gateway.running.json"


def gateway_state_path() -> Path:
    return state_dir() / "gateway.json"


def gateway_code_fingerprint(root: Path | None = None) -> str:
    """Content digest of bin/plugin-gateway, its unit, and the modules that
    build or read gateway.json, used to detect a stale running gateway."""
    base = root or lc.ROOT
    h = hashlib.sha256()
    for rel in GATEWAY_CODE_FILES:
        h.update(rel.encode("utf-8") + b"\0")
        try:
            h.update((base / rel).read_bytes())
        except OSError:
            h.update(b"<missing>")
        h.update(b"\0")
    return h.hexdigest()


def write_gateway_running(
    pid: int | None = None,
    fingerprint: str | None = None,
    root: Path | None = None,
) -> dict:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    doc = {
        "pid": os.getpid() if pid is None else pid,
        "fingerprint": gateway_code_fingerprint(root) if fingerprint is None else fingerprint,
    }
    path = gateway_running_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return doc


def read_gateway_running() -> dict | None:
    try:
        data = json.loads(gateway_running_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_gateway_state(want: dict[str, dict], door_owner: str | None) -> dict:
    """Persist lightweight gateway routing/wake state so bin/plugin-gateway
    never needs to import PyYAML or loam_plugins into its resident heap."""
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    doc = {"wanted": want, "door_owner": door_owner}
    tmp = gateway_state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n",
                   encoding="utf-8")
    os.replace(tmp, gateway_state_path())
    return doc


def read_gateway_state() -> dict | None:
    try:
        data = json.loads(gateway_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def gateway_state_stale() -> bool:
    """True when gateway.json is absent or older than plugin switch/port/manifest files."""
    path = gateway_state_path()
    try:
        gm = path.stat().st_mtime
    except OSError:
        return True
    yours = _yours_root()
    shipped = lc.ROOT / "plugins"
    sources = [
        ports_path(), yours, yours / "installed.json", shipped,
        lc.ROOT / "bin" / "plugin-servers",
        lc.ROOT / "lib" / "loam_plugin_servers.py",
        lc.ROOT / "lib" / "loam_plugins.py",
    ]
    for folder in (yours, shipped):
        try:
            if folder.is_dir():
                for child in folder.iterdir():
                    if child.is_dir() and not child.name.startswith("_"):
                        sources.append(child / "PLUGIN.md")
        except OSError:
            pass
    for src in sources:
        try:
            if src.stat().st_mtime > gm:
                return True
        except OSError:
            pass
    return False


def _gateway_unit_needs_render(root: Path | None = None) -> bool:
    base = root or lc.ROOT
    installed = lc.USER_UNIT_DIR / GATEWAY_UNIT
    template = base / "systemd" / GATEWAY_UNIT
    if not installed.is_file() or not template.is_file():
        return False
    try:
        import loam_units  # noqa: PLC0415
        want = loam_units.render(template.read_text(encoding="utf-8"))
        return installed.read_text(encoding="utf-8") != want
    except OSError:
        return False


def gateway_unit_status(root: Path | None = None) -> dict:
    """Report whether loam-plugins-gateway.service is inactive, current, or stale."""
    done = run(["systemctl", "--user", "show", "--no-pager",
                "-p", "ActiveState,MainPID", GATEWAY_UNIT])
    props: dict[str, str] = {}
    for line in (done.stdout or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            props[k] = v
    active = props.get("ActiveState", "")
    try:
        pid = int(props.get("MainPID") or 0)
    except ValueError:
        pid = 0
    if active != "active" or pid <= 0:
        return {"state": "inactive", "active": active or "unknown", "pid": pid,
                "reason": "not active", "unit_drift": False}
    unit_drift = _gateway_unit_needs_render(root)
    expected = gateway_code_fingerprint(root)
    rec = read_gateway_running()
    if not rec:
        return {"state": "stale", "active": active, "pid": pid,
                "reason": "running gateway predates fingerprint tracking",
                "unit_drift": unit_drift}
    if rec.get("pid") != pid:
        return {"state": "stale", "active": active, "pid": pid,
                "reason": f"gateway PID mismatch (unit {pid}, recorded {rec.get('pid')})",
                "unit_drift": unit_drift}
    if rec.get("fingerprint") != expected:
        return {"state": "stale", "active": active, "pid": pid,
                "reason": "gateway code on disk changed since the running process started",
                "unit_drift": unit_drift}
    if unit_drift:
        return {"state": "stale", "active": active, "pid": pid,
                "reason": f"{GATEWAY_UNIT} template changed since installed",
                "unit_drift": True}
    return {"state": "current", "active": active, "pid": pid,
            "reason": "ok", "unit_drift": False}


def wait_gateway_current(wait_s: float = 5.0, root: Path | None = None) -> dict:
    deadline = time.monotonic() + max(0.0, wait_s)
    info = gateway_unit_status(root)
    while info["state"] != "current" and time.monotonic() < deadline:
        time.sleep(0.05)
        info = gateway_unit_status(root)
    return info


def _update_in_progress() -> bool:
    """True while `loam update` holds its run.lock (non-blocking check)."""
    lock_path = Path(os.environ.get("LOAM_UPDATE_STATE") or lc.STATE / "update") / "run.lock"
    if not lock_path.exists():
        return False
    try:
        with open(lock_path, "a+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError:
        return False
    return False


def ensure_gateway_current(
    *,
    wait_s: float = 5.0,
    reconcile_after: bool = False,
    root: Path | None = None,
) -> dict:
    """Restart loam-plugins-gateway.service if active and running stale code.
    Re-renders the unit file via bin/gen-units first when its template drifted."""
    if not managed():
        return {"action": "unmanaged"}
    info = gateway_unit_status(root)
    if info["state"] == "inactive":
        return {"action": "inactive", "active": info["active"]}
    if info["state"] == "current":
        return {"action": "current", "pid": info["pid"]}
    base = root or lc.ROOT
    if info.get("unit_drift"):
        gen = base / "bin" / "gen-units"
        if gen.is_file():
            run([sys.executable, str(gen), GATEWAY_UNIT])
    done = run(["systemctl", "--user", "try-restart", GATEWAY_UNIT])
    if done.returncode != 0:
        err = (done.stderr or done.stdout or "").strip() or f"exit {done.returncode}"
        return {"action": "failed", "before_pid": info["pid"],
                "reason": info["reason"], "error": err}
    after = wait_gateway_current(wait_s=wait_s, root=root)
    verified = after["state"] == "current"
    out: dict = {
        "action": "restarted" if verified else "failed",
        "before_pid": info["pid"],
        "pid": after["pid"],
        "verified": verified,
        "reason": info["reason"],
    }
    if not verified:
        out["error"] = f"gateway did not verify current after restart ({after['reason']})"
    elif reconcile_after:
        out["reconcile"] = reconcile()
    return out


def refresh_gateway_state() -> dict:
    import loam_contributions  # noqa: PLC0415
    import loam_plugins as plugins  # noqa: PLC0415
    want = plugins.wanted_servers()
    try:
        owner = loam_contributions.door_owner()
    except Exception:
        owner = None
    return write_gateway_state(want, owner)


def notify_gateway() -> None:
    """Poke bin/plugin-gateway (if running) to sync listeners immediately."""
    try:
        pid = int(gateway_pid_path().read_text(encoding="utf-8").strip())
        if pid > 1:
            os.kill(pid, signal.SIGHUP)
    except (OSError, ValueError):
        pass


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


def _read_ports_doc() -> dict:
    try:
        data = json.loads(ports_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_ports_doc(ports: dict[str, int], backend_ports: dict[str, int]) -> None:
    doc: dict = {"ports": ports}
    if backend_ports:
        doc["backend_ports"] = backend_ports
    tmp = ports_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, ports_path())


def read_ports() -> dict[str, int]:
    """``{id: port}``; front port per plugin (LOAM_PLUGIN_URL and gateway table)."""
    ports = _read_ports_doc().get("ports")
    return {k: v for k, v in (ports or {}).items()
            if isinstance(k, str) and isinstance(v, int)}


def read_backend_ports() -> dict[str, int]:
    """``{id: port}``; backend port per on-demand plugin server."""
    ports = _read_ports_doc().get("backend_ports")
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
    """The plugin's front port: the one it had, or the first free one."""
    with _locked():
        ports = read_ports()
        backend = read_backend_ports()
        if pid in ports:
            return ports[pid]
        taken = set(ports.values()) | set(backend.values())
        for port in PORTS:
            if port not in taken and _free(port):
                ports[pid] = port
                _write_ports_doc(ports, backend)
                return port
    raise ServerError(f"no free port in {PORTS.start}-{PORTS.stop - 1}")


def backend_port_for(pid: str) -> int:
    """The plugin's backend port when sleeping on demand behind plugin-gateway."""
    front = port_for(pid)
    with _locked():
        ports = read_ports()
        backend = read_backend_ports()
        if pid in backend:
            return backend[pid]
        taken = set(ports.values()) | set(backend.values())
        preferred = front + BACKEND_PORT_OFFSET
        candidates = ([preferred] if preferred in BACKEND_PORTS else []) + [
            p for p in BACKEND_PORTS if p != preferred
        ]
        for port in candidates:
            if port not in taken and _free(port):
                backend[pid] = port
                _write_ports_doc(ports, backend)
                return port
    raise ServerError(f"no free backend port in {BACKEND_PORTS.start}-{BACKEND_PORTS.stop - 1}")


def listen_port_for(pid: str, want: dict) -> int:
    """Port the plugin process itself binds: front port if always_on, backend port if on-demand."""
    front = port_for(pid)
    if want.get("always_on"):
        return front
    return backend_port_for(pid)


def _env() -> dict:
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def run(args: list[str]) -> subprocess.CompletedProcess:
    """One systemctl, systemd-run or journalctl call. A test replaces it."""
    return subprocess.run(args, capture_output=True, text=True, timeout=60,
                          env=_env())


def environment(pid: str, want: dict, port: int) -> dict[str, str]:
    yours = _yours_root()
    data = yours / _DATA_DIR / pid
    settings_dir = yours / _SETTINGS_DIR / pid
    data.mkdir(parents=True, exist_ok=True)
    settings_dir.mkdir(parents=True, exist_ok=True)
    private = state_dir() / "private" / pid
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
        # Since 2026-11-18 (shared keys): the file of its declared providers'
        # keys, resolved; absent when it declares none (lib/loam_keys.py).
        "LOAM_PLUGIN_KEYS": str(private / "keys.env"),
        "LOAM_PLUGIN_FACTS": str(lc.ROOT / "bin" / "plugin-facts"),
        "LOAM_LIB": str(lc.ROOT / "lib"),
        "LOAM_BROKER_URL": f"http://{cfg.BIND_ADDR}:{cfg.PORT}",
        "LOAM_BROKER_TOKEN_FILE": str(cfg.TOKEN_PATH),
        "LOAM_API": str(_API),
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
    return f"{lc.vault_folder('system')}/Loam/data/{pid}"


def argv(pid: str, want: dict, port: int) -> list[str]:
    restart = "always" if want.get("always_on") else "on-failure"
    out = ["systemd-run", "--user", f"--unit={unit(pid)}",
           f"--description=Loam plugin server: {pid}",
           f"--property=WorkingDirectory={want['folder']}",
           f"--property=MemoryMax={MEMORY_MAX}",
           "--property=MemorySwapMax=0",
           f"--property=TasksMax={TASKS_MAX}",
           f"--property=OOMScoreAdjust={OOM_SCORE}",
           "--property=NoNewPrivileges=yes",
           f"--property=Restart={restart}",
           f"--property=RestartSec={RESTART_SEC}",
           "--property=SuccessExitStatus=SIGTERM 143",
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


def unit_port(pid: str) -> int | None:
    """PORT configured in a loaded unit's environment, or None if unavailable."""
    done = run(["systemctl", "--user", "show", "--no-pager", "-p", "Environment", unit(pid)])
    if done.returncode != 0:
        return None
    for token in (done.stdout or "").split():
        if token.startswith("Environment="):
            token = token[len("Environment="):]
        if token.startswith("PORT="):
            try:
                return int(token.split("=", 1)[1])
            except ValueError:
                return None
    return None


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


def status(
    pid: str,
    props: dict | None = None,
    with_logs: bool = False,
    *,
    wanted: bool | None = None,
    always_on: bool | None = None,
) -> dict:
    props = props if props is not None else _show([pid]).get(pid, {})
    state = _state(props)
    if state == "stopped" and wanted is True and always_on is False:
        state = "asleep"
    out = {"state": state, "unit": unit(pid), "port": read_ports().get(pid),
           "url": f"/x/{pid}/", "pid": None, "restarts": 0, "since": None,
           "memory_max": MEMORY_MAX}
    if always_on is not None:
        out["always_on"] = bool(always_on)
    try:
        out["pid"] = int(props.get("MainPID") or 0) or None
        out["restarts"] = int(props.get("NRestarts") or 0)
    except ValueError:
        pass
    if state not in ("stopped", "asleep"):
        out["since"] = props.get("ActiveEnterTimestamp") or None
    if with_logs or state == "failing":
        out["log"] = logs(pid)
    return out


def _log_error_line(lines: list[str]) -> str | None:
    """Pick the most informative recent error line from a failing unit's log."""
    cleaned = [ln.strip() for ln in lines if ln.strip()]
    if not cleaned:
        return None
    for ln in reversed(cleaned):
        if any(tok in ln for tok in (
            "Error:", "Exception:", "No such file", "not found",
            "Permission denied", "FATAL", "Cannot find module", "ModuleNotFoundError",
        )):
            return ln[:200]
    for ln in reversed(cleaned):
        if not ln.startswith(("Failed with result", "Main process exited", "Started ", "Stopped ")):
            return ln[:200]
    return cleaned[-1][:200]


def server_problems(
    want: dict[str, dict] | None = None,
    shown: dict[str, dict] | None = None,
) -> list[dict]:
    """Enabled plugin servers that have failed or cannot start on this host.

    Read-only: inspects every enabled server plugin (including sleeping
    on-demand servers) without waking or mutating anything.
    """
    if want is None:
        import loam_plugins as plugins  # noqa: PLC0415
        want = plugins.wanted_servers()
    if not want:
        return []
    if shown is None:
        shown = _show(sorted(want)) if managed() else {}
    problems: list[dict] = []
    for pid, spec in sorted(want.items()):
        props = shown.get(pid, {})
        always_on = bool(spec.get("always_on"))
        st = _state(props)
        if st == "stopped" and not always_on:
            st = "asleep"
        env = plugin_env.inspect(spec["folder"], spec["run"])
        name = spec.get("name") or pid
        if not env["ok"]:
            rep_err = plugin_env.last_repair_error(pid, spec["folder"], spec["run"])
            reason = plugin_env.format_reason(env, rep_err)
            problems.append({
                "id": pid,
                "name": name,
                "state": st,
                "code": env.get("code") or "env_broken",
                "reason": reason,
                "repairable": bool(env.get("repairable") and not rep_err),
                "unit": unit(pid),
                "folder": str(spec["folder"]),
            })
        elif st == "failing":
            tail = _log_error_line(logs(pid))
            reason = (f"its server crashed and stopped retrying ({tail})"
                      if tail else "its server crashed and stopped retrying")
            problems.append({
                "id": pid,
                "name": name,
                "state": st,
                "code": "unit_failed",
                "reason": reason,
                "repairable": False,
                "unit": unit(pid),
                "folder": str(spec["folder"]),
            })
    return problems


def annotate(rows: list[dict], logs: bool = False) -> list[dict]:
    """Each row that has a server gets ``service``: whether it should run
    (``wanted``), its state (running, asleep, starting, failing, stopping,
    stopped), port, address, process, restarts and, when failing or asked,
    its last log lines. One systemctl call for the whole list."""
    import loam_plugins as plugins  # noqa: PLC0415
    have = [r for r in rows if plugins.server_spec(r)]
    if not have:
        return rows
    if not managed():
        for r in have:
            spec = plugins.server_spec(r)
            r["service"] = {"state": "unmanaged", "wanted": False,
                            "always_on": bool(spec.get("always_on")),
                            "run": spec["run"], "url": f"/x/{r['id']}/",
                            "why": "server plugins are run on the box's own "
                                   "plugin folder only"}
        return rows
    effective = [r for r in have if r["effective"]]
    shown = _show([r["id"] for r in effective]) if effective else {}
    for r in have:
        spec = plugins.server_spec(r)
        if not r["effective"]:
            r["service"] = {"state": "stopped", "wanted": False,
                            "always_on": bool(spec.get("always_on")),
                            "run": spec["run"],
                            "url": None, "why": "another copy of this id is in force"}
            continue
        wanted = bool(r.get("on") and r.get("switch") == "plugin")
        always_on = bool(spec.get("always_on"))
        svc = status(r["id"], shown.get(r["id"], {}), with_logs=logs,
                     wanted=wanted, always_on=always_on)
        svc["wanted"] = wanted
        svc["run"] = spec["run"]
        if wanted:
            env = plugin_env.inspect(r["folder"], spec["run"])
            if not env["ok"]:
                rep_err = plugin_env.last_repair_error(r["id"], r["folder"], spec["run"])
                svc["env_problem"] = plugin_env.format_reason(env, rep_err)
        r["service"] = svc
    return rows


def stop(pid: str) -> dict:
    """Stop its unit and forget a failed one; quiet when there is none."""
    if not managed():
        return {"state": "unmanaged"}
    run(["systemctl", "--user", "stop", unit(pid)])
    run(["systemctl", "--user", "reset-failed", unit(pid)])
    notify_gateway()
    return status(pid)


def prepare_on_demand(pid: str, want: dict, reset: bool = False) -> dict:
    """Allocate front/backend ports for an on-demand plugin without starting it.
    With ``reset``, stop any running instance so the next request wakes fresh."""
    if not managed():
        return {"state": "unmanaged"}
    port_for(pid)
    backend_port_for(pid)
    if reset:
        run(["systemctl", "--user", "stop", unit(pid)])
        run(["systemctl", "--user", "reset-failed", unit(pid)])
    notify_gateway()
    return status(pid, wanted=True, always_on=False)


def start(pid: str, want: dict, fresh: bool = False, port: int | None = None) -> dict:
    """Start it if it is not running; with ``fresh``, stop it first (an
    update, a restart, a failing unit given another go)."""
    if not managed():
        return {"state": "unmanaged"}
    props = _show([pid]).get(pid, {})
    state = _state(props)
    if state in ("running", "starting") and not fresh:
        return status(pid, props, wanted=True, always_on=bool(want.get("always_on")))
    env_res = plugin_env.ensure_ready(pid, want["folder"], want["run"], force_retry=fresh)
    if not env_res["ok"]:
        raise ServerError(f"{pid}'s server cannot start: {env_res['reason']}")
    if props.get("LoadState") == "loaded":
        run(["systemctl", "--user", "stop", unit(pid)])
        run(["systemctl", "--user", "reset-failed", unit(pid)])
    listen_port = port if port is not None else listen_port_for(pid, want)
    done = run(argv(pid, want, listen_port))
    if done.returncode != 0:
        raise ServerError(f"systemd-run refused {unit(pid)}: "
                          f"{(done.stderr or done.stdout).strip()[-400:]}")
    notify_gateway()
    return status(pid, wanted=True, always_on=bool(want.get("always_on")))


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


def _folders(pids: list[str]) -> dict[str, str]:
    """The folder each loaded unit runs in (its WorkingDirectory)."""
    if not pids:
        return {}
    done = run(["systemctl", "--user", "show", "--no-pager", "-p", "WorkingDirectory",
                *[unit(p) for p in pids]])
    blocks = done.stdout.strip("\n").split("\n\n") if done.stdout.strip() else []
    out = {}
    for pid, block in zip(pids, blocks):
        for line in block.splitlines():
            if line.startswith("WorkingDirectory="):
                out[pid] = line.split("=", 1)[1]
    return out


def reconcile() -> dict:
    """Reconcile plugin servers:
    - Always-on wanted servers are started if not loaded (and restarted if moved).
    - On-demand wanted servers get their front/backend ports allocated and stay
      asleep until woken by plugin-gateway (if a pre-upgrade unit is still
      holding the front port or running from a stale folder, stop it cleanly).
    - Loaded units no longer wanted are stopped.
    """
    if not managed():
        return {"managed": False, "started": [], "stopped": [], "asleep": []}
    # The contributed timers too (2026-11-14, first release L4): a plugin
    # edited in place, or a timer someone removed by hand, comes back into
    # line within five minutes. Never stops a standing timer to do it.
    try:
        import loam_contributions  # noqa: PLC0415
        loam_contributions.apply_units()
    except Exception:  # noqa: BLE001 -- the servers' reconcile never fails for it
        pass
    # And each plugin's resolved keys (2026-11-18, lib/loam_keys.py): a
    # plugin edited in place to declare a provider gets its key within five
    # minutes, and one that stopped declaring it loses it.
    try:
        import loam_keys  # noqa: PLC0415
        loam_keys.adopt()
        loam_keys.sync()
    except Exception:  # noqa: BLE001
        pass
    import loam_plugins as plugins  # noqa: PLC0415
    want = plugins.wanted_servers()
    door_owner = None
    try:
        import loam_contributions  # noqa: PLC0415
        door_owner = loam_contributions.door_owner()
    except Exception:  # noqa: BLE001
        pass
    started, stopped, asleep, moved, repaired, errors, env_problems = [], [], [], [], [], {}, {}
    can_sleep_front = True
    gateway_action: str | None = None
    gw_info = gateway_unit_status()
    if gw_info["state"] == "stale":
        if os.environ.get("LOAM_UPDATE_GATEWAY_STEP") == "1":
            # loam update's gateway step just restarted the gateway; wait for
            # it to boot and write its fingerprint before touching front ports.
            after = wait_gateway_current(wait_s=5.0)
            if after["state"] == "current":
                gateway_action = "current"
            else:
                can_sleep_front = False
                gateway_action = "stale"
                errors["gateway"] = f"restarted gateway did not verify current ({after['reason']})"
        elif _update_in_progress():
            # Mid-update (e.g. timer fired before loam update's gateway step):
            # leave front-port servers running until loam update restarts the gateway.
            can_sleep_front = False
            gateway_action = "deferred-update"
        else:
            gw_res = ensure_gateway_current()
            gateway_action = gw_res["action"]
            if gw_res["action"] not in ("current", "restarted"):
                can_sleep_front = False
                errors["gateway"] = gw_res.get("error") or gw_res.get("reason") or "gateway stale"
    loaded = set(loaded_units())
    for pid, spec in sorted(want.items()):
        try:
            env_res = plugin_env.ensure_ready(
                pid, spec["folder"], spec["run"],
                cooldown_s=plugin_env.DEFAULT_REPAIR_COOLDOWN_S,
            )
            if env_res.get("repaired"):
                repaired.append(pid)
                if pid in loaded and _state(_show([pid]).get(pid, {})) == "failing":
                    if spec.get("always_on"):
                        start(pid, spec, fresh=True)
                        started.append(pid)
                    else:
                        stop(pid)
                        loaded.discard(pid)
            elif not env_res["ok"]:
                env_problems[pid] = env_res["reason"]

            front = port_for(pid)
            if spec.get("always_on"):
                if pid not in loaded and env_res["ok"]:
                    start(pid, spec)
                    started.append(pid)
            else:
                bport = backend_port_for(pid)
                if pid not in loaded:
                    asleep.append(pid)
                elif can_sleep_front:
                    # Stop pre-upgrade units still holding the front port so
                    # plugin-gateway can bind it and wake on demand.
                    uport = unit_port(pid)
                    if uport is not None and uport == front and uport != bport:
                        stop(pid)
                        stopped.append(pid)
                        loaded.discard(pid)
                        asleep.append(pid)
        except Exception as exc:  # noqa: BLE001 -- one plugin never stops the rest
            errors[pid] = str(exc)
    # A loaded unit that runs from another folder than its plugin's is not
    # this plugin's server: a test's scratch copy started under the real
    # unit name, then its folder removed (2026-11-19, Dictation and Read
    # aloud, until someone restarted them by hand).
    for pid, folder in sorted(_folders(sorted(set(want) & loaded)).items()):
        if folder and folder != str(want[pid]["folder"]):
            try:
                if want[pid].get("always_on"):
                    start(pid, want[pid], fresh=True)
                else:
                    stop(pid)
                    loaded.discard(pid)
                    if pid not in asleep:
                        asleep.append(pid)
                moved.append(pid)
            except Exception as exc:  # noqa: BLE001
                errors[pid] = str(exc)
    for pid in sorted(loaded - set(want)):
        stop(pid)
        stopped.append(pid)
    try:
        write_gateway_state(want, door_owner)
    except OSError:
        pass
    notify_gateway()
    out = {"managed": True, "started": started, "stopped": stopped,
           "asleep": sorted(asleep), "moved": moved, "repaired": repaired,
           "errors": errors,
           "running": sorted(set(want) & (loaded | set(started)))}
    if env_problems:
        out["env_problems"] = env_problems
    if gateway_action is not None:
        out["gateway"] = gateway_action
    return out
