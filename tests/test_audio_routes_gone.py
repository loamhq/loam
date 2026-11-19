#!/usr/bin/env python3
"""Loam's core carries no dictation or read-aloud server code (0.2.12).

Until 0.2.12 the app answered `/api/transcribe` and `/api/speech…` and the
broker answered `/v1/dictation-terms`, so a fresh install that had never had
Dictation or Read aloud still carried their server halves, and the plugins
worked only on a Loam that happened to ship those routes. Since Dictation
and Read aloud 1.1.0 each serves its own, through the plugin gateway at
`/x/<id>/`. Checks, each by asking:

  - the app's source has none of their modules, and the real app server
    (`node app/src/server.js`) answers 404 to every route they had, and its
    health read says nothing of speech;
  - the real broker's request handler does not answer `/v1/dictation-terms`;
  - a fresh install (the release's shipped folders, an empty Yours) has
    neither plugin, wants no server for either, and the real gateway
    answers 404 at `/x/dictation/…` and `/x/read-aloud/…`;
  - the listed copies carry each route in their own `server/`.

Run: python3 tests/test_audio_routes_gone.py
"""

from __future__ import annotations

import asyncio
import http.client
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-audio-gone-test-"))
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ["LOAM_HOST_SETTINGS"] = str(TMP / "broker" / "settings.json")
os.environ["LOAM_PLUGINS_DIR"] = str(TMP / "plugins")
os.environ["LOAM_PLUGIN_STATE"] = str(TMP / "state")
os.environ["LOAM_UI_CORE_OPTIONS"] = "none"
SHIPPED = [AGENTS / "app" / "public" / "themes", AGENTS / "app" / "public" / "plugins", AGENTS / "plugins"]
os.environ["LOAM_PLUGIN_SHIPPED"] = ":".join(str(p) for p in SHIPPED)
os.environ.pop("LOAM_PLUGIN_UNITS", None)
sys.path.insert(0, str(AGENTS / "lib"))

import loam_broker as cfg  # noqa: E402
import loam_plugins  # noqa: E402

