#!/usr/bin/env python3
"""The plugin host (2026-11-12): lib/loam_plugins.py, its routes in
bin/session-broker, and its three settings through `bin/settings`.

  - the manifest reader: spec v0's own example reads as written; the YAML
    subset (block and flow lists and mappings, `- key:` items, quotes, block
    text, comments) reads as YAML would; everything outside it (tabs,
    anchors, duplicate keys, unclosed brackets, stray indentation) is a
    sentence naming the line; random text is never anything but a
    ManifestError
  - the manifest: every required field, every type, every enumerated value
    refused with a sentence; what spec v0 does not name kept as a warning
  - the layers: loam-ui's theme folders (manifest.json) and PLUGIN.md
    plugins in Shipped; Yours shadows Shipped by id; a broken Yours copy
    never does; a second shipped folder of one id, a folder named as no id,
    a folder with no PLUGIN.md, malformed YAML, an id that is not the
    folder's, an installed line whose folder is gone and an unreadable
    installed list are each a row (or an `error`) and never break the list
  - on and off: an extension's switch persists in installed.json; a
    theme-only plugin has none (409) and is on where Appearance chose it; a
    broken one has none; built here is off until turned on
  - install from a link (a real git remote on disk): a tag and a commit
    install, a branch and a short commit are refused with the commit to use,
    a missing tag or folder is a 404, a folder that is not a plugin a 422;
    the first call writes nothing; a wrong token is a 409 that writes
    nothing; a later version declaring more shows exactly what it adds and
    the earlier token no longer installs it; a tag moved between the two
    calls is a 409; replacing an edited copy says so
  - remove: Yours only; a shipped one is a 409; a theme in use goes back to
    Latte where no shipped copy of it remains
  - a theme's cover (image, image_position) and dark cover (image_dark,
    image_position_dark, same rules): kept as named fields when
    loam-ui's page would take them; a missing file, a path out of the
    folder, a URL, another type and a bad position each a warning and the
    field left out, never a broken theme
  - files: page/, assets/, theme.css, PLUGIN.md and a theme's covers, with their types, from
    the copy in force; server/, `..`, a link out of the folder and a
    plugin not in force are 404s
  - the routes on a loopback broker, as the page calls them; and the same
    three settings through `bin/settings` as a persona, with `by`, refused
    from a cron

Run: python3 tests/test_plugins.py
"""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
SHIPPED, SHIPPED2, YOURS = TMP / "shipped", TMP / "shipped-2", TMP / "plugins"
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
os.environ["LOAM_HOST_SETTINGS"] = str(TMP / "broker" / "settings.json")
os.environ["LOAM_PLUGIN_SHIPPED"] = f"{SHIPPED}:{SHIPPED2}:{TMP / 'absent'}"
os.environ.pop("LOAM_PLUGINS_DIR", None)       # read the default first, below
os.environ["LOAM_PLUGIN_GIT_BASE"] = f"file://{TMP / 'gh'}/"
# loam-ui's core-option table (public/theme-css.js, `coreOptions`), as that
# file writes it; the host reads it from there (loam_plugins.core_options).
CORE_TABLE = TMP / "theme-css.js"
CORE_TABLE.write_text("const slug = /^[a-z][a-z0-9-]*$/\nconst coreOptions = {\n"
                      "  'agent-colours': ['palette', 'latte-tones'],\n"
                      "  'agent-shapes': ['distinct', 'circle', 'rounded-square', "
                      "'latte-art'],\n}\n")
os.environ["LOAM_UI_CORE_OPTIONS"] = str(CORE_TABLE)
for who in ("ada", "iris"):
    (TMP / "ai-labs" / who).mkdir(parents=True)
    (TMP / "ai-labs" / who / "agent.json").write_text(
        json.dumps({"name": who, "display": who.capitalize()}))
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents" / "comms.md").write_text("# From agents\n")

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_threads as threads  # noqa: E402
import loam_plugins as P  # noqa: E402
import loam_appearance as appearance  # noqa: E402
import loam_settings as settings  # noqa: E402

cfg.roster = threads.roster
cfg.roster_cards = lambda: [{"id": p} for p in threads.roster()]

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:70}"
          f"{'  | ' + str(detail)[:300] if detail != '' else ''}")


def refuses(fn, says):
    try:
        fn()
    except (P.ManifestError, P.PluginError) as exc:
        text = getattr(exc, "message", str(exc))
        return says in text, text
    return False, "not refused"


# -- the manifest reader -----------------------------------------------------

SPEC_EXAMPLE = """---
id: read-aloud
name: Read aloud
version: 1.2.0
author: Loam
api: 0                      # the interface version it was written for
kind: [extension]           # theme, extension, or both
places:                     # the named places it fills (extensions)
  - message.footer.action
  - settings.section
overrides: []               # core components it replaces, by id
touches:                    # what it reaches; shown before install
  network:
    - host: generativelanguage.googleapis.com
      why: sends the text of a reply to be turned into speech
  record: read              # none, read, write, or bypass
  memory: none              # agents' workspaces: none, read, write
  server: true              # it runs code on the machine
settings: settings.md       # where its own settings are kept, in the user's layer
---

# Read aloud

How it works.
"""
data, body = P.parse_frontmatter(SPEC_EXAMPLE)
check("spec v0's example manifest reads as written", data == {
    "id": "read-aloud", "name": "Read aloud", "version": "1.2.0", "author": "Loam",
    "api": 0, "kind": ["extension"],
    "places": ["message.footer.action", "settings.section"], "overrides": [],
    "touches": {"network": [{"host": "generativelanguage.googleapis.com",
                             "why": "sends the text of a reply to be turned into speech"}],
                "record": "read", "memory": "none", "server": True},
    "settings": "settings.md"}, data)
check("the body after the frontmatter is the guide",
      body == "# Read aloud\n\nHow it works.\n", repr(body))
m, w = P.manifest_of(data, "read-aloud")
check("the spec's example is a valid manifest with no warnings", not w, w)

subset = P.parse_yaml(
    "a:\n- x\n- 'it''s'\nb:\n  - c: 1\n    d: \"q: # not a comment\"\n  -\n    e: ~\n"
    "  - - n1\n    - n2\nf: [one, \"two, three\", {k: v}]\ng: {x: [1, 2], y: null}\n"
    "h: |\n  line # kept\n    indented\n\n  after\ni: >\n  folded\n  text\n"
    "j: true\nk: -3\nl: 1.2\nm:\nn: 'x'  # comment\no: [a,\n  b]\n")
check("the YAML subset reads as YAML would", subset == {
    "a": ["x", "it's"], "b": [{"c": 1, "d": "q: # not a comment"}, {"e": None},
                              ["n1", "n2"]],
    "f": ["one", "two, three", {"k": "v"}], "g": {"x": [1, 2], "y": None},
    "h": "line # kept\n  indented\n\nafter\n", "i": "folded text\n", "j": True,
    "k": -3, "l": "1.2", "m": None, "n": "x", "o": ["a", "b"]}, subset)
check("CRLF line ends and a byte-order mark are read",
      P.parse_frontmatter("\ufeff---\r\nid: x\r\n---\r\nbody")[0] == {"id": "x"})

for text, says in (
        ("id: x\n", "does not begin with a `---`"),
        ("---\nid: x\n", "no closing `---`"),
        ("---\n- a\n---\n", "not a mapping"),
        ("---\nid: x\nid: y\n---\n", "line 3: 'id' is given twice"),
        ("---\na: &anchor 1\n---\n", "line 2: '&'"),
        ("---\na: *alias\n---\n", "line 2: '*'"),
        ("---\na: !tag x\n---\n", "line 2: '!'"),
        ("---\na:\n\t- b\n---\n", "line 3: a tab"),
        ("---\na: [1, 2\n---\n", "never closes"),
        ("---\na: b\n  c: d\n---\n", "line 3: indented"),
        ("---\na: b: c\n---\n", "quote the text"),
        ("---\na: \"x\" y\n---\n", "text after a closing quote"),
        ("---\na: \"unterminated\n---\n", "never closes"),
        ("---\n? complex\n---\n", "complex keys"),
        ("---\nplaces:\n  - a\n  b: c\n---\n", "line 4: indented"),
        ("---\na: |x\n---\n", "block text")):
    ok, said = refuses(lambda t=text: P.parse_frontmatter(t), says)
    check(f"refused with its line: {text[4:30]!r}", ok, said)

rng = random.Random(1112)
alphabet = list("ab:- \n\t#'\"[]{},|>&*!?~0123456789\\") + ["\n  ", "\n- ", ": ", "---\n"]
odd = []
for _ in range(3000):
    text = "---\n" + "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60))) \
        + "\n---\n"
    try:
        P.parse_frontmatter(text)
    except P.ManifestError:
        pass
    except Exception as exc:  # noqa: BLE001
        odd.append((text, repr(exc)))
check("3000 random frontmatters: each reads or is a ManifestError, nothing else",
      not odd, odd[:2])


# -- the manifest -------------------------------------------------------------

