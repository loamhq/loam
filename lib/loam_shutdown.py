"""Bounded shutdown and stack diagnostics for processes that own session work."""
from __future__ import annotations

import asyncio
import faulthandler
import linecache
import os
import sys
import threading
import time
import traceback
from pathlib import Path

import loam_broker as cfg


def loaded_source(entry) -> dict[str, list[str]]:
    """This repo's source as the process loaded it: this file and every
    module imported from ``lib/``, read once while the broker starts.

    For the shutdown dump. A broker runs for days while the checkout moves on,
    and ``traceback`` reads a frame's line from the file as it is *now*: the
    10-14 dump printed a docstring under ``line 5639, in dump`` (2026-10-14).
    """
    root = Path(__file__).resolve().parent.parent
    files = {str(Path(entry)), str(Path(__file__))}
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if path and Path(path).resolve().is_relative_to(root):
            files.add(path)
    out = {}
    for path in files:
        try:
            out[path] = Path(path).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            pass
    return out


LOADED_SOURCE = {}


class ShutdownWatch:
    """What the broker is doing, written to the journal, when a stop hangs.

    Added 2026-10-13. Four stops between 10-07 and 10-09 ignored SIGTERM for
    systemd's full 90 s and were SIGKILLed with nothing captured: at +15 s
    the main thread sat idle in ``epoll_wait`` and a second thread in
    ``futex_wait_queue``, which fits an idle loop waiting on something that
    never finishes -- a task that will not end when cancelled, or
    ``asyncio.run`` joining a worker thread still inside a ``to_thread``
    call (Python 3.11 joins it with no timeout) -- and does not say which.

    Armed by the first SIGTERM or SIGINT, on a thread of its own and not on
    the loop, because the loop is the thing that may be stuck and because
    ``asyncio.run`` has stopped running it by the time it joins the executor.
    ``cfg.SHUTDOWN_DUMP_S`` later, if the process is still here, it writes
    one line per live session, every thread's stack by name and every
    asyncio task still pending; then the stop goes on as before. A stop that
    finishes in time takes the daemon thread with it and writes nothing.

    Not ``faulthandler`` alone, though it is armed too: on 3.11 it names a
    thread only by a hex ident, and the thread names and the tasks are what
    tell the two suspects apart. ``faulthandler`` needs no GIL, so it is the
    fallback at ``cfg.SHUTDOWN_FALLBACK_S`` for a stop where Python cannot
    run at all, and the first dump cancels it.
    """

    def __init__(self, broker: "Broker",
                 loop: asyncio.AbstractEventLoop) -> None:
        self.broker = broker
        self.loop = loop
        self.armed_at: float | None = None

    def arm(self, signame: str) -> None:
        if self.armed_at is not None:
            return
        self.armed_at = time.monotonic()
        self.signame = signame
        faulthandler.dump_traceback_later(cfg.SHUTDOWN_FALLBACK_S, exit=False,
                                          file=sys.stderr)
        timer = threading.Timer(cfg.SHUTDOWN_DUMP_S, self.dump)
        timer.name = "shutdown-watch"
        timer.daemon = True
        timer.start()
        # The backstop (2026-11-10): past SHUTDOWN_EXIT_S the broker says
        # what is still running and exits, rather than wait for SIGKILL.
        last = threading.Timer(cfg.SHUTDOWN_EXIT_S, self.give_up)
        last.name = "shutdown-exit"
        last.daemon = True
        last.start()

    def give_up(self) -> None:
        waited = time.monotonic() - (self.armed_at or time.monotonic())
        tasks = [line for line in self._tasks()
                 if line.startswith("shutdown: task ")]
        threads_left = sorted(t.name for t in threading.enumerate()
                              if not t.daemon and t is not threading.main_thread())
        lines = [f"shutdown: {self.signame} {waited:.1f}s ago and the broker is "
                 f"still here; exiting now, with {len(tasks)} task(s) and "
                 f"{len(threads_left)} worker thread(s) left running"]
        lines += [line.replace("shutdown: task ", "shutdown: left: task ", 1)
                  for line in tasks]
        lines += [f"shutdown: left: thread {name}" for name in threads_left]
        sys.stderr.write("\n".join(lines) + "\n")
        hard_exit(0)

    def dump(self) -> None:
        faulthandler.cancel_dump_traceback_later()
        waited = time.monotonic() - (self.armed_at or time.monotonic())
        lines = [f"shutdown: {self.signame} {waited:.1f}s ago and the broker "
                 f"has not exited; sessions, threads and tasks follow"]
        lines += self._sessions()
        lines += self._threads()
        lines += self._tasks()
        lines.append("shutdown: end of dump; the stop goes on")
        sys.stderr.write("\n".join(lines) + "\n")
        sys.stderr.flush()

    def _sessions(self) -> list[str]:
        try:
            live = [s for s in list(self.broker.sessions.values())
                    if s.is_live()]
        except Exception as exc:  # noqa: BLE001 - the dump must reach the stacks
            return [f"shutdown: sessions unreadable ({exc!r})"]
        if not live:
            return ["shutdown: no live sessions"]
        out = []
        for s in live:
            try:
                status = s.status()
            except Exception as exc:  # noqa: BLE001
                status = f"? ({exc!r})"
            pid = getattr(s.proc, "pid", None)
            out.append(f"shutdown: session {s.agent}/{s.id} state={s.state} "
                       f"status={status} pid={pid}")
        return out

    def _threads(self) -> list[str]:
        names = {t.ident: t for t in threading.enumerate()}
        out = []
        for ident, frame in sys._current_frames().items():
            t = names.get(ident)
            label = (f"{t.name} (native {t.native_id}, "
                     f"{'daemon' if t.daemon else 'not daemon'})"
                     if t else f"unnamed ident {ident:#x}")
            out.append(f"shutdown: thread {label}, most recent call last:")
            stack = [f for f, _ in traceback.walk_stack(frame)]
            for f in reversed(stack):
                out += self._frame(f)
        return out

    @staticmethod
    def _frame(frame) -> list[str]:
        """A frame as ``traceback`` prints one, its source line read from what
        the process loaded (`LOADED_SOURCE`). A file of this repo that was not
        loaded by then prints no line, rather than today's."""
        code, lineno = frame.f_code, frame.f_lineno
        out = [f'  File "{code.co_filename}", line {lineno}, '
               f"in {code.co_name}"]
        lines = LOADED_SOURCE.get(code.co_filename)
        if lines is not None:
            text = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        elif Path(code.co_filename).resolve().is_relative_to(
                Path(__file__).resolve().parent.parent):
            text = ""
        else:
            text = linecache.getline(code.co_filename, lineno)
        if text.strip():
            out.append(f"    {text.strip()}")
        return out

    @staticmethod
    def _awaiting(task: asyncio.Task) -> list:
        """Every frame a task is suspended in, outermost first, down to the
        innermost coroutine. ``Task.get_stack`` gives only the outermost, so
        the 10-14 dump stopped at ``Server.handle`` and could not name the
        route; following ``cr_await`` (``gi_yieldfrom`` for a generator-based
        awaitable) reaches the line actually waiting."""
        frames, obj = [], task.get_coro()
        while obj is not None and len(frames) < 64:
            frame = getattr(obj, "cr_frame", None) or getattr(obj, "gi_frame",
                                                              None)
            if frame is None:
                break
            frames.append(frame)
            obj = (getattr(obj, "cr_await", None)
                   or getattr(obj, "gi_yieldfrom", None))
        return frames

    def _tasks(self) -> list[str]:
        try:
            tasks = [t for t in asyncio.all_tasks(self.loop) if not t.done()]
        except Exception as exc:  # noqa: BLE001 - a closed loop has no tasks
            return [f"shutdown: tasks unreadable ({exc!r})"]
        if not tasks:
            return ["shutdown: no pending tasks"]
        out = []
        for task in tasks:
            coro = task.get_coro()
            out.append(f"shutdown: task {task.get_name()} "
                       f"{getattr(coro, '__qualname__', coro)!s}"
                       f"{' cancelling' if task.cancelling() else ''}:")
            for frame in self._awaiting(task) or task.get_stack():
                out += self._frame(frame)
        return out


