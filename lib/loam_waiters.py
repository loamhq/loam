"""Persist who briefed a job and where its result is owed. The broker delivers thread
results and bin/wake handles threadless ones. Remove pending records after delivery
and record each outcome in the delivery log.
"""

from __future__ import annotations

import json
import os
import re
import time
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
    """Record the calling lane and whichever session and thread IDs are known when the job
    is accepted.
    """
    env = os.environ if env is None else env
    lane = env.get("LOAM_WAKE") or ""
    kind = lane_kind(lane)
    if kind == "job":
        return _job_caller(lane.split(":", 1)[1])
    out: dict = {"caller": kind}
    if kind != "conversation":
        out["caller_lane"] = lane
    session = calling_session(env, pid, proc)
    if session:
        out["caller_session"] = session
        if kind == "conversation":
            thread = thread_of(session, env.get("LOAM_AGENT") or None, state)
            if thread:
                out["thread"] = thread
    return out


CALLER_FIELDS = ("caller", "caller_session", "thread", "caller_lane")


def _job_caller(job_id: str) -> dict:
    """When an engineer job briefs another job, inherit the original persona's delivery
    address from the calling job's brief.
    """
    out: dict = {"caller": "job", "asked_by_job": job_id}
    try:
        import loam_engineer as eng  # noqa: PLC0415
        for project in eng.projects().values():
            brief = project.jobs / job_id / "brief.md"
            if brief.is_file():
                from loam_memory import front_matter  # noqa: PLC0415
                head = front_matter(brief.read_text(encoding="utf-8"))
                for key in ("caller_session", "thread", "caller_lane"):
                    if head.get(key):
                        out[key] = str(head[key])
                break
    except Exception:  # noqa: BLE001 -- a brief never fails for want of it
        pass
    return out


# ---------------------------------------------------------------------------
# What is owed.
# ---------------------------------------------------------------------------

# A run's records have a folder of their own (2026-11-19): a broker that
# predates the run route reads only the top folder, and would otherwise
# take a cron's result for a thread's and drop it as "no thread".
RUNS = STATE / "runs"


def _path(job_id: str) -> Path:
    """Where this job's record is: in ``RUNS`` if it is there, else the top."""
    run = RUNS / f"{job_id}.json"
    return run if run.exists() else STATE / f"{job_id}.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write(record: dict) -> Path | None:
    """The record, replaced whole, in its route's folder. Fail-soft."""
    try:
        job = str(record["job"])
        home, other = ((RUNS, STATE) if route(record) == "run" else (STATE, RUNS))
        home.mkdir(parents=True, exist_ok=True)
        path = home / f"{job}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: v for k, v in record.items()
                                   if k != "result"}) + "\n", encoding="utf-8")
        tmp.replace(path)
        (other / f"{job}.json").unlink(missing_ok=True)
        return path
    except (OSError, KeyError):
        return None


def register(job_id: str, project: str, job_dir: Path, persona: str,
             caller_info: dict) -> Path | None:
    """The record that a result is owed for this job, written at accept.
    Fail-soft: a record that cannot be written costs only the delivery."""
    return write({"job": job_id, "project": project, "job_dir": str(job_dir),
                  "persona": persona, "since": now_iso(),
                  # To the fraction (2026-11-19): what tells the turn that
                  # briefed a job from one that started after (the broker's
                  # ThreadEngine.joins).
                  "since_ts": time.time(), "returned": True, **caller_info})


def settle(job_id: str) -> None:
    """Nothing is owed (the brief was refused, did not launch, or its
    result was delivered)."""
    for folder in (STATE, RUNS):
        try:
            (folder / f"{job_id}.json").unlink()
        except OSError:
            pass


def records() -> list[dict]:
    out = []
    try:
        paths = sorted([*STATE.glob("*.json"), *RUNS.glob("*.json")])
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


def route(record: dict) -> str:
    """``thread``: the broker delivers it as a turn in that thread. ``run``:
    bin/wake delivers it as a run of the persona (lib/loam_results.py) -- a
    caller with no thread, or one whose thread the broker found gone
    (``headless``)."""
    return "thread" if record.get("thread") and not record.get("headless") else "run"


def result_of(record: dict) -> dict | None:
    """The job's verdict, or None while it has none."""
    try:
        result = json.loads((Path(record["job_dir"]) / "result.json")
                            .read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return None
    if not isinstance(result, dict) or result.get("stage") == "queued":
        return None
    return result


def owed(by: str | None = "thread", persona: str | None = None) -> list[dict]:
    """Records whose job has a verdict, each with its `result` read in,
    oldest first by when the job finished. ``by`` is the route (``thread``,
    ``run``, or None for both); ``persona`` narrows to one asker."""
    out = []
    for record in records():
        if by and route(record) != by:
            continue
        if persona and record.get("persona") != persona:
            continue
        result = result_of(record)
        if result is None:
            continue
        out.append({**record, "result": result})
    return sorted(out, key=lambda r: (str(r["result"].get("finished") or ""),
                                      str(r.get("job"))))


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


# What a finished job's landing reads as in its delivery.
LANDED_WORDS = {"landed": "landed", "landed_earlier": "landed",
                "not_landed": "did not land", "nothing": "nothing to land",
                "read_only": "read-only, nothing landed",
                "pull_request": "opened a pull request"}


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


def verdict_line(result: dict, limit: int = 240) -> str:
    """The launcher's verdict in one line: the outcome, and the landing's
    own sentence when there was one, else the first line of what the
    launcher said (a refusal, a death)."""
    outcome = str(result.get("outcome") or result.get("stage") or "finished")
    said = str(result.get("landing") or "").strip()
    if not said and result.get("stage") != "done":
        said = next((ln.strip() for ln in str(result.get("receipt_text") or "")
                     .splitlines() if ln.strip() and not ln.startswith(("---", "#"))), "")
    said = re.sub(r"[*`]", "", " ".join(said.split()))
    line = f"{outcome}: {said}" if said else outcome
    return line if len(line) <= limit else line[:limit - 1].rstrip() + "…"


def handoff_text(record: dict, title: str | None = None) -> str:
    """Build the private delivery-turn input with the job ID, receipt location, verdict and
    landing status. Posting a public reply is the persona's choice.
    """
    result = record.get("result") or {}
    named = f" (\"{title.strip()}\")" if title and title.strip() else ""
    # When it finished (2026-11-19): a result given after a long outage says
    # how old it is.
    when = ""
    if result.get("finished"):
        import loam_clock  # noqa: PLC0415
        when = f" at {loam_clock.local_stamp(str(result['finished']))}"
    return (f"[Loam: the engineer job {record.get('job')}{named} you briefed "
            f"from this thread has finished{when}. Nothing was posted in the thread. "
            f"Receipt: {receipt_path(record)}. Launcher's verdict: "
            f"{verdict_line(result)}. Landing: {status_words(result)}. "
            f"Read the receipt if you need it. Tell {lc.OWNER_NAME} only what they "
            f"need to hear, in your own words and without the receipt's ids or "
            f"paths; if nothing, end your turn without writing anything and "
            f"nothing is posted.]")
