#!/usr/bin/env python3
"""Use a local HTTP catalog to check Marketplace lookup, empty and unavailable lists, malformed responses and the CLI."""

from __future__ import annotations

import hashlib
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-marketplace-"))
CONFIG = TMP / "loam.toml"

ANSWERS: dict[str, tuple[int, str | bytes]] = {}
HITS: list[str] = []


class Host(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        HITS.append(self.path)
        status, body = ANSWERS.get(self.path, (404, "not found"))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode())

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
    subprocess.run([sys.executable, str(repo / "build.py")], capture_output=True, text=True)
    check("a listing the reviewer has not passed is left out of list.json",
          json.loads((repo / "list.json").read_text())["plugins"], [])

    def passed(pid: str) -> None:
        """What the reviewer's decide.py writes on a pass (notes/<id>.json)."""
        link = json.loads((repo / "listings" / f"{pid}.json").read_text())["link"]
        (repo / "notes" / f"{pid}.json").write_text(json.dumps(
            {"id": pid, "link": link, "commit": "c" * 40, "note": {"summary": f"{pid}, reviewed."}}))
    passed("pet")
    passed("dusk")
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
    check("and the box reads them back (image as the cover)",
          {k: next(e for e in mk.parse((repo / "list.json").read_text())[0] if e["id"] == "dusk").get(k)
           for k in ("featured", "cover")}, {"featured": True, "cover": "cover.webp"})
    # Creator links, cover and README
    (repo / "listings" / "dusk.json").write_text(json.dumps(entry(
        "dusk", kind="theme", cover="cover-large.webp", cover_dark="https://pics.example/dusk.webp",
        readme="docs/ABOUT.md",
        author={"name": "Sam  Rivera", "links": [{"label": "Site", "url": "https://sam.example"},
                                                  {"url": "mailto:sam@example.com"}]})))
    check("build.py takes an author with links, covers and a readme",
          subprocess.run([sys.executable, str(repo / "build.py")], capture_output=True).returncode, 0)
    raw_dusk = next(e for e in json.loads((repo / "list.json").read_text())["plugins"] if e["id"] == "dusk")
    check("list.json: the author is the name alone, for a Loam that reads only a name",
          raw_dusk["author"], "Sam Rivera")
    check("list.json: and the whole is the creator",
          raw_dusk["creator"], {"name": "Sam Rivera", "links": [{"label": "Site", "url": "https://sam.example"},
                                                                {"label": "", "url": "mailto:sam@example.com"}]})
    old_reader = [e for e in json.loads((repo / "list.json").read_text())["plugins"]
                  if isinstance(e.get("author"), str) and e["author"].strip()]
    check("every built listing has a plain-string author (what 0.2.6 requires)",
          len(old_reader), len(json.loads((repo / "list.json").read_text())["plugins"]))
    dusk_read = next(e for e in mk.parse((repo / "list.json").read_text())[0] if e["id"] == "dusk")
    check("the box reads the creator, each link with its kind",
          dusk_read["creator"], {"name": "Sam Rivera", "links": [
              {"label": "Site", "url": "https://sam.example", "kind": "website"},
              {"label": "Email", "url": "mailto:sam@example.com", "kind": "email"}]})
    check("and the covers and readme, with where anyone can see the cover",
          {k: dusk_read.get(k) for k in ("cover", "cover_url", "cover_dark", "cover_dark_url", "readme")},
          {"cover": "cover-large.webp",
           "cover_url": "https://raw.githubusercontent.com/friend/loam-dusk/v1.0.0/dusk/cover-large.webp",
           "cover_dark": "https://pics.example/dusk.webp", "cover_dark_url": "https://pics.example/dusk.webp",
           "readme": "docs/ABOUT.md"})
    for value, why in (
            (entry("dusk", author={"name": "Sam", "links": [{"url": "http://sam.example"}]}), "https link or mailto"),
            (entry("dusk", author={"name": "Sam", "links": [{"url": "https://a.example", "label": "x" * 41}]}), "at most 40"),
            (entry("dusk", author={"name": "Sam", "links": [{"url": f"https://a{i}.example"} for i in range(9)]}), "at most 8"),
            (entry("dusk", author={"links": []}), "a line of text"),
            (entry("dusk", author={"name": "Sam", "site": "x"}), "unknown fields site"),
            (entry("dusk", cover="../c.webp"), "`cover` is a picture"),
            (entry("dusk", cover="http://pics.example/c.webp"), "`cover` is a picture"),
            (entry("dusk", cover="c.webp", image="c.webp"), "older name of `cover`"),
            (entry("dusk", readme="../README.md"), "`readme` is a Markdown file"),
            (entry("dusk", readme="notes.txt"), "`readme` is a Markdown file")):
        (repo / "listings" / "dusk.json").write_text(json.dumps(value))
        res = subprocess.run([sys.executable, str(repo / "build.py"), "--check"],
                             capture_output=True, text=True)
        check(f"--check refuses: {why}", (res.returncode, why in res.stderr), (1, True))
    (repo / "listings" / "dusk.json").write_text(json.dumps(entry("dusk", kind="theme")))
    check("a listing as it was before (author a name, no cover) still builds",
          subprocess.run([sys.executable, str(repo / "build.py"), "--check"], capture_output=True).returncode, 0)
    for value, why in ((entry("dusk", featured="yes"), "`featured` is true"),
                       (entry("dusk", image="../cover.webp"), "`image` is a picture")):
        (repo / "listings" / "dusk.json").write_text(json.dumps(value))
        res = subprocess.run([sys.executable, str(repo / "build.py"), "--check"],
                             capture_output=True, text=True)
        check(f"--check refuses: {why}", (res.returncode, why in res.stderr), (1, True))
    check("the README says Settings › Market reads it",
          "every Loam reads in Settings › Market" in (ROOT / "docs" / "marketplace" / "README.md").read_text(), True)
    readme_text = (ROOT / "docs" / "marketplace" / "README.md").read_text()
    check("the README points at the standards page, says the reviewer merges by itself, "
          "and keeps that a pass is not a promise",
          ("review/STANDARDS.md" in readme_text, "no person in the path" in readme_text,
           "reviewed by hand" in readme_text, "not a promise" in readme_text), (True, True, False, True))
    check("the reviewer's own tests pass in the shipped copy",
          subprocess.run([sys.executable, str(ROOT / "docs" / "marketplace" / "review" / "test_review.py")],
                         capture_output=True, text=True, timeout=120).returncode, 0)
    check("every proof fixture parses cleanly against this tree's manifest reader",
          subprocess.run([sys.executable, str(ROOT / "docs" / "marketplace" / "review" / "proof.py"),
                          "--offline-only", "--loam-dir", str(ROOT)],
                         capture_output=True, text=True, timeout=120).returncode, 0)
    check("the shipped list is the empty one",
          json.loads((ROOT / "docs" / "marketplace" / "list.json").read_text()),
          {"version": 1, "stats": "stats.json", "plugins": []})

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
    check("retrying the identical share resumes the existing tag", mk.share("dusk", "sam/loam-dusk")["commit"],
          shared["commit"])
    (plugin / "theme.css").write_text(":root { --paper: #333; }\n")
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

    # Listing detail
    check("featured and an image in the plugin's folder are kept (image is the cover)",
          {k: mk.check_entry(entry("dusk", featured=True, image="cover.webp"))[0].get(k)
           for k in ("featured", "cover")}, {"featured": True, "cover": "cover.webp"})
    check("a featured that is not true, or a cover path that leaves, is left out",
          [k for k in ("featured", "cover", "image")
           if k in mk.check_entry(entry("dusk", featured="yes", image="../x.png"))[0]], [])

    # Read the review note from the catalog record.
    sha = "a" * 40
    note = {"summary": "  Counts   words. ", "touches": ["Nothing off the page.", 7, ""],
            "may_change": ["x" * 900], "worth_knowing": [], "changed": ["Now reads threads."],
            "since": "1.0.0", "extra": "dropped"}
    reviewed, why = mk.check_entry(entry("pet", review={"commit": sha, "version": "1.0.0",
                                                        "reviewed_at": "2026-11-19T10:00:00Z",
                                                        "note": note}))
    check("a reviewed listing keeps its note, bounded, and nothing else in it",
          (why, reviewed["review"]["note"]["summary"], reviewed["review"]["note"]["touches"],
           len(reviewed["review"]["note"]["may_change"][0]), "worth_knowing" in reviewed["review"]["note"],
           "extra" in reviewed["review"]["note"], reviewed["review"]["note"]["since"],
           reviewed["review"]["reviewed_at"]),
          (None, "Counts words.", ["Nothing off the page."], 400, False, False, "1.0.0", "2026-11-19"))
    check("and is read and installed at the commit the review read, not its tag",
          (reviewed["pinned_link"], reviewed["pin"], reviewed["link"]),
          (f"https://github.com/friend/loam-pet/tree/{sha}/pet", {"ref": sha, "path": "pet"},
           entry("pet")["link"]))
    for bad, what in (({"commit": "v1.0.0", "note": note}, "a tag for a commit"),
                      ({"commit": sha, "note": {"touches": ["x"]}}, "a note with no summary"),
                      ("reviewed", "a review that is not an object")):
        got, why = mk.check_entry(entry("pet", review=bad))
        check(f"a review with {what} is left out, never the listing",
              (why, "review" in got, "pinned_link" in got), (None, False, False))

    # Creator, cover and README
    old_style, why = mk.check_entry(entry("pet"))
    check("an old listing is still valid: a plain-string author, no cover, no readme",
          (why, old_style["author"], old_style["creator"], "cover" in old_style, "readme" in old_style),
          (None, "A friend", {"name": "A friend", "links": []}, False, False))
    kinds = {
        "https://friend.example": "website", "https://www.github.com/friend": "github",
        "https://github.com/sponsors/friend": "github", "https://x.com/friend": "x",
        "https://twitter.com/friend": "x", "https://bsky.app/profile/friend.bsky.social": "bluesky",
        "https://friend.bsky.social": "bluesky", "https://instagram.com/friend": "instagram",
        "https://www.youtube.com/@friend": "youtube", "https://youtu.be/abc": "youtube",
        "https://www.patreon.com/friend": "patreon", "https://ko-fi.com/friend": "kofi",
        "https://mastodon.social/@friend": "mastodon", "https://social.example/@friend": "mastodon",
        "https://m.youtube.com/@friend": "youtube", "https://medium.example/@friend/a-post": "website",
        "mailto:friend@example.com": "email", "https://www.linkedin.com/in/friend": "linkedin",
        "https://buymeacoffee.com/friend": "buymeacoffee", "https://friend.substack.com": "substack",
        "https://notgithub.com/friend": "website",
    }
    check("each link's kind is read from its address",
          {u: mk.link_kind(u) for u in kinds}, kinds)
    check("every kind has a label", all(k in mk.LINK_LABEL for k in set(kinds.values())), True)
    mixed = mk.check_entry(entry("pet", author={"name": "  Sam   Rivera ", "links": [
        {"label": "My site", "url": "https://sam.example"}, "https://www.patreon.com/sam",
        {"url": "javascript:alert(1)"}, {"url": "http://plain.example"}, {"url": "https://sam.example"},
        {"url": "https://a b.example"}, {"url": "mailto:nobody"}, {"label": "Mail", "url": "mailto:sam@example.com"},
        42]}))[0]
    check("an author object: the name, and only the links that are links (no repeats)",
          (mixed["author"], mixed["creator"]),
          ("Sam Rivera", {"name": "Sam Rivera", "links": [
              {"label": "My site", "url": "https://sam.example", "kind": "website"},
              {"label": "Patreon", "url": "https://www.patreon.com/sam", "kind": "patreon"},
              {"label": "Mail", "url": "mailto:sam@example.com", "kind": "email"}]}))
    check("a long label is cut to 40",
          len(mk.check_link({"label": "x" * 80, "url": "https://a.example"})["label"]), 40)
    check("more than 8 links: the first 8",
          len(mk.check_entry(entry("pet", author={"name": "Sam", "links": [
              f"https://a{i}.example" for i in range(12)]}))[0]["creator"]["links"]), 8)
    check("an author object with no name is not a listing",
          mk.check_entry(entry("pet", author={"links": []}))[0], None)
    check("list.json's creator is read before the author",
          mk.check_entry(entry("pet", author="Sam", creator={"name": "Sam", "links": ["https://sam.example"]}))[0]["creator"]["links"][0]["kind"],
          "website")
    covers = {
        "cover.webp": "cover.webp", "art/cover-large.png": "art/cover-large.png",
        "https://pics.example/c.webp": "https://pics.example/c.webp",
        "../cover.webp": None, "/etc/passwd": None, "http://pics.example/c.webp": None,
        "cover.gif": None, "javascript:alert(1)": None, "https://": None, 42: None,
    }
    check("a cover is a picture in the folder or an https address",
          {str(k): mk.cover_value(k) for k in covers}, {str(k): v for k, v in covers.items()})
    check("a cover in the folder resolves against the link, at its tag",
          mk.check_entry(entry("pet", cover="art/c.webp"))[0]["cover_url"],
          "https://raw.githubusercontent.com/friend/loam-pet/v1.0.0/pet/art/c.webp")
    check("a readme path stays in the folder",
          [mk.is_readme_path(p) for p in ("README.md", "docs/About.markdown", "../README.md", "a/../b.md", "x.txt")],
          [True, True, False, False, False])

    # The README, made safe: no HTML, links to GitHub at the tag, pictures kept.
    at = {"repo": "friend/loam-pet", "ref": "v1.0.0", "path": "pet"}
    md, pics = mk.readme_markdown("\n".join([
        "# A pet",
        "<p align=\"center\"><img src=\"shots/one.png\" alt=\"The pet\" width=\"300\"></p>",
        "<!-- a note for maintainers -->",
        "<script>alert(1)</script> and <b>bold</b> &amp; more",
        "![sleeping](shots/two.webp) ![above](../other/x.png) ![remote](https://img.example/b.png)",
        "[the guide](docs/GUIDE.md), [root](/LICENSE), [up](../README.md), [out](https://example.com)",
        "[anchor](#usage) [bad](javascript:alert(1))",
        "<a href=\"https://friend.example\">my site</a><br>next",
        "`[not a link](x.md)`",
        "```html",
        "<img src=\"kept-as-code.png\">",
        "[code](y.md)",
        "```",
    ]), at)
    check("README: tags and comments are gone, their words stay",
          ("<" in md.split("```")[0], "a note for maintainers" in md, "alert(1) and bold & more" in md),
          (False, False, True))
    check("README: an <img> becomes its Markdown picture, kept from the folder",
          ("![The pet](shots/one.png)" in md, pics[:2]), (True, ["shots/one.png", "shots/two.webp"]))
    check("README: a picture outside the folder is GitHub's file; a remote one stays as written",
          ("![above](https://raw.githubusercontent.com/friend/loam-pet/v1.0.0/other/x.png)" in md,
           "![remote](https://img.example/b.png)" in md, len(pics)), (True, True, 2))
    check("README: relative links open on GitHub at the tag",
          ("[the guide](https://github.com/friend/loam-pet/blob/v1.0.0/pet/docs/GUIDE.md)" in md,
           "[root](https://github.com/friend/loam-pet/blob/v1.0.0/LICENSE)" in md,
           "[up](https://github.com/friend/loam-pet/blob/v1.0.0/README.md)" in md,
           "[out](https://example.com)" in md), (True, True, True, True))
    check("README: an anchor and a javascript: link are left as written (the page never follows them)",
          ("[anchor](#usage)" in md, "[bad](javascript:alert(1))" in md), (True, True))
    check("README: an <a> is a Markdown link; <br> a line break",
          "[my site](https://friend.example)\nnext" in md, True)
    check("README: code is kept as written, fenced and inline",
          ('<img src="kept-as-code.png">' in md, "[code](y.md)" in md, "`[not a link](x.md)`" in md),
          (True, True, True))
    shown, shown_pics = mk.readme_markdown("<!-- loam:hero -->\n**Its promise.**\n<!-- /loam:hero -->\n\nIntro.\n\n<!-- loam:gallery -->\n![one](shots/one.png)\n*It at work.*\n<!-- /loam:gallery -->\n\n## More", {**at, "path": ""})
    check("README: the gallery's own pictures are left to the gallery",
          (shown, shown_pics), ("Intro.\n\n## More", []))
    root_md, root_pics = mk.readme_markdown("![a](a.png) [b](b.md)", {**at, "path": ""})
    check("README of a plugin at the repository's root",
          (root_md, root_pics), ("![a](a.png) [b](https://github.com/friend/loam-pet/blob/v1.0.0/b.md)", ["a.png"]))
    check("a picture's type is read from its bytes",
          [mk.sniff_image(b) for b in (b"\x89PNG\r\n\x1a\n....", b"\xff\xd8\xff\xe0", b"RIFF0000WEBPVP8 ",
                                       b"\x00\x00\x00\x1cftypavif", b"<svg>", b"GIF89a")],
          ["image/png", "image/jpeg", "image/webp", "image/avif", None, None])
    check("a cover's address on this box or its LAN is never fetched",
          [mk.remote_allowed(u) for u in ("https://127.0.0.1/c.webp", "https://localhost/c.webp",
                                          "https://10.0.0.5/c.webp", "https://169.254.169.254/c.png",
                                          "http://example.com/c.webp", "https://[::1]/c.png")],
          [False] * 6)
    git = lambda *a, cwd=None: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)
    work = TMP / "dusk-src"
    (work / "dusk").mkdir(parents=True)
    (work / "dusk" / "PLUGIN.md").write_text(
        "---\nid: dusk\nname: Dusk\nversion: 1.2.0\napi: 0\nkind: [theme]\nauthor: Sam\n"
        "icon: icon.svg\ndescription: Warm lamplight for the evening.\n"
        "screenshots:\n  - {src: shots/one.png, src_dark: shots/two.webp, caption: A thread at dusk}\n"
        "  - {src: shots/one.png}\n"
        "palette:\n  - {token: --paper-page, name: Ember, name_dark: Coal}\n"
        "---\nA warm dark theme, for evenings.\n")
    (work / "dusk" / "fonts.css").write_text("@font-face { font-family: Dusk; src: url(d.woff2) format('woff2'); }\n")
    (work / "dusk" / "d.woff2").write_bytes(b"wOF2FONT")
    (work / "dusk" / "theme.css").write_text(':root[data-look="dusk"] { --paper-page: #222222; }\n')
    (work / "dusk" / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (work / "dusk" / "cover.webp").write_bytes(b"RIFF0000WEBPVP8 ")
    (work / "dusk" / "shots").mkdir()
    (work / "dusk" / "shots" / "one.png").write_bytes(b"\x89PNG\r\n\x1a\nPIXELS")
    (work / "dusk" / "shots" / "two.webp").write_bytes(b"RIFF0000WEBPVP8 DARK")
    (work / "dusk" / "README.md").write_text(
        "# Dusk\n\n<img src=\"shots/one.png\" alt=\"Dusk at work\">\n\n"
        "Read [the notes](NOTES.md). <script>alert(1)</script>\n")
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
          (about["listing"]["featured"], about["icon"], about["cover"], about["cover_dark"], about["theme"]),
          (True, True, True, False, {"options": [], "fonts": True,
                                     "palette": [{"token": "--paper-page", "name": "Ember", "name_dark": "Coal"}]}))
    check("details: its promise and its screenshots, each with its caption and dark pair",
          (about["description"], about["screenshots"]),
          ("Warm lamplight for the evening.", [{"caption": "A thread at dusk", "dark": True}, {"caption": "", "dark": False}]))
    check("its screenshots are served by number, light and dark",
          (mk.file("dusk", "shot/0"), mk.file("dusk", "shot/0-dark")[1], mk.file("dusk", "shot/1")[1]),
          ((b"\x89PNG\r\n\x1a\nPIXELS", "image/png"), "image/webp", "image/png"))
    check("a listed theme's fonts.css and its font, for its preview before install",
          (mk.file("dusk", "fonts.css")[1], mk.file("dusk", "font/d.woff2")), ("text/css; charset=utf-8", (b"wOF2FONT", "font/woff2")))
    check("details: the README at the tag, made safe, with the folder's picture kept",
          (about["readme"], about["readme_images"]),
          ("# Dusk\n\n![Dusk at work](shots/one.png)\n\nRead [the notes]"
           "(https://github.com/friend/loam-dusk/blob/v1.2.0/dusk/NOTES.md). alert(1)", ["shots/one.png"]))
    check("the README's picture is served by its path",
          mk.file("dusk", "readme/shots/one.png"), (b"\x89PNG\r\n\x1a\nPIXELS", "image/png"))
    check("the creator rides on the listing", about["listing"]["creator"], {"name": "Sam", "links": []})
    check("the theme's own CSS, for the swatch and Try it",
          mk.file("dusk", "theme.css"), (b':root[data-look="dusk"] { --paper-page: #222222; }\n',
                                         "text/css; charset=utf-8"))
    check("its icon and its cover, by type (image is the cover's older name)",
          (mk.file("dusk", "icon")[1], mk.file("dusk", "cover")[1], mk.file("dusk", "image")[1]),
          ("image/svg+xml", "image/webp", "image/webp"))
    # A listing's own covers: a file in the folder, and a dark one at an
    # address the broker fetches (the fetch is stood in for: this test's
    # host is on loopback, which a real cover may never be).
    fetched = []
    real_remote = mk._remote_image
    mk._remote_image = lambda url: fetched.append(url) or (b"\xff\xd8\xffJPEG", "image/jpeg")
    try:
        mine = mk._read_details({**mk.check_entry({**listed, "cover": "shots/one.png",
                                                    "cover_dark": "https://pics.example/dusk-dark.jpg"})[0]})
    finally:
        mk._remote_image = real_remote
    check("details: the listing's cover wins over the plugin's own",
          (mine["files"]["cover"][1], mine["files"]["cover-dark"], fetched),
          ("image/png", (b"\xff\xd8\xffJPEG", "image/jpeg"), ["https://pics.example/dusk-dark.jpg"]))
    nocover = mk._read_details(mk.check_entry({**listed, "cover": "missing.webp"})[0])
    check("details: a listed cover that is not there is no cover (not the plugin's)",
          (nocover["body"]["cover"], nocover["body"]["cover_dark"]), (False, False))
    # The fetch itself, against this test's host with the address check
    # stood in for: only an image's own bytes make a cover.
    ANSWERS["/cover.webp"] = (200, "RIFF0000WEBPVP8 ")
    ANSWERS["/fake.webp"] = (200, "<html>not a picture</html>")
    real_allowed = mk.remote_allowed
    mk.remote_allowed = lambda url: True
    try:
        check("a remote cover is fetched and typed by its bytes",
              (mk._remote_image(f"{BASE}/cover.webp"), mk._remote_image(f"{BASE}/fake.webp"),
               mk._remote_image(f"{BASE}/nothing.webp")),
              ((b"RIFF0000WEBPVP8 ", "image/webp"), None, None))
    finally:
        mk.remote_allowed = real_allowed
    check("and without the stand-in, this box's own address is refused",
          mk._remote_image(f"{BASE}/cover.webp"), None)
    check("nothing is installed by reading", (TMP / "plugins" / "dusk" / "theme.css").read_text().startswith(":root {"), True)
    # Confirm through the same entry point as the Market sheet. One asset GET
    # for a valid yes; an untrusted transport hint cannot change the reviewed tree.
    import loam_market_asset as asset
    commit = git("rev-parse", "HEAD", cwd=work).stdout.decode().strip()
    archive = git("-C", "dusk", "archive", "--format=tar", commit, cwd=work).stdout
    digest = hashlib.sha256(archive).hexdigest()
    metadata = {"tag": "v1.2.0", "sha256": digest}
    changed = archive.replace(b"Warm lamplight", b"Fake lamplight")
    assert changed != archive
    original_download = asset.download
    def download_here(descriptor, dest):
        return original_download({**descriptor, "url": BASE + "/asset"}, dest)
    expected = loam_plugins.fingerprint(work / "dusk")
    for case, advertised, answer, gets in (
            ("verified", metadata, (200, archive), 1), ("missing hint", None, (404, ""), 0),
            ("missing file", metadata, (404, ""), 1), ("wrong hash", metadata, (200, changed), 1),
            ("wrong tree", {**metadata, "sha256": hashlib.sha256(changed).hexdigest()}, (200, changed), 1)):
        candidate = {**listed, "review": {"commit": commit, "folder": "dusk", "note": {"summary": "Reviewed."}}}
        if advertised:
            candidate["asset"] = advertised
        ANSWERS["/list.json"] = 200, json.dumps({"version": 1, "plugins": [candidate], "stats": "stats.json"})
        ANSWERS["/stats.json"] = 200, json.dumps({"version": 1, "plugins": {"dusk": {"downloads": 3}}})
        ANSWERS["/asset"] = answer
        listing = mk.read(refresh=True)["plugins"][0]
        check(f"{case}: sibling totals", listing["downloads"], 3)
        request = {"url": listing["pinned_link"], "via": "market"}
        with patch.dict(os.environ, LOAM_PLUGINS_DIR=str(TMP / case), LOAM_PLUGIN_UNITS="0"), \
                patch.object(asset, "download", side_effect=download_here):
            hits = HITS.count("/asset")
            preview = loam_plugins.install(request)
            try:
                loam_plugins.install({**request, "confirm": "expired"})
            except loam_plugins.PluginError as exc:
                assert exc.status == 409
            check(f"{case}: preview and invalid consent leave count alone", HITS.count("/asset"), hits)
            result = loam_plugins.install({**request, "confirm": preview["confirm"]})
            check(f"{case}: installed the reviewed files", loam_plugins.fingerprint(TMP / case / "dusk"), expected)
            check(f"{case}: untouched and automatic updates available",
                  (result["installed"], result["plugin"]["edited"], result["plugin"]["auto_update"]), (True, False, True))
            check(f"{case}: release fetches", HITS.count("/asset") - hits, gets)
    ANSWERS["/list.json"] = 200, json.dumps({"version": 1, "plugins": [listed, entry("ghost")]})
    mk.read(refresh=True)
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
                "/v1/marketplace/dusk/icon", "/v1/marketplace/nobody", "/v1/marketplace/dusk/server",
                "/v1/marketplace/dusk/readme/shots/one.png", "/v1/marketplace/dusk/cover")]
        finally:
            tcp.close()

    got = asyncio.run(routes())
    check("broker: GET /v1/marketplace/<id> is the details",
          (got[0][0], json.loads(got[0][2])["name"]), (200, "Dusk"))
    check("broker: its theme.css and icon, by type",
          [(g[0], g[1]) for g in got[1:3]], [(200, "text/css; charset=utf-8"), (200, "image/svg+xml")])
    check("broker: a listing it does not have, and a file a listing has not, are 404",
          [g[0] for g in got[3:5]], [404, 404])
    check("broker: a README's picture at its path, and the cover",
          [(g[0], g[1]) for g in got[5:7]], [(200, "image/png"), (200, "image/webp")])
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

    # Run the page's own ranking code so tool search and page search agree.
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
                                "creator": {"name": "A friend", "links": []},
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
    ANSWERS["/list.json"] = (200, json.dumps({"version": 1, "plugins": [
        entry("pet", name="A pet", description="A small animal.", cover="art/pet.webp",
              author="A friend", creator={"name": "A friend", "links": [
                  {"label": "", "url": "https://ko-fi.com/friend"}]})]}))
    mk.read(refresh=True)
    rich = mk.search("animal")["results"][0]
    check("a result carries the creator's links, with kinds, and the cover's public address",
          (rich["creator"], rich["cover"]),
          ({"name": "A friend", "links": [{"label": "Ko-fi", "url": "https://ko-fi.com/friend", "kind": "kofi"}]},
           "https://raw.githubusercontent.com/friend/loam-pet/v1.0.0/pet/art/pet.webp"))
    cli_rich = subprocess.run([str(ROOT / "bin" / "loam-market"), "search", "animal"],
                              capture_output=True, text=True, timeout=60).stdout
    check("loam market search prints the links and the cover",
          ("A friend: Ko-fi https://ko-fi.com/friend" in cli_rich, "cover: https://raw.githubusercontent.com" in cli_rich),
          (True, True))
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
