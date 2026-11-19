#!/usr/bin/env python3
"""A stop that hangs writes what the broker is doing; one that does not, nothing.

A persona's brief, 2026-10-13: four broker stops between 10-07 and 10-09 ignored
SIGTERM for systemd's full 90 s and were SIGKILLed with no stack captured.
``ShutdownWatch`` in bin/session-broker writes one line per live session and
every thread's stack, by name, ``cfg.SHUTDOWN_DUMP_S`` after the signal, and
the stop then goes on as before.

Each case runs the real ``amain`` in a process of its own -- a real signal to
a real pid, read back off that process's stderr -- with the state directory
in a temp dir, the start-up passes that read the box's records stubbed out,
and the bounds shortened to seconds:

  1. **A hang writes the dump.** The broker is held the way the 10-09 lead
     says it may be: a ``to_thread`` call still outstanding when shutdown
     begins, which ``asyncio.run`` joins with no timeout on 3.11. A live
     session stands beside it. After SIGTERM the dump names the session and
     its state, names ``MainThread`` and the worker ``asyncio_0`` with the
     frame the worker is stuck in, and the process is still running after it,
     because the dump is an instrument and not a stop.
  2. **The GIL-free fallback is cancelled** by that dump: no faulthandler
     block follows it, past the fallback's own bound.
  3. **A hang ends itself** (2026-11-10): the held task is cancelled with a
     bound and, when it will not end, named in the journal and the broker
     exits 0 -- before systemd's SIGKILL, which the 11-10 stop needed.
  4. **The backstop**: a worker thread still busy after the loop is done is
     named and the broker exits at ``SHUTDOWN_EXIT_S``.
  5. **No start once the stop has begun**: ``start_session`` answers 503.
  6. **A clean stop writes nothing new**: same broker, no hold, exit 0 inside
     the bound, and no ``shutdown:`` line at all.

Run: python3 tests/test_broker_shutdown_dump.py
"""

import os
import shutil
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
import asyncio, importlib.machinery, importlib.util, sys, threading
from pathlib import Path

agents, state, port, mode = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
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
cfg.SHUTDOWN_DUMP_S = 2
cfg.SHUTDOWN_FALLBACK_S = 4
# The stop's own bounds (2026-11-10): the hang outlives the dump and the
# fallback, then the broker ends it itself; the worker-thread case reaches
# the backstop.
cfg.SHUTDOWN_SESSIONS_S = 3
cfg.SHUTDOWN_TASKS_S = 9
cfg.SHUTDOWN_EXIT_S = 4 if mode == "thread" else 30
mod.backfill_records = lambda: (0, 0)
mod.sync_titles = lambda: (0, [])
mod.prune_fork_duplicates = lambda: []
mod.Broker.adopt_app_sessions = lambda self: {"adopted": [], "refreshed": [],
                                               "agents": {}}

async def no_models():
    return None
mod.fetch_codex_models = no_models
# The start's steps that change the plugins folder never run against
# this box's own (2026-11-18: the built-in take-over removed a live copy).
mod.keep_listed_themes = no_models
mod.take_over_built_ins = no_models
mod.keep_moved_to_market = no_models

never = threading.Event()
running = threading.Event()

def held_by_a_test():
    running.set()
    never.wait()

async def worker_running():
    # The worker must be in its thread before the broker can be told to
    # stop: a task only created, not yet run, can be scheduled after the
    # stop began, find the executor closing and leave nothing behind, which
    # read as "the backstop exits at its bound (0.0s)" on a loaded box
    # (the landing of job 20261113-172022-5b8fcafb, 2026-11-14).
    while not running.is_set():
        await asyncio.sleep(0.01)

class Proc:
    pid = 4242
    returncode = None

class StandIn:
    """A session with a process behind it, as far as the dump can tell."""
    agent, id, state, proc = "ada", "0f0f0f0f-test", "running", Proc()
    def is_live(self):
        return True
    def status(self):
        return "working"
    async def stop(self):
        return None

real_init = mod.Broker.__init__
def init(self):
    real_init(self)
    if mode == "hang":
        self.sessions[StandIn.id] = StandIn()
mod.Broker.__init__ = init

async def held_innermost():
    await asyncio.get_running_loop().create_future()   # never set

