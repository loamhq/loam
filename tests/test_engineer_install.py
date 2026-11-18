#!/usr/bin/env python3
"""The platform engineer on a fresh install, and across updates (2026-11-18).

Until 0.2.2 `provision --engineer` copied its role and project file from the
vault, where only the first install had them, and the installer never ran
it. Now Loam ships both (`engineer/install/`), provision seeds them into the
vault, and the vault copy stays the source. In a temp install (a git checkout
with this tree's shipped files, a vault, a state folder), through the real
`bin/provision --engineer`:

  - a fresh install: both seeded into the vault, rendered beside the code
    verbatim, the rendered files in `info/exclude`, and the update's
    clean-tree rule still reads the stock checkout as clean;
  - a re-run changes nothing;
  - a newer shipped text replaces a vault copy still as seeded;
  - a vault copy the owner changed is kept, and is what the engineer reads;
  - a vault copy provision never seeded (the first install's own) is kept;
  - neither shipped file names a path or a particular vault's layout.

Run: python3 tests/test_engineer_install.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = ROOT / "engineer" / "install"
TMP = Path(tempfile.mkdtemp(prefix="test-engineer-install-"))
INSTALL, VAULT, HOME, STATE = TMP / "install", TMP / "vault", TMP / "home", TMP / "state"
CONFIG = TMP / "loam.toml"
CONFIG.write_text(f'''
[owner]
id = "sam"
name = "Sam"
[paths]
home = "{HOME}"
root = "{INSTALL}"
vault = "{VAULT}"
workspaces = "{TMP}/agents"
plugins = "{TMP}/plugins"
state = "{STATE}"
''')
ENV = {**os.environ, "LOAM_CONFIG": str(CONFIG), "HOME": str(HOME),
       "LOAM_LOCK_DIR": str(TMP / "locks"), "PYTHONDONTWRITEBYTECODE": "1",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test",
       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
for var in ("LOAM_VAULT", "LOAM_AI_LABS", "LOAM_UPDATE_STATE", "LOAM_LIVE_HELD"):
    ENV.pop(var, None)

FAILURES = 0


def check(label: str, got, want) -> None:
    global FAILURES  # noqa: PLW0603
    ok = got == want
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def run(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), capture_output=True, text=True, env=ENV)


def git(*args: str) -> str:
    done = run("git", "-C", str(INSTALL), *args)
    if done.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr}")
    return done.stdout


def provision() -> str:
    done = run(sys.executable, str(ROOT / "bin" / "provision"), "--engineer")
    if done.returncode:
        raise RuntimeError(f"provision --engineer: {done.stdout}{done.stderr}")
    return done.stdout


def stock_dirt() -> list[str]:
    code = ("import sys; sys.path.insert(0, %r); import loam_update as up; "
            "import json; print(json.dumps(up.dirt('stock')))" % str(ROOT / "lib"))
    done = run(sys.executable, "-c", code)
    if done.returncode:
        raise RuntimeError(done.stderr)
    return json.loads(done.stdout)


ROLE = VAULT / "Projects" / "Loam" / "engineer" / "role.md"
PROJECT = VAULT / "Projects" / "Loam" / "engineer" / "projects" / "loam.md"
LIVE_ROLE = INSTALL / "engineer" / "role.md"
LIVE_PROJECT = INSTALL / "engineer" / "project.md"


def release_role(text: str) -> None:
    """A newer release, as far as the shipped role goes."""
    (INSTALL / "engineer" / "install" / "role.md").write_text(text)
    git("commit", "-qam", "A newer role")


try:
    for d in (VAULT, HOME, STATE, TMP / "agents" / "_shared", TMP / "plugins"):
        d.mkdir(parents=True)
    (TMP / "agents" / "_shared" / "reach.md").write_text("# Reach\n")
    (INSTALL / "engineer" / "memory").mkdir(parents=True)
    shutil.copytree(SHIPPED, INSTALL / "engineer" / "install")
    (INSTALL / "engineer" / "memory" / "MEMORY.md").write_text("# Memory\n")
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-qm", "A release")
    shipped_role = (SHIPPED / "role.md").read_text()
    shipped_project = (SHIPPED / "project.md").read_text()

    # -- a fresh install -------------------------------------------------------
    out = provision()
    check("fresh: the role is seeded into the vault", ROLE.read_text(), shipped_role)
    check("fresh: the project file is seeded into the vault",
          PROJECT.read_text(), shipped_project)
    check("fresh: the engineer's role is the shipped one, verbatim",
          LIVE_ROLE.read_text(), shipped_role)
    check("fresh: its project file too", LIVE_PROJECT.read_text(), shipped_project)
    claude_md = (INSTALL / "engineer" / "CLAUDE.md").read_text()
    check("fresh: CLAUDE.md imports both and the shared reach",
          claude_md.splitlines()[:3], ["@role.md", "", "@project.md"])
    card = json.loads((INSTALL / "engineer" / "agent.json").read_text())
    check("fresh: agent.json is an engineer's, on this install's paths",
          (card["kind"], card["workspace"], card["vault"]),
          ("engineer", str(INSTALL / "engineer"), str(VAULT)))
    settings = json.loads((INSTALL / "engineer" / ".claude" / "settings.json").read_text())
    check("fresh: the plugins folder is in the engineer's reach",
          str(TMP / "plugins") in settings["permissions"]["additionalDirectories"], True)
    check("fresh: the conversations deny names this vault's folder",
          f"Edit(/{VAULT}/Conversations/**)" in settings["permissions"]["deny"], True)
    exclude = (INSTALL / ".git" / "info" / "exclude").read_text().splitlines()
    check("fresh: the rendered files are in info/exclude",
          all(f"/engineer/{n}" in exclude
              for n in ("CLAUDE.md", "role.md", "project.md", "agent.json", ".claude")), True)
    check("fresh: the checkout commits as the engineer, not as nobody",
          (git("config", "user.name").strip(), git("config", "user.email").strip()),
          ("Loam engineer", "engineer+loam@noreply.local"))
    check("fresh: git status shows nothing", git("status", "--porcelain"), "")
    ignored = git("status", "--porcelain", "--ignored", "--untracked-files=all")
    check("fresh: the rendered files are there, ignored", "!! engineer/role.md" in ignored, True)
    check("fresh: the update's clean-tree rule reads the stock checkout as clean",
          stock_dirt(), [])
    (INSTALL / "stray.txt").write_text("x\n")
    check("fresh: anything else in the tree is still dirt",
          stock_dirt(), ["?? stray.txt"])
    (INSTALL / "stray.txt").unlink()

    # -- again, nothing changed --------------------------------------------------
    before = ROLE.stat().st_mtime_ns
    out = provision()
    check("again: the vault copy is not rewritten", ROLE.stat().st_mtime_ns, before)
    check("again: nothing is seeded", "seeded " in out or "updated " in out, False)
    git("config", "user.name", "Ada")
    git("config", "user.email", "ada@example.test")
    provision()
    check("again: an identity the checkout has is kept",
          git("config", "user.email").strip(), "ada@example.test")

    # -- a newer release, the vault copy as seeded ------------------------------
    release_role(shipped_role + "\nA line a later release added.\n")
    out = provision()
    check("newer: a copy still as seeded takes the new text",
          ROLE.read_text().endswith("A line a later release added.\n"), True)
    check("newer: and the engineer reads it", LIVE_ROLE.read_text(), ROLE.read_text())
    check("newer: the project file, unchanged in the release, is left",
          PROJECT.read_text(), shipped_project)

    # -- the owner's own edit ------------------------------------------------------
    owners = ROLE.read_text() + "\nThe owner's own line.\n"
    ROLE.write_text(owners)
    release_role(shipped_role + "\nA third release's line.\n")
    out = provision()
    check("edited: the owner's copy is kept", ROLE.read_text(), owners)
    check("edited: the engineer reads the owner's", LIVE_ROLE.read_text(), owners)
    check("edited: provision says it kept it", f"kept {ROLE}" in out, True)
    out = provision()
    check("edited: and keeps it on every run after", ROLE.read_text(), owners)

    # -- a copy provision never seeded (the first install's) ---------------------
    (STATE / "engineer" / "seeded.json").unlink()
    first = "# The first install's own role\n"
    PROJECT.write_text(first)
    out = provision()
    check("never seeded: the vault copy is kept", PROJECT.read_text(), first)
    check("never seeded: the engineer reads it", LIVE_PROJECT.read_text(), first)

    # -- the text itself ------------------------------------------------------------
    # The words of the person and box they were written on are the push
    # gate's to refuse; what a test can say is that neither names a place.
    import re  # noqa: PLC0415
    both = shipped_role + shipped_project
    check("neither file names a path or a numbered vault folder",
          re.findall(r"/home/\S+|\b\d\d_[A-Z]+\b", both), [])
    check("the role says the owner, never a name",
          "the owner" in shipped_role and "[[" not in both, True)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'OK' if not FAILURES else f'{FAILURES} FAILED'}")
sys.exit(1 if FAILURES else 0)
