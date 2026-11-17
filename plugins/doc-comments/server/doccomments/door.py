"""The share door: what a person outside Loam, and their agent, reach
through a share link, ``/s/<token>``.

Loam's plugin gateway sends every request under ``/s/`` here (core's door,
docs/plugins.md, "The share door"), without the owner's login, as
``/door/<token><rest>``. Everything a visitor can do is decided here, from
the link alone:

- **One doc per link.** The doc is the one the link was made for, found by
  the id in its sidecar; nothing in a request names a root, a path or
  another doc, and anything that tries is ignored. A link that is off, a
  token that does not exist, a session from another link or past its days,
  and a page on a host the plugin is not yet open on, all answer the same
  404, so a stranger cannot tell one from another.
- **The password, then a session.** A right password sets an HMAC-signed
  cookie scoped to the link's path, good for the link's days and only while
  the link is on. Wrong passwords are slowed, then blocked (links.py).
- **An agent's key**, ``Authorization: Bearer lak_…``, acts as "<Name>'s
  agent" with the link's mode, and stops with the link.
- **Writes** need the custom header ``X-Loam-Share: 1`` from a browser (so
  another site cannot post with the visitor's cookie), count against the
  link's limits, and are refused when the doc's sidecar is already large.

Nothing here is HTML-from-text: the pages are fixed templates with the
title and names escaped, and the doc and its notes reach the page as JSON
and are drawn as text.
"""

from __future__ import annotations

import html
import json
import mimetypes
import re
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs

from . import links as links_mod
from . import frontmatter as fm
from .sidecar import NoteError

COOKIE = "loam_share"
ROBOTS = "noindex, nofollow, noarchive, nosnippet"
BODY_MAX = 1536 * 1024
STATIC = {
    "editor/editor.js", "editor/dom.js", "editor/live.js", "editor/format.js", "editor/textdiff.js",
    "vendor/codemirror.js", "api.js", "share/share.js", "share/share.css",
}
PAGE_POLICY = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
               "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
               "form-action 'self'; frame-ancestors 'none'")


class Answer:
    def __init__(self, status=200, body=b"", ctype="application/json; charset=utf-8", headers=None):
        self.status = status
        self.body = body if isinstance(body, bytes) else body.encode()
        self.ctype = ctype
        self.headers = list(headers or [])


def _json(status, obj, headers=None):
    return Answer(status, json.dumps(obj, ensure_ascii=False), headers=headers)


