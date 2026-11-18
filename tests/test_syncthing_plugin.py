#!/usr/bin/env python3
"""The Syncthing plugin (2026-11-18), first-party and listed, not shipped:
plugins/_listed/syncthing/, a general version for any install.

  - its own tests pass (a fake Syncthing on loopback)
  - its manifest reads with no warning, declares the listener, its one
    loopback host, and that the vault (records and memory) is synced
  - it is not in the Shipped layer, imports nothing of Loam's, and carries
    nothing of one install's: no home path, no device ID but the test's

Run: python3 tests/test_syncthing_plugin.py
"""

import re
import subprocess
import sys
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
FOLDER = AGENTS / "plugins" / "_listed" / "syncthing"
sys.path.insert(0, str(AGENTS / "lib"))
import loam_plugins as lp  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:66}{'  | ' + str(detail)[:400] if detail != '' else ''}")


run = subprocess.run([sys.executable, "-I", str(FOLDER / "tests" / "test_syncthing.py")],
                     capture_output=True, text=True, timeout=120)
check("the plugin's own tests pass", run.returncode == 0, run.stdout[-600:] + run.stderr[-600:])
found = lp.read_folder(FOLDER, "syncthing")
check("its manifest reads with no warning", found["warnings"] == [], found["warnings"])
decl = lp.declaration(found["manifest"], found["has"])
check("it declares Syncthing's listener", decl["touches"].get("inbound", [""])[0].startswith("syncthing:"), decl)
check("its one host is this machine", [n["host"] for n in decl["touches"]["network"]] == ["127.0.0.1"])
check("it says the vault is synced both ways", (decl["touches"]["record"], decl["touches"]["memory"]) == ("write", "write"))
check("it is not a shipped plugin", "syncthing" not in {p.name for p in lp._folders(AGENTS / "plugins")})
code = "".join(p.read_text() for p in (FOLDER / "tools").glob("*.py"))
check("it imports nothing of Loam's", "LOAM_LIB" not in code and "loam_" not in code)
every = "".join(p.read_text() for p in FOLDER.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
ids = set(re.findall(r"[A-Z2-7]{7}(?:-[A-Z2-7]{7}){7}", every))
check("it names no device but the test's own", len(ids) == 2 and all(i.startswith(("AAAAAAA", "MFZWI3D")) for i in ids), ids)
check("it names no home folder", "/home/" not in every)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
