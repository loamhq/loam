"""Plugin updates through real tagged repositories and the two-step installer.

Run: python3 tests/run-all test_plugin_updates.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import loam_plugin_updates as updates
import loam_plugins as plugins


class Updates(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.repo = root / "repos" / "maker" / "notes"
        self.repo.mkdir(parents=True)
        self.yours = root / "plugins"
        self.state = root / "state"
        self.listfile = root / "list.json"
        env = patch.dict(os.environ, LOAM_PLUGINS_DIR=str(self.yours), LOAM_PLUGIN_SHIPPED="",
                         LOAM_PLUGIN_STATE=str(self.state), LOAM_PLUGIN_GIT_BASE=(root / "repos").as_uri())
        env.start()
        self.addCleanup(env.stop)
        for p in (patch.object(updates.market, "list_url", return_value=self.listfile.as_uri()),
                  patch.object(plugins, "_servers_follow"), patch.object(plugins, "_contributions_follow")):
            p.start()
            self.addCleanup(p.stop)
        attention = patch.object(updates.loam_attention, "tell", return_value="told")
        self.tell = attention.start()
        self.addCleanup(attention.stop)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.release("1.0.0")
        self.listing("1.0.0")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, text=True).strip()

    def release(self, version, more=""):
        (self.repo / "PLUGIN.md").write_text(f"---\nid: notes\nname: Notes\nversion: {version}\n"
                                            f"api: 0\nkind: [extension]\n{more}---\nA notebook.\n")
        self.git("add", "PLUGIN.md")
        self.git("commit", "-qm", version)
        self.git("tag", f"v{version}")
        return self.git("rev-parse", "HEAD")

    def listing(self, version):
        entry = {"id": "notes", "name": "Notes", "version": version, "author": "Maker",
                 "kind": "extension", "link": f"https://github.com/maker/notes/tree/v{version}",
                 "review": {"commit": self.git("rev-parse", f"v{version}"),
                            "note": {"summary": "Reviewed this release."}}}
        self.listfile.write_text(json.dumps({"plugins": [entry]}))

    def install(self, via="market"):
        request = {"url": "maker/notes", "ref": "v1.0.0", "via": via}
        preview = plugins.install(request)
        plugins.install({**request, "confirm": preview["confirm"]})
        self.assertTrue(plugins.one("notes")["plugin"]["auto_update"])

    def test_reviewed_commit_wins_even_if_tag_moves_and_feed_records_once(self):
        self.install()
        reviewed = self.release("1.0.4")
        self.listing("1.0.4")
        unlisted = self.release("2.0.0", "touches: {record: write}\n")
        self.git("tag", "-f", "v1.0.4", unlisted)
        status = updates.check()["plugins"]["notes"]
        self.assertEqual(status["version"], "1.0.4")
        self.assertFalse(status["held"])
        with patch.object(plugins, "_remote", side_effect=AssertionError("unexpected network")), \
             patch.object(updates.market, "fetch", side_effect=AssertionError("unexpected list")):
            self.assertEqual(updates.check()["plugins"]["notes"], status)
            plugins.listing()
            plugins.one("notes")
        self.assertFalse(updates.check(nightly=True)["errors"])
        data = plugins.load_list()
        self.assertEqual(data["plugins"]["notes"]["source"]["commit"], reviewed)
        self.assertEqual([(e["from_version"], e["version"]) for e in data["update_history"]],
                         [("1.0.0", "1.0.4")])
        self.assertFalse(updates.check(nightly=True)["plugins"])
        self.tell.assert_not_called()

    def test_link_release_that_adds_access_waits_for_confirmation(self):
        self.install("link")
        self.release("1.1.0", "touches: {record: write}\n")
        found = updates.check(nightly=True)["plugins"]["notes"]
        self.assertTrue(found["held"])
        self.assertEqual(found["version"], "1.1.0")
        self.tell.assert_called_once()
        updates.check()
        self.tell.assert_called_once()
        self.assertEqual(plugins.one("notes")["plugin"]["version"], "1.0.0")
        request = {"url": found["link"], "via": "link"}
        preview = plugins.install(request)
        self.assertEqual(preview["added"], {"record": "write"})
        self.assertTrue(plugins.install({**request, "confirm": preview["confirm"]})["installed"])

    def test_edit_between_check_and_apply_pauses_until_owner_resumes(self):
        self.install("link")
        self.release("1.0.4")
        updates.check()
        edit = self.yours / "notes" / "draft.txt"
        edit.write_text("mine")
        updates.check(nightly=True)
        self.assertEqual(edit.read_text(), "mine")
        self.assertFalse(plugins.one("notes")["plugin"]["auto_update"])
        with self.assertRaisesRegex(plugins.PluginError, "replaces changes"):
            plugins.set_auto_update("notes", True)
        self.assertTrue(plugins.set_auto_update("notes", True, True)["plugin"]["auto_update"])
        edit.write_text("a different edit")
        updates.check(nightly=True)
        self.assertFalse(plugins.one("notes")["plugin"]["auto_update"])
        edit.unlink()
        self.assertFalse(plugins.one("notes")["plugin"]["auto_update"])

    def test_copy_wins_and_removing_it_restores_original_with_updates_off(self):
        self.install()
        self.release("1.0.4")
        self.listing("1.0.4")
        copy = plugins.copy_folder("notes")
        shutil.copytree(self.yours / "notes", copy)
        (copy / "mine.txt").write_text("my notes")
        updates.check(nightly=True)
        row = plugins.one("notes")["plugin"]
        self.assertEqual(row["folder"], str(copy))
        self.assertEqual(row["source"]["kind"], "built")
        self.assertFalse(row["auto_update"])
        self.assertEqual(plugins.load_list()["plugins"]["notes"]["version"], "1.0.0")
        plugins.remove("notes")
        restored = plugins.one("notes")["plugin"]
        self.assertEqual(restored["version"], "1.0.0")
        self.assertFalse(restored["auto_update"])

    def test_existing_install_can_disable_then_apply_without_waiting_for_another_check(self):
        self.install("link")
        data = plugins.load_list()
        del data["plugins"]["notes"]["auto_update"]
        plugins._save_list(data)
        self.assertTrue(plugins.one("notes")["plugin"]["auto_update"])
        plugins.set_auto_update("notes", False)
        self.release("1.0.4")
        self.assertEqual(updates.check(nightly=True)["plugins"]["notes"]["version"], "1.0.4")
        self.assertEqual(plugins.one("notes")["plugin"]["version"], "1.0.0")
        plugins.set_auto_update("notes", True)
        self.assertFalse(updates.check(nightly=True)["errors"])
        self.assertEqual(plugins.one("notes")["plugin"]["version"], "1.0.4")

    def test_legacy_market_source_remembers_its_authority_after_listing_removed(self):
        self.install()
        data = plugins.load_list()
        del data["plugins"]["notes"]["source"]["via"]
        plugins._save_list(data)
        self.release("2.0.0")
        self.assertEqual(updates.check()["plugins"]["notes"], {"via": "market"})
        self.listfile.write_text('{"plugins":[]}')
        with patch.object(updates.time, "time", return_value=updates.time.time() + updates.DAY + 1):
            self.assertFalse(updates.check(nightly=True)["errors"])
        self.assertEqual(plugins.one("notes")["plugin"]["version"], "1.0.0")

    def test_access_added_in_each_install_sheet_category(self):
        before = {"touches": {"record": "none", "memory": "none", "server": False,
                              "network": [], "agents": {"tools": ["read (persona)"], "prompt": []}},
                  "overrides": [], "page_code": False}
        for key, value in (("record", "write"), ("memory", "write"), ("server", True),
                           ("network", [{"host": "example.invalid", "why": "Reads notes"}]),
                           ("inbound", ["notes: receive notes"]), ("keys", [{"provider": "example"}]),
                           ("agents", {"tools": ["read (persona)", "sync (persona)"], "prompt": []})):
            after = {**before, "touches": {**before["touches"], key: value}}
            self.assertIn(key, plugins.added(before, after))
            self.assertFalse(plugins.added(after, before))
        self.assertEqual(plugins.added(before, {**before, "page_code": True}), {"page_code": True})
        self.assertEqual(plugins.added(before, {**before, "overrides": ["core.rail"]}), {"overrides": ["core.rail"]})

    def test_missing_consent_and_unverifiable_files_fail_closed(self):
        self.install()
        data = plugins.load_list()
        for key in ("files", "declaration", "auto_update"):
            data["plugins"]["notes"].pop(key, None)
        plugins._save_list(data)
        self.release("1.0.4")
        self.listing("1.0.4")
        self.assertTrue(updates.check(nightly=True)["plugins"]["notes"]["held"])
        self.assertEqual(plugins.load_list()["plugins"]["notes"]["version"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
