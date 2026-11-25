// Real Chromium check of unified file links and file headers across Files main,
// the side panel, chat messages, and the mounted Markdown editor plugin.
// Run: LOAM_PLAYWRIGHT=<playwright index.mjs> LOAM_CHROME=<chromium> node test/files-links-headers-browser.mjs
// Set FILES_LINKS_EVIDENCE=<dir> to save frames; FILES_LINKS_BASELINE=1 runs against the commit before.
import assert from 'node:assert/strict'
import { mkdir, readFile, readdir } from 'node:fs/promises'
import { join } from 'node:path'
import { deflateSync } from 'node:zlib'
import { filesHost } from './files-host.js'
import { row, readAnswer } from './fixtures/files-contract.js'
import { oneProductFixture } from './one-product-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const output = process.env.FILES_LINKS_EVIDENCE
const baseline = process.env.FILES_LINKS_BASELINE === '1'
const previewOrigin = 'https://preview.example.com'
const external = 'https://example.org/reading-list'
const threadPath = '/threads/a-quieter-week'
const htmlPath = '/files/vault/Notes/page.html'
const planPath = '/files/vault/Notes/plan.md'
const otherPath = '/files/vault/Notes/other.md'
const imagePath = '/files/vault/Notes/pic.png'
const pdfPath = '/files/vault/Notes/report.pdf'
const audioPath = '/files/vault/Notes/clip.mp3'
const textPath = '/files/vault/Notes/note.txt'
const binPath = '/files/vault/Notes/archive.bin'

const plan = `# The weekly plan

Review the [[other#Focus|companion note]] before Friday, or open the [Friday review mockup](${htmlPath}).

Save the [reading list](${external}) for later.

## Next steps

- Give the first draft another read.
- Bring the [reference image](${imagePath}) and the [one-page brief](${pdfPath}).`

const other = `# Companion note

Keep the open questions short so the review has room to breathe.

## Focus

- Pick one change to try first.
- Write down what surprised us.`

const mockup = `<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: light dark; background: light-dark(#f3f4eb,#252b24); color: light-dark(#2d382d,#e6eadc); }
  body { margin: clamp(24px,6vw,68px); font: 16px/1.6 system-ui; }
  p { max-width: 36ch; } h1 { font: 400 clamp(36px,6vw,60px)/1.02 Georgia; margin: 18px 0; }
  small { letter-spacing: .12em; text-transform: uppercase; } li { margin-block: 8px; }
</style>
<small>Studio planning</small><h1>Friday review</h1><p>Set aside an hour to look back on the week and choose where to spend our attention next.</p>
<ul><li>Read the short first draft.</li><li>Bring one open question.</li><li>Leave the afternoon free.</li></ul>
<script>document.body.dataset.ready = 'true'</script>`

const headerPluginSource = `export function activate(loam) {
  const H = loam.ui.h
  loam.ui.styles?.add?.('assets/sharing.css')
  const share = loam.places.files.action.add({
    id: 'fixture.share',
    slot: 'action',
    when: c => Boolean((c.resource || c.file)?.root === 'vault'),
    render: () => H('button', { type: 'button', class: 'sh-sharebtn secondary' }, H('span', { text: 'Share' })),
  })
  const comments = loam.places.files.viewer.add({
    id: 'fixture.comments-toggle',
    slot: 'panel',
    when: c => Boolean(c.file && c.file.view !== 'markdown'),
    render: () => ({
      title: 'Comments',
      count: 2,
      node: H('div', { class: 'dc-panel', 'aria-label': 'Comments' },
        H('div', { class: 'dc-panel-empty', text: 'Two open notes on this file.' })),
    }),
  })
  return () => { share.remove(); comments.remove() }
}`

