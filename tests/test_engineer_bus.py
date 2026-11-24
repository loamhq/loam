#!/usr/bin/env python3
"""Verify that the launcher and generated tool environment can reach the user bus from a
minimal environment.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_config as lc  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:60} {got!r}"[:220]
          + ("" if ok else f"   want {want!r}"))


HARNESS = """
import importlib.machinery, importlib.util, json, sys
from pathlib import Path
root, skip = Path(sys.argv[1]), sys.argv[2] == "skip"
job_id = "20261002-000000-b0b0b0b0"
loader = importlib.machinery.SourceFileLoader(
    "engineer_job_under_test", "@LAUNCHER@")
spec = importlib.util.spec_from_loader(loader.name, loader)
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)
eng = job.eng
project = eng.Project("Fixture", root / "tree", root / "jobs", platform=True)
(root / "jobs" / job_id).mkdir(parents=True)
(root / "jobs" / job_id / "brief.md").write_text(
    "---\\ntitle: bus probe\\nbriefed_by: ada\\nmax_minutes: 1\\n---\\n\\nprobe\\n")
eng.resolve_project = lambda key: project
job.platform_defect = lambda p: None
job.preflight = lambda *a: None
if skip:
    job.user_bus_env = lambda: None
sys.argv = ["engineer-job", "Fixture", job_id]
code = job.main()
print(json.dumps({"code": code, "result": json.loads(
    (root / "jobs" / job_id / "result.json").read_text())}))
"""

STAND_IN = """#!/bin/sh
echo "stand-in: XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-unset}" >&2
echo "stand-in: manager $(systemctl --user is-system-running 2>&1)" >&2
exit 97
"""

root = Path(tempfile.mkdtemp(prefix="loam-engineer-bus-"))
try:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "systemd-run").write_text(STAND_IN)
    (bin_dir / "systemd-run").chmod(0o755)
    # Use this checkout's launcher.
    (root / "harness.py").write_text(HARNESS.replace(
        "@LAUNCHER@", str(Path(__file__).resolve().parent.parent / "bin" / "engineer-job")))

    def run(mode: str) -> dict:
        work = root / mode
        work.mkdir()
        out = subprocess.run(
            ["env", "-i", f"HOME={lc.HOME}", f"USER={lc.USER}",
             f"PATH={bin_dir}:/usr/bin:/bin",
             "/usr/bin/python3", str(root / "harness.py"), str(work), mode],
            capture_output=True, text=True, timeout=60, check=False)
        lines = out.stdout.strip().splitlines()
        try:
            return json.loads(lines[-1])
        except (IndexError, ValueError):
            return {"code": None, "result": {}, "_stderr": out.stderr[-800:]}

    def _bus_missing(msg: str) -> bool:
        return ("No medium found" in msg
                or "$DBUS_SESSION_BUS_ADDRESS and $XDG_RUNTIME_DIR not defined" in msg)

    fixed = run("default")
    text = fixed["result"].get("receipt_text", "")
    check("bare env: the launch reached systemd-run (stand-in's code)",
          "systemd-run exited 97" in text, True)
    check("bare env: systemd-run was handed /run/user/<uid>",
          f"XDG_RUNTIME_DIR=/run/user/{os.getuid()}" in text, True)
    check("bare env: no user-bus missing error", _bus_missing(text), False)

    control = run("skip")
    text = control["result"].get("receipt_text", "")
    check("control, default off: the stand-in got no runtime dir",
          "XDG_RUNTIME_DIR=unset" in text, True)
    check("control, default off: user-bus missing error, as from Codex",
          _bus_missing(text), True)

    # The caller's own value wins: the default is for an absent variable.
    out = subprocess.run(
        ["env", "-i", f"HOME={lc.HOME}", "PATH=/usr/bin:/bin",
         "XDG_RUNTIME_DIR=/tmp/caller-chose", "/usr/bin/python3", "-c",
         "import importlib.machinery as m, importlib.util as u, os\n"
         f"l = m.SourceFileLoader('j', {str(Path(__file__).resolve().parent.parent / 'bin' / 'engineer-job')!r})\n"
         "s = u.spec_from_loader('j', l); j = u.module_from_spec(s)\n"
         "l.exec_module(j); j.user_bus_env()\n"
         "print(os.environ['XDG_RUNTIME_DIR'], os.environ.get('DBUS_SESSION_BUS_ADDRESS', 'unset'))"],
        capture_output=True, text=True, timeout=60, check=False)
    check("a caller's XDG_RUNTIME_DIR is kept", out.stdout.strip(),
          "/tmp/caller-chose unset")

    # Check the bus variables in generated provider settings.
    loader = importlib.machinery.SourceFileLoader(
        "provision_bus_test", str(AGENTS / "bin" / "provision"))
    spec = importlib.util.spec_from_loader("provision_bus_test", loader)
    provision = importlib.util.module_from_spec(spec)
    loader.exec_module(provision)
    config = provision.codex_config(lc.LEAD_AGENT, lc.WORKSPACES / lc.LEAD_AGENT)
    engineer = config.split("[mcp_servers.engineer]", 1)[1].split("\n[", 1)[0]
    # Compare environment variable names as a list.
    import tomllib  # noqa: PLC0415
    passed = tomllib.loads("[e]\n" + engineer).get("e", {}).get("env_vars") or []
    check("codex: engineer passes XDG_RUNTIME_DIR and DBUS_SESSION_BUS_ADDRESS",
          {"XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"} <= set(passed), True)
finally:
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
