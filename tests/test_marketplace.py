#!/usr/bin/env python3
"""lib/loam_marketplace.py, bin/loam-market and the marketplace repository's
own build.py (docs/marketplace/ here) (2026-11-17).

The list is served by a local HTTP server, so every answer a host can give
is real: a list, an empty one, none at all (404), a server error, not JSON.
Checks:

  - the format: an entry that breaks a rule (an id, a kind, a link at no
    ref, a long description) is left out with its reason and the rest are
    drawn, sorted by name; the same id twice is listed once;
  - reading: 404 and an empty list are `empty`, never an error; a 500 or a
    list that is not one is `unreachable` with a sentence; an empty
    `marketplace.list` turns it off; the answer is cached and `refresh`
    fetches again;
  - `listing` marks the entries a plugin of that id is installed for;
  - asking to be listed: the entry is read from the plugin's own preview,
    and the link is GitHub's new-file page in the marketplace repository
    with `listings/<id>.json` holding exactly that entry; no description is
    refused;
  - build.py: listings in, list.json out, which the box reads with nothing
    left out; a branch link, a file not named by its id and an unknown field
    are refused by `--check`;
  - the broker answers `GET /v1/marketplace` (the route is in the page's
    relay registry, tests/test_settings.py).

Run: python3 tests/test_marketplace.py
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-marketplace-"))
CONFIG = TMP / "loam.toml"

ANSWERS: dict[str, tuple[int, str]] = {}
HITS: list[str] = []


class Host(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        HITS.append(self.path)
        status, body = ANSWERS.get(self.path, (404, "not found"))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Host)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"
CONFIG.write_text(f'''
[paths]
home = "{TMP}/home"
plugins = "{TMP}/plugins"
state = "{TMP}/state"
[marketplace]
list = "{BASE}/list.json"
''')
os.environ["LOAM_CONFIG"] = str(CONFIG)

sys.path.insert(0, str(ROOT / "lib"))
import loam_marketplace as mk  # noqa: E402
import loam_plugins  # noqa: E402

FAILURES = 0


def check(label: str, got, want) -> None:
    global FAILURES  # noqa: PLW0603
    ok = got == want
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def entry(pid: str, **over) -> dict:
    base = {"id": pid, "name": pid.title(), "author": "A friend", "kind": ["extension"],
            "description": f"The {pid} plugin.",
            "link": f"https://github.com/friend/loam-{pid}/tree/v1.0.0/{pid}"}
    base.update(over)
    return base


try:
    good = [entry("pet", name="A pet"), entry("dusk", kind=["theme"], name="Dusk"),
            entry("pet", name="Again")]
    bad = [entry("Bad"), entry("nokind", kind=["toy"]), entry("noref", link="https://github.com/a/b"),
           entry("long", description="x" * 201), "not an object"]
    ANSWERS["/list.json"] = (200, json.dumps({"version": 1, "plugins": good + bad}))
    out = mk.read()
    check("a list is listed", out["state"], "listed")
    check("its good entries, by name, the same id once",
          [(e["id"], e["name"]) for e in out["plugins"]], [("pet", "A pet"), ("dusk", "Dusk")])
    check("each bad one is left out with its reason", len(out["skipped"]), 6)
    check("a reason says what is wrong",
          any("nokind: `kind`" in why for why in out["skipped"]), True)
    check("an entry carries its repository and ref",
          {k: out["plugins"][0][k] for k in ("repo", "ref", "path")},
          {"repo": "friend/loam-pet", "ref": "v1.0.0", "path": "pet"})

    hits = len(HITS)
    mk.read()
    check("the answer is cached", len(HITS), hits)
    mk.read(refresh=True)
    check("refresh fetches again", len(HITS), hits + 1)

    for label, answer, state in (
            ("no list there yet (404)", None, "empty"),
            ("an empty list", (200, '{"version": 1, "plugins": []}'), "empty"),
            ("a server error", (500, "oops"), "unreachable"),
            ("not JSON", (200, "<html>"), "unreachable"),
            ("a newer format", (200, '{"version": 2, "plugins": []}'), "unreachable")):
        if answer is None:
            ANSWERS.pop("/list.json", None)
        else:
            ANSWERS["/list.json"] = answer
        got = mk.read(refresh=True)
        check(f"{label}: {state}", (got["state"], got["plugins"]), (state, []))
        if state == "unreachable":
            check(f"{label}: says why", bool(got["message"]), True)
    check("an empty address turns it off", mk.read(url="")["state"], "off")
    check("only https (or this box) is fetched",
          mk.fetch("http://example.com/list.json")[0], "unreachable")

    # -- installed or not --------------------------------------------------------
    ANSWERS["/list.json"] = (200, json.dumps({"version": 1, "plugins": good}))
    real = loam_plugins.listing
    loam_plugins.listing = lambda: {"plugins": [
        {"id": "dusk", "effective": True, "version": "1.0.0",
         "source": {"kind": "link", "repo": "friend/loam-dusk"}},
        {"id": "pet", "effective": False, "version": "0.9.0", "source": {"kind": "built"}}]}
    try:
        marked = {e["id"]: (e["installed"], e["installed_version"])
                  for e in mk.listing(refresh=True)["plugins"]}
    finally:
        loam_plugins.listing = real
    check("an entry installed here is marked, at its version",
          marked, {"dusk": (True, "1.0.0"), "pet": (False, None)})

    # -- asking to be listed ------------------------------------------------------
    link = "https://github.com/friend/loam-pet/tree/v1.2.0/pet"
    real_install = loam_plugins.install
    calls = []

    def preview(body, by=None):
        calls.append(body)
        return {"installed": False, "id": "pet", "name": "A pet", "version": "1.2.0",
                "author": "A friend", "kind": ["extension"],
                "declaration": {"touches": {}}, "source": {"commit": "1" * 40}}

    loam_plugins.install = preview
    try:
        req = mk.listing_request(link, "A small animal for the page.")
        try:
            mk.listing_request(link, "")
            check("no description is refused", "no error", "ValueError")
        except ValueError:
            check("no description is refused", "ValueError", "ValueError")
    finally:
        loam_plugins.install = real_install
    check("the preview is read without a confirm (nothing installed)", calls[0], {"url": link})
    check("the entry is the plugin's own", req["entry"],
          {"id": "pet", "name": "A pet", "description": "A small animal for the page.",
           "author": "A friend", "kind": ["extension"], "version": "1.2.0", "link": link})
    url = urllib.parse.urlsplit(req["url"])
    q = urllib.parse.parse_qs(url.query)
    check("the link is the marketplace's new-file page",
          (url.netloc, url.path), ("github.com", "/loamhq/marketplace/new/main"))
    check("it names the listing's file", q["filename"], ["listings/pet.json"])
    check("and holds exactly the entry", json.loads(q["value"][0]), req["entry"])

    # -- the marketplace repository's build.py -------------------------------------
    repo = TMP / "marketplace"
    shutil.copytree(ROOT / "docs" / "marketplace", repo)
    (repo / "listings" / "pet.json").write_text(json.dumps(req["entry"]))
    (repo / "listings" / "dusk.json").write_text(json.dumps(entry("dusk", kind="theme")))
    built = subprocess.run([sys.executable, str(repo / "build.py")], capture_output=True,
                           text=True)
    check("build.py builds list.json", built.returncode, 0)
    entries, skipped = mk.parse((repo / "list.json").read_text())
    check("the box reads what it built, nothing left out",
          ([e["id"] for e in entries], skipped), (["pet", "dusk"], []))
    check("--check passes on main once built",
          subprocess.run([sys.executable, str(repo / "build.py"), "--check", "--strict"],
                         capture_output=True).returncode, 0)
    for name, value, why in (
            ("branch.json", entry("branch", link="https://github.com/a/b/tree/main/x"), "branch"),
            ("wrong.json", entry("right"), "named by the id"),
            ("extra.json", entry("extra", stars=5), "unknown fields")):
        (repo / "listings" / name).write_text(json.dumps(value))
        res = subprocess.run([sys.executable, str(repo / "build.py"), "--check"],
                             capture_output=True, text=True)
        check(f"--check refuses: {why}", (res.returncode, why in res.stderr), (1, True))
        (repo / "listings" / name).unlink()
    (repo / "listings" / "dusk.json").write_text(json.dumps(entry("dusk", kind="theme", featured=True, image="cover.webp")))
    check("build.py takes featured and image",
          subprocess.run([sys.executable, str(repo / "build.py")], capture_output=True).returncode, 0)
    check("and the box reads them back",
          {k: next(e for e in mk.parse((repo / "list.json").read_text())[0] if e["id"] == "dusk").get(k)
           for k in ("featured", "image")}, {"featured": True, "image": "cover.webp"})
    for value, why in ((entry("dusk", featured="yes"), "`featured` is true"),
                       (entry("dusk", image="../cover.webp"), "`image` is a picture")):
        (repo / "listings" / "dusk.json").write_text(json.dumps(value))
        res = subprocess.run([sys.executable, str(repo / "build.py"), "--check"],
                             capture_output=True, text=True)
        check(f"--check refuses: {why}", (res.returncode, why in res.stderr), (1, True))
    check("the README says Settings › Market reads it",
          "every Loam reads in Settings › Market" in (ROOT / "docs" / "marketplace" / "README.md").read_text(), True)
    check("the shipped list is the empty one",
          json.loads((ROOT / "docs" / "marketplace" / "list.json").read_text()),
          {"version": 1, "plugins": []})

    # -- sharing by link ---------------------------------------------------------
    os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
                       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test",
                       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
    hub = TMP / "hub"
    (hub / "sam").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(hub / "sam" / "loam-dusk.git")],
                   check=True)
    os.environ["LOAM_SHARE_GIT_BASE"] = f"file://{hub}/"
    plugin = TMP / "plugins" / "dusk"
    plugin.mkdir(parents=True)
    (plugin / "PLUGIN.md").write_text("---\nid: dusk\nname: Dusk\nversion: 1.0.0\napi: 0\n"
                                      "kind: [theme]\nauthor: Sam\n---\nA warm dark theme.\n")
    (plugin / "theme.css").write_text(":root { --paper: #222; }\n")
    shared = mk.share("dusk", "sam/loam-dusk")
    check("share answers the link to install it by", shared["link"],
          "https://github.com/sam/loam-dusk/tree/v1.0.0/dusk")
    bare = hub / "sam" / "loam-dusk.git"
    listed = subprocess.run(["git", "-C", str(bare), "ls-tree", "-r", "--name-only", "v1.0.0"],
                            capture_output=True, text=True).stdout.split()
    check("the tag holds the plugin's folder by its id", listed, ["dusk/PLUGIN.md", "dusk/theme.css"])
    check("and the link parses as an install link",
          loam_plugins.parse_source({"url": shared["link"]}),
          {"repo": "sam/loam-dusk", "ref": "v1.0.0", "path": "dusk"})
    for args, why in ((("dusk", "sam/loam-dusk"), "already has the tag v1.0.0"),
                      (("ghost", "sam/loam-dusk"), "no plugin 'ghost'"),
                      (("dusk", "not a repo"), "is not a repository"),
                      (("dusk", "sam/missing"), "could not be read")):
        try:
            mk.share(*args)
            check(f"share refuses: {why}", "no error", why)
        except mk.ShareError as exc:
            check(f"share refuses: {why}", why in str(exc), True)
    (plugin / "PLUGIN.md").write_text((plugin / "PLUGIN.md").read_text().replace("1.0.0", "1.1.0"))
    check("a new version is a new tag beside the old",
          mk.share("dusk", "sam/loam-dusk")["tag"], "v1.1.0")
    os.environ.pop("LOAM_SHARE_GIT_BASE")

    # -- a listing's own page (2026-11-18, Settings › Market) ---------------------
    check("featured and an image in the plugin's folder are kept",
          {k: mk.check_entry(entry("dusk", featured=True, image="cover.webp"))[0].get(k)
           for k in ("featured", "image")}, {"featured": True, "image": "cover.webp"})
    check("a featured that is not true, or an image path that leaves, is left out",
          [k for k in ("featured", "image")
           if k in mk.check_entry(entry("dusk", featured="yes", image="../x.png"))[0]], [])
    git = lambda *a, cwd=None: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)
    work = TMP / "dusk-src"
    (work / "dusk").mkdir(parents=True)
    (work / "dusk" / "PLUGIN.md").write_text(
        "---\nid: dusk\nname: Dusk\nversion: 1.2.0\napi: 0\nkind: [theme]\nauthor: Sam\n"
        "icon: icon.svg\n---\nA warm dark theme, for evenings.\n")
    (work / "dusk" / "theme.css").write_text(':root[data-look="dusk"] { --paper-page: #222222; }\n')
    (work / "dusk" / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (work / "dusk" / "cover.webp").write_bytes(b"RIFF0000WEBPVP8 ")
    git("init", "-q", "-b", "main", cwd=work)
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "dusk", cwd=work)
    git("tag", "v1.2.0", cwd=work)
    (hub / "friend").mkdir(parents=True, exist_ok=True)
    git("clone", "-q", "--bare", str(work), str(hub / "friend" / "loam-dusk"))
    os.environ["LOAM_PLUGIN_GIT_BASE"] = f"file://{hub}/"
    listed = entry("dusk", kind=["theme"], name="Dusk", author="Sam", featured=True, image="cover.webp",
                   link="https://github.com/friend/loam-dusk/tree/v1.2.0/dusk")
    ANSWERS["/list.json"] = (200, json.dumps({"version": 1, "plugins": [listed, entry("ghost")]}))
    mk.read(refresh=True)
    about = mk.details("dusk")
    check("details: what the plugin at the link says of itself",
          (about["name"], about["version"], about["kind"], about["installable"], about["guide"]),
          ("Dusk", "1.2.0", ["theme"], True, "A warm dark theme, for evenings."))
    check("details: its declaration, as an install's first call shows it",
          (about["declaration"]["touches"]["network"], about["declaration"]["page_code"],
           about["declaration"]["touches"]["server"]), ([], False, False))
    check("details: the listing marked featured, its icon, cover and theme",
          (about["listing"]["featured"], about["icon"], about["image"], about["theme"]),
          (True, True, True, {"options": []}))
    check("the theme's own CSS, for the swatch and Try it",
          mk.file("dusk", "theme.css"), (b':root[data-look="dusk"] { --paper-page: #222222; }\n',
                                         "text/css; charset=utf-8"))
    check("its icon and its cover, by type", (mk.file("dusk", "icon")[1], mk.file("dusk", "image")[1]),
          ("image/svg+xml", "image/webp"))
    check("nothing is installed by reading", (TMP / "plugins" / "dusk" / "theme.css").read_text().startswith(":root {"), True)
    shutil.rmtree(hub / "friend" / "loam-dusk")
    check("details are kept: a second read does not fetch", mk.details("dusk")["version"], "1.2.0")
    for args, kind, why in ((("nobody",), mk.NotListed, "not listed"),
                            (("ghost",), loam_plugins.PluginError, "unreadable link"),
                            (("dusk", "server/main.py"), mk.NotListed, "not a listing file")):
        try:
            (mk.file if len(args) == 2 else mk.details)(*args)
            check(f"refused: {why}", "no error", kind.__name__)
        except kind:
            check(f"refused: {why}", kind.__name__, kind.__name__)
    # The broker's own routes, through its real handler.
    import asyncio  # noqa: PLC0415
    import importlib.machinery  # noqa: PLC0415
    import importlib.util  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415
    loader = importlib.machinery.SourceFileLoader("session_broker", str(ROOT / "bin" / "session-broker"))
    broker_mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(broker_mod)

    class FakeBroker:
        sessions: dict = {}
        token = "test-token"

    def ask(port, path):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                     headers={"Authorization": "Bearer test-token"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type"), exc.read()

    async def routes():
        server = broker_mod.Server.__new__(broker_mod.Server)
        server.broker = FakeBroker()
        tcp = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = tcp.sockets[0].getsockname()[1]
        try:
            return [await asyncio.to_thread(ask, port, p) for p in (
                "/v1/marketplace/dusk", "/v1/marketplace/dusk/theme.css",
                "/v1/marketplace/dusk/icon", "/v1/marketplace/nobody", "/v1/marketplace/dusk/server")]
        finally:
            tcp.close()

    got = asyncio.run(routes())
    check("broker: GET /v1/marketplace/<id> is the details",
          (got[0][0], json.loads(got[0][2])["name"]), (200, "Dusk"))
    check("broker: its theme.css and icon, by type",
          [(g[0], g[1]) for g in got[1:3]], [(200, "text/css; charset=utf-8"), (200, "image/svg+xml")])
    check("broker: a listing it does not have, and a file a listing has not, are 404",
          [g[0] for g in got[3:]], [404, 404])
    os.environ.pop("LOAM_PLUGIN_GIT_BASE")
    src = (ROOT / "bin" / "session-broker").read_text()
    check("the broker serves GET /v1/marketplace/<id> and its files",
          "loam_marketplace.details" in src and "loam_marketplace.file" in src, True)

    # -- the command ----------------------------------------------------------------
    ANSWERS.pop("/list.json", None)
    cli = subprocess.run([str(ROOT / "bin" / "loam-market"), "list"], capture_output=True,
                         text=True, timeout=60)
    check("loam market list, nothing listed: calm", (cli.returncode, cli.stdout.strip()),
          (0, "Nothing is listed in the marketplace yet."))

    # -- finding things, for an agent (2026-11-18) ----------------------------------
    # The ranking is the page's own, run in Node; app/test/market-card.test.js
    # holds it equal to the page's over the fixture list. Here: the states.
    ANSWERS.pop("/list.json", None)
    found = mk.search("anything", refresh=True)
    check("search, nothing listed: no results, said plainly",
          (found["state"], found["results"], found["say"].startswith("The Market lists nothing yet")),
          ("empty", [], True))
    ANSWERS["/list.json"] = (200, json.dumps({"version": 1, "plugins": [
        entry("pet", name="A pet", description="A small animal that sleeps in the corner."),
        entry("dusk", kind=["theme"], name="Dusk", description="Low light for evenings."),
        entry("coach", kind=["agent"], name="Running coach")]}))
    mk.read(refresh=True)
    found = mk.search("an animal", limit=5)
    check("search finds by plain words", [r["id"] for r in found["results"]], ["pet"])
    check("each result carries what an agent needs and its card line",
          found["results"][0], {"id": "pet", "name": "A pet", "author": "A friend", "kind": ["extension"],
                                "description": "A small animal that sleeps in the corner.",
                                "installed": False, "installable": True,
                                "card": mk.card_line({"id": "pet", "name": "A pet"})})
    check("a kind narrows it", [r["id"] for r in mk.search("", "theme")["results"]], ["dusk"])
    check("an agent package is listed, not installable",
          {k: mk.search("coach")["results"][0].get(k) for k in ("installable", "why_not")},
          {"installable": False, "why_not": "Loam lists agent packages and installs none yet"})
    check("no match: offer to build it", mk.search("zebra jokes")["say"].endswith(
          "Offer to build it as the owner's own plugin."), True)
    try:
        mk.search("x", "toy")
        check("an unknown kind is refused", False, True)
    except ValueError:
        check("an unknown kind is refused", True, True)
    check("a card line: the name linked to the listing's page",
          mk.card_line({"id": "pet", "name": "A [pet]"}, "https://loam.example/"),
          "[A pet](https://loam.example/?modal=market&listing=pet)")
    ANSWERS.pop("/list.json", None)
    mk.read(refresh=True)

    # -- the broker's route ---------------------------------------------------------
    src = (ROOT / "bin" / "session-broker").read_text()
    check("the broker serves GET /v1/marketplace",
          'parts == ["v1", "marketplace"]' in src and "loam_marketplace.listing" in src, True)
finally:
    server.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'FAILED' if FAILURES else 'OK'}: {FAILURES} failure(s)")
sys.exit(1 if FAILURES else 0)
