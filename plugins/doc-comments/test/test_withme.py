#!/usr/bin/env python3
"""Exercise shared document access between local test servers over HTTP and TLS.
Check link validation, access modes, visitor identity, session renewal,
private credential storage and opening same-install links in-process."""

import io
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))

import main as server_main  # noqa: E402
from doccomments import links as L  # noqa: E402
from doccomments import withme as W  # noqa: E402
from doccomments.door import Door  # noqa: E402
from doccomments.service import Service  # noqa: E402
from doccomments.store import MemoryStore  # noqa: E402

DOC = "---\ntags: [x]\n---\n\n# Garden plan\n\nPlant the beans in May. Water them every morning.\n"
THEIRS = {"id": "ada", "name": "Ada", "kind": "owner"}
PASSWORD = "maple-otter-42"
SCRATCH = Path(tempfile.mkdtemp(prefix="dc-withme-"))


def cert():
    key, crt = SCRATCH / "key.pem", SCRATCH / "crt.pem"
    if not crt.exists():
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", str(key), "-out", str(crt), "-days", "1", "-subj", "/CN=door.test",
                        "-addext", "subjectAltName=DNS:door.test"], check=True, capture_output=True)
    return key, crt


class Https(ThreadingHTTPServer):
    daemon_threads = True


def serve_https(handler_cls):
    key, crt = cert()
    srv = Https(("127.0.0.1", 0), handler_cls)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(crt), str(key))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Theirs:
    """The other Loam: a doc, links, and its door at https://door.test:<port>/s/…"""

    def __init__(self):
        self.store = MemoryStore({"vault:Garden/plan.md": DOC}, people={"ada": THEIRS})
        self.svc = Service(self.store)
        self.links = L.Links(tempfile.mkdtemp(prefix="dc-theirs-"))
        self.door = Door(self.svc, self.links, HERE.parent, reach=lambda: "everywhere",
                         owner_name=lambda: "Ada", sleep=lambda s: None)
        self.did = self.svc.ensure_id(THEIRS, "vault", "Garden/plan.md")
        self.seen = []
        door, seen = self.door, self.seen

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self, method):
                seen.append((method, self.path, dict(self.headers)))
                if not self.path.startswith("/s/"):
                    return self._out(404, b"not here", [("Content-Type", "text/plain")])
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                ans = door.handle(method, "/door/" + self.path[3:], {k.lower(): v for k, v in self.headers.items()},
                                  body, self.client_address[0])
                self._out(ans.status, ans.body, [("Content-Type", ans.ctype)] + ans.headers)

            def _out(self, status, body, headers):
                self.send_response(status)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._go("GET")

            def do_POST(self):
                self._go("POST")

            def do_PATCH(self):
                self._go("PATCH")

            def do_DELETE(self):
                self._go("DELETE")

        self.srv = serve_https(H)
        self.port = self.srv.server_address[1]

    def link(self, mode="comment", name="Sam"):
        link, token, _ = self.links.create(self.did, mode, name, 30, PASSWORD)
        return link, f"https://door.test:{self.port}/s/{token}"

    def notes(self):
        return self.svc.get(THEIRS, "vault", "Garden/plan.md")["notes"]

    def text(self):
        return self.store.read_doc("vault", "Garden/plan.md")["text"]


def trusting_remote(resolve=None):
    _, crt = cert()
    ctx = ssl.create_default_context(cafile=str(crt))
    return W.Remote(resolve=resolve or (lambda h, p: ["127.0.0.1"]), context=ctx, allow_private=True)


