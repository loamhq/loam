"""A systemd user manager of a test's own, so no test makes a unit on the live one.

Since 2026-11-16. tests/test_schedule.py proves schedules against a REAL user
manager -- a timer installed, armed, moved, fired once -- and did it in the
box user's own manager, in its runtime unit directory under the names
`loam-*-zzsched<pid>` and `zzother<pid>`. It cleaned up after itself, but every
landing's test run made and removed timers in the manager every persona's
wakes and crons live in (seen 11-16), and since landings test outside the
live lock, two suites may now run at once.

So the test starts a second user manager for this uid, private to it:

    XDG_RUNTIME_DIR    a temp directory; `systemctl --user` reaches the
                       manager at `$XDG_RUNTIME_DIR/systemd/private`, so the
                       test's own process points there and nowhere else
                       (DBUS_SESSION_BUS_ADDRESS is dropped, or it would win)
    SYSTEMD_UNIT_PATH  the temp runtime unit directory and the distribution's
                       /usr/lib/systemd/user (targets and slices only) -- not
                       ~/.config/systemd/user or /etc/systemd/user, so not one
                       of the box's own units is loaded, and none can fire
    XDG_CONFIG_HOME,   temp, for the manager and for every `systemctl` the
    XDG_DATA_HOME      test runs, so an `enable` without `--runtime` links
                       into the temp tree too

Its timers are real timers and fire, as the live manager's do (measured the
same day, also inside the landing's user namespace). The manager is put in a
cgroup of its own under the caller's first, where the cgroup tree is writable:
a user manager moves every process of the cgroup it starts in into its
`init.scope`, which would otherwise be the whole calling unit. It is stopped,
and its cgroup removed, at the end.

A machine where it cannot start fails the test, never falls back to the live
manager.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

SYSTEMD = next((p for p in ("/lib/systemd/systemd", "/usr/lib/systemd/systemd")
                if os.path.exists(p)), "/lib/systemd/systemd")
DISTRO_UNITS = "/usr/lib/systemd/user"


class PrivateManager:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.run_dir = self.root / "run"
        self.unit_dir = self.run_dir / "systemd" / "user"
        self.proc: subprocess.Popen | None = None
        self.cgroup: Path | None = None
        self._saved: dict[str, str | None] = {}

    def env(self) -> dict[str, str]:
        """What points a `systemctl --user` at this manager."""
        return {"XDG_RUNTIME_DIR": str(self.run_dir),
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "XDG_DATA_HOME": str(self.root / "data"),
                "SYSTEMD_UNIT_PATH": f"{self.unit_dir}:{DISTRO_UNITS}"}

    def _own_cgroup(self) -> Path | None:
        try:
            line = Path("/proc/self/cgroup").read_text().splitlines()[0]
        except (OSError, IndexError):
            return None
        rel = line.partition("::")[2].strip()
        if not line.startswith("0::") or not rel:
            return None
        parent = Path("/sys/fs/cgroup") / rel.lstrip("/")
        child = parent / f"loam-test-systemd-{os.getpid()}"
        try:
            child.mkdir()
        except OSError:
            return None
        return child

    def start(self, wait: float = 20) -> "PrivateManager":
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.chmod(0o700)
        for name in ("config", "data"):
            (self.root / name).mkdir(exist_ok=True)
        env = {k: v for k, v in os.environ.items()
               if k not in ("DBUS_SESSION_BUS_ADDRESS", "NOTIFY_SOCKET",
                            "MANAGERPID", "INVOCATION_ID", "LISTEN_PID",
                            "LISTEN_FDS")}
        env.update(self.env())
        self.cgroup = self._own_cgroup()
        procs = str(self.cgroup / "cgroup.procs") if self.cgroup else None

        def into_cgroup() -> None:
            if procs:
                try:
                    with open(procs, "w") as handle:
                        handle.write(str(os.getpid()))
                except OSError:
                    pass

        self.proc = subprocess.Popen(
            [SYSTEMD, "--user", "--log-target=console"], env=env,
            cwd=str(self.root), stdin=subprocess.DEVNULL,
            stdout=(self.root / "manager.log").open("w"),
            stderr=subprocess.STDOUT, preexec_fn=into_cgroup)
        for key, value in self.env().items():
            self._saved.setdefault(key, os.environ.get(key))
            os.environ[key] = value
        self._saved.setdefault("DBUS_SESSION_BUS_ADDRESS",
                               os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None))
        deadline = time.monotonic() + wait
        state = ""
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                break
            state = self.systemctl("is-system-running").stdout.strip()
            if state in ("running", "degraded"):
                return self
            time.sleep(0.2)
        log = (self.root / "manager.log").read_text(errors="replace")[-600:]
        self.stop()
        raise RuntimeError(f"the private user manager did not start "
                           f"(state {state!r}): {log}")

    def systemctl(self, *args: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items()
               if k != "DBUS_SESSION_BUS_ADDRESS"}
        env.update(self.env())
        return subprocess.run(["systemctl", "--user", *args], env=env,
                              capture_output=True, text=True, check=False,
                              timeout=30)

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            # `exit` stops every unit it runs, then the manager itself.
            self.systemctl("exit")
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.proc = None
        if self.cgroup is not None:
            # Anything a unit left running dies with the cgroup.
            try:
                (self.cgroup / "cgroup.kill").write_text("1")
            except OSError:
                pass
            for _ in range(50):
                try:
                    for sub in sorted(self.cgroup.rglob("*"), reverse=True):
                        if sub.is_dir():
                            sub.rmdir()
                    self.cgroup.rmdir()
                    break
                except FileNotFoundError:
                    break
                except OSError:
                    time.sleep(0.1)
            self.cgroup = None
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._saved = {}
        shutil.rmtree(self.root / "run", ignore_errors=True)
