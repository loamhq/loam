// Captures for batch B step 5 (2026-11-19): Settings › Agents with its one
// row, "Hears when Loam needs attention"; and a thread where a turn is
// waiting for room and where one failed, both drawn from the turn's state
// (`turn.waiting`, `turn.mark`), with nothing added to the record. At 390
// and 1440, light and dark, against the fixture app: no person's data.
//
//   ATTENTION_EVIDENCE=<folder> LOAM_PLAYWRIGHT=<playwright/index.mjs> LOAM_CHROME=<chromium> node tools/capture-attention.mjs
//
// Asserts no page error, no sideways overflow, that the row changes the
// setting through PATCH /v1/system/persona, and that the thread's record
// holds the same messages before and after the marks.
import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from '../test/one-product-fixture.mjs'

const out = process.env.ATTENTION_EVIDENCE
if (!out) throw new Error('Set ATTENTION_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8')
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const at = '2026-11-18T19:30:00Z', thread = 'a-quieter-week'

try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = [], patches = []
    const attention = { persona: 'sprout', chosen: null, default: 'sprout' }
    app.routes['GET /v1/system/persona'] = (req, res) => json(res, attention)
    app.routes['PATCH /v1/system/persona'] = (req, res, url, raw) => {
      const body = JSON.parse(raw); patches.push(body)
      attention.chosen = body.persona; attention.persona = body.persona || attention.default
      json(res, attention)
    }
    app.routes['GET /v1/engineers'] = (req, res) => json(res, { engineers: [], default: { effective: { provider: 'claude-code' } }, applies: [] })
    app.routes['GET /v1/system/reviewer'] = (req, res) => json(res, { name: 'Nightly reviewer', plain: 'Reads the day’s work and leaves notes', effective: { provider: 'claude-code' } })
    const settings = { theme: 'latte', mode, size: 100, options: {} }
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: settings, desktop: settings })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode,
      isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce', timezoneId: 'UTC',
      extraHTTPHeaders: { 'Remote-User': 'sam', 'Remote-Name': 'Sam Rivera' } })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__cap={threads,goSettings,openSheet,schedule,state:page.state};' }))
    const page = await context.newPage(); page.setDefaultTimeout(12000)
    await page.clock.setFixedTime(new Date(at))
    page.on('pageerror', e => errors.push(e.message))
    const shot = async (surface) => {
      await page.mouse.move(0, 0)
      await page.evaluate(async () => { document.activeElement?.blur(); await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      await page.waitForTimeout(300)
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth || [...document.querySelectorAll('.setmain, .sheet.settings')].some(n => n.scrollWidth > n.clientWidth + 1))
      assert.equal(overflow, false, `${surface} ${width} ${mode}: sideways overflow`)
      const file = `${surface}-${width}-${mode}.png`
      await page.screenshot({ path: path.join(out, file), animations: 'disabled' })
      return file
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      const files = []
      await page.evaluate(() => __cap.goSettings('agents'))
      const select = page.locator('select[aria-label="Hears when Loam needs attention"]')
      await select.waitFor()
      assert.equal(await select.inputValue(), 'sprout', 'the first agent by default')
      files.push(await shot('a-settings-agents'))
      await select.selectOption('quill')
      await page.waitForFunction(() => document.querySelector('select[aria-label="Hears when Loam needs attention"]')?.value === 'quill')
      assert.deepEqual(patches, [{ persona: 'quill' }], 'changed through the route')
      files.push(await shot('b-settings-agents-changed'))

      await page.evaluate(() => { __cap.state.sheet = null; __cap.schedule() })
      await page.evaluate(async id => { await __cap.threads.open(id) }, thread)
      await page.locator('.tt-author-run').first().waitFor()
      await page.waitForTimeout(600)
      const before = (await app.f.api('GET', `/threads/${thread}?limit=50`)).messages.length
      // Waiting for room: the turn's state, said on the turn.
      app.emit(thread, { type: 'turn.started', persona: 'quill', engine: 'claude-code', model: null })
      app.emit(thread, { type: 'turn.waiting', persona: 'quill', text: 'Quill is waiting for room: the box is running 10 sessions (Sprout, Scout and 8 more). I’ll post as soon as one frees up.' })
      await page.waitForFunction(() => document.body.innerText.includes('waiting for room'))
      files.push(await shot('c-thread-waiting'))
      // The wait ran out: the turn ends with a mark, not a message.
      app.emit(thread, { type: 'turn.mark', persona: 'quill', at: new Date(Date.now() + 60000).toISOString(),
        text: 'Quill’s turn failed: the session exited before it answered' })
      app.emit(thread, { type: 'turn.finished', persona: 'quill' })
      await page.waitForFunction(() => document.querySelector('[data-message-id="mark:quill"]'))
      await page.evaluate(() => document.querySelector('[data-message-id="mark:quill"]').scrollIntoView({ block: 'center' }))
      files.push(await shot('d-thread-failed'))
      const after = (await app.f.api('GET', `/threads/${thread}?limit=50`)).messages.length
      assert.equal(after, before, 'nothing was added to the record')
      assert.deepEqual(errors, [])
      console.log(`PASS ${width} ${mode}: ${files.join(', ')} (record ${before} -> ${after} messages)`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
