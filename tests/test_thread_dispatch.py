#!/usr/bin/env python3
"""Kill the thread worker on either side of dispatch and after reply append.

The session host and its stream-json child are real processes throughout.
The private host socket carries the dispatch, the live reply and journal replay.
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


async def worker(mode, thread_id, session_id, path):
    from session_socket_reader import BoundReader
    from loam_thread_engine import ThreadEngine
    import loam_threads as threads

    class Remote(BoundReader):
        async def dispatch(self, key, text, attachments=None, **options):
            if mode == "before-write":
                os._exit(80)
            return await super().dispatch(key, text, attachments, **options)

    session = Remote(path, session_id)
    await session.connect()

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

    send = session.send_message

    async def hold_ack(*args, **kwargs):
        seq = await send(*args, **kwargs)
        await release_ack.wait()
        return seq

    session.send_message = hold_ack

    async def until(predicate):
        async with asyncio.timeout(15):
            while not predicate():
                await asyncio.sleep(.01)

    from loam_session_socket import listen
    os.chdir(folder)
    path = Path("private") / "host.sock"
    endpoint = listen(host, path)
    await endpoint.__aenter__()
    workers = []
    try:
        async def launch(mode):
            proc = await asyncio.create_subprocess_exec(
                sys.executable, __file__, "worker", mode, thread_id, session.id,
                str(path))
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
        crash = await launch("after-append")
        await until(lambda: len(session.subscribers) == 1)
        (folder / "finish").touch()
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
        await endpoint.__aexit__(None, None, None)
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
