// Captures of Settings › Connections › API keys (the old id, `accounts`, still opens them) and of an agent's images in a thread, at 390
// and 1440, light and dark, against the fixture app: no person's data.
//
//   ACCOUNTS_EVIDENCE=<folder> LOAM_PLAYWRIGHT=<playwright/index.mjs> node tools/capture-accounts.mjs
//
// Asserts no page error and no sideways overflow on any surface.
import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import { deflateSync } from 'node:zlib'
import path from 'node:path'
import { oneProductFixture } from '../test/one-product-fixture.mjs'

const out = process.env.ACCOUNTS_EVIDENCE
if (!out) throw new Error('Set ACCOUNTS_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8')
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }

// A picture without anyone's data: a dawn gradient with a sun, drawn here.
function crc(buf) { let c, crcv = 0xffffffff; for (const b of buf) { c = (crcv ^ b) & 0xff; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; crcv = (crcv >>> 8) ^ c } return (crcv ^ 0xffffffff) >>> 0 }
function chunk(type, data) { const len = Buffer.alloc(4); len.writeUInt32BE(data.length); const td = Buffer.concat([Buffer.from(type), data]); const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([len, td, c]) }
function picture(w, h, [top, bottom], sun) {
  const rows = []
  for (let y = 0; y < h; y++) {
    const row = Buffer.alloc(1 + w * 3); const t = y / h
    for (let x = 0; x < w; x++) {
      let rgb = top.map((v, i) => Math.round(v + (bottom[i] - v) * t))
      const d = Math.hypot(x - sun[0] * w, y - sun[1] * h)
      if (d < h * 0.14) rgb = [252, 226, 170]
      else if (y > h * 0.72) rgb = rgb.map(v => Math.round(v * 0.55))
      row.set(rgb, 1 + x * 3)
    }
    rows.push(row)
  }
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(Buffer.concat(rows))), chunk('IEND', Buffer.alloc(0))])
}
const PICTURES = { 'dawn.png': picture(960, 640, [[64, 86, 140], [236, 164, 120]], [0.68, 0.62]),
  'dusk.png': picture(960, 640, [[34, 30, 74], [190, 96, 110]], [0.3, 0.66]) }

const KEYS = JSON.parse(await readFile(new URL('./fake-keys.json', import.meta.url), 'utf8'))
const at = '2026-11-18T19:30:00Z', id = 'a-quieter-week'
const results = []
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = []
    const record = await app.f.api('GET', `/threads/${id}`)
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...record, messages: [
      { id: 'q', author: 'you', kind: 'message', at, text: 'Could you make two options for the cover of the week’s notes?' },
      { id: 'a', author: 'sprout', kind: 'message', at, engine: 'claude-code', text: 'Here are two: a dawn and a dusk. The dawn reads as a beginning; the dusk as a week put to rest. I like the dawn for Monday’s note.',
        attachments: [{ path: 'Images/2026-11-18-dawn.png', root: 'vault', name: 'dawn.png', type: 'image/png', alt: 'A dawn over water' },
          { path: 'sprout/art/dusk.png', root: 'spaces', name: 'dusk.png', type: 'image/png', alt: 'A dusk over water' }] },
    ], has_more: false, turn: null, turns: [] })
    const keys = JSON.parse(JSON.stringify(KEYS))
    app.routes['GET /v1/keys'] = (req, res) => json(res, { providers: keys })
    for (const k of keys) app.routes[`GET /v1/keys/${k.id}`] = (req, res) => json(res, k)
    const settings = { theme: 'latte', mode, size: 100, options: {} }
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: settings, desktop: settings })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode,
      isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce', timezoneId: 'UTC' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__cap={threads,goSettings,schedule,state:page.state};' }))
    await context.route(/\/api\/files\/download\?/, route => {
      const name = new URL(route.request().url()).searchParams.get('path').split('/').at(-1)
      route.fulfill({ contentType: 'image/png', body: PICTURES[name.includes('dusk') ? 'dusk.png' : 'dawn.png'] })
    })
    const page = await context.newPage(); page.setDefaultTimeout(12000)
    await page.clock.setFixedTime(new Date(at))
    page.on('pageerror', e => errors.push(e.message))
    const shot = async surface => {
      await page.mouse.move(0, 0)
      await page.evaluate(async () => { document.activeElement?.blur(); await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      await page.waitForTimeout(250)
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
      assert.equal(overflow, false, `${surface} ${width} ${mode}: sideways overflow`)
      const file = `${surface}-${width}-${mode}.png`
      await page.screenshot({ path: path.join(out, file), animations: 'disabled' })
      return file
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      const files = []
      await page.evaluate(() => __cap.goSettings('accounts'))
      await page.locator('.keys-list .setrow').first().waitFor()
      files.push(await shot('accounts'))
      await page.evaluate(() => __cap.goSettings('accounts', 'openai'))
      await page.locator('.keys-card').waitFor()
      files.push(await shot('accounts-openai'))
      await page.evaluate(() => __cap.goSettings('accounts', 'google'))
      await page.locator('.keys-user .setrow').first().click()
      await page.locator('.keys-user-detail').waitFor()
      files.push(await shot('accounts-google-plugin'))
      await page.evaluate(() => { __cap.state.sheet = null; __cap.schedule() })
      await page.evaluate(id => __cap.threads.open(id), id)
      await page.locator('[data-message-id="a"] .message-image img').first().waitFor()
      await page.waitForFunction(() => [...document.querySelectorAll('[data-message-id="a"] .message-image img')].every(i => i.complete && i.naturalWidth > 0))
      files.push(await shot('thread-agent-images'))
      await page.locator('[data-message-id="a"] .message-image').first().click()
      await page.locator('.image-view-img').waitFor()
      await page.waitForFunction(() => document.querySelector('.image-view-img')?.naturalWidth > 0)
      files.push(await shot('image-view'))
      await page.keyboard.press('ArrowRight')
      assert.equal(await page.locator('.image-view-name').textContent(), 'dusk.png')
      await page.keyboard.press('Escape')
      assert.equal(await page.locator('.image-view').count(), 0)
      assert.deepEqual(errors, [])
      results.push({ width, mode, files })
      console.log(`PASS ${width} ${mode}: ${files.join(', ')}`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
