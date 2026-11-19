#!/usr/bin/env python3
"""lib/loam_to_market.py (2026-11-18, 0.2.9): Dictation and Read aloud left
Loam's core for the Market; a fresh install has neither, and an install that
had one on keeps it.

A local git repository stands in for each maker's repository
(``LOAM_PLUGIN_GIT_BASE``), holding the release's own
``plugins/_listed/<id>/`` copy at the tag ``v1.0.1``, and a local list.json
stands in for the marketplace (``marketplace.list``), so the install is the
real two calls from the real listing. Checks:

  - the release: neither id is in any shipped folder (loam-ui's themes and
    plugins, this runtime's plugins/); each is a listed copy whose page code
    is there; the app server's own speech modules are the listed copy's;
  - a fresh install lists neither, and the update installs nothing;
  - one that was on is installed from its Market listing's link, an
    installed plugin by `loam-update`, still on, and each agent's voice
    (kept by id in `_settings/read-aloud/`) still answers;
  - one that was off is not installed;
  - one already in Yours is left alone; each id is settled once, and one
    removed later is not put back;
  - when the list cannot be read, the release's copy is put in the plugins
    folder (built here), and it is on because its switch was;
  - a failure is reported and tried again at the next start.

Run: python3 tests/test_to_market.py
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
TMP = Path(tempfile.mkdtemp(prefix="test-to-market-"))
CONFIG = TMP / "loam.toml"
CONFIG.write_text(f'''
[paths]
home = "{TMP}/home"
plugins = "{TMP}/plugins"
state = "{TMP}/state"

[marketplace]
list = "file://{TMP}/list.json"
''')
os.environ["LOAM_CONFIG"] = str(CONFIG)
os.environ["LOAM_HOST_SETTINGS"] = str(TMP / "settings.json")
os.environ["LOAM_PLUGIN_STATE"] = str(TMP / "state" / "plugins")
SHIPPED = [ROOT / "app" / "public" / "themes", ROOT / "app" / "public" / "plugins", ROOT / "plugins"]
os.environ["LOAM_PLUGIN_SHIPPED"] = ":".join(str(p) for p in SHIPPED)
os.environ["LOAM_PLUGIN_GIT_BASE"] = f"file://{TMP}/git/"
os.environ["LOAM_UI_CORE_OPTIONS"] = f"{ROOT}/app/public/theme-css.js"

sys.path.insert(0, str(ROOT / "lib"))
import loam_marketplace  # noqa: E402
import loam_plugins  # noqa: E402
import loam_to_market as moved  # noqa: E402

FAILURES = 0
MAKER = "a-maker"


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


def link(pid: str) -> str:
    return f"https://github.com/{MAKER}/loam-{pid}/tree/v1.0.1"


def publish() -> None:
    """Each maker's repository, the plugin at its root, and the listing."""
    entries = []
    for pid in moved.MOVED:
        repo = TMP / "git" / MAKER / f"loam-{pid}"
        shutil.copytree(ROOT / "plugins" / "_listed" / pid, repo)
        git("init", "-q", "-b", "main", cwd=repo)
        git("add", "-A", cwd=repo)
        git("commit", "-q", "-m", pid, cwd=repo)
        git("tag", "v1.0.1", cwd=repo)
        entries.append({"id": pid, "name": pid, "description": "A plugin.", "author": "A maker",
                        "kind": ["extension"], "version": "1.0.1", "link": link(pid)})
    (TMP / "list.json").write_text(json.dumps({"version": 1, "plugins": entries}))


def switches(on: dict) -> None:
    root = loam_plugins.yours_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / loam_plugins.LIST_FILE).write_text(json.dumps({"version": 1, "plugins": {}, "on": on}))


def ids() -> set[str]:
    return {r["id"] for r in loam_plugins._rows() if r.get("effective")}


def reset() -> None:
    shutil.rmtree(TMP / "plugins", ignore_errors=True)
    shutil.rmtree(TMP / "state", ignore_errors=True)
    loam_marketplace._cache.clear()


