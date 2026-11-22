"""ensure_static_roots must chown every intermediate directory it creates under
ai-labs (such as commons/ when creating commons/www/) and repair any existing
static root or intermediate directory under ai-labs left root-owned.

Run: python3 tests/test_static_root_ownership.py
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

loader = importlib.machinery.SourceFileLoader("gen_deploy", str(ROOT / "bin" / "gen-deploy"))
spec = importlib.util.spec_from_loader("gen_deploy", loader)
gen_deploy = importlib.util.module_from_spec(spec)
loader.exec_module(gen_deploy)

fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} {got!r}"[:220]
          + ("" if ok else f"\n      wanted {want!r}"))


scratch = Path(tempfile.mkdtemp(prefix="loam-static-root-own-"))
try:
    ai_labs = scratch / "agents"
    ai_labs.mkdir()
    commons = ai_labs / "commons"
    www = commons / "www"

    USER_UID, USER_GID = 1001, 1001
    fake_owners: dict[Path, tuple[int, int]] = {ai_labs: (USER_UID, USER_GID)}
    chown_calls: list[tuple[Path, int, int]] = []

    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        p = Path(path)
        if p in fake_owners:
            uid, gid = fake_owners[p]
            return SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=st.st_mode)
        return SimpleNamespace(st_uid=0, st_gid=0, st_mode=st.st_mode)

    def fake_chown(path, uid, gid):
        p = Path(path)
        chown_calls.append((p, uid, gid))
        fake_owners[p] = (uid, gid)

    with (
        mock.patch.object(gen_deploy.dep, "AI_LABS", ai_labs),
        mock.patch.object(gen_deploy.dep, "static_roots", return_value=[www]),
        mock.patch("os.stat", side_effect=fake_stat),
        mock.patch("os.chown", side_effect=fake_chown),
    ):
        # 1. Fresh install: neither commons/ nor commons/www/ exists yet.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gen_deploy.ensure_static_roots(dry=False)

        check("fresh: commons/ created", commons.is_dir(), True)
        check("fresh: commons/www/ created", www.is_dir(), True)
        check("fresh: chowned commons/ and commons/www/ to run-as user",
              chown_calls, [(commons, USER_UID, USER_GID), (www, USER_UID, USER_GID)])
        check("fresh: commons/ owned by run-as user",
              fake_owners.get(commons), (USER_UID, USER_GID))

        # 2. Existing install with root-owned commons/ (the 2026-11-19 bug).
        fake_owners[commons] = (0, 0)
        fake_owners[www] = (USER_UID, USER_GID)
        chown_calls.clear()

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gen_deploy.ensure_static_roots(dry=True)
        check("repair dry-run: no chown performed", chown_calls, [])
        check("repair dry-run: reports would chown commons/",
              f"would chown {commons}" in buf.getvalue(), True)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gen_deploy.ensure_static_roots(dry=False)
        check("repair live: chowned only commons/",
              chown_calls, [(commons, USER_UID, USER_GID)])
        check("repair live: commons/ now owned by run-as user",
              fake_owners.get(commons), (USER_UID, USER_GID))
        check("repair live: reports chowned commons/",
              f"chowned {commons}" in buf.getvalue(), True)

        # 3. Clean install: second run is a no-op.
        chown_calls.clear()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gen_deploy.ensure_static_roots(dry=False)
        check("idempotent: no chown on clean install", chown_calls, [])
finally:
    shutil.rmtree(scratch, ignore_errors=True)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
