import test from 'node:test'
import assert from 'node:assert/strict'
import { createBranchDrafter } from '../src/thread-branch.js'
import { createApp } from '../src/server.js'
import { createLog } from '../src/log.js'
import { readConfig, fixtureBroker } from './key-fixture.js'
import { applyThreadEvent } from '../public/thread-record.js'
import { fakeBroker, appAgainst, closing, shut, TOKEN } from './harness.js'
import { once } from 'node:events'
import { createThreads } from '../public/threads.js'
import { installDom } from './fakedom.js'
import { h } from '../public/ui.js'
import { all, find, text } from './browser.js'

const reading = { thread: 'source', message: 'm12', title: 'A quieter week', persona: 'sprout', people: ['sprout', 'quill'],
  summarizer: { system_instruction: 'Write a short opening.', input: 'A synthetic context window.', answer: { title: 'string', draft: 'string' } },
  fallback: { title: 'Keep Friday open', draft: 'Let’s make room on Friday.\n\nI’d like to explore ' } }
const model = (answer) => ({ status: 'completed', steps: [{ type: 'model_output', content: [{ type: 'text', text: answer }] }] })
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }

test('without a vendor key a record-based opening creates an editable branch without sending a message', async t => {
  const opening = { ...reading, fallback: { title: 'Keep Friday open', draft: 'Let’s make room on Friday.\n\nWhat can we move to keep that day clear?' } }
  const broker = await fakeBroker({
    'GET /v1/threads/source/branch': (req, res) => json(res, opening),
    'POST /v1/threads/source/branch': (req, res, url, raw) => {
      const body = JSON.parse(raw)
      json(res, { thread: { id: 'friday', title: body.title, draft: body.draft, members: [{ persona: body.persona }] }, from: {}, into: {}, window: [] }, 201)
    },
  })
  const app = await appAgainst(broker, { GEMINI_API_KEY: '' })
  t.after(closing(app, broker))
  assert.equal(app.config.geminiApiKey, '')
  const draft = await (await fetch(app.url + '/api/threads/source/branch?message=m12')).json()
  assert.deepEqual({ title: draft.title, draft: draft.draft }, opening.fallback)
  assert.equal(draft.summarizer, undefined)
  const created = await fetch(app.url + '/api/threads/source/branch', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ message: 'm12', persona: draft.persona, title: draft.title, draft: draft.draft }) })
  assert.equal(created.status, 201)
  const { thread } = await created.json()
  assert.deepEqual({ title: thread.title, draft: thread.draft }, opening.fallback)
  assert.deepEqual(broker.seen.map(row => `${row.method} ${row.path}`), ['GET /v1/threads/source/branch', 'POST /v1/threads/source/branch'])
  t.diagnostic(`No-key branch produced: ${JSON.stringify(thread)}`)
})

