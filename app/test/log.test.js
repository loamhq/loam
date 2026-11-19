// The app's own log: one JSON line per request, one per transcription, and
// nothing in either that a person said or sent or that could be spent. Against
// a broker and a Gemini that are only recordings, with the lines kept rather
// than printed.

import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import path from 'node:path'
import { once } from 'node:events'
import { stat } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

import { createApp } from '../src/server.js'
import { readConfig } from './key-fixture.js'
import { createLog, loggedQuery } from '../src/log.js'
import {
  VOCABULARY_MAX_TERMS,
  TRANSCRIBE_MAX_BYTES,
  GEMINI_TIMEOUT_FLOOR_MS,
  GEMINI_TIMEOUT_PER_MINUTE_MS,
  GEMINI_TIMEOUT_MAX_MS,
  AUDIO_BYTES_PER_MINUTE,
  geminiTimeoutMs,
} from '../../plugins/_listed/dictation/server/transcribe.js'
import { dictationApp } from './audio-servers.mjs'

const PUBLIC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'public')
import { TOKEN, fakeBroker, appAgainst, shut, closing, until, logInto } from './harness.js'

const REQUEST_KEYS = ['ts', 'level', 'event', 'id', 'method', 'path', 'query', 'status', 'bytes', 'ms']

function requestLines(lines) {
  return lines.filter((l) => l.event === 'request')
}

test('an API request writes exactly one line, of the right shape, and none of what was sent', async (t) => {
  const broker = await fakeBroker({
    'POST /v1/threads/s-1/messages': (req, res) => {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ accepted: true, seq: 12 }))
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(app, broker))

  const res = await fetch(`${app.url}/api/threads/s-1/messages?from=3&secret=hunter2`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', cookie: 'session=a-cookie-value', authorization: 'Bearer from-the-page' },
    body: JSON.stringify({ text: 'a thing only the agent should hear' }),
  })
  assert.equal(res.status, 200)
  const body = await res.text()
  const lines = await until(() => (requestLines(app.lines).length > 0 ? app.lines : null))

  assert.equal(app.lines.length, 1, 'one line, and only one')
  const [line] = lines
  assert.deepEqual(Object.keys(line), REQUEST_KEYS)
  assert.equal(line.level, 'info')
  assert.match(line.ts, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/)
  assert.match(line.id, /^[0-9a-f]{8}$/)
  assert.equal(line.id, res.headers.get('x-request-id'), 'the page can quote the id it was answered under')
  assert.equal(line.method, 'POST')
  assert.equal(line.path, '/api/threads/s-1/messages')
  assert.equal(line.query, 'from=3&secret=…', 'a parameter this app does not know is named, not quoted')
  assert.equal(line.status, 200)
  assert.equal(line.bytes, Buffer.byteLength(body))
  assert.ok(Number.isInteger(line.ms) && line.ms >= 0)

  const written = JSON.stringify(app.lines)
  for (const secret of ['hunter2', 'a-cookie-value', 'from-the-page', TOKEN, 'only the agent should hear']) {
    assert.ok(!written.includes(secret), `the log does not carry ${secret}`)
  }
})

test('/up is logged, and every request gets its own id', async (t) => {
  const broker = await fakeBroker()
  const app = await appAgainst(broker)
  t.after(closing(app, broker))

  await (await fetch(`${app.url}/up`)).text()
  await (await fetch(`${app.url}/up`)).text()
  const lines = await until(() => (app.lines.length >= 2 ? app.lines : null))
  assert.equal(lines.length, 2)
  assert.deepEqual(lines.map((l) => [l.path, l.status, l.level, l.bytes]), [['/up', 200, 'info', 2], ['/up', 200, 'info', 2]])
  assert.notEqual(lines[0].id, lines[1].id)
})

test('a static file is a debug line; a static miss is not', async (t) => {
  const lines = []
  const { server } = createApp({ config: readConfig({}), log: logInto(lines) })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(() => shut(server))
  const url = `http://127.0.0.1:${server.address().port}`

  await (await fetch(`${url}/`)).text()
  await (await fetch(`${url}/nothing-here.js`)).text()
  await until(() => lines.length >= 2)
  const byPath = Object.fromEntries(lines.map((l) => [l.path, l]))
  assert.equal(byPath['/'].level, 'debug')
  assert.equal(byPath['/'].status, 200)
  assert.equal(byPath['/'].bytes, (await stat(path.join(PUBLIC, 'index.html'))).size, 'a piped file is counted too')
  assert.equal(byPath['/nothing-here.js'].level, 'info')
  assert.equal(byPath['/nothing-here.js'].status, 404)
})

