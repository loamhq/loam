// Actual page and relay, synthetic records only. Chromium native range checks;
// operating-system copy callouts cannot be captured in headless Chromium.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_SELECTION_NATIVE_EVIDENCE
if (!out) throw new Error('Set THREAD_SELECTION_NATIVE_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8'), results = []
try {
  for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), id = 'a-quieter-week', errors = []
    const record = await app.f.api('GET', `/threads/${id}`)
    const messages = [
      { id: 'm1', author: 'you', text: 'Please keep Friday open for a slower morning.' },
      { id: 'm2', author: 'sprout', text: 'Keep the morning open.\n\nWe can gather the loose ends on Thursday, and leave Friday free for a slower start. Keep the useful details close to the conversation, with enough space to choose what matters. A calmer week begins with a short list, and we can make that list together.' },
    ].map((m) => ({ ...m, kind: 'message', at: '2026-11-11T20:45:00Z', engine: 'claude-code', did: [] }))
    app.routes[`GET /v1/threads/${id}`] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ ...record, messages, has_more: false, turn: null, turns: [] })) }
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: theme, isMobile: true, hasTouch: true })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__native = { threads, schedule };' }))
    await context.route('**/api/health', (route) => route.fulfill({ json: { configured: true, ok: true, speech: true } }))
    await context.route('**/api/speech/lookup', (route) => route.fulfill({ json: { status: 'none' } }))
    const page = await context.newPage(); page.on('pageerror', (e) => errors.push(e.message))
    const settle = async () => { await page.waitForTimeout(100); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }) }
    const shot = async (name) => { await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(out, `${name}-390-${theme}.png`), animations: 'disabled' }) }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate((id) => __native.threads.open(id), id)
      await page.waitForFunction(() => !__native.threads.st.open.loading && !__native.threads.st.open.refreshing)
      const article = page.locator('[data-message-id="m2"]'), prose = article.locator('.md p').first()
      await prose.waitFor(); await settle()
      await article.evaluate((n) => { const pane = n.closest('.tt-stream'); pane.scrollTop += n.getBoundingClientRect().top - pane.getBoundingClientRect().top - 150; pane.dispatchEvent(new Event('scroll')) })
      await settle()
      assert.deepEqual(await article.locator('.tt-footer-action,.speakbtn').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('aria-label'))), ['Reply', 'Branch into a new thread', 'Copy', 'Read aloud'])
      assert.deepEqual(await page.locator('[data-message-id="m1"] .tt-footer-action').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('aria-label'))), ['Reply', 'Branch into a new thread', 'Copy'])
      await shot('footer-icons')
      // Capture cancelation at the document bubble phase, after app handlers.
      await page.evaluate(() => {
        window.nativeEvents = []
        for (const type of ['touchstart', 'contextmenu']) document.addEventListener(type, (e) => {
          if (e.target.closest('.tt-message')) nativeEvents.push({ type, prevented: e.defaultPrevented })
        })
      })
      const session = await context.newCDPSession(page)
      const point = await prose.evaluate((n) => { const r = document.createRange(); r.setStart(n.firstChild, 5); r.setEnd(n.firstChild, 8); const b = r.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 } })
      const touch = async (type, dx = 0, dy = 0) => session.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x: point.x + dx, y: point.y + dy }] })
      const noAction = async () => { assert.equal(await page.locator('.tt-message-menu').count(), 0); assert.equal(await page.locator('.tt-reply-chip').count(), 0) }
      await touch('touchStart'); await page.waitForTimeout(700); await touch('touchEnd'); await settle(); await noAction()
      // Headless Chromium can omit native word selection on a held touch.
      // Use the browser's word-selection command if so; never inject a Range.
      let selectionMethod = 'held touch'
      if (!(await page.evaluate(() => getSelection().toString()))) {
        selectionMethod = 'held touch followed by native double-click'
        await page.mouse.dblclick(point.x, point.y); await settle()
      }
      const selected = await page.evaluate(() => ({ text: getSelection().toString(), ranges: getSelection().rangeCount, collapsed: getSelection().isCollapsed, inMessage: document.querySelector('[data-message-id="m2"]').contains(getSelection().anchorNode) }))
      assert.equal(selected.text.trim(), 'the'); assert.equal(selected.ranges, 1); assert.equal(selected.collapsed, false); assert.equal(selected.inMessage, true)
      const styles = await prose.evaluate((n) => { const s = getComputedStyle(n); return { select: s.userSelect, webkitSelect: s.webkitUserSelect, callout: s.webkitTouchCallout ?? 'unsupported' } })
      assert.notEqual(styles.select, 'none'); assert.notEqual(styles.webkitSelect, 'none'); assert.notEqual(styles.callout, 'none')
      await shot('long-press-word-selection')
      // An existing selection owns a horizontal drag, even past reply distance.
      await touch('touchStart'); await touch('touchMove', 70); await touch('touchEnd'); await settle(); await noAction()
      await page.evaluate(() => getSelection().removeAllRanges())
      // Small motion does not turn a held press into a late swipe.
      await touch('touchStart'); await touch('touchMove', 9); await page.waitForTimeout(600); await touch('touchMove', 70); await touch('touchEnd'); await settle(); await noAction()
      await page.evaluate(() => getSelection().removeAllRanges())
      await touch('touchStart'); await touch('touchMove', 2, 45); await touch('touchEnd'); await settle(); await noAction()
      // Context menu is native, including on ordinary prose.
      await prose.dispatchEvent('contextmenu', { bubbles: true, cancelable: true }); await noAction()
      const events = await page.evaluate(() => nativeEvents)
      assert.ok(events.some((e) => e.type === 'touchstart')); assert.ok(events.some((e) => e.type === 'contextmenu')); assert.ok(events.every((e) => !e.prevented))
      await page.evaluate(() => getSelection().removeAllRanges())
      await touch('touchStart')
      for (const dx of [20, 40, 65]) { await touch('touchMove', dx, 2); await page.waitForTimeout(40) }
      assert.equal(await article.evaluate((n) => n.classList.contains('reply-ready')), true)
      assert.notEqual(await prose.evaluate((n) => getComputedStyle(n).userSelect), 'none')
      await shot('swipe-mid-gesture')
      await touch('touchEnd'); await page.locator('.tt-reply-chip').waitFor()
      assert.equal(await page.evaluate(() => __native.threads.st.open.replyTo.id), 'm2')
      await page.getByRole('button', { name: 'Cancel reply', exact: true }).click()
      await session.detach(); assert.deepEqual(errors, [])
      results.push({ width: 390, theme, selected, selectionMethod, styles, events, errors, passed: ['native word selection', 'hold has no menu or reply', 'selection drag has no reply', 'held drift has no reply', 'vertical drag has no reply', 'uncanceled touchstart/contextmenu', 'horizontal threshold replies', 'footer icons'] })
      console.log(`PASS 390 ${theme}: native selection, hold/vertical/selection guards, swipe, footer icons (${selectionMethod})`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ results }, null, 2) + '\n')
} finally { await browser.close() }