class Mine:
    """This Loam: the plugin server with Shared with me, over HTTP."""

    def __init__(self, remote=None, door=None, own=lambda: set()):
        self.store = MemoryStore({}, people={"sam": {"id": "sam", "name": "Sam", "kind": "owner"},
                                             "iris": {"id": "iris", "name": "Iris", "kind": "agent"}})
        self.svc = Service(self.store)
        self.private = Path(tempfile.mkdtemp(prefix="dc-mine-private-"))
        self.withme = W.WithMe(self.private, own_tokens=own, door=door, remote=remote or trusting_remote(),
                               owner=self.store.owner, agent=self.store.agent)
        self.log = io.StringIO()
        self.srv = server_main.serve(self.svc, 0, withme=self.withme)
        threading.Thread(target=self._run, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.answers = []

    def _run(self):
        with redirect_stderr(self.log):
            self.srv.serve_forever()

    def call(self, method, route, body=None, agent=None):
        req = urllib.request.Request(self.base + route, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", **({"X-Loam-Agent": agent} if agent else {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                raw = res.read()
                status = res.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(), exc.code
        self.answers.append(raw)
        return status, json.loads(raw)


class Addresses(unittest.TestCase):
    def test_only_https_share_doors(self):
        ok = W.parse_link("https://loam.example.com/s/" + "a" * 32)
        self.assertEqual((ok["host"], ok["port"], ok["token"]), ("loam.example.com", 443, "a" * 32))
        for bad in ("http://loam.example.com/s/" + "a" * 32, "https://loam.example.com/files/x",
                    "https://user:pw@loam.example.com/s/" + "a" * 32, "ftp://x/s/" + "a" * 32,
                    "https://loam.example.com/s/short", "not a link at all", "", None,
                    "https://loam.example.com/s/" + "a" * 32 + "/api/doc", "javascript:alert(1)"):
            with self.assertRaises(W.Refused, msg=bad) as cm:
                W.parse_link(bad)
            self.assertEqual(cm.exception.code, "not-link")

    def test_a_whole_invite_fills_both(self):
        text = ("Sam, Ada invited you to edit “Garden plan”.\nhttps://loam.example.com/s/" + "b" * 32
                + "\nPassword: maple-otter-42\n(Your browser remembers it for 30 days.)")
        got = W.parse_invite(text)
        self.assertEqual(got, {"link": "https://loam.example.com/s/" + "b" * 32, "password": "maple-otter-42"})
        self.assertEqual(W.parse_link(text)["token"], "b" * 32)

    def test_private_and_own_addresses_are_refused(self):
        box = frozenset({__import__("ipaddress").ip_address("8.8.4.4")})
        for a in ("127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254", "100.64.1.1",
                  "0.0.0.0", "224.0.0.1", "::1", "fc00::1", "fe80::1", "::ffff:10.0.0.1", "8.8.4.4", "240.0.0.1"):
            with self.assertRaises(W.Refused, msg=a) as cm:
                W.vet_addresses([a], box)
            self.assertEqual(cm.exception.code, "private", a)
        W.vet_addresses(["8.8.8.8", "2606:4700:4700::1111"], box)
        with self.assertRaises(W.Refused):
            W.vet_addresses(["8.8.8.8", "10.0.0.1"], box)       # one bad record is enough

    def test_refused_before_any_connection(self):
        """A name that resolves to a private address is refused by the real
        Remote (not the tests' trusting one), and nothing is dialled."""
        dialled = []
        real = W.socket.create_connection
        W.socket.create_connection = lambda *a, **k: dialled.append(a) or real(*a, **k)
        try:
            for addr in ("127.0.0.1", "10.0.0.5", "192.168.0.2", "169.254.169.254"):
                remote = W.Remote(resolve=lambda h, p, a=addr: [a])
                mine = W.WithMe(tempfile.mkdtemp(), remote=remote)
                with self.assertRaises(W.Refused) as cm:
                    mine.add("https://loam.example.com/s/" + "c" * 32, PASSWORD)
                self.assertEqual(cm.exception.code, "private")
        finally:
            W.socket.create_connection = real
        self.assertEqual(dialled, [])

    def test_only_the_doors_own_endpoints(self):
        r = trusting_remote()
        link = W.parse_link("https://door.test/s/" + "d" * 32)
        for rest in ("/../../x", "/_/share/share.js", "/forget", "/api/../open", "/admin"):
            with self.assertRaises(W.Refused, msg=rest):
                r.call(link, "GET", rest, {}, None)


class Adding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.theirs = Theirs()

    def setUp(self):
        self.mine = Mine()

    def test_good_password_then_the_row(self):
        _, url = self.theirs.link("edit", "Sam")
        st, out = self.mine.call("POST", "/withme", {"link": url, "password": PASSWORD})
        self.assertEqual(st, 201, out)
        row = out["doc"]
        self.assertEqual((row["title"], row["owner"], row["mode"], row["name"], row["state"], row["new"]),
                         ("Garden plan", "Ada", "edit", "Sam", "ok", False))
        self.assertTrue(row["address"].startswith("shared:w-"))
        self.assertNotIn("/", row["address"].split(":", 1)[1])
        st, out = self.mine.call("GET", "/withme")
        self.assertEqual([d["id"] for d in out["docs"]], [row["id"]])

    def test_each_failure_says_what(self):
        link, url = self.theirs.link("view", "Sam")
        st, out = self.mine.call("POST", "/withme", {"link": url, "password": "wrong-one-1"})
        self.assertEqual((st, out["code"]), (403, "password"))
        self.assertIn("password doesn’t open", out["error"])
        self.theirs.links.update(link["id"], on=False)
        st, out = self.mine.call("POST", "/withme", {"link": url, "password": PASSWORD})
        self.assertEqual((st, out["code"]), (410, "gone"))
        self.assertIn("turned off or has expired", out["error"])
        st, out = self.mine.call("POST", "/withme", {"link": "https://example.com/hello", "password": PASSWORD})
        self.assertEqual((st, out["code"]), (400, "not-link"))
        st, out = self.mine.call("POST", "/withme", {"link": "https://door.test:1/s/" + "e" * 32, "password": PASSWORD})
        self.assertEqual((st, out["code"]), (502, "unreachable"))
        self.assertIn("Couldn’t reach", out["error"])
        st, out = self.mine.call("GET", "/withme")
        self.assertEqual(out["docs"], [])            # nothing failed is kept

    def test_a_group_link_asks_a_name_once(self):
        _, url = self.theirs.link("comment", None)
        st, out = self.mine.call("POST", "/withme", {"link": url, "password": PASSWORD})
        self.assertEqual((st, out["code"], out["suggest"]), (409, "need-name", "Sam"))
        st, out = self.mine.call("POST", "/withme", {"link": url, "password": PASSWORD, "name": "Sam K"})
        self.assertEqual(st, 201, out)
        self.assertEqual(out["doc"]["name"], "Sam K")
        wid = out["doc"]["id"]
        st, doc = self.mine.call("POST", f"/withme/{wid}/doc/notes", {"kind": "comment", "quote": "beans", "text": "Which beans?"})
        self.assertEqual(st, 201, doc)
        mine = [n for n in self.theirs.notes() if n.get("text") == "Which beans?"]
        self.assertEqual(mine[0]["author"]["name"], "Sam K")

    def test_a_pasted_invite_fills_the_password(self):
        _, url = self.theirs.link("view", "Sam")
        invite = f"Sam, Ada invited you to view “Garden plan”.\n{url}\nPassword: {PASSWORD}\n(Your browser remembers it for 30 days.)"
        st, out = self.mine.call("POST", "/withme", {"link": invite, "password": ""})
        self.assertEqual(st, 201, out)

    def test_already_there(self):
        _, url = self.theirs.link("view", "Sam")
        self.assertEqual(self.mine.call("POST", "/withme", {"link": url, "password": PASSWORD})[0], 201)
        st, out = self.mine.call("POST", "/withme", {"link": url, "password": PASSWORD})
        self.assertEqual((st, out["code"]), (409, "already"))


class OtherServers(unittest.TestCase):
    """Servers that are not this Loam's kind of door."""

    def serve(self, fn):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                fn(self)

            do_POST = do_GET
        srv = serve_https(H)
        self.addCleanup(srv.shutdown)
        return f"https://door.test:{srv.server_address[1]}/s/" + "f" * 32

    @staticmethod
    def send(h, status, body, headers=()):
        h.send_response(status)
        for k, v in headers:
            h.send_header(k, v)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    def add(self, url):
        mine = W.WithMe(tempfile.mkdtemp(), remote=trusting_remote())
        with self.assertRaises(W.Refused) as cm:
            mine.add(url, PASSWORD)
        return cm.exception

    def test_not_a_loam(self):
        url = self.serve(lambda h: self.send(h, 404, b"<h1>Nope</h1>"))
        self.assertEqual(self.add(url).code, "not-link")

    def test_an_older_loam(self):
        page = b'<main class="dc-gate">This link doesn\xe2\x80\x99t open anything</main><span class="dc-made">Made with Loam</span>'
        url = self.serve(lambda h: self.send(h, 404, page, [("Content-Type", "text/html")]))
        exc = self.add(url)
        self.assertEqual(exc.code, "too-old")
        self.assertIn("too old", exc.message)

    def test_a_newer_loam(self):
        url = self.serve(lambda h: self.send(h, 200, b'{"loam_share": 3, "min": 2}', [("Content-Type", "application/json")]))
        exc = self.add(url)
        self.assertEqual(exc.code, "too-new")
        self.assertIn("newer", exc.message)

    def test_no_redirect_is_followed(self):
        url = self.serve(lambda h: self.send(h, 302, b"", [("Location", "https://127.0.0.1/s/" + "g" * 32)]))
        self.assertEqual(self.add(url).code, "redirect")

    def test_an_answer_past_the_ceiling_stops(self):
        big = b'{"loam_share": 1, "pad": "' + b"x" * (W.ANSWER_MAX + 10) + b'"}'
        url = self.serve(lambda h: self.send(h, 200, big, [("Content-Type", "application/json")]))
        self.assertEqual(self.add(url).code, "too-big")

    def test_a_slow_server_times_out(self):
        old = W.TIMEOUT
        W.TIMEOUT = 1
        self.addCleanup(lambda: setattr(W, "TIMEOUT", old))
        import time as _t
        url = self.serve(lambda h: _t.sleep(3))
        self.assertEqual(self.add(url).code, "unreachable")

    def test_an_untrusted_certificate(self):
        url = self.serve(lambda h: self.send(h, 200, b'{"loam_share": 1}'))
        mine = W.WithMe(tempfile.mkdtemp(), remote=W.Remote(resolve=lambda h, p: ["127.0.0.1"], allow_private=True))
        with self.assertRaises(W.Refused) as cm:
            mine.add(url, PASSWORD)
        self.assertEqual(cm.exception.code, "unreachable")
        self.assertIn("certificate", cm.exception.message)


class Working(unittest.TestCase):
    def setUp(self):
        self.theirs = Theirs()          # a fresh doc for each: some tests change it
        self.addCleanup(self.theirs.srv.shutdown)
        self.mine = Mine()

    def add(self, mode, name="Sam"):
        link, url = self.theirs.link(mode, name)
        st, out = self.mine.call("POST", "/withme", {"link": url, "password": PASSWORD})
        self.assertEqual(st, 201, out)
        return link, out["doc"]["id"]

    def test_the_owner_and_a_persona_by_the_links_mode(self):
        _, wid = self.add("comment")
        st, doc = self.mine.call("GET", f"/withme/{wid}/doc")
        self.assertEqual(st, 200)
        self.assertTrue(doc["text"].lstrip().startswith("# Garden plan"))       # the body only
        self.assertNotIn("loam_doc_id", doc["text"])
        self.assertEqual((doc["me"]["name"], doc["can"]["comment"], doc["can"]["edit"]), ("Sam", True, False))
        st, out = self.mine.call("POST", f"/withme/{wid}/doc/notes", {"kind": "comment", "quote": "May", "text": "June?"})
        self.assertEqual(st, 201, out)
        st, out = self.mine.call("POST", f"/withme/{wid}/doc/notes",
                                 {"kind": "comment", "quote": "every morning", "text": "Or evening."}, agent="iris")
        self.assertEqual(st, 201, out)
        by = {n["text"]: n["author"] for n in self.theirs.notes()}
        self.assertEqual((by["June?"]["name"], by["June?"]["kind"]), ("Sam", "guest"))
        self.assertEqual((by["Or evening."]["name"], by["Or evening."]["kind"]), ("Iris · Sam's agent", "guest-agent"))
        st, out = self.mine.call("POST", f"/withme/{wid}/doc/save",
                                 {"base_revision": doc["revision"], "text": doc["text"].replace("May", "June")}, agent="iris")
        self.assertEqual(st, 403, out)                # a comment link does not save, for anyone
        self.assertIn("May", self.theirs.text())

    def test_view_reads_only(self):
        _, wid = self.add("view")
        st, out = self.mine.call("POST", f"/withme/{wid}/doc/notes", {"kind": "comment", "quote": "May", "text": "x"})
        self.assertEqual(st, 403, out)
        st, out = self.mine.call("POST", f"/withme/{wid}/doc/notes", {"kind": "comment", "quote": "May", "text": "x"}, agent="iris")
        self.assertEqual(st, 403, out)

    def test_edit_saves_merged(self):
        _, wid = self.add("edit")
        st, doc = self.mine.call("GET", f"/withme/{wid}/doc")
        base = doc["revision"]
        # their owner saves first, elsewhere in the text
        stored = self.theirs.store.read_doc("vault", "Garden/plan.md")
        self.theirs.svc.save(THEIRS, "vault", "Garden/plan.md",
                             {"base_revision": stored["revision"], "text": stored["text"].replace("Plant", "Sow")})
        st, out = self.mine.call("POST", f"/withme/{wid}/doc/save",
                                 {"base_revision": base, "text": doc["text"].replace("every morning", "at dusk")})
        self.assertEqual(st, 200, out)
        text = self.theirs.text()
        self.assertIn("Sow the beans", text)
        self.assertIn("at dusk", text)
        self.assertIn("loam_doc_id", text)            # their frontmatter kept as it was

    def test_a_lapsed_session_is_renewed(self):
        _, wid = self.add("comment")
        self.mine.withme._update(wid, session="expired.garbage")
        st, doc = self.mine.call("GET", f"/withme/{wid}/doc")
        self.assertEqual(st, 200, doc)

    def test_a_link_turned_off_greys_the_row(self):
        link, wid = self.add("comment")
        self.theirs.links.update(link["id"], on=False)
        st, out = self.mine.call("GET", f"/withme/{wid}/doc")
        self.assertEqual((st, out["code"]), (410, "gone"))
        st, out = self.mine.call("GET", "/withme")
        row = next(d for d in out["docs"] if d["id"] == wid)
        self.assertEqual(row["state"], "gone")
        st, out = self.mine.call("DELETE", f"/withme/{wid}")
        self.assertEqual(st, 200)
        self.assertNotIn(wid, json.dumps(self.mine.call("GET", "/withme")[1]))

    def test_a_changed_mode_follows(self):
        link, wid = self.add("comment")
        self.theirs.links.update(link["id"], mode="edit")
        st, out = self.mine.call("GET", "/withme")
        self.assertEqual(next(d for d in out["docs"] if d["id"] == wid)["mode"], "edit")
        st, doc = self.mine.call("GET", f"/withme/{wid}/doc")
        self.assertTrue(doc["can"]["edit"])

    def test_the_dot_follows_what_he_looked_at(self):
        _, wid = self.add("comment")
        row = lambda: next(d for d in self.mine.call("GET", "/withme")[1]["docs"] if d["id"] == wid)
        self.assertFalse(row()["new"])
        self.theirs.svc.add(THEIRS, "vault", "Garden/plan.md", {"kind": "comment", "quote": "beans", "text": "Runner beans."})
        self.assertTrue(row()["new"])
        self.mine.call("GET", f"/withme/{wid}/doc")
        self.assertFalse(row()["new"])
        # a persona reading does not count as the owner looking
        self.theirs.svc.add(THEIRS, "vault", "Garden/plan.md", {"kind": "comment", "quote": "Water", "text": "Twice."})
        self.mine.call("GET", f"/withme/{wid}/doc", agent="iris")
        self.assertTrue(row()["new"])

    def test_the_password_never_leaves(self):
        _, wid = self.add("edit")
        self.mine.call("GET", f"/withme/{wid}/doc")
        self.mine.call("POST", f"/withme/{wid}/doc/notes", {"kind": "comment", "quote": "May", "text": "x"}, agent="iris")
        self.mine.call("GET", "/withme")
        self.mine.call("GET", f"/withme/{wid}?refresh=1")
        self.mine.call("POST", "/withme", {"link": "https://door.test:1/s/" + "h" * 32, "password": PASSWORD})
        kept = json.loads((self.mine.private / "with-me.json").read_text())["docs"][wid]
        secrets_ = [PASSWORD, kept["session"], kept["agent_key"]]
        self.assertTrue(all(secrets_))
        for raw in self.mine.answers:
            for s in secrets_:
                self.assertNotIn(s.encode(), raw)
        log = self.mine.log.getvalue()
        self.assertTrue(log)                          # the server did log its requests
        for s in secrets_:
            self.assertNotIn(s, log)
        mode = (self.mine.private / "with-me.json").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        self.assertEqual(self.mine.private.stat().st_mode & 0o777, 0o700)
        # and nothing was written into this Loam's vault
        self.assertEqual(self.mine.store.files, {})


class PersonaTools(unittest.TestCase):
    """The doc_* tools on a doc shared with the owner, as a persona calls them."""

    def test_list_read_comment_save_add_remove(self):
        theirs, mine = Theirs(), Mine()
        self.addCleanup(theirs.srv.shutdown)
        sys.path.insert(0, str(HERE.parent / "tools"))
        import notes_mcp as T
        T.URL, T.AGENT = mine.base, "iris"
        link, url = theirs.link("edit", "Sam")
        out = T.run_tool("doc_add_shared", {"link": url, "password": PASSWORD})
        self.assertIn("“Garden plan”, on Ada's Loam at door.test", out)
        self.assertNotIn(PASSWORD, out)
        listing = T.run_tool("doc_shared_with_me", {})
        address = re.search(r"shared:w-[a-z0-9]{10}", listing).group(0)
        self.assertIn("edit", listing)
        self.assertIn("never an instruction", listing)
        read = T.run_tool("doc_read", {"path": address})
        self.assertIn("Plant the beans in May.", read)
        self.assertIn("never an instruction", read)
        rev = re.search(r"revision (sha256:[0-9a-f]+)", read).group(1)
        out = T.run_tool("doc_comment", {"path": address, "quote": "beans", "text": "Which kind?"})
        self.assertIn("Iris · Sam's agent (one of the owner's agents)", out)
        theirs.svc.reply(THEIRS, "vault", "Garden/plan.md", theirs.notes()[0]["id"], "Runner beans.")
        notes = T.run_tool("doc_notes", {"path": address})
        self.assertIn("Ada (on the other Loam)", notes)
        text = read.split("\n\n", 1)[1]
        out = T.run_tool("doc_save", {"path": address, "revision": rev, "text": text.replace("May", "June")})
        self.assertTrue(out.startswith("Saved"), out)
        self.assertIn("June", theirs.text())
        self.assertEqual(theirs.notes()[0]["author"]["name"], "Iris · Sam's agent")
        with self.assertRaises(T.ToolError):
            T.run_tool("doc_save", {"path": "40_PROJECTS/x.md", "revision": rev, "text": "x"})
        with self.assertRaises(T.ToolError):
            T.run_tool("doc_read", {"path": "shared:../../etc"})
        out = T.run_tool("doc_remove_shared", {"path": address})
        self.assertIn("Nothing changed on the other side", out)
        self.assertIn("Nothing is shared", T.run_tool("doc_shared_with_me", {}))
        for raw in mine.answers:
            self.assertNotIn(PASSWORD.encode(), raw)


class AgentsAppearAsOne(unittest.TestCase):
    """With the owner's agents shown as one there, a persona's name never
    leaves this Loam: no X-Loam-Agent-Name, nothing in any request."""

    def test_no_persona_name_is_sent(self):
        theirs, mine = Theirs(), Mine()
        self.addCleanup(theirs.srv.shutdown)
        _, url = theirs.link("comment", "Sam")
        st, out = mine.call("POST", "/withme", {"link": url, "password": PASSWORD})
        wid = out["doc"]["id"]
        self.assertEqual(out["doc"]["appear"], "default")
        st, out = mine.call("POST", f"/withme/{wid}/doc/notes",
                            {"kind": "comment", "quote": "beans", "text": "Which kind?"}, agent="iris")
        self.assertEqual(st, 201, out)
        self.assertEqual(theirs.notes()[-1]["author"]["name"], "Iris · Sam's agent")   # names: today's way
        st, out = mine.call("PATCH", f"/withme/{wid}", {"appear": "one"})
        self.assertEqual((st, out["doc"]["appear"]), (200, "one"))
        before = len(theirs.seen)
        st, out = mine.call("POST", f"/withme/{wid}/doc/notes",
                            {"kind": "comment", "quote": "May", "text": "June is warmer."}, agent="iris")
        self.assertEqual(st, 201, out)
        self.assertEqual(out["appear"], {"choice": "one", "as": "one"})
        st, doc = mine.call("GET", f"/withme/{wid}/doc", agent="iris")
        self.assertEqual(doc["me"]["name"], "Sam's agent")
        sent = theirs.seen[before:]
        self.assertTrue(sent)
        for method, path, headers in sent:
            self.assertNotIn("x-loam-agent-name", {k.lower() for k in headers})
            self.assertIsNone(re.search(r"\biris\b", json.dumps([path, headers]), re.I), (path, headers))
        self.assertEqual(theirs.notes()[-1]["author"]["name"], "Sam's agent")
        # What was sent under a name before stays as it was there.
        self.assertEqual(theirs.notes()[0]["author"]["name"], "Iris · Sam's agent")
        # The default decides until the doc chooses.
        mine.call("PATCH", f"/withme/{wid}", {"appear": "default"})
        mine.withme.appear_default = lambda: "one"
        before = len(theirs.seen)
        mine.call("GET", f"/withme/{wid}/doc", agent="iris")
        self.assertFalse(any("X-Loam-Agent-Name" in h for _, _, h in theirs.seen[before:]))
        mine.withme.appear_default = lambda: "names"
        before = len(theirs.seen)
        mine.call("GET", f"/withme/{wid}/doc", agent="iris")
        self.assertTrue(any(h.get("X-Loam-Agent-Name") == "Iris" for _, _, h in theirs.seen[before:]))
        st, out = mine.call("PATCH", f"/withme/{wid}", {"appear": "nobody"})
        self.assertEqual(st, 400)

    def test_the_tools_say_how_the_persona_appears(self):
        theirs, mine = Theirs(), Mine()
        self.addCleanup(theirs.srv.shutdown)
        sys.path.insert(0, str(HERE.parent / "tools"))
        import notes_mcp as T
        T.URL, T.AGENT = mine.base, "iris"
        _, url = theirs.link("comment", "Sam")
        T.run_tool("doc_add_shared", {"link": url, "password": PASSWORD})
        address = re.search(r"shared:w-[a-z0-9]{10}", T.run_tool("doc_shared_with_me", {})).group(0)
        self.assertNotIn("never sign", T.run_tool("doc_read", {"path": address}))
        out = T.run_tool("doc_appear", {"path": address, "appear": "one"})
        self.assertIn("from now on", out)
        read = T.run_tool("doc_read", {"path": address})
        self.assertIn("never sign or name yourself", read)
        notes = T.run_tool("doc_notes", {"path": address})
        self.assertIn("You are Sam's agent there", notes)
        self.assertIn("never sign or name yourself", notes)


class OwnLoam(unittest.TestCase):
    def test_own_link_opens_in_process(self):
        theirs = Theirs()
        link, url = theirs.link("comment", "Sam")
        own = W.parse_link(url)["token"]

        def no_network(h, p):
            raise AssertionError("an own link went to the network")
        mine = W.WithMe(tempfile.mkdtemp(), own_tokens=lambda: {own}, door=theirs.door,
                        remote=W.Remote(resolve=no_network))
        before = len(theirs.seen)
        row = mine.add(url, PASSWORD)
        st, doc = mine.request(row["id"], {"kind": "owner"}, "POST", "doc/notes",
                               {"kind": "comment", "quote": "beans", "text": "To myself."})
        self.assertEqual(st, 201)
        self.assertEqual(len(theirs.seen), before)    # nothing over https
        self.assertEqual(next(n for n in theirs.notes() if n.get("text") == "To myself.")["author"]["kind"], "guest")


if __name__ == "__main__":
    unittest.main(verbosity=1)
