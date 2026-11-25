#!/usr/bin/env python3
"""Verify CPU and I/O priority ordering across core services, background runs,
engineer jobs, landing tests, and test-runner worker scaling.

Run: python3 tests/test_priority_order.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_config as lc  # noqa: E402
import loam_cron  # noqa: E402
import loam_results  # noqa: E402
import loam_units  # noqa: E402
import loam_worktree as wt  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} {got!r}"[:220]
          + ("" if ok else f"   want {want!r}"))


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def unit_prop(text: str, key: str) -> list[str]:
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    return re.findall(rf"^\s*{re.escape(key)}=(\S+)", "\n".join(lines), re.M)


# 1. Hierarchy of constants: owner-facing core > default (100) > background > engineer.
check("core CPUWeight above default 100", lc.CORE_CPU_WEIGHT > 100, True)
check("background CPUWeight well below default 100",
      0 < lc.BACKGROUND_CPU_WEIGHT <= 25, True)
check("engineer CPUWeight at or below background",
      0 < lc.ENGINEER_CPU_WEIGHT <= lc.BACKGROUND_CPU_WEIGHT, True)
check("engineer Nice at lowest CFS priority (19)", lc.ENGINEER_NICE, 19)
check("background Nice lower priority than normal (0)",
      0 < lc.BACKGROUND_NICE <= lc.ENGINEER_NICE, True)
check("background I/O class is idle", lc.BACKGROUND_IO_CLASS, "idle")

# 2. Core owner-facing units (combined and split).
combined = loam_units.rendered(AGENTS / "systemd", split=False)
split = loam_units.rendered(AGENTS / "systemd", split=True)

for label, text in (
    ("combined broker", combined["loam-session-broker.service"]),
    ("split broker", split["loam-session-broker.service"]),
    ("split session host", split["loam-session-host.service"]),
):
    check(f"{label}: CPUWeight={lc.CORE_CPU_WEIGHT}",
          unit_prop(text, "CPUWeight"), [str(lc.CORE_CPU_WEIGHT)])
    check(f"{label}: no Nice throttle", unit_prop(text, "Nice"), [])

# 3. Background static & scheduled units (reviewer, reflection wake, cron).
bg_units = {
    "reviewer": combined["loam-reviewer.service"],
    "wake": loam_cron.render_units(loam_cron.Cron(
        agent="iris", display="Iris", name="reflection", workspace=Path("/w"),
        path=Path("/w/crons/reflection.md"), schedule_text="daily at 03:00 PT",
        oncalendar="*-*-* 03:00:00 America/Los_Angeles", enabled=True,
        body_chars=10, kind="reflection"))[0],
    "cron": loam_cron.render_units(loam_cron.Cron(
        agent="iris", display="Iris", name="x", workspace=Path("/w"),
        path=Path("/w/crons/x.md"), schedule_text="daily at 09:00 PT",
        oncalendar="*-*-* 09:00:00 America/Los_Angeles", enabled=True,
        body_chars=10))[0],
}
for kind, text in bg_units.items():
    check(f"{kind}: CPUWeight={lc.BACKGROUND_CPU_WEIGHT}",
          unit_prop(text, "CPUWeight"), [str(lc.BACKGROUND_CPU_WEIGHT)])
    check(f"{kind}: Nice={lc.BACKGROUND_NICE}",
          unit_prop(text, "Nice"), [str(lc.BACKGROUND_NICE)])
    check(f"{kind}: IOSchedulingClass={lc.BACKGROUND_IO_CLASS}",
          unit_prop(text, "IOSchedulingClass"), [lc.BACKGROUND_IO_CLASS])

# 4. Results drain transient unit (lib/loam_results.py).
res_seen: list[list[str]] = []
orig_run = loam_results.subprocess.run
orig_ws = loam_results.workspace
orig_en = loam_results.kicks_enabled
try:
    loam_results.kicks_enabled = lambda: True
    loam_results.workspace = lambda a: Path("/tmp")
    loam_results.subprocess.run = lambda argv, **kw: (
        res_seen.append(argv) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    loam_results.kick("iris")
finally:
    loam_results.subprocess.run = orig_run
    loam_results.workspace = orig_ws
    loam_results.kicks_enabled = orig_en
res_argv = res_seen[0] if res_seen else []
check("results unit: CPUWeight",
      f"--property=CPUWeight={lc.BACKGROUND_CPU_WEIGHT}" in res_argv, True)
check("results unit: Nice",
      f"--property=Nice={lc.BACKGROUND_NICE}" in res_argv, True)
check("results unit: IOSchedulingClass",
      f"--property=IOSchedulingClass={lc.BACKGROUND_IO_CLASS}" in res_argv, True)

# 5. Engineer job transient unit (bin/engineer-job launch).
job = load("engineer_job_priotest", AGENTS / "bin" / "engineer-job")
eng_seen: list[list[str]] = []
job.subprocess = SimpleNamespace(run=lambda argv, **kw: eng_seen.append(argv))
job.plugin_job_env = lambda *a, **kw: {}
with tempfile.TemporaryDirectory() as tmp:
    project = SimpleNamespace(name="Loam", root=Path(tmp), platform=True)
    job.launch(project, "20261125-000000-priotest", {"dir": Path(tmp)}, None)
eng_argv = eng_seen[0] if eng_seen else []
check("engineer job: CPUWeight",
      f"--property=CPUWeight={lc.ENGINEER_CPU_WEIGHT}" in eng_argv, True)
check("engineer job: IOWeight",
      f"--property=IOWeight={lc.ENGINEER_IO_WEIGHT}" in eng_argv, True)
check("engineer job: Nice=19",
      f"--property=Nice={lc.ENGINEER_NICE}" in eng_argv, True)
check("engineer job: IOSchedulingClass=idle",
      f"--property=IOSchedulingClass={lc.BACKGROUND_IO_CLASS}" in eng_argv, True)

# 6. Landing test command low-priority wrapper (lib/loam_worktree.py).
check("low_priority_argv wraps with ionice + nice when available",
      wt.low_priority_argv(["sh", "-c", "true"], which=lambda n: f"/usr/bin/{n}"),
      ["ionice", "-t", "-c", "3", "nice", "-n", str(lc.ENGINEER_NICE), "sh", "-c", "true"])
check("low_priority_argv falls back cleanly without ionice",
      wt.low_priority_argv(["sh", "-c", "true"], which=lambda n: "/usr/bin/nice" if n == "nice" else None),
      ["nice", "-n", str(lc.ENGINEER_NICE), "sh", "-c", "true"])
check("low_priority_argv is a no-op if neither binary exists",
      wt.low_priority_argv(["sh", "-c", "true"], which=lambda n: None),
      ["sh", "-c", "true"])

# 7. tests/run-all worker scaling across box sizes (1..32 vCPUs).
run_all = load("run_all_scaletest", AGENTS / "tests" / "run-all")
check("run-all workers on 1 vCPU", run_all.default_workers(1), 1)
check("run-all workers on 2 vCPU", run_all.default_workers(2), 1)
check("run-all workers on 4 vCPU", run_all.default_workers(4), 2)
check("run-all workers on 8 vCPU", run_all.default_workers(8), 4)
check("run-all workers on 16 vCPU", run_all.default_workers(16), 8)
check("run-all workers on 32 vCPU", run_all.default_workers(32), 8)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
