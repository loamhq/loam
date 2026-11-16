// Opt-in painted evidence: isolated fixtures, actual app, classic reference beside it.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const sharp = (await import(process.env.LOAM_SHARP || 'sharp')).default
const out = process.env.RUNNING_EVIDENCE
if (!out) throw new Error('Set RUNNING_EVIDENCE to the evidence directory')
await mkdir(out, { recursive: true })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const expose = '\nglobalThis.__running = { running, threads, state, schedule, goChats, openSheet, closeSheet, goSettings };\n'
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'], headless: true })
const checks = [], captures = []
try {
for (const width of (process.env.RUNNING_WIDTH ? [Number(process.env.RUNNING_WIDTH)] : [390, 1440])) for (const theme of (process.env.RUNNING_THEME ? [process.env.RUNNING_THEME] : ['light', 'dark'])) {
  const app = await oneProductFixture(), streams = new Set(), liveStreams = new Set(), errors = [], now = Date.now()
  const json = (res, value, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
  app.records.forEach((r, i) => { r.agent = app.f.agents[i % app.f.agents.length].id; r.status = ['working', 'needs_input', 'done', 'done'][i]; r.created = new Date(now - 120000).toISOString(); r.last_event_at = new Date(now - (i === 0 ? 120000 : 480000)).toISOString() })
  const view = () => ({ sessions: app.records.filter((r) => r.live).map((r, i) => ({ id: r.session_id, agent: r.agent, engine: r.provider, model: null,
    kind: r.session_id.includes('a-quieter-week') ? 'thread' : 'classic', thread_id: r.session_id.includes('a-quieter-week') ? 'a-quieter-week' : null,
    thread_title: r.session_id.includes('a-quieter-week') ? r.title : null, title: r.session_id.includes('a-quieter-week') ? null : r.title,
    state: r.status === 'needs_input' ? 'asking' : r.status === 'working' ? 'working' : 'idle', last_active: r.last_event_at,
    idle_since: r.status === 'done' ? r.last_event_at : null, started_at: r.created, rss_mb: 320 + i * 90,
    ending: { loses_work: r.status !== 'done', text: r.status === 'done' ? 'resumes later exactly where it was' : 'ends a turn in progress' } })),
    box: { ram_used_mb: 18432, ram_total_mb: 32768, can_start: 20, sessions: { live: app.records.filter((r) => r.live).length },
      units: { engineer_jobs: { count: 1, mb: 640 }, wakes: { count: 1, mb: 120 } } }, at: new Date().toISOString() })
  const emit = (record) => {
    for (const res of streams) res.write(`data: ${JSON.stringify({ type: 'broker.session', ...record })}\n\n`)
    for (const res of liveStreams) res.write(`data: ${JSON.stringify({ type: 'sessions.live', ...view() })}\n\n`)
  }
  app.routes['GET /v1/sessions/live'] = (req, res) => json(res, view())
  app.routes['GET /v1/sessions/live/events'] = (req, res) => { liveStreams.add(res); res.on('close', () => liveStreams.delete(res)); res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); res.write(`data: ${JSON.stringify({ type: 'sessions.live', ...view() })}\n\n`) }
  app.routes['GET /v1/sessions/events'] = (req, res) => { streams.add(res); res.on('close', () => streams.delete(res)); res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); for (const r of app.records) res.write(`data: ${JSON.stringify({ type: 'broker.session', ...r })}\n\n`) }
  app.routes['POST /v1/sessions/end-idle'] = (req, res) => { const ended = app.records.filter((r) => r.live && r.status === 'done'); for (const r of ended) { r.live = false; r.state = 'exited'; emit(r) }; json(res, { ended: ended.map((r) => ({ id: r.session_id, agent: r.agent })), kept: [] }) }
  for (const r of app.records) {
    app.routes[`GET /v1/sessions/${r.session_id}`] = (req, res) => json(res, r)
    app.routes[`DELETE /v1/sessions/${r.session_id}`] = (req, res) => { r.live = false; r.state = 'exited'; emit(r); json(res, r) }
  }
  const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
  await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + expose }))
  const page = await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', (e) => errors.push(e.message))
  const act = async (fn, arg) => { await page.evaluate(fn, arg); await page.waitForTimeout(100) }
  const threads = async () => { await act(() => { __running.closeSheet(); __running.goChats() }); await page.locator('.tt-row').first().waitFor() }
  const open = async () => { await page.getByTitle('Running now', { exact: true }).click(); await page.getByRole('dialog', { name: 'Running now', exact: true }).waitFor() }
  const capture = async (surface) => {
    await page.mouse.move(0, 0); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${surface} fits ${width}`)
    if (surface === 'header' && width === 1440) return page.screenshot({ clip: { x: 0, y: 0, width: 452, height: 1000 }, animations: 'disabled' })
    return page.screenshot({ animations: 'disabled' })
  }
  const pair = async (name, left) => { await left(); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) }); const file = name + '-' + width + '-' + theme + '.png'; const dest = path.join(out, file); await page.screenshot({ path: dest, animations: 'disabled' }); captures.push({ file, width, theme }); console.log(file) }
  await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await page.getByTitle('Running now').waitFor()
  await pair('header', threads)
  await pair('groups', async () => { await threads(); await open(); assert.deepEqual(await page.locator('.run-group > h3').allTextContents(), ['Working', 'Waiting on you', 'Idle']); await page.getByRole('button', { name: 'End all idle', exact: true }).waitFor() })
  await pair('members', async () => { await threads(); await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click(); await page.getByTitle('Thread details', { exact: true }).click(); if (width === 1440) await page.getByRole('navigation', { name: 'Thread settings' }).getByRole('button', { name: 'Members', exact: true }).click(); else await page.locator('.tt-details-scroll').evaluate((el) => el.scrollTop = el.querySelector('#tt-members').offsetTop - 24); await page.locator('.tt-member .run-live').waitFor() })
  await pair('confirm', async () => { await threads(); await open(); await page.locator('.run-group').filter({ has: page.getByRole('heading', { name: 'Working', exact: true }) }).getByRole('button', { name: /^End / }).click(); await page.getByText(/This stops .*turn in progress/).waitFor(); assert.equal(app.broker.seen.some((r) => r.method === 'DELETE'), false) })
  await pair('toast', async () => { await threads(); await open(); const idle = page.locator('.run-group').filter({ has: page.getByRole('heading', { name: 'Idle', exact: true }) }); await idle.getByRole('button', { name: /^End / }).first().click(); await page.locator('.run-toast').waitFor(); await act(() => __running.closeSheet()); assert.equal(app.records.filter((r) => r.live).length, 3) })
  await threads(); await open()
  await page.locator('.run-row').filter({ hasText: 'A little room for the week' }).getByRole('button', { name: 'Open A little room for the week', exact: true }).click()
  await page.locator('.tt-thread').waitFor(); await page.getByTitle('Thread details', { exact: true }).click()
  await page.locator('.tt-member .run-live').waitFor(); await page.getByRole('button', { name: 'End session', exact: true }).waitFor()
  await page.getByRole('button', { name: 'End session', exact: true }).click()
  await page.locator('.run-confirm').waitFor(); await page.getByRole('button', { name: 'Keep running', exact: true }).click()
  // Test the active confirmation, keep-running, and actual idle recheck.
  await threads(); await open(); await page.locator('.run-row').first().getByRole('button', { name: /^End / }).click(); await page.getByRole('button', { name: 'Keep running', exact: true }).click(); await page.locator('.run-confirm').waitFor({ state: 'detached' }); assert.equal(await page.locator('.run-confirm').count(), 0)
  await page.locator('.run-row').first().getByRole('button', { name: /^End / }).click(); await page.getByRole('button', { name: 'End session', exact: true }).click(); await page.waitForFunction(() => __running.running.rows().length === 2)
  for (const r of app.records.slice(2)) { r.live = true; r.state = 'running'; r.status = 'done'; emit(r) }
  await page.getByRole('button', { name: 'End all idle', exact: true }).waitFor()
  const deletesBefore = app.broker.seen.filter((r) => r.method === 'DELETE').length
  await page.getByRole('button', { name: 'End all idle', exact: true }).click()
  await page.waitForFunction(() => __running.running.rows().every((r) => r.state !== 'idle'))
  assert.equal(app.broker.seen.filter((r) => r.method === 'DELETE').length, deletesBefore)
  assert.ok(app.broker.seen.some((r) => r.method === 'POST' && r.path === '/v1/sessions/end-idle'))
  const memberSession = app.records.find((r) => r.session_id.includes('a-quieter-week'))
  memberSession.status = 'done'; emit(memberSession)
  await threads(); await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click(); await page.getByTitle('Thread details', { exact: true }).click()
  await page.getByRole('button', { name: 'End session', exact: true }).click()
  await page.locator('.run-toast').waitFor(); await page.waitForFunction(() => !__running.running.forMember('a-quieter-week', 'quill'))
  assert.equal(await page.locator('.run-sheet').count(), 0, 'idle member ends directly in context')
  for (const r of app.records) { r.live = false; emit(r) }
  await act(() => { __running.running.st.toast = null; __running.running.st.count = 0; __running.schedule() })
  await pair('empty', async () => { await threads(); await open(); await page.getByText('Nothing running', { exact: true }).waitFor() })
  assert.equal(errors.length, 0, errors.join('\n'))
  checks.push({ width, theme, allGroups: true, memoryGauge: 'exact runtime shape with synthetic values', activeConfirmation: true, idleImmediate: true, threadNavigation: true, memberLiveDot: true, memberEnd: true, idleMemberEnd: true, atomicBulkIdle: true, threadHeader: true, empty: true, browserErrors: errors })
  await context.close(); await app.close()
}
await writeFile(path.join(out, `browser${process.env.RUNNING_WIDTH ? '-' + process.env.RUNNING_WIDTH : ''}${process.env.RUNNING_THEME ? '-' + process.env.RUNNING_THEME : ''}.json`), JSON.stringify({ fixture: true, checks, captures }, null, 2) + '\n')
} finally { await browser.close() }
