#!/usr/bin/env python3
"""The editor alone, for working on it and for the browser tests: the real
service over an in-memory copy of the broker's file contract, and the
editor mounted standalone (test/harness.html), the way step 2's share page
will mount it. Nothing here touches a real vault.

Run: python3 plugins/doc-comments/test/dev_server.py [port]   (default 8790)
"""

import json
import mimetypes
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
sys.path.insert(0, str(PLUGIN / "server"))

import main as server_main  # noqa: E402
from doccomments.service import Service  # noqa: E402
from doccomments.store import MemoryStore  # noqa: E402

ESSAY = """---
created_by: "[[Sam]]"
tags: [essay, draft]
---

# On staying in one place

For ten years I moved every eighteen months, and I told myself it was curiosity. It was closer to a habit of leaving before anything could ask something of me.

The first winter I stayed, nothing happened, and that was the point. The baker learned my order. The man with the grey dog started nodding. These are small things, but small things are most of a life.

## What staying costs

Staying is not free. You trade the clean slate for a record: people remember the version of you that was careless in March, and you have to live next to it.

I think this is what roots are: not comfort, but accountability that accumulates. You can't outrun the person you were on this street, so eventually you stop trying and start becoming someone you'd be glad to be remembered as.

- [ ] Find a better ending
- [x] Cut the café paragraph
- A **bold** claim, an *aside*, `code` and a [[Wiki link]].

> [!note] For later
> The baker deserves a paragraph of her own.

| Year | Where |
| --- | --- |
| 2016 | Lisbon |
| 2019 | Porto |
"""


def seeded() -> Service:
    store = MemoryStore({"vault:Essays/drafts/On staying.md": ESSAY})
    svc = Service(store)
    owner = store.owner()
    ada = store.agent("ada")
    p = ("vault", "Essays/drafts/On staying.md")

    def add(who, quote, **kw):
        return svc.add(who, *p, {"quote": quote, **kw})

    a = add(ada, "It was closer to a habit of leaving before anything could ask something of me.",
            kind="comment", text="This is the essay's real claim, and it arrives in sentence two. Earn it: one concrete leaving first?")
    svc.reply(owner, *p, a["note"], "Agree. Try the Lisbon flat, the one I left with the lease half done.")
    add(ada, "These are small things, but small things are most of a life.", kind="suggestion",
        replacement="Small things, but a life is mostly small things.",
        text="Tighter, and the line lands on “small things”.")
    b = add(owner, "the version of you that was careless in March", kind="comment",
            text="@Ada is “careless” right? I mean something smaller.")
    svc.reply(ada, *p, b["note"], "“Careless” is right if March is the café story; otherwise “unkind”.")
    return svc


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
    svc = seeded()
    api = server_main.make_handler(svc)

    class Handler(api):
        def _static(self):
            path = self.path.split("?")[0]
            if path in ("/", "/harness"):
                f = HERE / "harness.html"
            elif path.startswith("/plugin/"):
                f = (PLUGIN / path[len("/plugin/"):]).resolve()
                if PLUGIN not in f.parents:
                    f = None
            else:
                f = None
            if not f or not f.is_file():
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data = f.read_bytes()
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            if f.suffix == ".js":
                ctype = "text/javascript"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _api(self, method):
            prefix = "/x/doc-comments"
            self.path = self.path[len(prefix):] or "/"
            self._handle(method)

        def do_GET(self):
            if self.path.startswith("/x/doc-comments/"):
                return self._api("GET")
            return self._static()

        def do_POST(self):
            return self._api("POST")

        def do_PATCH(self):
            return self._api("PATCH")

        def do_DELETE(self):
            return self._api("DELETE")

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"doc-comments dev: http://127.0.0.1:{port}/", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
