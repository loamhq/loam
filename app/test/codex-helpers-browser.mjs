// Verify live Codex helper activity (pre-prose stepFold, expanded steps,
// snapshot hydration, completed nested Agent steps, and quiet notification
// cleanup) in real Chromium on phone (390) and desktop (1440), light and dark.
import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.CODEX_HELPERS_EVIDENCE || ''
if (out) await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
const appSource = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const id = 'a-quieter-week', at = '2026-11-26T21:16:00Z'

const fullSteps = [
  { type: 'tool', id: 'call_critics', name: 'Agent', input: { description: 'critics', model: 'gpt-6.2-sol', reasoningEffort: 'xhigh' }, parent: null,
    output: 'Critics praised the lush 18th-century Cairo setting and political intrigue.', is_error: false },
  { type: 'tool', id: 'ws1', name: 'webSearch', input: { query: 'The City of Brass S A Chakraborty review' }, parent: 'call_critics',
    output: '12 results\nKirkus Review — https://example.test/kirkus', is_error: false },
  { type: 'text', text: 'Critics praised the lush 18th-century Cairo setting and political intrigue.', parent: 'call_critics' },
  { type: 'tool', id: 'call_readers', name: 'Agent', input: { description: 'readers', model: 'gpt-6.2-sol', reasoningEffort: 'xhigh' }, parent: null,
    output: 'Your reading notes highlight a strong love for atmospheric historical fantasy.', is_error: false },
  { type: 'tool', id: 'cmd1', name: 'Bash', input: { command: '/bin/bash -lc "cat reading-feedback.md"' }, parent: 'call_readers',
    output: 'Loved The Fifth Season and rich worldbuilding.', is_error: false },
  { type: 'text', text: 'Your reading notes highlight a strong love for atmospheric historical fantasy.', parent: 'call_readers' },
  { type: 'text', text: 'Yes — *The City of Brass* looks like a strong match for you.\n\nCritics highlight its atmospheric 18th-century Cairo setting and court intrigue, which lines up closely with what you noted enjoying in your reading log.', parent: null },
  { type: 'end', event: { type: 'result', subtype: 'success', is_error: false, duration_ms: 42300, model: 'gpt-6.2-sol' } },
]

