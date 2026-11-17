#!/usr/bin/env python3
"""The plugin's real server over the dev server's seeded, in-memory docs
(dev_server.py), with share links on two docs (a named one, a group link
with two people, a view link) and a second save for History, for
look-browser.mjs. Nothing here touches a real vault or a real door.

Run: python3 plugins/doc-comments/test/look_server.py <plugin dir> [port]
(0 picks a free one; the line it prints names it)"""
import json, sys, tempfile
from pathlib import Path
from http.server import ThreadingHTTPServer
PLUGIN = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(PLUGIN / "server"))
sys.path.insert(0, str(PLUGIN / "test"))
import dev_server  # noqa
import main as server_main  # noqa
from doccomments.door import Door  # noqa
from doccomments.links import Links  # noqa
from doccomments import links as links_mod  # noqa

svc = dev_server.seeded()
links = Links(tempfile.mkdtemp(prefix="dc-look-links-"))
door = Door(svc, links, PLUGIN, reach=lambda: "everywhere", owner_name=lambda: "Sam", sleep=lambda s: None)
owner = svc.store.owner()
did = svc.get(owner, "vault", "Essays/drafts/On staying.md")["id"]
out = {}
for mode, name in (("comment", "Robin"), ("edit", None), ("view", "Jo Marsh")):
    link, token, pw = links.create(did, mode, name, 30, "pass-" + mode)
    out[mode] = {"path": f"/s/{token}", "password": "pass-" + mode, "id": link["id"] if isinstance(link, dict) else None}
group = [l for l in links.for_doc(did) if not l.get("name")][0]["id"]
links._mutate(lambda d: d["links"][group].setdefault("people", []).extend([{"name": "Noor"}, {"name": "Ade"}]))
svc.sync_links(did, [links_mod.public(l) for l in links.for_doc(did)])
lid = svc.ensure_id(owner, "vault", "Essays/drafts/Long.md")
links.create(lid, "comment", "Kim", 30, "pass-long")
svc.sync_links(lid, [links_mod.public(l) for l in links.for_doc(lid)])
# a second save by Ada, so History has a version to read
p = ("vault", "Essays/drafts/On staying.md")
cur = svc.get(owner, *p)
rev = svc.revision(owner, *p)
base = svc.store.files["vault:Essays/drafts/On staying.md"]
svc.save(svc.store.agent("ada"), *p, {"base_revision": rev.get("revision") or rev.get("rev") or "", "base_text": base, "text": base.replace("Staying is not free.", "Staying is never free.")})
port = int(sys.argv[2]) if len(sys.argv) > 2 else 0
api = server_main.make_handler(svc, links, door, reach=lambda: "everywhere")

class Handler(api):
    def _share(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        hdrs = {k.lower(): v for k, v in self.headers.items()}
        ans = door.handle(method, "/door/" + self.path[3:], hdrs, body, "127.0.0.1")
        headers = [(k, v.replace("; Secure", "")) for k, v in ans.headers]
        self._raw(ans.status, ans.body, ans.ctype, headers)
    def _api(self, method):
        self.path = self.path[len("/x/doc-comments"):] or "/"
        self._handle(method)
    def _dispatch(self, method):
        if self.path.startswith("/s/"):
            return self._share(method)
        if self.path == "/look/links":
            data = json.dumps(out).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        if self.path.startswith("/x/doc-comments"):
            return self._api(method)
        self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
    def do_GET(self): self._dispatch("GET")
    def do_POST(self): self._dispatch("POST")
    def do_PATCH(self): self._dispatch("PATCH")
    def do_DELETE(self): self._dispatch("DELETE")
    def log_message(self, *a): pass

srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
print(f"look: http://127.0.0.1:{srv.server_address[1]}/", flush=True)
srv.serve_forever()
