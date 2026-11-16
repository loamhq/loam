// The live connection, caused rather than reasoned about.
//
// Every test here drives the page's own `link.js` against this app's own
// `/api`, with a broker that is a recording behind it. What is a fixture is
// said plainly each time: the clock and the timers are injected in two of them,
// because a test cannot wait fifteen seconds for a keepalive that never comes,
// and because a backoff is only worth asserting if the test controls the wait.
// The drops themselves are real: a real HTTP stream, really hung up on.

import test from 'node:test'
import assert from 'node:assert/strict'

import { createLink, statusLine, backoffFor, BACKOFF_MS, DEFAULT_PING_MS, STALE_MARGIN_MS } from '../public/link.js'
import { createTranscript, apply, caughtUp } from '../public/transcript.js'
import { createStore } from '../public/store.js'
import { fakeBroker, appAgainst, closing, until } from './harness.js'

const RECORD = {
  session_id: 's-1',
  agent: 'example',
  workspace: '/w/example',
  state: 'running',
  record_seq: 3,
  turns: [],
}

const LOG = [
  { type: 'broker.user_message', seq: 1, session_id: 's-1', text: 'what did you do yesterday?' },
  {
    type: 'assistant',
    seq: 2,
    session_id: 's-1',
    message: { id: 'm-1', role: 'assistant', model: 'claude-opus-5', content: [{ type: 'text', text: 'this and that.' }] },
  },
  { type: 'result', seq: 3, session_id: 's-1', subtype: 'success', duration_ms: 1200, num_turns: 1, usage: {} },
]

const line = (obj) => `data: ${JSON.stringify(obj)}\n\n`

// A session's event stream: the record first, then everything after `from` from
// the log, then silence — an open connection with nothing more to say, which is
// exactly the state a phone's connection dies in.
function eventStream(opened) {
  return (req, res, url) => {
    const from = Number(url.searchParams.get('after') ?? 0)
    const connection = { from, closed: false }
    opened.push(connection)
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(line({ type: 'broker.session', ...RECORD }))
    for (const event of LOG) if (event.seq > from) res.write(line(event))
    // The response, not the request: this broker has already read the request
    // to its end, so the thing that closes when the app hangs up is `res`.
    res.on('close', () => {
      connection.closed = true
    })
  }
}

function linkTo(app, state, options = {}) {
  return createLink({
    sessionId: 's-1',
    href: () => `${app.url}/api/threads/s-1/events${state.lastSeq == null ? '' : `?after=${state.lastSeq}`}`,
    from: () => state.lastSeq,
    onEvent: (event) => apply(state, event),
    ...options,
  })
}

test('opening a stream ignores old browser reading positions', async (t) => {
  const opened = []
  const broker = await fakeBroker({ 'GET /v1/threads/s-1/events': eventStream(opened) })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  // A browser that watched the tail of this session last week, the way the owner's
  // phone had: a position it remembered, and nothing else.
  const storage = fakeStorage({ 'loam-ui.session': '{"id":"s-1","agent":"example"}', 'loam-ui.seq.s-1': '2' })
  createStore(storage)
  assert.equal(storage.getItem('loam-ui.session'), null, 'which session was open is not remembered either')
  assert.equal(storage.getItem('loam-ui.seq.s-1'), null, 'the position it used to remember is gone')

  // The generic connection reads the href provided by its caller. These
  // recorded events exercise the transcript adapter; the production caller
  // now supplies a thread URL, and the retired session relay is not needed.
  const state = createTranscript()
  const link = linkTo(app, state)
  const running = link.start()
  t.after(async () => {
    link.stop()
    await running
  })

  await until(() => state.lastSeq === 3)
  const call = broker.seen.find((c) => c.path === '/v1/threads/s-1/events')
  assert.equal(call.search, '', 'the first connection asked for the session whole')
  assert.deepEqual(
    state.timeline.map((n) => n.kind),
    ['user', 'assistant', 'result'],
    'both sides of the conversation are drawn',
  )
  assert.equal(caughtUp(state), true, 'and the page knows it has read as far as the record goes')
})