class Door:
    def __init__(self, service, links, plugin_dir: Path, reach=lambda: "staging",
                 owner_name=lambda: "Loam", sleep=time.sleep):
        self.svc = service
        self.links = links
        self.page_dir = Path(plugin_dir) / "page"
        self.assets_dir = Path(plugin_dir) / "assets"
        self.reach = reach
        self.owner_name = owner_name
        self.sleep = sleep

    # -- the request ------------------------------------------------------------------

    def handle(self, method: str, path: str, headers: dict, body: bytes, client: str) -> Answer:
        try:
            ans = self._handle(method, path, headers, body, client)
        except NoteError as exc:
            ans = _json(exc.status, {"error": exc.message})
        ans.headers += [("X-Robots-Tag", ROBOTS), ("Referrer-Policy", "no-referrer"),
                        ("X-Content-Type-Options", "nosniff"), ("Cache-Control", "no-store"),
                        ("X-Frame-Options", "DENY")]
        return ans

    def _not_found(self):
        return Answer(404, _page("Not found", "<main class=\"dc-share-gone\"><p>This link does not "
                                 "open anything.</p></main>", None, None), "text/html; charset=utf-8",
                      [("Content-Security-Policy", PAGE_POLICY)])

    def _handle(self, method, path, headers, body, client):
        m = re.match(r"^/door/([A-Za-z0-9_-]{32,64})(/[^?]*)?(\?.*)?$", path)
        if not m:
            return self._not_found()
        token, rest, query = m.group(1), m.group(2) or "/", (m.group(3) or "")[1:]
        if ".." in rest or "//" in rest or "\\" in rest or "%" in rest:
            return self._not_found()
        host = (headers.get("host") or "").split(":")[0].lower()
        if self.reach() != "everywhere" and not host.startswith("next."):
            return self._not_found()
        if rest.startswith("/_/") and method == "GET":
            return self._static(rest[3:])
        link = self.links.by_token(token)
        if not link or not link["on"]:
            return self._not_found()
        doc_where = self._doc(link)
        if doc_where is None:
            return self._not_found()
        prefix = f"/s/{token}"
        session = self.links.read_session(link, _cookie(headers))
        agent_visitor = self.links.agent_for(link, _bearer(headers))

        if rest == "/" and method == "GET":
            return self._shell(link, session, doc_where, prefix)
        if rest == "/agent.md" and method == "GET":
            if not session and not agent_visitor:
                return self._not_found()
            return Answer(200, agent_guide(prefix, link["mode"], _origin(headers)),
                          "text/markdown; charset=utf-8")
        if rest == "/open" and method == "POST":
            return self._open(link, body, client, prefix)
        if rest == "/forget" and method == "POST":
            return _json(200, {"ok": True}, [("Set-Cookie", _cookie_header("", 0, prefix))])
        if not rest.startswith("/api/"):
            return self._not_found()

        # Who is asking: the agent by its key, or the browser by its session.
        if agent_visitor:
            who = self._who(link, agent_visitor, agent=True)
        elif session:
            visitor = link["name"] or session.get("v")
            if rest == "/api/name" and method == "POST" and not link["name"]:
                return self._name(link, body, headers, prefix)
            if not visitor:
                return _json(409, {"error": "say your name first", "need": "name"})
            who = self._who(link, visitor)
        else:
            return _json(401, {"error": "this link needs its password"})
        writes = method in ("POST", "PATCH", "DELETE")
        if writes and not agent_visitor and headers.get("x-loam-share") != "1":
            return _json(403, {"error": "a write from the page carries X-Loam-Share: 1"})
        if len(body) > BODY_MAX:
            return _json(413, {"error": "that request is too large"})
        args = _body(body) if writes else {k: v[-1] for k, v in parse_qs(query).items()}
        root, dpath = doc_where
        api = rest[len("/api/"):].strip("/").split("/")
        if api == ["agent-key"] and method == "POST" and not agent_visitor:
            self.links.count_write(link["id"])
            key = self.links.new_agent_key(link["id"], who["name"])
            return _json(201, {"key": key, "doc": _origin(headers) + prefix,
                               "guide": _origin(headers) + prefix + "/agent.md",
                               "as": f"{who['name']}'s agent", "mode": link["mode"]})
        if api == ["me"] and method == "GET":
            return _json(200, {"me": who, "mode": link["mode"], "link": link["id"]})
        if not api or api[0] != "doc":
            return _json(404, {"error": "no such route"})
        if writes:
            self.links.count_write(link["id"])
            if api[1:2] in (["notes"], ["save"]) and self._big(link):
                return _json(507, {"error": "this doc's notes have grown as large as a link may make them"})
        out = self._route(method, api[1:], args, who, root, dpath)
        created = method == "POST" and (api[1:] == ["notes"] or api[-1:] == ["replies"])
        return _json(201 if created else 200, _for_visitor(out))

    def _route(self, method, api, args, who, root, path):
        svc = self.svc
        if method == "GET" and api == []:
            return svc.get(who, root, path)
        if method == "GET" and api == ["history"]:
            return svc.history(who, root, path)
        if method == "GET" and api == ["version"]:
            return svc.version(who, root, path, args.get("save", ""))
        if method == "POST" and api == ["save"]:
            return svc.save(who, root, path, args)
        if method == "POST" and api == ["notes"]:
            return svc.add(who, root, path, args)
        if len(api) >= 2 and api[0] == "notes":
            tid = api[1]
            if method == "PATCH" and len(api) == 2:
                return svc.edit(who, root, path, tid, args.get("reply"), args.get("text"))
            if method == "POST" and api[2:] == ["replies"]:
                return svc.reply(who, root, path, tid, args.get("text"))
            if method == "DELETE" and len(api) == 4 and api[2] == "replies":
                return svc.delete_reply(who, root, path, tid, api[3])
            if method == "POST" and len(api) == 3:
                act = {"resolve": svc.resolve, "reopen": svc.reopen, "accept": svc.accept,
                       "reject": svc.reject}.get(api[2])
                if act:
                    return act(who, root, path, tid)
                if api[2] == "reattach":
                    return svc.reattach(who, root, path, tid, args)
        raise NoteError(404, "no such route")

    # -- pieces -------------------------------------------------------------------------

    def _doc(self, link):
        """The link's doc, ``(root, path)``, or None when it is gone or its
        id no longer matches (a copy, a hand edit)."""
        got = self.svc.store.read_sidecar(link["did"])
        if not got:
            return None
        sc = got[0]
        where = sc.get("doc") or {}
        root, path = where.get("root"), where.get("path")
        if root not in ("vault",) or not isinstance(path, str) or not path.lower().endswith((".md", ".markdown")):
            return None
        try:
            doc = self.svc.store.read_doc(root, path)
        except NoteError:
            return None
        if fm.doc_id(doc["text"]) != link["did"]:
            return None
        return root, path

    def _big(self, link) -> bool:
        got = self.svc.store.read_sidecar(link["did"])
        return bool(got) and len(json.dumps(got[0])) > links_mod.SIDECAR_MAX_BYTES

    def _who(self, link, visitor, agent=False):
        base = {"mode": link["mode"], "link": link["id"]}
        if agent:
            return {"id": f"guest-agent:{link['id']}:{links_mod.slug(visitor)}",
                    "name": f"{visitor}'s agent", "kind": "guest-agent", **base}
        return {"id": f"guest:{link['id']}:{links_mod.slug(visitor)}", "name": visitor,
                "kind": "guest", **base}

    def _open(self, link, body, client, prefix):
        args = _body(body)
        ok, wait, refusal = self.links.check_password(link, args.get("password"), client)
        if wait:
            self.sleep(wait)
        if refusal:
            return _json(429, {"error": refusal})
        if not ok:
            return _json(403, {"error": "That password does not open this link."})
        value, age = self.links.session_cookie(link, link["name"])
        if link["name"]:
            self.links.join(link["id"], link["name"])
        return _json(200, {"ok": True, "need": None if link["name"] else "name"},
                     [("Set-Cookie", _cookie_header(value, age, prefix))])

    def _name(self, link, body, headers, prefix):
        if headers.get("x-loam-share") != "1":
            return _json(403, {"error": "a write from the page carries X-Loam-Share: 1"})
        name = links_mod.clean_name(_body(body).get("name"))
        self.links.count_write(link["id"])
        self.links.join(link["id"], name)
        value, age = self.links.session_cookie(link, name)
        return _json(200, {"ok": True}, [("Set-Cookie", _cookie_header(value, age, prefix))])

    def _shell(self, link, session, doc_where, prefix):
        doc = self.svc.store.read_doc(*doc_where)
        title = _title(doc["text"], doc_where[1])
        owner = self.owner_name()
        if not session:
            state = "gate"
        elif not (link["name"] or session.get("v")):
            state = "name"
        else:
            state = "doc"
        boot = {"state": state, "title": title, "owner": owner, "days": link["days"],
                "mode": link["mode"], "named": bool(link["name"]), "prefix": prefix,
                "name": link["name"] or session.get("v") if session else None}
        return Answer(200, _page(f"{title} · {owner}", '<div id="dc-share"></div>', boot, prefix),
                      "text/html; charset=utf-8", [("Content-Security-Policy", PAGE_POLICY)])

    def _static(self, rel):
        if rel.startswith("assets/"):
            base, name = self.assets_dir, rel[len("assets/"):]
            if name != "doc-comments.css":
                return Answer(404, b"not found", "text/plain")
        else:
            base, name = self.page_dir, rel
            if name not in STATIC:
                return Answer(404, b"not found", "text/plain")
        f = (base / name).resolve()
        if base.resolve() not in f.parents or not f.is_file():
            return Answer(404, b"not found", "text/plain")
        ctype = "text/javascript; charset=utf-8" if f.suffix == ".js" else (
            mimetypes.guess_type(str(f))[0] or "application/octet-stream")
        return Answer(200, f.read_bytes(), ctype)


