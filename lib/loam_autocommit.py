"""The routine local commit: the recovery floor of every install.

Every fifteen minutes (``loam-autocommit.timer``, ``bin/autocommit``) the
vault, the agents' workspaces (AI Spaces) and the plugins' Yours layer are
each committed to their own git repository, on this machine, with nothing
sent anywhere. Every agent is told that a change it regrets can be got back
from that history; until 2026-11-18 a fresh install made the repositories
(`bin/loam-install`, step 6) and never committed to them again, so the
floor was only on the first box, where hand-made cron lines did it. This is
that routine, in core. Sending the history off the machine is the Git
backup plugin's (`plugins/git-backup/`, docs/backup.md), and nothing here
knows it exists.

What it does to each tree, in order, under a lock the plugin's push also
takes (``lock``):

- makes the repository (`git init -b main`) if the folder has none;
- keeps out of the commit any file larger than ``git.autocommit.max_file_mb``
  (100 MB): a git host refuses it (GitHub, at 100 MiB), and once in history
  it would stop every later push. The file stays on disk, untouched and
  uncommitted, and is named in the tree's record (``held``), which the
  plugin shows the owner. A tracked file that grows past the limit keeps its
  last committed version; the newer one waits;
- commits everything else as ``<tree>: <UTC time>`` under the install's
  ``git.autocommit`` identity, given with ``-c`` so the repository's own
  config is never touched.

**A hand-made routine wins.** On a box whose crontab already runs
``bin/vault-autocommit`` (or ``ai-labs-autocommit``, ``plugins-autocommit``),
that tree is left to it, and the record says so: two routines committing
one tree would race for its index. Taking the line out of the crontab hands
the tree to this one on its next run.

The record of each run is ``<state>/autocommit/<tree>.json`` (what was
committed, what was held back and why, when) and one line in
``<logs>/autocommit.log``.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import loam_config as lc

# tree id -> (the folder, the hand-made script that may already commit it)
HAND_SCRIPTS = {"vault": "vault-autocommit", "spaces": "ai-labs-autocommit",
                "plugins": "plugins-autocommit"}
ORDER = ("vault", "spaces", "plugins")
LOCK_NAME = "loam-backup.lock"

# Always kept out of the workspaces' commits: what the runtime writes and
# rewrites on every run, which no agent recovers from history. Written into
# the repository's own `.git/info/exclude`, between these markers, never into
# a tracked file; a `.gitignore` of the person's own still applies as well.
EXCLUDE_BEGIN = "# >>> loam: kept out of the routine commit (lib/loam_autocommit.py)"
EXCLUDE_END = "# <<< loam"
EXCLUDES = {
    "vault": [".trash/", ".DS_Store"],
    "spaces": ["**/scratch/", "**/logs/", "**/cache/", "**/.locks/",
               "**/node_modules/", "**/__pycache__/", "*.pyc", "**/.venv/",
               ".DS_Store"],
    "plugins": [".staging/", "**/__pycache__/", ".DS_Store"],
}


def trees() -> dict[str, Path]:
    """The trees this install commits, by id."""
    return {"vault": Path(lc.VAULT), "spaces": Path(lc.WORKSPACES),
            "plugins": Path(lc.PLUGINS)}


def state_dir() -> Path:
    return Path(os.environ.get("LOAM_AUTOCOMMIT_STATE") or (Path(lc.STATE) / "autocommit"))


def log_file() -> Path:
    return Path(os.environ.get("LOAM_AUTOCOMMIT_LOG") or (Path(lc.LOGS) / "autocommit.log"))


def max_bytes() -> int:
    raw = os.environ.get("LOAM_AUTOCOMMIT_MAX_BYTES")
    if raw and raw.isdigit():
        return int(raw)
    return int(lc.AUTOCOMMIT_MAX_FILE_MB) * 1_000_000


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(text: str) -> None:
    try:
        path = log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{now()}] {text}\n")
    except OSError:
        pass


def crontab_text() -> str:
    """The user's crontab (``LOAM_CRONTAB_FILE`` names a file instead, for a
    test). Empty when there is none or cron is not installed."""
    override = os.environ.get("LOAM_CRONTAB_FILE")
    if override is not None:
        try:
            return Path(override).read_text(encoding="utf-8") if override else ""
        except OSError:
            return ""
    try:
        done = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                              timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def hand_routine(tree: str, crontab: str | None = None) -> str | None:
    """The crontab line that already commits this tree, if one does."""
    script = HAND_SCRIPTS.get(tree)
    if not script:
        return None
    for line in (crontab_text() if crontab is None else crontab).splitlines():
        text = line.strip()
        if text and not text.startswith("#") and f"/{script}" in text.split("#", 1)[0]:
            return text
    return None


def git(where: Path, *args: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    return subprocess.run(["git", "-C", str(where), *args], capture_output=True,
                          check=check, env=env, **kw)


@contextlib.contextmanager
def lock(where: Path, timeout: float = 600):
    """One routine at a time on one tree: this commit and the plugin's push.
    The lock file is inside `.git/`, so it is never committed."""
    path = Path(where) / ".git" / LOCK_NAME
    if not path.parent.is_dir():
        raise OSError(f"{where} is not a git repository")
    fh = open(path, "a+")
    try:
        import time  # noqa: PLC0415
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{where} is locked by another backup run")
                time.sleep(0.5)
        yield
    finally:
        fh.close()


def _exclude(where: Path, tree: str) -> None:
    patterns = EXCLUDES.get(tree) or []
    path = where / ".git" / "info" / "exclude"
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        return
    block = "\n".join([EXCLUDE_BEGIN, *patterns, EXCLUDE_END]) + "\n"
    if EXCLUDE_BEGIN in text and EXCLUDE_END in text:
        head, rest = text.split(EXCLUDE_BEGIN, 1)
        tail = rest.split(EXCLUDE_END, 1)[1].lstrip("\n")
        new = head + block + tail
    else:
        new = text + ("" if not text or text.endswith("\n") else "\n") + block
    if new != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")


def oversize(where: Path, limit: int | None = None) -> list[dict]:
    """Files a commit would take that are over the limit: new ones (untracked,
    not ignored) and tracked ones changed since the last commit."""
    limit = max_bytes() if limit is None else limit
    out, seen = [], set()
    for args in (("ls-files", "-z", "-o", "--exclude-standard"), ("ls-files", "-z", "-m")):
        done = git(where, *args, check=False)
        for raw in done.stdout.split(b"\0"):
            if not raw or raw in seen:
                continue
            seen.add(raw)
            rel = raw.decode("utf-8", "surrogateescape")
            try:
                st = (where / rel).lstat()
            except OSError:
                continue
            if st.st_size > limit and not (where / rel).is_symlink():
                out.append({"path": rel, "bytes": st.st_size})
    out.sort(key=lambda f: f["path"])
    return out


def record_path(tree: str) -> Path:
    return state_dir() / f"{tree}.json"


def read_record(tree: str) -> dict:
    try:
        data = json.loads(record_path(tree).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_record(tree: str, record: dict) -> None:
    path = record_path(tree)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def commit(tree: str, where: Path | None = None, *, crontab: str | None = None,
           limit: int | None = None) -> dict:
    """Commit one tree. Never raises for the tree's own trouble: the answer's
    ``error`` says it, and the record keeps it."""
    where = Path(where or trees()[tree])
    record = {"tree": tree, "path": str(where), "at": now(), "committed": None,
              "held": [], "skipped": None, "error": None,
              "limit_bytes": max_bytes() if limit is None else limit}
    previous = read_record(tree)
    hand = hand_routine(tree, crontab)
    if hand:
        record["skipped"] = "hand-made"
        record["hand"] = hand
        if previous.get("skipped") != "hand-made":
            _log(f"{tree}: left to the crontab's own routine ({hand})")
        _write_record(tree, record)
        return record
    if not where.is_dir():
        record["skipped"] = "no folder"
        _write_record(tree, record)
        return record
    try:
        # Before the lock, whose file lives in `.git/` and would make one.
        if not (where / ".git" / "HEAD").exists():
            git(where, "init", "--quiet", "-b", "main")
            _log(f"{tree}: {where} is a repository now")
        with lock(where):
            _exclude(where, tree)
            held = oversize(where, record["limit_bytes"])
            record["held"] = held
            if held:
                with tempfile.NamedTemporaryFile("wb", delete=False) as spec:
                    spec.write(b".\0" + b"".join(
                        b":(exclude,literal)" + f["path"].encode("utf-8", "surrogateescape") + b"\0"
                        for f in held))
                try:
                    git(where, "add", "-A", f"--pathspec-from-file={spec.name}",
                        "--pathspec-file-nul")
                finally:
                    os.unlink(spec.name)
                # Anything already staged from before is taken back out.
                for f in held:
                    git(where, "reset", "-q", "--", f":(literal){f['path']}", check=False)
            else:
                git(where, "add", "-A")
            if git(where, "diff", "--cached", "--quiet", check=False).returncode != 0:
                stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
                git(where, "-c", f"user.name={lc.AUTOCOMMIT_NAME}",
                    "-c", f"user.email={lc.AUTOCOMMIT_EMAIL}",
                    "commit", "--quiet", "--no-verify", "-m", f"{tree}: {stamp}")
                sha = git(where, "rev-parse", "--short", "HEAD").stdout.decode().strip()
                record["committed"] = sha
                _log(f"{tree}: commit {sha}" + (f", {len(held)} held back" if held else ""))
            new_held = {f["path"] for f in held} - {f["path"] for f in previous.get("held") or []}
            for path in sorted(new_held):
                _log(f"{tree}: held back {path}: over {record['limit_bytes']} bytes")
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode("utf-8", "replace").strip()[-400:]
        record["error"] = err or f"git exited {exc.returncode}"
        _log(f"{tree}: FAIL {record['error']}")
    except (OSError, TimeoutError) as exc:
        record["error"] = str(exc)
        _log(f"{tree}: FAIL {exc}")
    _write_record(tree, record)
    return record


def run(names: list[str] | None = None) -> list[dict]:
    names = names or list(ORDER)
    found = trees()
    crontab = crontab_text()
    return [commit(name, found[name], crontab=crontab) for name in names if name in found]
