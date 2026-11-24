"""MCP-aware lazy-wake proxy and idle sleeper for the agents' browser (`loam-browser`).

Agents on every provider lane (Claude Code, Codex, Antigravity) connect to
`http://127.0.0.1:8931/mcp` as soon as a conversation starts and hold a
Streamable HTTP session (including `GET /mcp` SSE) open even when they never
call a browser tool. A raw TCP proxy would therefore wake Docker and Chromium
on every chat turn and prevent them from ever sleeping.

Instead, this proxy listens on `127.0.0.1:8931` (socket-activated via
`loam-browser-proxy.socket`, so standing idle RAM is 0 MB when no agent session
is connected):

- `initialize`, `notifications/initialized`, `tools/list`, `ping`,
  `resources/list`, `prompts/list`, `GET /mcp` (SSE), and `DELETE /mcp` are
  answered locally from the pinned Playwright MCP tool schema without waking
  `loam-browser` or Docker.
- On the first `tools/call` (or legacy `/sse`), the proxy acquires its
  lifecycle lock, starts Docker (if asleep), ensures the browser firewall
  (`loam-sight-firewall.service`, plus `loam-broker-firewall.service` if
  present) is raised, starts `loam-browser` on its internal backend port
  (`127.0.0.1:8937`), replays the client's stored `initialize` +
  `notifications/initialized` to obtain a real upstream session, and forwards
  the call transparently.
- An active browser session (open tabs / logged-in page — any session that has
  called a browser tool and has neither called `browser_close`, sent
  `DELETE /mcp`, nor closed its SSE stream past grace) prevents sleep.
- After `BROWSER_IDLE_S` (default 10 minutes) with no in-flight call and no
  active browser session, `loam-browser` is stopped. When no other container
  needs Docker, `docker.service` and `containerd.service` sleep with it.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import loam_deploy as dep

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA_PATH = ROOT / "deploy" / "browser-tools.json"
CACHE_PATH = Path(os.environ.get("LOAM_BROWSER_TOOLS_CACHE") or "/var/lib/loam/browser-tools-cache.json")
STATE_PATH = Path(os.environ.get("LOAM_BROWSER_STATE") or "/run/loam-browser/state.json")
LOCK_PATH = Path(os.environ.get("LOAM_BROWSER_LOCK") or "/run/loam-browser.lock")

BROWSER_IDLE_S = float(os.environ.get("LOAM_BROWSER_IDLE_S") or 600)
PROXY_IDLE_S = float(os.environ.get("LOAM_BROWSER_PROXY_IDLE_S") or 30)
SSE_DISCONNECT_GRACE_S = float(os.environ.get("LOAM_BROWSER_SSE_GRACE_S") or 15)
WAKE_TIMEOUT_S = float(os.environ.get("LOAM_BROWSER_WAKE_TIMEOUT_S") or 45)
HEAD_MAX = 64 * 1024
BODY_MAX = 32 * 1024 * 1024

HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-connection", "te", "trailer",
    "transfer-encoding", "upgrade",
})


def load_schema(cache_path: Path = CACHE_PATH, default_path: Path = DEFAULT_SCHEMA_PATH) -> dict[str, Any]:
    """Load cached Playwright MCP initialize/tools schema matching BROWSER_IMAGE."""
    for path in (cache_path, default_path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (isinstance(data, dict) and isinstance(data.get("initialize"), dict)
                and isinstance(data.get("tools"), list)):
            if path == cache_path and data.get("image") != dep.BROWSER_IMAGE:
                continue
            return data
    return {
        "image": dep.BROWSER_IMAGE,
        "initialize": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "Playwright", "version": "0.0.85"},
        },
        "tools": [],
    }


def save_schema(schema: dict[str, Any], cache_path: Path = CACHE_PATH) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(schema, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, cache_path)
    except OSError:
        pass


def write_state(payload: dict[str, Any], state_path: Path = STATE_PATH) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, state_path)
    except OSError:
        pass


def read_state(state_path: Path = STATE_PATH) -> dict[str, Any]:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@contextmanager
def _file_lock(lock_path: Path = LOCK_PATH):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _run(argv: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout)


class DockerController:
    """Starts and stops `loam-browser` (and Docker/containerd when unused)."""

    def __init__(
        self,
        *,
        backend_host: str = "127.0.0.1",
        backend_port: int = dep.BROWSER_BACKEND_PORT,
        compose_path: Path = dep.BROWSER_COMPOSE,
        container_name: str = dep.BROWSER_PROJECT,
        lock_path: Path = LOCK_PATH,
        state_path: Path = STATE_PATH,
        runner: Callable[[list[str], float], subprocess.CompletedProcess] | None = None,
    ) -> None:
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.compose_path = compose_path
        self.container_name = container_name
        self.lock_path = lock_path
        self.state_path = state_path
        self._run = runner or _run

    def docker_active(self) -> bool:
        res = self._run(["systemctl", "is-active", "--quiet", "docker.service"], 15)
        return res.returncode == 0

    def browser_running(self) -> bool:
        if not self.docker_active():
            return False
        res = self._run(
            ["docker", "inspect", "-f", "{{.State.Running}}", self.container_name], 15,
        )
        return res.returncode == 0 and res.stdout.strip().lower() == "true"

    def _container_matches_port(self) -> bool:
        """Whether the existing `loam-browser` container publishes our backend port."""
        res = self._run(
            ["docker", "inspect", "-f", "{{json .HostConfig.PortBindings}}", self.container_name],
            15,
        )
        if res.returncode != 0:
            return False
        return f"{self.backend_port}/tcp" in (res.stdout or "")

    def other_containers_need_docker(self) -> bool:
        """True if any container other than `loam-browser` is running or configured to run."""
        if not dep.unclaimed() and dep.AUTHELIA_SECRETS.exists():
            return True
        if not self.docker_active():
            return False
        ids = self._run(["docker", "ps", "-aq"], 15)
        if ids.returncode != 0:
            return True  # fail safe: do not stop Docker if we cannot inspect it
        cids = [ln.strip() for ln in (ids.stdout or "").splitlines() if ln.strip()]
        if not cids:
            return False
        insp = self._run(
            ["docker", "inspect", "-f",
             "{{.Name}}\t{{.State.Running}}\t{{.HostConfig.RestartPolicy.Name}}",
             *cids],
            20,
        )
        if insp.returncode != 0:
            return True
        for line in (insp.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            name = parts[0].lstrip("/")
            if name == self.container_name:
                continue
            running = parts[1].strip().lower() == "true"
            policy = parts[2].strip().lower()
            if running or policy not in ("", "no"):
                return True
        return False

    def _ensure_firewalls(self) -> None:
        """Raise browser/broker walls after Docker starts, before the browser runs."""
        if dep.SIGHT_FIREWALL_UNIT.exists():
            res = self._run(["systemctl", "start", dep.SIGHT_WALL_SERVICE], 30)
            if res.returncode != 0:
                raise RuntimeError(
                    f"could not raise {dep.SIGHT_WALL_SERVICE}: {(res.stderr or res.stdout).strip()}"
                )
        broker_unit = Path("/etc/systemd/system") / dep.loam_broker.WALL_SERVICE
        if broker_unit.exists():
            self._run(["systemctl", "start", dep.loam_broker.WALL_SERVICE], 30)

    def _wait_tcp(self, timeout: float = WAKE_TIMEOUT_S) -> None:
        """Wait until Playwright MCP inside the container answers HTTP, not just
        docker-proxy's early TCP accept before Node has bound the port."""
        deadline = time.monotonic() + timeout
        probe = (
            f"GET /mcp HTTP/1.1\r\n"
            f"Host: {self.backend_host}:{self.backend_port}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("latin-1")
        last_err = ""
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((self.backend_host, self.backend_port), timeout=1.0) as sock:
                    sock.settimeout(1.0)
                    sock.sendall(probe)
                    head = sock.recv(16)
                    if head.startswith(b"HTTP/1."):
                        return
                    last_err = f"empty/non-HTTP probe reply ({head!r})"
            except OSError as exc:
                last_err = str(exc)
            time.sleep(0.05)
        raise RuntimeError(
            f"{self.container_name} did not answer HTTP on {self.backend_host}:{self.backend_port} "
            f"within {timeout:.0f}s ({last_err})"
        )

    def wake(self) -> dict[str, Any]:
        t0 = time.monotonic()
        with _file_lock(self.lock_path):
            started_docker = False
            if not self.docker_active():
                res = self._run(["systemctl", "start", "docker.service"], 90)
                if res.returncode != 0:
                    raise RuntimeError(
                        f"systemctl start docker.service failed: {(res.stderr or res.stdout).strip()}"
                    )
                started_docker = True
            self._ensure_firewalls()
            if not self.browser_running():
                started_fast = False
                if self._container_matches_port():
                    res = self._run(["docker", "start", self.container_name], 60)
                    started_fast = res.returncode == 0
                if not started_fast:
                    res = self._run(
                        ["docker", "compose", "-f", str(self.compose_path),
                         "up", "-d", "--remove-orphans"],
                        180,
                    )
                    if res.returncode != 0:
                        raise RuntimeError(
                            f"docker compose up {self.container_name} failed: "
                            f"{(res.stderr or res.stdout).strip()}"
                        )
            self._wait_tcp()
            elapsed = round(time.monotonic() - t0, 3)
            info = {
                "state": "awake",
                "docker_active": True,
                "started_docker": started_docker,
                "wake_seconds": elapsed,
                "updated_at": time.time(),
            }
            write_state(info, self.state_path)
            return info

    def sleep(self) -> dict[str, Any]:
        with _file_lock(self.lock_path):
            stopped_docker = False
            if self.docker_active():
                if self.browser_running():
                    self._run(["docker", "stop", "-t", "10", self.container_name], 30)
                if not self.other_containers_need_docker():
                    self._run(["systemctl", "stop", "docker.service", "containerd.service"], 60)
                    stopped_docker = not self.docker_active()
            info = {
                "state": "asleep",
                "docker_active": self.docker_active(),
                "stopped_docker": stopped_docker,
                "updated_at": time.time(),
            }
            write_state(info, self.state_path)
            return info

    def sleep_docker_if_unused(self) -> dict[str, Any]:
        """Stop Docker and containerd when neither loam-browser nor any other container needs them."""
        with _file_lock(self.lock_path):
            stopped_docker = False
            if self.docker_active() and not self.browser_running() and not self.other_containers_need_docker():
                self._run(["systemctl", "stop", "docker.service", "containerd.service"], 60)
                stopped_docker = not self.docker_active()
            info = {
                "state": "awake" if self.browser_running() else "asleep",
                "docker_active": self.docker_active(),
                "stopped_docker": stopped_docker,
                "updated_at": time.time(),
            }
            write_state(info, self.state_path)
            return info


@dataclass
class ClientSession:
    client_sid: str
    init_body: bytes
    init_accept: str = "application/json, text/event-stream"
    init_protocol: str = "2025-06-18"
    initialized_sent: bool = False
    real_sid: str | None = None
    browser_open: bool = False
    had_sse: bool = False
    sse_closed_at: float | None = None
    sse_writers: set[asyncio.StreamWriter] = field(default_factory=set)
    in_flight: int = 0
    created_at: float = field(default_factory=time.monotonic)
    last_call_at: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _parse_headers(raw_lines: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in raw_lines:
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        out.append((k.strip(), v.strip()))
    return out


def _header(headers: list[tuple[str, str]], name: str) -> str | None:
    low = name.lower()
    for k, v in headers:
        if k.lower() == low:
            return v
    return None


async def _read_chunked(reader: asyncio.StreamReader, max_bytes: int = BODY_MAX) -> bytes:
    buf = bytearray()
    while True:
        line = await reader.readline()
        if not line:
            raise asyncio.IncompleteReadError(bytes(buf), None)
        size_str = line.split(b";", 1)[0].strip()
        size = int(size_str, 16)
        if size == 0:
            while True:
                trailer = await reader.readline()
                if not trailer or trailer in (b"\r\n", b"\n"):
                    break
            return bytes(buf)
        if len(buf) + size > max_bytes:
            raise ValueError("body too large")
        chunk = await reader.readexactly(size)
        buf.extend(chunk)
        crlf = await reader.readexactly(2)
        if crlf != b"\r\n":
            raise ValueError("malformed chunked body")


async def _read_http_request(
    reader: asyncio.StreamReader,
) -> tuple[str, str, str, list[tuple[str, str]], bytes] | None:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError:
        return None
    except asyncio.LimitOverrunError:
        raise ValueError("headers too large")
    text = head[:-4].decode("latin-1")
    lines = text.split("\r\n")
    parts = lines[0].split(" ", 2)
    if len(parts) != 3:
        raise ValueError("malformed request line")
    method, target, version = parts
    headers = _parse_headers(lines[1:])
    te = (_header(headers, "transfer-encoding") or "").lower()
    cl = _header(headers, "content-length")
    if "chunked" in te:
        body = await _read_chunked(reader)
    elif cl is not None:
        n = int(cl)
        if n < 0 or n > BODY_MAX:
            raise ValueError("body too large")
        body = await reader.readexactly(n) if n else b""
    else:
        body = b""
    return method, target, version, headers, body


def _http_response(
    status: int,
    reason: str,
    body: bytes,
    *,
    content_type: str = "text/plain; charset=utf-8",
    extra_headers: list[tuple[str, str]] | None = None,
    keep_alive: bool = True,
) -> bytes:
    hdrs = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
        ("Connection", "keep-alive" if keep_alive else "close"),
    ]
    if extra_headers:
        hdrs.extend(extra_headers)
    head = f"HTTP/1.1 {status} {reason}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdrs) + "\r\n"
    return head.encode("latin-1") + body


def _mcp_payload_response(
    payload: dict[str, Any],
    *,
    accept: str,
    sid: str | None = None,
    keep_alive: bool = True,
) -> bytes:
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    extra: list[tuple[str, str]] = [
        ("Cache-Control", "no-cache, no-transform"),
        ("X-Accel-Buffering", "no"),
    ]
    if sid:
        extra.append(("Mcp-Session-Id", sid))
    wants_sse = "text/event-stream" in accept.lower() or not accept.strip()
    if wants_sse:
        body = b"event: message\ndata: " + encoded + b"\n\n"
        return _http_response(
            200, "OK", body, content_type="text/event-stream",
            extra_headers=extra, keep_alive=keep_alive,
        )
    return _http_response(
        200, "OK", encoded, content_type="application/json",
        extra_headers=extra, keep_alive=keep_alive,
    )


def _extract_jsonrpc_messages(content_type: str, body: bytes) -> list[dict[str, Any]]:
    text = body.decode("utf-8", errors="replace")
    out: list[dict[str, Any]] = []
    if "text/event-stream" in content_type.lower():
        for line in text.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    out.append(parsed)
                elif isinstance(parsed, list):
                    out.extend(x for x in parsed if isinstance(x, dict))
        return out
    try:
        parsed = json.loads(text)
    except ValueError:
        return out
    if isinstance(parsed, dict):
        out.append(parsed)
    elif isinstance(parsed, list):
        out.extend(x for x in parsed if isinstance(x, dict))
    return out


class BrowserProxy:
    """Asyncio MCP-aware lazy-wake HTTP proxy for `loam-browser`."""

    def __init__(
        self,
        controller: Any,
        *,
        listen_host: str = "127.0.0.1",
        listen_port: int = dep.BROWSER_PORT,
        backend_host: str = "127.0.0.1",
        backend_port: int = dep.BROWSER_BACKEND_PORT,
        idle_s: float = BROWSER_IDLE_S,
        proxy_idle_s: float = PROXY_IDLE_S,
        sse_grace_s: float = SSE_DISCONNECT_GRACE_S,
        schema_cache_path: Path = CACHE_PATH,
        default_schema_path: Path = DEFAULT_SCHEMA_PATH,
        socket_activated: bool = False,
    ) -> None:
        self.controller = controller
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.idle_s = idle_s
        self.proxy_idle_s = proxy_idle_s
        self.sse_grace_s = sse_grace_s
        self.schema_cache_path = schema_cache_path
        self.default_schema_path = default_schema_path
        self.socket_activated = socket_activated

        self.schema = load_schema(schema_cache_path, default_schema_path)
        self._schema_refreshed = False
        self.sessions: dict[str, ClientSession] = {}
        self.lifecycle_lock = asyncio.Lock()
        self.state = "awake" if controller.browser_running() else "asleep"
        self.in_flight = 0
        self.active_http_conns = 0
        now = time.monotonic()
        self.last_tool_activity = now
        self.last_any_activity = now
        self.stop_event = asyncio.Event()
        self._server: asyncio.Server | None = None
        self._reaper_task: asyncio.Task | None = None

    def _allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.listen_port}", f"localhost:{self.listen_port}"}

    def _host_ok(self, headers: list[tuple[str, str]]) -> bool:
        host = (_header(headers, "host") or "").strip().lower()
        return host in self._allowed_hosts()

    def status_snapshot(self) -> dict[str, Any]:
        return {
            "ok": True,
            "browser": self.state,
            "in_flight": self.in_flight,
            "sessions": len(self.sessions),
            "open_browsers": sum(1 for s in self.sessions.values() if s.browser_open),
            "idle_seconds": self.idle_s,
        }

    async def ensure_awake(self, *, force: bool = False, reason: str = "tools/call") -> None:
        async with self.lifecycle_lock:
            if self.state == "awake" and not force:
                return
            self.state = "starting"
            try:
                info = await asyncio.to_thread(self.controller.wake)
            except Exception:
                self.state = "awake" if await asyncio.to_thread(self.controller.browser_running) else "asleep"
                raise
            self.state = "awake"
            self.last_tool_activity = time.monotonic()
            print(json.dumps({
                "event": "wake",
                "reason": reason,
                "started_docker": bool(isinstance(info, dict) and info.get("started_docker")),
                "wake_seconds": info.get("wake_seconds") if isinstance(info, dict) else None,
            }), flush=True)

    async def _upstream_roundtrip(
        self,
        method: str,
        target: str,
        headers: list[tuple[str, str]],
        body: bytes,
        *,
        real_sid: str | None = None,
        timeout: float = 300.0,
    ) -> tuple[int, str, list[tuple[str, str]], bytes]:
        up_reader, up_writer = await asyncio.wait_for(
            asyncio.open_connection(self.backend_host, self.backend_port), timeout=10.0,
        )
        try:
            out_hdrs: list[tuple[str, str]] = [
                ("Host", f"{self.backend_host}:{self.backend_port}"),
                ("Connection", "close"),
            ]
            for k, v in headers:
                low = k.lower()
                if low in HOP_HEADERS or low in ("host", "content-length", "mcp-session-id"):
                    continue
                out_hdrs.append((k, v))
            if real_sid:
                out_hdrs.append(("Mcp-Session-Id", real_sid))
            if body or method in ("POST", "PUT", "PATCH"):
                out_hdrs.append(("Content-Length", str(len(body))))
            req = (
                f"{method} {target} HTTP/1.1\r\n"
                + "".join(f"{k}: {v}\r\n" for k, v in out_hdrs)
                + "\r\n"
            ).encode("latin-1") + body
            up_writer.write(req)
            await up_writer.drain()

            head = await asyncio.wait_for(up_reader.readuntil(b"\r\n\r\n"), timeout=timeout)
            lines = head[:-4].decode("latin-1").split("\r\n")
            _, status_s, *reason_parts = lines[0].split(" ", 2)
            status = int(status_s)
            reason = reason_parts[0] if reason_parts else ""
            resp_hdrs = _parse_headers(lines[1:])
            te = (_header(resp_hdrs, "transfer-encoding") or "").lower()
            cl = _header(resp_hdrs, "content-length")
            if method == "HEAD" or status in (204, 304):
                resp_body = b""
            elif "chunked" in te:
                resp_body = await asyncio.wait_for(_read_chunked(up_reader), timeout=timeout)
            elif cl is not None:
                n = int(cl)
                resp_body = await asyncio.wait_for(up_reader.readexactly(n), timeout=timeout) if n else b""
            else:
                resp_body = await asyncio.wait_for(up_reader.read(), timeout=timeout)
            return status, reason, resp_hdrs, resp_body
        finally:
            up_writer.close()
            try:
                await up_writer.wait_closed()
            except Exception:
                pass

    async def _materialize(self, session: ClientSession) -> str:
        """Replay client's initialize + notifications/initialized on the real browser."""
        async with session.lock:
            if session.real_sid is not None:
                return session.real_sid
            hdrs = [
                ("Content-Type", "application/json"),
                ("Accept", session.init_accept),
            ]
            st, _, rhdrs, rbody = await self._upstream_roundtrip(
                "POST", "/mcp", hdrs, session.init_body,
            )
            real_sid = _header(rhdrs, "mcp-session-id")
            if st != 200 or not real_sid:
                raise RuntimeError(f"upstream browser initialize failed (HTTP {st}): {rbody[:200]!r}")
            session.real_sid = real_sid
            if session.initialized_sent:
                notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
                await self._upstream_roundtrip("POST", "/mcp", hdrs, notif, real_sid=real_sid)
            if not self._schema_refreshed:
                self._schema_refreshed = True
                asyncio.create_task(self._refresh_schema(real_sid, rbody, _header(rhdrs, "content-type") or ""))
            return real_sid

    async def _refresh_schema(self, real_sid: str, init_body: bytes, init_ct: str) -> None:
        try:
            init_msgs = _extract_jsonrpc_messages(init_ct, init_body)
            init_res = next((m.get("result") for m in init_msgs if isinstance(m.get("result"), dict)), None)
            hdrs = [
                ("Content-Type", "application/json"),
                ("Accept", "application/json, text/event-stream"),
            ]
            req = json.dumps({"jsonrpc": "2.0", "id": "loam-cache", "method": "tools/list", "params": {}}).encode()
            st, _, rhdrs, rbody = await self._upstream_roundtrip("POST", "/mcp", hdrs, req, real_sid=real_sid)
            if st != 200:
                return
            msgs = _extract_jsonrpc_messages(_header(rhdrs, "content-type") or "", rbody)
            tools = next((m["result"].get("tools") for m in msgs
                          if isinstance(m.get("result"), dict) and isinstance(m["result"].get("tools"), list)), None)
            if init_res and tools is not None:
                self.schema = {"image": dep.BROWSER_IMAGE, "initialize": init_res, "tools": tools}
                save_schema(self.schema, self.schema_cache_path)
        except Exception:
            pass

    async def _delete_upstream(self, real_sid: str) -> None:
        if self.state != "awake":
            return
        try:
            await self._upstream_roundtrip("DELETE", "/mcp", [], b"", real_sid=real_sid, timeout=10.0)
        except Exception:
            pass

    def _handle_virtual_rpc(
        self,
        msg: dict[str, Any],
        session: ClientSession | None,
        accept: str,
        raw_body: bytes,
        keep_alive: bool,
    ) -> tuple[bytes | None, ClientSession | None]:
        """Handle MCP methods that do not need a running browser.
        Playwright MCP advertises only `capabilities.tools`, so ONLY `tools/call`
        needs to wake Docker/Chromium; every handshake, discovery probe,
        notification, list, ping, or unsupported capability returns locally."""
        method = msg.get("method")
        req_id = msg.get("id")
        if method == "initialize":
            params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
            proto = params.get("protocolVersion")
            init_res = dict(self.schema["initialize"])
            if isinstance(proto, str) and proto:
                init_res["protocolVersion"] = proto
            client_sid = str(uuid.uuid4())
            sess = ClientSession(
                client_sid=client_sid,
                init_body=raw_body,
                init_accept=accept or "application/json, text/event-stream",
                init_protocol=init_res["protocolVersion"],
            )
            self.sessions[client_sid] = sess
            payload = {"jsonrpc": "2.0", "id": req_id, "result": init_res}
            return _mcp_payload_response(payload, accept=accept, sid=client_sid, keep_alive=keep_alive), sess
        if session is None:
            # Matches Playwright MCP's own uninitialized response (e.g. Claude
            # Code's pre-init `server/discover` probe) without waking Docker.
            err = {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Bad Request: Server not initialized"}, "id": None}
            return _http_response(
                400, "Bad Request", json.dumps(err).encode("utf-8"),
                content_type="application/json", keep_alive=keep_alive,
            ), None
        if method == "tools/call":
            return None, session
        if method is None or (isinstance(method, str) and method.startswith("notifications/") and "id" not in msg):
            if method == "notifications/initialized":
                session.initialized_sent = True
            return _http_response(202, "Accepted", b"", content_type="text/plain", keep_alive=keep_alive), session
        if method == "ping":
            payload = {"jsonrpc": "2.0", "id": req_id, "result": {}}
            return _mcp_payload_response(
                payload, accept=accept, sid=session.client_sid, keep_alive=keep_alive,
            ), session
        if method == "tools/list":
            payload = {"jsonrpc": "2.0", "id": req_id, "result": {"tools": self.schema["tools"]}}
            return _mcp_payload_response(
                payload, accept=accept, sid=session.client_sid, keep_alive=keep_alive,
            ), session
        if "id" not in msg:
            return _http_response(202, "Accepted", b"", content_type="text/plain", keep_alive=keep_alive), session
        payload = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}
        return _mcp_payload_response(
            payload, accept=accept, sid=session.client_sid, keep_alive=keep_alive,
        ), session

    async def _forward_mcp_post(
        self,
        target: str,
        headers: list[tuple[str, str]],
        body: bytes,
        session: ClientSession | None,
        msg: dict[str, Any] | list[Any] | None,
        keep_alive: bool,
    ) -> bytes:
        self.in_flight += 1
        if session is not None:
            session.in_flight += 1
        try:
            wake_reason = "tools/call"
            if isinstance(msg, dict):
                tname = (msg.get("params") or {}).get("name") if isinstance(msg.get("params"), dict) else None
                wake_reason = f"tools/call:{tname}" if tname else str(msg.get("method") or "tools/call")
            await self.ensure_awake(reason=wake_reason)
            real_sid = await self._materialize(session) if session is not None else None
            try:
                st, reason, rhdrs, rbody = await self._upstream_roundtrip(
                    "POST", target, headers, body, real_sid=real_sid,
                )
            except (ConnectionError, OSError, asyncio.IncompleteReadError):
                # Container stopped or restarted externally; re-wake once.
                await self.ensure_awake(force=True)
                if session is not None:
                    session.real_sid = None
                    real_sid = await self._materialize(session)
                st, reason, rhdrs, rbody = await self._upstream_roundtrip(
                    "POST", target, headers, body, real_sid=real_sid,
                )

            # If upstream lost our session while no browser page was open, re-materialize once.
            if st == 404 and session is not None and not session.browser_open:
                session.real_sid = None
                real_sid = await self._materialize(session)
                st, reason, rhdrs, rbody = await self._upstream_roundtrip(
                    "POST", target, headers, body, real_sid=real_sid,
                )

            self.last_tool_activity = time.monotonic()
            if session is not None:
                session.last_call_at = self.last_tool_activity
                self._update_browser_open(session, msg, st, _header(rhdrs, "content-type") or "", rbody)

            ct = _header(rhdrs, "content-type") or "application/json"
            out_extra: list[tuple[str, str]] = []
            for k, v in rhdrs:
                low = k.lower()
                if low in HOP_HEADERS or low in ("content-length", "content-type", "mcp-session-id"):
                    continue
                out_extra.append((k, v))
            if session is not None:
                out_extra.append(("Mcp-Session-Id", session.client_sid))
            elif _header(rhdrs, "mcp-session-id"):
                out_extra.append(("Mcp-Session-Id", _header(rhdrs, "mcp-session-id") or ""))
            return _http_response(
                st, reason or "OK", rbody, content_type=ct,
                extra_headers=out_extra, keep_alive=keep_alive,
            )
        finally:
            self.in_flight -= 1
            if session is not None:
                session.in_flight -= 1

    def _update_browser_open(
        self,
        session: ClientSession,
        msg: dict[str, Any] | list[Any] | None,
        status: int,
        content_type: str,
        resp_body: bytes,
    ) -> None:
        items = msg if isinstance(msg, list) else ([msg] if isinstance(msg, dict) else [])
        calls = [m for m in items if isinstance(m, dict) and m.get("method") == "tools/call"]
        if not calls:
            return
        responses = _extract_jsonrpc_messages(content_type, resp_body)
        by_id = {r.get("id"): r for r in responses if "id" in r}
        for call in calls:
            params = call.get("params") if isinstance(call.get("params"), dict) else {}
            tool = params.get("name")
            if tool == "browser_close":
                resp = by_id.get(call.get("id"))
                failed = (status != 200 or resp is None or "error" in resp
                          or (isinstance(resp.get("result"), dict) and resp["result"].get("isError")))
                if not failed:
                    session.browser_open = False
            else:
                session.browser_open = True

    async def _handle_sse_get(
        self,
        session: ClientSession | None,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        if session is None:
            writer.write(_http_response(
                400, "Bad Request",
                json.dumps({"jsonrpc": "2.0", "error": {"code": -32000, "message": "Bad Request: No valid session ID provided"}, "id": None}).encode(),
                content_type="application/json", keep_alive=False,
            ))
            await writer.drain()
            return
        head = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/event-stream\r\n"
            "Cache-Control: no-cache, no-transform\r\n"
            f"Mcp-Session-Id: {session.client_sid}\r\n"
            "X-Accel-Buffering: no\r\n"
            "Connection: keep-alive\r\n"
            "Transfer-Encoding: chunked\r\n\r\n"
        ).encode("latin-1")
        writer.write(head)
        await writer.drain()
        session.had_sse = True
        session.sse_closed_at = None
        session.sse_writers.add(writer)
        try:
            while not self.stop_event.is_set() and session.client_sid in self.sessions:
                try:
                    data = await asyncio.wait_for(reader.read(1), timeout=30.0)
                    if not data:
                        break
                except asyncio.TimeoutError:
                    if writer.is_closing():
                        break
                    continue
                except (ConnectionError, OSError):
                    break
        finally:
            session.sse_writers.discard(writer)
            if not session.sse_writers:
                session.sse_closed_at = time.monotonic()

    async def _proxy_legacy_raw(
        self,
        method: str,
        target: str,
        headers: list[tuple[str, str]],
        body: bytes,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Fallback raw proxy for legacy /sse and /messages endpoints."""
        self.in_flight += 1
        try:
            await self.ensure_awake(reason=f"legacy:{target}")
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(self.backend_host, self.backend_port), timeout=10.0,
            )
            out_hdrs = [("Host", f"{self.backend_host}:{self.backend_port}"), ("Connection", "close")]
            for k, v in headers:
                low = k.lower()
                if low in HOP_HEADERS or low in ("host", "content-length"):
                    continue
                out_hdrs.append((k, v))
            if body or method in ("POST", "PUT", "PATCH"):
                out_hdrs.append(("Content-Length", str(len(body))))
            up_writer.write(
                (f"{method} {target} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in out_hdrs) + "\r\n").encode("latin-1")
                + body
            )
            await up_writer.drain()
            while True:
                chunk = await up_reader.read(65536)
                if not chunk:
                    break
                self.last_tool_activity = time.monotonic()
                writer.write(chunk)
                await writer.drain()
            up_writer.close()
        finally:
            self.in_flight -= 1

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.active_http_conns += 1
        try:
            while not self.stop_event.is_set():
                try:
                    req = await _read_http_request(reader)
                except ValueError as exc:
                    writer.write(_http_response(400, "Bad Request", (str(exc) + "\n").encode(), keep_alive=False))
                    await writer.drain()
                    return
                if req is None:
                    return
                self.last_any_activity = time.monotonic()
                method, target, version, headers, body = req
                conn_hdr = (_header(headers, "connection") or "").lower()
                keep_alive = version == "HTTP/1.1" and "close" not in conn_hdr
                path = target.split("?", 1)[0]

                if path == "/up" and method == "GET":
                    snap = json.dumps(self.status_snapshot()).encode("utf-8")
                    writer.write(_http_response(
                        200, "OK", snap, content_type="application/json", keep_alive=keep_alive,
                    ))
                    await writer.drain()
                    if not keep_alive:
                        return
                    continue

                if not self._host_ok(headers):
                    msg = f"Access is only allowed at 127.0.0.1:{self.listen_port}, localhost:{self.listen_port}".encode()
                    writer.write(_http_response(403, "Forbidden", msg, keep_alive=False))
                    await writer.drain()
                    return

                if path == "/mcp":
                    sid = _header(headers, "mcp-session-id")
                    session = self.sessions.get(sid) if sid else None

                    if method == "GET":
                        await self._handle_sse_get(session, reader, writer)
                        return

                    if method == "DELETE":
                        if session is not None:
                            self.sessions.pop(session.client_sid, None)
                            for w in list(session.sse_writers):
                                try:
                                    w.close()
                                except Exception:
                                    pass
                            session.sse_writers.clear()
                            if session.real_sid:
                                asyncio.create_task(self._delete_upstream(session.real_sid))
                        writer.write(_http_response(200, "OK", b"", keep_alive=keep_alive))
                        await writer.drain()
                        if not keep_alive:
                            return
                        continue

                    if method == "POST":
                        accept = _header(headers, "accept") or "application/json, text/event-stream"
                        try:
                            parsed = json.loads(body.decode("utf-8")) if body else None
                        except ValueError:
                            writer.write(_http_response(
                                400, "Bad Request",
                                json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None}).encode(),
                                content_type="application/json", keep_alive=keep_alive,
                            ))
                            await writer.drain()
                            if not keep_alive:
                                return
                            continue

                        if isinstance(parsed, dict):
                            if parsed.get("method") != "initialize" and sid and session is None:
                                writer.write(_http_response(
                                    404, "Not Found",
                                    json.dumps({"jsonrpc": "2.0", "error": {"code": -32001, "message": "Session not found"}, "id": parsed.get("id")}).encode(),
                                    content_type="application/json", keep_alive=keep_alive,
                                ))
                                await writer.drain()
                                if not keep_alive:
                                    return
                                continue
                            virtual, session = self._handle_virtual_rpc(parsed, session, accept, body, keep_alive)
                            if virtual is not None:
                                writer.write(virtual)
                                await writer.drain()
                                if not keep_alive:
                                    return
                                continue
                        elif session is None:
                            status_code, reason_txt, err_code, err_msg = (
                                (404, "Not Found", -32001, "Session not found")
                                if sid else (400, "Bad Request", -32000, "Bad Request: Server not initialized")
                            )
                            writer.write(_http_response(
                                status_code, reason_txt,
                                json.dumps({"jsonrpc": "2.0", "error": {"code": err_code, "message": err_msg}, "id": None}).encode(),
                                content_type="application/json", keep_alive=keep_alive,
                            ))
                            await writer.drain()
                            if not keep_alive:
                                return
                            continue
                        elif isinstance(parsed, list) and not any(
                            isinstance(item, dict) and item.get("method") == "tools/call" for item in parsed
                        ):
                            replies = []
                            for item in parsed:
                                if not isinstance(item, dict):
                                    continue
                                m = item.get("method")
                                if m == "notifications/initialized":
                                    session.initialized_sent = True
                                elif m == "ping" and "id" in item:
                                    replies.append({"jsonrpc": "2.0", "id": item["id"], "result": {}})
                                elif m == "tools/list" and "id" in item:
                                    replies.append({"jsonrpc": "2.0", "id": item["id"], "result": {"tools": self.schema["tools"]}})
                                elif "id" in item:
                                    replies.append({"jsonrpc": "2.0", "id": item["id"], "error": {"code": -32601, "message": "Method not found"}})
                            if replies:
                                encoded = json.dumps(replies, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                                writer.write(_http_response(
                                    200, "OK", encoded, content_type="application/json",
                                    extra_headers=[("Mcp-Session-Id", session.client_sid)], keep_alive=keep_alive,
                                ))
                            else:
                                writer.write(_http_response(202, "Accepted", b"", content_type="text/plain", keep_alive=keep_alive))
                            await writer.drain()
                            if not keep_alive:
                                return
                            continue

                        try:
                            resp = await self._forward_mcp_post(target, headers, body, session, parsed, keep_alive)
                        except Exception as exc:
                            err = {
                                "jsonrpc": "2.0",
                                "id": parsed.get("id") if isinstance(parsed, dict) else None,
                                "error": {"code": -32000, "message": f"Browser unavailable: {exc}"},
                            }
                            resp = _http_response(
                                502, "Bad Gateway", json.dumps(err).encode("utf-8"),
                                content_type="application/json", keep_alive=False,
                            )
                            writer.write(resp)
                            await writer.drain()
                            return
                        writer.write(resp)
                        await writer.drain()
                        if not keep_alive:
                            return
                        continue

                if path in ("/sse", "/messages") or path.startswith("/messages"):
                    await self._proxy_legacy_raw(method, target, headers, body, reader, writer)
                    return

                writer.write(_http_response(404, "Not Found", b"Not Found\n", keep_alive=keep_alive))
                await writer.drain()
                if not keep_alive:
                    return
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        except Exception as exc:
            print(f"browser-proxy: {exc!r}", file=sys.stderr, flush=True)
        finally:
            self.active_http_conns -= 1
            try:
                writer.close()
            except Exception:
                pass

    async def reap_once(self) -> bool:
        """Clean up disconnected sessions and put the browser/Docker to sleep when idle."""
        now = time.monotonic()
        stale: list[ClientSession] = []
        for sid, sess in list(self.sessions.items()):
            if (sess.had_sse and not sess.sse_writers and sess.sse_closed_at is not None
                    and sess.in_flight == 0 and (now - sess.sse_closed_at) >= self.sse_grace_s):
                stale.append(sess)
                self.sessions.pop(sid, None)
        for sess in stale:
            if sess.real_sid:
                await self._delete_upstream(sess.real_sid)

        slept = False
        if (self.state == "awake" and self.in_flight == 0
                and not any(s.browser_open for s in self.sessions.values())
                and (now - self.last_tool_activity) >= self.idle_s):
            async with self.lifecycle_lock:
                now2 = time.monotonic()
                if (self.state == "awake" and self.in_flight == 0
                        and not any(s.browser_open for s in self.sessions.values())
                        and (now2 - self.last_tool_activity) >= self.idle_s):
                    self.state = "stopping"
                    sleep_info = None
                    try:
                        sleep_info = await asyncio.to_thread(self.controller.sleep)
                    finally:
                        for sess in self.sessions.values():
                            if not sess.browser_open:
                                sess.real_sid = None
                        self.state = "awake" if await asyncio.to_thread(self.controller.browser_running) else "asleep"
                    slept = self.state == "asleep"
                    if slept:
                        print(json.dumps({
                            "event": "sleep",
                            "stopped_docker": bool(isinstance(sleep_info, dict) and sleep_info.get("stopped_docker")),
                        }), flush=True)

        if (self.socket_activated and self.state == "asleep"
                and not self.sessions and self.in_flight == 0
                and self.active_http_conns == 0
                and (time.monotonic() - self.last_any_activity) >= self.proxy_idle_s):
            self.stop_event.set()
        return slept

    async def _reaper_loop(self) -> None:
        interval = max(0.05, min(15.0, self.idle_s / 4.0, self.proxy_idle_s / 4.0))
        while not self.stop_event.is_set():
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass
            try:
                await self.reap_once()
            except Exception as exc:
                print(f"browser-proxy reaper: {exc!r}", file=sys.stderr, flush=True)

    async def serve(self, sock: socket.socket | None = None) -> None:
        if sock is not None:
            sock.setblocking(False)
            self._server = await asyncio.start_server(self.handle_client, sock=sock, limit=HEAD_MAX)
        else:
            self._server = await asyncio.start_server(
                self.handle_client, self.listen_host, self.listen_port, limit=HEAD_MAX,
            )
            addr = self._server.sockets[0].getsockname()
            self.listen_port = addr[1]
        self._reaper_task = asyncio.create_task(self._reaper_loop())
        print(json.dumps({
            "listening": f"{self.listen_host}:{self.listen_port}",
            "backend": f"{self.backend_host}:{self.backend_port}",
            "state": self.state,
            "socket_activated": self.socket_activated,
        }), flush=True)
        try:
            await self.stop_event.wait()
        finally:
            if self._reaper_task:
                self._reaper_task.cancel()
                try:
                    await self._reaper_task
                except asyncio.CancelledError:
                    pass
            # Close our reference to the listening socket without shutdown(),
            # so systemd keeps the socket listening if socket-activated.
            if self._server is not None:
                self._server.close()
                for s in list(self.sessions.values()):
                    for w in list(s.sse_writers):
                        try:
                            w.close()
                        except Exception:
                            pass
                try:
                    await asyncio.wait_for(self._server.wait_closed(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass


def inherited_socket() -> socket.socket | None:
    """Adopt systemd's socket-activated fd 3 when started via loam-browser-proxy.socket."""
    if os.environ.get("LISTEN_PID") != str(os.getpid()):
        return None
    try:
        fds = int(os.environ.get("LISTEN_FDS") or "0")
    except ValueError:
        return None
    if fds < 1:
        return None
    return socket.socket(fileno=3)


def run_cli(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    ctrl = DockerController()
    if args == ["--sleep-if-idle"]:
        print(json.dumps(ctrl.sleep_docker_if_unused()))
        return 0
    if args == ["--status"]:
        running = ctrl.browser_running()
        print(json.dumps({
            "browser": "running" if running else "asleep",
            "docker_active": ctrl.docker_active(),
            "state_file": read_state(),
        }, indent=2))
        return 0
    if args == ["--sleep"]:
        print(json.dumps(ctrl.sleep()))
        return 0
    if args == ["--wake"]:
        print(json.dumps(ctrl.wake()))
        return 0

    sock = inherited_socket()
    listen_port = dep.BROWSER_PORT
    if sock is not None:
        try:
            listen_port = sock.getsockname()[1]
        except OSError:
            pass
    elif args:
        listen_port = int(args[0])

    proxy = BrowserProxy(
        ctrl,
        listen_port=listen_port,
        backend_port=dep.BROWSER_BACKEND_PORT,
        socket_activated=sock is not None,
    )

    async def main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, proxy.stop_event.set)
            except NotImplementedError:
                pass
        await proxy.serve(sock=sock)

    asyncio.run(main())
    return 0
