#!/usr/bin/env python3
"""Read GitHub checks for a pull request head through a loopback fixture, including
pending and failed checks.

Run: python3 tests/test_ci_checks.py"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_ci as ci  # noqa: E402
import loam_worktree as wt  # noqa: E402

FAILS: list[str] = []
SHA = "6ec132a172b46e193dce9442dfe21a9473fc7bab"
RUN = "https://github.com/acme-co/acme/actions/runs/38450660957"


def check(cond: bool, label: str) -> None:
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def fake(states):
    """A `get` that answers each look from `states` in turn (the last one
    repeats): (checks status, checks conclusion, workflow status)."""
    looks = {"n": 0}

    def get(path):
        i = min(looks["n"], len(states) - 1)
        status, conclusion, wstatus = states[i]
        if "/check-runs" in path:
            looks["n"] += 1
            if status == 403:
                raise ci.Unavailable("GitHub answered 403 for check-runs")
            return {"check_runs": [
                {"name": "checks / checks", "status": status, "conclusion": conclusion,
                 "html_url": f"{RUN}/job/1"},
                {"name": "deploy", "status": "completed" if status == "completed" else "queued",
                 "conclusion": "skipped" if status == "completed" else None,
                 "html_url": f"{RUN}/job/2"}]}
        return {"workflow_runs": [{"name": "Foundation", "status": wstatus,
                                   "conclusion": conclusion, "html_url": RUN}]}
    return get


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run(states, bound=900):
    clock = Clock()
    return ci.wait("acme-co/acme", SHA, "https://github.com/acme-co/acme/pull/8",
                   bound=bound, poll=20, get=fake(states), sleep=clock.sleep,
                   clock=clock)


def main() -> int:
    # setting
    records = {p.stem: json.loads(p.read_text()) for p in (AGENTS / "projects").glob("*.json")}
    with_checks = {n: r for n, r in records.items() if r.get(ci.SETTING)}
    check(all(r.get("landing") == "pull_request" and "test_command" not in r
              for r in with_checks.values()),
          f"a project with github_checks lands by PR, untested here ({sorted(with_checks) or 'none'})")
    on = SimpleNamespace(pull_request=True, record={"github_checks": True})
    check(ci.enabled(on), "enabled with the setting on a pull-request project")
    check(not ci.enabled(SimpleNamespace(pull_request=True, record={})), "off unless set")
    check(not ci.enabled(SimpleNamespace(pull_request=False, record={"github_checks": True})),
          "never on a project that does not land by pull request")

    # wait
    got = run([("in_progress", None, "in_progress"), ("in_progress", None, "in_progress"),
               ("completed", "success", "completed")])
    check(got["state"] == "passed" and got["waited_s"] == 40, f"passed after two polls ({got['waited_s']}s)")
    check(got["links"] == [RUN], "the run's link")
    line = ci.text(got)
    check("**CI passed**" in line and "`checks / checks` success" in line
          and "`deploy` skipped" in line and RUN in line, "passed line names each check and the run")

    got = run([("completed", "failure", "in_progress")])
    check(got["state"] == "failed" and got["waited_s"] == 0, "a failure ends the wait at once")
    check("**CI FAILED**" in ci.text(got) and "push stands" in ci.text(got), "failed is reported, push stands")

    got = run([("in_progress", None, "in_progress")], bound=60)
    check(got["state"] == "running" and got["waited_s"] == 60, "still running at the bound")
    check("**CI still running**" in ci.text(got) and RUN in ci.text(got), "running line carries the link")

    got = run([(403, None, None)])
    check(got["state"] == "unavailable", "403 is unavailable")
    check("CI result unavailable: missing permission" in ci.text(got)
          and "Checks: Read-only" in ci.text(got), "unavailable names the permission")

    # Checks registered later than the workflow: running, not none.
    check(ci.outcome({"checks": [], "workflows": [{"status": "queued"}]}) == "running",
          "a workflow with no check run yet is running")
    check(ci.outcome({"checks": [], "workflows": []}) == "none", "nothing at all is none")
    check(ci.outcome({"checks": [{"status": "completed", "conclusion": "success"}],
                      "workflows": None}) == "passed",
          "Actions unreadable: the check runs alone decide")

    # receipt
    base = {"state": "pull_request", "branch": "job/x", "sha": SHA, "untested": True,
            "tree": "/nonexistent", "test": {"command": None, "passed": None}}
    opened = dict(base, pull_request={"url": "https://github.com/acme-co/acme/pull/9",
                                      "created": True, "base": "main", "sha": SHA},
                  ci=run([("completed", "success", "completed")]))
    text = wt.receipt_section(SimpleNamespace(name="acme-foundation"), "x", opened)
    check("**CI passed**" in text and "untested" not in text, "opened PR: CI in place of untested")
    updated = dict(base, pull_request={"url": "https://github.com/acme-co/acme/pull/8",
                                       "updated": True, "base": "main", "head": "h",
                                       "before": SHA, "after": SHA, "sha": SHA},
                   ci=run([("completed", "failure", "completed")]))
    text = wt.receipt_section(SimpleNamespace(name="acme-foundation"), "x", updated)
    check("**CI FAILED**" in text and "untested" not in text, "updated PR: CI in place of untested")
    text = wt.receipt_section(SimpleNamespace(name="acme-foundation"), "x", dict(opened, ci=None))
    check("not read" in text, "a mid-job --land says the checks were not read")
    text = wt.receipt_section(SimpleNamespace(name="acme"), "x",
                              {k: v for k, v in opened.items() if k != "ci"})
    check("**untested**" in text and "CI" not in text, "a project without the setting is unchanged")

    # Landing releases admission before it waits for remote CI.
    src = (AGENTS / "bin" / "engineer-job").read_text()
    body = src[src.index("def report_checks"):src.index("def receipt_summary")]
    check('mark_landing(p, "ci"' in body, "the CI wait marks its phase on the landing record")
    check("lock.__exit__" not in body and "ci-wait" not in body.split('"""', 2)[2],
          "and neither releases a slot nor writes a file of its own")

    print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
