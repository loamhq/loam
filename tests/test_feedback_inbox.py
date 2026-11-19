#!/usr/bin/env python3
"""A report sent privately to Loam's makers, and the inbox that takes it
(2026-11-18, Loam 0.2.7).

Held here:

  sending     `loam report idea` drafts, keeps the draft under its id and
              shows both routes; `loam report send ID` posts exactly the kept
              words (and the install's domain, version and kind, nothing
              else) and prints the makers' short answer; an unknown id, an
              install with no domain, or an unclaimed one sends nothing.
  receiving   bin/loam-claim's POST /_loam/feedback, with the box's DNS
              faked: a report from the address its install's name resolves
              to lands as one markdown file (frontmatter kind, title,
              received, loam_version, install code; the body exactly as sent,
              in a fence so nothing renders) and one line under Other on the
              General board; the file keeps neither the caller's address nor
              the install's name; a caller the name does not resolve to, a
              private address, HTML-free control characters, unknown fields,
              an oversize body, a flood from one install (the sixth in an
              hour), and a run of refusals from one address are refused; with
              `feedback.receive` off the path is a 404.
  the route   with `feedback.receive`, the host `feedback.host` names (an
              app's or a `[[landing]]`'s) carries /_loam/feedback outside the
              login, capped by Caddy, ahead of the browser's subnet and the
              landing's files; no other host does, and without it none does.

Run: python3 tests/test_feedback_inbox.py
"""

from __future__ import annotations

import datetime as dt
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-feedback-inbox-"))
HOME = TMP / "home" / "samuser"
SENDER_CONFIG = TMP / "sender.toml"
os.environ["LOAM_CONFIG"] = str(TMP / "receiver.toml")
(TMP / "receiver.toml").write_text(f'''
user = "{os.environ.get("USER", "nobody")}"
[paths]
home = "{TMP}/makers"
vault = "{TMP}/makers/vault"
[feedback]
receive = true
host = "example.com"
''')
os.environ.pop("LOAM_AI_LABS", None)
sys.path.insert(0, str(ROOT / "lib"))
import loam_deploy as dep  # noqa: E402
import loam_feedback as fb  # noqa: E402

fails = 0
INSTALL = "sam.myloam.example"
ADDRESS = "93.184.216.34"
DNS = {INSTALL: {ADDRESS}}


def check(label: str, got, want=True) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:70} {got!r}"[:260]
          + ("" if ok else f"   want {want!r}"))


def load_claim():
    loader = importlib.machinery.SourceFileLoader("loam_claim", str(ROOT / "bin" / "loam-claim"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def post(url: str, body: bytes, ctype="application/json", xff=None) -> tuple[int, dict]:
    headers = {"Content-Type": ctype}
    if xff:
        headers["X-Forwarded-For"] = xff
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def payload(**over) -> bytes:
    doc = {"v": 1, "kind": "idea", "title": "A calmer morning", "body": "I wish it said less.",
           "version": "0.2.7", "install": INSTALL}
    doc.update(over)
    return json.dumps(doc).encode()


def report_cli(*args: str, stdin: str | None = None, env=None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "bin" / "loam-report"), *args],
                          input=stdin, capture_output=True, text=True, timeout=60,
                          env={**os.environ, "LOAM_CONFIG": str(SENDER_CONFIG), **(env or {})})


