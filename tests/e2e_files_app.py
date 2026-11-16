#!/usr/bin/env python3
"""Files end to end: the app's real server in front of the broker's real one.

Added 2026-10-02. `tests/test_broker_files.py` proves the broker's half over
real HTTP and loam-ui's suite proves the app's half against a recorded host;
neither proves the two agree. This does: the broker's `Server.handle` on a
loopback port over temp roots (the fixture of `test_broker_files.py`), and
the app's own `src/server.js`, exported from a git ref of its repository into
a temp directory and run by Node with `BROKER_URL` pointed at that port.
Every request below goes browser-side to the app (`/api/files/...`), which
relays it to the broker; nothing is mocked between them.

Sessions: two fixture sessions are put in the broker's table with a
`send_message` that records what it was handed, so `POST .../messages` runs
the broker's real route -- `resources` parsing, the agent's read scope, the
refusals -- and no CLI is started and no transcript written.

Upload (rewritten 2026-10-02 against 5148de9, which added the route): every
upload is a WHATWG `FormData` `fetch`ed by Node to the app's own
`POST /api/files/upload`, as the add sheet sends it -- the duplicate's new
name, binary integrity, metadata, scope and read-only refusals with nothing
written, both size caps. A real saved page with its stylesheet and images is
then uploaded the same way and previewed, and the preview's policy and body
are checked. What this cannot prove is what a browser does with that policy:
the page's `<form>`, `<iframe>` and `javascript:` link are left in the body
and are inert only because the browser enforces `sandbox` and
`default-src 'none'`.

The published vault URL is the one bin/deploy hands the app
(`FILES_VAULT_URLS`, `service_env`), read from there, so a change there is
tested here.

Not in `tests/run-all`, on purpose: it starts a broker and the app and takes
minutes. Run it by hand:

  python3 tests/e2e_files_app.py [<checkout>] [<git ref>]

defaults to this repository and `main`, and takes the app from its `app/`
folder (since 2026-11-16 the app is Loam's own; before, a loam-ui checkout,
whose root was the app). The checkout is only read (`git archive`). In a job's tree, run it under the tree's view of the
runtime (`loam_worktree.platform_argv`) or it imports the live broker.
"""

import asyncio
import hashlib
import http.client
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tbf", HERE / "test_broker_files.py")
tbf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tbf)
cfg, files, broker_mod = tbf.cfg, tbf.files, tbf.broker_mod
check, results, SECRET = tbf.check, tbf.results, tbf.SECRET

APP_REPO = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent
APP_REF = sys.argv[2] if len(sys.argv) > 2 else "main"


def deployed_vault_urls() -> str:
    """`FILES_VAULT_URLS` as the deployed app is given it, from bin/deploy's
    service_env with the install's origins (origins_env). Not a secret."""
    import importlib.machinery, importlib.util  # noqa: E401, PLC0415
    loader = importlib.machinery.SourceFileLoader("loam_deploy_cmd", str(HERE.parent / "bin" / "deploy"))
    spec = importlib.util.spec_from_loader("loam_deploy_cmd", loader)
    cmd = importlib.util.module_from_spec(spec)
    loader.exec_module(cmd)
    source = (HERE.parent / "bin" / "deploy").read_text()
    if '"FILES_VAULT_URLS": origins["LOAM_VAULT_ORIGIN"] + "/"' not in source:
        raise SystemExit("bin/deploy's service_env no longer hands FILES_VAULT_URLS as this reads it")
    return cmd.origins_env()["LOAM_VAULT_ORIGIN"] + "/"


