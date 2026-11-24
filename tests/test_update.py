#!/usr/bin/env python3
"""lib/loam_update.py, bin/loam-update's rules, and the broker's
`/v1/update` routes (first release L13).

Real git repositories in a temp directory: a bare `origin`, a release maker
cutting tags, and installs cloned at 1.0.0, with a scratch config
(LOAM_CONFIG, before anything imports loam_config) whose root is the
install's checkout. What the update does to the box beyond git and
System/Loam (units, agents, pins, the broker) is a fake that records each
call and can fail one; the real ones are proved on a scratch install of the
real tree in the job that built this (its receipt).

Checks:
  - check: the newest tag against the manifest, stock or development, the
    notice for the person's agent left once per version;
  - an edit in System/Loam is shown (file and diff) and holds an apply
    until it is said yes to; the copy is kept;
  - apply moves a stock checkout to the tag, replaces System/Loam, and
    regenerates only what reads what changed; rollback puts it all back;
  - the clean-tree rule: an untracked or an ignored file refuses a stock
    install; a development one merges clean with its local commit kept,
    refuses a conflict found before anything moves, and refuses an
    uncommitted change;
  - a failed step puts the previous release back (checkout, System/Loam,
    the broker again) and says so;
  - a release whose manifest disagrees with its tag, a version not newer,
    a checkout with no manifest (the owner's box), a second run while one
    runs: each refused, nothing moved;
  - the closure rule that decides what to regenerate;
  - the routes: GET the state, POST check, a refusal as 409, a start as 202
    as its own process; and the broker delivering a notice in General as a
    Loam line and a turn for General's default agent.

Run: python3 tests/test_update.py
"""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# The system persona's facts land in scratch, never the box's.
os.environ.setdefault("LOAM_ATTENTION_DIR", tempfile.mkdtemp(prefix="loam-update-attention-"))
os.environ.setdefault("LOAM_WAITERS_DIR", os.environ["LOAM_ATTENTION_DIR"] + "/waiters")

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-update-"))
INSTALL = TMP / "install"
CONFIG = TMP / "loam.toml"
CONFIG.write_text(f'''
[owner]
id = "sam"
name = "Sam"
[paths]
home = "{TMP}/home"
root = "{INSTALL}"
vault = "{TMP}/vault"
workspaces = "{TMP}/agents"
state = "{TMP}/state"
''')
os.environ["LOAM_CONFIG"] = str(CONFIG)
for var in ("LOAM_VAULT", "LOAM_AI_LABS", "LOAM_THREADS_STATE", "LOAM_AGENTS_STATE",
            "LOAM_UPDATE_STATE", "LOAM_UPDATE_RESTART", "LOAM_SESSION_ID"):
    os.environ.pop(var, None)
os.environ.setdefault("LOAM_LOCK_DIR", str(TMP / "locks"))
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
os.environ.update(GIT_ENV)
(TMP / "agents" / "iris").mkdir(parents=True)
(TMP / "agents" / "iris" / "agent.json").write_text(json.dumps({"name": "Iris"}))

sys.path.insert(0, str(ROOT / "lib"))
import loam_config as lc  # noqa: E402
import loam_skills as sk  # noqa: E402
import loam_update as up  # noqa: E402

FAILURES = 0


def check(label: str, got, want) -> None:
    global FAILURES  # noqa: PLW0603
    ok = got == want
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# -- the releases -----------------------------------------------------------------

ORIGIN, HQ = TMP / "origin.git", TMP / "hq"
subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(ORIGIN)], check=True)
subprocess.run(["git", "init", "-q", "-b", "main", str(HQ)], check=True)
git(HQ, "remote", "add", "origin", str(ORIGIN))
files = {
    "manifest.json": '{"version": "1.0.0"}\n',
    ".gitignore": "*.pyc\n*.local\n",
    "docs/library/README.md": "# Loam's library\n\nEvery page is a skill.\n",
    "docs/library/skills/loam-a/SKILL.md": "---\nname: loam-a\n---\nA.\n",
    "docs/releases/1.0.0.md": "The first release.\n",
    "docs/logs.md": "Logs.\n",
    "bin/gen-units": "import loam_units\n",
    "bin/session-broker": "import loam_threads\nimport loam_config\n",
    "bin/provision": "import loam_identity\n",
    "bin/provider-memory": "import loam_provider_memory\n",
    "bin/provider-pin": "import loam_pins\n",
    "lib/loam_pins.py": "PINS = {'claude-code': {'version': '2.1.1'}}\n",
    "bin/plugin-gateway": "import loam_config\n",
    "lib/loam_units.py": "import loam_config\n",
    "lib/loam_threads.py": "import loam_config\n",
    "lib/loam_identity.py": "import loam_config\n",
    "lib/loam_provider_memory.py": "\n",
    "lib/loam_config.py": "\n",
    "systemd/loam-x.service": "[Service]\n",
    "systemd/system/loam-y.service": "[Service]\n",
}
for name, text in files.items():
    write(HQ / name, text)


def release(version: str, changes: dict[str, str], manifest: str | None = None,
            notes: str | None = None) -> None:
    for name, text in changes.items():
        write(HQ / name, text)
    write(HQ / "manifest.json", json.dumps({"version": manifest or version}) + "\n")
    if notes:
        write(HQ / f"docs/releases/{version}.md", notes)
    git(HQ, "add", "-A")
    git(HQ, "commit", "-qm", f"Release {version}")
    git(HQ, "tag", "-a", version, "-m", f"Loam {version}")
    git(HQ, "push", "-q", "origin", "main", version)


release("1.0.0", {})
release("1.1.0", {"docs/library/README.md": "# Loam's library\n\nEvery page here is a skill.\n",
                  "systemd/loam-x.service": "[Service]\n# 1.1.0\n"},
        notes="Better words in the library.\n")


def install(path: Path, at: str = "1.0.0") -> None:
    shutil.rmtree(path, ignore_errors=True)
    subprocess.run(["git", "clone", "-q", "--branch", at, str(ORIGIN), str(path)],
                   check=True, capture_output=True)


def seed_library() -> None:
    sk.install_library(source=INSTALL / "docs" / "library")


class FakeBox(up.Box):
    def __init__(self, fail: dict[str, int] | None = None) -> None:
        super().__init__()
        self.calls: list[str] = []
        self.fail = dict(fail or {})

    def _do(self, name: str) -> str:
        self.calls.append(name)
        if self.fail.get(name):
            self.fail[name] -= 1
            raise RuntimeError(f"{name} broke")
        return f"{name} ran"

    def library(self) -> str:
        self.calls.append("library")
        sk.install_library(source=up.runtime() / "docs" / "library")
        return "installed"

    def units(self):
        return self._do("units")

    def agents(self):
        return self._do("agents")

    def providers(self):
        return self._do("providers")

    def provider_memory(self):
        return self._do("provider-memory")

    def broker(self):
        return self._do("broker")

    def gateway(self):
        return self._do("gateway")

    def deploy(self):
        return self._do("deploy")

    def app(self):
        return self._do("app")

    def swap(self):
        return self._do("swap")

    def host(self):
        return self._do("host")


def notices() -> list[dict]:
    return up.owed_notices()


