#!/usr/bin/env python3
"""The host's own setup, kept and put back (lib/loam_host.py, bin/loam-host),
on scratch folders standing in for /etc and the user unit folder.

What it holds:
- a snapshot commits what the rules cover, and a second one with nothing
  changed writes nothing and makes no commit;
- the history folder is its owner's alone, and a file kept from other users
  is recorded as a secret, its contents never shown by a restore's diff;
- a broken file, a removed enablement link and a stray unit are each named
  in the plan, put back by the restore with their modes, and followed by the
  right reload, start and stop; the present state is saved first, so the
  restore can be undone by naming it;
- the default state is the newest whose checks passed, and a duration or a
  time names the newest state at or before it;
- a state that does not pass its own checks is refused unless forced;
- `--only` puts back only what it names;
- the checkout's tracked files are never covered, its untracked records are;
- a config that cannot be read falls back to the last state's own scope;
- a file over the limit is named and not kept;
- the command refuses to run as anyone but root;
- the login's database is kept as SQLite's online backup, as a secret: an
  unchanged database makes no new state, a changed one does, a copy that
  fails keeps the last one; a restore that includes it stops the login,
  removes the journal and WAL beside it, writes it, starts the login, and
  says in capitals before it asks that newer passkeys and accounts go;
  `--except` its folder puts back the rest and leaves it;
- the login is found by asking Docker, and a machine without Docker or
  without the login's volume has no login rules and says why.

Run: python3 tests/test_host_history.py
"""

from __future__ import annotations

import getpass
import grp
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import loam_host as lh  # noqa: E402

ME = getpass.getuser()
MY_GROUP = grp.getgrgid(os.getgid()).gr_name


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="loam-host-test-")
        base = Path(self.tmp.name)
        self.etc = base / "etc"
        self.units = base / "units"
        self.h = base / "history"
        (self.etc / "caddy").mkdir(parents=True)
        (self.units / "timers.target.wants").mkdir(parents=True)
        (self.etc / "app.toml").write_text('name = "ada"\n')
        (self.etc / "caddy" / "site.conf").write_text("good line\n")
        key = self.etc / "secrets.env"
        key.write_text("TOKEN=s3cr3t-value\n")
        key.chmod(0o600)
        (self.units / "loam-sweep.timer").write_text("[Timer]\nOnCalendar=daily\n")
        (self.units / "loam-sweep.service").write_text("[Service]\nExecStart=/bin/true\n")
        os.symlink("../loam-sweep.timer", self.units / "timers.target.wants" / "loam-sweep.timer")
        self.rules = [lh.Rule(str(self.etc), "**", "config"),
                      lh.Rule(str(self.units), "loam*", "units"),
                      lh.Rule(str(self.units), "*.wants/loam*", "links")]
        self.about = {"user": ME, "user_units": str(self.units), "config_dir": str(self.etc)}
        self.scope = (self.rules, self.about)
        self.ran: list[list[str]] = []
        self._run, self._which, self._active = lh._run, lh.shutil.which, lh._is_active
        lh._run = lambda argv, said: (self.ran.append(argv), said.append("  done"))[0] or True
        lh.shutil.which = lambda name, path=None: f"/usr/bin/{name}"
        lh._is_active = lambda unit: True

    def tearDown(self):
        lh._run, lh.shutil.which, lh._is_active = self._run, self._which, self._active
        self.tmp.cleanup()

    def snap(self, reason=""):
        return lh.snapshot(reason, h=self.h, scope=self.scope)

    def restore(self, name=None, **kw):
        said: list[str] = []
        kw.setdefault("yes", True)
        code = lh.restore(name, h=self.h, scope=self.scope, out=said.append, **kw)
        return code, "\n".join(said)

    def commits(self):
        return int(subprocess.run(["git", "-C", str(self.h), "rev-list", "--count", "HEAD"],
                                  capture_output=True, text=True).stdout.strip() or 0)


