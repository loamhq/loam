"""A persona's failed scheduled run, told to it once in its next conversation.

Until 2026-11-18 a failed or refused wake left its note in the failure log
and comms -- the owner's places -- and in the file the next WAKE reads. One
persona spent two days in conversation without knowing its reflection was
dark, while six notes about it existed. So: when the last run of any of a
persona's scheduled lanes (its reflection, each cron) did not run to the
end, the next thread turn the broker starts for it, on any provider and in
any thread, carries one line saying so -- which run, when, the cause as
recorded, and the file to read.

**Once.** A failure is told once, in whichever turn comes first; the
failure's own stamp is kept in ``STATE/lane-notice/<agent>.json`` as told.
**Cleared by the next good run of that lane**: the line is read from the
lane's LAST row in ``logs/wake-<agent>.jsonl``, so once a run of it finishes
there is nothing to tell. A new failure after that is a new stamp, and is
told in its turn.

The source is the wake's own log because every outcome of a lane lands
there: bin/wake's own lines (preflight, identity, invoke, account_refused,
lock_timeout, crash) and bin/wake-failed's for a run that was killed. A row
with ``ok: true`` is a run that finished; ``not_enabled`` is a fire that was
not a run, and is passed over. Stdlib and loam_config only: this runs in the
broker on every turn, and nothing here may fail one -- every error reads as
nothing to tell.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import loam_config as lc

LOGS = Path(os.environ.get("LOAM_LANE_NOTICE_LOGS") or lc.LOGS)
STATE = Path(os.environ.get("LOAM_LANE_NOTICE_STATE") or lc.STATE / "lane-notice")
# Enough of the log's tail to hold the last row of every lane an agent has:
# a night writes one row per lane, and the rows are under 4 KB.
TAIL_BYTES = 256 * 1024
CAUSE_CHARS = 300
PASSED_OVER = {"not_enabled"}


def _tail_rows(path: Path) -> list[dict]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - TAIL_BYTES))
            data = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = data.splitlines()
    if len(data.encode("utf-8", "replace")) >= TAIL_BYTES and lines:
        lines = lines[1:]  # the first line may be cut
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def last_runs(agent: str, logs: Path | None = None) -> dict[str, dict]:
    """Each lane's last row that was a run, by lane name."""
    out: dict[str, dict] = {}
    for row in _tail_rows((logs or LOGS) / f"wake-{agent}.jsonl"):
        if row.get("stage") in PASSED_OVER:
            continue
        out[str(row.get("lane") or "reflection")] = row
    return out


def lane_label(lane: str) -> str:
    if lane == "reflection":
        return "nightly reflection wake"
    if lane.startswith("cron:"):
        return f"cron `{lane[5:]}`"
    return lane


def cause_of(row: dict) -> str:
    stage = row.get("stage") or "failed"
    said = (row.get("error") or row.get("stderr") or "").strip()
    if not said and stage == "died_mid_run":
        said = f"killed (systemd result {row.get('systemd_result')})"
    if not said and stage == "lock_timeout":
        said = "skipped: another run of yours was live, or the box had no memory"
    if not said and row.get("returncode") is not None:
        said = f"exit {row.get('returncode')}"
    said = " ".join(said.split())
    if len(said) > CAUSE_CHARS:
        said = said[:CAUSE_CHARS].rstrip() + "…"
    return f"{stage}: {said}" if said else stage


def failure_log() -> str:
    try:
        import loam_inbox  # noqa: PLC0415
        return str(lc.VAULT / loam_inbox.FAILURE_LOG)
    except Exception:  # noqa: BLE001
        return "the failure log (loam-wake-failures.md)"


def pending(agent: str, logs: Path | None = None,
            state: Path | None = None) -> list[tuple[str, dict]]:
    """The lanes whose last run failed and has not been told yet."""
    import loam_claim_start
    since = loam_claim_start.claimed_at()
    told = _told(agent, state)
    return [(lane, row) for lane, row in sorted(last_runs(agent, logs).items())
            if row.get("ok") is False and str(row.get("ts") or "")[:19] >= since
            and told.get(lane) != str(row.get("ts"))]


def line(agent: str, items: list[tuple[str, dict]],
         logs: Path | None = None) -> str:
    """The one line: every untold failure, joined."""
    parts = [f"your last {lane_label(lane)} ({row.get('ts')}) did not run to "
             f"the end -- {cause_of(row)}" for lane, row in items]
    log = (logs or LOGS) / f"wake-{agent}.jsonl"
    return (f"[Loam: {'; '.join(parts)}. The record is in {failure_log()}, "
            f"and the run's own line in {log}. This is said once; the next "
            f"good run of that lane clears it.]")


def take(agent: str, logs: Path | None = None,
         state: Path | None = None) -> str | None:
    """The line for this agent's next turn, marked told; None if nothing."""
    if logs is None and _scratch_world():
        return None
    try:
        items = pending(agent, logs, state)
        if not items:
            return None
        text = line(agent, items, logs)
        told = _told(agent, state)
        told.update({lane: str(row.get("ts")) for lane, row in items})
        where = (state or STATE)
        where.mkdir(parents=True, exist_ok=True)
        (where / f"{agent}.json").write_text(json.dumps(told, indent=1) + "\n",
                                             encoding="utf-8")
        return text
    except Exception:  # noqa: BLE001 - never fails a turn
        return None


def _scratch_world() -> bool:
    """A broker pointed at scratch workspaces (the thread tests do this with
    LOAM_AI_LABS) is not the box's: the live logs are not its personas', and
    a test persona sharing a live one's name must not be told -- or mark
    told -- the live one's failure. Unless the logs were pointed too."""
    scratch = os.environ.get("LOAM_AI_LABS")
    return (bool(scratch) and not os.environ.get("LOAM_LANE_NOTICE_LOGS")
            and Path(scratch) != Path(lc.WORKSPACES))


def _told(agent: str, state: Path | None = None) -> dict:
    try:
        data = json.loads(((state or STATE) / f"{agent}.json")
                          .read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
