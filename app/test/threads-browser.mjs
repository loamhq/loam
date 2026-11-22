import { chooseThreadOption } from './thread-controls.mjs'
// Real browser + real relay, synthetic runtime only; no vault or agents touched.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { createThreadFixture } from '../public/threads-fixture.js'
import { fakeBroker, appAgainst, closing } from './harness.js'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.THREADS_EVIDENCE
if (!out) throw new Error('Set THREADS_EVIDENCE to the evidence directory')
await mkdir(out, { recursive: true })
const f = createThreadFixture()
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const classic = { session_id: 'classic-chat', agent: 'scout', provider: 'claude-code', title: 'A conversation already in flight',
  created: new Date().toISOString(), last_activity: new Date().toISOString(), last_event_at: new Date().toISOString(),
  state: 'running', status: 'done', live: true, record_seq: 3, seen_seq: 3, pinned: false, archived: false, turns: [], pending_permissions: [], background_tasks: 0, last_message: 'The existing session is still here.' }
const routes = {
  'GET /v1/agents': (req, res) => json(res, { agents: f.agents }),
  'GET /v1/providers': (req, res) => json(res, { providers: f.providers }),
  'GET /v1/sessions': (req, res) => json(res, { sessions: [classic], live: 1, cap: 2 }),
  'GET /v1/sessions/events': (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'broker.session', ...classic })}\n\n`) },
  'GET /v1/sessions/classic-chat/events': (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n')
    for (const event of [{ type: 'broker.session', ...classic, window_first_seq: 1, turns_before: 0 },
      { type: 'broker.user_message', text: 'Carry on with our earlier conversation.', seq: 1 },
      { type: 'assistant', message: { id: 'classic-reply', role: 'assistant', content: [{ type: 'text', text: 'The existing session is still here.' }] }, seq: 2 },
      { type: 'result', subtype: 'success', is_error: false, seq: 3 }]) res.write(`data: ${JSON.stringify(event)}\n\n`)
  },
  'PATCH /v1/sessions/classic-chat': (req, res) => json(res, classic),
}
let supportsThreads = false
routes['GET /v1/threads'] = async (req, res) => supportsThreads ? json(res, emptyListing ? { threads: [], agents: f.agents } : await f.api('GET', '/threads')) : json(res, { error: 'not available yet' }, 405)
routes['GET /v1/threads/events'] = (req, res) => {
  if (!supportsThreads) return json(res, { error: 'not available yet' }, 405)
  res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n')
  const stop = emptyListing ? (() => { res.write(`data: ${JSON.stringify({ type: 'threads.snapshot', threads: [], agents: f.agents })}\n\n`); return () => {} })() : f.subscribeList((e) => res.write(`data: ${JSON.stringify(e)}\n\n`)); res.on('close', stop)
}
routes['POST /v1/threads'] = async (req, res, url, body) => {
  const t = await f.api('POST', '/threads', JSON.parse(body)); addRoutes(t.id); json(res, t, 201)
}
let lastMessage = null
let refuseSend = false
let messageDelay = 0
let refuseRead = false
let paginate = false
let emptyListing = false
let lastAnswer = null
let refuseAnswer = false
const streams = new Map()
function emit(id, event) { for (const res of streams.get(id) ?? []) res.write(`data: ${JSON.stringify(event)}\n\n`) }
for (const id of ['approval', 'question']) routes[`POST /v1/sessions/fixture-session/permissions/${id}`] = (req, res, url, body) => {
  lastAnswer = JSON.parse(body)
  if (refuseAnswer) return json(res, { error: 'Please review this answer before sending again.' }, 400)
  json(res, { behavior: lastAnswer.behavior })
  emit('a-quieter-week', { type: 'broker.permission_resolved', request_id: id, session_id: 'fixture-session' })
}
function addRoutes(id) {
  const base = `/v1/threads/${id}`
  for (const [method, suffix] of [['GET', ''], ['PATCH', ''], ['POST', '/messages']]) {
    routes[`${method} ${base}${suffix}`] = async (req, res, url, raw) => {
      if (method === 'GET' && refuseRead) return json(res, { error: 'The vault could not be read.' }, 503)
      if (suffix === '/messages' && refuseSend) return json(res, { error: 'The engine is taking a moment. Try again shortly.' }, 429)
      if (suffix === '/messages' && messageDelay) await new Promise((r) => setTimeout(r, messageDelay))
      const body = raw ? JSON.parse(raw) : undefined
      if (suffix === '/messages') lastMessage = body
      const answer = await f.api(method, `/threads/${id}${suffix}`, body)
      if (method === 'GET' && !suffix && paginate) {
        const end = answer.messages.length - 2
        answer.messages = url.searchParams.has('before') ? answer.messages.slice(0, end)
          : answer.messages.slice(end).map((m) => ({ ...m, text: m.kind === 'message' ? (m.text + '\n\n').repeat(25) : m.text }))
        answer.has_more = !url.searchParams.has('before')
      }
      json(res, answer)
    }
  }
  routes[`GET ${base}/events`] = (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n')
    if (!streams.has(id)) streams.set(id, new Set())
    streams.get(id).add(res)
    const stop = f.subscribe(id, (event) => res.write(`data: ${JSON.stringify(event)}\n\n`), req.url.includes('after=') ? new URL(req.url, 'http://fixture').searchParams.get('after') : null)
    res.on('close', () => { stop(); streams.get(id).delete(res) })
  }
}
for (const t of (await f.api('GET', '/threads')).threads) addRoutes(t.id)
const broker = await fakeBroker(routes), app = await appAgainst(broker, { LOG_LEVEL: 'off' })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
const results = []
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const firstRequest = broker.seen.length
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width < 1100, hasTouch: width < 1100 })
    const page = await context.newPage(), errors = []
    page.on('pageerror', (e) => errors.push(e.message))
    page.on('console', (e) => { if (e.type() === 'error' && !e.text().includes('status of')) errors.push(e.text()) })
    const shot = async (name) => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await Promise.all(document.getAnimations().filter((a) => a.effect.getTiming().iterations !== Infinity).map((a) => a.finished.catch(() => {}))) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name} overflows`)
      await page.screenshot({ animations: 'disabled', path: path.join(out, `${name}-${width}-${theme}.png`) })
    }
    await page.goto(`${app.url}/?threads=fixture`)
    await page.getByRole('button', { name: /General.*Sprout/ }).waitFor()
    await shot('chats')
    assert.equal(await page.locator('.tt-row').count(), 4)
    await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click()
    await page.locator('.fold-head').first().click()
    await page.locator('#draft[contenteditable=true]').fill('A thought for @qu')
    await page.locator('.tt-mentions').waitFor()
    if (width < 1100) await page.locator('.tt-message').filter({ hasText: 'A calmer week starts' }).evaluate((node) => node.scrollIntoView({ block: 'start' }))
    await shot('thread')
    assert.equal(await page.locator('.fold-head').first().getAttribute('aria-expanded'), 'true')
    await page.locator('.tt-mentions button').click()
    assert.equal(await page.locator('#draft').innerText(), 'A thought for @quill ')
    if (width < 1100) await page.getByRole('button', { name: 'Back to threads' }).click()
    await page.locator('.tt-row').filter({ hasText: 'General' }).click()
    await page.locator('.tt-speaker .tag').first().waitFor()
    await shot('general')
    if (width < 1100) {
      await page.getByRole('button', { name: 'Back to threads' }).click()
    }
    await page.getByRole('button', { name: 'Settings', exact: true }).first().click()
    await shot('settings')
    await page.getByRole('button', { name: 'Chats', exact: true }).first().click()
    await page.getByRole('button', { name: 'Ledger menu', exact: true }).click()
    await page.getByRole('button', { name: 'New thread', exact: true }).click()
    await page.getByRole('heading', { name: 'A new thread with Ledger', exact: true }).waitFor()
    await shot('empty-thread')
    assert.equal(broker.seen.slice(firstRequest).filter((r) => r.path.startsWith('/v1/threads') && r.method !== 'GET').length, 0, 'preview content never writes to the runtime')
    assert.deepEqual(errors, [])
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    console.log(`Painted and checked ${width}px ${theme}`)
    results.push({ width, theme, fixture: true, screenshots: 7, errors })
    await context.close()
  }
  // Automatic default: old runtime keeps today's UI, new runtime opens threads.
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } }), page = await context.newPage()
  await page.goto(app.url)
  await page.locator('.home').waitFor()
  assert.equal(await page.locator('.tt-shell').count(), 1)
  supportsThreads = true
  // The periodic probe must switch without a reload when the broker restarts.
  await page.locator('.tt-shell').waitFor({ timeout: 22000 })
  await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click()
  await page.locator('#draft[contenteditable=true]').fill('Keep it simple, @pip')
  await page.locator('.tt-mentions button').click()
  messageDelay = 2000
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await page.locator('.tt-message.mine').filter({ hasText: 'Keep it simple, @pip' }).waitFor()
  await page.locator('#draft[contenteditable=false]').waitFor()
  assert.equal(await page.locator('#draft').isEditable(), false)
  await page.getByText('You brought in Pip', { exact: true }).waitFor()
  await page.locator('.fold-head .live').filter({ hasText: 'Read the thread and gathered the open questions' }).waitFor()
  await page.screenshot({ animations: 'disabled', path: path.join(out, 'live-step-390-light.png') })
  await page.waitForFunction(() => document.querySelector('.tt-stream')?.getAttribute('aria-busy') === 'false')
  assert.equal(await page.locator('.tt-head .blob, .tt-avatar-stack').count(), 0)
  messageDelay = 0
  assert.equal(lastMessage.text, 'Keep it simple, @pip')
  await page.getByTitle('Thread details', { exact: true }).click()
  await chooseThreadOption(page, 'Provider for Pip', 'codex')
  await page.waitForFunction(() => document.querySelector('[aria-label="Provider for Pip"]')?.getAttribute('data-value') === 'codex')
  await page.getByRole('button', { name: 'Chat', exact: true }).click()
  assert.equal(await page.locator('.tt-member-strip').count(), 0, 'phone stream has no engine band')
  await page.getByTitle('Thread details', { exact: true }).click()
  await chooseThreadOption(page, 'Provider for Pip', 'claude-code')
  await page.getByRole('button', { name: 'Chat', exact: true }).click()
  refuseSend = true
  await page.locator('#draft[contenteditable=true]').fill('Keep this if sending fails')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await page.getByRole('alert').filter({ hasText: 'The engine is taking a moment' }).waitFor()
  assert.equal(await page.locator('#draft').innerText(), 'Keep this if sending fails')
  assert.ok(broker.seen.some((r) => r.path === '/v1/threads/a-quieter-week/messages' && r.auth))
  console.log('Checked automatic recovery, live steps and engine changes')
  results.push({ relay: true, automaticDefault: true, streamedReply: true, mentionJoined: true, engineChanged: true, refusedDraftRetained: true })
  refuseSend = false
  await page.getByRole('button', { name: 'Back to threads' }).click()
  refuseRead = true
  await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click()
  await page.getByRole('alert').filter({ hasText: 'The vault could not be read' }).waitFor()
  refuseRead = false
  results.push({ slowSendReadonly: true })
  await context.close()

  for (const width of [390, 1440]) {
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 } }), page = await context.newPage()
    await page.goto(app.url)
    await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click()
    await page.locator('.tt-message').first().waitFor()
    emit('a-quieter-week', { type: 'broker.permission_request', request_id: 'approval', session_id: 'fixture-session', persona: 'sprout', message_id: 'm3',
      tool_name: 'Write', decision_reason: 'Save the short plan to this thread’s workspace.', input: { file_path: 'notes/plan.md', content: 'A calm week starts here.' } })
    await page.getByRole('button', { name: 'Allow', exact: true }).scrollIntoViewIfNeeded()
    await page.screenshot({ animations: 'disabled', path: path.join(out, `permission-${width}-light.png`) })
    refuseAnswer = true
    await page.getByRole('button', { name: 'Allow', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: 'Please review this answer' }).waitFor()
    refuseAnswer = false
    await page.getByRole('button', { name: 'Allow', exact: true }).click()
    await page.getByRole('button', { name: 'Allow', exact: true }).waitFor({ state: 'detached' })
    assert.deepEqual(lastAnswer, { behavior: 'allow' })
    emit('a-quieter-week', { type: 'broker.permission_request', kind: 'question', request_id: 'question', session_id: 'fixture-session', persona: 'quill', message_id: 'm4', tool_name: 'AskUserQuestion',
      questions: [
        { id: 'tone', question: 'What should the draft feel like?', required: true, multi: false, other: true, options: [{ label: 'Warm and clear', description: 'A friendly voice, with room to breathe.' }, { label: 'Short and practical' }] },
        { id: 'include', question: 'What should we include?', required: true, multi: true, other: false, options: [{ label: 'The next step' }, { label: 'A little context' }] },
        { id: 'note', question: 'Anything else?', required: false, multi: false, other: true, options: [] },
      ] })
    await page.getByRole('radio', { name: /Warm and clear/ }).click()
    await page.getByRole('radiogroup', { name: 'What should the draft feel like?', exact: true }).getByRole('radio', { name: 'Other', exact: true }).click()
    await page.getByRole('textbox', { name: 'What should the draft feel like?', exact: true }).fill('Gentle and spare')
    assert.equal(await page.getByRole('radio', { name: /Warm and clear/ }).getAttribute('aria-checked'), 'false')
    await page.getByRole('radio', { name: /Warm and clear/ }).click()
    await page.getByRole('checkbox', { name: 'The next step', exact: true }).click()
    await page.getByRole('checkbox', { name: 'A little context', exact: true }).click()
    await page.locator('.tt-approval').getByRole('button', { name: 'Send', exact: true }).scrollIntoViewIfNeeded()
    await page.screenshot({ animations: 'disabled', path: path.join(out, `question-${width}-light.png`) })
    refuseAnswer = true
    await page.locator('.tt-approval').getByRole('button', { name: 'Send', exact: true }).click()
    await page.locator('.qrefused').filter({ hasText: 'Please review this answer' }).waitFor()
    assert.equal(await page.getByRole('radio', { name: /Warm and clear/ }).getAttribute('aria-checked'), 'true')
    refuseAnswer = false
    await page.locator('.tt-approval').getByRole('button', { name: 'Send', exact: true }).click()
    await page.locator('.tt-approval').getByRole('button', { name: 'Send', exact: true }).waitFor({ state: 'detached' })
    assert.deepEqual(lastAnswer, { behavior: 'allow', answers: { tone: ['Warm and clear'], include: ['The next step', 'A little context'] } })
    console.log(`Checked shared cards at ${width}px`)
    results.push({ width, permission: true, multiQuestion: true, optionalAnswer: true, refusedAnswersRetained: true })
    await context.close()
  }
  // A real runtime failure is a durable system line, not a persona reply.
  // Reconcile its active turn through a non-marking history read.
  const failureContext = await browser.newContext({ viewport: { width: 390, height: 844 } }), failurePage = await failureContext.newPage()
  await failurePage.goto(app.url)
  await failurePage.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click()
  await failurePage.locator('.tt-message').first().waitFor()
  emit('a-quieter-week', { type: 'turn.started', persona: 'sprout', engine: 'claude-code' })
  emit('a-quieter-week', { type: 'message.delta', persona: 'sprout', text: 'Before a failure', draft: 'Before a failure' })
  await failurePage.getByText('Before a failure', { exact: true }).waitFor()
  const failureStart = broker.seen.length
  emit('a-quieter-week', { type: 'message.done', message: { id: 'm999', author: 'system', kind: 'system', text: 'The engine could not start this turn.' } })
  emit('a-quieter-week', { type: 'turn.finished', persona: 'sprout' })
  await failurePage.getByText('The engine could not start this turn.', { exact: true }).waitFor()
  await failurePage.locator('.say.agent.streaming').waitFor({ state: 'detached' })
  assert.equal(await failurePage.getByText('Before a failure', { exact: true }).count(), 0)
  assert.equal(broker.seen.slice(failureStart).some((r) => r.method === 'GET' && r.search === '?before=m999&limit=1'), false)
  console.log('Checked failed turn settlement')
  results.push({ failedTurnSettlesTyping: true })
  await failureContext.close()
  paginate = true
  const moreContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } }), morePage = await moreContext.newPage()
  await morePage.goto(app.url)
  await morePage.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click()
  // The initial page has only replies. Opening now gathers context back to
  // the person's message automatically; history anchoring under an in-flight
  // page and a concurrent completion is exercised by thread-opening's driver.
  await morePage.locator('.tt-message').first().waitFor()
  await morePage.locator('.tt-message.mine').first().waitFor()
  await morePage.locator('.earlier').waitFor({ state: 'detached' })
  assert.ok(await morePage.locator('.tt-message').count() >= 3)
  assert.ok(await morePage.locator('.tt-stream').evaluate((n) => n.scrollHeight - n.scrollTop - n.clientHeight <= 2), 'opening context keeps a caught-up reader at the end')
  assert.ok(broker.seen.some((r) => r.search.includes('before=') && r.search.endsWith('&limit=30')))
  // Stay above the end while the connection drops. Replaying durable messages
  // must use after=, and must neither GET the thread nor mark unseen replies read.
  await morePage.locator('.tt-stream').evaluate((n) => { n.scrollTop = 0; n.dispatchEvent(new Event('scroll')) })
  const durable = await f.api('GET', '/threads/a-quieter-week')
  const cursor = durable.messages.at(-1).id
  const reconnectStart = broker.seen.length
  for (const res of streams.get('a-quieter-week') ?? []) res.end()
  const missed = await f.api('POST', '/threads/a-quieter-week/messages', { text: 'A message while the connection is down.' })
  await morePage.locator('.tt-message').filter({ hasText: missed.message.text }).waitFor({ timeout: 12000 })
  assert.equal(await morePage.locator('.tt-message').filter({ hasText: missed.message.text }).count(), 1)
  const replay = broker.seen.slice(reconnectStart)
  assert.ok(replay.some((r) => r.path.endsWith('/events') && r.search === `?after=${cursor}`))
  assert.equal(replay.filter((r) => r.path === '/v1/threads/a-quieter-week' && (r.method === 'GET' || r.method === 'PATCH')).length, 0, 'scroll-back reconnect cannot mark the thread read')
  results.push({ reconnectAfterMessageId: true, replayDeduplicated: true, scrollBackNeverMarkedRead: true, liveToolStep: true, immediateSend: true })
  const longTitle = 'A very long thread title with plenty of room to think together '.repeat(6).trim()
  await morePage.getByTitle('Thread details', { exact: true }).click()
  await morePage.getByTitle('Rename thread', { exact: true }).click()
  await morePage.getByRole('textbox', { name: 'Thread title', exact: true }).fill(longTitle)
  await morePage.getByRole('textbox', { name: 'Thread title', exact: true }).press('Enter')
  await morePage.waitForFunction((title) => document.querySelector('.tt-details-identity h3')?.textContent === title, longTitle)
  const added = Array.from({ length: 20 }, (_, i) => `sample-persona-${i}`)
  await f.api('PATCH', '/threads/a-quieter-week', { add: added })
  await morePage.waitForFunction(() => document.querySelectorAll('.tt-member').length > 20)
  assert.equal(await morePage.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
  assert.ok(await morePage.locator('.tt-details-scroll').evaluate((n) => n.scrollHeight > n.clientHeight))
  await morePage.locator('.tt-details-scroll').evaluate((pane) => { const section=pane.querySelector('#tt-members'); pane.scrollTop=section.offsetTop-24 })
  const detailsHead=await morePage.locator('.tt-details-head').boundingBox(), firstMember=await morePage.locator('.tt-member').first().boundingBox()
  assert.ok(firstMember.y>=detailsHead.y+detailsHead.height,'a crowded details page keeps its first member below the header')
  results.push({ optionalPagination: true, readingPositionPreserved: true, longTitle: true, manyMembers: true, crowdedDetailsHeaderClearance:true })
  await moreContext.close()
  emptyListing = true
  const emptyContext = await browser.newContext({ viewport: { width: 390, height: 844 } }), emptyPage = await emptyContext.newPage()
  await emptyPage.goto(app.url)
  await emptyPage.getByText('Room for a new thought', { exact: true }).waitFor()
  assert.equal(await emptyPage.locator('.tt-row').count(), 0, 'the page does not invent General or a thread')
  results.push({ noThreadsEmptyState: true })
  await emptyContext.close()
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
  console.log(JSON.stringify(results, null, 2))
} catch (error) {
  for (const context of browser.contexts()) for (const page of context.pages()) {
    console.error('PAGE AT FAILURE', await page.locator('body').innerText().catch(() => 'unavailable'))
    await page.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {})
  }
  throw error
} finally { await browser.close(); await closing(app, broker)() }
