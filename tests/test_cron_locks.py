#!/usr/bin/env python3
"""Run competing wakes in throwaway workspaces with a sleeping provider.
The run trace records overlap and the wake log distinguishes waiting from execution."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

WAKE = str(Path(__file__).resolve().parent.parent / "bin" / "wake")
SELF = str(Path(__file__).resolve())


def child(project: str, cron: str, seconds: float, trace: Path) -> int:
    import importlib.machinery
    import importlib.util

    import loam_identity

    loader = importlib.machinery.SourceFileLoader("wake_under_test", WAKE)
    spec = importlib.util.spec_from_loader("wake_under_test", loader)
    wake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wake)

    loam_identity.preflight_identity = lambda project, model=None: ("c", "o")

    real_run = subprocess.run

    def fake_run(argv, **kwargs):
        if argv and str(argv[0]).endswith("/claude"):
            # This is the run. Stamp the window it actually occupied.
            with trace.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"event": "start", "t": time.time(),
                                     "cron": cron}) + "\n")
            time.sleep(seconds)
            with trace.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"event": "finish", "t": time.time(),
                                     "cron": cron}) + "\n")

            class Done:
                returncode = 0
                stdout = json.dumps(
                    {"subtype": "success", "result": "ok", "num_turns": 1})
                stderr = ""
            return Done()
        return real_run(argv, **kwargs)

    wake.subprocess.run = fake_run
    argv = [project] + (["--cron", cron] if cron != "reflection" else [])
    sys.argv = ["wake", *argv]
    return wake.main()


if len(sys.argv) > 1 and sys.argv[1] == "--child":
    sys.exit(child(sys.argv[2], sys.argv[3], float(sys.argv[4]),
                   Path(sys.argv[5])))


results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:50}{'  | ' + detail if detail else ''}")


def build(root: Path, agent: str, cron: str) -> Path:
    ws = root / "ai-labs" / agent
    (ws / "crons").mkdir(parents=True)
    (root / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True, exist_ok=True)
    (ws / "agent.json").write_text(json.dumps({
        "name": agent, "display": agent.capitalize(),
        "workspace": str(ws), "vault": str(root / "vault"),
    }))
    (ws / "map.md").write_text("Reflect.\n")
    (ws / "crons" / f"{cron}.md").write_text(
        "---\nschedule: daily at 05:00 PT\napproved: 2026-09-05\n---\n\n"
        "Sleep, and report having slept.\n")
    return ws


def launch(ws: Path, cron: str, seconds: float, trace: Path, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.Popen(
        [sys.executable, SELF, "--child", str(ws), cron, str(seconds), str(trace)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )


def failures_text(root: Path) -> str:
    path = root / "vault" / "00_INBOX" / "from-agents" / "loam-wake-failures.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def windows(trace: Path) -> dict[str, tuple[float, float]]:
    out: dict[str, list[float]] = {}
    for line in trace.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        out.setdefault(row["cron"], []).append(row["t"])
    return {k: (v[0], v[-1]) for k, v in out.items() if len(v) >= 2}


import loam_config as lc  # noqa: E402
import loam_lock  # noqa: E402

PROBES = [f"probe{i}" for i in range(1, 5)]
ROOMY = 32 * 1024 * 1024     # kB: a simulated 32 GB box, all of it free
# Under the box's reserve on every tier (including 2 GB, where reserve is ~487 MB).
NO_ROOM = max(64, loam_lock.MEMORY_RESERVE_MB - 128) * 1024


def meminfo(root: Path, available_kb: int) -> str:
    path = root / "meminfo"
    path.write_text(f"MemTotal:       {ROOMY} kB\n"
                    f"MemAvailable:   {available_kb} kB\n", encoding="utf-8")
    return str(path)


def clean_locks() -> None:
    """Locks are per-agent by NAME, and the probe agents are throwaway."""
    for name in PROBES:
        (loam_lock.LOCK_DIR / f"agent-{name}.lock").unlink(missing_ok=True)


def clean_state(*agents: str) -> None:
    for agent in agents:
        for stray in (
            Path(f"{lc.STATE}/wake/{agent}.json"),
            Path(f"{lc.STATE}/wake/{agent}.running"),
            Path(f"{lc.LOGS}/wake-{agent}.jsonl"),
        ):
            stray.unlink(missing_ok=True)


print("--- one run per agent -----------------------------------------------")
root = Path(tempfile.mkdtemp(prefix="loam-lock-a-"))
try:
    ws = build(root, "probeone", "sleeper")
    trace = root / "trace.jsonl"
    trace.touch()
    first = launch(ws, "sleeper", 6, trace)
    time.sleep(1.5)                       # let the first take the lock
    second = launch(ws, "reflection", 1, trace)
    first.wait(timeout=180)
    second.wait(timeout=180)

    seen = windows(trace)
    both = "sleeper" in seen and "reflection" in seen
    check("both runs eventually ran", both,
          "" if both else f"only {sorted(seen)}")
    if both:
        overlap = (seen["reflection"][0] < seen["sleeper"][1]
                   and seen["sleeper"][0] < seen["reflection"][1])
        check("they did NOT overlap", not overlap)
        check("the second started after the first finished",
              seen["reflection"][0] >= seen["sleeper"][1] - 0.05,
              f"second start {seen['reflection'][0]:.2f} vs "
              f"first finish {seen['sleeper'][1]:.2f}")
        check("the second waited (>= the first's remaining 4.5s)",
              seen["reflection"][0] - seen["sleeper"][0] >= 4.5)
    check("both exited 0", first.returncode == 0 and second.returncode == 0,
          f"{first.returncode}/{second.returncode}")
    check("no failure was recorded — waiting is not failing",
          "waited" not in failures_text(root))
    # The wake timestamp includes time spent waiting for the lock.
    waits = {}
    log = Path(f"{lc.LOGS}/wake-probeone.jsonl")
    for raw in (log.read_text().splitlines() if log.is_file() else []):
        entry = json.loads(raw)
        waits[entry.get("lane")] = entry.get("lock_wait_s")
    check("the second's log line records its wait (>= 4 s)",
          (waits.get("reflection") or 0) >= 4, f"lock_wait_s {waits}")
finally:
    shutil.rmtree(root, ignore_errors=True)
    clean_locks()
    clean_state("probeone")


print()
print("--- no count: admission by memory -----------------------------------")
root = Path(tempfile.mkdtemp(prefix="loam-lock-b-"))
try:
    trace = root / "trace.jsonl"
    trace.touch()
    env = {"LOAM_LOCK_MEMINFO": meminfo(root, ROOMY)}
    records_before = set(loam_lock.LOCK_DIR.glob("run-*.lock"))
    procs = []
    for agent in PROBES:
        ws = build(root, agent, agent)
        procs.append(launch(ws, agent, 6, trace, env))
        time.sleep(0.5)
    for proc in procs:
        proc.wait(timeout=240)

    seen = windows(trace)
    check(f"all {len(PROBES)} ran", len(seen) == len(PROBES),
          f"ran: {sorted(seen)}")
    if len(seen) == len(PROBES):
        concurrent = max(
            sum(1 for s_, f in seen.values() if s_ <= t < f)
            for t in [v[0] + 0.2 for v in seen.values()]
        )
        check(f"all {len(PROBES)} ran at once (no slot count)",
              concurrent == len(PROBES), f"peak was {concurrent}")
    check(f"all {len(PROBES)} exited 0", all(p.returncode == 0 for p in procs),
          str([p.returncode for p in procs]))
    left = set(loam_lock.LOCK_DIR.glob("run-*.lock")) - records_before
    check("no run record is left behind", not left, str(sorted(left)))
finally:
    shutil.rmtree(root, ignore_errors=True)
    clean_locks()
    clean_state(*PROBES)


print()
print("--- waiting out the budget ------------------------------------------")
root = Path(tempfile.mkdtemp(prefix="loam-lock-c-"))
try:
    ws = build(root, "probeone", "sleeper")
    trace = root / "trace.jsonl"
    trace.touch()
    holder = launch(ws, "sleeper", 12, trace)
    time.sleep(1.5)
    waiter = launch(ws, "impatient", 1, trace, {"LOAM_LOCK_WAIT_S": "3"})
    waiter.wait(timeout=120)
    holder.wait(timeout=120)

    seen = windows(trace)
    text = failures_text(root)
    check("the waiter never ran", "impatient" not in seen)
    check("the holder was never killed", "sleeper" in seen)
    check("the waiter exited 0 (no OnFailure on a skip)",
          waiter.returncode == 0, str(waiter.returncode))
    check("a failure line was recorded", "gave up" in text)
    check("the line names the lane that waited", "cron:impatient" in text)
    check("the line names what it waited on", "sleeper" in text)
    print()
    for line in text.splitlines():
        if line.startswith("- `"):
            print("      " + line.strip())
finally:
    shutil.rmtree(root, ignore_errors=True)
    clean_locks()
    clean_state("probeone")

print()
print("--- waiting out the budget on memory --------------------------------")
root = Path(tempfile.mkdtemp(prefix="loam-lock-d-"))
try:
    trace = root / "trace.jsonl"
    trace.touch()
    waiter_name = PROBES[0]
    waiter = launch(build(root, waiter_name, waiter_name), waiter_name, 1, trace,
                    {"LOAM_LOCK_WAIT_S": "3",
                     "LOAM_LOCK_MEMINFO": meminfo(root, NO_ROOM)})
    waiter.wait(timeout=120)

    seen = windows(trace)
    text = failures_text(root)
    check("with no room the run never started", waiter_name not in seen)
    check("it was not a failure — it exited 0",
          waiter.returncode == 0, str(waiter.returncode))
    check("the line says it was memory, not the agent lock",
          "waited 0.1 minutes for memory" in text or "for memory on this box" in text)
    check("the line gives the box's numbers", "MB available" in text)
    print()
    for line in text.splitlines():
        if line.startswith("- `"):
            print("      " + line.strip())
finally:
    shutil.rmtree(root, ignore_errors=True)
    clean_locks()
    clean_state(*PROBES)


print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
