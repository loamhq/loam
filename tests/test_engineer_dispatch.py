"""Exercise queued admission and automatic continuations using temporary project and job
state.

Run: python3 tests/test_engineer_dispatch.py
"""

import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_engineer as eng  # noqa: E402

LAUNCHER = str(Path(__file__).resolve().parent.parent / "bin" / "engineer-job")
loader = importlib.machinery.SourceFileLoader("engineer_job_dispatch", LAUNCHER)
spec = importlib.util.spec_from_loader("engineer_job_dispatch", loader)
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:60} {got!r}"
          + ("" if ok else f"\n      wanted {want!r}"))


root = Path(tempfile.mkdtemp(prefix="loam-dispatch-test-"))
saved_projects = eng.projects
try:
    project = eng.Project("demo", root / "checkout", root / "demo-jobs",
                          platform=False, record={})
    other = eng.Project("other", root / "other", root / "other-jobs",
                        platform=False, record={})
    project.jobs.mkdir()
    other.jobs.mkdir()
    eng.projects = lambda: {"demo": project, "other": other}

    meta = {"title": "Edit the middle line", "briefed_by": "otto",
            "involvement": "go and do it", "max_minutes": 15,
            "auto_continuation": 0}
    conflict = {"state": "not_landed", "reason": "conflict",
                "branch": "job/20261002-100000-aaaaaaaa",
                "tree": "/tmp/tree", "files": ["lines.txt"]}

    first = job.plan_dispatch(project, "20261002-100000-aaaaaaaa", meta, conflict)
    brief = (project.jobs / first["job_id"] / "brief.md").read_text()
    head = job.front_matter(brief)
    check("conflict: a continuation job is claimed, count 1", first["count"], 1)
    check("...its brief continues this job and counts itself",
          (head.get("continue_job"), head.get("auto_continuation"),
           head.get("dispatched_by"), head.get("briefed_by")),
          ("20261002-100000-aaaaaaaa", "1", "20261002-100000-aaaaaaaa", "otto"))
    check("...titled as the first of two",
          head.get("title"), "Edit the middle line — automatic continuation 1 of 2")
    check("...and its body is the receipt's continuation brief",
          "did not land: merging it into `main` conflicts in `lines.txt`" in brief, True)
    check("...the receipt names the job it dispatched",
          f"as job `{first['job_id']}` (automatic continuation 1 of 2)"
          in job.dispatch_text(first), True)

    second_meta = {**meta, "title": head["title"], "auto_continuation": 1}
    second = job.plan_dispatch(project, first["job_id"], second_meta,
                               {**conflict, "reason": "merged_tests"})
    second_head = job.front_matter(
        (project.jobs / second["job_id"] / "brief.md").read_text())
    check("merged tests fail on the first continuation: a second, count 2",
          (second["count"], second_head.get("title")),
          (2, "Edit the middle line — automatic continuation 2 of 2"))

    third = job.plan_dispatch(project, second["job_id"],
                              {**meta, "auto_continuation": 2},
                              {**conflict, "reason": "branch_tests"})
    claimed = sorted(d.name for d in project.jobs.iterdir())
    check("the chain ends after two: nothing more is claimed",
          (third["ended"], third["job_id"], len(claimed)), (True, None, 2))
    check("...and the receipt says so, with the branch kept",
          ("**The chain ends here.**" in job.dispatch_text(third),
           "The branch and its tree are kept" in job.dispatch_text(third)),
          (True, True))

    for state in ({"state": "landed"}, {"state": "nothing"},
                  {**conflict, "reason": "incomplete"},
                  {**conflict, "reason": "uncommitted"}):
        check(f"no dispatch for {state.get('reason') or state['state']}",
              job.plan_dispatch(project, "20261002-100000-aaaaaaaa", meta, state),
              None)

    # The queue: by when each joined, across projects; a job with a verdict
    # has left it.
    for proj, job_id, joined in ((project, "20261002-110000-00000003", "2026-10-02T01:00:03Z"),
                                 (other, "20261002-110000-00000001", "2026-10-02T01:00:01Z"),
                                 (project, "20261002-110000-00000002", "2026-10-02T01:00:02Z")):
        d = proj.jobs / job_id
        d.mkdir()
        (d / "queued").write_text(json.dumps({"joined": joined}))
    done = project.jobs / "20261002-110000-00000000"
    done.mkdir()
    (done / "queued").write_text(json.dumps({"joined": "2026-10-02T00:00:00Z"}))
    (done / "result.json").write_text("{}")
    check("queue: oldest first, across projects, finished jobs gone",
          [j for _, _, j in job.queued_jobs()],
          ["20261002-110000-00000001", "20261002-110000-00000002",
           "20261002-110000-00000003"])
    check("queue: a position is 1-based of the whole queue",
          job.queue_position("20261002-110000-00000002"), (2, 3))
finally:
    eng.projects = saved_projects
    shutil.rmtree(root, ignore_errors=True)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
