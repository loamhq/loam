// Real app/relay and browser input against synthetic boards; no real vault write.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { oneProductFixture } from './one-product-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.BOARD_EVIDENCE
if (!out) throw new Error('Set BOARD_EVIDENCE to a retained evidence directory')
await mkdir(out, { recursive: true })
const source = '# Board\r\n\r\n## This week\r\n\r\n- [ ] Read the first draft <!-- Quill · new -->\r\n- [x] Gather the loose ends <!-- Sprout · until 2026-12-01 · future field -->\r\n\r\n### Keeping close\r\n\r\n- [ ] Leave Friday open <!-- Sam -->\r\n\r\n## Someday\r\n\r\n'
const results = [], browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = [], captures = []
    await app.f.api('PUT', '/threads/a-quieter-week/board', { markdown: source })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    await context.addInitScript(() => {
      globalThis.boardSettles = []
      const animate = Element.prototype.animate
      Element.prototype.animate = function (...args) {
        const result = animate.apply(this, args)
        if (this.matches('.tt-board-item')) boardSettles.push({ frames: result.effect.getKeyframes(), duration: result.effect.getTiming().duration })
        return result
      }
    })
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', (e) => errors.push(e.message))
    const shot = async (state) => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })
      const file = `${state}-${width}-${theme}.png`; captures.push(file)
      await page.screenshot({ path: path.join(out, file) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    }
    const current = async () => (await app.f.api('GET', '/threads/a-quieter-week/board')).markdown
    const writes = () => app.broker.seen.filter((r) => r.method === 'PUT' && r.path === '/v1/threads/a-quieter-week/board').length
    const opening = async () => {
      await page.goto(app.url)
      if (!await page.locator('.tt-head').isVisible()) await page.getByTitle('A little room for the week', { exact: true }).click()
      await page.locator('.tt-head').waitFor()
      await page.getByRole('button', { name: 'Board', exact: true }).click()
      await page.locator('.tt-board-item').first().waitFor()
    }
    try {
      // Default to the shipped renderer. Historical comparisons need a version
      // compatible with today's app services (the former hard-coded one predates retirement).
      if (process.env.BOARD_BASELINE) for (const file of ['threads.js', 'thread-identity.js']) {
        await context.route(`**/${file}`, (route) => route.fulfill({ contentType: 'text/javascript', body: execFileSync('git', ['show', `${process.env.BOARD_BASELINE}:public/${file}`], { encoding: 'utf8' }) }))
      }
      await opening()
      await page.getByRole('button', { name: 'Edit board', exact: true }).click()
      await page.locator('.tt-board-editor').waitFor()
      assert.equal(await page.locator('.tt-board-editor').inputValue(), source.replace(/\r\n/g, '\n'))
      await shot('edit-before')
      if (process.env.BOARD_BASELINE) { await context.unroute('**/threads.js'); await context.unroute('**/thread-identity.js') }
      await page.reload()
      await page.locator('.tt-head').waitFor()
      await page.getByRole('button', { name: 'Board', exact: true }).click()
      await page.locator('.tt-board-list-head').first().waitFor()
      assert.equal(await page.locator('.tt-list-add').count(), 3)
      assert.equal(await page.locator('.tt-quick').count(), 0)
      await shot('lists')
      await page.getByRole('region', { name: 'Keeping close', exact: true }).getByRole('button', { name: 'Add a note', exact: true }).click()
      const quick = page.locator('.tt-quick')
      assert.equal(await quick.evaluate((n) => n === document.activeElement), true)
      await quick.fill('Make a little room')
      await shot('add-open')
      await quick.press('Enter')
      await page.getByRole('checkbox', { name: 'Complete Make a little room', exact: true }).waitFor()
      assert.equal(await quick.inputValue(), '')
      assert.equal(await quick.evaluate((n) => n === document.activeElement), true)
      assert.equal(await current(), source.replace('- [ ] Leave Friday open <!-- Sam -->\r\n', '- [ ] Leave Friday open <!-- Sam -->\r\n- [ ] Make a little room <!-- Sam -->\r\n'))
      await quick.fill('One more')
      await quick.press('Enter')
      await page.getByRole('checkbox', { name: 'Complete One more', exact: true }).waitFor()
      await quick.press('Escape')
      await quick.waitFor({ state: 'detached' })
      assert.equal(await quick.count(), 0)
      assert.equal(await page.locator('.tt-board').isVisible(), true)
      await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === 'Add a note' && document.activeElement?.closest('[aria-label="Keeping close"]'))
      // Blur closes; it does not commit an unfinished item.
      const beforeBlur = writes()
      await page.getByRole('region', { name: 'This week', exact: true }).getByRole('button', { name: 'Add a note', exact: true }).click()
      await quick.fill('Not submitted')
      await page.locator('.tt-board-head h2').click()
      await quick.waitFor({ state: 'detached' })
      assert.equal(await quick.count(), 0); assert.equal(writes(), beforeBlur)
      await page.waitForFunction(() => !document.querySelector('.tt-grip')?.disabled)
      const beforeDrag = await current()
      const grip = page.getByRole('button', { name: 'Drag Gather the loose ends', exact: true })
      const from = await grip.boundingBox(), target = await page.locator('[data-board-section="heading:Someday:0"] .tt-board-list-end').boundingBox()
      const start = { x: from.x + from.width / 2, y: from.y + from.height / 2 }, end = { x: target.x + target.width / 2, y: target.y + 2 }
      let touch
      if (width === 390) {
        touch = await context.newCDPSession(page)
        await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [start] })
        await pause(120); assert.equal(await page.locator('.tt-board-ghost').count(), 0, 'touch must be held')
        await pause(320)
        await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [end] })
      } else {
        await page.mouse.move(start.x, start.y); await page.mouse.down()
        await page.mouse.move(end.x, end.y, { steps: 10 })
      }
      await page.locator('.tt-board-ghost').waitFor()
      await pause(80)
      assert.equal(await page.locator('.tt-board-drop').isVisible(), true)
      assert.equal(await current(), beforeDrag, 'mid-flight is not a write')
      await shot('drag')
      if (touch) await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
      else await page.mouse.up()
      const destination = page.locator('[data-board-section="heading:Someday:0"]')
      await destination.getByRole('checkbox', { name: 'Complete Gather the loose ends', exact: true }).waitFor()
      assert.equal(await destination.getByRole('checkbox').isChecked(), true)
      assert.ok((await current()).includes('## Someday\r\n- [x] Gather the loose ends <!-- Sprout · until 2026-12-01 · future field -->\r\n'))
      await pause(260); await shot('dropped')
      const settle = await page.evaluate(() => boardSettles.at(-1))
      assert.equal(settle.duration, 220)
      assert.equal(settle.frames.at(-1).opacity, '1')
      const gesture = async (label, point, cancel = false) => {
        await page.waitForFunction(() => !document.querySelector('.tt-grip')?.disabled)
        const box = await page.getByRole('button', { name: `Drag ${label}`, exact: true }).boundingBox()
        const origin = { x: box.x + box.width / 2, y: box.y + box.height / 2 }
        if (touch) {
          await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [origin] })
          await pause(420)
          await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [point] })
        } else {
          await page.mouse.move(origin.x, origin.y); await page.mouse.down()
          await page.mouse.move(point.x, point.y, { steps: 8 })
        }
        await page.locator('.tt-board-ghost').waitFor(); await pause(40)
        if (cancel && !touch) await page.keyboard.press('Escape')
        if (touch) await touch.send('Input.dispatchTouchEvent', { type: cancel ? 'touchCancel' : 'touchEnd', touchPoints: [] })
        else await page.mouse.up()
      }
      const first = await page.getByRole('checkbox', { name: 'Complete Leave Friday open', exact: true }).boundingBox()
      const point = { x: first.x + first.width + 30, y: first.y - 4 }
      const beforeCancelDrag = await current(), cancelWrites = writes()
      await gesture('One more', point, true)
      assert.equal(await current(), beforeCancelDrag); assert.equal(writes(), cancelWrites)
      assert.equal(await page.locator('.tt-board-ghost').count(), 0)
      await gesture('One more', point)
      await page.waitForFunction(() => document.querySelector('[data-board-section="heading:Keeping close:0"] .tt-item-copy')?.textContent === 'One more')
      assert.ok((await current()).includes('- [ ] One more <!-- Sam -->\r\n- [ ] Leave Friday open <!-- Sam -->'))
      // Keyboard destination action uses exactly the same move rewrite/PUT.
      await page.getByRole('button', { name: 'Actions for Read the first draft', exact: true }).focus()
      await page.keyboard.press('Enter')
      await page.getByRole('button', { name: 'Move to…', exact: true }).press('Enter')
      await page.locator('.tt-move-choices').waitFor(); await shot('move-menu')
      await page.locator('.tt-item-menu').getByRole('button', { name: 'Keeping close', exact: true }).press('Enter')
      await page.locator('[data-board-section="heading:Keeping close:0"]').getByRole('checkbox', { name: 'Complete Read the first draft', exact: true }).waitFor()
      assert.ok((await current()).includes('- [ ] Read the first draft <!-- Quill · new -->\r\n'))
      await page.getByRole('button', { name: 'Edit board', exact: true }).click()
      await page.locator('.tt-board-editor').waitFor()
      assert.equal(await page.locator('.tt-board-head button').count(), 1)
      assert.deepEqual(await page.locator('.tt-board-head button').allTextContents(), ['Done'])
      assert.equal(await page.getByRole('button', { name: 'Close board', exact: true }).count(), 0)
      await shot('edit-after')
      const beforeCancel = await current(), beforeCancelWrites = writes()
      await page.locator('.tt-board-editor').fill('Discard this draft')
      page.once('dialog', (dialog) => dialog.accept())
      await page.getByRole('button', { name: 'Cancel', exact: true }).click()
      assert.equal(await current(), beforeCancel); assert.equal(writes(), beforeCancelWrites)
      await page.getByRole('button', { name: 'Edit board', exact: true }).click()
      await page.locator('.tt-board-editor').waitFor()
      await page.locator('.tt-board-editor').fill(beforeCancel + '\r\nA little room for the unexpected.\r\n')
      await page.getByRole('button', { name: 'Done', exact: true }).click()
      await page.locator('.tt-board-items').waitFor()
      assert.equal(await current(), beforeCancel + '\r\nA little room for the unexpected.\r\n')
      await page.getByText('A little room for the unexpected.', { exact: true }).waitFor()
      await shot('done')
      // Failed Done retains a recoverable draft and leaves the file untouched.
      const saved = await current()
      await page.getByRole('button', { name: 'Edit board', exact: true }).click()
      await page.locator('.tt-board-editor').waitFor()
      await page.locator('.tt-board-editor').fill('A recoverable draft')
      await page.route('**/api/threads/a-quieter-week/board', (route) => route.request().method() === 'PUT' ? route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: 'Board changed. Please review your draft.' }) }) : route.continue())
      await page.getByRole('button', { name: 'Done', exact: true }).click()
      await page.getByText('Board changed. Please review your draft.', { exact: true }).waitFor()
      assert.equal(await page.locator('.tt-board-editor').inputValue(), 'A recoverable draft')
      assert.equal(await current(), saved)
      await page.unroute('**/api/threads/a-quieter-week/board')
      page.once('dialog', (dialog) => dialog.accept())
      await page.getByRole('button', { name: 'Cancel', exact: true }).click()
      await page.locator('.tt-board-items').waitFor()
      // A reload reconstructs the saved file, with no browser board copy.
      await page.reload(); await page.locator('.tt-head').waitFor()
      await page.getByRole('button', { name: 'Board', exact: true }).click()
      await page.getByText('A little room for the unexpected.', { exact: true }).waitFor()
      assert.equal(await current(), saved)
      // A board with no headings has one quiet entry point at the top.
      await app.f.api('PUT', '/threads/a-quieter-week/board', { markdown: '# Board\n\n- [ ] A starting point <!-- Sam -->\n' })
      await page.getByRole('region', { name: 'Board', exact: true }).getByRole('button', { name: 'Add a note', exact: true }).waitFor()
      assert.equal(await page.locator('.tt-list-add').count(), 1)
      await page.getByRole('region', { name: 'Board', exact: true }).getByRole('button', { name: 'Add a note', exact: true }).click()
      await quick.fill('Another point'); await quick.press('Enter')
      await page.getByRole('checkbox', { name: 'Complete Another point', exact: true }).waitFor()
      assert.equal(await current(), '# Board\n\n- [ ] A starting point <!-- Sam -->\n- [ ] Another point <!-- Sam -->\n')
      await quick.press('Escape'); await quick.waitFor({ state: 'detached' })
      await page.emulateMedia({ reducedMotion: 'reduce' })
      const motionBefore = await page.evaluate(() => boardSettles.length)
      await page.getByRole('button', { name: 'Actions for A starting point', exact: true }).click()
      await page.getByRole('button', { name: 'Move to…', exact: true }).click()
      await page.locator('.tt-item-menu').getByRole('button', { name: 'Board', exact: true }).click()
      await page.waitForFunction(() => !document.querySelector('.tt-grip')?.disabled)
      assert.equal(await page.locator('.tt-board').evaluate((n) => n.getAnimations({ subtree: true }).length), 0)
      assert.equal(await page.evaluate(() => boardSettles.length), motionBefore, 'reduced motion never starts a settle animation')
      assert.deepEqual(errors, [])
      results.push({ width, theme, captures, writes: writes(), settle, assertions: 'add/Enter/repeat/focus/Escape/blur, native pointer drag across/within lists/cancel, metadata/check, keyboard move, Done/Cancel/refused save/reload, no-heading add/reduced motion, no page errors/overflow' })
      console.log(`PASS ${width} ${theme}`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