class TestSnapshot(Scratch):
    def test_commits_then_costs_nothing(self):
        first = self.snap()
        self.assertTrue(first["commit"])
        self.assertIn(f"A {self.etc}/caddy/site.conf", first["changed"])
        self.assertIn(f"A {self.units}/timers.target.wants/loam-sweep.timer", first["changed"])
        meta = self.h / lh.META
        before = meta.stat().st_mtime_ns
        time.sleep(0.01)
        again = self.snap()
        self.assertIsNone(again["commit"])
        self.assertEqual(meta.stat().st_mtime_ns, before)
        self.assertEqual(self.commits(), 1)

    def test_private_and_secret(self):
        self.snap()
        self.assertEqual(self.h.stat().st_mode & 0o777, 0o700)
        line = next(ln for ln in (self.h / lh.META).read_text().splitlines()
                    if ln.startswith(f"{self.etc}/secrets.env\t"))
        self.assertTrue(line.endswith("\tsecret"))
        self.assertIn("\t0600\t", line)

    def test_big_file_named_not_kept(self):
        (self.etc / "huge.bin").write_bytes(b"x" * (lh.MAX_BYTES + 1))
        self.snap()
        body = subprocess.run(["git", "-C", str(self.h), "log", "-1", "--format=%B"],
                              capture_output=True, text=True).stdout
        self.assertIn("Not kept, larger than 2 MB", body)
        self.assertEqual((self.h / lh.FILES / str(self.etc / "huge.bin").lstrip("/"))
                         .stat().st_size, 0)

    def test_unreadable_config_uses_last_scope(self):
        self.snap()
        (self.etc / "new.toml").write_text("x = 1\n")
        real = lh.rules
        lh.rules = lambda cfg=None, previous=None: (_ for _ in ()).throw(ValueError("bad toml"))
        try:
            r = lh.snapshot(h=self.h)
        finally:
            lh.rules = real
        self.assertIn(f"A {self.etc}/new.toml", r["changed"])
        self.assertIn("could not be read", r["note"])

    def test_untracked_only(self):
        repo = Path(self.tmp.name) / "checkout"
        (repo / "deploy" / "shipped").mkdir(parents=True)
        (repo / "deploy" / "mine").mkdir(parents=True)
        (repo / "deploy" / "shipped" / "deploy.toml").write_text("a = 1\n")
        (repo / "deploy" / "mine" / "deploy.toml").write_text("b = 2\n")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "deploy/shipped"], check=True)
        got = lh.matched([lh.Rule(str(repo / "deploy"), "*/*", "records", untracked=str(repo))])
        self.assertEqual(got, [str(repo / "deploy" / "mine" / "deploy.toml")])