VAULT_URLS = deployed_vault_urls()
VAULT_URL = VAULT_URLS.split(",")[0].strip().rstrip("/")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class App:
    """The browser's side: requests to the app, never to the broker."""

    def __init__(self, port: int) -> None:
        self.port = port

    def raw(self, method: str, target: str, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        hdrs = dict(headers or {})
        data = body
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        conn.request(method, target, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, payload

    def json(self, method: str, target: str, body=None):
        status, headers, payload = self.raw(method, target, body)
        try:
            return status, json.loads(payload)
        except json.JSONDecodeError:
            return status, {"_raw": payload.decode("utf-8", "replace")[:200]}

    def get(self, route: str, **query):
        q = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in query.items())
        return self.json("GET", f"/api/files/{route}" + (f"?{q}" if q else ""))


def run(app: App, server, t: dict, app_dir: Path, broker_url: str, token: str) -> None:
    vault = t["vault"]
    # roots, list, stat
    status, body = app.get("roots")
    check("app → broker: roots", (status, [r["root"] for r in body.get("roots", [])]),
          (200, ["vault", "spaces", "system"]))
    status, body = app.get("list", root="vault", path="40_PROJECTS/Loam")
    names = [e["name"] for e in body.get("entries", [])]
    check("app → broker: list vault/40_PROJECTS/Loam",
          (status, "state.md" in names, "creds.env" in names), (200, True, False))
    status, body = app.get("list", root="vault", path="Many", limit="10", cursor="10")
    check("app → broker: a second page", (status, body.get("cursor"), body.get("next_cursor"),
                                          len(body.get("entries", []))), (200, 10, "20", 10))
    status, body = app.get("stat", root="vault", path="image.png")
    check("app → broker: stat", (status, body.get("media_type"), body.get("capabilities")),
          (200, "image/png", {"read": True, "download": True, "write": False, "create": False,
            "rename": True}))
    status, body = app.get("stat", root="vault", path="deploy.env")
    check("app → broker: a secret is a 404", status, 404)
    status, body = app.get("stat", root="vault", path="../outside/passwd")
    check("app → broker: traversal refused at the app (400)", status, 400)
    status, _, payload = app.raw("GET", "/api/files/stat?root=vault&path=%2e%2e%2foutside")
    check("app → broker: encoded traversal refused", (status, SECRET.encode() in payload),
          (400, False))
    status, body = app.get("list", root="vault", path="link-dir")
    check("app → broker: a linked folder is a 404", status, 404)

    # read, save, conflict
    path = "40_PROJECTS/Loam/state.md"
    status, body = app.get("read", root="vault", path=path)
    rev = body.get("revision")
    check("app → broker: read", (status, body.get("text", "").endswith("# State\n")), (200, True))
    status, saved = app.json("PUT", "/api/files/text", {
        "root": "vault", "path": path, "text": body["text"] + "\nfrom the app\n",
        "base_revision": rev})
    check("app → broker: save on the revision read",
          (status, saved.get("provenance"), (vault / path).read_text().endswith("from the app\n"),
           f'last_modified_by: {cfg.lc.OWNER_LINK}' in (vault / path).read_text()),
          (200, True, True, True))
    status, conflict = app.json("PUT", "/api/files/text", {
        "root": "vault", "path": path, "text": "stale edit", "base_revision": rev})
    check("app → broker: a save on a stale revision is 409 with current_revision",
          (status, conflict.get("current_revision"), "stale edit" in (vault / path).read_text()),
          (409, saved.get("revision"), False))
    status, body = app.json("PUT", "/api/files/text", {
        "root": "vault", "path": "15_CONVERSATIONS/2026-10-02 chat.md", "text": "x",
        "base_revision": files.revision(b"# a transcript\n")})
    check("app → broker: a transcript save is 403 transcripts",
          (status, body.get("authority")), (403, "transcripts"))

    # create
    status, body = app.json("POST", "/api/files/folder", {"root": "vault", "path": "From app"})
    check("app → broker: create folder", (status, (vault / "From app").is_dir()), (201, True))
    status, body = app.json("POST", "/api/files/file",
                            {"root": "vault", "path": "From app/new.md", "text": "# New\n"})
    check("app → broker: create file, with provenance",
          (status, body.get("provenance"), body.get("link")),
          (201, True, "/files/vault/From%20app/new.md"))
    status, body = app.json("POST", "/api/files/file",
                            {"root": "vault", "path": "From app/new.md", "text": "again"})
    check("app → broker: create over an existing name is 409",
          (status, "again" in (vault / "From app" / "new.md").read_text()), (409, False))

    # resolve, including the published vault URL the app is configured with
    status, body = app.get("resolve", ref="[[Loam/state]]")
    check("app → broker: resolve a partial wiki-link",
          (status, body.get("status"), body.get("resource", {}).get("path")),
          (200, "found", path))
    status, body = app.get("resolve", ref="[[../Loam/notes]]", from_root="vault",
                           from_path="40_PROJECTS/loam-ui/loam-ui.md")
    check("app → broker: resolve a relative wiki-link from its note",
          (status, body.get("resource", {}).get("path")), (200, "40_PROJECTS/Loam/notes.md"))
    status, body = app.get("resolve", ref="/files/vault/From%20app/new.md")
    check("app → broker: resolve the app's own deep link",
          (status, body.get("status")), (200, "found"))
    # Folders (2026-10-02): every way a chat writes one, relayed by the app,
    # answered as a folder row, and read by the page's own `resolveOf` and
    # `isFolder` -- the branch `openFileRef` takes to open the folder in Files.
    ui = {"from_root": "vault", "from_path": "40_PROJECTS/loam-ui/loam-ui.md"}
    folder_refs = [
        ("[[40_PROJECTS/Loam]]", {}, "found", "40_PROJECTS/Loam"),
        ("[[40_PROJECTS/Loam/|Projects]]", {}, "found", "40_PROJECTS/Loam"),
        ("/files/vault/40_PROJECTS/Loam", {}, "found", "40_PROJECTS/Loam"),
        (str(vault / "40_PROJECTS" / "Loam"), {}, "found", "40_PROJECTS/Loam"),
        ("[[research]]", {}, "found", "40_PROJECTS/loam-ui/research"),
        ("[[loam-ui/research]]", {}, "found", "40_PROJECTS/loam-ui/research"),
        ("[[Half Year]]", {}, "found", "40_PROJECTS/Half Year"),
        ("../Half%20Year/", ui, "found", "40_PROJECTS/Half Year"),
        ("[[../Loam]]", ui, "found", "40_PROJECTS/Loam"),
        ("[[Topics]]", {}, "found", "20_ATLAS/Topics.md"),
        ("[[Topics/]]", {}, "found", "20_ATLAS/Topics"),
        ("[[Loam/]]", {}, "ambiguous", None),
        ("[[.obsidian/]]", {}, "missing", None),
        ("[[link-dir]]", {}, "missing", None),
        ("[[../../..]]", ui, "missing", None),
    ]
    answers = []
    for ref, kw, st, p in folder_refs:
        status, body = app.get("resolve", ref=ref, **kw)
        answers.append(body)
        check(f"app → broker: folder link {ref} → {st}",
              (status, body.get("status"), (body.get("resource") or {}).get("path")),
              (200, st, p))
        check(f"...nothing secret in it ({ref})", SECRET in json.dumps(body), False)
    read = subprocess.run(
        ["node", "--input-type=module", "-e",
         f"import {{ resolveOf, isFolder }} from {json.dumps(str(app_dir / 'public' / 'files.js'))}\n"
         f"console.log(JSON.stringify({json.dumps(answers)}.map((b) => {{ const a = resolveOf(b); "
         f"return [a.status, a.resource ? isFolder(a.resource) : (a.candidates || []).map(isFolder)] }})))"],
        capture_output=True, text=True, timeout=30)
    seen = json.loads(read.stdout) if read.returncode == 0 else read.stderr[-300:]
    check("the page reads each answer as a folder to open, a note, or a choice",
          seen, [["found", True]] * 9 + [["found", False], ["found", True],
                                         ["ambiguous", [True, True]],
                                         ["missing", []], ["missing", []],
                                         ["missing", []]])
    status, body = app.get("resolve", ref="[[40_PROJECTS/Loam/]]")
    folder_row = body.get("resource") or {}
    status, listing = app.get("list", root=folder_row.get("root", ""),
                              path=folder_row.get("path", ""))
    check("...and the folder it opens lists through the app",
          (status, "state.md" in [e.get("name") for e in listing.get("entries", [])]),
          (200, True))

    print(f"INFO  FILES_VAULT_URLS from {COMPOSE.name}: {VAULT_URLS!r}")
    status, body = app.get("config")
    check("app: the configured vault URLs", (status, body.get("vault_urls")),
          (200, [VAULT_URL]))
    # A published vault URL, mapped by the page's own `vaultUrlRef`, then
    # resolved by the broker through the app: the vault is published by file
    # path (names percent-encoded, `.md` dropped), not by slug.
    # The vault app is Caddy's file_server over the vault, so a link copied
    # from it ends in `.md`; the `.md`-less form is the design's.
    urls = [VAULT_URL + p for p in ("/40_PROJECTS/Loam/state", "/From%20app/new#New",
                                    "/image.png", "/40-projects/loam/state",
                                    "/40_PROJECTS/Loam/state.md", "/40_PROJECTS/Loam/",
                                    "/40_PROJECTS/Half%20Year")]
    strangers = [VAULT_URL.replace("https://", "http://") + "/image.png",
                 VAULT_URL + ".evil.example/image.png"]
    mapped = subprocess.run(
        ["node", "--input-type=module", "-e",
         f"import {{ vaultUrlRef }} from {json.dumps(str(app_dir / 'public' / 'files.js'))}\n"
         f"console.log(JSON.stringify({json.dumps(urls + strangers)}.map((u) => "
         f"vaultUrlRef(u, {json.dumps([VAULT_URL])}))))"],
        capture_output=True, text=True, timeout=30)
    refs = json.loads(mapped.stdout) if mapped.returncode == 0 else []
    print(f"INFO  vaultUrlRef: {json.dumps(refs)} {mapped.stderr[-200:]}")
    want = [("found", "40_PROJECTS/Loam/state.md"), ("found", "From app/new.md"),
            ("found", "image.png"), ("missing", None), ("found", "40_PROJECTS/Loam/state.md"),
            ("found", "40_PROJECTS/Loam"), ("found", "40_PROJECTS/Half Year")]
    check("an http:// or look-alike host is not the vault", refs[len(urls):], [None, None])
    refs = refs[:len(urls)]
    for url, ref, (st, p) in zip(urls, refs, want):
        status, body = app.get("resolve", ref=ref["ref"])
        check(f"published {url[len(VAULT_URL):]} → {ref['ref']} → {st}",
              (status, body.get("status"), (body.get("resource") or {}).get("path")),
              (200, st, p))
    check("every published URL mapped", len(refs), len(urls))

    # preview (HTML) and download (bytes)
    status, headers, payload = app.raw("GET", "/api/files/preview?root=vault&path=page.html")
    check("app → broker: preview an HTML file, under the app's CSP",
          (status, "sandbox" in headers.get("content-security-policy", "")
           or "script-src 'none'" in headers.get("content-security-policy", "")),
          (200, True))
    check("...whose script is not carried live", b"<script>alert(1)</script>" in payload, False)
    status, headers, payload = app.raw("GET", "/api/files/download?root=vault&path=image.png")
    check("app → broker: download a binary byte for byte",
          (status, payload == (vault / "image.png").read_bytes(),
           headers.get("content-disposition", "").startswith("attachment")),
          (200, True, True))
    status, headers, payload = app.raw("GET", "/api/files/download?root=vault&path=link-to-passwd.md")
    check("app → broker: a link is not downloaded", (status, SECRET.encode() in payload),
          (404, False))

    # A file picked in Files, sent with a message, through the broker's real route.
    alpha, beta = tbf.FakeSession("alpha"), tbf.FakeSession("beta")
    server.broker.sessions["fx-alpha"] = alpha
    server.broker.sessions["fx-beta"] = beta
    status, body = app.json("POST", "/api/sessions/fx-alpha/messages", {
        "text": "look", "resources": [{"root": "vault", "path": "image.png"}]})
    carried = alpha.sent[-1][1][0] if alpha.sent else {}
    check("app → broker: a message with resources is accepted",
          (status, body.get("accepted")), (200, True))
    check("...carrying the file by host path, type and resource",
          (carried.get("path"), carried.get("type"), carried.get("resource")),
          (str(vault / "image.png"), "image/png", {"root": "vault", "path": "image.png"}))
    status, body = app.json("POST", "/api/sessions/fx-alpha/messages", {
        "text": "look", "resources": [{"root": "spaces", "path": "beta/soul.md"}]})
    check("app → broker: a resource outside the agent's read scope is 403, not sent",
          (status, len(alpha.sent)), (403, 1))
    status, body = app.json("POST", "/api/sessions/fx-beta/messages", {
        "text": "look", "resources": [{"root": "system", "path": "Engineer/Role.md"}]})
    check("app → broker: a System file is 403 to a persona", status, 403)
    status, body = app.json("POST", "/api/sessions/fx-beta/messages", {
        "text": "look", "resources": [{"root": "vault", "path": "../outside/passwd"}]})
    check("app → broker: a traversing resource is refused", status in (400, 404), True)
    status, body = app.json("POST", "/api/sessions/fx-beta/messages", {
        "text": "look", "resources": [{"root": "vault", "path": "deploy.env"}]})
    check("app → broker: a secret resource is a 404", status, 404)

    upload_section(app, t, app_dir)
    preview_section(app, t, app_dir)


def node_upload(app: App, app_dir: Path, tmp: Path, sends: list) -> list:
    """Each send as a browser makes it: a `FormData` with one `file` part,
    `fetch`ed to the app's own `POST /api/files/upload`. Node's `FormData` and
    `fetch` are the WHATWG ones a browser has, so the envelope, the boundary
    and the headers are a browser's, not this test's. `sends` is a list of
    `{query, name, type, file}`; the answer is `[{status, body}]` in order."""
    spec_path = tmp / "sends.json"
    spec_path.write_text(json.dumps(sends))
    script = f"""
import {{ readFileSync }} from 'node:fs'
const sends = JSON.parse(readFileSync({json.dumps(str(spec_path))}, 'utf8'))
const out = []
for (const s of sends) {{
  const form = new FormData()
  form.append('file', new Blob([readFileSync(s.file)], {{ type: s.type }}), s.name)
  const res = await fetch('http://127.0.0.1:{app.port}/api/files/upload?' + s.query, {{ method: 'POST', body: form }})
  out.push({{ status: res.status, body: await res.text() }})
}}
console.log(JSON.stringify(out))
"""
    proc = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True,
                          text=True, timeout=120)
    if proc.returncode != 0:
        check("node fetch upload ran", proc.stderr[-400:], "")
        return []
    answers = []
    for o in json.loads(proc.stdout):
        try:
            answers.append({"status": o["status"], **json.loads(o["body"])})
        except json.JSONDecodeError:
            answers.append({"status": o["status"], "_raw": o["body"][:200]})
    return answers


