#!/usr/bin/env python3
"""The doc-comments server: the operations of ``doccomments.service`` over
HTTP, on the loopback port Loam gives the plugin (``PORT``).

The page reaches it at ``/x/doc-comments/`` through Loam's plugin gateway,
behind the login, so a request without ``X-Loam-Agent`` is the owner's. A
persona's tool calls it on loopback with ``X-Loam-Agent: <its id>``, and the
note is that persona's. Step 2's share links will add a third caller, a
link's visitor, through core's share door; the routes stay the same.

Routes (every doc is named by ``root`` and ``path``, in the query for a GET
or a DELETE and in the JSON body otherwise)::

    GET    /doc                          the doc's text, revision and notes
    GET    /doc/revision                 only its revision (an open editor's poll)
    POST   /doc/notes                    a comment or a suggestion
    POST   /doc/notes/<id>/replies       a reply
    PATCH  /doc/notes/<id>               change your own words {text, reply?}
    DELETE /doc/notes/<id>/replies/<r>   remove your own reply
    POST   /doc/notes/<id>/<action>      resolve, reopen, accept, reject, reattach
    POST   /doc/save                     a save, merged three ways
    GET    /doc/history                  saves newest first, and who wrote what
    GET    /doc/version?save=<id>        the text as it was after one save
    POST   /doc/restore                  write an earlier version back {save}
    GET    /health
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from doccomments import store as stores  # noqa: E402
from doccomments.service import Service  # noqa: E402
from doccomments.sidecar import NoteError  # noqa: E402

BODY_MAX = 8 * 1024 * 1024


def make_handler(service: Service):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "doc-comments/1"

        def log_message(self, fmt, *args):  # one line per request, to the journal
            sys.stderr.write("%s %s\n" % (self.command, fmt % args))

        def _send(self, status: int, body: dict):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _who(self) -> dict:
            agent = (self.headers.get("X-Loam-Agent") or "").strip().lower()
            if agent:
                found = service.store.agent(agent)
                if not found:
                    raise NoteError(403, f"{agent!r} is not one of this Loam's agents")
                return found
            return service.store.owner()

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > BODY_MAX:
                raise NoteError(413, "that request is too large")
            if not n:
                return {}
            try:
                data = json.loads(self.rfile.read(n))
            except ValueError:
                raise NoteError(400, "the body is not JSON") from None
            if not isinstance(data, dict):
                raise NoteError(400, "the body is a JSON object")
            return data

        def _route(self, method: str):
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            if parts == ["health"]:
                return 200, {"ok": True}
            if not parts or parts[0] != "doc":
                raise NoteError(404, "no such route")
            body = self._body() if method in ("POST", "PATCH", "PUT") else {}
            args = {**q, **body}
            root, path = args.get("root"), args.get("path")
            if not isinstance(root, str) or not isinstance(path, str) or not path:
                raise NoteError(400, "name the doc: root and path")
            if not path.lower().endswith((".md", ".markdown")):
                raise NoteError(415, "notes are for Markdown docs")
            who = self._who()
            rest = parts[1:]
            if method == "GET" and rest == []:
                return 200, service.get(who, root, path)
            if method == "GET" and rest == ["revision"]:
                return 200, service.revision(who, root, path)
            if method == "GET" and rest == ["history"]:
                return 200, service.history(who, root, path)
            if method == "GET" and rest == ["version"]:
                return 200, service.version(who, root, path, args.get("save", ""))
            if method == "POST" and rest == ["save"]:
                return 200, service.save(who, root, path, args)
            if method == "POST" and rest == ["restore"]:
                return 200, service.restore(who, root, path, args.get("save", ""))
            if method == "POST" and rest == ["notes"]:
                return 201, service.add(who, root, path, args)
            if len(rest) >= 2 and rest[0] == "notes":
                tid = rest[1]
                if method == "PATCH" and len(rest) == 2:
                    return 200, service.edit(who, root, path, tid, args.get("reply"), args.get("text"))
                if method == "POST" and rest[2:] == ["replies"]:
                    return 201, service.reply(who, root, path, tid, args.get("text"))
                if method == "DELETE" and len(rest) == 4 and rest[2] == "replies":
                    return 200, service.delete_reply(who, root, path, tid, rest[3])
                if method == "POST" and len(rest) == 3:
                    action = rest[2]
                    if action == "resolve":
                        return 200, service.resolve(who, root, path, tid)
                    if action == "reopen":
                        return 200, service.reopen(who, root, path, tid)
                    if action == "accept":
                        return 200, service.accept(who, root, path, tid)
                    if action == "reject":
                        return 200, service.reject(who, root, path, tid)
                    if action == "reattach":
                        return 200, service.reattach(who, root, path, tid, args)
            raise NoteError(405, f"no route for {method} {url.path}")

        def _handle(self, method: str):
            try:
                status, body = self._route(method)
            except NoteError as exc:
                status, body = exc.status, {"error": exc.message}
            except Exception as exc:  # noqa: BLE001 -- one request, never the server
                traceback.print_exc()
                status, body = 500, {"error": f"the doc-comments server failed: {exc}"}
            self._send(status, body)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PATCH(self):
            self._handle("PATCH")

        def do_DELETE(self):
            self._handle("DELETE")

    return Handler


def serve(service: Service, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(service))
    server.daemon_threads = True
    return server


def main() -> int:
    port = int(os.environ.get("PORT") or os.environ.get("LOAM_PLUGIN_PORT") or 0)
    if not port:
        print("doc-comments: no PORT given", file=sys.stderr)
        return 2
    server = serve(Service(stores.from_env()), port)
    print(f"doc-comments: listening on 127.0.0.1:{port}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
