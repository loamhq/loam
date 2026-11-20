// The conflicting lifecycle path: real pointer drag and phone return, then pause.
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { oneProductFixture } from './one-product-fixture.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
try {
  for (const width of [390, 1440]) {
    const app = await oneProductFixture(), errors = []
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, hasTouch: width === 390, isMobile: width === 390 })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__mergeThreads = threads;' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', (e) => errors.push(e.message))
    const writes = () => app.broker.seen.filter((r) => r.method === 'PUT' && r.path.endsWith('/board')).length
    try {
      await page.goto(app.url)
      await page.getByTitle('A little room for the week', { exact: true }).click()
      await page.getByRole('button', { name: 'Board', exact: true }).click()
      const card = page.locator('.tt-item-copy').first(); await card.waitFor()
      const box = await card.boundingBox(), start = { x: box.x + box.width / 2, y: box.y + box.height / 2 }
      let touch
      if (width === 390) {
        touch = await context.newCDPSession(page)
        await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [start] })
        await new Promise((r) => setTimeout(r, 420))
        await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: start.x, y: start.y + 50 }] })
      } else {
        await page.mouse.move(start.x, start.y); await page.mouse.down(); await page.mouse.move(start.x, start.y + 50, { steps: 5 })
      }
      await page.locator('.tt-board-ghost').waitFor()
      const before = writes()
      await page.evaluate(() => __mergeThreads.pause())
      assert.equal(await page.locator('.tt-board-ghost,.tt-board-drop,.is-dragging').count(), 0)
      if (touch) await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
      else await page.mouse.up()
      assert.equal(writes(), before, 'releasing a paused drag cannot save a move')
      if (width === 390) {
        await page.clock.install()
        await page.getByRole('button', { name: 'Close board', exact: true }).click()
        await page.getByRole('button', { name: 'Back to threads', exact: true }).click()
        await page.waitForFunction(() => __mergeThreads.st.returning === 'a-quieter-week')
        await page.evaluate(() => __mergeThreads.pause())
        assert.equal(await page.evaluate(() => __mergeThreads.st.returning), null)
        await page.clock.runFor(1500)
        assert.equal(await page.evaluate(() => __mergeThreads.st.returning), null)
      } else {
        assert.equal(await page.getByTitle('A little room for the week', { exact: true }).getAttribute('aria-current'), 'page')
      }
      assert.deepEqual(errors, [])
      console.log(`PASS ${width}: pause cancels an active drag without a write${width === 390 ? ' and cancels the phone return timer' : '; current selection survives'}`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
