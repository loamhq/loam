"""Saved previews, single offers, confirmed issue links and nightly follow-up."""

import fcntl
import json
import re
import secrets
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing

import loam_config as lc
import loam_feedback as fb
import loam_offline

FORMAT = "loam-report/1"
MAX_BYTES = 64 * 1024


def database():
    folder = lc.STATE / "reports"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(folder / "reports.sqlite3", timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, preview TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS offers (topic TEXT PRIMARY KEY, decision TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sent (
            id TEXT PRIMARY KEY, repo TEXT NOT NULL, number INTEGER NOT NULL,
            title TEXT NOT NULL, last_comment INTEGER DEFAULT 0, fixed TEXT);
    """)
    return db


def relay_url():
    value = lc.get("feedback.relay")
    if not value:
        return None
    url = urllib.parse.urlsplit(value)
    if (not url.hostname or url.username or url.password or url.query or url.fragment
            or not (url.scheme == "https" or
                    (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}))):
        raise ValueError("feedback.relay needs an HTTPS URL (HTTP is allowed on loopback)")
    return value


def save(preview):
    preview["relay"] = relay_url()
    if preview["relay"]:
        import hashlib
        preview["id"] = hashlib.sha256((preview["id"] + "\0" + preview["relay"]).encode()).hexdigest()[:16]
    with closing(database()) as db, db:
        db.execute("INSERT OR IGNORE INTO drafts VALUES (?,?)", (preview["id"], json.dumps(preview)))
    return preview


def offer(topic, decline=False):
    if not topic.strip() or len(topic) > 200:
        raise ValueError("provide a short stable topic for this offer")
    with closing(database()) as db, db:
        if decline:
            db.execute("INSERT OR REPLACE INTO offers VALUES (?, 'declined')", (topic,))
            return "Decline remembered."
        added = db.execute("INSERT OR IGNORE INTO offers VALUES (?, 'offered')", (topic,))
        return "Offer once." if added.rowcount else "Already offered. Stay quiet."


def saved(db, ident):
    row = db.execute("SELECT preview FROM drafts WHERE id=?", (ident,)).fetchone()
    if not row:
        raise ValueError("no saved preview with that ID")
    return json.loads(row[0])


def issue_url(repo, number):
    return f"https://github.com/{repo}/issues/{number}"


def remember(db, report, url):
    prefix = issue_url(report["repo"], "")
    if not isinstance(url, str) or not re.fullmatch(re.escape(prefix) + r"[1-9][0-9]{0,12}", url):
        raise ValueError("the submitted issue link must be on the preview's repository")
    number = int(url[len(prefix):])
    db.execute("INSERT OR IGNORE INTO sent(id,repo,number,title) VALUES (?,?,?,?)",
               (report["id"], report["repo"], number, report["title"]))
    recorded = db.execute("SELECT number FROM sent WHERE id=?", (report["id"],)).fetchone()[0]
    if recorded != number:
        raise ValueError("this preview already has a different submitted issue")
    return url


def track(ident, url):
    with closing(database()) as db, db:
        return remember(db, saved(db, ident), url)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(ident, yes):
    with closing(database()) as db, db:
        report = saved(db, ident)
        prior = db.execute("SELECT repo,number FROM sent WHERE id=?", (ident,)).fetchone()
        if prior:
            return issue_url(*prior)
        if not yes:
            raise ValueError("show the saved preview and ask the owner before using --yes")
        url = relay_url()
        if not url:
            raise ValueError("account-free sending is not available yet. Use the preview's GitHub link.")
        if url != report.get("relay") or fb.repo() != report["repo"]:
            raise ValueError("the destination changed. Show a fresh preview and ask again.")
        db.execute("INSERT OR IGNORE INTO settings VALUES ('install',?)", (secrets.token_hex(16),))
        install = db.execute("SELECT value FROM settings WHERE name='install'").fetchone()[0]
        db.commit()  # a lost response must retry with the same installation ID
        packet = {"format": FORMAT, "install": install,
                  **{k: report[k] for k in ("repo", "kind", "title", "body")}}
        raw = json.dumps(packet, ensure_ascii=False).encode()
        if len(raw) > MAX_BYTES:
            raise ValueError("report too long for the relay. Use the preview's GitHub link.")
        loam_offline.guard_url(url)
        req = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=35) as response:
                result = json.loads(response.read(8192))
        except urllib.error.HTTPError as exc:
            if exc.code == 422:
                raise ValueError("the relay found more private text. Edit and show a fresh preview.") from None
            raise ValueError(f"relay returned HTTP {exc.code}. Keep this preview for retry.") from None
        if result.get("dry_run"):
            return "Dry run only. No issue filed."
        return remember(db, report, result.get("url"))


def public_get(path):
    import loam_github_app as github
    status, data = github.api("GET", path, "", ok=(200, 404))
    if status == 404:
        if "/git/ref/tags/" in path:
            return {}
        raise ValueError("public issue unavailable")
    return data


def check(get=public_get, notify=None):
    if notify is None:
        from functools import partial
        from loam_attention import tell
        notify = partial(tell, detail="Tell the owner this update in one line with the issue link.")
    from loam_github_app import IntegrationError
    with closing(database()) as db, (lc.STATE / "reports" / "check.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        issues = db.execute("SELECT * FROM sent WHERE fixed IS NULL ORDER BY number").fetchall()
        errors = []
        for row in issues:
            try:
                follow(db, row, get, notify)
            except (OSError, ValueError, RuntimeError, KeyError, IntegrationError) as exc:
                errors.append(f"#{row['number']} ({type(exc).__name__})")
        if errors:
            raise ValueError("could not check " + ", ".join(errors))


def follow(db, row, get, notify):
    base = f"/repos/{row['repo']}"
    path = f"{base}/issues/{row['number']}"
    url = issue_url(row["repo"], row["number"])
    issue = get(path)
    if "pull_request" in issue:
        raise ValueError("expected an issue")
    title = " ".join(row["title"].split())
    labels = [v.get("name", "") for v in issue.get("labels", [])]
    version = next((m[1] for label in labels
                    if (m := re.fullmatch(r"fixed-in:(v?\d+\.\d+\.\d+)", label))), None)
    if issue.get("state") == "closed" and version:
        # Loam publishes version tags. A target milestone alone can predate shipping.
        tag = get(f"{base}/git/ref/tags/{version}")
        if tag.get("ref") == f"refs/tags/{version}":
            kind = saved(db, row["id"])["kind"]
            line = (f'The bug you reported about “{title}” is fixed in {version}. Updating brings it.'
                    if kind == "bug" else f'Your report about “{title}” was addressed in {version}.')
            outcome = notify(line, by="loam-report", key=f"{url}/fixed/{version}", evidence=url)
            if outcome.startswith("failed"):
                raise ValueError("agent notification pending")
            with db:
                db.execute("UPDATE sent SET fixed=? WHERE id=?", (version, row["id"]))
            return
    if not issue.get("comments"):
        return
    # GitHub returns issue comments oldest first. Aggregate multiple new replies
    # into one notification, excluding the reporter's own comments.
    newest = row["last_comment"]
    page, reply = 1, None
    while True:
        batch = get(f"{path}/comments?per_page=100&page={page}")
        for comment in batch:
            cid = comment["id"]
            if cid <= row["last_comment"]:
                continue
            newest = max(newest, cid)
            if (comment.get("author_association") in {"MEMBER", "OWNER", "COLLABORATOR"}
                    and comment.get("user", {}).get("type") != "Bot"
                    and comment.get("user", {}).get("id") != issue.get("user", {}).get("id")):
                reply = cid
        if len(batch) < 100:
            break
        page += 1
    if reply:
        line = f'A maker replied to your report about “{title}”. {url}#issuecomment-{reply}'
        outcome = notify(line, by="loam-report", key=f"{url}/comment/{reply}", evidence=url)
        if outcome.startswith("failed"):
            raise ValueError("agent notification pending")
    with db:
        db.execute("UPDATE sent SET last_comment=max(last_comment,?) WHERE id=?", (newest, row["id"]))
