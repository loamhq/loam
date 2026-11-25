#!/usr/bin/env python3
"""Tests for reaping escaped engineer-job helper processes (e.g. from systemd-run --scope)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import loam_engineer as eng  # noqa: E402

results: list[bool] = []


def check(label: str, ok: bool, detail="") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:72}"
          f"{'  | ' + str(detail)[:240] if detail != '' else ''}")


# 1. Persistent non-engineer Loam services are never touched; scopes & engineer jobs are eligible.
check("spares loam-session-broker.service",
      eng._is_persistent_loam_unit("0::/user.slice/user@1000.service/app.slice/loam-session-broker.service"))
check("spares loam-plugins-gateway.service",
      eng._is_persistent_loam_unit("0::/user.slice/user@1000.service/app.slice/loam-plugins-gateway.service"))
check("spares loam-plugin-sharing.service",
      eng._is_persistent_loam_unit("0::/user.slice/user@1000.service/app.slice/loam-plugin-sharing.service"))
check("does NOT spare transient run-*.scope (the gallery_server leak cgroup)",
      not eng._is_persistent_loam_unit("0::/user.slice/user@1000.service/app.slice/run-rf0f5dcfba26c4bc08c5ece6d99554e63.scope"))
check("does NOT spare loam-engineer-<job>.service",
      not eng._is_persistent_loam_unit("0::/user.slice/user@1000.service/app.slice/loam-engineer-20261117-150934-8f16d1fa.service"))

# 2. Spawn detached helpers carrying LOAM_WAKE=engineer:<fake_job_id>
# Scope fake job ids to this process so concurrent test runs never reap each other.
_tag = f"{os.getpid() & 0xffffff:06x}"
job_a = f"20990101-000001-aa{_tag}"
job_b = f"20990101-000002-bb{_tag}"
base_env = {k: v for k, v in os.environ.items() if k not in ("LOAM_WAKE", "LOAM_JOB_DIR")}
p_a = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    env={**base_env, "LOAM_WAKE": f"engineer:{job_a}"},
    start_new_session=True,
)
p_b = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    env={**base_env, "LOAM_WAKE": f"engineer:{job_b}"},
    start_new_session=True,
)
try:
    time.sleep(0.1)
    found_a = eng.find_job_processes(job_a)
    check("find_job_processes locates detached helper for job_a only",
          [x["pid"] for x in found_a] == [p_a.pid], found_a)

    reaped_a = eng.reap_job_processes(job_a, grace=2.0)
    rc_a = p_a.wait(timeout=3)
    check("reap_job_processes terminates job_a helper cleanly",
          [x["pid"] for x in reaped_a] == [p_a.pid] and rc_a is not None, (reaped_a, rc_a))
    check("...and leaves job_b helper untouched", p_b.poll() is None, p_b.poll())

    # 3. reap_orphaned_job_processes reaps inactive jobs and spares active ones
    p_c = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        env={**base_env, "LOAM_WAKE": f"engineer:{job_a}"},
        start_new_session=True,
    )
    try:
        time.sleep(0.1)
        # Only mark job_a inactive; all other jobs (including any live real jobs on this host) active!
        reaped_orph = eng.reap_orphaned_job_processes(lambda jid: jid != job_a, grace=2.0)
        rc_c = p_c.wait(timeout=3)
        check("reap_orphaned_job_processes reaps inactive job_a helper",
              [x["pid"] for x in reaped_orph] == [p_c.pid] and rc_c is not None, (reaped_orph, rc_c))
        check("...and spares active job_b helper", p_b.poll() is None, p_b.poll())
    finally:
        if p_c.poll() is None:
            p_c.kill()
            p_c.wait(timeout=2)
finally:
    for p in (p_a, p_b):
        if p.poll() is None:
            p.kill()
            p.wait(timeout=2)

# 4. Processes whose binary scrubbed LOAM_WAKE/LOAM_JOB_DIR from /proc/<pid>/environ
#    (such as chromium-headless-shell) are still caught via their engineer cgroup
#    or via their scratch-dir cmdline (e.g. escaped app-org.chromium.Chromium-*.scope).
with tempfile.TemporaryDirectory() as fake_proc_str:
    fake_proc = Path(fake_proc_str)
    jid_c = "20990101-120000-cafebabe"
    jid_other = "20990101-120001-deadbeef"

    def make_proc(pid: int, *, environ: bytes = b"", cgroup: str = "", cmdline: bytes = b"") -> None:
        d = fake_proc / str(pid)
        d.mkdir()
        (d / "environ").write_bytes(environ)
        (d / "cgroup").write_text(cgroup, encoding="utf-8")
        (d / "cmdline").write_bytes(cmdline)
        (d / "status").write_text("PPid:\t1\n", encoding="utf-8")

    # 4a. Renderer inside loam-engineer-<jid_c>.service with scrubbed environ
    make_proc(
        900001,
        environ=b"PATH=/usr/bin\0HOME=/home/test\0",
        cgroup=f"0::/user.slice/user-1000.slice/user@1000.service/app.slice/loam-engineer-{jid_c}.service\n",
        cmdline=b"/usr/lib/chromium/chromium-headless-shell\0--type=renderer\0",
    )
    # 4b. Escaped browser main process in D-Bus scope with scrubbed environ + job scratch profile
    make_proc(
        900002,
        environ=b"PATH=/usr/bin\0",
        cgroup="0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-org.chromium.Chromium-900002.scope\n",
        cmdline=f"/usr/lib/chromium/chromium-headless-shell\0--user-data-dir=/tmp/loam-job-{jid_c}/prof\0".encode(),
    )
    # 4c. Persistent Loam service whose cmdline mentions the scratch path must STILL be spared
    make_proc(
        900003,
        environ=b"",
        cgroup="0::/user.slice/user-1000.slice/user@1000.service/app.slice/loam-session-broker.service\n",
        cmdline=f"python3\0/tmp/loam-job-{jid_c}/something\0".encode(),
    )
    # 4d. Another job's cgroup process must not match jid_c
    make_proc(
        900004,
        environ=b"",
        cgroup=f"0::/user.slice/user-1000.slice/user@1000.service/app.slice/loam-engineer-{jid_other}.service\n",
        cmdline=b"/usr/lib/chromium/chromium-headless-shell\0--type=gpu-process\0",
    )

    caught = eng.find_job_processes(jid_c, proc_root=fake_proc)
    check("cgroup + scratch-cmdline matching catches scrubbed Chromium processes and spares broker/other job",
          sorted(x["pid"] for x in caught) == [900001, 900002], caught)

failed = sum(not r for r in results)
print(f"\n{'FAIL' if failed else 'OK'}: {failed} failure(s) out of {len(results)}")
sys.exit(1 if failed else 0)
