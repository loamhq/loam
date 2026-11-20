#!/usr/bin/env python3
"""Check sampler rotation and daily disk surveys with a throwaway log."""

from __future__ import annotations

import datetime as dt
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

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

# A cold page reads capacity immediately. Only the sampler walks directories.
storage = ms.loam_storage
vault = tmp / "vault"
records = vault / "records"
spaces = tmp / "spaces"
records.mkdir(parents=True)
spaces.mkdir()
(records / "conversation").write_bytes(b"record" * 1500)
(spaces / "notes").hardlink_to(records / "conversation")
(vault / "other").write_bytes(b"vault" * 1000)
cache = tmp / "activity-storage.json"
device = vault.stat().st_dev
check("cold breakdown leaves live disk totals available",
      storage.storage(vault, cache)["breakdown"] is None and storage.disk(vault)["total_bytes"] > 0)
counts = storage.allocated([vault, spaces, records], device)


def allocated(path):
    return path.stat().st_blocks * 512


check("nested records and cross-group hardlinks are counted once",
      counts[records] == allocated(records) + allocated(records / "conversation") and
      counts[vault] == allocated(vault) + allocated(vault / "other") and
      counts[spaces] == allocated(spaces))
check("a root on another filesystem contributes no bytes",
      storage.allocated([spaces], device + 1) == {spaces: 0})

now = dt.datetime.now(TZ)
with (patch.object(ms.lc, "VAULT", vault), patch.object(ms.lc, "WORKSPACES", spaces),
      patch.object(ms.lc, "STATE", tmp / "state"), patch.object(ms.lc, "vault_folder", return_value="records"),
      patch.object(storage, "CONTAINER_ROOTS", [tmp / "unused-docker"]),
      patch.object(storage, "survey", wraps=storage.survey) as scan):
    storage.refresh(now, cache)
    sample = storage.storage(vault, cache)["breakdown"]
    check("survey publishes all named groups and the observation time",
          set(sample["groups"]) == {"vault", "workspaces", "records", "containers", "other"} and
          sample["sampled_at"] == now.isoformat(timespec="seconds") and
          sample["groups"]["records"] == counts[records] and sample["groups"]["other"] > 0)
    with patch.object(storage.subprocess, "run", side_effect=AssertionError("unexpected disk walk")):
        storage.refresh(now + dt.timedelta(minutes=5), cache)
        check("repeated reads and a warm sampler pass never walk disk",
              all(storage.storage(vault, cache)["breakdown"] == sample for _ in range(4)) and scan.call_count == 1)
    os.utime(cache, (now.timestamp() - 3600,) * 2)
    storage.refresh(now, cache)
    check("an expired cache is refreshed once", scan.call_count == 2)

cache.write_text("invalid")
check("a damaged cache does not hide capacity", storage.storage(vault, cache)["breakdown"] is None)
with patch.object(storage.os, "statvfs", return_value=SimpleNamespace(
        f_blocks=100, f_bfree=30, f_bavail=25, f_frsize=4096)):
    check("free space excludes reserved blocks and used space excludes free blocks",
          storage.disk(vault) == {"total_bytes": 409600, "used_bytes": 286720, "free_bytes": 102400})

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
