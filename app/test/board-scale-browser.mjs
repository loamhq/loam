// Integration with Appearance: viewport rectangles must not be scaled twice.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { oneProductFixture } from './one-product-fixture.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.BOARD_EVIDENCE
if (!out) throw new Error('Set BOARD_EVIDENCE')
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] }), results = []
const source = '# Board\n\n## This week\n\n- [x] Gather the loose ends <!-- Sprout · until 2026-12-01 · future field -->\n\n### Keeping close\n\n- [ ] Leave Friday open <!-- Sam -->\n\n## Someday\n\n'
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) for (const size of [90, 125]) {
    const app = await oneProductFixture()
    const value = { mode: theme, size }
    app.routes['GET /v1/settings/appearance'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ phone: value, desktop: value })) }
    await app.f.api('PUT', '/threads/a-quieter-week/board', { markdown: source })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, hasTouch: width === 390, isMobile: width === 390 })
    const page = await context.newPage(), errors = []; page.setDefaultTimeout(10000)
    page.on('pageerror', (e) => errors.push(e.message))
    try {
      await page.goto(app.url)
      await page.getByTitle('A little room for the week', { exact: true }).click()
      await page.getByRole('button', { name: 'Board', exact: true }).click()
      await page.locator('.tt-board-item').first().waitFor()
      await page.waitForFunction((scale) => document.documentElement.style.getPropertyValue('--ui-scale') === String(scale), size / 100)
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      const box = await page.getByRole('button', { name: 'Drag Gather the loose ends', exact: true }).boundingBox()
      const target = await page.locator('[data-board-section="heading:Someday:0"] .tt-board-list-end').boundingBox()
      const start = { x: box.x + box.width / 2, y: box.y + box.height / 2 }, end = { x: target.x + target.width / 2, y: target.y + 2 }
      let touch
      if (width === 390) {
        touch = await context.newCDPSession(page)
        await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [start] })
        await new Promise((resolve) => setTimeout(resolve, 420))
        await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [end] })
      } else {
        await page.mouse.move(start.x, start.y); await page.mouse.down(); await page.mouse.move(end.x, end.y, { steps: 8 })
      }
      await page.locator('.tt-board-ghost').waitFor(); await new Promise((resolve) => setTimeout(resolve, 80))
      const geometry = await page.evaluate(() => {
        const rect = (selector) => { const r = document.querySelector(selector).getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom } }
        return { ghost: rect('.tt-board-ghost'), indicator: rect('.tt-board-drop'), pane: rect('.tt-board-items'), end: rect('[data-board-section="heading:Someday:0"] .tt-board-list-end') }
      })
      assert.ok(geometry.ghost.left >= geometry.pane.left - 2 && geometry.ghost.right <= geometry.pane.right + 2, JSON.stringify(geometry))
      assert.ok(Math.abs(geometry.indicator.top - geometry.end.top) < 3, JSON.stringify(geometry))
      const file = `scale-drag-${width}-${theme}-${size}.png`
      await page.screenshot({ path: path.join(out, file) })
      if (touch) await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
      else await page.mouse.up()
      await page.locator('[data-board-section="heading:Someday:0"]').getByRole('checkbox').waitFor()
      assert.equal((await app.f.api('GET', '/threads/a-quieter-week/board')).markdown, source.replace('- [x] Gather the loose ends <!-- Sprout · until 2026-12-01 · future field -->\n', '').replace('## Someday\n', '## Someday\n- [x] Gather the loose ends <!-- Sprout · until 2026-12-01 · future field -->\n'))
      assert.deepEqual(errors, [])
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      results.push({ width, theme, size, geometry, file }); console.log(`PASS ${width} ${theme} ${size}%`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
await writeFile(path.join(out, 'scale-browser.json'), JSON.stringify(results, null, 2) + '\n')
