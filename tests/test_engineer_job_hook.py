#!/usr/bin/env python3
"""A plugin's `engineer.job` hook at a job's launch (2026-11-14, first release
L6): what it prints reaches the job's unit through the job's own 0600
environment file; a name the launcher sets itself stays the launcher's; why
anything offered was not used is written to the job folder's
`plugin-env.log`; and a project job's launch no longer reads its registry
record for anything (the record's `gcp` block was the last reader).

The hooks themselves are lib/loam_contributions.py's and are tested in
tests/test_contributions.py; here they are replaced by what they return, and
`systemd-run` by a recorder. Nothing is launched; nothing outside a temporary
directory is written.

Run: python3 tests/test_engineer_job_hook.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import stat
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_engineer as eng  # noqa: E402

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


job = load("engineer_job_hook", AGENTS / "bin" / "engineer-job")
JOB = "20261114-120000-0000beef"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    d = root / "jobs" / JOB
    d.mkdir(parents=True)
    calls: list[tuple] = []

    def fake_hooks(project, job_id, job_dir, scratch, plugins=None):
        calls.append((project, job_id, job_dir, scratch))
        return ({"CLOUD_CREDENTIALS": str(job_dir / "creds.json"),
                 "PATH": "/evil", "GIT_SSH_COMMAND": "ssh -i /evil"},
                ["acme: dropped a line that is not NAME=value"])

    job.contributions.engineer_env = lambda project: {"FIXED": "1"}
    job.contributions.engineer_job_env = fake_hooks
    argv = ["systemd-run", "--setenv=PATH=/usr/bin", "--setenv=GIT_SSH_COMMAND=ssh -i k"]
    env = job.plugin_job_env(eng.Project("acme", root, root / "jobs", platform=False),
                             JOB, {"dir": d}, argv)
    check("the hook is asked with the job's id, folder and scratch",
          calls[-1], ("acme", JOB, d, eng.scratch_dir(JOB)))
    check("fixed and hooked variables both reach the job",
          env, {"FIXED": "1", "CLOUD_CREDENTIALS": str(d / "creds.json")})
    log = (d / "plugin-env.log").read_text()
    check("a launcher's name is kept the launcher's, and the log says so",
          ("PATH is the launcher's" in log, "GIT_SSH_COMMAND is the launcher's" in log),
          (True, True))
    check("...the hook's own note is in the log, and no value is",
          ("not NAME=value" in log, "/evil" not in log), (True, True))

    job.contributions.engineer_job_env = lambda *a, **k: ({"OK": "1"}, [])
    job.plugin_job_env(eng.Project("acme", root, root / "jobs", platform=False),
                       JOB, {"dir": d}, [])
    check("a clean launch leaves no stale log", (d / "plugin-env.log").exists(), False)

    # The whole launch of a project job, systemd-run recorded.
    seen: list[list[str]] = []
    real_run = job.subprocess.run
    job.subprocess.run = lambda argv, **k: seen.append(argv)
    try:
        project = eng.Project("acme", root, root / "jobs", platform=False,
                              repo="acme-co/widget", record={"name": "acme"})
        # No registry record on disk for `acme`: the launch must not read one.
        job.launch(project, JOB, {"dir": d}, None)
    finally:
        job.subprocess.run = real_run
    unit = seen[-1]
    env_file = d / "plugin.env"
    check("the variables go in the job's own file, mounted on the unit",
          f"--property=EnvironmentFile=-{env_file}" in unit, True)
    check("...0600, with the hook's variable",
          (stat.S_IMODE(env_file.stat().st_mode), "OK=1" in env_file.read_text()),
          (0o600, True))
    check("...and never on the command line",
          any("OK=1" in a for a in unit), False)
    check("no Google variable is the launcher's own any more",
          [a for a in unit if "GOOGLE" in a or "CLOUDSDK" in a or "GCP" in a], [])

print()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
