#!/usr/bin/env python3
"""Kill the thread worker on either side of dispatch and after reply append.

The session host and its stream-json child are real processes throughout.
A test-only socket carries the dispatch; for the final two workers the
entire reply is already available on the real session journal.
"""
import asyncio
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tempfile
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def provider():
    scratch = Path(os.environ["TMPDIR"])
    for line in sys.stdin:
        if json.loads(line)["type"] != "user":
            continue
        with (scratch / "received").open("a") as f:
            print(os.getpid(), file=f)
        print(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "delta": {
                "type": "text_delta", "text": "first "}}}), flush=True)
        import time
        while not (scratch / "finish").exists():
            time.sleep(.01)
        print(json.dumps({"type": "assistant", "message": {
            "content": [{"type": "text", "text": "first second"}]}}), flush=True)
        print(json.dumps({"type": "result", "num_turns": 1}), flush=True)


async def worker(mode, thread_id, session_id, incarnation, port):
    from loam_provider_session import Session
    from loam_thread_engine import ThreadEngine
    import loam_threads as threads

    class Remote(Session):
        def save(self):
            pass  # the host alone owns the session record

        async def dispatch(self, key, text, attachments=None, **options):
            if mode == "before-write":
                os._exit(80)
            reader, writer = await asyncio.open_connection("127.0.0.1", int(port))
            writer.write((json.dumps([key, text, attachments, mode]) + "\n").encode())
            await writer.drain()
            try:
                return int(await reader.readline())
            finally:
                writer.close()

    session = Remote("iris", session_id, provider="claude-code")
    session.incarnation = incarnation
    session.state = "running"

    class Adapter:
        sessions = {session_id: session}

        def load(self, sid):
            assert sid == session_id
            return session

        async def start_session(self, *args, **kwargs):
            assert mode == "before-write", "recovery chose a new provider session"
            return session

    engine = ThreadEngine(Adapter())
    engine._memory_pins = AsyncMock()
    if mode == "after-append":
        def crash(*args, **kwargs):
            assert threads.read(thread_id)[1][-1]["text"] == "first second"
            os._exit(82)
        threads.remember_session = crash
    assert engine.resume_inputs()["scheduled"] == 1
    await engine.workers[thread_id, "iris"]


async def host():
    import loam_broker as cfg
    from loam_session_host import SessionHost
    import loam_thread_inputs as inputs
    import loam_thread_steps as steps
    import loam_threads as threads

    folder = Path(os.environ["TMPDIR"])
    cli = folder / "fixture-cli"
    cli.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{__file__}' provider\n")
    cli.chmod(0o700)
    cfg.CLAUDE = str(cli)
    host = SessionHost()
    session = await host.start_session("iris", provider="claude-code")
    session._titled = True
    pid = session.proc.pid
    thread_id = threads.create("iris", "dispatch probe")["id"]
    message = threads.append(thread_id, threads.OWNER, "answer this once")
    input_id = inputs.add(thread_id, "iris", {"reason": message["id"], "chain": 0})
    release_ack = asyncio.Event()

    async def serve(reader, writer):
        try:
            key, text, attachments, mode = json.loads(await reader.readline())
            assert key == input_id
            seq = await session.dispatch(key, text, attachments)
            if mode == "lose-ack":
                await release_ack.wait()
            writer.write(f"{seq}\n".encode())
            await writer.drain()
        finally:
            writer.close()

    async def until(predicate):
        async with asyncio.timeout(15):
            while not predicate():
                await asyncio.sleep(.01)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = str(server.sockets[0].getsockname()[1])
    workers = []
    try:
        async def launch(mode):
            proc = await asyncio.create_subprocess_exec(
                sys.executable, __file__, "worker", mode, thread_id, session.id,
                session.incarnation, port)
            workers.append(proc)
            return proc

        first = await launch("before-write")
        assert await first.wait() == 80
        intent = inputs.items(thread_id, "iris")[0]["dispatch"]
        assert intent["session_id"] == session.id and intent["through"] == "m1"
        assert not session.replay(intent["since"])
        lost = await launch("lose-ack")
        await until(lambda: any(e["type"] == "stream_event" for e in session.replay(0)))
        lost.kill()
        assert await lost.wait() == -signal.SIGKILL
        assert not inputs.items(thread_id, "iris")[0].get("sent")
        release_ack.set()
        (folder / "finish").touch()
        await until(lambda: any(e["type"] == "result" for e in session.replay(0)))
        crash = await launch("after-append")
        assert await crash.wait() == 82
        assert threads.session_for(thread_id, "iris") is None
        final = await launch("recover")
        assert await final.wait() == 0
        rows = threads.read(thread_id)[1]
        assert [(m["id"], m["text"]) for m in rows] == [
            ("m1", "answer this once"), ("m2", "first second")]
        assert list(steps.read(thread_id)) == ["m2"]
        assert threads.session_for(thread_id, "iris")["through"] == "m1"
        assert inputs.items(thread_id, "iris") == []
        receipts = [e["seq"] for e in session.replay(0) if e.get("dispatch_id") == input_id]
        assert len(receipts) == 1
        assert (folder / "received").read_text().splitlines() == [str(pid)]
        assert session.proc.returncode is None
        print(f"PASS host={os.getpid()} child={pid} workers={[w.pid for w in workers]} "
              f"prompt_seqs={receipts} child_reads=1 reply=m2 'first second' inputs=0")
    finally:
        release_ack.set()
        server.close()
        await server.wait_closed()
        for proc in workers:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        if session.proc.returncode is None:
            session.proc.kill()
            await session.proc.wait()


if __name__ == "__main__":
    if sys.argv[1:2] == ["provider"]:
        provider()
    elif sys.argv[1:2] == ["worker"]:
        asyncio.run(worker(*sys.argv[2:]))
    elif sys.argv[1:2] == ["host"]:
        asyncio.run(asyncio.wait_for(host(), 45))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            suite = runpy.run_path(str(ROOT / "tests/run-all"))
            suite["MACHINE"]["paths"]["home"] = tmp
            env = suite["sealed_env"](Path(tmp))
            subprocess.run([sys.executable, __file__, "host"], env=env, check=True)
