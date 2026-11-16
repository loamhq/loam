// Real page and relay, synthetic broker only. No writes to real threads.
import assert from 'node:assert/strict'
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_TASKS_EVIDENCE
if (!out) throw new Error('Set THREAD_TASKS_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const appSource = await readFile(new URL('../public/app.js', import.meta.url), 'utf8'), results = []
const id = 'a-quieter-week', now = '2026-11-11T00:36:00Z'
try {
  for (const width of [1440, 390]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), clients = new Set(), errors = [], reads = []
    const task = { id: 'one', description: 'Brief Loam: Check the current version and reply labels', started_at: '2026-11-11T00:34:00Z' }
    let members = [{ persona: 'sprout', engine: 'claude-code', model: 'opus', working: false, busy: false, background: 0, background_tasks: [] },
      { persona: 'quill', engine: 'codex', model: null, working: false, busy: false, background: 0, background_tasks: [] }]
    let turns = []
    const messages = [
      { id: 'm1', author: 'you', kind: 'message', at: '2026-11-11T00:34:00Z', text: 'Please check the current version and bring back the result.' },
      { id: 'm2', author: 'sprout', kind: 'message', engine: 'claude-code', model: 'claude-opus-5-5', at: '2026-11-11T00:35:00Z',
        text: 'Your expectation is right: the model alias should follow its current version.\n\n**Checked.** The saved choice is still the family alias. Each reply records the concrete version it used.\n\nI’ll bring the results back to this conversation. You can carry on here while the checks run.', did: [] },
      { id: 'm3', author: 'sprout', kind: 'message', engine: 'claude-code', model: 'opus', at: '2026-11-11T00:35:20Z', text: '', did: [{ label: '↳ background task' }] },
    ]
    const original = await app.f.api('GET', `/threads/${id}`)
    const summary = () => ({ ...original.thread, title: 'The current version', default: 'sprout', members, unread: 0, background: members.reduce((n, m) => n + m.background, 0), working: members.filter((m) => m.working).length })
    const snapshot = () => ({ type: 'thread.snapshot', thread: id, members, turns, pending: [] })
    const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    const emit = (event) => { for (const res of clients) res.write(`data: ${JSON.stringify(event)}\n\n`) }
    const publish = () => emit({ type: 'members', members })
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: [summary()], agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`data: ${JSON.stringify({ type: 'threads.snapshot', threads: [summary()], agents: app.f.agents })}\n\n`) }
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...original, thread: summary(), messages, has_more: false, turns })
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`data: ${JSON.stringify(snapshot())}\n\n`); clients.add(res); res.on('close', () => clients.delete(res)) }
    app.routes[`GET /v1/threads/${id}/steps`] = (req, res, url) => { reads.push(url.searchParams.get('message')); res.writeHead(404, { 'content-type': 'application/json' }); res.end('{"error":"No full record"}') }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, timezoneId: 'America/Los_Angeles', isMobile: width === 390, hasTouch: width === 390 })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: appSource + '\nglobalThis.__taskTest = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000); await page.clock.install({ time: new Date(now) }); page.on('pageerror', (error) => errors.push(error.message))
    const settle = () => page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
    const capture = async (state) => { await page.evaluate(() => document.activeElement?.blur()); await page.clock.runFor(400); await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(out, `${state}-${width}-${theme}.png`), animations: 'disabled' }) }
    const indicator = page.locator('.tt-tasks-button'), panel = page.locator('.tt-tasks-panel')
    const noFlow = async () => {
      assert.equal(await page.locator('.tt-live-area, .tt-member-activity, .tt-stream .fold, [data-message-id="m3"], [data-message-id="draft:sprout"]').count(), 0)
      assert.equal(reads.includes('m3'), false)
    }
    const fullyVisible = async () => {
      await page.clock.runFor(400); await settle()
      await page.waitForFunction(() => { const r = document.querySelector('.tt-tasks-panel')?.getBoundingClientRect(); return r && r.y >= 0 && r.bottom <= innerHeight + 1 })
      const rect = await panel.boundingBox()
      assert.ok(rect.x >= 0 && rect.y >= 0 && rect.x + rect.width <= width + 1 && rect.y + rect.height <= (width === 390 ? 844 : 1000) + 1, JSON.stringify(rect))
      return rect
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').waitFor()
      await page.waitForFunction(() => globalThis.__taskTest)
      await page.evaluate((id) => __taskTest.threads.open(id), id)
      await page.locator('[data-message-id="m2"] .tt-prose-content:not(.tt-prose-plain)').waitFor()
      await page.waitForFunction(() => __taskTest.threads.st.open.loading === false)
      assert.equal(await indicator.count(), 0); await noFlow(); await capture('no-tasks')
      members[0] = { ...members[0], busy: true, background: 1, background_tasks: [task] }
      turns = [{ persona: 'sprout', engine: 'claude-code', started_at: now, on_background: true }]
      emit(snapshot()); await page.getByRole('button', { name: '1 background task', exact: true }).waitFor(); await noFlow()
      assert.equal(await indicator.locator('.tt-task-count').innerText(), '1')
      assert.equal(await indicator.evaluate((n) => n.previousElementSibling.getAttribute('aria-label')), 'Board')
      await capture('one-task')
      if (width === 390) await indicator.tap(); else { await indicator.focus(); await indicator.press('Enter') }
      await panel.waitFor(); await fullyVisible()
      assert.equal(await panel.locator('.tt-task-row').count(), 1)
      assert.match(await panel.innerText(), /Sprout\s+Check the current version and reply labels\s+2 min/)
      assert.doesNotMatch(await panel.innerText(), /Brief Loam/)
      await panel.press('Escape'); await panel.waitFor({ state: 'hidden' })
      assert.equal(await indicator.evaluate((n) => n === document.activeElement), true)
      // Two creators may have identical task IDs; their rows remain distinct.
      members[0] = { ...members[0], background: 2, background_tasks: [task, { id: 'two', description: 'Brief loam-ui: Keep the phone composer clear', started_at: '2026-11-11T00:35:00Z' }] }
      members[1] = { ...members[1], busy: true, background: 1, background_tasks: [{ id: 'one', description: 'Compare the notes and summarize the next steps', started_at: '2026-11-11T00:35:30Z' }] }
      publish(); await page.getByRole('button', { name: '3 background tasks', exact: true }).waitFor(); await noFlow(); await capture('three-tasks')
      await indicator.click(); await panel.waitFor(); const geometry = await fullyVisible()
      assert.equal(await panel.locator('.tt-task-row').count(), 3)
      assert.deepEqual(await panel.locator('.tt-task-group-name').allTextContents(), ['SpSprout', 'QQuill'])
      assert.deepEqual(await panel.locator('time').allTextContents(), ['2 min', '1 min', '<1 min'])
      assert.doesNotMatch(await panel.innerText(), /Brief Loam|Brief loam-ui/)
      assert.equal(await panel.evaluate((n) => n.classList.contains('sheet')), width === 390)
      assert.equal(await page.locator('.tt-shell > main, .tt-shell > .tt-thread').first().evaluate((n) => n.inert), true)
      await capture('tasks-open')
      const controls = panel.locator('button:visible')
      await controls.last().focus(); await page.keyboard.press('Tab')
      assert.equal(await controls.first().evaluate((n) => n === document.activeElement), true)
      await page.keyboard.press('Shift+Tab')
      assert.equal(await controls.last().evaluate((n) => n === document.activeElement), true)
      await page.clock.fastForward(60000); await settle()
      assert.deepEqual(await panel.locator('time').allTextContents(), ['3 min', '2 min', '1 min'])
      const row = panel.locator('.tt-task-row').first(); await row.click(); await panel.getByText('Task details', { exact: true }).waitFor()
      assert.equal(await panel.locator('.tt-task-full').innerText(), task.description)
      assert.match(await panel.innerText(), /Started Nov 10, 2026, 4:34 PM/)
      assert.equal(await panel.locator('a').count(), 0, 'contract provides no launching reply link')
      await capture('task-details'); await panel.getByRole('button', { name: 'Back to task list' }).click()
      assert.equal(await panel.locator('.tt-task-row').first().evaluate((n) => n === document.activeElement), true)
      // Removed selected tasks return to the live list rather than stale details.
      await panel.locator('.tt-task-row').last().click(); await panel.getByText('Task details', { exact: true }).waitFor()
      members[1] = { ...members[1], busy: false, background: 0, background_tasks: [] }; publish()
      await page.waitForFunction(() => document.querySelectorAll('.tt-task-row').length === 2)
      assert.equal(await panel.locator('.tt-task-row').count(), 2)
      // Counts survive the interval before a result notification, without fake rows.
      members[0] = { ...members[0], background: 1, background_tasks: [] }; publish()
      await panel.getByText('Waiting for results…', { exact: true }).waitFor()
      assert.equal(await panel.locator('.tt-task-row').count(), 0)
      // New work cancels the automatic close, including while the badge fades.
      members[0] = { ...members[0], busy: false, background: 0 }; publish()
      await panel.getByText('Nothing running', { exact: true }).waitFor()
      members[0] = { ...members[0], busy: true, background: 1, background_tasks: [task] }; publish()
      await panel.locator('.tt-task-row').waitFor(); await page.clock.fastForward(1600)
      assert.equal(await panel.count(), 1)
      members[0] = { ...members[0], busy: false, background: 0, background_tasks: [] }; publish()
      await panel.getByText('Nothing running', { exact: true }).waitFor()
      assert.equal(await indicator.evaluate((n) => n.classList.contains('leaving')), true)
      await page.clock.fastForward(1500); await panel.waitFor({ state: 'hidden' }); await indicator.waitFor({ state: 'hidden' })
      assert.equal(await page.getByRole('button', { name: 'Board', exact: true }).evaluate((n) => n === document.activeElement), true)
      // Actual generation still owns its reply slot and classic waiting words.
      members[0] = { ...members[0], working: true, busy: true }; publish()
      emit({ type: 'turn.started', persona: 'sprout', engine: 'claude-code', started_at: now })
      await page.locator('[data-message-id="draft:sprout"] .awaiting-words').waitFor()
      assert.match(await page.locator('[data-message-id="draft:sprout"]').innerText(), /Thinking/)
      await capture('generating')
      emit({ type: 'message.delta', persona: 'sprout', draft: 'The new reply is arriving in the same place.' })
      await page.getByText('The new reply is arriving in the same place.', { exact: true }).waitFor(); await capture('streaming')
      // Long task lists scroll within a fully visible panel at both widths.
      members[0] = { ...members[0], working: false, background: 20, background_tasks: Array.from({ length: 20 }, (_, i) => ({ ...task, id: String(i), description: `Review section ${i + 1} of the notes and collect its next steps` })) }; publish()
      await page.getByRole('button', { name: '20 background tasks', exact: true }).click(); await panel.waitFor(); await fullyVisible()
      assert.equal(await panel.locator('.tt-tasks-body').evaluate((n) => n.scrollHeight > n.clientHeight), true)
      await panel.locator('.tt-task-row').last().scrollIntoViewIfNeeded(); await panel.locator('.tt-task-row').last().click()
      await fullyVisible()
      if (width === 390) {
        const handle = await panel.locator('.sheet-top').boundingBox(), cdp = await context.newCDPSession(page)
        const x = handle.x + handle.width / 2, y = handle.y + 8
        const touch = (type, offset = 0) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y: y + offset }] })
        await touch('touchStart'); await touch('touchMove', 180); await touch('touchEnd')
        await panel.waitFor({ state: 'hidden' }); await cdp.detach()
      } else {
        await page.locator('.tt-tasks-scrim').click({ position: { x: 10, y: 10 } })
        await panel.waitFor({ state: 'hidden' })
      }
      assert.deepEqual(errors, [])
      results.push({ width, theme, geometry, noFlowWhileBusy: true, minutesTick: true, identicalTaskIds: true, keyboardFocusReturn: true, emptyCloses: true, newWorkCancelsClose: true, scrollableLongList: true, tabWraps: true, outsideOrDragDismiss: true, errors })
      console.log(`PASS ${width} ${theme}: counts, live tasks/details, minute clock, empty/fade/focus, clear flow, generation and long lists`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
  const captures = []
  for (const file of (await readdir(out)).filter((file) => file.endsWith('.png')).sort()) captures.push({ file, sha256: createHash('sha256').update(await readFile(path.join(out, file))).digest('hex') })
  await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
} finally { await browser.close() }
