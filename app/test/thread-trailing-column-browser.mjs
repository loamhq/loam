// Real page/relay with local contract fixtures; no host sessions or credentials.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const output = process.env.THREAD_TRAILING_EVIDENCE
if (output) await mkdir(output, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8'), results = []
try {
  for (const width of [320, 390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture()
    const original = (await app.f.api('GET', '/threads/a-quieter-week')).thread
    const rows = [false, true].flatMap(archived => [true, false].flatMap(pinned => [0, 2].map(unread => ({
      ...original, id: `${archived}-${pinned}-${unread}`, title: `${pinned ? 'Pinned' : 'Recent'} ${unread ? 'unread' : 'read'}`,
      archived, pinned, unread, default: 'sprout', icon: null, general: false,
      members: (unread ? ['sprout', 'quill'] : ['sprout']).map(persona => ({ persona, engine: 'claude-code', working: false, background: 0 })),
      last: { author: 'sprout', text: 'An answer', at: pinned ? '2026-11-12T20:00:00Z' : '2025-01-01T20:00:00Z' },
    }))))
    const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: rows, agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'threads.snapshot', threads: rows, agents: app.f.agents })}\n\n`)
    }
    const context = await browser.newContext({ viewport: { width, height: width < 1100 ? 844 : 1000 }, colorScheme: theme, reducedMotion: 'reduce', isMobile: width < 1100, hasTouch: width < 1100 })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__trailing = { threads, schedule };' }))
    const page = await context.newPage(), errors = [], checks = []
    page.on('pageerror', error => errors.push(error.message))
    page.setDefaultTimeout(10000)
    const geometry = () => page.locator('.tt-row').evaluateAll(nodes => nodes.map(node => {
      const rect = n => { const r = n.getBoundingClientRect(); return { top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height, cx: r.x + r.width / 2, cy: r.y + r.height / 2 } }
      const row = rect(node), time = rect(node.querySelector('time')), slot = rect(node.querySelector('.tt-row-notification'))
      const range = document.createRange(); range.selectNodeContents(node.querySelector('time'))
      const text = rect(range), dot = node.querySelector('.tt-unread'), subtitle = node.querySelector('.srow-line')
      return { title: node.title, row, time, slot, textBaseline: text.bottom - row.top, dot: dot ? rect(dot) : null, subtitle: subtitle ? rect(subtitle) : null }
    }))
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await page.evaluate(() => document.fonts.ready)
      for (const archived of [false, true]) {
        if (archived) {
          await page.getByRole('button', { name: 'Thread list actions', exact: true }).click()
          await page.getByText('Archived threads', { exact: true }).click()
          await page.getByRole('heading', { name: 'Archived threads', exact: true }).waitFor()
        }
        const heading = await page.locator('.tt-list .home-head, .tt-list .list-head').evaluate(node => {
          const rect = n => { const r = n.getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height } }
          const range = document.createRange(); range.selectNodeContents(node.querySelector('.display'))
          return { title: rect(range), bounds: rect(node), controls: [...node.querySelectorAll('button')].map(rect) }
        })
        assert.ok(heading.title.left >= heading.bounds.left && heading.title.right <= heading.bounds.right, 'list title stays inside its header')
        for (const control of heading.controls) {
          assert.ok(control.width >= 44 && control.height >= 44, 'header actions keep their hit area')
          assert.ok(heading.title.right <= control.left || heading.title.left >= control.right || heading.title.bottom <= control.top || heading.title.top >= control.bottom,
            `list title must not overlap an action: ${JSON.stringify(heading)}`)
        }
        if (archived) {
          assert.equal(await page.locator('.tt-archived-head button').count(), 2)
          assert.equal(await page.locator('.tt-personas').count(), 0)
          assert.ok(heading.controls.every(control => Math.abs(control.top - heading.controls[0].top) < 1), 'archive controls stay on one row')
        }
        assert.equal(await page.locator('.tt-row').count(), 4)
        assert.equal(await page.locator('.tt-default-face').count(), 0)
        assert.equal(await page.getByRole('button', { name: /Default: Sprout/ }).count(), 4)
        assert.equal(await page.locator('.tt-row .avatar-wrap > .blob').count(), 2, 'solo rows keep their face')
        assert.equal(await page.locator('.tt-row .avatar-wrap .thread-tile').count(), 2, 'groups keep their icon')
        const before = await geometry()
        for (const row of before) {
          assert.ok(Math.abs(row.time.right - before[0].time.right) < 1, 'times share a right edge')
          assert.ok(Math.abs(row.textBaseline - before[0].textBaseline) < 1, 'time baselines share a row offset')
          assert.ok(Math.abs(row.row.height - before[0].row.height) < 1, 'read and unread row heights match')
          assert.ok(row.slot.top >= row.time.bottom, 'notification slot is below time')
          if (row.dot) {
            assert.ok(Math.abs(row.dot.right - row.time.right) < 1, 'dot follows the time right edge')
            assert.ok(Math.abs(row.dot.cy - row.subtitle.cy) < 1, 'dot aligns with secondary text line')
            assert.equal(row.dot.width, 6)
          }
        }
        // Changing unread on the same rows cannot move their time or change height.
        await page.evaluate(() => { for (const row of __trailing.threads.st.threads) row.unread = row.unread ? 0 : 2; __trailing.schedule() })
        await page.waitForFunction(() => [...document.querySelectorAll('.tt-row')].filter(n => n.title.endsWith('read') && !n.title.endsWith('unread')).every(n => n.querySelector('.tt-unread')))
        const after = await geometry()
        assert.deepEqual(after.map(r => [r.time, r.row.height]), before.map(r => [r.time, r.row.height]))
        await page.evaluate(() => { for (const row of __trailing.threads.st.threads) row.unread = row.unread ? 0 : 2; __trailing.schedule() })
        await page.waitForFunction(() => [...document.querySelectorAll('.tt-row')].filter(n => n.title.endsWith('unread')).every(n => n.querySelector('.tt-unread')))
        if (output) await page.screenshot({ path: path.join(output, `${archived ? 'archived' : 'chats'}-${width}-${theme}.png`), animations: 'disabled' })
        checks.push({ archived, heading, geometry: before, stableOnUnreadToggle: true })
      }
      assert.deepEqual(errors, [])
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      results.push({ width, theme, checks, errors })
      console.log(`PASS ${width} ${theme}: pinned/recent/archive alignment, stable unread toggle, solo/group icons, accessible default`)
    } finally { await context.close(); await app.close() }
  }
  if (output) await writeFile(path.join(output, 'geometry.json'), JSON.stringify(results, null, 2) + '\n')
} finally { await browser.close() }
