"""Private Unix transport for a bound session. One request per connection.

A subscriber owns its connection. The host owns the dispatch and the CLI.
Dropping a subscriber neither cancels a write nor changes session lifetime.
"""
from __future__ import annotations

import asyncio
import fcntl
from contextlib import asynccontextmanager, suppress
import json
import os
from pathlib import Path
import re
import socket
import stat

from loam_provider_session import BrokerError

VERSION = 1
# The provider reader accepts 16 MiB envelopes. Leave space for JSON framing.
FRAME_LIMIT = 32 * 1024 * 1024
OVERFLOW = object()


async def write_frame(writer, value):
    writer.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
    await writer.drain()


async def read_frame(reader):
    line = await reader.readline()
    if not line:
        raise ConnectionError("the session host connection closed")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("expected an object")
    return value


class Mailbox(asyncio.Queue):
    """Detach a stalled reader before it can hold up a provider's stdout."""
    def __init__(self):
        super().__init__(maxsize=32)
        self.overflow = False

    def put_nowait(self, item):
        if self.overflow:
            return
        if self.full():
            self.overflow = True
            while not self.empty():
                self.get_nowait()
            item = OVERFLOW
        super().put_nowait(item)


def snapshot(session):
    return {"record": session.meta(), "incarnation": session.incarnation,
            "permissions": session.pending_events(), "background": session.background_event()}


async def stream(session, since, writer):
    if not isinstance(since, int) or not 0 <= since <= session.seq:
        raise BrokerError(409, "cursor outside the session journal")
    queue = Mailbox()
    # Snapshot, subscription and cut are taken without a yield. A writer
    # can append while the old journal is being read on another thread.
    session.watch(queue)
    cut = session.seq
    initial = snapshot(session)
    live = session.state == "running"
    try:
        await write_frame(writer, {"attached": initial, "through": cut})
        sequence = since

        async def send(event):
            nonlocal sequence
            seq = event.get("seq")
            if seq is not None:
                if seq != sequence + 1:
                    raise BrokerError(409, f"journal missing sequence {sequence + 1}")
                sequence = seq
            await write_frame(writer, {"event": event})

        if since < cut:
            old = session.iter_replay(since)
            while sequence < cut:
                event = await asyncio.to_thread(next, old, None)
                if event is None or event["seq"] > cut:
                    raise BrokerError(409, f"journal missing sequence {sequence + 1}")
                await send(event)
            old.close()
        await write_frame(writer, {"caught_up": cut})
        while live:
            event = await queue.get()
            if event is OVERFLOW:
                raise BrokerError(503, "reader fell behind; reconnect at its last sequence")
            if event is None:
                break
            await send(event)
            if event.get("type") == "broker.exit":
                break
        await write_frame(writer, {"ended": sequence})
    finally:
        session.unwatch(queue)


class SessionSocket:
    def __init__(self, host):
        self.host = host
        self.connections = set()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.connections.add(task)
        try:
            req = await read_frame(reader)
            if req.get("version") != VERSION:
                raise BrokerError(409, "incompatible session host protocol")
            op = req.get("op")
            if op == "health":
                await write_frame(writer, {"ok": {"version": VERSION, "pid": os.getpid()}})
                return
            sid = req.get("session_id")
            if not isinstance(sid, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", sid):
                raise BrokerError(400, "invalid session id")
            session = self.host.load(sid)
            if op == "watch":
                # A quiet session may never write again. Notice EOF from a
                # dead API immediately so its queue and file descriptor go.
                watching = asyncio.create_task(stream(session, req["since"], writer))
                eof = asyncio.create_task(reader.read(1))
                try:
                    await asyncio.wait((watching, eof), return_when=asyncio.FIRST_COMPLETED)
                    if watching.done():
                        await watching
                finally:
                    for child in (watching, eof):
                        child.cancel()
                    await asyncio.gather(watching, eof, return_exceptions=True)
                return
            if op == "inspect":
                answer = snapshot(session)
            elif op == "dispatch":
                if req.get("incarnation") != session.incarnation:
                    raise BrokerError(409, "session binding changed; inspect its journal")
                key, text = req.get("id"), req.get("text")
                if not isinstance(key, str) or not key or not isinstance(text, str):
                    raise BrokerError(400, "a dispatch id and text are required")
                answer = await session.dispatch(key, text, req.get("attachments"),
                                                steer=bool(req.get("steer")))
            else:
                raise BrokerError(400, "unknown session host operation")
            await write_frame(writer, {"ok": answer})
        except (ConnectionError, BrokenPipeError):
            pass
        except (BrokerError, KeyError, TypeError, ValueError, OSError) as exc:
            with suppress(ConnectionError):
                await write_frame(writer, {"error": str(exc), "status": getattr(exc, "status", 400)})
        finally:
            self.connections.discard(task)
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()


@asynccontextmanager
async def listen(host, path: Path):
    """Serve an already owned registry inside its private runtime directory."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError("session socket needs an owner-only directory")
    endpoint = SessionSocket(host)
    lock = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.is_socket():
            # A hard-killed host leaves its filesystem socket behind. The
            # lock serializes cleanup with every host binding this directory.
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.setblocking(False)
                try:
                    probe.connect(str(path))
                except ConnectionRefusedError:
                    path.unlink()
                else:
                    raise FileExistsError(f"session socket already serving at {path}")
        sock.bind(str(path))
        os.chmod(path, 0o600)
        sock.listen()
        sock.setblocking(False)
        try:
            server = await asyncio.start_unix_server(endpoint.handle, sock=sock, limit=FRAME_LIMIT)
            async with server:
                yield
        finally:
            for task in tuple(endpoint.connections):
                task.cancel()
            await asyncio.gather(*endpoint.connections, return_exceptions=True)
            path.unlink(missing_ok=True)
    finally:
        sock.close()
        os.close(lock)


class HostClient:
    def __init__(self, path: Path):
        self.path = path

    @asynccontextmanager
    async def request(self, op, **fields):
        reader, writer = await asyncio.open_unix_connection(self.path, limit=FRAME_LIMIT)
        try:
            await write_frame(writer, {"version": VERSION, "op": op, **fields})
            yield reader
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()

    @staticmethod
    async def receive(reader):
        frame = await read_frame(reader)
        if "error" in frame:
            raise BrokerError(frame["status"], frame["error"])
        return frame

    async def call(self, op, **fields):
        async with self.request(op, **fields) as reader:
            return (await self.receive(reader))["ok"]

    async def watch(self, session_id, since):
        async with self.request("watch", session_id=session_id, since=since) as reader:
            while True:
                frame = await self.receive(reader)
                yield frame
                if "ended" in frame:
                    return
