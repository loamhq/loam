import test from 'node:test'
import assert from 'node:assert/strict'
import { openingContext, readingAnchor, restoreAnchor, mergeHistory, progressiveProse, threadFromPath, threadHref } from '../public/thread-opening.js'
import { createThreads } from '../public/threads.js'
import { installDom } from './fakedom.js'
import { fakeBroker, appAgainst, closing } from './harness.js'

const agents = [{ id: 'sprout' }, { id: 'quill' }]
const messages = [
  { id: 'm17', kind: 'message', author: 'sprout' },
  { id: 'm18', kind: 'message', author: 'you' },
  { id: 'm19', kind: 'system' },
  { id: 'm20', kind: 'message', author: 'quill' },
  { id: 'm21', kind: 'message', author: 'sprout' },
]
test('seen landing uses the frozen pre-read count, keeps the person’s context, and distinguishes incomplete windows', () => {
  assert.deepEqual(openingContext(messages, agents, 2), { unseen: 'm20', person: 'm18', needsEarlier: false })
  assert.equal(openingContext(messages, agents, 0).unseen, null)
  assert.equal(openingContext(messages.slice(-2), agents, 2).needsEarlier, true)
  assert.equal(openingContext(messages, agents, 10).unseen, null)
  assert.equal(openingContext(messages, agents, 10).needsEarlier, true)
  assert.equal(openingContext(messages, agents, 1, 'm20').unseen, 'm20', 'an exact host boundary wins over the fallback')
  assert.equal(openingContext(messages, agents, 1, 'm1').needsEarlier, true)
  assert.equal(openingContext([], agents).needsEarlier, true)
})
test('history and restoration anchor a message’s actual offset, independent of content added below it', () => {
  let addedAbove = 0
  const pane = { scrollTop: 400, getBoundingClientRect: () => ({ top: 50 }), querySelectorAll: () => [
    { getAttribute: () => 'm17', getBoundingClientRect: () => ({ top: -500 + addedAbove - pane.scrollTop, bottom: -100 + addedAbove - pane.scrollTop }) },
    { getAttribute: () => 'm18', getBoundingClientRect: () => ({ top: 430 + addedAbove - pane.scrollTop, bottom: 800 + addedAbove - pane.scrollTop }) },
  ] }
  const anchor = readingAnchor(pane)
  assert.deepEqual(anchor, { id: 'm18', offset: -20 })
  addedAbove = 1250
  assert.equal(restoreAnchor(pane, anchor), true)
  assert.equal(pane.scrollTop, 1650)
  assert.equal(readingAnchor(pane).offset, -20)
  pane.scrollTop = 0
  assert.equal(restoreAnchor(pane, anchor), true, 'returning to the pane uses the same anchor')
  assert.equal(pane.scrollTop, 1650)
  assert.equal(restoreAnchor(pane, { id: 'deleted', offset: 0 }), false)
})
test('prose paints without markdown work; visible newer blocks get separate tasks, reuse their nodes, and stop on departure', () => {
  const tasks = [], built = [], callbacks = [], nodes = []
  class Observer {
    constructor(callback) { callbacks.push(callback) }
    observe(node) { nodes.push(node) }
    unobserve() {}
    disconnect() {}
  }
  const options = { build: (text) => { built.push(text); return { formatted: text } }, plain: (text, key) => ({ plain: text, getAttribute: () => key }),
    changed: (format) => format(), Observer, defer: (fn) => tasks.push(fn) }
  const prose = progressiveProse(options)
  const old = prose.node('A very long old reply', 'old'), latest = prose.node('The newest message', 'new')
  assert.deepEqual(built, [])
  prose.watch({ querySelectorAll: () => [old, latest] })
  callbacks[0](nodes.map((target) => ({ target, isIntersecting: true })))
  tasks.shift()()
  assert.deepEqual(built, ['The newest message'])
  assert.equal(tasks.length, 1, 'the long reply has to yield for another paint')
  tasks.shift()()
  const formatted = prose.node('The newest message', 'new')
  assert.equal(prose.node('The newest message', 'new'), formatted)
  const pending = prose.node('Another reply', 'pending')
  callbacks[0]([{ target: pending, isIntersecting: true }])
  prose.stop(); tasks.shift()()
  assert.equal(built.length, 2)
  const returned = progressiveProse({ ...options, cache: prose.cache })
  assert.equal(returned.node('The newest message', 'new'), formatted, 'session revisits retain formatted nodes')
  returned.stop()
})
test('the page attaches after the newest loaded ID; history and snapshots cannot advance that cursor', async () => {
  installDom()
  const originalFetch = globalThis.fetch, streams = [], reads = []
  globalThis.fetch = async (href, { signal }) => {
    streams.push(href)
    return new Response(new ReadableStream({ start(controller) { signal.addEventListener('abort', () => controller.close()) } }), { headers: { 'content-type': 'text/event-stream' } })
  }
  const field = { value: '' }
  const row = { id: 'one', members: [{ persona: 'sprout' }, { persona: 'quill' }], unread: 2 }
  let opening = messages
  const threads = createThreads({ api: async (method, route) => { reads.push(route); return { thread: { ...row, unread: 0 }, messages: opening, has_more: false, turn: null, board: { path: 'board.md', markdown: '' } } },
    redraw() {}, enter() {}, agents: () => agents,
    compose: { field: () => field, restore() {}, files: () => [], leave() {} } })
  threads.st.checked = true; threads.st.threads = [row]
  try {
    await threads.open('one')
    assert.deepEqual(reads, ['/agents/drafts?thread=one', '/threads/one?limit=12'])
    assert.deepEqual(streams, ['/api/threads/one/events?after=m21'])
    assert.equal(threads.st.open.firstUnseen, 'm20', 'the GET’s zero count does not erase the landing')
    assert.equal(threads.st.open.landAt, 'm20')
    assert.equal(threads.st.open.atEnd, false)
    assert.equal(threads.st.open.after, 'm21')
    opening = []
    await threads.open('one')
    assert.equal(streams.at(-1), '/api/threads/one/events?after=m0', 'an empty page can recover the first message')
  } finally { threads.close(); globalThis.fetch = originalFetch }
})
test('thread addresses survive reload without making the root address reopen a remembered chat', () => {
  assert.equal(threadHref('a b'), '/threads/a%20b')
  assert.equal(threadFromPath('/threads/a%20b'), 'a b')
  for (const pathname of ['/', '/files/vault/note.md', '/threads/one/other', '/threads/%ZZ']) assert.equal(threadFromPath(pathname), null)
})