function makePng(w, h, [r, g, b]) {
  const crc = (buf) => {
    let c = 0xffffffff
    for (const byte of buf) { c ^= byte; for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (c & 1 ? 0xedb88320 : 0) }
    return (c ^ 0xffffffff) >>> 0
  }
  const chunk = (type, data) => {
    const t = Buffer.from(type), len = Buffer.alloc(4), tail = Buffer.alloc(4)
    len.writeUInt32BE(data.length, 0)
    tail.writeUInt32BE(crc(Buffer.concat([t, data])), 0)
    return Buffer.concat([len, t, data, tail])
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2
  const rows = Buffer.alloc((w * 3 + 1) * h)
  for (let y = 0; y < h; y++) {
    const off = y * (w * 3 + 1)
    rows[off] = 0
    for (let x = 0; x < w; x++) {
      const shade = Math.round(((x + y) / (w + h)) * 18)
      rows[off + 1 + x * 3] = Math.min(255, r + shade)
      rows[off + 2 + x * 3] = Math.min(255, g + shade)
      rows[off + 3 + x * 3] = Math.min(255, b + shade)
    }
  }
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(rows)),
    chunk('IEND', Buffer.alloc(0)),
  ])
}
const samplePng = makePng(320, 200, [138, 158, 132])

const commentsPluginDir = new URL('../../plugins/comments/', import.meta.url)
const sharingCssPath = new URL('../../plugins/sharing/assets/sharing.css', import.meta.url)

async function mountFixture({ mode, withComments = false }) {
  const app = await oneProductFixture({ FILES_CONTENT_ORIGIN: previewOrigin })
  const texts = {
    'vault:Notes/plan.md': plan,
    'vault:Notes/other.md': other,
    'vault:Notes/note.txt': 'Plain text notes for Friday review.\n',
  }
  const host = filesHost({ texts })
  Object.assign(app.routes, Object.fromEntries(Object.entries(host.routes).filter(([path]) => path.includes('/files/'))))
  const byRef = {
    '[[plan]]': { resource: row('vault', 'Notes/plan.md'), fragment: null },
    '[[plan#Next steps]]': { resource: row('vault', 'Notes/plan.md'), fragment: 'Next steps' },
    '[[other#Focus]]': { resource: row('vault', 'Notes/other.md'), fragment: 'Focus' },
    [planPath]: { resource: row('vault', 'Notes/plan.md'), fragment: null },
    [otherPath]: { resource: row('vault', 'Notes/other.md'), fragment: null },
    [htmlPath]: { resource: row('vault', 'Notes/page.html'), fragment: null },
    [imagePath]: { resource: row('vault', 'Notes/pic.png'), fragment: null },
    [pdfPath]: { resource: row('vault', 'Notes/report.pdf'), fragment: null },
  }
  app.routes['GET /v1/files/stat'] = (req, res, url) => {
    const root = url.searchParams.get('root'), p = url.searchParams.get('path')
    res.end(JSON.stringify(row(root, p)))
  }
  app.routes['GET /v1/files/resolve'] = (req, res, url) => {
    const ref = url.searchParams.get('ref')
    if (ref in byRef) return res.end(JSON.stringify({ status: 'found', ...byRef[ref] }))
    return host.routes['GET /v1/files/resolve'](req, res, url)
  }
  app.routes['GET /v1/files/read'] = (req, res, url) => {
    const root = url.searchParams.get('root'), p = url.searchParams.get('path')
    const key = `${root}:${p}`
    if (key in texts) return res.end(JSON.stringify(readAnswer(row(root, p), texts[key], 'sha256:r1')))
    return host.routes['GET /v1/files/read'](req, res, url)
  }
  const record = await app.f.api('GET', threadPath)
  record.messages.find(message => message.id === 'm4').text =
    `The [[plan#Next steps|weekly plan]] is ready. Open the [Friday review mockup](${htmlPath}) to see how it looks, or save the [reading list](${external}) for later.`
  app.routes[`GET /v1${threadPath}`] = (_, res) => res.end(JSON.stringify(record))
  const appearance = { theme: 'latte', mode, size: 100 }
  app.routes['GET /v1/settings/appearance'] = (_, res) => res.end(JSON.stringify({ phone: appearance, desktop: appearance }))

  const baseDownload = app.routes['GET /v1/files/download']
  app.routes['GET /v1/files/download'] = (req, res, url) => {
    if (url.searchParams.get('path') === 'Notes/pic.png') {
      res.writeHead(200, { 'content-type': 'image/png' })
      return res.end(samplePng)
    }
    return baseDownload(req, res, url)
  }
  const plugins = [
    { id: 'header-fixture', version: '1.0.0', api: 0, layer: 'default', switch: 'plugin', effective: true, on: true, has: { page_main: true } },
  ]
  app.routes['GET /v1/plugins/header-fixture/files/page/main.js'] = (_, res) => {
    res.setHeader('content-type', 'text/javascript')
    res.end(headerPluginSource)
  }
  app.routes['GET /v1/plugins/header-fixture/files/assets/sharing.css'] = async (_, res) => {
    res.setHeader('content-type', 'text/css')
    res.end(await readFile(sharingCssPath))
  }
  if (withComments) {
    plugins.push({ id: 'comments', version: '1.0.0', api: 0, layer: 'default', switch: 'plugin', effective: true, on: true, has: { page_main: true } })
    for (const rel of await readdir(commentsPluginDir, { recursive: true })) {
      if (!/^(page|assets)\/.*\.(js|css)$/.test(rel)) continue
      app.routes['GET /v1/plugins/comments/files/' + rel] = async (_, res) => {
        res.setHeader('content-type', rel.endsWith('.css') ? 'text/css' : 'text/javascript')
        res.end(await readFile(new URL(rel, commentsPluginDir)))
      }
    }
  }
  app.routes['GET /v1/plugins'] = (_, res) => res.end(JSON.stringify({ plugins }))
  return { app, texts }
}