def clear_notices() -> None:
    for n in notices():
        Path(n["_path"]).unlink()


# -- check ----------------------------------------------------------------------------

install(INSTALL)
seed_library()
v100 = git(INSTALL, "rev-parse", "HEAD")
st = up.status()
check("status: the running release from the manifest, a stock install",
      (st["current"]["version"], st["current"]["kind"], st["current"]["local_commits"]),
      ("1.0.0", "stock", 0))
st = up.check()
check("check: the newest tag where releases come from, available",
      ((st["check"]["latest"] or {}).get("version"), st["check"]["available"],
       st["check"]["blocked"], st["check"]["error"]), ("1.1.0", True, None, None))
check("...what the release changes in System/Loam, by name",
      st["check"]["changes"], {"added": [], "removed": [], "changed": ["README.md"]})
check("...the working tree untouched by the check",
      git(INSTALL, "status", "--porcelain", "--ignored"), "")
told = notices()
check("...one notice for the person's agent: a newer release, in General, its notes named",
      [(n["kind"], n["thread"], n["persona"]) for n in told], [("available", None, None)])
check("...the notice's turn input names the notes file, which holds the release's notes",
      (str(up.STATE / "notes" / "1.1.0.md") in told[0]["note"],
       (up.STATE / "notes" / "1.1.0.md").read_text().strip(),
       "own words" in told[0]["note"], "Sam" in told[0]["note"]),
      (True, "Better words in the library.", True, True))
up.check()
check("...and a second check tells nobody again", len(notices()), 1)
clear_notices()

# -- edits in System/Loam ------------------------------------------------------------

readme = sk.loam_dir() / "README.md"
readme.write_text(readme.read_text() + "Sam's own line.\n")
(sk.loam_dir() / "mine.md").write_text("Mine.\n")
edits = up.library_edits()
check("an edit in System/Loam is named: edited, added, and in the diff",
      (edits["any"], edits["edited"], edits["added"], edits["deleted"],
       "+Sam's own line." in edits["diff"], "yours/mine.md" in edits["diff"]),
      (True, ["README.md"], ["mine.md"], [], True, True))
box = FakeBox()
rec = up.apply(box=box)
check("an apply with edits not said yes to is refused, its edits in the refusal",
      (rec["outcome"], rec["refusal"]["code"], rec["refusal"]["library"]["edited"]),
      ("refused", "library-edits", ["README.md"]))
check("...nothing moved, nothing ran", (git(INSTALL, "rev-parse", "HEAD"), box.calls), (v100, []))
check("...and no refusal notice: the person is looking at the diff", notices(), [])

# -- apply and rollback ------------------------------------------------------------------

app_settings = INSTALL / "app" / ".claude" / "settings.json"
write(app_settings, "{}\n")
with (INSTALL / ".git" / "info" / "exclude").open("a") as exclude:
    exclude.write("/app/.claude/\n")
cache = INSTALL / "lib" / "__pycache__"
write(cache / "example.cpython-313.pyc", "cache")
write(cache / "important.txt", "owner data")
check("only bytecode is exempt from the stock preflight", len(up.dirt("stock")), 1)
(cache / "important.txt").unlink()
rec = up.apply(accept_library_edits=True, box=box)
check("apply: applied, the checkout at the tag's commit, detached",
      (rec["outcome"], git(INSTALL, "rev-parse", "HEAD"),
       git(INSTALL, "rev-parse", "1.1.0^{commit}"), up.branch()),
      ("applied", git(INSTALL, "rev-parse", "1.1.0^{commit}"),
       git(INSTALL, "rev-parse", "1.1.0^{commit}"), None))
check("...System/Loam replaced whole: the release's words, the extra file gone",
      ("Every page here is a skill." in readme.read_text(), "Sam's own line" in readme.read_text(),
       (sk.loam_dir() / "mine.md").exists()), (True, False, False))
backup = Path(rec["backup"])
check("...the edited copy kept in the backup",
      ("Sam's own line." in (backup / "README.md").read_text(), (backup / "mine.md").exists()),
      (True, True))
check("...only what reads what changed ran: the library and the units (systemd/)",
      box.calls, ["library", "units"])
check("...the others recorded as skipped, with why",
      [s["name"] for s in rec["steps"] if s["status"] == "skipped"],
      ["agents", "providers", "provider-memory", "broker", "gateway", "deploy", "app", "swap", "host"])
check("...the generated app settings survived and the updater sees a clean tree",
      (app_settings.read_text(), up.dirt("stock")), ("{}\n", []))
(cache / "example.cpython-313.pyc").unlink()
told = notices()
check("...a notice that it applied, naming the notes and the kept copy",
      ([n["kind"] for n in told], str(backup) in told[0]["note"],
       "rollback" in told[0]["note"]), (["applied"], True, True))
clear_notices()
st = up.status()
check("status: 1.1.0 running, the check no longer says available, a rollback offered",
      (st["current"]["version"], st["check"]["available"], st["rollback"]),
      ("1.1.0", False, {"to_version": "1.0.0", "from_version": "1.1.0",
                        "at": st["rollback"]["at"]}))
box = FakeBox()
rec = up.rollback(box=box)
check("rollback: one call puts 1.0.0 back",
      (rec["outcome"], git(INSTALL, "rev-parse", "HEAD"), up.running_version()),
      ("rolled-back", v100, "1.0.0"))
check("...the generated app settings survived rollback", app_settings.read_text(), "{}\n")
app_settings.unlink()
check("...System/Loam as it was before the update, Sam's edit included",
      ("Sam's own line." in readme.read_text(), (sk.loam_dir() / "mine.md").exists()),
      (True, True))
check("...the units regenerated from the old code", box.calls, ["units"])
check("...a notice that it was rolled back", [n["kind"] for n in notices()], ["rolled-back"])
clear_notices()
check("...and a second rollback has nothing to roll back",
      up.rollback(box=FakeBox())["refusal"]["code"], "nothing-to-roll-back")
readme.write_text(readme.read_text().replace("Sam's own line.\n", ""))
(sk.loam_dir() / "mine.md").unlink()

# -- the clean-tree rule, stock --------------------------------------------------------------

(INSTALL / "stray.txt").write_text("x")
(INSTALL / "lib" / "x.local").write_text("x")
rec = up.apply(box=FakeBox())
check("a stock install with an untracked and an ignored file refuses, naming both",
      (rec["outcome"], rec["refusal"]["code"], rec["refusal"]["files"]),
      ("refused", "dirty", ["?? stray.txt", "!! lib/x.local"]))
check("...nothing moved", git(INSTALL, "rev-parse", "HEAD"), v100)
told = notices()
check("...and the job handed to the person's agent, with what was found",
      ([n["kind"] for n in told], "stray.txt" in told[0]["note"],
       "1.1.0" in told[0]["line"]), (["refused"], True, True))
clear_notices()
(INSTALL / "stray.txt").unlink()
(INSTALL / "lib" / "x.local").unlink()

# -- a failed apply ------------------------------------------------------------------------

release("1.2.0", {
    "lib/loam_threads.py": "import loam_config\n# 1.2.0\n",
    "docs/releases/1.1.0.md": "## For your agent\n\nFind the updated library in Files.\n",
}, notes="## For your agent\n\nOpen Files to read the thread's notes.\n\n"
         "## Developer details\n\nInternal migration details.\n")
