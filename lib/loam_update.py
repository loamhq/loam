"""`loam update`: move this install to a newer release, and back (first
release, L13; the owner's decision 3, 2026-11-14: "tap to update, owning only
Loam's parts and `System/Loam/`").

**What an update replaces.** Only what ships: the checkout (``paths.root``'s
repository, moved to a release tag) and ``<vault>/<system>/Loam/``, copied
from the library. Nothing else is written: no agent workspace, no other
part of the vault (``System/skills/`` included), no Yours plugin, nothing in
``/etc/loam/``, no state but this module's own folder, no provider home, no
``_shared/reach.md``. What the new code then regenerates (units, the agents'
provisioned files) is the generators' to write, as on any install.

**Two kinds of install**, told apart by the checkout itself:

- *stock*: HEAD holds no commit the running release's tag does not. The
  update moves the checkout to the tag (a detached checkout, or a
  fast-forward of the branch it is on).
- *development* (a developer's): HEAD has local commits. The update merges the tag
  into the branch, and the local commits stay.

**The clean-tree rule** (the plan's §2). The update refuses, and hands the
job to the person's agent, when (a) on a stock install
``git status --porcelain --ignored`` is not empty (state leaked into the
tree, or someone edited it), or (b) on a development install the merge with
the tag conflicts (found by ``git merge-tree`` before anything moves; a
development tree must also have no uncommitted change, since a merge would
mix it in). A refusal names what it found.

**System/Loam is replaced whole**, and first compared with the library of
the release that is running now: what differs there is somebody's edit. It
is shown (``check``, ``diff``, the route), an apply with edits waits for a
yes (``accept_library_edits``), and the whole folder is copied to
``<state>/update/backups/<run>/`` before it is replaced, so even an edit
said yes to can be read again.

**After the move**, from the new code, each only when what it reads
changed between the two commits: the user units (``gen-units``), every
live agent's provisioned files (``provision``), the provider CLIs at the
release's pin (``provider-pin apply``, lib/loam_pins.py), the providers' memory pins
(``provider-memory pin``), the broker (``restart-broker --when-idle``: it
waits until no session is working, then restarts and must answer again on a
new process) and the plugin gateway. "What it reads" is the script and the
transitive closure of the ``lib/`` modules it imports, read from the new
commit, plus the folders named in ``TRIGGERS``.

**A failed apply puts the previous release back**: the checkout at its old
commit, System/Loam from the backup, the same regenerations from the old
code, the broker restarted again if it had been. The record says which step
failed and that the old release is running. ``rollback`` does the same for
the last successful apply, in one command.

**Release notes go to the person's agent**, never to the person as a
changelog. A notice (``<state>/update/notices/``) is left for the broker,
which posts one plain line in General and gives the person's default agent
(General's default) a turn that names the notes to read and asks it to say
in its own words what the update means for them. Four kinds: a newer
release found (once per version), applied, refused, failed and rolled back.

The broker's routes (``/v1/update``) and the CLI (``bin/loam-update``)
both call this module; the page contract is ``docs/update.md``.

Apply and rollback run as their own process (a transient user unit), so
the broker restart they end with cannot cut them off. Everything they
import is imported at the top of this file: the checkout changes under a
running apply, and a module imported after the move would be the new one.
"""

from __future__ import annotations

import contextlib
import difflib
import http.client
import fcntl
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import loam_agents
import loam_broker as cfg
import loam_config as lc
import loam_lock
import loam_skills as sk

STATE = Path(os.environ.get("LOAM_UPDATE_STATE") or lc.STATE / "update")
LOG = lc.LOGS / "update.jsonl"
REMOTE = "origin"
TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
VERSION_FILE = "manifest.json"
# Release notes, in the release's own tree: `<version>.md`. With none, the
# annotated tag's message; with neither, the commit subjects between the two
# releases, as material for the agent and never shown to the person as is.
NOTES_DIR = "docs/releases"
KEEP_BACKUPS = 5
BROKER_UNIT = "loam-session-broker.service"
GATEWAY_UNIT = "loam-plugins-gateway.service"
# How long a restarted broker has to answer again on a new process.
BROKER_UP_S = 90
# A unified diff of the person's edits is cut at this many lines; the files
# are always all named.
DIFF_LINES = 400

# What each regeneration reads: a script whose import closure is followed,
# and folders (relative to the runtime) whose change alone is enough.
TRIGGERS = {
    "units": {"script": "bin/gen-units", "dirs": ["systemd/"],
              "skip": ["systemd/system/"]},
    "agents": {"script": "bin/provision", "dirs": ["engineer-template/", "engineer/install/"]},
    # The provider CLIs at the new release's pin (lib/loam_pins.py,
    # 2026-11-19): a moved pin installs its builds and moves the links, and
    # then the memory pins and the broker follow, so new sessions run them.
    "providers": {"script": "bin/provider-pin", "dirs": []},
    "provider-memory": {"script": "bin/provider-memory", "dirs": ["lib/loam_pins.py"]},
    "broker": {"script": "bin/session-broker",
               "dirs": ["broker/", "plugins/", "lib/loam_pins.py"]},
    "gateway": {"script": "bin/plugin-gateway", "dirs": []},
    # Loam's own app runs from a release of this checkout (2026-11-16): the
    # deployment layer is re-rendered (its unit template among it), then the
    # app is unpacked at the new commit and restarted, and put back on its
    # old release if the new one does not answer.
    "deploy": {"script": "bin/gen-deploy", "dirs": ["deploy/"]},
    "app": {"script": "bin/deploy", "dirs": ["app/", "deploy/"]},
    # The host history's timer (2026-11-19, lib/loam_host.py): root's, so
    # gen-units does not install it; `loam host install` does.
    "host": {"script": "bin/loam-host", "dirs": ["systemd/system/loam-host-history."]},
}
STEP_WORDS = {
    "preflight": "Checking the install",
    "backup": "Keeping a copy of System/Loam",
    "move": "Moving Loam to the new release",
    "verify": "Checking the release now in place",
    "back": "Putting the previous release's code back",
    "library-back": "Putting System/Loam back as it was before the update",
    "library": "Replacing System/Loam",
    "units": "Regenerating the units",
    "agents": "Re-provisioning the agents",
    "providers": "Putting the AI tools at the versions this release was tested with",
    "provider-memory": "Pinning the providers' memory off",
    "broker": "Restarting the broker when every session is idle",
    "gateway": "Restarting the plugin gateway",
    "deploy": "Regenerating the web front and the app's unit",
    "app": "Starting the app on the release now in place",
    "host": "Installing the host history's timer",
    "views": "Refreshing the agents' skill views",
    "undo-move": "Putting the previous release's code back",
    "undo-library": "Putting System/Loam back",
    "undo-views": "Refreshing the agents' skill views",
    "undo-units": "Regenerating the units from the previous release",
    "undo-agents": "Re-provisioning the agents from the previous release",
    "undo-providers": "Putting the AI tools back at the previous release's versions",
    "undo-provider-memory": "Pinning the providers' memory again",
    "undo-broker": "Restarting the broker on the previous release",
    "undo-gateway": "Restarting the plugin gateway on the previous release",
    "undo-deploy": "Regenerating the web front from the previous release",
    "undo-app": "Starting the app on the previous release",
    "undo-host": "Putting the host history's timer back as the previous release had it",
}


class Refusal(Exception):
    """The update will not run, for a reason the person's agent can act on.
    ``code`` is stable for the app; ``message`` is the sentence to show."""

    def __init__(self, code: str, message: str, **detail) -> None:
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.detail}


class BrokerDeferred(RuntimeError):
    """``restart-broker --when-idle`` deferred (exit 75, 2026-11-17): the box
    did not go idle within its ceiling, so the broker was not restarted and
    still runs the code it started with."""


