#!/usr/bin/env python3
"""bin/check-memory's verdict on headroom, against a throwaway cgroup tree.

Until 2026-11-19 this proved the Remote Control session count; Remote
Control was retired that day and the count with it. What is left: headroom
below one wake is critical, below two is a warning, swap nearly gone is
critical, and no Remote Control row or warning is printed at all.

Driven through ``main()`` of the shipped script with its readers replaced,
so nothing on the box is read but the script itself.

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


cm = load()
root = Path(tempfile.mkdtemp(prefix="check-memory-test-"))
try:
    for name in ("memory.current", "memory.peak"):
        (root / name).write_text("0")
    (root / "memory.max").write_text("max")
    cm.CG = root
    cm.history_report = lambda rows, at: ([], [])

    def run(avail: int, swap_free: int = 2000) -> tuple[int, str]:
        cm.meminfo = lambda: {"MemTotal": 8000, "MemAvailable": avail,
                              "SwapFree": swap_free, "SwapTotal": 2000}
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cm.main()
        return code, buf.getvalue()

    code, out = run(6000)
    check("plenty of headroom: OK, exit 0", code == 0 and "OK --" in out, out[-120:])
    check("no Remote Control row or warning",
          "Remote Control" not in out and "sessions" not in out, out)
    code, out = run(cm.WAKE_PEAK_MB * 2 - 1 - 2000)
    check("room for one wake only: a warning, exit 1",
          code == 1 and "fits one wake but not two" in out, out[-160:])
    code, out = run(cm.WAKE_PEAK_MB - 1 - 2000)
    check("not room for one wake: critical, exit 2",
          code == 2 and "below the heaviest wake" in out, out[-160:])
    code, out = run(6000, swap_free=100)
    check("swap nearly gone: critical", code == 2 and "swap 100/2000" in out, out[-160:])
finally:
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
