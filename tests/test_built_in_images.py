#!/usr/bin/env python3
"""Verify built-in Images defaults and one-time migration of an unmodified Market copy.
Preserve the owner's settings, keys and edited copies.

Run: python3 tests/test_built_in_images.py"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-built-in-images-"))
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
os.environ["LOAM_PLUGINS_DIR"] = str(TMP / "plugins")
os.environ["LOAM_PLUGIN_SHIPPED"] = f"{ROOT}/plugins"
os.environ["LOAM_CONTRIBUTIONS_FOLLOW"] = "0"

sys.path.insert(0, str(ROOT / "lib"))
import loam_built_in as built_in  # noqa: E402
import loam_contributions  # noqa: E402
import loam_plugins  # noqa: E402

FAILURES = 0
SHIPPED = ROOT / "plugins" / "images"


def check(label: str, got, want) -> None:
    global FAILURES  # noqa: PLW0603
    ok = got == want
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def reset() -> None:
    for d in ("plugins", "state"):
        shutil.rmtree(TMP / d, ignore_errors=True)
    os.environ["LOAM_PLUGIN_SHIPPED"] = f"{ROOT}/plugins"


def rows(pid: str = "images") -> list[dict]:
    return [r for r in loam_plugins._rows() if r["id"] == pid]


def in_force(pid: str = "images") -> dict:
    return next(r for r in rows(pid) if r["effective"])


def market_copy(repo: str = "loamhq/plugins", on: bool = True) -> Path:
    """Images as the Market installed it before 0.2.10: its folder in Yours
    and its line in installed.json, at its tag in ``repo``."""
    yours = loam_plugins.yours_root()
    folder = yours / "images"
    shutil.copytree(SHIPPED, folder)
    man = folder / "PLUGIN.md"
    man.write_text(man.read_text().replace("version: 1.2.0\n", "version: 1.1.1\n")
                   .replace("default_on: false\n", ""))
    found = loam_plugins.read_folder(folder, "images")
    data = loam_plugins.load_list()
    data["plugins"]["images"] = {
        "source": {"kind": "link", "repo": repo, "path": "images", "ref": "images-v1.1.1",
                   "ref_kind": "tag", "commit": "0" * 40,
                   "url": f"https://github.com/{repo}/tree/images-v1.1.1/images"},
        "version": "1.1.1", "declaration": loam_plugins.declaration(found["manifest"], found["has"]),
        "confirmed": "x", "files": loam_plugins.fingerprint(folder),
        "installed_at": "2026-11-19T01:24:13Z", "installed_by": "owner"}
    data["on"]["images"] = on
    loam_plugins._save_list(data)
    return folder


try:
    # -- A fresh install ----------------------------------------------------
    reset()
    row = in_force()
    check("fresh: Images is built in, by Loam, in force",
          (row["layer"], row["source"], row["broken"], row["switch"]),
          ("shipped", {"kind": "loam"}, None, "plugin"))
    check("fresh: it is off", row["on"], False)
    check("fresh: no lane has its tool while it is off",
          "images" in loam_contributions.lane_servers("persona", "ada"), False)
    loam_plugins.set_on("images", True)
    check("turned on in Settings › Plugins, it is on", in_force()["on"], True)
    check("and the persona and engineer lanes get its tool",
          ["images" in loam_contributions.lane_servers(lane, "ada") for lane in ("persona", "engineer")],
          [True, True])
    check("its tool runs from the shipped folder",
          str(SHIPPED) in json.dumps(loam_contributions.lane_servers("persona", "ada")["images"]), True)
    check("a fresh install has nothing to take over", built_in.take_over()["images"]["how"], "absent")
    check("and it is settled once", built_in.take_over(), {})

    # -- Installed from the Market before 0.2.10 -------------------------------
    reset()
    market_copy()
    settings_dir = loam_plugins.yours_root() / "_settings" / "images"
    settings_dir.mkdir(parents=True)
    (settings_dir / "values.json").write_text(json.dumps({"folder": "Pictures", "backend": "google"}))
    private = loam_contributions.private_dir("images")
    private.mkdir(parents=True)
    (private / "secrets.env").write_text("GOOGLE_API_KEY=test-only\n")
    (private / "checks.json").write_text(json.dumps({"google@images": {"ok": True}}))
    before = in_force()
    check("before: the Market copy is in force over the built-in one",
          (before["layer"], before["version"], before["on"], (before.get("shadows") or {}).get("layer")),
          ("yours", "1.1.1", True, "shipped"))
    check("before: what it would do", built_in.look("images")["how"], "replace")
    out = built_in.take_over()
    check("the Market copy is replaced", out["images"]["how"], "replaced")
    check("what it was is on the record", out["images"]["was"]["source"]["repo"], "loamhq/plugins")
    after = in_force()
    check("after: the built-in copy is in force, still on",
          (after["layer"], after["version"], after["on"], after["source"]), ("shipped", "1.2.0", True, {"kind": "loam"}))
    check("after: no Yours copy and no installed line",
          ((loam_plugins.yours_root() / "images").exists(), "images" in loam_plugins.load_list()["plugins"]),
          (False, False))
    check("after: the switch is kept", loam_plugins.load_list()["on"].get("images"), True)
    check("after: its settings are where they were",
          loam_contributions.setting_values("images").get("folder"), "Pictures")
    check("after: its own key and key choice are where they were",
          ((private / "secrets.env").read_text(), json.loads((private / "checks.json").read_text())),
          ("GOOGLE_API_KEY=test-only\n", {"google@images": {"ok": True}}))
    check("after: the lanes still get its tool, from the shipped folder",
          str(SHIPPED) in json.dumps(loam_contributions.lane_servers("engineer")["images"]), True)
    check("a second start does nothing", built_in.take_over(), {})
    check("the record says so", json.loads(built_in.record_path().read_text())["settled"]["images"]["how"],
          "replaced")

    # A Market copy that was off stays off.
    reset()
    market_copy(on=False)
    built_in.take_over()
    check("a Market copy that was off: the built-in one is off", in_force()["on"], False)

    # -- The person's own copies stay ---------------------------------------
    reset()
    folder = market_copy()
    (folder / "README.md").write_text("mine\n")
    check("an edited copy is kept", built_in.take_over()["images"]["how"], "kept")
    check("an edited copy: why", built_in._read_record()["settled"]["images"]["why"],
          "edited since it was installed")
    check("an edited copy stays in force", in_force()["layer"], "yours")

    reset()
    market_copy(repo="someone/images-fork")
    check("a copy from another repository is kept",
          (built_in.take_over()["images"]["how"], in_force()["layer"]), ("kept", "yours"))

    reset()
    shutil.copytree(SHIPPED, loam_plugins.yours_root() / "images")
    check("a copy built here is kept",
          (built_in.take_over()["images"]["why"], in_force()["layer"]), ("built here", "yours"))

    # -- A release that does not ship it ------------------------------------
    reset()
    market_copy()
    os.environ["LOAM_PLUGIN_SHIPPED"] = f"{ROOT}/app/public/themes"
    check("without a shipped copy nothing is done", built_in.take_over(), {})
    check("and nothing is settled", built_in.record_path().exists(), False)
    os.environ["LOAM_PLUGIN_SHIPPED"] = f"{ROOT}/plugins"
    check("the release that ships it then takes over", built_in.take_over()["images"]["how"], "replaced")

    # -- A failure is tried again --------------------------------------------
    reset()
    market_copy()
    def broken(pid, by=None):
        raise OSError("disk full")
    check("a failed removal is reported", built_in.take_over(remove=broken)["images"]["how"], "failed")
    check("and tried again at the next start", built_in.take_over()["images"]["how"], "replaced")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'OK' if not FAILURES else 'FAILED'}: {FAILURES} failure(s)")
sys.exit(1 if FAILURES else 0)
