// Dictation's server half, against a Gemini that is only a recording. No
// microphone, no audio that means anything, no key that spends anything and no
// network beyond loopback: the bytes are a few made-up ones, and what is
// proved is what this app sends Gemini, what it hands the page back, and that
// every way it can go wrong is a sentence and not a crash.

import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { once } from 'node:events'

import { createApp } from '../src/server.js'
import { readConfig } from './key-fixture.js'
import { TRANSCRIBE_MAX_BYTES, TRANSCRIBE_MODEL, VOCABULARY_MAX_TERMS, cleanVocabulary, geminiType } from '../../plugins/_listed/dictation/server/transcribe.js'
import { dictationApp } from './audio-servers.mjs'
import { shut, logInto } from './harness.js'

const KEY = 'test-gemini-key-not-a-real-one'

// What a completed interaction looks like, per the Interactions API: the
// model's words in a `model_output` step. The `user_input` step is here
// because the API may hand the input back, and it must not be read as words.
const said = (text) => ({
  id: 'interactions/fake',
  status: 'completed',
  steps: [
    { type: 'user_input', content: [{ type: 'audio', mime_type: 'audio/webm' }] },
    { type: 'model_output', content: [{ type: 'text', text }] },
  ],
})

async function fakeGemini(answer = () => [200, said('Hello Ada, the deploy is done.')]) {
  const seen = []
  const server = http.createServer(async (req, res) => {
    const chunks = []
    for await (const c of req) chunks.push(c)
    const raw = Buffer.concat(chunks).toString('utf8')
    const call = { method: req.method, path: req.url, key: req.headers['x-goog-api-key'], body: JSON.parse(raw || 'null') }
    seen.push(call)
    const [status, body] = answer(call, seen.length)
    res.writeHead(status, { 'content-type': 'application/json' })
    res.end(JSON.stringify(body))
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  return { server, seen, url: `http://127.0.0.1:${server.address().port}` }
}

// Dictation's own server (plugins/_listed/dictation/server/), on a port of
// its own: the gateway takes `/x/dictation` off, so the take goes to
// `/transcribe`.
async function appWith(env) {
  return dictationApp(env)
}

// Loam's own app server, for what is the app's and not the plugin's.
async function coreWith(env) {
  const lines = []
  const { server } = createApp({ config: readConfig(env), log: logInto(lines) })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  return { server, lines, url: `http://127.0.0.1:${server.address().port}` }
}

async function rig(t, answer) {
  const gemini = await fakeGemini(answer)
  const app = await appWith({ GEMINI_API_KEY: KEY, GEMINI_API_URL: gemini.url })
  t.after(async () => {
    await shut(app.server)
    await shut(gemini.server)
  })
  return { gemini, app }
}

const AUDIO = Buffer.from([0x1a, 0x45, 0xdf, 0xa3, 1, 2, 3, 4, 5, 6])

function post(app, { body = AUDIO, type = 'audio/webm;codecs=opus', vocabulary } = {}) {
  const headers = { 'content-type': type }
  if (vocabulary !== undefined) {
    headers['x-vocabulary'] = typeof vocabulary === 'string' ? vocabulary : encodeURIComponent(JSON.stringify(vocabulary))
  }
  return fetch(`${app.url}/transcribe`, { method: 'POST', headers, body })
}

test('a take goes to Gemini inline, in smart mode, with the terms it is biased towards', async (t) => {
  const { gemini, app } = await rig(t)
  const res = await post(app, { vocabulary: ['Ada', 'Loam', '  Ada ', 42, ''] })
  assert.equal(res.status, 200)
  const out = await res.json()
  assert.deepEqual(out, { text: 'Hello Ada, the deploy is done.', biased: true, model: TRANSCRIBE_MODEL })

  assert.equal(gemini.seen.length, 1)
  const [call] = gemini.seen
  assert.equal(call.method, 'POST')
  assert.equal(call.path, '/v1beta/interactions')
  assert.equal(call.key, KEY, 'the key went out in the header Gemini reads')
  assert.equal(call.body.model, 'gemini-3.5-transcribe')
  assert.deepEqual(call.body.input, [{ type: 'audio', mime_type: 'audio/webm', data: AUDIO.toString('base64') }])
  // Smart mode is the cleanup, and it is the only instruction: no prompt, no
  // system instruction, nothing that would licence a rewrite.
  assert.deepEqual(call.body.generation_config, {
    transcription_config: { mode: 'smart', custom_vocabulary: ['Ada', 'Loam'] },
  })
  assert.equal('system_instruction' in call.body, false)
})

test('the key never reaches the browser', async (t) => {
  const { app } = await rig(t)
  const res = await post(app)
  const text = await res.text()
  assert.doesNotMatch(text, new RegExp(KEY))
  for (const [name, value] of res.headers) assert.doesNotMatch(String(value), new RegExp(KEY), `header ${name}`)
})

test('no terms is no custom_vocabulary key at all, and a malformed header is no terms', async (t) => {
  const { gemini, app } = await rig(t)
  assert.equal((await post(app)).status, 200)
  assert.equal((await post(app, { vocabulary: '%E0%A4%A' })).status, 200)
  assert.equal((await post(app, { vocabulary: encodeURIComponent('{"not":"a list"}') })).status, 200)
  for (const call of gemini.seen) {
    assert.deepEqual(call.body.generation_config.transcription_config, { mode: 'smart' })
  }
  const res = await post(app)
  assert.equal((await res.json()).biased, false)
})

test('should Gemini refuse the terms beside smart mode, the cleanup is kept and the take sent again', async (t) => {
  const { gemini, app } = await rig(t, (call) =>
    call.body.generation_config.transcription_config.custom_vocabulary
      ? [400, { error: { code: 400, message: 'custom_vocabulary is not supported with mode smart' } }]
      : [200, said('Plain words.')],
  )
  const res = await post(app, { vocabulary: ['Ada'] })
  assert.equal(res.status, 200)
  assert.deepEqual(await res.json(), { text: 'Plain words.', biased: false, model: TRANSCRIBE_MODEL })
  assert.equal(gemini.seen.length, 2)
  assert.deepEqual(gemini.seen[1].body.generation_config.transcription_config, { mode: 'smart' })
})

// What the page sends since 2026-09-30: a form, the audio and the terms side
// by side in the body, so a whole note's worth of terms fits where the header's
// 6000 bytes held about 380.
function postForm(app, { audio = AUDIO, type = 'audio/webm;codecs=opus', terms, fields = null } = {}) {
  const form = new FormData()
  if (audio !== null) form.append('audio', new Blob([audio], { type }), 'take')
  if (terms !== undefined) form.append('terms', typeof terms === 'string' ? terms : JSON.stringify(terms))
  for (const [k, v] of Object.entries(fields ?? {})) form.append(k, v)
  return fetch(`${app.url}/transcribe`, { method: 'POST', body: form })
}

test('the terms can ride in the body: a form with the audio and a list, all thousand of them', async (t) => {
  const { gemini, app } = await rig(t)
  const thousand = Array.from({ length: VOCABULARY_MAX_TERMS }, (_, i) => `Name${String(i).padStart(4, '0')}`)
  // Far past what the header could carry.
  assert.ok(encodeURIComponent(JSON.stringify(thousand)).length > 6000)
  const res = await postForm(app, { terms: ['Ada', 'Grace', 'Hopper', ' Ada ', 7, ...thousand] })
  assert.equal(res.status, 200)
  assert.deepEqual(await res.json(), { text: 'Hello Ada, the deploy is done.', biased: true, model: TRANSCRIBE_MODEL })

  assert.equal(gemini.seen.length, 1)
  const { body } = gemini.seen[0]
  assert.deepEqual(body.input, [{ type: 'audio', mime_type: 'audio/webm', data: AUDIO.toString('base64') }])
  const sent = body.generation_config.transcription_config
  assert.equal(sent.mode, 'smart')
  assert.deepEqual(sent.custom_vocabulary.slice(0, 4), ['Ada', 'Grace', 'Hopper', 'Name0000'])
  assert.equal(sent.custom_vocabulary.length, VOCABULARY_MAX_TERMS)
  const line = app.lines.find((l) => l.event === 'transcribe')
  assert.equal(line.terms, VOCABULARY_MAX_TERMS)
  assert.equal(line.mime, 'audio/webm;codecs=opus', 'the log named the envelope rather than the audio')
  assert.doesNotMatch(JSON.stringify(app.lines), /Hopper/, 'a term reached the log')
})

test('a form with no audio part is a 400, and terms that are not a list are no terms', async (t) => {
  const { gemini, app } = await rig(t)
  let res = await postForm(app, { audio: null, terms: ['Ada'] })
  assert.equal(res.status, 400)
  assert.match((await res.json()).error, /no audio/)
  res = await postForm(app, { audio: null, fields: { audio: 'not a file' } })
  assert.equal(res.status, 400)
  assert.equal(gemini.seen.length, 0)

  for (const terms of ['{"not":"a list"}', 'not json', undefined]) {
    res = await postForm(app, { terms })
    assert.equal(res.status, 200, String(terms))
  }
  for (const call of gemini.seen) assert.deepEqual(call.body.generation_config.transcription_config, { mode: 'smart' })
})

test('a form keeps the audio’s own refusals: over the cap is 413, a video is 415', async (t) => {
  const { gemini, app } = await rig(t)
  let res = await postForm(app, { audio: Buffer.alloc(TRANSCRIBE_MAX_BYTES + 1), terms: ['Ada'] })
  assert.equal(res.status, 413)
  assert.match((await res.json()).error, /over 12 MB/)
  res = await postForm(app, { type: 'video/webm' })
  assert.equal(res.status, 415)
  res = await postForm(app, { audio: Buffer.alloc(TRANSCRIBE_MAX_BYTES), terms: ['Ada'] })
  assert.equal(res.status, 200, 'a take at the cap with its terms beside it was refused')
  assert.equal(gemini.seen.length, 1)
})

test('with no key, the page is told so and nothing is sent', async (t) => {
  const gemini = await fakeGemini()
  const app = await appWith({ GEMINI_API_URL: gemini.url })
  t.after(async () => {
    await shut(app.server)
    await shut(gemini.server)
  })
  const res = await post(app)
  assert.equal(res.status, 503)
  assert.match((await res.json()).error, /Gemini API key. Add a Google key under API keys, in Settings › Connections./)
  assert.equal(gemini.seen.length, 0)
})

test('an empty take, a take over the cap and a type Gemini cannot read are each a sentence, and none is sent', async (t) => {
  const { gemini, app } = await rig(t)

  let res = await post(app, { body: Buffer.alloc(0) })
  assert.equal(res.status, 400)
  assert.match((await res.json()).error, /empty/)

  res = await post(app, { body: Buffer.alloc(TRANSCRIBE_MAX_BYTES + 1) })
  assert.equal(res.status, 413)
  assert.match((await res.json()).error, /over 12 MB/)

  res = await post(app, { type: 'video/webm' })
  assert.equal(res.status, 415)
  assert.match((await res.json()).error, /video\/webm/)

  assert.equal(gemini.seen.length, 0)
})

test('a take exactly at the cap is taken', async (t) => {
  const { gemini, app } = await rig(t)
  const res = await post(app, { body: Buffer.alloc(TRANSCRIBE_MAX_BYTES) })
  assert.equal(res.status, 200)
  assert.equal(gemini.seen.length, 1)
})

test('Gemini refusing, failing, hearing nothing or being gone is a sentence, not a crash', async (t) => {
  const answers = [
    [429, { error: { code: 429, message: 'Resource has been exhausted' } }],
    [500, { error: { code: 500, message: 'Internal error' } }],
    [200, { status: 'failed', steps: [] }],
    [200, said('   ')],
  ]
  const { app } = await rig(t, (call, n) => answers[n - 1])

  let res = await post(app)
  assert.equal(res.status, 429)
  assert.match((await res.json()).error, /Resource has been exhausted/)

  res = await post(app)
  assert.equal(res.status, 502)
  assert.match((await res.json()).error, /\(500\): Internal error/)

  res = await post(app)
  assert.equal(res.status, 502)
  assert.match((await res.json()).error, /did not finish/)

  res = await post(app)
  assert.equal(res.status, 422)
  assert.match((await res.json()).error, /no words/)

  // And the app still serves after all four.
  assert.equal((await fetch(`${app.url}/up`)).status, 200)
})

test('a Gemini that cannot be reached is a 502 with a sentence', async (t) => {
  const gemini = await fakeGemini()
  const gone = gemini.url
  await shut(gemini.server)
  const app = await appWith({ GEMINI_API_KEY: KEY, GEMINI_API_URL: gone })
  t.after(() => shut(app.server))
  const res = await post(app)
  assert.equal(res.status, 502)
  assert.match((await res.json()).error, /could not be reached/)
})

test('the recorder’s container is named the way Gemini names it', () => {
  assert.equal(geminiType('audio/webm;codecs=opus'), 'audio/webm')
  assert.equal(geminiType('audio/ogg; codecs=opus'), 'audio/ogg')
  assert.equal(geminiType('audio/mp4'), 'audio/m4a')
  assert.equal(geminiType('AUDIO/WEBM'), 'audio/webm')
  assert.equal(geminiType('video/webm'), null)
  assert.equal(geminiType(undefined), null)
})

test('the terms are capped, deduplicated and kept to names', () => {
  const many = Array.from({ length: 1200 }, (_, i) => `term${i}`)
  assert.equal(cleanVocabulary(many).length, VOCABULARY_MAX_TERMS)
  assert.deepEqual(cleanVocabulary(['Ada', 'ada', 'x'.repeat(81), 'Two  words']), ['Ada', 'Two words'])
  assert.deepEqual(cleanVocabulary('Ada'), [])
})

// ------------------------------------------------ the retired doors
//
// From 2026-09-28 to 10-01 the Obsidian plugin dictated through a token door
// here, `POST /api/dictate`, with its settings at `GET /api/dictation-setup`;
// and until 0.2.12 the app itself transcribed at `/api/transcribe`. Each is a
// route the app does not know now, whatever the environment says: dictation
// is the Dictation plugin's own server's (test/audio-routes-gone.test.js).

test('the retired doors: /api/dictate, /api/dictation-setup and /api/transcribe are unknown to the app', async (t) => {
  const gemini = await fakeGemini()
  const app = await coreWith({ GEMINI_API_KEY: KEY, GEMINI_API_URL: gemini.url, DICTATION_TOKEN: 'a-token-nobody-reads' })
  t.after(async () => {
    await shut(app.server)
    await shut(gemini.server)
  })
  const asked = [
    ['POST', '/api/dictate', { authorization: 'Bearer a-token-nobody-reads', 'content-type': 'audio/webm' }, AUDIO],
    ['POST', '/api/dictate', { 'content-type': 'audio/webm' }, AUDIO],
    ['GET', '/api/dictate', {}, undefined],
    ['GET', '/api/dictation-setup', {}, undefined],
    ['POST', '/api/transcribe', { 'content-type': 'audio/webm' }, AUDIO],
  ]
  for (const [method, path, headers, body] of asked) {
    const res = await fetch(`${app.url}${path}`, { method, headers, body })
    assert.equal(res.status, 404, `${method} ${path}`)
    assert.deepEqual(await res.json(), { error: `no such route: ${method} ${path}` })
  }
  assert.equal(gemini.seen.length, 0, 'a take went to Gemini through the app')
  assert.doesNotMatch(JSON.stringify(app.lines), /a-token-nobody-reads/)
})

test('a path with a dot segment is refused before it is normalised into another route', async (t) => {
  const app = await coreWith({})
  t.after(() => shut(app.server))
  const { port } = new URL(app.url)
  // `fetch` normalises a path before it sends it; a raw request does not,
  // which is what a request that has come past a proxy may be.
  for (const path of ['/api/transcribe/../health', '/api/transcribe/%2e%2e/health', '/api/x/./../health', '/./up']) {
    for (const method of ['GET', 'POST']) {
      const status = await new Promise((resolve, reject) => {
        const req = http.request({ host: '127.0.0.1', port, method, path }, (res) => {
          res.resume()
          res.on('end', () => resolve(res.statusCode))
        })
        req.on('error', reject)
        req.end()
      })
      assert.equal(status, 400, `${method} ${path}`)
    }
  }
})
