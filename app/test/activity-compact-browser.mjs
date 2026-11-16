// Real app/relay and contract fixtures. Runtime names are optional evidence data.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { fakeBroker, appAgainst, closing } from './harness.js'
import { activityFixture } from '../tools/activity-fixture.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright-core')
const output = process.env.ACTIVITY_COMPACT_EVIDENCE
if (!output) throw new Error('Set ACTIVITY_COMPACT_EVIDENCE to a directory outside the repository')
const relative = path.relative(process.cwd(), path.resolve(output))
if (!relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)) throw new Error('Evidence must stay outside the repository')
await mkdir(output, { recursive: true })
const agents = JSON.parse(process.env.ACTIVITY_COMPACT_AGENTS || '[{"id":"test-agent","name":"Test agent","emoji":null},{"id":"review-agent","name":"Reviewer","emoji":null}]')
const [first, second = first] = agents
const date = (days, hours, minutes = 0) => {
  const d = new Date(); d.setDate(d.getDate() - days); d.setHours(hours, minutes, 0, 0); return d.toISOString()
}
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const frame = (event) => `data: ${JSON.stringify(event)}\n\n`
const results = []
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const fixture = activityFixture(agents)
    fixture.items.splice(0, fixture.items.length,
      { id: 'job-landed', kind: 'job', agent: first.id, actor: first.id, title: 'Activity moves out of the tabs', outcome: 'completed', ok: true, landing: 'landed', started: date(0, 11), ts: date(0, 11, 5), receipt: 'Receipts/activity.md', seen: false },
      { id: 'run-failed', kind: 'fire', agent: first.id, actor: 'loam', record: 'reflection', outcome: 'failed', started: date(0, 10), finished: date(0, 10, 2), ts: date(0, 10, 2), produced: { line: { error: 'Provider login expired.' }, failure_log: 'Inbox/failures.md' }, seen: false },
      { id: 'reflection-ran', kind: 'fire', agent: second.id, actor: 'loam', record: 'reflection', outcome: 'ran', started: date(0, 9), finished: date(0, 9, 5), ts: date(0, 9, 5), seen: false },
      { id: 'job-cancelled', kind: 'job', agent: first.id, title: 'Review the export flow', outcome: 'cancelled', ok: false, started: null, ts: date(0, 8, 50), receipt: null, seen: false },
      { id: 'change-refused', kind: 'change', agent: first.id, actor: 'engineer', record: 'weekly-review', op: 'move', ts: date(0, 8, 40), diff: { header: { schedule: ['Sundays at 05:00 PT', 'Sundays at 06:00 PT'] } }, reason: 'Move the review after the weekly digest.', seen: false },
      { id: 'change-denied', kind: 'refused', agent: first.id, actor: 'engineer', record: 'reflection', ts: date(0, 8, 30), why: 'The nightly reflection cannot be deleted.', seen: false },
      { id: 'yesterday-run', kind: 'fire', agent: first.id, actor: 'loam', record: 'reflection', outcome: 'ran', started: date(1, 11), finished: date(1, 11, 5), ts: date(1, 11, 5), seen: true },
      { id: 'older-run', kind: 'run', agent: first.id, record: 'weekly-review', ts: date(3, 9), seen: true },
    )
    // Keep only the deliberate records above; the Schedule fake still owns Undo.
    fixture.schedules.activities.clear()
    const records = [
      { session_id: 'pinned-chat', title: 'Plan the next release', status: 'done', last_message: 'The plan is ready to review.', pinned: true, last_activity: date(1, 8) },
      { session_id: 'recent-chat', title: 'Review the implementation', status: 'working', last_message: 'I’m reviewing the changes and checking the results.', pinned: false, last_activity: date(1, 7) },
    ].map((r) => ({ agent: first.id, provider: 'claude-code', state: 'running', live: true, created: r.last_activity, last_event_at: r.last_activity, archived: false, record_seq: 3, seen_seq: 0, background_tasks: 0, pending_permissions: [], turns: [], ...r }))
    const streams = []
    const common = {
      'GET /v1/agents': (req, res) => json(res, { agents }),
      'GET /v1/providers': (req, res) => json(res, { providers: [{ id: 'claude-code', name: 'Claude Code', connected: true, usage: null }] }),
      'GET /v1/limits': (req, res) => json(res, { attachment_max_bytes: 10000000, attachment_types: [] }),
      'GET /v1/sessions': (req, res) => json(res, { sessions: records, live_count: 2, cap: 4 }),
      'GET /v1/sessions/events': (req, res) => {
        res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); streams.push(res)
        for (const record of records) res.write(frame({ type: 'broker.session', ...record }))
      },
      'POST /v1/sessions/seen': (req, res) => {
        for (const record of records) { record.seen_seq = record.record_seq; for (const stream of streams) stream.write(frame({ type: 'broker.session', ...record })) }
        json(res, { marked: records.length })
      },
    }
    for (const record of records) {
      common[`PATCH /v1/sessions/${record.session_id}`] = (req, res, url, raw) => {
        Object.assign(record, JSON.parse(raw)); json(res, record)
        for (const stream of streams) stream.write(frame({ type: 'broker.session', ...record }))
      }
      common[`GET /v1/sessions/${record.session_id}/events`] = (req, res) => {
        res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n')
        res.write(frame({ type: 'broker.session', ...record, window_first_seq: 1, turns_before: 0 }))
        res.write(frame({ type: 'broker.user_message', seq: 1, text: 'Please review the plan.' }))
        res.write(frame({ type: 'assistant', seq: 2, message: { id: record.session_id, content: [{ type: 'text', text: record.last_message }] } }))
        res.end(frame({ type: 'result', seq: 3, is_error: false, usage: null, total_cost_usd: null }))
      }
    }
    const routes = new Proxy(common, { get: (target, key) => target[key] || ((req, res, url, raw) => {
      const answer = fixture.handle(req.method, decodeURIComponent(url.pathname), url, raw ? JSON.parse(raw) : {}) ?? fixture.files(req.method, url.pathname, url)
      if (answer) return json(res, answer.body, answer.status)
      json(res, { error: 'No such fixture route.' }, 404)
    }) })
    const broker = await fakeBroker(routes), app = await appAgainst(broker)
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, timezoneId: 'America/Los_Angeles', isMobile: width === 390, hasTouch: width === 390 })
    try {
      const page = await context.newPage(), errors = [], checks = []
      page.setDefaultTimeout(10000)
      page.on('pageerror', (e) => errors.push(e.message))
      const shot = async (name) => {
        await page.mouse.move(width - 1, 1); await page.evaluate(() => document.fonts.ready); await page.waitForTimeout(200)
        await page.screenshot({ path: path.join(output, `${name}-${width}-${theme}.png`) })
      }
      const activityNav = async () => {
        if (width === 390) {
          await page.getByRole('button', { name: 'Settings', exact: true }).click()
          await page.locator('.setrow').filter({ hasText: 'Activity' }).click()
        } else await page.locator('.rail-foot button[title="Activity"]').click()
        await page.locator('.activity-item').first().waitFor()
      }
      const chips = page.locator('.activity-chips')
      await page.goto(app.url)
      assert.equal((await page.request.get(`${app.url}/up`)).status(), 200)
      await page.locator('.srow').first().waitFor()
      const mainRows = await page.locator('.srow').evaluateAll((nodes) => nodes.map((n) => n.innerHTML))
      await activityNav()
      await shot('main-activity-overview')
      assert.equal(fixture.calls.filter((c) => c.path === '/v1/activity/seen').length, 0)
      assert.deepEqual(await chips.locator('button').allTextContents(), ['All', 'Chats', 'Runs', 'Jobs', 'Changes'])
      assert.equal(await page.locator('.activity-filter select').count(), 1)
      await page.locator('.activity-page').evaluate((el) => { el.scrollTop = el.querySelector('.activity-feed').offsetTop - el.offsetTop - 20 })
      await shot('main-activity-feed')
      const metrics = await page.locator('.activity-item').evaluateAll((nodes) => nodes.map((n) => ({
        text: n.innerText, height: n.getBoundingClientRect().height,
        summaryHeight: n.querySelector('.activity-summary').getBoundingClientRect().height,
        timeHeight: n.querySelector('.activity-time').getBoundingClientRect().height,
        buttons: n.querySelectorAll('button').length,
        failed: n.classList.contains('activity-failed'), color: getComputedStyle(n.querySelector('.activity-summary')).color,
        errorColor: getComputedStyle(n).getPropertyValue('--danger').trim(),
      })))
      for (const metric of metrics) {
        assert.ok(metric.height <= 70, JSON.stringify(metric))
        assert.equal(metric.summaryHeight, 22); assert.equal(metric.timeHeight, 20); assert.equal(metric.buttons, 0)
        assert.doesNotMatch(metric.text, /Loam ·|Scheduled runs|Started|Finished|Unseen/)
      }
      assert.equal(await page.locator('.activity-item').first().locator('.activity-action').innerText(), 'landed')
      assert.ok(metrics.some((m) => m.failed && /Provider login expired/.test(m.text)))
      assert.notEqual(metrics.find((m) => m.failed).color, metrics.find((m) => !m.failed).color)
      assert.equal(await page.locator('.activity-unread').first().innerText(), '')
      assert.ok((await page.locator('.activity-day-title').allTextContents()).includes('Yesterday'))
      checks.push('two visual lines, agent identity, failure reason/color, dots, day groups, quiet agent select')
      if (width === 390) {
        await page.locator('.activity-page').evaluate((el) => { el.scrollTop = el.scrollHeight })
        const last = await page.locator('.activity-day').last().boundingBox(), nav = await page.locator('.nav').boundingBox()
        assert.ok(last.y + last.height <= nav.y, JSON.stringify({ last, nav }))
        await shot('main-bottom-clearance')
      }
      await chips.getByRole('button', { name: 'Chats', exact: true }).click()
      await page.waitForFunction(() => !document.querySelector('.activity-item'))
      const mainChat = page.locator('.activity-page .srow').last()
      if (width === 1440) await mainChat.click({ modifiers: ['Control'] })
      else {
        await mainChat.dispatchEvent('pointerdown', { pointerType: 'touch', clientX: 40, clientY: 400 })
        await page.getByText('1 chat selected', { exact: true }).waitFor()
        await mainChat.dispatchEvent('pointerup', { pointerType: 'touch' })
      }
      await page.getByRole('toolbar', { name: 'Selected chats' }).waitFor()
      await page.getByRole('button', { name: 'Cancel', exact: true }).click()
      checks.push('main Activity chat rows retain selection controls')
      await chips.getByRole('button', { name: 'Runs', exact: true }).click()
      await page.waitForFunction(() => document.querySelectorAll('.activity-item').length === 4)
      await page.getByRole('combobox', { name: 'Filter by agent' }).selectOption(second.id)
      await page.waitForFunction(() => document.querySelectorAll('.activity-item').length === 1)
      assert.match(await page.locator('.activity-item').innerText(), new RegExp(second.name))
      await page.getByRole('combobox', { name: 'Filter by agent' }).selectOption('')
      await page.waitForFunction(() => document.querySelectorAll('.activity-item').length === 4)
      assert.equal(await page.locator('.activity-chat').count(), 0)
      await chips.getByRole('button', { name: 'Changes', exact: true }).click()
      const change = page.locator('.activity-item').filter({ hasText: 'weekly-review moved' })
      await change.focus(); await page.keyboard.press('Enter')
      await page.locator('.activity-detail').waitFor()
      await page.locator('.activity-detail').getByRole('button', { name: 'Undo', exact: true }).click()
      await page.locator('.activity-detail [role="alert"]').waitFor()
      await shot('change-detail-undo-refusal')
      await page.keyboard.press('Escape')
      assert.equal(await change.evaluate((el) => el === document.activeElement), true)
      assert.equal(fixture.items.find((i) => i.id === 'change-refused').seen, true)
      checks.push('grouped kind query, keyboard open/close/focus, open marks seen, Undo refusal retained in details')
      await chips.getByRole('button', { name: 'Jobs', exact: true }).click()
      await page.locator('.activity-item').filter({ hasText: 'Activity moves out of the tabs' }).click()
      await page.locator('.fs-preview.md').waitFor()
      assert.equal(fixture.items.find((i) => i.id === 'job-landed').seen, true)
      checks.push('receipt opens through Files and marks host seen')
      // Return to Chats, then enter the profile via its existing agent gesture.
      if (width === 390) {
        // A phone file preview hides navigation; browser Back returns to the Files list.
        await page.goBack()
        await page.locator('.navpill button').getByText('Chats', { exact: true }).click()
      } else await page.locator('.rail-tabs button').filter({ hasText: 'Chats' }).click()
      await page.locator('.persona').filter({ hasText: first.name }).first().dispatchEvent('contextmenu')
      const profile = page.locator('.profile-activity')
      await profile.locator('.activity-item').first().waitFor()
      assert.equal(await page.locator('.segmented button').first().textContent(), 'Activity')
      assert.equal(await profile.locator('.activity-filter').count(), 0)
      assert.equal(await page.getByRole('button', { name: `New chat with ${first.name}`, exact: true }).count(), 1)
      assert.equal(await profile.locator('.pinned-card .srow').count(), 1)
      assert.deepEqual(await profile.locator('.srow').evaluateAll((nodes) => nodes.map((n) => n.innerHTML)), mainRows)
      assert.equal(await profile.locator('.activity-item').filter({ hasText: second.name }).count(), 0)
      await shot('agent-activity-overview')
      await page.locator('.profile-body').evaluate((el) => { el.scrollTop = el.querySelector('.profile-activity').offsetTop - el.offsetTop - 12 })
      await shot('agent-activity-feed')
      checks.push('profile Activity default, scoped feed, same chat DOM, pinned chats, New chat retained')
      await profile.getByRole('button', { name: 'Chats', exact: true }).click()
      await page.waitForFunction(() => !document.querySelector('.profile-activity .activity-item'))
      assert.equal(await profile.locator('.srow').count(), 2)
      if (width === 1440) await profile.locator('.srow').last().click({ modifiers: ['Control'] })
      else {
        const chat = profile.locator('.srow').last(); await chat.scrollIntoViewIfNeeded()
        await chat.dispatchEvent('pointerdown', { pointerType: 'touch', clientX: 40, clientY: 400 })
        await page.getByText('1 chat selected', { exact: true }).waitFor()
        await chat.dispatchEvent('pointerup', { pointerType: 'touch' })
      }
      await page.getByRole('toolbar', { name: 'Selected chats' }).first().waitFor()
      await page.getByRole('button', { name: 'Cancel', exact: true }).first().click()
      await profile.getByRole('button', { name: 'All', exact: true }).click()
      await profile.locator('.activity-item').first().waitFor()
      await profile.getByRole('button', { name: 'Mark all seen', exact: true }).click()
      await page.waitForFunction(() => !document.querySelector('.profile-activity .activity-unread'))
      assert.ok(fixture.items.filter((i) => i.agent === first.id).every((i) => i.seen))
      assert.equal(fixture.items.find((i) => i.agent === second.id).seen, false)
      checks.push('Chats chip, chat selection gestures/actions, Mark all seen stays agent-scoped')
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      assert.deepEqual(errors, [])
      results.push({ width, theme, source: 'contract fixtures through the real relay; no live broker', metrics, checks, errors })
    } finally { await context.close(); for (const stream of streams) stream.end(); await closing(app, broker)() }
  }
  await writeFile(path.join(output, 'checks.json'), JSON.stringify(results, null, 2) + '\n')
  console.log(JSON.stringify({ passed: results.length, errors: results.flatMap((r) => r.errors), rowHeights: [...new Set(results.flatMap((r) => r.metrics.map((m) => m.height)))] }))
} finally { await browser.close() }
