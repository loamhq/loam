// Real app/relay with synthetic records. No live conversation or preferences.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'
import { oneProductFixture } from './one-product-fixture.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.MEDIA_BOOT_EVIDENCE
assert.ok(out, 'Set MEDIA_BOOT_EVIDENCE')
await mkdir(out, { recursive: true })
const probe = process.env.MEDIA_BOOT_PROBE === '1'
const require = createRequire(path.join(process.env.TMPDIR, 'deps/package.json'))
const sharp = require('sharp')
const pictures = await Promise.all([[600, 400, '#b4b99b'], [300, 600, '#c5b4a6'], [600, 300, '#a9b6c4']].map(([width, height, fill], i) =>
  sharp(Buffer.from(`<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg"><rect width="100%" height="100%" fill="${fill}"/><circle cx="${width / 2}" cy="${height / 2}" r="${Math.min(width, height) / 4}" fill="#e8e2d8"/><text x="50%" y="52%" text-anchor="middle" font-size="32" fill="#514c45">${i + 1}</text></svg>`)).png().toBuffer()))
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const results = [], source = await readFile('public/app.js', 'utf8')
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
try {
  for (const width of [390, 1440]) for (const look of ['latte', 'plain', 'example']) for (const mode of ['light', 'dark']) {
    const app = await oneProductFixture(), id = 'a-quieter-week'
    const plugins = (await shippedPlugins(path.resolve('public'))).filter(row => row.kind.includes('theme'))
    const example = { id: 'example', name: 'Example', api: 0, version: '1.0.0', author: 'Test fixture', effective: true, layer: 'yours', kind: ['theme'] }
    const exampleCSS = ':root[data-look="example"] { --font-display: Georgia, serif; --radius-md: 17px; }'
    app.routes['GET /v1/plugins'] = (req, res) => json(res, { plugins: [...plugins, example] })
    app.routes['GET /v1/plugins/example/files/theme.css'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/css' }); res.end(exampleCSS) }
    app.routes['GET /v1/sessions/live'] = (req, res) => json(res, { sessions: [], live_count: 0, cap: 10 })
    app.routes['GET /v1/sessions/live/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
    app.routes['GET /v1/activity'] = (req, res) => json(res, { items: [], jobs: { slots: [], queue: [], landing: [] }, total: 0, next_offset: null })
    app.routes['GET /v1/limits'] = (req, res) => json(res, { attachment_max_bytes: 10 * 1024 * 1024 })
    app.routes['GET /v1/integrations/github'] = (req, res) => json(res, { connected: false })
    const settings = { mode, theme: look, size: 100 }
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: settings, desktop: settings })
    const record = await app.f.api('GET', `/threads/${id}`)
    const files = [
      { path: '/some/root/98_ATTACHMENTS/preview/one.png', name: 'one.png', type: 'image/png' },
      { path: '98_ATTACHMENTS/preview/two.png', name: 'two.png', type: 'image/png' },
      { path: '/another/root/98_ATTACHMENTS/preview/three.png', name: 'three.png', type: 'image/png' },
    ]
    const message = { id: 'images', author: 'you', kind: 'message', at: '2026-11-12T20:45:00Z', text: 'Three attached images.', attachments: files, did: [] }
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...record, messages: [message], has_more: false, turns: [], turn: null })
    app.routes['GET /v1/files/download'] = (req, res, url) => {
      const match = /^98_ATTACHMENTS\/preview\/(one|two|three)\.png$/.exec(url.searchParams.get('path'))
      if (!match) return json(res, { error: 'File not found' }, 404)
      res.writeHead(200, { 'content-type': 'image/png', 'cache-control': 'no-store' }); res.end(pictures[['one', 'two', 'three'].indexOf(match[1])])
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, isMobile: width === 390, hasTouch: width === 390, colorScheme: mode, reducedMotion: 'reduce' })
    const errors = [], requests = []
    try {
      if (probe) for (const file of ['chat-ui.js', 'plugin-themes.js']) {
        assert.ok(process.env.MEDIA_BOOT_PROBE_REF, 'Set MEDIA_BOOT_PROBE_REF to the pre-fix commit')
        const body = execFileSync('git', ['show', `${process.env.MEDIA_BOOT_PROBE_REF}:public/${file}`], { encoding: 'utf8' })
        await context.route(`**/${file}`, route => route.fulfill({ contentType: 'text/javascript', body }))
      }
      await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__mediaBoot = {threads, schedule};' }))
      // Slow both the blocking stylesheet and the plugin refresh independently.
      await context.route('**/themes.css*', async route => { await new Promise(resolve => setTimeout(resolve, 700)); await route.continue() })
      await context.route('**/api/health', route => route.fulfill({ json: { configured: true, ok: true, speech: false } }))
      await context.route('**/api/speech/lookup', route => route.fulfill({ json: { status: 'none' } }))
      await context.addInitScript(() => {
        globalThis.__frames = []
        const frame = () => {
          const root = document.documentElement, style = getComputedStyle(root)
          if (document.querySelector('.tt-shell')) __frames.push({ at: performance.now(), look: root.dataset.look, display: style.getPropertyValue('--font-display').trim(), paper: style.getPropertyValue('--paper-page').trim() })
          requestAnimationFrame(frame)
        }
        requestAnimationFrame(frame)
      })
      const page = await context.newPage()
      page.on('pageerror', error => errors.push(error.message))
      page.on('console', msg => { if (msg.type() === 'error') errors.push(`${msg.text()} ${msg.location().url}`) })
      page.on('request', req => { if (req.url().includes('/api/files/download')) requests.push(new URL(req.url()).searchParams.get('path')) })
      const client = await context.newCDPSession(page)
      await client.send('Network.enable'); await client.send('Network.setCacheDisabled', { cacheDisabled: true })
      const film = []
      client.on('Page.screencastFrame', event => { film.push({ data: event.data, timestamp: event.metadata.timestamp }); void client.send('Page.screencastFrameAck', { sessionId: event.sessionId }) })
      await client.send('Page.startScreencast', { format: 'png', everyNthFrame: 1 })
      const loads = []
      for (const reload of [false, true]) {
        if (reload) await page.reload({ waitUntil: 'domcontentloaded' })
        else await page.goto(app.url, { waitUntil: 'domcontentloaded' })
        await page.locator('.tt-shell').waitFor()
        await page.waitForTimeout(1600)
        const frames = await page.evaluate(() => ({ origin: performance.timeOrigin, frames: __frames, paints: performance.getEntriesByType('paint').map(({ name, startTime }) => ({ name, startTime })) }))
        loads.push({ reload, ...frames })
        if (!probe) for (const frame of frames.frames) {
          assert.equal(frame.look, look)
          assert.ok(frame.display.includes(look === 'latte' ? 'Caprasimo' : look === 'example' ? 'Georgia' : 'system-ui'), JSON.stringify(frame))
          if (look === 'latte') assert.equal(frame.paper.toUpperCase(), mode === 'light' ? '#EEEAE3' : '#1A1917')
        }
      }
      await client.send('Page.stopScreencast')
      // Retain the first app frame and the rest of the transition as a filmstrip.
      for (let i = 0; i < film.length; i++) await writeFile(path.join(out, `paint-${width}-${look}-${mode}-${i}.png`), Buffer.from(film[i].data, 'base64'))
      await page.evaluate(id => __mediaBoot.threads.open(id), id)
      await page.locator('.message-image').first().waitFor()
      await page.locator('.message-image').last().scrollIntoViewIfNeeded()
      await page.waitForTimeout(400)
      for (let i = 0; i < 20; i++) { await page.evaluate(() => __mediaBoot.schedule()); await page.waitForTimeout(30) }
      const images = await page.locator('.message-image').evaluateAll(nodes => nodes.map(n => { const r = n.getBoundingClientRect(), img = n.querySelector('img'); return { x: r.x, y: r.y, right: r.right, width: r.width, loaded: img.complete && img.naturalWidth > 0 } }))
      const successfulRequests = [...requests]
      const successErrors = [...errors]
      if (!probe) {
        assert.ok(images.every(i => i.loaded), JSON.stringify(images))
        assert.equal(images[0].y, images[1].y); assert.ok(images[1].x >= images[0].right + 7.5)
        assert.equal(requests.length, 3, JSON.stringify(requests))
        assert.deepEqual(errors, [])
      }
      await page.screenshot({ path: path.join(out, `images-${width}-${look}-${mode}.png`) })
      // Isolate expected failure console messages from the successful-page check.
      await page.evaluate(() => {
        globalThis.__badFiles = [{ path: '98_ATTACHMENTS/preview/missing.png', name: 'missing.png', type: 'image/png' }]
      })
      await page.evaluate(async () => {
        const { messageFiles } = await import('/chat-ui.js'), { morph } = await import('/ui.js')
        const host = document.createElement('div'); host.id = 'failure-probe'; document.body.append(host)
        globalThis.__drawFailure = () => morph(host, messageFiles(__badFiles))
        __drawFailure()
        host.scrollIntoView()
      })
      await page.waitForTimeout(150)
      for (let i = 0; i < 20; i++) { await page.evaluate(() => __drawFailure()); await page.waitForTimeout(30) }
      const failedRequests = requests.filter(p => p.endsWith('/missing.png')).length
      const chip = await page.locator('#failure-probe').innerText()
      if (!probe) { assert.equal(failedRequests, 1); assert.equal(chip, 'missing.png'); assert.equal(await page.locator('#failure-probe .attachment-chip.sent').count(), 1) }
      results.push({ width, look, mode, loads, film: film.map(({ timestamp }, i) => ({ timestamp, file: `paint-${width}-${look}-${mode}-${i}.png` })), images, successfulRequests, failedRequests, chip, successErrors, errors })
      console.log(`${probe ? 'PROBE' : 'PASS'} ${width} ${look} ${mode}: cold/reload paint; ${successfulRequests.length} image requests; ${failedRequests} failed-image requests across 20 redraws`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close(); await writeFile(path.join(out, 'checks.json'), JSON.stringify(results, null, 2) + '\n') }
