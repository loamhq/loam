"""Sample RAM and swap each minute. The check and capacity plugins read pressure.json.

Low swap can precede exhaustion even when RAM use is moderate. A sustained
breach of either threshold starts an episode in the pressure log. The
first reading below both thresholds ends it, and records one durable
``pressure_episode`` event in ``lib/loam_limits.py``.

Sustained swap use below the acute pressure alarm is tracked separately
(without alarming ``loam-check``) and records one ``sustained_swap`` event
in ``lib/loam_limits.py`` when the episode ends."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import loam_config as lc
import loam_limits as limits

PRESSURE = lc.STATE / "pressure.json"
EVENTS = lc.LOGS / "pressure.jsonl"
PRESSURE_PCT = 85
SWAP_FREE_PCT = 15
SUSTAIN_MIN = 3

# Sustained swap episode (recorded to loam_limits, does not wake attention):
# triggers when swap usage stays >= 20% of SwapTotal AND >= max(256, min(1024,
# MemTotal // 6)) MB for 5 consecutive minutes.
SWAP_SUSTAINED_PCT = 20
SUSTAIN_SWAP_MIN = 5
# If the sampler did not tick for longer than this (reboot/outage), close an
# open episode at its last observed tick rather than counting downtime.
GAP_CAP_S = 600


def now() -> float:
    return time.time()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n")
    tmp.replace(path)


def event(kind: str, **fields) -> None:
    EVENTS.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS.open("a") as f:
        f.write(json.dumps({"ts": iso(now()), "kind": kind, **fields}) + "\n")


def meminfo(path: Path = Path("/proc/meminfo")) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        k, _, v = line.partition(":")
        out[k] = int(v.split()[0]) // 1024
    return out


def swap_sustained_floor_mb(ram_total_mb: int) -> int:
    """Minimum swap used (MB) to count as sustained swap on a box of ``ram_total_mb``."""
    return max(256, min(1024, max(0, ram_total_mb) // 6))


def reading(mi: dict) -> dict:
    total, avail = mi.get("MemTotal", 0), mi.get("MemAvailable", 0)
    st, sf = mi.get("SwapTotal", 0), mi.get("SwapFree", 0)
    swap_used = max(0, st - sf)
    ram_pct = round(100 * (total - avail) / total) if total else 0
    swap_free_pct = round(100 * sf / st) if st else 100
    swap_used_pct = round(100 * swap_used / st) if st else 0
    swap_over = bool(
        st > 0
        and swap_used >= swap_sustained_floor_mb(total)
        and swap_used * 100 >= st * SWAP_SUSTAINED_PCT
    )
    return {
        "ram_pct": ram_pct,
        "ram_used_mb": max(0, total - avail),
        "ram_total_mb": total,
        "ram_available_mb": avail,
        "swap_total_mb": st,
        "swap_used_mb": swap_used,
        "swap_used_pct": swap_used_pct,
        "swap_free_pct": swap_free_pct,
        "swap_free_mb": sf,
        "over": ram_pct >= PRESSURE_PCT or swap_free_pct < SWAP_FREE_PCT,
        "swap_over": swap_over,
    }


def heavy_units(top: int = 3) -> list[str]:
    """List the largest service cgroups for the episode report."""
    cg = lc.USER_SLICE / "app.slice"
    found = []
    for d in cg.glob("loam-*"):
        try:
            found.append((int((d / "memory.current").read_text()) // 1048576,
                          d.name.removesuffix(".service")))
        except (OSError, ValueError):
            pass
    return [f"{n} {mb} MB" for mb, n in sorted(found, reverse=True)[:top]]


def _snap_from_reading(r: dict) -> dict:
    total = int(r.get("ram_total_mb") or 0)
    avail = r.get("ram_available_mb")
    if avail is None:
        avail = max(0, total - int(r.get("ram_used_mb") or 0))
    st = int(r.get("swap_total_mb") or 0)
    sf = int(r.get("swap_free_mb") or 0)
    su = int(r.get("swap_used_mb") if r.get("swap_used_mb") is not None else max(0, st - sf))
    box_mb = total if total > 0 else limits.box_memory_mb()
    return {
        "total_mb": total,
        "available_mb": avail,
        "reserve_mb": limits.memory_reserve_mb(box_mb),
        "start_mb": limits.start_allowance_mb(box_mb),
        "swap_total_mb": st,
        "swap_used_mb": su,
        "swap_free_mb": sf,
    }


def _worse_snap(prev_peak: dict | None, cur: dict) -> dict:
    if not isinstance(prev_peak, dict):
        return cur
    out = dict(prev_peak)
    if cur.get("available_mb") is not None and (
        out.get("available_mb") is None or cur["available_mb"] < out["available_mb"]
    ):
        out["available_mb"] = cur["available_mb"]
    if cur.get("swap_used_mb", 0) > out.get("swap_used_mb", 0):
        out["swap_used_mb"] = cur["swap_used_mb"]
        out["swap_free_mb"] = cur["swap_free_mb"]
    return out


def _episode_end_ts(prev: dict, at: float) -> float:
    prev_at = limits.parse_iso(prev.get("at"))
    if prev_at is not None and at - prev_at > GAP_CAP_S:
        return prev_at
    return at


def pressure_step(prev: dict, r: dict, at: float) -> tuple[dict, str | None]:
    """Return the next state and a report only when an acute pressure episode begins."""
    streak = prev.get("streak", 0) + 1 if r["over"] else 0
    swap_streak = prev.get("swap_streak", 0) + 1 if r.get("swap_over") else 0
    snap = _snap_from_reading(r)
    state: dict = {
        **r,
        "at": iso(at),
        "streak": streak,
        "episode_since": prev.get("episode_since"),
        "episode_id": prev.get("episode_id"),
        "episode_peak": prev.get("episode_peak"),
        "episode_max_ram_pct": prev.get("episode_max_ram_pct"),
        "episode_min_swap_free_pct": prev.get("episode_min_swap_free_pct"),
        "episode_heaviest": prev.get("episode_heaviest"),
        "swap_streak": swap_streak,
        "swap_episode_since": prev.get("swap_episode_since"),
        "swap_episode_id": prev.get("swap_episode_id"),
        "swap_episode_peak": prev.get("swap_episode_peak"),
        "threshold": f"RAM >= {PRESSURE_PCT}% or swap < {SWAP_FREE_PCT}% free, "
                     f"{SUSTAIN_MIN} min running",
    }
    post = None
    if streak >= SUSTAIN_MIN and not prev.get("episode_since"):
        state["episode_since"] = iso(at)
        state["episode_id"] = limits._make_id(at)
        state["episode_peak"] = snap
        state["episode_max_ram_pct"] = r["ram_pct"]
        state["episode_min_swap_free_pct"] = r["swap_free_pct"]
        state["episode_heaviest"] = None
        post = (f"memory pressure on the box since {iso(at)[11:16]}Z: RAM "
                f"{r['ram_pct']}% in use ({r['ram_used_mb']} of {r['ram_total_mb']} MB), "
                f"swap {r['swap_free_pct']}% free, for {SUSTAIN_MIN}+ min")
    elif streak > 0 and prev.get("episode_since"):
        state["episode_peak"] = _worse_snap(prev.get("episode_peak"), snap)
        state["episode_max_ram_pct"] = max(prev.get("episode_max_ram_pct") or 0, r["ram_pct"])
        prev_min = prev.get("episode_min_swap_free_pct")
        state["episode_min_swap_free_pct"] = (
            r["swap_free_pct"] if prev_min is None else min(prev_min, r["swap_free_pct"])
        )
        state["last_episode"] = prev.get("last_episode")
    elif streak == 0 and prev.get("episode_since"):
        end_ts = _episode_end_ts(prev, at)
        state["episode_since"] = None
        state["episode_id"] = None
        state["episode_peak"] = None
        state["episode_max_ram_pct"] = None
        state["episode_min_swap_free_pct"] = None
        state["episode_heaviest"] = None
        state["last_episode"] = {"since": prev["episode_since"], "until": iso(end_ts)}
    else:
        state["last_episode"] = prev.get("last_episode")

    if swap_streak >= SUSTAIN_SWAP_MIN and not prev.get("swap_episode_since"):
        state["swap_episode_since"] = iso(at)
        state["swap_episode_id"] = limits._make_id(at)
        state["swap_episode_peak"] = snap
    elif swap_streak > 0 and prev.get("swap_episode_since"):
        state["swap_episode_peak"] = _worse_snap(prev.get("swap_episode_peak"), snap)
        state["last_swap_episode"] = prev.get("last_swap_episode")
    elif swap_streak == 0 and prev.get("swap_episode_since"):
        end_ts = _episode_end_ts(prev, at)
        state["swap_episode_since"] = None
        state["swap_episode_id"] = None
        state["swap_episode_peak"] = None
        state["last_swap_episode"] = {"since": prev["swap_episode_since"], "until": iso(end_ts)}
    else:
        state["last_swap_episode"] = prev.get("last_swap_episode")

    return state, post


def tick() -> dict:
    at = now()
    r = reading(meminfo())
    prev = _read(PRESSURE)
    state, post = pressure_step(prev, r, at)
    if post:
        heavy = heavy_units()
        if heavy:
            post += ". Heaviest now: " + ", ".join(heavy)
            state["episode_heaviest"] = heavy
        state["episode_says"] = post
        event("pressure", **r)
    elif state.get("episode_since"):
        state["episode_says"] = prev.get("episode_says")
    elif prev.get("episode_since"):
        event("pressure-end", since=prev["episode_since"])
        end_ts = _episode_end_ts(prev, at)
        start_ts = limits.parse_iso(prev["episode_since"])
        dur = round(max(0.0, end_ts - start_ts), 1) if start_ts is not None else None
        limits.record_safe(
            "pressure_episode",
            outcome="ended",
            duration_s=dur,
            started_at=prev["episode_since"],
            event_id=prev.get("episode_id"),
            ts=end_ts,
            memory=prev.get("episode_peak") or _snap_from_reading(r),
            detail={
                "ram_pct": prev.get("episode_max_ram_pct"),
                "swap_free_pct": prev.get("episode_min_swap_free_pct"),
                "heaviest": prev.get("episode_heaviest"),
            },
        )
    if not state.get("swap_episode_since") and prev.get("swap_episode_since"):
        end_ts = _episode_end_ts(prev, at)
        start_ts = limits.parse_iso(prev["swap_episode_since"])
        dur = round(max(0.0, end_ts - start_ts), 1) if start_ts is not None else None
        limits.record_safe(
            "sustained_swap",
            outcome="ended",
            duration_s=dur,
            started_at=prev["swap_episode_since"],
            event_id=prev.get("swap_episode_id"),
            ts=end_ts,
            memory=prev.get("swap_episode_peak") or _snap_from_reading(r),
            detail={"reason": "swap"},
        )
    _write(PRESSURE, state)
    return state