class StepFailed(Exception):
    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step, self.message = step, message


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- git ------------------------------------------------------------------------

def runtime() -> Path:
    """The live runtime: where `bin/` and `lib/` are."""
    return lc.ROOT


def git(*args: str, check: bool = True, cwd: Path | None = None,
        text: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(["git", "-C", str(cwd or runtime()), *args],
                          capture_output=True, text=text, check=False,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0",
                               "LC_ALL": "C"})
    if check and done.returncode != 0:
        err = done.stderr if text else done.stderr.decode("utf-8", "replace")
        raise RuntimeError(f"git {' '.join(args)}: {err.strip() or done.returncode}")
    return done


def repo_top() -> Path:
    done = git("rev-parse", "--show-toplevel", check=False)
    if done.returncode != 0 and "dubious ownership" in done.stderr:
        # git refuses a repository another user owns (found 2026-11-17 by
        # the 0.2.0 walk: `loam update` as root read as "not a git
        # checkout"). bin/loam-update drops to the install's user first;
        # this says so plainly if anything still reaches here as another.
        raise Refusal("not-owner", f"{runtime()} belongs to the user "
                      f"{lc.USER}, and git will not read it as another user. "
                      f"Run `loam update` (it switches to {lc.USER} by itself "
                      f"when run as root), or `sudo -u {lc.USER} loam update`.")
    if done.returncode != 0:
        raise Refusal("no-git", f"{runtime()} is not a git checkout, so there is "
                                "nothing an update can move. Loam is updated by "
                                "moving its checkout to a release tag.")
    return Path(done.stdout.strip())


def prefix() -> str:
    """The runtime's folder inside the repository (`` `` here; ``runtime/``
    if the release lays the runtime out under one)."""
    return git("rev-parse", "--show-prefix").stdout.strip()


def library_prefix() -> str:
    """The library's path inside the repository (``docs/library/``)."""
    # Measured in the checkout loam_skills lives in, then read as a path
    # in the live one: the layout is the same in both.
    rel = os.path.relpath(sk.LIBRARY, Path(sk.__file__).resolve().parent.parent)
    return (prefix() + rel.replace(os.sep, "/")).rstrip("/") + "/"


def version_key(tag: str | None) -> tuple[int, int, int] | None:
    m = TAG_RE.match(tag or "")
    return tuple(int(x) for x in m.groups()) if m else None


