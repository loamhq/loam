#!/usr/bin/env python3
"""Git backup's server: what the settings page asks, on the loopback port
Loam gives the plugin (``PORT``), reached by the page at /x/git-backup/
behind the owner's login (docs/plugins.md, "Server plugins").

  GET  /status                  each tree's health (backup_core.status)
  POST /connect  {tree, remote} check the address, make the key, save it
  POST /test     {tree}         does the key work? On yes the first backup starts
  POST /now      {tree?}        back up now
  POST /every    {every}        how often: 15m, 1h, 6h, 1d
  POST /disconnect {tree}       stop backing a tree up

Every GET is safe; every change is a POST, which the gateway refuses from
any other origin. A push runs beside the answer, so the page never waits
on a slow host; `pushing` in the status says one is under way.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import backup_core as core  # noqa: E402

RUNNING: set[str] = set()
GUARD = threading.Lock()


def push_beside(trees: list[str]) -> list[str]:
    started = []
    for tree in trees:
        with GUARD:
            if tree in RUNNING:
                continue
            RUNNING.add(tree)
        started.append(tree)

        def go(t=tree):
            try:
                core.push(t, force=True)
            except Exception:  # noqa: BLE001 -- the record keeps what went wrong
                traceback.print_exc()
            finally:
                with GUARD:
                    RUNNING.discard(t)
        threading.Thread(target=go, daemon=True).start()
    return started


def status() -> dict:
    out = core.status()
    with GUARD:
        running = set(RUNNING)
    for t in out["trees"]:
        t["pushing"] = t["id"] in running
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "git-backup"

    def log_message(self, fmt, *args):  # the journal keeps errors, not every GET
        return

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64 * 1024:
            raise core.BackupError("That request is too large.")
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise core.BackupError("That request is not JSON.")
        return data if isinstance(data, dict) else {}

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/", "/status"):
            return self._send(200, status())
        if path == "/health":
            return self._send(200, {"ok": True})
        return self._send(404, {"ok": False, "error": "no such page"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        try:
            body = self._body()
            tree = body.get("tree")
            if tree is not None and tree not in core.TREES:
                raise core.BackupError("tree is vault or spaces")
            if path == "/connect":
                out = core.connect(tree, str(body.get("remote") or ""))
            elif path == "/test":
                out = core.test(tree, push_after=False)
                if out.get("ok"):
                    out["pushing"] = bool(push_beside([tree]))
            elif path == "/now":
                trees = [tree] if tree else [t for t in core.TREES if core.values()[core.SETTING[t]]]
                out = {"ok": True, "pushing": push_beside(trees)}
            elif path == "/every":
                every = str(body.get("every") or "")
                if every not in core.EVERY:
                    raise core.BackupError("How often is one of 15m, 1h, 6h or 1d.")
                core.set_value("every", every)
                out = {"ok": True, "every": every}
            elif path == "/disconnect":
                out = core.disconnect(tree)
            else:
                return self._send(404, {"ok": False, "error": "no such action"})
        except core.BackupError as exc:
            return self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._send(500, {"ok": False, "error": f"Something went wrong: {exc}"})
        out["status"] = status()
        return self._send(200, out)


def main() -> int:
    port = int(os.environ.get("PORT") or os.environ.get("LOAM_PLUGIN_PORT") or 9199)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(f"git-backup: listening on 127.0.0.1:{port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