if (output) await mkdir(output, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const wide = width === 1440

    // -- Pass 1: Built-in viewers (Markdown, HTML, Image, PDF, Audio, Text, Other) + chat links --
    {
      const { app } = await mountFixture({ mode, withComments: false })
      const context = await browser.newContext({
        viewport: { width, height: wide ? 900 : 844 },
        colorScheme: mode,
        hasTouch: !wide,
        isMobile: !wide,
        reducedMotion: 'reduce',
      })
      try {
        let previewRequests = 0
        await context.route(`${previewOrigin}/**`, route => {
          previewRequests++
          return route.fulfill({ contentType: 'text/html', body: mockup })
        })
        await context.route(external, route => route.fulfill({ body: 'Reading list' }))
        await context.route(`${app.url}/sw.js`, route => route.abort())

        const page = await context.newPage()
        await page.addInitScript(() => localStorage.setItem('loam-ui.file-panel', 'closed'))
        page.setDefaultTimeout(10000)
        const errors = []
        page.on('pageerror', error => errors.push(error.message))
        const capture = async (name) => {
          if (!output) return
          await page.evaluate(async () => {
            await document.fonts.ready
            await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))
          })
          await page.screenshot({ path: join(output, `${name}-${width}-${mode}.png`), animations: 'disabled' })
        }

        // 1. Built-in Markdown preview in Files main: clean header & chat-identical inline links.
        await page.goto(app.url + planPath)
        const main = wide ? page.locator('[data-panel="main"]') : page.locator('.screen.files')
        const side = page.getByRole('region', { name: 'Side panel' })
        const preview = main.locator('.fs-preview')
        await preview.waitFor()
        const wikiLink = preview.getByRole('link', { name: 'companion note' })
        const webLink = preview.getByRole('link', { name: 'reading list' })
        await wikiLink.waitFor()
        if (wide) {
          const rest = await wikiLink.evaluate(link => {
            const line = document.createRange()
            line.selectNodeContents(link.parentNode)
            return [...line.getClientRects()].map(rect => rect.toJSON())
          })
          await wikiLink.hover()
          if (!baseline) {
            assert.equal(await preview.locator('.filelink-side').count(), 0)
            assert.equal(await wikiLink.getAttribute('title'), null)
            assert.deepEqual(await wikiLink.evaluate(link => {
              const line = document.createRange()
              line.selectNodeContents(link.parentNode)
              return [...line.getClientRects()].map(rect => rect.toJSON())
            }), rest)
            assert.equal(await main.locator('.fs-copylink, .fs-dl').count(), 0)
          }
        }
        await capture('md-main')

        // Plain click/tap wiki-link from Files main.
        if (wide) await wikiLink.click()
        else await wikiLink.tap()
        if (wide && !baseline) {
          await side.locator('.fs-preview').getByRole('heading', { name: 'Focus' }).waitFor()
          assert.equal(new URL(page.url()).pathname, planPath)
          await capture('md-link-opened')
          // Cmd/Ctrl-click opens in main.
          await wikiLink.click({ modifiers: ['Control'] })
          await page.waitForURL(`**${otherPath}**`)
          await main.locator('.fs-preview').getByRole('heading', { name: 'Focus' }).waitFor()
          await page.goBack()
          await preview.getByRole('heading', { name: 'The weekly plan' }).waitFor()
        } else if (!wide) {
          await page.waitForURL(`**${otherPath}**`)
          await main.locator('.fs-preview').getByRole('heading', { name: 'Focus' }).waitFor()
          await capture('md-link-opened')
          await main.getByRole('button', { name: 'Back', exact: true }).click()
          await page.waitForURL(`**${planPath}**`)
          await preview.getByRole('heading', { name: 'The weekly plan' }).waitFor()
        } else {
          await page.waitForURL(`**${otherPath}**`)
          await capture('md-link-opened')
          await page.goBack()
          await preview.getByRole('heading', { name: 'The weekly plan' }).waitFor()
        }

        // Web link from Files main opens in a new tab.
        const [mdWeb] = await Promise.all([context.waitForEvent('page'), webLink.click()])
        await mdWeb.waitForURL(external)
        await mdWeb.close()
        assert.equal(new URL(page.url()).pathname, planPath)

        // 2. HTML file opened in Files main: Share + Comments + ⋯ only; Reload & Open in new tab in ⋯.
        await page.goto(app.url + htmlPath)
        const mainFrame = main.frameLocator('iframe')
        await mainFrame.getByRole('heading', { name: 'Friday review' }).waitFor()
        await mainFrame.locator('body[data-ready=true]').waitFor()
        await capture('html-main')
        if (!baseline && !wide) {
          const titleBox = await main.locator('.fs-htmlname').boundingBox()
          assert.ok(titleBox && titleBox.width >= 48, `phone HTML title has legible room (${titleBox?.width})`)
        }
        const mainDots = main.getByRole('button', { name: 'This file', exact: true })
        await mainDots.click()
        const menu = page.locator('.fs-sheet')
        await menu.waitFor()
        await capture('html-main-menu')
        if (!baseline) {
          assert.equal(await main.locator('.fs-htmlreload, .fs-htmlopen').count(), 0)
          assert.equal(await main.locator('.sh-sharebtn').count(), 1)
          assert.equal(await main.locator('.fs-comments-btn').count(), 1)
          const reload = menu.getByRole('button', { name: 'Reload', exact: true })
          const openTab = menu.getByRole('link', { name: 'Open in new tab', exact: true })
          assert.equal(await openTab.getAttribute('href'), await main.locator('iframe').getAttribute('src'))
          const beforeReq = previewRequests
          await reload.click()
          await menu.waitFor({ state: 'hidden' })
          await mainFrame.getByRole('heading', { name: 'Friday review' }).waitFor()
          assert.equal(previewRequests, beforeReq + 1)
          await mainDots.click()
          const [popup] = await Promise.all([context.waitForEvent('page'), openTab.click()])
          await popup.getByRole('heading', { name: 'Friday review' }).waitFor()
          await popup.close()
          await menu.waitFor({ state: 'hidden' })
          // View source -> ⋯ offers Preview (returning cleanly to live HTML preview).
          await mainDots.click()
          await menu.getByRole('button', { name: 'View source', exact: true }).click()
          await main.locator('.fs-field').waitFor()
          assert.equal(await main.locator('.fs-copylink, .fs-dl').count(), 0)
          await mainDots.click()
          await menu.getByRole('button', { name: 'Preview', exact: true }).click()
          await mainFrame.getByRole('heading', { name: 'Friday review' }).waitFor()
        } else {
          await page.keyboard.press('Escape')
          await menu.waitFor({ state: 'hidden' })
        }

        // 3. Audit every other viewer in main: Image, PDF, Audio, Text, Other.
        for (const [path, waitSel, kind] of [
          [imagePath, '.fs-img', 'image'],
          [pdfPath, '.fs-pdfframe', 'pdf'],
          [audioPath, '.fs-audio', 'audio'],
          [textPath, '.fs-field', 'text'],
          [binPath, '.fs-media', 'other'],
        ]) {
          await page.goto(app.url + path)
          await main.locator(waitSel).waitFor()
          if (kind === 'image') await capture('image-main')
          if (!baseline) {
            assert.equal(await main.locator('.fs-copylink, .fs-dl, .fs-htmlreload, .fs-htmlopen').count(), 0, `${kind} main header clean`)
            assert.equal(await main.locator('.sh-sharebtn').count(), 1, `${kind} main header has Share`)
            assert.equal(await main.locator('.fs-comments-btn').count(), 1, `${kind} main header has Comments toggle`)
            if (kind === 'pdf') {
              await mainDots.click()
              await menu.getByRole('link', { name: 'Open in new tab', exact: true }).waitFor()
              await menu.getByRole('link', { name: 'Download', exact: true }).waitFor()
              await page.keyboard.press('Escape')
              await menu.waitFor({ state: 'hidden' })
            }
          }
        }

        // 4. Chat links (unchanged) + HTML in side + Note beside chat (built-in).
        await page.goto(app.url + threadPath)
        const chat = page.locator('.tt-stream')
        const chatHtml = chat.getByRole('link', { name: 'Friday review mockup' })
        const chatNote = chat.getByRole('link', { name: 'weekly plan' })
        const chatWeb = chat.getByRole('link', { name: 'reading list' })
        await chatHtml.scrollIntoViewIfNeeded()
        if (wide) await chatHtml.click()
        else await chatHtml.tap()
        const viewer = wide ? side : page.locator('.screen.files')
        await viewer.frameLocator('iframe').getByRole('heading', { name: 'Friday review' }).waitFor()
        if (wide) {
          await capture('html-side')
          if (!baseline) {
            assert.equal(await side.locator('.side-head .sh-sharebtn').count(), 1, 'HTML side header has Share')
            assert.equal(await side.locator('.side-head .fs-comments-btn').count(), 1, 'HTML side header has Comments toggle')
            const header = await side.locator('.side-head').boundingBox()
            const iframe = await side.locator('iframe').boundingBox()
            assert.equal(iframe.y, header.y + header.height)
          }
          await side.getByRole('button', { name: 'Item actions', exact: true }).click()
          await menu.waitFor()
          await capture('html-side-menu')
          assert.equal(await menu.getByRole('button', { name: 'Reload', exact: true }).count(), 1)
          assert.equal(await menu.getByRole('link', { name: 'Open in new tab', exact: true }).count(), 1)
          await page.keyboard.press('Escape')
          await menu.waitFor({ state: 'hidden' })

          // Note beside chat: follow wiki-link (stays in side, Back returns) & Cmd/Ctrl-click (opens in main) & web link.
          await chatNote.click()
          await side.locator('.fs-preview').getByRole('heading', { name: 'Next steps' }).waitFor()
          if (!baseline) {
            assert.equal(await side.locator('.filelink-side').count(), 0)
            await side.getByRole('link', { name: 'companion note' }).click()
            await side.locator('.fs-preview').getByRole('heading', { name: 'Focus' }).waitFor()
            assert.equal(new URL(page.url()).pathname, threadPath)
            await side.getByRole('button', { name: 'Back', exact: true }).click()
            await side.locator('.fs-preview').getByRole('heading', { name: 'Next steps' }).waitFor()
            const [sideWeb] = await Promise.all([context.waitForEvent('page'), side.getByRole('link', { name: 'reading list' }).click()])
            await sideWeb.waitForURL(external)
            await sideWeb.close()
            await side.getByRole('link', { name: 'companion note' }).click({ modifiers: ['Control'] })
            await page.waitForURL(`**${otherPath}**`)
            await page.goBack()
            await page.waitForURL(`**${threadPath}**`)
            // Verify side image & PDF headers keep Share + Comments toggle too.
            await side.getByRole('link', { name: 'reference image' }).click()
            await side.locator('.fs-img').waitFor()
            assert.equal(await side.locator('.side-head .sh-sharebtn').count(), 1)
            assert.equal(await side.locator('.side-head .fs-comments-btn').count(), 1)
            await side.getByRole('button', { name: 'Back', exact: true }).click()
            await side.getByRole('link', { name: 'one-page brief' }).click()
            await side.locator('.fs-pdfframe').waitFor()
            assert.equal(await side.locator('.side-head .sh-sharebtn').count(), 1)
            assert.equal(await side.locator('.side-head .fs-comments-btn').count(), 1)
            await side.getByRole('button', { name: 'Item actions', exact: true }).click()
            await menu.getByRole('link', { name: 'Open in new tab', exact: true }).waitFor()
            await page.keyboard.press('Escape')
          }
        } else {
          // Phone path out from chat and Back to chat.
          await page.getByRole('button', { name: 'Back', exact: true }).click()
          await page.waitForURL(`**${threadPath}`)
          await chatNote.tap()
          await viewer.locator('.fs-preview').getByRole('heading', { name: 'Next steps' }).waitFor()
          await viewer.getByRole('link', { name: 'companion note' }).tap()
          await page.waitForURL(`**${otherPath}**`)
          await viewer.locator('.fs-preview').getByRole('heading', { name: 'Focus' }).waitFor()
          await page.getByRole('button', { name: 'Back', exact: true }).click()
          await page.waitForURL(`**${planPath}**`)
          await page.getByRole('button', { name: 'Back', exact: true }).click()
          await page.waitForURL(`**${threadPath}`)
        }
        const [chatExternal] = await Promise.all([context.waitForEvent('page'), chatWeb.click()])
        await chatExternal.waitForURL(external)
        await chatExternal.close()
        assert.deepEqual(errors, [])
      } finally {
        await context.close()
        await app.close()
      }
    }

    // -- Pass 2: With the real `comments` Markdown editor plugin mounted --
    {
      const { app, texts } = await mountFixture({ mode, withComments: true })
      const context = await browser.newContext({
        viewport: { width, height: wide ? 900 : 844 },
        colorScheme: mode,
        hasTouch: !wide,
        isMobile: !wide,
        reducedMotion: 'reduce',
      })
      try {
        await context.route('**/x/comments/**', route => {
          const url = new URL(route.request().url())
          const p = url.searchParams.get('path') || 'Notes/plan.md'
          const text = texts[`vault:${p}`] || plan
          return route.fulfill({ json: { text, revision: 'r1', notes: [], can: { edit: true, comment: true }, reach: 'everywhere' } })
        })
        await context.route(external, route => route.fulfill({ body: 'Reading list' }))
        await context.route(`${app.url}/sw.js`, route => route.abort())

        const page = await context.newPage()
        await page.addInitScript(() => localStorage.setItem('loam-ui.file-panel', 'closed'))
        page.setDefaultTimeout(10000)
        const errors = []
        page.on('pageerror', error => errors.push(error.message))
        const capture = async (name) => {
          if (!output) return
          await page.evaluate(async () => {
            await document.fonts.ready
            await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))
          })
          await page.screenshot({ path: join(output, `${name}-${width}-${mode}.png`), animations: 'disabled' })
        }

        const main = wide ? page.locator('[data-panel="main"]') : page.locator('.screen.files')
        const side = page.getByRole('region', { name: 'Side panel' })

        // Open plan.md in Files main with the calm editor; switch to Viewing (Reading) mode.
        await page.goto(app.url + planPath)
        await main.locator('.dc .cm-content').waitFor()
        await main.locator('.dc-head .sh-sharebtn').waitFor()
        await main.locator('.dc-mpill').click()
        await main.getByRole('menuitemradio', { name: /Viewing/ }).click()
        await main.locator('.dc.mode-view .dc-wiki[data-wiki]').first().waitFor()
        // Plain click wiki-link in Viewing mode from Files main.
        await main.locator('.dc.mode-view .dc-wiki[data-wiki]').first().click()
        if (wide && !baseline) {
          await side.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
          assert.equal(new URL(page.url()).pathname, planPath)
          // No duplicate Share button in side panel when custom editor owns its header.
          assert.equal(await side.locator('.sh-sharebtn').count(), 1)
          await capture('editor-reading-main')
          // Cmd/Ctrl-click wiki-link from Files main opens in main.
          await main.locator('.dc.mode-view .dc-wiki[data-wiki]').first().click({ modifiers: ['Control'] })
          await page.waitForURL(`**${otherPath}**`)
          await main.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
          await page.goBack()
          await main.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
        } else if (!wide) {
          await page.waitForURL(`**${otherPath}**`)
          await main.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
          await capture('editor-reading-main')
          await main.getByRole('button', { name: 'Back', exact: true }).click()
          await page.waitForURL(`**${planPath}**`)
          await main.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
        } else {
          await page.waitForURL(`**${otherPath}**`)
          await main.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
          await capture('editor-reading-main')
          await page.goBack()
          await main.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
        }

        // Web link in Viewing mode opens a new tab.
        await main.locator('.dc-mpill').click()
        await main.getByRole('menuitemradio', { name: /Viewing/ }).click()
        const [edWeb] = await Promise.all([
          context.waitForEvent('page'),
          main.locator('.dc.mode-view .dc-link').filter({ hasText: 'reading list' }).click(),
        ])
        await edWeb.waitForURL(external)
        await edWeb.close()

        // Open note beside a chat (desktop): no duplicate Share, Viewing click navigates side, Cmd/Ctrl opens main.
        if (wide) {
          await page.goto(app.url + threadPath)
          const chatNote = page.locator('.tt-stream').getByRole('link', { name: 'weekly plan' })
          await chatNote.scrollIntoViewIfNeeded()
          await chatNote.click()
          await side.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
          await capture('editor-side-chat')
          if (!baseline) {
            assert.equal(await side.locator('.sh-sharebtn').count(), 1, 'no duplicate Share button in side panel')
            await side.locator('.dc-mpill').click()
            await side.getByRole('menuitemradio', { name: /Viewing/ }).click()
            await side.locator('.dc.mode-view .dc-wiki[data-wiki]').first().click()
            await side.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
            assert.equal(new URL(page.url()).pathname, threadPath)
            await side.getByRole('button', { name: 'Back', exact: true }).click()
            await side.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
            await side.locator('.dc-mpill').click()
            await side.getByRole('menuitemradio', { name: /Viewing/ }).click()
            await side.locator('.dc.mode-view .dc-wiki[data-wiki]').first().click({ modifiers: ['Control'] })
            await page.waitForURL(`**${otherPath}**`)
            await page.goBack()
            await page.waitForURL(`**${threadPath}**`)
          }
        }
        assert.deepEqual(errors, [])
      } finally {
        await context.close()
        await app.close()
      }
    }

    console.log(`${width} ${mode} passed`)
  }
} finally {
  await browser.close()
}
