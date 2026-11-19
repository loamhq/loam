"""A result for a run with no thread: a new run of the persona that asked
(2026-11-19, batch B step 3).

Every brief returns at once. A brief from a thread gets its result back as
a turn in that thread (the broker's, lib/loam_waiters.py). A brief from a
wake, a cron, or a result run has no thread, so its result comes back by
the route this file names: when the job has a verdict, ``bin/wake <workspace>
--results`` starts, takes the agent's run lock and the box's memory admission
like any run, and hands the result to the persona as the input of a new run.

**Resumed when it can be.** The asking run's CLI session is resumed
(``claude -p --resume <id>``: the same session id, the whole run in front of
the persona, measured 2026-11-19 on 2.1.328), with the asker's own lane
loaded again (its cron's file, or ``_reflection.md``). bin/wake gives every
Claude run a ``--session-id`` and ``LOAM_SESSION_ID`` for this, and the
launcher records it in the brief (``loam_waiters.caller``).

**Fresh when it cannot**: no session recorded (a Codex wake keeps its thread
id where the brief cannot read it; a conversation the broker could not place
in a thread; a record from before this change), the session's file is gone,
or the CLI refused the resume. The fresh run's input (``fresh_text``) is the
result, what the asker was doing (its lane, the instruction file that lane
runs, when it asked), the brief the persona wrote, word for word up to
``BRIEF_CHARS``, and the brief's path. Never a run that resumes a session
the broker may be holding.

**Once, and in order.** One drain per agent at a time (the unit
``loam-results-<agent>``; the run lock besides), oldest verdict first, one
run per result. The record is marked ``given`` just before the model starts
and removed when the run ends; a ``given`` record found later belonged to a
run that died after the input went in, and is logged and reported, not
given twice (the same rule as a thread's sent result, step 2).

**Started from three places**, none of them the broker: the job's own
launcher the moment it writes the verdict (``kick_for``), the recovery
sweep every five minutes (``kick_all``: a kick that met a drain still
running, a drain that could not get the lock), and the broker when a
thread's result finds its thread gone (``to_headless``).

**No identity gate.** A result run does not ask the canary question a wake
asks before it starts: it is one more turn of a session that already ran
under that gate (or, fresh, of a lane that does on its next fire), and the
question would cost a model call per result. Its lane's file is loaded as
the asker's was.

**Silent.** The run posts nothing; the persona speaks only if it chooses to,
by the ordinary routes. Its record is its row in ``logs/wake-<agent>.jsonl``
(lane ``result:<job>``) and a fire in the schedule log, separate from the
run that asked.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import loam_config as lc
import loam_waiters as lw

UNIT_PREFIX = "loam-results-"
# How much of the brief a fresh run is handed inline; the rest is a path.
BRIEF_CHARS = 4000


def unit_name(agent: str) -> str:
    return f"{UNIT_PREFIX}{agent}"


def workspace(agent: str) -> Path | None:
    root = Path(os.environ.get("LOAM_AI_LABS") or lc.WORKSPACES)
    path = root / agent
    return path if (path / "agent.json").is_file() else None


def kicks_enabled() -> bool:
    """Only for the install's own records. A test with a scratch
    ``LOAM_WAITERS_DIR`` would otherwise start a real unit that drains the
    real records; ``LOAM_RESULTS_KICK=1`` lets a proof that means it."""
    said = os.environ.get("LOAM_RESULTS_KICK")
    if said is not None:
        return said == "1"
    return not os.environ.get("LOAM_WAITERS_DIR")


def kick(agent: str) -> str:
    """Start this agent's drain, unless one is running. Returns what
    happened: ``started``, ``running`` (a drain is under way and re-reads
    the records after each run; the sweep catches the rest), ``off``, or
    ``failed: ...``. Never raises."""
    if not kicks_enabled():
        return "off"
    ws = workspace(agent)
    if ws is None:
        return "failed: no workspace"
    argv = [
        "systemd-run", "--user", "--collect", "--no-block",
        f"--unit={unit_name(agent)}",
        f"--description=Loam results for {agent}",
        "--property=Type=oneshot",
        f"--property=OOMScoreAdjust={lc.AGENT_OOM_SCORE_ADJ}",
        f"--property=WorkingDirectory={ws}",
        f"--property=EnvironmentFile=-{lc.WAKE_ENV}",
        "--property=TimeoutStartSec=infinity",
        "--setenv=DISABLE_AUTOUPDATER=1",
        f"--setenv=PATH={lc.UNIT_PATH}",
        *([f"--setenv=LOAM_WAITERS_DIR={lw.STATE}"]
          if os.environ.get("LOAM_WAITERS_DIR") else []),
        *([f"--setenv=LOAM_CONFIG={os.environ['LOAM_CONFIG']}"]
          if os.environ.get("LOAM_CONFIG") else []),
        str(lc.ROOT / "bin" / "wake"), str(ws), "--results",
    ]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, check=False,
                             timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"failed: {exc}"
    if out.returncode == 0:
        return "started"
    said = (out.stderr or "").strip()
    if "already" in said.lower() or "exists" in said.lower():
        return "running"
    return f"failed: {said[:200]}"


def kick_for(job_id: str) -> str | None:
    """A job just wrote its verdict: start its asker's drain if its record
    goes by a run. None when there is nothing to start."""
    try:
        record = next((r for r in lw.records() if r.get("job") == job_id), None)
    except Exception:  # noqa: BLE001
        return None
    if not record or lw.route(record) != "run" or not record.get("persona"):
        return None
    return kick(str(record["persona"]))


def kick_all() -> dict:
    """The sweep's backstop: every agent owed a result by a run."""
    agents = sorted({str(r.get("persona")) for r in lw.owed(by="run")
                     if r.get("persona")})
    return {agent: kick(agent) for agent in agents}


