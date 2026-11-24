"""Per-minute, numbers-only box usage recorder and optional outbound batch pusher.

Every Loam install keeps a bounded local record under ``<state>/usage/``:
one-minute samples of host resources (RAM, swap, CPU, load, root and data-disk
space) and Loam activity counts (live and busy chat sessions, engineer jobs,
wakes, crons, browser awake/asleep), plus countable events (memory waits,
session memory evictions, OOM kills, and broker/host/app restarts).

Privacy invariant: numbers, booleans, UTC timestamps and closed enums only. No
file names, thread titles, message text, agent names, vault paths, prompts,
device IPs, hostnames or owner names ever enter a sample, event or wire batch.

Sending is OFF by default. A self-installed Loam ships with no collector URL
and never attempts an outbound connection. When ``[usage]`` in
``/etc/loam/loam.toml`` (or ``$LOAM_CONFIG``) sets both ``collector_url``
(HTTPS; loopback HTTP allowed for tests) and ``token_file`` (a readable file
containing a non-empty bearer token), batches are pushed outward on a slow beat
with retry, exponential backoff and outage catch-up, off the broker's event
loop so a dead collector never blocks or slows a chat.

Wire format specification: ``docs/usage.md`` (``loam-usage/1``).
Stdlib only.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import gzip
import json
import os
import re
import socket
import tempfile
import time
import tomllib
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import loam_config as lc
import loam_limits as limits
import loam_offline

WIRE_FORMAT = "loam-usage/1"

SAMPLE_INTERVAL_S = 60
PUSH_INTERVAL_S = 300
PUSH_TIMEOUT_S = 10
BASE_BACKOFF_S = 300
MAX_BACKOFF_S = 3600
MAX_FILE_BYTES = 2 * 1024 * 1024       # 2 MiB active file before mid-day rotation
MAX_TOTAL_BYTES = 16 * 1024 * 1024     # 16 MiB hard disk ceiling (~300+ days compressed)
RETENTION_DAYS = 90
MAX_BATCH_RECORDS = 250
MAX_BATCH_BYTES = 256 * 1024           # 256 KiB per outbound POST
MAX_CATCHUP_BATCHES = 20
LIMITS_SEEN_CAP = 2000

ARCHIVE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d+)-(\d+)\.jsonl\.gz$")

# Strict schema allowlists — every record written or sent is checked against these.
SAMPLE_KEYS = frozenset({
    "type", "seq", "ts", "interval_s",
    "mem_total_mb", "mem_used_mb", "mem_available_mb",
    "swap_total_mb", "swap_used_mb",
    "cpu_pct", "vcpus", "load_1m", "load_5m",
    "disk_root_total_mb", "disk_root_used_mb",
    "disk_data_total_mb", "disk_data_used_mb",
    "chats_live", "chats_busy", "jobs", "wakes", "crons", "browser_up",
})

EVENT_CORE_KEYS = frozenset({
    "type", "seq", "ts", "kind", "outcome", "mem_available_mb", "swap_used_mb",
})
EVENT_OPTIONAL_KEYS = frozenset({
    "duration_s", "idle_s", "trigger", "count", "service",
})
EVENT_KEYS = EVENT_CORE_KEYS | EVENT_OPTIONAL_KEYS

EVENT_KINDS = frozenset({
    "chat_wait",
    "chat_timeout",
    "chat_evicted",
    "turn_retried",
    "run_wait",
    "run_skipped",
    "job_queued",
    "summary_deferred",
    "pressure_episode",
    "sustained_swap",
    "oom_kill",
    "kernel_oom",
    "service_restart",
})

ALLOWED_OUTCOMES = frozenset({
    "started", "cancelled", "timed_out", "evicted",
    "retried", "recovered", "failed", "deferred",
    "ended", "killed", "restarted", "reboot",
})

ALLOWED_TRIGGERS = frozenset({
    "chat_start", "waiting_run", "under_reserve",
})

ALLOWED_SERVICES = frozenset({
    "broker", "session_host", "app",
})

ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def now_iso(ts: float | None = None) -> str:
    val = time.time() if ts is None else float(ts)
    return datetime.fromtimestamp(val, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_day(ts: float | None = None) -> str:
    val = time.time() if ts is None else float(ts)
    return datetime.fromtimestamp(val, timezone.utc).strftime("%Y-%m-%d")


def loam_version(root: Path | None = None) -> str:
    manifest = (root or lc.ROOT) / "manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        ver = data.get("version")
        if isinstance(ver, str) and ver.strip():
            return ver.strip()[:64]
    except (OSError, ValueError):
        pass
    return "0.0.0"


def usage_dir(directory: Path | str | None = None) -> Path:
    if directory is not None:
        return Path(directory)
    override = os.environ.get("LOAM_USAGE_DIR")
    if override:
        return Path(override)
    return lc.STATE / "usage"


# -- configuration & public status -------------------------------------------

@dataclass(frozen=True)
class CollectorConfig:
    collector_url: str | None
    collector_host: str | None
    token_file: Path | None
    token: str | None
    box_id: str | None
    plan: str | None

    @property
    def sending(self) -> bool:
        return bool(self.collector_url and self.token)


def validate_collector_url(value: Any) -> tuple[str, str] | tuple[None, None]:
    """Validate ``usage.collector_url``. Returns ``(clean_url, display_host)`` or
    ``(None, None)`` when unset or invalid.

    Requires ``https://`` for every remote host; allows ``http://`` only on
    loopback (``127.0.0.1``, ``::1``, ``localhost``) for tests and local
    rehearsals. Refuses embedded credentials, query strings and fragments.
    """
    if not isinstance(value, str):
        return None, None
    raw = value.strip()
    if not raw:
        return None, None
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return None, None
    host = parsed.hostname
    if (not host or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        return None, None
    scheme = (parsed.scheme or "").lower()
    if scheme == "https":
        pass
    elif scheme == "http" and host in {"127.0.0.1", "::1", "localhost"}:
        pass
    else:
        return None, None
    try:
        port = parsed.port
    except ValueError:
        return None, None
    default_port = 443 if scheme == "https" else 80
    if port is not None and port != default_port:
        display_host = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    else:
        display_host = host
    return raw, display_host


def _read_toml(config_path: Path | None = None) -> dict:
    path = Path(config_path) if config_path is not None else lc.CONFIG_FILE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _clean_opt_str(value: Any, max_len: int) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s[:max_len] if s else None


def load_collector_config(config_path: Path | None = None) -> CollectorConfig:
    """Read ``[usage]`` fresh from the install's ``loam.toml`` (no secrets in
    TOML: ``token_file`` points to a restricted file holding the bearer token).
    """
    raw = _read_toml(config_path)
    section = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    url, host = validate_collector_url(section.get("collector_url"))
    token_file_raw = _clean_opt_str(section.get("token_file"), 512)
    token_file = Path(token_file_raw).expanduser() if token_file_raw else None
    token: str | None = None
    if url and token_file is not None:
        try:
            text = token_file.read_text(encoding="utf-8").strip()
            if text:
                token = text
        except OSError:
            token = None
    box_id = _clean_opt_str(section.get("box_id"), 128)
    plan = _clean_opt_str(section.get("plan"), 64)
    return CollectorConfig(
        collector_url=url,
        collector_host=host if (url and token) else None,
        token_file=token_file,
        token=token,
        box_id=box_id,
        plan=plan,
    )


def public_status(config_path: Path | None = None) -> dict:
    """Honest, read-only summary for Settings (``GET /v1/usage/status``).

    Never exposes the token, token file path, or box ID.
    """
    cfg = load_collector_config(config_path)
    return {
        "recording": True,
        "sending": cfg.sending,
        "collector_host": cfg.collector_host if cfg.sending else None,
        "destination": cfg.collector_url if cfg.sending else None,
    }


# -- strict record validation (privacy guard) --------------------------------

def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_record(row: dict) -> None:
    """Raise ``ValueError`` if ``row`` contains any unexpected key or non-numeric /
    non-enum value. Enforces the numbers-only privacy contract at write time."""
    if not isinstance(row, dict):
        raise ValueError("record must be a dict")
    rtype = row.get("type")
    if rtype not in ("sample", "event"):
        raise ValueError(f"invalid record type {rtype!r}")
    if not _is_int(row.get("seq")) or row["seq"] < 1:
        raise ValueError(f"invalid seq {row.get('seq')!r}")
    ts = row.get("ts")
    if not isinstance(ts, str) or not ISO_UTC_RE.match(ts):
        raise ValueError(f"invalid UTC timestamp {ts!r}")

    if rtype == "sample":
        extra = set(row) - SAMPLE_KEYS
        missing = SAMPLE_KEYS - set(row)
        if extra or missing:
            raise ValueError(f"sample keys mismatch: extra={extra} missing={missing}")
        int_fields = (
            "mem_total_mb", "mem_used_mb", "mem_available_mb",
            "swap_total_mb", "swap_used_mb",
            "chats_live", "chats_busy", "jobs", "wakes", "crons",
        )
        for k in int_fields:
            if not _is_int(row[k]) or row[k] < 0:
                raise ValueError(f"sample.{k} must be non-negative int, got {row[k]!r}")
        opt_int_fields = (
            "interval_s", "vcpus",
            "disk_root_total_mb", "disk_root_used_mb",
            "disk_data_total_mb", "disk_data_used_mb",
        )
        for k in opt_int_fields:
            v = row[k]
            if v is not None and (not _is_int(v) or v < 0):
                raise ValueError(f"sample.{k} must be non-negative int or None, got {v!r}")
        for k in ("cpu_pct", "load_1m", "load_5m"):
            v = row[k]
            if v is not None and (not _is_num(v) or v < 0):
                raise ValueError(f"sample.{k} must be non-negative number or None, got {v!r}")
        if not isinstance(row["browser_up"], bool):
            raise ValueError(f"sample.browser_up must be bool, got {row['browser_up']!r}")
        return

    # event
    extra = set(row) - EVENT_KEYS
    missing = EVENT_CORE_KEYS - set(row)
    if extra or missing:
        raise ValueError(f"event keys mismatch: extra={extra} missing={missing}")
    if row["kind"] not in EVENT_KINDS:
        raise ValueError(f"unknown event kind {row['kind']!r}")
    if row["outcome"] is not None and row["outcome"] not in ALLOWED_OUTCOMES:
        raise ValueError(f"unknown event outcome {row['outcome']!r}")
    for k in ("mem_available_mb", "swap_used_mb"):
        v = row[k]
        if v is not None and (not _is_int(v) or v < 0):
            raise ValueError(f"event.{k} must be non-negative int or None, got {v!r}")
    if "duration_s" in row:
        v = row["duration_s"]
        if not _is_num(v) or v < 0:
            raise ValueError(f"event.duration_s must be non-negative number, got {v!r}")
    if "idle_s" in row:
        v = row["idle_s"]
        if not _is_int(v) or v < 0:
            raise ValueError(f"event.idle_s must be non-negative int, got {v!r}")
    if "count" in row:
        v = row["count"]
        if not _is_int(v) or v < 1:
            raise ValueError(f"event.count must be positive int, got {v!r}")
    if "trigger" in row and row["trigger"] not in ALLOWED_TRIGGERS:
        raise ValueError(f"unknown event trigger {row['trigger']!r}")
    if "service" in row and row["service"] not in ALLOWED_SERVICES:
        raise ValueError(f"unknown event service {row['service']!r}")


# -- system readers (pure /proc, /sys, statvfs — zero subprocesses) ----------

def read_meminfo(path: Path = Path("/proc/meminfo")) -> dict[str, int]:
    """Read MemTotal, MemAvailable, SwapTotal, SwapFree in integer MiB."""
    want = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
    raw: dict[str, int] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            k, _, rest = line.partition(":")
            if k in want:
                parts = rest.split()
                if parts and parts[0].isdigit():
                    raw[k] = int(parts[0]) // 1024
    except OSError:
        pass
    total = raw.get("MemTotal", 0)
    avail = raw.get("MemAvailable", 0)
    used = max(0, total - avail)
    swap_total = raw.get("SwapTotal", 0)
    swap_free = raw.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free)
    return {
        "mem_total_mb": total,
        "mem_used_mb": used,
        "mem_available_mb": avail,
        "swap_total_mb": swap_total,
        "swap_used_mb": swap_used,
    }


def read_proc_stat(path: Path = Path("/proc/stat")) -> tuple[int | None, int | None, int | None]:
    """Return ``(total_jiffies, idle_jiffies, btime)`` from ``/proc/stat``."""
    total: int | None = None
    idle: int | None = None
    btime: int | None = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("cpu "):
                vals = [int(p) for p in line.split()[1:9] if p.isdigit()]
                if len(vals) >= 4:
                    total = sum(vals)
                    idle = vals[3] + (vals[4] if len(vals) >= 5 else 0)
            elif line.startswith("btime "):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    btime = int(parts[1])
            if total is not None and btime is not None:
                break
    except (OSError, ValueError):
        pass
    return total, idle, btime


def read_vmstat_oom(path: Path = Path("/proc/vmstat")) -> int | None:
    """Return the kernel-wide monotonic ``oom_kill`` counter from ``/proc/vmstat``."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("oom_kill "):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1])
                break
    except (OSError, ValueError):
        pass
    return None


