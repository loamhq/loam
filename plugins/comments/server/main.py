#!/usr/bin/env python3
"""The Comments & suggestions plugin server.

Listens on the loopback port Loam gives it (``PORT``).

Owner & agent routes (reached by the page at ``/x/comments/`` behind Loam's
login, or on loopback by an agent's MCP tools with ``X-Loam-Agent: <id>``)::

    GET    /health
    GET    /config
    GET    /appear                        default appear setting, or per-file/withme
    PATCH  /appear                        set per-file or per-withme appear choice
    GET    /doc                           file/doc notes (and text for Markdown)
    GET    /doc/revision                  revision poll (text + notes)
    POST   /doc/notes                     add a comment (or suggestion on Markdown)
    POST   /doc/notes/<id>/replies        reply to a thread
    PATCH  /doc/notes/<id>                edit your own comment or reply
    DELETE /doc/notes/<id>/replies/<r>    delete your own reply
    POST   /doc/notes/<id>/<action>       resolve, reopen, accept, reject, reattach, withdraw, remove
    POST   /doc/save                      three-way merge save (Markdown)
    GET    /doc/history                   save history and authorship (Markdown)
    GET    /doc/version?save=<id>         text at a saved version (Markdown)
    POST   /doc/restore                   restore an earlier version (Markdown)
    *      /remote/<wid>/doc…             same operations on a file shared from another Loam
                                          (proxied via the public ``share.remote`` service)

Loopback service ``share.collaboration`` v1 (never exposed via ``/x/``)::

    GET    /collab/capabilities           supported modes and share-page UI entry
    POST   /collab/summary                open/resolved/new counts for shared files
    GET    /collab/appear                 effective appear choice + agent ids for a file
    GET    /collab/asset/<rel>            ES modules and CSS for the share page
    *      /collab/doc…                   visitor collaboration (actor in ``X-Loam-Share-Actor``,
                                          target file in ``X-Loam-Share-File``)
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from comments import appear as appear_mod  # noqa: E402
from comments import frontmatter as fm  # noqa: E402
from comments import store as stores  # noqa: E402
from comments.remote import RemoteClient  # noqa: E402
from comments.service import Service, is_markdown  # noqa: E402
from comments.sidecar import NoteError  # noqa: E402

BODY_MAX = 8 * 1024 * 1024
COLLAB_PAGE_ROOTS = ("share-collab.js",)
IMPORT_RE = re.compile(
    r"""(?:^|[\s;])(?:import|export)\s[^'"]*?from\s*['"](\.{1,2}/[^'"]+)['"]|(?:^|[\s;])import\s*['"](\.{1,2}/[^'"]+)['"]""",
    re.M,
)
_ASSET_CACHE: dict[str, tuple[float, set[str]]] = {}


def _collab_page_files(page_dir: Path) -> set[str]:
    key = str(page_dir)
    hit = _ASSET_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < 5:
        return hit[1]
    base = Path(page_dir).resolve()
    seen: set[str] = set()
    todo = list(COLLAB_PAGE_ROOTS)
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        f = (base / rel).resolve()
        if base not in f.parents or not f.is_file():
            continue
        seen.add(rel)
        if f.suffix != ".js":
            continue
        for m in IMPORT_RE.finditer(f.read_text(encoding="utf-8", errors="replace")):
            spec = m.group(1) or m.group(2)
            target = (f.parent / spec).resolve()
            if base in target.parents:
                todo.append(target.relative_to(base).as_posix())
    _ASSET_CACHE[key] = (time.monotonic(), seen)
    return seen


def _settings_values(settings_dir: Path) -> dict:
    try:
        values = json.loads((settings_dir / "values.json").read_text(encoding="utf-8"))
        return values if isinstance(values, dict) else {}
    except (OSError, ValueError):
        return {}


def reach_of(settings_dir: Path) -> str:
    return "staging" if _settings_values(settings_dir).get("reach") == "staging" else "everywhere"