def upload_section(app: App, t: dict, app_dir: Path) -> None:
    """Upload through the app's own route, as the add sheet sends it."""
    vault, tmp = t["vault"], t["tmp"]
    (vault / "Uploads").mkdir(exist_ok=True)

    def blob(name: str, data: bytes) -> str:
        path = tmp / "blobs" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        return str(path)

    # Every byte value, the multipart boundary's own shape and random bytes:
    # what a relay that decoded, trimmed or re-encoded would corrupt.
    data = (b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 64 + b"\r\n--x\r\n"
            + os.urandom(4096) + b"\r\n")
    png = blob("png", data)
    q = "root=vault&path=Uploads"
    first, second = (node_upload(app, app_dir, tmp, [
        {"query": q, "name": "IMG 2041.png", "type": "image/png", "file": png}] * 2) + [{}, {}])[:2]
    print("INFO  upload answer:", json.dumps(first))
    check("POST /api/files/upload → broker: 201, the name kept",
          (first.get("status"), first.get("path"), first.get("media_type")),
          (201, "Uploads/IMG 2041.png", "image/png"))
    check("...the metadata is the stored file's",
          (first.get("root"), first.get("name"), first.get("kind"), first.get("size"),
           first.get("authority"), first.get("link"), first.get("capabilities")),
          ("vault", "IMG 2041.png", "file", len(data), "user", "/files/vault/Uploads/IMG%202041.png",
           {"read": True, "download": True, "write": False, "create": False,
            "rename": True}))
    check("...the bytes on disk are the bytes sent",
          (vault / "Uploads" / "IMG 2041.png").read_bytes() == data, True)
    check("...the revision is their sha256",
          first.get("revision"), "sha256:" + hashlib.sha256(data).hexdigest())
    check("...the same again is 'IMG 2041 2.png', a new path, the first untouched",
          (second.get("status"), second.get("name"), second.get("path"),
           (vault / "Uploads" / "IMG 2041.png").read_bytes() == data,
           (vault / "Uploads" / "IMG 2041 2.png").read_bytes() == data),
          (201, "IMG 2041 2.png", "Uploads/IMG 2041 2.png", True, True))
    for name in ("IMG 2041.png", "IMG 2041 2.png"):
        status, headers, payload = app.raw("GET", "/api/files/download?root=vault&path="
                                           + quote(f"Uploads/{name}", safe=""))
        check(f"...the app downloads {name!r} back byte for byte",
              (status, payload == data, headers.get("content-type")), (200, True, "image/png"))
    status, body = app.get("stat", root="vault", path="Uploads/IMG 2041 2.png")
    check("...stat agrees with the upload's answer",
          (status, body.get("size"), body.get("revision", second.get("revision"))),
          (200, len(data), second.get("revision")))
    status, body = app.get("list", root="vault", path="Uploads")
    check("...and lists both", [e["name"] for e in body.get("entries", [])],
          ["IMG 2041 2.png", "IMG 2041.png"])

    # A Markdown note gains the owner's provenance, like one they create here.
    note = blob("note", "# Uploaded\n\nnaïve café — ok\n".encode())
    (md,) = node_upload(app, app_dir, tmp, [
        {"query": q, "name": "From phone.md", "type": "text/markdown", "file": note}]) or [{}]
    text = (vault / "Uploads" / "From phone.md").read_text() if md.get("status") == 201 else ""
    check("an uploaded note: 201, with provenance, its text intact",
          (md.get("status"), f'created_by: {cfg.lc.OWNER_LINK}' in text,
           text.endswith("# Uploaded\n\nnaïve café — ok\n")), (201, True, True))

    # Scope and read-only: each refused, and nothing written anywhere.
    before = sorted(str(p.relative_to(tmp)) for p in tmp.rglob("*") if "blobs" not in p.parts)
    small = blob("small", b"\x89PNG\r\n\x1a\nsmall")
    cases = [
        ("a transcript folder is 403 transcripts", "root=vault&path=15_CONVERSATIONS", "a.png",
         small, (403, "transcripts")),
        ("System is read-only: 403", "root=system&path=Engineer", "a.png", small, (403, None)),
        ("a persona's space is read-only: 403", "root=spaces&path=alpha", "a.png", small,
         (403, None)),
        ("a folder that does not exist: 404", "root=vault&path=Nowhere", "a.png", small,
         (404, None)),
        ("a linked folder: 404", "root=vault&path=link-dir", "a.png", small, (404, None)),
        ("a hidden folder: 404", "root=vault&path=.obsidian", "a.png", small, (404, None)),
        ("traversal refused at the app: 400", "root=vault&path=..%2Foutside", "a.png", small,
         (400, None)),
        ("an unknown root: 400 or 404", "root=elsewhere&path=", "a.png", small, (404, None)),
        ("a secret-shaped name: 400", q, "deploy.env", small, (400, None)),
        ("a hidden name: 400", q, ".bashrc.txt", small, (400, None)),
        ("a name with a slash is its last segment or refused", q, "../escape.png", small,
         (None, None)),
        ("an extension Files does not upload: 415", q, "tool.exe", small, (415, None)),
        ("a .png whose bytes are not a PNG: 415", q, "fake.png",
         blob("fake", b"<script>x</script>"), (415, None)),
        ("a Markdown note that is not UTF-8: 415", q, "bad.md", blob("bad", b"caf\xe9 \x00"),
         (415, None)),
    ]
    answers = node_upload(app, app_dir, tmp, [
        {"query": cq, "name": n, "type": "application/octet-stream", "file": f}
        for _, cq, n, f, _ in cases])
    for (label, _, name, _, (want_status, want_auth)), got in zip(cases, answers):
        if want_status is None:
            print(f"INFO  upload {name!r}: {got.get('status')} {got.get('path') or got.get('error')}")
            check(f"upload: {label}", got.get("status") == 400
                  or (got.get("status") == 201 and "/" not in got.get("name", "/")
                      and got.get("path", "").startswith("Uploads/")), True)
            if got.get("status") == 201:
                (vault / got["path"]).unlink()
            continue
        if label.startswith("an unknown root"):
            check(f"upload: {label}", got.get("status") in (400, 404), True)
            continue
        check(f"upload: {label}",
              (got.get("status"), got.get("authority") if want_auth else None),
              (want_status, want_auth))
    check("every refusal answered", len(answers), len(cases))
    after = sorted(str(p.relative_to(tmp)) for p in tmp.rglob("*") if "blobs" not in p.parts
                   and p.name != "sends.json")
    check("...and none of them wrote a file, or a temp file, anywhere",
          [p for p in after if p not in before], [])
    check("...nothing reached outside/", sorted(p.name for p in (tmp / "outside").iterdir()),
          ["Loam", "passwd"])

    # Limits: the app's own cap, then the host's on the same bytes.
    status, body = app.json("GET", "/api/limits")
    cap = body.get("files_upload_max_bytes")
    check("app → broker: /api/limits relays the upload cap", (status, cap), (200, files.UPLOAD_MAX))
    exact = blob("exact", b"\x89PNG\r\n\x1a\n" + b"\0" * (files.UPLOAD_MAX - 8))
    over = blob("over", b"\x89PNG\r\n\x1a\n" + b"\0" * (files.UPLOAD_MAX - 7))
    way_over = blob("way", b"\x89PNG\r\n\x1a\n" + b"\0" * (files.UPLOAD_MAX + 64 * 1024))
    a_exact, a_over, a_way = (node_upload(app, app_dir, tmp, [
        {"query": q, "name": "exact.png", "type": "image/png", "file": exact},
        {"query": q, "name": "over.png", "type": "image/png", "file": over},
        {"query": q, "name": "way.png", "type": "image/png", "file": way_over}]) + [{}] * 3)[:3]
    check("a file of exactly the cap is stored, byte for byte",
          (a_exact.get("status"), a_exact.get("size"),
           (vault / "Uploads" / "exact.png").read_bytes() == Path(exact).read_bytes()
           if a_exact.get("status") == 201 else False),
          (201, files.UPLOAD_MAX, True))
    check("one byte over the cap is 413 (the host's, inside the app's envelope allowance)",
          (a_over.get("status"), (vault / "Uploads" / "over.png").exists()), (413, False))
    check("64 KiB over is 413 from the app itself, with its max",
          (a_way.get("status"), a_way.get("max"), (vault / "Uploads" / "way.png").exists()),
          (413, files.UPLOAD_MAX, False))
    status, _, _ = app.raw("POST", "/api/files/upload?root=vault&path=Uploads", body=b"{}",
                           headers={"Content-Type": "application/json"})
    check("a body that is not multipart is refused (415 or 400)", status in (400, 415), True)
    status, _, _ = app.raw("GET", "/api/files/upload?root=vault&path=Uploads")
    check("GET on the upload route is not an upload", status in (404, 405), True)


