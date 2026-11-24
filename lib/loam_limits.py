"""Scaled memory admission sizing and the durable record of limit events.

One stdlib-only authority for two tightly coupled jobs:

1. **Scaled admission sizing.** Boxes of 16 GB+ usable RAM (> 12,288 MB) keep
   the original ``max(2048, 12%)`` reserve and ``450 MB`` start allowance
   unchanged (3,760 MB reserve on 31,335 MB; 2,048 MB on 16 GB). Smaller
   servers (2, 4 and 8 GB) scale the reserve to ``256 MB + 12% of RAM`` and
   use a ``350 MB`` start allowance matching post-stdlib-MCP session footprints.
   ``lib/loam_broker.py`` and ``lib/loam_lock.py`` both read from here so chat
   and run admission never diverge.

2. **Durable, countable limit events.** Whenever a resource limit bites
   (a chat waiting or timing out for room, an idle chat evicted to make room,
   a wake/cron/review waiting or skipped for memory, an engineer job queued
   for memory, an OOM kill, a memory pressure episode, sustained swap use, or
   a private handoff deferred/cancelled for memory), one structured JSON line
   is appended under ``<state>/limits/events.jsonl`` (on the data disk when
   ``--data-dir`` is used). No message content or private prose is ever
   recorded — only IDs, durations, outcomes and the memory picture.

   Bounded in size by rotating ``events.jsonl`` to ``events.jsonl.1`` at
   ``MAX_BYTES`` (2 MiB, so 4 MiB maximum total). One read path (``query()``,
   exposed through ``loam limits``, ``GET /v1/limit-events``, and
   ``mcp__engineer__limits``) answers counts by kind plus raw events for any
   window in days.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

# -- admission sizing ---------------------------------------------------------

# Boundary matching lib/loam_broker.py IDLE_TIERS_MB: above 12,288 MB (12 GiB)
# sits 16 GB+ servers, whose reserve and start allowance stay unchanged.
LARGE_BOX_MB = 12288
LARGE_RESERVE_FLOOR_MB = 2048
SMALL_RESERVE_FLOOR_MB = 256
RESERVE_PCT = 12
LARGE_START_MB = 450
SMALL_START_MB = 350

# Installer preflight memory floor (MB of usable MemTotal): admits 2 GB VMs
# (~1,800-2,048 MB usable after kernel/firmware reservation) and rejects 1 GB.
MIN_INSTALL_MEM_MB = 1700
RECOMMENDED_MEM_MB = 3600


def read_meminfo(path: Path | str = "/proc/meminfo") -> dict[str, int]:
    """Read ``/proc/meminfo`` values in integer MiB (``kB // 1024``)."""
    out: dict[str, int] = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                out[key] = int(parts[0]) // 1024
    except OSError:
        pass
    return out


def read_swaps(path: Path | str = "/proc/swaps") -> list[dict]:
    """Parse ``/proc/swaps`` into structured rows (sizes in MiB)."""
    rows: list[dict] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            size_mb = int(parts[2]) // 1024
            used_mb = int(parts[3]) // 1024
            prio = int(parts[4])
        except ValueError:
            continue
        name = parts[0]
        rows.append({
            "name": name,
            "type": parts[1],
            "size_mb": size_mb,
            "used_mb": used_mb,
            "priority": prio,
            "is_zram": name.startswith("/dev/zram"),
        })
    return rows


def box_memory_mb(meminfo: Path | str = "/proc/meminfo") -> int:
    """Usable physical RAM (``MemTotal``) in MB, with 7750 MB fallback."""
    total = read_meminfo(meminfo).get("MemTotal")
    return total if isinstance(total, int) and total > 0 else 7750


def memory_reserve_mb(box_mb: int | None = None) -> int:
    """Memory kept free by admission (MB) for a server of ``box_mb`` usable RAM.

    - ``> 12,288 MB`` (16 GB+): ``max(2048, box_mb * 12 // 100)`` — identical
      to the pre-scaling rule (2,048 MB on 16 GB, 3,760 MB on 31,335 MB).
    - ``<= 12,288 MB`` (2, 4, 8 GB): ``256 + box_mb * 12 // 100`` — keeps a
      256 MB kernel/page-cache floor plus 12% of RAM for in-flight turn spikes
      and un-admitted services (~487 MB on 2 GB, ~718 MB on 4 GB, ~1,192 MB on
      8 GB).
    """
    mb = box_memory_mb() if box_mb is None else int(box_mb)
    if mb > LARGE_BOX_MB:
        return max(LARGE_RESERVE_FLOOR_MB, mb * RESERVE_PCT // 100)
    return SMALL_RESERVE_FLOOR_MB + mb * RESERVE_PCT // 100


def start_allowance_mb(box_mb: int | None = None) -> int:
    """Assumed startup memory (MB) per new chat or run while starting.

    - ``> 12,288 MB`` (16 GB+): ``450 MB`` (unchanged).
    - ``<= 12,288 MB`` (2, 4, 8 GB): ``350 MB`` (matching measured Claude Code
      + stdlib MCP resident/start footprint of ~250-310 MB PSS with headroom).
    """
    mb = box_memory_mb() if box_mb is None else int(box_mb)
    return LARGE_START_MB if mb > LARGE_BOX_MB else SMALL_START_MB


def admission_rule(box_mb: int | None = None) -> dict:
    """Structured description of the admission rule for ``box_mb``."""
    mb = box_memory_mb() if box_mb is None else int(box_mb)
    reserve = memory_reserve_mb(mb)
    start = start_allowance_mb(mb)
    return {
        "box_mb": mb,
        "reserve_mb": reserve,
        "start_mb": start,
        "min_available_mb": reserve + start,
        "scaled_small": mb <= LARGE_BOX_MB,
    }


def admission_rule_sentence(box_mb: int | None = None) -> str:
    """Short plain-language statement of the scaled admission rule."""
    if box_mb is None:
        return (
            "256 MB plus 12% of RAM kept free on servers under 16 GB "
            "(350 MB per start), or 2 GB / 12% of RAM kept free on 16 GB+ "
            "(450 MB per start)"
        )
    r = admission_rule(box_mb)
    return (
        f"{r['reserve_mb']} MB kept free plus {r['start_mb']} MB per start "
        f"on this {r['box_mb']} MB server"
    )


# -- limit-event record -------------------------------------------------------

VERSION = 1
MAX_BYTES = 2 * 1024 * 1024  # 2 MiB active + 2 MiB rotated (.1) = 4 MiB cap

KINDS = (
    "chat_wait",         # thread chat waited for room and started (or was cancelled)
    "chat_timeout",      # thread chat waited 5 min for room and gave up (NoSeat)
    "chat_evicted",      # idle chat unloaded to make room (not ordinary idle timeout)
    "run_wait",          # wake, cron, reviewer or delivery waited for memory and started
    "run_skipped",       # wake, cron, reviewer or delivery waited 20 min for memory and skipped
    "job_queued",        # engineer job queued for memory (started or cancelled)
    "oom_kill",          # chat, job, wake, cron or reviewer killed by kernel for memory
    "pressure_episode",  # sustained RAM >= 85% or swap < 15% free for 3+ min
    "sustained_swap",    # sustained swap usage above threshold for 5+ min
    "summary_deferred",  # private handoff summary deferred or cancelled for memory
)

# Explicit privacy allowlists: only IDs, enums and numbers ever enter the log.
SUBJECT_KEYS = ("agent", "thread", "session", "job", "project", "lane", "unit")
DETAIL_KEYS = (
    "reason", "trigger", "for_agent", "retries", "idle_s", "position",
    "oom_kills", "returncode", "source", "systemd_result", "heaviest",
    "ram_pct", "swap_free_pct", "resolved_available_mb",
    "had_memory_wait", "waited_for_queue", "provider",
)
ID_MAX_LEN = 128


def events_path() -> Path:
    """Path of the active limit-events JSONL file (on the data disk via lc.STATE)."""
    override = os.environ.get("LOAM_LIMIT_EVENTS")
    if override:
        return Path(override)
    import loam_config as lc  # noqa: PLC0415
    return lc.STATE / "limits" / "events.jsonl"


def now_iso(ts: float | None = None) -> str:
    val = time.time() if ts is None else ts
    return datetime.fromtimestamp(val, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(stamp: str | None) -> float | None:
    if not stamp or not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def snapshot_memory(
    *,
    meminfo_path: Path | str = "/proc/meminfo",
    swaps_path: Path | str = "/proc/swaps",
    total_mb: int | None = None,
    available_mb: int | None = None,
    reserve_mb: int | None = None,
    start_mb: int | None = None,
) -> dict:
    """Capture the box's memory picture in MB at the moment a limit bites."""
    mi = read_meminfo(meminfo_path)
    total = total_mb if total_mb is not None else mi.get("MemTotal")
    avail = available_mb if available_mb is not None else mi.get("MemAvailable")
    box_mb = total if isinstance(total, int) and total > 0 else box_memory_mb(meminfo_path)
    reserve = reserve_mb if reserve_mb is not None else memory_reserve_mb(box_mb)
    start = start_mb if start_mb is not None else start_allowance_mb(box_mb)
    swap_total = mi.get("SwapTotal", 0)
    swap_free = mi.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free)
    snap: dict = {
        "total_mb": total,
        "available_mb": avail,
        "reserve_mb": reserve,
        "start_mb": start,
        "swap_total_mb": swap_total,
        "swap_used_mb": swap_used,
        "swap_free_mb": swap_free,
    }
    swaps = read_swaps(swaps_path)
    if swaps:
        zram_used = sum(s["used_mb"] for s in swaps if s["is_zram"])
        disk_used = sum(s["used_mb"] for s in swaps if not s["is_zram"])
        if zram_used or any(s["is_zram"] for s in swaps):
            snap["swap_zram_used_mb"] = zram_used
            snap["swap_disk_used_mb"] = disk_used
    return snap


def _clean_str(value, max_len: int = ID_MAX_LEN) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    if not text:
        return None
    return text[:max_len]


def _sanitize_subject(raw: dict | None) -> dict:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in SUBJECT_KEYS:
        val = _clean_str(raw.get(key))
        if val is not None:
            out[key] = val
    return out


def _sanitize_detail(raw: dict | None) -> dict:
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key in DETAIL_KEYS:
        if key not in raw or raw[key] is None:
            continue
        val = raw[key]
        if isinstance(val, (bool, int)):
            out[key] = val
        elif isinstance(val, float):
            out[key] = round(val, 1)
        elif isinstance(val, str):
            cleaned = _clean_str(val, 160)
            if cleaned is not None:
                out[key] = cleaned
        elif key == "heaviest" and isinstance(val, list):
            items = [_clean_str(x, 80) for x in val[:5]]
            items = [x for x in items if x is not None]
            if items:
                out[key] = items
    return out


def _make_id(ts: float) -> str:
    stamp = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(4)}"


def record(
    kind: str,
    *,
    outcome: str | None = None,
    subject: dict | None = None,
    duration_s: float | None = None,
    started_at: str | None = None,
    detail: dict | None = None,
    memory: dict | None = None,
    event_id: str | None = None,
    ts: float | None = None,
    path: Path | None = None,
    max_bytes: int = MAX_BYTES,
) -> dict:
    """Append one structured limit event atomically under an flock, rotating at
    ``max_bytes`` to ``<file>.1``."""
    if kind not in KINDS:
        raise ValueError(f"unknown limit event kind {kind!r}; expected one of {KINDS}")
    at = time.time() if ts is None else float(ts)
    dur = None if duration_s is None else max(0.0, round(float(duration_s), 1))
    mem = memory if isinstance(memory, dict) else snapshot_memory()
    row: dict = {
        "v": VERSION,
        "id": _clean_str(event_id, 64) or _make_id(at),
        "ts": now_iso(at),
        "kind": kind,
        "outcome": _clean_str(outcome, 32),
        "duration_s": dur,
        "subject": _sanitize_subject(subject),
        "memory": mem,
    }
    if started_at:
        cleaned_start = _clean_str(started_at, 40)
        if cleaned_start:
            row["started_at"] = cleaned_start
    clean_detail = _sanitize_detail(detail)
    if clean_detail:
        row["detail"] = clean_detail

    target = path or events_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_file = target.with_name(target.name + ".lock")
    line = json.dumps(row, separators=(",", ":")) + "\n"
    with lock_file.open("a+", encoding="utf-8") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            try:
                if target.stat().st_size >= max_bytes:
                    target.replace(target.with_name(target.name + ".1"))
            except FileNotFoundError:
                pass
            with target.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
        finally:
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN)
    return row


