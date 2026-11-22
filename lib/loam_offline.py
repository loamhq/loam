"""Guard external operations during tests, including callers that read credentials from
a file. The suite also installs a process-level socket guard in Python children so a
missing stub fails before any request can leave the box.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import sys
import urllib.parse
from pathlib import Path

ENV = "LOAM_OFFLINE"
TEST_ENV = "LOAM_TEST"


class Offline(RuntimeError):
    """A test reached for a real external service or live install store."""


class Blocked(ConnectionRefusedError):
    """A socket off loopback in a test run. An OSError, so code that handles
    an unreachable network handles this the same way."""


def _running_test_script() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    main = sys.modules.get("__main__")
    script = getattr(main, "__file__", None) or (sys.argv[0] if sys.argv else "")
    if script:
        p = Path(script).resolve()
        if p.parent.name == "tests" and p.name.startswith("test_"):
            return True
        if p.name in ("__main__.py", "pytest", "pytest3") and any(
            Path(a).name.startswith("test_") and Path(a).suffix == ".py"
            for a in sys.argv[1:]
        ):
            return True
    return False


def offline() -> bool:
    if os.environ.get(ENV) or os.environ.get(TEST_ENV):
        return True
    if _running_test_script():
        # Propagate test detection to child processes (e.g., bin/loam-tell, bin/wake)
        # without setting LOAM_OFFLINE (which marks tests/run-all's full seal).
        os.environ.setdefault(TEST_ENV, "1")
        return True
    return False


def guard(what: str) -> None:
    """Refuse ``what`` (a real credential read or an off-box call) in a test."""
    if offline():
        raise Offline(f"refused in a test: {what} would reach a real external "
                      "service; stub it (lib/loam_offline.py)")


def _local(host: str) -> bool:
    host = (host or "").strip("[]")
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def guard_url(url: str, what: str = "") -> None:
    """``guard`` for a URL that is off the box; loopback (a test's own
    server, the broker) passes."""
    if not _local(urllib.parse.urlsplit(url).hostname or ""):
        guard(what or url.split("?")[0])


def global_ipv4(ip: str) -> bool:
    """A routable public IPv4: not documentation, private, loopback,
    link-local, reserved or shared. A DNS record is never pointed elsewhere."""
    try:
        a = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return a.is_global and not a.is_multicast


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _check(sock: socket.socket, address) -> None:
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        host = address[0] if isinstance(address, tuple) else str(address)
        if not _local(host):
            raise Blocked(f"refused in a test: a connection to {host} (lib/loam_offline.py)")


def block_network() -> None:
    """Every socket connect off loopback fails in this process."""
    def connect(self, address):
        _check(self, address)
        return _real_connect(self, address)

    def connect_ex(self, address):
        _check(self, address)
        return _real_connect_ex(self, address)
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