BASE = {"id": "pet", "name": "A pet", "version": "1.0.0", "api": 0,
        "kind": ["extension"]}
for change, says in (
        ({"id": None}, "`id` is lower-case"), ({"id": "Pet"}, "`id` is lower-case"),
        ({"id": "1pet"}, "`id` is lower-case"), ({"name": ""}, "`name` is missing"),
        ({"version": "1.0"}, "major.minor.patch"), ({"version": None}, "major.minor.patch"),
        ({"api": None}, "there is none"), ({"api": "0"}, "`api` is the interface"),
        ({"api": True}, "`api` is the interface"), ({"kind": None}, "theme, extension, app, agent or skill"),
        ({"kind": ["widget"]}, "not 'widget'"), ({"places": [1]}, "`places` is a list"),
        ({"overrides": {"a": 1}}, "`overrides` is a list"),
        ({"touches": "all"}, "`touches` is a mapping"),
        ({"touches": {"record": "everything"}}, "`touches.record` is one of"),
        ({"touches": {"memory": "bypass"}}, "`touches.memory` is one of"),
        ({"touches": {"server": "yes"}}, "`touches.server` is true or false"),
        ({"touches": {"network": "example.com"}}, "`touches.network` is a list"),
        ({"touches": {"network": [{"why": "x"}]}}, "names a `host`"),
        ({"settings": "../x.md"}, "a path inside the plugin"),
        ({"forked_from": "read-aloud"}, "id@version"),
        ({"author": ""}, "`author` is a name")):
    ok, said = refuses(lambda c=change: P.manifest_of({**BASE, **c}, "pet"), says)
    check(f"manifest refused: {json.dumps(change)[:44]}", ok, said)
ok, said = refuses(lambda: P.manifest_of({**BASE, "id": "cat"}, "pet"),
                   "a plugin's folder is named by its id")
check("an id that is not its folder's name is refused", ok, said)
m, w = P.manifest_of({**BASE, "kind": "extension", "api": 3, "places": ["dock.tile"],
                      "overrides": ["thread-list"], "colour": "red",
                      "touches": {"network": ["example.com"], "camera": True}}, "pet")
check("what spec v0 does not name is kept, each with a warning",
      m["kind"] == ["extension"] and m["extra"] == {"colour": "red"}
      and m["touches"]["camera"] is True
      and m["touches"]["network"] == [{"host": "example.com", "why": None}]
      and len(w) == 6, w)
check("defaults where a manifest is silent",
      P.manifest_of(BASE, "pet")[0]["touches"]
      == {"network": [], "record": "none", "memory": "none", "server": False})


# -- the layers ---------------------------------------------------------------

def legacy(root, tid, mid=None, **over):
    (root / tid).mkdir(parents=True, exist_ok=True)
    (root / tid / "manifest.json").write_text(json.dumps({
        "schemaVersion": 1, "id": mid or tid, "name": tid.capitalize(),
        "version": "1.0.0", "author": "Loam contributors",
        "modes": ["light", "dark"], "stylesheet": "theme.css", **over}))
    (root / tid / "theme.css").write_text(f":root[data-look=\"{tid}\"] {{}}\n")


def plugin(root, folder, front=None, body="# Guide\n", files=None, raw=None):
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    front = {"id": folder, "name": folder.capitalize(), "version": "1.0.0", "api": 0,
             "kind": ["extension"], **(front or {})}
    text = raw if raw is not None else "---\n" + "".join(
        f"{k}: {json.dumps(v)}\n" for k, v in front.items()) + "---\n" + body
    (d / "PLUGIN.md").write_text(text)
    for rel, content in (files or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(content)
    return d


legacy(SHIPPED, "latte")
legacy(SHIPPED, "plain")
legacy(SHIPPED, "mocha", mid="espresso")                 # broken: misnamed
legacy(SHIPPED, "dusk", modes=["light"])                 # broken: page rule
plugin(SHIPPED, "read-aloud", {"places": ["message.footer.action"],
                               "touches": {"network": [{"host": "tts.example",
                                                        "why": "speech"}]}},
       files={"page/main.js": "export function activate(loam) {}\n"})
plugin(SHIPPED, "solar", {"kind": ["theme"]}, files={"theme.css": ":root{}\n"})
plugin(SHIPPED2, "solar", {"kind": ["theme"], "version": "9.9.9"},
       files={"theme.css": ":root{}\n"})                 # second shipped: broken
(SHIPPED / "Bad Name").mkdir()
(SHIPPED / "README.md").write_text("not a plugin")
(SHIPPED / ".hidden").mkdir()
plugin(YOURS, "latte", {"kind": ["theme"], "version": "1.1.0",
                        "forked_from": "latte@1.0.0"},
       files={"theme.css": ":root[data-look=\"latte\"] {}\n"})
plugin(YOURS, "plain", raw="---\nid: plain\nname: [unclosed\n---\n")   # broken
plugin(YOURS, "notes", files={"page/main.js": "export const x = 1\n"})
plugin(YOURS, "midnight", {"kind": ["theme"]}, files={"theme.css": ":root{}\n"})
plugin(YOURS, "wrong", {"id": "other"})
(YOURS / "empty").mkdir()


def rows(pid=None):
    found = P.listing()["plugins"]
    return [r for r in found if pid is None or r["id"] == pid]


def row(pid, layer):
    return next(r for r in rows(pid) if r["layer"] == layer)


check("Yours defaults to its own folder beside the vault and ai-labs",
      P.yours_root() == cfg.lc.PLUGINS, P.yours_root())
import loam_files  # noqa: E402
check("...which is no Files root and in neither the vault nor ai-labs",
      not any(P.YOURS_DEFAULT.is_relative_to(loam_files.root_path(r))
              for r in ("vault", "spaces"))
      and not P.YOURS_DEFAULT.is_relative_to(cfg.lc.VAULT)
      and not P.YOURS_DEFAULT.is_relative_to(cfg.lc.WORKSPACES))
os.environ["LOAM_PLUGINS_DIR"] = str(YOURS)
check("LOAM_PLUGINS_DIR still overrides it", P.yours_root() == YOURS)
listing = P.listing()
check("the list names both layers' folders",
      listing["layers"] == {"shipped": [str(SHIPPED), str(SHIPPED2), str(TMP / "absent")],
                            "yours": str(YOURS)}, listing["layers"])
check("every folder of both layers is a row, dot-folders and files aside",
      sorted((r["layer"], r["id"]) for r in listing["plugins"]) == sorted([
          ("shipped", "Bad Name"), ("shipped", "dusk"), ("shipped", "latte"),
          ("shipped", "mocha"), ("shipped", "plain"), ("shipped", "read-aloud"),
          ("shipped", "solar"), ("shipped", "solar"), ("yours", "empty"),
          ("yours", "latte"), ("yours", "midnight"), ("yours", "notes"),
          ("yours", "plain"), ("yours", "wrong")]),
      [(r["layer"], r["id"]) for r in listing["plugins"]])
broken = {(r["layer"], r["id"]): r["broken"] for r in listing["plugins"] if r["broken"]}
for key, says in ((("shipped", "mocha"), "manifest.json's id is 'espresso'"),
                  (("shipped", "dusk"), "modes are not [light, dark]"),
                  (("shipped", "Bad Name"), "is not an id"),
                  (("yours", "plain"), "never closes"),
                  (("yours", "wrong"), "named by its id"),
                  (("yours", "empty"), "no PLUGIN.md")):
    check(f"broken, with its reason: {key[0]} {key[1]}",
          says in (broken.get(key) or ""), broken.get(key))
solar = rows("solar")
check("a second shipped folder of one id is broken; the first is in force",
      solar[0]["effective"] and solar[0]["version"] == "1.0.0"
      and "another shipped folder" in (solar[1]["broken"] or ""), solar)
lat_y, lat_s = row("latte", "yours"), row("latte", "shipped")
check("a Yours plugin with a shipped id wins, and says what it shadows",
      lat_y["effective"] and not lat_s["effective"]
      and lat_y["shadows"] == {"layer": "shipped", "version": "1.0.0",
                               "folder": str(SHIPPED / "latte")}
      and lat_s["shadowed_by"] == "yours" and lat_y["forked_from"] == "latte@1.0.0",
      (lat_y, lat_s))
pl_y, pl_s = row("plain", "yours"), row("plain", "shipped")
check("a broken Yours copy never wins: the shipped one stays in force",
      pl_s["effective"] and not pl_y["effective"] and pl_s["shadowed_by"] is None,
      (pl_y, pl_s))
check("loam-ui's theme folder reads as a theme plugin from Loam",
      lat_s["kind"] == ["theme"] and lat_s["format"] == "manifest.json"
      and lat_s["source"] == {"kind": "loam"} and lat_s["api"] == 0
      and lat_s["author"] == "Loam contributors", lat_s)
ra = row("read-aloud", "shipped")
check("a shipped extension: on by default, its declaration and its files path",
      ra["on"] is True and ra["switch"] == "plugin"
      and ra["declaration"] == {"touches": {"network": [{"host": "tts.example",
                                                         "why": "speech"}],
                                            "record": "none", "memory": "none",
                                            "server": False},
                                "overrides": [], "page_code": True}
      and ra["files"] == "/v1/plugins/read-aloud/files/", ra)
notes = row("notes", "yours")
check("a plugin built here: source built, off until turned on",
      notes["source"] == {"kind": "built"} and notes["on"] is False, notes)
check("themes(): every theme in force, Latte first, the broken left out",
      P.themes() == ["latte", "midnight", "plain", "solar"]
      and appearance.themes() == P.themes(), P.themes())

(YOURS / P.LIST_FILE).write_text("{not json")
listing = P.listing()
check("an unreadable installed list: the list still draws, and says why",
      len(listing["plugins"]) == 14 and "cannot be read" in listing.get("error", ""),
      listing.get("error"))
ok, said = refuses(lambda: P.set_on("notes", True), "nothing is written over it")
check("...and a write is refused rather than overwrite it", ok, said)
(YOURS / P.LIST_FILE).unlink()


# -- on and off ---------------------------------------------------------------

check("a switch nobody has set is unchosen, on or off",
      row("notes", "yours")["chosen"] is False and row("read-aloud", "shipped")["chosen"] is False)
P.set_on("notes", True)
check("turn on a plugin built here: on, and in installed.json",
      row("notes", "yours")["on"] is True
      and json.loads((YOURS / P.LIST_FILE).read_text())["on"] == {"notes": True})
check("the first write leaves a README and a .gitignore for the staging folder",
      (YOURS / "README.md").is_file()
      and (YOURS / ".gitignore").read_text() == ".staging/\n")
P.set_on("read-aloud", False)
check("turn off a shipped extension: off", row("read-aloud", "shipped")["on"] is False)
check("a switch once set is chosen, whichever way (first setup's backup offer reads it)",
      row("notes", "yours")["chosen"] is True and row("read-aloud", "shipped")["chosen"] is True)
ok, said = refuses(lambda: P.set_on("midnight", True), "it is on where Appearance")
check("a theme has no switch of its own: 409, pointing at Appearance", ok, said)
ok, said = refuses(lambda: P.set_on("dusk", True), "dusk is broken")
check("a broken plugin cannot be switched", ok, said)
ok, said = refuses(lambda: P.set_on("ghost", True), "no plugin 'ghost'")
check("an unknown id is a 404", ok, said)
ok, said = refuses(lambda: P.set_on("notes", "yes"), "`on` is true or false")
check("`on` is a boolean", ok, said)
appearance.update({"desktop": {"theme": "midnight"}})
mid = row("midnight", "yours")
check("a theme is on where Appearance chose it: one truth",
      mid["on"] is True and mid["in_use"] == ["desktop"] and mid["switch"] == "appearance"
      and row("latte", "yours")["in_use"] == ["phone"], (mid, row("latte", "yours")))


# -- install from a link --------------------------------------------------------

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, check=True,
                          capture_output=True, text=True).stdout.strip()


