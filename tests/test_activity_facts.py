import time
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import loam_activity_facts as facts
import loam_plugins as plugins
import loam_preinstall as initial


class ActivityFacts(unittest.TestCase):
    def test_partition_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            projects = vault / facts.config.vault_folder("projects")
            for name in ["one", "two", "three"]:
                (vault / name).touch()
            p = projects / "A"
            p.mkdir(parents=True)
            (p / "note").touch()
            (vault / "alias").symlink_to(p, target_is_directory=True)
            with patch.object(facts.config, "VAULT", vault):
                result = facts.collect(["projects", "files"])
            self.assertEqual(result["files"], 3)
            self.assertEqual(result["projects"][0]["files"], 1)

    def test_declaration(self):
        with self.assertRaises(plugins.ManifestError):
            plugins.touches_of({"facts":["public-work"]}, [])
        with self.assertRaises(plugins.ManifestError):
            plugins.touches_of({"facts":["unknown"]}, [])
        decl = plugins.touches_of({"facts":["files","files"]}, [])
        self.assertEqual(decl["facts"], ["files"])
        with patch.object(plugins, "_rows", return_value=[{"id":"example","effective":True,"on":False,"touches":decl}]):
            with self.assertRaises(ValueError):
                facts.for_plugin("example")

    def test_pin_and_retry(self):
        sha = "a"*40
        pin = ("test", "owner/pkg", "test", sha)
        preview = dict(id="test", source=dict(repo="owner/pkg",path="test",commit=sha), confirm="pinned")
        with patch.object(plugins, "install", return_value=preview) as install:
            initial.install([pin])
            self.assertEqual(install.call_count,2)
            self.assertEqual(install.call_args.args[0]["confirm"],"pinned")
            install.reset_mock()
            initial.install()
            self.assertFalse(install.called)
            preview["replaces"] = {"version":"1.0.0"}
            initial.install([pin])
            self.assertEqual(install.call_count,1)
            preview["source"]["commit"] = "b"*40
            with self.assertRaises(ValueError):
                initial.install([pin])

    def test_usage_counts_survive_vacation_and_upgrade(self):
        url = "/x/notebook/"
        catalog = {"apps": [{"name": "Notebook", "url": url, "source": "yours"}]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(facts.config, "STATE", Path(tmp)), patch.object(facts.apps, "listing", return_value=catalog):
            from contextlib import closing
            with closing(facts.db()) as old, old:
                old.executemany("INSERT INTO app_use VALUES (?,?,?)", [(url, 1, 5), (url, 2, 3)])
                old.commit()
                def count():
                    return facts.collect(["apps"])["apps"][0]["opens"]
                self.assertEqual(count(), 8)
                facts.record_open(url)
                # The pre-upgrade writer can prune and update while the new API comes up.
                old.execute("DELETE FROM app_use WHERE day=1")
                old.execute("UPDATE app_use SET opens=opens+1 WHERE day=2")
                old.execute("INSERT INTO app_use VALUES (?,3,1)", (url,))
                old.commit()
                with patch.object(time, "time", return_value=93*86400):
                    self.assertEqual(count(), 11)
                with self.assertRaises(ValueError):
                    facts.record_open("https://unknown.example/")

    def test_publication_downloads_follow_repository_owner(self):
        import loam_public_work as work
        url = "https://raw.githubusercontent.com/example/list/main/list.json"
        def entry(pid, owner):
            return dict(id=pid, name=pid.title(), author="Same display name", kind=["theme"],
                        link=f"https://github.com/{owner}/{pid}/tree/v1.0.0")
        catalog = dict(version=1, stats="stats.json", plugins=[entry("fern", "Tester"), entry("moss", "other")])
        stats = dict(version=1, plugins={"fern": {"downloads": 12}, "moss": {"downloads": 4}})
        issues = {"total_count": 0, "items": []}
        with tempfile.TemporaryDirectory() as tmp, patch.object(work, "get", side_effect=[catalog, stats, issues, issues]):
            result = work.cached_work(Path(tmp) / "facts.json", ["tester", "example/core", url],
                                      dict.fromkeys(("publications", "reports", "contributions")))
            self.assertEqual([(p["id"], p["downloads"]) for p in result["publications"]], [("fern", 12)])

    def test_offline_public_collections_keep_last_success_for_identity(self):
        import json
        import loam_public_work as work
        values = {k: [{"id": k}] for k in ("publications", "reports", "contributions")}
        url = "https://raw.githubusercontent.com/example/list/main/list.json"
        key = ["tester", "example/core", url]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "public-work.json"
            path.write_text(json.dumps(dict(key=key, value=values, until=0)))
            def empty():
                return dict.fromkeys(values)
            with patch.object(work, "get", side_effect=OSError("offline")), patch.object(time, "time", return_value=90*86400):
                self.assertEqual(work.cached_work(path, key, empty()), values)
                self.assertEqual(work.cached_work(path, ["other", *key[1:]], empty()), empty())
            path.write_text(json.dumps(dict(key=key, value=values, until=0)))
            with patch.object(work, "get", side_effect=[{}, {"incomplete_results": True}, {"total_count": 0, "items": []}]):
                self.assertEqual(work.cached_work(path, key, empty()), {**values, "contributions": []})


if __name__ == "__main__":
    unittest.main()
