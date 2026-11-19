#!/usr/bin/env python3
"""Exercise multipart uploads, stored filenames and presentation of attachments to a session."""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
# The thread the uploads go to lives in a vault of the test's own; the
# threads module reads LOAM_VAULT when it is imported, under the broker.
root = Path(tempfile.mkdtemp(prefix="loam-attachments-"))
os.environ["LOAM_VAULT"] = str(root / "vault")
sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg
# Use fixture memory to isolate the broker from machine load.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_clock  # noqa: E402

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


# -- stand-ins --------------------------------------------------------------


class FakeStdin:
    def __init__(self) -> None:
        self.lines: list[dict] = []

    def write(self, data: bytes) -> None:
        self.lines.append(json.loads(data.decode()))

    async def drain(self) -> None:
        return None


class FakeProc:
    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.returncode = None


class FakeWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    def text(self) -> str:
        return b"".join(self.chunks).decode()

    def body(self):
        head, _, payload = self.text().partition("\r\n\r\n")
        return int(head.split()[1]), json.loads(payload)


def running(broker, agent: str = "probe"):
    session = broker.adopt(broker_mod.Session(agent))
    session.state = "running"
    session.proc = FakeProc()
    broker.sessions[session.id] = session
    return session


# A real PNG: one red pixel, 69 bytes, with the 0x1A and the NUL bytes that
# make a naive multipart parser corrupt it. That is the point of using one.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da63f8cfc0000003010100189dd1cd0000000049454e"
    "44ae426082")


def multipart(files, boundary="----loamtest"):
    """`(content_type, body)` for a browser-shaped upload of `files`, each a
    `(field, filename, content_type, bytes)`."""
    out = bytearray()
    for field, filename, ctype, data in files:
        out += f"--{boundary}\r\n".encode()
        out += (f'Content-Disposition: form-data; name="{field}"; '
                f'filename="{filename}"\r\n').encode()
        out += f"Content-Type: {ctype}\r\n\r\n".encode()
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", bytes(out)


async def route(server, method, path, params=None, payload=None,
                raw=b"", content_type=""):
    writer = FakeWriter()
    try:
        await server.route(method, path, params or {}, payload or {}, writer,
                           raw=raw, content_type=content_type)
    except (broker_mod.BrokerError, broker_mod.threads_rec.ThreadError) as exc:
        return exc.status, {"error": exc.message}
    return writer.body()


async def request(server, method, target, body=b"", content_type="",
                  token=None):
    """A whole HTTP request through the real `_handle`, header parsing, cap and
    all. The one way to prove the refusal that happens before the body is
    kept."""
    head = f"{method} {target} HTTP/1.1\r\n"
    head += f"Authorization: Bearer {token or server.broker.token}\r\n"
    if content_type:
        head += f"Content-Type: {content_type}\r\n"
    head += f"Content-Length: {len(body)}\r\n\r\n"
    reader = asyncio.StreamReader()
    reader.feed_data(head.encode() + body)
    reader.feed_eof()
    writer = FakeWriter()
    await server.handle(reader, writer)
    return writer.body()


