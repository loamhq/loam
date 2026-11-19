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
import time
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


def steer_provider():
    folder = Path(os.environ["TMPDIR"])
    def emit(kind, **fields):
        print(json.dumps({"type": kind, **fields}), flush=True)
    count = 0
    for line in sys.stdin:
        event = json.loads(line)
        if event["type"] != "user":
            continue
        text = json.dumps(event["message"]["content"])
        label = next(word for word in ("fourth-input", "job-note", "second-input", "first-input")
                     if word in text)
        with (folder / "steer-reads").open("a") as f:
            print(label, file=f)
        count += 1
        if count == 1:
            emit("system", subtype="init")
        if count == 4:
            while not (folder / "finish-steers").exists():
                time.sleep(.01)
            emit("system", subtype="background_tasks_changed", tasks=[{"task_id": "bg"}])
            emit("assistant", message={"content": [{"type": "text", "text": "First reply"}]})
            emit("result", num_turns=1)
            emit("system", subtype="background_tasks_changed", tasks=[])
            emit("system", subtype="task_notification", task_id="bg", status="completed")
            emit("system", subtype="init")
            emit("assistant", message={"content": [{"type": "text", "text": "Second reply"}]})
            emit("result", num_turns=1)


async def steering(host, cli, folder):
    """The reader goes away before the third steer's acknowledgement."""
    from loam_thread_engine import ThreadEngine, Turn
    import loam_thread_inputs as inputs
    import loam_threads as threads

    cli.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{__file__}' steers\n")
    tid = threads.create("iris", "carried inputs", members=["iris"])["id"]
    key = tid, "iris"
    engine = ThreadEngine(host)
    engine._memory_pins = AsyncMock()
    tasks = []
    engine.steer_soon = lambda key: tasks.append(asyncio.create_task(engine._steer(key)))
    engine.post_person(tid, "first-input")
    async def until(check):
        async with asyncio.timeout(10):
            while not check():
                await asyncio.sleep(.01)
    await until(lambda: key in engine.turn_watch)
    session = engine.turn_sessions[key]
    session._titled = True
    hold = asyncio.Event()
    original = session.steer
    async def delayed(text, *args, **kwargs):
        seq = await original(text, *args, **kwargs)
        if "fourth-input" in text:
            await hold.wait()
        return seq
    session.steer = delayed
    try:
        engine.post_person(tid, "second-input")
        await until(lambda: len(engine.turn_live[key].carried) == 1)
        engine.schedule(Turn(*key, 0, "job:probe", note="job-note", briefed_at=time.time()))
        await until(lambda: len(engine.turn_live[key].carried) == 2)
        engine.post_person(tid, "fourth-input")
        await until(lambda: (folder / "steer-reads").exists() and
                    len((folder / "steer-reads").read_text().splitlines()) == 4)
        entries = inputs.items(*key)
        assert [e["reason"] for e in entries] == ["m1", "m2", "job:probe", "m3"]
        assert entries[-1].get("sent") is None
        for task in [engine.workers[key], *tasks]:
            task.cancel()
        await asyncio.gather(engine.workers[key], *tasks, return_exceptions=True)
        hold.set()
        (folder / "finish-steers").touch()
        await until(lambda: len(session.turns) == 2)
        replacement = ThreadEngine(host)
        assert replacement.resume_inputs()["scheduled"] == 1
        await asyncio.wait_for(replacement.workers[key], 8)
        rows = [m for m in threads.read(tid)[1] if m["author"] == "iris"]
        assert [m["text"] for m in rows] == ["First reply", "Second reply"], rows
        assert rows[0]["steered"] == ["m2", "m3"]
        assert threads.session_for(*key)["through"] == "m3"
        assert inputs.items(*key) == []
        assert (folder / "steer-reads").read_text().splitlines() == [
            "first-input", "second-input", "job-note", "fourth-input"]
        replacement.stop_all_tails()
        print(f"PASS child={session.proc.pid} root/m2/job/m3 in FIFO order, third steer "
              "acknowledgement lost, both background replies filed once, inputs=0")
    finally:
        hold.set()
        await session.stop()


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
        # Output already in flight at the provider when the worker saved
        # its cursor precedes this dispatch. It cannot answer the new input.
        await session.emit({"type": "assistant", "message": {
            "content": [{"type": "text", "text": "another turn's reply"}]}})
        await session.emit({"type": "result", "num_turns": 1})
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
    await steering(host, cli, folder)


if __name__ == "__main__":
    if sys.argv[1:2] == ["provider"]:
        provider()
    elif sys.argv[1:2] == ["steers"]:
        steer_provider()
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
