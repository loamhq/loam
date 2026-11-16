#!/usr/bin/env python3
"""bin/check-memory's session count, against a throwaway cgroup tree.

Two things this proves, both found on 2026-10-13:

  a session is a process whose comm is a CLI version. An MCP server called
      `acme` (or `node`) is not one; before the fix it was, and every
      agent read two sessions.
  a running server with no session is named. Iris sat at 0 for five weeks
      inside a total that still looked high.

Driven through ``main()`` of the shipped script with its process readers
replaced, so nothing on the box is read but the script itself.

Run: python3 tests/test_check_memory.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:52}{'  | ' + detail if detail else ''}")


def load():
    loader = importlib.machinery.SourceFileLoader(
        "check_memory_under_test", str(ROOT / "bin" / "check-memory"))
    spec = importlib.util.spec_from_loader("check_memory_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# pid -> comm, per unit. "alpha" is a healthy agent with an MCP server called acme in its
# session; "beta" has one extra conversation open; "gamma" has no session.
UNITS = {
    "alpha": {10: "claude", 11: "2.1.281", 12: "python", 13: "acme", 14: "node"},
    "beta": {20: "claude", 21: "2.1.281", 22: "2.1.281", 23: "python"},
    "gamma": {30: "claude"},
}
COMM = {pid: c for procs in UNITS.values() for pid, c in procs.items()}

cm = load()
check("version comm is a session", cm.is_session("2.1.281"))
for c in ("claude", "python", "acme", "node", "", None):
    check(f"{c!r} is not a session", not cm.is_session(c))

root = Path(tempfile.mkdtemp(prefix="check-memory-test-"))
try:
    for name, procs in UNITS.items():
        d = root / f"loam-rc-{name}.service"
        d.mkdir()
        (d / "cgroup.procs").write_text("\n".join(map(str, procs)) + "\n")
        (d / "memory.current").write_text(str(300 * 1048576))
        (d / "memory.peak").write_text(str(400 * 1048576))
    (root / "memory.current").write_text("0")
    (root / "memory.peak").write_text("0")
    (root / "memory.max").write_text("max")

    cm.CG = root
    cm.roster = lambda: sorted(UNITS)
    cm.comm = COMM.get
    cm.rss_mb = lambda pid: 300
    cm.meminfo = lambda: {"MemTotal": 8000, "MemAvailable": 6000,
                          "SwapFree": 2000, "SwapTotal": 2000}

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cm.main()
    out = buf.getvalue()
    rows = {line.split()[0]: line for line in out.splitlines()
            if line.split() and line.split()[0] in UNITS}

    check("alpha counts one session, not three", rows["alpha"].split()[5] == "1",
          rows["alpha"])
    check("beta counts two", rows["beta"].split()[5] == "2", rows["beta"])
    check("gamma counts none and is flagged", "NO SESSION" in rows["gamma"],
          rows["gamma"])
    check("gamma is named in a warning",
          "WARNING:  gamma: Remote Control server is running with no session" in out)
    check("alpha and beta are not flagged",
          "alpha:" not in out and "beta:" not in out)
    check("extras are per agent: gamma's 0 does not cancel beta's 2",
          "1 extra" in out, [l for l in out.splitlines() if "extra" in l][0])
    check("a missing session is a warning, exit 1", code == 1, str(code))

    # The next MCP server added to a lane, under a name nothing here knows.
    before = len(cm.sessions_of("loam-rc-alpha.service"))
    COMM[15] = "some-future-mcp"
    (root / "loam-rc-alpha.service" / "cgroup.procs").write_text(
        "\n".join(map(str, [*UNITS["alpha"], 15])) + "\n")
    after = len(cm.sessions_of("loam-rc-alpha.service"))
    check("a new non-CLI child does not move the count",
          before == after == 1, f"{before} -> {after}")
finally:
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
