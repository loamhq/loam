"""An install-wide admission lock. Its pathname stays put across process replacement."""
from contextlib import contextmanager
import fcntl
from pathlib import Path
import subprocess

import loam_broker as cfg


@contextmanager
def admission(*, exclusive=False):
    cfg.STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (cfg.STATE_DIR / "admission.lock").open("a") as fd:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield exclusive or not (cfg.STATE_DIR / "replacing-host.json").exists()


def closed():
    with admission() as admitted:
        return not admitted

def revision():
    root = Path(__file__).resolve().parents[1]
    saved = root / "source.ready"
    if saved.is_file():
        return saved.read_text().strip()
    result = subprocess.run(["git", "-C", str(root),
                             "rev-parse", "--verify", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None
