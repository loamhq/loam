"""Run a job inside its filesystem boundary and verify that the launcher can read the
receipt the session wrote.

Run: python3 tests/test_engineer_walled.py
"""

import contextlib
import importlib.machinery
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_engineer as eng  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "engineer_job_walled", str(Path(__file__).resolve().parent.parent / "bin" / "engineer-job"))
spec = importlib.util.spec_from_loader("engineer_job_walled", loader)
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:60} {got!r}"
          + ("" if ok else f"\n      wanted {want!r}"))


STUB = """#!/bin/sh
if echo canon > "$LOAM_JOB_CANONICAL/canon.txt" 2>/dev/null; then W=wrote; else W=refused; fi
cd "$LOAM_JOB_TREE" && echo stub >> lines.txt && git add -A && git commit -qm "Stub edit"
printf 'Stub receipt. Walled: %s. Canonical write: %s.\\n' "$LOAM_WALLED" "$W" > "$LOAM_JOB_DIR/receipt.md"
echo '{"type":"result","subtype":"success","num_turns":1,"result":"done"}'
"""

root = Path(tempfile.mkdtemp(prefix="loam-walled-test-"))
saved = (job.CLAUDE, job.STATE_DIR, eng.VAULT, eng.log_line, eng.job_dir,
         job.loam_lock.hold_slot, job.wt.WORKTREES, job.resume_queued)
try:
    repo = root / "proj"
    repo.mkdir()
    (repo / "lines.txt").write_text("one\n")
    for argv in (["init", "-q", "-b", "main"], ["config", "user.name", "t"],
                 ["config", "user.email", "t@t"], ["add", "-A"],
                 ["commit", "-qm", "start"]):
        subprocess.run(["git", "-C", str(repo), *argv], check=True)
    project = eng.Project("walled", repo, root / "jobs", platform=False,
                          record={"test_command": "true"})
    job_id = "20261002-130000-0a11ed00"
    jdir = root / "jobs" / job_id
    jdir.mkdir(parents=True)
    (jdir / "brief.md").write_text(
        "---\ntitle: Walled receipt\nbriefed_by: ada\nmax_minutes: 2\n---\n\nx\n")
    stub = root / "claude-stub"
    stub.write_text(STUB)
    stub.chmod(0o755)

    logged: list[dict] = []
    job.CLAUDE = str(stub)
    job.STATE_DIR = root / "state"
    eng.VAULT = root / "vault"
    eng.log_line = logged.append
    eng.job_dir = lambda engineer, jid: root / "jobs" / jid
    job.loam_lock.hold_slot = lambda *a, **k: contextlib.nullcontext()
    job.wt.WORKTREES = root / "worktrees"
    job.resume_queued = lambda: []
    code = job.run_inner(project, job_id)
finally:
    (job.CLAUDE, job.STATE_DIR, eng.VAULT, eng.log_line, eng.job_dir,
     job.loam_lock.hold_slot, job.wt.WORKTREES, job.resume_queued) = saved

try:
    result = json.loads((jdir / "result.json").read_text())
    text = result["receipt_text"]
    check("the job completed and exited 0", (result.get("outcome"), code), ("completed", 0))
    check("the launcher read the receipt the walled session wrote",
          "Stub receipt. Walled: project. Canonical write: refused." in text, True)
    check("...and did not report it missing",
          "The engineer wrote no receipt" in text, False)
    check("the wall refused the canonical write; nothing reached it",
          (repo / "canon.txt").exists(), False)
    check("the commit made inside the wall landed on main",
          (result.get("landing_state"),
           subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%s", "main"],
                          capture_output=True, text=True).stdout.strip()),
          ("landed", "Stub edit"))
    check("the vault copy carries the same receipt",
          "Canonical write: refused." in Path(result["vault_receipt"]).read_text(), True)
finally:
    shutil.rmtree(root, ignore_errors=True)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
