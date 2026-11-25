// Real-Chromium browser regression test and before/after evidence capture for
// non-Markdown file comments panel open/close, persistence, auto-open, phone
// sheet, split-view beside chat, Markdown parity, and Files 404 handling.
//
// Run from app/:
//   LOAM_PLAYWRIGHT=<path>/playwright-core/index.mjs \
//   LOAM_CHROME=/usr/bin/chromium-headless-shell \
//   COMMENTS_PANEL_EVIDENCE=<out-dir> node test/file-comments-panel-browser.mjs
//
// Pass COMMENTS_PANEL_BASELINE=<git-archive-app-dir> to capture the pre-fix
// baseline screens from the commit before.
import assert from 'node:assert/strict'
import { mkdir, readFile, readdir } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import zlib from 'node:zlib'

const baseline = process.env.COMMENTS_PANEL_BASELINE || ''
const out = process.env.COMMENTS_PANEL_EVIDENCE || ''
const fixtureModule = baseline
  ? pathToFileURL(join(baseline, 'test/one-product-fixture.mjs'))
  : './one-product-fixture.mjs'
const { oneProductFixture } = await import(fixtureModule)
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const commentsPlugin = new URL('../../plugins/comments/', import.meta.url)

if (out) await mkdir(out, { recursive: true })

function makePng(w, h) {
  const rows = Buffer.alloc((w * 3 + 1) * h)
  for (let y = 0, o = 0; y < h; y++) {
    rows[o++] = 0
    for (let x = 0; x < w; x++) {
      const nx = x / w, ny = y / h
      let c = [238, 232, 222]
      if (nx > 0.08 && nx < 0.92 && ny > 0.1 && ny < 0.9) {
        c = [250, 247, 242]
        if (ny > 0.16 && ny < 0.25 && nx > 0.14 && nx < 0.52) c = [145, 104, 68]
        else if (ny > 0.31 && ny < 0.36 && nx > 0.14 && nx < 0.78) c = [55, 50, 45]
        else if (ny > 0.42 && ny < 0.80 && nx > 0.14 && nx < 0.86) {
          c = ((Math.floor(x / 24) + Math.floor(y / 24)) % 2 === 0) ? [224, 212, 196] : [235, 226, 214]
        }
      }
      rows[o++] = c[0]; rows[o++] = c[1]; rows[o++] = c[2]
    }
  }
  const chunk = (kind, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length)
    const body = Buffer.concat([Buffer.from(kind), data])
    const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32(body))
    return Buffer.concat([len, body, crc])
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(rows)),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

const SAMPLE_PDF = Buffer.from(
  '%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n'
  + '2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n'
  + '3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n'
  + '4 0 obj << /Length 58 >> stream\nBT /F1 20 Tf 72 700 Td (Quarterly Design Review) Tj ET\nendstream endobj\n'
  + '5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n'
  + 'xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000241 00000 n \n0000000349 00000 n \n'
  + 'trailer << /Size 6 /Root 1 0 R >>\nstartxref\n419\n%%EOF\n',
)

const SAMPLE_PNG = makePng(640, 420)