test('branch writer uses the broker prompt and configured brief model; only title/draft and picker facts reach the page', async () => {
  const calls = [], config = readConfig({ GEMINI_API_KEY: 'synthetic-key', BRANCH_MODEL: 'configured-brief-model' })
  const draft = createBranchDrafter(config, { fetchImpl: async (url, init) => {
    calls.push({ url, ...init }); return Response.json(model('```json\n{"title":"Make room for Friday","draft":"Let’s keep Friday open. What could move?"}\n```'))
  } })
  const result = await draft(reading), body = JSON.parse(calls[0].body)
  assert.deepEqual(body, { model: 'configured-brief-model', ...{ system_instruction: reading.summarizer.system_instruction, input: reading.summarizer.input }, store: false })
  assert.equal(calls[0].headers['x-goog-api-key'], 'synthetic-key')
  assert.deepEqual(result, { thread: 'source', message: 'm12', persona: 'sprout', people: ['sprout', 'quill'], title: 'Make room for Friday', draft: 'Let’s keep Friday open. What could move?' })
  assert.ok(!JSON.stringify(result).includes('synthetic-key'))
})
test('missing key, HTTP failure, timeout, incomplete or malformed model answers use the broker fallback', async () => {
  const config = readConfig({ GEMINI_API_KEY: 'synthetic-key' })
  const failures = [async () => Response.json({}, { status: 429 }), async () => { throw new Error('Offline') },
    async () => Response.json(model('Not JSON')), async () => Response.json(model('{"title":"Fine","draft":""}')),
    async () => Response.json({ ...model('{"title":"Fine","draft":"Opening"}'), status: 'in_progress' })]
  for (const fetchImpl of failures) assert.deepEqual(await createBranchDrafter(config, { fetchImpl })(reading),
    { thread: 'source', message: 'm12', persona: 'sprout', people: ['sprout', 'quill'], ...reading.fallback })
  let called = false
  const result = await createBranchDrafter(readConfig({}), { fetchImpl: () => { called = true } })(reading)
  assert.equal(result.draft, reading.fallback.draft); assert.equal(called, false)
  await assert.rejects(createBranchDrafter(config)({}), /did not supply/)
  const timeout = createBranchDrafter(config, { timeoutMs: 5, fetchImpl: (_, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(signal.reason), { once: true })
    setTimeout(resolve, 20) // Keep the test alive for the unref'ed abort timer.
  }) })
  assert.equal((await timeout(reading)).draft, reading.fallback.draft)
})
test('branch read and write are explicitly relayed with bearer, no-store and original refusal sentences', async (t) => {
  const payload = { message: 'm12', persona: 'quill', title: 'A new thought', draft: 'An edited opening.' }
  let refuse = false
  const broker = await fakeBroker({
    'GET /v1/threads/source/branch': (req, res) => json(res, refuse ? { error: 'This message is no longer here.' } : reading, refuse ? 404 : 200),
    'POST /v1/threads/source/branch': (req, res, url, raw) => json(res, { thread: { id: 'new', members: [], draft: JSON.parse(raw).draft }, from: {}, into: {}, window: [] }, 201),
  })
  const app = await appAgainst(broker)
  t.after(closing(app, broker))
  let res = await fetch(app.url + '/api/threads/source/branch?message=m12&persona=quill&ignored=1')
  assert.equal(res.status, 200); assert.equal(res.headers.get('cache-control'), 'no-store')
  assert.equal((await res.json()).draft, reading.fallback.draft)
  assert.equal(broker.seen.at(-1).search, '?message=m12&persona=quill')
  assert.equal(broker.seen.at(-1).auth, `Bearer ${TOKEN}`)
  res = await fetch(app.url + '/api/threads/source/branch', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) })
  assert.equal(res.status, 201); assert.equal((await res.json()).thread.draft, payload.draft)
  assert.deepEqual(JSON.parse(broker.seen.at(-1).body), payload)
  refuse = true; res = await fetch(app.url + '/api/threads/source/branch?message=m12')
  assert.equal(res.status, 404); assert.deepEqual(await res.json(), { error: 'This message is no longer here.' })
  assert.equal((await fetch(app.url + '/api/threads/source/branch', { method: 'DELETE' })).status, 405)
  assert.equal((await fetch(app.url + '/api/threads/source/branch', { method: 'POST', body: 'broken JSON' })).status, 400)
})
test('the actual web route enriches a read once without creating a thread or exposing a prompt', async (t) => {
  const broker = await fakeBroker({ 'GET /v1/threads/source/branch': (req, res) => json(res, reading) })
  let reads = 0
  const { server } = createApp({ config: readConfig({ BROKER_URL: broker.url, BROKER_TOKEN: TOKEN }), log: createLog({ level: 'off' }),
    branchDraft: async (body) => { reads++; assert.deepEqual(body, reading); return { title: 'A useful thought', draft: 'What if…' } } })
  server.listen(0, '127.0.0.1'); await once(server, 'listening')
  t.after(async () => { await shut(server); await shut(broker.server) })
  const res = await fetch(`http://127.0.0.1:${server.address().port}/api/threads/source/branch?message=m12`)
  assert.deepEqual(await res.json(), { title: 'A useful thought', draft: 'What if…' }); assert.equal(reads, 1)
  assert.deepEqual(broker.seen.map((r) => r.method), ['GET'])
})
test('live branch notices insert after their source, preserve sibling branches and deduplicate HTTP/SSE; off-window notices stay off-window', () => {
  const o = { thread: { members: [] }, messages: [{ id: 'm1', author: 'you' }, { id: 'm2', author: 'sprout' }, { id: 'm3', author: 'you' }], typing: new Set(), permissions: new Map() }
  const notice = (id, after) => ({ type: 'message.done', message: { id, kind: 'system', author: 'system', after, text: 'Branched into A thought', link: { thread: 'new', title: 'A thought' } } })
  applyThreadEvent(o, notice('m4', 'm1')); applyThreadEvent(o, notice('m4', 'm1')); applyThreadEvent(o, notice('m5', 'm1'))
  assert.deepEqual(o.messages.map((m) => m.id), ['m1', 'm4', 'm5', 'm2', 'm3'])
  applyThreadEvent(o, notice('m6', 'm0'))
  assert.equal(o.messages.length, 5); assert.equal(o.after, 'm6')
})