def main() -> int:
    claim = load_claim()
    vault = TMP / "makers" / "vault"
    state = TMP / "makers" / "feedback.json"
    real = fb.Inbox
    inbox = lambda: real(vault=vault, state=state,  # noqa: E731
                             folder="40_PROJECTS/Loam/feedback", owner="",
                             resolver=lambda host: DNS.get(host, set()))
    claim.fb.Inbox = inbox
    server = ThreadingHTTPServer(("127.0.0.1", 0), claim.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}{fb.RECEIVE_PATH}"
    folder = vault / "40_PROJECTS" / "Loam" / "feedback"
    import loam_inbox  # noqa: PLC0415
    board = vault / loam_inbox.COMMS

    try:
        # -- receiving -----------------------------------------------------------
        body = ("I wish the morning note said less.\n<script>alert(1)</script>\n"
                "```\nthree backticks\n```\n[[a link]]")
        status, answer = post(url, payload(body=body), xff=ADDRESS)
        check("a report from its install's own address is taken", (status, answer.get("ok")),
              (200, True))
        check("the answer is the short receipt, and says nothing of where it went",
              answer, {"ok": True, "message": fb.ANSWER})
        files = sorted(folder.glob("*.md"))
        check("one markdown file in the feedback folder", len(files), 1)
        text = files[0].read_text() if files else ""
        front = text.split("---\n")[1] if text.count("---\n") >= 2 else ""
        keys = [line.split(":")[0] for line in front.splitlines()]
        check("its frontmatter: kind, title, received, version, install code",
              keys, ["kind", "title", "received", "loam_version", "install"])
        check("the kind and title", ("kind: idea" in front,
                                     'title: "A calmer morning"' in front), (True, True))
        code = next((line.split(": ")[1] for line in front.splitlines()
                     if line.startswith("install:")), "")
        check("the install is a twelve-letter code", len(code) == 12 and code.isalnum())
        check("the file keeps neither the install's name nor the caller's address",
              (INSTALL in text, ADDRESS in text, "sam" in text.lower()), (False, False, False))
        check("the body is exactly as sent, inside a fence longer than any in it",
              f"````text\n{body}\n````\n" in text)
        lines = board.read_text().splitlines() if board.exists() else []
        mine = [ln for ln in lines if "Feedback from a Loam install" in ln]
        check("one line on the General board", len(mine), 1)
        check("...under Other", bool(mine) and lines.index(mine[0]) > lines.index("## Other"))
        check("...naming the kind and title and linking the file",
              bool(mine) and "an idea: “A calmer morning”" in mine[0]
              and f"[[40_PROJECTS/Loam/feedback/{files[0].stem}|the report]]" in mine[0])

        status, _ = post(url, payload(title="<img src=x onerror=alert(1)> [[x]] @iris"),
                         xff=ADDRESS)
        line = next((ln for ln in board.read_text().splitlines() if "img src" in ln), "")
        check("a title's markup never reaches the board as markup",
              (status, "<" in line, "[[x" in line, "@" in line), (200, False, False, False))

        # -- refusals ----------------------------------------------------------
        for label, args, want in (
                ("a caller the name does not resolve to", (payload(), "198.51.100.200"), 403),
                ("a name with no DNS", (payload(install="nobody.myloam.example"), ADDRESS), 403),
                ("no caller address at all", (payload(), None), 403),
                ("a loopback caller", (payload(), "127.0.0.1"), 403),
                ("a private caller", (payload(), "10.1.2.3"), 403),
                ("an unknown field (an attachment)",
                 (payload(attachment="aGVsbG8="), ADDRESS), 400),
                ("a control character", (payload(body="a\x1b[31mred"), ADDRESS), 400),
                ("a kind that is not one", (payload(kind="praise"), ADDRESS), 400),
                ("a body over the cap", (payload(body="x" * (fb.MAX_BODY + 1)), ADDRESS), 413),
                ("not JSON", (b"title=x", ADDRESS), 400)):
            status, answer = post(url, args[0], xff=args[1])
            check(f"refused: {label}", status, want)
        status, _ = post(url, payload(), ctype="text/plain", xff=ADDRESS)
        check("refused: not sent as JSON", status, 415)
        check("nothing refused left a file", len(list(folder.glob("*.md"))), 2)

        statuses = [post(url, payload(title=f"Flood {i}"), xff=ADDRESS)[0] for i in range(5)]
        check("a flood from one install: three more go, then the hour's cap refuses",
              statuses, [200, 200, 200, 429, 429])
        check("five files, the hour's cap", len(list(folder.glob("*.md"))), 5)
        later = inbox().receive(payload(title="Next hour"), ADDRESS,
                                now=dt.datetime.now().timestamp() + 3700)
        check("an hour later it takes one again", later["ok"])

        stranger = "198.51.100.77"
        DNS["stranger.example"] = {"8.8.4.4"}
        codes = [post(url, payload(install="stranger.example"), xff=stranger)[0]
                 for _ in range(fb.REFUSALS[1] + 1)]
        check("a run of refusals from one address is cut off before DNS is asked",
              (codes[0], codes[-1]), (403, 429))

        saved = fb.receiving
        claim.fb.receiving = lambda: False
        check("with feedback.receive off, the path is not there",
              post(url, payload(), xff=ADDRESS)[0], 404)
        claim.fb.receiving = saved

        # -- sending (the install's side) -----------------------------------------
        SENDER_CONFIG.write_text(f'''
[owner]
id = "sam"
name = "Sam Rivera"
[edge]
domain = "{INSTALL}"
[paths]
home = "{HOME}"
vault = "{HOME}/vault"
workspaces = "{HOME}/ai-labs"
state = "{HOME}/.local/state/loam"
[feedback]
url = "{url}"
''')
        # The CLI calls the service directly, with no Caddy in front to set
        # the caller's address; the handler is told it, as Caddy would.
        claim.Handler.client = lambda self: ADDRESS
        state.unlink()       # a fresh day for the sender
        draft = report_cli("idea", "--title", "Sam wishes the board were quieter",
                           "--json", stdin=f"Sam keeps notes in {HOME}/vault/Private/x.md.\n")
        check("loam report idea drafts: exit 0", draft.returncode, 0)
        d = json.loads(draft.stdout or "{}")
        check("an idea is a kind of its own", d.get("kind"), "idea")
        check("the draft is scrubbed (the planted name and path are gone)",
              ("Sam" in d.get("title", "") + d.get("body", ""),
               str(HOME) in d.get("body", ""), "Private/x" in d.get("body", "")),
              (False, False, False))
        check("the draft offers the private route", (d.get("private") or {}).get("ok"))
        kept = HOME / ".local" / "state" / "loam" / "feedback" / "drafts" / f"{d.get('id')}.json"
        check("and keeps the draft under its id", kept.exists())
        shown = report_cli("idea", "--title", "x", "--body", "y")
        check("the words for the owner name both routes and the send line",
              ("loam report send " in shown.stdout, "github.com" in shown.stdout,
               "Privately" in shown.stdout), (True, True, True))

        sent = report_cli("send", d.get("id", ""))
        check("loam report send: exit 0 and the makers' answer",
              (sent.returncode, sent.stdout.strip()), (0, fb.ANSWER))
        newest = max(folder.glob("*.md"), key=lambda p: p.stat().st_mtime)
        check("what arrived is exactly what was shown",
              f"\n{d.get('body')}\n" in newest.read_text()
              and json.dumps(d.get("title")) in newest.read_text())
        check("the sent draft is gone", kept.exists(), False)
        check("sending it again is refused, nothing sent",
              report_cli("send", d.get("id", "")).returncode, 2)
        check("an id that is not one is refused", report_cli("send", "../../etc").returncode, 2)

        nodomain = SENDER_CONFIG.read_text().replace(f'domain = "{INSTALL}"', "")
        (TMP / "nodomain.toml").write_text(nodomain)
        nd = report_cli("idea", "--title", "x", "--body", "y", "--json",
                        env={"LOAM_CONFIG": str(TMP / "nodomain.toml")})
        check("an install with no name on the web offers only GitHub",
              json.loads(nd.stdout or "{}").get("private", {}).get("ok"), False)

        try:
            saved_unclaimed = dep.unclaimed
            dep.unclaimed = lambda: True
            check("an unclaimed install cannot send", fb.private_route()["ok"], False)
        finally:
            dep.unclaimed = saved_unclaimed

        # -- the route ---------------------------------------------------------------
        saved_deploy = {k: getattr(dep, k) for k in ("EDGE", "CLAIMED_FILE", "PUBLIC_FILE",
                                                     "SHARE_DIR", "SITES_DIR", "DEPLOY",
                                                     "CONFIG_PATH")}
        try:
            dep.EDGE = {}
            dep.CLAIMED_FILE = TMP / "claimed"
            dep.PUBLIC_FILE = TMP / "public"
            dep.SHARE_DIR = TMP / "share"
            dep.SITES_DIR = TMP / "sites"
            dep.DEPLOY = ROOT / "deploy"
            dep.CONFIG_PATH = dep.DEPLOY / "loam.toml"
            today = dt.date(2026, 11, 18)
            shell = next(a for a in dep.records() if a.app_shell and a.apex)
            others = [a for a in dep.records() if not a.apex]
            block = dep.caddy_host_block(shell, False, today)
            at, sight = block.find(dep.FEEDBACK_PATH), block.find("@sight")
            check("the app's host carries the route ahead of the browser's subnet",
                  0 < at < (sight if sight > 0 else len(block)))
            section = block[at:block.find("\n\t}\n", at)]
            check("...outside the login, capped, to the claim service",
                  ("import gate" in section, f"max_size {dep.FEEDBACK_MAX_BODY}" in section,
                   f"127.0.0.1:{dep.CLAIM_PORT}" in section), (False, True, True))
            check("no other host carries it",
                  any(dep.FEEDBACK_PATH in dep.caddy_host_block(a, False, today)
                      for a in others), False)
            landing = {"host": "example.com", "root": "/srv/landing", "public": "yes"}
            lblock = dep.caddy_landing_block(landing)
            check("a [[landing]] on feedback.host carries it too, ahead of its files",
                  0 < lblock.find(dep.FEEDBACK_PATH) < lblock.find("file_server"))
            check("...and a landing on another host does not",
                  dep.FEEDBACK_PATH in dep.caddy_landing_block({**landing, "host": "x.example"}),
                  False)
            dep_host = dep.feedback_host
            dep.feedback_host = lambda: None
            check("without feedback.receive no host carries it",
                  (dep.FEEDBACK_PATH in dep.caddy_host_block(shell, False, today),
                   dep.FEEDBACK_PATH in dep.caddy_landing_block(landing)), (False, False))
            dep.feedback_host = dep_host
            caddy = shutil.which("caddy")
            if caddy:
                text = dep.GATE_SNIPPET + "\n" + block + "\n" + lblock.replace(
                    "example.com {", "landing.example.com {")
                text = text.replace("import access_log", "# import access_log").replace(
                    "import compress", "# import compress")
                path = TMP / "Caddyfile"
                path.write_text(text)
                res = subprocess.run([caddy, "adapt", "--config", str(path), "--adapter",
                                      "caddyfile"], capture_output=True, text=True, check=False)
                check("Caddy adapts the block", res.returncode, 0)
        finally:
            for k, v in saved_deploy.items():
                setattr(dep, k, v)
    finally:
        fb.Inbox = real
        server.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)

    print(f"\n{'FAILED' if fails else 'OK'}: {fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
