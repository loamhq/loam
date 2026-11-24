#!/usr/bin/env python3
"""Tests for Loam's native systemd login services (Authelia + Unix-socket Redis).

Covers:
- Pinned Authelia binary download, SHA-256 verification, mismatch refusal, version
  check, atomic symlink swap, and old-version pruning.
- Hardened systemd units (`loam-authelia.service`, `loam-authelia-redis.service`)
  and native Authelia config (loopback 127.0.0.1:9091, Unix-socket Redis,
  --data-dir mount dependencies).
- PTY password hashing without argv/env/disk exposure.
- WAL-safe SQLite copy + users_database.yml copy (both cross-dir and same-dir).
- Container -> native service migration (happy path, forced-failure automatic
  rollback to Docker, pre-cutover Redis failure, post-cutover crash recovery).
- Pre-service rollback via `gen-deploy --restore-container-login` (including
  pre-pull guard when offline/pull fails).
- Drift detection (`login_findings`), `loam-status` `login_health()`, `bin/logs`,
  and Docker-free `bin/loam-claim`.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import loam_deploy as dep  # noqa: E402


def load_cmd(name: str):
    path = ROOT / "bin" / name
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _make_fake_authelia_tarball(dest_tar: Path, version: str = dep.AUTHELIA_VERSION,
                                extra_script: str = "") -> str:
    """Create a .tar.gz containing an executable `authelia` script and return its sha256."""
    script = (
        "#!/bin/sh\n"
        f"{extra_script}\n"
        'if [ "$1" = "--version" ]; then\n'
        f'  echo "authelia version {version}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    ).encode("utf-8")
    with tarfile.open(dest_tar, "w:gz") as tf:
        ti = tarfile.TarInfo("authelia")
        ti.size = len(script)
        ti.mode = 0o755
        tf.addfile(ti, io.BytesIO(script))
    return hashlib.sha256(dest_tar.read_bytes()).hexdigest()


class TestConfigAndUnits(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loam-login-unit-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_authelia_config_uses_loopback_native_paths_and_unix_socket(self):
        with patch.object(dep, "domain", return_value="sam.example.test"), \
                patch.object(dep, "records", return_value=[]), \
                patch.object(dep, "login_data_dir", return_value=None):
            rendered = dep.authelia_config()
        cfg = yaml.safe_load(rendered)
        self.assertEqual(cfg["server"]["address"], f"tcp://127.0.0.1:{dep.AUTHELIA_PORT}")
        self.assertEqual(cfg["authentication_backend"]["file"]["path"],
                         f"{dep.AUTHELIA_DEFAULT_DATA_DIR}/users_database.yml")
        self.assertEqual(cfg["storage"]["local"]["path"],
                         f"{dep.AUTHELIA_DEFAULT_DATA_DIR}/db.sqlite3")
        self.assertEqual(cfg["notifier"]["filesystem"]["filename"],
                         f"{dep.AUTHELIA_DEFAULT_DATA_DIR}/notification.txt")
        self.assertEqual(cfg["session"]["redis"]["host"], str(dep.AUTHELIA_REDIS_SOCKET))
        self.assertEqual(cfg["session"]["redis"]["port"], 0)
        self.assertNotIn("SECRET", rendered)
        self.assertNotIn("ENCRYPTION", rendered)

    def test_authelia_config_and_unit_honor_data_dir(self):
        disk_root = self.tmp / "disk"
        dd_path = disk_root / "login" / "authelia-data"
        with patch.object(dep, "domain", return_value="sam.example.test"), \
                patch.object(dep, "records", return_value=[]), \
                patch.object(dep.lc, "DATA_DIR", disk_root):
            cfg = yaml.safe_load(dep.authelia_config())
            unit = dep.authelia_unit()
        self.assertEqual(cfg["authentication_backend"]["file"]["path"], f"{dd_path}/users_database.yml")
        self.assertEqual(cfg["storage"]["local"]["path"], f"{dd_path}/db.sqlite3")
        self.assertIn(f"RequiresMountsFor={disk_root}", unit)
        self.assertIn(f"ReadWritePaths={dd_path} {dep.AUTHELIA_REDIS_RUN_DIR}", unit)

    def test_systemd_units_security_posture(self):
        with patch.object(dep, "login_data_dir", return_value=None), \
                patch.object(dep, "redis_server_bin", return_value="/usr/bin/redis-server"):
            auth_unit = dep.authelia_unit()
            redis_unit = dep.authelia_redis_unit()

        # Authelia unit hardening and bindings
        for needle in (
            f"User={dep.AUTHELIA_USER}",
            f"Group={dep.AUTHELIA_GROUP}",
            f"EnvironmentFile={dep.AUTHELIA_SECRETS}",
            f"ExecStart={dep.AUTHELIA_BIN} --config {dep.AUTHELIA_CONFIG}",
            f"After=network-online.target {dep.AUTHELIA_REDIS_SERVICE_NAME}",
            f"Requires={dep.AUTHELIA_REDIS_SERVICE_NAME}",
            "OOMScoreAdjust=0",
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "PrivateTmp=yes",
            "PrivateDevices=yes",
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK",
            "MemoryMax=",
        ):
            if needle == "MemoryMax=":
                self.assertNotIn(needle, auth_unit)
            else:
                self.assertIn(needle, auth_unit)

        # Redis unit: strictly AF_UNIX, port 0, mode 600 socket, RuntimeDirectoryPreserve=yes
        for needle in (
            f"User={dep.AUTHELIA_USER}",
            f"Group={dep.AUTHELIA_GROUP}",
            "--port 0",
            f"--unixsocket {dep.AUTHELIA_REDIS_SOCKET}",
            "--unixsocketperm 600",
            "RuntimeDirectory=loam-authelia-redis",
            "RuntimeDirectoryMode=0750",
            "RuntimeDirectoryPreserve=yes",
            "RestrictAddressFamilies=AF_UNIX",
            "OOMScoreAdjust=0",
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
        ):
            self.assertIn(needle, redis_unit)
        self.assertNotIn("AF_INET", redis_unit)
        self.assertNotIn("MemoryMax=", redis_unit)


class TestBinaryPinAndVerification(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loam-login-bin-"))
        self.gd = load_cmd("gen-deploy")
        self.opt_root = self.tmp / "opt-authelia"
        self.versions_dir = self.opt_root / "versions"
        self.cur_link = self.opt_root / "current"
        self.cur_bin = self.cur_link / "authelia"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_download_verify_idempotent_and_prune(self):
        tar_path = self.tmp / "authelia-good.tar.gz"
        sha = _make_fake_authelia_tarball(tar_path, dep.AUTHELIA_VERSION)
        downloads: list[str] = []

        def fake_fetch(url: str) -> bytes:
            downloads.append(url)
            return tar_path.read_bytes()

        # Pre-seed 3 older versions to verify pruning keeps AUTHELIA_KEEP_VERSIONS (2)
        for old_v in ("4.38.0", "4.39.0", "4.39.10"):
            d = self.versions_dir / old_v
            d.mkdir(parents=True)
            (d / "authelia").write_text("old\n", encoding="utf-8")

        with patch.object(dep, "AUTHELIA_ROOT", self.opt_root), \
                patch.object(dep, "AUTHELIA_VERSIONS", self.versions_dir), \
                patch.object(dep, "AUTHELIA_BIN", self.cur_bin), \
                patch.object(dep, "authelia_arch", return_value="amd64"), \
                patch.dict(dep.AUTHELIA_BUILDS, {"amd64": {"url": "https://example.test/a.tgz", "sha256": sha}}), \
                patch.object(self.gd, "_fetch_url", side_effect=fake_fetch):
            changed1 = self.gd.ensure_authelia_binary(dry=False)
            self.assertTrue(changed1)
            self.assertEqual(len(downloads), 1)
            self.assertTrue(self.cur_bin.is_file())
            self.assertEqual(dep.authelia_live_version(), dep.AUTHELIA_VERSION)
            # Oldest version pruned, 2 newest old + active remain
            remaining = sorted(p.name for p in self.versions_dir.iterdir())
            self.assertEqual(remaining, ["4.39.0", "4.39.10", dep.AUTHELIA_VERSION])

            # Second run is a no-op with zero downloads
            changed2 = self.gd.ensure_authelia_binary(dry=False)
            self.assertFalse(changed2)
            self.assertEqual(len(downloads), 1)

    def test_checksum_mismatch_refused_without_touching_current(self):
        tar_path = self.tmp / "authelia-tampered.tar.gz"
        _make_fake_authelia_tarball(tar_path, dep.AUTHELIA_VERSION)

        with patch.object(dep, "AUTHELIA_ROOT", self.opt_root), \
                patch.object(dep, "AUTHELIA_VERSIONS", self.versions_dir), \
                patch.object(dep, "AUTHELIA_BIN", self.cur_bin), \
                patch.object(dep, "authelia_arch", return_value="amd64"), \
                patch.dict(dep.AUTHELIA_BUILDS, {"amd64": {"url": "https://example.test/a.tgz", "sha256": "0" * 64}}), \
                patch.object(self.gd, "_fetch_url", side_effect=lambda u: tar_path.read_bytes()):
            with self.assertRaises(SystemExit) as cm:
                self.gd.ensure_authelia_binary(dry=False)
        self.assertNotEqual(cm.exception.code, 0)
        self.assertFalse(self.cur_link.exists())

    def test_wrong_reported_version_refused(self):
        tar_path = self.tmp / "authelia-wrongver.tar.gz"
        sha = _make_fake_authelia_tarball(tar_path, "4.0.0")

        with patch.object(dep, "AUTHELIA_ROOT", self.opt_root), \
                patch.object(dep, "AUTHELIA_VERSIONS", self.versions_dir), \
                patch.object(dep, "AUTHELIA_BIN", self.cur_bin), \
                patch.object(dep, "authelia_arch", return_value="amd64"), \
                patch.dict(dep.AUTHELIA_BUILDS, {"amd64": {"url": "https://example.test/a.tgz", "sha256": sha}}), \
                patch.object(self.gd, "_fetch_url", side_effect=lambda u: tar_path.read_bytes()):
            with self.assertRaises(SystemExit):
                self.gd.ensure_authelia_binary(dry=False)
        self.assertFalse(self.cur_link.exists())


class TestPtyPasswordHashing(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loam-login-pty-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hash_password_uses_pty_and_never_passes_secret_in_argv_or_env(self):
        # Fake authelia binary that checks stdin+stdout are TTYs, checks argv/env
        # contain no password, prompts for Password:, reads line, outputs $argon2id$ hash.
        fake_bin = self.tmp / "authelia"
        fake_bin.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys, termios\n"
            "secret_marker = 'p@ss!w0rd#café'\n"
            "assert secret_marker not in ' '.join(sys.argv), 'password leaked in argv'\n"
            "assert all(secret_marker not in v for v in os.environ.values()), 'password leaked in env'\n"
            "assert os.isatty(0) and os.isatty(1), 'must run attached to a PTY'\n"
            "sys.stdout.write('Enter Password: ')\n"
            "sys.stdout.flush()\n"
            "pw = sys.stdin.readline().rstrip('\\r\\n')\n"
            "assert pw == secret_marker, f'got {pw!r}'\n"
            "print('Digest: $argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$dGVzdGhhc2g')\n",
            encoding="utf-8",
        )
        fake_bin.chmod(0o755)
        digest = dep.hash_password("p@ss!w0rd#café", binary=fake_bin)
        self.assertEqual(digest, "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$dGVzdGhhc2g")


class TestDataCopyAndMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loam-login-mig-"))
        self.gd = load_cmd("gen-deploy")
        self.me = (os.getuid(), os.getgid())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_login_dir(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "users_database.yml").write_text(
            "users:\n  sam:\n    displayname: sam\n    password: '$argon2id$v=19$m=65536,t=3,p=4$a$b'\n",
            encoding="utf-8",
        )
        (folder / "users_database.yml").chmod(0o600)
        (folder / "notification.txt").write_text("One-Time Code: 123456\n", encoding="utf-8")
        # Create WAL-mode SQLite DB with uncommitted WAL data (open connection kept during copy)
        db = folder / "db.sqlite3"
        con = sqlite3.connect(db)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE webauthn_credentials (id INTEGER PRIMARY KEY, label TEXT)")
        con.execute("INSERT INTO webauthn_credentials (label) VALUES ('yubikey-1'), ('phone-passkey')")
        con.commit()
        con.close()

    def test_copy_login_data_cross_dir_and_same_dir(self):
        src = self.tmp / "vol"
        dst = self.tmp / "native"
        self._seed_login_dir(src)
        self.gd.copy_login_data(src, dst, *self.me)

        self.assertTrue((dst / "users_database.yml").is_file())
        self.assertEqual((dst / "users_database.yml").stat().st_mode & 0o777, 0o600)
        self.assertTrue((dst / "notification.txt").is_file())
        self.assertFalse((dst / "db.sqlite3-wal").exists())
        self.assertFalse((dst / "db.sqlite3-shm").exists())
        con = sqlite3.connect(dst / "db.sqlite3")
        try:
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            rows = [r[0] for r in con.execute("SELECT label FROM webauthn_credentials ORDER BY id")]
        finally:
            con.close()
        self.assertEqual(rows, ["yubikey-1", "phone-passkey"])

        # Same-directory (--data-dir) copy leaves data intact and mode 0600
        self.gd.copy_login_data(dst, dst, *self.me)
        con = sqlite3.connect(dst / "db.sqlite3")
        try:
            rows2 = [r[0] for r in con.execute("SELECT label FROM webauthn_credentials ORDER BY id")]
        finally:
            con.close()
        self.assertEqual(rows2, ["yubikey-1", "phone-passkey"])

    def test_migration_happy_path_cleans_up_docker_only_after_health(self):
        vol = self.tmp / "vol"
        native = self.tmp / "native"
        etc = self.tmp / "etc"
        units = self.tmp / "units"
        etc.mkdir()
        units.mkdir()
        self._seed_login_dir(vol)

        cfg = etc / "configuration.yml"
        cfg.write_text("authentication_backend:\n  file:\n    path: /data/users_database.yml\n", encoding="utf-8")
        backup = etc / ".configuration.yml.container-backup"
        compose_f = etc / "compose.yaml"
        compose_f.write_text("services:\n  authelia: {}\n", encoding="utf-8")
        auth_unit = units / dep.AUTHELIA_SERVICE_NAME
        redis_unit = units / dep.AUTHELIA_REDIS_SERVICE_NAME
        auth_unit.write_text("[Unit]\n", encoding="utf-8")
        redis_unit.write_text("[Unit]\n", encoding="utf-8")

        events: list[str] = []

        def fake_run(argv, **kw):
            events.append(" ".join(argv))
            if argv[:3] == ["systemctl", "is-active", "--quiet"]:
                return subprocess.CompletedProcess(argv, 3, "", "")
            if argv[:2] == ["docker", "inspect"]:
                return subprocess.CompletedProcess(argv, 0, f"{dep.AUTHELIA_IMAGE}\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        def fake_compose(path, *args):
            events.append(f"compose {' '.join(args)}")
            return subprocess.CompletedProcess(["docker", "compose"], 0, "", "")

        new_cfg = f"authentication_backend:\n  file:\n    path: {native}/users_database.yml\n"

        with patch.multiple(dep, AUTHELIA_CONFIG=cfg, AUTHELIA_CONFIG_BACKUP=backup,
                            AUTHELIA_COMPOSE=compose_f, AUTHELIA_UNIT=auth_unit,
                            AUTHELIA_REDIS_UNIT=redis_unit,
                            authelia_data_dir=lambda: native, docker_active=lambda: True,
                            redis_ping=lambda: True, login_health_ok=lambda **kw: (True, "ok")), \
                patch.object(self.gd, "_authelia_ids", return_value=self.me), \
                patch.object(self.gd, "run", side_effect=fake_run), \
                patch.object(self.gd, "compose", side_effect=fake_compose), \
                patch.object(self.gd, "write_root", side_effect=lambda p, t, m=0o644: p.write_text(t, encoding="utf-8")), \
                patch.object(self.gd, "migrate_sessions_to_native", return_value=(12, "migrated 12 session key(s)")), \
                patch.object(self.gd, "check_native_login_ready", return_value=(True, "ok")), \
                patch("loam_data_disk.docker_volume_path", return_value=vol):
            self.gd.migrate_container_login(new_cfg)

        self.assertEqual(cfg.read_text(encoding="utf-8"), new_cfg)
        self.assertTrue((native / "users_database.yml").is_file())
        self.assertFalse(compose_f.exists())
        self.assertFalse(backup.exists())
        self.assertIn("compose down --remove-orphans", events)
        self.assertIn("docker volume rm authelia-data", events)
        self.assertIn("docker volume rm authelia-sessions", events)

    def test_migration_forced_failure_rolls_back_to_docker_and_preserves_volume(self):
        vol = self.tmp / "vol"
        native = self.tmp / "native"
        etc = self.tmp / "etc"
        units = self.tmp / "units"
        etc.mkdir()
        units.mkdir()
        self._seed_login_dir(vol)

        old_cfg = "authentication_backend:\n  file:\n    path: /data/users_database.yml\n"
        cfg = etc / "configuration.yml"
        cfg.write_text(old_cfg, encoding="utf-8")
        backup = etc / ".configuration.yml.container-backup"
        compose_f = etc / "compose.yaml"
        compose_f.write_text("services:\n  authelia: {}\n", encoding="utf-8")
        auth_unit = units / dep.AUTHELIA_SERVICE_NAME
        redis_unit = units / dep.AUTHELIA_REDIS_SERVICE_NAME
        auth_unit.write_text("[Unit]\n", encoding="utf-8")
        redis_unit.write_text("[Unit]\n", encoding="utf-8")

        events: list[str] = []

        def fake_run(argv, **kw):
            events.append(" ".join(argv))
            if argv[:3] == ["systemctl", "is-active", "--quiet"]:
                return subprocess.CompletedProcess(argv, 3, "", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        def fake_compose(path, *args):
            events.append(f"compose {' '.join(args)}")
            return subprocess.CompletedProcess(["docker", "compose"], 0, "", "")

        class FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        new_cfg = f"authentication_backend:\n  file:\n    path: {native}/users_database.yml\n"

        with patch.multiple(dep, AUTHELIA_CONFIG=cfg, AUTHELIA_CONFIG_BACKUP=backup,
                            AUTHELIA_COMPOSE=compose_f, AUTHELIA_UNIT=auth_unit,
                            AUTHELIA_REDIS_UNIT=redis_unit,
                            authelia_data_dir=lambda: native, docker_active=lambda: True,
                            redis_ping=lambda: True), \
                patch.object(self.gd, "_authelia_ids", return_value=self.me), \
                patch.object(self.gd, "run", side_effect=fake_run), \
                patch.object(self.gd, "compose", side_effect=fake_compose), \
                patch.object(self.gd, "write_root", side_effect=lambda p, t, m=0o644: p.write_text(t, encoding="utf-8")), \
                patch.object(self.gd, "migrate_sessions_to_native", return_value=(5, "migrated 5 session key(s)")), \
                patch.object(self.gd, "check_native_login_ready", return_value=(False, "simulated startup crash")), \
                patch("urllib.request.urlopen", return_value=FakeResp()), \
                patch("loam_data_disk.docker_volume_path", return_value=vol):
            with self.assertRaises(SystemExit) as cm:
                self.gd.migrate_container_login(new_cfg)

        self.assertNotEqual(cm.exception.code, 0)
        # Old config restored, native units removed, compose file + Docker volume kept, Docker brought back up!
        self.assertEqual(cfg.read_text(encoding="utf-8"), old_cfg)
        self.assertFalse(auth_unit.exists())
        self.assertFalse(redis_unit.exists())
        self.assertTrue(compose_f.exists())
        self.assertFalse(backup.exists())
        self.assertIn("compose up -d --force-recreate", events)
        self.assertFalse(any("volume rm" in e for e in events))

    def test_pre_cutover_redis_failure_leaves_docker_untouched(self):
        vol = self.tmp / "vol"
        native = self.tmp / "native"
        etc = self.tmp / "etc"
        units = self.tmp / "units"
        etc.mkdir()
        units.mkdir()
        self._seed_login_dir(vol)

        old_cfg = "authentication_backend:\n  file:\n    path: /data/users_database.yml\n"
        cfg = etc / "configuration.yml"
        cfg.write_text(old_cfg, encoding="utf-8")
        backup = etc / ".configuration.yml.container-backup"
        auth_unit = units / dep.AUTHELIA_SERVICE_NAME
        redis_unit = units / dep.AUTHELIA_REDIS_SERVICE_NAME
        auth_unit.write_text("[Unit]\n", encoding="utf-8")
        redis_unit.write_text("[Unit]\n", encoding="utf-8")

        events: list[str] = []

        def fake_run(argv, **kw):
            events.append(" ".join(argv))
            if argv[:3] == ["systemctl", "enable", "--now"]:
                return subprocess.CompletedProcess(argv, 1, "", "redis failed")
            if argv[:3] == ["systemctl", "is-active", "--quiet"]:
                return subprocess.CompletedProcess(argv, 3, "", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.multiple(dep, AUTHELIA_CONFIG=cfg, AUTHELIA_CONFIG_BACKUP=backup,
                            AUTHELIA_UNIT=auth_unit, AUTHELIA_REDIS_UNIT=redis_unit,
                            authelia_data_dir=lambda: native, docker_active=lambda: True), \
                patch.object(self.gd, "_authelia_ids", return_value=self.me), \
                patch.object(self.gd, "run", side_effect=fake_run), \
                patch.object(self.gd, "write_root", side_effect=lambda p, t, m=0o644: p.write_text(t, encoding="utf-8")), \
                patch("loam_data_disk.docker_volume_path", return_value=vol):
            with self.assertRaises(SystemExit):
                self.gd.migrate_container_login("new config")

        self.assertEqual(cfg.read_text(encoding="utf-8"), old_cfg)
        self.assertFalse(auth_unit.exists())
        self.assertFalse(redis_unit.exists())
        self.assertFalse(backup.exists())
        self.assertFalse(any(e.startswith("docker stop") for e in events))

    def test_interrupted_after_cutover_resumes_docker_cleanup_only(self):
        native = self.tmp / "native"
        self._seed_login_dir(native)
        etc = self.tmp / "etc"
        etc.mkdir()
        compose_f = etc / "compose.yaml"
        compose_f.write_text("services:\n  authelia: {}\n", encoding="utf-8")
        backup = etc / ".configuration.yml.container-backup"
        backup.write_text("old\n", encoding="utf-8")

        events: list[str] = []

        def fake_run(argv, **kw):
            events.append(" ".join(argv))
            if argv[:3] == ["systemctl", "is-active", "--quiet"]:
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.multiple(dep, AUTHELIA_COMPOSE=compose_f, AUTHELIA_CONFIG_BACKUP=backup,
                            authelia_data_dir=lambda: native, docker_active=lambda: True,
                            login_health_ok=lambda **kw: (True, "ok")), \
                patch.object(self.gd, "_authelia_ids", return_value=self.me), \
                patch.object(self.gd, "run", side_effect=fake_run), \
                patch.object(self.gd, "compose", return_value=subprocess.CompletedProcess(["docker"], 0, "", "")):
            self.gd.migrate_container_login("new config")

        self.assertFalse(compose_f.exists())
        self.assertFalse(backup.exists())
        self.assertIn("docker volume rm authelia-data", events)

    def test_restore_container_login_refuses_if_image_pull_fails_without_stopping_native(self):
        native = self.tmp / "native"
        self._seed_login_dir(native)
        auth_unit = self.tmp / dep.AUTHELIA_SERVICE_NAME
        auth_unit.write_text("[Unit]\n", encoding="utf-8")
        events: list[str] = []

        def fake_run(argv, **kw):
            events.append(" ".join(argv))
            if argv[:2] == ["docker", "image"] and "inspect" in argv:
                return subprocess.CompletedProcess(argv, 1, "", "not found")
            if argv[:2] == ["docker", "pull"]:
                return subprocess.CompletedProcess(argv, 1, "", "network unreachable")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.multiple(dep, AUTHELIA_UNIT=auth_unit, authelia_data_dir=lambda: native,
                            docker_active=lambda: True), \
                patch.object(self.gd.shutil, "which", return_value="/usr/bin/docker"), \
                patch.object(self.gd, "run", side_effect=fake_run):
            with self.assertRaises(SystemExit):
                self.gd.restore_container_login()

        # Never stopped the running native service!
        self.assertFalse(any("systemctl stop" in e or "systemctl disable" in e for e in events))


class TestDriftStatusLogsAndClaim(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loam-login-ops-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_login_findings_healthy_and_drifted(self):
        secrets = self.tmp / "secrets.env"
        secrets.write_text("AUTHELIA_JWT_SECRET=x\n", encoding="utf-8")
        secrets.chmod(0o600)
        compose_f = self.tmp / "compose.yaml"
        auth_bin = self.tmp / "authelia"
        auth_bin.write_text(
            f"#!/bin/sh\necho 'authelia version {dep.AUTHELIA_VERSION}'\n",
            encoding="utf-8",
        )
        auth_bin.chmod(0o755)
        redis_bin = self.tmp / "redis-server"
        redis_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        redis_bin.chmod(0o755)
        data_dir = self.tmp / "data"
        data_dir.mkdir(mode=0o700)
        os.chmod(data_dir, 0o700)
        (data_dir / "users_database.yml").write_text("users: {}\n", encoding="utf-8")

        fake_pw = type("PW", (), {"pw_uid": os.getuid(), "pw_gid": os.getgid()})()
        real_stat = os.stat

        def fake_stat(path, *args, **kwargs):
            st = real_stat(path, *args, **kwargs)
            if Path(path) == secrets:
                return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                                       0, 0, st.st_size, st.st_atime, st.st_mtime, st.st_ctime))
            return st

        def ok_sudo(argv):
            if argv[:2] == ["systemctl", "is-active"]:
                return subprocess.CompletedProcess(argv, 0, "active\n", "")
            if argv[:2] == ["systemctl", "is-enabled"]:
                return subprocess.CompletedProcess(argv, 0, "enabled\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.multiple(dep, AUTHELIA_SECRETS=secrets, AUTHELIA_COMPOSE=compose_f,
                            AUTHELIA_BIN=auth_bin, unclaimed=lambda: False,
                            authelia_data_dir=lambda: data_dir,
                            authelia_live_version=lambda: dep.AUTHELIA_VERSION,
                            redis_server_bin=lambda: str(redis_bin),
                            docker_active=lambda: False,
                            login_health_ok=lambda **kw: (True, "ok"),
                            _sudo=ok_sudo), \
                patch("pwd.getpwnam", return_value=fake_pw), \
                patch("os.stat", side_effect=fake_stat):
            self.assertEqual(dep.login_findings(), [])

        # Leftover compose.yaml + world-readable secrets.env + wrong version -> all reported
        compose_f.write_text("services: {}\n", encoding="utf-8")
        secrets.chmod(0o644)
        with patch.multiple(dep, AUTHELIA_SECRETS=secrets, AUTHELIA_COMPOSE=compose_f,
                            AUTHELIA_BIN=auth_bin, unclaimed=lambda: False,
                            authelia_data_dir=lambda: data_dir,
                            authelia_live_version=lambda: "4.0.0",
                            redis_server_bin=lambda: str(redis_bin),
                            docker_active=lambda: False,
                            login_health_ok=lambda **kw: (False, "502"),
                            _sudo=ok_sudo), \
                patch("pwd.getpwnam", return_value=fake_pw), \
                patch("os.stat", side_effect=fake_stat):
            findings = dep.login_findings()
        labels = [f[0] for f in findings]
        self.assertIn("authelia binary", labels)
        self.assertIn("authelia secrets", labels)
        self.assertIn("authelia legacy compose", labels)

    def test_loam_status_login_health_native_without_docker(self):
        st = load_cmd("loam-status")
        unit = self.tmp / dep.AUTHELIA_SERVICE_NAME
        unit.write_text("[Unit]\n", encoding="utf-8")
        compose_f = self.tmp / "compose.yaml"

        class FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def no_docker_run(argv, timeout=10):
            if argv[0] == "docker":
                raise AssertionError("loam-status must not invoke docker in native mode")
            return subprocess.CompletedProcess(argv, 0, "active\n", "")

        with patch.object(st.dep, "AUTHELIA_UNIT", unit), \
                patch.object(st.dep, "AUTHELIA_COMPOSE", compose_f), \
                patch.object(st, "run", side_effect=no_docker_run), \
                patch("urllib.request.urlopen", return_value=FakeResp()):
            got = st.login_health()
        self.assertEqual(got, "running healthy")

    def test_logs_routes_authelia_and_redis_to_journalctl(self):
        logs = load_cmd("logs")
        unit = self.tmp / dep.AUTHELIA_SERVICE_NAME
        unit.write_text("[Unit]\n", encoding="utf-8")
        compose_f = self.tmp / "compose.yaml"

        with patch.object(logs.dep, "AUTHELIA_UNIT", unit), \
                patch.object(logs.dep, "AUTHELIA_COMPOSE", compose_f):
            cmd_auth = logs.command("authelia", None, 25, False)
            cmd_redis = logs.command("authelia-redis", None, 50, True)

        self.assertEqual(cmd_auth[:3], ["journalctl", "-u", dep.AUTHELIA_SERVICE_NAME])
        self.assertIn("25", cmd_auth)
        self.assertNotIn("-f", cmd_auth)
        self.assertEqual(cmd_redis[:3], ["journalctl", "-u", dep.AUTHELIA_REDIS_SERVICE_NAME])
        self.assertIn("-f", cmd_redis)

    def test_loam_claim_writes_native_users_without_docker(self):
        claim = load_cmd("loam-claim")
        data_dir = self.tmp / "authelia-data"
        compose_f = self.tmp / "no-compose.yaml"

        with patch.object(claim.dep, "authelia_data_dir", return_value=data_dir), \
                patch.object(claim.dep, "AUTHELIA_COMPOSE", compose_f), \
                patch.object(claim.dep, "domain", return_value="sam.example.test"):
            self.assertEqual(claim.volume_dir(), data_dir)
            claim.write_users("sam", "Sam", "$argon2id$v=19$m=65536,t=3,p=4$a$b")

        users_file = data_dir / "users_database.yml"
        self.assertTrue(users_file.is_file())
        self.assertEqual(stat.S_IMODE(users_file.stat().st_mode), 0o600)
        parsed = yaml.safe_load(users_file.read_text(encoding="utf-8"))
        self.assertIn("sam", parsed["users"])
        self.assertEqual(parsed["users"]["sam"]["groups"], ["admins"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
