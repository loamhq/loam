"""Who briefed a job, and the receipt a waiting call could not carry
(2026-11-12).

Two things the brief call did not know until now, both for the same page
(Activity, the owner 11-12: "I also can't easily tell which persona started
them and what thread they are for"):

**The caller** (``caller()``): which lane the brief came from --
``conversation`` (a thread turn or a chat), ``wake`` (a reflection),
``cron``, ``reviewer``, or ``job`` (an engineer job briefing another) -- and,
for a conversation, the broker session that made the call and the thread
that session is a member's turn in. The broker puts ``LOAM_SESSION_ID`` in
every CLI it starts (all three engines); an MCP server may not pass its
environment on (Codex's `env_vars` whitelist does not), so the launcher
also walks its own ancestors and reads each one's environment, and for a
Claude CLI started before the variable existed, its ``--session-id`` /
``--resume`` argument. The thread is the session's marker
(``state/threads/steps/sessions/<id>``, written before a turn's first
message is sent) or else ``sessions.json``. Unknown is left out, never
guessed.

**The waiter** (``register`` / ``settle`` / ``owed``): the launcher's outer
half waits on the job inside the calling session's process tree, so a
broker restart (or the call's four-hour ceiling) ends the wait while the job
runs on as its own unit. Until now its receipt then reached the vault and
nobody told the thread. Each waiting call now leaves a record in
``state/engineer-waiters/<job>.json``; the outer removes it when it hands a
final verdict back. A record whose waiter process is gone and whose job has
a ``result.json`` is **owed**: the broker posts one plain line into the
caller's thread -- what finished and whether it landed, never the receipt --
and gives the briefing persona a turn whose input names the receipt's path,
so the persona reads it and tells the owner in its own words
(``ThreadEngine.deliver_job``), then removes the record. Until 20:30 PT
11-12 the line pasted the receipt's body; the owner said twice that day they
does not want receipts in threads. A turn the restart cut off posts no
"turn ended" line while a delivery for it is still to come
(``pending_for``). One without a
thread to post to is logged as undeliverable and removed.

Nothing here talks to the network; the broker does the posting.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import loam_config as lc

AGENTS = lc.ROOT
STATE = Path(os.environ.get("LOAM_WAITERS_DIR") or lc.STATE / "engineer-waiters")
LOG = lc.LOGS / "engineer-deliveries.jsonl"
THREADS_STATE = Path(os.environ.get("LOAM_THREADS_STATE") or lc.STATE / "threads")
PROC = Path("/proc")
SESSION_ENV = "LOAM_SESSION_ID"
JOB_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4,16}$")

# The one vocabulary of "what kind of run is this", shared by the session
# rows (`GET /v1/sessions/live`) and the job and run rows (`GET /v1/activity`).
RUN_KINDS = ("thread", "conversation", "wake", "cron", "reviewer", "job")
# The engine ids, as the broker names them; a job's `runtime` file says
# `claude` for Claude Code.
ENGINES = {"claude": "claude-code", "claude-code": "claude-code",
           "codex": "codex", "antigravity": "antigravity", "agy": "antigravity"}
ENGINE_LABELS = {"claude-code": "Claude Code", "codex": "Codex",
                 "antigravity": "Antigravity"}


def engine_id(value: str | None) -> str | None:
    return ENGINES.get((value or "").strip().lower()) if value else None


def lane_kind(lane: str | None) -> str:
    """A `LOAM_WAKE` value (or a lock holder's lane) as a run kind: unset or
    ``conversation`` is a conversation, ``reflection`` a wake, ``cron:<n>``
    a cron, ``review``/``review:<run>`` the reviewer, ``engineer:<job>`` and
    ``job:<job>`` a job."""
    value = (lane or "").strip().lower()
    if not value or value == "conversation":
        return "conversation"
    if value == "reflection":
        return "wake"
    if value.startswith("cron:"):
        return "cron"
    if value == "review" or value.startswith("review:"):
        return "reviewer"
    if value.startswith(("engineer:", "job:")):
        return "job"
    return "wake"


# ---------------------------------------------------------------------------
# The caller.
# ---------------------------------------------------------------------------

def _ppid(pid: int, proc: Path) -> int | None:
    try:
        stat = (proc / str(pid) / "stat").read_text(encoding="utf-8")
        return int(stat.rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def _environ(pid: int, proc: Path) -> dict[str, str]:
    try:
        raw = (proc / str(pid) / "environ").read_bytes()
    except OSError:
        return {}
    out = {}
    for item in raw.split(b"\0"):
        key, sep, value = item.partition(b"=")
        if sep:
            out[key.decode(errors="replace")] = value.decode(errors="replace")
    return out


def _argv(pid: int, proc: Path) -> list[str]:
    try:
        return [a.decode(errors="replace") for a in
                (proc / str(pid) / "cmdline").read_bytes().split(b"\0") if a]
    except OSError:
        return []


def _claude_session(argv: list[str]) -> str | None:
    """The session a Claude CLI in stream mode runs as, from its argv."""
    if not argv or not argv[0].endswith("claude"):
        return None
    for flag in ("--session-id", "--resume"):
        if flag in argv[:-1]:
            return argv[argv.index(flag) + 1]
    return None


def calling_session(env: dict | None = None, pid: int | None = None,
                    proc: Path = PROC, depth: int = 12) -> str | None:
    """The broker session this process was started from, or None."""
    env = os.environ if env is None else env
    if env.get(SESSION_ENV):
        return env[SESSION_ENV]
    pid = os.getpid() if pid is None else pid
    for _ in range(depth):
        pid = _ppid(pid, proc)
        if not pid or pid <= 1:
            return None
        found = _environ(pid, proc).get(SESSION_ENV) or _claude_session(_argv(pid, proc))
        if found:
            return found
    return None


def thread_of(session_id: str | None, persona: str | None = None,
              state: Path | None = None) -> str | None:
    """The thread a session is a member's turn in, or None."""
    if not session_id:
        return None
    state = state or THREADS_STATE
    try:
        marked = (state / "steps" / "sessions" / session_id).read_text(
            encoding="utf-8").strip()
        if marked:
            return marked
    except OSError:
        pass
    try:
        data = json.loads((state / "sessions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for tid, members in (data or {}).items():
        for who, kept in (members or {}).items():
            if (isinstance(kept, dict) and kept.get("session_id") == session_id
                    and (persona is None or who == persona)):
                return tid
    return None


def caller(env: dict | None = None, pid: int | None = None,
           proc: Path = PROC, state: Path | None = None) -> dict:
    """{"caller": run kind, "caller_session"?: id, "thread"?: id} for the
    brief being accepted now. Only what is known is there."""
    env = os.environ if env is None else env
    lane = env.get("LOAM_WAKE") or ""
    kind = lane_kind(lane)
    out: dict = {"caller": kind}
    if kind != "conversation":
        return out
    session = calling_session(env, pid, proc)
    if session:
        out["caller_session"] = session
        thread = thread_of(session, env.get("LOAM_AGENT") or None, state)
        if thread:
            out["thread"] = thread
    return out


# ---------------------------------------------------------------------------
# The waiter.
# ---------------------------------------------------------------------------

def _path(job_id: str) -> Path:
    return STATE / f"{job_id}.json"


def _start_ticks(pid: int, proc: Path = PROC) -> str | None:
    try:
        stat = (proc / str(pid) / "stat").read_text(encoding="utf-8")
        return stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def register(job_id: str, project: str, job_dir: Path, persona: str,
             caller_info: dict, pid: int | None = None) -> Path | None:
    """The outer's record that a call is waiting on this job. Fail-soft: a
    record that cannot be written costs only the redelivery."""
    pid = os.getpid() if pid is None else pid
    record = {"job": job_id, "project": project, "job_dir": str(job_dir),
              "persona": persona, "pid": pid, "start": _start_ticks(pid),
              "since": now_iso(), **caller_info}
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        tmp = _path(job_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(record) + "\n", encoding="utf-8")
        tmp.replace(_path(job_id))
        return _path(job_id)
    except OSError:
        return None


def settle(job_id: str) -> None:
    """The call handed a final verdict back: nothing is owed."""
    try:
        _path(job_id).unlink()
    except OSError:
        pass


def waiter_alive(record: dict, proc: Path = PROC) -> bool:
    pid = record.get("pid")
    if not isinstance(pid, int) or pid <= 1:
        return False
    start = _start_ticks(pid, proc)
    return start is not None and (record.get("start") in (None, start))


def records() -> list[dict]:
    out = []
    try:
        paths = sorted(STATE.glob("*.json"))
    except OSError:
        return out
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and JOB_ID_RE.match(str(record.get("job") or "")):
            out.append(record)
    return out


def owed(proc: Path = PROC) -> list[dict]:
    """Records whose waiter is gone and whose job has a verdict, each with
    its `result` read in."""
    out = []
    for record in records():
        if waiter_alive(record, proc):
            continue
        try:
            result = json.loads((Path(record["job_dir"]) / "result.json")
                                .read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            continue
        if not isinstance(result, dict) or result.get("stage") == "queued":
            continue
        out.append({**record, "result": result})
    return out


def done(record: dict, outcome: str, **extra) -> None:
    """A record dealt with: logged, and removed."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": now_iso(), "job": record.get("job"),
                                     "thread": record.get("thread"),
                                     "persona": record.get("persona"),
                                     "outcome": outcome, **extra}) + "\n")
    except OSError:
        pass
    settle(str(record.get("job")))


# What a finished job's state reads as in the thread's one line.
LANDED_WORDS = {"landed": "landed", "landed_earlier": "landed",
                "not_landed": "did not land"}


def status_words(result: dict) -> str:
    """``landed``, ``did not land``, or the job's outcome (``completed``,
    ``failed``, ...) when it had nothing to land."""
    state = LANDED_WORDS.get(str(result.get("landing_state") or ""))
    return state or str(result.get("outcome") or result.get("stage") or "finished")


def receipt_path(record: dict) -> str:
    """Where the persona reads the receipt: its vault copy, else the job's
    own ``receipt.md``."""
    result = record.get("result") or {}
    return str(result.get("vault_receipt")
               or Path(str(record.get("job_dir") or "")) / "receipt.md")


def delivery_text(record: dict, title: str | None = None,
                  who: str | None = None) -> str:
    """The one plain line posted into the thread (2026-11-12, the owner 11:25
    and 11:52 PT: "stop giving me these receipts", "Please do not send me
    this stuff"): what finished and whether it landed. No receipt body, no
    job id, no path -- the persona reads the receipt and says it in its own
    words (``handoff_text``)."""
    who = who or str(record.get("persona") or "a persona").capitalize()
    what = f": {title.strip()}" if title and title.strip() else ""
    return (f"An engineer job {who} started finished{what} "
            f"({status_words(record.get('result') or {})}).")


def handoff_text(record: dict, title: str | None = None) -> str:
    """The note the briefing persona's delivery turn carries, after the
    thread: which job, where its receipt is, and what to do with it."""
    result = record.get("result") or {}
    named = f" (\"{title.strip()}\")" if title and title.strip() else ""
    return (f"[Loam: the engineer job {record.get('job')}{named} you briefed "
            f"finished ({status_words(result)}) after the call that was waiting "
            f"on it had ended, so its receipt never reached you. The thread was "
            f"told in one line only. Read the receipt at {receipt_path(record)} "
            f"and tell {lc.OWNER_NAME} what came of it in your own words, briefly: what "
            f"changed, what is left, anything he must decide. Do not paste the "
            f"receipt, its ids or its paths into the thread.]")


def pending_for(thread_id: str | None, persona: str | None) -> bool:
    """A delivery is still to come into this thread for this persona: a
    waiting call's record names them and its job is still on disk. A turn
    the broker's restart cut off then posts no "turn ended" line, because
    the delivery line follows (2026-11-12)."""
    if not thread_id or not persona:
        return False
    for record in records():
        if (record.get("thread") == thread_id and record.get("persona") == persona
                and Path(str(record.get("job_dir") or "/nonexistent")).is_dir()):
            return True
    return False
