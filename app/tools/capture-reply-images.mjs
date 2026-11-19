// An agent's images where it wrote them (2026-11-18, evening), in a real
// browser: one image mid-reply, a row of two (one with no alt text), a file
// that cannot be read, and a reply from the morning before (images under the
// words). At 390 and 1440, light and dark, in Latte and Foundry. Fails on a
// page error, sideways overflow, any `![` on the screen, a filename under a
// picture, a row whose pictures differ in height, or an image view that does
// not open and close as before. Synthetic records; the pictures are drawn here.
//
//   REPLY_IMAGES_EVIDENCE=<dir> LOAM_PLAYWRIGHT=<playwright index.mjs> \
//     LOAM_CHROME=/usr/bin/chromium-headless-shell node tools/capture-reply-images.mjs
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import zlib from 'node:zlib'
import { oneProductFixture } from '../test/one-product-fixture.mjs'

const out = process.env.REPLY_IMAGES_EVIDENCE
if (!out) throw new Error('Set REPLY_IMAGES_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')

// -- the pictures ------------------------------------------------------------
function png(w, h, paint) {
  const rows = Buffer.alloc((w * 3 + 1) * h)
  for (let y = 0, o = 0; y < h; y++) { rows[o++] = 0; for (let x = 0; x < w; x++) { const [r, g, b] = paint(x / w, y / h); rows[o++] = r; rows[o++] = g; rows[o++] = b } }
  const chunk = (kind, data) => { const len = Buffer.alloc(4); len.writeUInt32BE(data.length); const body = Buffer.concat([Buffer.from(kind), data]); const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32(body)); return Buffer.concat([len, body, crc]) }
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(rows)), chunk('IEND', Buffer.alloc(0))])
}
const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * Math.max(0, Math.min(1, t))))
const pictures = {
  'Pictures/dawn.png': { w: 800, h: 450, data: png(800, 450, (x, y) => {
    const hill = 0.62 + 0.06 * Math.sin(x * 7) + 0.03 * Math.sin(x * 19)
    if (y > hill) return mix([92, 112, 84], [48, 62, 50], (y - hill) * 2.5)
    if ((x - 0.7) ** 2 + ((y - 0.35) * 1.6) ** 2 < 0.006) return [255, 236, 200]
    return mix([246, 205, 160], [120, 150, 190], y * 1.4)
  }) },
  'Pictures/list.png': { w: 360, h: 780, data: png(360, 780, (x, y) => {
    for (let i = 0; i < 6; i++) { const top = 0.1 + i * 0.14; if (y > top && y < top + 0.09 && x > 0.08 && x < 0.92) return x > 0.12 && x < 0.2 ? [188, 133, 82] : mix([255, 255, 255], [246, 240, 232], x) }
    return mix([236, 232, 224], [214, 206, 194], y)
  }) },
  'Pictures/sea.png': { w: 640, h: 480, data: png(640, 480, (x, y) => y < 0.45 ? mix([215, 228, 240], [160, 190, 220], y * 2) : mix([70, 120, 160], [24, 60, 92], (y - 0.45 + 0.02 * Math.sin(x * 40 + y * 30)) * 1.8)) },
}
const file = (p, alt, refs = [p]) => ({ path: p, root: 'vault', name: path.basename(p), type: 'image/png', size: pictures[p]?.data.length ?? 1000, width: pictures[p]?.w ?? 1200, height: pictures[p]?.h ?? 800, ...(alt ? { alt } : {}), refs })

// -- the thread ----------------------------------------------------------------
const at = '2026-11-18T22:56:00Z', id = 'a-quieter-week'
const messages = [
  { id: 'm1', author: 'you', kind: 'message', at, text: 'Can you show me the two directions for the settings page?' },
  { id: 'm2', author: 'sprout', kind: 'message', at, engine: 'claude-code', did: [],
    text: 'Here is the first direction, warmer and quieter than what we have now.\n\n![Providers with the latte touches](Pictures/dawn.png)\n\nThe headings sit lower and the rows breathe a little more. It reads as one surface instead of a stack of cards.',
    attachments: [file('Pictures/dawn.png', 'Providers with the latte touches')] },
  { id: 'm3', author: 'you', kind: 'message', at, text: 'And side by side with the phone version?' },
  { id: 'm4', author: 'sprout', kind: 'message', at, engine: 'claude-code', did: [],
    text: 'Side by side, desktop and phone:\n\n![The desktop column](Pictures/sea.png)\n![](Pictures/list.png)\n\nOn the phone the list keeps its rhythm; nothing wraps.',
    attachments: [file('Pictures/sea.png', 'The desktop column'), file('Pictures/list.png')] },
  { id: 'm5', author: 'you', kind: 'message', at, text: 'What about the old mockup?' },
  { id: 'm6', author: 'sprout', kind: 'message', at, engine: 'claude-code', did: [],
    text: 'That one has moved since, so it may not open:\n\n![The first mockup](Pictures/gone.png)\n\nI can make it again if you want it.',
    attachments: [file('Pictures/gone.png', 'The first mockup')] },
  // A reply posted the morning before: text taken off, no refs.
  { id: 'm7', author: 'sprout', kind: 'message', at, engine: 'claude-code', did: [],
    text: 'From this morning, for comparison:',
    attachments: [(({ refs, ...f }) => f)(file('Pictures/sea.png', 'This morning'))] },
]

