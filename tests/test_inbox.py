#!/usr/bin/env python3
"""Check failure and status reporting in an isolated inbox, including delivery across
repeated runs.

Run: python3 tests/test_inbox.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import pathlib
import shutil
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))

ATTENTION = Path(tempfile.mkdtemp(prefix="loam-inbox-attention-"))
import os  # noqa: E402
os.environ["LOAM_ATTENTION_DIR"] = str(ATTENTION)
os.environ.setdefault("LOAM_WAITERS_DIR", str(ATTENTION / "waiters"))

import json  # noqa: E402
import loam_inbox  # noqa: E402


fails = 0


def check(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'' if cond else '  | ' + detail}")


def load_sweep():
    """bin/sweep-inbox has no .py suffix, so it needs an explicit loader --
    the same trick bin/check-drift and bin/wake-failed use."""
    path = AGENTS / "bin" / "sweep-inbox"
    loader = importlib.machinery.SourceFileLoader("sweep_inbox", str(path))
    spec = importlib.util.spec_from_loader("sweep_inbox", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Aged:
    """A stat result with its times moved back. ctime cannot be backdated on
    a real file by any tool -- which is the property that makes it the right
    clock for 'when did this enter the archive' -- so the test moves it here
    rather than pretending the filesystem can."""

    def __init__(self, real, ts):
        self._real, self._ts = real, ts

    def __getattr__(self, key):
        return getattr(self._real, key)

    @property
    def st_ctime(self):
        return self._ts

    @property
    def st_mtime(self):
        return self._ts


sweep = load_sweep()

# ---------------------------------------------------------------- archive --
root = Path(tempfile.mkdtemp(prefix="loam-inbox-test-"))
try:
    archive = root / "00_INBOX" / "archive"
    archive.mkdir(parents=True)
    (archive / "at-31-days.md").write_text("gone\n")
    (archive / "at-29-days.md").write_text("stays\n")
    (archive / "README.md").write_text("structure, never swept\n")
    nested = archive / "a-folder"
    nested.mkdir()
    (nested / "at-31-days.md").write_text("gone, at depth\n")

    now = time.time()
    ages = {"at-31-days.md": 31, "at-29-days.md": 29, "README.md": 400}
    real_stat = pathlib.Path.stat

    def fake_stat(self, *a, **k):
        real = real_stat(self, *a, **k)
        days = ages.get(self.name)
        return real if days is None else Aged(real, now - days * 86400)

    pathlib.Path.stat = fake_stat
    try:
        removed = sweep.sweep_archive(archive, timedelta(days=30), now, False)
    finally:
        pathlib.Path.stat = real_stat

    check("archive: 31-day file removed", not (archive / "at-31-days.md").exists())
    check("archive: 29-day file survives", (archive / "at-29-days.md").exists())
    check("archive: 400-day README.md survives, it is structure",
          (archive / "README.md").exists())
    check("archive: recurses into subfolders", not (nested / "at-31-days.md").exists())
    check("archive: reported exactly the two it removed", removed == 2, str(removed))
finally:
    shutil.rmtree(root, ignore_errors=True)

# ------------------------------------------- the log, and nothing posted --
root = Path(tempfile.mkdtemp(prefix="loam-inbox-test-"))
try:
    def facts():
        return [json.loads(p.read_text()) for p in sorted(ATTENTION.glob("2*.json"))]

    loam_inbox.record_failure("testagent", root, "it broke. And here is the long tail.")
    log = (root / loam_inbox.FAILURE_LOG).read_text(encoding="utf-8")
    check("failure: the log carries the whole reason",
          "it broke. And here is the long tail." in log, log)
    check("failure creates only its ledger", list(root.rglob("*.md")) == [root / loam_inbox.FAILURE_LOG])
    check("failure: a persona's own failure is told to nobody else",
          facts() == [], str(facts()))

    loam_inbox.record_failure("testagent", root, "it broke. And here is the long tail.")
    log2 = (root / loam_inbox.FAILURE_LOG).read_text(encoding="utf-8")
    check("failure: the log records the repeat, it is a log",
          log2.count("it broke.") == 2, log2)

    # A fault that is no one persona's: logged, and one fact.
    loam_inbox.record_fault("lane-check", root, "ada: `_active.md` is 10,001 characters.")
    loam_inbox.record_fault("lane-check", root, "ada: `_active.md` is 10,240 characters.")
    told = facts()
    check("fault: told once though it happened twice (numbers are not the fact)",
          len(told) == 1 and "10,001" in told[0]["fact"], str(told))
    check("fault: its evidence is the failure log",
          told and told[0]["evidence"] == str(root / loam_inbox.FAILURE_LOG), str(told))
    check("fault: both occurrences are in the log",
          (root / loam_inbox.FAILURE_LOG).read_text().count("_active.md") == 2)
    loam_inbox.record_fault("lane-check", root, "ada: a different thing broke.")
    check("fault: a different fact is told", len(facts()) == 2)

finally:
    shutil.rmtree(root, ignore_errors=True)

# ------------------------------------------- the sweep touches nothing else --
root = Path(tempfile.mkdtemp(prefix="loam-inbox-test-"))
try:
    inbox = root / "00_INBOX"
    for sub in ("artifacts", "archive", "from-agents"):
        (inbox / sub).mkdir(parents=True)
    keep = [
        inbox / "a note the owner wrote.md",
        inbox / "artifacts" / "an old artifact.md",
        inbox / "from-agents" / "loam-wake-failures.md",
    ]
    for f in keep:
        f.write_text("x\n")
    doomed = inbox / "archive" / "genuinely archived.md"
    doomed.write_text("x\n")
    dated = inbox / "Personal notes.md"
    dated.write_bytes(b"- 2020-01-01: an old entry\r\n")
    far = time.time() + 9999 * 86400
    sweep.sweep_archive(inbox / "archive", timedelta(days=30), far, False)

    check("scope: the archived file is deleted", not doomed.exists())
    check("scope: nothing outside archive/ is touched",
          all(f.exists() for f in keep),
          str([str(f) for f in keep if not f.exists()]))
    check("dated personal notes retain their bytes", dated.read_bytes() == b"- 2020-01-01: an old entry\r\n")
finally:
    shutil.rmtree(root, ignore_errors=True)

shutil.rmtree(ATTENTION, ignore_errors=True)
print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)