box = FakeBox(fail={"broker": 1})
rec = up.apply(box=box)
check("a failed broker restart: failed, and the record says 1.0.0 is back",
      (rec["outcome"], rec["failed_step"], rec["rolled_back"], "back at 1.0.0" in rec["message"]),
      ("failed", "broker", True, True))
check("...the checkout at 1.0.0 again and clean",
      (git(INSTALL, "rev-parse", "HEAD"), git(INSTALL, "status", "--porcelain", "--ignored")),
      (v100, ""))
check("...System/Loam is 1.0.0's", "Every page is a skill." in readme.read_text(), True)
check("...the units and the broker regenerated from 1.0.0 after the failure",
      box.calls, ["library", "units", "broker", "units", "broker"])
check("...the undo steps in the record",
      [s["name"] for s in rec["steps"] if s["name"].startswith("undo-")],
      ["undo-move", "undo-library", "undo-views", "undo-units", "undo-broker"])
check("...a notice: failed and undone", [n["kind"] for n in notices()], ["failed"])
clear_notices()
check("...and no rollback offered for an apply that did not land", up.status()["rollback"], None)


class DeferringBox(FakeBox):
    """Simulate a busy host deferring restart without replacing the process."""

    def broker(self):
        self.calls.append("broker")
        raise up.BrokerDeferred("restart-broker: deferred: still held after 1800 s")


box = DeferringBox()
rec = up.apply(box=box)
check("a deferred broker restart: failed at the broker, and rolled back",
      (rec["outcome"], rec["failed_step"], rec["rolled_back"]), ("failed", "broker", True))
check("...the broker step is marked deferred and says why",
      [(s.get("deferred"), "deferred" in (s.get("detail") or ""))
       for s in rec["steps"] if s["name"] == "broker"], [(True, True)])
check("...and the undo does not wait to restart a broker that never restarted",
      (box.calls.count("broker"),
       [(s["name"], s["status"]) for s in rec["steps"] if s["name"] == "undo-broker"]),
      (1, [("undo-broker", "skipped")]))
clear_notices()

# -- an apply killed mid-way -----------------------------------------------------------------


class KilledBox(FakeBox):
    def units(self):
        self.calls.append("units")
        raise SystemExit("killed")       # what a reboot looks like from here


box = KilledBox()
try:
    up.apply(box=box)
except SystemExit:
    pass
st = up.status()
check("an apply killed mid-way: the record says interrupted, a rollback is offered",
      (st["run"]["outcome"], st["rollback"]["to_version"], st["rollback"]["interrupted"],
       "rollback" in st["run"]["message"]), ("interrupted", "1.0.0", True, True))
check("...it had moved: 1.2.0 is checked out", up.running_version(), "1.2.0")
box = FakeBox()
rec = up.rollback(box=box)
check("...and rollback puts 1.0.0 back from the rollback point it left",
      (rec["outcome"], git(INSTALL, "rev-parse", "HEAD"), "Every page is a skill." in readme.read_text(),
       box.calls), ("rolled-back", v100, True, ["units", "broker"]))
check("...the rollback point is gone after", (up.STATE / "pending.json").exists(), False)
clear_notices()

# -- a development install -------------------------------------------------------------------

DEV = TMP / "dev"
install(DEV)
git(DEV, "checkout", "-q", "-b", "mike")
write(DEV / "docs" / "logs.md", "Logs.\nSam's line.\n")
git(DEV, "commit", "-qam", "Sam: a local change")
local = git(DEV, "rev-parse", "HEAD")
up_root, lc.ROOT = lc.ROOT, DEV
st = up.status()
check("a checkout with a local commit is a development install",
      (st["current"]["kind"], st["current"]["local_commits"], st["current"]["branch"]),
      ("development", 1, "mike"))
st = up.check(tell=False)
check("...its check finds 1.2.0 mergeable", (st["check"]["latest"]["version"],
                                             st["check"]["blocked"]), ("1.2.0", None))
write(DEV / "docs" / "logs.md", "Logs.\nSam's line.\nUncommitted.\n")
rec = up.apply(box=FakeBox(), accept_library_edits=True)
check("...an uncommitted change refuses", (rec["outcome"], rec["refusal"]["code"],
                                          rec["refusal"]["files"]),
      ("refused", "uncommitted", [" M docs/logs.md"]))
git(DEV, "checkout", "-q", "docs/logs.md")
rec = up.apply(box=FakeBox(), accept_library_edits=True)
check("...clean, it merges: applied, on its branch, its local commit kept",
      (rec["outcome"], up.branch(), git(DEV, "merge-base", "--is-ancestor", local, "HEAD") == "",
       up.running_version(), "Sam's line." in (DEV / "docs" / "logs.md").read_text()),
      ("applied", "mike", True, "1.2.0", True))
applied_news = next(n for n in notices() if n["kind"] == "applied")
check("applied news includes the incoming and crossed agent sections only",
      ("Open Files to read" in applied_news["note"],
       "Find the updated library" in applied_news["note"],
       "Internal migration details" in applied_news["note"],
       "Better words in the library" in applied_news["note"],
       "Never send a list of changes" in applied_news["note"]),
      (True, True, False, False, True))
old_notice = json.loads(Path(applied_news["_path"]).read_text())
old_notice.pop("release_commit")
check("a notice queued by the old updater uses the release commit from the apply record",
      "Find the updated library" in up.release_notice_detail(old_notice), True)

DEVC = TMP / "devc"
install(DEVC)
lc.ROOT = DEVC
git(DEVC, "checkout", "-q", "-b", "mike")
write(DEVC / "docs" / "library" / "README.md", "# Loam's library\n\nEach page is a skill.\n")
git(DEVC, "commit", "-qam", "Sam: reword the library")
mine = git(DEVC, "rev-parse", "HEAD")
st = up.check(tell=False)
check("a development install whose commit and the release change the same line: blocked",
      (st["check"]["blocked"] or {}).get("code"), "conflict")
clear_notices()
rec = up.apply(box=FakeBox(), accept_library_edits=True)
check("...apply refuses, naming the file", (rec["outcome"], rec["refusal"]["code"],
                                           rec["refusal"]["files"]),
      ("refused", "conflict", ["docs/library/README.md"]))
check("...and nothing moved: HEAD, the tree, the index",
      (git(DEVC, "rev-parse", "HEAD"), git(DEVC, "status", "--porcelain")), (mine, ""))
check("...the refusal handed to the agent", [n["kind"] for n in notices()], ["refused"])
clear_notices()
lc.ROOT = up_root

# -- other refusals ---------------------------------------------------------------------------

release("1.3.0", {"docs/logs.md": "Logs 1.3.\n"}, manifest="1.2.9")
rec = up.apply(box=FakeBox())
check("a release whose manifest disagrees with its tag is refused",
      (rec["outcome"], rec["refusal"]["code"], git(INSTALL, "rev-parse", "HEAD")),
      ("refused", "bad-release", v100))
rec = up.apply(target="1.0.0", box=FakeBox())
check("a version that is not newer is refused", (rec["refusal"]["code"],
                                                 rec["message"]), ("not-newer", "Loam is already at 1.0.0."))