class TestRestore(Scratch):
    def break_things(self):
        (self.etc / "caddy" / "site.conf").write_text("WRONG line\n")
        (self.etc / "secrets.env").write_text("TOKEN=changed\n")
        (self.units / "timers.target.wants" / "loam-sweep.timer").unlink()  # disabled
        (self.units / "loam-stray.service").write_text("[Service]\nExecStart=/bin/false\n")
        os.chmod(self.etc / "app.toml", 0o600)

    def test_puts_it_back_and_says_so(self):
        good = self.snap()["commit"]
        self.break_things()
        code, said = self.restore(good, show_diff=True)
        self.assertEqual(code, 0, said)
        self.assertIn(f"change  {self.etc}/caddy/site.conf [contents]", said)
        self.assertIn(f"create  {self.units}/timers.target.wants/loam-sweep.timer", said)
        self.assertIn(f"remove  {self.units}/loam-stray.service", said)
        self.assertIn(f"{self.etc}/secrets.env [contents] (secret: contents not shown)", said)
        self.assertIn("mode 0600 -> 0644", said)
        self.assertIn("-WRONG line", said)
        self.assertNotIn("s3cr3t", said)
        self.assertNotIn("TOKEN=changed", said)
        self.assertEqual((self.etc / "caddy" / "site.conf").read_text(), "good line\n")
        self.assertEqual((self.etc / "secrets.env").read_text(), "TOKEN=s3cr3t-value\n")
        self.assertEqual((self.etc / "secrets.env").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.etc / "app.toml").stat().st_mode & 0o777, 0o644)
        link = self.units / "timers.target.wants" / "loam-sweep.timer"
        self.assertEqual(os.readlink(link), "../loam-sweep.timer")
        self.assertFalse((self.units / "loam-stray.service").exists())
        user = ["systemctl", "--user", "-M", f"{ME}@"]
        self.assertIn(user + ["stop", "loam-stray.service"], self.ran)
        self.assertIn(user + ["daemon-reload"], self.ran)
        self.assertIn(user + ["start", "loam-sweep.timer"], self.ran)
        self.assertLess(self.ran.index(user + ["stop", "loam-stray.service"]),
                        self.ran.index(user + ["daemon-reload"]))

    def test_undo_by_naming_the_saved_state(self):
        good = self.snap()["commit"]
        self.break_things()
        _code, said = self.restore(good)
        undo = said.split("To undo this restore: sudo loam host restore ")[1].strip()
        _code, _said = self.restore(undo)
        self.assertEqual((self.etc / "caddy" / "site.conf").read_text(), "WRONG line\n")
        self.assertTrue((self.units / "loam-stray.service").exists())

    def test_nothing_to_do_lists_states(self):
        self.snap()
        code, said = self.restore()
        self.assertEqual(code, 0)
        self.assertIn("Nothing differs from it.", said)
        self.assertIn("The newest states", said)
        self.assertEqual(self.ran, [])

    def test_default_skips_a_state_that_failed_checks(self):
        good = self.snap()["commit"]
        (self.etc / "app.toml").write_text("name = \n")       # not TOML
        bad = self.snap()
        self.assertTrue(bad["checks"])
        sha, how = lh.resolve(self.h, None)
        self.assertEqual(sha, good)
        self.assertIn("checks passed", how)

    def test_refuses_a_failing_state_unless_forced(self):
        (self.etc / "app.toml").write_text("name = \n")
        bad = self.snap()["commit"]
        (self.etc / "app.toml").write_text('name = "ada"\n')
        code, said = self.restore(bad)
        self.assertEqual(code, 3, said)
        self.assertIn("does not pass its own checks", said)
        self.assertEqual((self.etc / "app.toml").read_text(), 'name = "ada"\n')
        code, _said = self.restore(bad, force=True)
        self.assertEqual((self.etc / "app.toml").read_text(), "name = \n")

    def test_time_and_duration(self):
        first = self.snap()["commit"]
        self.assertEqual(lh.resolve(self.h, "0m")[0], first)
        with self.assertRaises(lh.HostError):
            lh.resolve(self.h, "2001-01-01 00:00")
        with self.assertRaises(lh.HostError):
            lh.resolve(self.h, "no-such-state")

    def test_only(self):
        good = self.snap()["commit"]
        self.break_things()
        code, said = self.restore(good, only=[str(self.etc / "caddy")])
        self.assertEqual(code, 0, said)
        self.assertEqual((self.etc / "caddy" / "site.conf").read_text(), "good line\n")
        self.assertTrue((self.units / "loam-stray.service").exists())
        self.assertEqual((self.etc / "secrets.env").read_text(), "TOKEN=changed\n")

    def test_dry_run_and_no_tty_change_nothing(self):
        good = self.snap()["commit"]
        self.break_things()
        code, said = self.restore(good, dry=True)
        self.assertEqual(code, 0)
        self.assertIn("nothing was changed", said)
        code, said = self.restore(good, yes=False)      # stdin is not a terminal here
        self.assertEqual(code, 2)
        self.assertEqual((self.etc / "caddy" / "site.conf").read_text(), "WRONG line\n")
        self.assertEqual(self.commits(), 1)


