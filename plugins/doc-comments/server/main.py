#!/usr/bin/env python3
"""The doc-comments server: the operations of ``doccomments.service`` over
HTTP, on the loopback port Loam gives the plugin (``PORT``).

The page reaches it at ``/x/doc-comments/`` through Loam's plugin gateway,
behind the login, so a request without ``X-Loam-Agent`` is the owner's. A
persona's tool calls it on loopback with ``X-Loam-Agent: <its id>``, and the
note is that persona's. Step 2's share links will add a third caller, a
link's visitor, through core's share door; the routes stay the same.

Routes (every doc is named by ``root`` and ``path``, in the query for a GET
and in the JSON body otherwise)::

    GET    /doc                          the doc's text, revision and notes
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

from doccomments import links as links_mod  # noqa: E402
from doccomments import store as stores  # noqa: E402
from doccomments.door import Door  # noqa: E402
from doccomments.service import Service  # noqa: E402
from doccomments.sidecar import NoteError  # noqa: E402

BODY_MAX = 8 * 1024 * 1024


def make_handler(service: Service, links=None, door=None, reach=lambda: "staging"):
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
            if parts and parts[0] == "door":
                # Only the gateway's door sends these, marked; never the owner.
                raise NoteError(404, "no such route")
            if parts == ["config"] and method == "GET":
                return 200, {"reach": reach()}
            if parts == ["shared"] and method == "GET":
                self._owner()
                docs = links.all() if links else []
                on = sorted({l["did"] for l in docs if l["on"]})
                seen = {d: links.seen(d) for d in on}
                return 200, {"docs": service.shared(on, seen)}
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
            if rest[:1] == ["links"]:
                return self._links(method, rest[1:], who, root, path, args)
            if method == "GET" and rest == []:
                out = service.get(who, root, path)
                if links and out.get("id") and who.get("kind") == "owner":
                    mine = links.for_doc(out["id"])
                    out["links"] = [self._public(l) for l in mine]
                    if any(l["on"] for l in mine):
                        out["seen"] = links.seen(out["id"])
                        links.mark_seen(out["id"])
                return 200, out
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

        def _owner(self):
            if (self.headers.get("X-Loam-Agent") or "").strip():
                raise NoteError(403, "share links are the owner's")

        def _public(self, link):
            return {**links_mod.public(link), "url_path": f"/s/{link['token']}"}

        def _links(self, method, rest, who, root, path, args):
            """The owner's share links for one doc."""
            self._owner()
            if links is None:
                raise NoteError(503, "share links are not set up here")
            if method == "GET" and rest == ["password"]:
                return 200, {"password": links_mod.make_password()}
            if method == "GET" and rest == []:
                did = service.get(who, root, path).get("id")
                return 200, {"links": [self._public(l) for l in links.for_doc(did)] if did else [],
                             "owner": service.store.owner()["name"]}
            if method == "POST" and rest == []:
                did = service.ensure_id(who, root, path)
                days = args.get("days", links_mod.DEFAULT_DAYS)
                link, token, password = links.create(did, args.get("mode", "comment"), args.get("name"),
                                                     days, args.get("password") or None)
                service.sync_links(did, [links_mod.public(l) for l in links.for_doc(did)])
                return 201, {"link": self._public(link), "password": password,
                             "owner": service.store.owner()["name"]}
            if method == "PATCH" and len(rest) == 1:
                old = links.get(rest[0])
                did = service.get(who, root, path).get("id")
                if not old or old["did"] != did:
                    raise NoteError(404, "no such link on this doc")
                link, password = links.update(rest[0], mode=args.get("mode"), days=args.get("days"),
                                              on=args.get("on"), name=args.get("name"),
                                              new_password=args.get("password"))
                service.sync_links(did, [links_mod.public(l) for l in links.for_doc(did)])
                return 200, {"link": self._public(link), "password": password}
            raise NoteError(405, "no such route")

        def _door(self, method: str):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2 * 1024 * 1024:
                return self._raw(413, b'{"error":"that request is too large"}', "application/json",
                                 [("X-Robots-Tag", "noindex, nofollow"), ("Cache-Control", "no-store")])
            body = self.rfile.read(n) if n else b""
            hdrs = {k.lower(): v for k, v in self.headers.items()}
            client = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or self.client_address[0]
            ans = door.handle(method, self.path, hdrs, body, client)
            self._raw(ans.status, ans.body, ans.ctype, ans.headers)

        def _raw(self, status, body, ctype, headers):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _handle(self, method: str):
            if self.headers.get("X-Loam-Door") == "1":
                if door is None or not self.path.startswith("/door/"):
                    return self._raw(404, b'{"error":"no such route"}', "application/json", [])
                try:
                    return self._door(method)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                    return self._raw(500, b'{"error":"the share door failed"}', "application/json", [])
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


def serve(service: Service, port: int, host: str = "127.0.0.1", links=None, door=None,
          reach=lambda: "staging") -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(service, links, door, reach))
    server.daemon_threads = True
    return server


def main() -> int:
    port = int(os.environ.get("PORT") or os.environ.get("LOAM_PLUGIN_PORT") or 0)
    if not port:
        print("doc-comments: no PORT given", file=sys.stderr)
        return 2
    service = Service(stores.from_env())
    private = os.environ.get("LOAM_PLUGIN_PRIVATE") or str(Path(os.environ.get("LOAM_PLUGIN_DATA", ".")) / "private")
    links = links_mod.Links(private)

    def reach() -> str:
        # The `reach` setting (settings.entry), read each time so a change
        # through Settings or `bin/settings` is in force at once.
        try:
            values = json.loads((Path(os.environ.get("LOAM_PLUGIN_SETTINGS_DIR", ".")) / "values.json").read_text())
        except (OSError, ValueError):
            values = {}
        return "everywhere" if values.get("reach") == "everywhere" else "staging"

    door = Door(service, links, Path(__file__).resolve().parent.parent, reach=reach,
                owner_name=lambda: service.store.owner()["name"])
    server = serve(service, port, links=links, door=door, reach=reach)
    print(f"doc-comments: listening on 127.0.0.1:{port}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