cfg.memory = lambda: {"available": None, "cgroup_room": None, "reserve": cfg.MEMORY_RESERVE_MB}
AUDIO = ("dictation", "read-aloud")
results: list[bool] = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ask(port: int, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    out = (r.status, r.read())
    c.close()
    return out


try:
    # -- the app ----------------------------------------------------------
    gone = ["speech.js", "transcribe.js", "polish.js", "speakable.js", "document-speech.js",
            "speech-descriptions.js", "spoken-length.js"]
    check("the app's source has none of their modules",
          [n for n in gone if (AGENTS / "app" / "src" / n).exists()], [])
    port = free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOAM_")}
    env.update(APP_PORT=str(port), LOG_LEVEL="off", APP_MEDIA_DIR=str(TMP / "media"))
    app = subprocess.Popen(["node", str(AGENTS / "app" / "src" / "server.js")], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                if ask(port, "GET", "/up")[0] == 200:
                    break
            except OSError:
                time.sleep(0.05)
        same = {"origin": f"http://127.0.0.1:{port}", "sec-fetch-site": "same-origin"}
        idx = "0" * 32
        for method, path, body, kind in (
                ("POST", "/api/transcribe", b"\x1a\x45\xdf\xa3", "audio/webm"),
                ("POST", "/api/speech", b'{"chat_id":"c","agent":"a","mode":"verbatim","text":"Hi."}', "application/json"),
                ("POST", "/api/speech/lookup", b'{"chat_id":"c","agent":"a","mode":"verbatim","text":"Hi."}', "application/json"),
                ("GET", f"/api/speech/{idx}", None, None),
                ("GET", f"/api/speech/{idx}/audio.mp3", None, None),
                ("GET", "/api/dictation-terms", None, None)):
            status, raw = ask(port, method, path, body, {**same, **({"content-type": kind} if kind else {})})
            check(f"the app answers {method} {path} with 404", (status, json.loads(raw).get("error")),
                  (404, f"no such route: {method} {path}"))
        status, raw = ask(port, "GET", "/api/health")
        check("the app's health read says nothing of speech", (status, "speech" in json.loads(raw)), (200, False))
    finally:
        app.terminate()
        app.wait(timeout=10)

    # -- the broker -------------------------------------------------------
    loader = importlib.machinery.SourceFileLoader("session_broker", str(AGENTS / "bin" / "session-broker"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    broker_mod = importlib.util.module_from_spec(spec)
    loader.exec_module(broker_mod)

    class Writer:
        def __init__(self):
            self.chunks = []

        def write(self, data):
            self.chunks.append(data)

        async def drain(self):
            return None

        def close(self):
            return None

    async def broker_ask(target: str):
        keep = (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH)
        cfg.STATE_DIR, cfg.SESSION_DIR = TMP / "broker", TMP / "broker" / "sessions"
        cfg.USAGE_PATH, cfg.TOKEN_PATH = TMP / "broker" / "usage.json", TMP / "broker" / "token"
        try:
            server = broker_mod.Server(broker_mod.Broker())
            reader = asyncio.StreamReader()
            reader.feed_data(f"GET {target} HTTP/1.1\r\nAuthorization: Bearer {server.broker.token}\r\n\r\n".encode())
            reader.feed_eof()
            w = Writer()
            await server.handle(reader, w)
            head, _, payload = b"".join(w.chunks).decode().partition("\r\n\r\n")
            return int(head.split()[1]), payload
        finally:
            cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH = keep

    note = TMP / "vault" / cfg.lc.vault_folder("system") / "Dictation terms.md"
    note.parent.mkdir(parents=True)
    note.write_text("Ada\n")
    status, payload = asyncio.run(broker_ask("/v1/dictation-terms"))
    check("the broker does not answer /v1/dictation-terms, even with a note there",
          (status in (404, 405), "Ada" in payload), (True, False))
    check("and keeps no reader of the note", hasattr(cfg, "dictation_terms"), False)

    # -- a fresh install ----------------------------------------------------
    rows = [r for r in loam_plugins._rows() if r["id"] in AUDIO]
    check("a fresh install has neither plugin, in any layer", rows, [])
    check("and wants no server for either", sorted(set(loam_plugins.wanted_servers()) & set(AUDIO)), [])
    (TMP / "state").mkdir(exist_ok=True)
    (TMP / "state" / "ports.json").write_text(json.dumps({"ports": {}}))
    gloader = importlib.machinery.SourceFileLoader("plugin_gateway", str(AGENTS / "bin" / "plugin-gateway"))
    gspec = importlib.util.spec_from_loader("plugin_gateway", gloader)
    gw = importlib.util.module_from_spec(gspec)
    gloader.exec_module(gw)
    gport = free_port()

    def serve():
        async def main():
            server = await asyncio.start_server(gw.handle, "127.0.0.1", gport, limit=gw.HEAD_MAX)
            async with server:
                await server.serve_forever()
        asyncio.run(main())

    threading.Thread(target=serve, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", gport), 0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    for path in ("/x/dictation/transcribe", "/x/dictation/terms", "/x/read-aloud/status",
                 "/x/read-aloud/speech/lookup"):
        status, raw = ask(gport, "GET", path)
        check(f"the gateway answers {path} with 404: no server", (status, b"has a server here" in raw), (404, True))

    # -- where each lives now ---------------------------------------------
    for pid, routes in (("dictation", ["'/transcribe'", "'/terms'"]),
                        ("read-aloud", ["'/status'", "'/speech'", "'/speech/lookup'", "audio\\.mp3"])):
        src = (AGENTS / "plugins" / "_listed" / pid / "server" / "app.js").read_text()
        check(f"{pid}'s own server has its routes", [r for r in routes if r not in src], [])
        manifest = loam_plugins.read_folder(AGENTS / "plugins" / "_listed" / pid)["manifest"]
        check(f"{pid} declares its server", (manifest["touches"]["server"], manifest["server"]),
              (True, {"run": "node server/main.js"}))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