rec = up.apply(target="9.9.9", box=FakeBox())
check("a version that does not exist is refused", rec["refusal"]["code"], "no-such-release")
clear_notices()

BARE = TMP / "nomanifest"
install(BARE)
git(BARE, "rm", "-q", "manifest.json")
git(BARE, "commit", "-qm", "no manifest")
lc.ROOT = BARE
st = up.check(tell=False)
check("a checkout with no manifest (the owner's box today) is not a release install",
      (st["check"]["error"]["code"], st["check"]["available"]), ("no-version", False))
rec = up.apply(box=FakeBox())
check("...and apply refuses it", rec["refusal"]["code"], "no-version")
clear_notices()
lc.ROOT = up_root

with up.run_lock():
    try:
        up.start("apply")
        got = None
    except up.Refusal as r:
        got = r.code
    check("a second run while one runs is refused", got, "busy")
    check("...and apply() in this process says so too",
          up.apply(box=FakeBox())["refusal"]["code"], "busy")

# -- what reads what --------------------------------------------------------------------------

old, new = git(INSTALL, "rev-parse", "1.0.0^{commit}"), git(INSTALL, "rev-parse", "1.2.0^{commit}")
changed = up.changed_files(old, new)
check("the closure: bin/session-broker reads lib/loam_threads.py through its import",
      sorted(up.import_closure(new, "bin/session-broker")),
      ["bin/session-broker", "lib/loam_config.py", "lib/loam_threads.py"])
check("...so 1.0.0 → 1.2.0 needs the broker and the units, not the agents",
      {s: up.needs(s, old, new, changed) for s in up.REGEN},
      {"units": True, "agents": False, "providers": False, "provider-memory": False,
       "broker": True, "gateway": False, "deploy": False, "app": False, "swap": False, "host": False})
check("...a change to the app's folder needs the app redeployed, and nothing else",
      {s: up.needs(s, old, new, ["app/src/server.js"]) for s in up.REGEN},
      {"units": False, "agents": False, "providers": False, "provider-memory": False,
       "broker": False, "gateway": False, "deploy": False, "app": True, "swap": False, "host": False})
check("...a release that moves a CLI pin (lib/loam_pins.py) puts the CLIs at it, "
      "re-pins their memory and restarts the broker, and nothing else",
      {s: up.needs(s, old, new, ["lib/loam_pins.py"]) for s in up.REGEN},
      {"units": False, "agents": False, "providers": True, "provider-memory": True,
       "broker": True, "gateway": False, "deploy": False, "app": False, "swap": False, "host": False})
check("...a change to swap helper or unit needs swap, not gen-units",
      (up.needs("swap", old, new, ["systemd/system/loam-swap.service"]),
       up.needs("units", old, new, ["systemd/system/loam-swap.service"])), (True, False))
check("...and the host history's timer needs `loam host install`, not gen-units",
      (up.needs("host", old, new, ["systemd/system/loam-host-history.timer"]),
       up.needs("units", old, new, ["systemd/system/loam-host-history.timer"])), (True, False))
check("...and a change under systemd/system/ alone needs no gen-units",
      up.needs("units", old, new, ["systemd/system/loam-y.service"]), False)

# -- the broker step, without a broker ------------------------------------------------------

FAKE = TMP / "fake-runtime"
write(FAKE / "bin" / "restart-broker",
      "import sys\nprint('restart-broker: held by iris (working)', flush=True)\n"
      "print('restart-broker: quiet', flush=True)\nsys.exit(int(sys.argv[-1] == 'fail'))\n")
lc.ROOT = FAKE
pids = iter([100, 100, 200])
real_pid, real_up = up._main_pid, up._broker_up
up._main_pid = lambda unit: next(pids, 200)
up._broker_up = lambda: True
up.BROKER_UP_S = 10
seen = []
b2 = up.Box()
b2.on_line = seen.append
check("the broker step: the wait's lines reach the progress, a new process must answer",
      (b2.broker(), seen), ("the broker answers again (pid 200)",
                            ["restart-broker: held by iris (working)", "restart-broker: quiet"]))
up._main_pid = lambda unit: 100
up.BROKER_UP_S = 1
try:
    b2.broker()
    got = None
except RuntimeError as exc:
    got = str(exc)
check("...a broker that keeps its old process is a failure", got,
      "the broker did not answer within 1 s of its restart")
write(FAKE / "bin" / "restart-broker", "import sys\nprint('restart-broker: still held')\n"
      "sys.exit(75)\n")
try:
    b2.broker()
    got = None
except RuntimeError as exc:
    got = str(exc)
check("...and a restart-broker that deferred is a failure, saying why", got,
      "restart-broker: still held")
try:
    b2.broker()
    got = None
except up.BrokerDeferred:
    got = "deferred"
except RuntimeError:
    got = "other"
check("...told apart as a deferral (exit 75: nothing was restarted)", got, "deferred")
up._main_pid, up._broker_up = real_pid, real_up
lc.ROOT = up_root

# -- the routes and the delivery ------------------------------------------------------------------


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


broker_mod = load("session_broker_update", ROOT / "bin" / "session-broker")
engine_mod = broker_mod.loam_thread_engine
threads = broker_mod.threads_rec


class Writer:
    def __init__(self):
        self.chunks = []

    def write(self, d):
        self.chunks.append(d)

    async def drain(self):
        pass

    def status(self):
        return int(b"".join(self.chunks).split(b" ", 2)[1])

    def json(self):
        t = b"".join(self.chunks).decode()
        return json.loads(t.split("\r\n\r\n", 1)[1])


class FakeBroker:
    def __init__(self):
        self.sessions, self.record_subscribers = {}, set()
        self.stopping = False

    def live(self):
        return []


async def route(server, method, path, payload=None):
    wr = Writer()
    await server.route(method, path, {}, payload or {}, wr)
    return wr.status(), wr.json()


