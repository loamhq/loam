#!/usr/bin/env python3
"""The suite is sealed from the install it runs on (since 2026-11-19;
tests/run-all `sealed_env`, lib/loam_worktree.py `platform_argv`).

Asserted from inside a test, the way every test sees it:

  - the configuration is the suite's own, never /etc/loam/loam.toml, and
    every data path in it (vault, workspaces, projects, plugins, state,
    jobs, the secrets files) is inside this test's own scratch folder,
    which is also its TMPDIR's parent; so is every store run-all names
  - the starter agent a fresh install has is there, and only it
  - the offline guard is on in this process

and, by causing it, of the landing's wall: a command run through
`platform_argv` finds each live folder empty (a file planted in each
before is not there), has a network namespace whose only link is its own
loopback, and still sees the checkout's own files and the MCP venv.

Run: python3 tests/test_sealed.py
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import loam_config as lc  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} {got!r}"
          + ("" if ok else f"\n      wanted {want!r}"))


under_runner = bool(os.environ.get("LOAM_OFFLINE")) and "LOAM_TEST_INSTALL_CONFIG" not in os.environ
if not under_runner:
    print("SKIP  run me through tests/run-all: the seal is the runner's")
    sys.exit(0)

scratch = Path(os.environ["TMPDIR"]).parent
check("the configuration is the suite's, in the test's scratch folder",
      Path(os.environ["LOAM_CONFIG"]).parent, scratch)
check("...not the install's", lc.CONFIG_FILE != Path("/etc/loam/loam.toml"), True)
for name, path in (("vault", lc.VAULT), ("workspaces", lc.WORKSPACES),
                   ("projects", lc.PROJECTS), ("plugins", lc.PLUGINS),
                   ("state", lc.STATE), ("logs", lc.LOGS), ("jobs", lc.JOBS),
                   ("secrets_env", lc.SECRETS_ENV), ("wake_env", lc.WAKE_ENV),
                   ("reviewer_state", lc.REVIEWER_STATE)):
    check(f"paths.{name} is inside the scratch folder", scratch in path.parents, True)
for var in ("LOAM_LOCK_DIR", "LOAM_SCHEDULE_STATE", "LOAM_HOST_SETTINGS",
            "LOAM_THREADS_STATE", "LOAM_VAULT", "LOAM_WAITERS_DIR"):
    check(f"{var} is inside the scratch folder",
          scratch in Path(os.environ[var]).parents, True)
check("the workspaces hold the starter agent and the shared folders only",
      sorted(p.name for p in lc.WORKSPACES.iterdir()), ["_shared", "commons", "iris"])
check("the offline guard is on", os.environ.get("LOAM_OFFLINE"), "1")
check("paths.root is this checkout", lc.ROOT, ROOT)

# --- the wall ---------------------------------------------------------------
import loam_engineer as eng  # noqa: E402
import loam_worktree as wt  # noqa: E402

hidden = wt.sealed_paths()
check("the wall covers the vault, workspaces, projects, plugins and state",
      {lc.VAULT, lc.WORKSPACES, lc.PROJECTS, lc.PLUGINS, lc.STATE} <= set(hidden), True)
check("...never the checkout, nor anything holding it",
      any(p == eng.AGENTS or p in eng.AGENTS.parents for p in hidden), False)
planted = []
for path in hidden:
    if os.access(path, os.W_OK):
        mark = path / "planted-by-test-sealed"
        mark.write_text("x")
        planted.append(mark)
check("a file is planted in each covered folder first", len(planted) >= 5, True)

probe = "; ".join(
    [f"test -e {wt._q(str(m))} && echo SEEN {wt._q(str(m))}" for m in planted]
    + ["ip -o link | wc -l | sed 's/^/LINKS /'",
       "ip -o link | grep -c 'lo:' | sed 's/^/LO /'",
       "test -f tests/test_sealed.py && echo CHECKOUT",
       "true"])
out = subprocess.run(wt.platform_argv(ROOT, probe), cwd=ROOT, capture_output=True,
                     text=True, timeout=60, check=False)
lines = out.stdout.splitlines()
check("the wall ran", out.returncode, 0)
check("...and saw none of the planted files", [ln for ln in lines if ln.startswith("SEEN")], [])
check("...its only network link is its own loopback",
      ("LINKS 1" in lines, "LO 1" in lines), (True, True))
check("...and it sees the checkout's own files", "CHECKOUT" in lines, True)
if out.returncode:
    print(out.stderr[-800:])
for mark in planted:
    mark.unlink(missing_ok=True)

print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
