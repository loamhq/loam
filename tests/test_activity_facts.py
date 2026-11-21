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
