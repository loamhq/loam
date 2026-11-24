#!/usr/bin/env python3
"""Tests for stale plugin-gateway and browser-proxy detection and reconciliation."""

from __future__ import annotations

import fcntl
import http.server
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-gw-reconcile-test-"))
YOURS = TMP / "plugins"
STATE = TMP / "state"
UPDATE_STATE = TMP / "update-state"

os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ["LOAM_HOST_SETTINGS"] = str(TMP / "broker" / "settings.json")
os.environ["LOAM_PLUGIN_SHIPPED"] = ""
os.environ["LOAM_PLUGINS_DIR"] = str(YOURS)
os.environ["LOAM_PLUGIN_STATE"] = str(STATE)
os.environ["LOAM_UPDATE_STATE"] = str(UPDATE_STATE)
os.environ["LOAM_PLUGIN_UNITS"] = "1"
os.environ["LOAM_BROWSER_PROXY_MANAGED"] = "1"

sys.path.insert(0, str(ROOT / "lib"))
import loam_browser_proxy as bp  # noqa: E402
import loam_plugin_servers as S  # noqa: E402
import loam_plugins as P  # noqa: E402
import loam_update as up  # noqa: E402

results: list[bool] = []


def check(label: str, ok: bool, detail="") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:72}"
          f"{'  | ' + str(detail)[:260] if detail != '' else ''}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_plugin(pid: str, extra: str = "") -> Path:
    d = YOURS / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "PLUGIN.md").write_text(
        f"---\nid: {pid}\nname: {pid.title()}\nversion: 1.0.0\napi: 0\n"
        f"kind: [extension]\n{extra}---\n\n# {pid}\n",
        encoding="utf-8",
    )
    (d / "server").mkdir(exist_ok=True)
    (d / "server" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    return d


class FakeUserSystemd:
    def __init__(self) -> None:
        self.units: dict[str, dict[str, str]] = {}
        self.calls: list[list[str]] = []
        self.restart_fails = False
        self.next_gw_pid = 2000
        self.write_fp_on_restart = True

    def __call__(self, args: list[str]):
        self.calls.append(list(args))
        out, err, rc = "", "", 0
        if args[:3] == ["systemctl", "--user", "show"]:
            names = [a for a in args[3:] if a.endswith(".service")]
            blocks = []
            for n in names:
                props = self.units.get(n, {
                    "LoadState": "not-found", "ActiveState": "inactive",
                    "SubState": "dead", "MainPID": "0", "NRestarts": "0",
                    "Result": "success", "ActiveEnterTimestamp": "",
                    "ExecMainStatus": "0", "Environment": "",
                })
                blocks.append("\n".join(f"{k}={v}" for k, v in props.items()))
            out = "\n\n".join(blocks) + "\n"
        elif args[:3] == ["systemctl", "--user", "try-restart"]:
            unit = args[3]
            if self.restart_fails:
                rc, err = 1, "simulated restart failure"
            elif unit in self.units and self.units[unit].get("ActiveState") == "active":
                self.next_gw_pid += 1
                self.units[unit]["MainPID"] = str(self.next_gw_pid)
                if unit == S.GATEWAY_UNIT and self.write_fp_on_restart:
                    S.write_gateway_running(pid=self.next_gw_pid)
        elif args[:3] == ["systemctl", "--user", "stop"]:
            self.units.pop(args[3], None)
        elif args[:3] == ["systemctl", "--user", "reset-failed"]:
            self.units.pop(args[3], None)
        elif args[:3] == ["systemctl", "--user", "list-units"]:
            out = "".join(
                f"{n} {p['LoadState']} {p['ActiveState']} {p['SubState']} x\n"
                for n, p in self.units.items()
                if n.startswith(S.UNIT_PREFIX)
            )
        return type("Done", (), {"returncode": rc, "stdout": out, "stderr": err})()


fake = FakeUserSystemd()
real_run = S.run
S.run = fake

try:
    # Seed an enabled on-demand plugin running on its front port (pre-sleep unit).
    make_plugin("demo")
    P.set_on("demo", True)
    front = S.port_for("demo")
    bport = S.backend_port_for("demo")

    def seed_pre_sleep_demo() -> None:
        fake.units["loam-plugin-demo.service"] = {
            "LoadState": "loaded", "ActiveState": "active", "SubState": "running",
            "MainPID": "5001", "NRestarts": "0", "Result": "success",
            "ActiveEnterTimestamp": "now", "ExecMainStatus": "0",
            "WorkingDirectory": str(YOURS / "demo"),
            "Environment": f"PORT={front} LOAM_PLUGIN_PORT={front}",
        }

    # 1. Stale gateway (no gateway.running.json, like pre-ac6a2de process):
    #    reconcile() restarts gateway FIRST, then stops the pre-sleep front-port server.
    seed_pre_sleep_demo()
    fake.units[S.GATEWAY_UNIT] = {"LoadState": "loaded", "ActiveState": "active",
                                  "SubState": "running", "MainPID": "1001"}
    S.gateway_running_path().unlink(missing_ok=True)
    fake.calls.clear()
    out = S.reconcile()
    restart_idx = next((i for i, c in enumerate(fake.calls)
                        if c[:4] == ["systemctl", "--user", "try-restart", S.GATEWAY_UNIT]), -1)
    stop_idx = next((i for i, c in enumerate(fake.calls)
                     if c[:4] == ["systemctl", "--user", "stop", "loam-plugin-demo.service"]), -1)
    check("stale gateway: reconcile restarts gateway before stopping front-port plugin",
          restart_idx != -1 and stop_idx != -1 and restart_idx < stop_idx
          and out.get("gateway") == "restarted" and "demo" in out["stopped"]
          and "demo" in out["asleep"],
          (restart_idx, stop_idx, out))

    # 2. Current gateway: reconcile() and ensure_gateway_current() do NOT restart gateway.
    fake.calls.clear()
    eg = S.ensure_gateway_current()
    out2 = S.reconcile()
    restarts = [c for c in fake.calls if len(c) >= 3 and c[2] == "try-restart"]
    check("current gateway: zero unnecessary restarts",
          eg["action"] == "current" and not restarts and "gateway" not in out2,
          (eg, restarts, out2))

    # 3. Failed gateway restart: reconcile() MUST NOT stop pre-sleep front-port server.
    seed_pre_sleep_demo()
    S.write_gateway_running(pid=int(fake.units[S.GATEWAY_UNIT]["MainPID"]),
                            fingerprint="0" * 64)
    fake.restart_fails = True
    fake.calls.clear()
    out3 = S.reconcile()
    fake.restart_fails = False
    check("failed gateway restart: front-port plugin kept running (no 502 orphan)",
          out3.get("gateway") == "failed" and "gateway" in out3["errors"]
          and "loam-plugin-demo.service" in fake.units and "demo" not in out3["stopped"],
          out3)

    # 4. Mid-update (loam update holds run.lock): timer reconcile() defers both
    #    gateway restart and front-port teardown so old gateway keeps serving.
    seed_pre_sleep_demo()
    S.write_gateway_running(pid=int(fake.units[S.GATEWAY_UNIT]["MainPID"]),
                            fingerprint="0" * 64)
    UPDATE_STATE.mkdir(parents=True, exist_ok=True)
    with open(UPDATE_STATE / "run.lock", "a+") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fake.calls.clear()
        out4 = S.reconcile()
        restarts4 = [c for c in fake.calls if len(c) >= 3 and c[2] == "try-restart"]
        check("mid-update timer tick: defers gateway restart and leaves front-port plugin up",
              out4.get("gateway") == "deferred-update" and not restarts4
              and "loam-plugin-demo.service" in fake.units and "demo" not in out4["stopped"],
              out4)
        # Once loam update's gateway step has restarted the gateway and sets
        # LOAM_UPDATE_GATEWAY_STEP=1, reconcile() proceeds even while run.lock is held.
        S.write_gateway_running(pid=int(fake.units[S.GATEWAY_UNIT]["MainPID"]))
        os.environ["LOAM_UPDATE_GATEWAY_STEP"] = "1"
        try:
            out4b = S.reconcile()
        finally:
            os.environ.pop("LOAM_UPDATE_GATEWAY_STEP", None)
        check("...and loam update gateway step reconciles cleanly while run.lock is held",
              "demo" in out4b["stopped"] and "loam-plugin-demo.service" not in fake.units,
              out4b)
        fcntl.flock(lock_fh, fcntl.LOCK_UN)

    # 5. Fingerprint detects edits to bin/plugin-gateway, lib/loam_plugins.py, and unit file.
    fake_root = TMP / "fake-root"
    for rel in S.GATEWAY_CODE_FILES:
        p = fake_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"initial {rel}\n", encoding="utf-8")
    fp1 = S.gateway_code_fingerprint(fake_root)
    S.write_gateway_running(pid=int(fake.units[S.GATEWAY_UNIT]["MainPID"]), fingerprint=fp1)
    check("fingerprint matches initially",
          S.gateway_unit_status(root=fake_root)["state"] == "current")
    (fake_root / "bin/plugin-gateway").write_text("edited gateway\n", encoding="utf-8")
    check("editing bin/plugin-gateway marks running gateway stale",
          S.gateway_unit_status(root=fake_root)["state"] == "stale")
    fp2 = S.gateway_code_fingerprint(fake_root)
    S.write_gateway_running(pid=int(fake.units[S.GATEWAY_UNIT]["MainPID"]), fingerprint=fp2)
    (fake_root / "lib/loam_plugins.py").write_text("edited plugins\n", encoding="utf-8")
    check("editing lib/loam_plugins.py marks running gateway stale",
          S.gateway_unit_status(root=fake_root)["state"] == "stale")

    # 6. gateway_state_stale() catches in-place PLUGIN.md edits.
    S.write_gateway_state({"demo": {"run": "python3 server/main.py", "always_on": False}}, None)
    gp = S.gateway_state_path()
    old_t = time.time() - 60
    os.utime(gp, (old_t, old_t))
    # Touch PLUGIN.md to now -> stale; touch gateway.json ahead of everything -> fresh.
    now_t = time.time()
    os.utime(YOURS / "demo" / "PLUGIN.md", (now_t, now_t))
    check("gateway_state_stale() detects PLUGIN.md newer than gateway.json",
          S.gateway_state_stale() is True)
    future_t = now_t + 60
    os.utime(gp, (future_t, future_t))
    check("...and returns False once gateway.json is newer than inputs",
          S.gateway_state_stale() is False)

    # 7. Real bin/plugin-gateway writes gateway.running.json on start and removes it on SIGTERM.
    gw_state = TMP / "live-gw-state"
    gw_state.mkdir(parents=True, exist_ok=True)
    gport, dport = free_port(), free_port()
    env = {
        **os.environ,
        "LOAM_PLUGIN_STATE": str(gw_state),
        "LOAM_PLUGIN_UNITS": "0",
    }
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "bin" / "plugin-gateway"), str(gport), str(dport)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    try:
        line = proc.stdout.readline() if proc.stdout else ""
        rec_path = gw_state / "gateway.running.json"
        deadline = time.monotonic() + 5.0
        while not rec_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        rec = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.is_file() else {}
        check("real bin/plugin-gateway writes gateway.running.json with pid + fingerprint",
              rec.get("pid") == proc.pid and rec.get("fingerprint") == S.gateway_code_fingerprint(ROOT),
              (line.strip(), rec))
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
    check("...and removes gateway.running.json on clean SIGTERM exit",
          not (gw_state / "gateway.running.json").exists())

    # 8. Browser proxy stale-process detection & reconcile (including open-browser defer).
    bp_running = TMP / "bp-running.json"
    bp_calls: list[list[str]] = []
    bp_state = {"active": "inactive", "pid": "0"}

    def fake_bp_run(cmd, **_kw):
        bp_calls.append(list(cmd))
        if "show" in cmd:
            return type("Done", (), {
                "returncode": 0,
                "stdout": f"ActiveState={bp_state['active']}\nMainPID={bp_state['pid']}\n",
                "stderr": "",
            })()
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    check("inactive browser-proxy: reconcile is a no-op (never wakes proxy/Docker)",
          bp.reconcile_proxy(running_path=bp_running, run=fake_bp_run)["action"] == "inactive"
          and not any("try-restart" in c for c in bp_calls), bp_calls)

    bp_state.update(active="active", pid="7777")
    bp.write_running(pid=7777, running_path=bp_running)
    bp_calls.clear()
    check("current browser-proxy: reconcile does not restart",
          bp.reconcile_proxy(running_path=bp_running, run=fake_bp_run)["action"] == "current"
          and not any("try-restart" in c for c in bp_calls), bp_calls)

    # Stale browser-proxy with an open browser session -> deferred unless forced.
    bp.write_running(pid=7777, fingerprint="deadbeef", running_path=bp_running)
    snap = {"open_browsers": 1, "in_flight": 0}

    class UpHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(snap).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    up_srv = http.server.HTTPServer(("127.0.0.1", 0), UpHandler)
    up_url = f"http://127.0.0.1:{up_srv.server_port}/up"
    threading.Thread(target=up_srv.serve_forever, daemon=True).start()
    try:
        bp_calls.clear()
        res_def = bp.reconcile_proxy(running_path=bp_running, run=fake_bp_run, up_url=up_url)
        check("stale browser-proxy with open browser tab: deferred",
              res_def["action"] == "deferred" and not any("try-restart" in c for c in bp_calls),
              (res_def, bp_calls))

        snap["open_browsers"] = 0
        bp_calls.clear()
        res_ok = bp.reconcile_proxy(running_path=bp_running, run=fake_bp_run, up_url=up_url)
        check("stale browser-proxy when idle: restarted via try-restart",
              res_ok["action"] == "restarted"
              and any("try-restart" in c for c in bp_calls),
              (res_ok, bp_calls))
    finally:
        up_srv.shutdown()

    # 9. loam_update TRIGGERS["gateway"] covers gateway script, plugin-servers,
    #    its systemd unit, and shipped plugins/.
    import loam_config as lc  # noqa: PLC0415
    prev_root = lc.ROOT
    lc.ROOT = ROOT
    try:
        head = up.head()
        check("loam_update gateway trigger fires on bin/plugin-gateway",
              up.needs("gateway", head, head, ["bin/plugin-gateway"]))
        check("loam_update gateway trigger fires on bin/plugin-servers",
              up.needs("gateway", head, head, ["bin/plugin-servers"]))
        check("loam_update gateway trigger fires on systemd/loam-plugins-gateway.service",
              up.needs("gateway", head, head, ["systemd/loam-plugins-gateway.service"]))
        check("loam_update gateway trigger fires on plugins/sharing/PLUGIN.md",
              up.needs("gateway", head, head, ["plugins/sharing/PLUGIN.md"]))
        check("loam_update gateway trigger ignores unrelated app/src/server.js",
              not up.needs("gateway", head, head, ["app/src/server.js"]))
    finally:
        lc.ROOT = prev_root

finally:
    S.run = real_run
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