# -- helpers ------------------------------------------------------------------------------

def _cookie(headers):
    raw = headers.get("cookie") or ""
    try:
        c = SimpleCookie()
        c.load(raw)
    except Exception:  # noqa: BLE001 -- a bad cookie is no cookie
        return None
    return c[COOKIE].value if COOKIE in c else None


def _cookie_header(value, age, prefix):
    return (f"{COOKIE}={value}; Path={prefix}; Max-Age={age}; HttpOnly; Secure; SameSite=Lax")


def _bearer(headers):
    a = headers.get("authorization") or ""
    return a[7:].strip() if a.lower().startswith("bearer ") else None


def _origin(headers):
    host = re.sub(r"[^A-Za-z0-9.:-]", "", headers.get("host") or "")
    return f"https://{host}"


def _body(body: bytes) -> dict:
    if not body:
        return {}
    try:
        data = json.loads(body)
    except ValueError:
        raise NoteError(400, "the body is not JSON") from None
    if not isinstance(data, dict):
        raise NoteError(400, "the body is a JSON object")
    return data


def _title(text: str, path: str) -> str:
    for line in text.splitlines():
        m = re.match(r"^#\s+(.+?)\s*#*\s*$", line)
        if m:
            return m.group(1)[:200]
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0][:200]


def _for_visitor(out):
    """An answer as a visitor may see it: no root, path, file row or copy
    note (where the doc lives in the vault is not theirs), and of the
    people, only those whose words are on this doc."""
    if not isinstance(out, dict):
        return out
    out = {k: v for k, v in out.items() if k not in ("root", "path", "row", "copy_of")}
    if "people" in out:
        named = {s.get("author", {}).get("id") if isinstance(s.get("author"), dict) else s.get("author")
                 for s in out.get("saves", []) + out.get("authors", [])}
        out["people"] = {k: v for k, v in out["people"].items() if k in named}
    return out


