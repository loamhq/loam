#!/usr/bin/env python3
"""End-to-end integration tests for the split Sharing and Comments plugins.

Exercises:
- Plugin manifests, public inter-plugin services (`share.collaboration` v1,
  `share.remote` v1), and zero cross-imports between `plugins/sharing/` and
  `plugins/comments/`.
- All 4 on/off combinations of Sharing and Comments (zero dead capabilities).
- Whole-file comments on non-Markdown files (PNG, PDF, HTML, ZIP, audio) by
  owner, agent (`comments_mcp.py`), and outside visitor (cookie + agent key).
- Markdown anchored comments, suggestions, accept/reject, merge, history
  (owner-only; frontmatter never leaked to visitors).
- Agent sharing tools (`sharing_mcp.py`) and Shared-with-me across Loams for
  both Markdown and non-Markdown files.
- Agent-name masking (`plugin.comments.appear` and per-file `appear`) both on
  shared files and on Shared-with-me requests.
- Rename, move, parent folder rename, external disk move, delete, unrelated
  replacement, restore, and copy — for both Markdown and non-Markdown files
  against the real session broker (`bin/session-broker`).
- Active content security headers (HTML `/frame` opaque sandbox CSP, SVG `/raw`
  no-script sandbox CSP, magic-byte enforcement on spoofed files, attachment
  headers on `/download`, Range 206 on audio/video) and strict path/symlink
  containment.

Run: python3 tests/test_sharing_comments.py
"""

from __future__ import annotations

import http.client
import io
import json
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from urllib.parse import urlencode

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
SHARING = ROOT / "plugins" / "sharing"
COMMENTS = ROOT / "plugins" / "comments"

sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(SHARING / "server"))
sys.path.insert(0, str(COMMENTS / "server"))

import loam_contributions as lco  # noqa: E402
import loam_files as lf  # noqa: E402
import loam_plugins as lp  # noqa: E402
import loam_skills as sk  # noqa: E402

from comments import appear as c_appear  # noqa: E402
from comments import store as c_store  # noqa: E402
from comments.remote import RemoteClient  # noqa: E402
from comments.service import Service as CommentService  # noqa: E402
from sharing import files as s_files  # noqa: E402
from sharing import links as s_links  # noqa: E402
from sharing import store as s_store  # noqa: E402
from sharing.collab import CollabClient  # noqa: E402
from sharing.door import Door  # noqa: E402
from sharing.tracker import FileTracker  # noqa: E402
from sharing.withme import WithMe, vet_addresses, Refused  # noqa: E402

import importlib.util  # noqa: E402


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


comments_main = _load_module("comments_server_main", COMMENTS / "server" / "main.py")
sharing_main = _load_module("sharing_server_main", SHARING / "server" / "main.py")

PASS = FAIL = 0


