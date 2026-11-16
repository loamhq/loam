// Local page and relay, synthetic broker only. Baseline assets come from git.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
const out = process.env.THREAD_SELECTION_EVIDENCE
if (!out) throw new Error('Set THREAD_SELECTION_EVIDENCE')
await mkdir(out, { recursive: true })
const baseline = process.env.THREAD_SELECTION_BASE || '48b2592'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8'), results = [], classics = new Map()
try {
  for (const width of [1440, 390]) for (const theme of ['light', 'dark']) for (const phase of ['before', 'after']) {
    const app = await oneProductFixture(), errors = []
    const original = await app.f.api('GET', '/threads/a-quieter-week')
    original.messages.forEach((m) => { m.at = '2026-11-11T19:29:00Z' })
    // Give Classic deterministic ordering as well as dates: fixture creation
    // can otherwise straddle milliseconds and change the order of tied rows.
    app.records.forEach((r, i) => { r.last_activity = r.last_event_at = r.created = new Date(Date.UTC(2026, 10, 11, 19, 30 - i)).toISOString() })
    const member = (persona, extra = {}) => ({ persona, engine: 'claude-code', working: false, background: 0, ...extra })
    const rows = [
      { id: 'a-quieter-week', title: 'A little room to think', pinned: true, members: [member('sprout'), member('quill')], unread: 1 },
      { id: 'solo', title: 'A thoughtful plan for the coming season', pinned: true, members: [member('quill')] },
      { id: 'general', title: 'General', pinned: true, general: true, members: [member('sprout'), member('scout')] },
      { id: 'review', title: 'Before the next step', members: [member('scout', { asking: true, working: true }), member('sprout')] },
      { id: 'working', title: 'Gather the loose ends', members: [member('ledger', { working: true })] },
      { id: 'waiting', title: 'Leave room for the unexpected', members: [member('pip', { background: 2 })] },
    ].map((r) => ({ ...original.thread, icon: null, pinned: false, unread: 0, ...r, default: r.members[0].persona,
      working: r.members.filter((m) => m.working).length, background: r.members.reduce((n, m) => n + m.background, 0),
      last: { author: r.members[0].persona, at: '2026-11-11T19:30:00Z', text: 'A useful next step.' } }))
    const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: rows, agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'threads.snapshot', threads: rows, agents: app.f.agents })}\n\n`) }
    for (const row of rows) {
      app.routes[`GET /v1/threads/${row.id}`] = (req, res) => json(res, { ...original, thread: row, turns: [], turn: null })
      app.routes[`PATCH /v1/threads/${row.id}`] = (req, res) => json(res, row)
      app.routes[`GET /v1/threads/${row.id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    if (phase === 'before') for (const file of ['threads.js', 'threads.css', 'thread-selection.js']) await context.route(`**/${file}`, (route) => route.fulfill({ contentType: file.endsWith('css') ? 'text/css' : 'text/javascript', body: execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' }) }))
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__selection = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', (e) => errors.push(e.message))
    page.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused to/.test(m.text())) errors.push(m.text()) })
    await page.clock.install({ time: new Date('2026-11-11T19:31:00Z') })
    // JS time drives the phone's one-second lifetime; transitions use real
    // compositor time. Keep their settling separate so captures are stable.
    const settle = async (ms = 350) => { await page.clock.runFor(ms); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }); await new Promise((r) => setTimeout(r, 480)) }
    const shot = async (label) => { assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(out, `${phase}-${label}-${width}-${theme}.png`) }) }
    const row = (id) => page.locator(`.tt-row[title="${rows.find((r) => r.id === id).title}"]`)
    const styles = (node) => node.evaluate((n) => { const s = getComputedStyle(n), p = getComputedStyle(n, '::before'); return { background: s.backgroundColor, outline: s.outlineStyle, outlineWidth: s.outlineWidth, shadow: s.boxShadow, radius: s.borderRadius, height: n.getBoundingClientRect().height,
      weight: getComputedStyle(n.querySelector('.srow-title')).fontWeight, marker: p.content, focus: n.matches(':focus-visible') } })
    const geometry = () => page.evaluate(() => {
      const rule = document.querySelector('.tt-selection'), row = document.querySelector('.tt-row.on')
      if (!row) return { opacity: getComputedStyle(rule).opacity }
      const rect = (n) => { const r = n.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, height: r.height, width: r.width } }
      const s = getComputedStyle(rule)
      return { rule: rect(rule), row: rect(row), column: rect(row.querySelector('.srow-who')), opacity: s.opacity, duration: s.transitionDuration,
        padding: parseFloat(getComputedStyle(row).paddingRight), thickness: s.height, radius: s.borderRadius, ink: s.backgroundColor,
        weight: getComputedStyle(row.querySelector('.srow-title')).fontWeight, scale: Number(document.documentElement.style.getPropertyValue('--ui-scale')) || 1 }
    })
    const aligned = (g) => {
      const near = (a, b) => assert.ok(Math.abs(a - b) < .15, `${a} differs from ${b}`)
      assert.equal(g.opacity, '1'); near(Number.parseFloat(g.thickness), 1.5); near(Number.parseFloat(g.radius), 1)
      near(g.rule.left, g.column.left); near(g.rule.right, g.column.right)
      near(g.rule.bottom, g.column.bottom + 7 * g.scale)
      assert.equal(g.weight, '650')
    }
    const open = async (id) => { await row(id).click(); await page.waitForFunction(() => !__selection.threads.st.open.loading && !__selection.threads.st.open.refreshing); await page.mouse.move(1200, 900); await settle() }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await settle()
      await open('a-quieter-week')
      const readings = {}
      if (width === 1440) {
        readings.pinned = await styles(row('a-quieter-week')); await shot('pinned-selected')
        await open('review'); readings.recent = await styles(row('review')); await shot('recent-selected')
        await row('solo').hover(); await settle(); readings.hover = await styles(row('solo')); await shot('hover')
        await page.mouse.move(1200, 900)
        await row('a-quieter-week').focus(); await page.keyboard.press('Tab'); await settle()
        readings.focus = await styles(row('solo')); await shot('keyboard-focus')
        if (phase === 'after') {
          assert.equal(await page.locator('.tt-list .pinned-card').count(), 0)
          assert.deepEqual(await page.locator('.tt-section-heading').allTextContents(), ['Pinned', 'Recent'])
          readings.sections = await page.locator('.tt-section').evaluateAll((nodes) => nodes.map((n) => { const s = getComputedStyle(n); return { background: s.backgroundColor, radius: s.borderRadius, shadow: s.boxShadow, padding: s.padding } }))
          assert.deepEqual(readings.sections[0], readings.sections[1])
          assert.equal(readings.sections[0].background, 'rgba(0, 0, 0, 0)'); assert.equal(readings.sections[0].radius, '0px'); assert.equal(readings.sections[0].shadow, 'none')
          assert.equal(await page.locator('.tt-selection').count(), 1, 'one rule serves both sections')
          assert.equal(readings.pinned.marker, 'none'); assert.equal(readings.recent.marker, 'none')
          assert.equal(readings.pinned.background, 'rgba(0, 0, 0, 0)'); assert.equal(readings.recent.background, 'rgba(0, 0, 0, 0)')
          assert.equal(readings.pinned.weight, '650'); assert.equal(readings.recent.weight, '650'); assert.equal(readings.hover.weight, '500')
          assert.notEqual(readings.hover.background, readings.recent.background)
          assert.equal(readings.focus.focus, true); assert.equal(readings.focus.outline, 'solid'); assert.equal(readings.focus.outlineWidth, '2px')
          assert.equal(readings.focus.shadow, 'none'); assert.equal(readings.focus.marker, 'none')
          assert.notEqual(readings.focus.background, readings.hover.background)
          assert.equal(await row('review').getAttribute('aria-current'), 'page')
          assert.equal(await row('review').locator('[data-icon-state="asking"]').count(), 1)
          assert.equal(await row('a-quieter-week').locator('.tt-unread').count(), 1)
          assert.equal(await row('a-quieter-week').locator('.tt-default-face').count(), 1)
          assert.equal(await row('solo').locator('.srow-title').evaluate((n) => getComputedStyle(n).webkitLineClamp), '2')
          await row('review').focus(); await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Tab'); await settle()
          assert.equal((await styles(row('review'))).outlineWidth, '2px', 'focus and selection coexist')
          // Quiet two-line and active subtitle geometry, plus CSS zoom.
          readings.geometry = []
          for (const scale of [1, .9, 1.25]) {
            await page.evaluate((scale) => { document.documentElement.style.setProperty('--ui-scale', scale) }, scale)
            for (const id of ['solo', 'review']) { await open(id); const g = await geometry(); aligned(g); readings.geometry.push({ id, ...g }) }
          }
          await page.evaluate(() => document.documentElement.style.setProperty('--ui-scale', 1))
          await open('review')
          await page.evaluate(() => { globalThis.__rule = document.querySelector('.tt-selection') })
          // Reordering and live redraw keep the same rule, with new geometry.
          await page.evaluate(() => { const t = __selection.threads.st.threads.find((t) => t.id === 'review'); t.pinned = true; __selection.schedule() })
          await settle(); aligned(await geometry())
          assert.equal(await page.evaluate(() => document.querySelector('.tt-selection') === __rule), true)
          await page.evaluate(() => { __selection.threads.st.query = 'no match'; __selection.schedule() }); await settle()
          assert.equal((await geometry()).opacity, '0')
          await page.evaluate(() => { __selection.threads.st.query = ''; __selection.schedule() }); await settle(); aligned(await geometry())
        }
      } else {
        await page.getByRole('button', { name: 'Back to threads', exact: true }).click(); await settle(150)
        readings.returning = await styles(row('a-quieter-week')); await shot('pinned-selected')
        if (phase === 'after') { readings.phonePinned = await geometry(); aligned(readings.phonePinned) }
        await settle(1200); readings.faded = await styles(row('a-quieter-week')); await shot('returned')
        if (phase === 'after') {
          assert.equal((await geometry()).opacity, '0')
          assert.equal(readings.returning.weight, '650'); assert.equal(readings.faded.weight, '500')
          assert.equal(readings.returning.background, readings.faded.background)
          assert.equal(await page.locator('.tt-row.on').count(), 0)
          // Another thread's return owns its own short lifetime.
          await open('review'); await page.goBack(); await page.locator('.tt-list').waitFor(); await settle(150)
          assert.equal(await row('review').evaluate((n) => n.classList.contains('on')), true)
          assert.equal(await row('a-quieter-week').evaluate((n) => n.classList.contains('on')), false)
          readings.phoneRecent = await geometry(); aligned(readings.phoneRecent); await shot('recent-selected')
        }
        if (phase === 'before') { await open('review'); await page.goBack(); await page.locator('.tt-list').waitFor(); await settle(150); await shot('recent-selected') }
      }
      if (phase === 'after') {
        readings.font = await page.evaluate(() => [...document.fonts].filter((f) => f.family.includes('Thread Figtree')).map((f) => ({ weight: f.weight, status: f.status })))
        assert.ok(readings.font.some((f) => f.weight === '300 900' && f.status === 'loaded'))
        await page.emulateMedia({ reducedMotion: 'reduce' })
        assert.equal(await row('solo').evaluate((n) => getComputedStyle(n).transitionDuration), '0s')
        assert.equal(await row('solo').locator('.srow-title').evaluate((n) => getComputedStyle(n).transitionDuration), '0s')
        assert.equal(await page.locator('.tt-selection').evaluate((n) => getComputedStyle(n).transitionDuration), '0s')
        if (width === 1440) { await open('solo'); aligned(await geometry()); await open('review'); aligned(await geometry()) }
      }
      assert.deepEqual(errors, []); results.push({ width, theme, phase, readings, errors }); console.log(`PASS ${phase} ${width} ${theme}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ baseline, fixturesOnly: true, browser: browser.version(), results }, null, 2) + '\n')
} finally { await browser.close() }
