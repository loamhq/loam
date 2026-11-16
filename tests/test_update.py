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
    ".gitignore": "*.pyc\n",
    "docs/library/README.md": "# Loam's library\n\nEvery page is a skill.\n",
    "docs/library/skills/loam-a/SKILL.md": "---\nname: loam-a\n---\nA.\n",
    "docs/releases/1.0.0.md": "The first release.\n",
    "docs/logs.md": "Logs.\n",
    "bin/gen-units": "import loam_units\n",
    "bin/session-broker": "import loam_threads\nimport loam_config\n",
    "bin/provision": "import loam_identity\n",
    "bin/provider-memory": "import loam_provider_memory\n",
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

    def provider_memory(self):
        return self._do("provider-memory")

    def broker(self):
        return self._do("broker")

    def gateway(self):
        return self._do("gateway")


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
      ["agents", "provider-memory", "broker", "gateway"])
check("...the tree is clean after it", git(INSTALL, "status", "--porcelain", "--ignored"), "")
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
(INSTALL / "lib" / "x.pyc").write_text("x")
rec = up.apply(box=FakeBox())
check("a stock install with an untracked and an ignored file refuses, naming both",
      (rec["outcome"], rec["refusal"]["code"], rec["refusal"]["files"]),
      ("refused", "dirty", ["?? stray.txt", "!! lib/x.pyc"]))
check("...nothing moved", git(INSTALL, "rev-parse", "HEAD"), v100)
told = notices()
check("...and the job handed to the person's agent, with what was found",
      ([n["kind"] for n in told], "stray.txt" in told[0]["note"],
       "1.1.0" in told[0]["line"]), (["refused"], True, True))
clear_notices()
(INSTALL / "stray.txt").unlink()
(INSTALL / "lib" / "x.pyc").unlink()

# -- a failed apply ------------------------------------------------------------------------

release("1.2.0", {"lib/loam_threads.py": "import loam_config\n# 1.2.0\n"})
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
      {"units": True, "agents": False, "provider-memory": False, "broker": True,
       "gateway": False})
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
check("...and a restart-broker that gave up is a failure, saying why", got,
      "restart-broker: still held")
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
    up.notice("applied", "Loam updated from 1.0.0 to 1.2.0.", "Read /x/notes.md and say it.")
    note = notices()[0]
    sent = []

    def schedule(turn):
        sent.append(turn)
    server.threads.schedule = schedule
    got = server.threads.deliver_note(note)
    meta, msgs, _ = threads.read(threads.GENERAL_ID)
    check("deliver_note: in General, one Loam line, the default agent given the turn",
          (got, msgs[-1]["author"], msgs[-1]["text"], sent[0].persona, sent[0].thread_id,
           sent[0].note), ("delivered", "system", "Loam updated from 1.0.0 to 1.2.0.",
                           meta["default"], threads.GENERAL_ID, "Read /x/notes.md and say it."))
    check("...General's default is the install's agent", meta["default"], "iris")
    check("...a notice naming an agent not in the thread is not delivered",
          server.threads.deliver_note({**note, "persona": "nobody"}), "not a member")
    up.notice_done(note, got)
    check("...and a delivered notice is gone", notices(), [])


asyncio.run(broker_checks())

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'FAILED' if FAILURES else 'OK'}: {FAILURES} failure(s)")
sys.exit(1 if FAILURES else 0)