function pageSetup(t, api) {
  installDom(); globalThis.innerWidth = 390
  const field = { value: 'Keep my source draft.' }, files = [{ path: 'notes.md' }]
  const threads = createThreads({ api, redraw() {}, agents: () => [{ id: 'sprout', name: 'Sprout' }, { id: 'quill', name: 'Quill' }], nav: () => null,
    compose: { field: () => field, files: () => files, busy: () => false, render: () => h('div'), dragProps: () => ({}) } })
  const o = threads.st.open = { thread: { id: 'source', title: 'A week', default: 'sprout', members: [{ persona: 'sprout', engine: 'claude-code' }] },
    messages: [{ id: 'm1', author: 'you', text: 'Friday?' }, { id: 'm12', author: 'sprout', text: 'Keep it open.' },
      { id: 'm13', kind: 'system', text: 'A system line' }, { id: 'draft:sprout', author: 'sprout', text: 'Still writing', streaming: true }],
    permissions: new Map(), typing: new Set(), steps: new Map(), board: { markdown: '', path: '' } }
  threads.st.listLive = true
  t.after(() => threads.pause())
  return { threads, o, field, files, view: () => threads.view(false) }
}
test('both authors get a branch footer at every width; loading is dismissible and late answers do not reopen the sheet', async (t) => {
  let resolve, calls = 0
  const { o, view, field, files } = pageSetup(t, async () => { calls++; return new Promise((done) => { resolve = done }) })
  for (const width of [390, 768, 1440]) {
    globalThis.innerWidth = width
    const controls = all(view(), 'tt-footer-branch')
    assert.equal(controls.length, 2)
    for (const control of controls) assert.equal(control.getAttribute('aria-label'), 'Branch into a new thread')
  }
  all(view(), 'tt-footer-branch')[0].fire('click')
  assert.equal(o.branch.message, 'm1'); assert.equal(o.branch.loading, true)
  assert.equal(find(view(), 'tt-branch-start').hasAttribute('disabled'), true)
  find(view(), 'surface-close').fire('click')
  assert.equal(o.branch, null)
  resolve({ ...reading, ...reading.fallback }); await new Promise(setImmediate)
  assert.equal(find(view(), 'tt-branch-panel'), null); assert.equal(calls, 1)
  assert.equal(field.value, 'Keep my source draft.'); assert.equal(files[0].path, 'notes.md')
})
test('persona taps and edits survive branch refusal; blank openings cannot start and a pending start cannot duplicate', async (t) => {
  const writes = []; let reject
  const { o, view, field } = pageSetup(t, async (method, route, body) => {
    if (method === 'GET') return { ...reading, ...reading.fallback }
    writes.push({ route, body }); return new Promise((resolve, no) => { reject = no })
  })
  all(view(), 'tt-footer-branch')[1].fire('click'); await new Promise(setImmediate)
  assert.equal(o.branch.persona, 'sprout')
  all(view(), 'tt-branch-person')[1].fire('click')
  assert.equal(o.branch.persona, 'quill')
  const input = o.branch.field
  input.value = ''; input.fire('input')
  assert.equal(find(view(), 'tt-branch-start').hasAttribute('disabled'), true)
  input.value = 'An edited opening.'; input.fire('input')
  find(view(), 'tt-branch-start').fire('click'); find(view(), 'tt-branch-start').fire('click')
  assert.equal(writes.length, 1)
  assert.deepEqual(writes[0], { route: '/threads/source/branch', body: { message: 'm12', persona: 'quill', title: reading.fallback.title, draft: 'An edited opening.' } })
  reject(new Error('This message is no longer here.')); await new Promise(setImmediate)
  assert.match(text(find(view(), 'tt-branch-error')), /This message is no longer here/)
  assert.equal(o.branch.field, input); assert.equal(input.value, 'An edited opening.')
  assert.equal(field.value, 'Keep my source draft.')
})
