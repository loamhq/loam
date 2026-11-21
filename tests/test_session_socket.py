#!/usr/bin/env python3
"""Exercise the private journal stream, including a stalled subscriber."""
import asyncio
from contextlib import aclosing
import os
from pathlib import Path
import stat
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import loam_broker as cfg
from loam_provider_session import BrokerError, Session
from loam_session_host import own_session_store, SessionHost
from loam_session_socket import HostClient, listen, read_frame, write_frame


async def check():
    host = SessionHost()
    session = Session("iris")
    session.state = "running"
    host.sessions[session.id] = session
    path = Path("private") / "host.sock"
    async with listen(host, path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        client = HostClient(path)
        assert (await client.call("health"))["pid"] == os.getpid()
        reader, writer = await asyncio.open_unix_connection(path)
        await write_frame(writer, {"version": 0, "op": "health"})
        assert (await read_frame(reader))["status"] == 409
        writer.close()
        await writer.wait_closed()

        await session.emit({"type": "fixture", "text": "before attachment"})
        async with aclosing(client.watch(session.id, 0)) as first:
            hello = await anext(first)
            assert hello["through"] == 1 and hello["attached"]["incarnation"] == session.incarnation
            await session.emit({"type": "fixture", "text": "during replay"})
            assert (await anext(first))["event"]["seq"] == 1
            assert await anext(first) == {"caught_up": 1}
            assert (await anext(first))["event"]["seq"] == 2
            async with aclosing(client.watch(session.id, 2)) as second:
                assert (await anext(second))["through"] == 2
                assert await anext(second) == {"caught_up": 2}
                partial = {"type": "stream_event", "text": "part", "nested": {"raw": [1, 2]}}
                await session.emit(partial)
                assert (await anext(first))["event"] == (await anext(second))["event"] == partial
            async with asyncio.timeout(3):
                while len(session.subscribers) != 1:
                    await asyncio.sleep(.01)
            # No yields while feeding a slow reader. Provider writes continue;
            # this reader must reconnect from the last sequence it received.
            for n in range(80):
                await session.emit({"type": "fixture", "n": n})
            try:
                await anext(first)
                raise AssertionError("slow reader silently skipped an event")
            except BrokerError as exc:
                assert exc.status == 503
        async with aclosing(client.watch(session.id, 3)) as replay:
            assert (await anext(replay))["through"] == 83
            assert [(await anext(replay))["event"]["n"] for _ in range(80)] == list(range(80))
            assert (await anext(replay)) == {"caught_up": 83}

        # Drop the private RPC while its provider is still working. No event
        # enters the session journal, and no seat is held after disconnect.
        started, reaped = asyncio.Event(), asyncio.Event()
        async def private_child(source, prompt):
            assert source.id == session.id and prompt == "private fixture"
            started.set()
            try:
                await asyncio.Future()
            finally:
                reaped.set()
        with patch("loam_private_summary.ask", side_effect=private_child):
            with patch.object(host, "room", return_value={"ok": False}):
                try:
                    await client.call("private_summary", session_id=session.id, prompt="private fixture")
                    raise AssertionError("private turn admitted without memory")
                except BrokerError as exc:
                    assert exc.status == 429 and not started.is_set()
            async with client.request("private_summary", session_id=session.id,
                                      prompt="private fixture"):
                await started.wait()
                assert host.seats_taken() == 1 and session.busy() is None
                assert session.seq == 83
            await reaped.wait()
            assert host.seats_taken() == 0 and session.seq == 83
        print("PASS hidden RPC honors memory, leaves journal and status alone, "
              "cancels on disconnect and releases its reservation")

        written = asyncio.Event()
        finish = asyncio.Event()
        calls = []
        async def send(text, attachments, dispatch_id):
            calls.append(dispatch_id)
            await session.emit({"type": "broker.user_message", "dispatch_id": dispatch_id})
            written.set()
            await finish.wait()
            return session.seq
        session.send_message = send
        args = dict(session_id=session.id, incarnation=session.incarnation, id="one", text="hello")
        async with client.request("dispatch", **args):
            await written.wait()
        second = asyncio.create_task(client.call("dispatch", **args))
        finish.set()
        assert await second == 84 and calls == ["one"]
        try:
            await client.call("dispatch", **{**args, "incarnation": "stale"})
            raise AssertionError("accepted a stale binding")
        except BrokerError as exc:
            assert exc.status == 409 and calls == ["one"]
    assert not path.exists() and not session.subscribers
    print("PASS private socket 0600, version and stale binding refused, replay/live 1..83, "
          "two subscribers, disconnect cleanup, slow reader caught up, one write after lost ACK")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as folder:
        os.chdir(folder)  # short Unix socket path on installs with a long state path
        cfg.SESSION_DIR = Path(folder) / "sessions"
        with own_session_store():
            asyncio.run(asyncio.wait_for(check(), 15))
