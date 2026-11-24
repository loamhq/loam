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
        with patch.object(lh.shutil, "which", return_value=str(fake)), \
                patch.object(lh, "LOGIN_NATIVE_DIR", str(self.base / "no-native-login")), \
                patch.object(lh, "LOGIN_SERVICE_UNIT", str(self.base / "no-native.service")), \
                patch.object(lh, "LOGIN_LEGACY_COMPOSE", str(self.base / "no-compose.yaml")):
            folder, note = lh.find_login()
        self.assertEqual(folder, str(login_dir), note)

    def test_gen_deploy_ensure_login_data_dir_plain_vs_data_dir(self):
        gd = load_cmd("gen-deploy")
        plain_dir = self.base / "default-authelia"
        me = (os.getuid(), os.getgid())

        # Without data_dir: creates default native dir with 0700 ownership.
        with patch.object(gd.dep, "login_data_dir", return_value=None), \
                patch.object(gd.dep, "AUTHELIA_DEFAULT_DATA_DIR", plain_dir), \
                patch.object(gd, "_authelia_ids", return_value=me):
            gd.ensure_login_data_dir(dry=False)
        self.assertTrue(plain_dir.is_dir())
        self.assertEqual(plain_dir.stat().st_mode & 0o777, 0o700)

        # With data_dir: creates <data_dir>/login/authelia-data directly without Docker.
        target = self.base / "disk" / "login" / "authelia-data"
        with patch.object(gd.dep, "login_data_dir", return_value=target), \
                patch.object(gd, "_authelia_ids", return_value=me):
            gd.ensure_login_data_dir(dry=False)
        self.assertTrue(target.is_dir())
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)
        # Idempotent on re-run.
        with patch.object(gd.dep, "login_data_dir", return_value=target), \
                patch.object(gd, "_authelia_ids", return_value=me):
            gd.ensure_login_data_dir(dry=False)
        self.assertTrue(target.is_dir())

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
        systemd_dir = self.base / "systemd"
        fstab.write_text(f"UUID=test-uuid {data_dir} ext4 discard,nofail,defaults 0 0\n")
        specs = dd.mount_specs(data_dir, home, etc=etc, local_base=local_base)
        dd.update_fstab(specs, fstab=fstab)
        dd.install_boot_ordering(
            data_dir, home, uid=1000, etc=etc, local_base=local_base,
            systemd_dir=systemd_dir, enable=False)

        # Native login mode: checks configuration.yml without waking Docker.
        auth_dir = etc / "authelia"
        auth_dir.mkdir(parents=True)
        cfg = auth_dir / "configuration.yml"
        cfg.write_text(
            f"authentication_backend:\n  file:\n    path: {dd.login_data_dir(data_dir)}/users_database.yml\n"
            f"storage:\n  local:\n    path: {dd.login_data_dir(data_dir)}/db.sqlite3\n",
            encoding="utf-8",
        )
        docker_called = []

        def fail_if_docker_called(cmd):
            docker_called.append(cmd)
            raise AssertionError("Docker should not be invoked in native login mode")

        good_systemd = lambda cmd: subprocess.CompletedProcess(cmd, 0, "enabled\n", "")

        with patch.object(dd, "is_separate_mount", return_value=True), \
                patch.object(dd, "is_bind_mounted", return_value=True):
            got = dd.drift_findings(
                data_dir, home, etc=etc, local_base=local_base, fstab=fstab,
                uid=1000, systemd_dir=systemd_dir,
                docker_run=fail_if_docker_called, systemd_run=good_systemd)
        self.assertEqual(got, [])
        self.assertEqual(docker_called, [])

        # Native login config pointing outside data disk -> reported without Docker.
        cfg.write_text(
            "authentication_backend:\n  file:\n    path: /var/lib/loam-authelia/users_database.yml\n"
            "storage:\n  local:\n    path: /var/lib/loam-authelia/db.sqlite3\n",
            encoding="utf-8",
        )
        with patch.object(dd, "is_separate_mount", return_value=True), \
                patch.object(dd, "is_bind_mounted", return_value=True):
            got = dd.drift_findings(
                data_dir, home, etc=etc, local_base=local_base, fstab=fstab,
                uid=1000, systemd_dir=systemd_dir,
                docker_run=fail_if_docker_called, systemd_run=good_systemd)
        self.assertIn("data disk login config", [g[0] for g in got])

        # Legacy Docker compose mode: still checks Docker bind volume.
        cfg.unlink()
        (auth_dir / "compose.yaml").write_text("services:\n  authelia: {}\n", encoding="utf-8")
        good_docker = lambda cmd: subprocess.CompletedProcess(
            cmd, 0, json.dumps({"Options": {"device": str(dd.login_data_dir(data_dir))}}), "")
        with patch.object(dd, "is_separate_mount", return_value=True), \
                patch.object(dd, "is_bind_mounted", return_value=True):
            got = dd.drift_findings(
                data_dir, home, etc=etc, local_base=local_base, fstab=fstab,
                uid=1000, systemd_dir=systemd_dir,
                docker_run=good_docker, systemd_run=good_systemd)
        self.assertEqual(got, [])

        # Missing bind mount + non-bind Docker volume + same-device root + disabled gate unit -> reported.
        bad_docker = lambda cmd: subprocess.CompletedProcess(
            cmd, 0, json.dumps({"Options": None, "Mountpoint": "/var/lib/docker/volumes/authelia-data/_data"}), "")
        bad_systemd = lambda cmd: subprocess.CompletedProcess(cmd, 1, "disabled\n", "")
        with patch.object(dd, "is_separate_mount", return_value=False), \
                patch.object(dd, "is_bind_mounted", return_value=False):
            got = dd.drift_findings(
                data_dir, home, etc=etc, local_base=local_base, fstab=fstab,
                uid=1000, systemd_dir=systemd_dir,
                docker_run=bad_docker, systemd_run=bad_systemd)
        labels = [g[0] for g in got]
        self.assertIn("data disk", labels)
        self.assertIn(f"data disk mount {home}", labels)
        self.assertIn("data disk login volume", labels)
        self.assertIn(f"data disk boot unit {dd.GATE_UNIT_NAME}", labels)