test('a 5xx from the broker is an error line carrying the broker\'s sentence, once', async (t) => {
  const broker = await fakeBroker({
    'GET /v1/sessions': (req, res) => {
      res.writeHead(500, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: 'the session store could not be read' }))
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(app, broker))

  const res = await fetch(`${app.url}/api/sessions`)
  assert.equal(res.status, 500)
  await res.text()
  await until(() => app.lines.length >= 1)
  assert.equal(app.lines.length, 1)
  const [line] = app.lines
  assert.equal(line.level, 'error')
  assert.equal(line.status, 500)
  assert.equal(line.error, 'the session store could not be read')
  assert.equal(line.id, res.headers.get('x-request-id'))
})

test('an exception the server catches is logged under the request\'s id, on its one line', async (t) => {
  // A broker that is not there: the relay throws, and the page is answered 502.
  const gone = http.createServer()
  gone.listen(0, '127.0.0.1')
  await once(gone, 'listening')
  const url = `http://127.0.0.1:${gone.address().port}`
  await shut(gone)

  const lines = []
  const { server } = createApp({ config: readConfig({ BROKER_URL: url, BROKER_TOKEN: TOKEN }), log: logInto(lines) })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(() => shut(server))

  const res = await fetch(`http://127.0.0.1:${server.address().port}/api/agents`)
  assert.equal(res.status, 502)
  const said = (await res.json()).error
  await until(() => lines.length >= 1)
  assert.equal(lines.length, 1)
  const [line] = lines
  assert.equal(line.level, 'error')
  assert.equal(line.status, 502)
  assert.equal(line.error, said)
  assert.equal(typeof line.stack, 'string')
  assert.equal(line.id, res.headers.get('x-request-id'))
})