async def held_outer():
    try:
        await held_innermost()
    except asyncio.CancelledError:
        await held_innermost()       # a task that will not end when cancelled

HELD = []

async def try_a_start(broker):
    while not broker.stopping:
        await asyncio.sleep(0.01)
    try:
        await broker.start_session("ada")
        print("start: a session started during the stop", file=sys.stderr, flush=True)
    except mod.BrokerError as exc:
        print(f"start: refused {exc.status} {exc.message}", file=sys.stderr, flush=True)

# A start arriving once the stop has begun, as the thread engine's did on
# 11-10: asked from inside the stop, before its own work.
real_shutdown = mod.shutdown
async def shutdown(broker, server):
    if mode == "refuse":
        await try_a_start(broker)
    await real_shutdown(broker, server)
mod.shutdown = shutdown

async def run():
    if mode == "thread":
        asyncio.get_running_loop().create_task(asyncio.to_thread(held_by_a_test))
        await worker_running()
    if mode == "hang":
        asyncio.get_running_loop().create_task(asyncio.to_thread(held_by_a_test))
        await worker_running()
        # Held by a name: a task waiting on a future nothing else holds is a
        # reference cycle the garbage collector may take before the dump
        # ("Task was destroyed but it is pending!"), which made this check
        # fail on some runs (2026-11-09).
        HELD.append(asyncio.get_running_loop().create_task(held_outer(),
                                                           name="held-outer"))
    return await mod.amain("127.0.0.1", port)

sys.exit(asyncio.run(run()))
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


