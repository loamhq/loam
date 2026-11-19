#!/usr/bin/env python3
"""bin/memsample's rotation and daily du, and check-memory's reading of it.

  rotation     the live file passes MAX_BYTES and becomes .1, replacing the
               last; two files at most, so the history is bounded.
  holders      the du runs on the first sample of a day at or after 01:45
               and on no other.
  history      check-memory's 24 h peak per unit (sampled current for the
               broker, the cgroup's own peak for a per-run
               unit), the disk delta per mount against the newest sample a
               day older, the holders against the day before, and the two
               warnings: a broker near its MemoryHigh and a stopped sampler.

Everything runs against the shipped scripts with a throwaway log file.

Run: python3 tests/test_memsample.py
"""

from __future__ import annotations

import datetime as dt
import importlib.machinery
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"
results: list[bool] = []


def load(name: str):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(BIN / name))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not ok else ""))


ms = load("memsample")
cm = load("check-memory")
TZ = dt.timezone(dt.timedelta(hours=-7))
NOON = dt.datetime(2026, 10, 13, 12, 0, tzinfo=TZ)

# --- rotation ---------------------------------------------------------------
tmp = Path(tempfile.mkdtemp(prefix="loam-memsample-"))
out = tmp / "memsample.jsonl"
line = "x" * 99
for _ in range(25):
    ms.append(line, out, max_bytes=1000)
rotated = out.with_name(out.name + ".1")
files = sorted(p.name for p in tmp.iterdir())
check("rotation keeps two files at most", files == ["memsample.jsonl", "memsample.jsonl.1"], str(files))
check("each file stops at the bound plus one line",
      all(p.stat().st_size <= 1000 + 100 for p in tmp.iterdir()),
      str([p.stat().st_size for p in tmp.iterdir()]))
check("nothing is lost but the oldest file",
      out.stat().st_size + rotated.stat().st_size == 100 * 15)

# --- holders once a day -----------------------------------------------------
log = tmp / "h.jsonl"
check("no du before 01:45", not ms.holders_due(NOON.replace(hour=1, minute=40), log))
check("du on the first sample after 01:45", ms.holders_due(NOON.replace(hour=1, minute=45), log))
log.write_text(json.dumps({"v": 2, "ts": "2026-10-13T01:45:00-07:00", "holders": {}},
                          separators=(",", ":")) + "\n")
check("no second du the same day", not ms.holders_due(NOON, log))
check("du again the next day", ms.holders_due(NOON + dt.timedelta(days=1), log))

# --- check-memory's history report ------------------------------------------


def row(at: dt.datetime, broker: int, root: int, wake: dict | None = None,
        holders: dict | None = None) -> dict:
    units = {
        "loam-session-broker": {"cur": broker, "peak": 1801, "high": 1800},
    }
    if wake:
        units["loam-wake-loom"] = wake
    r = {"v": 2, "ts": at.isoformat(timespec="seconds"), "units": units,
         "mounts": {"/": {"used": root, "size": 76600}}}
    if holders:
        r["holders"] = holders
    return r


rows = [
    row(NOON - dt.timedelta(hours=26), 1790, 40000, holders={"vault": 8000, "runroot-4a": 6100}),
    row(NOON - dt.timedelta(hours=25, minutes=10), 1795, 40100),
    row(NOON - dt.timedelta(hours=10), 1700, 40300,
        wake={"cur": 300, "peak": 712, "high": 1024}, holders={"vault": 8050, "runroot-4a": 8301}),
    row(NOON - dt.timedelta(minutes=5), 1650, 40402),
]
lines, warnings = cm.history_report(rows, NOON)
text = "\n".join(lines)
check("broker peak is the sampled current, with its High",
      "session-broker    1700 MB of 1800 high (94%)" in text, text)
check("a sample older than 24 h is outside the peak", "1795 MB" not in text, text)
check("a per-run unit reports its cgroup's own peak", "wake max          712 MB" in text, text)
check("disk delta is against the newest sample a day older",
      "since 2026-10-12T10:50" in text and "40402 MB used of 76600 MB (+302 MB)" in text, text)
check("holders are against the day before",
      "runroot-4a        8301 MB (+2201 MB)" in text and "vault             8050 MB (+50 MB)" in text, text)
check("a long holder name keeps a space before its size",
      all(" MB" not in ln.split()[0] for ln in lines if ln.startswith("  ")), text)
check("a broker at 90% of its High warns", any("session broker peaked" in w for w in warnings),
      str(warnings))
check("a live history does not warn as stopped", not any("stopped" in w for w in warnings))

_, warnings = cm.history_report(rows, NOON + dt.timedelta(hours=2))
check("a sampler silent for two hours warns", any("history stopped" in w for w in warnings))
lines, warnings = cm.history_report([{"v": 2, "ts": NOON.isoformat(), "error": "x"}], NOON)
check("no samples is a line and a warning, not a crash",
      "none" in lines[0] and any("loam-memsample.timer" in w for w in warnings))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
