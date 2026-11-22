// Real relay and plugins, synthetic conversations only. Sample every painted
// frame: a screenshot taken after a write has already missed this regression.
import assert from 'node:assert/strict'
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'

const playwright = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const engine = process.env.THREAD_PAINT_ENGINE || 'chromium'
const baseline = process.env.THREAD_PAINT_BASELINE
const out = process.env.THREAD_PAINT_EVIDENCE || path.join(process.env.TMPDIR, 'thread-update-paint')
await mkdir(out, { recursive: true })
const browser = await playwright[engine].launch({ ...(engine === 'chromium' ? { executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] } : {}) })
const source = await readFile('public/app.js', 'utf8'), results = []
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const id = 'a-quieter-week'
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = [], requests = [], checks = []
    const plugins = await shippedPlugins(path.resolve('public'))
    const json = (res, value, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
    app.routes['GET /v1/plugins'] = async (req, res) => { await delay(100); json(res, { plugins }) }
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: { theme: 'latte', mode }, desktop: { theme: 'latte', mode } })
    const send = app.routes[`POST /v1/threads/${id}/messages`]
    app.routes[`POST /v1/threads/${id}/messages`] = async (...args) => { await delay(300); return send(...args) }
    const read = app.routes[`GET /v1/threads/${id}`]
    let failRead = false
    app.routes[`GET /v1/threads/${id}`] = async (...args) => { await delay(300); if (failRead) return json(args[1], { error: 'Synthetic read failed.' }, 403); return read(...args) }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode,
      isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'no-preference' })
    // Keep the dictation control available in headless WebKit, which has no
    // speech backend. No recording or permission prompt is invoked here.
    await context.addInitScript(() => { globalThis.SpeechRecognition ??= class {} })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__paint = { threads, pluginLoader, schedule };' }))
    if (baseline) for (const file of ['plugin-styles.js', 'threads.js']) await context.route(`**/${file}`, route => route.fulfill({ contentType: 'text/javascript', body: execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' }) }))
    let slowCSS = false
    for (const plugin of ['dictation', 'read-aloud']) for (const file of await readdir(`../plugins/_listed/${plugin}/page`)) {
      const asset = `/plugins/${plugin}/files/page/${file}`
      app.routes[`GET /v1${asset}`] = async (req, res) => {
        requests.push({ file: asset, slow: slowCSS, at: Date.now() })
        if (slowCSS && file.endsWith('.css')) await delay(700)
        res.writeHead(200, { 'content-type': file.endsWith('.css') ? 'text/css' : 'text/javascript', 'cache-control': 'no-store' })
        res.end(await readFile(`../plugins/_listed/${plugin}/page/${file}`))
      }
    }
    const page = await context.newPage();
    if (process.env.THREAD_PAINT_DEBUG) { page.on('console', m => console.log(m.type(), m.text())); page.on('requestfailed', r => console.log('requestfailed', r.url(), r.failure())) }
    page.setDefaultTimeout(15000); page.on('pageerror', error => errors.push(error.message))
    const stamp = `${baseline ? 'before' : 'after'}-${engine}-${width}-${mode}`
    const settle = () => page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
    // Hold actual existing prose, composer and scroller; detect transient
    // replacement, sheet loss and the mic's browser-default rectangular paint.
    const start = async label => {
      await page.evaluate(label => {
        const mic = document.querySelector('.roundmic'), stream = document.querySelector('.tt-stream'), prose = document.querySelector('.tt-prose-content:not(.tt-prose-plain)'), draft = document.querySelector('#draft')
        const paint = () => { const s = getComputedStyle(mic); return [s.borderRadius, s.backgroundColor, s.borderTopWidth].join('|') }
        const expected = paint(), sheets = [...document.querySelectorAll('link[data-plugin]')]
        globalThis.__frames = { label, frames: [], removedSheets: 0, expected, active: true }
        const report = __frames
        report.observer = new MutationObserver(records => { for (const r of records) for (const n of r.removedNodes) if (sheets.includes(n)) report.removedSheets++ })
        report.observer.observe(document.head, { childList: true })
        const frame = () => {
          if (!report.active) return
          report.frames.push({ t: Math.round(performance.now()), paint: paint(), sheets: sheets.every(n => n.isConnected && n.sheet),
            connected: stream.isConnected && prose.isConnected && draft.isConnected,
            loading: __paint.threads.st.open.loading, focus: document.activeElement === draft, selection: [getSelection().anchorOffset, getSelection().focusOffset, getSelection().toString()], scroll: stream.scrollTop })
          requestAnimationFrame(frame)
        }
        frame()
      }, label)
    }
    const stop = async (label, { preserve = false } = {}) => {
      await page.waitForTimeout(950); await settle()
      const report = await page.evaluate(() => { const r = __frames; r.active = false; r.observer.disconnect(); delete r.observer; return r })
      const badPaint = report.frames.filter(f => f.paint !== report.expected).length
      const missingSheets = report.frames.filter(f => !f.sheets).length
      assert.ok(report.frames.length > 1, `${label}: sampled multiple frames`)
      if (!baseline || label !== 'pin') assert.ok(report.frames.every(f => f.connected && !f.loading), `${label}: retained transcript/composer at every frame`)
      else assert.ok(report.frames.some(f => !f.connected), 'baseline header menu replaces the conversation')
      if (!baseline) {
        assert.equal(badPaint, 0, `${label}: mic material never flashes to default gray`)
        assert.equal(missingSheets, 0, `${label}: loaded CSS stays applied`)
        assert.equal(report.removedSheets, 0, `${label}: no loaded sheet detached`)
      }
      if (!baseline && label === 'pin') assert.ok(report.frames.every(f => Math.abs(f.scroll - report.frames[0].scroll) < 2), 'header menu retains reading scroll')
      if (preserve) {
        const first = report.frames[0]
        assert.ok(report.frames.every(f => f.focus && String(f.selection) === String(first.selection)), `${label}: focused draft selection retained`)
        assert.ok(report.frames.every(f => Math.abs(f.scroll - first.scroll) < 2), `${label}: reading scroll retained`)
      }
      checks.push({ label, badPaint, missingSheets, ...report })
    }
    try {
      await page.goto(`${app.url}/threads/${id}`, { waitUntil: 'domcontentloaded' })
      await page.locator('.tt-opening-note').waitFor()
      await page.waitForFunction(() => globalThis.__paint?.pluginLoader.st.loaded && !__paint.threads.st.open.loading)
      assert.equal(await page.locator('.roundmic').count(), 1, JSON.stringify(await page.evaluate(() => [...__paint.pluginLoader.st.failures])))
      await page.locator('.roundmic').waitFor(); await settle(); await page.waitForTimeout(600)
      assert.equal(await page.locator('.tt-opening-note').count(), 0)
      // First load really had a loading indicator; its delayed read completed.
      assert.ok(app.broker.seen.some(r => r.path === `/v1/threads/${id}`))
      slowCSS = true
      await page.locator('#draft').fill('Synthetic paint regression message')
      await start('send')
      const posting = page.getByRole('button', { name: 'Send', exact: true }).click()
      if (baseline) {
        await page.waitForFunction(() => [...document.querySelectorAll('link[data-plugin]')].some(n => !n.sheet))
        await page.screenshot({ path: path.join(out, `${stamp}-gray.png`) })
      }
      await posting
      await page.waitForFunction(() => !__paint.threads.st.open.sending)
      await stop('send')
      assert.equal(await page.locator('#draft').evaluate(n => n.value), '')
      await page.screenshot({ path: path.join(out, `${stamp}-settled.png`) })
      if (baseline) {
        assert.ok(checks[0].badPaint > 0 && checks[0].missingSheets > 0 && checks[0].removedSheets > 0, 'baseline causes the actual painted regression')
      }
      // Keep a draft, focused caret and a reading position while server events
      // arrive. Use a long reply so this also exercises an off-bottom reader.
      const long = Array.from({ length: 22 }, (_, i) => `Paragraph ${i + 1}: keep this reading position while the thread updates.`).join('\n\n')
      app.emit(id, { type: 'message.done', message: { id: 'long-paint', author: 'sprout', kind: 'message', text: long } })
      await page.getByText('Paragraph 22: keep this reading position while the thread updates.', { exact: true }).waitFor()
      await page.locator('#draft').fill('Keep this unfinished thought')
      await page.evaluate(() => {
        const draft = document.querySelector('#draft'); draft.focus(); const range = document.createRange(); range.setStart(draft.firstChild, 5); range.setEnd(draft.firstChild, 9); getSelection().removeAllRanges(); getSelection().addRange(range)
        const stream = document.querySelector('.tt-stream'); stream.scrollTop = Math.max(1, stream.scrollHeight / 3); stream.dispatchEvent(new Event('scroll'))
      })
      await settle(); await start('incoming-status')
      app.emit(id, { type: 'members', members: (await app.f.api('GET', `/threads/${id}`)).thread.members.map(m => ({ ...m, working: false, busy: true, background: 1 })) })
      app.emit(id, { type: 'message.done', message: { id: 'incoming-paint', author: 'quill', kind: 'message', text: 'A synthetic incoming reply.' } })
      await page.getByText('A synthetic incoming reply.', { exact: true }).waitFor({ state: 'attached' })
      await stop('incoming-status', { preserve: true })
      assert.equal(await page.locator('#draft').evaluate(n => n.value), 'Keep this unfinished thought')
      // Native transcript selection also survives catalog/theme activity.
      await page.evaluate(() => {
        const n = document.querySelector('.tt-prose-content:not(.tt-prose-plain) p').firstChild
        const range = document.createRange(); range.setStart(n, 0); range.setEnd(n, Math.min(8, n.length)); getSelection().removeAllRanges(); getSelection().addRange(range)
        globalThis.__selectedText = getSelection().toString()
      })
      await start('catalog-refresh'); await page.evaluate(() => __paint.pluginLoader.refresh()); await stop('catalog-refresh')
      assert.equal(await page.evaluate(() => getSelection().toString()), await page.evaluate(() => __selectedText))
      await page.evaluate(() => getSelection().removeAllRanges())
      // Representative write actions through the visible controls.
      await start('pin'); await page.getByRole('button', { name: 'Thread actions', exact: true }).click()
       await page.getByRole('button', { name: 'Pin thread', exact: true }).click(); await stop('pin')
      assert.equal(await page.evaluate(() => __paint.threads.st.open.thread.pinned), true)
      // A genuine failed opening remains visible and retryable.
      failRead = true
      await page.evaluate(() => { __paint.threads.close(); __paint.threads.open('a-quieter-week') })
      await page.waitForFunction(() => __paint.threads.st.open.readFailed)
      assert.match(await page.locator('.tt-composer .banner').innerText(), /Synthetic read failed\./)
      assert.equal(await page.locator('#draft').evaluate(n => n.value), 'Keep this unfinished thought')
      failRead = false
      await page.getByRole('button', { name: 'Refresh thread', exact: true }).click()
      await page.waitForFunction(() => !__paint.threads.st.open.refreshing && !__paint.threads.st.open.readFailed)
      assert.equal(await page.locator('#draft').evaluate(n => n.value), 'Keep this unfinished thought')
      assert.deepEqual(errors, [])
      results.push({ stamp, browser: browser.version(), checks, requests, errors })
      console.log(`PASS ${stamp}: send, incoming/status, catalog and pin; drafts, selection, scroll, read failure`)
    } finally { await context.close(); await app.close() }
  }
} finally {
  await writeFile(path.join(out, `${baseline ? 'before' : 'after'}-${engine}.json`), JSON.stringify(results, null, 2) + '\n')
  await browser.close()
}
