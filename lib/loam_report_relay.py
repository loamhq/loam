"""Maintainer-side Loam report intake. No public listener or route is installed."""

import hashlib
import json
import re
import sqlite3
import time
import urllib.parse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer

import loam_feedback as fb
from loam_report_delivery import FORMAT, MAX_BYTES, issue_url

FIELDS = {"format", "install", "repo", "kind", "title", "body"}
FOOTER = re.compile(r"\n\n---\nLoam [^\n]{1,160}\. Written by an agent on a Loam install "
                    r"and sent by its owner, with personal details taken out\.\Z")
LABEL = "sent-from-Loam"


class Rejected(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status


def validate(packet, repo):
    if (not isinstance(packet, dict) or set(packet) != FIELDS
            or not all(isinstance(v, str) for v in packet.values())
            or packet["format"] != FORMAT or packet["repo"] != repo
            or packet["kind"] not in fb.KINDS or not re.fullmatch("[0-9a-f]{32}", packet["install"])):
        raise Rejected(400, "expected a Loam report")
    title, body = packet["title"], packet["body"]
    if (not title.strip() or len(title) > 256 or len(title.splitlines()) != 1
            or not FOOTER.search(body) or not body.split("\n\n---\n")[0].strip()
            or re.search(r"[\x00-\x08\x0b-\x1f\x7f]", title + body)):
        raise Rejected(400, "expected a titled Loam preview")
    # Only the submitting box knows its owner's names and paths. Apply the
    # shared shape rules here again; a change would invalidate the owner's yes.
    for value in (title, body):
        if fb.scrub(value, names=[], paths=[], domain=None)[0] != value:
            raise Rejected(422, "redraft required after privacy check")
    return {"title": title, "body": body, "labels": [fb.KINDS[packet["kind"]], LABEL]}


class BeforeSubmission(Exception):
    """Credential setup failed before an issue request left the relay."""


def file_issue(repository, payload):
    import loam_github_app as app
    try:
        slot, inst = app.route(app.load(), repository)
        token = app._mint(slot, inst["installation_id"], {
            "repositories": [repository.split("/")[1]], "permissions": {"issues": "write"},
        })["token"]
        # Fresh repositories may lack both the origin label and "idea".
        # Create them only when the live relay has an approved report.
        labels_path = f"/repos/{repository}/labels"
        for label in payload["labels"]:
            path = labels_path + "/" + urllib.parse.quote(label, safe="")
            status, _ = app.api("GET", path, token, ok=(200, 404))
            if status == 404:
                status, _ = app.api("POST", labels_path, token,
                                    {"name": label, "color": "536c83"}, ok=(201, 422))
                if status == 422:
                    app.api("GET", path, token)  # another worker may have created it
    except app.IntegrationError as exc:
        raise BeforeSubmission from exc
    _, issue = app.api("POST", f"/repos/{repository}/issues", token, body=payload)
    return issue["number"]


class Relay(HTTPServer):
    def __init__(self, port, repository, database, submit=None):
        self.repository = repository
        self.database = database
        self.submit = submit
        database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, install TEXT, created INTEGER, result TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS by_time ON requests(created)")
        super().__init__(("127.0.0.1", port), Handler)

    def accept_report(self, packet):
        payload = validate(packet, self.repository)
        install = hashlib.sha256(packet["install"].encode()).hexdigest()
        key = hashlib.sha256(json.dumps(packet, sort_keys=True).encode()).hexdigest()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT result FROM requests WHERE id=?", (key,)).fetchone()
            if existing:
                if existing[0] is None:
                    raise Rejected(409, "delivery pending verification by the makers")
                return json.loads(existing[0])
            since = int(time.time()) - 86400
            total, own = db.execute("SELECT count(*),sum(install=?) FROM requests WHERE created>?",
                                    (install, since)).fetchone()
            if (own or 0) >= 5 or total >= 200:
                raise Rejected(429, "daily report allowance reached")
            # Persist before an external write. A timeout after issue creation
            # must never create a second issue on retry.
            db.execute("INSERT INTO requests VALUES (?,?,?,NULL)", (key, install, int(time.time())))
            db.commit()
            if self.submit:
                try:
                    number = self.submit(self.repository, payload)
                except BeforeSubmission:
                    db.execute("DELETE FROM requests WHERE id=?", (key,))
                    db.commit()
                    raise Rejected(503, "bot unavailable; retry this preview later") from None
                result = {"url": issue_url(self.repository, number)}
            else:
                result = {"dry_run": True, "issue": payload}
            db.execute("UPDATE requests SET result=? WHERE id=?", (json.dumps(result), key))
            return result


class Handler(BaseHTTPRequestHandler):
    timeout = 8

    def log_message(self, *args):
        pass  # no report text or installation identifier in access logs

    def respond(self, code, value):
        body = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            if self.path != "/v1/reports":
                raise Rejected(404, "unknown route")
            if self.headers.get_content_type() != "application/json":
                raise Rejected(415, "expected JSON")
            length = self.headers.get("Content-Length", "")
            if not re.fullmatch(r"[0-9]{1,12}", length) or "Transfer-Encoding" in self.headers:
                raise Rejected(411, "expected a content length")
            if int(length) > MAX_BYTES:
                raise Rejected(413, "report too large")
            packet = json.loads(self.rfile.read(int(length)))
            result = self.server.accept_report(packet)
        except Rejected as exc:
            self.respond(exc.status, {"error": str(exc)})
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            self.respond(400, {"error": "invalid JSON"})
        except Exception:
            self.respond(503, {"error": "issue delivery unavailable"})
        else:
            self.respond(200, result)
