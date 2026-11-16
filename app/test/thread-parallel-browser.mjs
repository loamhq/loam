import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_PARALLEL_EVIDENCE
if (!out) throw new Error('Set THREAD_PARALLEL_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const sharp = (await import(process.env.LOAM_SHARP || 'sharp')).default
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const results = [], captures = [], id = 'a-quieter-week'
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = [], clients = new Set(), lists = new Set(), posts = []
    const at = new Date().toISOString(), started = (minutes) => new Date(Date.now() - minutes * 60000).toISOString()
    let members = [
      { persona: 'sprout', engine: 'claude-code', model: null, running: true, busy: true, working: true, background: 0, background_tasks: [] },
      { persona: 'quill', engine: 'codex', model: null, running: false, busy: false, working: false, background: 0, background_tasks: [] },
    ]
    let tailLimit = null
    let turns = [{ persona: 'sprout', engine: 'claude-code', model: null, started_at: started(5) }]
    const messages = [{ id: 'm40', author: 'you', kind: 'message', at, text: '@sprout Review the plan. I’ll ask Quill to check the details alongside you.' }]
    const original = await app.f.api('GET', `/threads/${id}`)
    const summary = () => ({ ...original.thread, title: 'Room to work together', members, unread: 0,
      last: { author: messages.at(-1).author, text: messages.at(-1).text, at }, background: members.reduce((n, m) => n + m.background, 0), working: members.filter((m) => m.working).length })
    const snapshot = () => ({ type: 'thread.snapshot', thread: id, turn: turns[0] ?? null, turns, members, pending: [] })
    const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    const write = (res, e) => res.write(`data: ${JSON.stringify(e)}\n\n`)
    const emit = (e) => { for (const res of clients) write(res, e) }
    const publish = () => { emit({ type: 'members', thread: id, members, background: summary().background, working: summary().working }); for (const res of lists) write(res, { type: 'thread.changed', thread: id, summary: summary() }) }
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: [summary()], agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); write(res, { type: 'threads.snapshot', threads: [summary()], agents: app.f.agents }); lists.add(res); res.on('close', () => lists.delete(res)) }
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...original, thread: summary(), messages: tailLimit ? messages.slice(-tailLimit) : messages, has_more: Boolean(tailLimit && messages.length > tailLimit), turns, turn: turns[0] ?? null })
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); write(res, snapshot()); clients.add(res); res.on('close', () => clients.delete(res)) }
    app.routes[`POST /v1/threads/${id}/messages`] = (req, res, url, raw) => {
      const body = JSON.parse(raw); posts.push(body)
      const message = { id: 'm41', author: 'you', kind: 'message', at, text: body.text }; messages.push(message); emit({ type: 'message.done', message })
      members = members.map((m) => m.persona === 'quill' ? { ...m, working: true, running: true, busy: true } : m)
      const turn = { persona: 'quill', engine: 'codex', model: null, started_at: at }; turns.push(turn)
      emit({ type: 'turn.started', ...turn }); publish(); json(res, { accepted: true, message })
    }
    app.routes[`GET /v1/threads/${id}/steps`] = (req, res) => { res.writeHead(404, { 'content-type': 'application/json' }); res.end('{"error":"No full record for this reply"}') }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, reducedMotion: 'reduce', isMobile: width === 390, hasTouch: width === 390 })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__parallel = { threads, state, schedule, goChats };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', (e) => errors.push(e.message))
    const settle = () => page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
    const capture = async () => { await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); return page.screenshot({ animations: 'disabled' }) }
    const pair = async (name, leftLabel, rightLabel, left, right) => {
      await left(); const a = await capture(); await right(); const b = await capture()
      const file = `${name}-${width}-${theme}.png`, dest = path.join(out, file), height = a.readUInt32BE(20)
      const ink = theme === 'dark' ? '#e6e1da' : '#2a2521', bg = theme === 'dark' ? '#1a1715' : '#e6e1da'
      const labels = Buffer.from(`<svg width="${width * 2}" height="32"><text x="14" y="22" fill="${ink}" font-family="sans-serif" font-size="13">${leftLabel}</text><text x="${width + 14}" y="22" fill="${ink}" font-family="sans-serif" font-size="13">${rightLabel}</text></svg>`)
      await sharp({ create: { width: width * 2, height: height + 32, channels: 4, background: bg } }).composite([{ input: a, left: 0, top: 32 }, { input: b, left: width, top: 32 }, { input: labels, left: 0, top: 0 }]).png().toFile(dest)
      captures.push({ file, width, theme, sha256: createHash('sha256').update(await readFile(dest)).digest('hex') }); console.log(file)
    }
    const draft = (who) => page.locator(`[data-message-id="draft:${who}"]`)
    const activity = (who) => draft(who)
    const order = () => page.locator('.tt-message').evaluateAll((nodes) => nodes.map((n) => n.dataset.messageId))
    const thread = async () => { await page.evaluate((id) => __parallel.threads.open(id), id); await page.waitForFunction(() => __parallel.threads.st.open && !__parallel.threads.st.open.loading); await activity('sprout').waitFor(); await settle() }
    const list = async () => { await page.evaluate(() => { __parallel.threads.close(); __parallel.goChats() }); await page.locator('.tt-row').waitFor(); await settle() }
    try {
      await page.goto(app.url); await page.locator('.tt-row').waitFor(); await thread()
      emit({ type: 'message.delta', persona: 'sprout', draft: 'I’m reviewing the plan and keeping the next step small.' })
      await draft('sprout').getByText('I’m reviewing the plan and keeping the next step small.').waitFor()
      await page.locator('#draft').fill('@quill Please check the details while Sprout works.')
      await page.evaluate(() => __parallel.threads.send())
      await activity('quill').waitFor(); assert.equal(posts.length, 1)
      assert.deepEqual(await order(), ['m40', 'draft:sprout', 'm41', 'draft:quill'], 'an actively generating member has a waiting slot')
      emit({ type: 'message.delta', persona: 'quill', draft: 'I’m checking the details alongside Sprout.' })
      await draft('quill').getByText('I’m checking the details alongside Sprout.').waitFor()
      await pair('parallel', 'Two members · streaming independently', 'Interleaved tools · replies keep their place', async () => {}, async () => {
        emit({ type: 'turn.step', persona: 'sprout', label: 'Read: plan.md' })
        emit({ type: 'message.delta', persona: 'quill', draft: 'The details line up. I’m checking one last note.' })
        emit({ type: 'message.delta', persona: 'sprout', draft: 'The plan is ready. Two helpers are checking the remaining details.' })
        await draft('sprout').getByText('The plan is ready. Two helpers are checking the remaining details.').waitFor()
        await draft('quill').getByText('The details line up. I’m checking one last note.').waitFor()
        assert.deepEqual(await order(), ['m40', 'draft:sprout', 'm41', 'draft:quill'])
        assert.equal(await draft('quill').getByText('I’m reviewing the plan and keeping the next step small.').count(), 0)
      })
      const reply = { id: 'm42', author: 'sprout', kind: 'message', engine: 'claude-code', at, text: 'The plan is ready. Two helpers are checking the remaining details.' }; messages.push(reply); emit({ type: 'message.done', message: reply })
      members = members.map((m) => m.persona === 'sprout' ? { ...m, working: false, background: 2, background_tasks: [
        { id: 'a', description: 'Review the chip mark and reply timestamp', started_at: started(3) },
        { id: 'b', description: 'Check the phone composer and keyboard focus', started_at: started(2) },
      ] } : m); turns[0] = { ...turns[0], on_background: true }; publish()
      await page.getByRole('button', { name: '2 background tasks', exact: true }).waitFor()
      assert.equal(await page.locator('.tt-member-activity, .tt-live-area').count(), 0)
      await pair('waiting', 'Sprout’s background count · Quill replies', 'Task list · each shows its running time', async () => {}, async () => {
        const toggle = page.locator('.tt-tasks-button')
        if (width === 390) await toggle.tap(); else { await toggle.focus(); await toggle.press('Enter') }
        const panel = page.locator('.tt-tasks-panel')
        await panel.getByText('Check the phone composer and keyboard focus', { exact: true }).waitFor()
        assert.equal(await panel.locator('time').count(), 2)
        assert.match(await panel.innerText(), /3 min/)
        assert.match(await panel.innerText(), /2 min/)
        assert.doesNotMatch(await panel.innerText(), /Thinking|Digging/)
      })
      await page.locator('.tt-tasks-panel').press('Escape')
      // Reconnect is represented by the documented snapshot; both turns and
      // tasks survive without reading or writing a real thread.
      emit(snapshot()); await page.waitForFunction(() => __parallel.threads.st.open.activeTurns.size === 2)
      assert.equal(await draft('quill').getByText('The details line up. I’m checking one last note.', { exact: true }).count(), 1, 'reconnect preserves the continuing member’s draft')
      await page.getByRole('button', { name: '2 background tasks', exact: true }).waitFor()
      await pair('list', 'One working member · two background tasks', 'Both members waiting · three background tasks', async () => {
        await list(); await page.locator('.tt-row .tt-icon-working').waitFor(); assert.equal(await page.locator('.tt-row .beating, .tt-row .srow-bg').count(), 0)
      }, async () => {
        members = members.map((m) => m.persona === 'quill' ? { ...m, working: false, background: 1, background_tasks: [{ id: 'c', description: 'Verify the final note', started_at: at }] } : m); publish()
        await page.locator('.tt-row .tt-thread-icon[data-icon-state=rest]').waitFor()
        assert.equal(await page.locator('.tt-row .tt-icon-layer, .tt-row .srow-bg').count(), 0)
        assert.equal(await page.locator('.tt-row .beating').count(), 0)
      })
      // The list keeps one activity layer while numeric task counts stay in the header.
      members = members.map((m) => ({ ...m, working: true, background: 0, background_tasks: [] })); publish()
      await page.locator('.tt-row .tt-icon-working').waitFor(); assert.equal(await page.locator('.tt-row .beating, .tt-row .srow-bg').count(), 0)
      await thread(); assert.deepEqual(await activity('sprout').count(), 1); assert.equal(await activity('quill').count(), 1)
      members = members.map((m) => ({ ...m, busy: false, running: false, working: false, background: 0, background_tasks: [] })); turns = []; emit(snapshot()); publish()
      await page.waitForFunction(() => document.querySelectorAll('[data-message-id^="draft:"]').length === 0)
      // Live start order can differ from the broker's completion order. A
      // cached revisit that overlaps only two tail replies must not duplicate
      // the earlier-positioned reply when the host sends its durable order.
      members = members.map((m) => ({ ...m, working: true, busy: true, running: true })); publish()
      turns = members.map((m) => ({ persona: m.persona, engine: m.engine, started_at: new Date().toISOString() }))
      for (const turn of turns) emit({ type: 'turn.started', ...turn })
      for (const who of ['sprout', 'quill']) emit({ type: 'message.delta', persona: who, draft: `A new reply from ${who}.` })
      for (const [author, mid] of [['quill', 'm43'], ['sprout', 'm44']]) {
        const message = { id: mid, author, kind: 'message', at: new Date().toISOString(), text: `The completed reply from ${author}.` }
        messages.push(message); emit({ type: 'message.done', message }); emit({ type: 'turn.finished', persona: author })
      }
      members = members.map((m) => ({ ...m, working: false, busy: false, running: false })); turns = []; publish()
      await page.locator('[data-message-id="m44"]').waitFor()
      assert.ok((await order()).indexOf('m44') < (await order()).indexOf('m43'), 'live start positions survive reverse completion')
      tailLimit = 2
      await page.evaluate(async (id) => { __parallel.threads.close(); await __parallel.threads.open(id) }, id)
      await page.waitForFunction(() => !__parallel.threads.st.open.loading && !__parallel.threads.st.open.refreshing)
      assert.equal(await page.locator('[data-message-id="m44"]').count(), 1)
      assert.equal(await page.locator('[data-message-id="m43"]').count(), 1)
      assert.deepEqual((await order()).slice(-2), ['m43', 'm44'], 'fresh tail adopts the host’s durable completion order once')
      assert.deepEqual(errors, [])
      results.push({ width, theme, sendWhileAnotherWorks: true, perMemberStreamPlacement: true, independentToolSteps: true, orderedTurnsSnapshot: true, taskNamesFoldAndElapsed: true, listDotAndCount: true, allSettledDisappears: true, reverseCompletionRevisit: true, errors })
      console.log(`PASS ${width} ${theme}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
  await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
} finally { await browser.close() }