WORK = TMP / "src"
WORK.mkdir()
git("init", "-q", "-b", "main", cwd=WORK)
plugin(WORK / "plugins", "pet-folder", raw="""---
id: pet
name: A pet
version: 1.0.0
author: A friend
api: 0
kind: [extension]
places: [app.overlay]
touches:
  network:
    - host: pets.example
      why: fetches a sprite
---
# A pet
""", files={"page/main.js": "export function activate(loam) {}\n",
            "assets/cat.svg": "<svg/>"})
plugin(WORK / "plugins", "read-aloud", {"version": "1.3.0"},
       files={"page/main.js": "export function activate() {}\n"})
(WORK / "not-a-plugin").mkdir()
(WORK / "not-a-plugin" / "README.md").write_text("hi")
git("add", "-A", cwd=WORK)
git("commit", "-q", "-m", "v1", cwd=WORK)
git("tag", "v1.0.0", cwd=WORK)
C1 = git("rev-parse", "HEAD", cwd=WORK)
text = (WORK / "plugins" / "pet-folder" / "PLUGIN.md").read_text()
(WORK / "plugins" / "pet-folder" / "PLUGIN.md").write_text(
    text.replace("version: 1.0.0", "version: 2.0.0").replace(
        "      why: fetches a sprite\n",
        "      why: fetches a sprite\n    - host: tracker.example\n      why: counts pets\n"
        "  memory: read\n"))
(WORK / "plugins" / "pet-folder" / "server").mkdir()
(WORK / "plugins" / "pet-folder" / "server" / "run.py").write_text("print(1)\n")
git("add", "-A", cwd=WORK)
git("commit", "-q", "-m", "v2", cwd=WORK)
git("tag", "-a", "v2.0.0", "-m", "two", cwd=WORK)      # an annotated tag
C2 = git("rev-parse", "HEAD", cwd=WORK)
BARE = TMP / "gh" / "friend" / "loam-pet"
BARE.parent.mkdir(parents=True)
subprocess.run(["git", "clone", "-q", "--bare", str(WORK), str(BARE)], check=True,
               env=GIT_ENV)
git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=BARE)
LINK = "https://github.com/friend/loam-pet/tree/v1.0.0/plugins/pet-folder"

for body, says in (
        ({}, "Paste a GitHub link"),
        ({"url": "https://gitlab.com/a/b"}, "That isn’t a GitHub link"),
        ({"url": "not a link at all"}, "That isn’t a GitHub link"),
        ({"url": f"https://github.com/friend/loam-pet/tree/{C1[:8]}/plugins"},
         "names a short commit"),
        ({"url": "https://github.com/friend/loam-pet/tree/v9/plugins"},
         "has no release or branch called v9"),
        ({"url": "https://github.com/friend/nothing/tree/v1.0.0"},
         "Nothing could be read at github.com/friend/nothing"),
        ({"url": "https://github.com/friend/nothing"}, "connect GitHub in Settings"),
        ({"url": "https://github.com/friend/loam-pet/tree/v1.0.0/plugins/nope"},
         "has no folder plugins/nope"),
        ({"url": "https://github.com/friend/loam-pet/tree/v1.0.0/not-a-plugin"},
         "There’s no Loam plugin in not-a-plugin"),
        ({"url": "https://github.com/friend/loam-pet/tree/v1.0.0/../x"},
         "isn’t a path inside the repository")):
    ok, said = refuses(lambda b=body: P.install(b), says)
    check(f"install refused: {json.dumps(body)[23:80] or '{}'}", ok, said)

# -- every shape a person pastes (2026-11-18) ---------------------------------
# What the link leaves out is worked out: the newest release, the plugin's
# folder, or the plugins to pick from; a branch is its commit now, said in a
# line. Only first calls here: nothing is installed.
git("branch", "feature/x", C1, cwd=BARE)


def bare(name, build):
    work = TMP / "src-shapes" / name
    work.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=work)
    tags = build(work) or []
    out = TMP / "gh" / "friend" / name
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(out)], check=True,
                   env=GIT_ENV)
    git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=out)
    return work


def mono(work):
    plugin(work, "autumn", {"kind": ["theme"]}, files={"theme.css": ":root{}\n"})
    plugin(work, "foundry", {"name": "Foundry"})
    git("add", "-A", cwd=work); git("commit", "-q", "-m", "1", cwd=work)
    git("tag", "autumn-v1.0.0", cwd=work); git("tag", "foundry-v1.0.0", cwd=work)
    (work / "autumn" / "PLUGIN.md").write_text(
        (work / "autumn" / "PLUGIN.md").read_text().replace('"1.0.0"', '"1.0.1"'))
    git("commit", "-qam", "2", cwd=work); git("tag", "-a", "autumn-v1.0.1", "-m", "x", cwd=work)
    (work / "foundry" / "PLUGIN.md").write_text(
        (work / "foundry" / "PLUGIN.md").read_text().replace('"1.0.0"', '"9.9.9"'))
    git("commit", "-qam", "unreleased", cwd=work)


def solo(work):
    plugin(work.parent, work.name, {"id": "lantern", "name": "Lantern"})
    (work / "README.md").write_text("# Lantern\n")
    git("add", "-A", cwd=work); git("commit", "-q", "-m", "1", cwd=work)


def folder_one(work):
    plugin(work / "src", "kite", {"version": "0.9.0"})
    git("add", "-A", cwd=work); git("commit", "-q", "-m", "1", cwd=work)
    git("tag", "v0.9.0", cwd=work)
    (work / "src" / "kite" / "PLUGIN.md").write_text(
        (work / "src" / "kite" / "PLUGIN.md").read_text().replace("0.9.0", "1.10.0"))
    git("commit", "-qam", "2", cwd=work); git("tag", "v1.10.0", cwd=work)
    (work / "src" / "kite" / "PLUGIN.md").write_text(
        (work / "src" / "kite" / "PLUGIN.md").read_text().replace("1.10.0", "1.2.0"))
    git("commit", "-qam", "3", cwd=work); git("tag", "v1.2.0", cwd=work)


def broken(work):
    (work / "PLUGIN.md").write_text("---\nid: [unclosed\n---\n")
    git("add", "-A", cwd=work); git("commit", "-q", "-m", "1", cwd=work)


