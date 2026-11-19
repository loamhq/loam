"""`gen-deploy --help` prints its usage and does nothing (2026-11-19).

Until then any argument it did not know fell through to a live render: on
2026-11-17 `gen-deploy --help` wrote the Caddyfile, reloaded Caddy and ran
`compose up`. Each form is run as the script, with `sudo` on PATH replaced
by a stub that records it was called and fails, so a form that slipped
through to the live path would be seen calling it and could do nothing.

Run: python3 tests/test_gen_deploy_args.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bin" / "gen-deploy"
fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:60} {got!r}"[:220]
          + ("" if ok else f"\n      wanted {want!r}"))


scratch = Path(tempfile.mkdtemp(prefix="loam-gen-deploy-args-"))
try:
    stub = scratch / "bin"
    stub.mkdir()
    called = scratch / "sudo-called"
    for name in ("sudo", "docker", "caddy", "systemctl"):
        (stub / name).write_text(f"#!/bin/sh\necho \"$0 $*\" >> {called}\nexit 99\n")
        (stub / name).chmod(0o755)
    env = {**os.environ, "PATH": f"{stub}:/usr/bin:/bin"}

    def run(*args):
        if called.exists():
            called.unlink()
        done = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                              text=True, env=env, timeout=60, check=False)
        return done.returncode, done.stdout, done.stderr, called.exists()

    for flag in ("--help", "-h"):
        code, out, _err, sudo = run(flag)
        check(f"{flag}: exits 0", code, 0)
        check(f"{flag}: prints the usage", "Usage:" in out and "--dry-run" in out, True)
        check(f"{flag}: calls nothing", sudo, False)
    code, _out, _err, sudo = run("--dry-run", "--help")
    check("--help with another flag: still only help", (code, sudo), (0, False))
    for bad in (["--hepl"], ["help"], ["--share"], ["--dry-run", "--force"]):
        code, _out, err, sudo = run(*bad)
        check(f"{' '.join(bad)}: refused with 2, nothing called", (code, sudo), (2, False))
        check(f"{' '.join(bad)}: says nothing was done", "nothing was done" in err, True)
finally:
    shutil.rmtree(scratch, ignore_errors=True)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
