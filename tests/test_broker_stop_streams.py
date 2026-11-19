#!/usr/bin/env python3
"""A broker stop never waits on a stream a client still holds open.

The 2026-10-14 stop hung for systemd's full 90 s and was SIGKILLed. The dump
(``ShutdownWatch``) showed ``asyncio.run`` cancelling a ``Server.handle`` task
that would not end. The cause: ending a live chat at the stop writes its
record, ``record_changed`` puts it on every live-list queue, and the stream's
``asyncio.wait_for(queue.get(), ...)`` -- which on Python 3.11 swallows a
cancel that lands as its inner future completes (CPython gh-86296) -- returned
the record instead of raising, so the loop sent it and waited again, pinging
a client that was still connected.

Two fixes, each enough on its own and both tested here together: the stream
loops wait under ``asyncio.timeout`` rather than ``wait_for``, and ``amain``
puts ``None`` on every live-list queue after the sessions stop, which
``stream_records`` returns on, so a stop does not depend on a cancel at all.

The case runs the real ``amain`` in a process of its own, as
tests/test_broker_shutdown_dump.py does: a stand-in live session whose
``stop()`` writes its record as its last act, exactly the 10-14 sequence, and
a real client holding ``GET /v1/sessions/events`` open across the SIGTERM.

  1. **The broker exits 0 within a few seconds** of SIGTERM, the client still
     connected. Before the fix it hung until killed, its dump written.
  2. **The client saw the session's last record** before the stream ended:
     the stop still tells the live list what happened.
  3. **The stream ended** from the broker's side, rather than being cut.

Run: python3 tests/test_broker_stop_streams.py
"""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent

results = []

DRIVER = r'''
import asyncio, importlib.machinery, importlib.util, sys
from pathlib import Path

agents, state, port = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(agents / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
cfg = mod.cfg
# Admission reads no real box here: a fixture session must not be refused
# because the machine running the suite is short of memory (a landing
# got 429 for one, 2026-11-09). test_broker_seats is where admission is tested.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
cfg.STATE_DIR = state
cfg.SESSION_DIR = state / "sessions"
cfg.TOKEN_PATH = state / "token"
cfg.SHUTDOWN_DUMP_S = 3
cfg.SHUTDOWN_FALLBACK_S = 30
mod.Broker.prepare_records = lambda self: None
mod.Broker.scan_for_new = lambda self: None

async def no_models():
    return None
mod.fetch_codex_models = no_models
# The start's steps that change the plugins folder never run against
# this box's own (2026-11-18: the built-in take-over removed a live copy).
mod.keep_listed_themes = no_models
mod.take_over_built_ins = no_models
mod.keep_moved_to_market = no_models

class StandIn:
    """A live chat whose stop writes its record, as Session.stop does."""
    agent, id, state, provider, proc = "ada", "0f0f0f0f-stop", "running", "claude", None
    def __init__(self, broker):
        self.broker = broker
        self.live = True
    def is_live(self):
        return self.live
    def status(self):
        return "idle" if self.live else "ended"
    def snapshot(self):
        return {"type": "broker.session", "session_id": self.id,
                "state": self.state}
    async def stop(self):
        await asyncio.sleep(0)
        self.live = False
        self.state = "exited"
        # The last act, with nothing awaited after it: the record lands on
        # the live-list queue in the same loop step amain returns in.
        self.broker.record_changed(self)

real_init = mod.Broker.__init__
def init(self):
    real_init(self)
    self.sessions[StandIn.id] = StandIn(self)
mod.Broker.__init__ = init

sys.exit(asyncio.run(mod.amain("127.0.0.1", port)))
'''


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok and detail:
        print("      " + detail.replace("\n", "\n      "))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Reader:
    """Whatever arrives on a socket, appended as it comes, and when it ended."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.data = b""
        self.closed = False
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        try:
            while chunk := self.sock.recv(65536):
                self.data += chunk
        except OSError:
            pass
        self.closed = True

    def text(self) -> str:
        return self.data.decode("utf-8", "replace")


def wait_until(test, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if test():
            return True
        time.sleep(0.05)
    return test()


def test_stop_with_a_live_list_open(tmp: Path) -> None:
    driver = tmp / "driver.py"
    driver.write_text(DRIVER, encoding="utf-8")
    state = tmp / "state"
    state.mkdir()
    port = free_port()
    lines: list[str] = []
    proc = subprocess.Popen(
        [sys.executable, str(driver), str(AGENTS), str(state), str(port)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True)
    threading.Thread(target=lambda: [lines.append(l.rstrip("\n"))
                                     for l in proc.stderr],
                     daemon=True).start()
    log = lambda: "\n".join(lines)
    sock = None
    try:
        check("the broker started",
              wait_until(lambda: "listening on" in log(), 15), log())
        token = (state / "token").read_text(encoding="utf-8").strip()
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.settimeout(None)
        sock.sendall(f"GET /v1/sessions/events HTTP/1.1\r\nHost: x\r\n"
                     f"Authorization: Bearer {token}\r\n\r\n".encode())
        reader = Reader(sock)
        check("the live list opened with its residency line",
              wait_until(lambda: "broker.residency" in reader.text(), 5),
              reader.text())
        contender = subprocess.run(
            [sys.executable, str(driver), str(AGENTS), str(state), str(free_port())],
            capture_output=True, text=True, timeout=8)
        check("a second host on a different port cannot take the same session store",
              contender.returncode != 0 and "another session host owns" in contender.stderr
              and "backfill:" not in contender.stderr, contender.stderr)
        opened = len(reader.data)

        proc.send_signal(signal.SIGTERM)
        sent = time.monotonic()
        try:
            code = proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            code = None
        took = time.monotonic() - sent
        check(f"the broker exited 0 within 5 s of SIGTERM, a live list open "
              f"({took:.1f}s)", code == 0 and took < 5,
              f"exit {code}\n{log()}")
        later = reader.data[opened:].decode("utf-8", "replace")
        check("the client was sent the ended session's record first",
              '"0f0f0f0f-stop"' in later and '"exited"' in later, later)
        check("the stream ended from the broker's side",
              wait_until(lambda: reader.closed, 3), reader.text()[-400:])
    finally:
        if sock is not None:
            sock.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def main() -> int:
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d:
        test_stop_with_a_live_list_open(Path(d))
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