bare("mono", mono)
SOLO = bare("solo", solo)
bare("kite", folder_one)
bare("broken", broken)
SOLO_HEAD = git("rev-parse", "HEAD", cwd=SOLO)
PET = ("plugins/pet-folder", "plugins/read-aloud")


def same(label, got, want):
    check(label, got == want, f"{got!r} != {want!r}" if got != want else "")


def shape(url, **more):
    return P.install({"url": url, **more})


def picks(out):
    return tuple(c["path"] for c in out.get("choose", []))


def at(out):
    return (out.get("id"), out.get("version"), (out.get("source") or {}).get("ref"),
            (out.get("source") or {}).get("path"))


for url, want, test in (
        ("friend/loam-pet", PET, picks),
        ("https://github.com/friend/loam-pet", PET, picks),
        ("github.com/friend/loam-pet/", PET, picks),
        ("https://github.com/friend/loam-pet.git", PET, picks),
        ("git@github.com:friend/loam-pet.git", PET, picks),
        ("https://github.com/friend/loam-pet?tab=readme-ov-file#install", PET, picks),
        ("https://github.com/friend/loam-pet/releases", PET, picks),
        ("https://github.com/friend/loam-pet/releases/tag/v2.0.0", PET, picks),
        ("https://github.com/friend/loam-pet/tree/v1.0.0/plugins/pet-folder/?x=1",
         ("pet", "1.0.0", "v1.0.0", "plugins/pet-folder"), at),
        ("https://github.com/friend/loam-pet/blob/v1.0.0/plugins/pet-folder/PLUGIN.md",
         ("pet", "1.0.0", "v1.0.0", "plugins/pet-folder"), at),
        ("https://raw.githubusercontent.com/friend/loam-pet/v1.0.0/plugins/pet-folder/PLUGIN.md",
         ("pet", "1.0.0", "v1.0.0", "plugins/pet-folder"), at),
        ("https://github.com/friend/loam-pet/tree/main/plugins/pet-folder",
         ("pet", "2.0.0", C2, "plugins/pet-folder"), at),
        ("https://github.com/friend/loam-pet/tree/feature/x/plugins/pet-folder",
         ("pet", "1.0.0", C1, "plugins/pet-folder"), at),
        ("https://github.com/friend/mono", ("autumn", "foundry"), picks),
        ("https://github.com/friend/mono/tree/foundry-v1.0.0",
         ("foundry", "1.0.0", "foundry-v1.0.0", "foundry"), at),
        ("https://github.com/friend/mono/releases/tag/autumn-v1.0.1",
         ("autumn", "1.0.1", "autumn-v1.0.1", "autumn"), at),
        ("https://github.com/friend/kite", ("kite", "1.10.0", "v1.10.0", "src/kite"), at),
        ("friend/solo", ("lantern", "1.0.0", SOLO_HEAD, ""), at),
        # A file's link under /tree/ (GitHub serves both): its folder.
        ("https://github.com/friend/solo/tree/main/README.md",
         ("lantern", "1.0.0", SOLO_HEAD, ""), at),
        ("https://github.com/friend/loam-pet/tree/v1.0.0/plugins/pet-folder/PLUGIN.md",
         ("pet", "1.0.0", "v1.0.0", "plugins/pet-folder"), at),
        ("friend/loam-pet/tree/v1.0.0/plugins/pet-folder",
         ("pet", "1.0.0", "v1.0.0", "plugins/pet-folder"), at)):
    out = shape(url)
    same(f"a pasted link: {url[:70]}", test(out), want)
same("a picked one is its own request: the newest release of that folder",
      at(shape("friend/mono", path="autumn")), ("autumn", "1.0.1", "autumn-v1.0.1", "autumn"))
same("...each folder its own release, never another's",
      at(shape("friend/mono", path="foundry")), ("foundry", "1.0.0", "foundry-v1.0.0", "foundry"))
out = shape("https://github.com/friend/loam-pet/tree/main/plugins/pet-folder")
same("a branch: its commit now, said in a line, and the request names the commit",
      ("is a branch" in (out["note"] or "") and C2[:7] in out["note"],
       out["source"]["ref_kind"], out["request"]),
      (True, "commit", {"url": "friend/loam-pet", "path": "plugins/pet-folder", "ref": C2}))
out = shape("friend/solo")
same("no release at all: the default branch's commit, said in a line",
      ("no release yet" in (out["note"] or ""), out["source"]["ref_kind"]), (True, "commit"))
out = shape("friend/loam-pet")
same("a pick names each plugin with its id, name and version",
      [(c["id"], c["version"]) for c in out["choose"]], [("pet", "2.0.0"), ("read-aloud", "1.3.0")])
ok, said = refuses(lambda: shape("friend/loam-pet", confirm="0" * 16), "pick one first")
check("...and a yes to a repository of several installs nothing", ok, said)
try:
    shape("friend/broken")
except P.PluginError as exc:
    same("a broken manifest: a plain sentence, reason manifest",
          (exc.detail.get("reason"), "can’t read" in exc.message), ("manifest", True))
for url, reason in (("https://gitlab.com/a/b", "not-github"), ("friend/nothing", "unreachable"),
                    ("https://github.com/friend/loam-pet/tree/v1.0.0/not-a-plugin", "no-plugin")):
    try:
        shape(url)
        check(f"reason {reason}", "not refused", reason)
    except P.PluginError as exc:
        same(f"a refusal carries its reason: {reason}", exc.detail.get("reason"), reason)
same("the newest tag is by version, not by text",
      (P.newest_tag(["v1.9.0", "v1.10.0", "v1.2.0", "v2.0.0-beta"]),
       P.newest_tag(["a-v3.0.0", "b-v1.0.0", "v0.1.0"], ["b"]),
       P.newest_tag(["a-v3.0.0"], ["b"])), ("v1.10.0", "b-v1.0.0", None))

before = sorted(p.name for p in YOURS.iterdir())
preview = P.install({"url": LINK}, "ada")
check("the first call answers what it is and declares, with a token",
      preview["installed"] is False and preview["id"] == "pet"
      and preview["version"] == "1.0.0" and len(preview["confirm"]) == 16
      and preview["declaration"] == {
          "touches": {"network": [{"host": "pets.example", "why": "fetches a sprite"}],
                      "record": "none", "memory": "none", "server": False},
          "overrides": [], "page_code": True}
      and preview["source"] == {"kind": "link", "repo": "friend/loam-pet",
                                "path": "plugins/pet-folder", "ref": "v1.0.0",
                                "ref_kind": "tag", "commit": C1,
                                "url": f"https://github.com/friend/loam-pet/tree/{C1}"
                                       f"/plugins/pet-folder"}
      and preview["added"] == {} and preview["replaces"] is None, preview)
check("...and writes nothing (the staging folder is left empty)",
      sorted(p.name for p in YOURS.iterdir()) == before
      and not any((YOURS / ".staging").iterdir()), sorted(p.name for p in YOURS.iterdir()))
ok, said = refuses(lambda: P.install({"url": LINK, "confirm": "0" * 16}),
                   "Nothing was installed")
check("a token that is not the answer's: 409, nothing installed",
      ok and not (YOURS / "pet").exists(), said)
try:
    P.install({"url": LINK, "confirm": "0" * 16})
except P.PluginError as exc:
    check("...and the 409 carries the new answer to confirm",
          exc.status == 409 and exc.detail["preview"]["confirm"] == preview["confirm"],
          exc.detail)
done = P.install({"url": LINK, "confirm": preview["confirm"]}, "ada")
pet = row("pet", "yours")
line = json.loads((YOURS / P.LIST_FILE).read_text())["plugins"]["pet"]
check("the second call installs it, on, from a link",
      done["installed"] and pet["effective"] and pet["on"] is True
      and pet["source"]["commit"] == C1 and pet["edited"] is False
      and (YOURS / "pet" / "page" / "main.js").is_file()
      and not (YOURS / "pet" / ".git").exists(), pet)
check("the installed list records source, ref, commit, the declaration and who",
      line["source"]["ref"] == "v1.0.0" and line["source"]["commit"] == C1
      and line["declaration"] == preview["declaration"]
      and line["confirmed"] == preview["confirm"] and line["installed_by"] == "ada"
      and line["version"] == "1.0.0" and line["files"], line)

by_commit = P.install({"url": f"https://github.com/friend/loam-pet/commit/{C1}",
                       "path": "plugins/pet-folder"})
check("a commit link with `path` beside it: the same plugin, ref_kind commit",
      by_commit["source"]["ref_kind"] == "commit" and by_commit["source"]["commit"] == C1
      and by_commit["replaces"]["version"] == "1.0.0"
      and by_commit["replaces"]["edited"] is False, by_commit)
short = P.install({"url": "friend/loam-pet", "ref": "v1.0.0", "path": "plugins/pet-folder"})
check("owner/repo with ref and path is the same link",
      short["source"]["commit"] == C1, short["source"])