def preview_section(app: App, t: dict, app_dir: Path) -> None:
    """A real saved page with its own stylesheet and image, uploaded through
    the app and then previewed through it. What the preview is allowed: inline
    styles and the page's own local images, inlined. What it is not: scripts,
    the network, forms, a `<base>`, a refresh, anything above its root."""
    vault, tmp = t["vault"], t["tmp"]
    pixel = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
             b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f"
             b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")
    css = ("body { color: rgb(1, 2, 3); }\n"
           "@import url(https://evil.example/x.css);\n"
           ".hero { background: url(img/bg.png); }\n"
           ".remote { background: url(https://evil.example/t.png); }\n")
    html = ("<!doctype html><html><head><title>Saved</title>"
            "<base href=\"https://evil.example/\">"
            "<meta http-equiv=\"refresh\" content=\"0;url=https://evil.example/\">"
            "<link rel=\"stylesheet\" href=\"style.css\">"
            "<link rel=\"stylesheet\" href=\"https://evil.example/remote.css\">"
            "<link rel=\"stylesheet\" href=\"../../outside/x.css\">"
            "<style>.inline { margin: 7px; }</style>"
            "<script src=\"https://evil.example/a.js\"></script>"
            "<script>document.title='ran'</script></head><body>"
            "<p class=\"inline\" style=\"padding: 3px\">kept</p>"
            "<img alt=\"local\" src=\"img/pixel.png\">"
            "<img alt=\"remote\" src=\"https://evil.example/t.png\">"
            "<img alt=\"climb\" src=\"../../image.png\">"
            "<img alt=\"inline-data\" src=\"data:image/png;base64,AAAA\">"
            "<a href=\"javascript:alert(1)\" onclick=\"alert(2)\">x</a>"
            "<form action=\"https://evil.example/\"><input name=q></form>"
            "<iframe src=\"https://evil.example/\"></iframe>"
            "</body></html>")
    (vault / "Saved" / "img").mkdir(parents=True)
    blobs = tmp / "blobs"
    for name, data in (("page.html", html.encode()), ("style.css", css.encode()),
                       ("pixel.png", pixel)):
        (blobs / name).write_bytes(data)
    answers = node_upload(app, app_dir, tmp, [
        {"query": "root=vault&path=Saved", "name": "page.html", "type": "text/html",
         "file": str(blobs / "page.html")},
        {"query": "root=vault&path=Saved", "name": "style.css", "type": "text/css",
         "file": str(blobs / "style.css")},
        {"query": "root=vault&path=Saved%2Fimg", "name": "pixel.png", "type": "image/png",
         "file": str(blobs / "pixel.png")},
        {"query": "root=vault&path=Saved%2Fimg", "name": "bg.png", "type": "image/png",
         "file": str(blobs / "pixel.png")}])
    check("a saved page, its stylesheet and two images uploaded through the app",
          [a.get("status") for a in answers], [201, 201, 201, 201])
    status, headers, payload = app.raw("GET", "/api/files/preview?root=vault&path=Saved%2Fpage.html")
    doc = payload.decode("utf-8", "replace")
    csp = headers.get("content-security-policy", "")
    print(f"INFO  preview CSP: {csp}")
    print(f"INFO  preview body ({len(doc)} chars): {doc[:1500]}")
    directives = {d.strip().split(" ")[0]: d.strip() for d in csp.split(";") if d.strip()}
    check("preview: 200, text/html, no-store-ish",
          (status, headers.get("content-type", "").startswith("text/html")), (200, True))
    check("preview CSP: sandbox with no allow-* token (no scripts, forms, popups, same-origin)",
          (directives.get("sandbox"), "allow-" in csp), ("sandbox", False))
    check("preview CSP: default-src 'none', no script-src, no connect-src",
          (directives.get("default-src"), "script-src" in directives,
           "connect-src" in directives), ("default-src 'none'", False, False))
    check("preview CSP: inline styles only, data: images and fonts only",
          (directives.get("style-src"), directives.get("img-src"), directives.get("font-src")),
          ("style-src 'unsafe-inline'", "img-src data:", "font-src data:"))
    check("preview CSP: base-uri and form-action 'none', framed only by the app",
          (directives.get("base-uri"), directives.get("form-action"),
           directives.get("frame-ancestors")),
          ("base-uri 'none'", "form-action 'none'", "frame-ancestors 'self'"))
    low = doc.lower()
    check("preview body: no <script>, no <base>, no meta refresh",
          ("<script" in low, "<base" in low, "refresh" in low), (False, False, False))
    check("preview body: nothing on the network is referenced as a loadable asset",
          [m for m in ("evil.example/remote.css", "evil.example/a.js", "evil.example/x.css",
                       "src=\"https://evil.example/t.png", "url(https://evil.example/t.png",
                       "<link") if m in low], [])
    b64 = __import__("base64").b64encode(pixel).decode()
    check("preview body: the page's own image inlined as a data: PNG",
          f'alt="local" src="data:image/png;base64,{b64}"' in doc, True)
    check("preview body: an image above the page's root is not followed",
          'alt="climb" src=""' in doc, True)
    check("preview body: inline <style> and style= are kept",
          (".inline { margin: 7px; }" in doc, 'style="padding: 3px"' in doc), (True, True))
    linked = "color: rgb(1, 2, 3)" in doc
    print(f"INFO  the linked style.css was {'inlined' if linked else 'NOT inlined'}")
    check("preview body: the linked local stylesheet is inlined as <style>", linked, True)
    check("preview body: its url(img/bg.png) is resolved against it, inlined",
          f'url("data:image/png;base64,{b64}")' in doc, True)
    check("preview body: its @import is dropped",  "@import" in low, False)
    check("preview: the original on disk is unchanged",
          (vault / "Saved" / "page.html").read_text(), html)
    status, headers, payload = app.raw("GET", "/api/files/download?root=vault&path=Saved%2Fpage.html")
    check("download of the page is an attachment under a sandbox, never rendered here",
          (status, headers.get("content-disposition", "").startswith("attachment"),
           "sandbox" in headers.get("content-security-policy", ""),
           headers.get("content-type")), (200, True, True, "application/octet-stream"))
    status, body = app.get("preview", root="vault", path="Saved/style.css")
    check("preview refuses a non-HTML file: 415", status, 415)
    status, body = app.get("preview", root="vault", path="15_CONVERSATIONS/shown.html")
    print(f"INFO  preview of a transcript-folder page answers {status}")