def record_safe(kind: str, **kwargs) -> dict | None:
    """Best-effort ``record()``: never raises so limit logging cannot break a run."""
    try:
        return record(kind, **kwargs)
    except Exception:  # noqa: BLE001
        return None


# -- OOM detection helpers (for chats and cgroups) ----------------------------

def cgroup_oom_kills(cgroup_file: Path | str = "/proc/self/cgroup") -> int | None:
    """Read ``oom_kill`` from this process's cgroup v2 ``memory.events``."""
    try:
        rel = next(
            line.split("::", 1)[1].strip()
            for line in Path(cgroup_file).read_text(encoding="utf-8").splitlines()
            if line.startswith("0::")
        )
        events = Path("/sys/fs/cgroup") / rel.lstrip("/") / "memory.events"
        for line in events.read_text(encoding="utf-8").splitlines():
            key, _, val = line.partition(" ")
            if key == "oom_kill":
                return int(val.strip())
    except (OSError, ValueError, StopIteration):
        return None
    return None


def journal_oom_pid(pid: int | None, since_s: int = 45) -> bool:
    """Check recent kernel log lines for an OOM kill of ``pid``. Fail-soft."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        res = subprocess.run(
            ["journalctl", "-k", "--no-pager", "-o", "cat", f"--since=-{since_s}s"],
            capture_output=True, text=True, check=False, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if res.returncode != 0 or not res.stdout:
        return False
    pat = re.compile(rf"\bKilled process {pid}\b")
    for line in res.stdout.splitlines():
        if pat.search(line) and ("Out of memory" in line or "oom" in line.lower()
                                 or "Killed process" in line):
            return True
    return False


# -- single read path ---------------------------------------------------------

def _read_active_episodes(pressure_path: Path | None, now: float) -> list[dict]:
    """Surface currently ongoing pressure or sustained-swap episodes from
    ``pressure.json`` without writing duplicate lines to ``events.jsonl``."""
    if pressure_path is not None:
        path = pressure_path
    else:
        import loam_config as lc  # noqa: PLC0415
        path = lc.STATE / "pressure.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(state, dict):
        return []
    active: list[dict] = []
    if state.get("episode_since"):
        start_ts = parse_iso(state.get("episode_since"))
        active.append({
            "v": VERSION,
            "id": state.get("episode_id") or f"active-pressure-{state['episode_since']}",
            "ts": state.get("at") or now_iso(now),
            "started_at": state["episode_since"],
            "kind": "pressure_episode",
            "outcome": "active",
            "duration_s": round(max(0.0, now - start_ts), 1) if start_ts else None,
            "subject": {},
            "memory": state.get("episode_peak") or snapshot_memory(),
        })
    if state.get("swap_episode_since"):
        start_ts = parse_iso(state.get("swap_episode_since"))
        active.append({
            "v": VERSION,
            "id": state.get("swap_episode_id") or f"active-swap-{state['swap_episode_since']}",
            "ts": state.get("at") or now_iso(now),
            "started_at": state["swap_episode_since"],
            "kind": "sustained_swap",
            "outcome": "active",
            "duration_s": round(max(0.0, now - start_ts), 1) if start_ts else None,
            "subject": {},
            "memory": state.get("swap_episode_peak") or snapshot_memory(),
        })
    return active


def read_events(path: Path | None = None) -> list[dict]:
    """Read all kept limit events (rotated ``.1`` then active), oldest first."""
    target = path or events_path()
    rows: list[dict] = []
    seen: set[str] = set()
    for candidate in (target.with_name(target.name + ".1"), target):
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in text.splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("kind") not in KINDS:
                continue
            eid = row.get("id")
            if isinstance(eid, str) and eid:
                if eid in seen:
                    continue
                seen.add(eid)
            rows.append(row)
    return rows


def query(
    *,
    days: float = 7,
    kind: str | None = None,
    limit: int = 200,
    now: float | None = None,
    path: Path | None = None,
    pressure_path: Path | None = None,
    meminfo_path: Path | str = "/proc/meminfo",
    swaps_path: Path | str = "/proc/swaps",
) -> dict:
    """Single read path answering 'what happened in the last N days'.

    Returns counts by kind across all matching events in the window, any
    currently active episodes, the current box memory summary, and the raw
    events newest-first up to ``limit``. ``days <= 0`` returns all kept history.
    """
    at = time.time() if now is None else float(now)
    wanted: set[str] | None = None
    if kind:
        parts = [p.strip() for p in str(kind).split(",") if p.strip()]
        bad = [p for p in parts if p not in KINDS]
        if bad:
            raise ValueError(f"unknown limit event kind(s): {', '.join(bad)}; "
                             f"known: {', '.join(KINDS)}")
        wanted = set(parts)

    cutoff = (at - float(days) * 86400.0) if days and days > 0 else None
    matched: list[dict] = []
    for row in read_events(path):
        if wanted is not None and row["kind"] not in wanted:
            continue
        if cutoff is not None:
            ets = parse_iso(row.get("ts"))
            if ets is None or ets < cutoff:
                continue
        matched.append(row)

    counts = {k: 0 for k in KINDS}
    for row in matched:
        counts[row["kind"]] += 1

    active = _read_active_episodes(pressure_path, at)
    if wanted is not None:
        active = [a for a in active if a["kind"] in wanted]

    # Newest first for the returned raw list; counts cover the whole window.
    matched.reverse()
    cap = max(0, int(limit))
    events = matched[:cap] if cap else []
    snap = snapshot_memory(meminfo_path=meminfo_path, swaps_path=swaps_path)
    return {
        "days": days,
        "since": now_iso(cutoff) if cutoff is not None else None,
        "until": now_iso(at),
        "total": len(matched),
        "counts": counts,
        "active": active,
        "box": {
            "total_mb": snap.get("total_mb"),
            "available_mb": snap.get("available_mb"),
            "reserve_mb": snap.get("reserve_mb"),
            "start_mb": snap.get("start_mb"),
            "swap_total_mb": snap.get("swap_total_mb"),
            "swap_used_mb": snap.get("swap_used_mb"),
        },
        "events": events,
        "truncated": len(matched) > len(events),
    }


def format_text(data: dict) -> str:
    """Human-readable summary for ``loam limits``."""
    days = data.get("days")
    window = f"last {days:g} day{'s' if days != 1 else ''}" if days and days > 0 else "all kept history"
    box = data.get("box") or {}
    lines = [
        f"Loam limit events ({window}, through {data.get('until', '?')}):",
        f"Box: {box.get('total_mb', '?')} MB RAM "
        f"({box.get('available_mb', '?')} MB available, "
        f"reserve {box.get('reserve_mb', '?')} MB, "
        f"start {box.get('start_mb', '?')} MB; "
        f"swap {box.get('swap_used_mb', 0)}/{box.get('swap_total_mb', 0)} MB used).",
        f"Total events: {data.get('total', 0)}",
    ]
    counts = data.get("counts") or {}
    nonzero = [(k, counts[k]) for k in KINDS if counts.get(k)]
    if nonzero:
        lines.append("Counts by kind:")
        for k, n in nonzero:
            lines.append(f"  {k:18s} {n}")
    else:
        lines.append("No limit events recorded in this window.")
    active = data.get("active") or []
    if active:
        lines.append("Active right now:")
        for a in active:
            dur = f"{a['duration_s']}s" if a.get("duration_s") is not None else "ongoing"
            lines.append(f"  {a['kind']} since {a.get('started_at', '?')} ({dur})")
    events = data.get("events") or []
    if events:
        lines.append(f"Recent events (showing {len(events)} of {data.get('total', len(events))}):")
        for ev in events:
            subj = " ".join(f"{k}={v}" for k, v in (ev.get("subject") or {}).items())
            dur = f" {ev['duration_s']}s" if ev.get("duration_s") is not None else ""
            out = f" [{ev['outcome']}]" if ev.get("outcome") else ""
            mem = ev.get("memory") or {}
            mtxt = (f" avail={mem.get('available_mb')}MB/"
                    f"res={mem.get('reserve_mb')}MB"
                    f" swap={mem.get('swap_used_mb')}/{mem.get('swap_total_mb')}MB")
            lines.append(
                f"  {ev.get('ts', '?')}  {ev.get('kind', '?'):16s}{out}{dur}"
                + (f"  {subj}" if subj else "")
                + mtxt
            )
    return "\n".join(lines)
