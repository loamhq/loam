#!/usr/bin/env python3
"""Exercise the Files API through real HTTP against temporary roots."""

import asyncio
import http.client
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import io
import time
import zipfile
from pathlib import Path
from urllib.parse import quote

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
# Use fixture memory to isolate the broker from machine load.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_files as files  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results = []
SECRET = "sk-THIS-MUST-NEVER-APPEAR"


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:72} got={str(got)[:120]!r}")
    if not ok:
        print(f"      wanted {want!r}")


class Client:
    def __init__(self, port: int, token: str) -> None:
        self.port, self.token = port, token

    def raw(self, method: str, target: str, body=None, token=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        headers = {}
        tok = self.token if token is None else token
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, target, body=data, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, payload

    def json(self, method: str, target: str, body=None, token=None):
        status, headers, payload = self.raw(method, target, body, token)
        return status, json.loads(payload), payload.decode("utf-8", "replace")

    def upload(self, parts, root="vault", path="", token=None,
               content_type=None, raw_body=None):
        """POST /v1/files/upload with a multipart body built here byte by
        byte. ``parts`` is a list of (field, filename, bytes)."""
        boundary = "----loamtest" + os.urandom(8).hex()
        body = b""
        for field, filename, data in parts:
            body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                     f"name=\"{field}\"; filename=\"{filename}\"\r\n"
                     f"Content-Type: application/octet-stream\r\n\r\n"
                     ).encode("utf-8") + data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        if raw_body is not None:
            body = raw_body
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        headers = {"Content-Type": content_type
                   or f"multipart/form-data; boundary={boundary}"}
        tok = self.token if token is None else token
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        target = (f"/v1/files/upload?root={quote(root, safe='')}"
                  f"&path={quote(path, safe='')}")
        conn.request("POST", target, body=body, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        try:
            return resp.status, json.loads(payload)
        except json.JSONDecodeError:
            return resp.status, {"raw": payload.decode("utf-8", "replace")}

    def get(self, route: str, **query):
        target = f"/v1/files/{route}"
        if query:
            target += "?" + "&".join(f"{k}={quote(str(v), safe='')}"
                                     for k, v in query.items())
        return self.json("GET", target)


def build(tmp: Path) -> dict:
    vault = tmp / "vault"
    labs = tmp / "ai-labs"
    system = tmp / "agents"
    outside = tmp / "outside"
    for d in (vault, labs, system, outside):
        d.mkdir()
    (outside / "passwd").write_text(f"root:{SECRET}\n")
    # The vault
    (vault / "40_PROJECTS" / "Loam").mkdir(parents=True)
    (vault / "40_PROJECTS" / "Loam" / "state.md").write_text(
        "---\ncreated_by: \"[[Ada]]\"\nlast_modified_by: \"[[Ada]]\"\n---\n\n# State\n")
    (vault / "40_PROJECTS" / "Loam" / "Plan.md").write_text("# Plan\n")
    (vault / "20_ATLAS").mkdir()
    (vault / "20_ATLAS" / "Plan.md").write_text("# Another plan\n")
    (vault / "20_ATLAS" / "Unique note.md").write_text("# Unique\n")
    (vault / "15_CONVERSATIONS").mkdir()
    (vault / "15_CONVERSATIONS" / "2026-10-02 chat.md").write_text("# a transcript\n")
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "app.json").write_text(SECRET)
    (vault / ".git").mkdir()
    (vault / ".git" / "config").write_text(SECRET)
    (vault / "deploy.env").write_text(f"KEY={SECRET}\n")
    (vault / "server.pem").write_text(SECRET)
    (vault / "big.md").write_bytes(b"x" * (files.TEXT_READ_MAX + 10))
    (vault / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    (vault / "latin1.txt").write_bytes(b"caf\xe9\n")
    (vault / "page.html").write_text("<script>alert(1)</script>")
    (vault / "Many").mkdir()
    for i in range(25):
        (vault / "Many" / f"n{i:02d}.md").write_text(str(i))
    os.symlink(outside / "passwd", vault / "link-to-passwd.md")
    os.symlink(outside, vault / "link-dir")
    os.symlink(vault / "40_PROJECTS", vault / "inner-link")
    (vault / "%2e%2e").mkdir()
    (vault / "%2e%2e" / "literal.md").write_text("a folder literally named %2e%2e\n")
    # Partial and relative paths.
    (vault / "40_PROJECTS" / "loam-ui" / "research").mkdir(parents=True)
    (vault / "40_PROJECTS" / "loam-ui" / "loam-ui.md").write_text("# loam-ui\n")
    (vault / "40_PROJECTS" / "loam-ui" / "research" / "deep.md").write_text("# deep\n")
    (vault / "40_PROJECTS" / "Loam" / "notes.md").write_text("# notes\n")
    (vault / "40_PROJECTS" / "Loam" / "creds.env").write_text(SECRET)
    (vault / "Zarchive" / "Loam").mkdir(parents=True)
    (vault / "Zarchive" / "Loam" / "notes.md").write_text("# other notes\n")
    # Copies of Loam/state.md where a lookup must never find them: a hidden
    # folder, and through each of the vault's links (inner-link names
    # 40_PROJECTS; link-dir names outside/).
    (vault / ".trash" / "40_PROJECTS" / "Loam").mkdir(parents=True)
    (vault / ".trash" / "40_PROJECTS" / "Loam" / "state.md").write_text(SECRET)
    (outside / "Loam").mkdir()
    (outside / "Loam" / "state.md").write_text(SECRET)
    (vault / "99_SYSTEM" / "conventions").mkdir(parents=True)
    (vault / "99_SYSTEM" / "conventions" / "provenance.md").write_text("# p\n")
    (vault / "40_PROJECTS" / "Loam" / "engineer").mkdir()
    (vault / "40_PROJECTS" / "Loam" / "engineer" / "role.md").write_text("# role\n")
    (vault / "15_CONVERSATIONS" / "shown.html").write_text("<p>a page</p>\n")
    (vault / "latin1.html").write_bytes(b"<p>caf\xe9</p>\n")
    (vault / "big.html").write_bytes(b"<p>" + b"x" * files.TEXT_READ_MAX + b"</p>")
    # Exercise links to folders, ambiguous names and paths with spaces.
    (vault / "40_PROJECTS" / "Half Year").mkdir()
    (vault / "40_PROJECTS" / "Half Year" / "agenda.md").write_text("# h\n")
    # A folder and its folder note share a name.
    (vault / "40_PROJECTS" / "Loam" / "Loam.md").write_text("# Loam\n")
    (vault / "20_ATLAS" / "Topics").mkdir()
    (vault / "20_ATLAS" / "Topics" / "one.md").write_text("# one\n")
    (vault / "20_ATLAS" / "Topics.md").write_text("# Topics, the note\n")
    (vault / "Zarchive" / "Many.md").write_text("# a note named Many\n")
    (vault / "40_PROJECTS" / "archive.key").mkdir()
    (vault / "40_PROJECTS" / "archive.key" / "x.md").write_text(SECRET)
    # The spaces
    for agent in ("alpha", "beta"):
        ws = labs / agent
        (ws / ".claude").mkdir(parents=True)
        (ws / "agent.json").write_text(json.dumps({"display": agent.title()}))
        (ws / "soul.md").write_text(f"# {agent}'s soul\n")
        (ws / "config.md").write_text(f"# {agent}'s config\n")
        (ws / "_active.md").write_text("active\n")
        (ws / "CLAUDE.md").write_text("@soul.md\n")
        (ws / ".mcp.json").write_text("{}\n")
        (ws / "logs").mkdir()
        (ws / "logs" / "wake.log").write_text(SECRET)
        (ws / "scratch").mkdir()
        (ws / ".locks").mkdir()
        (ws / "memory").mkdir()
        (ws / "memory" / "MEMORY.md").write_text("memory\n")
        (ws / "wake.env").write_text(SECRET)
        (ws / ".env").write_text(SECRET)
        os.symlink("../skills", ws / ".claude" / "skills")
    (labs / "alpha" / ".claude" / "settings.json").write_text(json.dumps(
        {"permissions": {"additionalDirectories": [str(vault)]}}))
    (labs / "beta" / ".claude" / "settings.json").write_text(json.dumps(
        {"permissions": {"additionalDirectories": [str(vault), str(labs)]}}))
    (labs / "commons").mkdir()
    (labs / "commons" / "alpha").mkdir()
    (labs / "commons" / "alpha" / "note.md").write_text("to the commons\n")
    (labs / ".git").mkdir()
    (labs / "stray").mkdir()          # no agent.json: not an agent's space
    (labs / "README.md").write_text("# ai-labs\n")
    (labs / "alpha" / "memory" / "page.html").write_text("<p>alpha's</p>\n")
    # System
    for rel in ("broker/README.md", "deploy/README.md", "engineer/role.md"):
        (system / rel).parent.mkdir(parents=True, exist_ok=True)
        (system / rel).write_text(f"# {rel}\n")
    (system / "wake.env").write_text(SECRET)
    return {"vault": vault, "labs": labs, "system": system, "outside": outside}


def no_secret(label: str, text: str) -> None:
    check(f"no secret in: {label}", SECRET in text, False)


def test_auth(c: Client) -> None:
    for route in ("roots", "list?root=vault", "read?root=vault&path=tmp.md",
                  "download?root=vault&path=tmp.md", "resolve?ref=x",
                  "stat?root=vault"):
        status, _, payload = c.raw("GET", f"/v1/files/{route}", token="")
        check(f"no token: 401 on {route}", status, 401)
        status, _, _ = c.raw("GET", f"/v1/files/{route}", token="wrong")
        check(f"wrong token: 401 on {route}", status, 401)
    for method, route in (("PUT", "text"), ("POST", "file"), ("POST", "folder"),
                          ("POST", "rename")):
        status, _, _ = c.raw(method, f"/v1/files/{route}",
                             {"root": "vault", "path": "x.md", "text": "x"},
                             token="")
        check(f"no token: 401 on {method} {route}", status, 401)


def test_read(c: Client, t: dict) -> None:
    status, body, text = c.get("roots")
    check("roots: three, by name", [r["root"] for r in body["roots"]],
          ["vault", "spaces", "system"])
    status, body, text = c.get("list", root="vault")
    names = [e["name"] for e in body["entries"]]
    check("vault list: 200", status, 200)
    check("vault list: folders first, then files",
          names[:6], ["%2e%2e", "15_CONVERSATIONS", "20_ATLAS", "40_PROJECTS",
                      "99_SYSTEM", "Many"])
    for hidden in (".obsidian", ".git", "deploy.env", "server.pem",
                   "link-to-passwd.md", "link-dir", "inner-link"):
        check(f"vault list hides {hidden}", hidden in names, False)
    no_secret("vault listing", text)
    check("no-store on a listing", c.raw("GET", "/v1/files/list?root=vault")[1]
          .get("cache-control"), "no-store")

    status, body, _ = c.get("list", root="vault", path="Many", limit=10)
    check("page 1: 10 of 25", (len(body["entries"]), body["total"],
                               body["next_cursor"]), (10, 25, "10"))
    status, body, _ = c.get("list", root="vault", path="Many", limit=10, cursor=20)
    check("last page: 5, no next", (len(body["entries"]), body["next_cursor"]),
          (5, None))
    status, body, _ = c.get("list", root="vault", path="Many", limit=99999)
    check("limit is capped, not refused", (status, len(body["entries"])), (200, 25))
    status, body, _ = c.get("list", root="vault", path="Many", limit="x")
    check("a limit that is not a number: 400", status, 400)

    status, body, _ = c.get("read", root="vault", path="40_PROJECTS/Loam/state.md")
    check("read: 200 with text", (status, body["text"].endswith("# State\n")),
          (200, True))
    check("read: a sha256 revision", body["revision"].startswith("sha256:"), True)
    check("read: capabilities", body["capabilities"],
          {"read": True, "download": True, "write": True, "create": False,
           "rename": True})
    check("read: the deep link", body["link"], "/files/vault/40_PROJECTS/Loam/state.md")

    status, body, _ = c.get("read", root="vault",
                            path="15_CONVERSATIONS/2026-10-02 chat.md")
    check("a transcript is readable", (status, body["text"]), (200, "# a transcript\n"))
    check("a transcript is read-only: its authority",
          (body["authority"], body["capabilities"]["write"]), ("transcripts", False))

    status, body, _ = c.get("read", root="vault", path="big.md")
    check("too big to read as text: 413 with the numbers",
          (status, body["max"]), (413, files.TEXT_READ_MAX))
    status, body, _ = c.get("read", root="vault", path="image.png")
    check("binary: 415, download instead", status, 415)
    status, body, _ = c.get("read", root="vault", path="latin1.txt")
    check("not UTF-8: 415", status, 415)
    status, body, _ = c.get("read", root="vault", path="nope.md")
    check("missing: 404", status, 404)
    status, body, _ = c.get("read", root="vault", path="40_PROJECTS")
    check("a folder read as a file: 400", status, 400)
    status, body, _ = c.get("list", root="vault", path="tmp/../x")
    check("list with ..: 400", status, 400)
    status, body, _ = c.get("list", root="nowhere")
    check("unknown root: 404", status, 404)
    status, body, _ = c.get("read", root="vault",
                            path="%2e%2e/literal.md")
    check("a name that is literally %2e%2e is a name", (status, body.get("text")),
          (200, "a folder literally named %2e%2e\n"))

    # Download: bytes, headers that cannot run anything
    status, headers, payload = c.raw("GET", "/v1/files/download?root=vault&path=image.png")
    check("download: the bytes", (status, payload),
          (200, b"\x89PNG\r\n\x1a\n\x00\x00binary"))
    check("download: content type", headers.get("content-type"), "image/png")
    check("download: nosniff", headers.get("x-content-type-options"), "nosniff")
    check("download: attachment", headers.get("content-disposition"),
          "attachment; filename*=UTF-8''image.png")
    status, headers, payload = c.raw("GET", "/v1/files/download?root=vault&path=page.html")
    check("html download: sandboxed by CSP",
          headers.get("content-security-policy"), "sandbox; default-src 'none'")
    status, headers, payload = c.raw(
        "GET", "/v1/files/download?root=vault&path=" + quote("15_CONVERSATIONS/2026-10-02 chat.md"))
    check("download with a space, encoded %20", (status, payload),
          (200, b"# a transcript\n"))
    status, headers, payload = c.raw(
        "GET", "/v1/files/download?root=vault&path=15_CONVERSATIONS/2026-10-02+chat.md")
    check("download with a space, encoded +", status, 200)
    old = files.DOWNLOAD_MAX
    files.DOWNLOAD_MAX = 100
    status, _, payload = c.raw("GET", "/v1/files/download?root=vault&path=big.md")
    files.DOWNLOAD_MAX = old
    check("download over the cap: 413", status, 413)

    # Spaces
    status, body, text = c.get("list", root="spaces")
    check("spaces: the agents and the commons, nothing else",
          [e["name"] for e in body["entries"]],
          ["alpha", "beta", "commons", "README.md"])
    status, body, text = c.get("list", root="spaces", path="alpha")
    names = [e["name"] for e in body["entries"]]
    check("alpha's space: config visible, private and secret not",
          names, [".claude", "memory", ".mcp.json", "_active.md", "agent.json",
                  "CLAUDE.md", "config.md", "soul.md"])
    no_secret("a workspace listing", text)
    rows = {e["name"]: e for e in body["entries"]}
    check("soul.md: the owner's, writable", (rows["soul.md"]["authority"],
                                           rows["soul.md"]["capabilities"]["write"]),
          ("user", True))
    check("_active.md: the persona's, read-only",
          (rows["_active.md"]["authority"], rows["_active.md"]["capabilities"]["write"]),
          ("persona", False))
    check("CLAUDE.md: the runtime's, read-only",
          (rows["CLAUDE.md"]["authority"], rows["CLAUDE.md"]["capabilities"]["write"]),
          ("runtime", False))
    status, body, _ = c.get("list", root="spaces", path="alpha/.claude")
    check("the .claude folder: settings shown, the skills link not",
          [e["name"] for e in body["entries"]], ["settings.json"])
    status, body, _ = c.get("read", root="spaces", path="commons/alpha/note.md")
    check("the commons reads", (status, body["text"]), (200, "to the commons\n"))
    status, body, _ = c.get("list", root="spaces", path="stray")
    check("a folder that is not an agent's: 404", status, 404)

    # System
    status, body, _ = c.get("list", root="system")
    check("system: the curated entries present on the box",
          [e["name"] for e in body["entries"]],
          ["Engineer", "Deployment.md", "Session broker.md"])
    status, body, _ = c.get("read", root="system", path="Engineer/Role.md")
    check("system reads its file", (status, body["text"]), (200, "# engineer/role.md\n"))
    check("system is read-only", body["capabilities"]["write"], False)
    status, body, text = c.get("read", root="system", path="wake.env")
    check("system: an unlisted file in the base is 404", status, 404)
    no_secret("system 404", text)


def test_refusals(c: Client, t: dict) -> None:
    secret_paths = [
        ("vault", "deploy.env"), ("vault", "server.pem"), ("vault", ".git/config"),
        ("vault", ".obsidian/app.json"), ("spaces", "alpha/wake.env"),
        ("spaces", "alpha/.env"), ("spaces", "alpha/logs/wake.log"),
        ("spaces", ".git"), ("vault", "link-to-passwd.md"),
        ("vault", "link-dir/passwd"), ("vault", "inner-link/Loam/state.md"),
        ("spaces", "alpha/.claude/skills"),
    ]
    for root, path in secret_paths:
        for route in ("read", "download", "stat", "list"):
            status, _, payload = c.raw(
                "GET", f"/v1/files/{route}?root={root}&path={quote(path, safe='')}")
            text = payload.decode("utf-8", "replace")
            check(f"{route} {root}/{path}: 404, nothing of it",
                  (status, SECRET in text), (404, False))
    traversal = ["../outside/passwd", "..%2Foutside%2Fpasswd",
                 "%2e%2e/outside/passwd", "%2E%2E%2Foutside%2Fpasswd",
                 "40_PROJECTS/../../outside/passwd", "./tmp.md",
                 "a//b", "..\\outside\\passwd", "x%00.md",
                 "%252e%252e/outside/passwd"]
    for raw in traversal:
        status, _, payload = c.raw("GET", f"/v1/files/read?root=vault&path={raw}")
        text = payload.decode("utf-8", "replace")
        check(f"traversal {raw}: refused",
              (status in (400, 404), SECRET in text), (True, False))
    for target in ("/v1/files/read?root=vault&path=/etc/passwd",
                   "/v1/files/read?root=%2Fetc&path=passwd",
                   "/v1/files/read?root=vault&path=" + quote(str(t["outside"] / "passwd"))):
        status, _, payload = c.raw("GET", target)
        check(f"absolute path {target[-30:]}: refused, no content",
              (status in (400, 404), SECRET in payload.decode("utf-8", "replace")),
              (True, False))
    status, _, _ = c.raw("GET", "/v1/files/exec?cmd=id")
    check("no route that is not one: 405", status, 405)
    status, _, _ = c.raw("DELETE", "/v1/files/text?root=vault&path=tmp.md")
    check("no deleting: 405", status, 405)

    # A race: the folder a listing showed is swapped for a link before the read.
    victim = t["vault"] / "Swap"
    victim.mkdir()
    (victim / "note.md").write_text("ok\n")
    status, body, _ = c.get("read", root="vault", path="Swap/note.md")
    check("before the swap: reads", status, 200)
    shutil.rmtree(victim)
    (t["outside"] / "note.md").write_text(SECRET)
    os.symlink(t["outside"], victim)
    status, _, text = c.get("read", root="vault", path="Swap/note.md")
    check("after the swap to a link: 404, nothing from outside",
          (status, SECRET in text), (404, False))
    status, _, text = c.json("PUT", "/v1/files/text",
                             {"root": "vault", "path": "Swap/note.md", "text": "x",
                              "base_revision": files.revision(SECRET.encode())})
    check("a save through the swapped link: refused, outside untouched",
          (status, (t["outside"] / "note.md").read_text()), (404, SECRET))
    os.unlink(victim)

    # The walk itself, under a racing swapper: never once outside.
    stop = threading.Event()
    (t["vault"] / "Race").mkdir()
    (t["vault"] / "Race" / "note.md").write_text("inside\n")

    def swapper() -> None:
        real, spare = t["vault"] / "Race", t["vault"] / ".race-real"
        while not stop.is_set():
            try:
                os.rename(real, spare)
                os.symlink(t["outside"], real)
                os.unlink(real)
                os.rename(spare, real)
            except OSError:
                pass

    (t["outside"] / "note.md").write_text(SECRET)
    thread = threading.Thread(target=swapper)
    thread.start()
    leaked = 0
    answered = set()
    for _ in range(300):
        status, _, payload = c.raw("GET", "/v1/files/read?root=vault&path=Race/note.md")
        answered.add(status)
        leaked += SECRET.encode() in payload
    stop.set()
    thread.join()
    check("300 reads against a swapping folder: nothing from outside", leaked, 0)
    check("...and every answer was the file or a 404", answered <= {200, 404}, True)


def test_writes(c: Client, t: dict) -> None:
    path = "40_PROJECTS/Loam/state.md"
    _, body, _ = c.get("read", root="vault", path=path)
    rev = body["revision"]
    new = body["text"] + "\nA line from the owner.\n"
    status, saved, _ = c.json("PUT", "/v1/files/text",
                              {"root": "vault", "path": path, "text": new,
                               "base_revision": rev})
    check("save: 200", status, 200)
    on_disk = (t["vault"] / path).read_text()
    check("save: the owner is the last to modify", cfg.lc.OWNER_LINK in
          on_disk.split("---")[1], True)
    check("save: created_by kept", 'created_by: "[[Ada]]"' in on_disk, True)
    check("save: the answer's text is the disk's", saved["text"], on_disk)
    check("save: the answer's revision is the disk's", saved["revision"],
          files.revision(on_disk.encode()))
    check("save: no temp file left", [p.name for p in (t["vault"] / "40_PROJECTS" / "Loam")
                                      .iterdir() if p.name.startswith(".")], [])
    status, body, _ = c.json("PUT", "/v1/files/text",
                             {"root": "vault", "path": path, "text": "clobber",
                              "base_revision": rev})
    check("a stale revision: 409 with the current one",
          (status, body.get("current_revision")), (409, saved["revision"]))
    check("...and the file untouched", (t["vault"] / path).read_text(), on_disk)
    status, body, _ = c.json("PUT", "/v1/files/text",
                             {"root": "vault", "path": path, "text": "x"})
    check("a save with no base_revision: 400", status, 400)

    # Read-only by authority
    for root, p, why in (("vault", "15_CONVERSATIONS/2026-10-02 chat.md", "transcripts"),
                         ("spaces", "alpha/_active.md", "persona"),
                         ("spaces", "alpha/CLAUDE.md", "runtime"),
                         ("spaces", "commons/alpha/note.md", "persona"),
                         ("system", "Engineer/Role.md", "runtime")):
        _, body, _ = c.get("read", root=root, path=p)
        before = body["text"]
        status, out, _ = c.json("PUT", "/v1/files/text",
                                {"root": root, "path": p, "text": "overwritten",
                                 "base_revision": body["revision"]})
        check(f"save {root}/{p}: 403, {why}", (status, out.get("authority")), (403, why))
        _, again, _ = c.get("read", root=root, path=p)
        check(f"...{p} unchanged", again["text"], before)
    status, out, _ = c.json("PUT", "/v1/files/text",
                            {"root": "vault", "path": "image.png", "text": "x",
                             "base_revision": "sha256:00"})
    check("save a binary file: refused", status in (403, 409), True)

    # soul.md: the owner's, and no provenance in a workspace
    _, body, _ = c.get("read", root="spaces", path="alpha/soul.md")
    status, out, _ = c.json("PUT", "/v1/files/text",
                            {"root": "spaces", "path": "alpha/soul.md",
                             "text": "# alpha's soul, revised\n",
                             "base_revision": body["revision"]})
    check("soul.md: the owner saves it, verbatim",
          (status, (t["labs"] / "alpha" / "soul.md").read_text(), out["provenance"]),
          (200, "# alpha's soul, revised\n", False))

    # Create
    status, out, _ = c.json("POST", "/v1/files/file",
                            {"root": "vault", "path": "00_INBOX/new.md",
                             "text": "# New\n"})
    check("create in a folder that does not exist: 404", status, 404)
    status, out, _ = c.json("POST", "/v1/files/folder",
                            {"root": "vault", "path": "00_INBOX"})
    check("create a folder: 201", (status, out["kind"]), (201, "folder"))
    status, out, _ = c.json("POST", "/v1/files/folder",
                            {"root": "vault", "path": "00_INBOX"})
    check("the same folder again: 409", status, 409)
    status, out, _ = c.json("POST", "/v1/files/file",
                            {"root": "vault", "path": "00_INBOX/new.md",
                             "text": "# New\n"})
    check("create a note: 201", status, 201)
    check("create: both provenance fields are the owner's",
          (t["vault"] / "00_INBOX" / "new.md").read_text(),
          f'---\ncreated_by: {cfg.lc.OWNER_LINK}\nlast_modified_by: '
          f'{cfg.lc.OWNER_LINK}\n---\n\n# New\n')
    status, out, _ = c.json("POST", "/v1/files/file",
                            {"root": "vault", "path": "00_INBOX/new.md",
                             "text": "clobber"})
    check("create over an existing file: 409, untouched",
          (status, "clobber" in (t["vault"] / "00_INBOX" / "new.md").read_text()),
          (409, False))
    os.symlink(t["outside"] / "made.md", t["vault"] / "00_INBOX" / "dangling.md")
    status, out, _ = c.json("POST", "/v1/files/file",
                            {"root": "vault", "path": "00_INBOX/dangling.md",
                             "text": "through a link"})
    check("create over a dangling link: 409, nothing made outside",
          (status, (t["outside"] / "made.md").exists()), (409, False))
    for name in (".env", "x.env", ".hidden.md", "key.pem", "run.sh", "a.png"):
        status, out, _ = c.json("POST", "/v1/files/file",
                                {"root": "vault", "path": f"00_INBOX/{name}",
                                 "text": "x"})
        check(f"create {name}: refused", status in (400, 415), True)
    for root, folder in (("vault", "15_CONVERSATIONS"), ("spaces", "alpha"),
                         ("spaces", "commons"), ("system", "Engineer")):
        status, out, _ = c.json("POST", "/v1/files/file",
                                {"root": root, "path": f"{folder}/x.md", "text": "x"})
        check(f"create in {root}/{folder}: 403", status, 403)
    status, out, _ = c.json("POST", "/v1/files/file",
                            {"root": "vault", "path": "00_INBOX/../../outside/x.md",
                             "text": "x"})
    check("create with traversal: 400", status, 400)
    big = "x" * (files.TEXT_WRITE_MAX + 1)
    status, out, _ = c.json("POST", "/v1/files/file",
                            {"root": "vault", "path": "00_INBOX/big.md", "text": big})
    check("create over the write cap: 413", status, 413)


def test_resolve(c: Client) -> None:
    def res(ref, **kw):
        return c.get("resolve", ref=ref, **kw)[1]
    got = res("/files/vault/40_PROJECTS/Loam/Plan.md#Goals")
    check("app link: found, fragment kept",
          (got["status"], got["resource"]["path"], got["fragment"]),
          ("found", "40_PROJECTS/Loam/Plan.md", "Goals"))
    got = res("/files/vault/15_CONVERSATIONS/2026-10-02%20chat.md")
    check("app link with an encoded space", got["status"], "found")
    got = res(str(cfg.VAULT / "40_PROJECTS" / "Loam" / "state.md"))
    check("absolute vault path", (got["status"], got["resource"]["root"]),
          ("found", "vault"))
    got = res(str(cfg.AI_LABS / "alpha" / "soul.md"))
    check("absolute workspace path", (got["status"], got["resource"]["root"],
                                      got["resource"]["path"]),
          ("found", "spaces", "alpha/soul.md"))
    got = res("/etc/passwd")
    check("absolute path outside: missing", got["status"], "missing")
    got = res(str(cfg.VAULT / "deploy.env"))
    check("absolute path to a secret: missing", got["status"], "missing")
    got = res("obsidian://open?vault=vault&file=40_PROJECTS%2FLoam%2Fstate")
    check("obsidian link", (got["status"], got["resource"]["path"]),
          ("found", "40_PROJECTS/Loam/state.md"))
    got = res("[[Unique note]]")
    check("wiki-link by unique name", (got["status"], got["resource"]["path"]),
          ("found", "20_ATLAS/Unique note.md"))
    got = res("[[Plan]]")
    check("wiki-link ambiguous: a choice, never a guess",
          (got["status"], sorted(x["path"] for x in got["candidates"])),
          ("ambiguous", ["20_ATLAS/Plan.md", "40_PROJECTS/Loam/Plan.md"]))
    got = res("[[Plan|the plan]]", from_root="vault",
              from_path="40_PROJECTS/Loam/state.md")
    check("wiki-link from a note: its own folder first",
          (got["status"], got["resource"]["path"]),
          ("found", "40_PROJECTS/Loam/Plan.md"))
    got = res("[[20_ATLAS/Plan#Top]]")
    check("wiki-link by full path, with heading",
          (got["status"], got["resource"]["path"], got["fragment"]),
          ("found", "20_ATLAS/Plan.md", "Top"))
    got = res("../../20_ATLAS/Plan.md", from_root="vault",
              from_path="40_PROJECTS/Loam/state.md")
    check("relative markdown link", (got["status"], got["resource"]["path"]),
          ("found", "20_ATLAS/Plan.md"))
    got = res("../../../../etc/passwd", from_root="vault",
              from_path="40_PROJECTS/Loam/state.md")
    check("relative link climbing out: missing", got["status"], "missing")
    got = res("https://example.com/a")
    check("web link stays a web link", got["status"], "external")
    got = res("[[Nothing like it]]")
    check("unknown wiki-link: missing", got["status"], "missing")


def test_resolve_paths(c: Client) -> None:
    """Obsidian's partial and relative paths, as the vault writes them."""
    def res(ref, **kw):
        status, body, text = c.get("resolve", ref=ref, **kw)
        no_secret(f"resolve {ref}", text)
        return body
    ui = {"from_root": "vault", "from_path": "40_PROJECTS/loam-ui/loam-ui.md"}
    got = res("[[Loam/state]]")
    check("partial path: [[Loam/state]] is 40_PROJECTS/Loam/state.md, once "
          "(not the hidden copy, not through either link)",
          (got["status"], got.get("resource", {}).get("path")),
          ("found", "40_PROJECTS/Loam/state.md"))
    got = res("[[loam-ui/research/deep]]")
    check("partial path, three names deep",
          (got["status"], got["resource"]["path"]),
          ("found", "40_PROJECTS/loam-ui/research/deep.md"))
    got = res("[[loam/STATE]]")
    check("partial path: case-insensitive, as Obsidian is",
          (got["status"], got["resource"]["path"]),
          ("found", "40_PROJECTS/Loam/state.md"))
    got = res("[[oam/state]]")
    check("partial path matches whole names only", got["status"], "missing")
    got = res("[[Loam/notes]]")
    check("partial path matching two files: candidates, never a guess",
          (got["status"], sorted(x["path"] for x in got["candidates"]),
           got["candidates_total"], got["truncated"]),
          ("ambiguous", ["40_PROJECTS/Loam/notes.md", "Zarchive/Loam/notes.md"],
           2, False))
    got = res("[[Loam/notes]]", from_root="vault",
              from_path="40_PROJECTS/Loam/state.md")
    check("...from a note beside one of them too: still a choice",
          (got["status"], got["candidates_total"]), ("ambiguous", 2))
    got = res("[[notes]]", from_root="vault",
              from_path="40_PROJECTS/Loam/state.md")
    check("a short name the linking note's own folder has: that one",
          (got["status"], got["resource"]["path"]),
          ("found", "40_PROJECTS/Loam/notes.md"))
    got = res("[[../Loam/state]]", **ui)
    check("relative wiki-link climbs to a sibling project",
          (got["status"], got["resource"]["path"]),
          ("found", "40_PROJECTS/Loam/state.md"))
    got = res("[[../Loam/state#Goals|the state]]", **ui)
    check("relative, with heading and alias",
          (got["status"], got["resource"]["path"], got["fragment"]),
          ("found", "40_PROJECTS/Loam/state.md", "Goals"))
    got = res("[[./research/deep]]", **ui)
    check("relative ./ from the note's folder",
          (got["status"], got["resource"]["path"]),
          ("found", "40_PROJECTS/loam-ui/research/deep.md"))
    got = res("[[research/deep]]", **ui)
    check("plain path from the note's folder first",
          (got["status"], got["resource"]["path"]),
          ("found", "40_PROJECTS/loam-ui/research/deep.md"))
    got = res("../Loam/state.md", **ui)
    check("relative Markdown link to a sibling project",
          (got["status"], got["resource"]["path"]),
          ("found", "40_PROJECTS/Loam/state.md"))
    got = res("[[../../20_ATLAS/Unique note]]", **ui)
    check("relative climbing two, to the vault's top and down",
          (got["status"], got["resource"]["path"]),
          ("found", "20_ATLAS/Unique note.md"))
    got = res("[[../research/deep]]", **ui)
    check("relative to a file not there: missing, no fallback search",
          (got["status"], got.get("path")), ("missing", "40_PROJECTS/research/deep"))
    got = res("[[../Loam/state]]")
    check("relative climb with no linking note: missing", got["status"], "missing")
    for ref in ("[[../../../etc/passwd]]", "[[../../../outside/passwd]]",
                "[[Loam/../../../../outside/passwd]]", "../../../outside/passwd",
                "..%2F..%2F..%2Foutside%2Fpasswd", "[[../../../../../../../../x]]"):
        got = res(ref, **ui)
        check(f"climbing out of the vault: missing ({ref})",
              (got["status"], "resource" in got), ("missing", False))
    for ref in ("[[../../deploy.env]]", "[[../../server.pem]]", "[[Loam/creds.env]]",
                "[[../../.obsidian/app.json]]", "[[.trash/40_PROJECTS/Loam/state]]",
                "[[../../link-to-passwd]]", "[[../../link-dir/passwd]]",
                "[[link-dir/Loam/state]]", "[[inner-link/Loam/state]]",
                "[[..\\..\\deploy.env]]", "[[x\x00/state]]"):
        got = res(ref, **ui)
        check(f"a secret, hidden or linked path: missing ({ref})",
              (got["status"], "resource" in got), ("missing", False))
    sp = {"from_root": "spaces", "from_path": "alpha/memory/MEMORY.md"}
    got = res("[[../soul]]", **sp)
    check("relative in the spaces: inside its own root",
          (got["status"], got["resource"]["root"], got["resource"]["path"]),
          ("found", "spaces", "alpha/soul.md"))
    for ref in ("[[../../beta/wake.env]]", "[[../logs/wake.log]]",
                "[[../../../vault/40_PROJECTS/Loam/state]]"):
        got = res(ref, **sp)
        check(f"spaces: private or out of the root: missing ({ref})",
              (got["status"], "resource" in got), ("missing", False))
    got = res("[[../Engineer/Role]]", from_root="system",
              from_path="Engineer/Platform project.md")
    check("relative from a System document: missing, never into the vault",
          got["status"], "missing")
    got = res("[[#Top]]", from_root="vault", from_path="40_PROJECTS/Loam/state.md")
    check("[[#heading]]: the linking note itself",
          (got["status"], got["resource"]["path"], got["fragment"]),
          ("found", "40_PROJECTS/Loam/state.md", "Top"))
    got = res("![[Loam/state]]")
    check("an embed resolves like a link", got["status"], "found")
    got = res("[[Loam/state\\|alias in a table]]")
    check("an alias pipe escaped in a table", got["status"], "found")

    # Bounds: a walk that stops says so, and never answers found on a part.
    keep = (files.RESOLVE_SCAN_MAX, files.RESOLVE_CANDIDATES_MAX)
    try:
        files.RESOLVE_CANDIDATES_MAX = 1
        got = res("[[Loam/notes]]")
        check("candidates capped, the total still said",
              (got["status"], len(got["candidates"]), got["candidates_total"]),
              ("ambiguous", 1, 2))
        files.RESOLVE_SCAN_MAX = 3
        got = res("[[Unique note]]")
        check("a lookup over its bound: not found, truncated",
              (got["status"] != "found", got.get("truncated")), (True, True))
    finally:
        files.RESOLVE_SCAN_MAX, files.RESOLVE_CANDIDATES_MAX = keep


def test_resolve_folders(c: Client) -> None:
    """A link to a folder answers the folder's row, wherever no note is."""
    def res(ref, **kw):
        status, body, text = c.get("resolve", ref=ref, **kw)
        no_secret(f"resolve {ref}", text)
        check(f"resolve {ref}: 200", status, 200)
        return body

    def folder(got):
        r = got.get("resource") or {}
        return (got["status"], r.get("kind"), r.get("root"), r.get("path"))

    ui = {"from_root": "vault", "from_path": "40_PROJECTS/loam-ui/loam-ui.md"}
    want = ("found", "folder", "vault", "40_PROJECTS/Loam")
    for ref in ("/files/vault/40_PROJECTS/Loam", "/files/vault/40_PROJECTS/Loam/",
                str(cfg.VAULT / "40_PROJECTS" / "Loam"),
                str(cfg.VAULT / "40_PROJECTS" / "Loam") + "/",
                "[[40_PROJECTS/Loam]]", "[[40_PROJECTS/Loam/]]",
                "[[40_PROJECTS/Loam/|Projects]]", "[[40_projects/loam]]",
                "obsidian://open?vault=vault&file=40_PROJECTS%2FLoam",
                "40_PROJECTS/Loam/"):
        check(f"a folder by full path: {ref}", folder(res(ref)), want)
    got = res("[[40_PROJECTS/Loam|Projects]]")
    check("the folder's row: its link, its capabilities",
          (got["resource"]["link"], got["resource"]["name"],
           got["resource"]["size"], got["resource"]["capabilities"],
           got["fragment"]),
          ("/files/vault/40_PROJECTS/Loam", "Loam", None,
           {"read": True, "download": False, "write": False, "create": True,
            "rename": False},
           None))
    got = res(str(cfg.VAULT))
    check("the vault's own folder by host path",
          folder(got), ("found", "folder", "vault", ""))

    # Bare names and partial paths, by Obsidian's ends-with rule.
    want = ("found", "folder", "vault", "40_PROJECTS/loam-ui/research")
    for ref in ("[[research]]", "[[loam-ui/research]]", "[[LOAM-UI/Research/]]",
                "[[research|the research]]"):
        check(f"a unique folder by name or partial path: {ref}",
              folder(res(ref)), want)
    got = res("[[Loam/]]")
    check("two folders of one name: a choice, never a guess (not .trash, "
          "not through a link)",
          (got["status"], [(x["kind"], x["path"]) for x in got["candidates"]],
           got["candidates_total"], got["truncated"]),
          ("ambiguous", [("folder", "40_PROJECTS/Loam"),
                         ("folder", "Zarchive/Loam")], 2, False))
    got = res("[[Loam/]]", **ui)
    check("...from a note whose folder has none: still a choice",
          got["status"], "ambiguous")

    # Encoded spaces: a folder named with one, every way a link writes it.
    want = ("found", "folder", "vault", "40_PROJECTS/Half Year")
    for ref, kw in (("[[Half Year]]", {}), ("[[40_PROJECTS/Half Year/]]", {}),
                    ("/files/vault/40_PROJECTS/Half%20Year", {}),
                    (str(cfg.VAULT / "40_PROJECTS" / "Half Year"), {}),
                    ("obsidian://open?vault=v&file=40_PROJECTS%2FHalf%20Year", {}),
                    ("../Half%20Year/", ui), ("[[../Half Year]]", ui)):
        got = res(ref, **kw)
        check(f"a folder with a space: {ref}", folder(got), want)
    check("...its link encodes the space once",
          got["resource"]["link"], "/files/vault/40_PROJECTS/Half%20Year")

    # A note and a folder of one name: the note, always; `/` asks for the folder.
    for ref in ("[[Topics]]", "[[20_ATLAS/Topics]]", "[[Topics|alias]]",
                "obsidian://open?vault=v&file=20_ATLAS%2FTopics"):
        got = res(ref)
        check(f"note and folder of one name: the note ({ref})",
              folder(got), ("found", "file", "vault", "20_ATLAS/Topics.md"))
    for ref in ("[[Topics/]]", "[[20_ATLAS/Topics/]]", "/files/vault/20_ATLAS/Topics"):
        check(f"...the folder when the link says folder ({ref})",
              folder(res(ref)), ("found", "folder", "vault", "20_ATLAS/Topics"))
    got = res("[[Many]]")
    check("a note anywhere outranks a folder named alike at the top",
          folder(got), ("found", "file", "vault", "Zarchive/Many.md"))
    got = res("[[Many/]]")
    check("...and `/` finds the folder", folder(got),
          ("found", "folder", "vault", "Many"))
    got = res("[[Loam]]")
    check("a folder note (40_PROJECTS/Loam/Loam.md) outranks its two folders",
          folder(got), ("found", "file", "vault", "40_PROJECTS/Loam/Loam.md"))
    got = res("[[research/]]")
    check("`/` with one folder of that name", folder(got),
          ("found", "folder", "vault", "40_PROJECTS/loam-ui/research"))
    got = res("[[Loam/state]]")
    check("a note under a folder name is still the note",
          folder(got), ("found", "file", "vault", "40_PROJECTS/Loam/state.md"))

    # Relative: from the linking note, inside its own root.
    for ref, path in (("[[./research]]", "40_PROJECTS/loam-ui/research"),
                      ("[[../Loam]]", "40_PROJECTS/Loam"),
                      ("[[..]]", "40_PROJECTS"), ("../", "40_PROJECTS"),
                      ("[[../..]]", ""), ("./research/", "40_PROJECTS/loam-ui/research")):
        check(f"a relative folder link: {ref}", folder(res(ref, **ui)),
              ("found", "folder", "vault", path))
    for ref in ("[[../../..]]", "../../../", "[[../../../outside]]",
                "..%2F..%2F..%2Foutside", "[[Loam/../../../../outside/]]",
                "[[../nothing here]]"):
        got = res(ref, **ui)
        check(f"a relative folder out of the root or absent: missing ({ref})",
              (got["status"], "resource" in got), ("missing", False))
    sp = {"from_root": "spaces", "from_path": "alpha/memory/MEMORY.md"}
    got = res("[[../memory]]", **sp)
    check("relative folder in the spaces", folder(got),
          ("found", "folder", "spaces", "alpha/memory"))
    got = res(str(cfg.AI_LABS / "alpha" / "memory"))
    check("a workspace folder by host path", folder(got),
          ("found", "folder", "spaces", "alpha/memory"))
    for ref in ("[[../logs]]", "[[../scratch/]]", "[[../../../vault/40_PROJECTS]]"):
        got = res(ref, **sp)
        check(f"spaces: a private folder or out of the root: missing ({ref})",
              (got["status"], "resource" in got), ("missing", False))

    # Read-only and System folders keep their own authority.
    got = res("[[15_CONVERSATIONS/]]")
    check("the transcripts folder: found, read-only",
          (folder(got), got["resource"]["authority"],
           got["resource"]["capabilities"]["create"]),
          (("found", "folder", "vault", "15_CONVERSATIONS"), "transcripts", False))
    got = res("/files/system/Engineer")
    check("a System folder by its app link", (folder(got),
          got["resource"]["capabilities"]["create"]),
          (("found", "folder", "system", "Engineer"), False))
    got = res("[[Engineer/]]", from_root="system", from_path="Engineer/Role.md")
    check("a wiki-link from a System document reads the vault, never System",
          folder(got), ("found", "folder", "vault", "40_PROJECTS/Loam/engineer"))

    # Hidden, secret-shaped and linked folders are absent; so is nothing.
    for ref in ("[[.obsidian]]", "[[.obsidian/]]", "[[.trash/]]", "[[.git]]",
                "[[archive.key]]", "[[archive.key/]]", "[[40_PROJECTS/archive.key]]",
                "/files/vault/40_PROJECTS/archive.key", "[[link-dir]]",
                "[[link-dir/]]", "[[inner-link]]", "[[inner-link/Loam/]]",
                "/files/vault/inner-link", str(cfg.VAULT / "link-dir"),
                str(cfg.VAULT / ".obsidian"), "[[No such folder/]]",
                "[[No such folder]]", "/files/vault/No%20such"):
        got = res(ref)
        check(f"hidden, secret, linked or absent folder: missing ({ref})",
              (got["status"], "resource" in got, "candidates" in got),
              ("missing", False, False))
    status, body, text = c.get("resolve", ref="/files/vault/../outside")
    check("app-link traversal to a folder: refused, nothing found",
          (status in (400, 404), "resource" in body), (True, False))

    # Bounds: a folder lookup over its bound is never found.
    keep = files.RESOLVE_SCAN_MAX
    try:
        files.RESOLVE_SCAN_MAX = 3
        got = res("[[research]]")
        check("a folder lookup over its bound: not found, truncated",
              (got["status"] != "found", got.get("truncated")), (True, True))
        got = res("[[40_PROJECTS/Loam]]")
        check("...and a full folder path, which a note might still outrank",
              (got["status"] != "found", got.get("truncated")), (True, True))
    finally:
        files.RESOLVE_SCAN_MAX = keep


def test_html(c: Client, t: dict) -> None:
    """HTML is stored text: read and saved as text where the owner writes,
    never executed by the broker, every other gate as before."""
    status, body, _ = c.get("read", root="vault", path="page.html")
    check("html read: text, text/html, writable in the vault",
          (status, body["text"], body["media_type"], body["capabilities"]["write"]),
          (200, "<script>alert(1)</script>", "text/html", True))
    new = "<!doctype html><title>t</title><p>edited</p>\n"
    status, saved, _ = c.json("PUT", "/v1/files/text",
                              {"root": "vault", "path": "page.html", "text": new,
                               "base_revision": body["revision"]})
    check("html save: 200, stored verbatim (no frontmatter)",
          (status, (t["vault"] / "page.html").read_text(), saved["provenance"]),
          (200, new, False))
    status, out, _ = c.json("PUT", "/v1/files/text",
                            {"root": "vault", "path": "page.html", "text": "stale",
                             "base_revision": body["revision"]})
    check("html stale save: 409, file untouched",
          (status, out.get("current_revision"), (t["vault"] / "page.html").read_text()),
          (409, saved["revision"], new))
    for name in ("made.html", "made.htm"):
        status, out, _ = c.json("POST", "/v1/files/file",
                                {"root": "vault", "path": f"00_INBOX/{name}",
                                 "text": "<p>new</p>"})
        check(f"create {name}: 201, verbatim",
              (status, (t["vault"] / "00_INBOX" / name).read_text()),
              (201, "<p>new</p>"))
    status, out, _ = c.json("POST", "/v1/files/file",
                            {"root": "vault", "path": "00_INBOX/made.html",
                             "text": "clobber"})
    check("create html over an existing one: 409, untouched",
          (status, (t["vault"] / "00_INBOX" / "made.html").read_text()),
          (409, "<p>new</p>"))
    status, out, _ = c.json("POST", "/v1/files/file",
                            {"root": "vault", "path": "00_INBOX/huge.html",
                             "text": "x" * (files.TEXT_WRITE_MAX + 1)})
    check("create html over the write cap: 413", status, 413)
    status, body, _ = c.get("read", root="vault", path="latin1.html")
    check("non-UTF-8 html: 415, download it", status, 415)
    status, body, _ = c.get("read", root="vault", path="big.html")
    check("html over the read cap: 413", status, 413)
    for root, p, why in (("vault", "15_CONVERSATIONS/shown.html", "transcripts"),
                         ("spaces", "alpha/memory/page.html", "persona")):
        _, body, _ = c.get("read", root=root, path=p)
        check(f"{root}/{p}: readable, not writable",
              (body["capabilities"]["write"], body["authority"]), (False, why))
        status, out, _ = c.json("PUT", "/v1/files/text",
                                {"root": root, "path": p, "text": "<p>x</p>",
                                 "base_revision": body["revision"]})
        check(f"save {root}/{p}: 403", (status, out.get("authority")), (403, why))
    status, out, _ = c.json("POST", "/v1/files/file",
                            {"root": "vault", "path": "15_CONVERSATIONS/new.html",
                             "text": "<p>x</p>"})
    check("create html in transcripts: 403", status, 403)
    status, headers, payload = c.raw("GET", "/v1/files/download?root=vault&path=page.html")
    check("html download after a save: still sandboxed, an attachment",
          (status, headers.get("content-security-policy"),
           headers.get("content-disposition", "").startswith("attachment"),
           headers.get("x-content-type-options")),
          (200, "sandbox; default-src 'none'", True, "nosniff"))


def test_system_pointers(c: Client) -> None:
    status, body, text = c.get("roots")
    check("roots: still the three, in order",
          [r["root"] for r in body["roots"]], ["vault", "spaces", "system"])
    rows = {s["label"]: s["resource"] for s in body["shortcuts"]}
    check("shortcuts: only the vault folders that exist",
          sorted(rows), ["Conventions", "Loam project", "Vault system"])
    check("shortcuts are vault rows, with the vault's capabilities",
          {(r["root"], r["kind"], r["capabilities"]["create"]) for r in rows.values()},
          {("vault", "folder", True)})
    _, row, _ = c.get("stat", root="system", path="Engineer/Role.md")
    check("a System copy names its vault source",
          row.get("source"), {"root": "vault",
                              "path": "40_PROJECTS/Loam/engineer/role.md",
                              "link": "/files/vault/40_PROJECTS/Loam/engineer/role.md"})
    check("...and stays read-only", row["capabilities"]["write"], False)
    _, row, _ = c.get("stat", root="system", path="Session broker.md")
    check("a System file with no vault copy has no source", "source" in row, False)


# Bytes that a text-minded parser would mangle: every byte value, CRLFs, a
# NUL, invalid UTF-8, and lines that look like a multipart boundary.
NASTY = (bytes(range(256)) + b"\r\n--\r\n------loamtest\r\n\r\n\x00\xff\xfe"
         + os.urandom(64 * 1024) + b"\r\n")
PNG = b"\x89PNG\r\n\x1a\n" + NASTY
JPEG = b"\xff\xd8\xff\xe0" + NASTY
PDF = b"%PDF-1.7\n" + NASTY + b"%%EOF\n"


def leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir()
                  if p.name.startswith(".loam-files-"))


