#!/usr/bin/env python3
"""Tests for the MCP-aware lazy-wake browser proxy and Docker idle controller."""

from __future__ import annotations

import asyncio
import http.client
import json
import socket
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import loam_browser_proxy as bp  # noqa: E402
import loam_deploy as dep  # noqa: E402

results: list[bool] = []


def check(label: str, ok: bool, detail="") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:72}"
          f"{'  | ' + str(detail)[:240] if detail != '' else ''}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeBrowserMCP:
    """Loopback fake Playwright MCP server with controllable wake/sleep delays."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.running = False
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.wakes = 0
        self.sleeps = 0
        self.sessions: dict[str, list[str]] = {}
        self.deleted: list[str] = []
        self.wake_delay = 0.05
        self.sleep_entered = threading.Event()
        self.sleep_release: threading.Event | None = None
        self._lock = threading.Lock()

    def browser_running(self) -> bool:
        return self.running

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                return

            def _send(self, status: int, body: bytes, ctype: str = "text/event-stream", sid: str | None = None):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                if sid:
                    self.send_header("Mcp-Session-Id", sid)
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                msg = json.loads(raw.decode("utf-8"))
                method = msg.get("method")
                req_id = msg.get("id")
                sid = self.headers.get("Mcp-Session-Id")
                if method == "initialize":
                    new_sid = "real-" + uuid.uuid4().hex[:8]
                    with outer._lock:
                        outer.sessions[new_sid] = []
                    payload = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {"tools": {"listChanged": True}},
                            "serverInfo": {"name": "Playwright", "version": "test"},
                        },
                    }
                    body = b"event: message\ndata: " + json.dumps(payload).encode() + b"\n\n"
                    self._send(200, body, sid=new_sid)
                    return
                if method == "notifications/initialized":
                    self._send(202, b"", ctype="text/plain")
                    return
                if sid not in outer.sessions:
                    self._send(404, b'{"error":"Session not found"}', ctype="application/json")
                    return
                if method == "tools/list":
                    payload = {"jsonrpc": "2.0", "id": req_id, "result": {"tools": [{"name": "browser_navigate"}]}}
                    body = b"event: message\ndata: " + json.dumps(payload).encode() + b"\n\n"
                    self._send(200, body, sid=sid)
                    return
                if method == "tools/call":
                    tool = (msg.get("params") or {}).get("name")
                    args = (msg.get("params") or {}).get("arguments") or {}
                    with outer._lock:
                        outer.sessions[sid].append(tool)
                    payload = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": f"ok:{tool}:{sid}:{args.get('url', '')}"}],
                        },
                    }
                    body = b"event: message\ndata: " + json.dumps(payload).encode() + b"\n\n"
                    self._send(200, body, sid=sid)
                    return
                self._send(400, b"{}", ctype="application/json")

            def do_DELETE(self):
                sid = self.headers.get("Mcp-Session-Id")
                if sid:
                    with outer._lock:
                        outer.sessions.pop(sid, None)
                        outer.deleted.append(sid)
                self._send(200, b"", ctype="text/plain")

        return Handler

    def wake(self) -> dict:
        time.sleep(self.wake_delay)
        self.wakes += 1
        srv = ThreadingHTTPServer(("127.0.0.1", self.port), self._make_handler())
        srv.daemon_threads = True
        self.server = srv
        self.thread = threading.Thread(target=srv.serve_forever, daemon=True)
        self.thread.start()
        self.running = True
        return {"state": "awake"}

    def sleep(self) -> dict:
        self.sleeps += 1
        self.sleep_entered.set()
        if self.sleep_release is not None:
            self.sleep_release.wait(timeout=5.0)
        srv = self.server
        self.server = None
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        with self._lock:
            self.sessions.clear()
        self.running = False
        return {"state": "asleep"}


def rpc_post(port: int, payload: dict, *, sid: str | None = None, host: str | None = None) -> tuple[int, dict, dict | None]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    hdrs = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Host": host or f"127.0.0.1:{port}",
    }
    if sid:
        hdrs["Mcp-Session-Id"] = sid
    conn.request("POST", "/mcp", body=json.dumps(payload).encode(), headers=hdrs)
    res = conn.getresponse()
    raw = res.read()
    rhdrs = {k.lower(): v for k, v in res.getheaders()}
    conn.close()
    msgs = bp._extract_jsonrpc_messages(rhdrs.get("content-type", ""), raw) if raw else []
    return res.status, rhdrs, (msgs[0] if msgs else None)


def run_proxy_tests() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="loam-browser-proxy-test-"))
    bport = free_port()
    pport = free_port()
    fake = FakeBrowserMCP(bport)
    proxy = bp.BrowserProxy(
        fake,
        listen_port=pport,
        backend_port=bport,
        idle_s=0.35,
        proxy_idle_s=60.0,
        sse_grace_s=0.15,
        schema_cache_path=tmp / "cache.json",
    )

    loop_ready = threading.Event()
    loop_holder: list[asyncio.AbstractEventLoop] = []

    def bg():
        async def go():
            loop_holder.append(asyncio.get_running_loop())
            loop_ready.set()
            await proxy.serve()
        asyncio.run(go())

    th = threading.Thread(target=bg, daemon=True)
    th.start()
    loop_ready.wait(timeout=5)
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", pport), timeout=0.2):
                break
        except OSError:
            time.sleep(0.02)

    try:
        # 1. /up answers without waking
        c = http.client.HTTPConnection("127.0.0.1", pport, timeout=5)
        c.request("GET", "/up")
        r = c.getresponse()
        up = json.loads(r.read())
        c.close()
        check("GET /up reports asleep without waking browser",
              r.status == 200 and up["browser"] == "asleep" and fake.wakes == 0, (up, fake.wakes))

        # 2. Bad Host header refused with 403 without waking
        st, _, _ = rpc_post(pport, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                            host="evil.example:8931")
        check("non-loopback Host header gets 403 without waking",
              st == 403 and fake.wakes == 0, (st, fake.wakes))

        # 2.5 Pre-init discovery probe (Claude Code 2.1.328 sends server/discover before initialize)
        st_d, _, msg_d = rpc_post(pport, {
            "jsonrpc": "2.0", "id": "server-discover-probe-1", "method": "server/discover", "params": {},
        })
        check("pre-init server/discover returns 400 Server not initialized without waking",
              st_d == 400 and (msg_d or {}).get("error", {}).get("code") == -32000 and fake.wakes == 0,
              (st_d, msg_d, fake.wakes))

        # 3. Handshake + tools/list + ping + resources/list + logging/setLevel + SSE GET NEVER wake browser
        st, hdrs, msg = rpc_post(pport, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "c1", "version": "1"}},
        })
        sid1 = hdrs.get("mcp-session-id")
        check("initialize succeeds from cache with Mcp-Session-Id and zero wakes",
              st == 200 and bool(sid1) and msg["result"]["serverInfo"]["name"] == "Playwright" and fake.wakes == 0,
              (st, sid1, fake.wakes))
        st_n, _, _ = rpc_post(pport, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid=sid1)
        st_t, _, msg_t = rpc_post(pport, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, sid=sid1)
        st_p, _, msg_p = rpc_post(pport, {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}}, sid=sid1)
        st_r, _, msg_r = rpc_post(pport, {"jsonrpc": "2.0", "id": 4, "method": "resources/list", "params": {}}, sid=sid1)
        st_l, _, msg_l = rpc_post(pport, {"jsonrpc": "2.0", "id": 5, "method": "logging/setLevel", "params": {"level": "info"}}, sid=sid1)
        tools = msg_t["result"]["tools"]
        check("notifications/initialized, tools/list (25 tools), ping, resources/list, logging/setLevel stay asleep",
              (st_n, st_t, len(tools), st_p, msg_p["result"], st_r, msg_r["error"]["code"], st_l, msg_l["error"]["code"], fake.wakes)
              == (202, 200, 25, 200, {}, 200, -32601, 200, -32601, 0),
              (st_n, st_t, len(tools), st_l, fake.wakes))

        # Open an SSE stream on sid1 (as Claude/Agy do) -> still asleep
        sse_sock = socket.create_connection(("127.0.0.1", pport), timeout=5)
        sse_sock.sendall(
            f"GET /mcp HTTP/1.1\r\nHost: 127.0.0.1:{pport}\r\nAccept: text/event-stream\r\n"
            f"Mcp-Session-Id: {sid1}\r\n\r\n".encode()
        )
        sse_head = sse_sock.recv(1024).decode("latin-1")
        check("GET /mcp SSE stream opens 200 text/event-stream without waking",
              "200 OK" in sse_head and "text/event-stream" in sse_head and fake.wakes == 0,
              (sse_head.splitlines()[:2], fake.wakes))

        # 4. Two concurrent first tools/call requests while asleep coalesce onto ONE wake
        _, hdrs2, _ = rpc_post(pport, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "c2", "version": "1"}},
        })
        sid2 = hdrs2["mcp-session-id"]
        rpc_post(pport, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid=sid2)

        fake.wake_delay = 0.25
        out_pair: list[tuple[int, dict | None]] = []
        bar = threading.Barrier(2)

        def caller(sid, url):
            bar.wait()
            st_c, _, msg_c = rpc_post(pport, {
                "jsonrpc": "2.0", "id": 10, "method": "tools/call",
                "params": {"name": "browser_navigate", "arguments": {"url": url}},
            }, sid=sid)
            out_pair.append((st_c, msg_c))

        t1 = threading.Thread(target=caller, args=(sid1, "https://one.test"))
        t2 = threading.Thread(target=caller, args=(sid2, "https://two.test"))
        t1.start(); t2.start(); t1.join(); t2.join()
        texts = sorted(m["result"]["content"][0]["text"] for _, m in out_pair if m)
        check("two concurrent first tools/call coalesce into 1 wake and both succeed",
              len(out_pair) == 2 and all(s == 200 for s, _ in out_pair)
              and fake.wakes == 1 and len(texts) == 2
              and any("one.test" in x for x in texts) and any("two.test" in x for x in texts),
              (fake.wakes, texts))

        # 5. Open browser session is NOT stopped under the agent after quiet period
        time.sleep(0.6)
        check("open browser session (no browser_close / DELETE) stays awake past quiet period",
              fake.running and fake.sleeps == 0, (fake.running, fake.sleeps))

        # Close sid2 via DELETE, close sid1 via browser_close -> now eligible to sleep
        c = http.client.HTTPConnection("127.0.0.1", pport, timeout=5)
        c.request("DELETE", "/mcp", headers={"Host": f"127.0.0.1:{pport}", "Mcp-Session-Id": sid2})
        c.getresponse().read(); c.close()
        st_cl, _, _ = rpc_post(pport, {
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {"name": "browser_close", "arguments": {}},
        }, sid=sid1)
        check("browser_close succeeds", st_cl == 200)

        # 6. Call arriving DURING shutdown waits for sleep to finish and re-wakes cleanly
        fake.sleep_entered.clear()
        fake.sleep_release = threading.Event()
        fake.wake_delay = 0.05
        check("browser enters shutdown after idle with no open browser session",
              fake.sleep_entered.wait(timeout=3.0), (fake.sleeps, proxy.state))

        mid_result: list[tuple[int, dict | None]] = []

        def mid_call():
            st_m, _, msg_m = rpc_post(pport, {
                "jsonrpc": "2.0", "id": 12, "method": "tools/call",
                "params": {"name": "browser_navigate", "arguments": {"url": "https://during-shutdown.test"}},
            }, sid=sid1)
            mid_result.append((st_m, msg_m))

        tm = threading.Thread(target=mid_call)
        tm.start()
        time.sleep(0.1)
        fake.sleep_release.set()
        tm.join(timeout=10)
        fake.sleep_release = None
        mid_txt = mid_result[0][1]["result"]["content"][0]["text"] if mid_result and mid_result[0][1] else ""
        check("call arriving during shutdown waits, re-wakes same client session, and succeeds",
              len(mid_result) == 1 and mid_result[0][0] == 200
              and "during-shutdown.test" in mid_txt and fake.wakes == 2 and fake.sleeps == 1,
              (mid_result, fake.wakes, fake.sleeps))

        # 7. Closing SSE stream (simulating agent CLI exit without browser_close/DELETE) lets browser sleep
        sse_sock.close()
        deadline = time.monotonic() + 3.0
        while fake.running and time.monotonic() < deadline:
            time.sleep(0.05)
        check("SSE disconnect past grace clears session and sleeps browser",
              not fake.running and fake.sleeps == 2, (fake.running, fake.sleeps))

    finally:
        loop_holder[0].call_soon_threadsafe(proxy.stop_event.set)
        th.join(timeout=5)
        if fake.running:
            fake.sleep()


def run_docker_controller_tests() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="loam-docker-ctrl-test-"))
    orig_unclaimed = dep.unclaimed
    dep.unclaimed = lambda: True
    try:
        # Case A: browser-only host -> stops loam-browser AND docker.service + containerd.service
        calls_a: list[list[str]] = []
        docker_up_a = [True]

        def runner_a(argv, timeout=120):
            calls_a.append(argv)
            if argv[:3] == ["systemctl", "is-active", "--quiet"]:
                return type("R", (), {"returncode": 0 if docker_up_a[0] else 3, "stdout": "", "stderr": ""})()
            if argv[:2] == ["docker", "inspect"] and "{{.State.Running}}" in argv:
                return type("R", (), {"returncode": 0, "stdout": "true\n", "stderr": ""})()
            if argv == ["docker", "ps", "-aq"]:
                return type("R", (), {"returncode": 0, "stdout": "abc123\n", "stderr": ""})()
            if argv[:2] == ["docker", "inspect"] and "RestartPolicy" in argv[3]:
                return type("R", (), {"returncode": 0, "stdout": "/loam-browser\tfalse\tno\n", "stderr": ""})()
            if argv[:2] == ["systemctl", "stop"] and "docker.service" in argv:
                docker_up_a[0] = False
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        ctrl_a = bp.DockerController(lock_path=tmp / "a.lock", state_path=tmp / "a.json", runner=runner_a)
        out_a = ctrl_a.sleep()
        check("browser-only box: sleep() stops loam-browser AND docker+containerd",
              out_a["stopped_docker"] is True
              and any(c[:2] == ["docker", "stop"] for c in calls_a)
              and any(c == ["systemctl", "stop", "docker.service", "containerd.service"] for c in calls_a),
              (out_a, calls_a))

        # Case B: host with other containers (e.g. Authelia / app) -> stops ONLY loam-browser
        calls_b: list[list[str]] = []

        def runner_b(argv, timeout=120):
            calls_b.append(argv)
            if argv[:3] == ["systemctl", "is-active", "--quiet"]:
                return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            if argv[:2] == ["docker", "inspect"] and "{{.State.Running}}" in argv:
                return type("R", (), {"returncode": 0, "stdout": "true\n", "stderr": ""})()
            if argv == ["docker", "ps", "-aq"]:
                return type("R", (), {"returncode": 0, "stdout": "abc123\ndef456\n", "stderr": ""})()
            if argv[:2] == ["docker", "inspect"] and "RestartPolicy" in argv[3]:
                return type("R", (), {
                    "returncode": 0,
                    "stdout": "/loam-browser\tfalse\tno\n/loam-authelia\ttrue\tunless-stopped\n",
                    "stderr": "",
                })()
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        ctrl_b = bp.DockerController(lock_path=tmp / "b.lock", state_path=tmp / "b.json", runner=runner_b)
        out_b = ctrl_b.sleep()
        check("multi-container box: sleep() stops ONLY loam-browser and keeps Docker up",
              out_b["stopped_docker"] is False
              and any(c[:2] == ["docker", "stop"] for c in calls_b)
              and not any(c[:2] == ["systemctl", "stop"] for c in calls_b),
              (out_b, calls_b))

        # Case C: claimed native-login box (secrets exist, NO legacy compose.yaml, no app containers)
        # -> sleep() stops loam-browser AND puts Docker + containerd to sleep!
        orig_secrets, orig_compose = dep.AUTHELIA_SECRETS, dep.AUTHELIA_COMPOSE
        try:
            fake_secrets = tmp / "secrets.env"
            fake_secrets.write_text("AUTHELIA_JWT_SECRET=x\n", encoding="utf-8")
            fake_compose = tmp / "nonexistent-compose.yaml"
            dep.AUTHELIA_SECRETS = fake_secrets
            dep.AUTHELIA_COMPOSE = fake_compose
            dep.unclaimed = lambda: False

            calls_c: list[list[str]] = []
            docker_up_c = [True]

            def runner_c(argv, timeout=120):
                calls_c.append(argv)
                if argv[:3] == ["systemctl", "is-active", "--quiet"]:
                    return type("R", (), {"returncode": 0 if docker_up_c[0] else 3, "stdout": "", "stderr": ""})()
                if argv[:2] == ["docker", "inspect"] and "{{.State.Running}}" in argv:
                    return type("R", (), {"returncode": 0, "stdout": "true\n", "stderr": ""})()
                if argv == ["docker", "ps", "-aq"]:
                    return type("R", (), {"returncode": 0, "stdout": "abc123\n", "stderr": ""})()
                if argv[:2] == ["docker", "inspect"] and "RestartPolicy" in argv[3]:
                    return type("R", (), {"returncode": 0, "stdout": "/loam-browser\tfalse\tno\n", "stderr": ""})()
                if argv[:2] == ["systemctl", "stop"] and "docker.service" in argv:
                    docker_up_c[0] = False
                return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            ctrl_c = bp.DockerController(lock_path=tmp / "c.lock", state_path=tmp / "c.json", runner=runner_c)
            out_c = ctrl_c.sleep()
            check("claimed native-login box: sleep() stops loam-browser AND docker+containerd",
                  out_c["stopped_docker"] is True
                  and any(c == ["systemctl", "stop", "docker.service", "containerd.service"] for c in calls_c),
                  (out_c, calls_c))

            # And legacy compose.yaml still keeps Docker awake when claimed.
            fake_compose.write_text("services:\n  authelia: {}\n", encoding="utf-8")
            check("claimed legacy-compose box: other_containers_need_docker() stays True",
                  ctrl_c.other_containers_need_docker() is True,
                  calls_c)
        finally:
            dep.AUTHELIA_SECRETS, dep.AUTHELIA_COMPOSE = orig_secrets, orig_compose
    finally:
        dep.unclaimed = orig_unclaimed


run_proxy_tests()
run_docker_controller_tests()
failed = sum(not r for r in results)
print(f"\n{'FAIL' if failed else 'OK'}: {failed} failure(s) out of {len(results)}")
sys.exit(1 if failed else 0)
