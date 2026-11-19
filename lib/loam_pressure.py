"""Memory pressure on the box: one reading a minute, one line when it begins.

Every install needs it, so it is core (2026-11-14, first release L5; until
then it lived inside the module that resizes this box, which is a plugin of
the owner's now). RAM in use at or above ``PRESSURE_PCT`` for
``SUSTAIN_MIN`` consecutive minutes, or swap nearly gone, is an episode. Its
start and end are recorded (``logs/pressure.jsonl``) and the live reading is
``<state>/pressure.json``, with the episode's sentence and the heaviest units
in ``episode_says``. This module tells nobody since 2026-11-19: it is a
sampler, and the one check on a clock (lib/loam_check.py) reads the episode
from that file as one of its facts and hands it to the agent that hears when
Loam needs attention. A plugin that can add capacity reads the same file, and
says how to pull its lever in its own words to the agents.

Why 85% for 3 minutes, measured on logs/memsample.jsonl 2026-10-13..11-06
(6,994 samples every 5 min): RAM in use was p50 50%, p95 65%, p99 73%, max
90%, and at or above 85% for two samples running (10 minutes) never once. So
the line does not fire on an ordinary heavy day, and when it fires it is new.
Swap below 15% free was the common term in all four global kills
(the retired bin/check-memory); swap reached 77 MB free on 10-27 with RAM at 75%, which
the RAM line alone would have missed.
"""

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
    """The biggest loam-* cgroups now, for the line that names the pressure."""
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
    """The next pressure state from the last one and this minute's reading,
    and the fact to tell if an episode just began. Pure, for tests."""
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
