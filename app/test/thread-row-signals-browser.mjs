// Real local page/relay. Synthetic threads and Classic records only.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile, readdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
const out = process.env.THREAD_ROW_EVIDENCE
if (!out) throw new Error('Set THREAD_ROW_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] })
const baseline = 'd3b20dd', id = 'a-quieter-week', results = [], classic = new Map()
const old = (file) => execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' })
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) for (const phase of ['before', 'after']) {
    const app = await oneProductFixture(), errors = []
    const original = await app.f.api('GET', `/threads/${id}`)
    const member = (persona, extra = {}) => ({ persona, engine: 'claude-code', working: false, background: 0, ...extra })
    const row = (key, title, members, extra = {}) => ({ ...original.thread, id: key, title, members, default: members[0]?.persona ?? null,
      pinned: false, unread: 0, icon: null, working: members.filter((m) => m.working).length, background: members.reduce((n, m) => n + m.background, 0),
      last: { author: members[0]?.persona, text: 'The latest message belongs inside the conversation.', at: '2026-11-11T04:54:00Z' }, ...extra })
    const rows = [
      row('general', 'General', [member('sprout'), member('quill')], { pinned: true, general: true, unread: 2 }),
      row(id, 'A little room to think', [member('sprout', { working: true }), member('quill')], { icon: 'glyph:chat' }),
      row('waiting', 'Checking the loose ends', [member('scout', { background: 2 })]),
      row('reply', 'A fresh perspective', [member('quill')], { unread: 3 }),
      row('quiet', 'Room for the unexpected', [member('ledger')]),
      row('asking', 'Before the next step', [member('pip', { working: true, asking: true })]),
    ]
    const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: rows, agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'threads.snapshot', threads: rows, agents: app.f.agents })}\n\n`) }
    const headerMembers = [member('sprout', { background: 2, background_tasks: [{ id: 'one', description: 'Review the wording', started_at: '2026-11-11T04:50:00Z' }, { id: 'two', description: 'Check the next steps', started_at: '2026-11-11T04:51:00Z' }] }), member('quill')]
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...original, thread: { ...rows[1], members: headerMembers }, turn: null, turns: [] })
    app.routes[`PATCH /v1/threads/${id}`] = (req, res) => json(res, { ...rows[1], members: headerMembers })
    app.records.forEach((r, i) => Object.assign(r, { status: ['working', 'waiting', 'done', 'idle'][i], record_seq: 7, seen_seq: 0, background_tasks: i < 2 ? 2 : 0,
      last_event_at: '2026-11-11T04:54:00Z', last_activity: '2026-11-11T04:54:00Z' }))
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    if (phase === 'before') for (const file of ['threads.js', 'thread-identity.js']) await context.route(`**/${file}`, (route) => route.fulfill({ contentType: 'text/javascript', body: old(file) }))
    const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__rows = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', (e) => errors.push(e.message))
    await page.clock.install({ time: new Date('2026-11-11T04:55:00Z') })
    const settle = async () => { await page.clock.runFor(800); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }) }
    const shot = async (label) => { await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(out, `${phase}-${label}-${width}-${theme}.png`), animations: 'disabled' }) }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await settle()
      const heights = await page.locator('.tt-row').evaluateAll((nodes) => nodes.map((n) => ({ title: n.title, height: n.getBoundingClientRect().height })))
      await shot('list')
      await page.locator('.tt-list .scroll').evaluate((n) => { n.scrollTop = n.scrollHeight })
      await shot('list-end')
      const quiet = await page.getByTitle('Room for the unexpected', { exact: true }).boundingBox()
      assert.ok(quiet.y >= 0 && quiet.y + quiet.height < (width === 390 ? 770 : 1000), 'the lower list capture includes the quiet thread')
      await page.locator('.tt-list .scroll').evaluate((n) => { n.scrollTop = 0 })
      assert.equal(await page.locator('.pinned-card .tt-row').getAttribute('title'), 'General')
      assert.equal(await page.locator('.tt-row .tt-unread').count(), 2)
      if (phase === 'before') {
        assert.equal(await page.locator('.tt-row .box').count(), 4)
        assert.equal(await page.locator('.tt-row .beating').count(), 2)
        assert.equal(await page.locator('.tt-row .srow-bg').count(), 1)
      } else {
        assert.equal(await page.locator('.tt-row .box, .tt-row .box-text, .tt-row .beating, .tt-row .srow-bg, .tt-row .ring, .tt-row .breathe').count(), 0)
        assert.equal(await page.locator('.tt-row time').count(), 6)
        assert.equal(await page.locator('.tt-row .tt-default-face').count(), 2)
        assert.deepEqual(await page.locator('.tt-row .srow-name').allTextContents(), ['Sprout replied', 'Sprout working', 'Scout waiting', 'Quill replied', 'Quiet', 'Pip is asking you'])
        assert.ok(heights.every((r) => r.height >= 68 && r.height <= 84))
        for (const node of await page.locator('.tt-row .tt-unread').all()) assert.equal(await node.evaluate((n) => getComputedStyle(n, '::after').width), '6px')
        const first = heights[0].height
        assert.ok(heights.every((r) => Math.abs(r.height - first) < 1), 'consistent row density')
        // Activity does not add a row tint; selection has its own independent surface.
        assert.equal(await page.getByTitle('A little room to think', { exact: true }).evaluate((n) => getComputedStyle(n).backgroundColor), await page.getByTitle('Room for the unexpected', { exact: true }).evaluate((n) => getComputedStyle(n).backgroundColor))
      }
      // Header retains its only task-list entry point, including named tasks.
      await page.evaluate((id) => __rows.threads.open(id), id)
      await page.waitForFunction(() => !__rows.threads.st.open.loading && !__rows.threads.st.open.refreshing)
      await page.getByRole('button', { name: '2 background tasks', exact: true }).waitFor()
      await shot('header')
      await page.getByRole('button', { name: '2 background tasks', exact: true }).click()
      await page.locator('.tt-task-row').first().waitFor(); assert.equal(await page.locator('.tt-task-row').count(), 2)
      assert.match(await page.locator('.tt-tasks-panel').innerText(), /Review the wording/)
      await page.keyboard.press('Escape'); await page.locator('.tt-tasks-panel').waitFor({ state: 'hidden' })
      assert.deepEqual(errors, [])
      results.push({ width, theme, phase, heights, headerTasks: true, errors })
      console.log(`PASS ${phase} ${width} ${theme}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ baseline, fixturesOnly: true, results }, null, 2) + '\n')
  const hashes = []
  for (const file of (await readdir(out)).filter((f) => f.endsWith('.png')).sort()) hashes.push({ file, sha256: createHash('sha256').update(await readFile(path.join(out, file))).digest('hex') })
  await writeFile(path.join(out, 'captures.json'), JSON.stringify(hashes, null, 2) + '\n')
} finally { await browser.close() }
