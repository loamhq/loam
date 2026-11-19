#!/usr/bin/env python3
"""The Images plugin (2026-11-18): built into Loam since 0.2.10, in
plugins/images/ (it was listed in the Market from plugins/_listed/ before).

  - its own tests pass (a fake OpenAI and a fake Google)
  - its manifest reads with no warning, declares both keys as optional, and
    is a shipped plugin that is off by default (tests/test_built_in_images.py
    holds the switch and the take-over of a Market copy)
  - the core's own tools still carry no image tool: the composition server
    has no generate_image; the plugin's tool is the one

Run: python3 tests/test_images_plugin.py
"""

import subprocess
import sys
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
FOLDER = AGENTS / "plugins" / "images"
sys.path.insert(0, str(AGENTS / "lib"))
import loam_plugins as lp  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:66}{'  | ' + str(detail)[:400] if detail != '' else ''}")


run = subprocess.run([sys.executable, "-I", str(FOLDER / "tests" / "test_images.py")],
                     capture_output=True, text=True, timeout=300)
check("the plugin's own tests pass", run.returncode == 0, run.stdout[-600:] + run.stderr[-600:])
found = lp.read_folder(FOLDER, "images")
check("its manifest reads with no warning", found["warnings"] == [], found["warnings"])
check("it declares OpenAI and Google, each optional",
      [(k["provider"], k["optional"]) for k in found["manifest"]["keys"]]
      == [("openai", True), ("google", True)])
check("it is a shipped plugin", "images" in {p.name for p in lp._folders(AGENTS / "plugins")})
check("it is off by default", found["manifest"].get("default_on") is False, found["manifest"].get("default_on"))
text = (FOLDER / "tools" / "images_core.py").read_text() + (FOLDER / "tools" / "images_mcp.py").read_text()
check("it imports nothing of Loam's", "LOAM_LIB" not in text and "loam_" not in text)
comp = (AGENTS / "mcp" / "composition_server.py").read_text()
check("the composition server has no generate_image of its own",
      'name="generate_image"' not in comp)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
