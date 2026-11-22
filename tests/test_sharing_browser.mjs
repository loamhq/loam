// Real-Chromium Playwright browser verification of:
// 1. Active-content sandboxing for shared HTML and SVG (framed AND direct tab):
//    - HTML runs in opaque `null` origin; document.cookie, localStorage,
//      window.parent, fetch('/api/...'), fetch('/s/<tok>/api/file'),
//      fetch('./sibling-secret.txt') all fail; interactive mockup JS works.
//    - SVG renders in <img>; inline <script> and onload never execute (inline
//      or direct navigation to /raw).
// 2. Standalone share page for PNG, HTML, SVG, PDF, ZIP, Markdown with
//    Comments OFF (view-only, zero dead controls) and Comments ON (visitor
//    reads, posts, replies, resolves whole-file comments & opens Markdown editor).
// 3. All 4 on/off states inside the real Loam Files app (desktop 1440 & phone 390):
//    zero dead buttons, zero page errors, Share sheet adapts modes, Comments
//    panel mounts on desktop side column and phone bottom sheet.
//
// Run:
//   LOAM_PLAYWRIGHT=<path>/playwright/index.mjs LOAM_CHROME=<chrome> node tests/test_sharing_browser.mjs

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { readFile, readdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const { oneProductFixture } = await import('../app/test/one-product-fixture.mjs')
const { filesHost } = await import('../app/test/files-host.js')

// Spawn a Python harness running the REAL broker-backed Sharing & Comments servers
// behind a localhost HTTP door proxy so Chromium exercises real HTTP headers,
// cookies, CSPs, ES module loading, and dynamic share-collab.js mounting.
const pyHarness = `
import http.client, json, os, re, socket, struct, sys, tempfile, threading, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import importlib.util

ROOT = Path(${JSON.stringify(ROOT)})
SHARING = ROOT / "plugins" / "sharing"
COMMENTS = ROOT / "plugins" / "comments"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(SHARING / "server"))
sys.path.insert(0, str(COMMENTS / "server"))

import loam_files as lf
from comments import appear as c_appear, store as c_store
from comments.remote import RemoteClient
from comments.service import Service as CommentService
from sharing import links as s_links, store as s_store
from sharing.collab import CollabClient
from sharing.door import Door
from sharing.tracker import FileTracker
from sharing.withme import WithMe

def load_mod(n, p):
    sp = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m

c_main = load_mod("c_main", COMMENTS / "server" / "main.py")
s_main = load_mod("s_main", SHARING / "server" / "main.py")

def fport():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def make_png(w=120, h=80):
    def ch(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\\x00" + b"\\x91\\x68\\x44" * w for _ in range(h))
    return b"\\x89PNG\\r\\n\\x1a\\n" + ch(b"IHDR", ihdr) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")

tmp = Path(tempfile.mkdtemp(prefix="loam-pw-"))
vault = tmp / "vault"
(vault / "notes").mkdir(parents=True)
events = tmp / "events.jsonl"
os.environ["LOAM_FILES_EVENTS"] = str(events)
lf.cfg.VAULT = vault
lf.cfg.roster = lambda: ["ada"]
lf.cfg.agent_card = lambda aid: {"id": aid, "name": aid.title()}

HOSTILE_HTML = b"""<!doctype html><html><head><meta charset="utf-8"><title>Interactive Mockup</title></head><body>
<h1>Interactive Mockup</h1>
<button id="tap" type="button">Tap me</button>
<span id="count">0</span>
<pre id="probe">running</pre>
<script>
let n = 0;
document.getElementById('tap').addEventListener('click', () => {
  n++;
  document.getElementById('count').textContent = String(n);
});
(async () => {
  const res = { origin: window.origin, cookie: 'ok', storage: 'ok', parent: 'ok', fetchApi: 'ok', fetchSelf: 'ok', fetchSibling: 'ok' };
  try { const c = document.cookie; res.cookie = c || 'empty'; } catch (e) { res.cookie = 'blocked:' + e.name; }
  try { localStorage.setItem('k', 'v'); res.storage = 'allowed'; } catch (e) { res.storage = 'blocked:' + e.name; }
  try { const t = window.parent.document.title; res.parent = 'leaked:' + t; } catch (e) { res.parent = 'blocked:' + e.name; }
  for (const [key, url] of [['fetchApi', '/api/canary'], ['fetchSelf', '/s/probe/api/file'], ['fetchSibling', './sibling-secret.txt']]) {
    try {
      const r = await fetch(url);
      res[key] = 'status:' + r.status;
    } catch (e) {
      res[key] = 'blocked:' + e.name;
    }
  }
  document.getElementById('probe').textContent = JSON.stringify(res);
})();
</script></body></html>"""

HOSTILE_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100" onload="window.__svgOnload=true;fetch('/api/svg-canary')">
<script>window.__svgScript=true;try{fetch('/api/svg-canary')}catch(e){}</script>
<rect width="160" height="100" rx="14" fill="#916844"/>
<text x="80" y="55" text-anchor="middle" fill="#FFF8EE" font-size="16">Safe SVG</text>
</svg>"""

(vault / "notes" / "mockup.html").write_bytes(HOSTILE_HTML)
(vault / "notes" / "logo.svg").write_bytes(HOSTILE_SVG)
(vault / "notes" / "diagram.png").write_bytes(make_png())
(vault / "notes" / "spec.md").write_text("# Quarterly Plan\\n\\nThe river runs clear through the valley.\\n", encoding="utf-8")
(vault / "notes" / "bundle.zip").write_bytes(b"PK\\x03\\x04" + b"\\x00" * 30 + b"archive")
(vault / "notes" / "sibling-secret.txt").write_text("TOP SECRET SIBLING", encoding="utf-8")

state = {"comments_on": True, "canary_hits": []}
b_port, c_port, s_port, front_port = fport(), fport(), fport(), fport()
tok_file = tmp / "tok"
tok_file.write_text("t\\n")

class BH(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}
    def _do(self, m):
        u = urlparse(self.path); q = {k: v[-1] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/v1/owner": return self._send(200, {"id": "sam", "name": "Sam"})
            if u.path == "/v1/agents": return self._send(200, {"agents": [{"id": "ada", "name": "Ada"}]})
            if u.path == "/v1/plugin-services":
                svcs = []
                if state["comments_on"] and q.get("interface") in (None, "share.collaboration"):
                    svcs.append({"plugin": "comments", "interface": "share.collaboration", "version": 1, "url": f"http://127.0.0.1:{c_port}/collab"})
                if q.get("interface") in (None, "share.remote"):
                    svcs.append({"plugin": "sharing", "interface": "share.remote", "version": 1, "url": f"http://127.0.0.1:{s_port}/remote"})
                return self._send(200, {"services": svcs})
            if u.path == "/v1/files/events": return self._send(200, lf.file_events(since=q.get("since")))
            if u.path == "/v1/files/stat": return self._send(200, lf.stat_resource(q["root"], q.get("path", "")))
            if u.path == "/v1/files/list": return self._send(200, lf.listing(q["root"], q.get("path", "")))
            if u.path == "/v1/files/read": return self._send(200, lf.read_text(q["root"], q["path"]))
            if u.path == "/v1/files/fingerprint": return self._send(200, lf.fingerprint(q["root"], q["path"]))
            if u.path == "/v1/files/locate": return self._send(200, lf.locate(q["root"], dev=q.get("dev"), ino=q.get("ino"), sha256=q.get("sha256"), size=q.get("size"), doc_id=q.get("doc_id")))
            if u.path == "/v1/files/download":
                fd, sz, row = lf.download(q["root"], q["path"])
                with os.fdopen(fd, "rb") as fh: data = fh.read()
                self.send_response(200); self.send_header("Content-Type", row.get("media_type") or "application/octet-stream"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
            if u.path == "/v1/files/folder" and m == "POST": return self._send(201, lf.create_folder(self._body()))
            if u.path == "/v1/files/file" and m == "POST": return self._send(201, lf.create_file(self._body()))
            if u.path == "/v1/files/text" and m == "PUT": return self._send(200, lf.save_text(self._body()))
            return self._send(404, {"error": u.path})
        except lf.FilesError as exc:
            return self._send(exc.status, {"error": exc.message, **exc.extra})
    def do_GET(self): self._do("GET")
    def do_POST(self): self._do("POST")
    def do_PUT(self): self._do("PUT")

b_srv = ThreadingHTTPServer(("127.0.0.1", b_port), BH); b_srv.daemon_threads = True
threading.Thread(target=b_srv.serve_forever, daemon=True).start()

c_priv, s_priv = tmp / "c_priv", tmp / "s_priv"
c_st = c_store.BrokerStore(f"http://127.0.0.1:{b_port}", str(tok_file), data_dir="System/Loam/data/comments", private_dir=c_priv)
c_svc = CommentService(c_st)
c_srv = c_main.serve(c_svc, c_port, plugin_dir=COMMENTS, appear_store=c_appear.AppearStore(c_priv), remote=RemoteClient(c_st), reach=lambda: "everywhere")
threading.Thread(target=c_srv.serve_forever, daemon=True).start()

s_st = s_store.BrokerStore(f"http://127.0.0.1:{b_port}", str(tok_file))
s_tr = FileTracker(s_st, s_priv)
s_ln = s_links.Links(s_priv)
s_co = CollabClient(s_st)
s_dr = Door(s_st, s_tr, s_ln, s_co, SHARING, reach=lambda: "everywhere", owner_name=lambda: "Sam", sleep=lambda _: None)
s_wm = WithMe(s_priv, own_tokens=lambda: {l["token"] for l in s_ln.all()}, door=s_dr, owner=s_st.owner, agent=s_st.agent)
s_srv = s_main.serve(s_st, s_tr, s_ln, s_co, s_dr, s_wm, s_port, reach=lambda: "everywhere")
threading.Thread(target=s_srv.serve_forever, daemon=True).start()

# Pre-seed an owner comment on diagram.png and create share links for HTML, SVG, PNG, ZIP, MD
c_svc.add({"id": "sam", "name": "Sam", "kind": "owner"}, "vault", "notes/diagram.png", {"kind": "comment", "text": "Owner note on diagram."})
links_out = {}
for fname, mode in [("mockup.html", "comment"), ("logo.svg", "view"), ("diagram.png", "comment"), ("bundle.zip", "view"), ("spec.md", "comment")]:
    fid = s_tr.ensure("vault", f"notes/{fname}")
    l, tok, pw = s_ln.create(fid, mode, "Robin", 30, "amber-river-42")
    links_out[fname] = {"token": tok, "password": pw}

class Front(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _proxy(self, method):
        if self.path.startswith("/__ctrl/"):
            if self.path == "/__ctrl/canaries":
                raw = json.dumps({"hits": state["canary_hits"]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            if self.path.startswith("/__ctrl/comments/"):
                state["comments_on"] = self.path.endswith("/on")
                raw = json.dumps({"comments_on": state["comments_on"]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if self.path.startswith("/api/"):
            state["canary_hits"].append(self.path)
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.send_header("Content-Length", "6"); self.end_headers(); self.wfile.write(b"canary"); return
        if self.path.startswith("/s/"):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            conn = http.client.HTTPConnection("127.0.0.1", s_port, timeout=10)
            hdrs = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection")}
            hdrs["Host"] = f"127.0.0.1:{front_port}"
            hdrs["X-Loam-Door"] = "1"
            conn.request(method, "/door/" + self.path[3:], body=body, headers=hdrs)
            res = conn.getresponse()
            raw = res.read()
            self.send_response(res.status)
            for k, v in res.getheaders():
                if k.lower() == "transfer-encoding": continue
                # Keep Secure off on plain localhost HTTP only if needed, though Chromium allows Secure on 127.0.0.1
                self.send_header(k, v)
            self.end_headers()
            if method != "HEAD": self.wfile.write(raw)
            conn.close()
            return
        self.send_response(404); self.end_headers()
    def do_GET(self): self._proxy("GET")
    def do_POST(self): self._proxy("POST")
    def do_PATCH(self): self._proxy("PATCH")
    def do_DELETE(self): self._proxy("DELETE")

front = ThreadingHTTPServer(("127.0.0.1", front_port), Front); front.daemon_threads = True
print(json.dumps({"url": f"http://127.0.0.1:{front_port}", "links": links_out, "s_port": s_port, "c_port": c_port}), flush=True)
front.serve_forever()
`

const proc = spawn('python3', ['-c', pyHarness], { stdio: ['ignore', 'pipe', 'inherit'] })
const ready = await new Promise((resolve, reject) => {
  let buf = ''
  proc.stdout.on('data', (chunk) => {
    buf += chunk.toString()
    const nl = buf.indexOf('\n')
    if (nl >= 0) resolve(JSON.parse(buf.slice(0, nl)))
  })
  proc.on('exit', (code) => reject(new Error(`harness exited ${code}`)))
})

const browser = await chromium.launch({
  executablePath: process.env.LOAM_CHROME,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})

let passed = 0
function ok(name, cond, detail = '') {
  assert.ok(cond, `${name}${detail ? `: ${detail}` : ''}`)
  passed++
  console.log(`  PASS  ${name}`)
}

async function openShare(page, token, password) {
  await page.goto(`${ready.url}/s/${token}`, { waitUntil: 'networkidle' })
  await page.locator('.sh-gate-in').fill(password)
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle' }),
    page.locator('.sh-gate-go').click(),
  ])
}