test('the page is catching up until it has read as far as the record says', async (t) => {
  // The record first and the log a moment later, which is how a replay of any
  // length actually arrives: the page knows where the session ends before it
  // has read its way there.
  let release
  const held = new Promise((resolve) => {
    release = resolve
  })
  const broker = await fakeBroker({
    'GET /v1/threads/s-1/events': async (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write(line({ type: 'broker.session', ...RECORD }))
      await held
      for (const event of LOG) res.write(line(event))
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const state = createTranscript()
  const phases = []
  const link = linkTo(app, state, { onPhase: (phase) => phases.push(phase) })
  const running = link.start()
  t.after(async () => {
    link.stop()
    await running
  })

  // The record arrives first and says the log reaches 3. Until the page has
  // read that far it is replaying history, and the status line says so.
  await until(() => state.session !== null)
  assert.equal(statusLine('open', { caughtUp: caughtUp(state) }), 'catching up…')
  release()
  await until(() => state.lastSeq === 3)
  assert.equal(statusLine('open', { caughtUp: caughtUp(state) }), 'live')
  assert.deepEqual(phases, ['connecting', 'open'])
})

test('a stream that has gone quiet is dropped and reopened at the right position', async (t) => {
  const opened = []
  const broker = await fakeBroker({ 'GET /v1/threads/s-1/events': eventStream(opened) })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  // The clock is the fixture: a real wait for a missed keepalive is fifteen
  // seconds, and what is being tested is the arithmetic, not the patience.
  let clock = 1_000_000
  const state = createTranscript()
  const link = linkTo(app, state, { now: () => clock })
  const running = link.start()
  t.after(async () => {
    link.stop()
    await running
  })

  await until(() => state.lastSeq === 3)
  assert.equal(link.pingMs, DEFAULT_PING_MS, 'the interval was read off the stream’s opening comment')
  assert.equal(link.isStale(), false)
  assert.equal(link.wake(), false, 'a healthy stream is left alone')
  assert.equal(opened.length, 1)

  // Nothing since: no event, and no `: ping` either. Past the threshold that is
  // not a quiet connection, it is a gone one.
  clock += DEFAULT_PING_MS + STALE_MARGIN_MS + 1
  assert.equal(link.isStale(), true)
  const wokeAt = Date.now()
  assert.equal(link.wake(), true)

  await until(() => opened.length === 2)
  assert.ok(Date.now() - wokeAt < 1000, 'it reopened at once rather than waiting to be told')
  await until(() => opened[0].closed, { timeout: 2000 }) // the one it no longer believed in was hung up on
  assert.equal(opened[1].from, 3, 'reopened at the last event seen, not at the end of the log')
  assert.equal(state.timeline.length, 3, 'the replay after it changed nothing: the same events are the same events')
})

test('bytes of an unfinished large event keep the link alive without advancing the transcript', async (t) => {
  let stream
  const broker = await fakeBroker({
    'GET /v1/threads/s-1/events': (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write(': open ping=1000\n\n' + line({ type: 'broker.session', ...RECORD }) + line(LOG[0]))
      stream = res
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))
  let clock = 100000
  const state = createTranscript()
  const link = linkTo(app, state, { now: () => clock })
  const running = link.start()
  t.after(async () => { link.stop(); await running })
  await until(() => state.lastSeq === 1)
  stream.write('data: {"type":"user","seq":2,"padding":"')
  for (let i = 0; i < 3; i++) {
    clock += 11001
    assert.equal(link.isStale(), true)
    stream.write('x'.repeat(16000))
    await until(() => !link.isStale())
    assert.equal(link.wake(), false, 'a partial frame was mistaken for a dead connection')
    assert.equal(state.lastSeq, 1, 'bytes were treated as a complete event')
  }
  clock += 11001
  assert.equal(link.isStale(), true, 'silence after the bytes stopped was not noticed')
})

test('coming back reconnects at once rather than waiting out the backoff', async (t) => {
  let refusals = 2
  const opened = []
  const serve = eventStream(opened)
  const broker = await fakeBroker({
    'GET /v1/threads/s-1/events': (req, res, url) => {
      if (refusals > 0) {
        refusals -= 1
        res.writeHead(503, { 'content-type': 'application/json' })
        return res.end(JSON.stringify({ error: 'the broker is not answering' }))
      }
      return serve(req, res, url)
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  // The timers are the fixture: they never fire, so the link stays parked
  // between attempts exactly as a backgrounded phone parks it — and waking it
  // is the only thing that can move it.
  const waits = []
  const timers = { set: (fn, ms) => waits.push(ms), clear: () => {} }

  const state = createTranscript()
  const link = linkTo(app, state, { timers })
  const running = link.start()
  t.after(async () => {
    link.stop()
    await running
  })

  await until(() => waits.length === 1)
  assert.equal(link.phase, 'retrying')
  assert.equal(statusLine(link.phase, { detail: link.detail }), 'disconnected, retrying… (the broker is not answering)')
  const attempts = () => broker.seen.filter((c) => c.path === '/v1/threads/s-1/events').length
  assert.equal(attempts(), 1, 'and it is going nowhere on its own')

  const wokeAt = Date.now()
  assert.equal(link.wake(), true)
  await until(() => attempts() === 2)
  assert.ok(Date.now() - wokeAt < 1000, 'the page came back and the connection came back with it')

  // Still refused once more; the second wake gets the stream that works.
  await until(() => waits.length === 2)
  link.wake()
  await until(() => state.lastSeq === 3)
  assert.equal(link.phase, 'open')
})

test('the retry waits climb from nothing to a few seconds and no further', async (t) => {
  const broker = await fakeBroker({
    'GET /v1/threads/s-1/events': (req, res) => {
      res.writeHead(503, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: 'still nothing' }))
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  // The waits are asked for and recorded, then taken immediately: what is under
  // test is the sequence of lengths, not the wall clock.
  const waits = []
  const timers = { set: (fn, ms) => { waits.push(ms); return setTimeout(fn, 0) }, clear: (h) => clearTimeout(h) }

  const state = createTranscript()
  const link = linkTo(app, state, { timers })
  const running = link.start()
  await until(() => waits.length >= 8)
  link.stop()
  await running

  assert.deepEqual(waits.slice(0, 5), BACKOFF_MS, 'first retry immediate, then a short climb')
  assert.ok(Math.max(...waits) <= 4000, `no wait exceeded the cap: ${waits.join(', ')}`)
  assert.equal(backoffFor(99), 4000, 'and it stays there however long this goes on')
  assert.equal(backoffFor(0), 0)
})

test('a session the broker does not have is not a session to retry at', async (t) => {
  const broker = await fakeBroker({}) // every route is a 404
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const state = createTranscript()
  const link = linkTo(app, state)
  await link.start()
  assert.equal(link.phase, 'stopped')
  assert.equal(link.detail, 'no such route')
  assert.equal(link.wake(), false, 'and waking it does not start it again')
})

test('every phase of the connection has a word for it', () => {
  assert.equal(statusLine('connecting'), 'connecting…')
  assert.equal(statusLine('open', { caughtUp: false }), 'catching up…')
  assert.equal(statusLine('open', { caughtUp: true }), 'live')
  assert.equal(statusLine('retrying'), 'disconnected, retrying…')
  assert.equal(statusLine('retrying', { detail: 'the stream went quiet' }), 'disconnected, retrying… (the stream went quiet)')
  assert.equal(statusLine('stopped', { detail: 'no such session' }), 'no such session')
})

// localStorage, in about as many lines as it takes to say what it is.
function fakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial))
  return {
    get length() {
      return map.size
    },
    key: (i) => [...map.keys()][i] ?? null,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
  }
}

test('the browser remembers neither which session was open nor where it had read to', () => {
  const storage = fakeStorage({ 'loam-ui.seq.s-1': '44', 'loam-ui.seq.s-2': '9', 'loam-ui.session': '{"id":"s-2","agent":"example"}', 'loam-ui.theme': '"dark"' })
  const store = createStore(storage)
  assert.equal('current' in store, false, 'there is no open chat to keep')
  assert.equal(storage.getItem('loam-ui.session'), null, 'the chat an earlier version kept is dropped')
  for (let i = 0; i < storage.length; i += 1) {
    assert.doesNotMatch(storage.key(i), /seq/, 'nothing positional is persisted')
  }
  assert.equal(storage.getItem('loam-ui.theme'), null, 'appearance no longer has a browser-owned value')
})

// ------------------------------------------------- an ended chat's stream

// A session whose process is gone: the log replayed, and the stream ended
// where the log ends. Nothing more will ever arrive on it.
const ENDED = { ...RECORD, state: 'exited', record_seq: 4 }

function endedStream(opened) {
  return (req, res, url) => {
    const from = Number(url.searchParams.get('after') ?? 0)
    opened.push({ from })
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(line({ type: 'broker.session', ...ENDED }))
    for (const event of LOG) if (event.seq > from) res.write(line(event))
    res.write(line({ type: 'broker.exit', seq: 4, session_id: 's-1', exit_code: 0 }))
    res.end() // the log ends here, and so does the stream
  }
}

test('an ended chat draws its transcript and settles, rather than reconnecting for ever', async (t) => {
  const opened = []
  const broker = await fakeBroker({ 'GET /v1/threads/s-1/events': endedStream(opened) })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const state = createTranscript()
  const link = linkTo(app, state, {
    settled: () => Boolean(state.exit) || state.session?.state === 'exited',
  })
  await link.start()

  // The whole conversation, both sides, from a session this browser has never
  // watched — which is what opening one from the home screen is.
  assert.deepEqual(
    state.timeline.map((item) => item.kind),
    ['user', 'assistant', 'result', 'exit'],
  )
  assert.equal(opened.length, 1, `the stream was reopened ${opened.length - 1} times after it had said everything`)
  assert.equal(link.phase, 'stopped')

  // And it stays settled: coming back to the page does not start it again.
  assert.equal(link.wake(), false)
  await new Promise((resolve) => setTimeout(resolve, 60))
  assert.equal(opened.length, 1)
})

test('sending to an ended chat opens the stream again, at where the page has read to', async (t) => {
  const opened = []
  const broker = await fakeBroker({ 'GET /v1/threads/s-1/events': endedStream(opened) })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const state = createTranscript()
  const link = linkTo(app, state, { settled: () => Boolean(state.exit) })
  await link.start()
  assert.equal(opened.length, 1)
  assert.equal(opened[0].from, 0, 'opening a session draws it whole')

  // The session was resumed under the same id, so there is something to wait
  // for again. A resumed session keeps its numbering, so the position is the
  // page's own last seq and not zero.
  link.restart()
  await until(() => opened.length === 2)
  assert.equal(opened[1].from, 4, 'the reopened stream asked for the log all over again')
  link.stop()
})
