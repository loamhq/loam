#!/usr/bin/env python3
"""Keep original tool-result images in the journal while bounding the event sent to clients."""

import asyncio
import copy
import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
# Use fixture memory to isolate the broker from machine load.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


SID = "05d262de-4c2f-4159-8094-766bb79b1b44"
PAGE = "/9j/4AAQSkZJRgABAQEAZABk"

PDF_READ = {
    "type": "user",
    "message": {"role": "user", "content": [{
        "tool_use_id": "toolu_01USotRcvmrwnWNhFvbxwgqa",
        "type": "tool_result",
        "content": [
            {"type": "text", "text": "PDF pages extracted: 20 page(s) from "
             "/home/loam/vault/30_LIBRARY/Articles/"
             "llm_eval_course_notes (56).pdf (4.8MB)"},
            {"type": "image", "source": {"type": "base64", "data": PAGE,
                                         "media_type": "image/jpeg"}},
            {"type": "image", "source": {"type": "base64", "data": PAGE,
                                         "media_type": "image/jpeg"}},
        ]}]},
    "parent_tool_use_id": None,
    "session_id": SID,
    "uuid": "7679451f-51e0-4376-9f31-5a0487b4d286",
    "timestamp": "2026-10-15T15:56:10.579Z",
    "tool_use_result": {
        "type": "parts",
        "file": {"filePath": "/home/loam/vault/30_LIBRARY/Articles/"
                             "llm_eval_course_notes (56).pdf",
                 "originalSize": 5047584, "count": 20},
        "firstPage": 1},
    "seq": 3,
}


class FakeWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None

    def items(self) -> list[dict]:
        text = b"".join(self.chunks).decode()
        return [json.loads(line[len("data: "):])
                for line in text.splitlines() if line.startswith("data: ")]


def pages_of(event: dict) -> list:
    return [b for b in event["message"]["content"][0]["content"]
            if b["type"] == "image"]


async def main() -> None:
    # -- 1. the cut --------------------------------------------------------
    given = copy.deepcopy(PDF_READ)
    wire = broker_mod.for_the_wire(given)
    check("each page keeps its block and its media type, and loses its data",
          [b["source"] for b in pages_of(wire)],
          [{"type": "base64", "media_type": "image/jpeg",
            "omitted_chars": len(PAGE)}] * 2)
    check("...the result's own words are still there",
          wire["message"]["content"][0]["content"][0]["text"][:21],
          "PDF pages extracted: ")
    check("...and so is everything around the message",
          {k: v for k, v in wire.items() if k != "message"},
          {k: v for k, v in PDF_READ.items() if k != "message"})

    # -- 2. nothing else moves ---------------------------------------------
    check("the event it was handed is unchanged", given, PDF_READ)
    said = {"type": "assistant", "seq": 4, "message": {"content": [
        {"type": "text", "text": "I've finished the course reader."}]}}
    plain = {"type": "user", "seq": 5, "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}}
    check("an event with nothing to cut is the same object",
          [broker_mod.for_the_wire(e) is e for e in (said, plain)],
          [True, True])
    bare = {"type": "user", "seq": 6, "message": {"content": [
        {"type": "image", "source": {"type": "base64", "data": PAGE,
                                     "media_type": "image/png"}}]}}
    check("an image straight in a user message is cut the same way",
          broker_mod.for_the_wire(bare)["message"]["content"][0]["source"],
          {"type": "base64", "media_type": "image/png",
           "omitted_chars": len(PAGE)})

    # Use a recorded image-result shape to check wire encoding.
    photo = {"type": "user", "seq": 7, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_x", "content": [
            {"type": "image", "source": {"type": "base64", "data": PAGE,
                                         "media_type": "image/jpeg"}}]}]},
        "tool_use_result": {"type": "image", "file": {
            "base64": PAGE, "type": "image/jpeg", "originalSize": 5928169,
            "dimensions": {"originalWidth": 1440, "originalHeight": 1920}}}}
    held = copy.deepcopy(photo)
    sent = broker_mod.for_the_wire(photo)
    check("a photograph's second copy goes, and what it was stays",
          sent["tool_use_result"]["file"],
          {"type": "image/jpeg", "originalSize": 5928169,
           "dimensions": {"originalWidth": 1440, "originalHeight": 1920},
           "base64_omitted_chars": len(PAGE)})
    check("...and its first copy with it",
          [b["source"].get("data") for b in pages_of(sent)], [None])
    check("...and the logged event is as it was", photo, held)

    # -- 3. the SSE writer, and the log behind it ---------------------------
    events = [
        {"type": "broker.user_message", "session_id": SID,
         "text": "do a little research", "seq": 1},
        {"type": "system", "subtype": "init", "session_id": SID, "seq": 2},
        PDF_READ, said,
        {"type": "result", "subtype": "success", "is_error": False,
         "seq": 5},
        {"type": "broker.exit", "session_id": SID, "exit_code": 0,
         "seq": 6},
    ]
    log = cfg.SESSION_DIR / f"{SID}.jsonl"
    log.write_text("".join(json.dumps(e) + "\n" for e in events),
                   encoding="utf-8")
    (cfg.SESSION_DIR / f"{SID}.json").write_text(json.dumps({
        "session_id": SID, "agent": "ada", "state": "exited",
        "workspace": "/home/loam/ai-labs/ada",
        "created": "2026-10-15T15:55:38Z",
        "last_activity": "2026-10-15T16:02:51Z",
        "last_event_at": "2026-10-15T16:02:51Z",
        "status": "done", "title": "Acme evals", "last_message": "x",
        "pinned": False, "archived": False, "record_seq": 6, "turns": [],
        "pending_permissions": [],
    }), encoding="utf-8")
    before = log.read_bytes()

    broker = broker_mod.Broker()
    server = broker_mod.Server(broker)
    writer = FakeWriter()
    for event in broker.load(SID).replay(0):
        await server._send_event(writer, event)
    items = writer.items()
    sent = [i for i in items if i.get("seq") == 3]
    check("the SSE writer sends the read with no page bytes",
          [b["source"].get("data") for e in sent for b in pages_of(e)],
          [None, None])
    check("...and the reply after it, whole",
          [i["message"]["content"][0]["text"] for i in items
           if i.get("seq") == 4],
          ["I've finished the course reader."])
    check("the log on disk is byte-for-byte what it was",
          log.read_bytes() == before, True)
    check("...pictures and all",
          [b["source"]["data"] for b in pages_of(
              json.loads(log.read_text().splitlines()[2]))], [PAGE, PAGE])


root = Path(tempfile.mkdtemp(prefix="loam-broker-wire-images-test-"))
saved = {name: getattr(cfg, name) for name in
         ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "USAGE_PATH", "AI_LABS")}
try:
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.USAGE_PATH = cfg.STATE_DIR / "provider-usage.json"
    cfg.SESSION_DIR.mkdir(parents=True)
    cfg.AI_LABS = root / "agents"
    (cfg.AI_LABS / "ada").mkdir(parents=True)
    (cfg.AI_LABS / "ada" / "agent.json").write_text(
        '{"display": "Ada", "emoji": "\\ud83e\\uddf1"}', encoding="utf-8")

    asyncio.run(main())
finally:
    for name, value in saved.items():
        setattr(cfg, name, value)
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
