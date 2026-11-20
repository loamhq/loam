#!/usr/bin/env python3
"""Ensure that temporary probe threads, sessions and records are removed after
execution.

Run: python3 tests/test_probe_cleanup.py
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True

fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"  | got={got!r} want={want!r}"))


loader = importlib.machinery.SourceFileLoader("broker_probe", str(ROOT / "bin" / "broker-probe"))
bp = importlib.util.module_from_spec(importlib.util.spec_from_loader("broker_probe", loader))
loader.exec_module(bp)


def fake(answers):
    sent = []

    def call(method, path, body=None):
        sent.append((method, path))
        if path == "/v1/sessions/live":
            return 200, {"sessions": [{"thread_id": "t-1", "agent": "ada", "id": "s-1"},
                                      {"thread_id": "t-other", "agent": "ada", "id": "s-2"}]}
        if method == "DELETE" and path.startswith("/v1/threads/"):
            return answers.pop(0)
        if method == "PATCH":
            return 200, {"archived": True}
        return 200, {}
    bp.call = call
    bp.time.sleep = lambda s: None
    return sent


sent = fake([(200, {"deleted": True})])
with contextlib.redirect_stdout(io.StringIO()):
    got = bp.clean_up("t-1", "ada")
check("the probe's thread is deleted", got, "deleted")
check("...after its own member's process, and nothing else is touched",
      [s for s in sent if s[0] != "GET"],
      [("DELETE", "/v1/sessions/s-1"), ("DELETE", "/v1/threads/t-1")])

sent = fake([(409, {}), (409, {}), (200, {"deleted": True})])
with contextlib.redirect_stdout(io.StringIO()):
    got = bp.clean_up("t-1", "ada")
check("a turn still finishing is waited for", got, "deleted")

sent = fake([(500, {})])
with contextlib.redirect_stdout(io.StringIO()) as out:
    got = bp.clean_up("t-1", "ada")
check("a thread that cannot be deleted is archived", (got, sent[-1]),
      ("archived", ("PATCH", "/v1/threads/t-1")))
check("...and says so", "NOT deleted" in out.getvalue(), True)

src = (ROOT / "bin" / "broker-probe").read_text(encoding="utf-8")
check("the probe's body runs inside the clean-up's try/finally",
      bool(re.search(r"try:\n\s+return probe\(.*\)\n\s+finally:\n\s+if not args\.keep:\n"
                     r"\s+clean_up\(", src)), True)

# 2. The runner's scratch stores.
runner = (ROOT / "tests" / "run-all").read_text(encoding="utf-8")
for var in ("LOAM_THREADS_STATE", "LOAM_VAULT"):
    check(f"run-all gives every test a scratch {var}",
          f'"{var}"' in runner and "def sealed_env(scratch" in runner, True)
print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
