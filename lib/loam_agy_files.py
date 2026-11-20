"""Retain an engineer job's or review's Antigravity conversation for a week after the
run. Clean other conversations after a week idle. Attribute opaque implicit files by
the job's recorded file set. A continuation needs the conversation database until
retention expires.
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
    ``antigravity.json``, which is what a continuation's refusal reads.
    Then every conversation no job names that has been idle a week, and the
    implicit files no job names that are a week old (``clear_idle``)."""
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
    # Read again: a job conversation cleared above is gone from the disk,
    # and the jobs still name it, so it is not counted idle twice.
    return done + clear_idle(rows, now, dry)


# -- every other conversation: cleared once idle a week (2026-11-16) ------------
#
# The owner's "Sounds good" (2026-11-15 20:53 PT) was to clearing
# Antigravity's files after a week, read by the lead persona as all of them.
# A conversation no job names -- a persona's thread session, a probe, the
# owner's own -- is cleared once nothing has touched it for KEEP_DAYS: its
# .db (and -wal), its brain folder and every file in it, and its summary
# rows. Idle is the newest mtime among those files, which every turn writes.
# One an ``agy`` process is running on is never touched, whatever its age.
#
# A thread session resumed after the clearing is not resumed: the thread
# engine sees its conversation gone and starts a fresh session, which is
# given the thread's recent exchange and the persona's summary, as a
# rotation is (lib/loam_thread_engine.py ``agy_conversation_gone``). Asked
# to resume a conversation it no longer has, ``agy`` 1.5.9 says
# `conversation "<id>" not found` and starts a new one with no context, so
# the engine must not leave it to that (read 2026-11-16).
#
# Implicit files name no conversation. Resuming does not read them: a
# conversation whose turn's implicit file was moved away resumed with what
# it had been told (2026-11-16), and each turn writes a new one. So one no
# job names is cleared once it is KEEP_DAYS old; a job's are its job's.


def last_active(conversation: str) -> float | None:
    """The newest mtime among a conversation's files, or None when it has
    none left (only summary rows)."""
    base = root()
    newest = None
    paths = [base / "conversations" / f"{conversation}{ext}"
             for ext in (".db", ".db-wal", ".db-shm")]
    brain = base / "brain" / conversation
    paths.append(brain)
    if brain.is_dir():
        for dirpath, dirnames, filenames in os.walk(brain):
            paths += [Path(dirpath) / n for n in dirnames + filenames]
    for path in paths:
        try:
            mtime = path.lstat().st_mtime
        except OSError:
            continue
        newest = mtime if newest is None else max(newest, mtime)
    return newest


def known() -> set[str]:
    """Every conversation with anything left: a .db, a brain folder, or a
    summary row."""
    base = root()
    ids: set[str] = set()
    try:
        ids.update(p.name[:-3] for p in (base / "conversations").glob("*.db"))
    except OSError:
        pass
    try:
        ids.update(p.name for p in (base / "brain").iterdir() if p.is_dir())
    except OSError:
        pass
    db = base / "conversation_summaries.db"
    if db.exists():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        try:
            ids.update(r[0] for r in con.execute(
                "SELECT conversation_id FROM conversation_summaries") if r[0])
        except sqlite3.Error:
            pass
        finally:
            con.close()
    return ids


def running() -> set[str]:
    """Conversations an ``agy`` process names (``--conversation <id>``)
    right now."""
    found: set[str] = set()
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        for i, arg in enumerate(argv[:-1]):
            if arg == b"--conversation":
                found.add(argv[i + 1].decode(errors="replace"))
            elif arg.startswith(b"--conversation="):
                found.add(arg.split(b"=", 1)[1].decode(errors="replace"))
    return found


def idle(rows: list[dict], now: float | None = None) -> dict[str, float | None]:
    """``{conversation id: last active}`` for each conversation no job names
    that nothing has touched for KEEP_DAYS and no process is running on."""
    now = time.time() if now is None else now
    jobs_own, _ = owned(rows)
    busy = running()
    out = {}
    for cid in known() - jobs_own - busy:
        last = last_active(cid)
        if last is None or last + KEEP_DAYS * 86400 <= now:
            out[cid] = last
    return out


def stale_implicit(rows: list[dict], now: float | None = None) -> list[str]:
    """Implicit files no job names that are KEEP_DAYS old."""
    now = time.time() if now is None else now
    _, jobs_own = owned(rows)
    out = []
    try:
        for path in (root() / "implicit").iterdir():
            if path.name in jobs_own:
                continue
            try:
                if path.lstat().st_mtime + KEEP_DAYS * 86400 <= now:
                    out.append(path.name)
            except OSError:
                continue
    except OSError:
        pass
    return sorted(out)


def clear_idle(rows: list[dict], now: float | None = None,
               dry: bool = False) -> list[dict]:
    """Clear every idle conversation no job names, then the stale implicit
    files: one row each, ``{"conversation", "jobs": [], "idle_since",
    "removed", "rows", "error"}``; the implicit files are one row whose
    ``conversation`` is None."""
    now = time.time() if now is None else now
    base = root()
    done = []
    for cid, last in sorted(idle(rows, now).items()):
        files = [base / "conversations" / f"{cid}{ext}"
                 for ext in (".db", ".db-wal", ".db-shm")] + [base / "brain" / cid]
        row = {"conversation": cid, "jobs": [],
               "idle_since": iso(last) if last else None,
               "removed": [], "rows": 0, "error": None}
        if dry:
            row["removed"] = [str(f.relative_to(base)) for f in files if f.exists()]
            done.append(row)
            continue
        # Asked again at the last moment: a turn may have begun since.
        last_now = last_active(cid)
        if (last_now is not None and last_now + KEEP_DAYS * 86400 > now) \
                or cid in running():
            continue
        try:
            for f in files:
                if _remove(f):
                    row["removed"].append(str(f.relative_to(base)))
            row["rows"] = _summary_rows(cid)
        except (OSError, sqlite3.Error) as exc:
            row["error"] = str(exc)
        done.append(row)
    stale = stale_implicit(rows, now)
    if stale:
        row = {"conversation": None, "jobs": [], "idle_since": None,
               "removed": [], "rows": 0, "error": None}
        for name in stale:
            path = base / "implicit" / name
            if dry:
                row["removed"].append(f"implicit/{name}")
                continue
            try:
                if _remove(path):
                    row["removed"].append(f"implicit/{name}")
            except OSError as exc:
                row["error"] = str(exc)
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
