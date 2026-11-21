"""Plugin updates through real tagged repositories and the two-step installer.

Run: python3 tests/run-all test_plugin_updates.py
"""

import hashlib
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
import loam_plugin_review as review
import loam_preinstall as preinstall
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

    def legacy_install(self, **prefs):
        self.install()
        data = plugins.load_list()
        # The pre-v2 installer hashed the path, a NUL and the binary SHA-256
        # of each file. Compute this fixture's install record independently.
        content = (self.repo / "PLUGIN.md").read_bytes()
        old = hashlib.sha256(b"PLUGIN.md\0" + hashlib.sha256(content).digest()).hexdigest()
        data["plugins"]["notes"].update(files=old, **prefs)
        plugins._save_list(data)
        return old

    def test_clean_legacy_install_migrates_on_first_list_without_network(self):
        self.legacy_install()
        with patch.object(plugins, "_remote", side_effect=AssertionError("unexpected network")):
            row, = plugins.listing()["plugins"]
            self.assertFalse(row["edited"] or row["updates_paused"])
            self.assertTrue(row["auto_update"])
            saved = json.loads((self.yours / plugins.LIST_FILE).read_text())["plugins"]["notes"]
            self.assertEqual(saved["files"], plugins.fingerprint(self.yours / "notes"))
            self.assertNotIn("updates_paused", saved)
        self.release("1.1.0")
        self.listing("1.1.0")
        self.assertFalse(updates.check(nightly=True)["errors"])
        self.assertEqual(plugins.one("notes")["plugin"]["version"], "1.1.0")
        self.tell.assert_not_called()

    def test_clean_legacy_install_preserves_owner_preference(self):
        self.legacy_install(auto_update=False, updates_paused=True)
        row = plugins.one("notes")["plugin"]
        self.assertFalse(row["edited"] or row["auto_update"])
        self.assertTrue(row["updates_paused"])

    def test_edited_legacy_install_remains_protected(self):
        old = self.legacy_install()
        edit = self.yours / "notes" / "draft.txt"
        edit.write_text("personal")
        self.release("1.1.0")
        self.listing("1.1.0")
        self.assertTrue(updates.check(nightly=True)["plugins"]["notes"]["personal"])
        row = plugins.one("notes")["plugin"]
        self.assertTrue(row["edited"] and row["updates_paused"])
        self.assertFalse(row["auto_update"])
        self.assertEqual(plugins.load_list()["plugins"]["notes"]["files"], old)
        self.assertEqual(edit.read_text(), "personal")
        self.tell.assert_called_once()

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
        with self.assertRaisesRegex(plugins.PluginError, "agent"):
            plugins.set_auto_update("notes", True)
        with self.assertRaisesRegex(plugins.PluginError, "agent"):
            plugins.set_auto_update("notes", True, True)
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

    def test_market_link_preinstall_and_old_resume_cannot_replace_owner_bytes(self):
        self.install()
        newer = self.release("1.1.0")
        self.listing("1.1.0")
        edit = self.yours / "notes" / "PLUGIN.md"
        edit.chmod(0o755)
        mine = plugins.fingerprint(edit.parent)
        data = plugins.load_list()
        data["plugins"]["notes"]["update_consent"] = {"files": mine, "copy": False}
        plugins._save_list(data)
        for via in ("market", "link"):
            request = {"url": "maker/notes", "ref": newer, "via": via}
            preview = plugins.install(request)
            with self.assertRaisesRegex(plugins.PluginError, "agent"):
                plugins.install({**request, "confirm": preview["confirm"]})
        preinstall.install([("notes", "maker/notes", "", newer)])
        self.assertTrue(updates.check(nightly=True)["plugins"]["notes"]["personal"])
        self.assertEqual(plugins.fingerprint(edit.parent), mine)
        self.tell.assert_called_once()

    def test_side_by_side_review_recovery_and_next_release_decline(self):
        self.install()
        folder = self.yours / "my-notes"
        shutil.copytree(self.yours / "notes", folder)
        manifest = folder / "PLUGIN.md"
        manifest.write_text(manifest.read_text().replace("id: notes", "id: my-notes")
                            .replace("A notebook.", "My own guide."))
        review.action("my-notes", {"review": "track", "original": "notes"})
        (self.repo / "tips.md").write_text("New tips")
        self.git("add", "tips.md")
        self.release("1.1.0")
        manifest_up = self.repo / "PLUGIN.md"
        manifest_up.write_text(manifest_up.read_text().replace("A notebook.", "Their new guide."))
        self.git("commit", "-qam", "Revise guide")
        self.git("tag", "-f", "v1.1.0")
        self.listing("1.1.0")
        self.assertTrue(updates.check(nightly=True)["plugins"]["my-notes"]["personal"])
        self.assertFalse((folder / "tips.md").exists())
        self.assertEqual(plugins.one("notes")["plugin"]["version"], "1.1.0")
        work = review.action("my-notes", {"review": "prepare"})
        self.assertIn("PLUGIN.md", work["choices"])
        draft = Path(work["folder"])
        self.assertEqual((draft / "tips.md").read_text(), "New tips")
        with self.assertRaisesRegex(plugins.PluginError, "highlighted"):
            review.action("my-notes", {"review": "preview"})
        (draft / "PLUGIN.md").write_text(manifest.read_text().replace("1.0.0", "1.1.0"))
        confirm = review.action("my-notes", {"review": "preview"})["confirm"]
        (draft / "tips.md").write_text("A different draft")
        with self.assertRaisesRegex(plugins.PluginError, "draft changed"):
            review.action("my-notes", {"review": "apply", "confirm": confirm})
        (draft / "tips.md").write_text("New tips")
        original = manifest.read_bytes()
        manifest.write_text("Another local edit")
        with self.assertRaisesRegex(plugins.PluginError, "copy changed"):
            review.action("my-notes", {"review": "apply", "confirm": confirm})
        manifest.write_bytes(original)
        out = review.action("my-notes", {"review": "apply", "confirm": confirm})
        self.assertEqual((Path(out["recovery"]) / "my-notes" / "PLUGIN.md").read_bytes(), original)
        self.assertFalse(out["plugin"]["broken"])
        self.assertEqual((folder / "tips.md").read_text(), "New tips")
        self.release("1.2.0")
        self.listing("1.2.0")
        with patch.object(updates.time, "time", return_value=updates.time.time() + updates.DAY + 2):
            self.assertEqual(updates.check()["plugins"]["my-notes"]["version"], "1.2.0")
            review.action("my-notes", {"review": "skip", "version": "1.2.0"})
            count = self.tell.call_count
            self.assertNotIn("my-notes", updates.check(nightly=True)["plugins"])
            self.assertEqual(self.tell.call_count, count)
        self.assertEqual(review.original(plugins.load_list(), "my-notes")["version"], "1.1.0")

    def test_shipped_original_and_legacy_copy_follow_the_same_review(self):
        shipped = self.state / "shipped"
        src = shipped / "notes"
        src.mkdir(parents=True)
        shutil.copy2(self.repo / "PLUGIN.md", src)
        folder = self.yours / "notes"
        shutil.copytree(src, folder)
        (folder / "mine").write_text("personal")
        with patch.dict(os.environ, LOAM_PLUGIN_SHIPPED=str(shipped)):
            updates.check()
            self.release("1.1.0")
            shutil.copy2(self.repo / "PLUGIN.md", src)
            with patch.object(updates.time, "time", return_value=updates.time.time() + updates.DAY + 1):
                self.assertTrue(updates.check(nightly=True)["plugins"]["notes"]["personal"])
            work = review.action("notes", {"review": "prepare"})
            self.assertEqual(work["choices"], [])
            self.assertEqual((Path(work["folder"]) / "mine").read_text(), "personal")

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