def appear_of(settings_dir: Path) -> str:
    return appear_mod.effective(_settings_values(settings_dir).get("appear"), appear_mod.DEFAULT)


def _front(text: str) -> int:
    m = fm.FRONTMATTER.match(text or "")
    if not m:
        return 0
    end = m.end()
    if text.startswith("\r\n", end):
        return end + 2
    return end + 1 if text.startswith("\n", end) else end


def _shift_in(args: dict, front: str) -> dict:
    if not front or not isinstance(args, dict):
        return args
    out = dict(args)
    for k in ("start", "end"):
        if isinstance(out.get(k), int):
            out[k] = out[k] + len(front)
    for k in ("text", "base_text"):
        if isinstance(out.get(k), str) and "base_revision" in out:
            body = out[k]
            if k == "text" and body.startswith("---"):
                body = "\n" + body
            out[k] = front + body
    if isinstance(out.get("changes"), list):
        n = len(front)
        out["changes"] = [
            [c[0] + n, c[1] + n, c[2]]
            for c in out["changes"]
            if isinstance(c, list) and len(c) == 3 and all(isinstance(x, int) for x in c[:2])
        ]
    return out


def _body_only(out: dict, cut: int) -> dict:
    out = dict(out)
    out["text"] = out["text"][cut:]
    notes = []
    for n in out.get("notes") or []:
        n = dict(n)
        w = dict(n.get("where") or {})
        for k in ("start", "end"):
            if isinstance(w.get(k), int):
                w[k] = max(0, w[k] - cut)
        n["where"] = w
        if isinstance(n.get("anchor"), dict):
            n["anchor"] = {"quote": n["anchor"].get("quote", "")}
        notes.append(n)
    if "notes" in out:
        out["notes"] = notes
    return out


def _for_visitor(out):
    if not isinstance(out, dict):
        return out
    out = {k: v for k, v in out.items() if k not in ("root", "path", "row", "copy_of")}
    if isinstance(out.get("text"), str):
        out = _body_only(out, _front(out["text"]))
    if "people" in out:
        named = {
            s.get("author", {}).get("id") if isinstance(s.get("author"), dict) else s.get("author")
            for s in out.get("saves", []) + out.get("authors", [])
        }
        out["people"] = {k: v for k, v in out["people"].items() if k in named}
    return out