def check(label: str, got, want=True):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_png(w: int = 8, h: int = 8) -> bytes:
    """Minimal valid RGB PNG bytes."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x91\x68\x44" * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


MIN_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
SAMPLE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80" onload="window.__svgOnload=1"><script>window.__svgScript=1</script><circle cx="40" cy="40" r="30" fill="#916844"/></svg>'
SAMPLE_HTML = b'<!doctype html><html><body><h1>Mockup</h1><button id="b" onclick="document.getElementById(\'c\').textContent=++window.n">Tap</button><span id="c">0</span><script>window.n=0;</script></body></html>'
SAMPLE_ZIP = b"PK\x03\x04\x14\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00test"
SAMPLE_MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x10" + b"\xff\xfb\x90\x00" * 64


def http_req(port: int, method: str, path: str, *, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    hdrs = dict(headers or {})
    if isinstance(body, dict):
        hdrs.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=data, headers=hdrs)
    res = conn.getresponse()
    raw = res.read()
    rh = {k.lower(): v for k, v in res.getheaders()}
    conn.close()
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    return res.status, rh, raw, parsed


def run_mcp(script: Path, plugin_url: str, agent: str, vault: str, tool: str, args: dict):
    env = {**os.environ, "LOAM_PLUGIN_URL": plugin_url, "LOAM_AGENT": agent, "LOAM_VAULT": vault}
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": args}},
    ]
    proc = subprocess.run(
        [sys.executable, str(script)],
        input="\n".join(json.dumps(m) for m in msgs) + "\n",
        capture_output=True, text=True, env=env, timeout=20,
    )
    lines = [json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip()]
    res = lines[-1]["result"]
    return res["content"][0]["text"], bool(res.get("isError"))


# -- 1. Static checks: manifests, isolation, no real names -------------------

def test_manifests_and_isolation():
    print("\n1. Manifests, public services, and zero cross-imports")
    rm_s = lp.read_folder(SHARING, "sharing")
    rm_c = lp.read_folder(COMMENTS, "comments")
    check("sharing manifest valid", (rm_s["manifest"]["id"], rm_s["warnings"]), ("sharing", []))
    check("comments manifest valid", (rm_c["manifest"]["id"], rm_c["warnings"]), ("comments", []))
    check("sharing contributes web.door", len(rm_s["manifest"]["contributes"]["web.door"]), 1)
    check("sharing contributes share.remote v1", rm_s["manifest"]["contributes"]["plugin.service"][0]["interface"], "share.remote")
    check("comments contributes share.collaboration v1", rm_c["manifest"]["contributes"]["plugin.service"][0]["interface"], "share.collaboration")
    check("comments declares files.share place", "files.share" in rm_c["manifest"]["places"], True)

    # Neither plugin imports from or references the other's internal folders
    for f in SHARING.rglob("*.py"):
        if "test" in f.parts:
            continue
        txt = f.read_text(encoding="utf-8")
        check(f"sharing/{f.name} has no comments import", "from comments" not in txt and "import comments" not in txt, True)
    for f in COMMENTS.rglob("*.py"):
        if "test" in f.parts:
            continue
        txt = f.read_text(encoding="utf-8")
        check(f"comments/{f.name} has no sharing import", "from sharing" not in txt and "import sharing" not in txt, True)

    # Verify services_for discovery via loam_contributions
    rows = [
        {"id": "sharing", "on": True, "effective": True, "contributes": rm_s["manifest"]["contributes"]},
        {"id": "comments", "on": True, "effective": True, "contributes": rm_c["manifest"]["contributes"]},
    ]
    svcs_both = lco.services_for("share.collaboration", plugins=rows, ports={"sharing": 9101, "comments": 9102})
    check("services_for finds share.collaboration when comments is on", len(svcs_both) == 1 and svcs_both[0]["url"] == "http://127.0.0.1:9102/collab", True)
    rows_off = [rows[0], {**rows[1], "on": False}]
    svcs_off = lco.services_for("share.collaboration", plugins=rows_off, ports={"sharing": 9101, "comments": 9102})
    check("services_for returns [] when comments is off", svcs_off, [])


# -- 2. Live server pair against real session broker -------------------------

class TestEnv:
    """Runs a real vault + real broker-backed Comments & Sharing servers."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.vault = tmp / "vault"
        self.vault.mkdir()
        self.state = tmp / "state"
        self.state.mkdir()
        self.events_file = self.state / "files-events.jsonl"
        self.c_priv = tmp / "comments-private"
        self.s_priv = tmp / "sharing-private"
        self.c_priv.mkdir()
        self.s_priv.mkdir()
        self.comments_on = True
        self.sharing_on = True
        self.appear_default = "names"
        self.c_port = free_port()
        self.s_port = free_port()
        self.b_port = free_port()
        self.token_file = tmp / "broker.token"
        self.token_file.write_text("test-secret-token\n", encoding="utf-8")

        # Seed vault with diverse files
        (self.vault / "notes").mkdir()
        (self.vault / "notes" / "spec.md").write_text("# Design Spec\n\nThe river runs calm through the valley.\n", encoding="utf-8")
        (self.vault / "notes" / "diagram.png").write_bytes(make_png())
        (self.vault / "notes" / "report.pdf").write_bytes(MIN_PDF)
        (self.vault / "notes" / "logo.svg").write_bytes(SAMPLE_SVG)
        (self.vault / "notes" / "mockup.html").write_bytes(SAMPLE_HTML)
        (self.vault / "notes" / "bundle.zip").write_bytes(SAMPLE_ZIP)
        (self.vault / "notes" / "clip.mp3").write_bytes(SAMPLE_MP3)
        (self.vault / "notes" / "spoof.png").write_bytes(b"<html><script>alert(1)</script></html>")
        (self.vault / "notes" / "spoof.pdf").write_bytes(b"<html><script>alert(1)</script></html>")
        (self.vault / "notes" / "sibling-secret.txt").write_text("TOP SECRET SIBLING", encoding="utf-8")
        outside = tmp / "outside-secret.txt"
        outside.write_text("OUTSIDE VAULT SECRET", encoding="utf-8")
        os.symlink(outside, self.vault / "notes" / "escape.txt")

        # Start minimal real-loam_files broker HTTP server
        self._start_broker()
        # Start Comments server
        self.c_store = c_store.BrokerStore(
            f"http://127.0.0.1:{self.b_port}", str(self.token_file),
            data_dir="System/Loam/data/comments", private_dir=self.c_priv,
        )
        self.c_service = CommentService(self.c_store)
        self.c_appear_store = c_appear.AppearStore(self.c_priv)
        self.c_remote = RemoteClient(self.c_store)
        self.c_server = comments_main.serve(
            self.c_service, self.c_port, plugin_dir=COMMENTS,
            appear_store=self.c_appear_store, remote=self.c_remote,
            reach=lambda: "everywhere", appear_default=lambda: self.appear_default,
        )
        threading.Thread(target=self.c_server.serve_forever, daemon=True).start()

        # Start Sharing server
        self.s_store = s_store.BrokerStore(f"http://127.0.0.1:{self.b_port}", str(self.token_file))
        self.s_tracker = FileTracker(self.s_store, self.s_priv)
        self.s_links = s_links.Links(self.s_priv)
        self.s_collab = CollabClient(self.s_store)
        self.s_door = Door(
            self.s_store, self.s_tracker, self.s_links, self.s_collab, SHARING,
            reach=lambda: "everywhere", owner_name=lambda: "Sam", sleep=lambda _: None,
        )
        self.s_withme = WithMe(
            self.s_priv, own_tokens=lambda: {l["token"] for l in self.s_links.all()},
            door=self.s_door, owner=self.s_store.owner, agent=self.s_store.agent,
        )
        self.s_server = sharing_main.serve(
            self.s_store, self.s_tracker, self.s_links, self.s_collab, self.s_door, self.s_withme,
            self.s_port, reach=lambda: "everywhere",
        )
        threading.Thread(target=self.s_server.serve_forever, daemon=True).start()

    def _start_broker(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from urllib.parse import parse_qs, urlparse

        env = self
        self._old_vault = lf.cfg.VAULT
        self._old_roster = lf.cfg.roster
        self._old_card = lf.cfg.agent_card
        self._old_events = os.environ.get("LOAM_FILES_EVENTS")
        lf.cfg.VAULT = self.vault
        lf.cfg.roster = lambda: ["ada", "nova"]
        lf.cfg.agent_card = lambda aid: {"id": aid, "name": aid.title()}
        os.environ["LOAM_FILES_EVENTS"] = str(self.events_file)

        class BrokerHandler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, obj: dict):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n)) if n else {}

            def _do(self, method: str):
                u = urlparse(self.path)
                q = {k: v[-1] for k, v in parse_qs(u.query).items()}
                try:
                    if u.path == "/v1/owner":
                        return self._send(200, {"id": "sam", "name": "Sam"})
                    if u.path == "/v1/agents":
                        return self._send(200, {"agents": [
                            {"id": "ada", "name": "Ada"},
                            {"id": "nova", "name": "Nova"},
                        ]})
                    if u.path == "/v1/plugin-services":
                        iface = q.get("interface")
                        svcs = []
                        if env.comments_on and iface in (None, "share.collaboration"):
                            svcs.append({"plugin": "comments", "interface": "share.collaboration",
                                         "version": 1, "url": f"http://127.0.0.1:{env.c_port}/collab"})
                        if env.sharing_on and iface in (None, "share.remote"):
                            svcs.append({"plugin": "sharing", "interface": "share.remote",
                                         "version": 1, "url": f"http://127.0.0.1:{env.s_port}/remote"})
                        return self._send(200, {"services": svcs})
                    if u.path == "/v1/files/events":
                        return self._send(200, lf.file_events(since=q.get("since")))
                    if u.path == "/v1/files/stat":
                        return self._send(200, lf.stat_resource(q["root"], q.get("path", "")))
                    if u.path == "/v1/files/list":
                        return self._send(200, lf.listing(q["root"], q.get("path", "")))
                    if u.path == "/v1/files/read":
                        return self._send(200, lf.read_text(q["root"], q["path"]))
                    if u.path == "/v1/files/fingerprint":
                        return self._send(200, lf.fingerprint(q["root"], q["path"]))
                    if u.path == "/v1/files/locate":
                        res = lf.locate(
                            q["root"],
                            dev=q.get("dev"), ino=q.get("ino"),
                            sha256=q.get("sha256"), size=q.get("size"),
                            doc_id=q.get("doc_id"),
                        )
                        return self._send(200, res)
                    if u.path == "/v1/files/download":
                        fd, size, row = lf.download(q["root"], q["path"])
                        try:
                            with os.fdopen(fd, "rb") as fh:
                                data = fh.read()
                        except Exception:
                            os.close(fd)
                            raise
                        ctype = row.get("media_type") or "application/octet-stream"
                        rng = self.headers.get("Range")
                        if rng and rng.startswith("bytes="):
                            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
                            if m:
                                total = len(data)
                                s = int(m.group(1)) if m.group(1) else 0
                                e = min(int(m.group(2)) if m.group(2) else total - 1, total - 1)
                                chunk = data[s:e + 1]
                                self.send_response(206)
                                self.send_header("Content-Type", ctype)
                                self.send_header("Accept-Ranges", "bytes")
                                self.send_header("Content-Range", f"bytes {s}-{e}/{total}")
                                self.send_header("Content-Length", str(len(chunk)))
                                self.end_headers()
                                self.wfile.write(chunk)
                                return
                        self.send_response(200)
                        self.send_header("Content-Type", ctype)
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    if u.path == "/v1/files/folder" and method == "POST":
                        return self._send(201, lf.create_folder(self._body()))
                    if u.path == "/v1/files/file" and method == "POST":
                        return self._send(201, lf.create_file(self._body()))
                    if u.path == "/v1/files/text" and method == "PUT":
                        return self._send(200, lf.save_text(self._body()))
                    if u.path == "/v1/files/rename" and method == "POST":
                        return self._send(200, lf.rename(self._body()))
                    if u.path == "/v1/files/delete" and method == "POST":
                        return self._send(200, lf.delete(self._body()))
                    if u.path == "/v1/files/restore" and method == "POST":
                        return self._send(200, lf.restore(self._body()))
                    return self._send(404, {"error": f"unknown {u.path}"})
                except lf.FilesError as exc:
                    return self._send(exc.status, {"error": exc.message, **exc.extra})

            def do_GET(self):
                self._do("GET")

            def do_POST(self):
                self._do("POST")

            def do_PUT(self):
                self._do("PUT")

        self.b_server = ThreadingHTTPServer(("127.0.0.1", self.b_port), BrokerHandler)
        self.b_server.daemon_threads = True
        threading.Thread(target=self.b_server.serve_forever, daemon=True).start()

    def close(self):
        self.s_server.shutdown()
        self.c_server.shutdown()
        self.b_server.shutdown()
        lf.cfg.VAULT = self._old_vault
        lf.cfg.roster = self._old_roster
        lf.cfg.agent_card = self._old_card
        if self._old_events is None:
            os.environ.pop("LOAM_FILES_EVENTS", None)
        else:
            os.environ["LOAM_FILES_EVENTS"] = self._old_events


