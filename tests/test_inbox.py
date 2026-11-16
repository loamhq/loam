#!/usr/bin/env python3
"""The inbox outlet and its sweep, both halves proven against a temp vault.

Two things here can destroy something the owner wanted: the archive sweep
deletes files, and the comms sweep rewrites a file line by line. Both are
bounded by a date, and a date boundary is exactly the kind of thing that is
off by one and looks fine. So both are tested at the boundary from both
sides, plus the cases where the sweep must do nothing at all.

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

# ------------------------------------------------------------------ comms --
root = Path(tempfile.mkdtemp(prefix="loam-inbox-test-"))
try:
    comms = root / loam_inbox.COMMS
    comms.parent.mkdir(parents=True)
    today = date(2026, 9, 5)
    old, young = today - timedelta(days=15), today - timedelta(days=13)
    comms.write_text(
        loam_inbox.COMMS_HEADER
        .replace("## Urgent\n",
                 f"## Urgent\n\n- {old} · **a** · fifteen days · [[x]]\n"
                 f"- {young} · **a** · thirteen days · [[x]]\n")
        .replace("## Other\n", "## Other\n\n- a hand-written line with no date\n"),
        encoding="utf-8",
    )
    dropped = sweep.sweep_comms(comms, timedelta(days=14), today, False)
    after = comms.read_text(encoding="utf-8")

    check("comms: 15-day line dropped", "fifteen days" not in after)
    check("comms: 13-day line survives", "thirteen days" in after)
    check("comms: undated line untouched", "a hand-written line with no date" in after)
    check("comms: all three headings stand, empty or not",
          all(f"## {s}" in after for s in loam_inbox.SECTIONS), after)
    check("comms: register note stands", "Nothing pings me" in after)
    check("comms: reported exactly the one it dropped", dropped == 1, str(dropped))

    # An empty section must survive its last line leaving.
    dropped = sweep.sweep_comms(comms, timedelta(days=0), today, False)
    after = comms.read_text(encoding="utf-8")
    check("comms: sections stay when every line expires",
          all(f"## {s}" in after for s in loam_inbox.SECTIONS)
          and "thirteen days" not in after, after)
finally:
    shutil.rmtree(root, ignore_errors=True)

# -------------------------------------------------------- the failure pair --
root = Path(tempfile.mkdtemp(prefix="loam-inbox-test-"))
try:
    loam_inbox.record_failure("testagent", root, "it broke. And here is the long tail.")
    log = (root / loam_inbox.FAILURE_LOG).read_text(encoding="utf-8")
    comms = (root / loam_inbox.COMMS).read_text(encoding="utf-8")

    check("failure: the log carries the whole reason",
          "it broke. And here is the long tail." in log, log)
    check("failure: comms carries the first sentence only",
          "· it broke ·" in comms.replace("it broke.", "it broke"), comms)
    check("failure: comms line is dated first, so the sweep can read it",
          any(sweep.DATED_LINE.match(l) and "testagent" in l
              for l in comms.splitlines()), comms)
    check("failure: comms line links to the log",
          loam_inbox.FAILURE_LOG_LINK in comms, comms)
    check("failure: it lands under Urgent",
          comms.index("## Urgent") < comms.index("**testagent**")
          < comms.index("## Important"), comms)

    loam_inbox.record_failure("testagent", root, "it broke. And here is the long tail.")
    log2 = (root / loam_inbox.FAILURE_LOG).read_text(encoding="utf-8")
    comms2 = (root / loam_inbox.COMMS).read_text(encoding="utf-8")
    check("failure: the log records the repeat, it is a log",
          log2.count("it broke.") == 2, log2)
    check("failure: comms does NOT, it is not a log",
          comms2.count("**testagent**") == 1, comms2)

    loam_inbox.record_failure("testagent", root, "a different thing broke.")
    comms3 = (root / loam_inbox.COMMS).read_text(encoding="utf-8")
    check("failure: a different item is a new line, newest first",
          comms3.index("a different thing") < comms3.index("it broke"), comms3)
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
    (inbox / "from-agents" / "comms.md").write_text(
        loam_inbox.COMMS_HEADER.replace(
            "## Urgent\n", "## Urgent\n\n- 2020-01-01 · **a** · ancient · [[x]]\n"))

    # A clock far enough forward that EVERY file in the tree is past every
    # window. Only the two declared-temporary things may go.
    far = time.time() + 9999 * 86400
    sweep.sweep_archive(inbox / "archive", timedelta(days=30), far, False)
    sweep.sweep_comms(inbox / "from-agents" / "comms.md",
                      timedelta(days=14), date(2099, 1, 1), False)

    check("scope: the archived file is deleted", not doomed.exists())
    check("scope: nothing outside archive/ and comms.md is touched",
          all(f.exists() for f in keep),
          str([str(f) for f in keep if not f.exists()]))
    check("scope: the failure log beside comms.md is not line-swept",
          (inbox / "from-agents" / "loam-wake-failures.md").read_text() == "x\n")
finally:
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)