V2 = LINK.replace("v1.0.0", "v2.0.0")
up = P.install({"url": V2})
check("a later version that declares more: the answer names exactly what it adds",
      up["version"] == "2.0.0" and up["source"]["commit"] == C2
      and up["added"] == {"network": [{"host": "tracker.example", "why": "counts pets"}],
                          "memory": "read", "server": True}, up["added"])
ok, said = refuses(lambda: P.install({"url": V2, "confirm": preview["confirm"]}),
                   "Nothing was installed")
check("...and the earlier yes does not install it", ok and row("pet", "yours")
      ["version"] == "1.0.0", said)
(YOURS / "pet" / "page" / "main.js").write_text("// my change\n")
check("an installed plugin edited since: `edited`", row("pet", "yours")["edited"] is True)
up = P.install({"url": V2})
check("...and replacing it says so before the yes",
      up["replaces"]["edited"] is True and up["replaces"]["version"] == "1.0.0", up)
# The tag moves between the two calls: the yes was to another commit.
git("tag", "-f", "v2.0.0", C1, cwd=BARE)
ok, said = refuses(lambda: P.install({"url": V2, "confirm": up["confirm"]}),
                   "Nothing was installed")
check("a tag moved between the two calls: 409, nothing installed",
      ok and row("pet", "yours")["version"] == "1.0.0", said)
git("tag", "-f", "-a", "v2.0.0", "-m", "two", C2, cwd=BARE)
up = P.install({"url": V2})
done = P.install({"url": V2, "confirm": up["confirm"]}, "iris")
pet = row("pet", "yours")
check("the update installs on its own yes: version 2, server/ listed, not run",
      pet["version"] == "2.0.0" and pet["has"]["server"] and pet["edited"] is False
      and pet["declaration"]["touches"]["server"] is True
      and "does not declare touches.server" in " ".join(pet["warnings"]), pet)
check("on/off survives an update (it was on)", pet["on"] is True)

over = P.install({"url": "https://github.com/friend/loam-pet/tree/v1.0.0/plugins/read-aloud"})
check("installing a shipped plugin's id from a link: the answer says it shadows it",
      over["id"] == "read-aloud" and over["replaces"] is None
      and over["shadows"] == {"layer": "shipped", "version": "1.0.0"}, over)
P.install({"url": "https://github.com/friend/loam-pet/tree/v1.0.0/plugins/read-aloud",
           "confirm": over["confirm"]})
check("...and once installed it is the one in force, on though the shipped one "
      "was off, the shipped one shadowed",
      row("read-aloud", "yours")["effective"] and row("read-aloud", "yours")["on"] is True
      and row("read-aloud", "yours")["shadows"]["version"] == "1.0.0"
      and row("read-aloud", "shipped")["shadowed_by"] == "yours")
gone = P.remove("read-aloud")
check("removing that copy puts the shipped one back in force, its switch kept",
      gone["plugin"]["layer"] == "shipped" and gone["plugin"]["on"] is True
      and json.loads((YOURS / P.LIST_FILE).read_text())["on"]["read-aloud"] is True,
      gone)
P.set_on("read-aloud", False)


# -- remove -------------------------------------------------------------------

ok, said = refuses(lambda: P.remove("read-aloud"), "turn it off instead")
check("a shipped plugin cannot be removed: turn it off", ok, said)
ok, said = refuses(lambda: P.remove("ghost"), "no plugin 'ghost'")
check("removing an unknown id: 404", ok, said)
gone = P.remove("midnight")
check("removing a theme in use sets that device kind back to Latte",
      gone["appearance_reset"] == ["desktop"] and not (YOURS / "midnight").exists()
      and appearance.read()["desktop"]["theme"] == "latte", gone)
appearance.update({"desktop": {"theme": "latte"}, "phone": {"theme": "latte"}})
gone = P.remove("latte")
check("removing a Yours copy of a shipped theme: the shipped one takes over, "
      "Appearance untouched",
      gone["appearance_reset"] == [] and gone["plugin"]["layer"] == "shipped"
      and appearance.read()["phone"]["theme"] == "latte", gone)
P.remove("notes")
check("removing a plugin clears its switch",
      "notes" not in json.loads((YOURS / P.LIST_FILE).read_text())["on"])
shutil.rmtree(YOURS / "pet")
check("an installed line whose folder went by hand is a broken row",
      "its folder is gone" in (row("pet", "yours")["broken"] or ""))
gone = P.remove("pet")
check("...and removing it clears the line", gone["installed_line"]
      and "pet" not in json.loads((YOURS / P.LIST_FILE).read_text())["plugins"]
      and not rows("pet"), gone)


# -- files ----------------------------------------------------------------------

plugin(YOURS, "lamp", files={"page/main.js": "export function activate() {}\n",
                             "page/lib/util.mjs": "export const a = 1\n",
                             "assets/icon.svg": "<svg/>", "server/secret.py": "x",
                             "theme.css": "", "notes.txt": "private"})
(YOURS / "lamp" / "page" / "out.js").symlink_to(TMP / "vault" / "00_INBOX" /
                                                "from-agents" / "comms.md")
(YOURS / "lamp" / "assets" / "back.js").symlink_to(YOURS / "lamp" / "server" /
                                                   "secret.py")
for rel, ctype in ((["page", "main.js"], "text/javascript; charset=utf-8"),
                   (["page", "lib", "util.mjs"], "text/javascript; charset=utf-8"),
                   (["assets", "icon.svg"], "image/svg+xml"),
                   (["theme.css"], "text/css; charset=utf-8"),
                   (["PLUGIN.md"], "text/markdown; charset=utf-8")):
    path, got = P.file_for("lamp", rel)
    check(f"served: {'/'.join(rel)} as {ctype.split(';')[0]}",
          got == ctype and path == (YOURS / "lamp").joinpath(*rel).resolve(), got)
for rel in (["server", "secret.py"], ["notes.txt"], ["page", "..", "notes.txt"],
            ["page", "out.js"], ["assets", "back.js"], ["page", "missing.js"],
            ["page"], []):
    ok, said = refuses(lambda r=rel: P.file_for("lamp", r), "")
    check(f"not served: {'/'.join(rel) or '(nothing)'}", ok, said)
ok, said = refuses(lambda: P.file_for("evening", ["theme.css"]), "is in force")
check("a broken plugin's files are not served", ok, said)
path, _ = P.file_for("plain", ["theme.css"])
check("a shipped theme's theme.css is served from the shipped layer",
      path == (SHIPPED / "plain" / "theme.css").resolve(), path)


# -- the routes, and bin/settings ---------------------------------------------

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)
TOKEN = TMP / "token"
TOKEN.write_text("test-token")


class FakeBroker:
    sessions: dict = {}
    token = "test-token"


async def http(port, method, path, body=None, raw=False):
    def go():
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer test-token",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                return r.status, (data, r.headers) if raw else json.loads(data)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
    return await asyncio.to_thread(go)


async def cli(port, *argv, lane=None):
    env = dict(os.environ, LOAM_BROKER_URL=f"http://127.0.0.1:{port}",
               LOAM_BROKER_TOKEN_FILE=str(TOKEN), LOAM_AGENT="ada")
    env.pop("LOAM_WAKE", None)
    if lane:
        env["LOAM_WAKE"] = lane
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(AGENTS / "bin" / "settings"), *argv, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    try:
        return json.loads(out)
    except ValueError:
        return {"ok": False, "error": f"no JSON: {out!r} {err[-400:]!r}"}


