// Browser regression test and before/after captures for wiki-links in replies
// whose exact text was parsed earlier without linking (`imageRefs()` for inline
// images, `strip()` for thread-list/activity previews).
import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'
import { deflateSync } from 'node:zlib'
import { filesHost } from './files-host.js'
import { row } from './fixtures/files-contract.js'
import { oneProductFixture } from './one-product-fixture.mjs'

function samplePng(width = 320, height = 180) {
  const crcTable = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1)
    crcTable[n] = c >>> 0
  }
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length)
    const head = Buffer.from(type, 'ascii')
    let c = 0xffffffff
    for (const buf of [head, data]) for (const b of buf) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8)
    const crc = Buffer.alloc(4); crc.writeUInt32BE((c ^ 0xffffffff) >>> 0)
    return Buffer.concat([len, head, data, crc])
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4)
  ihdr[8] = 8; ihdr[9] = 2 // 8-bit RGB
  const raw = Buffer.alloc((width * 3 + 1) * height)
  for (let y = 0; y < height; y++) {
    const rowAt = y * (width * 3 + 1)
    raw[rowAt] = 0
    for (let x = 0; x < width; x++) {
      const px = rowAt + 1 + x * 3
      const card = x > 24 && x < width - 24 && y > 24 && y < height - 24
      const bar = card && y > 44 && y < 60 && x > 44 && x < 170
      const line1 = card && y > 78 && y < 86 && x > 44 && x < width - 64
      const line2 = card && y > 96 && y < 104 && x > 44 && x < width - 104
      raw[px] = bar ? 186 : (line1 || line2) ? 208 : card ? 248 : 234
      raw[px + 1] = bar ? 132 : (line1 || line2) ? 202 : card ? 245 : 229
      raw[px + 2] = bar ? 92 : (line1 || line2) ? 192 : card ? 238 : 220
    }
  }
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ])
}
const PREVIEW_PNG = samplePng()

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const output = process.env.WIKILINK_CACHE_EVIDENCE
const baseline = process.env.WIKILINK_CACHE_BASELINE === '1'
const previewOrigin = 'https://preview.example.com'
const threadPath = '/threads/a-quieter-week'
const htmlPath = '/files/vault/Notes/page.html'
const replyText = 'I rebuilt [[Notes/page.html|Friday review]] with the updated notes and saved the layout snapshot below.\n\n![Layout preview](Notes/pic.png)'
const attachment = {
  path: 'Notes/pic.png',
  root: 'vault',
  name: 'pic.png',
  type: 'image/png',
  size: PREVIEW_PNG.length,
  width: 320,
  height: 180,
  alt: 'Layout preview',
  refs: ['Notes/pic.png'],
}
const mockup = `<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: light dark; background: light-dark(#f3f4eb,#252b24); color: light-dark(#2d382d,#e6eadc); }
  body { margin: clamp(24px,6vw,68px); font: 16px/1.6 system-ui; }
  p { max-width: 36ch; } h1 { font: 400 clamp(36px,6vw,60px)/1.02 Georgia; margin: 18px 0; }
  small { letter-spacing: .12em; text-transform: uppercase; }
</style>
<small>Studio notes</small><h1>Friday review</h1><p>A quiet page opened straight from the reply's wiki-link.</p>
<script>document.body.dataset.ready = 'true'</script>`