def hard_exit(code: int) -> None:
    """Exit now, past anything still running. Every record the broker
    writes is written whole and closed as it goes (a session log line, a
    record's atomic replace, a thread's append), so there is no buffer of
    ours to lose; stdout and stderr are flushed for the journal."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001 - exiting regardless
            pass
    os._exit(code)


async def shutdown(broker: "Broker", server: "Server") -> None:
    """The stop, bounded (2026-11-10; see ``cfg.SHUTDOWN_*``). Each step that
    runs out of time names what it left in the journal and the stop goes on."""
    def say(line: str) -> None:
        print(f"shutdown: {line}", file=sys.stderr, flush=True)

    begun = time.monotonic()
    # Every session ends with the broker. A persona process outliving its
    # broker would hold ~450 MB that nothing can reach, answer to nobody, and
    # still be counted against the cap by nothing at all. Together, not one
    # after another: seven sessions at 10 s each was past systemd's 90 s.
    stops = {asyncio.ensure_future(s.stop()): s for s in broker.live()}
    if stops:
        done, pending = await asyncio.wait(stops, timeout=cfg.SHUTDOWN_SESSIONS_S)
        for fut in done:
            if fut.exception() is not None:
                s = stops[fut]
                say(f"session {s.agent}/{s.id} failed to stop "
                    f"({fut.exception()!r})")
        for fut in pending:
            s = stops[fut]
            say(f"session {s.agent}/{s.id} did not stop within "
                f"{cfg.SHUTDOWN_SESSIONS_S}s; going on without it")
    # Each thread's turn in flight ends on its session's exit. One that had
    # said something posts it and its last line ("Ada's turn ended: the
    # broker restarted"); one cut before a word, and every queued one, keeps
    # its input on disk for the next start and posts nothing (2026-11-19).
    engine = getattr(server, "threads", None)
    workers = {t: tid for tid, t in getattr(engine, "workers", {}).items()
               if not t.done()}
    if workers:
        _, pending = await asyncio.wait(workers, timeout=cfg.SHUTDOWN_TASKS_S)
        for task in pending:
            say(f"thread {workers[task]}: its turn did not end within "
                f"{cfg.SHUTDOWN_TASKS_S}s; cancelled")
    # And every live list told it is over, after the records those stops
    # wrote. A per-chat stream already ends on its session's `None`.
    broker.close_record_streams()
    # Everything else, cancelled here with a bound, rather than by
    # `asyncio.run`, which waits on a cancelled task for ever.
    me = asyncio.current_task()
    rest = [t for t in asyncio.all_tasks() if t is not me and not t.done()]
    for task in rest:
        task.cancel()
    if rest:
        _, pending = await asyncio.wait(rest, timeout=cfg.SHUTDOWN_TASKS_S)
        if pending:
            for task in pending:
                coro = task.get_coro()
                say(f"task {task.get_name()} "
                    f"{getattr(coro, '__qualname__', coro)!s} did not end when "
                    f"cancelled")
            say(f"exiting {time.monotonic() - begun:.1f}s into the stop with "
                f"{len(pending)} task(s) that would not end")
            hard_exit(0)
