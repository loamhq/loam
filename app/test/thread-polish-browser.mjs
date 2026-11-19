import { chooseThreadOption } from './thread-controls.mjs'
// Real Chromium + the real relay; synthetic records, microphone and transcription.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { createThreadFixture } from '../public/threads-fixture.js'
import { fakeBroker, appAgainst, closing } from './harness.js'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.THREAD_POLISH_EVIDENCE
if (!out) throw new Error('Set THREAD_POLISH_EVIDENCE to a retained evidence directory')
await mkdir(out, { recursive: true })
const f = createThreadFixture(), streams = new Map(), created = new Set(), results = []
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
let releaseCreate = null, createDelay = 0, expire = false, oldModels = false
const routes = {
  'GET /v1/agents': (req, res) => json(res, { agents: f.agents }),
  'GET /v1/providers': (req, res) => json(res, { providers: f.providers }),
  'GET /v1/sessions': async (req, res) => { await new Promise((r) => setTimeout(r, 2000)); json(res, { sessions: [], live: 0, cap: 4 }) },
  'GET /v1/sessions/events': (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') },
  'GET /v1/dictation-terms': (req, res) => json(res, { path: 'terms.md', text: '# Terms\n\nSprout, Quill\n', obsidian_url: 'obsidian://open?file=terms' }),
  'GET /v1/limits': (req, res) => json(res, { attachment_max_bytes: 1024 * 1024 }),
  'GET /v1/threads': async (req, res, url) => json(res, await f.api('GET', '/threads' + url.search)),
  'GET /v1/threads/events': (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); const stop = f.subscribeList((e) => res.write(`data: ${JSON.stringify(e)}\n\n`)); res.on('close', stop) },
  'POST /v1/threads': async (req, res, url, raw) => {
    if (createDelay) await new Promise((r) => { releaseCreate = r })
    const t = await f.api('POST', '/threads', JSON.parse(raw)); created.add(t.id); addRoutes(t.id); json(res, t, 201)
  },
}
function emit(id, event) { for (const res of streams.get(id) ?? []) res.write(`data: ${JSON.stringify(event)}\n\n`) }
function addRoutes(id) {
  const base = `/v1/threads/${id}`
  for (const [method, suffix] of [['GET', ''], ['PATCH', ''], ['DELETE', ''], ['POST', '/messages'], ['GET', '/board'], ['PUT', '/board']]) {
    routes[`${method} ${base}${suffix}`] = async (req, res, url, raw) => {
      if (expire) return json(res, { error: 'Sign in again' }, 401)
      const answer = await f.api(method, `/threads/${id}${suffix}${url.search}`, raw ? JSON.parse(raw) : undefined)
      if (oldModels && answer?.thread) for (const m of answer.thread.members) delete m.model
      if (method === 'DELETE') created.delete(id)
      json(res, answer)
    }
  }
  routes[`POST ${base}/attachments`] = (req, res, url, raw) => {
    assert.match(req.headers['content-type'], /multipart\/form-data/)
    assert.match(raw, /name="file"/)
    json(res, { attachments: [{ path: '98_ATTACHMENTS/test/Board-notes.md', name: 'Board-notes.md', size: 12, type: 'text/markdown' }] }, 201)
  }
  routes[`GET ${base}/events`] = (req, res) => {
    if (expire) return json(res, { error: 'Sign in again' }, 401)
    res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n')
    if (!streams.has(id)) streams.set(id, new Set())
    streams.get(id).add(res)
    const stop = f.subscribe(id, (e) => res.write(`data: ${JSON.stringify(e)}\n\n`))
    res.on('close', () => { stop(); streams.get(id).delete(res) })
  }
}
for (const t of (await f.api('GET', '/threads')).threads) addRoutes(t.id)
const generalBoard = await f.api('GET', '/threads/general/board')
await f.api('PUT', '/threads/general/board', { markdown: generalBoard.markdown.replace('# Inbox\n\n', '# Inbox\n\n> A place for things worth keeping\n> beyond this conversation.\n\n') })
const broker = await fakeBroker(routes), app = await appAgainst(broker)
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] })
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, permissions: ['microphone'], isMobile: width === 390, hasTouch: width === 390 })
    const page = await context.newPage(), errors = [], start = broker.seen.length
    page.on('pageerror', (e) => { errors.push(e.message); console.error('PAGE ERROR', e.message) })
    const shot = async (name) => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name}: horizontal overflow`)
      await page.screenshot({ path: path.join(out, `${name}-${width}-${theme}.png`), animations: 'disabled' })
    }
    await page.goto(app.url)
    await page.locator('.tt-row').first().waitFor()
    assert.equal(await page.getByRole('button', { name: 'Refresh threads' }).count(), 0)
    assert.equal(broker.seen.slice(start).filter((r) => r.path === '/v1/sessions').length, 0, 'threads never download the classic session list')
    await shot('chats')
    createDelay = 1
    await page.getByRole('button', { name: 'New thread', exact: true }).click()
    const draft = page.locator('#draft[contenteditable=true]')
    await draft.fill('A thought before the thread is created.')
    assert.equal(await page.locator('.tt-stream .pane-nothing .display').innerText(), 'A little room to think')
    assert.equal(await page.locator('.roundsend').isDisabled(), true)
    releaseCreate()
    await page.locator('.roundsend:not(:disabled)').waitFor()
    createDelay = 0
    const nobody = broker.seen.filter((r) => r.method === 'POST' && r.path === '/v1/threads').at(-1)
    assert.deepEqual(JSON.parse(nobody.body), {})
    const id = [...created].at(-1)
    await page.locator('.roundsend').click()
    await page.locator('.tt-message.mine').filter({ hasText: 'A thought before the thread is created.' }).waitFor()
    assert.equal(await page.locator('.tt-typing').count(), 0, 'a nobody thread starts no turn')
    await shot('quiet-thread')
    await page.locator('.tt-board-button').click()
    await page.getByText('A short list for this thread. You and the agents can edit it.', { exact: true }).waitFor()
    await shot('empty-board')
    await page.getByRole('button', { name: 'Close board', exact: true }).filter({ hasNot: page.locator('.tt-board-scrim') }).click()
    await page.getByTitle('Thread details', { exact: true }).click()
    await page.getByTitle('Rename thread', { exact: true }).click()
    await page.getByRole('textbox', { name: 'Thread title', exact: true }).fill('Room for a good idea')
    await page.getByRole('textbox', { name: 'Thread title', exact: true }).press('Enter')
    await page.getByTitle('Rename thread', { exact: true }).filter({ hasText: 'Room for a good idea' }).waitFor()
    await page.getByRole('button', { name: 'Use Conversation icon' }).click()
    await page.waitForFunction(() => document.querySelector('[aria-label="Use Conversation icon"]')?.getAttribute('aria-pressed') === 'true')
    await page.getByRole('button', { name: 'Add Sprout', exact: true }).click()
    await page.getByRole('group', { name: 'Default agent', exact: true }).getByRole('button', { name: 'Sprout', exact: true }).click()
    await chooseThreadOption(page, 'Model for Sprout', 'deep')
    await page.waitForFunction(() => document.querySelector('[aria-label="Model for Sprout"]')?.getAttribute('data-value') === 'deep' && !document.querySelector('[aria-label="Model for Sprout"]').disabled)
    await page.getByRole('button', { name: 'Add Quill', exact: true }).click()
    await page.getByRole('button', { name: 'Provider for Quill' }).waitFor()
    await page.getByRole('button', { name: 'Pin thread', exact: true }).click()
    await page.getByRole('button', { name: 'Unpin thread', exact: true }).waitFor()
    await page.locator('.tt-details-scroll').evaluate((node) => { node.scrollTop = 0 })
    await shot('details')
    await page.getByRole('button', { name: 'Chat', exact: true }).click()
    assert.equal(await page.locator('.tt-head .blob, .tt-avatar-stack').count(), 0)
    await draft.fill('Hello')
    await page.getByTitle('Mention an agent', { exact: true }).click()
    assert.equal(await draft.innerText(), 'Hello', '@ opens the picker without inserting a second @')
    await page.locator('.tt-mentions button').filter({ hasText: 'Quill' }).click()
    assert.equal(await draft.innerText(), 'Hello @quill ')
    assert.equal(await draft.locator('.mention').innerText(), '@quill')
    await page.locator('.modelbtn').click()
    await chooseThreadOption(page, 'Model for Quill', 'swift')
    await page.locator('.tt-configuration').waitFor({ state: 'detached' })
    await page.locator('.roundsend').click()
    await page.locator('.tt-message.mine .mention').filter({ hasText: '@quill' }).waitFor()
    await page.waitForFunction(() => document.querySelector('.tt-stream')?.getAttribute('aria-busy') === 'false' && !document.querySelector('#draft')?.textContent)
    const sent = broker.seen.filter((r) => r.path === `/v1/threads/${id}/messages`).at(-1)
    assert.equal(Object.hasOwn(JSON.parse(sent.body), 'model'), false)
    // Genuine MediaRecorder with synthetic audio, the existing take UI, and a fake transcription reply.
    await page.route('**/x/dictation/transcribe', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ text: 'A clear thought from dictation.' }) }))
    await page.getByRole('button', { name: 'Dictate', exact: true }).click()
    await page.getByRole('group', { name: 'Dictating', exact: true }).waitFor()
    await page.waitForTimeout(400)
    await shot('dictating')
    await page.getByRole('button', { name: 'Keep', exact: true }).click()
    await page.waitForFunction(() => document.querySelector('#draft')?.textContent.includes('A clear thought from dictation.'))
    assert.ok(broker.seen.some((r) => r.path === '/v1/dictation-terms'))
    // The classic + menu and upload route preserve the host's returned filename.
    await page.getByTitle('Attach a file', { exact: true }).click()
    const chosen = page.waitForEvent('filechooser')
    await page.getByRole('button', { name: /Upload a file/ }).click()
    await (await chosen).setFiles({ name: 'Board notes.md', mimeType: 'text/markdown', buffer: Buffer.from('A short note') })
    await page.locator('.chip-name').filter({ hasText: 'Board-notes.md' }).waitFor()
    await shot('composer')
    await page.locator('.roundsend').click()
    await page.waitForFunction(() => !document.querySelector('.attachstrip') && !document.querySelector('#draft')?.textContent)
    const attachmentMessage = JSON.parse(broker.seen.filter((r) => r.path === `/v1/threads/${id}/messages`).at(-1).body)
    assert.deepEqual(attachmentMessage.attachments, ['98_ATTACHMENTS/test/Board-notes.md'])
    await page.waitForTimeout(2500)
    emit(id, { type: 'turn.started', persona: 'sprout' })
    emit(id, { type: 'message.delta', persona: 'sprout', draft: 'First I will read the note.' })
    await page.getByText('First I will read the note.', { exact: true }).waitFor()
    assert.equal(await page.locator('.tt-typing').filter({ hasText: 'Sprout is thinking' }).count(), 0)
    emit(id, { type: 'turn.step', persona: 'sprout', label: 'Read the shared note', steps: 1 })
    await page.locator('.tt-typing').filter({ hasText: 'Read the shared note' }).waitFor()
    await page.getByText('First I will read the note.', { exact: true }).waitFor()
    emit(id, { type: 'message.delta', persona: 'sprout', draft: 'The next step is clear.' })
    await page.getByText('The next step is clear.', { exact: true }).waitFor()
    assert.equal(await page.locator('.tt-typing').filter({ hasText: 'Read the shared note' }).count(), 0)
    await shot('stream-order')
    emit(id, { type: 'message.done', message: { id: 'm1000', author: 'sprout', text: 'The next step is clear.', did: [] } })
    emit(id, { type: 'message.done', message: { id: 'm1001', kind: 'system', text: 'End one with DELETE /v1/sessions/id' } })
    await page.getByText('Couldn’t start — the box is busy; try again in a moment.', { exact: true }).waitFor()
    // Archiving and deletion have explicit paths and preserve the current page until acted on.
    await page.getByTitle('Thread details', { exact: true }).click()
    await page.getByRole('button', { name: 'Archive thread', exact: true }).click()
    await page.getByRole('button', { name: 'Unarchive thread', exact: true }).click()
    await page.getByRole('button', { name: 'Delete thread', exact: true }).click()
    await page.getByRole('group', { name: 'Confirm delete' }).waitFor()
    assert.equal(await page.getByRole('group', { name: 'Confirm delete' }).isVisible(), true)
    await page.getByRole('button', { name: 'Keep thread', exact: true }).click()
    await page.getByRole('button', { name: 'Delete thread', exact: true }).click()
    await page.getByRole('button', { name: 'Delete permanently', exact: true }).click()
    await page.getByRole('button', { name: 'Sprout menu', exact: true }).click()
    await page.getByRole('button', { name: 'New thread', exact: true }).click()
    await draft.waitFor()
    await page.getByRole('heading', { name: 'A new thread with Sprout', exact: true }).waitFor()
    await page.waitForFunction(() => !document.querySelector('.roundsend')?.disabled || document.querySelector('.tt-head .thread-tile'))
    await page.getByTitle('Thread details', { exact: true }).click()
    await page.getByRole('button', { name: 'Delete thread', exact: true }).click()
    await page.getByRole('button', { name: 'Delete permanently', exact: true }).click()
    await page.locator('.tt-row').filter({ hasText: 'General' }).click()
    await page.getByTitle('Thread details', { exact: true }).click()
    assert.equal(await page.getByRole('textbox', { name: 'Thread title' }).count(), 0)
    assert.equal(await page.getByRole('button', { name: 'Delete thread', exact: true }).count(), 0)
    await page.getByRole('button', { name: 'Open board', exact: true }).click()
    assert.equal(await page.locator('.tt-board-prose blockquote br').count(), 0, 'the header quote reflows')
    assert.match(await page.locator('.tt-board-prose blockquote').innerText(), /worth keeping beyond this conversation/)
    await shot('full-board')
    assert.deepEqual(errors, [])
    results.push({ width, theme, optimisticCreation: true, noDefault: true, mentions: true, dictate: 'fake microphone + fake transcription', upload: true, memberAndMessageModels: true, orderedStreaming: true, details: true, emptyAndFullBoards: true, cleanup: true, errors })
    console.log(`Checked polished threads at ${width}px ${theme}`)
    await context.close()
  }
  // Older runtimes keep the shared entry and explain unavailable model editing. Expiry stops every background caller.
  oldModels = true
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } }), page = await context.newPage()
  await page.goto(app.url)
  await page.locator('.tt-row').filter({ hasText: 'General' }).click()
  await page.locator('#draft[contenteditable=true]').waitFor()
  assert.equal(await page.locator('.tt-composer .modelbtn').count(), 1)
  expire = true
  for (const stream of streams.get('general') ?? []) stream.end()
  await page.getByRole('link', { name: 'Sign in again', exact: true }).waitFor({ timeout: 10000 })
  const count = broker.seen.length
  await page.clock.install()
  await page.clock.fastForward(60000)
  await page.waitForTimeout(100)
  assert.equal(broker.seen.length, count, 'auth expiry stops SSE retries, polling and lookups')
  assert.equal(await page.getByRole('link', { name: 'Sign in again', exact: true }).getAttribute('href'), app.url + '/')
  await page.screenshot({ path: path.join(out, 'sign-in-again-390-light.png') })
  results.push({ oldRuntimeModelsHidden: true, authStopsRequests: true })
  await context.close()
  expire = false
  const redirectContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } }), redirectPage = await redirectContext.newPage()
  await redirectPage.goto(app.url)
  await redirectPage.locator('.tt-row').first().waitFor()
  let lookups = 0
  await redirectPage.route('**/x/read-aloud/speech/lookup', (route) => { lookups++; return route.fulfill({ status: 303, headers: { location: '/login' } }) })
  await redirectPage.evaluate(async () => { const { authenticatedFetch } = await import('/auth.js'); await authenticatedFetch('/x/read-aloud/speech/lookup', { method: 'POST' }).catch(() => {}); for (let i = 0; i < 10; i++) await authenticatedFetch('/x/read-aloud/speech/lookup', { method: 'POST' }).catch(() => {}) })
  await redirectPage.getByRole('link', { name: 'Sign in again', exact: true }).waitFor()
  assert.equal(lookups, 1, 'a redirect cannot start a speech-lookup storm')
  await redirectPage.screenshot({ path: path.join(out, 'sign-in-again-1440-light.png') })
  results.push({ authRedirectStoppedLookups: true })
  await redirectContext.close()
  assert.equal(created.size, 0, 'all synthetic test threads deleted')
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
  console.log(JSON.stringify(results, null, 2))
} catch (error) {
  for (const context of browser.contexts()) for (const page of context.pages()) {
    console.error('PAGE AT FAILURE', await page.locator('body').innerText().catch(() => 'unavailable'))
    await page.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {})
  }
  throw error
} finally { await browser.close(); await closing(app, broker)() }