async def broker_checks():
    server = broker_mod.Server.__new__(broker_mod.Server)
    server.broker = FakeBroker()
    server.threads = engine_mod.ThreadEngine(server.broker)
    code, body = await route(server, "GET", "/v1/update")
    check("GET /v1/update: the running release and the last check",
          (code, body["current"]["version"], body["check"]["latest"]["version"], body["running"]),
          (200, "1.0.0", "1.3.0", False))
    code, body = await route(server, "POST", "/v1/update/check")
    check("POST /v1/update/check: a fresh check", (code, body["check"]["latest"]["version"]),
          (200, "1.3.0"))
    code, body = await route(server, "POST", "/v1/update/apply", {"to": "1.3.0"})
    check("POST /v1/update/apply: a refusal is a 409 with its code and sentence",
          (code, body["refusal"]["code"], bool(body["error"])), (409, "bad-release", True))
    started = []
    real_spawn, up.spawn = up.spawn, lambda argv, run_id: started.append(argv)
    try:
        code, body = await route(server, "POST", "/v1/update/apply",
                                 {"to": "1.2.0", "by": "iris",
                                  "accept_library_edits": True})
    finally:
        up.spawn = real_spawn
    check("...a start is a 202 with the run's first record, as its own process",
          (code, body["run"]["outcome"], body["run"]["action"], body["run"]["by"]),
          (202, "starting", "apply", "iris"))
    check("...the process runs bin/loam-update apply in the foreground with the target",
          (started[0][1].endswith("bin/loam-update"), started[0][2:4], "--to" in started[0],
           "1.2.0" in started[0]), (True, ["apply", "--foreground"], True, True))
    code, body = await route(server, "GET", "/v1/update/progress")
    check("GET /v1/update/progress: the run alone", (code, body["action"]), (200, "apply"))
    code, body = await route(server, "POST", "/v1/update/rollback")
    check("POST /v1/update/rollback after an apply this checkout has moved past: 409",
          (code, body["refusal"]["code"]), (409, "moved-since"))
    (up.STATE / "last.json").unlink()
    code, body = await route(server, "POST", "/v1/update/rollback")
    check("...and with nothing to roll back: 409", (code, body["refusal"]["code"]),
          (409, "nothing-to-roll-back"))
    code, _ = await route(server, "DELETE", "/v1/update")
    check("...another method on a known route is a 405", code, 405)
    code, _ = await route(server, "GET", "/v1/update/nope")
    check("...an unknown route a 404", code, 404)
    code, body = await route(server, "POST", "/v1/update/apply", {"to": 5})
    check("...a `to` that is not a version a 400", code, 400)

    clear_notices()
    note = applied_news
    sent = []

    def schedule(turn):
        sent.append(turn)
    server.threads.schedule = schedule
    attention = Path(os.environ["LOAM_ATTENTION_DIR"])

    def facts():
        return [json.loads(f.read_text()) for f in sorted(attention.glob("2*.json"))]
    threads.ensure_general()
    general_before = len(threads.read(threads.GENERAL_ID)[1])
    got = server.threads.deliver_note(note)
    told = facts()
    check("deliver_note: a fact for the system persona, the line and its note",
          (got, [(f["fact"], f["by"], f["detail"]) for f in told]),
          ("delivered", [("Loam updated from 1.0.0 to 1.2.0.", "loam update",
                          applied_news["note"])]))
    prompt = up.loam_attention.input_text(told)
    check("the agent's input contains the editorial and sharing instructions",
          ("Open Files to read" in prompt, "Find the updated library" in prompt,
           "Internal migration details" in prompt, "what they have asked for" in prompt),
          (True, True, False, True))
    check("...nothing posted in General and no turn given (2026-11-19)",
          (len(threads.read(threads.GENERAL_ID)[1]) - general_before, len(sent)), (0, 0))
    up.notice_done(note, got)
    check("...and a delivered notice is gone", notices(), [])

    # Available and applied share delivery policy. Use real notice files and a
    # renamed starter thread; a thread the owner happens to call Hello is unrelated.
    import loam_starter as starter
    from unittest.mock import patch

    hello = threads.create("iris", "Hello", extra={"hello": True})["id"]
    threads.update(hello, title="Welcome home")
    threads.create("iris", "Hello")
    record = {"first_weeks": True, "thread": hello}
    with patch.object(starter, "status", return_value=record), patch.object(
            up, "save_notes", return_value=TMP / "release.md"):
        up.tell_available("9.1.0", "9.0.0")
        available = notices()[0]
        check("available release news names the original Hello and allows silence",
              (hello in available["note"], "leave the release unannounced" in available["note"],
               "if it has been deleted" in available["note"]), (True, True, True))
        up.notice_done(available, "delivered")
        threads.delete(hello)
        up.tell_applied({"to_tag": "9.1.0", "to_version": "9.1.0", "from_version": "9.0.0"})
        applied = notices()[0]
        check("applied release news after deleting Hello goes to General",
              ("post in General" in applied["note"], hello in applied["note"],
               "leave the release unannounced" in applied["note"]), (True, False, True))
        up.notice_done(applied, "delivered")
    with patch.object(starter, "status", return_value={"thread": hello}):
        check("a legacy starter keeps the original notice",
              up._release_message("what changed"), f"Tell {lc.OWNER_NAME}, briefly and in your own words, what changed.")

    # Repeated reads of a pending notice must not start duplicate turns.
    check("a notice delivered again is not told again",
          server.threads.deliver_note(note), "already delivered")
    up.notice("refused", "Loam's update did not run: X.", "Fix X.")
    again = notices()[0]
    check("...a new notice is its own fact",
          server.threads.deliver_note(again), "delivered")
    check("...still nothing in General and no turn",
          (len(threads.read(threads.GENERAL_ID)[1]) - general_before, len(sent), len(facts())),
          (0, 0, 2))
    server.threads._system_turns = None   # as after a broker restart: read from disk
    check("...and the record outlives the broker",
          server.threads.deliver_note(note), "already delivered")

    job = {"job": "j1", "thread": threads.GENERAL_ID, "persona": "iris"}
    # The provider matrix tests sign-in. This fixture exercises deduplication.
    with patch.object(threads, "member_engine", return_value="claude-code"):
        check("the same guard holds for a job's delivery",
              (server.threads.deliver_job(job, "Job one finished."),
               server.threads.deliver_job(job, "Job one finished.")),
              ("delivered", "already delivered"))

    # A notice whose file cannot be removed (a root run left it) is said
    # once, loudly, and never swallowed.
    stuck_dir = up.STATE / "notices"
    os.chmod(stuck_dir, 0o500)
    try:
        import contextlib as _cl
        import io as _io
        err = _io.StringIO()
        with _cl.redirect_stderr(err):
            first = up.notice_done(again, "delivered")
            second = up.notice_done(again, "already delivered")
        log = [json.loads(x) for x in up.LOG.read_text().splitlines()]
        stuck = [e for e in log if e.get("kind") == "notice-stuck"
                 and e.get("id") == again["id"]]
        check("notice_done: a notice that cannot be removed says so",
              (first, second), (False, False))
        check("...once, in the log and on stderr, with the fix",
              (len(stuck), err.getvalue().count("loam_update: update notice"),
               "chown -R" in stuck[0]["message"] if stuck else None), (1, 1, True))
    finally:
        os.chmod(stuck_dir, 0o700)
    clear_notices()


asyncio.run(broker_checks())


# Run the updater from a root login environment.

drop = up.as_install_user(["apply", "--yes"], euid=0)
check("as root, loam-update re-runs itself as the install's user",
      (drop[:4], drop[-3:], drop[-4] == sys.executable),
      (["runuser", "-u", lc.USER, "--"], [str(up.runtime() / "bin" / "loam-update"),
                                          "apply", "--yes"], True))
env = dict(x.split("=", 1) for x in drop[drop.index("-i") + 1:drop.index(sys.executable)])
check("...with that user's home, runtime dir and bytecode cache, and LOAM_ kept",
      (env["HOME"], env["XDG_RUNTIME_DIR"], env["PYTHONPYCACHEPREFIX"].startswith(str(lc.HOME)),
       env.get("LOAM_CONFIG")),
      (str(lc.HOME), f"/run/user/{lc.UID}", True, str(CONFIG)))
check("...and not when it already is that user",
      up.as_install_user(["check"], euid=os.geteuid()), None)
check("repair_owner changes nothing unless run as root", up.repair_owner(euid=1000), [])

