#!/usr/bin/env python3
"""The waiter waits through the queue (since 2026-10-13).

Until then `wait_for` returned `stage: queued` the moment a job joined the
queue, and the background subagent carrying the brief ended with no receipt:
job 20261013-101042-70bcc8af queued behind three Loam jobs and nothing ever
told its briefer it had finished. This file drives `wait_for` against a job
directory whose `queued` marker and `result.json` are written by a thread on
a timeline, with the unit's state stubbed:

  queued, then ran, then finished   the call returns the job's verdict.
  the job's clock starts at the     a job queued longer than its own clock
  end of the queue                  still gets its whole clock to run.
  an unattended lane                returns `queued` at once, as before: its
                                    wake holds a run slot.
  the ceiling                       a queue longer than one call may wait
                                    ends in a sentence, not a transport abort.
  a queued unit that died           is relaunched by the waiter, which keeps
                                    waiting, rather than reported dead.

Nothing outside a temporary directory is written; no unit is launched.

Run: python3 tests/test_engineer_queue_wait.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:60} {got!r}"[:220]
          + ("" if ok else f"   want {want!r}"))


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


job = load("engineer_job_queue_wait", AGENTS / "bin" / "engineer-job")
job.POLL_SECONDS = 0.1
job.STARTUP_GRACE_SECONDS = 0.5
job.GRACE_SECONDS = 1
state = {"unit": "active", "resumed": []}
job.unit_state = lambda unit: state["unit"]
job.queue_position = lambda job_id: (1, 1)

root = Path(tempfile.mkdtemp(prefix="loam-queue-wait-"))


def job_paths(name: str) -> dict[str, Path]:
    d = root / name
    d.mkdir(parents=True)
    return {"dir": d, "queued": d / "queued", "result": d / "result.json",
            "heartbeat": d / "heartbeat"}


def timeline(steps):
    def run():
        for delay, act in steps:
            time.sleep(delay)
            act()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def queue(p):
    return lambda: p["queued"].write_text(json.dumps(
        {"joined": "2026-10-13T17:10:42Z", "held_by": "3 runs"}))


def start(p):
    return lambda: p["queued"].unlink()


def finish(p, text="done"):
    return lambda: p["result"].write_text(json.dumps(
        {"ok": True, "stage": "finished", "receipt_text": text}))


# 0. The ceiling sits under the tool call's timeout, as provision renders it.
rendered = re.search(r'"timeout": (\d+),',
                     (AGENTS / "bin" / "provision").read_text(encoding="utf-8"))
check("the ceiling is five minutes under the rendered tool timeout",
      int(rendered.group(1)) // 1000 - job.WAITER_CEILING_SECONDS, 300)

try:
    # 1. Queued, then ran, then finished: the verdict, not `queued`.
    p = job_paths("queued-then-finished")
    queue(p)()
    timeline([(1.0, start(p)), (0.5, finish(p, "the receipt"))])
    got = job.wait_for(p, "j1", 0)
    check("queued then finished: the call returns the job's verdict",
          (got["stage"], got["receipt_text"]), ("finished", "the receipt"))

    # 2. The job's clock starts when it leaves the queue. Its clock here is
    #    0 minutes + 1 s grace; it waits 2 s in the queue and runs 0.6 s.
    #    Counted from the call, the waiter would have given up at 1 s.
    p = job_paths("clock-after-queue")
    queue(p)()
    timeline([(2.0, start(p)), (0.6, finish(p))])
    got = job.wait_for(p, "j2", 0)
    check("the job's clock starts at the end of the queue",
          got["stage"], "finished")

    # 3. An unattended lane keeps the early return.
    p = job_paths("unattended")
    queue(p)()
    t0 = time.monotonic()
    got = job.wait_for(p, "j3", 0, return_if_queued=True)
    check("unattended: returns `queued` at once",
          (got["stage"], time.monotonic() - t0 < 1), ("queued", True))

    # 4. The ceiling: a queue longer than one call may wait.
    job.WAITER_CEILING_SECONDS = 1.5
    p = job_paths("ceiling")
    queue(p)()
    got = job.wait_for(p, "j4", 60)
    check("ceiling while queued: a sentence that says it is still queued",
          (got["stage"], got["receipt_text"].startswith("Still queued")),
          ("waiter_timeout", True))
    check("ceiling while queued: it says nothing will report back",
          "read the receipt from the vault" in got["receipt_text"], True)

    # 4b. The ceiling while running says it was the ceiling, not the clock.
    p = job_paths("ceiling-running")
    got = job.wait_for(p, "j4b", 60)
    check("ceiling while running: names the ceiling",
          "the most one call may wait" in got["receipt_text"], True)
    job.WAITER_CEILING_SECONDS = 3600

    # 5. A queued unit that died is relaunched, and the wait goes on.
    p = job_paths("queued-unit-died")
    queue(p)()
    state["unit"] = "inactive"

    def relaunch():
        state["resumed"].append("j5")
        state["unit"] = "active"
        timeline([(0.3, start(p)), (0.3, finish(p, "after relaunch"))])
        return ["j5"]

    job.resume_queued = relaunch
    got = job.wait_for(p, "j5", 0)
    check("queued unit gone: relaunched, and the verdict comes back",
          (state["resumed"], got["receipt_text"]), (["j5"], "after relaunch"))

    # 6. A running unit that died is still reported dead.
    p = job_paths("running-unit-died")
    state["unit"] = "inactive"
    job.resume_queued = lambda: []
    got = job.wait_for(p, "j6", 0)
    check("running unit gone: still `died`", got["stage"], "died")
finally:
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'all passed' if not fails else f'{fails} failed'}")
sys.exit(1 if fails else 0)
