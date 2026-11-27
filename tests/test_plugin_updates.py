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
        self.real_tell = updates.loam_attention.tell
        queue = patch.object(updates.loam_attention, "STATE", root / "attention")
        queue.start()
        self.addCleanup(queue.stop)
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

    def publish_local(self):
        folder = self.yours / "notes"
        (folder / "guide.txt").write_text("my guide\n")
        manifest = folder / "PLUGIN.md"
        manifest.write_text(manifest.read_text().replace("1.0.0", "1.1.0"))
        self.assertTrue(plugins.one("notes")["plugin"]["edited"])
        for name in ("PLUGIN.md", "guide.txt"):
            shutil.copy2(folder / name, self.repo / name)
        self.git("add", ".")
        self.git("commit", "-qm", "Publish the locally edited copy")
        self.git("tag", "v1.1.0")
        self.listing("1.1.0")
        return folder

    def assert_adopted(self, folder, *, automatic=True):
        row = plugins.one("notes")["plugin"]
        self.assertEqual(row["installed"]["version"], "1.1.0")
        self.assertFalse(row["edited"] or row.get("personal") or row.get("custom_copy"))
        self.assertEqual(row["auto_update"], automatic)
        line = plugins.load_list()["plugins"]["notes"]
        self.assertEqual(line["files"], plugins.fingerprint(folder))
        self.assertEqual(line["source"]["commit"], self.git("rev-parse", "v1.1.0"))

    def test_nightly_adopts_published_edits_without_swapping_or_notifying(self):
        self.install()
        folder = self.publish_local()
        inode = (folder / "guide.txt").stat().st_ino
        result = updates.check(nightly=True)
        self.assertFalse(result["errors"] or result["plugins"])
        self.assert_adopted(folder)
        self.assertEqual((folder / "guide.txt").stat().st_ino, inode)
        self.tell.assert_not_called()
        self.assertFalse(updates.check(nightly=True)["plugins"])

    def test_adoption_withdraws_pending_offer_and_restores_automatic_updates(self):
        self.install()
        folder = self.publish_local()
        guide = folder / "guide.txt"
        guide.write_text("unpublished line\n")
        attention = updates.loam_attention
        self.tell.side_effect = lambda *a, **kw: self.real_tell(*a, **kw, kick=False)
        self.assertTrue(updates.check(nightly=True)["plugins"]["notes"]["personal"])
        pending = attention.pending()
        self.assertEqual(len(pending), 1)
        review.action("notes", {"review": "prepare"})
        guide.write_bytes((self.repo / "guide.txt").read_bytes())
        self.real_tell("Another plugin's update", by="plugin-updates", key="other-release", kick=False)
        with patch.object(plugins, "_remote", side_effect=AssertionError("used network on cached check")):
            result = updates.check(nightly=True)
        self.assertFalse(result["errors"] or result["plugins"])
        self.assert_adopted(folder)
        self.assertNotIn("notes", plugins.load_list().get("copies", {}))
        self.assertEqual(attention.mark_given(pending), [])
        attention.failed(pending)
        self.assertEqual([r["key"] for r in attention.pending()], ["plugin-updates:other-release"])
        self.tell.assert_called_once()

    def test_unlisted_built_copy_joins_its_listing_without_turning_on(self):
        folder = self.yours / "notes"
        folder.mkdir(parents=True)
        self.release("1.1.0")
        shutil.copy2(self.repo / "PLUGIN.md", folder)
        self.listfile.write_text('{"plugins":[]}')
        self.assertFalse(updates.check(nightly=True)["plugins"])
        self.assertEqual(plugins.one("notes")["plugin"]["source"]["kind"], "built")
        inode = folder.stat().st_ino
        self.listing("1.1.0")
        with patch.object(updates.time, "time", return_value=updates.time.time() + updates.DAY + 1):
            result = updates.check(nightly=True)
        self.assertFalse(result["errors"] or result["plugins"])
        self.assert_adopted(folder)
        self.assertEqual(folder.stat().st_ino, inode)
        row = plugins.one("notes")["plugin"]
        self.assertFalse(row["on"])
        self.assertEqual(row["source"]["via"], "market")
        self.tell.assert_not_called()

    def test_nightly_updates_a_built_copy_of_an_earlier_tag(self):
        folder = self.yours / "notes"
        folder.mkdir(parents=True)
        access = "touches: {record: write}\n"
        self.git("tag", "first-edition", self.release("1.0.1", access))
        self.git("tag", "-d", "v1.0.1")
        shutil.copy2(self.repo / "PLUGIN.md", folder)
        self.release("1.1.0", access)
        self.listing("1.1.0")
        with patch.object(updates, "market_tag", side_effect=AssertionError("tag fetch on page load")):
            result = updates.check()
        self.assertFalse(result["errors"] or result["plugins"])
        self.assertEqual(plugins.one("notes")["plugin"]["source"]["kind"], "built")
        result = updates.check(nightly=True)
        self.assertFalse(result["errors"] or result["plugins"])
        self.assertEqual(result["adopted"], ["notes"])
        self.assert_adopted(folder)
        row = plugins.one("notes")["plugin"]
        self.assertEqual(row["source"]["via"], "market")
        self.assertFalse(row["on"] or row["updates_paused"])
        self.tell.assert_not_called()

    def check_earlier_link(self, automatic):
        self.listfile.write_text('{"plugins":[]}')
        self.install("link")
        plugins.set_auto_update("notes", automatic)
        self.assertEqual(updates.check()["market_state"], "empty")
        self.release("1.1.0")
        self.listing("1.1.0")
        with patch.object(updates.time, "time", return_value=updates.time.time() + updates.DAY + 1):
            result = updates.check(nightly=True)
        self.assertFalse(result["errors"])
        self.assertEqual(result["adopted"], ["notes"])
        row = plugins.one("notes")["plugin"]
        self.assertEqual(row["source"]["via"], "market")
        self.assertEqual(row["version"], "1.1.0" if automatic else "1.0.0")
        self.assertEqual(row["auto_update"], automatic)
        self.assertTrue(row["on"])
        self.assertFalse(row["edited"] or row["updates_paused"])
        self.tell.assert_not_called()
        return result["plugins"]

    def test_link_copy_of_an_earlier_tag_updates_from_the_market(self):
        self.assertFalse(self.check_earlier_link(automatic=True))

    def test_link_copy_of_an_earlier_tag_keeps_manual_updates(self):
        offer = self.check_earlier_link(automatic=False)["notes"]
        self.assertEqual((offer["via"], offer["version"], offer["held"]), ("market", "1.1.0", False))

    def test_link_to_the_newly_listed_tag_joins_the_market(self):
        self.listfile.write_text('{"plugins":[]}')
        self.install("link")
        self.listing("1.0.0")
        result = updates.check(nightly=True)
        self.assertFalse(result["errors"])
        self.assertEqual(result["adopted"], ["notes"])
        self.assertEqual(plugins.one("notes")["plugin"]["source"]["via"], "market")
        self.assertFalse(plugins.load_list().get("update_history"))
        self.tell.assert_not_called()

    def test_edited_earlier_tag_stays_with_the_agent(self):
        self.listfile.write_text('{"plugins":[]}')
        self.install("link")
        edit = self.yours / "notes" / "draft.txt"
        edit.write_text("mine")
        self.release("1.1.0")
        self.listing("1.1.0")
        result = updates.check(nightly=True)
        self.assertFalse(result["errors"] or result["adopted"])
        self.assertTrue(result["plugins"]["notes"]["personal"])
        self.assertEqual(edit.read_text(), "mine")
        row = plugins.one("notes")["plugin"]
        self.assertEqual((row["source"]["via"], row["version"]), ("link", "1.0.0"))
        self.assertTrue(row["edited"] and row["updates_paused"])
        self.tell.assert_called_once()

    def test_same_id_and_bytes_from_another_repository_stay_linked(self):
        other = self.repo.parent / "other"
        shutil.copytree(self.repo, other)
        request = {"url": "maker/other", "ref": "v1.0.0", "via": "link"}
        preview = plugins.install(request)
        plugins.install({**request, "confirm": preview["confirm"]})
        self.release("1.1.0")
        self.listing("1.1.0")
        result = updates.check(nightly=True)
        self.assertFalse(result["adopted"] or result["errors"])
        row = plugins.one("notes")["plugin"]
        self.assertEqual((row["source"]["repo"], row["source"]["via"], row["version"]),
                         ("maker/other", "link", "1.0.0"))
        self.tell.assert_not_called()

    def test_direct_install_adopts_built_copy_only_from_its_listing(self):
        folder = self.yours / "notes"
        folder.mkdir(parents=True)
        self.release("1.1.0")
        shutil.copy2(self.repo / "PLUGIN.md", folder)
        inode = (folder / "PLUGIN.md").stat().st_ino
        self.listfile.write_text('{"plugins":[]}')
        request = {"url": "maker/notes", "ref": "v1.1.0", "via": "market"}
        preview = plugins.install(request)
        self.assertTrue(preview["replaces"]["edited"])
        with self.assertRaisesRegex(plugins.PluginError, "agent"):
            plugins.install({**request, "confirm": preview["confirm"]})
        self.listing("1.1.0")
        updates.market.read(refresh=True)
        preview = plugins.install(request)
        self.assertFalse(preview["replaces"]["edited"])
        self.assertTrue(plugins.install({**request, "recognise": True})["adopted"])
        self.assert_adopted(folder)
        self.assertEqual((folder / "PLUGIN.md").stat().st_ino, inode)

    def test_exact_match_preserves_disabled_updates(self):
        self.install()
        plugins.set_auto_update("notes", False)
        folder = self.publish_local()
        self.assertFalse(updates.check(nightly=True)["plugins"])
        self.assert_adopted(folder, automatic=False)
        self.tell.assert_not_called()

    def test_exact_match_uses_reviewed_contents_without_an_asset_download(self):
        import loam_market_asset
        self.install()
        folder = self.publish_local()
        entries = json.loads(self.listfile.read_text())
        entries["plugins"][0]["asset"] = {"tag": "v1.1.0", "sha256": "a" * 64}
        self.listfile.write_text(json.dumps(entries))
        entry = updates.market.read(refresh=True)["plugins"][0]
        self.assertIn("asset", entry)
        request = {"url": entry["pinned_link"], "via": "market"}
        with patch.object(loam_market_asset, "download", side_effect=AssertionError("spurious download")):
            preview = plugins.install(request)
            with patch.object(plugins, "fetch", side_effect=AssertionError("missed verified preview cache")):
                plugins.install({**request, "confirm": preview["confirm"]})
        self.assert_adopted(folder)
        self.assertFalse(updates.check(nightly=True)["errors"])

    def test_matching_new_access_is_not_silently_confirmed(self):
        self.install()
        prior = plugins.load_list()["plugins"]["notes"]["declaration"]
        self.release("1.1.0", "touches: {record: write}\n")
        shutil.copy2(self.repo / "PLUGIN.md", self.yours / "notes")
        self.listing("1.1.0")
        self.assertFalse(updates.check(nightly=True)["plugins"])
        self.assert_adopted(self.yours / "notes")
        self.assertEqual(plugins.one("notes")["plugin"]["installed"]["confirmed"], prior)
        self.release("1.2.0", "touches: {record: write}\n")
        self.listing("1.2.0")
        with patch.object(updates.time, "time", return_value=updates.time.time() + updates.DAY + 1):
            self.assertTrue(updates.check(nightly=True)["plugins"]["notes"]["held"])

    def test_extra_local_line_still_waits_for_agent(self):
        self.install()
        folder = self.publish_local()
        guide = folder / "guide.txt"
        guide.write_text(guide.read_text() + "still local\n")
        result = updates.check(nightly=True)
        self.assertTrue(result["plugins"]["notes"]["personal"])
        self.assertEqual(plugins.load_list()["plugins"]["notes"]["version"], "1.0.0")
        self.assertTrue(plugins.one("notes")["plugin"]["updates_paused"])
        self.assertIn("still local", guide.read_text())
        self.tell.assert_called_once()

    def test_adopted_overlay_can_update_and_reveal_original(self):
        self.install()
        original = self.yours / "notes"
        copy = plugins.copy_folder("notes")
        copy.mkdir(parents=True)
        self.release("1.1.0")
        self.listing("1.1.0")
        shutil.copy2(self.repo / "PLUGIN.md", copy)
        self.assertFalse(updates.check(nightly=True)["errors"])
        self.assert_adopted(copy)
        self.release("1.2.0")
        self.listing("1.2.0")
        with patch.object(updates.time, "time", return_value=updates.time.time() + updates.DAY + 1):
            self.assertFalse(updates.check(nightly=True)["errors"])
        self.assertIn("1.2.0", (copy / "PLUGIN.md").read_text())
        self.assertIn("1.0.0", (original / "PLUGIN.md").read_text())
        plugins.remove("notes")
        row = plugins.one("notes")["plugin"]
        self.assertEqual(row["installed"]["version"], "1.0.0")
        self.assertFalse(row["edited"])

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
        self.listfile.write_text('{"plugins":[]}')
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
        self.listfile.write_text('{"plugins":[]}')
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
                           ("secrets", ["EXAMPLE_API_KEY"]),
                           ("schedule", ["tick: daily at 05:00 PT"]),
                           ("vault", ["Notes"]),
                           ("open_web", "Public links"),
                           ("facts", ["agents"]),
                           ("drift_check", True),
                           ("engineer_env", ["EXAMPLE_MODE"]),
                           ("engineer_job", ["python3 job.py (every project)"]),
                           ("agents", {"tools": ["read (persona)", "sync (persona)"], "prompt": []}),
                           ("agents", {"tools": ["read (persona)"], "prompt": ["persona"]})):
            after = {**before, "touches": {**before["touches"], key: value,
                                           "settings": ["tip-dismissed"]}}
            got = plugins.added(before, after)
            self.assertIn(key, got)
            self.assertNotIn("settings", got)
            self.assertFalse(plugins.added(after, before))
        self.assertEqual(plugins.added(before, {**before, "page_code": True}), {"page_code": True})
        self.assertEqual(plugins.added(before, {**before, "overrides": ["core.rail"]}), {"overrides": ["core.rail"]})
        settings_only = {**before, "touches": {**before["touches"], "settings": ["tip-dismissed"]}}
        self.assertEqual(plugins.added(before, settings_only), {})
        more_settings = {**before, "touches": {**before["touches"], "settings": ["tip-dismissed", "theme"]}}
        self.assertEqual(plugins.added(settings_only, more_settings), {})

    def test_settings_entry_only_update_is_not_held_and_auto_updates(self):
        self.install()
        setting_v1 = ("contributes:\n  settings.entry:\n"
                      "    - key: gemini-suggestion\n"
                      "      label: Hide browser speech tip\n"
                      "      choices: [show, hide]\n"
                      "      default: show\n")
        commit = self.release("1.2.5", setting_v1)
        self.listing("1.2.5")
        preview = plugins.install({"url": f"https://github.com/maker/notes/tree/{commit}", "via": "market"})
        self.assertEqual(preview["declaration"]["touches"]["settings"], ["gemini-suggestion"])
        self.assertEqual(preview["added"], {})
        status = updates.check()["plugins"]["notes"]
        self.assertEqual(status["version"], "1.2.5")
        self.assertFalse(status["held"])
        self.tell.assert_not_called()
        result = updates.check(nightly=True)
        self.assertFalse(result["errors"] or result["plugins"])
        self.assertEqual(plugins.one("notes")["plugin"]["version"], "1.2.5")
        self.assertEqual(plugins.one("notes")["plugin"]["declaration"]["touches"]["settings"],
                         ["gemini-suggestion"])
        self.assertEqual([(e["from_version"], e["version"]) for e in plugins.load_list()["update_history"]],
                         [("1.0.0", "1.2.5")])
        self.tell.assert_not_called()

        setting_v2 = setting_v1 + ("    - key: auto-clean\n"
                                   "      label: Auto clean\n"
                                   "      choices: [on, off]\n"
                                   "      default: on\n")
        self.release("1.2.6", setting_v2)
        self.listing("1.2.6")
        with patch.object(updates.time, "time", return_value=updates.time.time() + updates.DAY + 1):
            result = updates.check(nightly=True)
        self.assertFalse(result["errors"] or result["plugins"])
        self.assertEqual(plugins.one("notes")["plugin"]["version"], "1.2.6")
        self.tell.assert_not_called()

    def test_real_capability_updates_still_hold_and_notify(self):
        cases = [
            ("network", "touches:\n  network:\n    - host: api.example.invalid\n      why: Syncs notes\n", None),
            ("keys", "keys:\n  - provider: openai\n    why: Summarizes notes\n", None),
            ("secrets", "contributes:\n  settings.secret:\n    - name: NOTES_TOKEN\n      label: Notes token\n", None),
            ("record", "touches:\n  record: write\n", None),
            ("memory", "touches:\n  memory: read\n", None),
            ("server", "touches:\n  server: true\n", None),
            ("page_code", "", ("page/main.js", "export function activate() {}\n")),
            ("agent.tool", "contributes:\n  agent.tool:\n    - name: notes\n      run: python3 tool.py\n", None),
            ("agent.prompt", "contributes:\n  agent.prompt:\n    - text: Use notes.\n", None),
            ("schedule", "contributes:\n  schedule.kind:\n    - name: sync\n      schedule: daily at 05:00 PT\n      run: python3 sync.py\n", None),
            ("vault", "contributes:\n  vault.folder:\n    - path: Notes\n", None),
            ("thread.file", "contributes:\n  thread.file:\n    - label: checklist\n", None),
            ("inbound", "contributes:\n  box.listener:\n    - process: notesd\n      why: Accepts sync connections\n", None),
            ("open_web", "contributes:\n  web.door:\n    - why: Serves shared notes\n", None),
            ("facts", "touches:\n  facts:\n    - agents\n", None),
            ("drift.check", "contributes:\n  drift.check:\n    - run: python3 check.py\n", None),
            ("engineer.env", "contributes:\n  engineer.env:\n    - name: NOTES_MODE\n      value: on\n", None),
            ("engineer.job", "contributes:\n  engineer.job:\n    - run: python3 env.py\n", None),
            ("overrides", "overrides:\n  - core.rail\n", None),
        ]
        for idx, (label, manifest_extra, extra_file) in enumerate(cases, start=1):
            with self.subTest(capability=label):
                self.setUp()
                self.install()
                if extra_file:
                    rel, body = extra_file
                    target = self.repo / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(body)
                    self.git("add", rel)
                if "contributes:\n" in manifest_extra:
                    more = manifest_extra + "  settings.entry:\n    - key: tip\n      label: Tip\n"
                else:
                    more = manifest_extra + "contributes:\n  settings.entry:\n    - key: tip\n      label: Tip\n"
                self.release(f"1.{idx}.0", more)
                self.listing(f"1.{idx}.0")
                result = updates.check(nightly=True)
                self.assertFalse(result["errors"])
                found = result["plugins"]["notes"]
                self.assertTrue(found["held"], label)
                self.assertEqual(found["version"], f"1.{idx}.0")
                self.assertEqual(plugins.one("notes")["plugin"]["version"], "1.0.0")
                self.tell.assert_called_once()

    def test_cached_settings_only_hold_is_reevaluated_and_notice_withdrawn(self):
        self.install()
        setting = ("contributes:\n  settings.entry:\n"
                   "    - key: gemini-suggestion\n"
                   "      label: Hide browser speech tip\n"
                   "      choices: [show, hide]\n"
                   "      default: show\n")
        commit = self.release("1.2.5", setting)
        self.listing("1.2.5")
        line = plugins.load_list()["plugins"]["notes"]
        preview = plugins.install({"url": f"https://github.com/maker/notes/tree/{commit}", "via": "market"})
        stale_cache = {
            "checked": updates.time.time(),
            "market_state": "listed",
            "review_format": 3,
            "entries": {},
            "errors": {},
            "plugins": {
                "notes": {
                    "base": line["source"],
                    "via": "market",
                    "candidate": {
                        "id": "notes",
                        "version": "1.2.5",
                        "source": {**preview["source"], "via": "market"},
                        "files": preview["files"],
                        "declaration": preview["declaration"],
                    },
                    "commit": commit,
                    "version": "1.2.5",
                    "link": preview["source"]["url"],
                    "confirm": preview["confirm"],
                    "held": True,
                    "added": {"settings": ["gemini-suggestion"]},
                    "notified": True,
                }
            },
        }
        cache_path = updates.loam_plugin_servers.state_dir() / "updates.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(stale_cache))
        attention = updates.loam_attention
        self.real_tell(
            "notes 1.2.5 needs your yes. Review the update in Settings › Plugins.",
            by="plugin-updates", key=f"notes-{commit}", evidence=preview["source"]["url"],
            detail=json.dumps({"settings": ["gemini-suggestion"]}), kick=False)
        self.real_tell("Other plugin update", by="plugin-updates", key="other-commit", kick=False)
        self.assertEqual(len(attention.pending()), 2)

        # Prompt re-evaluation on non-nightly check without waiting DAY.
        checked = updates.check()
        self.assertFalse(checked["errors"])
        self.assertEqual(checked["plugins"]["notes"]["version"], "1.2.5")
        self.assertFalse(checked["plugins"]["notes"]["held"])
        saved_cache = json.loads(cache_path.read_text())
        self.assertEqual(saved_cache["review_format"], updates.REVIEW_FORMAT)
        self.assertFalse(saved_cache["plugins"]["notes"]["held"])
        self.assertEqual(saved_cache["plugins"]["notes"]["added"], {})
        self.assertEqual([r["key"] for r in attention.pending()], ["plugin-updates:other-commit"])
        self.tell.assert_not_called()

        # Nightly housekeeping then applies the update automatically.
        applied = updates.check(nightly=True)
        self.assertFalse(applied["errors"] or applied["plugins"])
        self.assertEqual(plugins.one("notes")["plugin"]["version"], "1.2.5")
        self.tell.assert_not_called()

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

    def test_market_update_with_note_differences_is_held_and_cached_with_review(self):
        self.install()
        self.release("1.1.0")
        diff_line = "The plugin says it runs no server code; it runs a Python script on your server every night."
        entry = {"id": "notes", "name": "Notes", "version": "1.1.0", "author": "Maker",
                 "kind": "extension", "link": "https://github.com/maker/notes/tree/v1.1.0",
                 "review": {"commit": self.git("rev-parse", "v1.1.0"),
                            "version": "1.1.0",
                            "note": {"summary": "Reviewed this release.", "differences": [diff_line]}}}
        self.listfile.write_text(json.dumps({"plugins": [entry]}))
        checked = updates.check(nightly=True)
        self.assertFalse(checked["errors"])
        self.assertTrue(checked["plugins"]["notes"]["held"])
        self.assertEqual(plugins.one("notes")["plugin"]["version"], "1.0.0")
        self.assertEqual(checked["entries"]["notes"]["review"]["note"]["differences"], [diff_line])
        self.tell.assert_called_once()
        self.assertIn(diff_line, self.tell.call_args.kwargs.get("detail") or "")


if __name__ == "__main__":
    unittest.main()