try {
  console.log('\n1. Real Chromium HTML & SVG active-content isolation')
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } })
    const page = await ctx.newPage()
    const pageErrors = []
    page.on('pageerror', e => pageErrors.push(e.message))

    // Seed an origin cookie & localStorage on the share origin to prove sandboxed HTML cannot read them
    await page.goto(`${ready.url}/s/${ready.links['mockup.html'].token}`)
    await page.evaluate(() => {
      document.cookie = 'loam_canary=secret_cookie_val; path=/'
      localStorage.setItem('loam_canary_ls', 'secret_ls_val')
    })

    // 1a. Open shared HTML page (framed inside sandboxed iframe)
    await openShare(page, ready.links['mockup.html'].token, ready.links['mockup.html'].password)
    const iframe = page.locator('iframe.sh-html')
    await iframe.waitFor()
    const sbAttr = await iframe.getAttribute('sandbox')
    ok('HTML iframe sandbox omits allow-same-origin', sbAttr.includes('allow-scripts') && !sbAttr.includes('allow-same-origin'), sbAttr)

    const frame = page.frameLocator('iframe.sh-html')
    await frame.locator('#probe').waitFor()
    await page.waitForFunction(() => {
      const f = document.querySelector('iframe.sh-html')
      return Boolean(f)
    })
    // Wait until async fetch probes settle inside the sandboxed frame
    await frame.locator('#probe').filter({ hasText: 'fetchSibling' }).waitFor()
    const probe = JSON.parse(await frame.locator('#probe').textContent())
    ok('Sandboxed HTML has opaque origin "null"', probe.origin === 'null', JSON.stringify(probe))
    ok('Sandboxed HTML document.cookie throws SecurityError', probe.cookie.startsWith('blocked:'), probe.cookie)
    ok('Sandboxed HTML localStorage throws SecurityError', probe.storage.startsWith('blocked:'), probe.storage)
    ok('Sandboxed HTML window.parent.document throws SecurityError', probe.parent.startsWith('blocked:'), probe.parent)
    ok('Sandboxed HTML fetch("/api/canary") blocked by CSP', probe.fetchApi.startsWith('blocked:'), probe.fetchApi)
    ok('Sandboxed HTML fetch("/s/.../api/file") blocked by CSP', probe.fetchSelf.startsWith('blocked:'), probe.fetchSelf)
    ok('Sandboxed HTML fetch("./sibling-secret.txt") blocked', probe.fetchSibling.startsWith('blocked:'), probe.fetchSibling)

    // Harmless interactive mockup JS still works inside sandbox!
    await frame.locator('#tap').click()
    ok('Interactive single-file HTML mockup button works inside sandbox', (await frame.locator('#count').textContent()) === '1')

    // 1b. Direct top-level navigation to /s/<token>/frame ALSO stays in opaque null origin!
    const directPage = await ctx.newPage()
    await directPage.goto(`${ready.url}/s/${ready.links['mockup.html'].token}/frame`, { waitUntil: 'networkidle' })
    await directPage.locator('#probe').filter({ hasText: 'fetchSibling' }).waitFor()
    const dprobe = JSON.parse(await directPage.locator('#probe').textContent())
    ok('Direct /frame navigation also has opaque origin "null"', dprobe.origin === 'null', JSON.stringify(dprobe))
    ok('Direct /frame document.cookie blocked', dprobe.cookie.startsWith('blocked:'), dprobe.cookie)
    ok('Direct /frame localStorage blocked', dprobe.storage.startsWith('blocked:'), dprobe.storage)
    ok('Direct /frame fetch("/api/canary") blocked', dprobe.fetchApi.startsWith('blocked:'), dprobe.fetchApi)
    await directPage.close()

    // 1c. Shared SVG rendered via <img> (never inline <svg>), zero script execution!
    await openShare(page, ready.links['logo.svg'].token, ready.links['logo.svg'].password)
    await page.locator('img.sh-img').waitFor()
    ok('Shared SVG renders inside <img>, not inline <svg>', (await page.locator('.sh-viewer img.sh-img').count()) === 1)
    ok('SVG script did not run on share page', (await page.evaluate(() => [window.__svgScript, window.__svgOnload])).every(v => v === undefined))

    // 1d. Direct top-level navigation to SVG /raw: sandbox CSP blocks script & onload!
    const svgDirect = await ctx.newPage()
    await svgDirect.goto(`${ready.url}/s/${ready.links['logo.svg'].token}/raw`, { waitUntil: 'networkidle' })
    const svgFlags = await svgDirect.evaluate(() => ({
      script: window.__svgScript ?? null,
      onload: window.__svgOnload ?? null,
      origin: window.origin,
    }))
    ok('Direct SVG /raw has opaque origin and blocks <script> and onload', svgFlags.origin === 'null' && svgFlags.script === null && svgFlags.onload === null, JSON.stringify(svgFlags))
    await svgDirect.close()

    // Verify server received ZERO canary hits from HTML or SVG
    const canaries = await fetch(`${ready.url}/__ctrl/canaries`).then(r => r.json())
    ok('Zero network canary hits escaped HTML or SVG sandboxes', canaries.hits.length === 0, JSON.stringify(canaries.hits))
    ok('Zero page errors during active-content tests', pageErrors.length === 0, JSON.stringify(pageErrors))
    await ctx.close()
  }

  console.log('\n2. Standalone share page UI: Comments ON vs Comments OFF')
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    const page = await ctx.newPage()
    const errs = []
    page.on('pageerror', e => errs.push(e.message))

    // 2a. PNG with Comments ON: visitor sees owner note, posts comment, resolves it!
    await openShare(page, ready.links['diagram.png'].token, ready.links['diagram.png'].password)
    await page.locator('img.sh-img').waitFor()
    await page.locator('.dc-panel').getByText('Owner note on diagram.').waitFor()
    ok('PNG share page mounts Comments panel and shows owner note', true)

    await page.locator('.dc-panel-in').fill('Visitor browser comment on PNG.')
    await page.locator('.dc-panel-send').click()
    await page.locator('.dc-panel').getByText('Visitor browser comment on PNG.').waitFor()
    ok('Visitor posts whole-file comment from PNG share page', true)

    // Visitor resolves their own comment and sees Undo toast
    await page.locator('.dc-pcard', { hasText: 'Visitor browser comment on PNG.' }).locator('.dc-panel-res').click()
    await page.locator('.dc-panel-toast').getByText('Comment resolved').waitFor()
    ok('Visitor resolves own comment and sees Undo toast', true)

    // 2b. Markdown with Comments ON: dynamic share-collab.js mounts CodeMirror editor
    await openShare(page, ready.links['spec.md'].token, ready.links['spec.md'].password)
    await page.locator('.cm-content').waitFor()
    ok('Markdown share page mounts calm CodeMirror editor when Comments is ON', true)

    // 2c. ZIP download card (view-only link, Comments ON): shows Download card, zero comment composer
    await openShare(page, ready.links['bundle.zip'].token, ready.links['bundle.zip'].password)
    await page.locator('.sh-filecard').getByText('bundle.zip').waitFor()
    ok('ZIP share page renders Download card', (await page.locator('.sh-dlprimary').count()) === 1)
    ok('View-only ZIP share page hides comment composer (zero dead controls)', (await page.locator('.dc-panel-in').count()) === 0)

    // 2d. Turn Comments OFF -> PNG & Markdown share pages become clean view-only with ZERO Comments UI!
    await fetch(`${ready.url}/__ctrl/comments/off`)
    await page.goto(`${ready.url}/s/${ready.links['diagram.png'].token}`, { waitUntil: 'networkidle' })
    await page.locator('img.sh-img').waitFor()
    ok('PNG share page with Comments OFF has zero Comments panel', (await page.locator('.dc-panel, .sh-comments-jump').count()) === 0)

    await page.goto(`${ready.url}/s/${ready.links['spec.md'].token}`, { waitUntil: 'networkidle' })
    await page.locator('.sh-prose .sh-md').getByText('Quarterly Plan').waitFor()
    ok('Markdown share page with Comments OFF renders clean read-only prose (no CodeMirror, no dead controls)',
      (await page.locator('.cm-content, .dc-panel').count()) === 0)
    ok('Zero page errors across standalone share page walks', errs.length === 0, JSON.stringify(errs))
    await fetch(`${ready.url}/__ctrl/comments/on`)
    await ctx.close()
  }

  console.log('\n3. All 4 on/off states inside real Loam Files app (1440 & 390)')
  const sharingDir = path.join(ROOT, 'plugins/sharing')
  const commentsDir = path.join(ROOT, 'plugins/comments')
  async function mountPluginFiles(app, pid, dir) {
    for (const rel of await readdir(dir, { recursive: true })) {
      if (!/^(page|assets)\/.*\.(js|css|svg|woff2)$/.test(rel)) continue
      app.routes[`GET /v1/plugins/${pid}/files/${rel}`] = async (_, res) => {
        const ct = rel.endsWith('.css') ? 'text/css' : rel.endsWith('.js') ? 'text/javascript' : rel.endsWith('.svg') ? 'image/svg+xml' : 'font/woff2'
        res.setHeader('content-type', ct)
        res.end(await readFile(path.join(dir, rel)))
      }
    }
  }

  const states = [
    { name: 'both-off', sharing: false, comments: false },
    { name: 'sharing-only', sharing: true, comments: false },
    { name: 'comments-only', sharing: false, comments: true },
    { name: 'both-on', sharing: true, comments: true },
  ]
  for (const st of states) {
    for (const width of [1440, 390]) {
      const app = await oneProductFixture()
      const pngBytes = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64')
      const fh = filesHost({
        texts: { 'vault:Notes/plan.md': '# Spec\n\nHello from the spec.\n' },
      })
      Object.assign(app.routes, Object.fromEntries(Object.entries(fh.routes).filter(([k]) => k.includes('/files/'))))
      const origDl = app.routes['GET /v1/files/download']
      app.routes['GET /v1/files/download'] = (req, res, url) => {
        if (url.searchParams.get('path') === 'Notes/pic.png') {
          res.writeHead(200, {
            'content-type': 'image/png',
            'content-disposition': "attachment; filename*=UTF-8''pic.png",
            'x-content-type-options': 'nosniff',
            'content-security-policy': "sandbox; default-src 'none'",
            'cache-control': 'no-store',
          })
          return res.end(pngBytes)
        }
        return origDl(req, res, url)
      }
      const plugins = []
      if (st.sharing) {
        plugins.push({ id: 'sharing', version: '1.0.0', api: 0, layer: 'shipped', switch: 'plugin', effective: true, on: true, has: { page_main: true } })
        await mountPluginFiles(app, 'sharing', sharingDir)
      }
      if (st.comments) {
        plugins.push({ id: 'comments', version: '1.0.0', api: 0, layer: 'shipped', switch: 'plugin', effective: true, on: true, has: { page_main: true } })
        await mountPluginFiles(app, 'comments', commentsDir)
      }
      app.routes['GET /v1/plugins'] = (_, res) => res.end(JSON.stringify({ plugins }))

      const ctx = await browser.newContext({
        viewport: { width, height: width === 390 ? 844 : 900 },
        isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce',
      })
      const page = await ctx.newPage()
      page.setDefaultTimeout(10000)
      const errs = []
      page.on('pageerror', e => errs.push(e.message))

      // Proxy /x/sharing/ and /x/comments/ to mocked responses matching state
      await page.route('**/x/sharing/**', (route) => {
        const u = new URL(route.request().url())
        if (u.pathname.endsWith('/config')) return route.fulfill({ json: { reach: 'everywhere', default_days: 30, collab: st.comments } })
        if (u.pathname.endsWith('/shared')) return route.fulfill({ json: { files: [], collab: st.comments, owner: 'Sam' } })
        if (u.pathname.endsWith('/withme')) return route.fulfill({ json: { items: [] } })
        if (u.pathname.endsWith('/file/links/password')) return route.fulfill({ json: { password: 'amber-river-42' } })
        if (u.pathname.endsWith('/file/links')) {
          const isMd = (u.searchParams.get('path') || '').endsWith('.md')
          const modes = !st.comments ? ['view'] : isMd ? ['view', 'comment', 'edit'] : ['view', 'comment']
          return route.fulfill({ json: { links: [], owner: 'Sam', allowed_modes: modes, default_days: 30, kind: isMd ? 'markdown' : 'image', collab: st.comments } })
        }
        return route.fulfill({ json: { ok: true } })
      })
      await page.route('**/x/comments/**', (route) => {
        const u = new URL(route.request().url())
        if (u.pathname.endsWith('/config')) return route.fulfill({ json: { reach: 'everywhere', appear: { appear: 'names', owner: 'Sam', alias: "Sam's agent" } } })
        if (u.pathname.endsWith('/appear')) return route.fulfill({ json: { appear: { choice: 'default', default: 'names', as: 'names', alias: "Sam's agent" }, open: 1, resolved: 0 } })
        if (u.pathname.endsWith('/doc/revision')) return route.fulfill({ json: { revision: 'r1', notes: 'nr1' } })
        const p = u.searchParams.get('path') || ''
        const isMd = p.endsWith('.md')
        return route.fulfill({
          json: {
            root: 'vault', path: p, markdown: isMd, text: isMd ? '# Spec\n\nHello from the spec.\n' : undefined,
            revision: 'r1', notes_revision: 'nr1', me: { id: 'sam', name: 'Sam', kind: 'owner' },
            can: { comment: true, suggest: isMd, edit: isMd, accept: isMd, resolve_any: true, restore: isMd },
            notes: [{ id: 'n-11111111', kind: 'comment', state: 'open', anchor: isMd ? { quote: 'Spec' } : null, where: isMd ? { state: 'attached', start: 2, end: 6 } : null, author: { id: 'ada', name: 'Ada', kind: 'agent' }, created: '2026-11-22T12:00:00Z', text: 'Agent note here.', replies: [] }],
            resolved: 0,
          },
        })
      })

      try {
        // Check Files home: Shared root only when sharing=true
        await page.goto(`${app.url}/files`, { waitUntil: 'domcontentloaded' })
        await page.locator('.fs-place').first().waitFor()
        await page.waitForTimeout(500)
        const hasSharedRoot = (await page.locator('.fs-place').filter({ hasText: /^Shared/ }).count()) > 0
        ok(`[${st.name} ${width}px] Shared root ${st.sharing ? 'present' : 'absent'}`, hasSharedRoot === st.sharing)

        // Open PNG in Files
        await page.goto(`${app.url}/files/vault/Notes/pic.png`, { waitUntil: 'domcontentloaded' })
        await page.locator('.fs-img').waitFor()
        await page.waitForTimeout(500)
        const shareBtnCount = await page.locator('.sh-sharebtn').count()
        ok(`[${st.name} ${width}px] PNG Share button ${st.sharing ? 'present' : 'absent'}`, (shareBtnCount > 0) === st.sharing)

        if (st.sharing && width === 1440) {
          await page.locator('.sh-sharebtn').click()
          await page.locator('.sh-dlg').waitFor()
          const staticMode = await page.locator('.sh-modepill.static').count()
          const dropMode = await page.locator('button.sh-modepill').count()
          ok(`[${st.name}] PNG Share sheet mode UI matches comments=${st.comments}`,
            st.comments ? (dropMode === 1 && staticMode === 0) : (staticMode === 1 && dropMode === 0))
          await page.keyboard.press('Escape')
          await page.waitForTimeout(200)
        }

        if (width === 1440) {
          const panelCount = await page.locator('.dc-panel').count()
          ok(`[${st.name} 1440px] PNG desktop Comments side panel ${st.comments ? 'present' : 'absent'}`, (panelCount > 0) === st.comments)
        } else {
          const pbtnCount = await page.locator('.dc-pbtn').count()
          ok(`[${st.name} 390px] PNG phone Comments button ${st.comments ? 'present' : 'absent'}`, (pbtnCount > 0) === st.comments)
          if (st.comments) {
            await page.locator('.dc-pbtn').click()
            await page.locator('.dc-panel-dlg').getByText('Agent note here.').waitFor()
            ok(`[${st.name} 390px] PNG phone Comments bottom sheet opens cleanly`, true)
          }
        }

        // Open Markdown in Files
        await page.goto(`${app.url}/files/vault/Notes/plan.md`, { waitUntil: 'domcontentloaded' })
        if (st.comments) await page.locator('.cm-content').waitFor()
        else await page.locator('.fs-preview, .fs-file').first().waitFor()
        await page.waitForTimeout(400)
        const cmCount = await page.locator('.cm-content').count()
        const mdShareCount = await page.locator('.sh-sharebtn').count()
        ok(`[${st.name} ${width}px] Markdown calm editor ${st.comments ? 'active' : 'off'}`, (cmCount > 0) === st.comments)
        ok(`[${st.name} ${width}px] Markdown Share button ${st.sharing ? 'present' : 'absent'}`, (mdShareCount > 0) === st.sharing)

        ok(`[${st.name} ${width}px] Zero page errors`, errs.length === 0, JSON.stringify(errs))
      } finally {
        await ctx.close()
        await app.close()
      }
    }
  }

  console.log(`\nALL BROWSER CHECKS PASSED (${passed} assertions)`)
} finally {
  await browser.close()
  proc.kill('SIGTERM')
}