bad = TMP / "notmine"
subprocess.run(["git", "init", "-q", str(bad)], check=True)
dubious = subprocess.CompletedProcess([], 128, "", "fatal: detected dubious ownership "
                                      "in repository at '/home/loam/loam'\n")
real_git = up.git
up.git = lambda *a, **k: dubious
try:
    up.repo_top()
    got = None
except up.Refusal as r:
    got = (r.code, lc.USER in r.message, "not a git checkout" in r.message)
finally:
    up.git = real_git
check("git's ownership refusal reads as whose the checkout is, not 'not a git checkout'",
      got, ("not-owner", True, False))


def bare_update(stdin=subprocess.DEVNULL) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "bin" / "loam-update")],
                          capture_output=True, text=True, stdin=stdin,
                          env={**os.environ, "LOAM_UPDATE_NO_UNIT": "1"})


clear_notices()
install(INSTALL)
done = bare_update()
newest = sorted(git(ORIGIN, "tag").split(), key=up.version_key)[-1]
check("bare `loam update` is not a usage error: it checks and says what to type",
      (done.returncode, f"Loam {newest} is available" in done.stdout,
       "loam update apply" in done.stdout, git(INSTALL, "rev-parse", "HEAD") == v100),
      (0, True, True, True))
check("...and leaves no notice for the agent (the person is right there)", notices(), [])

# -- post-apply notice failure, stale dead-pid records, --no-tell, swap catch-up --------

from unittest.mock import patch as _patch  # noqa: E402

# (2) A post-apply notice failure must never leave apply.json marked "running".
clear_notices()
install(INSTALL)
seed_library()
with _patch.object(up, "tell_applied", side_effect=AttributeError("simulated notice boom")):
    rec = up.apply(target="1.2.0", accept_library_edits=True, box=FakeBox())
on_disk = up.read_json("apply.json") or {}
check("post-apply notice failure still records applied outcome on disk",
      (rec["outcome"], on_disk.get("outcome"), on_disk.get("phase"),
       bool(on_disk.get("ended_at")),
       any("could not leave applied notice" in w for w in on_disk.get("warnings") or [])),
      ("applied", "applied", None, True, True))
check("...and status reports applied, not running or interrupted",
      (up.status()["running"], up.status()["run"]["outcome"]), (False, "applied"))

# (3) A stale "running" record with a dead pid is reconciled by status, the broker routes,
# and never blocks the next update.
DEAD_PID = 2_147_483_640
# 3a: post-apply crash (last.json matches this run and pending.json is gone) -> healed to applied.
v120 = git(INSTALL, "rev-parse", "HEAD")
stale_applied = {
    "id": "20261124-060335-dead01", "action": "apply", "outcome": "running",
    "phase": "host", "started_at": "2026-11-24T06:03:35Z", "ended_at": None,
    "pid": DEAD_PID, "from_version": "1.0.0", "to_version": "1.2.0",
    "steps": [{"name": "host", "words": "Installing the host history's timer", "status": "done"}],
    "message": None,
}
up.write_json("apply.json", stale_applied)
up.write_json("last.json", {
    "from_version": "1.0.0", "from_commit": v100, "to_version": "1.2.0",
    "to_commit": v120, "kind": "stock", "backup": None, "ran": ["library", "host"],
    "at": "2026-11-24T06:03:55Z", "run": "20261124-060335-dead01",
})
(up.STATE / "pending.json").unlink(missing_ok=True)
st = up.status()
check("stale running record with dead pid and matching last.json heals to applied",
      (st["running"], st["run"]["outcome"], st["run"]["phase"], st["run"]["message"],
       (up.read_json("apply.json") or {}).get("outcome")),
      (False, "applied", None, "Loam is at 1.2.0.", "applied"))


async def _stale_route_checks():
    server = broker_mod.Server.__new__(broker_mod.Server)
    server.broker = FakeBroker()
    server.threads = engine_mod.ThreadEngine(server.broker)
    up.write_json("apply.json", stale_applied)
    code1, body1 = await route(server, "GET", "/v1/update")
    up.write_json("apply.json", stale_applied)
    code2, body2 = await route(server, "GET", "/v1/update/progress")
    check("...and GET /v1/update and /v1/update/progress both see applied",
          (code1, body1["running"], body1["run"]["outcome"], code2, body2["outcome"]),
          (200, False, "applied", 200, "applied"))


asyncio.run(_stale_route_checks())

# 3b: mid-apply crash (dead pid, pending.json present) -> healed to interrupted + rollback offered.
stale_mid = {**stale_applied, "id": "20261124-060335-dead02", "phase": "units"}
up.write_json("apply.json", stale_mid)
up.write_json("pending.json", {
    "from_version": "1.0.0", "from_commit": v100, "to_version": "1.2.0",
    "to_commit": v120, "kind": "stock", "backup": None, "library_replaced": False,
    "ran": [], "run": "20261124-060335-dead02", "at": "2026-11-24T06:03:40Z",
})
st = up.status()
check("stale running record with dead pid and pending.json heals to interrupted",
      (st["running"], st["run"]["outcome"], (st["rollback"] or {}).get("interrupted"),
       (up.read_json("apply.json") or {}).get("outcome")),
      (False, "interrupted", True, "interrupted"))
(up.STATE / "pending.json").unlink(missing_ok=True)

# 3c: stale running record with dead pid does not block the next apply or start().
install(INSTALL)
seed_library()
up.write_json("apply.json", {**stale_applied, "id": "20261124-060335-dead03"})
(up.STATE / "run.lock").touch()
rec = up.apply(target="1.1.0", accept_library_edits=True, box=FakeBox(), tell=False)
check("stale running record with dead pid does not block the next update",
      (rec["outcome"], up.running_version(), (up.read_json("apply.json") or {}).get("outcome")),
      ("applied", "1.1.0", "applied"))

# (4) --no-tell honored on apply, rollback, and start (success, refusal, failure).
clear_notices()
install(INSTALL)
seed_library()
rec = up.apply(target="1.1.0", accept_library_edits=True, box=FakeBox(), tell=False)
check("apply(tell=False) leaves no applied notice", (rec["outcome"], notices()), ("applied", []))
rec = up.rollback(box=FakeBox(), tell=False)
check("rollback(tell=False) leaves no rolled-back notice", (rec["outcome"], notices()), ("rolled-back", []))
rec = up.apply(target="1.2.0", accept_library_edits=True, box=FakeBox(fail={"broker": 1}), tell=False)
check("apply(tell=False) leaves no failed notice", (rec["outcome"], notices()), ("failed", []))
(INSTALL / "stray-notell.txt").write_text("x")
rec = up.apply(target="1.1.0", box=FakeBox(), tell=False)
check("apply(tell=False) leaves no refusal notice", (rec["outcome"], notices()), ("refused", []))
try:
    up.start("apply", target="1.1.0", tell=False)
    got_refusal = None
except up.Refusal as r:
    got_refusal = r.code
check("start(tell=False) leaves no refusal notice", (got_refusal, notices()), ("dirty", []))
(INSTALL / "stray-notell.txt").unlink()
spawned_argv: list[list[str]] = []
with _patch.object(up, "spawn", side_effect=lambda argv, run_id: spawned_argv.append(argv)):
    up.start("apply", target="1.1.0", accept_library_edits=True, tell=False)