class TestLogin(Scratch):
    """A scratch login folder standing in for the authelia-data volume."""

    def setUp(self):
        super().setUp()
        import sqlite3
        self.sqlite3 = sqlite3
        self.login = Path(self.tmp.name) / "volume" / "_data"
        self.login.mkdir(parents=True)
        self.db = self.login / lh.LOGIN_DB
        con = sqlite3.connect(self.db)
        con.execute("create table webauthn_credentials (id integer primary key, name text)")
        con.execute("insert into webauthn_credentials (name) values ('owner phone')")
        con.commit()
        con.close()
        (self.login / lh.LOGIN_USERS).write_text("users:\n  owner: {}\n")
        (self.login / lh.LOGIN_USERS).chmod(0o600)
        (self.login / "notification.txt").write_text("code 123456\n")
        self.rules = self.rules + lh.login_rules(str(self.login))
        self.about = dict(self.about, login={"dir": str(self.login),
                                             "container": "scratch-login"})
        self.scope = (self.rules, self.about)
        self._running = lh._container_running
        lh._container_running = lambda name: True

    def tearDown(self):
        lh._container_running = self._running
        super().tearDown()

    def names(self):
        con = self.sqlite3.connect(self.db)
        try:
            return [r[0] for r in con.execute("select name from webauthn_credentials")]
        finally:
            con.close()

    def add(self, name):
        con = self.sqlite3.connect(self.db)
        con.execute("insert into webauthn_credentials (name) values (?)", (name,))
        con.commit()
        con.close()

    def test_kept_as_a_secret_and_unchanged_makes_no_state(self):
        first = self.snap()
        self.assertIn(f"A {self.db}", first["changed"])
        self.assertNotIn(f"A {self.login}/notification.txt", first["changed"])
        line = next(ln for ln in (self.h / lh.META).read_text().splitlines()
                    if ln.startswith(f"{self.db}\t"))
        self.assertTrue(line.endswith("\tsecret"))      # 0644 on disk, a secret all the same
        kept = lh.mirror_path(self.h, str(self.db))
        con = self.sqlite3.connect(f"file:{kept}?mode=ro", uri=True)
        self.assertEqual(con.execute("pragma integrity_check").fetchone()[0], "ok")
        con.close()
        for _ in range(3):
            self.assertIsNone(self.snap()["commit"])
        self.assertEqual(self.commits(), 1)
        self.add("laptop")
        self.assertIn(f"M {self.db}", self.snap()["changed"])

    def test_a_copy_that_fails_keeps_the_last(self):
        self.snap()
        real = lh.sqlite_copy
        lh.sqlite_copy = lambda path, uid, gid: (_ for _ in ()).throw(lh.CopyError("busy"))
        try:
            (self.etc / "app.toml").write_text('name = "bo"\n')
            r = self.snap()
        finally:
            lh.sqlite_copy = real
        self.assertNotIn(f"D {self.db}", r["changed"])
        self.assertIn("could not be copied", r["note"])
        self.assertTrue(lh.mirror_path(self.h, str(self.db)).exists())

    def test_restore_brings_back_a_passkey_and_warns(self):
        good = self.snap()["commit"]
        con = self.sqlite3.connect(self.db)
        con.execute("delete from webauthn_credentials")
        con.commit()
        con.close()
        (self.login / lh.LOGIN_USERS).write_text("users: {}\n")
        (self.login / (lh.LOGIN_DB + "-journal")).write_bytes(b"stale")
        code, said = self.restore(good, show_diff=True)
        self.assertEqual(code, 0, said)
        self.assertEqual(self.names(), ["owner phone"])
        self.assertEqual((self.login / lh.LOGIN_USERS).read_text(), "users:\n  owner: {}\n")
        self.assertFalse((self.login / (lh.LOGIN_DB + "-journal")).exists())
        self.assertIn("THIS PUTS BACK THE LOGIN'S ACCOUNTS AND PASSKEYS", said)
        self.assertIn(f"--except {self.login}", said)
        self.assertNotIn("owner: {}", said)                   # a secret: no diff
        self.assertIn(["docker", "stop", "scratch-login"], self.ran)
        self.assertIn(["docker", "start", "scratch-login"], self.ran)
        # the warning is the last thing said before the writes
        before_writes = said.split("Saved the present state first")[0]
        self.assertTrue(before_writes.rstrip().endswith("!" * 76))

    def test_except_leaves_the_login(self):
        good = self.snap()["commit"]
        self.add("added since")
        (self.etc / "caddy" / "site.conf").write_text("WRONG line\n")
        code, said = self.restore(good, leave=[str(self.login)])
        self.assertEqual(code, 0, said)
        self.assertEqual((self.etc / "caddy" / "site.conf").read_text(), "good line\n")
        self.assertEqual(self.names(), ["owner phone", "added since"])
        self.assertNotIn("LOGIN'S ACCOUNTS", said)
        self.assertFalse(any(a[:1] == ["docker"] for a in self.ran))

    def test_finding_the_login(self):
        orig_native, orig_unit, orig_legacy = lh.LOGIN_NATIVE_DIR, lh.LOGIN_SERVICE_UNIT, lh.LOGIN_LEGACY_COMPOSE
        lh.LOGIN_NATIVE_DIR = str(Path(self.tmp.name) / "no-native-login")
        lh.LOGIN_SERVICE_UNIT = str(Path(self.tmp.name) / "no-native.service")
        lh.LOGIN_LEGACY_COMPOSE = str(Path(self.tmp.name) / "no-compose.yaml")
        try:
            lh.shutil.which = lambda name, path=None: None
            folder, note = lh.find_login()
            self.assertIsNone(folder)
            self.assertIn("no Docker", note)
            fake = Path(self.tmp.name) / "docker"
            for script, want in [
                    ("echo 'Error response from daemon: get authelia-data: no such volume' >&2;"
                     " exit 1", None),
                    (f"echo {self.login}", str(self.login)),
                    ("echo /nowhere/at/all", None)]:
                fake.write_text(f"#!/bin/sh\n{script}\n")
                fake.chmod(0o755)
                lh.shutil.which = lambda name, path=None: str(fake)
                folder, note = lh.find_login()
                self.assertEqual(folder, want, note)
            # Docker there but not answering: the last state's folder, if still there
            fake.write_text("#!/bin/sh\necho 'Cannot connect to the Docker daemon' >&2; exit 1\n")
            folder, _note = lh.find_login({"login": {"dir": str(self.login)}})
            self.assertEqual(folder, str(self.login))

            # Native login folder found directly without invoking Docker
            lh.LOGIN_NATIVE_DIR = str(self.login)
            lh.shutil.which = lambda name, path=None: (_ for _ in ()).throw(
                AssertionError("find_login must not invoke Docker in native mode"))
            folder, note = lh.find_login()
            self.assertEqual(folder, str(self.login))
            self.assertIn(lh.LOGIN_SERVICE, note)
        finally:
            lh.LOGIN_NATIVE_DIR, lh.LOGIN_SERVICE_UNIT, lh.LOGIN_LEGACY_COMPOSE = orig_native, orig_unit, orig_legacy

    def test_native_service_restore_stops_and_starts_systemd_unit(self):
        self.about = dict(self.about, login={"dir": str(self.login),
                                             "service": lh.LOGIN_SERVICE})
        self.scope = (self.rules, self.about)
        orig_active = lh._is_active
        lh._is_active = lambda name: name == lh.LOGIN_SERVICE
        try:
            good = self.snap()["commit"]
            self.add("phone-2")
            self.assertEqual(self.names(), ["owner phone", "phone-2"])
            code, said = self.restore(good)
            self.assertEqual(code, 0, said)
            self.assertEqual(self.names(), ["owner phone"])
            self.assertIn(["systemctl", "stop", lh.LOGIN_SERVICE], self.ran)
            self.assertIn(["systemctl", "start", lh.LOGIN_SERVICE], self.ran)
            self.assertFalse(any(a[:1] == ["docker"] for a in self.ran))
        finally:
            lh._is_active = orig_active


