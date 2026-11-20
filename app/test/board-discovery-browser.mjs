// Brief-derived real-board shape, synthetic item text; actual page/HTTP relay.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile, readdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.BOARD_DISCOVERY_EVIDENCE
if (!out) throw new Error('Set BOARD_DISCOVERY_EVIDENCE')
await mkdir(out, { recursive: true })
const source = await readFile(new URL('./fixtures/threads/board-unheaded-ideas.md', import.meta.url), 'utf8')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] }), results = []
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = [], captures = []
    await app.f.api('PUT', '/threads/a-quieter-week/board', { markdown: source })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    await context.addInitScript(() => {
      globalThis.boardLifts = []
      const animate = Element.prototype.animate
      Element.prototype.animate = function (...args) {
        const animation = animate.apply(this, args)
        if (this.classList.contains('tt-board-ghost')) boardLifts.push({ frames: animation.effect.getKeyframes(), duration: animation.effect.getTiming().duration })
        return animation
      }
    })
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', (e) => errors.push(e.message))
    const current = async () => (await app.f.api('GET', '/threads/a-quieter-week/board')).markdown
    const shot = async (state) => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      const file = `${state}-${width}-${theme}.png`; captures.push(file); await page.screenshot({ path: path.join(out, file) })
    }
    const open = async () => {
      await page.goto(app.url)
      if (!await page.locator('.tt-head').isVisible()) await page.getByTitle('A little room for the week', { exact: true }).click()
      await page.getByRole('button', { name: 'Board', exact: true }).click()
      await page.locator('.tt-board-item').first().waitFor()
      await page.mouse.move(1, 1)
    }
    const editorControls = async () => {
      await page.getByRole('button', { name: 'Edit board', exact: true }).click()
      await page.locator('.tt-board-editor').waitFor()
      assert.deepEqual(await page.locator('.tt-board-head button').allTextContents(), ['Cancel', 'Done'])
      assert.equal(await page.getByRole('button', { name: 'Close board', exact: true }).count(), 0)
    }
    try {
      for (const file of ['threads.js', 'threads.css', 'board-drag.js']) await context.route(`**/${file}`, (route) => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: execFileSync('git', ['show', `b2dceb8:public/${file}`], { encoding: 'utf8' }) }))
      await open(); await shot('before-rest')
      const baseline = await page.locator('.tt-list-add').evaluateAll((nodes) => nodes.map((n) => ({ opacity: getComputedStyle(n).opacity, text: n.textContent })))
      if (width === 1440) assert.ok(baseline.every((n) => n.opacity === '0'), 'deployed-commit Add controls are hidden at desktop rest')
      await editorControls(); await shot('deployed-commit-editor')
      for (const file of ['threads.js', 'threads.css', 'board-drag.js']) await context.unroute(`**/${file}`)
      await open()
      await page.locator('.tt-board-hint').waitFor()
      const adds = page.locator('.tt-list-add')
      assert.deepEqual(await adds.allTextContents(), ['Add', 'Add'])
      const atRest = await adds.evaluateAll((nodes) => nodes.map((n) => {
        const s = getComputedStyle(n), r = n.getBoundingClientRect()
        return { text: n.textContent, opacity: s.opacity, display: s.display, visibility: s.visibility, height: r.height, section: n.closest('[data-board-section]').dataset.boardSection }
      }))
      assert.ok(atRest.every((n) => n.opacity === '1' && n.visibility === 'visible' && n.display !== 'none' && n.height >= 44))
      assert.deepEqual(atRest.map((n) => n.section), ['top', 'heading:Ideas::0'])
      assert.equal(await page.locator('.tt-board-list-title').textContent(), 'Ideas')
      assert.equal(await page.locator('.tt-board-list-count').textContent(), '2')
      const heading = await page.locator('.tt-board-list-title').evaluate((n) => {
        const s = getComputedStyle(n), count = getComputedStyle(n.nextElementSibling)
        return { size: parseFloat(s.fontSize), weight: s.fontWeight, transform: s.textTransform, color: s.color, countColor: count.color }
      })
      assert.ok(heading.size >= 12 && Number(heading.weight) >= 700)
      assert.equal(heading.transform, 'uppercase'); assert.notEqual(heading.color, heading.countColor)
      assert.equal(await current(), source)
      await shot('rest')
      await page.clock.install(); await page.clock.runFor(7200)
      await page.locator('.tt-board-hint').waitFor({ state: 'detached' })
      await page.getByRole('button', { name: 'Close board', exact: true }).click()
      await page.getByRole('button', { name: 'Board', exact: true }).click()
      await adds.first().waitFor(); assert.equal(await page.locator('.tt-board-hint').count(), 0, 'hint appears once per page visit')
      await page.getByRole('region', { name: 'Ideas', exact: true }).getByRole('button', { name: 'Add a note', exact: true }).click()
      const input = page.locator('.tt-quick'); await input.fill('Make room for one experiment')
      assert.equal(await input.evaluate((n) => n === document.activeElement), true)
      assert.equal(await input.evaluate((n) => n.closest('[data-board-section]').dataset.boardSection), 'heading:Ideas::0')
      await shot('add-open')
      await input.press('Enter')
      await page.getByRole('checkbox', { name: 'Complete Make room for one experiment', exact: true }).waitFor()
      const added = source + '- [ ] Make room for one experiment <!-- Sam -->\n'
      assert.equal(await current(), added)
      await input.press('Escape'); await input.waitFor({ state: 'detached' })
      await page.waitForFunction(() => !document.querySelector('.tt-grip')?.disabled)
      const grip = page.getByRole('button', { name: 'Drag Gather the loose ends', exact: true })
      const row = grip.locator('..'), handle = width === 390 ? grip : row.locator('.tt-item-copy')
      const box = await handle.boundingBox(), target = await page.locator('[data-board-section="heading:Ideas::0"] .tt-board-list-head').boundingBox()
      if (width === 1440) assert.equal(await row.evaluate((n) => getComputedStyle(n).cursor), 'grab')
      const start = { x: box.x + box.width / 2, y: box.y + box.height / 2 }, end = { x: target.x + target.width / 2, y: target.y + target.height + 4 }
      let touch
      if (width === 390) {
        touch = await context.newCDPSession(page)
        await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [start] })
        await page.clock.runFor(120); assert.equal(await page.locator('.tt-board-ghost').count(), 0)
        await page.clock.runFor(300)
        await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [end] })
      } else {
        await page.mouse.move(start.x, start.y); await page.mouse.down(); await page.mouse.move(end.x, end.y, { steps: 10 })
      }
      await page.locator('.tt-board-ghost').waitFor(); await page.clock.runFor(80)
      assert.equal(await page.locator('.tt-board-drop').isVisible(), true)
      assert.equal(await current(), added, 'drag flight does not write')
      await shot('drag')
      if (touch) await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
      else await page.mouse.up()
      const destination = page.locator('[data-board-section="heading:Ideas::0"]')
      await destination.getByRole('checkbox', { name: 'Complete Gather the loose ends', exact: true }).waitFor()
      const raw = '- [x] Gather the loose ends <!-- Sprout · until 2026-12-01 · future field -->\n'
      const moved = added.replace(raw, '').replace('### Ideas:\n\n', '### Ideas:\n\n' + raw)
      assert.equal(await current(), moved)
      assert.equal(await page.locator('.tt-board-list-count').textContent(), '4')
      await page.clock.runFor(260); await shot('dropped')
      const lifts = await page.evaluate(() => boardLifts)
      if (width === 390) { assert.equal(lifts.length, 1); assert.equal(lifts[0].duration, 220); assert.equal(lifts[0].frames[1].scale, '1.025') }
      await page.getByRole('button', { name: 'Actions for Gather the loose ends', exact: true }).click()
      await page.getByRole('button', { name: 'Move to…', exact: true }).click()
      await page.locator('.tt-item-menu').getByRole('button', { name: 'Ideas', exact: true }).waitFor()
      await page.keyboard.press('Escape'); await page.locator('.tt-item-menu').waitFor({ state: 'detached' })
      await editorControls(); await shot('editor')
      assert.equal(await page.locator('.tt-board-editor').inputValue(), moved)
      await page.getByRole('button', { name: 'Done', exact: true }).click()
      await page.locator('.tt-board-items').waitFor(); assert.equal(await current(), moved, 'display-only colon stripping never changes the file')
      if (touch) {
        await page.emulateMedia({ reducedMotion: 'reduce' })
        await page.waitForFunction(() => !document.querySelector('.tt-grip')?.disabled)
        const r = await page.getByRole('button', { name: 'Drag Gather the loose ends', exact: true }).boundingBox()
        await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x + r.width / 2, y: r.y + r.height / 2 }] })
        await page.clock.runFor(400); await page.locator('.tt-board-ghost').waitFor()
        assert.equal(await page.evaluate(() => boardLifts.length), lifts.length, 'reduced motion does not start a lift scale animation')
        await touch.send('Input.dispatchTouchEvent', { type: 'touchCancel', touchPoints: [] })
        assert.equal(await current(), moved)
      }
      assert.deepEqual(errors, [])
      results.push({ width, theme, baseline, atRest, heading, lifts, captures, deployedCommit: 'b2dceb8', source: 'brief-derived shape with synthetic item text', assertions: 'visible end-of-list Add for both lists, title/count/colon, one-time hint, add route, row/grip native drag, metadata, keyboard Move to, deployed/current Done/Cancel, no errors or overflow' })
      console.log(`PASS ${width} ${theme}`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
const files = (await readdir(out)).filter((f) => f.endsWith('.png')).sort()
const captures = await Promise.all(files.map(async (file) => { const bytes = await readFile(path.join(out, file)); return { file, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') } }))
await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