check("start(tell=False) passes --no-tell to the spawned updater",
      "--no-tell" in (spawned_argv[0] if spawned_argv else []), True)

# (5) Box.swap_needed() triggers swap on update when bin/loam-swap exists and no swap is active,
# even if swap files did not change between the two releases.
class SwapCatchupBox(FakeBox):
    def swap_needed(self) -> bool:
        return True


install(INSTALL)
seed_library()
sbox = SwapCatchupBox()
rec = up.apply(target="1.1.0", accept_library_edits=True, box=sbox, tell=False)
check("swap runs automatically on update when host has no swap yet",
      ("swap" in sbox.calls, any(s["name"] == "swap" and s["status"] == "done" for s in rec["steps"])),
      (True, True))

# (1) Real-subprocess re-exec test: upgrading to a release whose loam_config gained a new
# attribute referenced by loam_deploy (the exact 0.2.31 -> 0.2.34 BROWSER_OUTPUT skew) AND
# whose updater added a new post-move step succeeds cleanly by re-execing into the new release.
SKEW = TMP / "skew"
SKEW.mkdir(parents=True, exist_ok=True)
SKEW_ORIGIN, SKEW_HQ, SKEW_INSTALL = SKEW / "origin.git", SKEW / "hq", SKEW / "install"
SKEW_CFG = SKEW / "loam.toml"
SKEW_CFG.write_text(f'''
[owner]
id = "sam"
name = "Sam"
[edge]
app = "loam-ui"
[paths]
home = "{SKEW}/home"
root = "{SKEW_INSTALL}"
vault = "{SKEW}/vault"
workspaces = "{SKEW}/agents"
state = "{SKEW}/state"
''')
(SKEW / "agents" / "iris").mkdir(parents=True)
(SKEW / "agents" / "iris" / "agent.json").write_text(json.dumps({"name": "Iris"}))
subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(SKEW_ORIGIN)], check=True)
subprocess.run(["git", "init", "-q", "-b", "main", str(SKEW_HQ)], check=True)
git(SKEW_HQ, "remote", "add", "origin", str(SKEW_ORIGIN))

