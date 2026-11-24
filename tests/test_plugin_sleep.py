#!/usr/bin/env python3
"""Tests for on-demand wake and idle sleep of plugin servers through bin/plugin-gateway."""

from __future__ import annotations

import asyncio
import http.client
import importlib.machinery
import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-plugin-sleep-test-"))
os.environ["LOAM_PLUGIN_STATE"] = str(TMP / "state")
os.environ["LOAM_PLUGINS_DIR"] = str(TMP / "plugins")
os.environ["LOAM_PLUGIN_SHIPPED"] = ""

sys.path.insert(0, str(ROOT / "lib"))
import loam_plugin_servers as S  # noqa: E402
import loam_plugins as P  # noqa: E402

loader = importlib.machinery.SourceFileLoader("plugin_gateway_sleep", str(ROOT / "bin" / "plugin-gateway"))
spec = importlib.util.spec_from_loader("plugin_gateway_sleep", loader)
gw = importlib.util.module_from_spec(spec)
sys.modules["plugin_gateway_sleep"] = gw
loader.exec_module(gw)

results: list[bool] = []


def check(label: str, ok: bool, detail="") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:72}"
          f"{'  | ' + str(detail)[:240] if detail != '' else ''}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakePluginFleet:
    """Runs real loopback HTTP backends for each simulated on-demand plugin."""

    def __init__(self, comments_front_port: int) -> None:
        self.comments_front_port = comments_front_port
        self.running: dict[str, ThreadingHTTPServer] = {}
        self.starts: dict[str, int] = {}
        self.stops: dict[str, int] = {}
        self.start_delay: dict[str, float] = {}
        self.stop_entered: dict[str, threading.Event] = {}
        self.stop_release: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def _handler_for(self, pid: str):
        fleet = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                return

            def do_GET(self):
                if self.path.startswith("/stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for i in range(6):
                        chunk = f"chunk-{i}\n".encode()
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.flush()
                        time.sleep(0.15)  # total ~0.9s > idle_s (0.35s)
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                    return
                if pid == "sharing" and self.path.startswith("/door/"):
                    # Simulate sharing calling sleeping comments over its front port (LOAM_PLUGIN_URL)
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{fleet.comments_front_port}/collab/capabilities",
                        timeout=10,
                    ) as resp:
                        collab = json.loads(resp.read())
                    body = json.dumps({
                        "plugin": pid,
                        "path": self.path,
                        "door": self.headers.get("X-Loam-Door"),
                        "collab": collab,
                    }).encode()
                else:
                    body = json.dumps({
                        "plugin": pid,
                        "path": self.path,
                        "prefix": self.headers.get("X-Forwarded-Prefix"),
                        "garden_tool": self.headers.get("X-Garden-Tool"),
                    }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_GET

        return Handler

    def start(self, pid: str, want: dict, fresh: bool = False, port: int | None = None) -> dict:
        delay = self.start_delay.get(pid, 0.04)
        if delay:
            time.sleep(delay)
        with self._lock:
            if pid in self.running and not fresh:
                return {"state": "running"}
            if pid in self.running:
                srv = self.running.pop(pid)
                srv.shutdown(); srv.server_close()
            self.starts[pid] = self.starts.get(pid, 0) + 1
            srv = ThreadingHTTPServer(("127.0.0.1", port), self._handler_for(pid))
            srv.daemon_threads = True
            self.running[pid] = srv
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        return {"state": "running"}

    def stop(self, pid: str) -> dict:
        ev = self.stop_entered.get(pid)
        if ev is not None:
            ev.set()
        rel = self.stop_release.get(pid)
        if rel is not None:
            rel.wait(timeout=5.0)
        with self._lock:
            self.stops[pid] = self.stops.get(pid, 0) + 1
            srv = self.running.pop(pid, None)
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        return {"state": "stopped"}

    def status(self, pid: str) -> dict:
        with self._lock:
            up = pid in self.running
        return {"state": "running" if up else "asleep"}

    def stop_all(self) -> None:
        for pid in list(self.running):
            self.stop(pid)


def http_get(port: int, path: str, headers: dict | None = None) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    conn.request("GET", path, headers=headers or {})
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, body


def test_box_listener_default() -> None:
    yours = TMP / "plugins"
    d = yours / "listener-app"
    d.mkdir(parents=True)
    (d / "PLUGIN.md").write_text(
        "---\nid: listener-app\nname: Listener\nversion: 1.0.0\napi: 0\nkind: [extension]\n"
        "contributes:\n  box.listener:\n    - process: syncthing\n      why: sync\n---\n"
    )
    (d / "server").mkdir()
    (d / "server" / "main.py").write_text("#\n")
    row = next(r for r in P.listing()["plugins"] if r["id"] == "listener-app")
    spec_l = P.server_spec(row)
    check("plugin declaring box.listener defaults to always_on=True",
          spec_l is not None and spec_l["always_on"] is True, spec_l)


def test_gateway_wake_sleep() -> None:
    (TMP / "state").mkdir(parents=True, exist_ok=True)
    gport, dport = free_port(), free_port()
    f_demo, b_demo = free_port(), free_port()
    f_garden, b_garden = free_port(), free_port()
    f_sharing, b_sharing = free_port(), free_port()
    f_comments, b_comments = free_port(), free_port()
    (TMP / "state" / "ports.json").write_text(json.dumps({
        "ports": {"demo": f_demo, "garden": f_garden, "sharing": f_sharing, "comments": f_comments},
        "backend_ports": {"demo": b_demo, "garden": b_garden, "sharing": b_sharing, "comments": b_comments},
    }))
    wanted = {
        pid: {"run": "python3 server/main.py", "folder": str(TMP / "plugins" / pid),
              "settings": None, "default": True, "always_on": False}
        for pid in ("demo", "garden", "sharing", "comments")
    }
    fleet = FakePluginFleet(f_comments)
    sup = gw.GatewaySupervisor(
        idle_s=0.35,
        wake_timeout_s=10.0,
        managed_fn=lambda: True,
        wanted_fn=lambda: wanted,
        start_fn=fleet.start,
        stop_fn=fleet.stop,
        status_fn=fleet.status,
    )
    orig_owner = gw.door_owner
    gw.door_owner = lambda: "sharing"

    stop_ev_holder: list[asyncio.Event] = []
    loop_holder: list[asyncio.AbstractEventLoop] = []
    ready = threading.Event()

    def serve():
        async def go():
            loop_holder.append(asyncio.get_running_loop())
            stop_ev = asyncio.Event()
            stop_ev_holder.append(stop_ev)
            wake_sync = asyncio.Event()
            await sup.sync(bind_direct=True)
            s1 = await asyncio.start_server(lambda r, w: gw.handle(r, w, supervisor=sup), "127.0.0.1", gport)
            s2 = await asyncio.start_server(lambda r, w: gw.door(r, w, supervisor=sup), "127.0.0.1", dport)
            hk = asyncio.create_task(gw._housekeep(sup, wake_sync, stop_ev))
            ready.set()
            await stop_ev.wait()
            hk.cancel()
            await sup.close_all()
            s1.close(); s2.close()
        asyncio.run(go())

    th = threading.Thread(target=serve, daemon=True)
    th.start()
    ready.wait(timeout=5)

    try:
        # 1. /up never wakes anything
        st_up, _ = http_get(gport, "/up")
        check("GET /up answers 200 without waking any plugin",
              st_up == 200 and fleet.starts == {}, (st_up, fleet.starts))

        # 2. First call while asleep via /x/demo/ wakes demo and succeeds
        st, body = http_get(gport, "/x/demo/hello")
        data = json.loads(body)
        check("first call while asleep (/x/demo/hello) wakes demo and returns 200",
              st == 200 and data["plugin"] == "demo" and data["prefix"] == "/x/demo"
              and fleet.starts.get("demo") == 1, (st, data, fleet.starts))

        # Wait for demo to sleep
        deadline = time.monotonic() + 3.0
        while "demo" in fleet.running and time.monotonic() < deadline:
            time.sleep(0.05)
        check("demo goes back to sleep after quiet period",
              "demo" not in fleet.running and fleet.stops.get("demo", 0) >= 1,
              (list(fleet.running), fleet.stops))

        # 3. Two concurrent first calls while asleep coalesce into ONE start and both succeed
        fleet.start_delay["demo"] = 0.25
        bar = threading.Barrier(2)
        pair: list[tuple[int, dict]] = []

        def hit(tag: str):
            bar.wait()
            s, b = http_get(gport, f"/x/demo/{tag}")
            pair.append((s, json.loads(b)))

        t1 = threading.Thread(target=hit, args=("one",))
        t2 = threading.Thread(target=hit, args=("two",))
        t1.start(); t2.start(); t1.join(); t2.join()
        check("two concurrent first calls coalesce into ONE start and both return 200",
              len(pair) == 2 and all(s == 200 for s, _ in pair)
              and fleet.starts.get("demo") == 2, (pair, fleet.starts))

        # 4. Call arriving DURING shutdown waits for stop to finish and re-wakes cleanly
        fleet.start_delay["demo"] = 0.04
        ev_in = threading.Event()
        ev_rel = threading.Event()
        fleet.stop_entered["demo"] = ev_in
        fleet.stop_release["demo"] = ev_rel
        check("demo enters shutdown after quiet period", ev_in.wait(timeout=3.0))
        mid: list[tuple[int, dict]] = []

        def mid_req():
            s, b = http_get(gport, "/x/demo/during-stop")
            mid.append((s, json.loads(b)))

        tm = threading.Thread(target=mid_req)
        tm.start()
        time.sleep(0.1)
        ev_rel.set()
        tm.join(timeout=10)
        fleet.stop_entered.pop("demo", None)
        fleet.stop_release.pop("demo", None)
        check("call arriving during shutdown waits, re-wakes demo, and returns 200",
              len(mid) == 1 and mid[0][0] == 200 and mid[0][1]["path"] == "/during-stop"
              and fleet.starts.get("demo") == 3, (mid, fleet.starts))

        # 5. Stream LONGER than quiet period is NOT interrupted and sleeps only after finishing
        stops_before = fleet.stops.get("demo", 0)
        st_s, body_s = http_get(gport, "/x/demo/stream")
        lines = body_s.decode().splitlines()
        check("stream longer than quiet period completes all 6 chunks without stopping mid-stream",
              st_s == 200 and lines == [f"chunk-{i}" for i in range(6)]
              and fleet.stops.get("demo", 0) == stops_before,
              (st_s, lines, stops_before, fleet.stops.get("demo", 0)))
        deadline = time.monotonic() + 3.0
        while "demo" in fleet.running and time.monotonic() < deadline:
            time.sleep(0.05)
        check("...and sleeps once the stream finishes and quiet period elapses",
              "demo" not in fleet.running and fleet.stops.get("demo", 0) == stops_before + 1)

        # 6. Direct loopback call (LOAM_PLUGIN_URL, e.g. garden/tools/idea.py) wakes without X-Forwarded-Prefix
        st_g, body_g = http_get(f_garden, "/agent-idea", headers={"X-Garden-Tool": "1"})
        data_g = json.loads(body_g)
        check("direct loopback call wakes sleeping garden preserving X-Garden-Tool and no X-Forwarded-Prefix",
              st_g == 200 and data_g["plugin"] == "garden" and data_g["garden_tool"] == "1"
              and data_g["prefix"] is None and fleet.starts.get("garden") == 1,
              (st_g, data_g, fleet.starts))

        # 7. Unauthenticated share link (/s/<token>) wakes sleeping sharing AND sharing wakes sleeping comments
        tok = "A" * 40
        st_d, body_d = http_get(dport, f"/s/{tok}/")
        data_d = json.loads(body_d)
        check("unauthenticated /s/<token> wakes sleeping sharing AND sleeping comments",
              st_d == 200 and data_d["plugin"] == "sharing" and data_d["door"] == "1"
              and data_d["collab"]["plugin"] == "comments"
              and fleet.starts.get("sharing") == 1 and fleet.starts.get("comments") == 1,
              (st_d, data_d, fleet.starts))
    finally:
        gw.door_owner = orig_owner
        loop_holder[0].call_soon_threadsafe(stop_ev_holder[0].set)
        th.join(timeout=5)
        fleet.stop_all()


def test_git_backup_sigterm_drains_push() -> None:
    """Verify git-backup server waits for an in-flight background push on SIGTERM."""
    marker = TMP / "push-finished"
    port = free_port()
    helper = TMP / "run_gb.py"
    helper.write_text(f"""
import sys, time
from pathlib import Path
sys.path.insert(0, {str(ROOT / 'plugins' / 'git-backup' / 'server')!r})
import main as gb
def fake_push(tree, force=False):
    time.sleep(0.4)
    Path({str(marker)!r}).write_text(tree)
gb.core.push = fake_push
gb.core.status = lambda: {{"trees": [{{"id": "vault"}}]}}
gb.core.values = lambda: {{"vault_remote": "x", "spaces_remote": ""}}
sys.exit(gb.main())
""")
    env = {**os.environ, "PORT": str(port), "LOAM_PLUGIN_PRIVATE": str(TMP / "gb-priv"),
           "LOAM_PLUGIN_SETTINGS_DIR": str(TMP / "gb-set")}
    (TMP / "gb-priv").mkdir(exist_ok=True)
    (TMP / "gb-set").mkdir(exist_ok=True)
    proc = subprocess.Popen([sys.executable, str(helper)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/now", body=b'{"tree":"vault"}', headers={"Content-Type": "application/json"})
        r = conn.getresponse(); r.read(); conn.close()
        time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        rc = proc.wait(timeout=5)
        check("git-backup SIGTERM waits for in-flight push_beside thread before exiting 0",
              rc == 0 and marker.is_file() and marker.read_text() == "vault",
              (rc, marker.is_file()))
    finally:
        if proc.poll() is None:
            proc.kill()


test_box_listener_default()
test_gateway_wake_sleep()
test_git_backup_sigterm_drains_push()
failed = sum(not r for r in results)
print(f"\n{'FAIL' if failed else 'OK'}: {failed} failure(s) out of {len(results)}")
sys.exit(1 if failed else 0)
