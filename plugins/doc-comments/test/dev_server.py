#!/usr/bin/env python3
"""The editor alone, for working on it and for the browser tests: the real
service over an in-memory copy of the broker's file contract, and the
editor mounted standalone (test/harness.html), the way step 2's share page
will mount it. Nothing here touches a real vault.

Run: python3 plugins/doc-comments/test/dev_server.py [port]   (default 8790;
0 picks a free one, and the line it prints names it)
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
from doccomments.door import Door  # noqa: E402
from doccomments.links import Links  # noqa: E402
from doccomments.service import Service  # noqa: E402
import tempfile  # noqa: E402
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


# A long doc with everything that is drawn as a block (a callout, a table, a
# code block, pictures), for checking that what is placed beside a line sits
# beside it all the way down.
LONG_SECTIONS = []
for i in range(1, 7):
    LONG_SECTIONS.append(f"""## Section {i}

Paragraph {i}a. The river kept its own time, and the town learned to keep it too; every spring the water came up to the third step of the chapel and every summer it went back down, leaving a line of silt that the children traced with sticks.

> [!note] A callout in section {i}
> The note in the callout runs to two lines so that its height is not one line's.
> A second line of the callout.

| Year | Where | Why |
| --- | --- | --- |
| 201{i} | Lisbon | the light |
| 202{i} | Porto | the river |
| 202{i} | Braga | the quiet |

```python
def section_{i}():
    # a code block of a few lines
    return "{i}"
```

![[picture-{i}.png]]

Paragraph {i}b, after the blocks. Nothing here is remarkable except where it sits: below a callout, a table, a code block and a picture, which is where a note used to drift above its words.

Paragraph {i}c closes the section with a sentence long enough to wrap onto a second line at the desktop width of the column.
""")
LONG = "---\ncreated_by: \"[[Sam]]\"\n---\n\n# A long doc\n\n" + "\n".join(LONG_SECTIONS)
FRESH = "# A doc nobody has commented on\n\nIt has two short paragraphs.\n\nThis is the second.\n"


def seeded() -> Service:
    store = MemoryStore({"vault:Essays/drafts/On staying.md": ESSAY,
                         "vault:Essays/drafts/Long.md": LONG,
                         "vault:Essays/drafts/Fresh.md": FRESH})
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
    lp = ("vault", "Essays/drafts/Long.md")
    for i, who in ((1, ada), (3, owner), (6, ada)):
        svc.add(who, *lp, {"quote": f"Paragraph {i}b, after the blocks", "kind": "comment",
                           "text": f"A note on paragraph {i}b."})
    svc.add(owner, *lp, {"quote": "Paragraph 4a", "kind": "suggestion", "replacement": "Paragraph four a",
                         "text": ""})
    svc.add(ada, *lp, {"quote": "Paragraph 6c closes the section", "kind": "comment",
                       "text": "The last note, at the bottom."})
    return svc


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
    svc = seeded()
    links = Links(tempfile.mkdtemp(prefix="dc-dev-links-"))
    door = Door(svc, links, PLUGIN, reach=lambda: "everywhere", owner_name=lambda: "Sam", sleep=lambda s: None)
    did = svc.get(svc.store.owner(), "vault", "Essays/drafts/On staying.md")["id"]
    for mode, name in (("comment", "Robin"), ("edit", None)):
        link, token, pw = links.create(did, mode, name, 30, "pass-" + mode)
        print(f"{mode} link: http://127.0.0.1:{port}/s/{token}  password: pass-{mode}", flush=True)
    api = server_main.make_handler(svc, links, door)

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

        def _share(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            hdrs = {k.lower(): v for k, v in self.headers.items()}
            ans = door.handle(method, "/door/" + self.path[3:], hdrs, body, "127.0.0.1")
            headers = [(k, v.replace("; Secure", "")) for k, v in ans.headers]
            self._raw(ans.status, ans.body, ans.ctype, headers)

        def _api(self, method):
            prefix = "/x/doc-comments"
            self.path = self.path[len(prefix):] or "/"
            self._handle(method)

        def do_GET(self):
            if self.path.startswith("/s/"):
                return self._share("GET")
            if self.path.startswith("/dev/file?"):
                # A test's look at what is stored: the doc and its sidecar.
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                key = f"{q.get('root', ['vault'])[0]}:{q['path'][0]}"
                text = svc.store.files.get(key)
                did = None
                if text:
                    from doccomments import frontmatter as fm
                    did = fm.doc_id(text)
                side = svc.store.files.get(f"vault:{svc.store.sidecar_path(did)}") if did else None
                data = json.dumps({"text": text, "sidecar": json.loads(side) if side else None}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if self.path.startswith("/x/doc-comments/"):
                return self._api("GET")
            return self._static()

        def do_POST(self):
            if self.path.startswith("/s/"):
                return self._share("POST")
            if self.path == "/dev/outside":
                # A stand-in for an edit made on disk by someone else.
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                svc.store.outside_edit(body.get("root", "vault"), body["path"], body["text"])
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._api("POST")

        def do_PATCH(self):
            return self._api("PATCH")

        def do_DELETE(self):
            return self._api("DELETE")

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"doc-comments dev: http://127.0.0.1:{srv.server_address[1]}/", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