# Copy the real updater + lib modules into SKEW_HQ so the subprocess executes the real code.
shutil.copytree(ROOT / "lib", SKEW_HQ / "lib",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
write(SKEW_HQ / "bin" / "loam-update", (ROOT / "bin" / "loam-update").read_text(encoding="utf-8"))
os.chmod(SKEW_HQ / "bin" / "loam-update", 0o755)
write(SKEW_HQ / "bin" / "library", "#!/usr/bin/env python3\nprint('library ok')\n")
os.chmod(SKEW_HQ / "bin" / "library", 0o755)
write(SKEW_HQ / "manifest.json", '{"version": "2.0.0"}\n')
write(SKEW_HQ / "docs/library/README.md", "# Library 2.0.0\n")
write(SKEW_HQ / "docs/releases/2.0.0.md", "Release 2.0.0.\n")
# Ensure loam_deploy.CLAIMED_FILE points into SKEW so unclaimed() returns False (claimed install).
claimed_marker = SKEW / "claimed"
claimed_marker.write_text(json.dumps({"at": "2026-01-01T00:00:00Z"}))
deploy_src = (SKEW_HQ / "lib" / "loam_deploy.py").read_text(encoding="utf-8")
deploy_src = deploy_src.replace('CLAIMED_FILE = ETC / "claimed"',
                                f'CLAIMED_FILE = Path({str(claimed_marker)!r})')
write(SKEW_HQ / "lib" / "loam_deploy.py", deploy_src)
git(SKEW_HQ, "add", "-A")
git(SKEW_HQ, "commit", "-qm", "Release 2.0.0")
git(SKEW_HQ, "tag", "-a", "2.0.0", "-m", "Loam 2.0.0")
git(SKEW_HQ, "push", "-q", "origin", "main", "2.0.0")

# Release 2.1.0 gains a brand-new attribute in loam_config referenced at module top-level in
# loam_deploy (reproducing BROWSER_OUTPUT), plus a marker proving 2.1.0's updater ran post-move.
cfg_v21 = (SKEW_HQ / "lib" / "loam_config.py").read_text(encoding="utf-8") + \
    '\nNEW_IN_2_1_0 = "v2.1.0-only-attr"\n'
write(SKEW_HQ / "lib" / "loam_config.py", cfg_v21)
deploy_v21 = deploy_src + '\nSYNTHETIC_SKEW_CHECK = lc.NEW_IN_2_1_0\n'
write(SKEW_HQ / "lib" / "loam_deploy.py", deploy_v21)
marker_file = SKEW / "reexec-ran.txt"
up_v21 = (SKEW_HQ / "lib" / "loam_update.py").read_text(encoding="utf-8")
up_v21 = up_v21.replace(
    '    res = run.end("applied", f"Loam is at {to_version}.")',
    f'    Path({str(marker_file)!r}).write_text(loam_deploy.SYNTHETIC_SKEW_CHECK, encoding="utf-8")\n'
    '    res = run.end("applied", f"Loam is at {to_version}.")',
)
write(SKEW_HQ / "lib" / "loam_update.py", up_v21)
write(SKEW_HQ / "manifest.json", '{"version": "2.1.0"}\n')
write(SKEW_HQ / "docs/library/README.md", "# Library 2.1.0\n")
write(SKEW_HQ / "docs/releases/2.1.0.md", "## For your agent\n\nRelease 2.1.0.\n")
git(SKEW_HQ, "add", "-A")
git(SKEW_HQ, "commit", "-qm", "Release 2.1.0")
git(SKEW_HQ, "tag", "-a", "2.1.0", "-m", "Loam 2.1.0")
git(SKEW_HQ, "push", "-q", "origin", "main", "2.1.0")

subprocess.run(["git", "clone", "-q", "--branch", "2.0.0", str(SKEW_ORIGIN), str(SKEW_INSTALL)],
               check=True, capture_output=True)
skew_env = {**os.environ, "LOAM_CONFIG": str(SKEW_CFG), "LOAM_UPDATE_NO_UNIT": "1",
            "PYTHONDONTWRITEBYTECODE": "1"}
# 1a: apply with notices enabled -> re-execs into 2.1.0, uses 2.1.0's loam_config + loam_deploy.
done = subprocess.run(
    [sys.executable, str(SKEW_INSTALL / "bin" / "loam-update"),
     "apply", "--foreground", "--to", "2.1.0", "--yes", "--json"],
    capture_output=True, text=True, env=skew_env,
)
skew_rec = json.loads(done.stdout) if done.returncode == 0 and done.stdout.strip() else {}
skew_notices = list((SKEW / "state" / "update" / "notices").glob("*.json"))
check("re-exec apply succeeds when new release adds a loam_config attr used by loam_deploy",
      (done.returncode, skew_rec.get("outcome"),
       marker_file.read_text(encoding="utf-8") if marker_file.exists() else None,
       len(skew_notices)),
      (0, "applied", "v2.1.0-only-attr", 1))
for p in skew_notices:
    p.unlink()

# 1b: rollback with --no-tell re-execs back to 2.0.0 and leaves no notice.
done_rb = subprocess.run(
    [sys.executable, str(SKEW_INSTALL / "bin" / "loam-update"),
     "rollback", "--foreground", "--no-tell", "--json"],
    capture_output=True, text=True, env=skew_env,
)
rb_rec = json.loads(done_rb.stdout) if done_rb.returncode == 0 and done_rb.stdout.strip() else {}
check("re-exec rollback with --no-tell puts 2.0.0 back and leaves no notice",
      (done_rb.returncode, rb_rec.get("outcome"),
       git(SKEW_INSTALL, "rev-parse", "HEAD") == git(SKEW_ORIGIN, "rev-parse", "2.0.0^{commit}"),
       list((SKEW / "state" / "update" / "notices").glob("*.json"))),
      (0, "rolled-back", True, []))

# 1c: `loam update update --to 2.1.0 --yes --foreground --no-tell` honors --to and --no-tell.
marker_file.unlink(missing_ok=True)
done_u = subprocess.run(
    [sys.executable, str(SKEW_INSTALL / "bin" / "loam-update"),
     "update", "--to", "2.1.0", "--yes", "--foreground", "--no-tell"],
    capture_output=True, text=True, env=skew_env,
)
check("`loam-update update --to 2.1.0 --yes --foreground --no-tell` applies without notice",
      (done_u.returncode,
       marker_file.read_text(encoding="utf-8") if marker_file.exists() else None,
       list((SKEW / "state" / "update" / "notices").glob("*.json"))),
      (0, "v2.1.0-only-attr", []))

# -- Rollback across native-login -> pre-service-login boundary ----------------------
# Verify `_target_has_native_login` distinguishes pre-service and native commits,
# and `restore_container_login_if_older` invokes `gen-deploy --restore-container-login`
# only when rolling back to a pre-service commit while native login is installed.
RB_REPO = TMP / "rb-login-repo"
RB_REPO.mkdir()
subprocess.run(["git", "init", "-q", "-b", "main", str(RB_REPO)], check=True)
write(RB_REPO / "bin" / "gen-deploy", "#!/usr/bin/env python3\n")
write(RB_REPO / "lib" / "loam_deploy.py", "def authelia_compose():\n    return ''\n")
git(RB_REPO, "add", "-A")
git(RB_REPO, "commit", "-qm", "Pre-service login")
old_commit = git(RB_REPO, "rev-parse", "HEAD")
write(RB_REPO / "lib" / "loam_deploy.py", "def authelia_unit():\n    return ''\n")
git(RB_REPO, "add", "-A")
git(RB_REPO, "commit", "-qm", "Native login service")
new_commit = git(RB_REPO, "rev-parse", "HEAD")

orig_root = up.lc.ROOT
orig_subproc = up.subprocess.run
orig_unit = up.loam_deploy.AUTHELIA_UNIT
orig_compose = up.loam_deploy.AUTHELIA_COMPOSE
try:
    up.lc.ROOT = RB_REPO
    check("_target_has_native_login is False for pre-service commit and True for native commit",
          (up._target_has_native_login(old_commit), up._target_has_native_login(new_commit)),
          (False, True))
    fake_unit = TMP / "fake-authelia.service"
    fake_unit.write_text("[Unit]\n", encoding="utf-8")
    fake_compose = TMP / "fake-authelia-compose.yaml"
    up.loam_deploy.AUTHELIA_UNIT = fake_unit
    up.loam_deploy.AUTHELIA_COMPOSE = fake_compose
    ran_cmds: list[list[str]] = []

    def fake_subproc(argv, **kw):
        if "gen-deploy" in " ".join(str(a) for a in argv):
            ran_cmds.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "ok", "")
        return orig_subproc(argv, **kw)

    up.subprocess.run = fake_subproc
    up.restore_container_login_if_older(new_commit)
    check("rolling to a native-login commit does NOT invoke --restore-container-login",
          ran_cmds, [])
    up.restore_container_login_if_older(old_commit)
    check("rolling back to a pre-service commit invokes gen-deploy --restore-container-login",
          len(ran_cmds) == 1 and "--restore-container-login" in ran_cmds[0], True)

    # Rollback across docker-idle-watch -> 0.2.36 (pre-watch) boundary runs inside reset_to()
    # BEFORE re-execing into 0.2.36's --resume-rollback.
    write(RB_REPO / "lib" / "loam_deploy.py",
          "def authelia_unit():\n    return ''\n\ndef docker_idle_watch_timer():\n    return ''\n")
    git(RB_REPO, "add", "-A")
    git(RB_REPO, "commit", "-qm", "Docker idle watcher")
    watch_commit = git(RB_REPO, "rev-parse", "HEAD")

    orig_wtimer = up.loam_deploy.DOCKER_IDLE_WATCH_TIMER
    orig_wunit = up.loam_deploy.DOCKER_IDLE_WATCH_UNIT
    try:
        fake_wtimer = TMP / "loam-docker-idle-watch.timer"
        fake_wunit = TMP / "loam-docker-idle-watch.service"
        fake_wtimer.write_text("[Timer]\n", encoding="utf-8")
        fake_wunit.write_text("[Service]\n", encoding="utf-8")
        up.loam_deploy.DOCKER_IDLE_WATCH_TIMER = fake_wtimer
        up.loam_deploy.DOCKER_IDLE_WATCH_UNIT = fake_wunit
        check("_target_has_docker_idle_watch distinguishes 0.2.36 from 0.2.37",
              (up._target_has_docker_idle_watch(new_commit),
               up._target_has_docker_idle_watch(watch_commit)),
              (False, True))
        watch_cmds: list[list[str]] = []

        def watch_subproc(argv, **kw):
            if argv and argv[0] == "sudo":
                watch_cmds.append(list(argv))
                return subprocess.CompletedProcess(argv, 0, "", "")
            return orig_subproc(argv, **kw)

        up.subprocess.run = watch_subproc
        up.remove_docker_idle_watch_if_older(watch_commit)
        check("rolling to a commit with docker_idle_watch_timer leaves watcher units untouched",
              watch_cmds, [])
        up.remove_docker_idle_watch_if_older(new_commit)
        check("rolling back to 0.2.36 disables, removes watcher units, and daemon-reloads",
              (len(watch_cmds) == 3
               and "disable" in watch_cmds[0]
               and up.loam_deploy.DOCKER_IDLE_WATCH_TIMER_NAME in watch_cmds[0]
               and "rm" in watch_cmds[1] and str(fake_wtimer) in watch_cmds[1]
               and "daemon-reload" in watch_cmds[2]),
              True)
    finally:
        up.loam_deploy.DOCKER_IDLE_WATCH_TIMER = orig_wtimer
        up.loam_deploy.DOCKER_IDLE_WATCH_UNIT = orig_wunit
finally:
    up.lc.ROOT = orig_root
    up.subprocess.run = orig_subproc
    up.loam_deploy.AUTHELIA_UNIT = orig_unit
    up.loam_deploy.AUTHELIA_COMPOSE = orig_compose

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'FAILED' if FAILURES else 'OK'}: {FAILURES} failure(s)")
sys.exit(1 if FAILURES else 0)