try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), clients = new Set(), listClients = new Set(), errors = []
    let members = [{ persona: 'sprout', engine: 'codex', model: 'gpt-6.2-sol', working: true, busy: true, running: true, background: 0, background_tasks: [] }]
    let turns = [{ persona: 'sprout', engine: 'codex', model: 'gpt-6.2-sol', started_at: at }]
    const messages = [
      { id: 'm1', author: 'you', kind: 'message', at, text: 'What do you think of City of Brass? Would I like it?' },
    ]
    const original = await app.f.api('GET', `/threads/${id}`)
    const summary = () => ({ ...original.thread, title: 'City of Brass', default: 'sprout', members, unread: 0, background: members[0].background, working: Number(members[0].working), last: messages.at(-1) })
    const snapshot = () => ({ type: 'thread.snapshot', thread: id, members, turns, turn: turns[0] ?? null, pending: [] })
    const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    const emit = (event) => { for (const res of clients) res.write(`data: ${JSON.stringify(event)}\n\n`) }
    const publishList = () => { const e = { type: 'thread.changed', thread: id, summary: summary() }; for (const res of listClients) res.write(`data: ${JSON.stringify(e)}\n\n`) }
    const publish = () => { emit({ type: 'members', members, working: Number(members[0].working), background: members[0].background }); publishList() }

    app.routes['GET /v1/providers'] = (req, res) => json(res, { providers: [{ id: 'codex', name: 'Codex', connected: true, models: [{ id: 'gpt-6.2-sol', name: 'GPT-6.2 Sol' }] }] })
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: [summary()], agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`data: ${JSON.stringify({ type: 'threads.snapshot', threads: [summary()], agents: app.f.agents })}\n\n`); listClients.add(res); res.on('close', () => listClients.delete(res)) }
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...original, thread: summary(), messages, has_more: false, turn: turns[0] ?? null, turns })
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`data: ${JSON.stringify(snapshot())}\n\n`); clients.add(res); res.on('close', () => clients.delete(res)) }
    app.routes[`GET /v1/threads/${id}/steps`] = (req, res, url) => {
      if (url.searchParams.get('message') === 'm2') json(res, { thread: id, steps: [{ message: 'm2', thread: id, author: 'sprout', steps: fullSteps }] })
      else { res.writeHead(404, { 'content-type': 'application/json' }); res.end('{"error":"No full record"}') }
    }

    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, timezoneId: 'America/Los_Angeles', isMobile: width === 390, hasTouch: width === 390 })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: appSource + '\nglobalThis.__codex = { threads };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', (e) => errors.push(e.message))
    const settle = () => page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
    const capture = async (name) => {
      await settle()
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name} overflow`)
      if (out) await page.screenshot({ path: path.join(out, `${name}-${width}-${theme}.png`), animations: 'disabled' })
    }

    try {
      await page.goto(app.url); await page.locator('.tt-row').waitFor(); await page.waitForFunction(() => globalThis.__codex)
      await page.evaluate((id) => __codex.threads.open(id), id)
      const draft = page.locator('[data-message-id="draft:sprout"]')
      await draft.locator('.awaiting-words').waitFor()
      assert.equal(await draft.locator('.fold').count(), 0, 'no fold before first step')

      // Live helper steps arrive before any prose.
      const liveSteps = [
        'Agent: critics',
        'Agent: readers',
        '↳ webSearch: The City of Brass S A Chakraborty review',
        '↳ Bash: cat reading-feedback.md',
      ]
      for (const [i, label] of liveSteps.entries()) {
        emit({ type: 'turn.step', persona: 'sprout', label, steps: i + 1 })
      }
      await draft.locator('.fold-head .live').filter({ hasText: '↳ Bash: cat reading-feedback.md' }).waitFor()
      assert.equal(await draft.locator('.fold-head .spinner').count(), 1, 'live fold shows running spinner')
      assert.equal(await draft.locator('.awaiting-words').count(), 0, 'no duplicate waiting dot while live step fold is running')
      assert.equal(await page.locator('.tt-tasks-button').count(), 0, 'in-turn helpers do not show header background tasks button')
      const liveText = await draft.innerText()
      assert.doesNotMatch(liveText, /subAgentActivity|\/root\//, 'no raw internal labels shown')

      // Expand the live fold mid-run to confirm helper steps are visible and clean.
      await draft.locator('.fold-head').click()
      await draft.locator('.fold-body').waitFor()
      const expandedText = await draft.locator('.fold-body').innerText()
      assert.match(expandedText, /Agent: critics/)
      assert.match(expandedText, /Agent: readers/)
      assert.match(expandedText, /↳ webSearch: The City of Brass/)
      assert.match(expandedText, /↳ Bash: cat reading-feedback\.md/)
      await capture('mid-run')

      // Reconnect mid-run with snapshot carrying turn.step + turn.did hydrates live fold.
      turns = [{ persona: 'sprout', engine: 'codex', model: 'gpt-6.2-sol', started_at: at,
        step: '↳ Bash: cat reading-feedback.md', steps: 4, did: liveSteps.map((label) => ({ label })) }]
      emit(snapshot())
      await draft.locator('.fold-head .live').filter({ hasText: '↳ Bash: cat reading-feedback.md' }).waitFor()

      // Final combined reply lands after all helpers finish.
      const finalText = 'Yes — *The City of Brass* looks like a strong match for you.\n\nCritics highlight its atmospheric 18th-century Cairo setting and court intrigue, which lines up closely with what you noted enjoying in your reading log.'
      emit({ type: 'message.delta', persona: 'sprout', draft: finalText })
      const done = { id: 'm2', author: 'sprout', kind: 'message', engine: 'codex', model: 'gpt-6.2-sol', at: '2026-11-26T21:16:45Z', text: finalText, did: liveSteps.map((label) => ({ label })) }
      messages.push(done)
      emit({ type: 'message.done', message: done })
      emit({ type: 'turn.finished', persona: 'sprout' })
      turns = []
      members = [{ ...members[0], working: false, busy: false, running: false }]
      publish()

      const settledMsg = page.locator('[data-message-id="m2"]')
      await settledMsg.waitFor()
      await page.waitForFunction(() => Array.isArray(__codex.threads.st.open.messages.find((m) => m.id === 'm2')?.steps))
      assert.equal(await page.locator('[data-message-id="draft:sprout"]').count(), 0)
      assert.equal(await page.locator('.awaiting-words').count(), 0)
      // Open the completed steps fold (if not already expanded) and verify nested Agent tool calls match Claude's form.
      const head = settledMsg.locator('.fold-head').first()
      if (await head.getAttribute('aria-expanded') !== 'true') await head.click()
      await settledMsg.locator('.toolrow').first().waitFor()
      const stepsText = await settledMsg.locator('.fold-body').first().innerText()
      assert.match(stepsText, /Agent/)
      assert.match(stepsText, /critics/)
      assert.match(stepsText, /readers/)
      assert.doesNotMatch(stepsText, /subAgentActivity|\/root\//)
      await capture('after')

      // Quiet notification turn after result leaves nothing visible.
      members = [{ ...members[0], working: true }]; publish()
      members = [{ ...members[0], working: false }]; publish()
      await settle()
      assert.equal(await page.locator('[data-message-id="draft:sprout"]').count(), 0)
      assert.equal(await page.locator('.awaiting-words').count(), 0)
      assert.equal(await page.locator('.tt-message').count(), 2)
      assert.deepEqual(errors, [])
      console.log(`PASS ${width} ${theme}`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