def make_handler(
    service: Service,
    plugin_dir: Path,
    appear_store: appear_mod.AppearStore | None = None,
    remote: RemoteClient | None = None,
    reach=lambda: "everywhere",
    appear_default=lambda: appear_mod.DEFAULT,
):
    page_dir = Path(plugin_dir) / "page"
    assets_dir = Path(plugin_dir) / "assets"

    if appear_store is not None:
        service.store.on_move_callback = appear_store.move_path

    def appear_info() -> dict:
        owner = service.store.owner()["name"]
        return {
            "appear": appear_mod.effective(None, appear_default()),
            "owner": owner,
            "alias": appear_mod.alias_name(owner),
        }

    def appear_of_file(root: str, path: str, fid: str | None = None) -> dict:
        info = appear_info()
        fid = fid or service.file_id_for(root, path)
        choice = (
            appear_store.get(f"file:{fid}" if fid else None, f"path:{root}:{path}")
            if appear_store is not None
            else "default"
        )
        return {
            "choice": choice,
            "default": info["appear"],
            "as": appear_mod.effective(choice, info["appear"]),
            "alias": info["alias"],
        }

    def appear_of_withme(wid: str) -> dict:
        info = appear_info()
        choice = appear_store.get(f"withme:{wid}") if appear_store is not None else "default"
        return {
            "choice": choice,
            "default": info["appear"],
            "as": appear_mod.effective(choice, info["appear"]),
            "alias": info["alias"],
        }

    def masked(root: str, path: str, out):
        ap = appear_of_file(root, path)
        if ap["as"] != "one":
            return out
        agents = [pid for pid, p in (service.store.people() or {}).items() if p.get("kind") == "agent"]
        return appear_mod.mask(out, agents, appear_mod.alias(service.store.owner()["name"]))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "loam-comments"
        sys_version = ""

        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.command, fmt % args))

        def end_headers(self):
            if self.close_connection:
                self.send_header("Connection", "close")
            super().end_headers()

        def _send(self, status: int, body: dict):
            data = json.dumps(body, ensure_ascii=False).encode()
            self._raw(status, data, "application/json; charset=utf-8")

        def _raw(self, status: int, body: bytes, ctype: str, headers=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (headers or ()):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

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

        def _collab_asset(self, rel: str):
            if ".." in rel or "//" in rel or "\\" in rel or "%" in rel:
                return self._raw(404, b"not found", "text/plain; charset=utf-8")
            if rel.startswith("assets/"):
                name = rel[len("assets/"):]
                if name not in ("comments.css", "icon.svg"):
                    return self._raw(404, b"not found", "text/plain; charset=utf-8")
                base = assets_dir
            elif rel.startswith("page/"):
                name = rel[len("page/"):]
                if name not in _collab_page_files(page_dir):
                    return self._raw(404, b"not found", "text/plain; charset=utf-8")
                base = page_dir
            else:
                return self._raw(404, b"not found", "text/plain; charset=utf-8")
            f = (base / name).resolve()
            if base.resolve() not in f.parents or not f.is_file():
                return self._raw(404, b"not found", "text/plain; charset=utf-8")
            ctype = (
                "text/javascript; charset=utf-8"
                if f.suffix == ".js"
                else "text/css; charset=utf-8"
                if f.suffix == ".css"
                else mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            )
            self._raw(200, f.read_bytes(), ctype)

        def _collab(self, method: str, rest: list[str], q: dict):
            # Never reachable through the browser /x/ gateway.
            if self.headers.get("X-Forwarded-Prefix"):
                raise NoteError(404, "no such route")
            if rest == ["capabilities"] and method == "GET":
                return 200, {
                    "interface": "share.collaboration",
                    "version": 1,
                    "plugin": "comments",
                    "modes": {"markdown": ["view", "comment", "edit"], "other": ["view", "comment"]},
                    "page": {"entry": "page/share-collab.js", "styles": ["assets/comments.css"]},
                }
            if rest == ["summary"] and method == "POST":
                body = self._body()
                items = body.get("items")
                if not isinstance(items, list):
                    raise NoteError(400, "items must be a list")
                return 200, {"items": service.summary(items)}
            if rest == ["appear"] and method == "GET":
                root, path = q.get("root"), q.get("path")
                if not root or not path:
                    raise NoteError(400, "name the file: root and path")
                agents = [
                    {"id": pid, "name": p.get("name", "")}
                    for pid, p in (service.store.people() or {}).items()
                    if p.get("kind") == "agent"
                ]
                return 200, {
                    **appear_of_file(root, path),
                    "owner": service.store.owner()["name"],
                    "agents": agents,
                }
            if rest[:1] == ["asset"] and method == "GET":
                self._collab_asset("/".join(rest[1:]))
                return None
            if rest[:1] != ["doc"]:
                raise NoteError(404, "no such route")
            raw_actor = self.headers.get("X-Loam-Share-Actor")
            raw_file = self.headers.get("X-Loam-Share-File")
            if not raw_actor or not raw_file:
                raise NoteError(403, "missing share actor or file header")
            try:
                actor = json.loads(raw_actor)
                fdesc = json.loads(raw_file)
            except ValueError:
                raise NoteError(400, "invalid share context header") from None
            if (
                not isinstance(actor, dict)
                or actor.get("kind") not in ("guest", "guest-agent")
                or not isinstance(actor.get("id"), str)
                or not isinstance(actor.get("name"), str)
                or actor.get("mode") not in ("view", "comment", "edit")
            ):
                raise NoteError(403, "invalid share actor")
            if not isinstance(fdesc, dict) or fdesc.get("root") != "vault" or not isinstance(fdesc.get("path"), str) or not fdesc["path"]:
                raise NoteError(403, "invalid share file")
            root, path = fdesc["root"], fdesc["path"]
            sub = rest[1:]
            body = self._body() if method in ("POST", "PATCH", "PUT") else {}
            args = {**q, **body}
            args.pop("root", None)
            args.pop("path", None)
            # Sidecar size limit for external link writes
            if method == "POST" and sub[:1] in (["notes"], ["save"]) and service.sidecar_too_large(root, path):
                raise NoteError(507, "this file's notes have grown as large as a link may make them")
            # For Markdown, keep frontmatter invisible and unchangeable by visitors
            if is_markdown(path):
                stored = service.store.read_doc(root, path)["text"]
                cut = _front(stored)
                args = _shift_in(args, stored[:cut])
            status, out = self._visitor_op(method, sub, actor, root, path, args)
            return status, masked(root, path, _for_visitor(out))

        def _visitor_op(self, method: str, rest: list[str], who: dict, root: str, path: str, args: dict):
            # History, version and restore are owner-only and never exposed to visitors.
            if method == "GET" and rest == []:
                return 200, service.get(who, root, path)
            if method == "GET" and rest == ["revision"]:
                return 200, service.revision(who, root, path)
            if method == "POST" and rest == ["save"]:
                return 200, service.save(who, root, path, args)
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
                    act = {
                        "resolve": service.resolve,
                        "reopen": service.reopen,
                        "accept": service.accept,
                        "reject": service.reject,
                        "withdraw": service.withdraw,
                        "remove": service.remove,
                    }.get(rest[2])
                    if act:
                        return 200, act(who, root, path, tid)
                    if rest[2] == "reattach":
                        return 200, service.reattach(who, root, path, tid, args)
            raise NoteError(404, "no such route")

        def _doc_op(self, method: str, rest: list[str], who: dict, root: str, path: str, args: dict, url):
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
                    act = {
                        "resolve": service.resolve,
                        "reopen": service.reopen,
                        "accept": service.accept,
                        "reject": service.reject,
                        "withdraw": service.withdraw,
                        "remove": service.remove,
                    }.get(rest[2])
                    if act:
                        return 200, act(who, root, path, tid)
                    if rest[2] == "reattach":
                        return 200, service.reattach(who, root, path, tid, args)
            raise NoteError(405, f"no route for {method} {url.path}")

        def _route(self, method: str):
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            if parts == ["health"]:
                return 200, {"ok": True}
            if parts == ["config"] and method == "GET":
                return 200, {"reach": reach(), "appear": appear_info()}
            if parts and parts[0] == "collab":
                return self._collab(method, parts[1:], q)
            if parts == ["appear"]:
                self._who()
                if method == "GET":
                    if q.get("withme"):
                        return 200, {"appear": appear_of_withme(q["withme"])}
                    if q.get("root") and q.get("path"):
                        s = service.summary([{"root": q["root"], "path": q["path"]}]).get(f"{q['root']}:{q['path']}", {})
                        return 200, {
                            "appear": appear_of_file(q["root"], q["path"]),
                            "open": s.get("open", 0),
                            "resolved": s.get("resolved", 0),
                        }
                    return 200, appear_info()
                if method == "PATCH":
                    if appear_store is None:
                        raise NoteError(503, "appear settings are not configured here")
                    body = self._body()
                    value = body.get("appear")
                    if value not in appear_mod.CHOICES + ("default",):
                        raise NoteError(400, "appear is names, one or default")
                    if body.get("withme"):
                        wid = str(body["withme"])
                        if not re.fullmatch(r"w-[a-z0-9]{10}", wid):
                            raise NoteError(400, "not a Shared with me id")
                        appear_store.set(f"withme:{wid}", value)
                        return 200, {"appear": appear_of_withme(wid)}
                    root, path = body.get("root"), body.get("path")
                    if not isinstance(root, str) or not isinstance(path, str) or not path:
                        raise NoteError(400, "name the file: root and path (or withme)")
                    fid = service.file_id_for(root, path)
                    if fid:
                        appear_store.set(f"file:{fid}", value, clear_keys=(f"path:{root}:{path}",))
                    else:
                        appear_store.set(f"path:{root}:{path}", value)
                    return 200, {"appear": appear_of_file(root, path, fid=fid)}
                raise NoteError(405, "no such route")
            if len(parts) >= 3 and parts[0] == "remote" and parts[2] == "doc":
                if remote is None:
                    raise NoteError(503, "Turn on the Sharing plugin to work on files shared from another Loam.")
                wid = parts[1]
                if not re.fullmatch(r"w-[a-z0-9]{10}", wid):
                    raise NoteError(404, "no such shared file")
                who = self._who()
                body = self._body() if method in ("POST", "PATCH", "PUT") else {}
                args = {**q, **body}
                ap = appear_of_withme(wid)
                route = "/".join(parts[2:])
                status, out = remote.call(wid, who, method, route, args, appear=ap["as"])
                if isinstance(out, dict):
                    out["appear"] = ap
                return status, out
            if not parts or parts[0] != "doc":
                raise NoteError(404, "no such route")
            body = self._body() if method in ("POST", "PATCH", "PUT") else {}
            args = {**q, **body}
            root, path = args.get("root"), args.get("path")
            if not isinstance(root, str) or not isinstance(path, str) or not path:
                raise NoteError(400, "name the file: root and path")
            who = self._who()
            status, out = self._doc_op(method, parts[1:], who, root, path, args, url)
            if isinstance(out, dict) and "notes" in out:
                out["appear"] = appear_of_file(root, path, fid=out.get("id"))
            return status, out

        def _handle(self, method: str):
            try:
                res = self._route(method)
                if res is None:
                    return
                status, body = res
            except NoteError as exc:
                status, body = exc.status, {"error": exc.message}
                if getattr(exc, "code", None):
                    body["code"] = exc.code
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                status, body = 500, {"error": f"the comments server failed: {exc}"}
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


class _HTTPServer(ThreadingHTTPServer):
    request_queue_size = 64
    daemon_threads = True


def serve(
    service: Service,
    port: int,
    plugin_dir: Path | None = None,
    host: str = "127.0.0.1",
    appear_store: appear_mod.AppearStore | None = None,
    remote: RemoteClient | None = None,
    reach=lambda: "everywhere",
    appear_default=lambda: appear_mod.DEFAULT,
) -> ThreadingHTTPServer:
    pdir = Path(plugin_dir) if plugin_dir else Path(__file__).resolve().parent.parent
    return _HTTPServer(
        (host, port),
        make_handler(service, pdir, appear_store=appear_store, remote=remote, reach=reach, appear_default=appear_default),
    )


def main() -> int:
    port = int(os.environ.get("PORT") or os.environ.get("LOAM_PLUGIN_PORT") or 0)
    if not port:
        print("comments: no PORT given", file=sys.stderr)
        return 2
    store = stores.from_env()
    service = Service(store)
    private = os.environ.get("LOAM_PLUGIN_PRIVATE") or str(Path(os.environ.get("LOAM_PLUGIN_DATA", ".")) / "private")
    appear_store = appear_mod.AppearStore(private)
    remote = RemoteClient(store)
    settings_dir = Path(os.environ.get("LOAM_PLUGIN_SETTINGS_DIR", "."))
    server = serve(
        service,
        port,
        plugin_dir=Path(__file__).resolve().parent.parent,
        appear_store=appear_store,
        remote=remote,
        reach=lambda: reach_of(settings_dir),
        appear_default=lambda: appear_of(settings_dir),
    )
    print(f"comments: listening on 127.0.0.1:{port}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
