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


def test_child_propagation() -> None:
    saved_offline = os.environ.pop("LOAM_OFFLINE", None)
    saved_test = os.environ.pop("LOAM_TEST", None)
    try:
        assert loam_offline.offline()
        assert os.environ.get("LOAM_TEST") == "1"
        env = {k: v for k, v in os.environ.items()
               if k not in ("LOAM_OFFLINE", "LOAM_ATTENTION_DIR", "LOAM_CONFIG")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        out = subprocess.run([str(ROOT / "bin" / "loam"), "tell", "direct child probe",
                              "--by", "test"], env=env, capture_output=True, text=True, timeout=30)
        assert out.returncode == 1, (out.returncode, out.stdout, out.stderr)
        assert out.stdout.strip().startswith("failed: refused in a test:"), (out.stdout, out.stderr)
    finally:
        os.environ.pop("LOAM_TEST", None)
        if saved_offline is not None:
            os.environ["LOAM_OFFLINE"] = saved_offline
        if saved_test is not None:
            os.environ["LOAM_TEST"] = saved_test


def test_config_guard() -> None:
    """Any test (including standalone `python3 tests/test_*.py`) must refuse
    the live install config and any partial scratch config that leaks live
    install paths (vault, workspaces, logs, state, plugins, user_unit_dir)."""
    base_env = {k: v for k, v in os.environ.items()
                if not k.startswith("LOAM_") and k != "PYTEST_CURRENT_TEST"}
    base_env["PYTHONDONTWRITEBYTECODE"] = "1"

    # 1. Standalone test_check.py without LOAM_CONFIG refuses immediately.
    r1 = subprocess.run([sys.executable, str(ROOT / "tests" / "test_check.py")],
                        env=base_env, capture_output=True, text=True, timeout=30)
    assert r1.returncode != 0, (r1.returncode, r1.stdout, r1.stderr)
    assert "refused in a test: loam_config would load the live install config" in r1.stderr, r1.stderr

    # 2. Explicit LOAM_CONFIG=/etc/loam/loam.toml in a test refuses immediately.
    r2 = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(ROOT / 'lib')!r}); import loam_config"],
        env={**base_env, "LOAM_OFFLINE": "1", "LOAM_CONFIG": "/etc/loam/loam.toml"},
        capture_output=True, text=True, timeout=30,
    )
    assert r2.returncode != 0, (r2.returncode, r2.stdout, r2.stderr)
    assert "refused in a test: loam_config would load the live install config" in r2.stderr, r2.stderr

    with tempfile.TemporaryDirectory(prefix="loam-cfg-guard-") as td:
        t = Path(td)
        # 3. Partial scratch LOAM_CONFIG omitting paths.home/logs/workspaces/user_unit_dir refuses.
        partial = t / "partial.toml"
        partial.write_text(f'[paths]\nplugins = "{t / "plugins"}"\n', encoding="utf-8")
        r3 = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, {str(ROOT / 'lib')!r}); import loam_config"],
            env={**base_env, "LOAM_OFFLINE": "1", "LOAM_CONFIG": str(partial)},
            capture_output=True, text=True, timeout=30,
        )
        assert r3.returncode != 0, (r3.returncode, r3.stdout, r3.stderr)
        assert "still reaches live install path(s):" in r3.stderr, r3.stderr
        for leaked in ("paths.vault=", "paths.workspaces=", "paths.logs=", "paths.user_unit_dir="):
            assert leaked in r3.stderr, (leaked, r3.stderr)

        # 4. Complete scratch LOAM_CONFIG (setting paths.home under temp) succeeds cleanly.
        clean = t / "clean.toml"
        clean.write_text(f'[paths]\nhome = "{t / "home"}"\n', encoding="utf-8")
        r4 = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, {str(ROOT / 'lib')!r}); import loam_config as lc; print(lc.USER_UNIT_DIR)"],
            env={**base_env, "LOAM_OFFLINE": "1", "LOAM_CONFIG": str(clean)},
            capture_output=True, text=True, timeout=30,
        )
        assert r4.returncode == 0, (r4.returncode, r4.stdout, r4.stderr)
        assert str(t / "home") in r4.stdout, r4.stdout


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