if (output) await mkdir(output, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const wide = width === 1440
    const app = await oneProductFixture({ FILES_CONTENT_ORIGIN: previewOrigin })
    const context = await browser.newContext({
      viewport: { width, height: wide ? 900 : 844 },
      colorScheme: mode,
      hasTouch: !wide,
      isMobile: !wide,
      reducedMotion: 'reduce',
    })
    try {
      const host = filesHost()
      Object.assign(app.routes, Object.fromEntries(Object.entries(host.routes).filter(([path]) => path.includes('/files/'))))
      app.routes['GET /v1/files/download'] = (req, res, url) => {
        if (url.searchParams.get('path') !== 'Notes/pic.png') return host.routes['GET /v1/files/download'](req, res, url)
        res.writeHead(200, {
          'content-type': 'image/png',
          'content-disposition': "attachment; filename*=UTF-8''pic.png",
          'x-content-type-options': 'nosniff',
          'content-security-policy': "sandbox; default-src 'none'",
          'cache-control': 'no-store',
        })
        res.end(PREVIEW_PNG)
      }
      app.routes['GET /v1/files/resolve'] = (req, res, url) => {
        const ref = url.searchParams.get('ref')
        if (ref === '[[Notes/page.html]]' || ref === htmlPath) {
          return res.end(JSON.stringify({ status: 'found', resource: row('vault', 'Notes/page.html'), fragment: null }))
        }
        return host.routes['GET /v1/files/resolve'](req, res, url)
      }
      const record = await app.f.api('GET', threadPath)
      const target = record.messages.find((message) => message.id === 'm4')
      target.text = replyText
      target.attachments = [attachment]
      app.routes[`GET /v1${threadPath}`] = (_, res) => res.end(JSON.stringify(record))

      const list = await app.f.api('GET', '/threads')
      const listed = (list.threads ?? []).find((t) => t.id === 'a-quieter-week')
      if (listed?.last) listed.last.text = replyText
      app.routes['GET /v1/threads'] = (_, res) => res.end(JSON.stringify(list))

      const appearance = { theme: 'latte', mode, size: 100 }
      app.routes['GET /v1/settings/appearance'] = (_, res) => res.end(JSON.stringify({ phone: appearance, desktop: appearance }))

      await context.route(`${previewOrigin}/**`, (route) => route.fulfill({ contentType: 'text/html', body: mockup }))
      await context.route(`${app.url}/sw.js`, (route) => route.abort())

      const page = await context.newPage()
      page.setDefaultTimeout(8000)
      const errors = []
      page.on('pageerror', (error) => errors.push(error.message))
      const capture = async (name) => {
        if (!output) return
        await page.evaluate(() => document.fonts.ready)
        await page.screenshot({ path: join(output, `${name}-${width}-${mode}.png`), animations: 'disabled' })
      }

      await page.goto(app.url + threadPath)
      const chat = page.locator('.tt-stream')
      const reply = chat.locator('.say.agent.md').filter({ hasText: 'I rebuilt' }).last()
      await reply.waitFor()
      await reply.scrollIntoViewIfNeeded()
      await reply.locator('figure.reply-figure.loaded img').waitFor()
      await page.evaluate(() => document.fonts.ready)
      await page.mouse.move(0, 0)

      if (baseline) {
        assert.equal(await reply.locator('a.filelink').count(), 0, 'baseline should reproduce poisoned cache (no filelink)')
        assert.match(await reply.innerText(), /\[\[Notes\/page\.html\|Friday review\]\]/)
        await capture('before')
        console.log(`baseline ${width} ${mode} reproduced`)
        continue
      }

      const link = reply.locator('a.filelink', { hasText: 'Friday review' })
      await link.waitFor()
      assert.equal(await reply.locator('a.filelink').count(), 1)
      assert.doesNotMatch(await reply.innerText(), /\[\[/)
      await capture('after')

      const viewer = wide ? page.getByRole('region', { name: 'Side panel' }) : page.locator('.screen.files')
      const frame = viewer.frameLocator('iframe')
      if (wide) await link.click()
      else await link.tap()
      await frame.getByRole('heading', { name: 'Friday review' }).waitFor()
      await frame.locator('body[data-ready=true]').waitFor()
      assert.equal(new URL(page.url()).pathname, wide ? threadPath : htmlPath)
      await capture('opened')

      assert.deepEqual(errors, [])
      console.log(`${width} ${mode} passed`)
    } finally {
      await context.close()
      await app.close()
    }
  }
} finally {
  await browser.close()
}
