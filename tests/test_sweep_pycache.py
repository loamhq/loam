"""Exercise the nightly bytecode sweep against a private prefix cache."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SWEEP = Path(__file__).resolve().parents[1] / "bin" / "sweep-pycache"


def test(root: Path) -> None:
    cache = root / "cache"
    source = root / "src"
    source.mkdir()
    live = source / "live.py"
    live.write_text("value = 42\n")
    gone = root / "gone"
    gone.mkdir()
    discarded = gone / "discarded.py"
    discarded.write_text("value = 42\n")
    subprocess.run([sys.executable, "-I", "-X", f"pycache_prefix={cache}",
                    "-m", "py_compile", str(live), str(discarded)], check=True)
    discarded.unlink()
    gone.rmdir()
    live_cache = cache / source.relative_to("/")
    gone_cache = cache / gone.relative_to("/")
    live_pyc = live_cache / Path(importlib.util.cache_from_source(live)).name
    stale = live_cache / "old.cpython-311.pyc"
    partial = live_cache / "incomplete.cpython-311.pyc.1234"
    recent_partial = live_cache / "writing.cpython-311.pyc.5678"
    for entry in (stale, partial, recent_partial):
        entry.write_bytes(b"cache data")
    old = time.time() - 15 * 86400
    os.utime(stale, (old, old))
    os.utime(partial, (old, old))

    outside = root / "outside"
    outside.mkdir()
    sentinel = outside / "outside.pyc"
    sentinel.write_bytes(b"keep")
    (cache / "link").symlink_to(outside, target_is_directory=True)
    (live_cache / "link.pyc").symlink_to(sentinel)
    unrelated = live_cache / "notes.txt"
    unrelated.write_text("keep")

    config = root / "loam.toml"
    config.write_text(f"[paths]\npycache = {json.dumps(str(cache))}\n")
    argv = [sys.executable, "-IB", str(SWEEP)]
    env = {**os.environ, "LOAM_CONFIG": str(config)}
    out = subprocess.check_output([*argv, "--dry-run"], env=env, text=True)
    assert "would remove 3 files" in out, out
    assert gone_cache.is_dir() and stale.is_file() and partial.is_file()

    out = subprocess.check_output(argv, env=env, text=True)
    assert "removed 3 files" in out, out
    assert not gone_cache.exists() and not stale.exists() and not partial.exists()
    for kept in (live_pyc, recent_partial, sentinel, live_cache / "link.pyc", unrelated):
        assert kept.is_file(), kept
    assert "removed 0 files" in subprocess.check_output(argv, env=env, text=True)
    print("PASS sweep prunes orphaned and idle bytecode, keeps current imports and ignores symlinks")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as scratch:
        test(Path(scratch))
