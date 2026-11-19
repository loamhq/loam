#!/usr/bin/env python3
"""The routine local commit (lib/loam_autocommit.py) and the Git backup
plugin (plugins/git-backup/), on scratch installs with local bare
repositories standing in for the git host.

What it holds:
- the local commit: makes the repository on `main`, commits, keeps a file
  over the limit out and names it, leaves a tree to a hand-made crontab line;
- the plugin is built in, off by default, and declares its timer, tool,
  settings and server;
- an address as a person pastes it becomes the SSH address;
- a host's failure messages read as the right plain sentence;
- connecting, testing and the first push; the routine push when due, and
  not before; a remote with history of its own is never overwritten, and
  its failure reaches the owner's General board at the second attempt, and
  its recovery after;
- a repository anyone can read is refused at connect and at push, and
  nothing is sent;
- a file too large for the host does not stop the rest;
- restore onto a second install brings both trees back; a tree with its
  own history is not replaced unless asked;
- the page's server answers status and refuses a wrong address in words.

Run: python3 tests/test_git_backup.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "git-backup"
BACKUP = [sys.executable, str(PLUGIN / "tools" / "backup.py")]
sys.path.insert(0, str(ROOT / "lib"))


def run(cmd, env, check=True, cwd=None):
    done = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, timeout=300)
    if check and done.returncode != 0:
        raise AssertionError(f"{cmd} exited {done.returncode}:\n{done.stdout}\n{done.stderr}")
    return done


def git(where, *args):
    return subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


class Install:
    """A scratch install: its own config and trees, as the installer leaves
    them (the starting-point commit on `main`), no systemd reachable."""

    def __init__(self, base: Path, name: str):
        self.home = base / name
        for d in ("vault", "agents", "plugins", "state", "logs", "run"):
            (self.home / d).mkdir(parents=True, exist_ok=True)
        self.config = self.home / "loam.toml"
        self.config.write_text(f'''user = "{os.environ.get("USER") or "loam"}"
timezone = "America/Los_Angeles"
[owner]
id = "owner"
name = "Sam"
[paths]
home = "{self.home}"
root = "{ROOT}"
vault = "{self.home}/vault"
workspaces = "{self.home}/agents"
projects = "{self.home}/projects"
plugins = "{self.home}/plugins"
state = "{self.home}/state"
logs = "{self.home}/logs"
[git.autocommit]
name = "loam"
email = "loam@{name}.test"
''', encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("LOAM_", "GIT_"))}
        self.env.update(LOAM_CONFIG=str(self.config), XDG_RUNTIME_DIR=str(self.home / "run"),
                        DBUS_SESSION_BUS_ADDRESS=f"unix:path={self.home}/run/none",
                        LOAM_CONTRIBUTIONS_FOLLOW="0", LOAM_CRONTAB_FILE="",
                        LOAM_HOST_SETTINGS=str(self.home / "state" / "host.json"),
                        LOAM_OFFLINE="1", PYTHONDONTWRITEBYTECODE="1")
        self.vault, self.spaces = self.home / "vault", self.home / "agents"

    def starter(self):
        (self.vault / "README.md").write_text("# Your vault\n")
        (self.spaces / "_shared").mkdir(exist_ok=True)
        (self.spaces / "_shared" / "owner.md").write_text("Sam\n")
        for d in (self.vault, self.spaces):
            subprocess.run(["git", "-C", str(d), "init", "-q", "-b", "main"], check=True)
            subprocess.run(["git", "-C", str(d), "-c", "user.name=loam", "-c", "user.email=l@x",
                            "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(d), "-c", "user.name=loam", "-c", "user.email=l@x",
                            "commit", "-q", "--allow-empty", "-m", "Loam: the starting point"],
                           check=True)
        return self

    def py(self, code: str, check=True):
        return run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(ROOT / 'lib')!r})\n" + code],
                   self.env, check=check)

    def on(self):
        self.py("import loam_plugins as lp; lp.set_on('git-backup', True)")
        return self

    def backup(self, *args, check=True, env=None):
        done = run([*BACKUP, *args], {**self.env, **(env or {})}, check=False)
        if check and done.returncode not in (0, 1):
            raise AssertionError(done.stderr)
        try:
            return json.loads(done.stdout)
        except ValueError:
            raise AssertionError(f"not JSON: {done.stdout}\n{done.stderr}")

    @property
    def state_file(self) -> Path:
        return self.home / "state" / "plugins" / "private" / "git-backup" / "state.json"

    def rewind(self, minutes=16):
        s = json.loads(self.state_file.read_text())
        then = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for t in s["trees"].values():
            if t.get("last_attempt_at"):
                t["last_attempt_at"] = then
        self.state_file.write_text(json.dumps(s))

    def board(self) -> str:
        p = self.vault / "Inbox" / "boards" / "general.md"
        return p.read_text() if p.exists() else ""


def bare(base: Path, name: str) -> Path:
    path = base / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True)
    return path


class LocalCommit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.i = Install(self.base, "a")

    def tearDown(self):
        self.tmp.cleanup()

    def autocommit(self, *args, env=None):
        return run([sys.executable, str(ROOT / "bin" / "autocommit"), *args],
                   {**self.i.env, **(env or {})}, check=False)

    def test_makes_the_repository_on_main_and_commits(self):
        (self.i.vault / "note.md").write_text("hello\n")
        done = self.autocommit("vault")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(git(self.i.vault, "rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertRegex(git(self.i.vault, "log", "-1", "--format=%s %an"), r"^vault: \S+Z loam$")
        self.assertEqual(git(self.i.vault, "status", "--porcelain"), "")
        # Nothing new: no new commit.
        before = git(self.i.vault, "rev-parse", "HEAD")
        self.autocommit("vault")
        self.assertEqual(git(self.i.vault, "rev-parse", "HEAD"), before)

    def test_a_file_over_the_limit_is_held_back_and_named(self):
        (self.i.vault / "small.md").write_text("x\n")
        (self.i.vault / "film.mov").write_bytes(b"0" * 5000)
        self.autocommit("vault", env={"LOAM_AUTOCOMMIT_MAX_BYTES": "4000"})
        tracked = git(self.i.vault, "ls-files").splitlines()
        self.assertIn("small.md", tracked)
        self.assertNotIn("film.mov", tracked)
        self.assertTrue((self.i.vault / "film.mov").exists(), "the file stays on disk")
        rec = json.loads((self.i.home / "state" / "autocommit" / "vault.json").read_text())
        self.assertEqual(rec["held"], [{"path": "film.mov", "bytes": 5000}])
        # A tracked file that grows past it keeps its committed version.
        (self.i.vault / "small.md").write_bytes(b"y" * 5000)
        self.autocommit("vault", env={"LOAM_AUTOCOMMIT_MAX_BYTES": "4000"})
        self.assertEqual(git(self.i.vault, "show", "HEAD:small.md"), "x")

    def test_a_hand_made_crontab_line_keeps_its_tree(self):
        cron = self.base / "crontab"
        cron.write_text("*/15 * * * * /home/x/agents/bin/vault-autocommit >> log 2>&1\n"
                        "# */15 * * * * /home/x/agents/bin/ai-labs-autocommit\n")
        (self.i.vault / "n.md").write_text("n\n")
        (self.i.spaces / "n.md").write_text("n\n")
        out = self.autocommit(env={"LOAM_CRONTAB_FILE": str(cron)}).stdout.splitlines()
        rows = {json.loads(line)["tree"]: json.loads(line) for line in out}
        self.assertEqual(rows["vault"]["skipped"], "hand-made")
        self.assertFalse((self.i.vault / ".git").exists(), "the vault was left alone")
        self.assertIsNotNone(rows["spaces"]["committed"], "a commented line is no routine")

    def test_runtime_churn_is_kept_out_of_the_workspaces(self):
        (self.i.spaces / "iris" / "scratch").mkdir(parents=True)
        (self.i.spaces / "iris" / "scratch" / "tmp.txt").write_text("t")
        (self.i.spaces / "iris" / "soul.md").write_text("s")
        self.autocommit("spaces")
        tracked = git(self.i.spaces, "ls-files").splitlines()
        self.assertIn("iris/soul.md", tracked)
        self.assertNotIn("iris/scratch/tmp.txt", tracked)
        self.assertNotIn("loam", git(self.i.spaces, "ls-files", ".gitignore"),
                         "no tracked file is written")


class Manifest(unittest.TestCase):
    def test_built_in_off_by_default_and_declares_what_it_does(self):
        import loam_plugins  # noqa: PLC0415
        row = loam_plugins.read_folder(PLUGIN)
        self.assertFalse(row.get("broken"), row)
        m = row["manifest"]
        self.assertIs(m["default_on"], False)
        c = m["contributes"]
        self.assertEqual([s["name"] for s in c["schedule.kind"]], ["push"])
        self.assertEqual({e["key"] for e in c["settings.entry"]},
                         {"vault_remote", "spaces_remote", "every"})
        self.assertEqual([t["name"] for t in c["agent.tool"]], ["git-backup"])
        self.assertTrue(m["touches"]["server"])
        self.assertEqual(m["touches"]["record"], "write")
        self.assertTrue((PLUGIN / "page" / "main.js").is_file())
        self.assertTrue((PLUGIN / "server" / "main.py").is_file())

    def test_the_timer_calendar_is_one_systemd_reads(self):
        import loam_plugins  # noqa: PLC0415
        cal = loam_plugins.read_folder(PLUGIN)["manifest"]["contributes"]["schedule.kind"][0]["calendar"]
        done = subprocess.run(["systemd-analyze", "calendar", cal], capture_output=True, text=True)
        if done.returncode == 127:
            self.skipTest("no systemd-analyze")
        self.assertEqual(done.returncode, 0, done.stderr)


class Words(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(PLUGIN / "tools"))
        import backup_core  # noqa: PLC0415
        cls.core = backup_core

    def test_addresses_as_pasted(self):
        n = self.core.normalise
        self.assertEqual(n("https://github.com/sam/vault"), "git@github.com:sam/vault.git")
        self.assertEqual(n("https://github.com/sam/vault.git"), "git@github.com:sam/vault.git")
        self.assertEqual(n("https://github.com/sam/vault/tree/main/docs"), "git@github.com:sam/vault.git")
        self.assertEqual(n("https://gitlab.com/group/sub/vault/-/settings/repository"),
                         "git@gitlab.com:group/sub/vault.git")
        self.assertEqual(n(" git@github.com:sam/vault.git "), "git@github.com:sam/vault.git")
        self.assertEqual(n("ssh://git@git.example.org:2222/sam/vault.git"),
                         "ssh://git@git.example.org:2222/sam/vault.git")
        for bad in ("hello there", "https://github.com/sam"):
            with self.assertRaises(self.core.BackupError):
                n(bad)

    def test_the_hosts_failures_in_plain_words(self):
        c = self.core.classify
        self.assertEqual(c("remote: error: GH001: Large files detected.\nremote: error: File "
                           "films/a.mov is 120.50 MB; this exceeds GitHub's file size limit of "
                           "100.00 MB")["files"], ["films/a.mov"])
        self.assertEqual(c("ERROR: The key you are authenticating with has been marked as read "
                           "only.\nfatal: Could not read from remote repository.")["kind"], "read-only")
        self.assertEqual(c("git@github.com: Permission denied (publickey).\nfatal: Could not "
                           "read from remote repository.")["kind"], "key")
        self.assertEqual(c(" ! [rejected]        HEAD -> main (fetch first)\nerror: failed to "
                           "push some refs")["kind"], "diverged")
        self.assertEqual(c("remote: error: This repository is over its data quota.")["kind"], "quota")
        self.assertEqual(c("ssh: Could not resolve hostname github.com: Temporary failure in "
                           "name resolution")["kind"], "network")

    def test_deploy_key_pages(self):
        self.assertEqual(self.core.deploy_key_page("git@github.com:sam/vault.git"),
                         "https://github.com/sam/vault/settings/keys/new")
        self.assertIn("deploy-keys", self.core.deploy_key_page("git@gitlab.com:sam/vault.git"))


class Plugin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.i = Install(self.base, "sam").starter().on()
        self.rv, self.rs = bare(self.base, "vault"), bare(self.base, "spaces")

    def tearDown(self):
        self.tmp.cleanup()

    def connect_both(self):
        for tree, remote in (("vault", self.rv), ("spaces", self.rs)):
            out = self.i.backup("connect", tree, str(remote))
            self.assertTrue(out["ok"], out)
            self.assertTrue(out["public_key"].startswith("ssh-ed25519 "))
            out = self.i.backup("test", tree)
            self.assertTrue(out["ok"], out)
            self.assertTrue(out["first_push"]["ok"], out)

    def test_connect_test_first_push_and_the_routine(self):
        self.connect_both()
        self.assertEqual(git(self.rv, "rev-parse", "main"), git(self.i.vault, "rev-parse", "HEAD"))
        self.assertEqual(git(self.rs, "rev-parse", "main"), git(self.i.spaces, "rev-parse", "HEAD"))
        key = self.i.home / "state" / "plugins" / "private" / "git-backup" / "keys" / "vault"
        self.assertEqual(oct(key.stat().st_mode & 0o777), "0o600")
        self.assertNotIn("PRIVATE KEY", "".join(p.read_text(errors="ignore") for p in
                                                self.i.vault.rglob("*") if p.is_file()))
        # Not due yet: nothing sent.
        (self.i.vault / "groceries.md").write_text("- oat milk\n")
        out = self.i.backup("push")
        self.assertEqual([r.get("skipped") for r in out], ["not due", "not due"])
        # Due: the change is committed and arrives.
        self.i.rewind()
        out = self.i.backup("push")
        self.assertTrue(all(r["ok"] for r in out), out)
        self.assertEqual(git(self.rv, "show", "main:groceries.md"), "- oat milk")
        status = self.i.backup("status")
        self.assertEqual(status["summary"]["level"], "ok")
        self.assertEqual([t["level"] for t in status["trees"]], ["ok", "ok"])
        self.assertEqual(status["trees"][0]["last_pushed_ago"], "just now")

    def test_a_push_moves_origin_main_when_origin_is_that_repository(self):
        # The push goes to the address, so a tree whose `origin` is the same
        # repository read "ahead N" for ever (2026-11-19). Another remote is
        # left alone.
        git(self.i.vault, "remote", "add", "origin", str(self.rv))
        git(self.i.vault, "remote", "add", "elsewhere", str(self.rs))
        self.connect_both()
        (self.i.vault / "groceries.md").write_text("- oat milk\n")
        self.i.rewind()
        out = self.i.backup("push")
        self.assertTrue(all(r["ok"] for r in out), out)
        head = git(self.i.vault, "rev-parse", "HEAD")
        self.assertEqual(git(self.i.vault, "rev-parse", "refs/remotes/origin/main"), head)
        git(self.i.vault, "branch", "--set-upstream-to=origin/main", "main")
        self.assertNotIn("ahead", git(self.i.vault, "status", "-sb"))
        self.assertEqual(git(self.i.vault, "for-each-ref", "refs/remotes/elsewhere"), "")

    def test_a_diverged_remote_is_never_overwritten_and_the_owner_hears(self):
        self.connect_both()
        other = self.base / "other"
        subprocess.run(["git", "clone", "-q", str(self.rv), str(other)], check=True)
        (other / "elsewhere.md").write_text("e\n")
        subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(other), "-c", "user.name=x", "-c", "user.email=x@x",
                        "commit", "-qm", "elsewhere"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)
        theirs = git(self.rv, "rev-parse", "main")
        (self.i.vault / "mine.md").write_text("m\n")
        out = self.i.backup("push", "--now", "vault")
        self.assertEqual(out[0]["kind"], "diverged")
        self.assertNotIn("backup of your vault", self.i.board(), "one failure is not told")
        out = self.i.backup("push", "--now", "vault")
        self.assertEqual(out[0]["attempts"], 2)
        self.assertEqual(git(self.rv, "rev-parse", "main"), theirs, "never forced")
        board = self.i.board()
        self.assertIn("backup of your vault has been failing since", board)
        self.assertIn("Settings › Plugins › Git backup", board)
        status = self.i.backup("status")
        self.assertEqual(status["trees"][0]["level"], "fail")
        self.assertEqual(status["summary"]["level"], "fail")
        # It works again: the other history is taken in by hand, and it says so.
        subprocess.run(["git", "-C", str(self.i.vault), "pull", "-q", "--no-rebase", "--no-edit",
                        str(self.rv), "main"], check=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "s", "GIT_AUTHOR_EMAIL": "s@s",
                            "GIT_COMMITTER_NAME": "s", "GIT_COMMITTER_EMAIL": "s@s"})
        out = self.i.backup("push", "--now", "vault")
        self.assertTrue(out[0]["ok"], out)
        self.assertIn("backup of your vault works again", self.i.board())

    def test_a_public_repository_is_refused_and_nothing_is_sent(self):
        public = bare(self.base, "public")
        anon = json.dumps({str(public): str(public)})
        out = self.i.backup("connect", "vault", str(public), env={"LOAM_GIT_BACKUP_ANON": anon})
        self.assertFalse(out["ok"])
        self.assertIn("This repository is public", out["error"])
        values = self.i.home / "plugins" / "_settings" / "git-backup" / "values.json"
        self.assertFalse(values.exists() and json.loads(values.read_text()).get("vault_remote"))
        # Connected while private, public later: the push refuses.
        self.connect_both()
        before = git(self.rv, "rev-parse", "main")
        (self.i.vault / "secret.md").write_text("s\n")
        flip = json.dumps({str(self.rv): str(self.rv)})
        out = self.i.backup("push", "--now", "vault", env={"LOAM_GIT_BACKUP_ANON": flip})
        self.assertEqual(out[0]["kind"], "public")
        self.assertEqual(git(self.rv, "rev-parse", "main"), before)
        self.assertIn("This repository is public", self.i.board(), "told at once")

    def test_a_file_too_large_does_not_stop_the_rest(self):
        self.connect_both()
        (self.i.vault / "huge.bin").write_bytes(b"0" * 6000)
        (self.i.vault / "note.md").write_text("n\n")
        out = self.i.backup("push", "--now", "vault", env={"LOAM_AUTOCOMMIT_MAX_BYTES": "5000"})
        self.assertTrue(out[0]["ok"], out)
        files = git(self.rv, "ls-tree", "-r", "--name-only", "main").splitlines()
        self.assertIn("note.md", files)
        self.assertNotIn("huge.bin", files)
        held = self.i.backup("status")["trees"][0]["held"]
        self.assertEqual([f["path"] for f in held], ["huge.bin"])

    def test_restore_onto_a_new_install(self):
        (self.i.vault / "Inbox").mkdir()
        (self.i.vault / "Inbox" / "groceries.md").write_text("- oat milk\n")
        (self.i.spaces / "iris" / "memory").mkdir(parents=True)
        (self.i.spaces / "iris" / "memory" / "MEMORY.md").write_text("- Sam likes oat milk\n")
        self.connect_both()
        new = Install(self.base, "new")
        (new.vault / "Welcome.md").write_text("A new install's own starter page\n")
        new.starter().on()
        for tree, remote in (("vault", self.rv), ("spaces", self.rs)):
            out = new.backup("restore", tree, str(remote))
            self.assertTrue(out["ok"], out)
            self.assertTrue(Path(out["set_aside"]).is_dir())
        self.assertEqual((new.vault / "Inbox" / "groceries.md").read_text(), "- oat milk\n")
        self.assertFalse((new.vault / "Welcome.md").exists(), "the tree is the backup's")
        aside = next(new.home.glob("vault.before-restore-*"))
        self.assertTrue((aside / "Welcome.md").exists(), "what was here is kept aside")
        self.assertEqual((new.spaces / "iris" / "memory" / "MEMORY.md").read_text(),
                         "- Sam likes oat milk\n")
        self.assertEqual(git(new.vault, "rev-parse", "HEAD"), git(self.i.vault, "rev-parse", "HEAD"))
        self.assertTrue(git(new.vault, "rev-parse", "refs/loam/before-restore"))
        # Restored and connected: its next backup goes to the same repository.
        self.assertEqual(new.backup("status")["trees"][0]["remote"], str(self.rv))
        # A tree with its own history is not replaced unless asked.
        (new.vault / "today.md").write_text("written after the restore\n")
        out = new.backup("restore", "vault", str(self.rv))
        self.assertFalse(out["ok"])
        self.assertIn("already has its own history", out["error"])
        self.assertTrue((new.vault / "today.md").exists())
        out = new.backup("restore", "vault", str(self.rv), "--replace")
        self.assertTrue(out["ok"], out)
        self.assertFalse((new.vault / "today.md").exists())
        self.assertTrue((Path(out["set_aside"]) / "today.md").exists())

    def test_the_pages_server(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        proc = subprocess.Popen([sys.executable, str(PLUGIN / "server" / "main.py")],
                                env={**self.i.env, "PORT": str(port)}, cwd=str(PLUGIN),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            url = f"http://127.0.0.1:{port}/"
            for _ in range(50):
                try:
                    status = json.loads(urllib.request.urlopen(url + "status", timeout=2).read())
                    break
                except OSError:
                    time.sleep(0.1)
            self.assertEqual([t["id"] for t in status["trees"]], ["vault", "spaces"])
            self.assertEqual(status["summary"]["level"], "off")
            req = urllib.request.Request(url + "connect", method="POST",
                                         data=json.dumps({"tree": "vault", "remote": "not an address"}).encode(),
                                         headers={"content-type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(err.exception.code, 400)
            self.assertIn("not a repository address", json.loads(err.exception.read())["error"])
            req = urllib.request.Request(url + "connect", method="POST",
                                         data=json.dumps({"tree": "vault", "remote": str(self.rv)}).encode())
            out = json.loads(urllib.request.urlopen(req, timeout=20).read())
            self.assertTrue(out["ok"])
            self.assertEqual(out["status"]["trees"][0]["level"], "wait")
        finally:
            proc.terminate()
            proc.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main(verbosity=1)