const results = []
try {
  for (const look of ['latte', 'foundry']) for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = []
    const record = await app.f.api('GET', `/threads/${id}`)
    record.thread.unread = 0
    const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...record, messages, has_more: false, turn: null, turns: [] })
    app.routes[`GET /v1/threads/${id}/steps`] = (req, res) => json(res, { error: 'No full record' }, 404)
    const settings = { theme: look, mode, size: 100, options: {} }
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: settings, desktop: settings })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, timezoneId: 'UTC' })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__pics = { threads };' }))
    await context.route('**/api/files/download**', (route) => {
      const p = new URL(route.request().url()).searchParams.get('path')
      const pic = pictures[p]
      return pic ? route.fulfill({ contentType: 'image/png', body: pic.data }) : route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"not found"}' })
    })
    const page = await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', (e) => errors.push(e.message))
    const settle = async () => { await page.mouse.move(0, 0); await page.evaluate(async () => { document.activeElement?.blur(); await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }); await page.waitForTimeout(450) }
    const name = `${look}-${width}-${mode}`
    if (process.env.REPLY_IMAGES_ONLY && process.env.REPLY_IMAGES_ONLY !== name) { await context.close(); await app.close(); continue }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate((id) => __pics.threads.open(id), id)
      await page.locator('[data-message-id="m6"]').waitFor()
      // Pictures load lazily: bring each reply near the screen and wait for its own.
      for (const m of ['m2', 'm4', 'm6']) {
        await page.locator(`[data-message-id="${m}"]`).evaluate((n) => { const pane = n.closest('.tt-stream'); pane.scrollTop += n.getBoundingClientRect().top - pane.getBoundingClientRect().top - 80 })
        // The thread may still settle at its end after opening: keep the reply in view while waiting.
        await page.waitForFunction((m) => {
          const n = document.querySelector(`[data-message-id="${m}"]`), pane = n?.closest('.tt-stream')
          if (pane) pane.scrollTop += n.getBoundingClientRect().top - pane.getBoundingClientRect().top - 80
          const fs = [...(n?.querySelectorAll('.reply-figure') ?? [])]
          return fs.length && fs.every((f) => /\b(loaded|missing)\b/.test(f.className))
        }, m, { polling: 250 })
      }
      // Nothing on the screen is image syntax, and no filename shows.
      const shown = await page.locator('.tt-stream').evaluate((n) => n.innerText)
      assert.ok(!shown.includes('!['), `${name}: raw image syntax on screen`)
      assert.ok(!/dawn\.png|list\.png|gone\.png/.test(shown), `${name}: a filename on screen`)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name}: sideways overflow`)
      // In place: m2's picture is between its two paragraphs.
      const order = await page.locator('[data-message-id="m2"] .tt-prose-content').evaluate((n) => [...n.children].map((c) => c.className || c.tagName))
      assert.deepEqual(order, ['P', 'reply-figures', 'P'], `${name}: m2 in place`)
      const captions = await page.locator('.reply-caption').allInnerTexts()
      assert.deepEqual(captions, ['Providers with the latte touches', 'The desktop column', 'The first mockup'], `${name}: captions`)
      // The row of two: side by side and one height on desktop, stacked on a phone.
      const boxes = await page.locator('[data-message-id="m4"] .reply-image').evaluateAll((ns) => ns.map((n) => { const r = n.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height } }))
      assert.equal(boxes.length, 2)
      if (width === 1440) { assert.ok(Math.abs(boxes[0].h - boxes[1].h) < 1.5 && Math.abs(boxes[0].y - boxes[1].y) < 1, `${name}: row of one height ${JSON.stringify(boxes)}`) }
      else assert.ok(boxes[1].y > boxes[0].y + boxes[0].h, `${name}: stacked on a phone`)
      // A picture keeps its shape.
      const dawn = await page.locator('[data-message-id="m2"] .reply-image').evaluate((n) => n.getBoundingClientRect().width / n.getBoundingClientRect().height)
      assert.ok(Math.abs(dawn - 800 / 450) < 0.02, `${name}: aspect ${dawn}`)
      // The morning's reply keeps its picture under the words.
      assert.equal(await page.locator('[data-message-id="m7"] .message-image').count(), 1, `${name}: old reply's picture at the end`)
      // A tap opens the full view, Escape closes it.
      await page.locator('[data-message-id="m4"] .reply-image').first().scrollIntoViewIfNeeded()
      await page.locator('[data-message-id="m4"] .reply-image').first().click()
      await page.locator('.image-view').waitFor()
      assert.equal(await page.locator('.image-view-count').innerText(), '1 of 2')
      await page.keyboard.press('ArrowRight')
      assert.equal(await page.locator('.image-view-count').innerText(), '2 of 2')
      await page.keyboard.press('Escape')
      await page.locator('.image-view').waitFor({ state: 'detached' })
      // A tap on the missing picture opens nothing.
      await page.locator('[data-message-id="m6"] .reply-image').click()
      await page.waitForTimeout(150)
      assert.equal(await page.locator('.image-view').count(), 0, `${name}: a missing picture opens nothing`)
      for (const m of ['m2', 'm4', 'm6']) {
        await page.locator(`[data-message-id="${m}"]`).evaluate((n) => { const pane = n.closest('.tt-stream'); pane.scrollTop += n.getBoundingClientRect().top - pane.getBoundingClientRect().top - 80 })
        await settle()
        await page.screenshot({ path: path.join(out, `${name}-${m}.png`), animations: 'disabled' })
      }
      assert.deepEqual(errors, [])
      results.push({ name, ok: true })
    } catch (error) {
      await page.screenshot({ path: path.join(out, `${name}-FAILED.png`) }).catch(() => {})
      results.push({ name, ok: false, error: error.message })
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
await writeFile(path.join(out, 'results.json'), JSON.stringify(results, null, 2))
for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.error ? `  | ${r.error}` : ''}`)
process.exit(results.every((r) => r.ok) ? 0 : 1)
