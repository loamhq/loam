#!/usr/bin/env python3
"""Check sampler rotation and daily disk surveys with a throwaway log."""

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

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
