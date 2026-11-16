"""Echo: the smallest server plugin (Loam's example; see ../PLUGIN.md)."""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", "8000"))
DATA = Path(os.environ.get("LOAM_PLUGIN_DATA", "."))
HITS = DATA / "hits.json"


def count() -> int:
    try:
        n = json.loads(HITS.read_text(encoding="utf-8")).get("hits", 0) + 1
    except (OSError, ValueError):
        n = 1
    HITS.write_text(json.dumps({"hits": n}) + "\n", encoding="utf-8")
    return n


class Echo(BaseHTTPRequestHandler):
    def answer(self) -> None:
        if self.path.split("?")[0] == "/crash":
            print("echo: asked to crash, exiting with status 3", flush=True)
            os._exit(3)
        body = json.dumps({
            "plugin": os.environ.get("LOAM_PLUGIN_ID"),
            "method": self.command,
            "path": self.path,
            "prefix": self.headers.get("X-Forwarded-Prefix"),
            "port": PORT,
            "pid": os.getpid(),
            "hits": count(),
        }, indent=2).encode() + b"\n"
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = answer

    def log_message(self, fmt, *args):
        print("echo: " + fmt % args, flush=True)


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    print(f"echo: listening on 127.0.0.1:{PORT} (pid {os.getpid()})", flush=True)
    sys.stdout.flush()
    ThreadingHTTPServer(("127.0.0.1", PORT), Echo).serve_forever()
