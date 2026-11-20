#!/usr/bin/env python3
"""Exercise the installer's progress display through a terminal and a pipe."""

import errno
import os
import pty
import re
import subprocess
import sys
import tempfile
from pathlib import Path

INSTALLER = Path(__file__).resolve().parents[1] / "bin" / "loam-install"
EXERCISE = """
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

loader = importlib.machinery.SourceFileLoader('installer', sys.argv[1])
installer = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(installer)
installer.LOG = Path(sys.argv[2])
with installer.progress('Installing software', 'Software installed.'):
    installer.run([sys.executable, '-c', 'import time; print("package output"); time.sleep(1.2)'])
try:
    with installer.progress('Starting the app', 'App started.'):
        raise installer.Stop('the app failed')
except installer.Stop as exc:
    installer.say(exc.say)
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "install.log"
        args = [sys.executable, "-c", EXERCISE, str(INSTALLER), str(log)]
        env = {**os.environ, "TERM": "xterm-256color"}
        plain = subprocess.run(args, env=env, capture_output=True, check=True).stdout
        assert plain.decode() == ("  Installing software…\n  ✓ Software installed.\n"
                                  "  Starting the app…\nthe app failed\n"), plain
        assert "package output\n" in log.read_text()
        print("PASS  redirected output is plain, with command output in the log")

        master, terminal = pty.openpty()
        try:
            subprocess.run(args, env=env, stdout=terminal, check=True, timeout=10)
        finally:
            os.close(terminal)
        try:
            output = bytearray()
            while chunk := os.read(master, 4096):
                output.extend(chunk)
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise
        finally:
            os.close(master)
        display = output.decode()
        assert "\r\x1b[K  | 0:00  Installing software…" in display, display
        assert re.search(r"\r\x1b\[K  / 0:0[1-9]  Installing software…", display), display
        assert "\r\x1b[K  ✓ Software installed.\r\n" in display, display
        assert "\r\x1b[Kthe app failed\r\n" in display, display
        assert "App started." not in display and "package output" not in display, display
        print("PASS  terminal ticks in place and clears before success and error lines")


if __name__ == "__main__":
    main()