test('gap pages merge through overlaps, including a page extending above the saved history', () => {
  const range = (from, to) => Array.from({ length: to - from + 1 }, (_, i) => ({ id: `opaque-${from + i}` }))
  const saved = [...range(27, 38), ...range(50, 61)]
  saved[0].steps = ['already loaded']
  const joined = mergeHistory(saved, range(20, 49), 'opaque-50')
  assert.deepEqual(joined.messages.map((m) => m.id), range(20, 61).map((m) => m.id))
  assert.equal(joined.connected, true); assert.equal(joined.extendsStart, true)
  assert.equal(joined.messages.find((m) => m.id === 'opaque-27'), saved[0])
  const separate = mergeHistory([...range(1, 5), ...range(50, 61)], range(20, 49), 'opaque-50')
  assert.equal(separate.connected, false)
  assert.deepEqual(separate.messages.map((m) => m.id), [...range(1, 5), ...range(20, 61)].map((m) => m.id))
})

test('a lazy full record that finishes during a revisit’s tail read survives that response', async () => {
  installDom(); document.querySelector = () => null
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (href, { signal }) => new Response(new ReadableStream({ start(controller) { signal.addEventListener('abort', () => controller.close()) } }))
  const row = { id: 'one', members: [{ persona: 'sprout' }], unread: 0 }
  const record = () => ({ thread: row, messages: [{ id: 'm1', author: 'you', text: 'A thought' }, { id: 'm2', author: 'sprout', text: 'The reply' }], has_more: false, turn: null, board: { path: 'board.md', markdown: '' } })
  let finishTail, held = false
  const steps = [{ type: 'text', text: 'The whole reply' }], field = { value: '' }
  const threads = createThreads({ api: async (method, route) => {
    if (route.includes('/steps?')) return { steps: [{ message: 'm2', steps }] }
    if (held) return new Promise((resolve) => { finishTail = () => resolve(record()) })
    return record()
  }, redraw() {}, enter() {}, agents: () => agents,
  compose: { field: () => field, restore() {}, files: () => [], leave() {} } })
  threads.st.checked = true; threads.st.threads = [row]
  try {
    await threads.open('one'); threads.close()
    held = true
    const returned = threads.open('one')
    threads.st.open.replySteps.ensure('m2')
    await new Promise((resolve) => setTimeout(resolve, 0))
    assert.equal(threads.st.open.messages[1].steps, steps)
    finishTail(); await returned
    assert.equal(threads.st.open.messages[1].steps, steps)
  } finally { threads.close(); globalThis.fetch = originalFetch }
})

test('a thread address serves the app shell on a cold HTTP load without reading the broker', async (t) => {
  const broker = await fakeBroker(), app = await appAgainst(broker)
  t.after(closing(app, broker))
  const response = await fetch(app.url + '/threads/opaque-thread')
  assert.equal(response.status, 200)
  assert.match(response.headers.get('content-type'), /text\/html/)
  assert.match(await response.text(), /app\.js/)
  assert.deepEqual(broker.seen, [])
  assert.equal((await fetch(app.url + '/threads/one/extra')).status, 404)
})