def test_upload(c: Client, t: dict) -> None:
    vault, outside = t["vault"], t["outside"]
    up = vault / "Uploads"
    up.mkdir()
    before_outside = sorted(p.name for p in outside.rglob("*"))

    # Who may: a token, always.
    for tok in ("", "wrong"):
        status, _ = c.upload([("file", "IMG.png", PNG)], path="Uploads", token=tok)
        check(f"upload with token {tok!r}: 401", status, 401)
    check("...and nothing written", sorted(p.name for p in up.iterdir()), [])

    # Bytes in, the same bytes out.
    status, row = c.upload([("file", "IMG_2041.png", PNG)], path="Uploads")
    check("a PNG upload: 201", status, 201)
    check("...its row", {k: row.get(k) for k in (
        "root", "path", "name", "kind", "size", "media_type", "authority",
        "link", "requested_name", "renamed", "provenance")},
        {"root": "vault", "path": "Uploads/IMG_2041.png", "name": "IMG_2041.png",
         "kind": "file", "size": len(PNG), "media_type": "image/png",
         "authority": "user", "link": "/files/vault/Uploads/IMG_2041.png",
         "requested_name": "IMG_2041.png", "renamed": False, "provenance": False})
    check("...capabilities: read and download, no text write",
          row.get("capabilities"),
          {"read": True, "download": True, "write": False, "create": False,
           "rename": True})
    check("...revision is the sha256 of the bytes sent",
          row.get("revision"), files.revision(PNG))
    check("...on disk byte for byte", (up / "IMG_2041.png").read_bytes() == PNG, True)
    check("...mode 0644, never executable",
          oct((up / "IMG_2041.png").stat().st_mode & 0o777), "0o644")
    status, _, got = c.raw("GET", "/v1/files/download?root=vault&path=Uploads/IMG_2041.png")
    check("...and downloaded back byte for byte", (status, got == PNG), (200, True))
    for name, data, mtype in (("scan.pdf", PDF, "application/pdf"),
                              ("photo.JPG", JPEG, "image/jpeg"),
                              ("blob.csv", NASTY, "text/csv"),
                              ("page.html", b"<script>alert(1)</script>\xff", "text/html")):
        status, row = c.upload([("file", name, data)], path="Uploads")
        check(f"{name}: 201, {mtype}, byte-exact",
              (status, row.get("media_type"), (up / name).read_bytes() == data),
              (201, mtype, True))
    status, headers, _ = c.raw("GET", "/v1/files/download?root=vault&path=Uploads/page.html")
    check("an uploaded HTML file downloads as a sandboxed attachment",
          (headers.get("content-disposition", "").startswith("attachment"),
           "sandbox" in headers.get("content-security-policy", "")), (True, True))

    # Never an overwrite: the design's "name 2.ext".
    other = JPEG + b"second"
    status, row = c.upload([("file", "IMG_2041.png", PNG[:8] + b"second")], path="Uploads")
    check("the same name again: 201 as 'IMG_2041 2.png'",
          (status, row.get("name"), row.get("renamed"), row.get("requested_name")),
          (201, "IMG_2041 2.png", True, "IMG_2041.png"))
    check("...the first untouched", (up / "IMG_2041.png").read_bytes() == PNG, True)
    status, row = c.upload([("file", "IMG_2041.png", PNG)], path="Uploads")
    check("and a third: 'IMG_2041 3.png'", (status, row.get("name")), (201, "IMG_2041 3.png"))
    (up / "taken.jpg").mkdir()
    os.symlink(outside / "passwd", up / "dangling.jpg")
    os.symlink(outside / "nowhere", up / "dangling2.jpg")
    for name in ("taken.jpg", "dangling.jpg", "dangling2.jpg"):
        status, row = c.upload([("file", name, other)], path="Uploads")
        check(f"a name held by a folder or link ({name}): numbered, not followed",
              (status, row.get("name")), (201, name.replace(".jpg", " 2.jpg")))
    check("...the link's target untouched", (outside / "passwd").read_text().endswith(SECRET + "\n"), True)
    check("...and no file at the dangling link's target", (outside / "nowhere").exists(), False)
    status, row = c.upload([("file", "Makefile.txt", b"a")], path="Uploads")
    status, row = c.upload([("file", "Makefile.txt", b"b")], path="Uploads")
    check("numbering keeps the extension", row.get("name"), "Makefile 2.txt")

    # The race: eight at once, one name, eight files.
    out: list = []
    bodies = [JPEG + bytes([i]) * 1000 for i in range(8)]

    def send(i: int) -> None:
        out.append(c.upload([("file", "race.jpg", bodies[i])], path="Uploads"))

    threads = [threading.Thread(target=send, args=(i,)) for i in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    names = sorted(r.get("name") for s, r in out if s == 201)
    check("8 concurrent uploads of race.jpg: 8 distinct names", len(set(names)), 8)
    stored = sorted((up / n).read_bytes() for n in names)
    check("...and each holds exactly one of the bodies sent", stored == sorted(bodies), True)

    # Markdown: the owner's provenance; everything else verbatim.
    status, row = c.upload([("file", "Notes from phone.md", "\ufeff# Hi\n\ncafé\n".encode())],
                           path="Uploads")
    disk = (up / "Notes from phone.md").read_text()
    check("a Markdown upload gains created_by and last_modified_by",
          (status, row.get("provenance"), disk),
          (201, True, f'---\ncreated_by: {cfg.lc.OWNER_LINK}\nlast_modified_by: '
                      f'{cfg.lc.OWNER_LINK}\n---\n\n# Hi\n\ncafé\n'))
    check("...its revision is of what is on disk",
          row.get("revision"), files.revision(disk.encode()))
    status, row = c.upload([("file", "theirs.md", b"---\ncreated_by: \"[[Ada]]\"\n---\nx\n")],
                           path="Uploads")
    check("...a created_by already in the file is kept",
          (up / "theirs.md").read_text().startswith('---\ncreated_by: "[[Ada]]"\nlast_modified_by'), True)
    status, row = c.upload([("file", "latin.md", b"caf\xe9\n")], path="Uploads")
    check("a Markdown upload that is not UTF-8: 415, nothing written",
          (status, (up / "latin.md").exists()), (415, False))
    status, row = c.upload([("file", "latin.txt", b"caf\xe9\n")], path="Uploads")
    check("a .txt that is not UTF-8: stored verbatim",
          (status, (up / "latin.txt").read_bytes()), (201, b"caf\xe9\n"))

    # Names: the last part of whatever the browser sent, refused if unsafe.
    status, row = c.upload([("file", "../../outside/escape.png", PNG)], path="Uploads")
    check("a filename with ../: only its last name, in the folder asked",
          (status, row.get("path"), (outside / "escape.png").exists()),
          (201, "Uploads/escape.png", False))
    status, row = c.upload([("file", "C:\\Users\\m\\win.png", PNG)], path="Uploads")
    check("a Windows path: its last name", (status, row.get("name")), (201, "win.png"))
    status, row = c.upload([("file", "Cafe\u0301 menu.pdf", PDF)], path="Uploads")
    check("a decomposed name is stored NFC", (status, row.get("name")), (201, "Caf\u00e9 menu.pdf"))
    for name, want in ((".hidden.png", 400), ("server.pem", 400), ("deploy.env", 400),
                       ("id_rsa", 400), ("..", 400), ("", 400), ("ctl\x01.png", 400),
                       ("a" * 237 + ".png", 400), ("noext", 415), ("tool.exe", 415),
                       ("x.svg", 415), ("fake.png", 415), ("fake.pdf", 415),
                       ("fake.docx", 415)):
        data = b"<html><script>alert(1)</script></html>"
        status, body = c.upload([("file", name, data)], path="Uploads")
        check(f"filename {name[:30]!r}: {want}", status, want)
    check("...none of them written",
          [n for n in (".hidden.png", "server.pem", "deploy.env", "noext", "tool.exe",
                       "x.svg", "fake.png", "fake.pdf", "fake.docx")
           if (up / n).exists()], [])

    # Where: a vault folder the owner may create in, and nowhere else.
    for root, path, want in (("vault", "15_CONVERSATIONS", 403),
                             ("spaces", "alpha", 403), ("spaces", "", 403),
                             ("spaces", "alpha/memory", 403), ("system", "", 403),
                             ("system", "Engineer", 403), ("vault", "Nope", 404),
                             ("vault", "../outside", 400), ("vault", "link-dir", 404),
                             ("vault", "inner-link", 404), ("vault", ".obsidian", 404),
                             ("vault", "image.png", 404), ("elsewhere", "", 404),
                             ("vault", "/etc", 404), ("vault", "%2e%2e", 201)):
        status, body = c.upload([("file", "where.png", PNG)], root=root, path=path)
        check(f"upload into {root}/{path}: {want}", status, want)
    check("...nothing in the transcripts, spaces or outside",
          ((vault / "15_CONVERSATIONS" / "where.png").exists(),
           (t["labs"] / "alpha" / "where.png").exists(),
           sorted(p.name for p in outside.rglob("*")) == before_outside),
          (False, False, True))
    status, row = c.upload([("file", "top.png", PNG)], path="")
    check("the vault's top folder: 201", (status, row.get("path")), (201, "top.png"))

    # Size: the attachment cap, exactly.
    cap = files.UPLOAD_MAX
    status, row = c.upload([("file", "cap.pdf", b"%PDF-" + b"\x00" * (cap - 5))], path="Uploads")
    check(f"a file of exactly {cap} bytes: 201", (status, row.get("size")), (201, cap))
    status, body = c.upload([("file", "over.pdf", b"%PDF-" + b"\x00" * (cap - 4))], path="Uploads")
    check("one byte over, inside the envelope's slack: 413 with size and max",
          (status, body.get("max"), body.get("size")), (413, cap, cap + 1))
    status, body = c.upload([("file", "way.pdf", b"%PDF-" + b"\x00" * (cap + 20_000))],
                            path="Uploads")
    check("past the body cap: 413 before the body is kept", status, 413)
    check("...neither written", ((up / "over.pdf").exists(), (up / "way.pdf").exists()),
          (False, False))

    # The envelope.
    status, body = c.upload([("file", "a.png", PNG), ("file", "b.png", PNG)], path="Uploads")
    check("two file parts: 400, neither written",
          (status, (up / "a.png").exists(), (up / "b.png").exists()), (400, False, False))
    status, body = c.upload([("other", "a.png", PNG)], path="Uploads")
    check("a part not named `file`: 400", status, 400)
    status, body = c.upload([], path="Uploads", content_type="application/json",
                            raw_body=b'{"file": "x"}')
    check("a JSON body: 415", status, 415)
    status, body = c.upload([], path="Uploads", content_type="multipart/form-data",
                            raw_body=b"x")
    check("multipart with no boundary: 400", status, 400)
    status, _, _ = c.raw("PUT", "/v1/files/upload?root=vault&path=Uploads")
    check("PUT upload: 405 (no replace route)", status, 405)
    # Rename is a separate route.
    for method, route in (("DELETE", "file"), ("POST", "move"),
                          ("DELETE", "upload")):
        status, _, _ = c.raw(method, f"/v1/files/{route}?root=vault&path=Uploads/top.png")
        check(f"{method} {route}: 405, nothing deletes", status, 405)

    # The parent swapped for a link while uploads run: never once outside.
    (vault / "Race2").mkdir()
    stop = threading.Event()

    def swapper() -> None:
        real, spare = vault / "Race2", vault / ".race2-real"
        while not stop.is_set():
            try:
                os.rename(real, spare)
                os.symlink(outside, real)
                os.unlink(real)
                os.rename(spare, real)
            except OSError:
                pass

    thread = threading.Thread(target=swapper)
    thread.start()
    answered = set()
    for i in range(150):
        status, _ = c.upload([("file", f"r{i}.png", PNG)], path="Race2")
        answered.add(status)
    stop.set()
    thread.join()
    check("150 uploads into a folder being swapped for a link: none outside",
          sorted(p.name for p in outside.rglob("*")) == before_outside, True)
    check("...every answer a 201 or a 404", answered <= {201, 404}, True)
    check("no temp file left in any folder written",
          leftovers(up) + leftovers(vault) + leftovers(vault / "Race2"), [])
    status, body, _ = c.json("GET", "/v1/limits")
    check("GET /v1/limits states the Files upload cap and types",
          (body.get("files_upload_max_bytes"), "pdf" in body.get("files_upload_extensions", [])),
          (cap, True))


async def serve(server, ready: dict) -> None:
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    ready["port"] = srv.sockets[0].getsockname()[1]
    ready["loop"] = asyncio.get_running_loop()
    ready["event"].set()
    async with srv:
        await ready["stop"]


def test_rename(c: Client, t: dict) -> None:
    """Rename a vault file or folder to a free name within its parent."""
    v = t["vault"]
    (v / "Renames" / "sub").mkdir(parents=True)
    note = v / "Renames" / "draft.md"
    note.write_text("---\ncreated_by: \"[[Ada]]\"\n---\n\n# Draft\n")
    os.utime(note, (1_700_000_000, 1_700_000_000))
    before = note.read_bytes()
    (v / "Renames" / "taken.md").write_text("# taken\n")
    (v / "Renames" / "sub" / "inner.md").write_text("# inner\n")
    os.symlink(t["outside"] / "passwd", v / "Renames" / "link.md")
    os.symlink(t["outside"] / "gone", v / "Renames" / "dangling.md")
    outside_before = sorted(p.name for p in t["outside"].iterdir())

    def post(body, token=None):
        return c.json("POST", "/v1/files/rename", body, token=token)

    status, _, _ = post({"root": "vault", "path": "Renames/draft.md",
                         "name": "x.md"}, token="")
    check("rename: no token, 401", status, 401)
    check("...nothing moved", note.exists(), True)

    status, row, _ = post({"root": "vault", "path": "Renames/draft.md",
                           "name": "Final draft.md"})
    moved = v / "Renames" / "Final draft.md"
    check("rename in place: 200, the new row",
          (status, row.get("path"), row.get("name"), row.get("kind"),
           row.get("link")),
          (200, "Renames/Final draft.md", "Final draft.md", "file",
           "/files/vault/Renames/Final%20draft.md"))
    check("...`from` names where it was", row.get("from"),
          {"root": "vault", "path": "Renames/draft.md",
           "link": "/files/vault/Renames/draft.md"})
    check("...the old name is gone, the new one there",
          (note.exists(), moved.exists()), (False, True))
    check("...the bytes untouched: no provenance stamped",
          moved.read_bytes(), before)
    check("...the modified time is the file's own",
          int(moved.stat().st_mtime), 1_700_000_000)
    check("...the row's capabilities say it may be renamed again",
          row.get("capabilities", {}).get("rename"), True)

    status, row, _ = post({"root": "vault", "path": "Renames/Final draft.md",
                           "name": "taken.md"})
    check("onto an existing file: 409", (status, "already exists" in
                                         row.get("error", "")), (409, True))
    check("...neither touched", ((v / "Renames" / "taken.md").read_text(),
                                 moved.read_bytes()), ("# taken\n", before))
    status, _, _ = post({"root": "vault", "path": "Renames/Final draft.md",
                         "name": "sub"})
    check("onto an existing folder: 409", status, 409)
    status, _, _ = post({"root": "vault", "path": "Renames/Final draft.md",
                         "name": "dangling.md"})
    check("onto a dangling link: 409, and the link's target never made",
          (status, (t["outside"] / "gone").exists()), (409, False))

    for name, why in (("", "empty"), ("   ", "blank"), ("a/b.md", "a slash"),
                      ("a\\b.md", "a backslash"), ("..", "dot-dot"),
                      (".", "dot"), ("bad\x01.md", "a control character"),
                      ("x" * 256, "too long"), (".hidden.md", "hidden"),
                      ("deploy.env", "secret-shaped"), (7, "not a string")):
        status, row, _ = post({"root": "vault", "path": "Renames/Final draft.md",
                               "name": name})
        check(f"a bad name ({why}): 400 with a sentence",
              (status, isinstance(row.get("error"), str)), (400, True))
    check("...the file still where it was", moved.read_bytes(), before)
    status, _, _ = post({"root": "vault", "path": "Renames/Final draft.md",
                         "name": "Final draft.md"})
    check("the same name: 400", status, 400)
    status, _, _ = post({"root": "vault", "path": "Renames/Final draft.md"})
    check("neither `name` nor `to`: 400", status, 400)
    status, _, _ = post({"root": "vault", "path": "Renames/Final draft.md",
                         "name": "a.md", "to": "b.md"})
    check("both `name` and `to`: 400", status, 400)
    status, row, _ = post({"root": "vault", "path": "Renames/  Spaced.md ",
                           "name": "x.md"})
    check("a source that is not there: 404", status, 404)
    status, row, _ = post({"root": "vault", "path": "Renames/Final draft.md",
                           "name": "  Trimmed.md  "})
    check("a name is trimmed as an upload's is", (status, row.get("name")),
          (200, "Trimmed.md"))
    moved = v / "Renames" / "Trimmed.md"

    # Links: absent to Files, so never renamed, and nothing outside touched.
    for p in ("Renames/link.md", "link-to-passwd.md", "link-dir",
              "inner-link/Loam/state.md"):
        status, row, text = post({"root": "vault", "path": p, "name": "moved.md"})
        check(f"a link ({p}): 404", status, 404)
        no_secret(f"rename of {p}", text)
    check("...the links still where they were",
          (os.path.islink(v / "Renames" / "link.md"),
           os.path.islink(v / "link-to-passwd.md"),
           (t["outside"] / "passwd").exists()), (True, True, True))

    # Outside the roots, by every spelling.
    for p in ("../outside/passwd", "Renames/../../outside/passwd", "/etc/passwd"):
        status, _, text = post({"root": "vault", "path": p, "name": "x.md"})
        check(f"outside ({p!r}): 400 or 404", status in (400, 404), True)
        no_secret(f"rename of {p!r}", text)
    for to in ("../outside/x.md", "Renames/../../x.md"):
        status, _, _ = post({"root": "vault", "path": "Renames/Trimmed.md",
                             "to": to})
        check(f"a `to` outside ({to!r}): 400", status, 400)
    check("...nothing written outside", sorted(p.name for p in t["outside"]
                                               .iterdir()), outside_before)
    status, _, _ = post({"root": "nowhere", "path": "x.md", "name": "y.md"})
    check("an unknown root: 404", status, 404)
    status, _, text = post({"root": "vault", "path": "deploy.env", "name": "x.md"})
    check("a secret-shaped source: 404, as everywhere",
          status, 404)
    no_secret("rename of a secret", text)
    status, _, _ = post({"root": "vault", "path": ".obsidian", "name": "x"})
    check("a hidden source: 404", status, 404)

    # Moving
    status, row, _ = post({"root": "vault", "path": "Renames/Trimmed.md",
                           "to": "Renames/sub/Trimmed.md"})
    check("move into a folder: 200", (status, row.get("path")),
          (200, "Renames/sub/Trimmed.md"))
    check("...on disk", ((v / "Renames" / "sub" / "Trimmed.md").read_bytes(),
                         moved.exists()), (before, False))
    status, row, _ = post({"root": "vault", "path": "Renames/sub/Trimmed.md",
                           "to": "Renames/nowhere/Trimmed.md"})
    check("move into a folder that is not there: 404", status, 404)
    status, row, _ = post({"root": "vault", "path": "Renames/sub/Trimmed.md",
                           "to": "15_CONVERSATIONS/Trimmed.md"})
    check("move into the transcripts: 403",
          (status, row.get("authority")), (403, "transcripts"))
    status, row, _ = post({"root": "vault", "path": "Renames/sub",
                           "to": "Renames/sub/deeper"})
    check("a folder into itself: 400", status, 400)

    # Folders
    status, row, _ = post({"root": "vault", "path": "Renames/sub",
                           "name": "Renamed folder"})
    check("rename a folder: 200, a folder row",
          (status, row.get("kind"), row.get("path")),
          (200, "folder", "Renames/Renamed folder"))
    check("...its contents came with it",
          sorted(p.name for p in (v / "Renames" / "Renamed folder").iterdir()),
          ["Trimmed.md", "inner.md"])

    # What may not be renamed: by authority, by root, and pinned names.
    for root, p, want in (
            ("vault", "15_CONVERSATIONS/2026-10-02 chat.md", "transcripts"),
            ("vault", "15_CONVERSATIONS", "transcripts"),
            ("spaces", "alpha/soul.md", "user"),
            ("spaces", "alpha/_active.md", "persona"),
            ("spaces", "alpha/memory", "persona"),
            ("spaces", "commons/alpha/note.md", "persona"),
            ("system", "Engineer/Role.md", "runtime")):
        status, row, _ = post({"root": root, "path": p, "name": "renamed.md"})
        check(f"{root}/{p}: 403 ({want})", (status, row.get("authority")),
              (403, want))
    check("...soul.md where it was", (t["labs"] / "alpha" / "soul.md").exists(),
          True)
    (v / "00_INBOX" / "boards").mkdir(parents=True)
    (v / "00_INBOX" / "boards" / "t-1.md").write_text("- [ ] x\n")
    (v / "40_PROJECTS" / "Proj" / "receipts").mkdir(parents=True)
    (v / "40_PROJECTS" / "Other").mkdir()
    for p in ("40_PROJECTS", "00_INBOX/boards", "00_INBOX/boards/t-1.md",
              "40_PROJECTS/Proj/receipts", "40_PROJECTS/Proj",
              "40_PROJECTS/Loam", "40_PROJECTS/Loam/engineer/role.md"):
        status, row, _ = post({"root": "vault", "path": p, "name": "renamed"})
        check(f"pinned {p}: 403, pinned", (status, row.get("pinned")),
              (403, True))
        _, srow, _ = c.get("stat", root="vault", path=p)
        check(f"...and its row says rename: false",
              srow["capabilities"].get("rename"), False)
    status, row, _ = post({"root": "vault", "path": "40_PROJECTS/Other",
                           "name": "Other, renamed"})
    check("a project folder with no receipts: renamable", status, 200)
    status, row, _ = post({"root": "vault", "path": "tmp-top.md", "name": "x.md"})
    check("a top-level file that is not there: 404", status, 404)
    (v / "top.md").write_text("# top\n")
    status, row, _ = post({"root": "vault", "path": "top.md", "name": "Top note.md"})
    check("a top-level file: renamable", (status, row.get("path")),
          (200, "Top note.md"))
    status, _, _ = c.json("DELETE", "/v1/files/rename",
                          {"root": "vault", "path": "Top note.md"})
    check("another method on rename: 405", status, 405)
    check("no temp files or strays in Renames",
          sorted(p.name for p in (v / "Renames").iterdir()),
          ["Renamed folder", "dangling.md", "link.md", "taken.md"])


def fetch_archive(c: Client, query: str, token=None):
    """GET /v1/files/archive, read as far as the broker sends it. Returns the
    status, the headers, the bytes received, and whether the body ended as
    HTTP says a body ends (False: reset, or cut short)."""
    conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=60)
    tok = c.token if token is None else token
    conn.request("GET", "/v1/files/archive" + query,
                 headers={"Authorization": f"Bearer {tok}"} if tok else {})
    resp = conn.getresponse()
    got = bytearray()
    whole = True
    try:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            got += chunk
    except http.client.IncompleteRead as exc:
        got += exc.partial
        whole = False
    except (ConnectionError, OSError):
        whole = False
    conn.close()
    return (resp.status, {k.lower(): v for k, v in resp.getheaders()},
            bytes(got), whole)


