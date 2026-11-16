// Real page/relay geometry, synthetic records; optional external Playwright.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_CLEARANCE_EVIDENCE
if (!out) throw new Error('Set THREAD_CLEARANCE_EVIDENCE')
await mkdir(out, { recursive: true })
const baseline = process.env.THREAD_CLEARANCE_BASE, phase = baseline ? 'before' : 'after'
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const results = [], near = (a, b) => assert.ok(Math.abs(a - b) < .2, `${a} differs from ${b}`)
const overlap = (a, b) => a.width > .01 && a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top
try {
  for (const width of [1440, 390]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = [], checks = []
    const original = await app.f.api('GET', '/threads/a-quieter-week')
    original.messages.forEach((m) => { m.at = '2026-11-11T20:45:00Z' })
    const member = (persona, working = false) => ({ persona, engine: 'claude-code', working, background: 0 })
    const rows = [
      { id: 'a-quieter-week', title: 'A little room for the week', members: [member('sprout', true), member('quill')] },
      { id: 'small-details', title: 'A useful next step', members: [member('quill', true)] },
      { id: 'weekend', title: 'A thoughtful plan for the coming season', members: [member('scout')] },
    ].map((t) => ({ ...original.thread, ...t, default: t.members[0].persona, icon: null, pinned: false, unread: 0,
      last: { author: t.members[0].persona, at: '2026-11-11T20:45:00Z', text: 'A useful next step.' } }))
    const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: rows, agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
    for (const row of rows) {
      app.routes[`GET /v1/threads/${row.id}`] = (req, res) => json(res, { ...original, thread: row, turns: [], turn: null })
      app.routes[`PATCH /v1/threads/${row.id}`] = (req, res) => json(res, row)
      app.routes[`GET /v1/threads/${row.id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    if (baseline) await context.route('**/thread-selection.js', (route) => route.fulfill({ contentType: 'text/javascript', body: execFileSync('git', ['show', `${baseline}:public/thread-selection.js`], { encoding: 'utf8' }) }))
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__clearance = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', (e) => errors.push(e.message))
    await page.clock.install({ time: new Date('2026-11-11T20:46:00Z') })
    const settle = async () => {
      await page.clock.runFor(100)
      await page.evaluate(() => document.fonts.ready)
      await new Promise((r) => setTimeout(r, 460))
    }
    const geometry = () => page.evaluate(() => {
      const row = document.querySelector('.tt-row.on'), rule = document.querySelector('.tt-selection')
      const rect = (n) => { const r = n.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height } }
      return { rule: rect(rule), column: rect(row.querySelector('.srow-who')), meta: rect(row.querySelector('.srow-meta')),
        face: row.querySelector('.tt-default-face') ? rect(row.querySelector('.tt-default-face')) : null,
        avatar: rect(row.querySelector('.avatar-wrap')), title: rect(row.querySelector('.srow-title')),
        lineHeight: parseFloat(getComputedStyle(row.querySelector('.srow-title')).lineHeight),
        subtitle: row.querySelector('.srow-name')?.textContent ?? null,
        opacity: getComputedStyle(rule).opacity,
        scale: Number(document.documentElement.style.getPropertyValue('--ui-scale')) || 1 }
    })
    const aligned = (g) => {
      near(g.rule.left, g.column.left); near(g.rule.right, g.column.right)
      near(g.rule.bottom, g.column.bottom + 7 * g.scale)
      near(g.column.left - g.avatar.right, g.meta.left - g.rule.right)
      assert.ok(g.rule.right < g.meta.left, 'rule ends before the entire right column')
      if (g.face) {
        assert.equal(overlap(g.rule, g.face), false, 'rule box never overlaps the corner face box')
        assert.ok(g.rule.right < g.face.left, 'rule also stays clear of the face horizontally')
      }
      assert.equal(g.opacity, '1')
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await settle()
      for (const [index, row] of rows.entries()) {
        await page.locator(`.tt-row[title="${row.title}"]`).click()
        await page.mouse.move(0, 0)
        await page.waitForFunction(() => !__clearance.threads.st.open.loading && !__clearance.threads.st.open.refreshing)
        if (width === 390) await page.getByRole('button', { name: 'Back to threads', exact: true }).click()
        await settle()
        const g = await geometry()
        assert.equal(Boolean(g.face), index === 0)
        if (index === 2) { assert.equal(g.subtitle, null); near(g.title.height, 2 * g.lineHeight) }
        else assert.ok(g.subtitle)
        if (baseline) {
          if (index === 0) assert.ok(g.rule.right > g.face.left, 'baseline rule extends underneath the corner face')
        } else aligned(g)
        const label = ['group', 'solo', 'quiet-two-line'][index]
        await page.screenshot({ path: path.join(out, `${phase}-${label}-${width}-${theme}.png`) })
        checks.push({ label, ...g })
      }
      if (!baseline) {
        // At phone width retain the return cue while inspecting layout changes;
        // the captures above use actual open/Back navigation and its timer.
        for (const scale of [.9, 1.25]) for (const row of rows) {
          await page.evaluate(({ scale, id }) => {
            document.documentElement.style.setProperty('--ui-scale', scale)
            if (__clearance.threads.st.wide) __clearance.threads.open(id)
            else { __clearance.threads.pause(); __clearance.threads.st.returning = id; __clearance.schedule() }
          }, { scale, id: row.id })
          await settle(); const g = await geometry(); aligned(g); checks.push({ label: row.id, ...g })
        }
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      assert.deepEqual(errors, [])
      results.push({ width, theme, checks, errors })
      console.log(`PASS ${phase} ${width} ${theme}: ${baseline ? 'rule extending beneath corner face reproduced' : 'group, solo, quiet two-line; equal gaps, no face overlap; 90/100/125% scale'}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, `${phase}-browser.json`), JSON.stringify({ baseline, browser: browser.version(), fixturesOnly: true, results }, null, 2) + '\n')
} finally { await browser.close() }
