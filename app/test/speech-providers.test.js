import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createTranscriber } from '../../plugins/_listed/dictation/server/transcribe.js'
import { readConfig } from '../../plugins/_listed/read-aloud/server/config.js'
import { speechProviders } from '../../plugins/_listed/read-aloud/server/providers.js'
import { narrate } from '../../plugins/_listed/read-aloud/server/openai-speech.js'
import { openAIVoices } from '../../plugins/_listed/read-aloud/page/openai-voices.js'
import { PREBUILT_VOICES } from '../../plugins/_listed/read-aloud/page/document-speech.js'
import { voiceGender } from '../../plugins/_listed/read-aloud/page/voices.js'
import { BrowserAudio } from '../../plugins/_listed/read-aloud/page/browser-audio.js'

test('one recording stays with its selected provider, including polish and refusals', async () => {
  let google = 'example-google', openai = 'example-openai', refuse = false
  const requests = []
  const t = createTranscriber({}, { getKey: async () => google, getOpenAIKey: async () => openai,
    fetchImpl: async (url, init) => {
      requests.push(url)
      assert.equal(init.headers.authorization, 'Bearer example-openai')
      if (refuse) return new Response('', { status: 429 })
      if (url.endsWith('/transcriptions')) {
        assert.deepEqual(init.body.getAll('keywords[]'), ['garden'])
        assert.equal(init.body.get('model'), 'gpt-transcribe')
        return Response.json({ text: 'the garden grows' })
      }
      const body = JSON.parse(init.body)
      assert.match(body.input, /garden/)
      assert.equal(body.store, false)
      return Response.json({ status: 'completed', output: [{ content: [{ type: 'output_text', text: 'The garden grows.' }] }] })
    } })
  const take = { audio: Buffer.from('example'), contentType: 'audio/webm', vocabulary: ['garden','<term>'], polish: 'light', expectedProvider: 'openai' }
  assert.equal(await t.provider(), 'google')
  assert.equal((await t.transcribe(take)).status, 409)
  google = ''
  assert.equal(await t.provider(), 'openai')
  assert.equal((await t.transcribe(take)).body.text, 'The garden grows.')
  refuse = true
  const stopped = await t.transcribe(take)
  assert.equal(stopped.status, 429); assert.match(stopped.body.error, /OpenAI/)
  assert.equal(requests.length, 3)
  openai = ''
  assert.equal(await t.provider(), 'browser')
})

test('Read aloud rechecks declared keys and refuses a stale selection before synthesis', async t => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'speech-route-'))
  t.after(() => rm(dir, { recursive: true, force: true }))
  const file = path.join(dir, 'keys.env')
  const service = speechProviders(readConfig({ LOAM_PLUGIN_KEYS: file, SPEECH_DIR: dir }), { encoder: { probe: async () => true } })
  t.after(() => service.stop())
  assert.equal((await service.status()).provider, 'browser')
  await writeFile(file, 'OPENAI_API_KEY=test\n')
  assert.equal((await service.status()).provider, 'openai')
  await writeFile(file, 'GEMINI_API_KEY=test\nOPENAI_API_KEY=test\n')
  assert.equal((await service.status()).provider, 'google')
  assert.equal((await service.request({ provider: 'openai' })).status, 409)
})

test('OpenAI narration maps every profile voice, collects PCM, and names a refused request', async () => {
  assert.deepEqual(Object.keys(openAIVoices).sort(), [...PREBUILT_VOICES].sort())
  const families = new Map()
  for (const [profile, mapped] of Object.entries(openAIVoices)) {
    const gender = voiceGender(profile)
    if (families.has(mapped)) assert.equal(families.get(mapped), gender, mapped + ' crosses profile voice groups')
    families.set(mapped, gender)
  }
  let socket
  class Socket extends EventTarget {
    constructor(url, options) { super(); socket = this; assert.match(url, /api\.openai\.com/); assert.equal(options.headers.authorization, 'Bearer test') }
    send(raw) { this.sent = JSON.parse(raw) }
    close() {}
    receive(data) { this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(data) })) }
  }
  const request = { key: 'test', voice: 'Kore', style: 'gentle', chunk: 'The garden grows.', signal: new AbortController().signal, Socket }
  const playing = narrate(request)
  socket.receive({ type: 'session.created' })
  assert.equal(socket.sent.response.audio.output.voice, openAIVoices.Kore)
  assert.equal(socket.sent.response.input[0].content[0].text, request.chunk)
  socket.receive({ type: 'response.output_audio.delta', delta: 'AQI=' })
  socket.receive({ type: 'response.done', response: { status: 'completed' } })
  assert.deepEqual(await playing, { pcm: Buffer.from([1,2]), rate: 24000 })
  const denied = narrate(request)
  socket.receive({ type: 'error', error: { code: 'rate_limit_exceeded' } })
  await assert.rejects(denied, /OpenAI.*rate_limit/)
})

test('browser Listen and Files use the fixed skip notices and release the native queue', () => {
  const previous = globalThis.SpeechSynthesisUtterance
  globalThis.SpeechSynthesisUtterance = class { constructor(text) { this.text = text } }
  try {
    const spoken = []
    const synth = { speak(u) { spoken.push(u) }, cancel() {}, resume() {}, pause() {} }
    const audio = new BrowserAudio({ request: { source: 'document' }, text: '---\nid: private\n---\nThe garden.\n\n| Place | Name |\n| --- | --- |\n| Garden | Rose |' }, synth)
    audio.play(); audio.pause(); assert.equal(audio.paused, true)
    audio.play()
    while (!audio.ended) spoken.at(-1).onend()
    const text = spoken.map(u => u.text).join(' ')
    assert.match(text, /table.*skipped/i); assert.doesNotMatch(text, /private|Rose/)
    audio.play(); audio.release(); assert.equal(audio.utterance, null)
  } finally { globalThis.SpeechSynthesisUtterance = previous }
})
