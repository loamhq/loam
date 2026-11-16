// Synthetic thread jobs through the real page/relay; no host jobs are started.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { renameHost } from './fixtures/files-rename.js'

const out = process.env.THREAD_JOBS_EVIDENCE
assert.ok(out, 'Set THREAD_JOBS_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8'), results = []
const id = 'a-quieter-week', now = Date.parse('2026-11-13T20:00:00Z')
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
try {
  for (const width of [390, 1440]) for (const look of ['latte', 'plain']) for (const mode of ['light', 'dark']) {
    const app = await oneProductFixture(), host = renameHost(), label = `${width}-${look}-${mode}`, errors = [], reads = []
    const original = await app.f.api('GET', `/threads/${id}`), streams = new Set(), lists = new Set()
    let members = [{ persona: 'sprout', engine: 'claude-code', busy: true, working: false, background: 1,
      background_tasks: [{ id: 'same-id', description: 'Review the shared notes', started_at: new Date(now - 120000).toISOString() }] }]
    const job = (fields = {}) => ({ job: 'same-id', project: 'Garden', title: 'Keep the work in this conversation', briefed_by: 'sprout', thread: id,
      state: 'running', phase: null, since: new Date(now - 60000).toISOString(), started: new Date(now - 360000).toISOString(), finished: null,
      elapsed_seconds: 360, outcome: null, outcome_detail: null, ok: null, receipt: null, position: null, held_by: null, ...fields })
    let jobs = [job(), job({ job: 'landing', briefed_by: 'quill', title: 'A clearer view of the next step', state: 'landing', phase: 'ci' }),
      job({ job: 'queued', title: 'Leave room for a longer project title and a useful explanation of what happens next', project: 'A project with a longer name', state: 'queued', started: null, elapsed_seconds: null, position: 1, held_by: 'Waiting for the current change to land' }),
      job({ job: 'done', title: 'Bring the result back here', state: 'finished', finished: new Date(now - 60000).toISOString(), outcome: 'deployed', ok: true, receipt: 'Notes/plan.md' })]
    let jobsError = null, fail = false
    const summary = () => ({ ...original.thread, members }) // streams never carry jobs
    Object.assign(app.routes, host.routes)
    app.routes['GET /v1/files/resolve'] = (req, res, url) => {
      assert.equal(url.searchParams.get('ref'), 'Notes/plan.md')
      json(res, { status: 'found', resource: host.entries.get('Notes/plan.md') })
    }
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: { mode, theme: look, size: 100 }, desktop: { mode, theme: look, size: 100 } })
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: [summary()], agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`data: ${JSON.stringify({ type: 'threads.snapshot', threads: [summary()], agents: app.f.agents })}\n\n`)
      lists.add(res); res.on('close', () => lists.delete(res))
    }
    app.routes[`GET /v1/threads/${id}`] = (req, res, url) => {
      reads.push(url.search)
      if (fail) { res.writeHead(503, { 'content-type': 'application/json' }); res.end('{"error":"temporarily unavailable"}'); return }
      json(res, { ...original, thread: { ...summary(), jobs: jobsError ? [] : jobs, jobs_error: jobsError }, turns: [], turn: null, has_more: false })
    }
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`data: ${JSON.stringify({ type: 'thread.snapshot', members, turns: [], pending: [] })}\n\n`)
      streams.add(res); res.on('close', () => streams.delete(res))
    }
    const publish = () => {
      for (const res of streams) res.write(`data: ${JSON.stringify({ type: 'members', members })}\n\n`)
      for (const res of lists) res.write(`data: ${JSON.stringify({ type: 'thread.changed', thread: id, summary: summary() })}\n\n`)
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__jobs = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', e => errors.push(e.message))
    await page.clock.install({ time: new Date(now) })
    const panel = page.locator('.tt-tasks-panel'), indicator = page.locator('.tt-tasks-button')
    const advance = async (ms = 5100) => { await page.clock.fastForward(ms); await page.waitForFunction(() => !__jobs.threads.st.open?.jobsReading) }
    const shot = async state => {
      await page.evaluate(async () => { await document.fonts.ready; document.activeElement?.blur(); await new Promise(requestAnimationFrame) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      await page.screenshot({ path: path.join(out, `${state}-${label}.png`), animations: 'disabled' })
    }
    try {
      await page.goto(`${app.url}/threads/${id}`)
      await page.waitForFunction(() => globalThis.__jobs && !__jobs.threads.st.open?.refreshing)
      assert.equal(await page.locator('.tt-task-count').innerText(), '4')
      await shot('header')
      await indicator.click(); await panel.waitFor()
      assert.equal(await panel.locator('.tt-task-row').count(), 5)
      assert.equal(await panel.locator('[data-job="same-id"] .tt-job-elapsed').innerText(), '6 min')
      await panel.getByText('Waiting for the current change to land', { exact: true }).waitFor()
      const rect = await panel.boundingBox()
      assert.ok(rect.x >= 0 && rect.y >= 0 && rect.x + rect.width <= width + 1 && rect.y + rect.height <= (width === 390 ? 844 : 1000) + 1)
      assert.equal(await panel.locator('.tt-task-row').evaluateAll(rows => rows.every(r => r.scrollWidth <= r.clientWidth)), true)
      await shot('mixed')
      await advance(61000)
      assert.equal(await panel.locator('[data-job="same-id"] .tt-job-elapsed').innerText(), '7 min')
      assert.equal(await panel.locator('[data-job="done"] .tt-job-elapsed').innerText(), '6 min')
      // Ending the persona and receiving job-less list rows must retain jobs.
      members = []; publish()
      await page.waitForFunction(() => document.querySelector('.tt-task-count')?.textContent === '3')
      assert.equal(await panel.locator('.tt-job-row').count(), 4)
      await shot('jobs-only')
      jobs[0] = { ...jobs[0], state: 'landing', phase: 'deploy' }
      await advance(); await panel.getByText('Making it live', { exact: true }).waitFor()
      // Keep a member task through job errors so the existing panel stays open.
      members = [{ persona: 'sprout', engine: 'claude-code', working: false, background: 1, background_tasks: [] }]; publish()
      jobsError = 'Job state unavailable'; await advance()
      await page.waitForFunction(() => !document.querySelector('.tt-job-row'))
      assert.equal(await page.locator('.tt-task-count').innerText(), '1')
      jobsError = null; await advance()
      await page.waitForFunction(() => document.querySelectorAll('.tt-job-row').length === 4)
      // Preserve the last reading through transport errors; recover on the next poll.
      fail = true; await advance(); assert.equal(await panel.locator('.tt-job-row').count(), 4); fail = false
      members = []; publish()
      const finished = new Date(await page.evaluate(() => Date.now())).toISOString()
      jobs = jobs.map(j => ({ ...j, state: 'finished', finished, outcome: j.job === 'queued' ? 'not landed' : 'deployed', receipt: j.job === 'done' ? 'Notes/plan.md' : null }))
      await advance()
      await page.waitForFunction(() => !document.querySelector('.tt-task-count'))
      assert.equal(await page.locator('.tt-task-count').count(), 0)
      assert.equal(await indicator.isEnabled(), true)
      await panel.getByText('Didn’t land', { exact: true }).waitFor()
      await shot('finished')
      await page.keyboard.press('Escape'); await panel.waitFor({ state: 'hidden' })
      assert.equal(await indicator.evaluate(n => n === document.activeElement), true)
      await indicator.click()
      const receipt = panel.locator('[data-job="done"]')
      await receipt.scrollIntoViewIfNeeded(); await receipt.click()
      await page.waitForURL('**/files/vault/Notes/plan.md')
      await page.locator('.fs-edtitle').waitFor()
      assert.equal(await panel.count(), 0)
      await shot('receipt')
      // Reopen to verify a completed-only panel stays reachable, then ages out.
      await page.goto(`${app.url}/threads/${id}`)
      await indicator.waitFor(); await indicator.click(); await panel.waitFor()
      const completed = jobs
      jobs = Array.from({ length: 20 }, (_, i) => job({ job: `long-${i}`, title: `Review section ${i + 1} and bring its useful findings back to this thread` }))
      await advance()
      await page.waitForFunction(() => document.querySelectorAll('.tt-job-row').length === 20)
      assert.equal(await panel.locator('.tt-tasks-body').evaluate(n => n.scrollHeight > n.clientHeight), true)
      await panel.locator('.tt-job-row').last().scrollIntoViewIfNeeded()
      const last = await panel.locator('.tt-job-row').last().boundingBox(), body = await panel.locator('.tt-tasks-body').boundingBox()
      assert.ok(last.y + last.height <= body.y + body.height + 1)
      await shot('long-list')
      jobs = completed; await advance()
      await page.clock.fastForward(10 * 60000 + 5000)
      await page.waitForFunction(() => !document.querySelector('.tt-tasks-button'))
      await panel.waitFor({ state: 'hidden' })
      assert.ok(reads.some(s => s === '?limit=1&seen=0'))
      assert.equal(app.broker.seen.some(r => /\/v1\/threads\/.*\/messages/.test(r.path) && r.method === 'POST'), false)
      assert.deepEqual(errors, [])
      results.push({ width, look, mode, rect, jobsSurvivePersonaExit: true, liveClock: true, frozenCompletion: true, optionalErrorsRecover: true, receiptInFiles: true, longListsScroll: true, tenMinuteExpiry: true, errors })
      console.log(`PASS ${label}: mixed rows, live clock, ended persona, phases/errors/recovery, receipts, expiry and focus`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
} finally { await browser.close() }