async def main():
    server = broker_mod.Server.__new__(broker_mod.Server)
    server.broker = FakeBroker()
    tcp = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    port = tcp.sockets[0].getsockname()[1]
    try:
        status, body = await http(port, "GET", "/v1/plugins")
        check("GET /v1/plugins: the list", status == 200 and body["api"] == 0
              and {r["id"] for r in body["plugins"]} >= {"latte", "lamp", "read-aloud"},
              status)
        status, body = await http(port, "GET", "/v1/plugins/read-aloud")
        check("GET /v1/plugins/<id>: the row in force, its guide, every row",
              status == 200 and body["plugin"]["layer"] == "shipped"
              and body["plugin"]["guide"] == "# Guide\n" and len(body["rows"]) == 1, body)
        status, body = await http(port, "GET", "/v1/plugins/ghost")
        check("GET an unknown plugin: 404 with a sentence",
              status == 404 and "no plugin" in body["error"], body)
        status, body = await http(port, "PATCH", "/v1/plugins/lamp", {"on": True})
        check("PATCH {on: true} as the page (no by): on",
              status == 200 and body["plugin"]["on"] is True, body)
        status, body = await http(port, "PATCH", "/v1/plugins/lamp", {"enabled": True})
        check("PATCH anything but `on`: 400", status == 400 and "`on`" in body["error"],
              body)
        status, body = await http(port, "PATCH", "/v1/plugins/solar", {"on": True})
        check("PATCH a theme: 409 pointing at Appearance",
              status == 409 and "Appearance" in body["error"], body)
        status, body = await http(port, "PATCH", "/v1/plugins/lamp",
                                  {"on": False, "by": "nobody"})
        check("a `by` naming nobody: 400, nothing changed",
              status == 400 and row("lamp", "yours")["on"] is True, body)
        status, (data, headers) = await http(port, "GET",
                                             "/v1/plugins/lamp/files/page/main.js",
                                             raw=True)
        check("GET a page file: its bytes as text/javascript, nosniff",
              status == 200 and data == b"export function activate() {}\n"
              and headers["Content-Type"] == "text/javascript; charset=utf-8"
              and headers["X-Content-Type-Options"] == "nosniff", headers)
        status, body = await http(port, "GET", "/v1/plugins/lamp/files/server/secret.py")
        check("GET server code: 404", status == 404, body)
        status, body = await http(port, "GET", "/v1/plugins/lamp/files/page/%2e%2e/notes.txt")
        check("GET an encoded `..`: 404", status == 404, body)
        status, body = await http(port, "POST", "/v1/plugins/lamp/files/page/main.js")
        check("POST to a file: 405", status == 405, body)
        status, body = await http(port, "PUT", "/v1/plugins")
        check("PUT the list: 405", status == 405, body)

        status, body = await http(port, "POST", "/v1/plugins", {"url": LINK})
        check("POST /v1/plugins: the preview", status == 200
              and body["installed"] is False and body["id"] == "pet", body)
        status, bad = await http(port, "POST", "/v1/plugins",
                                 {"url": LINK, "confirm": "f" * 16})
        check("POST with a wrong token: 409 with the preview beside the sentence",
              status == 409 and bad["preview"]["confirm"] == body["confirm"], bad)
        status, done = await http(port, "POST", "/v1/plugins",
                                  {"url": LINK, "confirm": body["confirm"]})
        check("POST with its token: installed", status == 200 and done["installed"]
              and done["plugin"]["source"]["commit"] == C1, done)
        status, body = await http(port, "DELETE", "/v1/plugins/pet")
        check("DELETE /v1/plugins/<id>: removed", status == 200
              and body["removed"] == "pet" and not (YOURS / "pet").exists(), body)

        # -- the same, from a persona's hands ----------------------------------
        out = await cli(port, "describe", "plugin.on")
        check("describe plugin.on: the switchable plugins as `allowed`",
              "lamp" in out["setting"].get("allowed", [])
              and "solar" not in out["setting"]["allowed"], out)
        out = await cli(port, "set", "plugin.on", "false", "--target", "lamp")
        check("set plugin.on false as a persona: read back false",
              out.get("ok") and out.get("value") is False and out["by"] == "ada", out)
        out = await cli(port, "get", "plugin.on", "--target", "lamp")
        check("get plugin.on: what the page's GET says",
              out.get("value") is False, out)
        out = await cli(port, "set", "plugin.on", "true", "--target", "lamp",
                        lane="cron:watch")
        check("set plugin.on from a cron: refused, unchanged",
              out.get("ok") is False and "unattended" in out.get("error", "")
              and row("lamp", "yours")["on"] is False, out)
        out = await cli(port, "set", "plugin.on", "true", "--target", "midnight-gone")
        check("set plugin.on for no such plugin: the broker's 404 sentence",
              out.get("ok") is False and "no plugin" in out.get("error", ""), out)
        out = await cli(port, "do", "plugin.install", LINK)
        answer = out.get("answer") or {}
        check("do plugin.install <link>: the preview, its declaration and token",
              out.get("ok") and answer.get("installed") is False
              and answer.get("declaration", {}).get("touches", {}).get("network")
              and len(answer.get("confirm", "")) == 16, out)
        out2 = await cli(port, "do", "plugin.install",
                         json.dumps({"url": LINK, "confirm": answer.get("confirm")}))
        line = json.loads((YOURS / P.LIST_FILE).read_text())["plugins"].get("pet", {})
        check("do plugin.install with the token: installed, by Ada",
              out2.get("ok") and (out2.get("answer") or {}).get("installed")
              and line.get("installed_by") == "ada", out2)
        out = await cli(port, "do", "plugin.install", LINK, lane="cron:watch")
        check("do plugin.install from a cron: refused",
              out.get("ok") is False and "unattended" in out.get("error", ""), out)
        out = await cli(port, "do", "plugin.remove", "--target", "read-aloud")
        check("do plugin.remove of a shipped plugin: the broker's 409 sentence",
              out.get("ok") is False and "turn it off" in out.get("error", ""), out)
        out = await cli(port, "do", "plugin.remove", "--target", "pet")
        check("do plugin.remove: gone", out.get("ok") and not (YOURS / "pet").exists(),
              out)
        out = await cli(port, "set", "appearance.desktop.theme", "solar")
        status, body = await http(port, "GET", "/v1/plugins/solar")
        check("a persona's appearance.desktop.theme is the theme plugin's `on`",
              out.get("value") == "solar" and body["plugin"]["on"] is True
              and body["plugin"]["in_use"] == ["desktop"], (out, body["plugin"]))
    finally:
        tcp.close()
        await tcp.wait_closed()


asyncio.run(main())
check("the registry's plugin entries name the routes the broker serves",
      settings.BY_KEY["plugin.on"].route == "PATCH /v1/plugins/{plugin}"
      and settings.BY_KEY["plugin.install"].route == "POST /v1/plugins"
      and settings.BY_KEY["plugin.remove"].route == "DELETE /v1/plugins/{plugin}"
      and all(not settings.BY_KEY[k].unattended
              for k in ("plugin.on", "plugin.install", "plugin.remove")))


# -- bin/plugins-autocommit -------------------------------------------------

def autocommit(folder):
    # As cron runs it: no git identity in the environment (it would win
    # over the script's own -c user.name).
    env = {k: v for k, v in GIT_ENV.items() if not k.startswith(("GIT_AUTHOR",
                                                                "GIT_COMMITTER"))}
    env.update(LOAM_PLUGINS_DIR=str(folder),
               LOAM_PLUGINS_AUTOCOMMIT_LOG=str(TMP / "autocommit.log"))
    return subprocess.run([str(AGENTS / "bin" / "plugins-autocommit")], env=env,
                          capture_output=True, text=True)


LAYER = TMP / "layer"
done = autocommit(LAYER)
check("autocommit with no folder yet: a silent no-op",
      done.returncode == 0 and not LAYER.exists(), done.stderr)
plugin(LAYER, "lamp", files={"page/main.js": "x\n"})
(LAYER / ".staging" / "install-1").mkdir(parents=True)
(LAYER / ".staging" / "install-1" / "half.js").write_text("partial")
(LAYER / ".gitignore").write_text(".staging/\n")
done = autocommit(LAYER)
tracked = git("ls-files", cwd=LAYER).splitlines()
check("the first run makes the folder a repository on main and commits it",
      done.returncode == 0 and git("branch", "--show-current", cwd=LAYER) == "main"
      and sorted(tracked) == [".gitignore", "lamp/PLUGIN.md", "lamp/page/main.js"]
      and git("log", "-1", "--format=%an %s", cwd=LAYER).startswith(
          f"{__import__('loam_config').AUTOCOMMIT_NAME} plugins: "),
      (done.stderr, tracked))
check("...the staging folder is not committed",
      not any(t.startswith(".staging") for t in tracked))
head = git("rev-parse", "HEAD", cwd=LAYER)
done = autocommit(LAYER)
check("a run with nothing changed makes no commit and, with no origin, no push",
      done.returncode == 0 and git("rev-parse", "HEAD", cwd=LAYER) == head, done.stderr)
ORIGIN = TMP / "plugins-origin.git"
subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(ORIGIN)], check=True,
               env=GIT_ENV)
git("remote", "add", "origin", str(ORIGIN), cwd=LAYER)
(LAYER / "lamp" / "page" / "main.js").write_text("y\n")
done = autocommit(LAYER)
check("with an origin added: the next change is committed and pushed",
      done.returncode == 0 and git("rev-parse", "main", cwd=ORIGIN)
      == git("rev-parse", "HEAD", cwd=LAYER) != head,
      (done.stderr, (TMP / "autocommit.log").read_text()[-300:]))


# -- a theme's options (2026-11-12), held to loam-ui's loader -----------------

OPT = {"id": "agent-shapes", "label": "Agent shapes",
       "choices": [{"id": "distinct", "label": "Each its own"},
                   {"id": "circle", "label": "Round"}], "default": "distinct"}
OPT_ROOT = TMP / "option-themes"
legacy(OPT_ROOT, "tinted", options=[OPT, {"id": "tint", "label": "Tint",
                                          "choices": [{"id": "warm", "label": "Warm"},
                                                      {"id": "cool", "label": "Cool"}],
                                          "default": "cool"}])
row = P.read_folder(OPT_ROOT / "tinted", "tinted")["manifest"]
check("a manifest.json theme's options are read whole, in order",
      [o["id"] for o in row.get("options", [])] == ["agent-shapes", "tint"]
      and row["options"][1]["default"] == "cool", row.get("options"))
check("a PLUGIN.md theme may carry options too",
      P.manifest_of({**BASE, "kind": ["theme"], "options": [OPT]}, "pet")[0]
      .get("options", [{}])[0].get("id") == "agent-shapes")
