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
- the command refuses to run as anyone but root.

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
        lh.rules = lambda cfg=None: (_ for _ in ()).throw(ValueError("bad toml"))
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
        got, about = lh.rules(Cfg)
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