class Broker:
    """A broker process, its stderr read into a list as it arrives."""

    def __init__(self, tmp: Path, mode: str) -> None:
        driver = tmp / "driver.py"
        driver.write_text(DRIVER, encoding="utf-8")
        state = tmp / f"state-{mode}"
        state.mkdir()
        # A copy of the broker, so the test can change the file under a
        # running process the way a checkout moves under a days-old broker.
        self.agents = tmp / f"agents-{mode}"
        (self.agents / "bin").mkdir(parents=True)
        (self.agents / "lib").symlink_to(AGENTS / "lib")
        shutil.copy(AGENTS / "bin" / "session-broker",
                    self.agents / "bin" / "session-broker")
        self.proc = subprocess.Popen(
            [sys.executable, str(driver), str(self.agents), str(state),
             str(free_port()), mode],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True)
        self.lines: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stderr:
            self.lines.append(line.rstrip("\n"))

    def text(self) -> str:
        return "\n".join(self.lines)

    def wait_for(self, needle: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.text():
                return True
            if self.proc.poll() is not None:
                time.sleep(0.2)
                return needle in self.text()
            time.sleep(0.05)
        return False

    def end(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()


def test_hang(tmp: Path) -> None:
    b = Broker(tmp, "hang")
    try:
        check("hang: the broker started", b.wait_for("listening on", 15), b.text())
        # The file moves on under the running broker: every line now reads
        # the same, and none of them is what the process is running.
        script = b.agents / "bin" / "session-broker"
        script.write_text("# moved on since the broker started\n"
                          * len(script.read_text().splitlines()),
                          encoding="utf-8")
        b.proc.send_signal(signal.SIGTERM)
        sent = time.monotonic()
        got = b.wait_for("shutdown: end of dump", 10)
        took = time.monotonic() - sent
        check(f"hang: the dump was written after SIGTERM ({took:.1f}s, bound 2s)",
              got and took >= 1.9, b.text())
        text = b.text()
        dump = [line for line in text.splitlines()
                if line.startswith("shutdown:")]
        print("      dump opens:")
        for line in text[text.find("shutdown: SIGTERM"):].splitlines()[:8]:
            print("      | " + line)
        check("hang: the first line names the signal and the wait",
              bool(dump) and dump[0].startswith("shutdown: SIGTERM ")
              and "has not exited" in dump[0], text)
        check("hang: one line for the live session, with its state",
              "shutdown: session ada/0f0f0f0f-test state=running "
              "status=working pid=4242" in text, text)
        check("hang: the main thread is named",
              "shutdown: thread MainThread (native" in text, text)
        check("hang: the executor worker is named",
              "shutdown: thread asyncio_0 (native" in text, text)
        worker = text[text.find("shutdown: thread asyncio_0"):]
        worker = worker[:worker.find("\nshutdown:", 1)]
        check("hang: the worker's stack holds the call it is stuck in",
              "in held_by_a_test" in worker, worker)
        check("hang: the stop's own thread is in the dump",
              "shutdown: thread shutdown-watch" in text, text)
        check("hang: the pending tasks are listed",
              "shutdown: task " in text or "shutdown: no pending tasks" in text,
              text)
        held = text[text.find("shutdown: task held-outer"):]
        held = held[:held.find("\nshutdown:", 1)]
        check("hang: a held task is followed down to its innermost frame",
              "in held_outer" in held and "in held_innermost" in held
              and "await asyncio.get_running_loop().create_future()" in held,
              held)
        check("hang: source is printed as the broker loaded it, not as the "
              "file reads now",
              "lines += self._threads()" in text
              and "moved on since the broker started" not in text, text)
        # The fallback's bound is 4 s; give it 3 s more to prove it is gone.
        time.sleep(5)
        check("hang: the broker is still stopping after the dump",
              b.proc.poll() is None, f"exit {b.proc.returncode}")
        check("hang: the faulthandler fallback did not write a second dump",
              "most recent call first" not in b.text()
              and b.text().count("shutdown: end of dump") == 1, b.text())
        # The stop's own bound (9 s here): the broker ends it, before any
        # SIGKILL, and names the task that would not end (2026-11-10).
        try:
            code = b.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            code = None
        took = time.monotonic() - sent
        check(f"hang: the broker exits by itself at its bound ({took:.1f}s, "
              f"bound 9s), exit 0", code == 0 and 8.5 <= took < 12,
              f"exit {code}\n{b.text()}")
        time.sleep(0.3)
        check("hang: the journal names the task that would not end, and says so",
              "shutdown: task held-outer held_outer did not end when cancelled"
              in b.text() and "with 1 task(s) that would not end" in b.text(),
              b.text())
    finally:
        b.end()


def test_thread(tmp: Path) -> None:
    """A worker thread still busy after the loop is done -- `asyncio.run`
    joins it with no timeout -- is past every bound but the backstop."""
    b = Broker(tmp, "thread")
    try:
        check("thread: the broker started", b.wait_for("listening on", 15), b.text())
        b.proc.send_signal(signal.SIGTERM)
        sent = time.monotonic()
        try:
            code = b.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            code = None
        took = time.monotonic() - sent
        check(f"thread: the backstop exits at its bound ({took:.1f}s, bound 4s), "
              f"exit 0", code == 0 and 3.9 <= took < 7, f"exit {code}\n{b.text()}")
        time.sleep(0.3)
        check("thread: it says it is exiting and names the worker left",
              "still here; exiting now" in b.text()
              and "shutdown: left: thread asyncio_0" in b.text(), b.text())
    finally:
        b.end()


def test_refuse(tmp: Path) -> None:
    """No session starts once the stop has begun: the 11-10 stop hung on
    one the thread engine began after SIGTERM."""
    b = Broker(tmp, "refuse")
    try:
        check("refuse: the broker started", b.wait_for("listening on", 15), b.text())
        b.proc.send_signal(signal.SIGTERM)
        try:
            code = b.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            code = None
        time.sleep(0.3)
        check("refuse: a start during the stop is refused with a 503",
              "start: refused 503 the broker is stopping" in b.text(), b.text())
        check("refuse: and the stop is clean, exit 0", code == 0, f"exit {code}")
    finally:
        b.end()


def test_clean(tmp: Path) -> None:
    b = Broker(tmp, "clean")
    try:
        check("clean: the broker started", b.wait_for("listening on", 15), b.text())
        b.proc.send_signal(signal.SIGTERM)
        sent = time.monotonic()
        try:
            code = b.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            code = None
        took = time.monotonic() - sent
        check(f"clean: exit 0 inside the bound ({took:.1f}s)",
              code == 0 and took < 2, f"exit {code}\n{b.text()}")
        time.sleep(0.3)
        check("clean: no shutdown line written",
              "shutdown:" not in b.text()
              and "most recent call first" not in b.text(), b.text())
    finally:
        b.end()


def main() -> int:
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d:
        test_hang(Path(d))
        test_thread(Path(d))
        test_refuse(Path(d))
        test_clean(Path(d))
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