for bad, says in (
        ("not a list", "a list of at most"),
        ([OPT] * 2, "is not an option"),
        ([{**OPT, "default": "square"}], "default is not one of"),
        ([{**OPT, "choices": OPT["choices"][:1]}], "is not an option"),
        ([{**OPT, "extra": 1}], "is not an option"),
        ([{**OPT, "id": "Upper"}], "is not an option"),
        ([{**OPT, "choices": [*OPT["choices"], {"id": "hexagon", "label": "Hex"}]}],
         "choice loam-ui's loader refuses"),
        ([{**OPT, "choices": [OPT["choices"][0], OPT["choices"][0]]}],
         "choice loam-ui's loader refuses")):
    try:
        P.options_of(bad)
        said = None
    except P.ManifestError as exc:
        said = str(exc)
    check(f"options refused: {says}", said and says in said, said)
try:
    P.manifest_of({**BASE, "kind": ["extension"], "options": [OPT]}, "pet")
    said = None
except P.ManifestError as exc:
    said = str(exc)
check("options on a plugin that is not a theme: refused",
      said and "a theme's" in said, said)
check("the core table is read from loam-ui's file as it writes it",
      P.core_options() == {"agent-colours": ("palette", "latte-tones"),
                           "agent-shapes": ("distinct", "circle", "rounded-square",
                                            "latte-art")}, P.core_options())
check("parse_core_options: double quotes, bare keys, trailing commas",
      P.parse_core_options('const coreOptions = {\n  "a-b": ["x", "y",],\n  c: '
                           "['z', 'w'],\n}") == {"a-b": ("x", "y"), "c": ("z", "w")})
check("parse_core_options: no table is None",
      P.parse_core_options("export const other = {}") is None)
check("a core option's newer choice is accepted once loam-ui's table has it",
      [c["id"] for c in P.options_of([{**OPT, "choices": [
          *OPT["choices"], {"id": "latte-art", "label": "Latte art"}]}])[0]["choices"]]
      == ["distinct", "circle", "latte-art"])
saved_core = os.environ["LOAM_UI_CORE_OPTIONS"]
for where in ("none", str(TMP / "no-such-table.js")):
    os.environ["LOAM_UI_CORE_OPTIONS"] = where
    try:
        took = P.options_of([{**OPT, "choices": [
            *OPT["choices"], {"id": "hexagon", "label": "Hex"}]}])
    except P.ManifestError:
        took = None
    check(f"no core table ({'none' if where == 'none' else 'unreadable'}): each "
          f"theme's own choices stand", took is not None)
os.environ["LOAM_UI_CORE_OPTIONS"] = saved_core
real_table = (Path(__file__).resolve().parent.parent / "app") / P.CORE_TABLE
if real_table.is_file():
    real_core = P.parse_core_options(real_table.read_text(encoding="utf-8"))
    check("loam-ui's own theme-css.js has a core table the host can read",
          bool(real_core) and all(real_core.values()), real_core)
# A plugin-shaped theme (PLUGIN.md, as loam-ui ships Latte since 0adf959) with
# options: in the catalog the way a manifest.json theme's are.
plugin(OPT_ROOT, "poured", {"kind": ["theme"], "author": "Loam contributors",
                            "options": [{**OPT, "choices": [
                                *OPT["choices"],
                                {"id": "latte-art", "label": "Latte art"}]},
                                {"id": "latte-mug", "label": "In a mug",
                                 "choices": [{"id": "on", "label": "On"},
                                             {"id": "off", "label": "Off"}],
                                 "default": "on"}],
                            "places": [], "overrides": [],
                            "touches": {"network": [], "record": "none",
                                        "memory": "none", "server": False}},
       files={"theme.css": ":root[data-look=\"poured\"] {}\n"})
legacy(OPT_ROOT, "broken-opts", options=[{**OPT, "default": "square"}])
saved = os.environ.get("LOAM_PLUGIN_SHIPPED")
os.environ["LOAM_PLUGIN_SHIPPED"] = str(OPT_ROOT)
try:
    cat = P.theme_options()
    names = P.themes()
finally:
    os.environ["LOAM_PLUGIN_SHIPPED"] = saved
check("theme_options: the themes in force with options, a PLUGIN.md one "
      "among them; a bad option list breaks only its own theme",
      sorted(cat) == ["poured", "tinted"] and "broken-opts" not in names
      and [o["id"] for o in cat["poured"]] == ["agent-shapes", "latte-mug"]
      and cat["poured"][0]["choices"][-1]["id"] == "latte-art", (cat, names))

# -- a theme's cover (2026-11-13), held to loam-ui's themeImage ---------------

COVER_ROOT = TMP / "covers"
THEME = {"kind": ["theme"]}


def cover_row(folder, front, files=None):
    d = plugin(COVER_ROOT, folder, {**THEME, **front},
               files={"theme.css": "", **(files or {})})
    return P.read_folder(d, folder)


got = cover_row("lit", {"image": "cover.webp", "image_position": "62% 45%"},
                {"cover.webp": "RIFF"})
check("cover: image and image_position are named fields, with no warning "
      "and nothing under extra",
      got["manifest"].get("image") == "cover.webp"
      and got["manifest"].get("image_position") == "62% 45%"
      and "extra" not in got["manifest"] and got["warnings"] == [], got)
got = cover_row("deep", {"image": "assets/art/c.AVIF"}, {"assets/art/c.AVIF": "x"})
check("cover: a path in a subfolder, any case of extension, and no position",
      got["manifest"].get("image") == "assets/art/c.AVIF"
      and "image_position" not in got["manifest"] and got["warnings"] == [], got)
got = cover_row("bare", {})
check("cover: a theme without one has neither field and no warning",
      "image" not in got["manifest"] and "image_position" not in got["manifest"]
      and got["warnings"] == [], got)
(TMP / "outside.webp").write_text("x")
(COVER_ROOT / "linked").mkdir(parents=True, exist_ok=True)
(COVER_ROOT / "linked" / "out.webp").symlink_to(TMP / "outside.webp")
for name, front, files, says in (
        ("missing", {"image": "cover.webp"}, {}, "names no file"),
        ("updir", {"image": "../outside.webp"}, {}, "is a path inside"),
        ("absolute", {"image": str(TMP / "outside.webp")}, {}, "is a path inside"),
        ("url", {"image": "https://example.com/c.webp"}, {}, "is a path inside"),
        ("gif", {"image": "c.gif"}, {"c.gif": "x"}, "is a path inside"),
        ("spaced", {"image": "my cover.webp"}, {"my cover.webp": "x"}, "is a path inside"),
        ("number", {"image": 3}, {}, "is a path inside"),
        ("adir", {"image": "d.webp"}, {"d.webp/x": "x"}, "names no file"),
        ("linked", {"image": "out.webp"}, {}, "leaves the theme's folder")):
    got = cover_row(name, front, files)
    check(f"cover refused, theme not broken: {name}",
          "image" not in got["manifest"] and "extra" not in got["manifest"]
          and any(says in w for w in got["warnings"]), got["warnings"])
for pos in ("center", "50%", "50% 50% 0", "101% 0%", "-1% 5%", "50px 10px",
            "50%  50%", "0% " + "1" * 40 + "%", 50):
    got = cover_row("pos", {"image": "c.png", "image_position": pos},
                    {"c.png": "x"})
    check(f"cover position refused, image kept: {pos!r}",
          got["manifest"].get("image") == "c.png"
          and "image_position" not in got["manifest"]
          and any("two percentages" in w for w in got["warnings"]), got["warnings"])
for pos in ("0% 100%", "12.5% 87.25%", "100% 100%"):
    got = cover_row("pos", {"image": "c.png", "image_position": pos})
    check(f"cover position kept: {pos!r}",
          got["manifest"].get("image_position") == pos and not got["warnings"],
          got["warnings"])
got = cover_row("lonely", {"image_position": "10% 10%"})
check("cover: a position with no image is left out, with a warning",
      "image_position" not in got["manifest"]
      and any("without a usable image" in w for w in got["warnings"]), got)
got = P.read_folder(plugin(COVER_ROOT, "ext", {"image": "c.png"},
                           files={"c.png": "x", "page/main.js": ""}), "ext")
check("cover: an extension's image is left out, with a warning",
      "image" not in got["manifest"]
      and any("a theme's cover" in w for w in got["warnings"]), got)
plugin(YOURS, "lit", {**THEME, "image": "cover.webp"},
       files={"theme.css": "", "cover.webp": "RIFF", "other.webp": "x"})
path, ctype = P.file_for("lit", ["cover.webp"])
check("files: a Yours theme's cover is served, as image/webp",
      ctype == "image/webp" and path == (YOURS / "lit" / "cover.webp").resolve(),
      (path, ctype))
ok, said = refuses(lambda: P.file_for("lit", ["other.webp"]), "")
check("files: another image beside it is not", ok, said)
# -- a theme's dark-mode cover (2026-11-13), the light pair's rules ----------

