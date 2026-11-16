"""Antigravity's per-conversation files for engineer jobs, cleared a week
after the job (2026-11-15).

The owner, 2026-11-15 20:53 PT, on clearing Antigravity's per-conversation
files a week after a job: "Sounds good." Every ``agy`` conversation leaves,
under ``~/.gemini/antigravity-cli/`` (1.5.9, read 2026-11-15):

- ``conversations/<id>.db``: the conversation itself, which a continuation
  (``--conversation <id>``) resumes;
- ``brain/<id>/``: its working files;
- ``implicit/<uuid>.pb``: opaque files, named by no conversation (14 for 11
  conversations when read), so they are tied to a job by when they appeared
  (``attribute``) and never by name;
- rows in ``conversation_summaries.db`` (``conversation_summaries``,
  ``conversations_fts``, ``conversation_parents``): the ``/resume`` index.

Tested by the lead persona 11-15: the CLI has no memory tools and nothing
carries across conversations, so these are transcripts and an index, not a
memory. An engineer job's are cleared ``KEEP_DAYS`` after the last job that
names its conversation finished (a continuation appends to its parent's
conversation, so a chain keeps it until a week after its last job), by the
nightly ``provider-follow`` pass (``clear``). Only conversations a Loam job
record names are touched: a persona's own conversation, or the owner's, is
named by no job and is never cleared. The provider-memory check
(lib/loam_provider_memory.py) leaves the same files out of its counts, and
since 2026-11-16 says no growth of these per-conversation stores at all, a
persona's conversation's included: only a new kind of entry under the tree.

The reviewer's runs on Antigravity (since 2026-11-15, bin/review) are
cleared the same way: its run directories (``<reviewer state>/runs/<id>``)
are job directories here, each with a ``runtime`` file, an
``antigravity.json`` and its ``run.json``, whose ``finished`` is the
verdict's time.

A job's record of its files is ``antigravity.json`` in its job directory,
written by bin/engineer-job when the session ends: the conversations it ran
(a quota retry is a second one) and the implicit files attributed to it. A
job from before that file (the 11-15 trial's) is read from its
``session-id``.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import loam_antigravity as agy
import loam_config as lc

KEEP_DAYS = 7
RECORD = "antigravity.json"

# The summaries database's tables keyed by a conversation id.
SUMMARY_TABLES = (("conversation_summaries", "conversation_id"),
                  ("conversations_fts", "conversation_id"),
                  ("conversation_parents", "conversation_id"),
                  ("conversation_parents", "parent_id"))


def root() -> Path:
    return Path(os.environ.get("LOAM_AGY_CLI_HOME") or agy.HOME / "antigravity-cli")


def job_roots() -> list[Path]:
    """Every engineer's jobs directory: the platform engineer's and each
    project's under ``<state>/projects/<name>/jobs``, read off the disk
    rather than the registry, so a job of a project since unregistered is
    still one Loam ran."""
    import loam_engineer as eng  # noqa: PLC0415
    override = os.environ.get("LOAM_AGY_JOB_ROOTS")
    if override:
        return [Path(p) for p in override.split(os.pathsep) if p]
    roots = [lc.JOBS, lc.REVIEWER_STATE / "runs"]
    try:
        roots += sorted(d / "jobs" for d in eng.PROJECT_STATE.iterdir()
                        if (d / "jobs").is_dir())
    except OSError:
        pass
    return roots


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _parse(text) -> float | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_record(job_dir: Path) -> dict:
    try:
        data = json.loads((job_dir / RECORD).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def job_files(job_dir: Path) -> dict | None:
    """``{"dir", "conversations", "implicit", "finished", "cleared"}`` for a
    job that ran on Antigravity, None for any other. ``finished`` is None
    while it has no verdict (running, queued, or interrupted)."""
    if _read(job_dir / "runtime") != "antigravity":
        return None
    record = read_record(job_dir)
    convs = [c for c in record.get("conversations") or [] if isinstance(c, str) and c]
    session = _read(job_dir / "session-id")
    if session and session not in convs:
        convs.append(session)
    try:
        result = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A reviewer run's verdict is its run.json (bin/review).
        try:
            result = json.loads((job_dir / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result = {}
    return {"dir": job_dir, "conversations": convs,
            "implicit": [n for n in record.get("implicit") or [] if isinstance(n, str)],
            "finished": _parse(result.get("finished")) if isinstance(result, dict) else None,
            "cleared": record.get("cleared")}


def jobs() -> list[dict]:
    out = []
    for jobs_root in job_roots():
        try:
            dirs = list(jobs_root.iterdir())
        except OSError:
            continue
        for d in dirs:
            row = job_files(d) if d.is_dir() else None
            if row:
                out.append(row)
    return out


def owned(rows: list[dict] | None = None) -> tuple[set[str], set[str]]:
    """(conversation ids, implicit file names) Loam's engineer jobs own,
    finished or not: what the provider-memory check leaves out."""
    rows = jobs() if rows is None else rows
    convs, implicit = set(), set()
    for row in rows:
        convs.update(row["conversations"])
        implicit.update(row["implicit"])
    return convs, implicit


# -- what a job's run left: read by bin/engineer-job ------------------------------

def snapshot() -> dict:
    """What is there before a session starts: the implicit files' names and
    the time."""
    try:
        names = sorted(p.name for p in (root() / "implicit").iterdir())
    except OSError:
        names = []
    return {"implicit": names, "at": time.time()}


def attribute(before: dict, conversations: list[str]) -> dict:
    """The record of a finished session: its conversations, and the implicit
    files that appeared while it ran -- only when no conversation Loam's jobs
    do not name was written in that window, because an implicit file names no
    conversation and a persona's (or the owner's) running at the same time
    could have made it. Ambiguous, none is attributed and the note says why;
    those files then stay, and the memory check still counts them."""
    now = snapshot()
    new = sorted(set(now["implicit"]) - set(before.get("implicit") or []))
    since = float(before.get("at") or 0)
    job_convs, _ = owned()
    job_convs.update(conversations)
    others = []
    try:
        for db in (root() / "conversations").glob("*.db"):
            cid = db.name[:-3]
            if cid not in job_convs and db.stat().st_mtime >= since - 1:
                others.append(cid)
    except OSError:
        pass
    record = {"conversations": [c for c in conversations if c],
              "implicit": [] if others else new,
              "window": [iso(since), iso(now["at"])]}
    if others and new:
        record["implicit_note"] = (
            f"{len(new)} implicit file(s) appeared during the run, while "
            f"conversation(s) no job names were written too "
            f"({', '.join(sorted(others))}); none attributed, none will be "
            f"cleared")
    return record


def write_record(job_dir: Path, record: dict) -> None:
    current = read_record(job_dir)
    current.update(record)
    tmp = job_dir / (RECORD + ".tmp")
    tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, job_dir / RECORD)


# -- clearing: the nightly pass -------------------------------------------------

def due(rows: list[dict], now: float | None = None) -> dict[str, list[dict]]:
    """``{conversation id: [the jobs naming it]}`` for each conversation
    whose every job finished more than KEEP_DAYS ago and is not yet
    cleared. One job still running, or with no verdict, keeps it."""
    now = time.time() if now is None else now
    by_conv: dict[str, list[dict]] = {}
    for row in rows:
        for cid in row["conversations"]:
            by_conv.setdefault(cid, []).append(row)
    out = {}
    for cid, named in by_conv.items():
        if any(r["finished"] is None for r in named):
            continue
        if max(r["finished"] for r in named) + KEEP_DAYS * 86400 > now:
            continue
        if all(cid in ((r["cleared"] or {}).get("conversations") or []) for r in named):
            continue
        out[cid] = named
    return out


def _remove(path: Path) -> bool:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
        else:
            return False
        return True
    except FileNotFoundError:
        return False


def _summary_rows(conversation: str) -> int:
    db = root() / "conversation_summaries.db"
    if not db.exists():
        return 0
    con = sqlite3.connect(str(db), timeout=10)
    try:
        removed = 0
        for table, column in SUMMARY_TABLES:
            try:
                removed += con.execute(
                    f'DELETE FROM "{table}" WHERE "{column}" = ?',
                    (conversation,)).rowcount or 0
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise
        con.commit()
        return removed
    finally:
        con.close()


def clear(now: float | None = None, dry: bool = False) -> list[dict]:
    """Clear every due conversation's files and say what went, one row per
    conversation: ``{"conversation", "jobs", "removed": [...], "rows",
    "error"}``. Each job naming it records the clearing in its
    ``antigravity.json``, which is what a continuation's refusal reads."""
    now = time.time() if now is None else now
    rows = jobs()
    base = root()
    done = []
    for cid, named in sorted(due(rows, now).items()):
        files = [base / "conversations" / f"{cid}.db",
                 base / "conversations" / f"{cid}.db-wal",
                 base / "conversations" / f"{cid}.db-shm",
                 base / "brain" / cid]
        implicit = sorted({n for r in named for n in r["implicit"]})
        files += [base / "implicit" / n for n in implicit]
        row = {"conversation": cid, "jobs": sorted(r["dir"].name for r in named),
               "removed": [], "rows": 0, "error": None}
        if dry:
            row["removed"] = [str(f.relative_to(base)) for f in files if f.exists()]
            done.append(row)
            continue
        try:
            row["rows"] = _summary_rows(cid)
            for f in files:
                if _remove(f):
                    row["removed"].append(str(f.relative_to(base)))
        except (OSError, sqlite3.Error) as exc:
            row["error"] = str(exc)
            done.append(row)
            continue          # not marked: the next night tries again
        for r in named:
            cleared = dict(r["cleared"] or {})
            cleared["at"] = iso(now)
            cleared["conversations"] = sorted(
                set(cleared.get("conversations") or []) | {cid})
            try:
                write_record(r["dir"], {"cleared": cleared})
            except OSError as exc:
                row["error"] = f"cleared, but not recorded in {r['dir']}: {exc}"
        done.append(row)
    return done


def cleared_refusal(job_dir: Path, job_id: str, conversation: str) -> str | None:
    """The sentence a continuation of a cleared job is refused with, or None
    when its conversation was not cleared."""
    cleared = read_record(job_dir).get("cleared") or {}
    if conversation not in (cleared.get("conversations") or []):
        return None
    return (f"Refused before starting: job {job_id}'s Antigravity conversation "
            f"({conversation}) was cleared on {cleared.get('at', '?')}, "
            f"{KEEP_DAYS} days after its last job finished; an engineer job's "
            f"Antigravity files are kept a week. There is nothing to resume. "
            f"Send the brief without `continue_job`, restating what that job "
            f"had found (its receipt is in the vault).")
