// Real page and HTTP relay, with board writes confined to the in-memory broker.
// BOARD_SOURCE can be a private board. Keep its captures outside the repository.
import assert from 'node:assert/strict'
import { readFile, mkdir, readdir } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.BOARD_EVIDENCE
await mkdir(out, { recursive: true })
const owner = process.env.BOARD_OWNER || 'Sam'
const source = process.env.BOARD_SOURCE ? await readFile(process.env.BOARD_SOURCE, 'utf8') :
  '---\r\nproperty: "[[Quill]]"\r\n---\r\n# Board\r\n## Ideas\r\n- 2026-11-14 · Review **the draft** <!-- Sam · until 2027-01-15 -->\r\n- 2026-11-19 · **quill** · Send [[Notes|the notes]]\r\n## Later\r\n'
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture()
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 960 },
      colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    const rich = process.env.BOARD_EDITOR !== 'builtin'
    if (rich) {
      const folder = new URL('../../plugins/doc-comments/', import.meta.url)
      app.routes['GET /v1/plugins'] = (_, res) => res.end(JSON.stringify({ plugins: [{
        id: 'doc-comments', name: 'Comments', version: '1.0.0', layer: 'default', api: 0,
        effective: true, on: true, switch: 'plugin', has: { page_main: true },
      }] }))
      for (const rel of await readdir(folder, { recursive: true })) {
        if (!/^(page|assets)\/.*\.(js|css)$/.test(rel)) continue
        app.routes['GET /v1/plugins/doc-comments/files/' + rel] = async (_, res) => {
          res.setHeader('content-type', rel.endsWith('.css') ? 'text/css' : 'text/javascript')
          res.end(await readFile(new URL(rel, folder)))
        }
      }
    }
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    page.setDefaultTimeout(8000)
    const url = '/threads/general/board'
    const get = async () => (await app.f.api('GET', url)).markdown
    const seed = markdown => app.f.api('PUT', url, { markdown })
    const board = page.locator('.tt-board')
    const click = name => board.getByRole('button', { name, exact: true }).click()
    const capture = async state => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      await pause(280)
      await page.screenshot({ animations: 'disabled', path: path.join(out, `${state}-${width}-${theme}.png`) })
    }
    const open = async () => {
      await page.goto(app.url + '/threads/general')
      if (!await page.locator('.tt-head').isVisible()) {
        const general = page.getByTitle('General', { exact: true }).first()
        await Promise.race([page.locator('.tt-head').waitFor(), general.waitFor()])
        if (!await page.locator('.tt-head').isVisible()) await general.click()
      }
      await page.getByRole('button', { name: 'Board', exact: true }).click()
      await board.waitFor()
    }
    try {
      await seed(source)
      app.routes['GET /v1/owner'] = (_, res) => res.end(JSON.stringify({ id: 'you', name: owner, accepts: ['you', 'owner'] }))
      const value = { mode: theme, size: 100 }
      app.routes['GET /v1/settings/appearance'] = (_, res) => res.end(JSON.stringify({ phone: value, desktop: value }))
      app.routes['PATCH /v1/settings/appearance'] = (_, res, u, raw) => {
        Object.assign(value, Object.values(JSON.parse(raw))[0])
        res.end(JSON.stringify({ phone: value, desktop: value }))
      }
      await open()
      const first = board.locator('.tt-item-copy').first()
      await first.waitFor()
      assert.doesNotMatch(await board.innerText(), /last_modified_by:|<!--/)
      const text = await first.innerText()
      await capture('cards')
      await click('Doc')
      const word = text.trim().split(/\s+/)[0]
      let afterDoc
      if (rich) {
        const cm = board.locator('.cm-content')
        await cm.waitFor()
        assert.doesNotMatch(await cm.innerText(), /<!--|last_modified_by:|2026-11-14 · /)
        await capture('doc')
        const heading = board.locator('.cm-line').filter({ hasText: 'Ideas' }).first()
        await heading.click()
        await page.keyboard.press('End')
        const written = page.waitForResponse(r => r.request().method() === 'PUT' && r.url().endsWith('/general/board') && r.status() === 200)
        await page.keyboard.insertText(' revised')
        await written
        afterDoc = source.replace('## Ideas', '## Ideas revised')
      } else {
        await capture('doc')
        await click('Edit document')
        const doc = board.getByRole('textbox', { name: 'Edit board document' })
        const shown = await doc.inputValue()
        assert.doesNotMatch(shown, /<!--|last_modified_by:|2026-11-14 · /)
        await doc.fill(shown.replace(word, word + ' revised'))
        await click('Done')
        await doc.waitFor({ state: 'detached' })
        afterDoc = source.replace(word, word + ' revised')
      }
      assert.equal(await get(), afterDoc)
      await click('Cards')
      await first.click()
      const field = board.getByRole('textbox', { name: 'Edit item' })
      const plain = await field.inputValue()
      await field.fill(plain + ' today')
      await capture('inline')
      await field.press('Enter')
      await field.waitFor({ state: 'detached' })
      const afterCard = await get()
      assert.equal(afterCard, afterDoc.replace(/( · [^\r\n]*?)( <!--[^\r\n]*?-->)/, '$1 today$2'))
      await board.locator('.tt-item-actions > button').first().click()
      await board.getByRole('button', { name: 'Delete item' }).waitFor()
      await capture('menu')
      await page.keyboard.press('Escape')
      // Drag the first card below the second, keeping the normal touch scroll gesture.
      const rect = await first.boundingBox(), next = await board.locator('.tt-board-item').nth(1).boundingBox()
      const start = { x: rect.x + 48, y: rect.y + 14 }, end = { x: start.x, y: next.y + next.height - 4 }
      const touch = width === 390 ? await context.newCDPSession(page) : null
      if (touch) {
        await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [start] })
        await pause(440)
        await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [end] })
      } else {
        await page.mouse.move(start.x, start.y); await page.mouse.down()
        await page.mouse.move(end.x, end.y, { steps: 8 })
      }
      await board.locator('.tt-board-ghost').waitFor()
      await capture('drag')
      if (touch) await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
      else await page.mouse.up()
      await page.waitForFunction(t => document.querySelector('.tt-item-copy')?.innerText !== t, plain + ' today')
      await board.locator('.tt-item-actions > button:not(:disabled)').first().waitFor()
      // Agent writes while a card is open. The real page receives HTTP 409.
      await first.click()
      await field.fill((await field.inputValue()) + ' with my edit')
      const prior = await get()
      const agent = '- 2026-11-20 · **quill** · Check the latest draft'
      const afterAgent = prior.replace(/^([-*] )/m, (_, mark) => agent + (prior.includes('\r\n') ? '\r\n' : '\n') + mark)
      await seed(afterAgent)
      const rejected = page.waitForResponse(r => r.url().endsWith('/general/board') && r.status() === 409)
      await field.press('Enter')
      await rejected
      await board.getByRole('button', { name: 'Reapply my edit' }).waitFor()
      await board.getByText('Check the latest draft', { exact: true }).waitFor().catch(async e => { if (!process.env.BOARD_SOURCE) console.error(JSON.stringify({ live: await get(), visible: await board.innerText() })); throw e })
      await capture('conflict')
      assert.equal(await get(), afterAgent)
      await click('Reapply my edit')
      await board.locator('.tt-recovery').waitFor({ state: 'detached' })
      const merged = await get()
      assert.ok(merged.includes(agent) && merged.includes(' with my edit'))
      // The menu works for an agent's dated line with no metadata comment.
      const item = board.locator('.tt-board-item').filter({ hasText: 'Check the latest draft' })
      await item.locator('.tt-item-actions > button').click()
      await click('Set expiry')
      await board.locator('input[type=date]').fill('2027-01-30')
      await item.getByText(/expires Jan 30/).waitFor()
      await item.locator('.tt-item-actions > button').click()
      await click('Clear expiry')
      await item.getByText(/expires Jan 30/).waitFor({ state: 'detached' })
      await item.locator('.tt-item-actions > button').click()
      await click('Move to…')
      const dest = board.locator('.tt-board-list').last()
      const name = await dest.getAttribute('aria-label')
      await click(name)
      await dest.getByText('Check the latest draft', { exact: true }).waitFor()
      if (rich) {
        await click('Doc')
        const heading = board.locator('.cm-line').filter({ hasText: 'Ideas revised' }).first()
        await heading.click()
        await page.keyboard.press('End')
        const rejected = page.waitForResponse(r => r.request().method() === 'PUT' && r.url().endsWith('/general/board') && r.status() === 409)
        await page.keyboard.insertText(' together')
        const before = await get()
        const addition = '- An independent idea <!-- Quill -->'
        await seed(before + '\n' + addition)
        await rejected
        await board.getByRole('button', { name: 'Reapply my edit' }).waitFor()
        // Closing and reopening the document still offers the uncommitted edit.
        await page.reload()
        await click('Reapply my edit')
        await board.locator('.tt-recovery').waitFor({ state: 'detached' })
        const recovered = await get()
        assert.equal(recovered, before.replace('## Ideas revised', '## Ideas revised together') + '\n' + addition)
        await click('Cards')
      }
      await seed('# New thread — board\n\n---\n')
      await board.getByText('Room for your first idea').waitFor()
      await capture('empty')
      assert.equal(await board.locator('.tt-list-add').count(), 1)
      assert.deepEqual(errors, [])
      console.log(`${width} ${theme}: Cards, Doc, inline save, menu, drag, 409 recovery and empty board passed`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
