// Synthetic reproduction of a finished reply with one parked member/task.
// Historical reference: render the two landed commits, not today’s header UI.
// Current task UI coverage lives in thread-tasks-browser.mjs.
import assert from 'node:assert/strict'
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_WAITING_REDRAW_EVIDENCE
if (!out) throw new Error('Set THREAD_WAITING_REDRAW_EVIDENCE')
const baseline = process.env.THREAD_WAITING_REDRAW_BASELINE === '1'
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const appSource = await readFile(new URL('../public/app.js', import.meta.url), 'utf8'), results = []
const id = 'a-quieter-week', now = '2026-11-11T00:36:00Z'
try {
  for (const width of [1440, 390]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), clients = new Set(), errors = [], reads = []
    let members = [{ persona: 'sprout', engine: 'claude-code', model: 'opus', working: false, busy: true, running: true, background: 1,
      background_tasks: [{ id: 'one', description: 'Brief Interface: Claude Code 2.1.322, Fable 5.2', started_at: '2026-11-11T00:35:20Z' }] }]
    let turns = [{ persona: 'sprout', engine: 'claude-code', model: 'opus', started_at: '2026-11-11T00:34:00Z', on_background: true }]
    const messages = [
      { id: 'm1', author: 'you', kind: 'message', at: '2026-11-11T00:34:00Z', text: 'Please check the current version and bring back the result.' },
      { id: 'm2', author: 'sprout', kind: 'message', engine: 'claude-code', model: 'claude-opus-5-5', at: '2026-11-11T00:35:00Z',
        text: 'Your expectation is right: the model alias should follow its current version.\n\n**Checked.** The saved choice is still the family alias. The reply records the concrete version it used, so the chip can name it accurately.\n\n**Under way.** A background job is checking the program version and the next reply. I’ll bring its result back to this conversation.\n\nThe conversation stays available while that work runs.', did: [{ label: 'Checked the current version' }], steps: [] },
      // A durable record with tool activity but no prose escaped the previous
      // streaming-only guard. Keep it in the fixture and assert it is not drawn.
      { id: 'm3', author: 'sprout', kind: 'message', engine: 'claude-code', model: 'opus', at: '2026-11-11T00:35:20Z', text: '', did: [{ label: '↳ mcp__engineer__brief' }] },
    ]
    const original = await app.f.api('GET', `/threads/${id}`)
    const summary = () => ({ ...original.thread, title: 'The current version', default: 'sprout', members, unread: 0, background: members[0].background, working: Number(members[0].working) })
    const snapshot = () => ({ type: 'thread.snapshot', thread: id, members, turns, turn: turns[0] ?? null, pending: [] })
    const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    const emit = (event) => { for (const res of clients) res.write(`data: ${JSON.stringify(event)}\n\n`) }
    const publish = () => emit({ type: 'members', members })
    app.routes['GET /v1/providers'] = (req, res) => json(res, { providers: [{ id: 'claude-code', name: 'Claude Code', connected: true, models: [{ id: 'opus', name: 'Opus' }] }] })
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: [summary()], agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`data: ${JSON.stringify({ type: 'threads.snapshot', threads: [summary()], agents: app.f.agents })}\n\n`) }
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...original, thread: summary(), messages, has_more: false, turn: turns[0] ?? null, turns })
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`data: ${JSON.stringify(snapshot())}\n\n`); clients.add(res); res.on('close', () => clients.delete(res)) }
    app.routes[`GET /v1/threads/${id}/steps`] = (req, res, url) => { reads.push(url.searchParams.get('message')); res.writeHead(404, { 'content-type': 'application/json' }); res.end('{"error":"No full record"}') }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, timezoneId: 'America/Los_Angeles', reducedMotion: 'no-preference', isMobile: width === 390, hasTouch: width === 390 })
    for (const file of ['threads.js', 'thread-record.js', 'thread-experience.js', 'threads.css']) {
      await context.route(`**/${file}`, (route) => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: execFileSync('git', ['show', `${baseline ? '9c5db46' : '1976ed0'}:public/${file}`], { encoding: 'utf8' }) }))
    }
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: appSource + '\nglobalThis.__waiting = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000); await page.clock.setFixedTime(new Date(now)); page.on('pageerror', (error) => errors.push(error.message))
    const settle = () => page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
    const capture = async (state) => { await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(out, `${state}-${width}-${theme}.png`), animations: 'disabled' }) }
    const activity = page.locator('.tt-live-area [data-live-member="sprout"]')
    try {
      await page.goto(app.url); await page.locator('.tt-row').waitFor()
      await page.evaluate((id) => __waiting.threads.open(id), id); await activity.waitFor()
      await page.locator('[data-message-id="m2"] .tt-prose-content:not(.tt-prose-plain)').waitFor(); await settle()
      const emptyReply = await page.locator('[data-message-id="m3"]').count()
      let geometry = null, pulse = null
      if (!baseline) {
        assert.equal(emptyReply, 0, 'a working:false member has no completed task-only reply slot')
        assert.equal(await page.locator('.tt-author-run').count(), 1, 'only the finished reply owns an author chip')
        assert.equal(await page.locator('.tt-stream .fold').count(), 0, 'the task-only record draws no waiting fold')
        assert.equal(reads.includes('m3'), false, 'the hidden empty record starts no lazy steps read')
        assert.equal(await activity.locator('button, .chev, .awaiting-dot, .fold').count(), 0, 'one task has no control, chevron, bullet or card')
        assert.match(await activity.innerText(), /Waiting on\s+Claude Code 2\.1\.322, Fable 5\.2\s+<1 min/)
        assert.doesNotMatch(await activity.innerText(), /Sprout is|Brief Interface|Running|less than a minute|mcp__/)
        geometry = await activity.evaluate((node) => {
          const row = node.getBoundingClientRect(), summary = node.querySelector('.tt-wait-summary').getBoundingClientRect(), time = node.querySelector('time').getBoundingClientRect()
          const reply = document.querySelector('[data-message-id="m2"] .say').getBoundingClientRect(), composer = document.querySelector('.composer-box').getBoundingClientRect()
          return { height: row.height, left: row.left - reply.left, timeRight: summary.right - time.right, timeTop: time.top - summary.top, gap: composer.top - row.bottom }
        })
        assert.ok(geometry.height <= 36, JSON.stringify(geometry))
        assert.ok(Math.abs(geometry.left) <= 2, JSON.stringify(geometry))
        assert.ok(Math.abs(geometry.timeRight) <= 2, JSON.stringify(geometry))
        assert.ok(geometry.gap >= 0 && geometry.gap < 40, JSON.stringify(geometry))
        pulse = await activity.locator('.tt-wait-face').evaluate((node) => { const style = getComputedStyle(node, '::after'); return { name: style.animationName, duration: style.animationDuration, tone: node.style.getPropertyValue('--tt-wait-tone') } })
        assert.equal(pulse.name, 'ttWaitRing'); assert.equal(pulse.duration, '5s'); assert.ok(pulse.tone)
        const phases = await activity.evaluate((node) => {
          const animations = node.getAnimations({ subtree: true }), ring = animations.find((animation) => animation.animationName === 'ttWaitRing')
          const face = node.querySelector('.tt-wait-face')
          ring.pause(); ring.currentTime = 0; const low = Number(getComputedStyle(face, '::after').opacity)
          ring.currentTime = 2500; const high = Number(getComputedStyle(face, '::after').opacity); ring.play()
          return { count: animations.length, low, high }
        })
        assert.equal(phases.count, 1); assert.ok(phases.high > phases.low && phases.high < .6)
        Object.assign(pulse, phases)
        await page.emulateMedia({ reducedMotion: 'reduce' })
        assert.equal(await activity.locator('.tt-wait-face').evaluate((node) => getComputedStyle(node, '::after').animationName), 'none')
        await page.emulateMedia({ reducedMotion: 'no-preference' })
      }
      await capture('one-task')
      // A blank completion and a stale live draft cannot resurrect the chip.
      emit({ type: 'message.done', message: { ...messages[2], id: 'm4', text: '  ' } })
      emit({ type: 'message.delta', persona: 'sprout', draft: '' })
      emit(snapshot()); await settle()
      if (!baseline) assert.equal(await page.locator('[data-message-id="m3"], [data-message-id="m4"], [data-message-id="draft:sprout"]').count(), 0)
      members = members.map((m) => ({ ...m, background: 2, background_tasks: [{ ...m.background_tasks[0], started_at: '2026-11-11T00:34:00Z' },
        { id: 'two', description: '↳ Brief Interface: Keep the phone composer clear', started_at: '2026-11-11T00:35:00Z' }] })); publish()
      await page.waitForFunction(() => __waiting.threads.st.open.thread.members[0].background === 2); await settle()
      if (!baseline) {
        assert.equal(await activity.locator('.tt-task-more').innerText(), '+1')
        assert.equal(await activity.locator('.tt-wait-summary time').innerText(), '2 min')
      }
      await capture('two-tasks')
      const toggle = activity.locator('.tt-task-toggle')
      if (width === 390) await toggle.tap(); else { await toggle.focus(); await toggle.press('Enter') }
      await activity.locator('.tt-background-more').waitFor()
      if (!baseline) {
        assert.equal(await activity.locator('.tt-background-task').count(), 2)
        assert.deepEqual(await activity.locator('.tt-background-task time').allTextContents(), ['2 min', '1 min'])
        assert.doesNotMatch(await activity.locator('.tt-background-more').innerText(), /Brief Interface|↳|Running/)
      }
      await capture('tasks-open'); await toggle.click(); await settle()
      members = members.map((m) => ({ ...m, working: true, background: 0, background_tasks: [] })); publish()
      turns = [{ persona: 'sprout', engine: 'claude-code', model: 'opus', started_at: now }]
      emit({ type: 'turn.started', ...turns[0] }); await settle()
      if (!baseline) {
        await page.locator('[data-message-id="draft:sprout"] .awaiting-words').waitFor()
        assert.equal(await activity.count(), 0)
        assert.equal(await page.locator('[data-message-id="draft:sprout"] .fold').count(), 0)
        assert.match(await page.locator('[data-message-id="draft:sprout"]').innerText(), /Thinking/)
        await page.evaluate(() => { globalThis.__generatingNode = document.querySelector('[data-message-id="draft:sprout"]') })
      }
      await capture('generating')
      emit({ type: 'message.delta', persona: 'sprout', draft: 'The new reply is arriving in the same place.' })
      await page.getByText('The new reply is arriving in the same place.', { exact: true }).waitFor()
      if (!baseline) assert.equal(await page.evaluate(() => __generatingNode === document.querySelector('[data-message-id="draft:sprout"]')), true)
      await capture('streaming')
      emit({ type: 'message.done', message: { id: 'm5', author: 'sprout', kind: 'message', engine: 'claude-code', model: 'claude-opus-5-5', at: now, text: 'The new reply is arriving in the same place.' } })
      emit({ type: 'turn.finished', persona: 'sprout' }); members = members.map((m) => ({ ...m, working: false })); turns = []; publish()
      await page.locator('[data-message-id="m5"]').waitFor(); await settle()
      if (!baseline) {
        assert.equal(await page.locator('.tt-live-area [data-live-member]').count(), 0)
        assert.equal(await page.evaluate(() => __generatingNode === document.querySelector('[data-message-id="m5"]')), true)
      }
      assert.deepEqual(errors, [])
      results.push({ width, theme, baseline, emptyReply, geometry, pulse, workingFalseEmptySlotSuppressed: !baseline, generatingNodeStable: !baseline, errors })
      console.log(`PASS ${width} ${theme}: ${baseline ? 'baseline empty slot=' + emptyReply : 'one line, compact time, no empty slot; generating node retained'}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
  const captures = []
  for (const file of (await readdir(out)).filter((file) => file.endsWith('.png')).sort()) captures.push({ file, sha256: createHash('sha256').update(await readFile(path.join(out, file))).digest('hex') })
  await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
} finally { await browser.close() }
