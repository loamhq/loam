import { setOwner } from '../public/owner.js'
setOwner({ id: 'you', name: 'Sam', accepts: ['you', 'owner', 'person'] })
import test from 'node:test'
import assert from 'node:assert/strict'
import { setImmediate as settle } from 'node:timers/promises'
import { createThreads } from '../public/threads.js'
import { createLink, CONNECT_TIMEOUT_MS, DEFAULT_PING_MS, STALE_MARGIN_MS } from '../public/link.js'
import { installDom } from './fakedom.js'
import { h } from '../public/ui.js'
import { find, text } from './browser.js'

const agents = [{ id: 'sprout', name: 'Sprout' }]
const baseRow = () => ({
  id: 'one',
  title: 'A quiet check',
  default: 'sprout',
  members: [{ persona: 'sprout', engine: 'claude-code', model: null, busy: false, working: false, background: 0 }],
  unread: 0,
})

function setup(t, { mockDate = false } = {}) {
  installDom()
  document.querySelector = () => null
  t.mock.timers.enable({ apis: mockDate ? ['setTimeout', 'Date'] : ['setTimeout'] })
  const originalFetch = globalThis.fetch
  const originalOnLine = Object.getOwnPropertyDescriptor(globalThis.navigator ?? {}, 'onLine')
  const state = {
    row: baseRow(),
    messages: [{ id: 'm1', author: 'you', kind: 'message', at: '2026-11-23T20:00:00Z', text: 'First thought.' }],
    turns: [],
    streamStatus: 200,
    hangConnect: false,
    autoSnapshot: true,
    nextId: 2,
  }
  const streams = []
  const reads = []
  const field = { value: '' }
  const setOnLine = (value) => Object.defineProperty(globalThis.navigator, 'onLine', { value, configurable: true })

  globalThis.fetch = async (href, { signal } = {}) => {
    if (String(href).includes('archived=1')) {
      const s = { href: String(href), signal, kind: 'list' }
      streams.push(s)
      return new Response(new ReadableStream({
        start(controller) {
          s.emit = (ev) => controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`))
          signal?.addEventListener('abort', () => { try { controller.close() } catch {} })
        },
      }), { headers: { 'content-type': 'text/event-stream' } })
    }
    if (state.hangConnect) {
      return new Promise((_, reject) => {
        signal?.addEventListener('abort', () => reject(Object.assign(new Error('Aborted'), { name: 'AbortError' })))
      })
    }
    if (state.streamStatus !== 200) {
      return new Response(JSON.stringify({ error: 'The server is unavailable.' }), {
        status: state.streamStatus,
        headers: { 'content-type': 'application/json' },
      })
    }
    const url = new URL(String(href), 'http://example.com')
    const afterNum = Number(String(url.searchParams.get('after') ?? 'm0').match(/(\d+)$/)?.[1] ?? 0)
    const s = { href: String(href), after: url.searchParams.get('after'), signal, kind: 'thread', closed: false }
    streams.push(s)
    const body = new ReadableStream({
      start(controller) {
        const enc = new TextEncoder()
        const push = (raw) => { if (!s.closed) controller.enqueue(enc.encode(raw)) }
        s.comment = (line) => push(`: ${line}\n\n`)
        s.emit = (ev) => push(`data: ${JSON.stringify(ev)}\n\n`)
        s.end = () => { if (!s.closed) { s.closed = true; controller.close() } }
        s.fail = (err = new Error('Socket reset')) => { if (!s.closed) { s.closed = true; controller.error(err) } }
        signal?.addEventListener('abort', () => {
          if (!s.closed) { s.closed = true; try { controller.close() } catch {} }
        })
        if (state.autoSnapshot) {
          s.comment(`open ping=${DEFAULT_PING_MS}`)
          s.emit({
            type: 'thread.snapshot',
            thread: 'one',
            turn: state.turns[0] ?? null,
            turns: structuredClone(state.turns),
            marks: [],
            pending: [],
            members: structuredClone(state.row.members),
          })
          for (const msg of state.messages) {
            const n = Number(String(msg.id).match(/(\d+)$/)?.[1] ?? 0)
            if (n > afterNum) s.emit({ type: 'message.done', message: structuredClone(msg) })
          }
        }
      },
    })
    return new Response(body, { headers: { 'content-type': 'text/event-stream' } })
  }

  let redraws = 0
  const threads = createThreads({
    api: async (method, route, body) => {
      reads.push({ method, route, body })
      if (route === '/threads?archived=1') return { threads: [structuredClone(state.row)], agents }
      if (route.startsWith('/agents/drafts')) return { drafts: [] }
      if (route.startsWith('/threads/one/messages') && method === 'POST') {
        const msg = { id: `m${state.nextId++}`, author: 'you', kind: 'message', at: new Date().toISOString(), text: body.text }
        state.messages.push(msg)
        return { accepted: true, message: structuredClone(msg) }
      }
      if (route.startsWith('/threads/one')) {
        return {
          thread: structuredClone(state.row),
          messages: structuredClone(state.messages),
          has_more: false,
          turn: state.turns[0] ?? null,
          turns: structuredClone(state.turns),
        }
      }
      return {}
    },
    redraw() { redraws++ },
    enter() {},
    agents: () => agents,
    nav: () => null,
    compose: {
      field: () => field,
      restore() {},
      files: () => [],
      clear() {},
      busy: () => false,
      leave() {},
      render: () => h('div', { class: 'composer' }),
      dragProps: () => ({}),
    },
  })

  t.after(() => {
    threads.pause()
    threads.close()
    globalThis.fetch = originalFetch
    if (originalOnLine) Object.defineProperty(globalThis.navigator, 'onLine', originalOnLine)
    else delete globalThis.navigator?.onLine
  })

  const threadStreams = () => streams.filter((s) => s.kind === 'thread')
  const flush = async () => { for (let i = 0; i < 6; i++) await settle() }
  const tick = async (ms) => {
    await flush()
    t.mock.timers.tick(ms)
    await flush()
    t.mock.timers.tick(0)
    await flush()
  }
  return { state, threads, field, reads, streams, threadStreams, tick, flush, setOnLine, get redraws() { return redraws } }
}

test('a dropped thread stream reconnects, shows a running turn and delivers a missed reply without navigation', async (t) => {
  const f = setup(t, { mockDate: true })
  await f.threads.open('one')
  await f.tick(10)
  const initialReads = f.reads.filter((r) => r.route.startsWith('/threads/one?limit=')).length
  assert.equal(f.threadStreams().length, 1)

  // Drop the open stream and start a turn on the server while disconnected.
  f.state.turns = [{ persona: 'sprout', engine: 'claude-code', started_at: '2026-11-23T20:01:00Z' }]
  f.state.row.members[0].working = true
  f.threadStreams()[0].end()
  await f.tick(10)

  assert.equal(f.threadStreams().length, 2, 'reconnected immediately after the drop')
  assert.equal(f.threadStreams()[1].after, 'm1', 'reconnected from the last durable message')
  const runningView = f.threads.view(false)
  assert.match(text(find(runningView, 'awaiting')), /Sprout · Thinking…/)
  assert.equal(f.threads.st.open.connection, '')

  // Drop again; a reply finishes on the server while the stream is down.
  f.state.turns = []
  f.state.row.members[0].working = false
  f.state.messages.push({ id: 'm2', author: 'sprout', kind: 'message', at: '2026-11-23T20:02:00Z', text: 'Here is the answer.' })
  f.threadStreams()[1].end()
  await f.tick(10)

  assert.equal(f.threadStreams().length, 3)
  assert.equal(f.threads.st.open.messages.some((m) => m.id === 'm2' && m.text === 'Here is the answer.'), true)
  assert.equal(find(f.threads.view(false), 'awaiting'), null, 'thinking indicator cleared when turn finished')
  assert.equal(f.reads.filter((r) => r.route.startsWith('/threads/one?limit=')).length, initialReads, 'no full thread GET was needed')
})

test('a silently stalled thread stream reconnects on heartbeat timeout and catches up without leaving the thread', async (t) => {
  const f = setup(t, { mockDate: true })
  await f.threads.open('one')
  await f.tick(10)
  const first = f.threadStreams()[0]
  assert.equal(first.closed, false)

  // Socket stays open and never errors, but stops sending pings while a reply lands.
  f.state.messages.push({ id: 'm2', author: 'sprout', kind: 'message', at: '2026-11-23T20:03:00Z', text: 'Arrived during silent stall.' })
  await f.tick(DEFAULT_PING_MS + STALE_MARGIN_MS - 1000)
  assert.equal(f.threadStreams().length, 1, 'healthy window does not reconnect early')

  // Cross the ping + margin threshold: the watchdog detects silence and reopens.
  await f.tick(2000)
  assert.equal(first.signal.aborted, true, 'dead socket was aborted')
  assert.equal(f.threadStreams().length, 2, 'reopened on heartbeat timeout')
  assert.equal(f.threadStreams()[1].after, 'm1')
  assert.equal(f.threads.st.open.messages.some((m) => m.id === 'm2' && m.text === 'Arrived during silent stall.'), true)
  assert.equal(f.threads.st.open.connection, '')
})

test('returning from hidden to visible resyncs at once even inside the ping window', async (t) => {
  const f = setup(t, { mockDate: true })
  await f.threads.open('one')
  await f.tick(10)
  const first = f.threadStreams()[0]

  // Only 3 seconds pass (well under 25 s), socket is silently dead, and a reply lands.
  await f.tick(3000)
  f.state.messages.push({ id: 'm2', author: 'sprout', kind: 'message', at: '2026-11-23T20:01:03Z', text: 'Finished while backgrounded.' })
  f.state.turns = [{ persona: 'sprout', engine: 'claude-code', started_at: '2026-11-23T20:01:05Z' }]
  f.state.row.members[0].working = true

  // Simulate visibilitychange / online / pageshow resync.
  f.threads.tick(true, { resync: true })
  await f.tick(10)

  assert.equal(first.signal.aborted, true, 'previous socket closed for resync')
  assert.equal(f.threadStreams().length, 2, 'fresh stream opened right away')
  assert.equal(f.threads.st.open.messages.some((m) => m.id === 'm2' && m.text === 'Finished while backgrounded.'), true)
  assert.match(text(find(f.threads.view(false), 'awaiting')), /Sprout · Thinking…/)
  assert.equal(f.threads.st.open.connection, '', 'clean resync does not flash a disconnect notice')
})

test('sending on a silently dead stream does not skip missed replies and recovers the running turn quickly', async (t) => {
  const f = setup(t, { mockDate: true })
  await f.threads.open('one')
  await f.tick(10)
  const first = f.threadStreams()[0]

  // While socket #1 is silently dead, an earlier reply m2 lands on the server.
  f.state.messages.push({ id: 'm2', author: 'sprout', kind: 'message', at: '2026-11-23T20:01:00Z', text: 'Earlier missed reply.' })
  f.state.nextId = 3

  // Owner sends a new message (m3) via HTTP POST; the server starts a turn for it.
  f.field.value = 'Did you see that?'
  f.state.turns = [{ persona: 'sprout', engine: 'claude-code', started_at: '2026-11-23T20:02:00Z' }]
  f.state.row.members[0].working = true
  await f.threads.send()

  assert.equal(f.threads.st.open.messages.some((m) => m.id === 'm3' && m.text === 'Did you see that?'), true, 'own message renders right away')
  assert.equal(f.threads.st.open.after, 'm1', 'HTTP send confirmation must not jump the SSE cursor over unseen messages')

  // Because dead socket #1 never echoes m3, the send-echo watchdog resyncs within a few seconds.
  await f.tick(4000)
  assert.equal(first.signal.aborted, true, 'silent dead stream aborted after missing echo')
  assert.equal(f.threadStreams().length, 2)
  assert.equal(f.threadStreams()[1].after, 'm1', 'reconnected before m2 so nothing is skipped')
  assert.deepEqual(
    f.threads.st.open.messages.filter((m) => !m.streaming).map((m) => m.id),
    ['m1', 'm2', 'm3'],
    'missed reply m2 recovered in order and own message m3 deduplicated',
  )
  assert.equal(f.threads.st.open.after, 'm3')
  assert.match(text(find(f.threads.view(false), 'awaiting')), /Sprout · Thinking…/)
})

test('when the connection stays down or offline, a calm honest notice with Try again appears and recovers cleanly', async (t) => {
  const f = setup(t, { mockDate: true })
  f.setOnLine(true)
  await f.threads.open('one')
  await f.tick(10)

  // Server becomes unreachable and the open stream drops.
  f.state.streamStatus = 503
  f.threadStreams()[0].end()
  await f.tick(10)
  assert.equal(f.threads.st.open.connection, 'Can’t reach the server — still trying.')
  assert.equal(f.threads.st.open.connectionDown, true)

  const notice = find(f.threads.view(false), 'tt-connection')
  assert.ok(notice, 'connection notice rendered')
  assert.match(notice.getAttribute('class'), /\bwarn\b/)
  assert.ok(find(notice, 'noticerow-dot'), 'calm warning dot rendered')
  const retryBtn = find(notice, 'tt-connection-retry')
  assert.equal(text(retryBtn), 'Try again')

  // Offline state speaks plainly right away.
  f.setOnLine(false)
  f.threads.offline()
  assert.equal(f.threads.st.open.connection, 'Offline — updates will resume when you’re back online.')

  // Network and server return; tapping Try again reconnects and delivers missed reply.
  f.setOnLine(true)
  f.state.streamStatus = 200
  f.state.messages.push({ id: 'm2', author: 'sprout', kind: 'message', at: '2026-11-23T20:04:00Z', text: 'Back online.' })
  retryBtn.fire('click')
  await f.tick(10)

  assert.equal(f.threads.st.open.connection, '')
  assert.equal(f.threads.st.open.connectionDown, false)
  assert.equal(f.threads.st.open.messages.some((m) => m.id === 'm2' && m.text === 'Back online.'), true)
  assert.equal(find(f.threads.view(false), 'tt-connection'), null)
})

test('a hung connect attempt times out instead of sticking in connecting forever', async (t) => {
  let clock = 1000
  const timers = []
  const fakeTimers = {
    set(fn, ms) {
      const entry = { fn, at: clock + ms, cancelled: false }
      timers.push(entry)
      return entry
    },
    clear(entry) { if (entry) entry.cancelled = true },
  }
  const runTimers = async () => {
    for (;;) {
      const next = timers.filter((x) => !x.cancelled && x.at <= clock).sort((a, b) => a.at - b.at)[0]
      if (!next) break
      next.cancelled = true
      next.fn()
      await settle()
    }
  }

  let calls = 0
  const phases = []
  const link = createLink({
    href: () => '/api/threads/one/events?after=m1',
    watchdog: true,
    now: () => clock,
    timers: fakeTimers,
    onEvent() {},
    onPhase: (phase, detail) => phases.push({ phase, detail }),
    fetchImpl: async (_url, { signal }) => {
      calls++
      if (calls === 1) {
        return new Promise((_, reject) => {
          signal.addEventListener('abort', () => reject(Object.assign(new Error('Aborted'), { name: 'AbortError' })))
        })
      }
      return new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(': open ping=15000\n\n'))
          signal.addEventListener('abort', () => { try { controller.close() } catch {} })
        },
      }), { headers: { 'content-type': 'text/event-stream' } })
    },
  })
  const running = link.start()
  t.after(async () => { link.stop(); await running })
  await settle()

  assert.equal(link.phase, 'connecting')
  assert.equal(link.isStale(), false)

  clock += CONNECT_TIMEOUT_MS + 10
  assert.equal(link.isStale(), true)
  await runTimers()
  await settle()

  assert.equal(calls, 2, 'hung connect was aborted and retried')
  assert.equal(link.phase, 'open')
  assert.ok(phases.some((p) => p.phase === 'retrying' && p.detail === 'the stream went quiet'))
})