const SAMPLE_HTML = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Workspace Mockup</title>
<style>
  body { margin: 0; font-family: system-ui, sans-serif; background: #f6f3ee; color: #1c1b19; padding: 36px 28px; }
  .wrap { max-width: 720px; margin: 0 auto; }
  .kicker { font-size: 12px; letter-spacing: .1em; text-transform: uppercase; color: #8c6444; font-weight: 700; }
  h1 { font-size: 30px; margin: 10px 0 12px; line-height: 1.15; }
  p { font-size: 16px; line-height: 1.6; color: #4b4741; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; margin-top: 24px; }
  .card { background: #faf8f4; border: 1px solid rgba(28,27,25,.12); border-radius: 16px; padding: 18px; }
  .card h2 { margin: 0 0 6px; font-size: 16px; }
  .card p { margin: 0; font-size: 13.5px; }
</style></head><body>
  <div class="wrap">
    <div class="kicker">Draft artifact</div>
    <h1>A calmer workspace preview</h1>
    <p>This HTML artifact checks how much reading room the preview keeps when the Comments panel is closed, opened beside it, or viewed beside a live chat.</p>
    <div class="grid">
      <div class="card"><h2>Full canvas</h2><p>HTML previews claim the full pane until you ask for Comments.</p></div>
      <div class="card"><h2>One click away</h2><p>Unresolved notes stay visible as a quiet badge in the toolbar.</p></div>
      <div class="card"><h2>Remembers you</h2><p>Your open or closed choice sticks across files and reloads.</p></div>
    </div>
  </div>
</body></html>`

const SAMPLE_MD = `# Release plan

We are shipping the calm file preview update this week.

- Verify HTML, PDF, and image viewers
- Confirm Markdown anchored notes keep working
`

function buildCommentsStore() {
  let seq = 10
  const me = { id: 'owner:you', kind: 'owner', name: 'Sam' }
  const agent = { id: 'scout', kind: 'agent', name: 'Scout' }
  const now = new Date(Date.now() - 4 * 60 * 1000).toISOString()

  const docs = new Map([
    ['vault:Notes/preview.html', {
      id: 'd-html', path: 'Notes/preview.html', text: SAMPLE_HTML, revision: 'r1', notes_revision: 'n1',
      me, can: { comment: true, suggest: false, edit: false, accept: false, resolve_any: true }, notes: [],
    }],
    ['vault:Notes/split.html', {
      id: 'd-split', path: 'Notes/split.html', text: SAMPLE_HTML, revision: 'r1', notes_revision: 'n1',
      me, can: { comment: true, suggest: false, edit: false, accept: false, resolve_any: true }, notes: [],
    }],
    ['vault:Notes/report.pdf', {
      id: 'd-pdf', path: 'Notes/report.pdf', text: '', revision: 'r1', notes_revision: 'n1',
      me, can: { comment: true, suggest: false, edit: false, accept: false, resolve_any: true }, notes: [],
    }],
    ['vault:Notes/mockup.png', {
      id: 'd-png', path: 'Notes/mockup.png', text: '', revision: 'r1', notes_revision: 'n1',
      me, can: { comment: true, suggest: false, edit: false, accept: false, resolve_any: true }, notes: [],
    }],
    ['vault:Notes/reviewed.png', {
      id: 'd-rev', path: 'Notes/reviewed.png', text: '', revision: 'r1', notes_revision: 'n2',
      me, can: { comment: true, suggest: false, edit: false, accept: false, resolve_any: true },
      notes: [
        {
          id: 'n-open-1', kind: 'comment', state: 'open', author: agent, created: now,
          text: 'The card contrast in dark mode looks clean — worth checking the header spacing too.',
          replies: [],
        },
        {
          id: 'n-res-1', kind: 'comment', state: 'resolved', author: me, created: now,
          text: 'Earlier margin check is done.',
          replies: [],
        },
      ],
    }],
    ['vault:Notes/plan.md', {
      id: 'd-md', path: 'Notes/plan.md', text: SAMPLE_MD, revision: 'r1', notes_revision: 'n3',
      me, can: { comment: true, suggest: true, edit: true, accept: true, resolve_any: true },
      notes: (() => {
        const quote = 'calm file preview update'
        const start = SAMPLE_MD.indexOf(quote)
        const end = start + quote.length
        return [{
          id: 'n-md-1', kind: 'comment', state: 'open', author: agent, created: now,
          anchor: { quote, before: 'shipping the ', after: ' this week.' },
          where: { state: 'attached', start, end },
          text: 'Anchored Markdown comment still lives in the margin.',
          replies: [],
        }]
      })(),
    }],
  ])

  const bump = (doc) => { seq += 1; doc.notes_revision = `n${seq}`; return doc }

  return {
    handle(method, url, rawBody) {
      const pathName = url.pathname.replace(/^\/x\/comments\//, '')
      if (pathName === 'config') return { status: 200, json: { reach: 'everywhere', appear: { appear: 'names', alias: "Sam's agent", owner: 'Sam' } } }
      const body = rawBody ? JSON.parse(rawBody) : {}
      const root = url.searchParams.get('root') || body.root || 'vault'
      const path = url.searchParams.get('path') || body.path || ''
      const doc = docs.get(`${root}:${path}`)
      if (!doc) return { status: 404, json: { error: `no doc at ${root}/${path}` } }
      if (pathName === 'doc' && method === 'GET') return { status: 200, json: doc }
      if (pathName === 'doc/revision' && method === 'GET') return { status: 200, json: { revision: doc.revision, notes: doc.notes_revision } }
      if (pathName === 'doc/notes' && method === 'POST') {
        seq += 1
        doc.notes.push({
          id: `n-${seq}`, kind: body.kind || 'comment', state: 'open', author: me,
          created: new Date().toISOString(), text: body.text || '', replies: [],
        })
        return { status: 200, json: bump(doc) }
      }
      const m = /^doc\/notes\/([^/]+)\/(.+)$/.exec(pathName)
      if (m && method === 'POST') {
        const [, id, action] = m
        const note = doc.notes.find(n => n.id === decodeURIComponent(id))
        if (!note) return { status: 404, json: { error: 'note not found' } }
        if (action === 'replies') {
          seq += 1
          note.replies.push({ id: `r-${seq}`, author: me, created: new Date().toISOString(), text: body.text || '' })
        } else if (action === 'resolve') {
          note.state = 'resolved'
        } else if (action === 'reopen') {
          note.state = 'open'
        } else if (action === 'remove') {
          doc.notes = doc.notes.filter(n => n.id !== note.id)
        }
        return { status: 200, json: bump(doc) }
      }
      return { status: 200, json: doc }
    },
  }
}

async function setupFiles(app, mode) {
  const json = (res, value, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
  const modified = '2026-11-25T16:30:00Z'
  const fileRow = (path, media_type, size) => ({
    root: 'vault', path, name: path.split('/').pop(), kind: 'file', media_type, size, modified,
    authority: 'user', capabilities: { read: true, download: true, write: true, create: false, rename: true, delete: true },
  })
  const rows = [
    fileRow('Notes/preview.html', 'text/html', Buffer.byteLength(SAMPLE_HTML)),
    fileRow('Notes/split.html', 'text/html', Buffer.byteLength(SAMPLE_HTML)),
    fileRow('Notes/report.pdf', 'application/pdf', SAMPLE_PDF.length),
    fileRow('Notes/mockup.png', 'image/png', SAMPLE_PNG.length),
    fileRow('Notes/reviewed.png', 'image/png', SAMPLE_PNG.length),
    fileRow('Notes/plan.md', 'text/markdown', Buffer.byteLength(SAMPLE_MD)),
  ]
  const notesFolder = {
    root: 'vault', path: 'Notes', name: 'Notes', kind: 'folder', modified, authority: 'user',
    capabilities: { read: true, download: false, write: false, create: true, rename: true, delete: false },
  }
  const byPath = new Map(rows.map(r => [r.path, r]))
  byPath.set('Notes', notesFolder)

  app.routes['GET /v1/settings/appearance'] = (req, res) => {
    const look = { mode, theme: 'latte', size: 100 }
    json(res, { phone: look, desktop: look })
  }
  app.routes['GET /v1/plugins'] = (req, res) => json(res, {
    plugins: [{ id: 'comments', version: '1.0.0', api: 0, layer: 'shipped', switch: 'plugin', effective: true, on: true, has: { page_main: true } }],
  })
  for (const rel of await readdir(commentsPlugin, { recursive: true })) {
    if (!/^(page|assets)\/.*\.(js|css)$/.test(rel)) continue
    app.routes['GET /v1/plugins/comments/files/' + rel] = async (_, res) => {
      res.setHeader('content-type', rel.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/javascript; charset=utf-8')
      res.end(await readFile(new URL(rel, commentsPlugin)))
    }
  }
  app.routes['GET /v1/files/config'] = (req, res) => json(res, { vault_urls: [] })
  app.routes['GET /v1/files/roots'] = (req, res) => json(res, {
    roots: [
      { root: 'vault', name: 'Your files', description: 'Your notes and files.', capabilities: { read: true, create: true } },
      { root: 'spaces', name: 'Agent files', description: 'Agent workspaces.', capabilities: { read: true, create: false } },
    ],
    shortcuts: [],
    loam_files: [],
    workspace_names: { scout: 'Scout' },
  })
  app.routes['GET /v1/files/recent'] = (req, res) => json(res, { who: 'all', window: 'default', days: [], total_shown: 0, total_week: 0 })
  app.routes['GET /v1/files/link'] = (req, res, url) => json(res, {
    url: `/files/${url.searchParams.get('root')}/${url.searchParams.get('path')}`, kind: 'Loam link',
  })
  app.routes['GET /v1/files/stat'] = (req, res, url) => {
    const root = url.searchParams.get('root')
    const p = url.searchParams.get('path') || ''
    if (root !== 'vault') return json(res, { error: `no root called '${root}'; GET /v1/files/roots names them` }, 404)
    if (p === '') return json(res, { root: 'vault', path: '', name: 'Your files', kind: 'folder', capabilities: { read: true, create: true } })
    const hit = byPath.get(p)
    if (!hit) return json(res, { error: `nothing at ${root}/${p}` }, 404)
    return json(res, hit)
  }
  app.routes['GET /v1/files/list'] = (req, res, url) => {
    const root = url.searchParams.get('root')
    const p = url.searchParams.get('path') || ''
    if (root !== 'vault') return json(res, { error: `no root called '${root}'; GET /v1/files/roots names them` }, 404)
    if (p === '') return json(res, { folder: { root: 'vault', path: '', name: 'Your files', kind: 'folder', capabilities: { read: true, create: true } }, entries: [notesFolder], next_cursor: null })
    if (p === 'Notes') return json(res, { folder: notesFolder, entries: rows, next_cursor: null })
    return json(res, { error: `nothing at ${root}/${p}` }, 404)
  }
  app.routes['GET /v1/files/read'] = (req, res, url) => {
    const p = url.searchParams.get('path') || ''
    const hit = byPath.get(p)
    if (!hit) return json(res, { error: `nothing at vault/${p}` }, 404)
    if (p === 'Notes/preview.html' || p === 'Notes/split.html') return json(res, { ...hit, text: SAMPLE_HTML, revision: 'r1' })
    if (p === 'Notes/plan.md') return json(res, { ...hit, text: SAMPLE_MD, revision: 'r1' })
    return json(res, { error: 'not UTF-8 text' }, 415)
  }
  app.routes['GET /v1/files/download'] = (req, res, url) => {
    const p = url.searchParams.get('path') || ''
    if (p === 'Notes/preview.html' || p === 'Notes/split.html') { res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }); return res.end(SAMPLE_HTML) }
    if (p === 'Notes/report.pdf') { res.writeHead(200, { 'content-type': 'application/pdf' }); return res.end(SAMPLE_PDF) }
    if (p === 'Notes/mockup.png' || p === 'Notes/reviewed.png') { res.writeHead(200, { 'content-type': 'image/png' }); return res.end(SAMPLE_PNG) }
    return json(res, { error: `nothing at vault/${p}` }, 404)
  }
}

const browser = await chromium.launch({
  executablePath: process.env.LOAM_CHROME,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})

const prefix = baseline ? 'before' : 'after'
const checks = []
const ok = (label, cond, detail = '') => {
  if (!cond) throw new Error(`FAIL: ${label}${detail ? ` (${detail})` : ''}`)
  checks.push(label)
}

try {
  for (const width of [1440, 390]) {
    for (const mode of ['light', 'dark']) {
      const app = await oneProductFixture()
      await setupFiles(app, mode)
      const comments = buildCommentsStore()
      const context = await browser.newContext({
        viewport: { width, height: width === 390 ? 844 : 900 },
        colorScheme: mode,
        reducedMotion: 'reduce',
        isMobile: width === 390,
        hasTouch: width === 390,
      })
      const page = await context.newPage()
      page.setDefaultTimeout(10000)
      const errors = []
      page.on('pageerror', err => errors.push(err.message))
      page.on('dialog', d => { errors.push(`dialog: ${d.message()}`); d.dismiss() })

      await page.route('**/x/comments/**', route => {
        const req = route.request()
        const res = comments.handle(req.method(), new URL(req.url()), req.postData())
        return route.fulfill({ status: res.status, contentType: 'application/json', body: JSON.stringify(res.json) })
      })

      const shot = async (name) => {
        if (!out) return
        await page.evaluate(async () => {
          await document.fonts.ready
          await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))
        })
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
        assert.equal(overflow, false, `${name} (${width} ${mode}) has horizontal overflow`)
        await page.screenshot({ path: join(out, `${prefix}-${name}-${width}-${mode}.png`), animations: 'disabled' })
      }

      try {
        // --- 1. HTML file with NO comments ---------------------------------
        await page.goto(`${app.url}/files/vault/Notes/preview.html`, { waitUntil: 'domcontentloaded' })
        await page.locator('iframe.fs-iframe').waitFor()
        await page.waitForFunction(() => document.documentElement.dataset.theme === (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'))
        await page.waitForTimeout(350)
        await shot('html-empty-initial')

        if (!baseline) {
          const toggle = page.locator('.fs-comments-btn').first()
          await toggle.waitFor()
          if (width === 1440) {
            const panelW = await page.locator('.fs-side-panel').evaluate(n => n.getBoundingClientRect().width)
            ok(`[${width} ${mode}] HTML with 0 comments starts closed`, panelW < 2, `width=${panelW}`)
            ok(`[${width} ${mode}] HTML toggle shows aria-pressed=false`, (await toggle.getAttribute('aria-pressed')) === 'false')
          } else {
            ok(`[${width} ${mode}] Phone HTML sheet starts closed`, (await page.locator('.fs-panel-sheet-wrap.open').count()) === 0)
          }

          // Open via Comments control -> focuses composer because 0 comments!
          await toggle.click()
          if (width === 1440) {
            await page.locator('.fs-side-panel.open').waitFor()
          } else {
            await page.locator('.fs-panel-sheet-wrap.open').waitFor()
          }
          await page.waitForTimeout(250)
          const focusedComposer = await page.evaluate(() => document.activeElement?.classList?.contains('dc-panel-in'))
          ok(`[${width} ${mode}] Opening empty Comments panel focuses composer`, focusedComposer)
          const hintHidden = await page.locator('.dc-panel-hint').evaluate(n => getComputedStyle(n).display === 'none')
          ok(`[${width} ${mode}] Whole-file comment hint hidden`, hintHidden)
          const emptyWarm = await page.locator('.fs-panel-empty .empty-state-title').textContent()
          ok(`[${width} ${mode}] Warm empty state shown`, emptyWarm === 'No comments yet', emptyWarm)
          const sendOpacity = await page.locator('.dc-panel-send').evaluate(n => Number(getComputedStyle(n).opacity))
          ok(`[${width} ${mode}] Resting Comment button is not washed out`, sendOpacity >= 0.95, `opacity=${sendOpacity}`)
          await shot('html-empty-open')

          // Add a comment -> count updates to 1!
          await page.locator('.dc-panel-in').fill('Looks much cleaner with full preview width.')
          await page.locator('.dc-panel-send').click()
          await page.locator('.dc-pcard').getByText('Looks much cleaner with full preview width.').waitFor()
          await page.locator('.fs-comments-badge').getByText('1', { exact: true }).waitFor()
          ok(`[${width} ${mode}] Adding comment updates toolbar badge to 1`, true)

          // Close with ×
          await page.locator('.fs-panel-close').first().click()
          await page.waitForTimeout(260)
          if (width === 1440) {
            const closedW = await page.locator('.fs-side-panel').evaluate(n => n.getBoundingClientRect().width)
            ok(`[${width} ${mode}] Close (×) hides desktop panel`, closedW < 2, `width=${closedW}`)
          } else {
            ok(`[${width} ${mode}] Close (×) dismisses phone sheet`, (await page.locator('.fs-panel-sheet-wrap.open').count()) === 0)
          }

          // Re-open with 1 comment -> must NOT focus composer!
          await toggle.click()
          await page.waitForTimeout(260)
          const focusedComposerWithNotes = await page.evaluate(() => document.activeElement?.classList?.contains('dc-panel-in'))
          ok(`[${width} ${mode}] Opening panel with comments does NOT focus composer`, !focusedComposerWithNotes)

          // Close with Escape when focus is inside the panel
          await page.keyboard.press('Escape')
          await page.waitForTimeout(260)
          if (width === 1440) {
            const escW = await page.locator('.fs-side-panel').evaluate(n => n.getBoundingClientRect().width)
            ok(`[${width} ${mode}] Escape inside panel closes it`, escW < 2, `width=${escW}`)
          } else {
            ok(`[${width} ${mode}] Escape closes phone sheet`, (await page.locator('.fs-panel-sheet-wrap.open').count()) === 0)
          }

          // Reload -> remembers closed even though file now has 1 comment!
          await page.reload({ waitUntil: 'domcontentloaded' })
          await page.locator('iframe.fs-iframe').waitFor()
          await page.locator('.fs-comments-badge').getByText('1', { exact: true }).waitFor()
          await page.waitForTimeout(250)
          if (width === 1440) {
            const reloadClosedW = await page.locator('.fs-side-panel').evaluate(n => n.getBoundingClientRect().width)
            ok(`[${width} ${mode}] Reload remembers closed choice`, reloadClosedW < 2, `width=${reloadClosedW}`)
            // Open again and reload -> remembers open choice too!
            await page.locator('.fs-comments-btn').first().click()
            await page.locator('.fs-side-panel.open').waitFor()
            await page.reload({ waitUntil: 'domcontentloaded' })
            await page.locator('iframe.fs-iframe').waitFor()
            await page.locator('.fs-side-panel.open').waitFor()
            ok(`[${width} ${mode}] Reload remembers open choice`, true)
          }
        }

        // --- 2. PDF and Image files with NO comments -----------------------
        await page.evaluate(() => localStorage.removeItem('loam-ui.file-panel'))
        for (const [label, filePath, readySel] of [
          ['pdf-empty', 'Notes/report.pdf', 'iframe.fs-pdfframe'],
          ['image-empty', 'Notes/mockup.png', 'img.fs-img'],
        ]) {
          await page.goto(`${app.url}/files/vault/${filePath}`, { waitUntil: 'domcontentloaded' })
          await page.locator(readySel).waitFor()
          await page.waitForTimeout(300)
          if (!baseline) {
            await page.locator('.fs-comments-btn').first().waitFor()
            if (width === 1440) {
              const w = await page.locator('.fs-side-panel').evaluate(n => n.getBoundingClientRect().width)
              ok(`[${width} ${mode}] ${label} starts closed`, w < 2, `width=${w}`)
            }
          }
          if (label === 'image-empty' || label === 'pdf-empty') await shot(label)
        }

        // --- 3. File with an unresolved comment (auto-open on desktop, reply, resolve) --
        await page.evaluate(() => localStorage.removeItem('loam-ui.file-panel'))
        await page.goto(`${app.url}/files/vault/Notes/reviewed.png`, { waitUntil: 'domcontentloaded' })
        await page.locator('img.fs-img').waitFor()
        await page.waitForTimeout(400)

        if (!baseline) {
          await page.locator('.fs-comments-badge').getByText('1', { exact: true }).waitFor()
          if (width === 1440) {
            await page.locator('.fs-side-panel.open').waitFor()
            await page.locator('.dc-pcard').getByText('The card contrast in dark mode looks clean').waitFor()
            const autoFocused = await page.evaluate(() => document.activeElement?.classList?.contains('dc-panel-in'))
            ok(`[${width} ${mode}] Unresolved comment auto-opens desktop panel without focusing composer`, !autoFocused)
          } else {
            ok(`[${width} ${mode}] Phone shows count badge before opening sheet`, true)
            await page.locator('.fs-comments-btn').first().click()
            await page.locator('.fs-panel-sheet-wrap.open').waitFor()
            await page.locator('.dc-pcard').getByText('The card contrast in dark mode looks clean').waitFor()
          }
          await shot('comments-populated')

          // Reply to the comment
          await page.locator('.dc-panel-replybtn').click()
          await page.locator('.dc-pcard .dc-input').fill('Header spacing checked on both widths.')
          await page.locator('.dc-pcard .dc-send').click()
          await page.locator('.dc-rp').getByText('Header spacing checked on both widths.').waitFor()
          ok(`[${width} ${mode}] Reply appears inside comment card`, true)
          await shot('comments-replied')

          // Resolve the comment -> count drops from 1 to 0, folded behind Resolved (2), warm "All caught up"!
          await page.locator('.dc-panel-res').first().click()
          await page.waitForFunction(() => !document.querySelector('.fs-comments-badge'))
          await page.locator('.fs-panel-empty .empty-state-title').getByText('All caught up').waitFor()
          await page.locator('.dc-panel-tog').getByText('Resolved (2)').waitFor()
          ok(`[${width} ${mode}] Resolve drops badge count and folds into Resolved (2)`, true)
          await shot('comments-resolved-empty')

          // Open Resolved view -> shows resolved cards with Reopen
          await page.locator('.dc-panel-tog').click()
          await page.locator('.dc-pcard.resolved').first().waitFor()
          ok(`[${width} ${mode}] Resolved tab shows folded resolved comments`, (await page.locator('.dc-pcard.resolved').count()) === 2)
        } else {
          await shot('comments-populated')
        }

        // --- 4. Markdown file comments still work as before ----------------
        await page.goto(`${app.url}/files/vault/Notes/plan.md`, { waitUntil: 'domcontentloaded' })
        await page.locator('.cm-content').waitFor()
        await page.waitForTimeout(350)
        if (!baseline) {
          ok(`[${width} ${mode}] Markdown keeps calm editor without duplicate non-MD Comments toggle`,
            (await page.locator('.fs-comments-btn').count()) === 0 && (await page.locator('.cm-content').count()) === 1)
          if (width === 1440) {
            await page.locator('.dc-margin .dc-card').getByText('Anchored Markdown comment still lives in the margin.').waitFor()
            ok(`[${width} ${mode}] Markdown anchored margin comment still renders`, true)
          }
        }

        // --- 5. Split view: Chat beside HTML file (1440 only) --------------
        if (width === 1440) {
          await page.evaluate(() => localStorage.removeItem('loam-ui.file-panel'))
          const sideParam = encodeURIComponent(JSON.stringify({ kind: 'file', root: 'vault', path: 'Notes/split.html', mode: 'preview' }))
          await page.goto(`${app.url}/threads/a-quieter-week?side=${sideParam}`, { waitUntil: 'domcontentloaded' })
          const side = page.locator('[data-panel="side"]')
          await side.locator('iframe.fs-iframe').waitFor()
          await page.waitForTimeout(400)
          const closedIframeW = await side.locator('iframe.fs-iframe').evaluate(n => n.getBoundingClientRect().width)
          const sidePaneW = await side.evaluate(n => n.getBoundingClientRect().width)
          await shot('split-chat-html-closed')

          if (!baseline) {
            ok(`[1440 ${mode}] Split view HTML preview gets full pane width when closed`,
              closedIframeW >= sidePaneW - 4, `iframe=${Math.round(closedIframeW)} pane=${Math.round(sidePaneW)}`)
            const sideToggle = side.locator('.side-head .fs-comments-btn')
            await sideToggle.waitFor()
            await sideToggle.click()
            await side.locator('.fs-side-panel.open').waitFor()
            await page.waitForTimeout(300)
            const openIframeW = await side.locator('iframe.fs-iframe').evaluate(n => n.getBoundingClientRect().width)
            ok(`[1440 ${mode}] Split view Comments drawer slides over without crushing HTML preview`,
              openIframeW >= closedIframeW - 4, `openIframe=${Math.round(openIframeW)} closedIframe=${Math.round(closedIframeW)}`)
            await shot('split-chat-html-open')
            await side.locator('.fs-panel-close').click()
            await page.waitForTimeout(260)
            ok(`[1440 ${mode}] Split view Comments closes cleanly via ×`, (await side.locator('.fs-side-panel.open').count()) === 0)
          }
        }

        // --- 6. Bad-URL 404 (wrong root /files/40_PROJECTS/...) ------------
        await page.goto(`${app.url}/files/40_PROJECTS/missing/page.html`, { waitUntil: 'domcontentloaded' })
        await page.waitForTimeout(450)
        await shot('bad-url-404')
        if (!baseline) {
          await page.locator('.fs-notfound').waitFor()
          const bodyText = await page.locator('.fs-notfound').innerText()
          ok(`[${width} ${mode}] 404 says plainly it is not there and never blames a busy box`,
            /Not found/.test(bodyText) && /isn’t there/.test(bodyText) && !/box is busy/i.test(bodyText), bodyText)
          ok(`[${width} ${mode}] 404 has no red danger banner`, (await page.locator('.banner-retry').count()) === 0)
          await page.locator('.fs-notfound-back').click()
          await page.waitForURL('**/files/vault')
          await page.locator('.fs-name').getByText('Notes', { exact: true }).waitFor()
          ok(`[${width} ${mode}] Back to Your files lands on /files/vault`, true)
        }

        ok(`[${width} ${mode}] Zero uncaught page errors`, errors.length === 0, JSON.stringify(errors))
      } finally {
        await context.close()
        await app.close()
      }
    }
  }
  console.log(`ALL ${prefix.toUpperCase()} BROWSER CHECKS PASSED (${checks.length} assertions)`)
} finally {
  await browser.close()
}