def to_headless(record: dict, why: str) -> str:
    """A thread result the broker could not deliver (its thread is gone,
    or the persona left it): it goes by a run instead, never dropped."""
    record = {k: v for k, v in record.items() if k != "result"}
    record.update(headless=True, headless_why=why)
    lw.write(record)
    return kick(str(record.get("persona") or ""))


# ---------------------------------------------------------------------------
# What the persona is handed.
# ---------------------------------------------------------------------------

def _title(record: dict) -> str | None:
    try:
        from loam_memory import front_matter  # noqa: PLC0415
        head = front_matter((Path(record["job_dir"]) / "brief.md")
                            .read_text(encoding="utf-8"))
        return str(head.get("title") or "").strip() or None
    except (OSError, KeyError):
        return None


def _brief_body(record: dict) -> tuple[str, Path]:
    path = Path(str(record.get("job_dir") or "")) / "brief.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "", path
    from loam_memory import strip_frontmatter  # noqa: PLC0415
    return strip_frontmatter(text).strip(), path


def _finished(record: dict) -> str:
    result = record.get("result") or {}
    if not result.get("finished"):
        return ""
    import loam_clock  # noqa: PLC0415
    return f" at {loam_clock.local_stamp(str(result['finished']))}"


def _verdict(record: dict) -> str:
    result = record.get("result") or {}
    return (f"Receipt: {lw.receipt_path(record)}. Launcher's verdict: "
            f"{lw.verdict_line(result)}. Landing: {lw.status_words(result)}.")


SILENT = ("Nobody is in a conversation and nothing has been posted anywhere. "
          "Read the receipt if you need it and do what it calls for. If "
          f"{lc.OWNER_NAME} needs to hear of it, tell him by the ordinary "
          "routes, in your own words; if not, stop without writing anything.")


def lane_words(lane: str | None) -> str:
    lane = lane or ""
    if lane == "reflection":
        return "your reflection wake"
    if lane.startswith("cron:"):
        return f"your cron `{lane[5:]}`"
    if lane.startswith("result:"):
        return "a run that was handed an earlier result"
    return "a conversation" if not lane or lane == "conversation" else f"your run `{lane}`"


def resumed_text(record: dict) -> str:
    """The input of a run that resumed the asking session."""
    title = _title(record)
    named = f" (\"{title}\")" if title else ""
    return (f"[Loam: the engineer job {record.get('job')}{named} you briefed "
            f"earlier in this session has finished{_finished(record)}. This is "
            f"a new run that resumed your session to hand you its result. "
            f"{_verdict(record)} {SILENT}]")


def fresh_text(record: dict, why: str) -> str:
    """The input of a fresh run, when the asking session cannot be resumed:
    the result, and enough of what the asker was doing to act on it."""
    import loam_clock  # noqa: PLC0415
    title = _title(record)
    named = f" (\"{title}\")" if title else ""
    lane = record.get("caller_lane") or record.get("caller") or ""
    asked = record.get("since")
    when = f" at {loam_clock.local_stamp(str(asked))}" if asked else ""
    where = ""
    if str(lane).startswith("cron:"):
        where = (f" That cron's instructions are `crons/{str(lane)[5:]}.md` in your "
                 f"workspace, and they are loaded for this run.")
    thread = ""
    if record.get("headless"):
        thread = (f" It was briefed from thread {record.get('thread')}, which "
                  f"could not take it ({record.get('headless_why')}).")
    body, path = _brief_body(record)
    if len(body) > BRIEF_CHARS:
        body = body[:BRIEF_CHARS].rstrip() + f"\n[... the rest is in {path}]"
    return (f"[Loam: the engineer job {record.get('job')}{named} you briefed "
            f"from {lane_words(str(lane))}{when} has finished{_finished(record)}. "
            f"That run's session could not be resumed ({why}), so this is a "
            f"fresh run carrying the result.{thread}{where} {_verdict(record)} "
            f"{SILENT}]\n\nThe brief you sent ({path}):\n\n{body}\n")


def project_slug(project: Path) -> str:
    """The CLI's folder name for a working directory's sessions."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(project))


def resumable(record: dict, project: Path, provider: str) -> tuple[str | None, str]:
    """(session id to resume, or None; why not). Only a Claude Code
    session that bin/wake started in this workspace, whose file is still
    there; never a conversation, which the broker may hold."""
    session = record.get("caller_session")
    if provider != "claude-code":
        return None, "this persona's runs are on Codex, and its session id was not recorded"
    if record.get("caller") == "conversation" or record.get("headless"):
        return None, "it was a conversation's, which the session host holds"
    if not session:
        return None, "the asking run recorded no session"
    import loam_session  # noqa: PLC0415
    found = loam_session.session_file(str(session))
    if found is None or found.parent.name != project_slug(project):
        return None, "its session file is gone"
    return str(session), ""