test('an event stream is one line, written when it closes, with what it carried', async (t) => {
  const broker = await fakeBroker({
    'GET /v1/threads/s-1/events': (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write('data: {"type":"broker.session"}\n\n')
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(app, broker))

  const abort = new AbortController()
  const res = await fetch(`${app.url}/api/threads/s-1/events?last=10`, { signal: abort.signal })
  const reader = res.body.getReader()
  await reader.read()
  assert.equal(app.lines.length, 0, 'nothing is written while the stream is open')
  abort.abort()
  await until(() => app.lines.length >= 1)
  const [line] = app.lines
  assert.equal(line.path, '/api/threads/s-1/events')
  assert.equal(line.query, 'last=10')
  assert.equal(line.status, 200)
  assert.equal(line.aborted, true)
  assert.ok(line.bytes > 0)
})

test('the level decides what is written, and off is silence', () => {
  const out = []
  const at = (level) => createLog({ level, write: (l) => out.push(JSON.parse(l).level) })
  for (const level of ['debug', 'info', 'error', 'off', 'nonsense']) {
    const log = at(level)
    log.debug({})
    log.info({})
    log.warn({})
    log.error({})
  }
  assert.deepEqual(out, [
    'debug', 'info', 'warn', 'error', // debug
    'info', 'warn', 'error', //          info
    'error', //                          error
    //                                   off
    'info', 'warn', 'error', //          anything else reads as info
  ])
  assert.equal(loggedQuery(new URLSearchParams('')), null)
  assert.equal(loggedQuery(new URLSearchParams('agent=a&archived=1&token=x')), 'agent=a&archived=1&token=…')
})

// Transcription, against a Gemini that is only a recording.

const KEY = 'test-gemini-key-not-a-real-one'
const WORDS = 'Hello Ada, the deploy is done.'
const AUDIO = Buffer.from([0x1a, 0x45, 0xdf, 0xa3, 1, 2, 3, 4, 5, 6])

async function transcribing(t, answer) {
  const gemini = http.createServer(async (req, res) => {
    for await (const _ of req) { /* drain */ }
    const [status, body] = answer()
    res.writeHead(status, { 'content-type': 'application/json' })
    res.end(JSON.stringify(body))
  })
  gemini.listen(0, '127.0.0.1')
  await once(gemini, 'listening')
  // Dictation's own server (its server half since 1.1.0): its lines go to
  // the plugin's journal, one per take.
  const lines = []
  const { server } = await dictationApp({ GEMINI_API_KEY: KEY, GEMINI_API_URL: `http://127.0.0.1:${gemini.address().port}` }, { lines })
  t.after(async () => {
    await shut(server)
    await shut(gemini)
  })
  const post = (vocabulary) => fetch(`http://127.0.0.1:${server.address().port}/transcribe`, {
    method: 'POST',
    headers: {
      'content-type': 'audio/webm;codecs=opus',
      ...(vocabulary ? { 'x-vocabulary': encodeURIComponent(JSON.stringify(vocabulary)) } : {}),
    },
    body: AUDIO,
  })
  return { lines, post, url: `http://127.0.0.1:${server.address().port}` }
}

const completed = (text) => ({ status: 'completed', steps: [{ type: 'model_output', content: [{ type: 'text', text }] }] })

test('a transcription writes its own line: sizes, timings, counts, outcome — never the words, the audio or the key', async (t) => {
  const { lines, post } = await transcribing(t, () => [200, completed(WORDS)])
  const res = await post(['Ada', 'Loam', 'Ada'])
  assert.equal(res.status, 200)
  await res.json()
  await until(() => lines.length >= 1)

  const line = lines.find((l) => l.event === 'transcribe')
  assert.equal(lines.length, 1, 'the transcription line')
  assert.equal(line.level, 'info')
  assert.equal(line.status, 200)
  assert.equal(line.bytes, AUDIO.length)
  assert.equal(line.mime, 'audio/webm;codecs=opus')
  assert.equal(line.gemini_status, 200)
  assert.ok(Number.isInteger(line.gemini_ms) && line.gemini_ms >= 0)
  assert.equal(line.timeout_ms, geminiTimeoutMs(AUDIO.length))
  assert.equal(line.chars, WORDS.length)
  assert.equal(line.biased, true)
  assert.equal(line.terms, 2)
  assert.equal(line.retried_without_terms, false)
  assert.equal(line.outcome, 'ok')
  assert.equal('error' in line, false)

  const written = JSON.stringify(lines)
  for (const secret of [KEY, 'deploy is done', AUDIO.toString('base64'), 'Loam']) {
    assert.ok(!written.includes(secret), `the log does not carry ${secret}`)
  }
})

test('a take with no words in it is heard-no-words, and a refusal is an error with its sentence', async (t) => {
  const quiet = await transcribing(t, () => [200, completed('')])
  const res = await quiet.post()
  assert.equal(res.status, 422)
  await res.json()
  await until(() => quiet.lines.some((l) => l.event === 'transcribe'))
  const heard = quiet.lines.find((l) => l.event === 'transcribe')
  assert.equal(heard.outcome, 'heard-no-words')
  assert.equal(heard.chars, 0)
  assert.equal(heard.biased, false)
  assert.equal(heard.terms, 0)

  const busy = await transcribing(t, () => [429, { error: { message: 'Quota exceeded.' } }])
  const refused = await busy.post()
  assert.equal(refused.status, 429)
  const said = (await refused.json()).error
  await until(() => busy.lines.some((l) => l.event === 'transcribe'))
  const line = busy.lines.find((l) => l.event === 'transcribe')
  assert.equal(line.level, 'warn')
  assert.equal(line.outcome, 'error')
  assert.equal(line.gemini_status, 429)
  assert.equal(line.error, said)
  assert.equal(line.chars, null)
})

test('a take over the size cap is an error line, refused before Gemini is asked', async (t) => {
  const { lines, url } = await transcribing(t, () => [200, completed(WORDS)])
  const res = await fetch(`${url}/transcribe`, {
    method: 'POST',
    headers: { 'content-type': 'audio/webm' },
    body: Buffer.alloc(TRANSCRIBE_MAX_BYTES + 1),
  })
  assert.equal(res.status, 413)
  const said = (await res.json()).error
  await until(() => lines.some((l) => l.event === 'transcribe'))
  const line = lines.find((l) => l.event === 'transcribe')
  assert.equal(line.outcome, 'error')
  assert.equal(line.status, 413)
  assert.equal(line.error, said)
})

// The two dictation limits, which the page's own numbers have to fit inside.

test('the server takes as many terms as the page keeps', async () => {
  const page = await import('../../plugins/_listed/dictation/page/dictate.js')
  assert.equal(VOCABULARY_MAX_TERMS, 1000)
  assert.equal(VOCABULARY_MAX_TERMS, page.VOCABULARY_MAX_TERMS)
})

test('Gemini is given longer for a longer take, from a floor, to a cap', async () => {
  const page = await import('../../plugins/_listed/dictation/page/dictate.js')
  assert.equal(GEMINI_TIMEOUT_FLOOR_MS, 120_000)
  assert.equal(GEMINI_TIMEOUT_PER_MINUTE_MS, 30_000)
  assert.equal(GEMINI_TIMEOUT_MAX_MS, 600_000)
  assert.equal(AUDIO_BYTES_PER_MINUTE, (page.RECORD_BITS_PER_SECOND / 8) * 60, 'read at the bitrate the page asks for')

  assert.equal(geminiTimeoutMs(0), 120_000)
  assert.equal(geminiTimeoutMs(5 * AUDIO_BYTES_PER_MINUTE), 270_000)
  // The longest take the page's settings allow gets the time it needs, inside the cap.
  const longest = geminiTimeoutMs(page.RECORD_MINUTES_MAX * AUDIO_BYTES_PER_MINUTE)
  assert.equal(longest, 570_000)
  assert.ok(longest < GEMINI_TIMEOUT_MAX_MS)
  assert.equal(geminiTimeoutMs(12 * 1024 * 1024), GEMINI_TIMEOUT_MAX_MS)
})
