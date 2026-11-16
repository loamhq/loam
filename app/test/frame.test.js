import { threadPage } from './thread-page.js'
// A frame that never comes, and a page that draws anyway.
//
// The page draws whole screens, so it waits for the next animation frame before
// it draws one and coalesces everything that happened in between. What it used
// to do with that wait was set a flag, ask for a frame, and clear the flag in
// the callback — so a callback that never ran left the flag set, and every
// later redraw, for anything, returned at its first line. The page was then
// frozen for the rest of its life: the rows it had, the marks it had, the times
// it had, until it was reloaded.
//
// **That is a phone's ordinary day**, not an exotic failure: a tab in the
// background, a screen that goes off, a page restored from the back/forward
// cache. It is also the shape of what the owner saw on 2026-09-21 — "when I mark
// all as read, the border line around the icon stays until I refresh the page".
//
// So this loads the whole page against a browser whose `requestAnimationFrame`
// **never calls back at all** and asks for the one thing that matters: does the
// screen still arrive? It is the extreme case on purpose — a real browser
// swallows a frame now and then rather than all of them — and a page that draws
// with none of them draws with some.
//
// Fixture-only, and worth saying plainly: `test/fakedom.js` is a DOM written
// for these tests, not a browser. What is proved here is the page's own
// scheduling, not that a phone paints it.

import test from 'node:test'
import assert from 'node:assert/strict'

import { installDom } from './fakedom.js'
import { fakeBroker, appAgainst, closing, until } from './harness.js'

const RECORDS = [
  {
    session_id: 'only-1',
    agent: 'scout',
    provider: 'claude-code',
    workspace: '/w/scout',
    created: '2026-09-21T10:00:00Z',
    last_activity: '2026-09-21T11:00:00Z',
    last_event_at: '2026-09-21T11:00:00Z',
    state: 'exited',
    status: 'done',
    title: 'The frame that never came',
    last_message: 'And the page drew anyway.',
    pinned: false,
    archived: false,
    record_seq: 7,
    pending_permissions: [],
    turns: [],
  },
]

const line = (obj) => `data: ${JSON.stringify(obj)}\n\n`
const json = (res, body) => {
  res.writeHead(200, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}

const streams = []
const routes = {
  'GET /v1/agents': (req, res) => json(res, { agents: [{ id: 'scout', name: 'Scout', emoji: '🔭' }] }),
  'GET /v1/providers': (req, res) =>
    json(res, { providers: [{ id: 'claude-code', name: 'Claude Code', connected: true, reason: null, usage: null }] }),
  'GET /v1/sessions': (req, res) => json(res, { sessions: RECORDS, live: 0, cap: 2 }),
  'GET /v1/sessions/events': (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(': open ping=15000\n\n')
    for (const record of RECORDS) res.write(line(record.session_id ? { type: 'broker.session', ...record } : record))
    streams.push(res)
  },
}
const runtime = threadPage(routes, { initial: [{ id: 'frame', title: RECORDS[0].title }] })
const broker = await fakeBroker(routes)
const app = await appAgainst(broker)

// A browser that gives the page everything except a frame.
const document = installDom()
const root = document.createElement('div')
const html = document.createElement('html')
html.dataset = {}
document.documentElement = html
document.getElementById = (id) => (id === 'app' ? root : null)
document.querySelector = () => null
document.addEventListener = () => {}
document.visibilityState = 'visible'

const asked = []
globalThis.requestAnimationFrame = (fn) => asked.push(fn) // and never called
globalThis.window = { addEventListener: () => {} }
if (!globalThis.navigator?.language) {
  Object.defineProperty(globalThis, 'navigator', { value: { language: 'en-GB' }, configurable: true })
}
const held = new Map()
globalThis.localStorage = {
  getItem: (k) => (held.has(k) ? held.get(k) : null),
  setItem: (k, v) => held.set(k, String(v)),
  removeItem: (k) => held.delete(k),
  key: (i) => [...held.keys()][i] ?? null,
  get length() {
    return held.size
  },
}
const realFetch = globalThis.fetch
globalThis.fetch = (url, options) => realFetch(String(url).startsWith('/') ? `${app.url}${url}` : url, options)

await import('../public/app.js')

test.after(async () => {
  globalThis.fetch = realFetch
  for (const stream of streams) stream.end()
  await closing(broker, app)()
  setImmediate(() => process.exit(process.exitCode ?? 0))
})

const all = (node, cls, out = []) => {
  if (node.nodeType === 1 && (node.getAttribute('class') ?? '').split(' ').includes(cls)) out.push(node)
  for (const child of node.childNodes ?? []) all(child, cls, out)
  return out
}
const text = (node) => (node ? node.textContent : null)

test('a page that is never given a frame still draws its screen', async () => {
  const rows = await until(() => (all(root, 'srow').length > 0 ? all(root, 'srow') : null), { timeout: 5000 })
  assert.equal(rows.length, 1)
  assert.equal(text(all(rows[0], 'srow-title')[0]), 'The frame that never came')
  // And the page did ask for frames: this is a fallback behind the frame, not
  // the frame being abandoned.
  assert.equal(asked.length > 0, true, 'the page never asked for an animation frame')
})

test('and it keeps drawing: a later change arrives on the same page', async () => {
  const moved = { ...RECORDS[0], title: 'And the one after it', last_activity: '2026-09-21T12:00:00Z' }
  await until(() => runtime.listening())
  runtime.change({ ...runtime.records.get('frame').thread, title: moved.title })
  await until(() => all(root, 'srow-title').some((node) => node.textContent === 'And the one after it'), {
    timeout: 5000,
  })
})