try:
    # The release itself.
    for pid in moved.MOVED:
        check(f"{pid} is in no shipped folder", [str(r) for r in SHIPPED if (r / pid).exists()], [])
        found = loam_plugins.read_folder(ROOT / "plugins" / "_listed" / pid)
        check(f"{pid} is a listed copy with its page code",
              (found["manifest"]["id"], found["manifest"]["kind"],
               (ROOT / "plugins" / "_listed" / pid / "page" / "main.js").is_file()),
              (pid, ["extension"], True))
    for name in ("speakable.js", "document-speech.js"):
        check(f"the app server's {name} is the listed Read aloud's",
              (ROOT / "app" / "src" / name).read_bytes(),
              (ROOT / "plugins" / "_listed" / "read-aloud" / "page" / name).read_bytes())

    # A fresh install: neither, and nothing to keep.
    reset()
    publish()
    check("a fresh install lists neither", ids() & set(moved.MOVED), set())
    check("and the update installs nothing", moved.keep(), {})

    # On: installed from the Market listing's link.
    reset()
    switches({"dictation": True, "read-aloud": True, "daily-notes": True})
    settings_dir = loam_plugins.agents_path("read-aloud")
    settings_dir.parent.mkdir(parents=True)
    settings_dir.write_text(json.dumps({"agents": {"ada": {"voice": "Kore"}}}))
    check("both are wanted", moved.wanted(), ["dictation", "read-aloud"])
    out = moved.keep()
    check("both installed from their listings' links", {k: (v["how"], v.get("link")) for k, v in out.items()},
          {pid: ("link", link(pid)) for pid in moved.MOVED})
    listed = loam_plugins.load_list()
    check("each is an installed plugin at its maker's repository, by the update",
          {k: (v["source"]["repo"], v["source"]["ref"], v["installed_by"]) for k, v in listed["plugins"].items()},
          {pid: (f"{MAKER}/loam-{pid}", "v1.0.1", "loam-update") for pid in moved.MOVED})
    rows = {r["id"]: r for r in loam_plugins._rows() if r["id"] in moved.MOVED}
    check("each is on, from Yours", {k: (r["layer"], r["on"], r["effective"]) for k, r in rows.items()},
          {pid: ("yours", True, True) for pid in moved.MOVED})
    check("an agent's voice still answers", loam_plugins.agent_values("read-aloud", "ada")["values"]["voice"], "Kore")
    check("another agent's is the default", loam_plugins.agent_values("read-aloud", "bo")["values"]["voice"], "Charon")

    # Once each.
    check("a second start does nothing", moved.keep(), {})
    loam_plugins.remove("dictation", by="ada")
    check("a plugin removed later is not put back", moved.keep(), {})
    check("and is gone", "dictation" in ids(), False)

    # Off: not installed.
    reset()
    publish_list = (TMP / "list.json").read_text()
    switches({"dictation": False, "read-aloud": True})
    calls = []
    out = moved.keep(install=lambda body, by=None: calls.append(body) or {"id": "read-aloud", "confirm": "x"})
    check("one that was off is not installed", (sorted(out), [c["url"] for c in calls]),
          (["read-aloud"], [link("read-aloud")] * 2))

    # Already in Yours: left alone.
    reset()
    switches({"read-aloud": True})
    shutil.copytree(ROOT / "plugins" / "_listed" / "read-aloud", loam_plugins.yours_root() / "read-aloud")
    calls = []
    out = moved.keep(install=lambda *a, **k: calls.append(a))
    check("one already in Yours is left alone", (out["read-aloud"]["how"], calls), ("present", []))

    # The list unreachable: the release's copy, on.
    reset()
    (TMP / "list.json").unlink()
    switches({"dictation": True})
    out = moved.keep()
    check("an unreadable list falls back to the release's copy", out["dictation"]["how"], "release")
    check("the reason is kept", "could not be fetched" in out["dictation"]["link_error"], True)
    row = next(r for r in loam_plugins._rows() if r["id"] == "dictation")
    check("it is built here, in force and on",
          (row["layer"], row["source"]["kind"], row["effective"], row["on"], row["broken"]),
          ("yours", "built", True, True, None))
    (TMP / "list.json").write_text(publish_list)

    # Nothing to fall back on: tried again next time.
    reset()
    switches({"read-aloud": True})
    def nothing(pid):
        raise FileNotFoundError("gone")
    out = moved.keep(from_release=nothing, link_of=lambda pid: (_ for _ in ()).throw(LookupError("no list")))
    check("a plugin that could not be kept is reported", out["read-aloud"]["how"], "failed")
    check("and tried again at the next start", "read-aloud" in moved.keep(from_release=nothing), True)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'OK' if not FAILURES else 'FAILED'}: {FAILURES} failure(s)")
sys.exit(1 if FAILURES else 0)
