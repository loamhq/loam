// General leads the pinned threads (2026-11-18), in a real browser against a
// stub runtime: no vault, no agents and no real threads are touched. The stub
// answers in the runtime's old order (newest pin first) so the page's own sort
// is what is seen; then reload, then live activity in another pinned thread.
//   THREADS_EVIDENCE=<dir> LOAM_PLAYWRIGHT=<playwright> LOAM_CHROME=<chrome> node test/general-pinned-first-browser.mjs
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir } from 'node:fs/promises'
import { createThreadFixture } from '../public/threads-fixture.js'
import { fakeBroker, appAgainst } from './harness.js'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.THREADS_EVIDENCE
if (!out) throw new Error('Set THREADS_EVIDENCE to the evidence directory')
await mkdir(out, { recursive: true })

const f = createThreadFixture()
const template = (await f.api('GET', '/threads')).threads.find((t) => t.id === 'general')
const row = (id, title, at, pinned) => ({ ...structuredClone(template), id, title, pinned, unread: 0, board_new: 0,
  general: id === 'general', last: { ...template.last, at }, created: at, members: [structuredClone(template.members[0])] })
const minutes = (n) => new Date(Date.now() - n * 60000).toISOString()
let rows = [row('newest-pin', 'Newest pinned', minutes(1), true), row('plain-general', 'General', minutes(2), false),
  row('pin-b', 'Pinned two', minutes(30), true), row('pin-c', 'Pinned three', minutes(60), true),
  row('general', 'General', minutes(600), true), row('plain', 'An ordinary thread', minutes(90), false)]

const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const streams = new Set()
const routes = {
  'GET /v1/agents': (req, res) => json(res, { agents: f.agents }),
  'GET /v1/providers': (req, res) => json(res, { providers: f.providers }),
  'GET /v1/sessions': (req, res) => json(res, { sessions: [], live: 0, cap: 2 }),
  'GET /v1/threads': (req, res) => json(res, { threads: structuredClone(rows), agents: f.agents }),
  'PATCH /v1/threads/plain': (req, res, url, raw) => {
    const thread = rows.find(t => t.id === 'plain')
    thread.pinned = JSON.parse(raw).pinned
    json(res, thread)
  },
  'GET /v1/threads/events': (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'threads.snapshot', threads: structuredClone(rows), agents: f.agents })}\n\n`)
    streams.add(res); res.on('close', () => streams.delete(res))
  },
}
const broker = await fakeBroker(routes), app = await appAgainst(broker, { LOG_LEVEL: 'off' })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
const order = (page) => page.evaluate(() => [...document.querySelectorAll('.tt-section')].map((s) =>
  [...s.querySelectorAll('.tt-row')].map((r) => r.querySelector('.srow-title')?.textContent.trim())))
const results = []
try {
  for (const width of [390, 1440]) {
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, isMobile: width < 1100, hasTouch: width < 1100 })
    const page = await context.newPage(), errors = []
    page.on('pageerror', (e) => errors.push(e.message))
    await page.goto(app.url)
    await page.locator('.tt-row').filter({ hasText: 'Pinned three' }).waitFor()
    const expected = [['General', 'Newest pinned', 'Pinned two', 'Pinned three'], ['General', 'An ordinary thread']]
    assert.deepEqual(await order(page), expected, `${width}: first load`)
    await page.screenshot({ path: path.join(out, `general-first-${width}.png`) })
    await page.reload()
    await page.locator('.tt-row').filter({ hasText: 'Pinned three' }).waitFor()
    assert.deepEqual(await order(page), expected, `${width}: after reload`)
    // Live: the oldest pinned thread gets a reply; it rises, General stays first.
    const changed = { ...rows.find((t) => t.id === 'pin-c'), last: { ...template.last, at: new Date().toISOString() } }
    for (const res of streams) res.write(`data: ${JSON.stringify({ type: 'thread.changed', thread: 'pin-c', summary: changed })}\n\n`)
    await page.waitForFunction(() => document.querySelectorAll('.tt-section')[0]?.querySelectorAll('.tt-row')[1]?.querySelector('.srow-title')?.textContent === 'Pinned three')
    const live = await order(page)
    assert.deepEqual(live, [['General', 'Pinned three', 'Newest pinned', 'Pinned two'], ['General', 'An ordinary thread']], `${width}: after live activity`)
    await page.screenshot({ path: path.join(out, `general-first-live-${width}.png`) })
    const plain = page.locator('[data-pin-row="plain"]')
    for (const pinned of [true, false]) {
      const action = pinned ? 'Pin thread' : 'Unpin thread'
      if (width === 390) {
        await plain.locator('.tt-row').dispatchEvent('pointerdown', { pointerType: 'touch', clientX: 20, clientY: 20 })
        await page.getByRole('menuitem', { name: action, exact: true }).click()
      } else {
        await plain.locator('.tt-row-pin').focus()
        await plain.locator('.tt-row-pin').press('Enter')
      }
      await page.waitForFunction(want => {
        const group = document.querySelector('[data-pin-row="plain"]')?.closest('.tt-section')
        return group?.previousElementSibling?.textContent === (want ? 'Pinned' : 'Recent')
      }, pinned)
      assert.equal(rows.find(t => t.id === 'plain').pinned, pinned)
    }
    assert.equal(await page.locator('[data-pin-row="general"]').count(), 0)
    assert.deepEqual(errors, [])
    results.push({ width, load: expected, live })
    await context.close()
  }
  console.log(JSON.stringify(results, null, 1))
} finally {
  await browser.close(); app.server.closeAllConnections?.(); broker.server.closeAllConnections?.()
}
process.exit(0)
