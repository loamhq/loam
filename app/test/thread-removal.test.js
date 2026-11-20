import test from 'node:test'
import assert from 'node:assert/strict'
import { setImmediate as settle } from 'node:timers/promises'
import { createThreads } from '../public/threads.js'
import { installDom } from './fakedom.js'

const row = { id: 'one', title: 'A thought', members: [{ persona: 'sprout' }], unread: 3 }
const record = () => ({ thread: structuredClone(row), messages: [{ id: 'm1', author: 'sprout', text: 'Still here.' }],
  has_more: false, turn: null, board: { path: 'board.md', markdown: '' } })
const listing = (rows = [row]) => ({ threads: structuredClone(rows), agents: [{ id: 'sprout' }] })
const missing = () => Object.assign(new Error('Thread not found'), { status: 404 })

async function setup(t, { onMissing } = {}) {
  installDom(); document.querySelector = () => null
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const originalFetch = globalThis.fetch, streams = [], calls = [], field = { value: '' }
  const f = { list: () => listing(), read: () => record(), streamStatus: 200 }
  globalThis.fetch = async (href, { signal }) => {
    if (!href.includes('archived=1') && f.streamStatus !== 200) return new Response('{"error":"Thread not found"}', { status: f.streamStatus })
    const stream = { href, signal }
    streams.push(stream)
    return new Response(new ReadableStream({ start(controller) {
      stream.emit = (event) => controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`))
      signal.addEventListener('abort', () => controller.close())
    } }), { headers: { 'content-type': 'text/event-stream' } })
  }
  const threads = createThreads({ api: async (method, path) => {
    calls.push(path)
    if (path === '/threads?archived=1') return f.list()
    if (path.includes('/steps?')) throw missing()
    return f.read()
  }, redraw() {}, enter() {}, agents: () => listing().agents, missing: onMissing,
  compose: { field: () => field, restore() {}, files: () => [], leave() {} } })
  t.after(() => { threads.pause(); threads.close(); globalThis.fetch = originalFetch })
  await threads.load(); await threads.open('one'); await settle()
  const list = streams.find((s) => s.href.includes('archived=1'))
  const live = streams.find((s) => !s.href.includes('archived=1'))
  list.emit({ type: 'threads.snapshot', ...listing() }); await settle()
  return Object.assign(f, { threads, field, calls, streams, live,
    emit: async (event) => { list.emit(event); await settle() },
    removed: async () => { list.emit({ type: 'thread.removed', thread: 'one' }); await settle() },
    advance: async () => { t.mock.timers.tick(2500); await settle() } })
}

test('a false removed event leaves the conversation, unsent draft and live stream intact', async (t) => {
  const f = await setup(t), o = f.threads.st.open
  f.field.value = 'An unsent thought'
  await f.removed()
  assert.equal(o.error, undefined)
  assert.equal(o.readFailed, undefined)
  assert.equal(f.live.signal.aborted, false)
  assert.equal(f.threads.st.threads.length, 1)
  f.live.emit({ type: 'message.done', message: { id: 'm2', author: 'sprout', text: 'And still listening.' } })
  await settle()
  assert.equal(o.messages.at(-1).text, 'And still listening.')
  assert.equal(f.field.value, 'An unsent thought')
  assert.deepEqual(f.calls, ['/threads?archived=1', '/agents/drafts?thread=one', '/threads/one?limit=12', '/threads?archived=1'])
})

test('one empty reading during a rewrite recovers quietly, including an archived thread', async (t) => {
  const f = await setup(t), o = f.threads.st.open
  f.list = () => listing([])
  await f.removed()
  assert.equal(o.readFailed, undefined)
  assert.equal(f.live.signal.aborted, false)
  f.list = () => listing([{ ...row, archived: true, title: 'A renamed thought' }])
  await f.advance()
  assert.equal(o.thread.title, 'A renamed thought')
  assert.equal(o.error, undefined)
  assert.equal(o.thread.archived, true)
  assert.equal(f.live.signal.aborted, false)
})

test('real removal needs two fresh complete listings and then stops the open thread', async (t) => {
  const f = await setup(t), o = f.threads.st.open
  f.list = () => listing([])
  await f.removed(); await f.removed()
  assert.equal(f.calls.filter((p) => p === '/threads?archived=1').length, 2, 'duplicate events share a confirmation')
  assert.equal(f.threads.st.threads.length, 1)
  await f.advance()
  assert.equal(o.error, 'This thread was deleted.')
  assert.equal(o.readFailed, true)
  assert.equal(f.live.signal.aborted, true)
  assert.equal(f.threads.st.threads.length, 0)
  await f.advance()
  assert.equal(f.streams.length, 2, 'no reconnect after confirmed removal')
})

test('HTTP failures, malformed JSON envelopes and unsupported routes cannot confirm deletion', async (t) => {
  const f = await setup(t), o = f.threads.st.open
  f.list = () => listing([])
  await f.removed()
  for (const answer of [missing(), Object.assign(new Error('Restarting'), { status: 503 }), null, {}, { threads: [] }]) {
    f.list = () => { if (answer instanceof Error) throw answer; return answer }
    await f.advance()
    assert.equal(o.readFailed, undefined)
  }
  f.list = () => listing([])
  await f.advance()
  assert.equal(o.readFailed, undefined, 'an error resets the consecutive absence count')
  f.list = () => listing()
  await f.advance()
  assert.equal(o.error, undefined)
  assert.equal(f.live.signal.aborted, false)
})

for (const source of ['thread.changed', 'threads.snapshot', 'HTTP listing']) test(`${source} cancels an older removal confirmation in flight`, async (t) => {
  const f = await setup(t), o = f.threads.st.open
  let finish
  f.list = () => new Promise((resolve) => { finish = resolve })
  await f.removed()
  if (source === 'HTTP listing') { f.list = () => listing(); await f.threads.load() }
  else await f.emit(source === 'threads.snapshot' ? { type: source, ...listing() } : { type: source, summary: row })
  finish(listing([])); await settle(); await f.advance()
  assert.equal(o.error, undefined)
  assert.equal(f.live.signal.aborted, false)
  assert.equal(f.calls.filter((p) => p === '/threads?archived=1').length, source === 'HTTP listing' ? 3 : 2)
})

test('pausing cancels removal checks, including a late HTTP response', async (t) => {
  const f = await setup(t)
  let finish
  f.list = () => new Promise((resolve) => { finish = resolve })
  await f.removed(); f.threads.pause()
  finish(listing([])); await settle(); await f.advance()
  assert.equal(f.threads.st.open.error, undefined)
  assert.equal(f.calls.filter((p) => p === '/threads?archived=1').length, 2)
})

for (const source of ['thread read', 'thread.snapshot', 'message.done']) test(`${source} proves the thread still exists during a removal check`, async (t) => {
  const f = await setup(t)
  let finish
  f.list = () => new Promise((resolve) => { finish = resolve })
  await f.removed()
  if (source === 'thread read') await f.threads.open('one')
  else {
    f.live.emit(source === 'thread.snapshot' ? { type: source, turns: [], pending: [] }
      : { type: source, message: { id: 'm2', author: 'sprout', text: 'Still here.' } })
    await settle()
  }
  finish(listing([])); await settle(); await f.advance()
  assert.equal(f.threads.st.open.error, undefined)
  assert.equal(f.calls.filter((p) => p === '/threads?archived=1').length, 2)
})

test('a completed check for the previous thread cannot label the newly opened thread deleted', async (t) => {
  const f = await setup(t)
  f.list = () => listing([])
  await f.removed()
  f.read = () => ({ ...record(), thread: { ...row, id: 'two' } })
  await f.threads.open('two')
  await f.advance()
  assert.equal(f.threads.st.open.thread.id, 'two')
  assert.equal(f.threads.st.open.error, undefined)
})

test('transient opening reads retry without a refresh button or replacing the draft', async (t) => {
  const f = await setup(t)
  f.field.value = 'Keep this draft'
  for (const error of [new TypeError('Network unavailable'), Object.assign(new Error('Restarting'), { status: 503 }), missing()]) {
    f.read = () => { throw error }
    await f.threads.open('one')
    const o = f.threads.st.open
    assert.equal(o.error, undefined)
    assert.equal(o.readFailed, undefined)
    assert.equal(o.connection, 'Reconnecting…')
    f.read = () => null
    await f.advance()
    assert.equal(o.readFailed, undefined, 'an empty or malformed read retries too')
    f.read = () => record()
    await f.advance()
    assert.equal(o.connection, '')
    assert.equal(o.refreshing, false)
    assert.equal(f.field.value, 'Keep this draft')
  }
})

test('an opening 404 still reports a truly deleted thread after confirmation', async (t) => {
  const f = await setup(t)
  f.read = () => { throw missing() }; f.list = () => listing([])
  await f.threads.open('one'); await settle()
  assert.equal(f.threads.st.open.error, undefined)
  await f.advance()
  assert.equal(f.threads.st.open.error, 'This thread was deleted.')
})

test('the panel recovers once after a missing opening is confirmed', async (t) => {
  const recovered = []
  const f = await setup(t, { onMissing: id => { recovered.push(id); f.threads.close({ navigate: false }) } })
  f.read = () => { throw missing() }; f.list = () => listing([])
  await f.threads.open('one'); await settle()
  assert.deepEqual(recovered, [], 'a single absent listing leaves the opening in place')
  await f.advance()
  assert.deepEqual(recovered, ['one'])
  assert.equal(f.threads.st.open, null)
  const calls = f.calls.length
  await f.advance()
  assert.equal(f.calls.length, calls, 'the repaired visit schedules no more reads')
})

test('an event-stream 404 reconnects from the completed message and does not imply deletion', async (t) => {
  const f = await setup(t)
  f.streamStatus = 404
  await f.threads.open('one'); await settle()
  const o = f.threads.st.open
  assert.equal(o.error, undefined)
  assert.equal(o.connection, 'Reconnecting…')
  f.streamStatus = 200
  await f.advance()
  const resumed = f.streams.at(-1)
  assert.equal(resumed.href, '/api/threads/one/events?after=m1')
  resumed.emit({ type: 'thread.snapshot', turns: [], pending: [] }); await settle()
  assert.equal(o.connection, '')
  assert.equal(o.readFailed, undefined)
})

test('a missing full reply record is independent of thread existence', async (t) => {
  const f = await setup(t), o = f.threads.st.open
  o.replySteps.ensure('m1'); await settle()
  assert.equal(o.replySteps.entries.get('m1').status, 'missing')
  assert.equal(o.error, undefined)
  assert.equal(f.calls.filter((p) => p === '/threads?archived=1').length, 1)
})

test('a failed opening response arriving after pause cannot schedule another read', async (t) => {
  const f = await setup(t)
  let fail
  f.read = () => new Promise((resolve, reject) => { fail = reject })
  const opening = f.threads.open('one')
  f.threads.pause(); fail(missing()); await opening
  const calls = f.calls.length
  await f.advance()
  assert.equal(f.calls.length, calls)
  assert.equal(f.threads.st.open.connection, undefined)
})