async def main() -> None:
    broker = broker_mod.Broker()
    server = broker_mod.Server(broker)
    session = running(broker)
    thread = broker_mod.threads_rec.create(None, "Attachments")["id"]
    upload = f"/v1/threads/{thread}/attachments"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day_dir = cfg.ATTACHMENT_ROOT / "loam-ui" / today

    # -- 1. the cap and the allow-list -------------------------------------

    status, body = await request(server, "GET", "/v1/limits")
    check("GET /v1/limits states the cap", (status, body["attachment_max_bytes"]),
          (200, cfg.ATTACHMENT_MAX_BYTES))
    check("...and the image the API will take inline",
          body["inline_image_max_bytes"], cfg.INLINE_IMAGE_MAX_BYTES)

    ctype, big = multipart([("file", "huge.png", "image/png",
                             b"x" * (cfg.ATTACHMENT_MAX_BYTES + 9 * 1024))])
    status, body = await request(server, "POST",
                                 upload,
                                 big, ctype)
    check("a body over the cap is refused on the wire", status, 413)
    check("...in a sentence with the number in it",
          "10.0 MB" in body["error"], True)

    ctype, body_bytes = multipart([("file", "just-over.txt", "text/plain",
                                    b"x" * (cfg.ATTACHMENT_MAX_BYTES + 1))])
    status, body = await route(server, "POST",
                               upload,
                               raw=body_bytes, content_type=ctype)
    check("a file over the cap inside a body under it is refused too",
          status, 413)

    ctype, body_bytes = multipart([("file", "installer.exe",
                                    "application/octet-stream", b"MZ")])
    status, body = await route(server, "POST",
                               upload,
                               raw=body_bytes, content_type=ctype)
    check("a type this interface does not carry is refused", status, 415)
    check("...and nothing was written for it",
          (day_dir / "installer.exe").exists(), False)

    status, body = await route(server, "POST",
                               upload,
                               raw=b'{"file": "please"}',
                               content_type="application/json")
    check("a JSON body to the upload route is refused", status, 415)

    status, body = await route(server, "POST",
                               "/v1/threads/t-nobody/attachments",
                               raw=body_bytes, content_type=ctype)
    check("an upload to a thread nobody has is a 404", status, 404)

    # -- 2, 3. the sanitising, the path, the bytes --------------------------

    ctype, body_bytes = multipart([
        ("file", r"..\..\etc\Board notes (final).md", "text/markdown",
         b"# Board notes\n\nThe wall held.\n"),
        ("file", "shot.png", "", PNG),
    ])
    status, body = await route(server, "POST",
                               upload,
                               raw=body_bytes, content_type=ctype)
    check("an upload of two files answers 201", status, 201)
    stored = body["attachments"]
    check("...one entry per file", len(stored), 2)
    check("a name is a name and not a path", stored[0]["name"],
          "Board-notes--final-.md")
    check("...stored under the app and the day", stored[0]["path"],
          str(day_dir / "Board-notes--final-.md"))
    check("...with its size", stored[0]["size"], 30)
    check("...and the type read off the extension, not the browser's guess",
          stored[0]["type"], "text/markdown")
    check("a part with no content-type of its own is still typed",
          stored[1]["type"], "image/png")
    check("the bytes on disk are the bytes that were sent",
          Path(stored[1]["path"]).read_bytes(), PNG)
    check("a dotfile cannot be written",
          cfg.sanitise_name(".bashrc"), "bashrc")

    ctype, body_bytes = multipart([("file", "shot.png", "image/png", PNG)])
    status, again = await route(server, "POST",
                                upload,
                                raw=body_bytes, content_type=ctype)
    check("the same name twice lands beside the first, not on it",
          again["attachments"][0]["name"], "shot-1.png")

    # -- 4, 5. the message ---------------------------------------------------

    carried = [server._attachment(p) for p in (stored[1]["path"],
                                               stored[0]["path"])]
    await session.send_message("attachment check", carried)
    sent = session.proc.stdin.lines[-1]["message"]["content"]
    check("...and the CLI gets content blocks, not a string",
          isinstance(sent, list), True)
    check("...the words first", sent[0]["type"], "text")
    check("...naming every file by its vault path",
          [line for line in sent[0]["text"].splitlines()
           if line.startswith("- ")],
          [f"- {stored[1]['path']} (image/png, 69 B)"
           " -- the image itself is attached below",
           f"- {stored[0]['path']} (text/markdown, 30 B)"])
    check("...then the image itself, as a base64 block",
          [b["type"] for b in sent[1:]], ["image"])
    check("...in the shape the CLI's input takes",
          sent[1]["source"]["type"], "base64")
    check("...with the media type the store decided",
          sent[1]["source"]["media_type"], "image/png")
    import base64 as b64
    check("...and the image's own bytes",
          b64.b64decode(sent[1]["source"]["data"]), PNG)

    event = [e for e in session.replay(0)
             if e["type"] == "broker.user_message"][-1]
    check("broker.user_message carries the attachments",
          [a["name"] for a in event["attachments"]],
          ["shot.png", "Board-notes--final-.md"])
    check("...and the text a person actually typed", event["text"],
          "attachment check")

    await session.send_message("and nothing attached")
    sent = session.proc.stdin.lines[-1]["message"]["content"]
    # Skip the injected clock to compare attachment text.
    check("a message with nothing attached is still a plain string",
          (isinstance(sent, str), sent.startswith("<loam-clock>"),
           loam_clock.strip(sent)), (True, True, "and nothing attached"))

    # An image over the API's ceiling is named, not base64'd: the persona can
    # still Read it, and the turn does not carry megabytes it would refuse.
    big_png = day_dir / "poster.png"
    big_png.write_bytes(PNG + b"\0" * (cfg.INLINE_IMAGE_MAX_BYTES + 1))
    await session.send_message("look", [server._attachment(str(big_png))])
    sent = session.proc.stdin.lines[-1]["message"]["content"]
    check("an image over the inline ceiling is named and not shown",
          [b["type"] for b in sent], ["text"])
    check("...and said to be too big, rather than silently dropped",
          "too big to show inline" in sent[0]["text"], True)

    # -- 6. a path the broker did not write ---------------------------------

    for bad, want in ((str(cfg.AGENTS_ROOT / "state" / "broker" / "token"), 400),
                      ("../../etc/hostname", 400),
                      ("/etc/hostname", 400),
                      (str(day_dir / "never-uploaded.txt"), 404),
                      (42, 400)):
        try:
            server._attachment(bad)
            status = 200
        except broker_mod.BrokerError as exc:
            status = exc.status
        check(f"a message cannot name {bad!r}", status, want)

    status, body = await route(server, "POST", f"/v1/threads/{thread}/messages",
                               payload={"text": "hi", "attachments": "one"})
    check("`attachments` must be a list", status, 400)


saved = {name: getattr(cfg, name) for name in
         ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "USAGE_PATH", "AI_LABS",
          "VAULT", "ATTACHMENT_ROOT")}
try:
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.USAGE_PATH = cfg.STATE_DIR / "provider-usage.json"
    cfg.AI_LABS = root / "agents"
    cfg.VAULT = root / "vault"
    cfg.ATTACHMENT_ROOT = cfg.VAULT / "98_ATTACHMENTS"
    (cfg.AI_LABS / "probe").mkdir(parents=True)
    (cfg.AI_LABS / "probe" / "agent.json").write_text("{}", encoding="utf-8")
    asyncio.run(main())
finally:
    for name, value in saved.items():
        setattr(cfg, name, value)
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
