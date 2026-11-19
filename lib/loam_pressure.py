"""Sample RAM and swap each minute. The check and capacity plugins read pressure.json.

Low swap can precede exhaustion even when RAM use is moderate. A sustained
breach of either threshold starts an episode in the pressure log. The
first reading below both thresholds ends it."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import loam_config as lc

PRESSURE = lc.STATE / "pressure.json"
EVENTS = lc.LOGS / "pressure.jsonl"
PRESSURE_PCT = 85
SWAP_FREE_PCT = 15
SUSTAIN_MIN = 3


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


def reading(mi: dict) -> dict:
    total, avail = mi.get("MemTotal", 0), mi.get("MemAvailable", 0)
    st, sf = mi.get("SwapTotal", 0), mi.get("SwapFree", 0)
    ram_pct = round(100 * (total - avail) / total) if total else 0
    swap_free_pct = round(100 * sf / st) if st else 100
    return {"ram_pct": ram_pct, "ram_used_mb": total - avail, "ram_total_mb": total,
            "swap_free_pct": swap_free_pct, "swap_free_mb": sf,
            "over": ram_pct >= PRESSURE_PCT or swap_free_pct < SWAP_FREE_PCT}


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


def pressure_step(prev: dict, r: dict, at: float) -> tuple[dict, str | None]:
    """Return the next state and a report only when an episode begins."""
    streak = prev.get("streak", 0) + 1 if r["over"] else 0
    state = {**r, "at": iso(at), "streak": streak,
             "episode_since": prev.get("episode_since"),
             "threshold": f"RAM >= {PRESSURE_PCT}% or swap < {SWAP_FREE_PCT}% free, "
                          f"{SUSTAIN_MIN} min running"}
    post = None
    if streak >= SUSTAIN_MIN and not prev.get("episode_since"):
        state["episode_since"] = iso(at)
        post = (f"memory pressure on the box since {iso(at)[11:16]}Z: RAM "
                f"{r['ram_pct']}% in use ({r['ram_used_mb']} of {r['ram_total_mb']} MB), "
                f"swap {r['swap_free_pct']}% free, for {SUSTAIN_MIN}+ min")
    elif streak == 0 and prev.get("episode_since"):
        state["episode_since"] = None
        state["last_episode"] = {"since": prev["episode_since"], "until": iso(at)}
    else:
        state["last_episode"] = prev.get("last_episode")
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
        state["episode_says"] = post
        event("pressure", **r)
    elif state.get("episode_since"):
        state["episode_says"] = prev.get("episode_says")
    elif prev.get("episode_since"):
        event("pressure-end", since=prev["episode_since"])
    _write(PRESSURE, state)
    return state
