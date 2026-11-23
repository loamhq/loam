#!/usr/bin/env python3
"""The Sharing plugin server.

Listens on the loopback port Loam gives it (``PORT``).

Routes:
- Door (``X-Loam-Door: 1``, ``/door/<token>…``): visitor gate, file viewing,
  active-content sandboxing, downloads, and collaboration proxy.
- Owner & agent (``/x/sharing/`` behind login, or loopback with ``X-Loam-Agent``):
    GET    /health
    GET    /config
    GET    /shared                        files shared by me (enriched by collab summary if on)
    GET    /file/links/password           generate a readable password
    GET    /file/links?root=…&path=…      share links & allowed modes for one vault file
    POST   /file/links                    create a share link for one vault file
    PATCH  /file/links/<id>               change mode, days, password, name, or on/off
    POST   /file/seen                     mark a shared file seen by the owner
    GET    /withme                        files shared with me from other Loams
    POST   /withme                        add one {link, password, name?}
    GET    /withme/<id>                   one Shared-with-me entry
    DELETE /withme/<id>                   forget one Shared-with-me entry
    GET    /withme/<id>/file              read metadata (and UTF-8 text if applicable)
    GET    /withme/<id>/raw               stream inline media/image/SVG/PDF safely
    GET    /withme/<id>/download          download as attachment
    GET    /withme/<id>/frame             sandboxed HTML preview frame
- Loopback service ``share.remote`` v1 (never exposed via ``/x/``):
    *      /remote/<id>/doc…              proxy collaboration calls for ``shared:w-…``
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

from sharing import files as ftypes  # noqa: E402
from sharing import links as links_mod  # noqa: E402
from sharing import store as stores  # noqa: E402
from sharing.collab import CollabClient  # noqa: E402
from sharing.door import Door  # noqa: E402
from sharing.store import ShareError  # noqa: E402
from sharing.tracker import FileTracker  # noqa: E402
from sharing.withme import WithMe  # noqa: E402

BODY_MAX = 8 * 1024 * 1024


def _settings(settings_dir: Path) -> dict:
    try:
        vals = json.loads((settings_dir / "values.json").read_text(encoding="utf-8"))
        return vals if isinstance(vals, dict) else {}
    except (OSError, ValueError):
        return {}


def reach_of(settings_dir: Path) -> str:
    return "staging" if _settings(settings_dir).get("reach") == "staging" else "everywhere"


def home_of(settings_dir: Path) -> str:
    url = str(_settings(settings_dir).get("home") or "")
    return url if url.startswith("https://") and len(url) < 200 else ""


def default_days_of(settings_dir: Path) -> int:
    raw = _settings(settings_dir).get("default_days")
    try:
        d = int(raw)
        if 1 <= d <= links_mod.MAX_DAYS:
            return d
    except (TypeError, ValueError):
        pass
    return links_mod.DEFAULT_DAYS


def make_handler(
    store,
    tracker: FileTracker,
    links: links_mod.Links,
    collab: CollabClient,
    door: Door,
    withme: WithMe,
    *,
    reach=lambda: "everywhere",
    default_days=lambda: links_mod.DEFAULT_DAYS,
):
    def public_link(link: dict, *, is_md: bool, collab_on: bool) -> dict:
        eff = collab.effective_mode(link["mode"], is_markdown=is_md) if collab_on else "view"
        return {
            **links_mod.public(link),
            "effective_mode": eff,
            "url_path": f"/s/{link['token']}",
        }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "loam-sharing"
        sys_version = ""

        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.command, fmt % args))

        def end_headers(self):
            # Mirror Connection: close so reverse proxies behind plugin-gateway
            # do not pool sockets that the gateway closes after one request.
            if self.close_connection:
                self.send_header("Connection", "close")
            super().end_headers()

        def _send(self, status: int, body: dict):
            data = json.dumps(body, ensure_ascii=False).encode()
            self._raw(status, data, "application/json; charset=utf-8", [
                ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"),
            ])

        def _raw(self, status: int, body: bytes, ctype: str, headers=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or ()):
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _who(self) -> dict:
            agent = (self.headers.get("X-Loam-Agent") or "").strip().lower()
            if agent:
                found = store.agent(agent)
                if not found:
                    raise ShareError(403, f"{agent!r} is not one of this Loam's agents")
                return found
            return store.owner()

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > BODY_MAX:
                raise ShareError(413, "that request is too large")
            if not n:
                return {}
            try:
                data = json.loads(self.rfile.read(n))
            except ValueError:
                raise ShareError(400, "the body is not JSON") from None
            if not isinstance(data, dict):
                raise ShareError(400, "the body is a JSON object")
            return data

        def _shared_list(self) -> dict:
            tracker.sync_events()
            all_links = links.all()
            by_fid: dict[str, list[dict]] = {}
            for l in all_links:
                if l.get("on"):
                    by_fid.setdefault(l["fid"], []).append(l)
            collab_on = bool(collab.capabilities()) if collab else False
            resolved_rows = []
            summary_req = []
            for fid, flinks in by_fid.items():
                where = tracker.resolve(fid)
                if not where:
                    continue
                root, path = where
                filename = path.rsplit("/", 1)[-1]
                kind = ftypes.classify(filename)
                is_md = kind == "markdown"
                seen = links.seen(fid) or ""
                summary_req.append({"root": root, "path": path, "since": seen})
                resolved_rows.append((fid, root, path, filename, kind, is_md, flinks, seen))
            summaries = collab.summary(summary_req) if (collab_on and summary_req) else {}
            out = []
            for fid, root, path, filename, kind, is_md, flinks, seen in resolved_rows:
                s = summaries.get(f"{root}:{path}", {})
                last = max([l.get("created", "") for l in flinks] + [s.get("last", "")])
                out.append({
                    "id": fid,
                    "root": root,
                    "path": path,
                    "filename": filename,
                    "kind": kind,
                    "label": ftypes.label_of(filename),
                    "links": [public_link(l, is_md=is_md, collab_on=collab_on) for l in flinks],
                    "open": s.get("open", 0),
                    "resolved": s.get("resolved", 0),
                    "new": s.get("new", 0),
                    "last": last,
                })
            out.sort(key=lambda r: r.get("last") or "", reverse=True)
            return {"files": out, "collab": collab_on, "owner": store.owner()["name"]}

        def _file_links(self, method: str, rest: list[str], q: dict):
            self._who()
            if method == "GET" and rest == ["password"]:
                return 200, {"password": links_mod.make_password()}
            body = self._body() if method in ("POST", "PATCH", "PUT") else {}
            args = {**q, **body}
            root, path = args.get("root"), args.get("path")
            if not isinstance(root, str) or not isinstance(path, str) or not path:
                raise ShareError(400, "name the file: root and path")
            if root != "vault":
                raise ShareError(400, "only vault files can be shared")
            filename = path.rsplit("/", 1)[-1]
            kind = ftypes.classify(filename)
            is_md = kind == "markdown"
            collab_on = bool(collab.capabilities()) if collab else False
            allowed_modes = collab.allowed_modes(is_markdown=is_md) if collab_on else ["view"]

            if method == "GET" and rest == []:
                fid = tracker.find_by_path(root, path)
                flinks = links.for_file(fid) if fid else []
                return 200, {
                    "fid": fid,
                    "kind": kind,
                    "label": ftypes.label_of(filename),
                    "collab": collab_on,
                    "allowed_modes": allowed_modes,
                    "default_days": default_days(),
                    "owner": store.owner()["name"],
                    "links": [public_link(l, is_md=is_md, collab_on=collab_on) for l in flinks],
                }

            if method == "POST" and rest == []:
                fid = tracker.ensure(root, path)
                default_mode = "comment" if "comment" in allowed_modes else "view"
                mode = args.get("mode") or default_mode
                days = args.get("days", default_days())
                link, _token, password = links.create(
                    fid, mode, args.get("name"), days, args.get("password") or None,
                    allowed_modes=allowed_modes,
                )
                return 201, {
                    "fid": fid,
                    "link": public_link(link, is_md=is_md, collab_on=collab_on),
                    "password": password,
                    "owner": store.owner()["name"],
                    "allowed_modes": allowed_modes,
                }

            if method == "PATCH" and len(rest) == 1:
                lid = rest[0]
                old = links.get(lid)
                fid = tracker.find_by_path(root, path)
                if not old or old.get("fid") != fid:
                    raise ShareError(404, "no such link on this file")
                link, password = links.update(
                    lid,
                    mode=args.get("mode"),
                    days=args.get("days"),
                    on=args.get("on"),
                    name=args.get("name"),
                    new_password=args.get("password"),
                    allowed_modes=allowed_modes,
                )
                return 200, {
                    "link": public_link(link, is_md=is_md, collab_on=collab_on),
                    "password": password,
                }
            raise ShareError(405, "no such route")

        def _withme(self, method: str, rest: list[str], q: dict):
            who = self._who()
            body = self._body() if method in ("POST", "PATCH") else {}
            if rest == [] and method == "GET":
                return 200, {"items": withme.list(refresh=q.get("refresh") != "0")}
            if rest == [] and method == "POST":
                return 201, {"item": withme.add(body.get("link"), body.get("password"), body.get("name"), by=who)}
            if not rest:
                raise ShareError(405, "no such route")
            wid = rest[0]
            if rest[1:] == [] and method == "GET":
                return 200, {"item": withme.status(wid) if q.get("refresh") == "1" else withme.get(wid)}
            if rest[1:] == [] and method == "DELETE":
                return 200, withme.remove(wid)
            if rest[1:] == ["file"] and method == "GET":
                return 200, withme.read_file(wid, who)
            if rest[1:] in (["raw"], ["download"], ["frame"]) and method in ("GET", "HEAD"):
                st, raw, ctype, hdrs = withme.fetch_binary(
                    wid, who, rest[1], range_header=self.headers.get("Range")
                )
                hdrs = list(hdrs) + [
                    ("Cache-Control", "no-store"),
                    ("X-Content-Type-Options", "nosniff"),
                    ("Referrer-Policy", "no-referrer"),
                ]
                self._raw(st, raw, ctype, hdrs)
                return None
            raise ShareError(405, "no such route")

        def _remote_service(self, method: str, rest: list[str], q: dict):
            # Loopback `share.remote` v1 service only (never reachable via /x/).
            if self.headers.get("X-Forwarded-Prefix"):
                raise ShareError(404, "no such route")
            if len(rest) < 2 or rest[1] != "doc":
                raise ShareError(404, "no such route")
            wid = rest[0]
            who = self._who()
            appear = (self.headers.get("X-Loam-Appear") or "names").strip().lower()
            if appear not in ("names", "one"):
                appear = "names"
            body = self._body() if method in ("POST", "PATCH", "PUT") else {}
            args = {**q, **body}
            route = "/".join(rest[1:])
            return withme.collab_request(wid, who, method, route, args, appear=appear)

        def _route(self, method: str):
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            if parts == ["health"]:
                return 200, {"ok": True}
            if parts and parts[0] == "door":
                raise ShareError(404, "no such route")
            if parts == ["config"] and method == "GET":
                return 200, {
                    "reach": reach(),
                    "default_days": default_days(),
                    "collab": bool(collab.capabilities()) if collab else False,
                }
            if parts == ["shared"] and method == "GET":
                self._who()
                return 200, self._shared_list()
            if parts[:2] == ["file", "links"]:
                return self._file_links(method, parts[2:], q)
            if parts == ["file", "seen"] and method == "POST":
                who = self._who()
                if who.get("kind") == "owner":
                    body = self._body()
                    fid = tracker.find_by_path(body.get("root") or "", body.get("path") or "")
                    if fid:
                        links.mark_seen(fid)
                return 200, {"ok": True}
            if parts and parts[0] == "withme":
                return self._withme(method, parts[1:], q)
            if parts and parts[0] == "remote":
                return self._remote_service(method, parts[1:], q)
            raise ShareError(404, "no such route")

        def _door(self, method: str):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2 * 1024 * 1024:
                return self._raw(413, b'{"error":"that request is too large"}', "application/json", [
                    ("X-Robots-Tag", "noindex, nofollow"), ("Cache-Control", "no-store"),
                ])
            body = self.rfile.read(n) if n else b""
            hdrs = {k.lower(): v for k, v in self.headers.items()}
            client = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or self.client_address[0]
            ans = door.handle(method, self.path, hdrs, body, client)
            self._raw(ans.status, ans.body, ans.ctype, ans.headers)

        def _handle(self, method: str):
            if self.headers.get("X-Loam-Door") == "1":
                if not self.path.startswith("/door/"):
                    return self._raw(404, b'{"error":"no such route"}', "application/json", [])
                try:
                    return self._door(method)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                    return self._raw(500, b'{"error":"the share door failed"}', "application/json", [])
            try:
                res = self._route(method)
                if res is None:
                    return
                status, body = res
            except ShareError as exc:
                status, body = exc.status, {"error": exc.message}
                if exc.code:
                    body["code"] = exc.code
                    if exc.code == "need-name":
                        body["suggest"] = store.owner().get("name") or ""
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                status, body = 500, {"error": f"the sharing server failed: {exc}"}
            self._send(status, body)

        def do_GET(self):
            self._handle("GET")

        def do_HEAD(self):
            self._handle("HEAD")

        def do_POST(self):
            self._handle("POST")

        def do_PATCH(self):
            self._handle("PATCH")

        def do_DELETE(self):
            self._handle("DELETE")

    return Handler


class _HTTPServer(ThreadingHTTPServer):
    request_queue_size = 64
    daemon_threads = True


def serve(
    store,
    tracker: FileTracker,
    links: links_mod.Links,
    collab: CollabClient,
    door: Door,
    withme: WithMe,
    port: int,
    *,
    host: str = "127.0.0.1",
    reach=lambda: "everywhere",
    default_days=lambda: links_mod.DEFAULT_DAYS,
) -> ThreadingHTTPServer:
    return _HTTPServer(
        (host, port),
        make_handler(store, tracker, links, collab, door, withme, reach=reach, default_days=default_days),
    )


def main() -> int:
    port = int(os.environ.get("PORT") or os.environ.get("LOAM_PLUGIN_PORT") or 0)
    if not port:
        print("sharing: no PORT given", file=sys.stderr)
        return 2
    store = stores.from_env()
    private = os.environ.get("LOAM_PLUGIN_PRIVATE") or str(Path(os.environ.get("LOAM_PLUGIN_DATA", ".")) / "private")
    settings_dir = Path(os.environ.get("LOAM_PLUGIN_SETTINGS_DIR", "."))
    tracker = FileTracker(store, private)
    links = links_mod.Links(private)
    collab = CollabClient(store)
    door = Door(
        store, tracker, links, collab, Path(__file__).resolve().parent.parent,
        reach=lambda: reach_of(settings_dir),
        owner_name=lambda: store.owner()["name"],
        home=lambda: home_of(settings_dir),
    )
    withme = WithMe(
        private,
        own_tokens=lambda: {l["token"] for l in links.all()},
        door=door,
        owner=store.owner,
        agent=store.agent,
    )
    server = serve(
        store, tracker, links, collab, door, withme, port,
        reach=lambda: reach_of(settings_dir),
        default_days=lambda: default_days_of(settings_dir),
    )
    print(f"sharing: listening on 127.0.0.1:{port}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