def unzip(data: bytes):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        return z if z.testzip() is None else None
    except zipfile.BadZipFile:
        return None


def test_archive(c: Client, t: dict) -> None:
    v, labs = t["vault"], t["labs"]
    # The fixture's additions: an empty folder, a name that is not ASCII, the
    # secret-shaped names only the archive rule names, a file over the 50 MiB
    # download cap (sparse: its 51 MiB cost no disk), and a known mtime.
    (v / "Empty").mkdir()
    (v / "Café notes.md").write_text("# accents\n")
    (v / "secrets.yaml").write_text(SECRET)
    (v / "20_ATLAS" / "secret.json").write_text(SECRET)
    (v / ".stversions").mkdir()
    (v / ".stversions" / "old.md").write_text(SECRET)
    (v / ".stfolder").mkdir()
    (v / "Big").mkdir()
    with open(v / "Big" / "huge.bin", "wb") as fh:
        fh.truncate(files.DOWNLOAD_MAX + (1 << 20))
    os.utime(v / "20_ATLAS" / "Unique note.md", (1700000000, 1700000000))
    for agent in ("alpha", "beta"):
        (labs / agent / ".agents").mkdir()
        (labs / agent / ".agents" / "mcp_config.json").write_text("{}\n")
        (labs / agent / ".locks" / "wake.lock").write_text("")
        (labs / agent / "secrets.toml").write_text(SECRET)
        (labs / agent / "memory" / "notes.pyc").write_bytes(b"\x00")

    # Auth, method, roots and paths: refused before a byte of zip.
    for tok in ("", "wrong"):
        status, _, _, _ = fetch_archive(c, "?root=vault", token=tok)
        check(f"archive: token {tok!r} is 401", status, 401)
    status, _, _ = c.json("POST", "/v1/files/archive?root=vault", {})
    check("archive: POST is 405", status, 405)
    for query, want in (("?root=system", 404), ("?root=nowhere", 404),
                        ("?root=vault&path=nothing-here", 404),
                        ("?root=vault&path=.obsidian", 404),
                        ("?root=vault&path=.git", 404),
                        ("?root=vault&path=40_PROJECTS/archive.key", 404),
                        ("?root=vault&path=inner-link", 404),
                        ("?root=vault&path=link-dir", 404),
                        ("?root=vault&path=..", 400),
                        ("?root=vault&path=%2e%2e%2f40_PROJECTS", 400),
                        ("?root=vault&path=tmp.md", 404),
                        ("?root=vault&path=Many/n01.md", 400),
                        ("?root=spaces&path=stray", 404),
                        ("?root=spaces&path=alpha/logs", 404)):
        status, headers, body, _ = fetch_archive(c, query)
        check(f"archive {query}: {want}, a JSON sentence, no-store",
              (status, "error" in json.loads(body), headers.get("cache-control")),
              (want, True, "no-store"))
        no_secret(f"archive refusal {query}", body.decode("utf-8", "replace"))

    # The whole vault.
    status, headers, body, whole = fetch_archive(c, "?root=vault")
    check("vault archive: 200, a zip, ended as HTTP ends a body",
          (status, headers.get("content-type"), whole),
          (200, "application/zip", True))
    check("vault archive: chunked, no length",
          (headers.get("transfer-encoding"), headers.get("content-length")),
          ("chunked", None))
    check("vault archive: headers",
          (headers.get("content-disposition"), headers.get("x-content-type-options"),
           headers.get("cache-control")),
          ("attachment; filename*=UTF-8''vault.zip", "nosniff", "no-store"))
    z = unzip(body)
    check("vault archive: a valid zip, every CRC right", z is not None, True)
    names = set(z.namelist()) if z else set()
    for name in (".obsidian/", ".obsidian/app.json", ".trash/40_PROJECTS/Loam/state.md",
                 "15_CONVERSATIONS/2026-10-02 chat.md", "%2e%2e/literal.md",
                 "Empty/", "Café notes.md", "Big/huge.bin", "image.png",
                 "40_PROJECTS/Loam/state.md"):
        check(f"vault archive holds {name}", name in names, True)
    for name in (".git/config", "deploy.env", "server.pem", "40_PROJECTS/Loam/creds.env",
                 "40_PROJECTS/archive.key/x.md", "secrets.yaml", "20_ATLAS/secret.json",
                 ".stversions/old.md", ".stfolder/", "link-to-passwd.md",
                 "link-dir/", "inner-link/", "inner-link/Loam/state.md"):
        check(f"vault archive leaves out {name}", name in names, False)
    check("vault archive: nothing reached through a link",
          [n for n in names if n.startswith(("link-dir", "inner-link"))], [])
    if z:
        check("vault archive: a file over 50 MiB, whole",
              z.getinfo("Big/huge.bin").file_size, files.DOWNLOAD_MAX + (1 << 20))
        check("vault archive: bytes as on disk",
              z.read("image.png"), (v / "image.png").read_bytes())
        info = z.getinfo("Café notes.md")
        check("vault archive: a UTF-8 name says so (bit 11)",
              bool(info.flag_bits & 0x800), True)
        info = z.getinfo("20_ATLAS/Unique note.md")
        check("vault archive: a file keeps its modified time",
              info.date_time, time.localtime(1700000000)[:6])
        check("vault archive: text deflated, binary stored",
              (info.compress_type, z.getinfo("image.png").compress_type),
              (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED))

    # A folder: names relative to it, its name on the file.
    status, headers, body, _ = fetch_archive(
        c, "?root=vault&path=" + quote("40_PROJECTS/Loam", safe=""))
    z = unzip(body)
    check("folder archive: named for the folder",
          headers.get("content-disposition"), "attachment; filename*=UTF-8''Loam.zip")
    check("folder archive: entries relative to it",
          sorted(n for n in (z.namelist() if z else []) if not n.endswith("/")),
          ["Loam.md", "Plan.md", "engineer/role.md", "notes.md", "state.md"])

    # The spaces.
    status, headers, body, whole = fetch_archive(c, "?root=spaces")
    z = unzip(body)
    check("spaces archive: 200, a valid zip, ai-spaces.zip",
          (status, z is not None, whole, headers.get("content-disposition")),
          (200, True, True, "attachment; filename*=UTF-8''ai-spaces.zip"))
    names = set(z.namelist()) if z else set()
    for name in ("alpha/soul.md", "alpha/.claude/settings.json", "alpha/.mcp.json",
                 "alpha/.agents/mcp_config.json", "alpha/memory/MEMORY.md",
                 "commons/alpha/note.md", "README.md", "beta/config.md"):
        check(f"spaces archive holds {name}", name in names, True)
    for name in ("alpha/logs/wake.log", "alpha/scratch/", "alpha/.locks/wake.lock",
                 "alpha/wake.env", "alpha/.env", "alpha/secrets.toml",
                 "alpha/memory/notes.pyc", "alpha/.claude/skills", "stray/", ".git/"):
        check(f"spaces archive leaves out {name}", name in names, False)
    check("spaces archive: nothing secret in it", SECRET.encode() in body, False)

    # A failure partway: the zip is never finished, the HTTP body never ends.
    broken = v / "Broken"
    broken.mkdir()
    (broken / "a.bin").write_bytes(os.urandom(2 << 20))
    (broken / "z.md").write_text("unreadable\n")
    keep = files._zipinfo

    def failing(arcname, st, folder):
        if arcname == "z.md":
            raise OSError(5, "Input/output error")
        return keep(arcname, st, folder)
    files._zipinfo = failing
    try:
        status, _, body, whole = fetch_archive(c, "?root=vault&path=Broken")
    finally:
        files._zipinfo = keep
    check("failure partway: 200 begun, then reset, not ended",
          (status, whole), (200, False))
    check("failure partway: some of the zip was sent", len(body) > (1 << 20), True)
    check("failure partway: what arrived is not a valid zip", unzip(body), None)
    if os.geteuid() != 0:
        os.chmod(broken / "z.md", 0)
        try:
            status, _, body, whole = fetch_archive(c, "?root=vault&path=Broken")
        finally:
            os.chmod(broken / "z.md", 0o644)
        check("an unreadable file fails the archive, never left out quietly",
              (status, whole, unzip(body)), (200, False, None))
    status, _, body, whole = fetch_archive(c, "?root=vault&path=Broken")
    check("...and the same folder readable again is a whole zip",
          (status, whole, sorted(unzip(body).namelist())), (200, True, ["a.bin", "z.md"]))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="broker-files-test-"))
    keep = (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH,
            cfg.VAULT, cfg.AI_LABS, files.SYSTEM_BASE)
    try:
        cfg.STATE_DIR = tmp / "state"
        cfg.SESSION_DIR = tmp / "state" / "sessions"
        cfg.USAGE_PATH = tmp / "state" / "provider-usage.json"
        cfg.TOKEN_PATH = tmp / "state" / "token"
        t = build(tmp)
        cfg.VAULT, cfg.AI_LABS, files.SYSTEM_BASE = t["vault"], t["labs"], t["system"]
        server = broker_mod.Server(broker_mod.Broker())
        ready = {"event": threading.Event()}

        def run() -> None:
            loop = asyncio.new_event_loop()
            ready["stop"] = loop.create_future()
            loop.run_until_complete(serve(server, ready))

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        ready["event"].wait(10)
        c = Client(ready["port"], server.broker.token)
        try:
            test_auth(c)
            test_read(c, t)
            test_refusals(c, t)
            test_writes(c, t)
            test_resolve(c)
            test_resolve_paths(c)
            test_resolve_folders(c)
            test_html(c, t)
            test_system_pointers(c)
            test_upload(c, t)
            test_rename(c, t)
            test_archive(c, t)
        finally:
            ready["loop"].call_soon_threadsafe(ready["stop"].set_result, None)
            thread.join(10)
    finally:
        (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH,
         cfg.VAULT, cfg.AI_LABS, files.SYSTEM_BASE) = keep
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
