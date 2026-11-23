#!/usr/bin/env python3
"""Unit tests for the optional separate data disk (`install.sh --data-dir`)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import loam_data_disk as dd  # noqa: E402
import loam_host as lh  # noqa: E402
import loam_install as li  # noqa: E402


def load_cmd(name: str):
    path = ROOT / "bin" / name
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class TestValidationAndClassify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_validate_path_rejects_bad_paths(self):
        for bad in ("", "relative/dir", "/mnt/has space", "/mnt/a/../b",
                    "/", "/etc", "/etc/loam", "/etc/loam/sub",
                    "/home", "/home/loam", "/home/loam/vault",
                    "/var/lib/loam", "/var/lib/loam/local/loam",
                    "/proc/foo", "/sys/foo", "/dev/shm"):
            path, why = dd.validate_path(bad, require_separate_mount=False)
            self.assertIsNone(path, f"should reject {bad!r}")
            self.assertTrue(why, f"should explain rejection of {bad!r}")

    def test_validate_path_requires_existing_dir_not_symlink(self):
        missing = self.base / "missing"
        self.assertIsNone(dd.validate_path(missing, require_separate_mount=False)[0])

        regular = self.base / "file"
        regular.write_text("hi")
        self.assertIsNone(dd.validate_path(regular, require_separate_mount=False)[0])

        real = self.base / "disk"
        real.mkdir()
        link = self.base / "link"
        link.symlink_to(real)
        path, why = dd.validate_path(link, require_separate_mount=False)
        self.assertIsNone(path)
        self.assertIn("symbolic link", why)

        # Same device as root is rejected when require_separate_mount=True.
        path, why = dd.validate_path(real, require_separate_mount=True, root=real)
        self.assertIsNone(path)
        self.assertIn("separate disk", why)

        # Accepted when separate mount check passes.
        with patch.object(dd, "is_separate_mount", return_value=True):
            path, why = dd.validate_path(real, require_separate_mount=True)
        self.assertEqual(path, real)
        self.assertIsNone(why)

    def test_classify_fresh_lost_and_found_unrecognized_and_existing(self):
        d = self.base / "disk"
        d.mkdir()
        self.assertEqual(dd.classify(d)["kind"], "fresh")

        # Fresh ext4 volumes contain lost+found; must still classify as fresh.
        (d / "lost+found").mkdir()
        self.assertEqual(dd.classify(d)["kind"], "fresh")

        # Unrelated files without marker -> unrecognized.
        (d / "notes.txt").write_text("keep me")
        info = dd.classify(d)
        self.assertEqual(info["kind"], "unrecognized")
        self.assertIn("notes.txt", info["reason"])
        (d / "notes.txt").unlink()

        # Corrupt marker -> unrecognized.
        (d / dd.MARKER).write_text("{not json")
        self.assertEqual(dd.classify(d)["kind"], "unrecognized")

        # Unsupported future version -> unrecognized.
        (d / dd.MARKER).write_text(json.dumps({"version": dd.FORMAT_VERSION + 1}))
        self.assertEqual(dd.classify(d)["kind"], "unrecognized")

        # Valid marker but missing etc/loam.toml -> unrecognized.
        (d / dd.MARKER).unlink()
        dd.write_marker(d, user="loam", domain="loam.example.test")
        self.assertEqual(dd.classify(d)["kind"], "unrecognized")
        self.assertIn("etc/loam.toml", dd.classify(d)["reason"])

        # Add etc/loam.toml without home/ -> still unrecognized.
        (d / "etc").mkdir()
        (d / "etc" / "loam.toml").write_text(li.render_config(
            name="", domain="loam.example.test", owner="Sam", timezone="UTC",
            address="203.0.113.10", home="/home/loam", data_dir=str(d)))
        self.assertEqual(dd.classify(d)["kind"], "unrecognized")
        self.assertIn("home/", dd.classify(d)["reason"])

        # Add home/ -> existing Loam data disk!
        (d / "home").mkdir()
        info = dd.classify(d)
        self.assertEqual(info["kind"], "existing")
        self.assertEqual(info["marker"]["domain"], "loam.example.test")

    def test_write_marker_preserves_created_at(self):
        d = self.base / "disk"
        d.mkdir()
        first = dd.write_marker(d, user="loam", domain="sam.example.test", uid=1000, gid=1000)
        second = dd.write_marker(d, last_adopted_at="2026-11-23T12:00:00+00:00")
        self.assertEqual(second["created_at"], first["created_at"])
        self.assertEqual(second["uid"], 1000)
        self.assertEqual(second["last_adopted_at"], "2026-11-23T12:00:00+00:00")


class TestFstabAndMounts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_mount_specs_and_fstab_roundtrip(self):
        data_dir = Path("/mnt/loam-data")
        home = Path("/home/loam")
        specs = dd.mount_specs(data_dir, home)
        self.assertEqual(len(specs), 2 + len(dd.LOCAL_OVERLAYS))
        self.assertEqual(specs[0].source, Path("/mnt/loam-data/etc"))
        self.assertEqual(specs[0].target, Path("/etc/loam"))
        self.assertEqual(specs[0].requires_mount, data_dir)
        self.assertEqual(specs[1].source, Path("/mnt/loam-data/home"))
        self.assertEqual(specs[1].target, home)
        self.assertEqual(specs[1].requires_mount, data_dir)
        for overlay in specs[2:]:
            self.assertEqual(overlay.role, "local")
            self.assertEqual(overlay.requires_mount, home)
            self.assertTrue(str(overlay.source).startswith("/var/lib/loam/local/"))

        fstab = self.base / "fstab"
        fstab.write_text("UUID=abc / ext4 defaults 0 1\nUUID=def /mnt/loam-data ext4 defaults,nofail 0 2\n")
        self.assertTrue(dd.update_fstab(specs, fstab=fstab))
        first = fstab.read_text()
        self.assertIn("UUID=abc / ext4 defaults 0 1", first)
        self.assertIn(dd.FSTAB_BEGIN, first)
        self.assertIn("/mnt/loam-data/home /home/loam none bind,nofail,x-systemd.requires-mounts-for=/mnt/loam-data 0 0", first)
        self.assertIn("/var/lib/loam/local/worktrees /home/loam/.local/state/loam/worktrees none bind,nofail,x-systemd.requires-mounts-for=/home/loam 0 0", first)
        self.assertIn(dd.FSTAB_END, first)

        # Idempotent second call changes nothing.
        self.assertFalse(dd.update_fstab(specs, fstab=fstab))

        # Updating data_dir replaces only the managed block, keeping user entries before and after.
        fstab.write_text(first + "# user footer\n")
        specs2 = dd.mount_specs(Path("/mnt/other-disk"), home)
        self.assertTrue(dd.update_fstab(specs2, fstab=fstab))
        second = fstab.read_text()
        self.assertIn("UUID=abc / ext4 defaults 0 1", second)
        self.assertIn("# user footer", second)
        self.assertIn("/mnt/other-disk/home /home/loam", second)
        self.assertNotIn("/mnt/loam-data/home /home/loam", second)

    def test_unterminated_fstab_block_raises(self):
        with self.assertRaises(dd.DataDiskError):
            dd.replace_fstab_block(f"{dd.FSTAB_BEGIN}\n/a /b none bind 0 0\n", "x\n")

    def test_mountinfo_and_ensure_bind_mount(self):
        src = self.base / "src"
        tgt = self.base / "tgt"
        src.mkdir()
        mi = self.base / "mountinfo"
        mi.write_text(
            "21 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw\n"
            f"22 21 8:2 / /mnt/with\\040space rw - ext4 /dev/sdb1 rw\n"
        )
        targets = dd.mount_targets(mi)
        self.assertIn("/", targets)
        self.assertIn("/mnt/with space", targets)

        # Unmounted target runs `mount --bind src tgt`.
        calls = []

        def fake_run(argv):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        self.assertTrue(dd.ensure_bind_mount(src, tgt, mountinfo_path=mi, run=fake_run))
        self.assertEqual(calls, [["mount", "--bind", str(src), str(tgt)]])

        # Already mounted with matching inode -> no-op.
        mi.write_text(f"23 21 8:1 / {src} rw - ext4 /dev/sda1 rw\n")
        calls.clear()
        self.assertFalse(dd.ensure_bind_mount(src, src, mountinfo_path=mi, run=fake_run))
        self.assertEqual(calls, [])

        # Already mounted at target with different inode -> conflict error.
        other = self.base / "other"
        other.mkdir()
        mi.write_text(f"24 21 8:1 / {other} rw - ext4 /dev/sda1 rw\n")
        with self.assertRaises(dd.DataDiskError):
            dd.ensure_bind_mount(src, other, mountinfo_path=mi, run=fake_run)


class TestConfigAndAdopt(unittest.TestCase):
    def test_render_config_unchanged_without_data_dir(self):
        plain = li.render_config(
            name="sam", owner="Sam", timezone="UTC", address="203.0.113.10",
            home="/home/loam")
        self.assertNotIn("[storage]", plain)
        self.assertNotIn("data_dir", plain)
        parsed = tomllib.loads(plain)
        self.assertNotIn("storage", parsed)

        with_disk = li.render_config(
            name="sam", owner="Sam", timezone="UTC", address="203.0.113.10",
            home="/home/loam", data_dir="/mnt/loam-data")
        self.assertEqual(with_disk, plain + '\n[storage]\ndata_dir = "/mnt/loam-data"\n')
        parsed_disk = tomllib.loads(with_disk)
        self.assertEqual(parsed_disk["storage"]["data_dir"], "/mnt/loam-data")

    def test_adopt_config_preserves_owner_edits_and_comments(self):
        original = (
            li.render_config(
                name="", domain="loam.example.test", owner="Sam",
                timezone="America/New_York", address="198.51.100.1",
                home="/home/loam", resolver="system", tls="internal",
                data_dir="/mnt/disk1")
            + "\n# owner custom note\n"
            + "[firewall]\nopen = [\"22000/tcp\"]\n"
            + "\n[keys.providers.custom]\nenv = \"CUSTOM_KEY\"\n"
        )
        adopted = dd.adopt_config_text(
            original, address="203.0.113.2", data_dir="/mnt/disk2")
        self.assertIn("# owner custom note", adopted)
        parsed = tomllib.loads(adopted)
        self.assertEqual(parsed["edge"]["address"], "203.0.113.2")
        self.assertEqual(parsed["edge"]["domain"], "loam.example.test")
        self.assertTrue(parsed["edge"]["own_domain"])
        # Rehearsal settings preserved when not overridden.
        self.assertEqual(parsed["edge"]["resolver"], "system")
        self.assertEqual(parsed["edge"]["tls"], "internal")
        self.assertEqual(parsed["storage"]["data_dir"], "/mnt/disk2")
        self.assertEqual(parsed["owner"]["name"], "Sam")
        self.assertEqual(parsed["timezone"], "America/New_York")
        self.assertEqual(parsed["firewall"]["open"], ["22000/tcp"])
        self.assertEqual(parsed["keys"]["providers"]["custom"]["env"], "CUSTOM_KEY")

        # Explicit overrides apply cleanly, and missing [storage] table is appended.
        no_storage = li.render_config(
            name="sam", owner="Sam", timezone="UTC", address="198.51.100.1",
            home="/home/loam")
        adopted2 = dd.adopt_config_text(
            no_storage, address="203.0.113.5", data_dir="/mnt/loam-data",
            resolver="system", tls="internal")
        parsed2 = tomllib.loads(adopted2)
        self.assertEqual(parsed2["edge"]["address"], "203.0.113.5")
        self.assertEqual(parsed2["edge"]["resolver"], "system")
        self.assertEqual(parsed2["edge"]["tls"], "internal")
        self.assertEqual(parsed2["storage"]["data_dir"], "/mnt/loam-data")


class TestDockerLoginVolumeAndDrift(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_docker_volume_path_prefers_options_device(self):
        bind_json = json.dumps({
            "Driver": "local",
            "Mountpoint": "/var/lib/docker/200000.200000/volumes/authelia-data/_data",
            "Name": "authelia-data",
            "Options": {"device": "/mnt/loam-data/login/authelia-data",
                        "o": "bind", "type": "none"},
        })
        plain_json = json.dumps({
            "Driver": "local",
            "Mountpoint": "/var/lib/docker/200000.200000/volumes/authelia-data/_data",
            "Name": "authelia-data",
            "Options": None,
        })
        for raw, want in [
            (bind_json, Path("/mnt/loam-data/login/authelia-data")),
            (plain_json, Path("/var/lib/docker/200000.200000/volumes/authelia-data/_data")),
            ("/tmp/plain-path", Path("/tmp/plain-path")),
        ]:
            got = dd.docker_volume_path(
                "authelia-data", docker="/usr/bin/docker",
                run=lambda cmd, r=raw: subprocess.CompletedProcess(cmd, 0, r + "\n", ""))
            self.assertEqual(got, want)

    def test_loam_host_find_login_supports_bind_volume_json(self):
        login_dir = self.base / "login-data"
        login_dir.mkdir()
        fake = self.base / "docker"
        payload = json.dumps({
            "Mountpoint": "/nonexistent/docker/_data",
            "Options": {"device": str(login_dir), "o": "bind", "type": "none"},
        })
        fake.write_text(f"#!/bin/sh\nprintf '%s\\n' '{payload}'\n")
        fake.chmod(0o755)
        with patch.object(lh.shutil, "which", return_value=str(fake)):
            folder, note = lh.find_login()
        self.assertEqual(folder, str(login_dir), note)

    def test_gen_deploy_ensure_login_volume_plain_vs_data_dir(self):
        gd = load_cmd("gen-deploy")
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            if argv[:3] == ["docker", "volume", "inspect"]:
                return subprocess.CompletedProcess(argv, 1, "", "no such volume")
            return subprocess.CompletedProcess(argv, 0, "authelia-data\n", "")

        # Without data_dir: plain volume create, unchanged.
        with patch.object(gd.dep, "login_data_dir", return_value=None), \
                patch.object(gd, "run", side_effect=fake_run):
            gd.ensure_login_volume()
        self.assertEqual(calls, [["docker", "volume", "create", "authelia-data"]])

        # With data_dir: bind-backed local volume + ownership aligned.
        calls.clear()
        target = self.base / "disk" / "login" / "authelia-data"
        me = (os.getuid(), os.getgid())
        with patch.object(gd.dep, "login_data_dir", return_value=target), \
                patch.object(gd.dep, "docker_root_owner", return_value=me), \
                patch.object(gd, "run", side_effect=fake_run):
            gd.ensure_login_volume()
        self.assertTrue(target.is_dir())
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)
        self.assertIn(
            ["docker", "volume", "create", "--driver", "local",
             "--opt", "type=none", "--opt", "o=bind",
             "--opt", f"device={target}", "authelia-data"],
            calls)

        # Idempotent when volume already points at target.
        calls.clear()
        existing_json = json.dumps({"Options": {"device": str(target)}})

        def fake_existing(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, existing_json, "")

        with patch.object(gd.dep, "login_data_dir", return_value=target), \
                patch.object(gd.dep, "docker_root_owner", return_value=me), \
                patch.object(gd, "run", side_effect=fake_existing):
            gd.ensure_login_volume()
        self.assertFalse(any(c[:3] == ["docker", "volume", "create"] for c in calls))

    def test_drift_findings_clean_and_broken_states(self):
        data_dir = self.base / "disk"
        data_dir.mkdir()
        (data_dir / "etc").mkdir()
        (data_dir / "home").mkdir()
        dd.login_data_dir(data_dir).mkdir(parents=True)
        dd.write_marker(data_dir, user="loam", domain="sam.example.test")

        home = self.base / "home-loam"
        etc = self.base / "etc-loam"
        local_base = self.base / "local"
        fstab = self.base / "fstab"
        specs = dd.mount_specs(data_dir, home, etc=etc, local_base=local_base)
        dd.update_fstab(specs, fstab=fstab)

        good_docker = lambda cmd: subprocess.CompletedProcess(
            cmd, 0, json.dumps({"Options": {"device": str(dd.login_data_dir(data_dir))}}), "")

        # All checks green -> 0 findings.
        with patch.object(dd, "is_separate_mount", return_value=True), \
                patch.object(dd, "is_bind_mounted", return_value=True):
            got = dd.drift_findings(
                data_dir, home, etc=etc, local_base=local_base, fstab=fstab,
                docker_run=good_docker)
        self.assertEqual(got, [])

        # Missing bind mount + non-bind Docker volume + same-device root -> reported.
        bad_docker = lambda cmd: subprocess.CompletedProcess(
            cmd, 0, json.dumps({"Options": None, "Mountpoint": "/var/lib/docker/volumes/authelia-data/_data"}), "")
        with patch.object(dd, "is_separate_mount", return_value=False), \
                patch.object(dd, "is_bind_mounted", return_value=False):
            got = dd.drift_findings(
                data_dir, home, etc=etc, local_base=local_base, fstab=fstab,
                docker_run=bad_docker)
        labels = [g[0] for g in got]
        self.assertIn("data disk", labels)
        self.assertIn(f"data disk mount {home}", labels)
        self.assertIn("data disk login volume", labels)


class TestInstallerAdoptionGuards(unittest.TestCase):
    def test_step_name_allows_taken_on_adopt_and_stops_on_fresh(self):
        inst = load_cmd("loam-install")
        edge = {"name": "sam", "domain": "sam.loam.test", "address": "203.0.113.2",
                "resolver": "system"}
        taken = {"state": "taken", "domain": "sam.loam.test",
                 "apex": ["198.51.100.1"], "wildcard": ["198.51.100.1"]}

        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "install.log"
            with patch.object(inst, "LOG", log_path), \
                    patch.object(inst, "config_values", return_value={"edge": edge}), \
                    patch.object(inst, "save_answers"), \
                    patch.object(inst.li, "name_check", return_value=taken):
                # Fresh install stops on taken.
                with self.assertRaises(inst.Stop):
                    inst.step_name(SimpleNamespace(), {"adopted": False})
                # Adopted disk continues (old server still holds DNS).
                ans = {"adopted": True}
                inst.step_name(SimpleNamespace(), ans)
                self.assertEqual(ans["name_check"]["state"], "taken")

    def test_cli_help_mentions_data_dir(self):
        res_sh = subprocess.run(["bash", str(ROOT / "install.sh"), "--help"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(res_sh.returncode, 0)
        self.assertIn("--data-dir", res_sh.stdout)

        res_py = subprocess.run([sys.executable, str(ROOT / "bin" / "loam-install"), "--help"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(res_py.returncode, 0)
        self.assertIn("--data-dir", res_py.stdout)


if __name__ == "__main__":
    unittest.main()