def bare(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def show(rev: str, path: str) -> str | None:
    done = git("show", f"{rev}:{path}", check=False)
    return done.stdout if done.returncode == 0 else None


def version_at(rev: str) -> str | None:
    text = show(rev, VERSION_FILE)
    try:
        value = json.loads(text).get("version") if text else None
    except ValueError:
        return None
    return value if isinstance(value, str) else None


def running_version() -> str | None:
    """The version the running checkout says it is: its manifest's."""
    try:
        value = json.loads((repo_top() / VERSION_FILE).read_text()).get("version")
    except (OSError, ValueError):
        return None
    return value if isinstance(value, str) else None


def head() -> str:
    return git("rev-parse", "HEAD").stdout.strip()


def branch() -> str | None:
    done = git("symbolic-ref", "-q", "--short", "HEAD", check=False)
    return done.stdout.strip() or None


def remote_tags() -> dict[str, str]:
    """Every release tag where releases come from, newest first:
    ``{tag: commit}``. Reads only (``ls-remote``)."""
    done = git("ls-remote", "--tags", REMOTE, check=False)
    if done.returncode != 0:
        raise Refusal("unreachable", "Loam could not reach the place its releases "
                                     f"come from ({REMOTE}): "
                                     + (done.stderr.strip().splitlines() or ["no answer"])[-1])
    tags: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in done.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            peeled[name[:-3]] = sha
        elif version_key(name):
            tags[name] = sha
    tags = {t: peeled.get(t, s) for t, s in tags.items()}
    return dict(sorted(tags.items(), key=lambda kv: version_key(kv[0]), reverse=True))


def fetch_tag(tag: str) -> None:
    """The tag's objects and its ref, into the checkout's `.git` only (the
    working tree is untouched)."""
    git("fetch", "--quiet", "--no-tags", REMOTE,
        f"+refs/tags/{tag}:refs/tags/{tag}")


def tag_of(version: str | None) -> str | None:
    """The local tag naming ``version`` (``1.2.0`` or ``v1.2.0``)."""
    if not version:
        return None
    for name in (version, f"v{version}"):
        if git("rev-parse", "-q", "--verify", f"refs/tags/{name}^{{commit}}",
               check=False).returncode == 0:
            return name
    return None


def local_commits(current: str | None) -> list[str]:
    """Commits HEAD holds that the running release does not: none on a
    stock install. Measured from the running version's tag when the
    checkout has it, else from every tag it has."""
    base = tag_of(current)
    spec = [f"refs/tags/{base}..HEAD"] if base else ["HEAD", "--not", "--tags"]
    out = git("rev-list", "--no-merges", "--format=%h %s", *spec).stdout
    return [ln for ln in out.splitlines() if not ln.startswith("commit ")]


def dirt(kind: str) -> list[str]:
    """What `git status` says is in the tree: with ignored files on a stock
    install (rule a), without them on a development one. The engineer's
    rendered files (``lc.ENGINEER_HARNESS``) are not dirt: provision writes
    them on every install, no release carries them, and an update never
    touches them (2026-11-18)."""
    args = ["status", "--porcelain", "--untracked-files=all"]
    if kind == "stock":
        args.append("--ignored")
    harness = tuple(prefix() + name for name in lc.ENGINEER_HARNESS)

    def ours(line: str) -> bool:
        path = line[3:].strip().strip('"')
        return untracked(line) and any(
            path == h or path.startswith(h + "/") for h in harness)
    return [ln for ln in git(*args).stdout.splitlines() if ln.strip() and not ours(ln)]


def untracked(line: str) -> bool:
    return line[:2] in ("??", "!!")


def merge_conflicts(tag: str) -> list[str]:
    """The files a merge of ``tag`` into HEAD would conflict in; none when
    it merges clean. Nothing in the tree or the index moves."""
    done = git("merge-tree", "--write-tree", "--name-only", "--no-messages",
               "HEAD", f"refs/tags/{tag}", check=False)
    if done.returncode == 0:
        return []
    if done.returncode == 1:
        return [ln for ln in done.stdout.splitlines()[1:] if ln.strip()]
    raise RuntimeError(f"git merge-tree: {done.stderr.strip()}")


def changed_files(old: str, new: str) -> list[str]:
    return [ln for ln in git("diff", "--name-only", old, new).stdout.splitlines() if ln]


def import_closure(rev: str, script: str) -> set[str]:
    """``script`` and every ``lib/loam_*.py`` it imports, transitively, as
    they are at ``rev`` (paths relative to the repository)."""
    pre = prefix()
    seen: set[str] = set()
    todo = [pre + script]
    pattern = re.compile(r"^\s*(?:from|import)\s+(loam_[a-z0-9_]+)", re.M)
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        text = show(rev, path)
        if text is None:
            continue
        seen.add(path)
        for mod in pattern.findall(text):
            todo.append(f"{pre}lib/{mod}.py")
    return seen


def needs(step: str, old: str, new: str, changed: list[str]) -> bool:
    spec = TRIGGERS[step]
    pre = prefix()
    reads = import_closure(new, spec["script"]) | import_closure(old, spec["script"])
    if not reads and not spec["dirs"]:
        return False
    for path in changed:
        if any(path.startswith(pre + s) for s in spec.get("skip", [])):
            continue
        if path in reads or any(path.startswith(pre + d) for d in spec["dirs"]):
            return True
    return False


# -- the library ------------------------------------------------------------------

def library_at(rev: str, into: Path) -> Path:
    """The library as it is at ``rev``, unpacked under ``into``."""
    lib = library_prefix()
    done = git("archive", "--format=tar", rev, lib, check=False, text=False)
    into.mkdir(parents=True, exist_ok=True)
    if done.returncode == 0:
        with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
            # Python 3.11.2 (Debian 12) has no extraction filter; the
            # archive is git's of this repository, and only plain files and
            # folders under the library's own path are taken from it.
            members = [m for m in tar.getmembers()
                       if (m.isfile() or m.isdir()) and m.name.startswith(lib)
                       and ".." not in Path(m.name).parts]
            tar.extractall(into, members=members)
    return into / lib


def _read(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return ["(not text)\n"]


def library_edits(rev: str = "HEAD") -> dict:
    """How ``<system>/Loam/`` differs from the library of the release at
    ``rev`` (the one running): somebody's edits. ``edited``, ``added`` (in
    the vault only), ``deleted`` (in the library only), and ``diff``, a
    unified diff of them, cut at DIFF_LINES."""
    dest = sk.loam_dir()
    with tempfile.TemporaryDirectory(prefix="loam-update-lib-") as tmp:
        lib = library_at(rev, Path(tmp))
        if not dest.is_dir():
            return {"folder": str(dest), "edited": [], "added": [], "deleted": [],
                    "diff": "", "any": False, "installed": False}
        d = sk.library_diff(source=lib, dest=dest)
        lines: list[str] = []
        for name in d["changed"]:
            lines += difflib.unified_diff(_read(lib / name), _read(dest / name),
                                          f"shipped/{name}", f"yours/{name}")
        for name in d["removed"]:
            lines += difflib.unified_diff([], _read(dest / name), "/dev/null",
                                          f"yours/{name}")
        for name in d["added"]:
            lines += [f"deleted: {name}\n"]
    cut = len(lines) > DIFF_LINES
    text = "".join(lines[:DIFF_LINES]) + (
        f"\n… {len(lines) - DIFF_LINES} more lines\n" if cut else "")
    return {"folder": str(dest), "edited": d["changed"], "added": d["removed"],
            "deleted": d["added"], "diff": text,
            "any": any(d.values()), "installed": True}


def library_changes(old: str, new: str) -> dict:
    """What the release itself changes in System/Loam: names only."""
    with tempfile.TemporaryDirectory(prefix="loam-update-lib-") as tmp:
        a = library_at(old, Path(tmp) / "old")
        b = library_at(new, Path(tmp) / "new")
        d = sk.library_diff(source=b, dest=a)
    return {"added": d["added"], "removed": d["removed"], "changed": d["changed"]}


# -- this module's own state ----------------------------------------------------------

def _path(name: str) -> Path:
    return STATE / name


def read_json(name: str) -> dict | None:
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(name: str, value: dict) -> None:
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def log(entry: dict) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": now_iso(), **entry}, ensure_ascii=False) + "\n")
    except OSError:
        pass


@contextlib.contextmanager
def run_lock():
    """One apply or rollback at a time. Raises Refusal if one is running."""
    STATE.mkdir(parents=True, exist_ok=True)
    fh = open(_path("run.lock"), "a+")  # noqa: SIM115
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise Refusal("busy", "An update is already running; its progress is "
                              "in Settings › About Loam.") from None
    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def running() -> bool:
    STATE.mkdir(parents=True, exist_ok=True)
    with open(_path("run.lock"), "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
    return False


# -- notices for the person's agent ------------------------------------------------------

def notes_for(tag: str, old: str | None) -> str:
    """The release's notes: its notes file, its tag message, or the commit
    subjects since ``old``."""
    text = show(f"refs/tags/{tag}", f"{prefix()}{NOTES_DIR}/{bare(tag)}.md") or \
        show(f"refs/tags/{tag}", f"{NOTES_DIR}/{bare(tag)}.md")
    if text:
        return text.strip()
    kind = git("cat-file", "-t", f"refs/tags/{tag}", check=False).stdout.strip()
    if kind == "tag":
        body = git("for-each-ref", f"refs/tags/{tag}",
                   "--format=%(contents)").stdout.strip()
        body = re.sub(r"-----BEGIN [A-Z ]*SIGNATURE-----.*", "", body, flags=re.S).strip()
        if body:
            return body
    spec = f"{old}..refs/tags/{tag}" if old else f"refs/tags/{tag}"
    subjects = git("log", "--no-merges", "--format=- %s", "-n", "200", spec,
                   check=False).stdout.strip()
    return ("This release has no notes; these are its commits, as material "
            "for you, not for pasting:\n\n" + subjects) if subjects else ""


def save_notes(tag: str, old: str | None) -> Path:
    path = _path(f"notes/{bare(tag)}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(notes_for(tag, old) + "\n", encoding="utf-8")
    return path


def notice(kind: str, line: str, note: str, key: str | None = None) -> dict | None:
    """Leave a notice for the broker to deliver: ``line`` posted in General,
    ``note`` the default agent's turn input. ``key`` makes it once only."""
    sent = read_json("told.json") or {}
    if key and key in sent:
        return None
    rec = {"id": f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}",
           "kind": kind, "line": line, "note": note, "at": now_iso(),
           "thread": None, "persona": None}
    # "Told" is written first (2026-11-17): if it cannot be, no notice is
    # left, rather than a new one on every check that cannot record it.
    if key:
        sent[key] = rec["at"]
        write_json("told.json", sent)
    write_json(f"notices/{rec['id']}.json", rec)
    log({"kind": "notice", "notice": kind, "id": rec["id"], "line": line})
    return rec


def owed_notices() -> list[dict]:
    folder = _path("notices")
    out = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            out.append({**json.loads(path.read_text(encoding="utf-8")),
                        "_path": str(path)})
        except (OSError, ValueError):
            continue
    return out


# Notices whose file could not be removed, said once each (2026-11-17).
_STUCK: set[str] = set()


def notice_done(rec: dict, outcome: str) -> bool:
    """A notice dealt with: logged and removed. Until 2026-11-17 a failed
    removal was swallowed, and a notice a root run had left (root-owned,
    so the broker could not unlink it) was posted to General and given a
    turn every pass, without end. Now a notice that cannot be removed is
    said once, loudly, with the fix (the thread engine will not deliver
    the same notice twice either way). False when it is still there."""
    path = Path(rec["_path"])
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        nid = str(rec.get("id"))
        if nid not in _STUCK:
            _STUCK.add(nid)
            why = (f"update notice {nid} could not be removed ({exc}); it will "
                   f"not be delivered again. Fix the owner: "
                   f"chown -R {lc.USER}: {STATE}")
            log({"kind": "notice-stuck", "id": nid, "outcome": outcome,
                 "error": str(exc), "message": why})
            print(f"loam_update: {why}", file=sys.stderr, flush=True)
        return False
    if rec.get("id") not in _STUCK:
        log({"kind": "notice-delivered", "id": rec.get("id"), "outcome": outcome})
    return True


def as_install_user(argv: list[str], euid: int | None = None) -> list[str] | None:
    """What to exec so a root shell's `loam update` runs as the install's
    user (2026-11-17, the 0.2.0 walk): the checkout and the update state
    are that user's, git will not read the checkout as root, and a root
    run used to leave root-owned files that broke every later run. None
    when this process already is that user (or the install's user is
    root). ``argv`` is the command's own arguments."""
    euid = os.geteuid() if euid is None else euid
    if euid != 0 or lc.UID == 0:
        return None
    runtime_dir = f"/run/user/{lc.UID}"
    env = {"HOME": str(lc.HOME), "USER": lc.USER, "LOGNAME": lc.USER,
           "PATH": "/usr/local/bin:/usr/bin:/bin",
           "LANG": os.environ.get("LANG", "C.UTF-8"),
           "XDG_RUNTIME_DIR": runtime_dir,
           "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime_dir}/bus",
           "PYTHONPYCACHEPREFIX": str(lc.HOME / ".cache" / "loam" / "pycache")}
    env.update({k: v for k, v in os.environ.items() if k.startswith("LOAM_")})
    return ["runuser", "-u", lc.USER, "--", "env", "-i",
            *(f"{k}={v}" for k, v in env.items()),
            sys.executable, str(runtime() / "bin" / "loam-update"), *argv]


def repair_owner(euid: int | None = None) -> list[str]:
    """As root: give back to the install's user anything in the update
    state (and its log) that a root run left owned by someone else.
    Answers the paths changed; nothing as any other user."""
    euid = os.geteuid() if euid is None else euid
    if euid != 0:
        return []
    fixed = []
    roots = [STATE, LOG] if STATE.exists() else [LOG]
    for top in roots:
        if not top.exists():
            continue
        paths = [top] + (sorted(top.rglob("*")) if top.is_dir() else [])
        for path in paths:
            try:
                st = path.lstat()
                if st.st_uid != lc.UID:
                    os.lchown(path, lc.UID, lc.GID)
                    fixed.append(str(path))
            except OSError:
                continue
    return fixed


def _owner() -> str:
    return lc.OWNER_NAME


def tell_available(tag: str, current: str | None) -> None:
    path = save_notes(tag, tag_of(current))
    notice("available",
           f"Loam {bare(tag)} is available (this install runs {current}).",
           f"Loam {bare(tag)} is out; this install runs {current}. Its notes are "
           f"in {path}. Read them and tell {_owner()}, briefly and in your own "
           f"words, what this release would change for them, and that they can "
           f"update from Settings › About Loam when they like. Do not paste the notes "
           f"or list commits.",
           key=f"available:{bare(tag)}")


# What the agent is asked to offer when the refusal is the owner's own change
# to Loam's parts (2026-11-17): it is never lost, and never silently merged
# into something that may not run.
OWN_CHANGE = {
    "conflict": (
        "\n\nThese are this install's own changes to Loam (`git -C {root} log "
        "{base}..HEAD`), and the release changes the same lines. Nothing moved, "
        "and nothing of theirs is lost. Offer {owner} the choices: (1) send the "
        "change to Loam's makers (`{root}/bin/loam report bug --patch`, skill "
        "loam-filing-a-bug-or-feature), and read the release's notes first, "
        "since it may already fix the same thing; (2) with their yes, brief the "
        "engineer to revert the conflicting local commits (`git revert`, so the "
        "history keeps them), update, and make the change again on the new "
        "release, as a plugin in their plugins folder if a plugin can do it "
        "(skill loam-building-a-plugin), because a plugin survives every update."),
    "uncommitted": (
        "\n\nAn edit to Loam's own files that is not committed is a change "
        "nobody has decided to keep. Ask {owner} whether it is wanted; if it is, "
        "brief the engineer to commit it (or to make it a plugin instead); if "
        "not, it can be put back with their yes."),
}


def tell_refused(tag: str | None, refusal: Refusal) -> None:
    tag = tag or refusal.detail.get("to")
    files = refusal.detail.get("files") or []
    listing = "".join(f"\n  {f}" for f in files[:40])
    if refusal.code in OWN_CHANGE:
        listing += OWN_CHANGE[refusal.code].format(
            root=runtime(), owner=_owner(),
            base=tag_of(running_version()) or "the release tag")
    notice("refused",
           f"Loam's update{f' to {bare(tag)}' if tag else ''} did not run: "
           f"{refusal.message}",
           f"Loam's update{f' to {bare(tag)}' if tag else ''} was refused, and "
           f"the job is yours: {refusal.message}{listing}\n\nThe checkout is "
           f"{runtime()}. Find out what this is (state that leaked into the "
           f"tree, an edit, a conflicting local change), explain it to "
           f"{_owner()} in plain words, and with their yes put it right. Then "
           f"`{runtime()}/bin/loam-update apply` (or Settings › About Loam) runs "
           f"the update again. Never discard a change of theirs without asking.",
           key=f"refused:{tag}:{refusal.code}:{len(files)}")


def tell_applied(rec: dict) -> None:
    notes = save_notes(rec["to_tag"], rec.get("from_tag"))
    edits = rec.get("library_edits") or {}
    kept = (f" System/Loam had edits; they were replaced, and the copy is "
            f"kept at {rec['backup']}." if edits.get("any") else "")
    warn = "".join(f" Also: {w}." for w in rec.get("warnings") or [])
    notice("applied",
           f"Loam updated from {rec.get('from_version')} to {rec['to_version']}.",
           f"Loam was updated from {rec.get('from_version')} to "
           f"{rec['to_version']}. Its notes are in {notes}. Tell {_owner()}, "
           f"briefly and in your own words, what is new for them; do not paste "
           f"the notes or list commits.{kept} If something is wrong with it, "
           f"`{runtime()}/bin/loam-update rollback` puts "
           f"{rec.get('from_version')} back.{warn}")


def tell_failed(rec: dict, why: str) -> None:
    notice("failed",
           f"Loam's update to {rec['to_version']} failed at "
           f"“{STEP_WORDS.get(rec.get('failed_step'), rec.get('failed_step'))}” "
           f"and was undone; {rec.get('from_version')} is running.",
           f"Loam's update from {rec.get('from_version')} to {rec['to_version']} "
           f"failed at the step `{rec.get('failed_step')}`: {why}\n\nIt was "
           f"undone and {rec.get('from_version')} is running"
           f"{'' if rec.get('rolled_back') else ' — except that the undo itself did not finish; see the record'}"
           f". The record is {_path('apply.json')} and the log {LOG}. Tell "
           f"{_owner()} plainly what happened; nothing of theirs was changed.")


# -- check ------------------------------------------------------------------------------

def current() -> dict:
    version = running_version()
    commits = local_commits(version)
    return {"version": version, "commit": head(), "branch": branch(),
            "kind": "development" if commits else "stock",
            "local_commits": len(commits)}


def blockers(kind: str, tag: str) -> Refusal | None:
    """The clean-tree rule for moving to ``tag`` (fetched), or None."""
    found = dirt(kind)
    if found:
        if kind == "stock":
            return Refusal("dirty", "Loam's own folder has files in it that no "
                           "release put there, so an update could overwrite or "
                           "mix with them.", files=found)
        return Refusal("uncommitted", "This development checkout has changes "
                       "that are not committed; a merge would mix them in.",
                       files=found)
    if kind == "development":
        conflicts = merge_conflicts(tag)
        if conflicts:
            return Refusal("conflict", f"This checkout's own commits and release "
                           f"{bare(tag)} change the same lines, so they cannot "
                           f"be merged without a person deciding.", files=conflicts)
    return None


def check(tell: bool = True) -> dict:
    """Is there a newer release, and could it be applied now? Writes
    ``check.json`` and answers ``status()``. ``tell``: leave a notice for
    the person's agent the first time a version is seen."""
    started = now_iso()
    out: dict = {"checked_at": started, "latest": None, "available": False,
                 "blocked": None, "error": None, "library": None,
                 "changes": None}
    try:
        cur = current()
        out["current"] = cur
        tags = remote_tags()
        if not tags:
            out["error"] = {"code": "no-release",
                            "message": "No release has been published yet."}
        else:
            tag = next(iter(tags))
            out["latest"] = {"version": bare(tag), "tag": tag, "commit": tags[tag]}
            newer = (version_key(tag) > version_key(cur["version"])
                     if version_key(cur["version"]) else False)
            out["available"] = bool(newer)
            if cur["version"] is None:
                out["error"] = {"code": "no-version", "message": (
                    f"This checkout has no {VERSION_FILE} with a version, so it "
                    f"is not a release install and is not updated this way.")}
            elif newer:
                fetch_tag(tag)
                block = blockers(cur["kind"], tag)
                out["blocked"] = block.as_dict() if block else None
                out["changes"] = library_changes("HEAD", f"refs/tags/{tag}")
                if tell:
                    tell_available(tag, cur["version"])
        out["library"] = library_edits("HEAD")
    except Refusal as r:
        out["error"] = r.as_dict()
    except (RuntimeError, OSError) as exc:
        out["error"] = {"code": "error", "message": str(exc)}
    write_json("check.json", out)
    log({"kind": "check", "latest": (out["latest"] or {}).get("version"),
         "available": out["available"], "error": (out["error"] or {}).get("code")})
    return status()


def status() -> dict:
    """What the page reads: the running release, the last check, the last or
    current apply, and whether a rollback is possible."""
    try:
        cur = current()
    except (Refusal, RuntimeError) as exc:
        cur = {"version": None, "error": getattr(exc, "message", str(exc))}
    last, pending = read_json("last.json"), read_json("pending.json")
    rollback = None
    if pending and not running() and cur.get("commit") in (
            pending.get("from_commit"), pending.get("to_commit")):
        rollback = {"to_version": pending.get("from_version"),
                    "from_version": pending.get("to_version"), "at": pending.get("at"),
                    "interrupted": True}
    elif last and cur.get("commit") == last.get("to_commit"):
        rollback = {"to_version": last.get("from_version"),
                    "from_version": last.get("to_version"), "at": last.get("at")}
    run, busy = read_json("apply.json"), running()
    if run and run.get("outcome") in ("starting", "running") and not busy:
        age = _age_s(run.get("started_at"))
        if run["outcome"] == "running" or age is None or age > STARTING_GRACE_S:
            run = {**run, "outcome": "interrupted", "message": (
                f"The {run.get('action', 'update')} stopped without finishing"
                + (f", during “{STEP_WORDS.get(run.get('phase'), run.get('phase'))}”"
                   if run.get("phase") else "")
                + f". `{runtime()}/bin/loam-update rollback` puts "
                + f"{run.get('from_version') or 'the previous release'} back.")}
    chk = read_json("check.json")
    if chk:
        latest = (chk.get("latest") or {}).get("version")
        newer = (version_key(latest) or (0,)) > (version_key(cur.get("version")) or (0,)) \
            if version_key(cur.get("version")) else False
        chk = {**chk, "available": bool(newer)}
        if not newer:
            chk.update(blocked=None, changes=None)
    return {"current": cur, "check": chk,
            "run": run, "running": busy, "rollback": rollback}


# A run that was started is given this long to take its lock before a
# record still saying `starting` is reported as stopped.
STARTING_GRACE_S = 60


def _age_s(stamp: str | None) -> float | None:
    try:
        then = datetime.strptime(stamp or "", "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - then).total_seconds()


# -- apply ------------------------------------------------------------------------------

def preflight(target: str | None = None, accept_library_edits: bool = False) -> dict:
    """Everything that must hold before anything moves. Returns the plan;
    raises Refusal. Fetches the tag (the `.git` only)."""
    cur = current()
    if cur["version"] is None:
        raise Refusal("no-version", f"This checkout has no {VERSION_FILE} with a "
                      f"version, so it is not a release install and is not "
                      f"updated this way.")
    if version_key(cur["version"]) is None:
        raise Refusal("no-version", f"This checkout's {VERSION_FILE} says version "
                      f"{cur['version']!r}, which is not a release number.")
    tags = remote_tags()
    if not tags:
        raise Refusal("no-release", "No release has been published yet.")
    if target:
        tag = next((t for t in tags if bare(t) == bare(target)), None)
        if not tag:
            raise Refusal("no-such-release", f"There is no release {bare(target)}.")
    else:
        tag = next(iter(tags))
    if version_key(tag) <= version_key(cur["version"]):
        raise Refusal("not-newer", f"Loam is already at {cur['version']}"
                      + ("" if bare(tag) == cur["version"]
                         else f", newer than {bare(tag)}") + ".")
    fetch_tag(tag)
    said = version_at(f"refs/tags/{tag}")
    if said != bare(tag):
        raise Refusal("bad-release", f"Release {tag} says it is version {said!r} "
                      f"in its {VERSION_FILE}, so it is not a release this can "
                      f"apply.")
    block = blockers(cur["kind"], tag)
    if block:
        block.detail["to"] = bare(tag)
        raise block
    edits = library_edits("HEAD")
    if edits["any"] and not accept_library_edits:
        raise Refusal("library-edits", "System/Loam has edits that this update "
                      "would replace. Look at them first; a copy is kept when "
                      "you go ahead.", library=edits)
    return {"current": cur, "tag": tag, "version": bare(tag),
            "commit": git("rev-parse", f"refs/tags/{tag}^{{commit}}").stdout.strip(),
            "library_edits": edits}


class Box:
    """What an apply does to the box beyond git and System/Loam. Each runs
    the checkout's own scripts as it is at that moment, so the new code
    regenerates after the move and the old code after the undo. A test
    replaces it."""

    def __init__(self) -> None:
        # Bytecode goes to the install's cache, never into the checkout.
        self.env = {"PYTHONPYCACHEPREFIX": str(lc.PYCACHE), **os.environ}
        # Told each line a long step prints while it runs (the broker's
        # wait says what holds it), so the page can show it.
        self.on_line = lambda text: None

    def _run(self, *argv: str, timeout: float | None = 3600) -> str:
        done = subprocess.run([sys.executable, *argv], capture_output=True,
                              text=True, check=False, timeout=timeout, env=self.env)
        if done.returncode != 0:
            raise RuntimeError((done.stderr or done.stdout).strip()[-1500:]
                               or f"exit {done.returncode}")
        return done.stdout.strip()[-1500:]

    def bin(self, name: str) -> str:
        return str(runtime() / "bin" / name)

    def library(self) -> str:
        return self._run(self.bin("library"), "install")

    def units(self) -> str:
        said = self._run(self.bin("gen-units"))
        # The routine local commit is new (2026-11-18, lib/loam_autocommit.py):
        # gen-units never arms a timer, and an install made before it has its
        # repositories and no routine, so the update arms it, once. A box
        # whose crontab commits the trees by hand is left as it is.
        armed = self._run("-c", (
            "import sys; sys.path.insert(0, sys.argv[1]); import loam_units as u, "
            "loam_autocommit as a\n"
            "t = 'loam-autocommit.timer'\n"
            "if t in u.rendered() and not any(a.hand_routine(x) for x in a.ORDER):\n"
            "    print(t, u.arm_timer(t))\n"), str(runtime() / "lib"))
        return "\n".join(x for x in (said, armed) if x)

    def agents(self) -> str:
        said = []
        for name in loam_agents.live(loam_agents.every()):
            wake = loam_agents.card(name).get("wake_schedule")
            if not isinstance(wake, str):
                continue
            self._run(self.bin("provision"), name, wake)
            said.append(name)
        # The engineer too (2026-11-18): its role and project file come from
        # the vault, seeded from this release's and never over the owner's
        # own (bin/provision, ENGINEER_SEEDS).
        if (runtime() / "engineer" / "install" / "role.md").is_file():
            self._run(self.bin("provision"), "--engineer")
            said.append("the engineer")
        return f"provisioned {', '.join(said) or 'no agent'}"

    def providers(self) -> str:
        """Every CLI at the pin of the code now in place (the new release's
        after a move, the old one's on an undo). The broker step after it
        restarts what still runs the old build. A release from before the
        pin (0.2.11 and older, put back by an undo) has no `provider-pin`:
        the CLIs are left where they are."""
        if not Path(self.bin("provider-pin")).is_file():
            return "this release pins no CLI versions; left as they are"
        return self._run(self.bin("provider-pin"), "apply", "--no-restart")

    def provider_memory(self) -> str:
        return self._run(self.bin("provider-memory"), "pin")

    def broker(self) -> str:
        seam = os.environ.get("LOAM_UPDATE_RESTART")
        if seam:
            done = subprocess.run(seam, shell=True, capture_output=True, text=True,
                                  check=False)
            if done.returncode:
                raise RuntimeError(done.stderr.strip() or f"exit {done.returncode}")
            return done.stdout.strip() or "restarted (seam)"
        before = _main_pid(BROKER_UNIT)
        proc = subprocess.Popen([sys.executable, self.bin("restart-broker"), "--when-idle"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, env=self.env)
        said = []
        for line in proc.stdout:
            line = line.strip()
            if line:
                said.append(line)
                self.on_line(line)
        if proc.wait() == 75:
            raise BrokerDeferred("; ".join(said[-3:]) or "deferred: not restarted")
        if proc.returncode != 0:
            raise RuntimeError("; ".join(said[-3:]) or f"exit {proc.returncode}")
        deadline = time.monotonic() + BROKER_UP_S
        while time.monotonic() < deadline:
            pid = _main_pid(BROKER_UNIT)
            if pid and pid != before and _broker_up():
                return f"the broker answers again (pid {pid})"
            time.sleep(2)
        raise RuntimeError(f"the broker did not answer within {BROKER_UP_S} s "
                           f"of its restart")

    def gateway(self) -> str:
        done = subprocess.run(["systemctl", "--user", "try-restart", GATEWAY_UNIT],
                              capture_output=True, text=True, check=False)
        if done.returncode:
            raise RuntimeError(done.stderr.strip() or f"exit {done.returncode}")
        return "restarted"

    def _root(self, *argv: str, timeout: float | None = 3600) -> str:
        """A step that is root's (the deployment layer, the app's unit), by
        sudo, which the install's user holds."""
        done = subprocess.run(["sudo", "-n", "env", f"PYTHONPYCACHEPREFIX={ROOT_PYCACHE}",
                               sys.executable, *argv], capture_output=True, text=True,
                              check=False, timeout=timeout)
        if done.returncode != 0:
            raise RuntimeError((done.stderr or done.stdout).strip()[-1500:]
                               or f"exit {done.returncode}")
        return done.stdout.strip()[-1500:]

    def deploy(self) -> str:
        self._root(self.bin("gen-deploy"))
        return "rendered"

    def app(self) -> str:
        """Every app that runs from this checkout, on what is checked out
        now: the new release after a move, the old one on an undo."""
        names = self._run("-c", OWN_APPS.format(lib=str(runtime() / "lib"))).split()
        commit = head()
        said = []
        for name in names:
            self._root(self.bin("deploy"), name, "up", commit)
            said.append(f"{name} on {commit[:7]}")
        return ", ".join(said) or "no app runs from this checkout"

    def host(self) -> str:
        """The host history's timer, as the release now in place has it; a
        release from before it (an undo) has none, so the timer is stopped
        rather than left firing a script that is not there."""
        if (runtime() / "bin" / "loam-host").is_file():
            return self._root(self.bin("loam-host"), "install")
        done = subprocess.run(["sudo", "-n", "systemctl", "disable", "--now",
                               "loam-host-history.timer"], capture_output=True, text=True,
                              check=False)
        return "the host history's timer is off: this release has none" + (
            "" if done.returncode == 0 else f" ({done.stderr.strip()[-200:]})")


# Root's Python bytecode, outside the checkout (bin/loam-install's too).
ROOT_PYCACHE = "/var/cache/loam/pycache"

# The apps that run from this checkout, read by the code now in place.
OWN_APPS = """
import sys
sys.path.insert(0, {lib!r})
import loam_config as lc, loam_deploy as d
root = lc.ROOT.resolve()
print(" ".join(a.name for a in d.records()
               if a.service and a.checkout and a.checkout.resolve() == root))
"""


def _main_pid(unit: str) -> int:
    done = subprocess.run(["systemctl", "--user", "show", "-p", "MainPID",
                           "--value", unit], capture_output=True, text=True, check=False)
    try:
        return int(done.stdout.strip() or 0)
    except ValueError:
        return 0


def _broker_up() -> bool:
    try:
        conn = http.client.HTTPConnection(cfg.BIND_ADDR, cfg.PORT, timeout=5)
        conn.request("GET", "/up")
        return conn.getresponse().status == 200
    except OSError:
        return False


class Run:
    """One apply or rollback's record, written to ``apply.json`` at each
    step so the page can follow it."""

    def __init__(self, action: str, rec: dict) -> None:
        self.rec = {"action": action, "outcome": "running", "phase": None,
                    "started_at": now_iso(), "ended_at": None,
                    "pid": os.getpid(), "steps": [], "message": None, **rec}
        self.rec["id"] = rec.get("id") or (
            f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}")
        self.save()

    def save(self) -> None:
        write_json("apply.json", self.rec)

    def step(self, name: str, fn, *args):
        entry = {"name": name, "words": STEP_WORDS.get(name, name),
                 "status": "running", "started_at": now_iso(), "detail": None}
        self.rec["steps"].append(entry)
        self.rec["phase"] = name
        self.save()
        try:
            detail = fn(*args)
        except Refusal:
            entry.update(status="refused", ended_at=now_iso())
            self.save()
            raise
        except Exception as exc:  # noqa: BLE001 -- every failure is the record's
            entry.update(status="failed", ended_at=now_iso(), detail=str(exc)[-1500:],
                         **({"deferred": True} if isinstance(exc, BrokerDeferred) else {}))
            self.save()
            raise StepFailed(name, str(exc)) from exc
        entry.update(status="done", ended_at=now_iso(),
                     detail=detail if isinstance(detail, str) else None)
        self.save()
        return detail

    def note(self, text: str) -> None:
        """A line from the running step, as its detail until it ends."""
        if self.rec["steps"] and self.rec["steps"][-1]["status"] == "running":
            self.rec["steps"][-1]["detail"] = text[-500:]
            self.save()

    def skip(self, name: str, why: str) -> None:
        self.rec["steps"].append({"name": name, "words": STEP_WORDS.get(name, name),
                                  "status": "skipped", "detail": why,
                                  "started_at": now_iso(), "ended_at": now_iso()})
        self.save()

    def end(self, outcome: str, message: str) -> dict:
        self.rec.update(outcome=outcome, message=message, ended_at=now_iso(),
                        phase=None)
        self.save()
        log({"kind": self.rec["action"], "id": self.rec["id"], "outcome": outcome,
             "from": self.rec.get("from_version"), "to": self.rec.get("to_version"),
             "message": message})
        return self.rec


def backup_library(run_id: str) -> str | None:
    src = sk.loam_dir()
    if not src.is_dir():
        return None
    dst = _path(f"backups/{run_id}/{sk.LOAM_DIR}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Not the kept folder: it is never restored from a backup (restore_library).
    shutil.copytree(src, dst, symlinks=True,
                    ignore=lambda d, names: [sk.KEPT_DIR] if Path(d) == src else [])
    folders = sorted(p for p in _path("backups").iterdir() if p.is_dir())
    for old in folders[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)
    return str(dst)


def restore_library(backup: str | None) -> str:
    """System/Loam exactly as the backup has it (or gone, if there was none)."""
    dst = sk.loam_dir()
    tmp = dst.with_name(f".{sk.LOAM_DIR}.restore-{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    if backup and Path(backup).is_dir():
        shutil.copytree(backup, tmp, symlinks=True)
    old = dst.with_name(f".{sk.LOAM_DIR}.undone-{os.getpid()}")
    if dst.exists():
        os.replace(dst, old)
    # The kept folder is the person's data as it is now, never the backup's
    # older copy (lib/loam_skills.py, KEPT_DIR).
    if tmp.exists() or (old / sk.KEPT_DIR).is_dir():
        tmp.mkdir(parents=True, exist_ok=True)
        sk.carry_kept(old, tmp)
    if tmp.exists():
        os.replace(tmp, dst)
    shutil.rmtree(old, ignore_errors=True)
    return f"restored from {backup}" if backup else "removed (there was none before)"


def refresh_views() -> str:
    changed = sk.refresh()
    return f"{len(changed)} skill view(s) changed" if changed else "views current"


def move_to(tag: str, kind: str) -> str:
    with loam_lock.live("Loam", "update"):
        if kind == "development":
            who = [] if git("config", "user.email", check=False).stdout.strip() else [
                "-c", f"user.name={lc.AUTOCOMMIT_NAME}",
                "-c", f"user.email={lc.AUTOCOMMIT_EMAIL}"]
            done = git(*who, "merge", "--no-edit", "-m", f"Merge Loam release {bare(tag)}",
                       f"refs/tags/{tag}", check=False)
            if done.returncode:
                git("merge", "--abort", check=False)
                raise Refusal("conflict", f"Merging release {bare(tag)} stopped: "
                              + (done.stdout + done.stderr).strip()[-500:])
            return f"merged {tag} into {branch() or 'HEAD'}"
        if branch():
            git("merge", "--ff-only", "--quiet", f"refs/tags/{tag}")
            return f"{branch()} fast-forwarded to {tag}"
        git("checkout", "--quiet", "--detach", f"refs/tags/{tag}")
        return f"checked out {tag}"


def reset_to(commit: str) -> str:
    with loam_lock.live("Loam", "update"):
        git("merge", "--abort", check=False)
        git("reset", "--quiet", "--hard", commit)
    return f"the checkout is at {commit[:12]} again"


def verify(version: str) -> str:
    said = running_version()
    if said != version:
        raise RuntimeError(f"after the move {VERSION_FILE} says {said!r}, not {version!r}")
    return f"{VERSION_FILE} says {version}"


REGEN = ("units", "agents", "providers", "provider-memory", "broker", "gateway",
         "deploy", "app", "host")


def _regenerate(run: Run, box: Box, old: str, new: str, changed: list[str],
                done: list[str]) -> None:
    for name in REGEN:
        if needs(name, old, new, changed):
            run.step(name, getattr(box, name.replace("-", "_")))
            done.append(name)
        else:
            run.skip(name, "nothing it reads changed")


def apply(target: str | None = None, accept_library_edits: bool = False,
          box: Box | None = None, run_id: str | None = None, by: str | None = None) -> dict:
    """Move this install to ``target`` (default: the newest release), in
    this process. Returns the run's record; never raises for a refusal or a
    failure, which are the record's ``outcome``."""
    box = box or Box()
    try:
        with run_lock():
            return _apply(target, accept_library_edits, box, run_id, by)
    except Refusal as r:
        # Only `busy` reaches here: a second run while one holds the lock.
        return {"outcome": "refused", "refusal": r.as_dict(), "message": r.message}


def _apply(target, accept_library_edits, box, run_id, by) -> dict:
    run = Run("apply", {"id": run_id, "by": by, "to_version": target and bare(target)})
    box.on_line = run.note
    try:
        plan = run.step("preflight", preflight, target, accept_library_edits)
    except Refusal as r:
        run.rec["refusal"] = r.as_dict()
        # Edits waiting on a yes are the person's to look at, not a refusal
        # to hand on.
        if r.code != "library-edits":
            tell_refused(target and (tag_of(target) or target), r)
        return run.end("refused", r.message)
    except StepFailed as f:
        return run.end("failed", f"The check before the update failed: {f.message}. "
                                 f"Nothing was changed.")
    cur, tag = plan["current"], plan["tag"]
    run.rec.update(from_version=cur["version"], from_commit=cur["commit"],
                   from_tag=tag_of(cur["version"]), to_version=plan["version"],
                   to_tag=tag, to_commit=plan["commit"], kind=cur["kind"],
                   library_edits={k: plan["library_edits"][k]
                                  for k in ("any", "edited", "added", "deleted")})
    run.save()
    backup = None
    done: list[str] = []
    # The rollback point, on disk before anything moves: an apply that is
    # killed mid-way (a reboot, the OOM killer) leaves no record of its own
    # end, and `rollback` reads this to put the previous release back.
    pending = {"from_version": cur["version"], "from_commit": cur["commit"],
               "to_version": plan["version"], "to_commit": plan["commit"],
               "kind": cur["kind"], "backup": None, "library_replaced": False,
               "ran": done, "run": run.rec["id"], "at": now_iso()}
    write_json("pending.json", pending)
    try:
        backup = run.step("backup", backup_library, run.rec["id"])
        run.rec["backup"] = pending["backup"] = backup
        write_json("pending.json", pending)
        try:
            run.step("move", move_to, tag, cur["kind"])
        except Refusal as r:
            run.rec["refusal"] = r.as_dict()
            _path("pending.json").unlink(missing_ok=True)
            tell_refused(tag, r)
            return run.end("refused", r.message)
        new = head()
        run.rec["merged_commit"] = pending["to_commit"] = new
        write_json("pending.json", pending)
        run.step("verify", verify, plan["version"])
        pending["library_replaced"] = True
        write_json("pending.json", pending)
        run.step("library", box.library)
        done.append("library")
        changed = changed_files(cur["commit"], new)
        run.rec["changed_files"] = len(changed)
        by_hand = [f for f in changed if f.startswith(prefix() + "systemd/system/")]
        if by_hand:
            run.rec["warnings"] = [f"system units changed and are installed by hand "
                                   f"(root): {', '.join(by_hand)}"]
        _regenerate(run, box, cur["commit"], new, changed, done)
    except StepFailed as f:
        run.rec["failed_step"] = f.step
        # Undone whatever step failed: a reset to the old commit and the
        # backup put back are both no-ops when nothing had moved yet.
        run.rec["rolled_back"] = (True if f.step == "backup" else
                                  _undo(run, box, cur["commit"], backup, done))
        if run.rec["rolled_back"]:
            _path("pending.json").unlink(missing_ok=True)
        else:
            write_json("pending.json", {**pending, "ran": done})
        tell_failed(run.rec, f.message)
        state = "back at" if run.rec["rolled_back"] else "trying to put back"
        return run.end("failed",
                       f"The update failed at “{STEP_WORDS.get(f.step, f.step)}”: "
                       f"{f.message.strip().splitlines()[-1] if f.message.strip() else ''}. "
                       f"Loam is {state} {cur['version']}.")
    write_json("last.json", {"from_version": cur["version"], "from_commit": cur["commit"],
                             "to_version": plan["version"], "to_commit": head(),
                             "kind": cur["kind"], "backup": backup, "ran": done,
                             "at": now_iso(), "run": run.rec["id"]})
    _path("pending.json").unlink(missing_ok=True)
    tell_applied(run.rec)
    return run.end("applied", f"Loam is at {plan['version']}.")


def _undo(run: Run, box: Box, old_commit: str, backup: str | None,
          done: list[str]) -> bool:
    """Put the previous release back after a failure: the checkout, then
    System/Loam, then what was regenerated, from the old code."""
    ok = True
    for name, fn, args in (("undo-move", reset_to, (old_commit,)),
                           ("undo-library", restore_library, (backup,)),
                           ("undo-views", refresh_views, ())):
        try:
            run.step(name, fn, *args)
        except StepFailed:
            ok = False
    deferred = any(e.get("name") == "broker" and e.get("deferred")
                   for e in run.rec.get("steps") or [])
    for name in REGEN:
        if name == "broker" and deferred:
            # Never restarted, so it runs the release it started on: nothing
            # to undo, and a second idle wait would only defer again.
            run.skip("undo-broker", "the broker was not restarted (its restart "
                     "deferred: the box was not idle), so it runs the previous "
                     "release already")
            continue
        if name in done or (name == "broker" and run.rec.get("failed_step") == "broker"):
            try:
                run.step(f"undo-{name}", getattr(box, name.replace("-", "_")))
            except StepFailed:
                ok = False
    return ok


def rollback(box: Box | None = None, run_id: str | None = None,
             by: str | None = None) -> dict:
    """Put back the release the last successful apply replaced."""
    box = box or Box()
    try:
        with run_lock():
            return _rollback(box, run_id, by)
    except Refusal as r:
        return {"outcome": "refused", "refusal": r.as_dict(), "message": r.message}


def rollback_preflight() -> dict:
    """The rollback point: an apply that stopped without finishing
    (``pending.json``) first, else the last one that landed (``last.json``)."""
    pending = read_json("pending.json")
    cur = current()
    if pending:
        if cur["commit"] not in (pending["from_commit"], pending["to_commit"]):
            raise Refusal("moved-since", f"Loam has changed since the update to "
                          f"{pending['to_version']} stopped (the checkout is at "
                          f"{cur['commit'][:12]}), so a rollback would discard that.")
        # The tree is the stopped run's own doing, not somebody's: no
        # dirt check; `reset --hard` puts back what is tracked and removes
        # nothing untracked.
        return {**pending, "interrupted": True, "_file": "pending.json"}
    last = read_json("last.json")
    if not last:
        raise Refusal("nothing-to-roll-back", "There is no update to roll back.")
    if cur["commit"] != last["to_commit"]:
        raise Refusal("moved-since", f"Loam has changed since the update to "
                      f"{last['to_version']} (the checkout is at "
                      f"{cur['commit'][:12]}, not {last['to_commit'][:12]}), so "
                      f"rolling back would discard that too.")
    found = dirt(last.get("kind") or cur["kind"])
    if found:
        raise Refusal("dirty", "Loam's own folder has files in it that no release "
                      "put there; a rollback would overwrite them.", files=found)
    return {**last, "_file": "last.json"}


def _rollback(box, run_id, by) -> dict:
    run = Run("rollback", {"id": run_id, "by": by})
    box.on_line = run.note
    try:
        last = run.step("preflight", rollback_preflight)
    except Refusal as r:
        run.rec["refusal"] = r.as_dict()
        return run.end("refused", r.message)
    run.rec.update(from_version=last["to_version"], to_version=last["from_version"],
                   from_commit=last["to_commit"], to_commit=last["from_commit"])
    run.save()
    try:
        here = run.step("backup", backup_library, run.rec["id"])
        run.rec["backup"] = here
        run.step("back", reset_to, last["from_commit"])
        run.step("verify", verify, last["from_version"])
        if last.get("interrupted") and not last.get("library_replaced"):
            run.skip("library-back", "the stopped update had not replaced it")
        else:
            run.step("library-back", restore_library, last.get("backup"))
        run.step("views", refresh_views)
        changed = changed_files(last["from_commit"], last["to_commit"])
        done: list[str] = []
        _regenerate(run, box, last["to_commit"], last["from_commit"], changed, done)
    except StepFailed as f:
        run.rec["failed_step"] = f.step
        notice("failed", f"Rolling Loam back to {last['from_version']} failed at "
               f"“{STEP_WORDS.get(f.step, f.step)}”.",
               f"A rollback of Loam from {last['to_version']} to "
               f"{last['from_version']} failed at `{f.step}`: {f.message}\n\nThe "
               f"record is {_path('apply.json')}. Tell {_owner()} plainly; the "
               f"checkout is at {head()[:12]}.")
        return run.end("failed", f"The rollback failed at “{STEP_WORDS.get(f.step, f.step)}”: "
                       f"{f.message.strip().splitlines()[-1] if f.message.strip() else ''}.")
    _path(last["_file"]).unlink(missing_ok=True)
    notice("rolled-back", f"Loam was rolled back from {last['to_version']} to "
           f"{last['from_version']}.",
           f"Loam was rolled back from {last['to_version']} to "
           f"{last['from_version']}{f' by {by}' if by else ''}. Tell {_owner()} "
           f"briefly, in your own words, that the earlier release is running "
           f"again.")
    return run.end("rolled-back", f"Loam is back at {last['from_version']}.")


# -- starting a run as its own process ------------------------------------------------------

def start(action: str, target: str | None = None, accept_library_edits: bool = False,
          by: str | None = None) -> dict:
    """Check what must hold, then run ``action`` (``apply`` or ``rollback``)
    as its own process, so a broker restart cannot cut it. Raises Refusal
    for what the page can show at once; answers the run's first record."""
    if running():
        raise Refusal("busy", "An update is already running.")
    if action == "apply":
        try:
            preflight(target, accept_library_edits)
        except Refusal as r:
            if r.code != "library-edits":
                tell_refused(target and (tag_of(target) or target), r)
            raise
    elif action == "rollback":
        rollback_preflight()
    else:
        raise ValueError(action)
    run_id = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    argv = [sys.executable, str(runtime() / "bin" / "loam-update"), action,
            "--foreground", "--quiet", "--run-id", run_id]
    if action == "apply":
        if target:
            argv += ["--to", target]
        if accept_library_edits:
            argv.append("--yes")
    if by:
        argv += ["--by", by]
    write_json("apply.json", {"id": run_id, "action": action, "outcome": "starting",
                              "started_at": now_iso(), "steps": [], "by": by,
                              "message": None, "phase": "starting"})
    spawn(argv, run_id)
    return read_json("apply.json") or {}


def spawn(argv: list[str], run_id: str) -> None:
    """A transient user unit when systemd is there (it outlives the broker
    and the shell that asked); a detached process otherwise."""
    keep = {k: v for k, v in os.environ.items()
            if k.startswith("LOAM_") or k in ("PATH", "HOME", "PYTHONPYCACHEPREFIX",
                                              "XDG_RUNTIME_DIR", "LANG",
                                              "DBUS_SESSION_BUS_ADDRESS")}
    if not os.environ.get("LOAM_UPDATE_NO_UNIT"):
        # Never a plain child as the fallback: started by the broker, a child
        # is in the broker's cgroup, and the restart it ends with kills it.
        cmd = ["systemd-run", "--user", "--quiet", "--collect",
               f"--unit=loam-update-{run_id}", "--description=Loam update",
               *[f"--setenv={k}={v}" for k, v in keep.items()], *argv]
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except OSError as exc:
            done = subprocess.CompletedProcess(cmd, 1, "", str(exc))
        if done.returncode != 0:
            write_json("apply.json", {**(read_json("apply.json") or {}),
                                      "outcome": "failed", "ended_at": now_iso(),
                                      "message": "The update could not be started "
                                      f"as its own unit: {done.stderr.strip()}"})
            raise RuntimeError(f"systemd-run: {done.stderr.strip()}")
        return
    _path("runs").mkdir(parents=True, exist_ok=True)
    out = open(_path(f"runs/{run_id}.log"), "a")  # noqa: SIM115
    subprocess.Popen(argv, stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                     start_new_session=True, env=keep, cwd="/")
