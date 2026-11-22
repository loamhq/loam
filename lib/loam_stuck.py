"""Legacy command-stop records and the reviewer's quiet-command watcher.

Engineer jobs yield through their shell tools. Only the reviewer opts into
this watcher. Historical engineer receipts still read child-stops.jsonl.
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from pathlib import Path
from typing import Callable

import loam_live

CHILD_STOP_MINUTES = 30
CHECK_SECONDS = 60
GRACE_SECONDS = 10
RECORD = "child-stops.jsonl"
# A command can start a moment before the event that names it is written.
SLACK_SECONDS = 1.0


def agy_busy(event: dict) -> str | None:
    """What Antigravity's last event says is running, or None."""
    if event.get("event") != "step_update":
        return None
    step = event.get("step_update")
    step = step if isinstance(step, dict) else event
    if step.get("state") != "ACTIVE" or step.get("step_type") != "tool":
        return None
    info = step.get("tool_info") if isinstance(step.get("tool_info"), dict) else {}
    params = info.get("parameters") if isinstance(info.get("parameters"), dict) else {}
    return str(params.get("CommandLine") or step.get("tool_name") or "a tool")


def codex_busy(event: dict) -> str | None:
    """What Codex's last event says is running, or None."""
    item = event.get("item") if isinstance(event.get("item"), dict) else {}
    if event.get("type") != "item.started" or item.get("type") != "command_execution":
        return None
    return str(item.get("command") or "a command")


def read_stops(job_dir: Path) -> list[dict]:
    try:
        lines = (job_dir / RECORD).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _cmdline(pid: int, proc: Path) -> str:
    try:
        raw = (proc / str(pid) / "cmdline").read_bytes()
    except OSError:
        return "?"
    return raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()[:300] or "?"


def _pgid(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _subtree(pid: int, kids: dict[int, list[int]]) -> list[int]:
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        if p in out:
            continue
        out.append(p)
        todo.extend(kids.get(p, ()))
    return out


class Watch:
    """Watches one engine process and the stream it writes.

    The reader calls ``line`` for every line it writes to the stream; the
    thread ``start`` begins calls ``check`` every ``check_seconds``.
    The reviewer opts in at launch."""

    def __init__(self, root_pid: int, busy: Callable[[dict], str | None],
                 job_dir: Path,
                 limit_minutes: float | None = None,
                 check_seconds: float | None = None,
                 clock: Callable[[], float] = time.time,
                 proc: Path = loam_live.PROC):
        self.root = root_pid
        self.busy = busy
        self.job_dir = job_dir
        self.limit = (CHILD_STOP_MINUTES if limit_minutes is None else limit_minutes) * 60
        self.check_seconds = CHECK_SECONDS if check_seconds is None else check_seconds
        self.clock = clock
        self.proc = proc
        self.started = clock()
        self.last_at: float | None = None
        self.prev_at: float | None = None
        self.last_event: dict = {}
        self.stopped: set[int] = set()
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None

    def line(self, text: str) -> None:
        try:
            event = json.loads(text)
        except ValueError:
            event = None
        with self._lock:
            self.prev_at, self.last_at = self.last_at, self.clock()
            if isinstance(event, dict):
                self.last_event = event

    def candidates(self) -> list[int]:
        """The command's topmost processes: descendants of the engine that
        started no earlier than the event before the last one."""
        with self._lock:
            since = self.prev_at if self.prev_at is not None else self.last_at
        if since is None:
            return []
        kids = loam_live.children(self.proc)
        tree = _subtree(self.root, kids)
        fresh = set()
        for pid in tree:
            if pid == self.root or pid in self.stopped:
                continue
            at = loam_live.started_ts(pid, self.proc)
            if at is not None and at >= since - SLACK_SECONDS:
                fresh.add(pid)
        parent = {c: p for p, cs in kids.items() for c in cs}
        return sorted(pid for pid in fresh if parent.get(pid) not in fresh)

    def check(self) -> list[dict]:
        """Stop the running command if the engine has been silent past the
        limit while its last event says a command runs; the records made."""
        now = self.clock()
        with self._lock:
            quiet_from = self.last_at if self.last_at is not None else self.started
            event = dict(self.last_event)
        if now - quiet_from < self.limit:
            return []
        command = self.busy(event) if event else None
        if command is None:
            return []
        made = []
        for pid in self.candidates():
            argv = _cmdline(pid, self.proc)
            how = self._stop(pid)
            self.stopped.add(pid)
            record = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                      "pid": pid, "argv": argv, "command": command[:300],
                      "quiet_minutes": round((now - quiet_from) / 60),
                      "how": how}
            try:
                with open(self.job_dir / RECORD, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
            except OSError:
                pass
            made.append(record)
        return made

    def _stop(self, pid: int) -> str:
        """SIGTERM, then SIGKILL after the grace; by group when the command
        leads its own group and that group is not the engine's."""
        group = _pgid(pid)
        own = group == pid and group != _pgid(self.root)
        targets = [] if own else _subtree(pid, loam_live.children(self.proc))

        def send(sig: int) -> None:
            if own:
                try:
                    os.killpg(pid, sig)
                except OSError:
                    pass
                return
            for t in targets:
                try:
                    os.kill(t, sig)
                except OSError:
                    pass

        send(signal.SIGTERM)
        deadline = time.monotonic() + GRACE_SECONDS
        while time.monotonic() < deadline and _alive(pid):
            time.sleep(0.2)
        if _alive(pid):
            send(signal.SIGKILL)
            return "SIGKILL after SIGTERM" + (" (group)" if own else " (subtree)")
        return "SIGTERM" + (" (group)" if own else " (subtree)")

    def _run(self) -> None:
        while not self._done.wait(self.check_seconds):
            try:
                self.check()
            except Exception:  # noqa: BLE001 - a watcher never ends a job
                pass

    def start(self) -> "Watch":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """End the watch, after any stop in flight has been recorded: the
        engine can finish in the moment between a command's death and its
        record, and the receipt is read right after."""
        self._done.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(GRACE_SECONDS + 5)


def receipt_text(job_dir: Path) -> str:
    """The receipt's paragraph on stopped commands, or nothing."""
    stops = read_stops(job_dir)
    if not stops:
        return ""
    lines = [f"- {s.get('at')}: pid {s.get('pid')} `{s.get('argv')}`, after "
             f"{s.get('quiet_minutes')} min with no engine event while it ran "
             f"`{s.get('command')}` ({s.get('how')})" for s in stops]
    noun = "command" if len(stops) == 1 else "commands"
    return (f"\n**The launcher stopped {len(stops)} {noun} that did not "
            f"return**, so the engine got an error back and carried on; the "
            f"job itself was not stopped (`lib/loam_stuck.py`, after "
            f"{CHILD_STOP_MINUTES} minutes of engine silence):\n\n"
            + "\n".join(lines) + "\n")
