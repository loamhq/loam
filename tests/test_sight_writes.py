#!/usr/bin/env python3
"""Verify that browser-subnet access to gated pages permits viewing while refusing
owner-only actions.

Run: python3 tests/test_sight_writes.py
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import http.client
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-sight-writes-"))
os.environ["LOAM_PLUGIN_STATE"] = str(TMP / "state")
sys.path.insert(0, str(ROOT / "lib"))
import loam_deploy as dep  # noqa: E402

fails = 0


def check(label: str, got, want=True) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:70} {got!r}"[:260]
          + ("" if ok else f"   want {want!r}"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


SEEN: list[dict] = []


class Backend(BaseHTTPRequestHandler):
    """The plugin's server and the app alike: say who you are and record."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def any(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        SEEN.append({"who": self.server.who, "method": self.command, "path": self.path,
                     "remote_user": self.headers.get("Remote-User")})
        body = json.dumps({"who": self.server.who}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PATCH = do_DELETE = do_OPTIONS = any  # noqa: N815


def backend(who: str) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    srv.who = who
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def wait_port(port: int) -> None:
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return
        except OSError:
            time.sleep(0.05)


def ask(port: int, method: str, path: str, host: str, headers: dict | None = None,
        body: bytes | None = None) -> tuple[int, str, str | None]:
    """(status, body, who answered) -- who is None when no backend did."""
    before = len(SEEN)
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    c.request(method, path, body=body, headers={"Host": host, **(headers or {})})
    r = c.getresponse()
    text = r.read().decode(errors="replace")
    c.close()
    time.sleep(0.05)
    who = SEEN[-1]["who"] if len(SEEN) > before else None
    return r.status, text, who


# -- the stand-ins and the real gateway ---------------------------------------

plugin, app, door_plugin = backend("plugin"), backend("app"), backend("door")
(TMP / "state").mkdir()
(TMP / "state" / "ports.json").write_text(json.dumps({"ports": {
    "doc-comments": plugin.server_port, "sharer": door_plugin.server_port}}))
loader = importlib.machinery.SourceFileLoader("plugin_gateway", str(ROOT / "bin" / "plugin-gateway"))
spec = importlib.util.spec_from_loader("plugin_gateway", loader)
gw = importlib.util.module_from_spec(spec)
loader.exec_module(gw)
gw.door_owner = lambda: "sharer"
gport, dport = free_port(), free_port()


def serve_gateway():
    async def main():
        a = await asyncio.start_server(gw.handle, "127.0.0.1", gport, limit=gw.HEAD_MAX)
        b = await asyncio.start_server(gw.door, "127.0.0.1", dport, limit=gw.HEAD_MAX)
        async with a, b:
            await asyncio.gather(a.serve_forever(), b.serve_forever())
    asyncio.run(main())


threading.Thread(target=serve_gateway, daemon=True).start()
wait_port(gport)
wait_port(dport)

# -- the gateway on its own: the owner's browser's path -------------------------

H = f"127.0.0.1:{gport}"
NOTE = json.dumps({"root": "vault", "path": "scratch.md", "text": "x"}).encode()
JSON = {"Content-Type": "application/json"}
s, t, who = ask(gport, "POST", "/x/doc-comments/doc/notes", H,
                {**JSON, "Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}, NOTE)
check("gateway: a cross-site POST to /x/<id>/ is a 403, never the plugin's",
      (s, who, "own origin" in t), (403, None, True))
s, t, who = ask(gport, "POST", "/x/doc-comments/doc/notes", H,
                {"Content-Type": "text/plain", "Origin": "https://evil.example"}, NOTE)
check("gateway: ...and a no-cors POST that only says its Origin", (s, who), (403, None))
s, t, who = ask(gport, "POST", "/x/doc-comments/doc/notes", H,
                {**JSON, "Sec-Fetch-Site": "same-site"}, NOTE)
check("gateway: ...and a same-site one (another subdomain is another origin)", (s, who), (403, None))
s, t, who = ask(gport, "GET", "/x/doc-comments/ws", H,
                {"Upgrade": "websocket", "Connection": "Upgrade", "Sec-Fetch-Site": "cross-site"})
check("gateway: ...and a cross-site WebSocket upgrade", (s, who), (403, None))
s, t, who = ask(gport, "POST", "/x/doc-comments/doc/notes", H,
                {**JSON, "Sec-Fetch-Site": "same-origin", "Origin": f"http://{H}"}, NOTE)
check("gateway: a same-origin POST reaches the plugin", (s, who), (200, "plugin"))
s, t, who = ask(gport, "POST", "/x/doc-comments/doc/notes", H, JSON, NOTE)
check("gateway: a POST with no fetch metadata (a tool, not a page) reaches it", (s, who), (200, "plugin"))
s, t, who = ask(gport, "GET", "/x/doc-comments/doc?root=vault&path=a.md", H,
                {"Sec-Fetch-Site": "cross-site"})
check("gateway: a cross-site GET reaches it (reading is the page's own SOP's)", (s, who), (200, "plugin"))

# -- through Caddy, as the browser's network ------------------------------------

caddy = shutil.which("caddy")
if not caddy:
    print("SKIP  caddy is not installed here: the generated block is not run")
else:
    dep.PLUGIN_GATEWAY_PORT, dep.PLUGIN_DOOR_PORT = gport, dport
    dep.SHARE_DIR, dep.SITES_DIR = TMP / "share", TMP / "sites"
    shell = next(a for a in dep.records() if a.app_shell and a.apex)
    shell = dataclasses.replace(shell, port=app.server_port, content_host=None)
    cport = free_port()
    host = "app.test"
    block = dep.caddy_host_block(shell, False, dt.date(2026, 11, 18), fqdn=host)
    check("the block refuses the subnet's cross-origin writes ahead of passing it",
          0 < block.find("@sight_cross") < block.find("handle @sight {"))
    check("...and the share door is still ahead of both", 0 < block.find("handle /s/*") < block.find("@sight_cross"))
    block = (block.replace(dep.SIGHT_SUBNET, "127.0.0.0/8")
             .replace(f"{host} {{", f"http://{host}:{cport} {{", 1)
             .replace("import access_log", "# import access_log")
             .replace("import compress", "# import compress"))
    conf = TMP / "Caddyfile"
    conf.write_text("{\n\tadmin off\n\tauto_https off\n\tpersist_config off\n}\n"
                    + dep.GATE_SNIPPET + "\n" + block)
    env = {**os.environ, "HOME": str(TMP), "XDG_DATA_HOME": str(TMP / "data"),
           "XDG_CONFIG_HOME": str(TMP / "config")}
    proc = subprocess.Popen([caddy, "run", "--config", str(conf), "--adapter", "caddyfile"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
    try:
        wait_port(cport)
        HH = f"{host}:{cport}"
        cross = {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}
        # The page's own origin as the gateway sees it: here the Host carries
        # this test's port, which a real host on 443 does not.
        same = {"Sec-Fetch-Site": "same-origin", "Origin": f"http://{HH}"}
        rows = [
            ("a cross-site POST to a plugin route (doc-comments' notes)", "POST",
             "/x/doc-comments/doc/notes", {**JSON, **cross}, (403, None)),
            ("a cross-site POST to an app route (/api/files)", "POST",
             "/api/files/write", {**JSON, **cross}, (403, None)),
            ("a cross-site PATCH to an app route (/api/settings)", "PATCH",
             "/api/settings", {**JSON, **cross}, (403, None)),
            ("a POST that says nothing of where it came from", "POST",
             "/x/doc-comments/doc/notes", JSON, (403, None)),
            ("a POST whose only word is another site's Origin", "POST",
             "/api/threads", {"Content-Type": "text/plain", "Origin": "https://evil.example"}, (403, None)),
            ("a same-site POST from a sibling subdomain", "POST",
             "/x/doc-comments/doc/notes", {**JSON, "Sec-Fetch-Site": "same-site",
                                           "Origin": "https://evil.owner.example"}, (403, None)),
            ("a cross-site WebSocket upgrade", "GET", "/x/doc-comments/ws",
             {"Upgrade": "websocket", "Connection": "Upgrade", **cross}, (403, None)),
            ("a same-origin POST to the plugin route", "POST",
             "/x/doc-comments/doc/notes", {**JSON, **same}, (200, "plugin")),
            ("a same-origin POST to the app route", "POST",
             "/api/files/write", {**JSON, **same}, (200, "app")),
            ("a POST with only this host's Origin (no fetch metadata)", "POST",
             "/api/threads", {**JSON, "Origin": f"https://{host}"}, (200, "app")),
            ("a cross-site GET of the app's page (sight reads)", "GET", "/", cross, (200, "app")),
            ("a cross-site GET of a plugin's page", "GET", "/x/doc-comments/", cross, (200, "plugin")),
            ("a navigation typed into the address bar", "GET", "/files",
             {"Sec-Fetch-Site": "none"}, (200, "app")),
            ("a cross-site OPTIONS (a preflight)", "OPTIONS", "/api/threads", cross, (200, "app")),
            ("the share door: a cross-site POST to /s/<token>/ is as before", "POST",
             "/s/" + "a" * 40 + "/notes", {**JSON, **cross}, (200, "door")),
        ]
        for label, method, path, headers, want in rows:
            s, t, who = ask(cport, method, path, HH, headers, NOTE if method != "GET" else None)
            check(f"sight: {label}", (s, who), want)
        ask(cport, "GET", "/", HH, {"Remote-User": "owner"})
        check("sight: a Remote-User sent on this path never reaches a backend",
              SEEN[-1]["remote_user"], None)
    finally:
        proc.terminate()
        try:
            _, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
        if fails:
            print(err.decode(errors="replace")[-2000:])

for srv in (plugin, app, door_plugin):
    srv.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'FAIL' if fails else 'OK'}: {fails} failed")
sys.exit(1 if fails else 0)