class TestBootOrderingAndVolumeSeam(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_persistent_mount_detection_ignores_managed_block(self):
        fstab = self.base / "fstab"
        systemd_dir = self.base / "systemd"
        systemd_dir.mkdir()
        data_dir = Path("/mnt/loam-data")

        # Only managed bind-mount block present -> NOT a persistent mount for data_dir itself.
        specs = dd.mount_specs(data_dir, Path("/home/loam"))
        dd.update_fstab(specs, fstab=fstab)
        self.assertFalse(dd.fstab_has_mount(data_dir, fstab=fstab))
        self.assertFalse(dd.has_persistent_mount(data_dir, fstab=fstab, systemd_dir=systemd_dir))

        # Standard cloud volume fstab entry (Hetzner / DigitalOcean style) -> detected.
        fstab.write_text(
            "UUID=1111 / ext4 defaults 0 1\n"
            "UUID=2222 /mnt/loam-data ext4 discard,nofail,defaults 0 0\n"
            + fstab.read_text()
        )
        self.assertTrue(dd.fstab_has_mount(data_dir, fstab=fstab))
        self.assertTrue(dd.has_persistent_mount(data_dir, fstab=fstab, systemd_dir=systemd_dir))

        # Systemd .mount unit without fstab entry -> also detected.
        self.assertEqual(dd.systemd_escape_mount(Path("/mnt/loam-data")), r"mnt-loam\x2ddata.mount")
        self.assertEqual(dd.systemd_escape_mount(Path("/")), "-.mount")
        empty_fstab = self.base / "empty-fstab"
        empty_fstab.write_text("")
        (systemd_dir / dd.systemd_escape_mount(data_dir)).write_text("[Mount]\nWhere=/mnt/loam-data\n")
        self.assertTrue(dd.has_persistent_mount(data_dir, fstab=empty_fstab, systemd_dir=systemd_dir))

    def test_boot_ordering_units_and_dropins(self):
        data_dir = Path("/mnt/loam-data")
        home = Path("/home/loam")
        systemd_dir = self.base / "systemd"
        calls = []

        def fake_run(argv):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        written = dd.install_boot_ordering(
            data_dir, home, uid=1001, systemd_dir=systemd_dir, run=fake_run, enable=True)
        self.assertEqual(len(written), 1 + len(dd.ORDERED_UNITS) + 1)
        self.assertEqual(calls, [
            ["systemctl", "daemon-reload"],
            ["systemctl", "enable", dd.GATE_UNIT_NAME],
            ["systemctl", "start", dd.GATE_UNIT_NAME],
        ])

        gate = (systemd_dir / dd.GATE_UNIT_NAME).read_text()
        self.assertIn("After=local-fs.target", gate)
        self.assertIn("RequiresMountsFor=/mnt/loam-data /etc/loam /home/loam /home/loam/loam", gate)
        self.assertIn("/var/lib/loam/local/loam/bin/loam-data-disk ready", gate)

        for unit in (*dd.ORDERED_UNITS, "user@1001.service"):
            dropin = systemd_dir / f"{unit}.d" / dd.DROPIN_NAME
            self.assertTrue(dropin.is_file(), f"missing drop-in for {unit}")
            text = dropin.read_text()
            self.assertIn(f"Requires={dd.GATE_UNIT_NAME}", text)
            self.assertIn(f"After={dd.GATE_UNIT_NAME}", text)

        # Idempotent second call writes nothing and skips daemon-reload.
        calls.clear()
        written2 = dd.install_boot_ordering(
            data_dir, home, uid=1001, systemd_dir=systemd_dir, run=fake_run, enable=True)
        self.assertEqual(written2, [])
        self.assertNotIn(["systemctl", "daemon-reload"], calls)

    def test_ensure_ready_holds_when_unmounted_and_mounts_missing_binds(self):
        data_dir = self.base / "disk"
        data_dir.mkdir()
        (data_dir / "etc").mkdir()
        (data_dir / "home").mkdir()
        (data_dir / "etc" / "loam.toml").write_text(li.render_config(
            name="", domain="loam.example.test", owner="Sam", timezone="UTC",
            address="203.0.113.10", home="/home/loam", data_dir=str(data_dir)))
        dd.write_marker(data_dir, user="loam", domain="loam.example.test")

        home = self.base / "home-loam"
        etc = self.base / "etc-loam"
        local_base = self.base / "local"
        for sub, _rel, _mode in dd.LOCAL_OVERLAYS:
            (local_base / sub).mkdir(parents=True)

        # Volume not mounted separately -> refuses cleanly so Loam services stay held.
        with patch.object(dd, "is_separate_mount", return_value=False):
            ok, msg = dd.ensure_ready(data_dir, home, etc=etc, local_base=local_base)
        self.assertFalse(ok)
        self.assertIn("not mounted", msg)

        # Volume mounted separately, bind mounts missing -> mounts all 9 cleanly.
        calls = []
        mi = self.base / "mountinfo"
        mi.write_text("21 1 8:1 / / rw - ext4 /dev/sda1 rw\n")

        def fake_run(argv):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.object(dd, "is_separate_mount", return_value=True):
            ok, msg = dd.ensure_ready(
                data_dir, home, etc=etc, local_base=local_base,
                mountinfo_path=mi, run=fake_run)
        self.assertTrue(ok, msg)
        self.assertEqual(len(calls), 2 + len(dd.LOCAL_OVERLAYS))
        self.assertEqual(calls[0], ["mount", "--bind", str(data_dir / "etc"), str(etc)])
        self.assertEqual(calls[1], ["mount", "--bind", str(data_dir / "home"), str(home)])

    def test_inbound_firewall_unit_orders_after_data_disk_when_configured(self):
        import loam_deploy as dep
        with patch.object(dep.lc, "DATA_DIR", None):
            plain = dep.inbound_firewall_unit()
        self.assertIn("Before=network-pre.target", plain)
        self.assertNotIn(dd.GATE_UNIT_NAME, plain)

        with patch.object(dep.lc, "DATA_DIR", Path("/mnt/loam-data")):
            with_disk = dep.inbound_firewall_unit()
        self.assertNotIn("Before=network-pre.target", with_disk)
        self.assertIn(f"Requires={dd.GATE_UNIT_NAME}", with_disk)
        self.assertIn(f"After=local-fs.target {dd.GATE_UNIT_NAME}", with_disk)
        self.assertIn("Before=docker.service caddy.service", with_disk)


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
