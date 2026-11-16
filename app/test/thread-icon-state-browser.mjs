// Real page/relay, synthetic broker only. Compare the removal with its parent.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile, readdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
const out = process.env.THREAD_ICON_EVIDENCE
if (!out) throw new Error('Set THREAD_ICON_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const currentOnly = process.argv.includes('--current-only')
const baseline = '95c62a7', results = [], states = ['waiting', 'working', 'asking', 'quiet']
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
try {
  const design = await browser.newPage()
  await design.goto(new URL('../design/Loam App.dc.html', import.meta.url).href)
  await design.screenshot({ path: path.join(process.env.TMPDIR, 'icon-design-reference.png') }); await design.close()
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) for (const base of ['persona', 'group']) for (const phase of (currentOnly ? ['after'] : ['before', 'after'])) {
    const app = await oneProductFixture(), listClients = new Set(), clients = new Map(), errors = [], checks = {}
    const original = await app.f.api('GET', '/threads/a-quieter-week')
    const member = (persona, extra = {}) => ({ persona, engine: 'claude-code', model: null, working: false, background: 0, ...extra })
    const rows = states.map((state) => ({ ...original.thread, id: `${base}-${state}`, title: `${base === 'persona' ? 'One clear next step' : 'A shared review'} · ${state}`, icon: null, default: 'quill', pinned: false, unread: state === 'quiet' ? 0 : 1,
      members: [member('quill', { working: state === 'working' || state === 'asking', asking: state === 'asking', background: state === 'quiet' ? 0 : 4 }), ...(base === 'group' ? [member('scout')] : [])],
      background: state === 'quiet' ? 0 : 4, working: state === 'working' || state === 'asking' ? 1 : 0 }))
    const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
    const sse = (res, set, event) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); res.write(`data: ${JSON.stringify(event)}\n\n`); set.add(res); res.on('close', () => set.delete(res)) }
    const emit = (set, event) => { for (const res of set ?? []) res.write(`data: ${JSON.stringify(event)}\n\n`) }
    const snapshot = (row) => ({ type: 'thread.snapshot', members: row.members, pending: [], turns: [], background: row.background, working: row.working })
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: rows, agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => sse(res, listClients, { type: 'threads.snapshot', threads: rows, agents: app.f.agents })
    for (const row of rows) {
      clients.set(row.id, new Set())
      app.routes[`GET /v1/threads/${row.id}`] = (req, res) => json(res, { ...original, thread: row, turns: [], turn: null })
      app.routes[`GET /v1/threads/${row.id}/events`] = (req, res) => sse(res, clients.get(row.id), snapshot(row))
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    if (phase === 'before') for (const file of ['threads.js', 'threads.css', 'thread-identity.js']) await context.route(`**/${file}`, (route) => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' }) }))
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__iconTest = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', (e) => errors.push(e.message))
    page.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused to/.test(m.text())) errors.push(m.text()) })
    await page.clock.install({ time: new Date('2026-11-11T19:45:00Z') })
    const settle = async () => { await page.clock.runFor(800); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }) }
    const shot = async (label) => {
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      await page.screenshot({ path: path.join(out, `${phase}-${base}-${label}-${width}-${theme}.png`) })
    }
    const inspect = async (face, state) => {
      const kind = state === 'waiting' ? phase === 'before' ? 'background' : 'rest' : state === 'quiet' ? 'rest' : state
      assert.equal(await face.getAttribute('data-icon-state'), kind)
      if (state === 'quiet') assert.match(await face.getAttribute('aria-label'), /Quiet$/)
      if (state === 'waiting') assert.match(await face.getAttribute('aria-label'), /Quill waiting$/)
      assert.equal(await face.locator('.tt-icon-layer').count(), kind === 'rest' ? 0 : 1)
      assert.equal(await face.evaluate((n) => n.classList.contains('blob')), base === 'persona')
      const values = await face.evaluate((n) => {
        for (const a of n.getAnimations()) { a.pause(); a.currentTime = 1500 }
        const style = getComputedStyle(n), layer = n.querySelector('.tt-icon-layer'), ls = layer && getComputedStyle(layer), rect = n.getBoundingClientRect()
        const radii = (s) => ['borderTopLeftRadius', 'borderTopRightRadius', 'borderBottomRightRadius', 'borderBottomLeftRadius'].map((key) => s[key])
        return { width: rect.width, height: rect.height, shape: radii(style), animation: style.animationName, transform: style.transform, filter: style.filter,
          layer: ls && { shape: radii(ls), background: ls.backgroundColor, shadow: ls.boxShadow, pointer: ls.pointerEvents, hidden: layer.getAttribute('aria-hidden') } }
      })
      if (state === 'asking' || state === 'working') {
        assert.deepEqual(values.layer.shape, values.shape, 'glow follows avatar/tile corners')
        assert.notDeepEqual(values.shape, ['50%', '50%', '50%', '50%'], 'fixture uses a non-circular base')
        assert.equal(values.layer.pointer, 'none'); assert.equal(values.layer.hidden, 'true')
        assert.equal(values.animation, state === 'working' ? 'tt-icon-breath' : 'none')
        if (state === 'working') assert.match(values.transform, /1\.025/)
      }
      return values
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await settle()
      for (const state of states) checks[`${state}-row`] = await inspect(page.getByTitle(rows[states.indexOf(state)].title, { exact: true }).locator('.tt-thread-icon'), state)
      await shot('rows')
      for (const state of states) {
        const row = rows[states.indexOf(state)]
        await page.evaluate((id) => __iconTest.threads.open(id), row.id)
        await page.waitForFunction(() => !__iconTest.threads.st.open.loading && !__iconTest.threads.st.open.refreshing)
        await page.locator('.tt-head .tt-thread-icon').waitFor(); await settle()
        checks[`${state}-header`] = await inspect(page.locator('.tt-head .tt-thread-icon'), state)
        await page.getByTitle('Thread details', { exact: true }).click(); await settle()
        checks[`${state}-details`] = await inspect(page.locator('.tt-details-icon .tt-thread-icon'), state)
        if (state === 'waiting' || state === 'asking') await shot(`details-${state}`)
        await page.getByRole('button', { name: 'Chat', exact: true }).click(); await settle()
        if (state === 'waiting' && phase === 'after') {
          // Task completion, additions, and a lagging detail reading never add a layer.
          for (const count of [2, 4, 0, 3]) {
            row.background = count; row.members[0].background = count
            emit(listClients, { type: 'thread.changed', summary: row }); emit(clients.get(row.id), snapshot(row))
            await page.waitForFunction((n) => __iconTest.threads.st.open.thread.background === n, count); await settle()
            assert.equal(await page.locator('.tt-head .tt-icon-layer').count(), 0)
          }
        }
        if (phase === 'after' && state === 'working') {
          row.members.forEach((m) => { m.working = true }); row.working = row.members.length
          emit(listClients, { type: 'thread.changed', summary: row }); emit(clients.get(row.id), snapshot(row)); await settle()
          assert.equal(await page.locator('.tt-head .tt-thread-icon').evaluate((n) => n.style.getPropertyValue('--tt-icon-tone')), base === 'group' ? 'var(--paper-ink-3)' : await page.locator('.tt-head .tt-thread-icon').evaluate((n) => n.style.background))
        }
        if (phase === 'after') {
          await page.emulateMedia({ reducedMotion: 'reduce' }); await settle()
          assert.equal(await page.locator('.tt-head .tt-thread-icon').evaluate((n) => getComputedStyle(n).animationName), 'none')
          assert.equal(await page.locator('.tt-head .tt-icon-layer').count(), state === 'working' || state === 'asking' ? 1 : 0)
          await page.emulateMedia({ reducedMotion: 'no-preference' })
        }
        await page.evaluate(() => __iconTest.threads.close()); await settle()
      }
      assert.deepEqual(errors, [])
      if (phase === 'after' && !currentOnly) {
        const before = results.find((r) => r.width === width && r.theme === theme && r.base === base && r.phase === 'before')
        for (const state of ['working', 'asking', 'quiet']) for (const surface of ['row', 'header', 'details']) assert.deepEqual(checks[`${state}-${surface}`], before.checks[`${state}-${surface}`], `${state} ${surface} unchanged`)
      }
      results.push({ width, theme, base, phase, checks, errors }); console.log(`PASS ${phase} ${base} ${width} ${theme}: all four states in rows/header/Details, shape${phase === 'after' ? ', preserved glow/breath, task changes, reduced motion' : ''}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ baseline: currentOnly ? null : baseline, currentOnly, fixturesOnly: true, results }, null, 2) + '\n')
  const captures = []
  for (const file of (await readdir(out)).filter((f) => f.endsWith('.png')).sort()) captures.push({ file, sha256: createHash('sha256').update(await readFile(path.join(out, file))).digest('hex') })
  await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
} finally { await browser.close() }
