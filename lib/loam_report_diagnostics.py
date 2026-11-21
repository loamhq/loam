"""Bounded runtime evidence. Only selected machine fields can enter a report."""

import json
import os
import platform
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import loam_config as lc
import loam_lane_notice
import loam_waiters

def instant(value):
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError("--at needs an ISO timestamp with its time zone")
    return at.astimezone(timezone.utc)


LIMIT = 4096
STAGES = {"preflight", "identity", "invoke", "account_refused", "lock_timeout",
          "crash", "died_mid_run", "cancelled", "landing", "session", "timeout"}


def bounded(text, size):
    data = text.encode()
    return text if len(data) <= size else data[:size - 4].decode(errors="ignore") + " …"


def metadata(path):
    try:
        with path.open("rb") as f:
            data = json.loads(f.read(65536))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def context():
    import loam_broker
    import loam_lock

    lane_raw = os.getenv("LOAM_WAKE", "")
    lane = loam_waiters.lane_kind(lane_raw)
    agent = os.getenv("LOAM_AGENT") or Path(os.getenv("CLAUDE_PROJECT_DIR") or os.getcwd()).name
    session = os.getenv("LOAM_SESSION_ID", "")
    job = lane_raw.partition(":")[2]
    info = {}
    if lane == "job" and loam_waiters.JOB_ID_RE.fullmatch(job):
        info = metadata(lc.JOBS / job / "engine.json")
    elif lane == "conversation" and re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", session):
        info = metadata(loam_broker.SESSION_DIR / f"{session}.json")
        agent = info.get("agent") or agent
    elif re.fullmatch(r"[a-z0-9_-]+", agent):
        info = loam_lock._read_holder(loam_lock.LOCK_DIR / f"agent-{agent}.lock") or {}
    return ("engineer" if lane == "job" else lane, agent, info, job)


def status():
    try:
        proc = subprocess.run([str(lc.ROOT / "bin/loam-status"), "--json"],
                              capture_output=True, timeout=18)
        data = json.loads(proc.stdout)
        return ", ".join(f"{row['key']} {row['state']}" for row in data["parts"]
                         if re.fullmatch(r"[a-z-]{1,32}", row["key"])
                         and row["state"] in {"ok", "problem", "waiting"}) or "unavailable"
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return "unavailable"


def plugins():
    import loam_plugins
    try:
        rows = loam_plugins._rows()
        return ", ".join(f"{r['id']} {r.get('version') or 'unknown'}"
                         for r in rows if r["effective"]) or "none"
    except (ValueError, OSError, KeyError):
        return "unavailable"


def errors(lane, agent, job, now):
    # Do not copy free-form errors, stderr, prompts, note names or log messages.
    # A runtime failure row can contain the complete input alongside its exit code.
    lines = []
    path = None
    if lane == "engineer":
        path = lc.LOGS / "engineer.jsonl"
    elif lane in {"wake", "cron"} and re.fullmatch(r"[a-z0-9_-]+", agent):
        path = lc.LOGS / f"wake-{agent}.jsonl"
    for row in loam_lane_notice._tail_rows(path) if path else ():
        ts = row.get("finished") or row.get("ts")
        try:
            at = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            age = (now - at).total_seconds()
        except (ValueError, TypeError):
            continue
        if not 0 <= age <= 3600 or row.get("ok") is not False:
            continue
        if lane == "engineer" and row.get("job_id") != job:
            continue
        if lane != "engineer" and row.get("lane", "reflection") != os.getenv("LOAM_WAKE"):
            continue
        stage = row.get("stage") or row.get("landing")
        stage = stage if isinstance(stage, str) and stage in STAGES else "run failed"
        code = row.get("returncode")
        lines.append(f"{at.astimezone(timezone.utc):%H:%M UTC} {stage}" + (f" (exit {code})" if type(code) is int else ""))
    # Broker failures also affect a running conversation or unfinished job.
    # Select machine-generated HTTP error codes and exception classes only.
    try:
        proc = subprocess.run(["journalctl", "--user", "-u", "loam-session-broker.service",
                               "-u", "loam-session-host.service", "--since",
                               f"@{int((now - timedelta(hours=1)).timestamp())}",
                               "--until", f"@{int(now.timestamp())}", "-n", "80",
                               "-o", "cat", "--no-pager"],
                              capture_output=True, timeout=3)
        if proc.returncode:
            lines.append("broker journal unavailable")
        for line in proc.stdout.decode(errors="replace").splitlines():
            match = re.search(r'HTTP/1\.[01]" ([45]\d\d)\b', line)
            if match:
                lines.append(f"broker HTTP {match[1]}")
            elif (match := re.match(r"^(TimeoutError|ConnectionError|PermissionError|"
                                    r"FileNotFoundError|BrokenPipeError):", line)):
                lines.append(f"broker {match[1]}")
    except (OSError, subprocess.TimeoutExpired):
        lines.append("broker journal unavailable")
    return "\n".join(lines[-5:]) or "no matching failures in the last hour"


def collect(at=None):
    import loam_update

    lane, agent, info, job = context()
    try:
        cur = loam_update.current()
        changes = "yes" if cur["local_commits"] or loam_update.dirt(cur["kind"]) else "no"
    except (OSError, ValueError, RuntimeError, loam_update.Refusal, subprocess.SubprocessError):
        cur, changes = {}, "unknown"
    try:
        system = platform.freedesktop_os_release().get("PRETTY_NAME") or platform.system()
    except OSError:
        system = platform.system()
    return bounded("\n".join([
        "### Diagnostics",
        f"Loam {cur.get('version') or 'unknown'}, commit {cur.get('commit') or 'unknown'}",
        f"Local changes since release: {changes}",
        f"OS: {system}",
        f"Provider/model: {info.get('provider') or info.get('engine') or 'unknown'} / "
        f"{info.get('model') or 'unknown'}",
        f"Lane: {lane}",
        "Status: " + bounded(status(), 512),
        "Plugins: " + bounded(plugins(), 1750),
        "Recent errors (UTC): " + errors(lane, agent, job, at or datetime.now(timezone.utc)),
    ]), LIMIT)
