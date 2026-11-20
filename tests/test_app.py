#!/usr/bin/env python3
"""Run the app's npm test suite as part of the repository landing suite.

Run: python3 tests/test_app.py"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"


def main() -> int:
    node = shutil.which("node")
    if node is None:
        print("FAIL  node is not on PATH; the app's suite needs Node 22 or newer")
        return 1
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOAM_")}
    env.setdefault("TMPDIR", os.environ.get("TMPDIR", "/tmp"))
    res = subprocess.run(["npm", "test", "--silent"], cwd=APP, env=env,
                         capture_output=True, text=True, timeout=1800)
    out = res.stdout + res.stderr
    summary = [ln for ln in out.splitlines() if ln.startswith(("ℹ tests", "ℹ pass", "ℹ fail"))]
    failed = [ln for ln in out.splitlines() if ln.lstrip().startswith("✖")]
    print("\n".join(summary))
    if res.returncode != 0:
        print("\n".join(failed[:40]) or out[-4000:])
        print(f"FAIL  npm test in app/ exited {res.returncode}")
        return 1
    print("PASS  the app's suite (npm test in app/)")
    print("\nOK: 0 failure(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