got = cover_row("dusk", {"image": "c.webp", "image_position": "10% 20%",
                         "image_dark": "art/c-dark.webp",
                         "image_position_dark": "30% 40%"},
                {"c.webp": "x", "art/c-dark.webp": "x"})
check("dark cover: image_dark and image_position_dark are named fields, "
      "with no warning and nothing under extra",
      got["manifest"].get("image_dark") == "art/c-dark.webp"
      and got["manifest"].get("image_position_dark") == "30% 40%"
      and got["manifest"].get("image") == "c.webp"
      and "extra" not in got["manifest"] and got["warnings"] == [], got)
got = cover_row("dusk2", {"image": "c.webp", "image_dark": "d.webp"},
                {"c.webp": "x", "d.webp": "x"})
check("dark cover: image_dark alone, its position left to fall back",
      got["manifest"].get("image_dark") == "d.webp"
      and "image_position_dark" not in got["manifest"] and not got["warnings"], got)
got = cover_row("dusk3", {"image": "c.webp", "image_position_dark": "5% 5%"},
                {"c.webp": "x"})
check("dark cover: a dark position over the light image it falls back to",
      got["manifest"].get("image_position_dark") == "5% 5%"
      and "image_dark" not in got["manifest"] and not got["warnings"], got)
got = cover_row("dusk4", {"image_dark": "d.webp", "image_position_dark": "1% 2%"},
                {"d.webp": "x"})
check("dark cover: image_dark without image is left out, as the page throws",
      "image_dark" not in got["manifest"]
      and "image_position_dark" not in got["manifest"]
      and any("image_dark is given without a usable image" in w
              for w in got["warnings"]), got)
got = cover_row("dusk5", {"image": "gone.webp", "image_dark": "d.webp"},
                {"d.webp": "x"})
check("dark cover: left out with an image that is itself left out",
      "image_dark" not in got["manifest"] and "image" not in got["manifest"]
      and any("names no file" in w for w in got["warnings"])
      and any("image_dark is given without a usable image" in w
              for w in got["warnings"]), got["warnings"])
for name, front, files, says in (
        ("dmissing", {"image_dark": "d.webp"}, {}, "names no file"),
        ("dupdir", {"image_dark": "../outside.webp"}, {}, "is a path inside"),
        ("durl", {"image_dark": "https://example.com/d.webp"}, {}, "is a path inside"),
        ("dgif", {"image_dark": "d.gif"}, {"d.gif": "x"}, "is a path inside")):
    got = cover_row(name, {"image": "c.png", **front}, {"c.png": "x", **files})
    check(f"dark cover refused, light kept, theme not broken: {name}",
          "image_dark" not in got["manifest"] and got["manifest"].get("image") == "c.png"
          and "extra" not in got["manifest"]
          and any(w.startswith("image_dark ") and says in w
                  for w in got["warnings"]), got["warnings"])
(COVER_ROOT / "dlinked").mkdir(parents=True, exist_ok=True)
(COVER_ROOT / "dlinked" / "out.webp").symlink_to(TMP / "outside.webp")
got = cover_row("dlinked", {"image": "c.webp", "image_dark": "out.webp"},
                {"c.webp": "x"})
check("dark cover refused: a link out of the folder",
      "image_dark" not in got["manifest"]
      and any("leaves the theme's folder" in w for w in got["warnings"]), got)
for pos in ("center", "101% 0%", "50px 10px"):
    got = cover_row("dpos", {"image": "c.png", "image_dark": "d.png",
                             "image_position_dark": pos},
                    {"c.png": "x", "d.png": "x"})
    check(f"dark cover position refused, images kept: {pos!r}",
          got["manifest"].get("image_dark") == "d.png"
          and "image_position_dark" not in got["manifest"]
          and any(w.startswith("image_position_dark ") and "two percentages" in w
                  for w in got["warnings"]), got["warnings"])
got = cover_row("dlonely", {"image_position_dark": "10% 10%"})
check("dark cover: a dark position with no image at all is left out",
      "image_position_dark" not in got["manifest"]
      and any("image_position_dark is given without a usable image" in w
              for w in got["warnings"]), got)
got = P.read_folder(plugin(COVER_ROOT, "dext", {"image_dark": "d.png"},
                           files={"d.png": "x", "page/main.js": ""}), "dext")
check("dark cover: an extension's image_dark is left out, with a warning",
      "image_dark" not in got["manifest"]
      and any("a theme's cover" in w for w in got["warnings"]), got)
plugin(YOURS, "evening", {**THEME, "image": "c.webp", "image_dark": "art/d.avif"},
       files={"theme.css": "", "c.webp": "x", "art/d.avif": "x", "e.webp": "x"})
path, ctype = P.file_for("evening", ["art", "d.avif"])
check("files: a Yours theme's dark cover is served, as image/avif",
      ctype == "image/avif"
      and path == (YOURS / "evening" / "art" / "d.avif").resolve(), (path, ctype))
path, ctype = P.file_for("evening", ["c.webp"])
check("files: and its light cover beside it", ctype == "image/webp", (path, ctype))
ok, said = refuses(lambda: P.file_for("evening", ["e.webp"]), "")
check("files: another image beside them is not", ok, said)

# -- what a plugin says about itself (2026-11-19): description, screenshots,
# a theme's palette; each wrong one left out with a warning, never broken ---

got = P.read_folder(plugin(COVER_ROOT, "said", {
    "kind": ["extension"], "description": "Listen to your agents instead of reading them.",
    "screenshots": [{"src": "assets/a.webp", "src_dark": "assets/a-dark.webp", "caption": "Listen under a reply"},
                    {"src": "assets/b.png"}, {"src": "../out.webp"}, {"src": "assets/none.webp"},
                    {"src": "assets/a.webp", "src_dark": "gone.webp"}],
    "palette": [{"token": "--paper-page", "name": "Fern"}]},
    files={"assets/a.webp": "x", "assets/a-dark.webp": "x", "assets/b.png": "x", "page/main.js": ""}), "said")
m = got["manifest"]
check("words: a description is kept as given",
      m.get("description") == "Listen to your agents instead of reading them.", m)
check("words: screenshots in the folder are kept, with their dark pair and caption",
      m.get("screenshots", [])[:2] == [{"src": "assets/a.webp", "src_dark": "assets/a-dark.webp",
                                         "caption": "Listen under a reply"}, {"src": "assets/b.png"}], m)
check("words: a screenshot outside the folder or missing is left out, with a warning",
      len(m.get("screenshots", [])) == 3
      and sum("] names no picture" in w for w in got["warnings"]) == 2, got["warnings"])
check("words: a missing dark pair falls back to the light one, with a warning",
      m["screenshots"][2] == {"src": "assets/a.webp"}
      and any("src_dark names no picture" in w for w in got["warnings"]), m)
check("words: an extension's palette is left out, with a warning",
      "palette" not in m and any("palette is a theme's" in w for w in got["warnings"]), got["warnings"])
check("words: the new fields are spec fields, not extras", "extra" not in m, m.get("extra"))
got = P.read_folder(plugin(COVER_ROOT, "hued", {
    **THEME, "description": "x" * 161,
    "palette": [{"token": "--paper-page", "name": "Fern floor", "name_dark": "Night floor"},
                {"token": "paper", "name": "Bad"}, {"token": "--latte-espresso", "name": ""}]},
    files={"theme.css": ""}), "hued")
m = got["manifest"]
check("words: a theme's palette is kept by token, with its dark name",
      m.get("palette") == [{"token": "--paper-page", "name": "Fern floor", "name_dark": "Night floor"}]
      and sum("palette[" in w for w in got["warnings"]) == 2, (m, got["warnings"]))
check("words: a description over 160 characters is left out, with a warning",
      "description" not in m and any("description is one line" in w for w in got["warnings"]), got["warnings"])
plugin(YOURS, "pictured", {"kind": ["extension"], "screenshots": [{"src": "shots/one.webp"}]},
       files={"shots/one.webp": "RIFF", "shots/other.webp": "x", "page/main.js": ""})
path, ctype = P.file_for("pictured", ["shots", "one.webp"])
check("files: a screenshot it names is served", ctype == "image/webp", (path, ctype))
ok, said = refuses(lambda: P.file_for("pictured", ["shots", "other.webp"]), "")
check("files: a picture beside it that it does not name is not", ok, said)

real_themes = (Path(__file__).resolve().parent.parent / "app") / "public" / "themes"
# Held to the core table those themes ship beside, not this file's fixture:
# loam-ui 05bcfb8 (2026-11-13) gave five themes `signature` and `face-content`,
# which the fixture above does not know, and the check read them as broken.
real_table = real_themes.parent / "theme-css.js"
if real_table.is_file():
    os.environ["LOAM_UI_CORE_OPTIONS"] = str(real_table)
for real in sorted(real_themes.glob("*/PLUGIN.md")) if real_themes.is_dir() else []:
    got = P.read_folder(real.parent, real.parent.name)
    check(f"loam-ui's own {real.parent.name} reads with no warning",
          got["warnings"] == [] and "extra" not in got["manifest"], got["warnings"])

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if results and all(results) else 1)
