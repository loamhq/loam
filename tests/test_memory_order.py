#!/usr/bin/env python3
"""No memory ceiling on any agent run, and an OOM order that keeps Loam's
core (2026-11-16).

The owner: "remove all of these limits on the different agents, whether
personas or engineers." A job had been OOM-killed at its cgroup's
MemoryMax (20% of RAM) on a box with over 20 GB free. So every generator
that renders an agent run renders no MemoryMax, MemoryHigh or MemorySwapMax
and the agents' OOMScoreAdjust (lib/loam_config.py), and the core units
render the core's, below it:

  agent runs   an engineer job (bin/engineer-job's systemd-run line), a
               wake (provision), a cron (lib/loam_cron.py), the reviewer and
               the provider smoke
               runs (static templates), a chat (the broker's `choom`).
  core         the session broker's unit and the plugins gateway's.

Rendered here from the generators themselves, so a ceiling that comes back
in any of them fails this test before it reaches a box. Nothing is started.

Run: python3 tests/test_memory_order.py
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
import loam_units  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:62} {got!r}"[:220]
          + ("" if ok else f"   want {want!r}"))


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


CEILING = re.compile(r"^\s*(MemoryMax|MemoryHigh|MemorySwapMax)=", re.M)


def props(text: str) -> tuple[list[str], list[str]]:
    """(ceilings set, OOMScoreAdjust values), comments ignored."""
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    body = "\n".join(lines)
    return (CEILING.findall(body),
            re.findall(r"^\s*OOMScoreAdjust=(\S+)", body, re.M))


AGENT = str(lc.AGENT_OOM_SCORE_ADJ)
CORE = str(lc.CORE_OOM_SCORE_ADJ)

check("the order: agents well above the core",
      lc.AGENT_OOM_SCORE_ADJ - lc.CORE_OOM_SCORE_ADJ >= 500, True)
check("the core at the user manager's floor, not below it",
      lc.CORE_OOM_SCORE_ADJ >= 100, True)

# -- the rendered unit files ----------------------------------------------------
units: dict[str, str] = {}
static = loam_units.rendered(AGENTS / "systemd")
units["reviewer"] = static["loam-reviewer.service"]
units["provider smoke"] = static["loam-provider-follow.service"]

prov = load("provision_memtest", AGENTS / "bin" / "provision")
units["wake"] = prov.wake_units("iris", Path("/w"), "03:00")[0]

import loam_cron  # noqa: E402
cron = loam_cron.Cron(agent="iris", display="Iris", name="x", workspace=Path("/w"),
                      path=Path("/w/crons/x.md"), schedule_text="daily at 09:00 PT",
                      oncalendar="*-*-* 09:00:00 America/Los_Angeles",
                      enabled=True, body_chars=10)
units["cron"] = loam_cron.render_units(cron)[0]

for kind, text in units.items():
    ceilings, adj = props(text)
    check(f"{kind}: no memory or swap ceiling", ceilings, [])
    check(f"{kind}: the agents' OOM score", adj, [AGENT])

for name in ("loam-session-broker.service", "loam-plugins-gateway.service"):
    ceilings, adj = props(static[name])
    check(f"{name}: the core's OOM score", adj, [CORE])
check("the broker: no ceiling on the chats it carries",
      props(static["loam-session-broker.service"])[0], [])

# -- a chat ----------------------------------------------------------------------
import loam_broker  # noqa: E402
check("a chat's CLI is raised to the agents' score",
      loam_broker.CHILD_OOM_SCORE_ADJ, lc.AGENT_OOM_SCORE_ADJ)

# -- an engineer job: the systemd-run line, caught, not run ------------------------
job = load("engineer_job_memtest", AGENTS / "bin" / "engineer-job")
seen: list[list[str]] = []
job.subprocess = SimpleNamespace(run=lambda argv, **kw: seen.append(argv))
job.plugin_job_env = lambda *a, **kw: {}
with tempfile.TemporaryDirectory() as tmp:
    project = SimpleNamespace(name="Loam", root=Path(tmp), platform=True)
    job.launch(project, "20261116-000000-memtest", {"dir": Path(tmp)}, None)
argv = seen[0] if seen else []
check("engineer job: no MemoryMax / MemorySwapMax / MemoryHigh",
      [a for a in argv if re.match(r"--property=Memory(Max|High|SwapMax)=", a)], [])
check("engineer job: the agents' OOM score",
      [a for a in argv if a.startswith("--property=OOMScoreAdjust=")],
      [f"--property=OOMScoreAdjust={AGENT}"])
check("engineer job: a kill leaves the launcher alive to report it",
      "--property=OOMPolicy=continue" in argv, True)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
