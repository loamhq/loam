#!/usr/bin/env python3
"""Verify offline test isolation for credentials and network calls while allowing local
fixture servers.

Run: python3 tests/test_offline.py
"""

import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import loam_offline  # noqa: E402


def test_guards() -> None:
    assert loam_offline.offline()
    import loam_github
    import loam_github_app
    calls = [lambda: loam_github.call("GET", "/user"),
             lambda: loam_github_app._request("GET", "https://api.github.com/app")]
    for i, call in enumerate(calls):
        try:
            call()
        except loam_offline.Offline:
            continue
        raise AssertionError(f"call {i} was not refused")


def test_network() -> None:
    env = {**os.environ, "LOAM_OFFLINE": "1",
           "PYTHONPATH": str(ROOT / "tests" / "offline")}
    probe = ("import socket\n"
             "s = socket.socket()\n"
             "try:\n"
             "    s.connect(('1.1.1.1', 443)); print('reached')\n"
             "except ConnectionRefusedError as e:\n"
             "    print('blocked' if 'refused in a test' in str(e) else 'other')\n"
             "l = socket.socket(); l.bind(('127.0.0.1', 0)); l.listen()\n"
             "c = socket.socket(); c.connect(l.getsockname()); print('loopback')\n")
    out = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True,
                         text=True, timeout=30)
    assert out.stdout.split() == ["blocked", "loopback"], (out.stdout, out.stderr)
    if os.environ.get("LOAM_OFFLINE"):   # under tests/run-all: this process too
        try:
            socket.create_connection(("1.1.1.1", 443), timeout=5)
            raise AssertionError("reached")
        except loam_offline.Blocked:
            pass


def test_ranges() -> None:
    assert loam_offline.global_ipv4("8.8.8.8")
    for ip in ("203.0.113.8", "198.51.100.1", "192.0.2.1", "10.0.0.1", "127.0.0.1",
               "0.0.0.0", "100.64.0.1", "not-an-ip"):
        assert not loam_offline.global_ipv4(ip), ip


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
