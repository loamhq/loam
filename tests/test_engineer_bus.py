#!/usr/bin/env python3
"""The launcher finds the user bus whatever environment called it.

Codex starts its stdio MCP servers with a short environment allow-list, and
XDG_RUNTIME_DIR is not on it. `systemd-run --user` without it answers
"Failed to connect to bus: No medium found", so every brief from a Codex
thread came back `launch_failed` (job 20261001-170601-22043567, 2026-10-01).

Caused, not read: the launcher's own `main` runs under `env -i HOME PATH
USER`, the shape of a Codex-started server, against a fixture project, with a
stand-in `systemd-run` first on PATH. The stand-in asks the box user's REAL user
manager `systemctl --user is-system-running` with the environment it was
handed and then fails on purpose, so no unit is started and the answer comes
back in the launcher's own `launch_failed` text. A control run with the
launcher's default switched off must show "No medium found", or this test is
not testing anything.
"""

from __future__ import annotations

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
    # This tree's launcher, so a branch's pass proves the branch (2026-11-09).
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

    fixed = run("default")
    text = fixed["result"].get("receipt_text", "")
    check("bare env: the launch reached systemd-run (stand-in's code)",
          "systemd-run exited 97" in text, True)
    check("bare env: systemd-run was handed /run/user/<uid>",
          f"XDG_RUNTIME_DIR=/run/user/{os.getuid()}" in text, True)
    check("bare env: the user manager answered",
          any(f"manager {s}" in text for s in ("running", "degraded")), True)
    check("bare env: no 'No medium found'", "No medium found" in text, False)

    control = run("skip")
    text = control["result"].get("receipt_text", "")
    check("control, default off: the stand-in got no runtime dir",
          "XDG_RUNTIME_DIR=unset" in text, True)
    check("control, default off: 'No medium found', as from Codex",
          "No medium found" in text, True)

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

    # The second wall: Codex is told to pass both through to `engineer`.
    config = ((lc.WORKSPACES / lc.LEAD_AGENT / ".codex" / "config.toml")
              .read_text(encoding="utf-8"))
    engineer = config.split("[mcp_servers.engineer]", 1)[1].split("\n[", 1)[0]
    # Read as a list, not a line: LOAM_WAKE joined it on 2026-11-13.
    import tomllib  # noqa: PLC0415
    passed = tomllib.loads("[e]\n" + engineer).get("e", {}).get("env_vars") or []
    check("codex: engineer passes XDG_RUNTIME_DIR and DBUS_SESSION_BUS_ADDRESS",
          {"XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"} <= set(passed), True)
finally:
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