def read_loadavg(path: Path = Path("/proc/loadavg")) -> tuple[float | None, float | None]:
    """Return ``(load_1m, load_5m)`` rounded to 2 decimal places."""
    try:
        parts = path.read_text(encoding="utf-8").split()
        if len(parts) >= 2:
            return round(max(0.0, float(parts[0])), 2), round(max(0.0, float(parts[1])), 2)
    except (OSError, ValueError):
        pass
    return None, None


def read_disk_mb(path: Path) -> tuple[int | None, int | None]:
    """Return ``(total_mb, used_mb)`` for the filesystem containing ``path``."""
    try:
        st = os.statvfs(path)
        total = (st.f_blocks * st.f_frsize) // 1048576
        used = max(0, ((st.f_blocks - st.f_bfree) * st.f_frsize) // 1048576)
        return int(total), int(used)
    except OSError:
        return None, None


def read_unit_counts(app_slice: Path) -> dict[str, int]:
    """Count active engineer jobs, wakes and crons from the user ``app.slice``
    cgroup tree without spawning ``systemctl``."""
    counts = {"jobs": 0, "wakes": 0, "crons": 0}
    try:
        entries = list(app_slice.iterdir())
    except OSError:
        return counts
    for entry in entries:
        name = entry.name
        if not name.endswith(".service") or not entry.is_dir():
            continue
        procs_file = entry / "cgroup.procs"
        if procs_file.exists():
            try:
                if not procs_file.read_text(encoding="utf-8").strip():
                    continue
            except OSError:
                continue
        if name.startswith("loam-engineer-"):
            counts["jobs"] += 1
        elif name.startswith("loam-wake-") and not name.startswith("loam-wake-failed"):
            counts["wakes"] += 1
        elif (name.startswith("loam-cron-")
              or name == "loam-reviewer.service"
              or name.startswith("loam-plugin-reviewer--")):
            counts["crons"] += 1
    return counts


def read_browser_up(state_path: Path) -> bool:
    """Check whether ``loam-browser`` is awake without waking the proxy socket
    or Docker."""
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return isinstance(data, dict) and data.get("state") == "awake"
    except (OSError, ValueError):
        return False


def _proc_starttime(pid: int, proc_dir: Path = Path("/proc")) -> int | None:
    if pid <= 0:
        return None
    try:
        raw = (proc_dir / str(pid) / "stat").read_text(encoding="utf-8")
        fields = raw.rsplit(")", 1)[-1].split()
        return int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def _cgroup_main_identity(
    cgroup_dir: Path | None,
    btime: int | None,
    proc_dir: Path = Path("/proc"),
) -> list[int | None] | None:
    """Return ``[btime, pid, starttime]`` for the oldest live PID in a service
    cgroup, or ``None`` if the service is not currently running."""
    if cgroup_dir is None:
        return None
    try:
        raw = (cgroup_dir / "cgroup.procs").read_text(encoding="utf-8")
    except OSError:
        return None
    best: tuple[int, int] | None = None  # (starttime, pid)
    for token in raw.split():
        if not token.isdigit():
            continue
        pid = int(token)
        st = _proc_starttime(pid, proc_dir)
        if st is None:
            continue
        if best is None or (st, pid) < best:
            best = (st, pid)
    if best is None:
        return None
    return [btime, best[1], best[0]]


def default_app_cgroup_dir(config_path: Path | None = None) -> Path:
    raw = _read_toml(config_path)
    edge = raw.get("edge") if isinstance(raw.get("edge"), dict) else {}
    app_name = _clean_opt_str(edge.get("app"), 64) or "loam-ui"
    sliced = Path(f"/sys/fs/cgroup/system.slice/system-loam\\x2dapp.slice/loam-app@{app_name}.service")
    if sliced.is_dir():
        return sliced
    direct = Path(f"/sys/fs/cgroup/system.slice/loam-app@{app_name}.service")
    return direct if direct.is_dir() else sliced


def project_limit_event(raw: dict) -> dict | None:
    """Project a local ``loam_limits`` row into a strictly numbers-and-enums
    usage event body (without ``seq`` or ``type``).

    Drops ``subject``, ``id``, ``heaviest``, ``for_agent``, ``provider``,
    ``lane``, ``unit``, ``reason``, and every other free-form or identifying
    field.
    """
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    if kind not in EVENT_KINDS or kind in ("kernel_oom", "service_restart"):
        return None
    ts = raw.get("ts")
    if not isinstance(ts, str) or not ISO_UTC_RE.match(ts):
        return None

    outcome = raw.get("outcome")
    if outcome not in ALLOWED_OUTCOMES:
        outcome = None

    mem = raw.get("memory") if isinstance(raw.get("memory"), dict) else {}
    mem_avail = mem.get("available_mb")
    swap_used = mem.get("swap_used_mb")
    mem_avail_int = int(mem_avail) if _is_int(mem_avail) and mem_avail >= 0 else None
    swap_used_int = int(swap_used) if _is_int(swap_used) and swap_used >= 0 else None

    out: dict[str, Any] = {
        "ts": ts,
        "kind": kind,
        "outcome": outcome,
        "mem_available_mb": mem_avail_int,
        "swap_used_mb": swap_used_int,
    }

    dur = raw.get("duration_s")
    if _is_num(dur) and dur >= 0:
        out["duration_s"] = round(float(dur), 1)

    detail = raw.get("detail") if isinstance(raw.get("detail"), dict) else {}
    if kind == "chat_evicted":
        idle_s = detail.get("idle_s")
        if _is_num(idle_s) and idle_s >= 0:
            out["idle_s"] = int(round(float(idle_s)))
    if kind in ("chat_evicted", "summary_deferred"):
        trig = detail.get("trigger")
        if trig in ALLOWED_TRIGGERS:
            out["trigger"] = trig
    if kind == "oom_kill":
        kills = detail.get("oom_kills")
        out["count"] = int(kills) if _is_int(kills) and kills >= 1 else 1
    return out


# -- atomic JSON helpers -----------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"), sort_keys=True)
            fh.write("\n")
            fh.flush()
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# -- bounded local store -----------------------------------------------------

class UsageStore:
    """Append-only JSONL store with daily + size rotation, gzip archiving,
    90-day retention and hard total-size ceiling."""

    def __init__(
        self,
        directory: Path | str | None = None,
        *,
        max_file_bytes: int = MAX_FILE_BYTES,
        max_total_bytes: int = MAX_TOTAL_BYTES,
        retention_days: int = RETENTION_DAYS,
    ) -> None:
        self.dir = usage_dir(directory)
        self.active_path = self.dir / "usage.jsonl"
        self.archive_dir = self.dir / "archive"
        self.state_path = self.dir / "state.json"
        self.push_path = self.dir / "push.json"
        self.lock_path = self.dir / "usage.lock"
        self.push_lock_path = self.dir / "push.lock"
        self.max_file_bytes = max(512, int(max_file_bytes))
        self.max_total_bytes = max(1024, int(max_total_bytes))
        self.retention_days = max(1, int(retention_days))

    @contextlib.contextmanager
    def _locked(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _archive_entries(self) -> list[tuple[str, int, int, Path]]:
        """Sorted ``(day, first_seq, last_seq, path)`` for archived segments."""
        if not self.archive_dir.is_dir():
            return []
        entries: list[tuple[str, int, int, Path]] = []
        for p in self.archive_dir.iterdir():
            if not p.is_file():
                continue
            m = ARCHIVE_RE.match(p.name)
            if not m:
                continue
            entries.append((m.group(1), int(m.group(2)), int(m.group(3)), p))
        entries.sort(key=lambda e: (e[0], e[1], e[2]))
        return entries

    def _scan_active_bounds(self) -> tuple[str | None, int | None, int | None]:
        """Return ``(day, first_seq, last_seq)`` by scanning ``usage.jsonl``."""
        day: str | None = None
        first_seq: int | None = None
        last_seq: int | None = None
        try:
            with self.active_path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        continue
                    seq = row.get("seq")
                    ts = row.get("ts")
                    if _is_int(seq):
                        if first_seq is None:
                            first_seq = seq
                        last_seq = seq
                    if day is None and isinstance(ts, str) and len(ts) >= 10:
                        day = ts[:10]
        except OSError:
            pass
        return day, first_seq, last_seq

    def _rotate_active_locked(self, state: dict) -> None:
        """Compress non-empty ``usage.jsonl`` into ``archive/`` and start a fresh
        empty ``usage.jsonl``."""
        try:
            st = self.active_path.stat()
        except FileNotFoundError:
            return
        if st.st_size == 0:
            return

        day = state.get("active_day")
        first_seq = state.get("active_first_seq")
        last_seq = state.get("last_seq")
        if not (isinstance(day, str) and len(day) == 10 and _is_int(first_seq) and _is_int(last_seq)):
            s_day, s_first, s_last = self._scan_active_bounds()
            day = s_day or utc_day()
            first_seq = s_first if s_first is not None else 0
            last_seq = s_last if s_last is not None else first_seq

        self.archive_dir.mkdir(parents=True, exist_ok=True)
        dest_name = f"{day}-{first_seq:012d}-{last_seq:012d}.jsonl.gz"
        dest = self.archive_dir / dest_name
        # Avoid collision if an identical range somehow already exists.
        if dest.exists():
            dest = self.archive_dir / f"{day}-{first_seq:012d}-{last_seq + 1:012d}.jsonl.gz"

        fd, tmp_name = tempfile.mkstemp(dir=self.archive_dir, prefix=".rot-", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as raw_out:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw_out, compresslevel=6, mtime=0) as gz:
                    with self.active_path.open("rb") as src:
                        while True:
                            chunk = src.read(65536)
                            if not chunk:
                                break
                            gz.write(chunk)
                raw_out.flush()
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)

        # Atomically replace usage.jsonl with an empty file so any concurrent reader
        # finishing on the old inode sees complete lines.
        fd_empty, tmp_empty_name = tempfile.mkstemp(dir=self.dir, prefix=".usage-", suffix=".tmp")
        tmp_empty = Path(tmp_empty_name)
        try:
            os.close(fd_empty)
            os.replace(tmp_empty, self.active_path)
        finally:
            tmp_empty.unlink(missing_ok=True)
        state["active_day"] = None
        state["active_first_seq"] = None

    def _trim_active_to_budget_locked(self) -> None:
        """Last-resort ceiling when no archives exist and active file alone
        exceeds ``max_total_bytes``."""
        try:
            if self.active_path.stat().st_size <= self.max_total_bytes:
                return
            lines = self.active_path.read_bytes().splitlines(keepends=True)
        except OSError:
            return
        kept: list[bytes] = []
        total = 0
        for line in reversed(lines):
            if total + len(line) > self.max_total_bytes and kept:
                break
            kept.append(line)
            total += len(line)
        kept.reverse()
        fd, tmp_name = tempfile.mkstemp(dir=self.dir, prefix=".trim-", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.writelines(kept)
                fh.flush()
            os.replace(tmp, self.active_path)
        finally:
            tmp.unlink(missing_ok=True)

    def _prune_locked(self, now_ts: float) -> None:
        cutoff_day = (
            datetime.fromtimestamp(now_ts, timezone.utc).date()
            - timedelta(days=self.retention_days)
        ).isoformat()
        entries = self._archive_entries()
        kept: list[tuple[str, int, int, Path]] = []
        for entry in entries:
            day, _, _, path = entry
            if day < cutoff_day:
                path.unlink(missing_ok=True)
            else:
                kept.append(entry)

        total = self._size_of(self.active_path) + sum(self._size_of(e[3]) for e in kept)
        while total > self.max_total_bytes and kept:
            _, _, _, oldest = kept.pop(0)
            total -= self._size_of(oldest)
            oldest.unlink(missing_ok=True)

        if total > self.max_total_bytes:
            self._trim_active_to_budget_locked()

    @staticmethod
    def _size_of(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def total_bytes(self) -> int:
        return self._size_of(self.active_path) + sum(
            self._size_of(e[3]) for e in self._archive_entries()
        )

    def _recover_last_seq_locked(self) -> int:
        best = 0
        for _, _, last_seq, _ in self._archive_entries():
            if last_seq > best:
                best = last_seq
        _, _, active_last = self._scan_active_bounds()
        if active_last is not None and active_last > best:
            best = active_last
        return best

    def append_batch(
        self,
        bodies: list[tuple[str, dict]],
        *,
        now_ts: float,
        state_updater: Callable[[dict], None] | None = None,
    ) -> list[dict]:
        """Atomically assign ``seq`` numbers, validate, append and rotate.

        ``bodies`` is a list of ``(rtype, fields_without_type_and_seq)``.
        Returns the written record dicts.
        """
        with self._locked():
            state = _read_json(self.state_path)
            last_seq = state.get("last_seq")
            if not _is_int(last_seq) or last_seq < 0:
                last_seq = self._recover_last_seq_locked()

            written: list[dict] = []
            for rtype, fields in bodies:
                last_seq += 1
                row = {"type": rtype, "seq": last_seq, **fields}
                validate_record(row)
                rec_day = row["ts"][:10]

                active_day = state.get("active_day")
                active_size = self._size_of(self.active_path)
                if active_size > 0 and (
                    (active_day and rec_day > active_day)
                    or active_size >= self.max_file_bytes
                ):
                    self._rotate_active_locked(state)

                line = json.dumps(row, separators=(",", ":")) + "\n"
                with self.active_path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
                    fh.flush()
                if not state.get("active_day"):
                    state["active_day"] = rec_day
                    state["active_first_seq"] = last_seq
                state["last_seq"] = last_seq
                written.append(row)

                if self._size_of(self.active_path) >= self.max_file_bytes:
                    self._rotate_active_locked(state)

            if state_updater is not None:
                state_updater(state)
            state["last_seq"] = last_seq
            self._prune_locked(now_ts)
            _write_json_atomic(self.state_path, state)
            return written

    def read_state(self) -> dict:
        return _read_json(self.state_path)

    def read_after(
        self,
        after_seq: int = 0,
        *,
        max_records: int | None = None,
        max_bytes: int | None = None,
    ) -> list[dict]:
        """Read records with ``seq > after_seq`` in strict ascending ``seq``
        order across archives and the active file.

        Archive files whose filename ``last_seq <= after_seq`` are skipped
        without decompressing.
        """
        out: list[dict] = []
        seen_seq: set[int] = set()
        total_bytes = 0

        def _consume(fh) -> bool:
            nonlocal total_bytes
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                seq = row.get("seq")
                if not _is_int(seq) or seq <= after_seq or seq in seen_seq:
                    continue
                est = len(line.encode("utf-8")) + 2
                if out and max_bytes is not None and total_bytes + est > max_bytes:
                    return True
                seen_seq.add(seq)
                out.append(row)
                total_bytes += est
                if max_records is not None and len(out) >= max_records:
                    return True
            return False

        for _, _, last_seq, path in self._archive_entries():
            if last_seq <= after_seq:
                continue
            try:
                with gzip.open(path, "rt", encoding="utf-8") as fh:
                    if _consume(fh):
                        return out
            except (OSError, EOFError, ValueError):
                continue

        try:
            with self.active_path.open("r", encoding="utf-8") as fh:
                _consume(fh)
        except OSError:
            pass
        return out

    def read_tail(self, limit: int = 20) -> list[dict]:
        """Return the newest ``limit`` records across active + archive files."""
        if limit <= 0:
            return []
        rows: list[dict] = []
        # Read active file first, then newest archives if needed.
        sources: list[tuple[bool, Path]] = [(False, self.active_path)]
        for _, _, _, path in reversed(self._archive_entries()):
            sources.append((True, path))
        for is_gz, path in sources:
            if not path.exists():
                continue
            file_rows: list[dict] = []
            try:
                opener = (lambda p: gzip.open(p, "rt", encoding="utf-8")) if is_gz else (lambda p: p.open("r", encoding="utf-8"))
                with opener(path) as fh:
                    for raw in fh:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            row = json.loads(raw)
                        except ValueError:
                            continue
                        if isinstance(row, dict) and _is_int(row.get("seq")):
                            file_rows.append(row)
            except (OSError, EOFError, ValueError):
                continue
            rows = file_rows + rows
            if len(rows) >= limit:
                break
        # Deduplicate by seq and return last `limit` in chronological order.
        by_seq = {r["seq"]: r for r in rows}
        ordered = [by_seq[s] for s in sorted(by_seq)]
        return ordered[-limit:]


# -- sampler + event collector -----------------------------------------------

class UsageRecorder:
    """Takes one-minute resource/activity samples and ingests countable events."""

    def __init__(
        self,
        store: UsageStore | None = None,
        *,
        config_path: Path | None = None,
        meminfo_path: Path = Path("/proc/meminfo"),
        stat_path: Path = Path("/proc/stat"),
        vmstat_path: Path = Path("/proc/vmstat"),
        loadavg_path: Path = Path("/proc/loadavg"),
        proc_dir: Path = Path("/proc"),
        root_disk_path: Path = Path("/"),
        data_disk_path: Path | None = None,
        app_slice_dir: Path | None = None,
        system_app_cgroup_dir: Path | None = None,
        browser_state_path: Path | None = None,
        limits_events_path: Path | None = None,
        chats_provider: Callable[[], tuple[int, int]] | None = None,
        clock: Callable[[], float] = time.time,
        vcpus_provider: Callable[[], int | None] = os.cpu_count,
    ) -> None:
        self.store = store or UsageStore()
        self.config_path = config_path
        self.meminfo_path = Path(meminfo_path)
        self.stat_path = Path(stat_path)
        self.vmstat_path = Path(vmstat_path)
        self.loadavg_path = Path(loadavg_path)
        self.proc_dir = Path(proc_dir)
        self.root_disk_path = Path(root_disk_path)
        self.data_disk_path = Path(data_disk_path) if data_disk_path is not None else None
        self.app_slice_dir = Path(app_slice_dir) if app_slice_dir is not None else (lc.USER_SLICE / "app.slice")
        self.system_app_cgroup_dir = (
            Path(system_app_cgroup_dir)
            if system_app_cgroup_dir is not None
            else default_app_cgroup_dir(config_path)
        )
        self.browser_state_path = (
            Path(browser_state_path)
            if browser_state_path is not None
            else Path(os.environ.get("LOAM_BROWSER_STATE") or "/run/loam-browser/state.json")
        )
        self.limits_events_path = Path(limits_events_path) if limits_events_path is not None else None
        self.chats_provider = chats_provider
        self.clock = clock
        self.vcpus_provider = vcpus_provider

    def _data_disk_usage(self) -> tuple[int | None, int | None]:
        if self.data_disk_path is not None:
            return read_disk_mb(self.data_disk_path)
        if lc.DATA_DIR is not None and lc.DATA_DIR.exists():
            return read_disk_mb(lc.DATA_DIR)
        try:
            if lc.VAULT.exists() and lc.VAULT.stat().st_dev != self.root_disk_path.stat().st_dev:
                return read_disk_mb(lc.VAULT)
        except OSError:
            pass
        return None, None

    def _service_identities(
        self,
        btime: int | None,
        *,
        broker_pid: int | None = None,
        host_pid: int | None = None,
    ) -> dict[str, list[int | None]]:
        out: dict[str, list[int | None]] = {}
        if broker_pid is not None and broker_pid > 0:
            st = _proc_starttime(broker_pid, self.proc_dir)
            if st is not None:
                out["broker"] = [btime, broker_pid, st]
        else:
            ident = _cgroup_main_identity(self.app_slice_dir / "loam-session-broker.service", btime, self.proc_dir)
            if ident is not None:
                out["broker"] = ident

        if host_pid is not None and host_pid > 0 and host_pid != broker_pid:
            st = _proc_starttime(host_pid, self.proc_dir)
            if st is not None:
                out["session_host"] = [btime, host_pid, st]
        else:
            ident = _cgroup_main_identity(self.app_slice_dir / "loam-session-host.service", btime, self.proc_dir)
            if ident is not None and (not out.get("broker") or ident[1] != out["broker"][1]):
                out["session_host"] = ident

        app_ident = _cgroup_main_identity(self.system_app_cgroup_dir, btime, self.proc_dir)
        if app_ident is not None:
            out["app"] = app_ident
        return out

    def tick(
        self,
        *,
        chats_live: int | None = None,
        chats_busy: int | None = None,
        broker_pid: int | None = None,
        host_pid: int | None = None,
        now_ts: float | None = None,
    ) -> list[dict]:
        """Collect one sample plus any newly observed events and persist them."""
        at = float(self.clock() if now_ts is None else now_ts)
        ts = now_iso(at)
        prev = self.store.read_state()

        mem = read_meminfo(self.meminfo_path)
        cpu_total, cpu_idle, btime = read_proc_stat(self.stat_path)
        vm_oom = read_vmstat_oom(self.vmstat_path)
        load_1m, load_5m = read_loadavg(self.loadavg_path)
        vcpus_raw = self.vcpus_provider()
        vcpus = int(vcpus_raw) if _is_int(vcpus_raw) and vcpus_raw > 0 else None
        disk_root_total, disk_root_used = read_disk_mb(self.root_disk_path)
        disk_data_total, disk_data_used = self._data_disk_usage()
        units = read_unit_counts(self.app_slice_dir)
        browser_up = read_browser_up(self.browser_state_path)

        if chats_live is None or chats_busy is None:
            if self.chats_provider is not None:
                try:
                    c_live, c_busy = self.chats_provider()
                except Exception:  # noqa: BLE001
                    c_live, c_busy = 0, 0
            else:
                c_live, c_busy = 0, 0
            if chats_live is None:
                chats_live = c_live
            if chats_busy is None:
                chats_busy = c_busy
        chats_live = max(0, int(chats_live))
        chats_busy = max(0, int(chats_busy))

        # CPU % over the whole interval since previous sample.
        cpu_pct: float | None = None
        prev_cpu = prev.get("prev_cpu")
        prev_btime = prev.get("prev_btime")
        if (isinstance(prev_cpu, list) and len(prev_cpu) == 2
                and _is_int(prev_cpu[0]) and _is_int(prev_cpu[1])
                and cpu_total is not None and cpu_idle is not None
                and (prev_btime is None or btime is None or prev_btime == btime)):
            dt = cpu_total - prev_cpu[0]
            di = cpu_idle - prev_cpu[1]
            if dt > 0 and 0 <= di <= dt:
                cpu_pct = round(max(0.0, min(100.0, 100.0 * (dt - di) / dt)), 1)

        prev_sample_ts = prev.get("prev_sample_ts")
        interval_s: int | None = None
        if _is_num(prev_sample_ts) and at >= float(prev_sample_ts):
            interval_s = int(round(at - float(prev_sample_ts)))

        pending_bodies: list[tuple[str, dict]] = []

        # 1. Service restarts (broker / session_host / app)
        cur_services = self._service_identities(btime, broker_pid=broker_pid, host_pid=host_pid)
        prev_services = prev.get("services") if isinstance(prev.get("services"), dict) else {}
        merged_services = dict(prev_services)
        for svc, cur_id in cur_services.items():
            old_id = prev_services.get(svc)
            if isinstance(old_id, list) and len(old_id) == 3 and cur_id != old_id:
                reboot = (old_id[0] is not None and cur_id[0] is not None and cur_id[0] != old_id[0])
                pending_bodies.append(("event", {
                    "ts": ts,
                    "kind": "service_restart",
                    "outcome": "reboot" if reboot else "restarted",
                    "service": svc,
                    "mem_available_mb": mem["mem_available_mb"],
                    "swap_used_mb": mem["swap_used_mb"],
                }))
            merged_services[svc] = cur_id

        # 2. Kernel OOM kill counter delta (/proc/vmstat oom_kill)
        prev_vm_oom = prev.get("prev_vmstat_oom")
        if (_is_int(prev_vm_oom) and vm_oom is not None
                and (prev_btime is None or btime is None or prev_btime == btime)
                and vm_oom > prev_vm_oom):
            pending_bodies.append(("event", {
                "ts": ts,
                "kind": "kernel_oom",
                "outcome": "killed",
                "count": int(vm_oom - prev_vm_oom),
                "mem_available_mb": mem["mem_available_mb"],
                "swap_used_mb": mem["swap_used_mb"],
            }))

        # 3. Limit events from <state>/limits/events.jsonl (scrubbed strictly)
        limits_seen_list: list[str] = list(prev.get("limits_seen") or [])
        limits_seen_set = set(limits_seen_list)
        watermark = prev.get("limits_watermark_ts")
        if not _is_num(watermark):
            # First run on an existing box: capture recent events (last 60s)
            # without backfilling weeks-old historical rows.
            watermark = at - 60.0
        else:
            watermark = float(watermark)

        limits_path = self.limits_events_path if self.limits_events_path is not None else limits.events_path()
        try:
            raw_events = limits.read_events(limits_path)
        except Exception:  # noqa: BLE001
            raw_events = []

        max_ingested_ts = watermark
        for raw_ev in raw_events:
            eid = raw_ev.get("id")
            if not isinstance(eid, str) or not eid:
                eid = f"{raw_ev.get('ts')}:{raw_ev.get('kind')}:{raw_ev.get('outcome')}"
            if eid in limits_seen_set:
                continue
            ets = limits.parse_iso(raw_ev.get("ts"))
            if ets is not None and ets < watermark:
                continue
            projected = project_limit_event(raw_ev)
            if projected is None:
                continue
            pending_bodies.append(("event", projected))
            limits_seen_set.add(eid)
            limits_seen_list.append(eid)
            if ets is not None and ets > max_ingested_ts:
                max_ingested_ts = ets

        if len(limits_seen_list) > LIMITS_SEEN_CAP:
            limits_seen_list = limits_seen_list[-LIMITS_SEEN_CAP:]
            watermark = max(watermark, max_ingested_ts - 60.0)

        # 4. The one-minute sample itself
        sample_body = {
            "ts": ts,
            "interval_s": interval_s,
            "mem_total_mb": mem["mem_total_mb"],
            "mem_used_mb": mem["mem_used_mb"],
            "mem_available_mb": mem["mem_available_mb"],
            "swap_total_mb": mem["swap_total_mb"],
            "swap_used_mb": mem["swap_used_mb"],
            "cpu_pct": cpu_pct,
            "vcpus": vcpus,
            "load_1m": load_1m,
            "load_5m": load_5m,
            "disk_root_total_mb": disk_root_total,
            "disk_root_used_mb": disk_root_used,
            "disk_data_total_mb": disk_data_total,
            "disk_data_used_mb": disk_data_used,
            "chats_live": chats_live,
            "chats_busy": chats_busy,
            "jobs": units["jobs"],
            "wakes": units["wakes"],
            "crons": units["crons"],
            "browser_up": browser_up,
        }
        pending_bodies.append(("sample", sample_body))

        def _update_state(st: dict) -> None:
            if cpu_total is not None and cpu_idle is not None:
                st["prev_cpu"] = [cpu_total, cpu_idle]
            if btime is not None:
                st["prev_btime"] = btime
            if vm_oom is not None:
                st["prev_vmstat_oom"] = vm_oom
            st["prev_sample_ts"] = at
            st["services"] = merged_services
            st["limits_seen"] = limits_seen_list
            st["limits_watermark_ts"] = watermark

        return self.store.append_batch(pending_bodies, now_ts=at, state_updater=_update_state)


# -- outbound batch pusher ---------------------------------------------------

def _http_post(url: str, token: str, payload: bytes, *, timeout_s: float, version: str) -> tuple[int, dict, int | None]:
    """Send one JSON batch over HTTPS (or loopback HTTP). Never follows redirects.

    Returns ``(http_status, parsed_json_or_empty_dict, retry_after_s_or_None)``.
    Raises ``OSError`` / ``TimeoutError`` / ``urllib.error.URLError`` on network errors.
    """
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    loam_offline.guard_url(url)
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": f"loam-usage/{version}",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            status = int(resp.status)
            body = resp.read(16384)
            parsed: dict = {}
            if body:
                with contextlib.suppress(ValueError, UnicodeDecodeError):
                    obj = json.loads(body.decode("utf-8"))
                    if isinstance(obj, dict):
                        parsed = obj
            return status, parsed, None
    except urllib.error.HTTPError as exc:
        retry_after: int | None = None
        ra = exc.headers.get("Retry-After") if exc.headers else None
        if isinstance(ra, str) and ra.strip().isdigit():
            retry_after = int(ra.strip())
        with contextlib.suppress(Exception):
            exc.read(4096)
        return int(exc.code), {}, retry_after


def _sanitize_transport_error(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    reason = getattr(exc, "reason", None)
    if reason is not None:
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "timeout"
        if isinstance(reason, ConnectionRefusedError):
            return "connection_refused"
        if isinstance(reason, socket.gaierror):
            return "dns_error"
        if isinstance(reason, OSError):
            return f"network_error:{reason.errno or type(reason).__name__}"
        return "url_error"
    if isinstance(exc, loam_offline.Offline):
        return "offline_guard"
    return type(exc).__name__


def build_wire_batch(
    records: list[dict],
    *,
    box_id: str | None,
    plan: str | None,
    version: str,
    sent_at: str,
) -> dict:
    samples: list[dict] = []
    events: list[dict] = []
    for row in records:
        rtype = row.get("type")
        clean = {k: v for k, v in row.items() if k != "type"}
        if rtype == "sample":
            samples.append(clean)
        elif rtype == "event":
            events.append(clean)
    return {
        "format": WIRE_FORMAT,
        "box_id": box_id,
        "plan": plan,
        "loam_version": version,
        "sent_at": sent_at,
        "samples": samples,
        "events": events,
    }


class UsagePusher:
    """Pushes unsent local samples and events outward in bounded batches when
    configured. Does nothing at all when no collector URL + token exist."""

    def __init__(
        self,
        store: UsageStore | None = None,
        *,
        config_path: Path | None = None,
        timeout_s: float = PUSH_TIMEOUT_S,
        base_backoff_s: float = BASE_BACKOFF_S,
        max_backoff_s: float = MAX_BACKOFF_S,
        max_batch_records: int = MAX_BATCH_RECORDS,
        max_batch_bytes: int = MAX_BATCH_BYTES,
        max_batches_per_run: int = MAX_CATCHUP_BATCHES,
        transport: Callable[..., tuple[int, dict, int | None]] | None = None,
        clock: Callable[[], float] = time.time,
        version: str | None = None,
    ) -> None:
        self.store = store or UsageStore()
        self.config_path = config_path
        self.timeout_s = float(timeout_s)
        self.base_backoff_s = float(base_backoff_s)
        self.max_backoff_s = float(max_backoff_s)
        self.max_batch_records = max(1, int(max_batch_records))
        self.max_batch_bytes = max(1024, int(max_batch_bytes))
        self.max_batches_per_run = max(1, int(max_batches_per_run))
        self.transport = transport or _http_post
        self.clock = clock
        self.version = version or loam_version()

    def read_push_state(self) -> dict:
        return _read_json(self.store.push_path)

    def push_once(self, *, now_ts: float | None = None, force: bool = False) -> dict:
        cfg = load_collector_config(self.config_path)
        if not cfg.sending:
            return {"status": "off", "sent": 0, "batches": 0}

        at = float(self.clock() if now_ts is None else now_ts)
        self.store.dir.mkdir(parents=True, exist_ok=True)
        with self.store.push_lock_path.open("a+", encoding="utf-8") as lk:
            try:
                fcntl.flock(lk.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    return {"status": "busy", "sent": 0, "batches": 0}
                raise
            try:
                return self._push_locked(cfg, at, force=force)
            finally:
                fcntl.flock(lk.fileno(), fcntl.LOCK_UN)

    def _push_locked(self, cfg: CollectorConfig, at: float, *, force: bool) -> dict:
        pst = _read_json(self.store.push_path)
        if pst.get("collector_url") != cfg.collector_url:
            pst = {
                "collector_url": cfg.collector_url,
                "acked_seq": 0,
                "consecutive_failures": 0,
                "next_attempt_at": 0.0,
                "last_push_at": None,
                "last_error": None,
                "last_error_at": None,
            }
            _write_json_atomic(self.store.push_path, pst)

        next_at = float(pst.get("next_attempt_at") or 0.0)
        if not force and at < next_at:
            return {
                "status": "backoff",
                "sent": 0,
                "batches": 0,
                "acked_seq": int(pst.get("acked_seq") or 0),
                "next_attempt_at": next_at,
            }

        acked_seq = int(pst.get("acked_seq") or 0)
        total_sent = 0
        batches_sent = 0

        for _ in range(self.max_batches_per_run):
            records = self.store.read_after(
                acked_seq,
                max_records=self.max_batch_records,
                max_bytes=max(512, self.max_batch_bytes - 512),
            )
            if not records:
                break

            batch_max_seq = max(int(r["seq"]) for r in records)
            envelope = build_wire_batch(
                records,
                box_id=cfg.box_id,
                plan=cfg.plan,
                version=self.version,
                sent_at=now_iso(at),
            )
            raw = json.dumps(envelope, separators=(",", ":")).encode("utf-8")

            try:
                status, resp_body, retry_after = self.transport(
                    cfg.collector_url,
                    cfg.token,
                    raw,
                    timeout_s=self.timeout_s,
                    version=self.version,
                )
            except Exception as exc:  # noqa: BLE001
                err = _sanitize_transport_error(exc)
                self._record_failure(pst, at, err, retry_after=None)
                return {
                    "status": "error",
                    "error": err,
                    "sent": total_sent,
                    "batches": batches_sent,
                    "acked_seq": acked_seq,
                }

            if 200 <= status < 300:
                resp_acked = resp_body.get("acked_seq") if isinstance(resp_body, dict) else None
                if _is_int(resp_acked) and acked_seq < resp_acked <= batch_max_seq:
                    acked_seq = int(resp_acked)
                else:
                    acked_seq = batch_max_seq
                total_sent += len(records)
                batches_sent += 1
                pst["acked_seq"] = acked_seq
                pst["consecutive_failures"] = 0
                pst["next_attempt_at"] = 0.0
                pst["last_push_at"] = now_iso(at)
                pst["last_error"] = None
                pst["last_error_at"] = None
                _write_json_atomic(self.store.push_path, pst)
                # If fewer than a full batch remained, we are caught up.
                if len(records) < self.max_batch_records:
                    remaining = self.store.read_after(acked_seq, max_records=1)
                    if not remaining:
                        break
            else:
                err = f"HTTP {status}"
                self._record_failure(pst, at, err, retry_after=retry_after, http_status=status)
                return {
                    "status": "error",
                    "error": err,
                    "sent": total_sent,
                    "batches": batches_sent,
                    "acked_seq": acked_seq,
                }

        return {
            "status": "sent" if batches_sent else "idle",
            "sent": total_sent,
            "batches": batches_sent,
            "acked_seq": acked_seq,
        }

    def _record_failure(
        self,
        pst: dict,
        at: float,
        err: str,
        *,
        retry_after: int | None,
        http_status: int | None = None,
    ) -> None:
        failures = int(pst.get("consecutive_failures") or 0) + 1
        delay = min(self.max_backoff_s, self.base_backoff_s * (2 ** min(failures - 1, 10)))
        if http_status in (401, 403):
            delay = max(delay, min(self.max_backoff_s, 1800.0))
        if retry_after is not None and retry_after > 0:
            delay = max(delay, min(self.max_backoff_s, float(retry_after)))
        pst["consecutive_failures"] = failures
        pst["next_attempt_at"] = at + delay
        pst["last_error"] = err
        pst["last_error_at"] = now_iso(at)
        _write_json_atomic(self.store.push_path, pst)


# -- status summary for CLI --------------------------------------------------

def status_summary(
    *,
    store: UsageStore | None = None,
    config_path: Path | None = None,
) -> dict:
    st_store = store or UsageStore()
    pub = public_status(config_path)
    state = st_store.read_state()
    push = _read_json(st_store.push_path)
    archives = st_store._archive_entries()
    last_seq = int(state.get("last_seq") or 0)
    acked_seq = int(push.get("acked_seq") or 0) if pub["sending"] and push.get("collector_url") == pub["destination"] else 0
    latest = st_store.read_tail(1)
    return {
        **pub,
        "dir": str(st_store.dir),
        "total_bytes": st_store.total_bytes(),
        "archive_files": len(archives),
        "last_seq": last_seq,
        "pending": max(0, last_seq - acked_seq) if pub["sending"] else 0,
        "last_push_at": push.get("last_push_at") if pub["sending"] else None,
        "last_error": push.get("last_error") if pub["sending"] else None,
        "latest": latest[-1] if latest else None,
    }