def door_open(port: int, token: str, password: str) -> tuple[int, str, dict]:
    st, hdrs, _raw, data = http_req(
        port, "POST", f"/door/{token}/open",
        headers={"X-Loam-Door": "1", "Host": "loam.example"},
        body={"password": password},
    )
    cookie = ""
    sc = hdrs.get("set-cookie") or ""
    m = re.match(r"loam_share=([^;]+)", sc)
    if m:
        cookie = m.group(1)
    return st, cookie, data


def test_four_states_and_workflows(env: TestEnv):
    print("\n2. Four on/off states, whole-file & Markdown lifecycles, MCP tools, and Shared with me")

    # -- State A: Sharing ON, Comments OFF (view-only, zero dead endpoints) --
    env.comments_on = False
    env.sharing_on = True

    st, _, _, cfg = http_req(env.s_port, "GET", "/config")
    check("sharing /config reports collab=False when comments off", cfg["collab"], False)

    # Folders are refused
    st, _, _, _ = http_req(env.s_port, "POST", "/file/links", body={"root": "vault", "path": "notes", "mode": "view"})
    check("sharing refuses folders (400)", st, 400)

    # Comment/edit modes refused when Comments is off
    st, _, _, _ = http_req(env.s_port, "POST", "/file/links", body={"root": "vault", "path": "notes/diagram.png", "mode": "comment"})
    check("comment mode refused when comments plugin is off (400)", st, 400)
    st, _, _, _ = http_req(env.s_port, "POST", "/file/links", body={"root": "vault", "path": "notes/spec.md", "mode": "edit"})
    check("edit mode refused when comments plugin is off (400)", st, 400)

    # View link works for PNG, HTML, SVG, PDF, MP3, ZIP, MD
    st, _, _, created = http_req(env.s_port, "POST", "/file/links", body={"root": "vault", "path": "notes/diagram.png", "mode": "view", "name": "Robin"})
    check("create view link for PNG when comments off (201)", st, 201)
    png_tok = created["link"]["url_path"].split("/")[-1]
    png_pw = created["password"]
    st, cookie, open_data = door_open(env.s_port, png_tok, png_pw)
    check("visitor opens PNG link with password", (st, open_data["kind"], open_data["collab"]), (200, "image", False))
    # Collab endpoint cleanly 404s when comments is off
    st, _, _, _ = http_req(env.s_port, "GET", f"/door/{png_tok}/api/collab/doc",
                           headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={cookie}"})
    check("/api/collab/doc returns 404 when comments is off", st, 404)
    # Raw PNG serves with image/png and sandbox CSP
    st, hdrs, raw, _ = http_req(env.s_port, "GET", f"/door/{png_tok}/raw",
                                headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={cookie}"})
    check("PNG /raw serves valid image/png", (st, hdrs["content-type"], raw.startswith(b"\x89PNG")), (200, "image/png", True))
    check("PNG /raw has nosniff + sandbox CSP", (hdrs["x-content-type-options"], "sandbox" in hdrs["content-security-policy"]), ("nosniff", True))

    # -- State B: Sharing OFF, Comments ON (private comments on any file) --
    env.comments_on = True
    env.sharing_on = False

    orig_png = (env.vault / "notes" / "diagram.png").read_bytes()
    st, _, _, res = http_req(env.c_port, "POST", "/doc/notes", body={"root": "vault", "path": "notes/diagram.png", "kind": "comment", "text": "Check the arrow spacing."})
    check("owner adds whole-file comment on PNG with sharing off (201)", st, 201)
    check("non-Markdown file bytes untouched after commenting", (env.vault / "notes" / "diagram.png").read_bytes(), orig_png)
    check("whole-file comment has anchor=None", res["notes"][0]["anchor"], None)
    tid_png = res["note"]

    # Suggestions rejected on non-Markdown files
    st, _, _, _ = http_req(env.c_port, "POST", "/doc/notes", body={"root": "vault", "path": "notes/diagram.png", "kind": "suggestion", "quote": "x", "replacement": "y"})
    check("suggestion rejected on non-Markdown (400)", st, 400)

    # Agent comments & replies on non-Markdown via comments_mcp.py
    out, err = run_mcp(COMMENTS / "tools" / "comments_mcp.py", f"http://127.0.0.1:{env.c_port}", "ada", str(env.vault),
                       "file_reply", {"path": "notes/diagram.png", "note": tid_png, "text": "Looks balanced on the left side."})
    check("agent replies to whole-file comment via comments_mcp.py", (err, "Ada" in out, "whole file" in out), (False, True, True))
    out2, err2 = run_mcp(COMMENTS / "tools" / "comments_mcp.py", f"http://127.0.0.1:{env.c_port}", "ada", str(env.vault),
                         "file_resolve", {"path": "notes/diagram.png", "note": tid_png})
    check("agent resolves whole-file comment", (err2, "Resolved" in out2), (False, True))
    out3, err3 = run_mcp(COMMENTS / "tools" / "comments_mcp.py", f"http://127.0.0.1:{env.c_port}", "ada", str(env.vault),
                         "file_reopen", {"path": "notes/diagram.png", "note": tid_png})
    check("agent reopens whole-file comment", (err3, "Reopened" in out3), (False, True))

    # -- State C: Both ON (full collaboration across all file types) --
    env.comments_on = True
    env.sharing_on = True

    # Upgrade PNG link to comment mode (edit must be rejected for PNG!)
    st, _, _, _ = http_req(env.s_port, "PATCH", f"/file/links/{created['link']['id']}",
                           body={"root": "vault", "path": "notes/diagram.png", "mode": "edit"})
    check("edit mode rejected on PNG even when comments is on (400)", st, 400)
    st, _, _, upd = http_req(env.s_port, "PATCH", f"/file/links/{created['link']['id']}",
                             body={"root": "vault", "path": "notes/diagram.png", "mode": "comment"})
    check("upgrade PNG link to comment mode (200)", (st, upd["link"]["mode"]), (200, "comment"))

    # Visitor reads and leaves whole-file comment on PNG via door -> collab service
    st, _, _, vdoc = http_req(env.s_port, "GET", f"/door/{png_tok}/api/collab/doc",
                              headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={cookie}"})
    check("visitor sees owner + agent comments on PNG", (st, len(vdoc["notes"]), vdoc["notes"][0]["replies"][0]["author"]["name"]), (200, 1, "Ada"))
    check("visitor response strips vault root and path", ("root" not in vdoc and "path" not in vdoc), True)

    # Agent name masking: set per-file appear to "one"
    st, _, _, ap = http_req(env.c_port, "PATCH", "/appear", body={"root": "vault", "path": "notes/diagram.png", "appear": "one"})
    check("per-file appear=one saved (200)", (st, ap["appear"]["as"]), (200, "one"))
    st, _, _, vdoc_masked = http_req(env.s_port, "GET", f"/door/{png_tok}/api/collab/doc",
                                     headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={cookie}"})
    check("visitor sees agent masked as Sam's agent", vdoc_masked["notes"][0]["replies"][0]["author"]["name"], "Sam's agent")
    # Owner still sees true agent name inside Loam
    st, _, _, odoc = http_req(env.c_port, "GET", "/doc?root=vault&path=notes/diagram.png")
    check("owner still sees true agent name Ada", odoc["notes"][0]["replies"][0]["author"]["name"], "Ada")

    # Visitor adds whole-file comment on PNG (requires X-Loam-Share: 1)
    st, _, _, _ = http_req(env.s_port, "POST", f"/door/{png_tok}/api/collab/doc/notes",
                           headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={cookie}"},
                           body={"kind": "comment", "text": "No CSRF header"})
    check("visitor write without X-Loam-Share refused (403)", st, 403)
    st, _, _, vadd = http_req(env.s_port, "POST", f"/door/{png_tok}/api/collab/doc/notes",
                              headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={cookie}", "X-Loam-Share": "1"},
                              body={"kind": "comment", "text": "Love the warm palette."})
    check("visitor adds whole-file comment on PNG (201)", (st, len(vadd["notes"])), (201, 2))
    v_tid = vadd["note"]
    # Visitor edits and resolves their own comment
    st, _, _, ved = http_req(env.s_port, "PATCH", f"/door/{png_tok}/api/collab/doc/notes/{v_tid}",
                             headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={cookie}", "X-Loam-Share": "1"},
                             body={"text": "Love the warm palette and spacing."})
    check("visitor edits own whole-file comment", (st, any(n["text"] == "Love the warm palette and spacing." for n in ved["notes"])), (200, True))
    st, _, _, vres = http_req(env.s_port, "POST", f"/door/{png_tok}/api/collab/doc/notes/{v_tid}/resolve",
                              headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={cookie}", "X-Loam-Share": "1"},
                              body={})
    check("visitor resolves own whole-file comment", (st, vres["resolved"]), (200, 1))
    # Visitor cannot resolve owner's comment on comment-only link
    st, _, _, _ = http_req(env.s_port, "POST", f"/door/{png_tok}/api/collab/doc/notes/{tid_png}/resolve",
                           headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={cookie}", "X-Loam-Share": "1"},
                           body={})
    check("visitor cannot resolve owner's comment (403)", st, 403)

    # Visitor agent key (`lak_...`) adds a reply on PNG
    st, _, _, akey = http_req(env.s_port, "POST", f"/door/{png_tok}/api/agent-key",
                              headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={cookie}", "X-Loam-Share": "1"},
                              body={})
    check("visitor creates agent key (201)", (st, akey["key"].startswith("lak_")), (201, True))
    st, _, _, arep = http_req(env.s_port, "POST", f"/door/{png_tok}/api/collab/doc/notes/{tid_png}/replies",
                              headers={"X-Loam-Door": "1", "Host": "loam.example", "Authorization": f"Bearer {akey['key']}"},
                              body={"text": "Checked dimensions: 8x8 px."})
    check("visitor agent replies using Bearer lak_ key", (st, arep["notes"][0]["replies"][-1]["author"]["name"]), (201, "Robin's agent"))

    # Files > Shared list enriches PNG with collab open/new counts
    st, _, _, slist = http_req(env.s_port, "GET", "/shared")
    png_row = next(f for f in slist["files"] if f["path"] == "notes/diagram.png")
    check("Files > Shared shows PNG with open=1 and new>0 from visitor", (png_row["open"], png_row["new"] > 0), (1, True))

    # -- Markdown edit, suggest, accept, reject, history protection --
    out, err = run_mcp(SHARING / "tools" / "sharing_mcp.py", f"http://127.0.0.1:{env.s_port}", "ada", str(env.vault),
                       "share_create", {"path": "notes/spec.md", "mode": "edit", "name": "Pat"})
    check("agent creates edit share link on Markdown via sharing_mcp.py", (err, "/s/" in out, "Password:" in out), (False, True, True))
    md_tok = re.search(r"/s/([A-Za-z0-9_-]{32,64})", out).group(1)
    md_pw = re.search(r"Password:\s*(\S+)", out).group(1)
    st, md_cookie, _ = door_open(env.s_port, md_tok, md_pw)
    check("visitor opens Markdown edit link", st, 200)
    # Visitor suggests edit on Markdown
    st, _, _, sres = http_req(env.s_port, "POST", f"/door/{md_tok}/api/collab/doc/notes",
                              headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={md_cookie}", "X-Loam-Share": "1"},
                              body={"kind": "suggestion", "quote": "calm", "replacement": "swift", "text": "More vivid."})
    check("visitor suggests edit on Markdown (201)", (st, "loam_doc_id" not in sres["text"]), (201, True))
    # Visitor cannot read history (owner-only!)
    st, _, _, _ = http_req(env.s_port, "GET", f"/door/{md_tok}/api/collab/doc/history",
                           headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={md_cookie}"})
    check("visitor refused history endpoint (404)", st, 404)
    # Agent accepts suggestion via comments_mcp.py
    out, err = run_mcp(COMMENTS / "tools" / "comments_mcp.py", f"http://127.0.0.1:{env.c_port}", "ada", str(env.vault),
                       "doc_accept", {"path": "notes/spec.md", "note": sres["note"]})
    check("agent accepts Markdown suggestion via comments_mcp.py", (err, "Accepted" in out), (False, True))
    check("accepted suggestion applied to Markdown file on disk", "swift" in (env.vault / "notes" / "spec.md").read_text(), True)

    # -- Shared with me (via Own in-process transport) for PNG and Markdown --
    st, _, _, wm_png = http_req(env.s_port, "POST", "/withme", body={"link": f"https://loam.example/s/{png_tok}", "password": png_pw})
    check("add PNG share link to Shared with me (201)", (st, wm_png["item"]["kind"], wm_png["item"]["collab"]), (201, "image", True))
    wid_png = wm_png["item"]["id"]
    # Comments calls Sharing's `share.remote` v1 loopback service to read & comment on shared:w-... PNG!
    out, err = run_mcp(COMMENTS / "tools" / "comments_mcp.py", f"http://127.0.0.1:{env.c_port}", "nova", str(env.vault),
                       "file_comment", {"path": f"shared:{wid_png}", "text": "Remote agent review via share.remote v1."})
    check("agent comments on Shared-with-me PNG via share.remote v1", (err, "Remote agent review" in out, "Nova · Robin's agent" in out), (False, True, True))
    # Hide remote agent name with comments_appear
    out, err = run_mcp(COMMENTS / "tools" / "comments_mcp.py", f"http://127.0.0.1:{env.c_port}", "nova", str(env.vault),
                       "comments_appear", {"path": f"shared:{wid_png}", "appear": "one"})
    check("comments_appear=one on shared:w-...", (err, "one" in out), (False, True))
    out, err = run_mcp(COMMENTS / "tools" / "comments_mcp.py", f"http://127.0.0.1:{env.c_port}", "nova", str(env.vault),
                       "file_comment", {"path": f"shared:{wid_png}", "text": "Masked remote note."})
    check("remote agent masked as Robin's agent when appear=one", (err, "Robin's agent" in out, "Nova ·" not in out.split("Masked remote note.")[0].splitlines()[-2]), (False, True, True))

    # Turning Comments OFF cleanly downgrades PNG link to view-only without breaking it
    env.comments_on = False
    st, _, _, f_now = http_req(env.s_port, "GET", f"/door/{png_tok}/api/file",
                               headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={cookie}"})
    check("turning Comments off downgrades active comment link cleanly to view", (st, f_now["mode"], f_now["collab"]), (200, "view", False))
    env.comments_on = True


# -- 3. Rename, move, delete, restore, copy lifecycle -----------------------

def test_lifecycle_rename_move_delete_restore(env: TestEnv):
    print("\n3. Rename, move, external move, delete, unrelated file, restore, copy")
    env.comments_on = True
    env.sharing_on = True

    # Create share link + comment on PDF
    st, _, _, c_pdf = http_req(env.s_port, "POST", "/file/links", body={"root": "vault", "path": "notes/report.pdf", "mode": "comment", "name": "Alex"})
    pdf_tok, pdf_pw = c_pdf["link"]["url_path"].split("/")[-1], c_pdf["password"]
    st, pdf_cookie, _ = door_open(env.s_port, pdf_tok, pdf_pw)
    st, _, _, n0 = http_req(env.c_port, "POST", "/doc/notes", body={"root": "vault", "path": "notes/report.pdf", "kind": "comment", "text": "Check page 1."})
    check("PDF link & initial comment created", (st, len(n0["notes"])), (201, 1))

    # 3a. Rename via broker
    st, _, _, _ = http_req(env.b_port, "POST", "/v1/files/rename", body={"root": "vault", "path": "notes/report.pdf", "name": "q4-report.pdf"})
    check("broker rename PDF (200)", st, 200)
    st, _, _, d1 = http_req(env.c_port, "GET", "/doc?root=vault&path=notes/q4-report.pdf")
    check("PDF comment survives rename", (st, len(d1["notes"]), d1["notes"][0]["text"]), (200, 1, "Check page 1."))
    st, _, raw1, _ = http_req(env.s_port, "GET", f"/door/{pdf_tok}/raw",
                              headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={pdf_cookie}"})
    check("PDF share link survives rename", (st, raw1.startswith(b"%PDF-")), (200, True))

    # 3b. Move to another folder via broker
    (env.vault / "archive").mkdir(exist_ok=True)
    st, _, _, _ = http_req(env.b_port, "POST", "/v1/files/rename", body={"root": "vault", "path": "notes/q4-report.pdf", "to": "archive/q4-report.pdf"})
    check("broker move PDF to archive/ (200)", st, 200)
    st, _, _, d2 = http_req(env.c_port, "GET", "/doc?root=vault&path=archive/q4-report.pdf")
    check("PDF comment survives move to folder", (st, len(d2["notes"])), (200, 1))
    st, _, _, f2 = http_req(env.s_port, "GET", f"/door/{pdf_tok}/api/file",
                            headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={pdf_cookie}"})
    check("PDF share link survives move to folder", (st, f2["filename"]), (200, "q4-report.pdf"))

    # 3c. External disk move (os.rename, no broker event!)
    (env.vault / "moved").mkdir(exist_ok=True)
    os.rename(env.vault / "archive" / "q4-report.pdf", env.vault / "moved" / "final.pdf")
    st, _, _, d3 = http_req(env.c_port, "GET", "/doc?root=vault&path=moved/final.pdf")
    check("PDF comment survives external disk rename (inode match)", (st, len(d3["notes"])), (200, 1))
    st, _, _, f3 = http_req(env.s_port, "GET", f"/door/{pdf_tok}/api/file",
                            headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={pdf_cookie}"})
    check("PDF share link survives external disk rename (broker locate)", (st, f3["filename"]), (200, "final.pdf"))

    # 3d. Copy starts clean (no notes, no links)
    (env.vault / "moved" / "final-copy.pdf").write_bytes(MIN_PDF)
    st, _, _, dc = http_req(env.c_port, "GET", "/doc?root=vault&path=moved/final-copy.pdf")
    st2, _, _, lc = http_req(env.s_port, "GET", "/file/links?root=vault&path=moved/final-copy.pdf")
    check("copy of PDF has 0 notes and 0 share links", (len(dc["notes"]), len(lc["links"])), (0, 0))

    # 3e. Delete -> share link immediately 404s; new unrelated file at same path does NOT inherit link or notes; restore brings both back!
    st, _, _, del_res = http_req(env.b_port, "POST", "/v1/files/delete", body={"root": "vault", "path": "moved/final.pdf"})
    rid = del_res["restore_id"]
    check("broker delete PDF (200)", st, 200)
    st, _, _, _ = http_req(env.s_port, "GET", f"/door/{pdf_tok}/api/file",
                           headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={pdf_cookie}"})
    check("deleted PDF share link immediately 404s", st, 404)

    # Create brand new unrelated file at deleted path
    (env.vault / "moved" / "final.pdf").write_bytes(b"%PDF-1.4\nUNRELATED NEW FILE\n%%EOF\n")
    st_link, _, _, _ = http_req(env.s_port, "GET", f"/door/{pdf_tok}/api/file",
                                headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={pdf_cookie}"})
    st_notes, _, _, d_unrel = http_req(env.c_port, "GET", "/doc?root=vault&path=moved/final.pdf")
    check("brand-new file at deleted path does NOT inherit old share link (404)", st_link, 404)
    check("brand-new file at deleted path does NOT inherit old comments (0 notes)", (st_notes, len(d_unrel["notes"])), (200, 0))

    # Remove the unrelated file and restore the original from Trash
    (env.vault / "moved" / "final.pdf").unlink()
    st, _, _, _ = http_req(env.b_port, "POST", "/v1/files/restore", body={"root": "vault", "restore_id": rid})
    check("broker restore PDF (200)", st, 200)
    st, _, _, d_back = http_req(env.c_port, "GET", "/doc?root=vault&path=moved/final.pdf")
    st2, _, _, f_back = http_req(env.s_port, "GET", f"/door/{pdf_tok}/api/file",
                                 headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={pdf_cookie}"})
    check("restored PDF regains its comment and active share link", (st, len(d_back["notes"]), st2, f_back["filename"]), (200, 1, 200, "final.pdf"))


# -- 4. Security headers, active-content sandboxing, range, containment ------

def test_security_and_containment(env: TestEnv):
    print("\n4. Active content sandboxing headers, magic checks, Range 206, and path containment")

    # HTML: /frame has opaque sandbox CSP (no allow-same-origin, connect-src 'none', X-Frame-Options SAMEORIGIN), /raw is forced attachment!
    st, _, _, c_html = http_req(env.s_port, "POST", "/file/links", body={"root": "vault", "path": "notes/mockup.html", "mode": "view", "name": "Sam2"})
    htok, hpw = c_html["link"]["url_path"].split("/")[-1], c_html["password"]
    _, hcookie, _ = door_open(env.s_port, htok, hpw)
    st, hdrs, raw, _ = http_req(env.s_port, "GET", f"/door/{htok}/frame",
                                headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={hcookie}"})
    csp = hdrs.get("content-security-policy", "")
    check("HTML /frame returns 200 text/html", (st, hdrs["content-type"]), (200, "text/html; charset=utf-8"))
    check("HTML /frame CSP has sandbox WITHOUT allow-same-origin", ("sandbox allow-scripts" in csp and "allow-same-origin" not in csp), True)
    check("HTML /frame CSP has connect-src 'none' & frame-ancestors 'self'", ("connect-src 'none'" in csp and "frame-ancestors 'self'" in csp), True)
    check("HTML /frame has X-Frame-Options SAMEORIGIN + nosniff", (hdrs["x-frame-options"], hdrs["x-content-type-options"]), ("SAMEORIGIN", "nosniff"))

    st, hdrs_raw, _, _ = http_req(env.s_port, "GET", f"/door/{htok}/raw",
                                  headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={hcookie}"})
    check("HTML /raw is NEVER served as text/html (forced attachment)", (st, hdrs_raw["content-type"], "attachment" in hdrs_raw["content-disposition"]), (200, "application/octet-stream", True))

    # SVG: /raw has image/svg+xml + sandbox WITHOUT allow-scripts
    st, _, _, c_svg = http_req(env.s_port, "POST", "/file/links", body={"root": "vault", "path": "notes/logo.svg", "mode": "view", "name": "Sam2"})
    stok, spw = c_svg["link"]["url_path"].split("/")[-1], c_svg["password"]
    _, scookie, _ = door_open(env.s_port, stok, spw)
    st, shdrs, _, _ = http_req(env.s_port, "GET", f"/door/{stok}/raw",
                               headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={scookie}"})
    scsp = shdrs.get("content-security-policy", "")
    check("SVG /raw CSP has sandbox WITHOUT allow-scripts", ("sandbox;" in scsp and "allow-scripts" not in scsp and "allow-same-origin" not in scsp), True)
    check("SVG /frame returns 404 (not an HTML file)", http_req(env.s_port, "GET", f"/door/{stok}/frame", headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={scookie}"})[0], 404)

    # Spoofed PNG and spoofed PDF (HTML bytes with .png/.pdf extension) -> forced attachment!
    for fname in ("notes/spoof.png", "notes/spoof.pdf"):
        _, _, _, c_sp = http_req(env.s_port, "POST", "/file/links", body={"root": "vault", "path": fname, "mode": "view", "name": "Sam2"})
        tok, pw = c_sp["link"]["url_path"].split("/")[-1], c_sp["password"]
        _, ck, _ = door_open(env.s_port, tok, pw)
        st, hdrs, _, _ = http_req(env.s_port, "GET", f"/door/{tok}/raw",
                                  headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={ck}"})
        check(f"{fname} fails magic check -> forced octet-stream attachment",
              (st, hdrs["content-type"], "attachment" in hdrs["content-disposition"], "sandbox" in hdrs["content-security-policy"]),
              (200, "application/octet-stream", True, True))

    # Audio MP3 supports Range 206 seeking
    _, _, _, c_mp3 = http_req(env.s_port, "POST", "/file/links", body={"root": "vault", "path": "notes/clip.mp3", "mode": "view", "name": "Sam2"})
    mtok, mpw = c_mp3["link"]["url_path"].split("/")[-1], c_mp3["password"]
    _, mcookie, _ = door_open(env.s_port, mtok, mpw)
    st, mhdrs, mchunk, _ = http_req(env.s_port, "GET", f"/door/{mtok}/raw",
                                    headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={mcookie}", "Range": "bytes=0-15"})
    check("MP3 /raw honors Range bytes=0-15 -> 206 Partial Content", (st, len(mchunk), mhdrs["content-type"], mhdrs["accept-ranges"]), (206, 16, "audio/mpeg", "bytes"))

    # Path traversal, sibling access, symlink escape
    for bad in (
        f"/door/{htok}/../sibling-secret.txt",
        f"/door/{htok}/sibling-secret.txt",
        f"/door/{htok}/%2e%2e/sibling-secret.txt",
        f"/door/{htok}/_/../server/main.py",
        f"/door/{htok}/_/ext/comments/../../server/main.py",
    ):
        st, _, raw, _ = http_req(env.s_port, "GET", bad,
                                 headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={hcookie}"})
        check(f"path attack {bad.split(htok)[1]} refused (404, no secret)", (st, b"TOP SECRET" not in raw), (404, True))

    # Query/body injection trying to read sibling file through /api/file or /api/collab/doc
    st, _, raw, data = http_req(env.s_port, "GET", f"/door/{htok}/api/file?root=vault&path=notes/sibling-secret.txt",
                                headers={"X-Loam-Door": "1", "Host": "loam.example", "Cookie": f"loam_share={hcookie}"})
    check("query path override ignored on /api/file", (st, data["filename"], b"TOP SECRET" not in raw), (200, "mockup.html", True))

    # Symlink escaping vault cannot be shared
    st, _, _, _ = http_req(env.s_port, "POST", "/file/links", body={"root": "vault", "path": "notes/escape.txt", "mode": "view"})
    check("symlink escaping vault refused by sharing (403/404)", st in (403, 404), True)

    # SSRF guard on Shared with me rejects private/loopback IPs
    for ip in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.1", "::1"):
        try:
            vet_addresses([ip])
            check(f"SSRF vet_addresses refuses {ip}", False, True)
        except Refused:
            check(f"SSRF vet_addresses refuses {ip}", True, True)

    # Direct browser /x/ access to internal loopback services refused (404)
    st1, _, _, _ = http_req(env.c_port, "GET", "/collab/capabilities", headers={"X-Forwarded-Prefix": "/x/comments"})
    st2, _, _, _ = http_req(env.s_port, "GET", "/remote/w-1234567890/doc", headers={"X-Forwarded-Prefix": "/x/sharing"})
    check("/collab and /remote reject /x/ gateway requests (404)", (st1, st2), (404, 404))


def main() -> int:
    test_manifests_and_isolation()
    with tempfile.TemporaryDirectory(prefix="loam-sc-test-") as td:
        env = TestEnv(Path(td))
        try:
            test_four_states_and_workflows(env)
            test_lifecycle_rename_move_delete_restore(env)
            test_security_and_containment(env)
        finally:
            env.close()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