class TestCommand(unittest.TestCase):
    def test_needs_root(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        done = subprocess.run([sys.executable, str(ROOT / "bin" / "loam-host"), "log"],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn("sudo", done.stderr)

    def test_rules_cover_what_loam_installs(self):
        class Cfg:
            USER, HOME = "sam", Path("/home/sam")
            USER_UNIT_DIR = Path("/home/sam/.config/systemd/user")
            STATE, ROOT = Path("/home/sam/.local/state/loam"), Path("/opt/loam")
            CONFIG_FILE = Path("/etc/loam/loam.toml")
            SECRETS_ENV = Path("/home/sam/.config/loam/secrets.env")
            WAKE_ENV = Path("/home/sam/.config/loam/wake.env")
        real = lh.find_login
        lh.find_login = lambda previous=None: ("/srv/docker/volumes/authelia-data/_data", "found")
        try:
            got, about = lh.rules(Cfg)
        finally:
            lh.find_login = real
        self.assertEqual(about["login"]["container"], "loam-authelia")
        db = next(r for r in got if r.pattern == lh.LOGIN_DB)
        self.assertEqual((db.kind, db.secret), ("sqlite", True))
        roots = {(r.root, r.pattern) for r in got}
        for want in [("/etc/loam", "**"), ("/etc/caddy", "**"),
                     ("/etc/systemd/system", "*.wants/loam*"),
                     ("/home/sam/.config/systemd/user", "*.wants/loam*"),
                     ("/var/spool/cron/crontabs", "sam"), ("/etc/sudoers.d", "loam*"),
                     ("/home/sam/.local/state/loam/broker", "settings.json")]:
            self.assertIn(want, roots)
        self.assertEqual(about["user"], "sam")
        # the person's own units are not Loam's to put back
        self.assertNotIn(("/home/sam/.config/systemd/user", "**"), roots)


if __name__ == "__main__":
    unittest.main(verbosity=1)