async def serve(server, ready: dict) -> None:
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    ready["port"] = srv.sockets[0].getsockname()[1]
    ready["loop"] = asyncio.get_running_loop()
    ready["event"].set()
    async with srv:
        await ready["stop"]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="files-e2e-"))
    keep = (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH,
            cfg.VAULT, cfg.AI_LABS, files.SYSTEM_BASE)
    node = None
    try:
        sha = subprocess.run(["git", "-C", str(APP_REPO), "rev-parse", APP_REF],
                             capture_output=True, text=True, check=True).stdout.strip()
        app_dir = tmp / "app"
        app_dir.mkdir()
        has_app = subprocess.run(["git", "-C", str(APP_REPO), "cat-file", "-e", f"{sha}:app/src/server.js"],
                                 capture_output=True).returncode == 0
        archive = subprocess.run(["git", "-C", str(APP_REPO), "archive", f"{sha}:app" if has_app else sha],
                                 capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(app_dir)], input=archive, check=True)
        print(f"INFO  app: {APP_REPO} at {APP_REF} = {sha}, exported to {app_dir}")
        cfg.STATE_DIR = tmp / "state"
        cfg.SESSION_DIR = tmp / "state" / "sessions"
        cfg.USAGE_PATH = tmp / "state" / "provider-usage.json"
        cfg.TOKEN_PATH = tmp / "state" / "token"
        t = tbf.build(tmp)
        t["tmp"] = tmp
        cfg.VAULT, cfg.AI_LABS, files.SYSTEM_BASE = t["vault"], t["labs"], t["system"]
        server = broker_mod.Server(broker_mod.Broker())
        ready = {"event": threading.Event()}

        def loop() -> None:
            lp = asyncio.new_event_loop()
            ready["stop"] = lp.create_future()
            lp.run_until_complete(serve(server, ready))

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        ready["event"].wait(10)
        broker_url = f"http://127.0.0.1:{ready['port']}"
        port = free_port()
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp),
               "APP_PORT": str(port), "BROKER_URL": broker_url,
               "BROKER_TOKEN": server.broker.token, "LOG_LEVEL": "off",
               "SPEECH_DIR": str(tmp / "speech"),
               "FILES_VAULT_URLS": VAULT_URLS}
        node = subprocess.Popen(["node", "src/server.js"], cwd=app_dir, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        app = App(port)
        for _ in range(100):
            try:
                if app.raw("GET", "/up")[0] == 200:
                    break
            except OSError:
                time.sleep(0.1)
        print(f"INFO  broker on {broker_url} (fixture roots under {tmp}); app on :{port}")
        try:
            run(app, server, t, app_dir, broker_url, server.broker.token)
        finally:
            ready["loop"].call_soon_threadsafe(ready["stop"].set_result, None)
            thread.join(10)
    finally:
        if node is not None:
            node.terminate()
            node.wait(10)
        (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH,
         cfg.VAULT, cfg.AI_LABS, files.SYSTEM_BASE) = keep
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