def _page(title, body, boot, prefix):
    css = f'<link rel="stylesheet" href="{prefix}/_/assets/doc-comments.css">' \
          f'<link rel="stylesheet" href="{prefix}/_/share/share.css">' if prefix else ""
    script = f'<script type="module" src="{prefix}/_/share/share.js"></script>' if prefix else ""
    data = ""
    if boot is not None:
        data = ('<script type="application/json" id="dc-boot">'
                + json.dumps(boot, ensure_ascii=False).replace("<", "\\u003c") + "</script>")
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
            f"<meta name=\"robots\" content=\"{ROBOTS}\"><meta name=\"referrer\" content=\"no-referrer\">"
            f"<title>{html.escape(title)}</title>{css}</head><body>{body}{data}{script}</body></html>")


def agent_guide(prefix: str, mode: str, origin: str) -> str:
    """Plain instructions for any agent: HTTP and JSON, nothing to install."""
    doc = origin + prefix
    can = {"view": "read the doc and its notes",
           "comment": "read the doc and its notes, comment, suggest changes and reply",
           "edit": "read, comment, suggest, reply, edit the text, and accept or reject suggestions"}[mode]
    lines = [
        "# Working on a shared doc",
        "",
        f"You can {can}. Send your key on every request:",
        "",
        "    Authorization: Bearer <your key>",
        "",
        "Everything is JSON. Your notes show as your person's agent.",
        "",
        "## Read",
        "",
        f"    curl -s -H \"Authorization: Bearer $KEY\" {doc}/api/doc",
        "",
        "Answers the doc's Markdown (`text`), its `revision`, and its `notes`:",
        "each has an `id`, `kind` (comment or suggestion), `author`, `state`,",
        "`anchor.quote` (the words it is about), `replacement` (a suggestion's",
        "new words) and `replies`.",
    ]
    if mode in ("comment", "edit"):
        lines += [
            "",
            "## Comment, suggest, reply",
            "",
            "Name a passage by its exact words (`quote`); add `occurrence` (1, 2, …)",
            "when they appear more than once.",
            "",
            f"    curl -s -X POST -H \"Authorization: Bearer $KEY\" {doc}/api/doc/notes \\",
            "      -d '{\"kind\":\"comment\",\"quote\":\"the exact words\",\"text\":\"Your comment\"}'",
            "",
            f"    curl -s -X POST -H \"Authorization: Bearer $KEY\" {doc}/api/doc/notes \\",
            "      -d '{\"kind\":\"suggestion\",\"quote\":\"old words\",\"replacement\":\"new words\",\"text\":\"why\"}'",
            "",
            f"    curl -s -X POST -H \"Authorization: Bearer $KEY\" {doc}/api/doc/notes/<id>/replies \\",
            "      -d '{\"text\":\"Your reply\"}'",
            "",
            f"Resolve a comment you made: POST {doc}/api/doc/notes/<id>/resolve",
        ]
    if mode == "edit":
        lines += [
            "",
            "## Edit",
            "",
            "Send the whole new text with the revision you read; if someone saved",
            "first, changes to different passages both land and yours becomes a",
            "suggestion where you both changed the same words.",
            "",
            f"    curl -s -X POST -H \"Authorization: Bearer $KEY\" {doc}/api/doc/save \\",
            "      -d '{\"base_revision\":\"sha256:…\",\"text\":\"…the whole doc…\"}'",
            "",
            f"Accept or reject a suggestion: POST {doc}/api/doc/notes/<id>/accept (or /reject)",
        ]
    lines += ["", "The owner can turn this link off at any time; then your key stops working.", ""]
    return "\n".join(lines)
