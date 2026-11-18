#!/usr/bin/env python3
"""lib/loam_listed_themes.py (2026-11-18, 0.2.5): the first-party looks that
left the shipped set are kept for whoever was using or had chosen one.

A local git repository stands in for loamhq/plugins (``LOAM_PLUGIN_GIT_BASE``),
holding the release's own ``plugins/_listed/<id>/`` folders at the tags
``<id>-v<version>``, so the install is the real two calls. Checks:

  - which looks are wanted: one in use on a device kind, one with saved
    option choices, none for Latte or a look nobody touched;
  - each wanted look is installed from its link, an installed plugin with
    that link and its tag, and Appearance lists it again;
  - a look already in force is left alone, and each id is tried once: a
    look removed after it was kept is not put back at the next start;
  - when the link cannot be read, the release's copy is put in the plugins
    folder (built here) and Appearance wears it;
  - an installed theme serves its fonts.css and font files (and no other
    top-level file);
  - every MOVED id is a listed folder whose version is its MOVED version,
    and none is in the shipped themes any more.

Run: python3 tests/test_listed_themes.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-listed-themes-"))
CONFIG = TMP / "loam.toml"
CONFIG.write_text(f'''
[paths]
home = "{TMP}/home"
plugins = "{TMP}/plugins"
state = "{TMP}/state"
''')
os.environ["LOAM_CONFIG"] = str(CONFIG)
os.environ["LOAM_HOST_SETTINGS"] = str(TMP / "settings.json")
os.environ["LOAM_PLUGIN_STATE"] = str(TMP / "state" / "plugins")
os.environ["LOAM_PLUGIN_SHIPPED"] = f"{ROOT}/app/public/themes"
os.environ["LOAM_PLUGIN_GIT_BASE"] = f"file://{TMP}/git/"
os.environ["LOAM_UI_CORE_OPTIONS"] = f"{ROOT}/app/public/theme-css.js"

sys.path.insert(0, str(ROOT / "lib"))
import loam_appearance  # noqa: E402
import loam_listed_themes as kept  # noqa: E402
import loam_plugins  # noqa: E402

FAILURES = 0


def check(label: str, got, want) -> None:
    global FAILURES  # noqa: PLW0603
    ok = got == want
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"})


def publish() -> None:
    """loamhq/plugins as a local repository, one folder and tag per look."""
    repo = TMP / "git" / "loamhq" / "plugins"
    repo.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=repo)
    for pid, version in kept.MOVED.items():
        shutil.copytree(ROOT / "plugins" / "_listed" / pid, repo / pid)
        git("add", pid, cwd=repo)
        git("commit", "-q", "-m", pid, cwd=repo)
        git("tag", f"{pid}-v{version}", cwd=repo)


def settings(appearance: dict) -> None:
    Path(os.environ["LOAM_HOST_SETTINGS"]).write_text(json.dumps({"appearance": appearance}))


def reset() -> None:
    shutil.rmtree(TMP / "plugins", ignore_errors=True)
    shutil.rmtree(TMP / "state", ignore_errors=True)


try:
    # The release itself: each moved look is a listed folder at its version,
    # and the shipped themes are Latte and Plain alone.
    for pid, version in kept.MOVED.items():
        found = loam_plugins.read_folder(ROOT / "plugins" / "_listed" / pid)
        check(f"{pid} is a listed theme at {version}",
              (found["manifest"]["kind"], found["manifest"]["version"]), (["theme"], version))
    check("the shipped themes are Latte and Plain",
          sorted(p.name for p in (ROOT / "app" / "public" / "themes").iterdir() if p.is_dir()),
          ["latte", "plain"])
    check("the link is the listing's, at the look's own tag", kept.link("autumn"),
          "https://github.com/loamhq/plugins/tree/autumn-v1.0.0/autumn")

    # Which are wanted.
    settings({"phone": {"theme": "foundry", "options": {"latte": {"latte-mug": "on"}}},
              "desktop": {"theme": "latte", "options": {"autumn": {"agent-shapes": "signature"},
                                                        "yosemite": {}}}})
    check("in use and chosen are wanted; Latte and untouched looks are not",
          kept.wanted(), {"foundry": "in use on phone", "autumn": "options chosen on desktop"})
    check("before: Appearance has only the shipped two", loam_appearance.themes(), ["latte", "plain"])

    # Installed from the link.
    publish()
    out = kept.keep()
    check("both installed from their links", {k: v["how"] for k, v in out.items()},
          {"autumn": "link", "foundry": "link"})
    listed = loam_plugins.load_list()["plugins"]
    check("each is an installed plugin with its link and tag",
          {k: (v["source"]["repo"], v["source"]["ref"], v["installed_by"]) for k, v in listed.items()},
          {"autumn": ("loamhq/plugins", "autumn-v1.0.0", "loam-update"),
           "foundry": ("loamhq/plugins", "foundry-v1.0.0", "loam-update")})
    check("Appearance lists them again", loam_appearance.themes(),
          ["latte", "autumn", "foundry", "plain"])
    # Its display faces are served from its folder (0.2.5); nothing else new.
    served = {}
    for rel in (["fonts.css"], ["display-latin.woff2"], ["OFL.txt"], ["theme.css"]):
        try:
            served[rel[0]] = loam_plugins.file_for("autumn", rel)[1]
        except loam_plugins.PluginError as exc:
            served[rel[0]] = exc.args[0][:10]
    check("a theme serves its fonts.css and font files, not its other files", served,
          {"fonts.css": "text/css; charset=utf-8", "display-latin.woff2": "font/woff2",
           "OFL.txt": "a plugin s", "theme.css": "text/css; charset=utf-8"})
    check("the phone still wears Foundry", loam_appearance.read()["phone"]["theme"], "foundry")
    check("the saved Autumn choice still stands",
          loam_appearance.read()["desktop"]["options"]["autumn"]["agent-shapes"], "signature")

    # Once each: a second start does nothing; a removed look stays removed.
    check("a second start does nothing", kept.keep(), {})
    loam_plugins.remove("autumn", by="ada")
    check("a look removed later is not put back", kept.keep(), {})
    check("and is gone from Appearance", "autumn" in loam_appearance.themes(), False)

    # Already present: left alone.
    reset()
    loam_plugins.yours_root().mkdir(parents=True)
    shutil.copytree(ROOT / "plugins" / "_listed" / "foundry", loam_plugins.yours_root() / "foundry")
    settings({"phone": {"theme": "foundry"}})
    calls = []
    out = kept.keep(install=lambda *a, **k: calls.append(a))
    check("a look already in force is left alone", (out["foundry"]["how"], calls), ("present", []))

    # GitHub unreachable: the release's copy.
    reset()
    os.environ["LOAM_PLUGIN_GIT_BASE"] = f"file://{TMP}/nowhere/"
    settings({"desktop": {"theme": "mist-trail"}})
    out = kept.keep()
    check("an unreadable link falls back to the release's copy", out["mist-trail"]["how"], "release")
    check("the reason is kept", "cannot be read" in out["mist-trail"]["link_error"], True)
    row = next(r for r in loam_plugins._rows() if r["id"] == "mist-trail")
    check("it is a theme built here, in force", (row["layer"], row["source"]["kind"], row["effective"], row["broken"]),
          ("yours", "built", True, None))
    check("Appearance wears it", loam_appearance.themes(), ["latte", "mist-trail", "plain"])

    # Nothing at all to fall back on: tried again next time.
    reset()
    settings({"desktop": {"theme": "yosemite"}})
    def nothing(pid):
        raise FileNotFoundError("gone")
    out = kept.keep(from_release=nothing)
    check("a look that could not be kept is reported", out["yosemite"]["how"], "failed")
    check("and tried again at the next start", "yosemite" in kept.keep(from_release=nothing), True)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'OK' if not FAILURES else 'FAILED'}: {FAILURES} failure(s)")
sys.exit(1 if FAILURES else 0)
