// Speech's server half, against a Gemini that is only a recording. The TTS
// model answers a short, real WAV; the brief's writer answers words; nothing
// leaves loopback and no key spends anything. The encoder is a stand-in that
// writes the joined samples under a marker, so every route can be driven on a
// box without `ffmpeg` — and the tests at the end that need the real one run
// it when it is there and say by name why they skipped when it is not.

import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { once } from 'node:events'
import { fileURLToPath } from 'node:url'
import { createHash } from 'node:crypto'

import { readAloudConfig as readConfig } from './key-fixture.js'
import { readAloudApp } from './audio-servers.mjs'
import { speakable, numberWords } from '../../plugins/_listed/read-aloud/page/speakable.js'
import { documentSpeakable, documentSpeechBlocks } from '../../plugins/_listed/read-aloud/page/document-speech.js'
import { spokenWords } from '../../plugins/_listed/read-aloud/server/spoken-length.js'
import {
  createSpeech, createFfmpegEncoder, chunkText, cleanBrief, parseVoices, pcmOfWav, joinPcm, PREBUILT_VOICES,
  durationCheck, parsePaces, chunkBlocks, CLEAN_SPEECH_ERROR, briefInstruction, documentBriefInstruction, speechId,
} from '../../plugins/_listed/read-aloud/server/speech.js'
import { fakeBroker, shut, logInto, until } from './harness.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const KEY = 'test-gemini-key-not-a-real-one'
const FIXTURE = fs.readFileSync(path.join(HERE, 'fixtures', 'speech-reply.md'), 'utf8')
// A 400-word reply whose speech text is 2,284 characters, the size of the
// first real one voiced on the deployed box: one chunk at the old 2,500.
const FOUR_HUNDRED_WORDS = fs.readFileSync(path.join(HERE, 'fixtures', 'speech-400-words.md'), 'utf8')
const SUMMARY_DOCUMENT = fs.readFileSync(path.join(HERE, 'fixtures', 'summary-document.md'), 'utf8')
const SUMMARY_CHAT = fs.readFileSync(path.join(HERE, 'fixtures', 'summary-chat.md'), 'utf8')
const SHAPE_EVIDENCE = new URL('../docs/evidence/summary-shapes/', import.meta.url)
const DOCUMENT_SUMMARY = fs.readFileSync(new URL('document-summary.txt', SHAPE_EVIDENCE), 'utf8').trim()
const CHAT_BRIEF = fs.readFileSync(new URL('chat-brief.txt', SHAPE_EVIDENCE), 'utf8').trim()
const VOICES = 'scout=Iapetus:clear and direct;quill=Vindemiatrix:gentle and reflective'

const document = (over = {}) => ({ source: 'document', title: 'A draft', mode: 'verbatim', minutes: 'full', text: '# A short note\n\nPlease decide today.', ...over })

// ------------------------------------------------------------------ fakes

// A WAV as the TTS model answers one: RIFF, 24 kHz mono 16-bit PCM.
// voicedText sizes the tone to the transcript's words at the reference pace.
function wav(ms = 100, rate = 24000) {
  const samples = Math.round((rate * ms) / 1000)
  const pcm = Buffer.alloc(samples * 2)
  for (let i = 0; i < samples; i++) pcm.writeInt16LE(Math.round(Math.sin((2 * Math.PI * 220 * i) / rate) * 8000), i * 2)
  const head = Buffer.alloc(44)
  head.write('RIFF', 0, 'ascii')
  head.writeUInt32LE(36 + pcm.length, 4)
  head.write('WAVE', 8, 'ascii')
  head.write('fmt ', 12, 'ascii')
  head.writeUInt32LE(16, 16)
  head.writeUInt16LE(1, 20)
  head.writeUInt16LE(1, 22)
  head.writeUInt32LE(rate, 24)
  head.writeUInt32LE(rate * 2, 28)
  head.writeUInt16LE(2, 32)
  head.writeUInt16LE(16, 34)
  head.write('data', 36, 'ascii')
  head.writeUInt32LE(pcm.length, 40)
  return Buffer.concat([head, pcm])
}

const voiced = (ms) => [200, {
  status: 'completed',
  steps: [{ type: 'model_output', content: [{ type: 'audio', mime_type: 'audio/wav', data: wav(ms).toString('base64') }] }],
}]
const secondsFor = (text) => (text.match(/\S+/g)?.length ?? 0) * 60 / 150
const voicedText = (call, ratio = 1) => voiced(secondsFor(call.body.input[0].content[0].text) * ratio * 1000)
const wrote = (text) => [200, {
  status: 'completed',
  steps: [
    { type: 'user_input', content: [{ type: 'text', text: 'the input, handed back' }] },
    { type: 'model_output', content: [{ type: 'text', text }] },
  ],
}]

// Gemini: the models list, and the interactions route answering by model.
// `tts` and `brief` decide each answer; `hold` keeps a kind of call waiting
// until a test lets it go, which is how a stage is caught in the act.
async function fakeGemini({ tts = (call) => voicedText(call), brief = () => wrote('I finished the deploy. Nothing needs you.'), models = [] } = {}) {
  const seen = []
  const gates = { tts: null, brief: null }
  const server = http.createServer(async (req, res) => {
    const chunks = []
    for await (const c of req) chunks.push(c)
    const raw = Buffer.concat(chunks).toString('utf8')
    if (req.method === 'GET' && req.url.startsWith('/v1beta/models')) {
      seen.push({ kind: 'models', key: req.headers['x-goog-api-key'] })
      res.writeHead(200, { 'content-type': 'application/json' })
      return res.end(JSON.stringify({ models: models.map((name) => ({ name: `models/${name}` })) }))
    }
    const body = JSON.parse(raw)
    const kind = /tts/.test(body.model) ? 'tts' : 'brief'
    const call = { kind, key: req.headers['x-goog-api-key'], body }
    seen.push(call)
    await gates[kind]?.promise
    const n = seen.filter((c) => c.kind === kind).length
    const [status, answer, headers = {}] = await (kind === 'tts' ? tts : brief)(call, n)
    res.writeHead(status, { 'content-type': 'application/json', ...headers })
    res.end(JSON.stringify(answer))
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const hold = (kind) => {
    let open
    gates[kind] = { promise: new Promise((resolve) => (open = resolve)), open }
  }
  const release = (kind) => {
    gates[kind]?.open()
    gates[kind] = null
  }
  return { server, seen, hold, release, url: `http://127.0.0.1:${server.address().port}` }
}

// The encoder stand-in: the samples it was given, under a marker, so a test
// can read back exactly what was joined. `hold()` keeps it at the encode.
function fakeEncoder() {
  const encoder = {
    name: 'fake-encoder',
    calls: [],
    gate: null,
    async probe() {
      return true
    },
    async encode({ pcm, rate, outPath }) {
      encoder.calls.push({ bytes: pcm.length, rate })
      await encoder.gate?.promise
      await fsp.writeFile(outPath, Buffer.concat([Buffer.from('FAKEMP3:'), pcm]))
    },
    hold() {
      let open
      encoder.gate = { promise: new Promise((resolve) => (open = resolve)), open }
    },
    release() {
      encoder.gate?.open()
      encoder.gate = null
    },
  }
  return encoder
}

async function tmpDir(t) {
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), 'loam-speech-'))
  t.after(() => fsp.rm(dir, { recursive: true, force: true }))
  return dir
}

// The app, with a fake Gemini and a speech directory of the test's own.
async function rig(t, { env = {}, gemini: geminiOpts, encoder = fakeEncoder(), withKey = true, descriptionTimeoutMs, descriptionBudgetSignal } = {}) {
  const gemini = await fakeGemini(geminiOpts)
  const own = await fsp.mkdtemp(path.join(os.tmpdir(), 'loam-speech-'))
  const dir = env.SPEECH_DIR ?? path.join(own, 'speech')
  const config = readConfig({
    ...(withKey ? { GEMINI_API_KEY: KEY } : {}),
    GEMINI_API_URL: gemini.url,
    SPEECH_DIR: dir,
    SPEECH_VOICES: VOICES,
    // The server's own address, as the gateway hands it requests: `/`.
    LOAM_PLUGIN_BASE: '/',
    ...env,
  })
  const lines = []
  const log = logInto(lines)
  const speech = createSpeech(config, { log, encoder, retryBaseMs: 5, descriptionTimeoutMs, descriptionBudgetSignal })
  const { server } = await readAloudApp({ config, speech, log })
  // Every job let go of before the directory goes, so a take still being
  // written cannot race the removal.
  t.after(async () => {
    gemini.release('tts')
    gemini.release('brief')
    encoder.release?.()
    await speech.stop()
    await shut(server)
    await shut(gemini.server)
    await fsp.rm(own, { recursive: true, force: true })
  })
  return { gemini, encoder, speech, lines, dir, url: `http://127.0.0.1:${server.address().port}` }
}

const ask = (app, body) =>
  fetch(`${app.url}/speech`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })

const reply = (over = {}) => ({ chat_id: 'chat-1', agent: 'scout', mode: 'verbatim', title: 'Deploy', text: FIXTURE, describe_tables_and_diagrams: false, ...over })

async function poll(app, id) {
  const res = await fetch(`${app.url}/speech/${id}`)
  return { status: res.status, body: await res.json() }
}

// Until the id is no longer preparing; its last answer.
async function settled(app, id) {
  let last = null
  const ask = async () => {
    last = await poll(app, id)
    return last.body.status !== 'preparing'
  }
  const deadline = Date.now() + 8000
  while (!(await ask())) {
    if (Date.now() > deadline) throw new Error(`still preparing: ${JSON.stringify(last.body)}`)
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  return last
}

// ------------------------------------------------------------- the transform

test('the transform says the fixture as prose: headings, emphasis, links, URLs, lists, quotes, emoji, and skips the table and the code', () => {
  assert.equal(
    speakable(FIXTURE),
    [
      'Deploy status.',
      'The deploy is done and on time, see the plan, Speech plan Voices and the PR. Logs are at a link. Run npm_test --watch first; snake_case_name stays as it is.',
      'Next steps.',
      'Merge the job/speech branch. Ask the owner about code, then wait. Write the receipt.',
      'Build the image. Deploy it.',
      'Quoted wisdom here. Still quoted.',
      'A table of four rows, skipped.',
      'A code block of twelve lines, skipped.',
      'Setext Title.',
      'All done.',
    ].join('\n\n'),
  )
})

test('what reaches the voice has nothing in it that looks like markup', () => {
  const said = speakable(FIXTURE)
  assert.doesNotMatch(said, /[*`#|>[\]_]{2}|\]\(|https?:|\p{Extended_Pictographic}/u)
  assert.doesNotMatch(said, /^\s*[-*+]\s/m)
})

test('a code block or a table of one is said in the singular, and counts are words', () => {
  assert.equal(speakable('```\nx\n```'), 'A code block of one line, skipped.')
  assert.equal(speakable('a | b\n--|--\n1 | 2'), 'A table of one row, skipped.')
  assert.equal(numberWords(42), 'forty-two')
  assert.equal(numberWords(301), 'three hundred and one')
  assert.equal(numberWords(4000), '4000')
})

test('a horizontal rule is a pause, and an unclosed fence still counts its lines', () => {
  assert.equal(speakable('One.\n\n***\n\nTwo.'), 'One.\n\nTwo.')
  assert.equal(speakable('Before.\n```\na\nb'), 'Before.\n\nA code block of two lines, skipped.')
})

test('chunks: paragraphs packed up to the size, never split inside a sentence', () => {
  const sentence = (n) => `Sentence number ${n} is here.`
  const long = Array.from({ length: 40 }, (_, i) => sentence(i)).join(' ')
  const text = `Short opener.\n\n${long}\n\nShort closer.`
  const chunks = chunkText(text, 200)
  for (const c of chunks) assert.ok(c.length <= 200, `chunk of ${c.length}`)
  for (const c of chunks) assert.match(c, /\.$/, 'every chunk ends at a sentence end')
  assert.equal(chunks.join(' ').replace(/\s+/g, ' '), text.replace(/\s+/g, ' '), 'nothing lost or reordered')
  assert.deepEqual(chunkText('A.\n\nB.', 2500), ['A.\n\nB.'])
})

test('chunks are about a thousand characters by default: a short reply is one, a long paragraph splits at its sentences', () => {
  assert.equal(readConfig({}).speech.chunkChars, 1000)
  const short = 'The deploy is done.\n\nNothing needs you.'
  assert.deepEqual(chunkText(short, 1000), [short])

  const sentence = (n) => `This is sentence ${String(n).padStart(2, '0')} of a paragraph that runs long.`
  const paragraph = Array.from({ length: 30 }, (_, i) => sentence(i)).join(' ')
  assert.ok(paragraph.length > 1000)
  const chunks = chunkText(paragraph, 1000)
  assert.equal(chunks.length, 2)
  for (const c of chunks) assert.match(c, /^This is sentence \d\d .*runs long\.$/, 'each chunk starts and ends at a sentence')
  assert.equal(chunks.join(' '), paragraph, 'nothing lost or reordered')
})

test('the 2,284-character reply: one chunk at 2,500, three even ones at 1,000', () => {
  const spoken = speakable(FOUR_HUNDRED_WORDS)
  assert.equal(spoken.length, 2284)
  assert.equal(chunkText(spoken, 2500).length, 1)
  const chunks = chunkText(spoken, 1000)
  assert.deepEqual(chunks.map((c) => c.length), [799, 745, 737], 'as even as the sentences allow, not 941, 603 and 737')
  for (const c of chunks) assert.match(c, /[.!?]$/)
  assert.equal(chunks.join(' ').replace(/\s+/g, ' '), spoken.replace(/\s+/g, ' '))
})

test('at the default size the 2,284-character reply voices as three calls at once, and progress moves as each lands', async (t) => {
  const answers = []
  const app = await rig(t, {
    gemini: { tts: (call) => new Promise((resolve) => answers.push(() => resolve(voicedText(call)))) },
  })
  app.encoder.hold()
  const { id } = await (await ask(app, reply({ text: FOUR_HUNDRED_WORDS }))).json()
  const progressNow = async () => (await poll(app, id)).body
  await until(() => answers.length === 3)
  assert.deepEqual(await progressNow(), { id, status: 'preparing', stage: 'voicing', progress: 0 })
  for (const expected of [0.333, 0.667]) {
    answers.shift()()
    let body
    for (let i = 0; i < 400 && (body = await progressNow()).progress !== expected; i++) await new Promise((r) => setTimeout(r, 10))
    assert.deepEqual(body, { id, status: 'preparing', stage: 'voicing', progress: expected })
  }
  answers.shift()()
  await until(() => app.encoder.calls.length === 1)
  assert.deepEqual(await progressNow(), { id, status: 'preparing', stage: 'encoding', progress: 0.95 })
  app.encoder.release()
  assert.equal((await settled(app, id)).body.status, 'ready')
  assert.equal(app.gemini.seen.filter((c) => c.kind === 'tts').length, 3)
})

test('a brief that leaks markup or runs long is cleaned and cut at a sentence', () => {
  assert.equal(cleanBrief('## Summary\n\n- **One** thing.\n- Two', 300), 'Summary.\n\nOne thing. Two.')
  const long = Array.from({ length: 80 }, () => 'This sentence has five words.').join(' ')
  const cut = cleanBrief(long, 300)
  assert.ok(cut.split(/\s+/).length <= 300)
  assert.match(cut, /words\.$/)
})

test('voices: every configured one is a prebuilt voice, and a name that is not one is dropped', () => {
  const brief = 'scout=Iapetus:clear and direct;quill=Vindemiatrix:gentle and reflective;ember=Achird:warm and friendly;' +
    'fern=Algieba:smooth and measured;moss=Schedar:even and calm;sage=Rasalgethi:informative and precise;wren=Pulcherrima:bright and forward'
  const { voices, dropped } = parseVoices(`${brief};odd=Nobody:quiet`)
  assert.equal(voices.size, 7)
  for (const v of voices.values()) assert.ok(PREBUILT_VOICES.has(v.voice))
  assert.ok(PREBUILT_VOICES.has('Charon'))
  assert.deepEqual(dropped, ['odd=Nobody:quiet'])
  assert.deepEqual(voices.get('scout'), { voice: 'Iapetus', character: 'clear and direct' })
})

test('a WAV is read to its samples, and chunks join end to end with a pause between', () => {
  const a = pcmOfWav(wav(100))
  assert.equal(a.rate, 24000)
  assert.equal(a.pcm.length, 2400 * 2)
  const joined = joinPcm([a, a])
  assert.equal(joined.pcm.length, 2 * 4800 + 8400 * 2)
  assert.equal(joined.duration_s, 0.6)
  assert.throws(() => pcmOfWav(Buffer.from('not a wav at all')), /not a WAV/)
})

// ------------------------------------------------------------- the routes

test('verbatim: 202 while voicing, then 200 with the audio; the voice, the style and the plain text went to Gemini', async (t) => {
  const app = await rig(t)
  app.gemini.hold('tts')
  const first = await ask(app, reply())
  assert.equal(first.status, 202)
  const preparing = await first.json()
  assert.match(preparing.id, /^[0-9a-f]{32}$/)
  assert.deepEqual(preparing, { id: preparing.id, status: 'preparing', stage: 'voicing', progress: 0 })

  app.gemini.release('tts')
  const done = await settled(app, preparing.id)
  assert.equal(done.status, 200)
  assert.deepEqual(done.body, {
    id: preparing.id, status: 'ready', duration_s: secondsFor(speakable(FIXTURE)), url: `/speech/${preparing.id}/audio.mp3`, brief_text: null,
  })

  const tts = app.gemini.seen.filter((c) => c.kind === 'tts')
  assert.equal(tts.length, 1, 'the fixture is one chunk')
  assert.equal(tts[0].key, KEY)
  const sent = tts[0].body
  assert.equal(sent.model, 'gemini-3.8-flash-tts')
  assert.deepEqual(sent.generation_config, { speech_config: [{ voice: 'Iapetus' }] })
  assert.deepEqual(sent.response_format, { type: 'audio', mime_type: 'audio/wav', sample_rate: 24000 })
  const item = sent.input[0].content[0]
  assert.equal(item.text, speakable(FIXTURE))
  assert.equal(item.annotations[0].type, 'speech_metadata')
  assert.equal(item.annotations[0].style, 'clear and direct, natural conversational pace, even and clear')
  assert.doesNotMatch(item.text, /<[a-z ]+>/, 'no vocal tags in the text')

  // Asked again: already on disk, so 200 at once and no second call.
  const again = await ask(app, reply())
  assert.equal(again.status, 200)
  assert.deepEqual(await again.json(), done.body)
  assert.equal(app.gemini.seen.filter((c) => c.kind === 'tts').length, 1)
})

test('brief: written by the Flash model from the raw markdown, cleaned, voiced, and returned as brief_text', async (t) => {
  const app = await rig(t, {
    gemini: { brief: () => wrote('## Briefing\n\nThe tests pass. I need **your** call on the deploy, which is pending 🚀.') },
  })
  app.gemini.hold('brief')
  const res = await ask(app, reply({ mode: 'brief' }))
  assert.equal(res.status, 202)
  const { id, stage, progress } = await res.json()
  assert.equal(stage, 'writing')
  assert.equal(progress, 0)
  app.gemini.release('brief')

  const done = await settled(app, id)
  assert.equal(done.body.status, 'ready')
  assert.equal(done.body.brief_text, 'Briefing.\n\nThe tests pass. I need your call on the deploy, which is pending.')

  const writer = app.gemini.seen.find((c) => c.kind === 'brief').body
  assert.equal(writer.model, 'gemini-flash-latest')
  assert.equal(writer.store, false)
  assert.ok(writer.input.includes(FIXTURE.trim()), 'the writer reads the raw markdown, table and code included')
  assert.match(writer.input, /Agent: Scout/)
  assert.match(writer.input, /Chat title: Deploy/)
  for (const rule of [/first person/, /needs their attention or a decision/, /accurate/, /no headings, no lists/, /about 220 words and never more than 300/, /what the reply was about/]) {
    assert.match(writer.system_instruction, rule)
  }

  const tts = app.gemini.seen.find((c) => c.kind === 'tts').body.input[0].content[0]
  assert.equal(tts.text, done.body.brief_text, 'what is voiced is what the page is shown')
  assert.equal(tts.annotations[0].style, 'clear and direct, unhurried pace, even and clear')

  const sidecar = JSON.parse(await fsp.readFile(path.join(app.dir, `${id}.json`), 'utf8'))
  assert.equal(sidecar.brief_text, done.body.brief_text)
  assert.deepEqual(
    { agent: sidecar.agent, mode: sidecar.mode, chat_id: sidecar.chat_id, title: sidecar.title, duration_s: sidecar.duration_s },
    { agent: 'scout', mode: 'brief', chat_id: 'chat-1', title: 'Deploy', duration_s: secondsFor(done.body.brief_text) },
  )
  assert.ok(Date.parse(sidecar.created))
})

// The writer is a fixture, not a model evaluation: pin the actual instructions
// at the service boundary and prove cleanup/TTS preserve the reference shapes.
test('chat brief: summary first, attention last, with no empty attention section requested', async (t) => {
  const app = await rig(t, { gemini: { brief: (call) => {
    const instruction = call.body.system_instruction
    assert.equal(instruction, briefInstruction(300))
    assert.match(instruction, /summarise what was said before any attention items/)
    assert.match(instruction, /Close with anything that needs their attention or a decision/)
    assert.match(instruction, /Nothing follows these attention items/)
    assert.match(instruction, /If nothing needs their attention or a decision, end on the summary without an empty attention section/)
    assert.doesNotMatch(instruction, /lead with anything that needs/i)
    assert.ok(call.body.input.includes(SUMMARY_CHAT.trim()))
    return wrote(CHAT_BRIEF)
  } } })
  const { id } = await (await ask(app, reply({ mode: 'brief', text: SUMMARY_CHAT }))).json()
  const done = await settled(app, id)
  assert.equal(done.body.status, 'ready', JSON.stringify(done.body))
  assert.equal(done.body.brief_text, CHAT_BRIEF)
  const paragraphs = done.body.brief_text.split('\n\n')
  assert.match(paragraphs[0], /^I finished the Archive catalogue trial/)
  assert.match(paragraphs.at(-1), /^I need your decision by 2026-11-03 on expanding to Shelf C and including photographs/)
  assert.ok(paragraphs.slice(0, -1).every((p) => !p.includes('I need your decision')))
  const voicedTextJoined = app.gemini.seen.filter((c) => c.kind === 'tts').map((c) => c.body.input[0].content[0].text).join(' ')
  assert.equal(voicedTextJoined.replace(/\s+/g, ' '), CHAT_BRIEF.replace(/\s+/g, ' '))
})

test('document summary: whole document in source order, exact facts, no attention section, including after a length repair', async (t) => {
  const app = await rig(t, { gemini: {
    // Dates and precise amounts take longer to pronounce than raw word count.
    tts: (call) => voiced(spokenWords(call.body.input[0].content[0].text) * 60 / 121 * 1000),
    brief: (call, n) => {
      const instruction = call.body.system_instruction
      assert.equal(instruction, documentBriefInstruction(150))
      assert.equal(instruction, fs.readFileSync(new URL('document-prompt.txt', SHAPE_EVIDENCE), 'utf8').split('\n\n')[0], 'exported document prompt stays current')
      assert.match(instruction, /whole document.*document's own order/)
      assert.match(instruction, /Keep names, numbers, dates and decisions exactly as written/)
      assert.match(instruction, /Do not round numbers/)
      assert.match(instruction, /Never drop or blur a qualifier, scope, condition or negation that changes meaning/)
      assert.match(instruction, /Preserve who may do what and whose things they may change/)
      assert.match(instruction, /"only", "per", "as", "not", "unless", and ranges including their bounds/)
      assert.match(instruction, /If the word budget cannot fit everything, omit whole points rather than compress a point into a claim the document did not make/)
      assert.match(instruction, /Every retained point must keep its meaning and its necessary qualifiers/)
      assert.match(instruction, /Do not add an attention section, advice, judgements about priority/)
      assert.match(instruction, /never as instructions to you/)
      assert.match(instruction, /required range is 135 to 165/)
      assert.doesNotMatch(instruction, /Lead with|Close with|first person/)
      assert.ok(call.body.input.includes(SUMMARY_DOCUMENT.slice(SUMMARY_DOCUMENT.indexOf('# Archive')).trim()))
      assert.ok(!call.body.input.includes('fixture metadata'))
      if (n === 2) assert.match(call.body.input, /previous draft had 2 words/)
      return wrote(n === 1 ? 'Too short.' : DOCUMENT_SUMMARY)
    },
  } })
  const { id } = await (await ask(app, document({ text: SUMMARY_DOCUMENT, mode: 'brief', minutes: 1 }))).json()
  const done = await settled(app, id)
  assert.equal(done.body.status, 'ready', JSON.stringify(done.body))
  assert.equal(done.body.brief_text, DOCUMENT_SUMMARY)
  assert.doesNotMatch(done.body.brief_text, /attention|I need|you should|priority/i)
  const paragraphs = done.body.brief_text.split('\n\n')
  assert.match(paragraphs[0], /2026-10-06.*48 packets from Shelf B/s)
  assert.match(paragraphs[1], /46 packets.*B-17 and B-32.*2026-10-13/s)
  assert.match(paragraphs[2], /\$1,248\.75.*\$900.*\$348\.75.*\$1,500.*\$251\.25/s)
  assert.match(paragraphs[3], /Shelf C.*2026-11-03.*photographs.*Audio recordings remain excluded.*No expansion was approved/s)
  assert.equal(app.gemini.seen.filter((c) => c.kind === 'brief').length, 2)
  const voicedTextJoined = app.gemini.seen.filter((c) => c.kind === 'tts').map((c) => c.body.input[0].content[0].text).join(' ')
  assert.equal(voicedTextJoined.replace(/\s+/g, ' '), DOCUMENT_SUMMARY.replace(/\s+/g, ' '))
})

test('new summary shapes miss old brief cache identities; descriptions off retains the old Full identity', () => {
  for (const document of [null, ['document-v1', 1, 'gemini-flash-latest']]) {
    for (const mode of ['brief', 'verbatim']) {
      const text = 'A trial report.'
      const parts = ['test-model', 'Aoede', mode, text, 'speech-checked-v2']
      if (document) parts.push(document)
      const oldId = createHash('sha256').update(JSON.stringify(parts)).digest('hex').slice(0, 32)
      const id = speechId({ model: 'test-model', voice: 'Aoede', mode, text, document, describe: false })
      if (mode === 'brief') assert.notEqual(id, oldId)
      else assert.equal(id, oldId)
    }
  }
})

test('qualifier prompt changes only the document summary cache identity', () => {
  for (const document of [null, ['document-v1', 1, 'gemini-flash-latest']]) {
    for (const mode of ['brief', 'verbatim']) {
      const text = 'A trial report.'
      const parts = ['test-model', 'Aoede', mode, text, 'speech-checked-v2']
      if (mode === 'brief') parts.push('summary-shape-v1')
      if (document) parts.push(document)
      const oldId = createHash('sha256').update(JSON.stringify(parts)).digest('hex').slice(0, 32)
      const id = speechId({ model: 'test-model', voice: 'Aoede', mode, text, document, describe: false })
      if (document && mode === 'brief') assert.notEqual(id, oldId)
      else assert.equal(id, oldId)
    }
  }
})

test('the stages in order: voicing counts chunks done over chunks, encoding is 0.95, then ready', async (t) => {
  const app = await rig(t, { env: { SPEECH_CHUNK_CHARS: '40', SPEECH_CONCURRENCY: '1' } })
  const text = 'The first paragraph is here.\n\nThe second paragraph is here.\n\nThe third paragraph is here.'
  app.gemini.hold('tts')
  app.encoder.hold()
  const { id } = await (await ask(app, reply({ text }))).json()
  const progressNow = async () => (await poll(app, id)).body

  assert.deepEqual(await progressNow(), { id, status: 'preparing', stage: 'voicing', progress: 0 })
  for (const expected of [0.333, 0.667]) {
    const before = app.gemini.seen.length
    app.gemini.release('tts')
    app.gemini.hold('tts')
    await until(() => app.gemini.seen.length > before)
    assert.deepEqual(await progressNow(), { id, status: 'preparing', stage: 'voicing', progress: expected })
  }
  app.gemini.release('tts')
  await until(() => app.encoder.calls.length === 1)
  assert.deepEqual(await progressNow(), { id, status: 'preparing', stage: 'encoding', progress: 0.95 })
  app.encoder.release()
  const done = await settled(app, id)
  assert.equal(done.body.status, 'ready')
  assert.equal(app.gemini.seen.filter((c) => c.kind === 'tts').length, 3)
  // Fifteen fixture words at 150 wpm and two 0.35-second pauses.
  assert.equal(done.body.duration_s, 6.7)
})

test('a second POST for a reply being prepared joins its job: one 202 each, one set of calls', async (t) => {
  const app = await rig(t)
  app.gemini.hold('tts')
  const a = await (await ask(app, reply())).json()
  const second = await ask(app, reply())
  assert.equal(second.status, 202)
  const b = await second.json()
  assert.equal(b.id, a.id)
  app.gemini.release('tts')
  await settled(app, a.id)
  assert.equal(app.gemini.seen.filter((c) => c.kind === 'tts').length, 1)
})

test('the id: the same for the same text, different when the text grows, the mode changes or the voice does', async (t) => {
  const app = await rig(t)
  const idOf = async (body) => (await (await ask(app, reply(body))).json()).id
  const base = await idOf({})
  assert.equal(await idOf({}), base)
  assert.equal(await idOf({ text: FIXTURE.replace(/\n/g, '\r\n') + '   \n' }), base, 'line endings and trailing space are the same text')
  assert.equal(await idOf({ chat_id: 'another-chat', title: 'Other' }), base, 'the chat is not part of what is said')
  const grown = await idOf({ text: `${FIXTURE}\nOne more thing.` })
  const brief = await idOf({ mode: 'brief' })
  const otherVoice = await idOf({ agent: 'quill' })
  assert.equal(new Set([base, grown, brief, otherVoice]).size, 4)
  // Two agents with no voice of their own share the default, so the same file.
  assert.equal(await idOf({ agent: 'someone' }), await idOf({ agent: 'someone-else' }))
})

test('an unknown agent is not an error: it is read in the default voice', async (t) => {
  const app = await rig(t)
  const res = await ask(app, reply({ agent: 'nobody-configured' }))
  assert.equal(res.status, 202)
  await settled(app, (await res.json()).id)
  assert.deepEqual(app.gemini.seen.find((c) => c.kind === 'tts').body.generation_config.speech_config, [{ voice: 'Charon' }])
  assert.match(app.gemini.seen.find((c) => c.kind === 'tts').body.input[0].content[0].annotations[0].style, /^informative,/)
})

// The seam for a voice per agent: a request's own `voice` and `style` win over
// SPEECH_VOICES for that request; absent, the map stands; a voice that is not
// prebuilt is refused before anything is voiced.
test('a voice and a style on the request override the agent’s entry for that request', async (t) => {
  const app = await rig(t)
  const res = await ask(app, reply({ voice: 'kore', style: 'warm and unhurried' }))
  assert.equal(res.status, 202)
  const { id } = await res.json()
  await settled(app, id)
  const sent = app.gemini.seen.find((c) => c.kind === 'tts').body
  assert.deepEqual(sent.generation_config.speech_config, [{ voice: 'Kore' }])
  assert.equal(sent.input[0].content[0].annotations[0].style, 'warm and unhurried, natural conversational pace, even and clear')
  // Another file from the map's: the voice and the style are what is said.
  const mapped = await (await ask(app, reply())).json()
  assert.notEqual(mapped.id, id)
  const styleOnly = await (await ask(app, reply({ style: 'brisk' }))).json()
  assert.notEqual(styleOnly.id, mapped.id)
})

test('an unknown voice, or a style that is not a short phrase, is a 400 naming the field, and nothing is voiced', async (t) => {
  const app = await rig(t)
  const voice = await ask(app, reply({ voice: 'Nobody' }))
  assert.equal(voice.status, 400)
  assert.match((await voice.json()).error, /"voice"/)
  const notString = await ask(app, reply({ voice: 7 }))
  assert.equal(notString.status, 400)
  assert.match((await notString.json()).error, /"voice"/)
  const style = await ask(app, reply({ style: 'x'.repeat(500) }))
  assert.equal(style.status, 400)
  assert.match((await style.json()).error, /"style"/)
  assert.equal(app.gemini.seen.filter((c) => c.kind === 'tts').length, 0)
})

test('with neither on the request the agent’s entry stands, under the shared checked-synthesis id', async (t) => {
  const app = await rig(t)
  const res = await ask(app, reply())
  const { id } = await res.json()
  assert.equal(id, app.speech.idFor({ agent: 'scout', mode: 'verbatim', text: FIXTURE, describe: false }))
  await settled(app, id)
  const sent = app.gemini.seen.find((c) => c.kind === 'tts').body
  assert.deepEqual(sent.generation_config.speech_config, [{ voice: 'Iapetus' }])
  assert.match(sent.input[0].content[0].annotations[0].style, /^clear and direct,/)
  // `null` is absent, not a voice called "null".
  assert.equal((await (await ask(app, reply({ voice: null, style: null }))).json()).id, id)
})

test('a missing field and an unknown mode are 400 with a sentence; text over the cap is 413', async (t) => {
  const app = await rig(t, { env: { SPEECH_MAX_CHARS: '100' } })
  for (const field of ['chat_id', 'agent', 'mode', 'text']) {
    const body = reply({ text: 'Short.' })
    delete body[field]
    const res = await ask(app, body)
    assert.equal(res.status, 400, field)
    assert.match((await res.json()).error, new RegExp(`"${field}"`))
  }
  const mode = await ask(app, reply({ text: 'Short.', mode: 'sung' }))
  assert.equal(mode.status, 400)
  assert.match((await mode.json()).error, /"sung" is not a way/)

  const over = await ask(app, reply({ text: 'x'.repeat(101) }))
  assert.equal(over.status, 413)
  assert.match((await over.json()).error, /101 characters, over the 100/)
  const at = await ask(app, reply({ text: 'x'.repeat(100) }))
  assert.equal(at.status, 202)
  assert.equal(app.gemini.seen.filter((c) => c.kind === 'tts').length <= 1, true)
})

test('a reply that is nothing but code says so rather than voicing silence', async (t) => {
  const app = await rig(t)
  const res = await ask(app, reply({ text: '🎉' }))
  assert.equal(res.status, 400)
  assert.match((await res.json()).error, /nothing in this reply a voice could read/)
})

// ------------------------------------------------------------- the audio

test('the audio: whole with its length, a Range is a 206 with the right bytes, and a range past the end is a 416', async (t) => {
  const app = await rig(t)
  const { id } = await (await ask(app, reply())).json()
  const { body } = await settled(app, id)
  const onDisk = await fsp.readFile(path.join(app.dir, `${id}.mp3`))

  const whole = await fetch(`${app.url}${body.url}`)
  assert.equal(whole.status, 200)
  assert.equal(whole.headers.get('content-type'), 'audio/mpeg')
  assert.equal(whole.headers.get('accept-ranges'), 'bytes')
  assert.equal(whole.headers.get('content-length'), String(onDisk.length))
  assert.deepEqual(Buffer.from(await whole.arrayBuffer()), onDisk)

  const part = await fetch(`${app.url}${body.url}`, { headers: { range: 'bytes=2-9' } })
  assert.equal(part.status, 206)
  assert.equal(part.headers.get('content-range'), `bytes 2-9/${onDisk.length}`)
  assert.equal(part.headers.get('content-length'), '8')
  assert.deepEqual(Buffer.from(await part.arrayBuffer()), onDisk.subarray(2, 10))

  const open = await fetch(`${app.url}${body.url}`, { headers: { range: `bytes=${onDisk.length - 5}-` } })
  assert.equal(open.status, 206)
  assert.deepEqual(Buffer.from(await open.arrayBuffer()), onDisk.subarray(onDisk.length - 5))

  const tail = await fetch(`${app.url}${body.url}`, { headers: { range: 'bytes=-4' } })
  assert.equal(tail.status, 206)
  assert.equal(tail.headers.get('content-range'), `bytes ${onDisk.length - 4}-${onDisk.length - 1}/${onDisk.length}`)
  assert.deepEqual(Buffer.from(await tail.arrayBuffer()), onDisk.subarray(onDisk.length - 4))

  const past = await fetch(`${app.url}${body.url}`, { headers: { range: `bytes=${onDisk.length}-` } })
  assert.equal(past.status, 416)
  assert.equal(past.headers.get('content-range'), `bytes */${onDisk.length}`)
  await past.arrayBuffer()

  const head = await fetch(`${app.url}${body.url}`, { method: 'HEAD' })
  assert.equal(head.status, 200)
  assert.equal(head.headers.get('content-length'), String(onDisk.length))
})

test('an unknown id is a 404 on both routes, and the page re-POSTs', async (t) => {
  const app = await rig(t)
  const unknown = 'f'.repeat(32)
  assert.equal((await fetch(`${app.url}/speech/${unknown}`)).status, 404)
  assert.equal((await fetch(`${app.url}/speech/${unknown}/audio.mp3`)).status, 404)
  assert.equal((await fetch(`${app.url}/speech/not-an-id`)).status, 404)
})

test('a play bumps the last-played time, and the sweep removes only what has gone unplayed past the TTL', async (t) => {
  const app = await rig(t, { env: { SPEECH_TTL_HOURS: '48' } })
  const ready = async (text) => {
    const { id } = await (await ask(app, reply({ text }))).json()
    await settled(app, id)
    return id
  }
  const played = await ready('This one is played again.')
  const stale = await ready('This one is left alone.')
  const fresh = await ready('This one was only just made.')
  const old = new Date(Date.now() - 49 * 3_600_000)
  for (const id of [played, stale]) await fsp.utimes(path.join(app.dir, `${id}.mp3`), old, old)

  const play = await fetch(`${app.url}/speech/${played}/audio.mp3`, { headers: { range: 'bytes=0-1' } })
  assert.equal(play.status, 206)
  await play.arrayBuffer()
  const bumped = (await fsp.stat(path.join(app.dir, `${played}.mp3`))).mtimeMs
  assert.ok(Date.now() - bumped < 60_000, 'a play is a last play')

  const { removed } = await app.speech.sweep()
  assert.equal(removed, 1)
  const left = (await fsp.readdir(app.dir)).sort()
  assert.deepEqual(left, [`${played}.json`, `${played}.mp3`, `${fresh}.json`, `${fresh}.mp3`].sort())
  assert.equal((await poll(app, stale)).status, 404)
  assert.equal((await poll(app, played)).body.status, 'ready')
})

test('audio made before a restart is still there after it; a job running at the restart is a 404', async (t) => {
  const app = await rig(t)
  const { id } = await (await ask(app, reply())).json()
  await settled(app, id)
  app.gemini.hold('tts')
  const { id: running } = await (await ask(app, reply({ text: 'Being voiced when the app restarts.' }))).json()

  const config = readConfig({ GEMINI_API_KEY: KEY, GEMINI_API_URL: app.gemini.url, SPEECH_DIR: app.dir, SPEECH_VOICES: VOICES, LOAM_PLUGIN_BASE: '/' })
  const speech = createSpeech(config, { encoder: fakeEncoder() })
  const after = await readAloudApp({ config, log: logInto([]), speech })
  t.after(async () => {
    await speech.stop()
    await shut(after.server)
  })
  const url = `http://127.0.0.1:${after.server.address().port}`
  assert.equal((await (await fetch(`${url}/speech/${id}`)).json()).status, 'ready')
  assert.equal((await fetch(`${url}/speech/${running}`)).status, 404)
})

// ------------------------------------------------------------- unavailable

test('no key: speech is false on the plugin’s status read and a POST is 503 "no key"', async (t) => {
  const app = await rig(t, { withKey: false })
  const health = await (await fetch(`${app.url}/status`)).json()
  assert.equal(health.speech, false)
  assert.equal(health.reason, 'no key')
  const res = await ask(app, reply())
  assert.equal(res.status, 503)
  assert.deepEqual(await res.json(), { error: 'speech unavailable', reason: 'no key' })
  assert.equal(app.gemini.seen.length, 0)
})

test('no encoder: an ffmpeg that is not there makes speech false and a POST 503 "no encoder", and one line says so', async (t) => {
  const app = await rig(t, { encoder: createFfmpegEncoder('/nonexistent/ffmpeg-is-not-here') })
  const health = await (await fetch(`${app.url}/status`)).json()
  assert.equal(health.speech, false)
  const res = await ask(app, reply())
  assert.equal(res.status, 503)
  assert.deepEqual(await res.json(), { error: 'speech unavailable', reason: 'no encoder' })
  assert.equal(app.lines.filter((l) => l.event === 'speech-config' && /no encoder/.test(l.message)).length, 1)
  // Nothing else broke: the app still serves.
  assert.equal((await fetch(`${app.url}/up`)).status, 200)
})

test('no storage: a SPEECH_DIR that cannot be made makes speech false and a POST 503 "no storage"', async (t) => {
  const dir = await tmpDir(t)
  const file = path.join(dir, 'a-file')
  await fsp.writeFile(file, '')
  const app = await rig(t, { env: { SPEECH_DIR: path.join(file, 'speech') } })
  const health = await (await fetch(`${app.url}/status`)).json()
  assert.equal(health.speech, false)
  const res = await ask(app, reply())
  assert.equal(res.status, 503)
  assert.deepEqual(await res.json(), { error: 'speech unavailable', reason: 'no storage' })
})

test('with key, encoder and storage, speech is true on the health read', async (t) => {
  const app = await rig(t)
  assert.equal((await (await fetch(`${app.url}/status`)).json()).speech, true)
})

// ------------------------------------------------------------- failures

test('duration band admits 1.35, rejects 1.9 and truncation; paces are per voice and mode', () => {
  assert.equal(durationCheck(100, 40 * 1.35).accepted, true)
  assert.equal(durationCheck(100, 40 * 0.65).accepted, true)
  assert.equal(durationCheck(100, 40 * 1.29).accepted, true)
  assert.equal(durationCheck(100, 40 * 0.66).accepted, true)
  assert.equal(durationCheck(100, 40 * 1.4).accepted, false)
  assert.equal(durationCheck(100, 40 * 1.9).accepted, false)
  assert.equal(durationCheck(100, 20).accepted, false)
  for (const args of [[0, 1], [100, NaN], [100, null], [100, 40, 0]]) {
    assert.equal(durationCheck(...args).accepted, false)
  }
  const { paces, dropped } = parsePaces('aoede=145.5;Aoede.BRIEF=121;Charon=160;Nobody=150;Kore=0;Kore=oops;Aoede.fast=130')
  assert.deepEqual([...paces], [['Aoede', 145.5], ['Aoede.brief', 121], ['Charon', 160]])
  assert.deepEqual(dropped, ['Nobody=150', 'Kore=0', 'Kore=oops', 'Aoede.fast=130'])
})

test('spoken length expands identifiers, acronyms, numbers and dimensions without changing prose', () => {
  assert.equal(spokenWords('Please decide on this today.'), 5)
  assert.equal(spokenWords('pixel_watch_api34'), 7)
  assert.equal(spokenWords('454×454'), 9)
  assert.equal(spokenWords('camelCase HTTPServer build-id-120'), 2 + 5 + 5)
  assert.equal(spokenWords('12.05 007 25%'), 4 + 3 + 3)
  assert.equal(spokenWords('1,234'), 6)
  assert.equal(spokenWords(''), 0)
})

test('code-heavy Full regression: faithful spelled-out identifiers and dimensions prepare without retry', async (t) => {
  const text = '# Step one\n\nThe `pixel_watch_api34` emulator renders `454×454` frames with `camelCase` and `build-id-120`.'
  // Independent pronunciation, rather than a WAV sized by the estimator.
  const pronunciation = 'Step one. The pixel watch A P I thirty four emulator renders four hundred fifty four by four hundred fifty four frames with camel case and build id one hundred twenty.'
  const seconds = pronunciation.split(/\s+/).length * 60 / 147
  assert.equal(durationCheck(documentSpeakable(text).split(/\s+/).length, seconds, 147).accepted, false, 'the old whitespace estimate rejected this faithful reading')
  const app = await rig(t, { gemini: { tts: () => voiced(seconds * 1000) } })
  const input = document({ text })
  const { id } = await (await ask(app, input)).json()
  assert.equal((await settled(app, id)).body.status, 'ready')
  assert.equal(app.gemini.seen.filter((c) => c.kind === 'tts').length, 1)
  const check = app.lines.find((l) => l.event === 'speech').chunk_checks[0]
  assert.equal(check.attempt, 1)
  assert.equal(check.spoken_words, pronunciation.split(/\s+/).length)
  assert.equal(check.pace_wpm, 147)
  assert.ok(check.accepted)
  assert.equal((await app.speech.peek(input)).body.status, 'ready')
  assert.ok(await app.speech.audio(id))
})

test('Aoede three-minute brief admits the supplied 1.22–1.30 Full-pace envelope on its first takes', async (t) => {
  const draft = Array.from({ length: 50 }, () => 'This is a sentence about the newly proposed design.').join('\n\n')
  const app = await rig(t, { env: { SPEECH_PACES: 'Aoede=147' }, gemini: {
    brief: () => wrote(draft),
    tts: (call, n) => voiced(call.body.input[0].content[0].text.split(/\s+/).length * 60 / 147 * (n % 2 ? 1.22 : 1.30) * 1000),
  } })
  const input = document({ text: 'Source sentence. '.repeat(1000), mode: 'brief', minutes: 3 })
  const { id } = await (await ask(app, input)).json()
  assert.equal((await settled(app, id)).body.status, 'ready')
  const line = app.lines.find((l) => l.event === 'speech')
  assert.equal(line.brief_words, 450)
  assert.ok(line.chunks > 1)
  assert.ok(line.chunk_checks.every((c) => c.attempt === 1 && c.accepted && c.pace_wpm === 121 && c.ratio < 1.071))
  assert.equal((await app.speech.peek(input)).body.status, 'ready', 'cache uses the same brief pace')
})

test('document chunks prefer headings, then paragraphs, then list items, and preserve all words', () => {
  const para = 'A sentence about the design. '.repeat(18).trim()
  const text = `# First section\n\n${para}\n\n# Second section\n\n${para}\n\n# Third section\n\n${para}`
  const chunks = chunkBlocks(documentSpeechBlocks(text), 1000)
  assert.equal(chunks.length, 3)
  assert.ok(chunks.every((c) => /^(First|Second|Third) section\./.test(c)))
  assert.equal(chunks.join(' ').replace(/\s+/g, ' '), documentSpeakable(text).replace(/\s+/g, ' '))
  assert.ok(chunks.every((c) => c.length <= 1000 && c.length > 450))

  const mixed = `# A heading\n\n${'Paragraph sentence. '.repeat(35)}\n\n- ${'One item sentence. '.repeat(30)}\n- ${'Next item sentence. '.repeat(30)}`
  const packed = chunkBlocks(documentSpeechBlocks(mixed), 1000)
  assert.match(packed[0], /^A heading\./)
  assert.match(packed[1], /^One item sentence\./)
  assert.match(packed[2], /^Next item sentence\./)
  assert.equal(packed.join(' ').replace(/\s+/g, ' '), documentSpeakable(mixed).replace(/\s+/g, ' '))
  const longItem = `- ${'A whole sentence. '.repeat(130)}`
  assert.ok(chunkBlocks(documentSpeechBlocks(longItem), 1000).every((c) => c.length <= 1000 && c.endsWith('.')))
  const indivisible = 'A'.repeat(1200) + '.'
  assert.deepEqual(chunkBlocks(documentSpeechBlocks(indivisible), 1000), [indivisible])
  assert.deepEqual(chunkBlocks([], 1000), [])
})

test('Full document preparation sends section chunks in parallel, with headings beside their text', async (t) => {
  const paragraph = 'A sentence about the design. '.repeat(10)
  const names = ['First', 'Second', 'Third', 'Fourth']
  const text = names.map((name) => `# ${name} section\n\n${paragraph}\n\n${paragraph}`).join('\n\n')
  assert.equal(chunkText(documentSpeakable(text), 1000).length, 3, 'old balanced prose packed across sections')
  const app = await rig(t)
  app.gemini.hold('tts')
  const { id } = await (await ask(app, document({ text }))).json()
  await until(() => app.gemini.seen.filter((c) => c.kind === 'tts').length === 4)
  const calls = app.gemini.seen.filter((c) => c.kind === 'tts')
  calls.forEach((call, i) => {
    assert.ok(call.body.input[0].content[0].text.startsWith(`${names[i]} section.`))
    assert.equal((call.body.input[0].content[0].text.match(/section/g) ?? []).length, 1)
  })
  app.gemini.release('tts')
  assert.equal((await settled(app, id)).body.status, 'ready')
  const line = app.lines.find((l) => l.event === 'speech')
  assert.equal(line.chunks, 4)
  assert.ok(line.chunk_checks.every((c) => c.accepted && c.attempt === 1))
})

test('an over-long chunk is retried with identical text; only its clean retry is encoded and cached', async (t) => {
  const app = await rig(t, { gemini: { tts: (call, n) => voicedText(call, n === 1 ? 1.6 : 1) } })
  const text = 'Please decide on this today.'
  const { id } = await (await ask(app, reply({ text }))).json()
  const done = await settled(app, id)
  assert.equal(done.body.status, 'ready')
  assert.equal(done.body.duration_s, 2)
  assert.deepEqual(app.encoder.calls, [{ bytes: 2 * 24000 * 2, rate: 24000 }])
  const calls = app.gemini.seen.filter((c) => c.kind === 'tts')
  assert.equal(calls.length, 2)
  assert.deepEqual(calls[0].body, calls[1].body)
  const line = app.lines.find((l) => l.event === 'speech')
  assert.deepEqual(line.chunk_checks.map((c) => [c.chunk, c.attempt, c.ratio, c.accepted]), [[1, 1, 1.6, false], [1, 2, 1, true]])
  const meta = JSON.parse(await fsp.readFile(path.join(app.dir, `${id}.json`), 'utf8'))
  assert.equal(meta.validation.chunks.length, 1)
  assert.equal(meta.validation.chunks[0].duration_s, 2)
  assert.equal((await ask(app, reply({ text }))).status, 200)
})

for (const [label, input] of [
  ['chat Full', reply({ text: 'Please decide on this today.' })],
  ['chat Brief', reply({ mode: 'brief' })],
  ['Files Full', document()],
  ['Files Brief', document({ text: 'Source sentence. '.repeat(1000), mode: 'brief', minutes: 3 })],
]) {
  test(`${label}: two over-long chunks fail the take, never encode, play or cache`, async (t) => {
    const draft = Array.from({ length: 50 }, () => 'This is a sentence about the newly proposed design.').join('\n\n')
    const app = await rig(t, { env: { SPEECH_CONCURRENCY: '1' }, gemini: {
      tts: (call) => voicedText(call, 1.9), brief: () => wrote(draft),
    } })
    const { id } = await (await ask(app, input)).json()
    const done = await settled(app, id)
    assert.deepEqual(done.body, { id, status: 'failed', error: CLEAN_SPEECH_ERROR })
    assert.equal(app.gemini.seen.filter((c) => c.kind === 'tts').length, 2)
    assert.equal(app.encoder.calls.length, 0)
    assert.deepEqual(await fsp.readdir(app.dir), [])
    assert.equal((await fetch(`${app.url}/speech/${id}/audio.mp3`)).status, 404)
    const line = app.lines.find((l) => l.event === 'speech' && l.id === id)
    assert.equal(line.outcome, 'error')
    assert.deepEqual(line.chunk_checks.map((c) => [c.chunk, c.attempt, c.accepted]), [[1, 1, false], [1, 2, false]])
    const peek = await app.speech.peek(input)
    assert.equal(peek.body.status, 'failed')
    assert.equal((await ask(app, input)).status, 202, 'an explicit retry starts a new job')
    await settled(app, id)
    assert.equal(app.gemini.seen.filter((c) => c.kind === 'tts').length, 4)
    assert.deepEqual(await fsp.readdir(app.dir), [])
  })
}

test('one bad middle chunk fails even when the whole take would be inside the duration band', async (t) => {
  const app = await rig(t, { env: { SPEECH_CHUNK_CHARS: '40', SPEECH_CONCURRENCY: '1' }, gemini: {
    tts: (call) => voicedText(call, call.body.input[0].content[0].text.includes('second') ? 1.4 : 1),
  } })
  const text = 'The first paragraph is here.\n\nThe second paragraph is here.\n\nThe third paragraph is here.'
  const { id } = await (await ask(app, reply({ text }))).json()
  assert.equal((await settled(app, id)).body.status, 'failed')
  const checks = app.lines.find((l) => l.event === 'speech').chunk_checks
  assert.deepEqual(checks.map((c) => [c.chunk, c.attempt, c.accepted]), [[1, 1, true], [2, 1, false], [2, 2, false]])
  assert.equal(app.encoder.calls.length, 0)
  assert.deepEqual(await fsp.readdir(app.dir), [])
})

test('concurrent synthesis still fails as a whole when one chunk remains over-long', async (t) => {
  const app = await rig(t, { env: { SPEECH_CHUNK_CHARS: '40', SPEECH_CONCURRENCY: '3' }, gemini: {
    tts: (call) => voicedText(call, call.body.input[0].content[0].text.includes('second') ? 1.4 : 1),
  } })
  const input = reply({ text: 'The first paragraph is here.\n\nThe second paragraph is here.\n\nThe third paragraph is here.' })
  const { id } = await (await ask(app, input)).json()
  assert.deepEqual((await settled(app, id)).body, { id, status: 'failed', error: CLEAN_SPEECH_ERROR })
  assert.equal(app.encoder.calls.length, 0)
  assert.deepEqual(await fsp.readdir(app.dir), [])
  assert.equal((await fetch(`${app.url}/speech/${id}/audio.mp3`)).status, 404)
  const badChecks = app.lines.find((l) => l.event === 'speech').chunk_checks.filter((c) => c.chunk === 2)
  assert.deepEqual(badChecks.map((c) => [c.attempt, c.accepted]), [[1, false], [2, false]])
})

test('a configured voice pace is used during synthesis and cache reads', async (t) => {
  const app = await rig(t, { env: { SPEECH_PACES: 'Iapetus=100' }, gemini: { tts: () => voiced(6000) } })
  const input = reply({ text: 'One two three four five six seven eight nine ten.' })
  const { id } = await (await ask(app, input)).json()
  assert.equal((await settled(app, id)).body.status, 'ready')
  const check = app.lines.find((l) => l.event === 'speech').chunk_checks[0]
  assert.equal(check.pace_wpm, 100)
  assert.equal(check.ratio, 1)
  assert.equal((await app.speech.peek(input)).body.status, 'ready')
  const after = createSpeech(readConfig({ GEMINI_API_KEY: KEY, SPEECH_DIR: app.dir, SPEECH_PACES: 'Iapetus=150' }), { encoder: fakeEncoder() })
  t.after(() => after.stop())
  assert.equal(await after.audio(id), null, 'the current pace rejects the old six-second take')
  assert.deepEqual(await fsp.readdir(app.dir), [])
})

test('a mode-specific pace overrides the base during synthesis and after a restart', async (t) => {
  const spec = 'Iapetus=150;Iapetus.brief=100'
  const app = await rig(t, { env: { SPEECH_PACES: spec }, gemini: {
    brief: () => wrote('One two three four five six seven eight nine ten.'), tts: () => voiced(6000),
  } })
  const { id } = await (await ask(app, reply({ mode: 'brief' }))).json()
  assert.equal((await settled(app, id)).body.status, 'ready')
  assert.equal(app.lines.find((l) => l.event === 'speech').chunk_checks[0].pace_wpm, 100)
  const config = readConfig({ GEMINI_API_KEY: KEY, SPEECH_DIR: app.dir, SPEECH_PACES: spec })
  const after = createSpeech(config, { encoder: fakeEncoder() })
  t.after(() => after.stop())
  assert.ok(await after.audio(id))
  const changed = createSpeech(readConfig({ GEMINI_API_KEY: KEY, SPEECH_DIR: app.dir, SPEECH_PACES: 'Iapetus=150' }), { encoder: fakeEncoder() })
  t.after(() => changed.stop())
  assert.equal(await changed.audio(id), null)
})

test('startup purges the named bad take and unchecked legacy audio, retaining a checked take', async (t) => {
  const app = await rig(t)
  const input = reply({ text: 'Please decide on this today.' })
  const { id } = await (await ask(app, input)).json()
  await settled(app, id)
  const meta = JSON.parse(await fsp.readFile(path.join(app.dir, `${id}.json`), 'utf8'))
  const bad = '2bd0bee386eea59a7d19dbc1f4c3bec6'
  const legacy = 'a'.repeat(32)
  const versionOne = 'b'.repeat(32)
  for (const [other, data] of [[bad, meta], [legacy, { duration_s: 327.5, brief_text: 'word '.repeat(424) }],
    [versionOne, { ...meta, validation: { ...meta.validation, version: 1 } }]]) {
    await fsp.writeFile(path.join(app.dir, `${other}.mp3`), 'old audio')
    await fsp.writeFile(path.join(app.dir, `${other}.json`), JSON.stringify(data))
  }
  const lines = []
  const after = createSpeech(readConfig({ GEMINI_API_KEY: KEY, SPEECH_DIR: app.dir }), { encoder: fakeEncoder(), log: logInto(lines) })
  t.after(() => after.stop())
  await after.start()
  assert.deepEqual((await fsp.readdir(app.dir)).sort(), [`${id}.json`, `${id}.mp3`])
  assert.equal((await after.lookup(id)).status, 'ready')
  assert.equal(await after.audio(bad), null)
  assert.deepEqual(lines.filter((l) => l.event === 'speech-cache-purge').map((l) => l.id).sort(), [bad, legacy, versionOne].sort())
})

test('cache serving rechecks each chunk, its stored total, and missing validation before any audio bytes', async (t) => {
  const app = await rig(t, { env: { SPEECH_CHUNK_CHARS: '40' } })
  const text = 'The first paragraph is here.\n\nThe second paragraph is here.\n\nThe third paragraph is here.'
  const input = reply({ text })
  for (const corrupt of [
    (meta) => { meta.validation.chunks[1].duration_s *= 1.4; meta.duration_s = 7.5 },
    (meta) => { meta.duration_s += 20 },
    (meta) => { delete meta.validation },
  ]) {
    const { id } = await (await ask(app, input)).json()
    await settled(app, id)
    const meta = JSON.parse(await fsp.readFile(path.join(app.dir, `${id}.json`), 'utf8'))
    corrupt(meta)
    await fsp.writeFile(path.join(app.dir, `${id}.json`), JSON.stringify(meta))
    assert.equal((await fetch(`${app.url}/speech/${id}/audio.mp3`)).status, 404)
    assert.equal((await app.speech.peek(input)).body.status, 'none')
    assert.deepEqual(await fsp.readdir(app.dir), [])
  }
})

test('a Gemini refusal is status "failed" with one plain sentence, and a POST after it starts again', async (t) => {
  let refuse = true
  const app = await rig(t, {
    gemini: { tts: (call) => (refuse ? [400, { error: { message: 'Voice not allowed for this request.' } }] : voicedText(call)) },
  })
  const { id } = await (await ask(app, reply())).json()
  const failed = await settled(app, id)
  assert.equal(failed.status, 200)
  assert.deepEqual(failed.body, {
    id, status: 'failed', error: 'Gemini could not finish voicing part of this (400): Voice not allowed for this request.',
  })
  assert.equal(app.gemini.seen.filter((c) => c.kind === 'tts').length, 1, 'a 400 is not retried')
  refuse = false
  const again = await ask(app, reply())
  assert.equal(again.status, 202)
  assert.equal((await settled(app, id)).body.status, 'ready')
})

test('a 429 or a 5xx is retried three times; a fourth failure fails the take, and a recovery does not', async (t) => {
  const app = await rig(t, {
    gemini: { tts: (call, n) => (call.body.input[0].content[0].text.startsWith('Recover') && n % 2 === 0 ? voicedText(call) : [n % 2 ? 429 : 503, { error: { message: 'busy' } }]) },
  })
  const { id } = await (await ask(app, reply({ text: 'Never recovers.' }))).json()
  const failed = await settled(app, id)
  assert.equal(failed.body.status, 'failed')
  assert.match(failed.body.error, /^Gemini could not finish voicing part of this \(503\): busy\.$/)
  assert.equal(app.gemini.seen.filter((c) => c.kind === 'tts').length, 4)

  const { id: second } = await (await ask(app, reply({ text: 'Recovers on the second try.' }))).json()
  assert.equal((await settled(app, second)).body.status, 'ready')
})

test('a brief the writer cannot write fails the take before any voicing', async (t) => {
  const app = await rig(t, { gemini: { brief: () => [403, { error: { message: 'Permission denied.' } }] } })
  const { id } = await (await ask(app, reply({ mode: 'brief' }))).json()
  const failed = await settled(app, id)
  assert.deepEqual(failed.body, { id, status: 'failed', error: 'Gemini could not finish writing the brief (403): Permission denied.' })
  assert.equal(app.gemini.seen.filter((c) => c.kind === 'tts').length, 0)
})

// ------------------------------------------------------------- the log

test('one log line per synthesis: counts, statuses and the outcome, never the text or the key', async (t) => {
  const app = await rig(t, { gemini: { tts: (call, n) => (n === 1 ? [429, { error: { message: 'slow down' } }] : voicedText(call)) } })
  const { id } = await (await ask(app, reply())).json()
  await settled(app, id)
  await until(() => app.lines.some((l) => l.event === 'speech'))
  const lines = app.lines.filter((l) => l.event === 'speech')
  assert.equal(lines.length, 1)
  const line = lines[0]
  assert.equal(line.id, id)
  assert.equal(line.agent, 'scout')
  assert.equal(line.mode, 'verbatim')
  assert.equal(line.chars, FIXTURE.trim().length)
  assert.equal(line.chunks, 1)
  assert.equal(line.duration_s, secondsFor(speakable(FIXTURE)))
  assert.deepEqual(line.gemini_statuses, [429, 200])
  assert.equal(line.outcome, 'ok')
  assert.equal(typeof line.ms, 'number')
  const all = JSON.stringify(app.lines)
  assert.ok(!all.includes(KEY), 'never the key')
  assert.ok(!all.includes('Deploy status') && !all.includes('the owner'), 'never the text')
})

// ------------------------------------------------------------- the model

test('a tracking alias in the models list is used when SPEECH_TTS_MODEL is unset, and logged', async (t) => {
  const app = await rig(t, { gemini: { models: ['gemini-3.8-flash-tts', 'gemini-flash-tts-latest', 'gemini-flash-latest'] } })
  const { id } = await (await ask(app, reply())).json()
  await settled(app, id)
  assert.equal(app.gemini.seen.find((c) => c.kind === 'tts').body.model, 'gemini-flash-tts-latest')
  const line = app.lines.find((l) => l.event === 'speech-models')
  assert.equal(line.tracking_alias, 'gemini-flash-tts-latest')
  assert.equal(line.tts_model, 'gemini-flash-tts-latest')
  assert.equal(app.gemini.seen.filter((c) => c.kind === 'models').length, 1, 'listed once')
})

test('a named SPEECH_TTS_MODEL wins over an alias, and no alias is no change', async (t) => {
  const named = await rig(t, { env: { SPEECH_TTS_MODEL: 'gemini-3.8-flash-lite-tts' }, gemini: { models: ['gemini-flash-tts-latest'] } })
  await settled(named, (await (await ask(named, reply())).json()).id)
  assert.equal(named.gemini.seen.find((c) => c.kind === 'tts').body.model, 'gemini-3.8-flash-lite-tts')
  assert.equal(named.lines.find((l) => l.event === 'speech-models').tracking_alias, 'gemini-flash-tts-latest')

  const none = await rig(t, { gemini: { models: ['gemini-3.8-flash-tts', 'gemini-3.8-flash-lite-tts'] } })
  await settled(none, (await (await ask(none, reply())).json()).id)
  assert.equal(none.gemini.seen.find((c) => c.kind === 'tts').body.model, 'gemini-3.8-flash-tts')
  assert.equal(none.lines.find((l) => l.event === 'speech-models').tracking_alias, null)
})

// ------------------------------------------------------------- the real encoder

const FFMPEG = process.env.SPEECH_FFMPEG || 'ffmpeg'
const haveFfmpeg = await createFfmpegEncoder(FFMPEG).probe()
const NO_FFMPEG = haveFfmpeg ? false : `no ffmpeg with libmp3lame on this box (${FFMPEG}); set SPEECH_FFMPEG to one to run it`

test('real ffmpeg: the joined chunks become one mono MP3, and the duration is the audio’s own', { skip: NO_FFMPEG }, async (t) => {
  const app = await rig(t, {
    encoder: createFfmpegEncoder(FFMPEG),
    env: { SPEECH_CHUNK_CHARS: '40' },
  })
  const text = 'The first paragraph is here.\n\nThe second paragraph is here.'
  const { id } = await (await ask(app, reply({ text }))).json()
  const done = await settled(app, id)
  assert.equal(done.body.status, 'ready', JSON.stringify(done.body))
  // Ten fixture words at 150 wpm and one pause.
  assert.equal(done.body.duration_s, 4.4)
  const mp3 = await fsp.readFile(path.join(app.dir, `${id}.mp3`))
  const sync = mp3.indexOf(Buffer.from([0xff]), mp3.subarray(0, 3).toString('ascii') === 'ID3' ? 10 + id3Size(mp3) : 0)
  assert.equal(mp3[sync] === 0xff && (mp3[sync + 1] & 0xe0) === 0xe0, true, 'an MPEG audio frame')
  assert.equal(mp3[sync + 3] >> 6, 3, 'mono')
  // 4.4 s at 64 kbit/s is about 35 kB; a WAV of it would be 211 kB.
  assert.ok(mp3.length > 10_000 && mp3.length < 40_000, `${mp3.length} bytes`)
  const dir = await fsp.readdir(app.dir)
  assert.deepEqual(dir.sort(), [`${id}.json`, `${id}.mp3`].sort(), 'no temporary file left behind')
})

test('real ffmpeg: the probe finds it and speech is available', { skip: NO_FFMPEG }, async (t) => {
  const app = await rig(t, { encoder: createFfmpegEncoder(FFMPEG) })
  assert.equal((await (await fetch(`${app.url}/status`)).json()).speech, true)
})

function id3Size(buf) {
  return ((buf[6] & 0x7f) << 21) | ((buf[7] & 0x7f) << 14) | ((buf[8] & 0x7f) << 7) | (buf[9] & 0x7f)
}

test('force regenerates only its variant, deduplicates concurrent requests and publishes after success', async (t) => {
  const app = await rig(t)
  const input = reply({ text: 'Please decide on the new design today.', voice: 'Kore', style: 'calm and clear' })
  const old = (await (await ask(app, input)).json()).id
  await settled(app, old)
  const briefInput = { ...input, mode: 'brief' }
  const brief = (await (await ask(app, briefInput)).json()).id
  await settled(app, brief)
  const bytes = await fsp.readFile(path.join(app.dir, `${old}.mp3`))
  const before = app.gemini.seen.filter(c => c.kind === 'tts').length
  app.gemini.hold('tts')
  const responses = await Promise.all(Array.from({ length: 6 }, () => ask(app, { ...input, force: true })))
  assert.ok(responses.every(r => r.status === 202))
  const pending = await Promise.all(responses.map(r => r.json()))
  assert.equal(new Set(pending.map(b => b.id)).size, 1)
  const next = pending[0].id
  assert.notEqual(next, old)
  await until(() => app.gemini.seen.filter(c => c.kind === 'tts').length === before + 1)
  assert.equal((await app.speech.peek(input)).body.previous.id, old)
  assert.deepEqual(await fsp.readFile(path.join(app.dir, `${old}.mp3`)), bytes)
  assert.equal((await fetch(`${app.url}/speech/${old}/audio.mp3`, { headers: { range: 'bytes=0-7' } })).status, 206)
  assert.equal((await ask(app, briefInput)).status, 200)
  assert.equal((await (await ask(app, briefInput)).json()).id, brief)
  app.gemini.release('tts')
  const done = await settled(app, next)
  assert.equal(done.body.status, 'ready')
  assert.equal((await (await ask(app, input)).json()).id, next)
  assert.equal((await app.speech.peek(input)).body.id, next)
  assert.equal((await fetch(`${app.url}${done.body.url}`)).status, 200)
  assert.deepEqual(await fsp.readFile(path.join(app.dir, `${old}.mp3`)), bytes, 'old playback URL remains immutable')
  const tts = app.gemini.seen.filter(c => c.kind === 'tts').at(-1).body
  assert.deepEqual(tts.generation_config.speech_config, [{ voice: 'Kore' }])
  assert.match(tts.input[0].content[0].annotations[0].style, /^calm and clear/)
  assert.equal(tts.input[0].content[0].text, speakable(input.text))
  const after = createSpeech(readConfig({ GEMINI_API_KEY: KEY, SPEECH_DIR: app.dir }), { encoder: fakeEncoder() })
  t.after(() => after.stop())
  assert.equal((await after.peek(input)).body.id, next, 'replacement survives app restart')
  assert.equal((await ask(app, { ...input, force: 'yes' })).status, 400)
})

test('failed forced generation or encoding preserves cached audio and permits retry', async (t) => {
  let fail = false
  const encoder = fakeEncoder(), encode = encoder.encode.bind(encoder)
  const app = await rig(t, { encoder, gemini: { tts: call => fail ? [400, { error: { message: 'Fixture refusal' } }] : voicedText(call) } })
  const input = reply({ text: 'Please keep the old audio until this succeeds.' })
  const old = (await (await ask(app, input)).json()).id
  await settled(app, old)
  for (const kind of ['generation', 'encoding']) {
    fail = kind === 'generation'
    encoder.encode = kind === 'encoding' ? async () => { throw new Error('Fixture encoder refusal') } : encode
    const next = (await (await ask(app, { ...input, force: true })).json()).id
    assert.equal((await settled(app, next)).body.status, 'failed')
    assert.equal((await (await ask(app, input)).json()).id, old)
    assert.equal((await app.speech.peek(input)).body.id, old)
    assert.equal((await fetch(`${app.url}/speech/${old}/audio.mp3`)).status, 200)
  }
  fail = false; encoder.encode = encode
  const next = (await (await ask(app, { ...input, force: true })).json()).id
  assert.equal((await settled(app, next)).body.status, 'ready')
  assert.equal((await (await ask(app, input)).json()).id, next)
})

test('force supports chat Brief and Files Full/Brief without invalidating another stop, source or voice', async (t) => {
  const app = await rig(t, { gemini: { brief: () => wrote('Keep this clear and simple. '.repeat(30).trim()) } })
  const text = 'Keep this document clear and simple. '.repeat(100)
  const inputs = [reply({ text, mode: 'brief' }), document({ text }), document({ text, mode: 'brief', minutes: 1 }), document({ text, voice: 'Kore' })]
  const original = []
  for (const input of inputs) {
    const { id } = await (await ask(app, input)).json()
    assert.equal((await settled(app, id)).body.status, 'ready')
    original.push(id)
  }
  for (let i = 0; i < 3; i++) {
    const { id } = await (await ask(app, { ...inputs[i], force: true })).json()
    assert.notEqual(id, original[i])
    assert.equal((await settled(app, id)).body.status, 'ready')
    original[i] = id
    for (let j = 0; j < inputs.length; j++) assert.equal((await (await ask(app, inputs[j])).json()).id, original[j])
  }
  // Expiring a replacement does not resurrect its predecessor; the new
  // synthesis must publish a fresh pointer under the same variant identity.
  await fsp.rm(path.join(app.dir, `${original[0]}.mp3`))
  const stale = new Date(Date.now() - 49 * 3_600_000)
  const pointer = path.join(app.dir, `${app.speech.idFor(inputs[0])}.current`)
  await fsp.utimes(pointer, stale, stale)
  await app.speech.sweep()
  assert.equal((await app.speech.peek(inputs[0])).body.status, 'none')
  const { id } = await (await ask(app, inputs[0])).json()
  assert.equal((await settled(app, id)).body.status, 'ready')
  assert.equal((await app.speech.peek(inputs[0])).body.id, id)
})

// Brief 2026-10-04: the page says "Ready" before a tap from what the server
// has, not from a guess. The lookup takes the POST's own body and starts
// nothing: none, then preparing while a POST's job runs, then ready under the
// id the POST answered; and none again once the sweep has taken the file.
test('lookup: none, preparing, ready under the POST’s id, and none again after the sweep; it never starts a take', async (t) => {
  const app = await rig(t, { env: { SPEECH_TTL_HOURS: '48' } })
  const look = async (body) => {
    const res = await fetch(`${app.url}/speech/lookup`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
    return { status: res.status, cache: res.headers.get('cache-control'), body: await res.json() }
  }
  const none = await look(reply())
  assert.equal(none.status, 200)
  assert.equal(none.cache, 'no-store')
  assert.equal(none.body.status, 'none')
  assert.match(none.body.id, /^[0-9a-f]{32}$/)
  assert.equal(app.gemini.seen.filter((c) => c.kind === 'tts').length, 0, 'a lookup voiced something')
  assert.equal((await look(reply())).body.status, 'none', 'a lookup started a take')

  app.gemini.hold('tts')
  const made = await (await ask(app, reply())).json()
  assert.equal(made.id, none.body.id, 'the lookup names a different id from the POST')
  assert.equal((await look(reply())).body.status, 'preparing')
  app.gemini.release('tts')
  const done = await settled(app, made.id)
  assert.deepEqual((await look(reply())).body, done.body)
  // Another mode of the same text is its own take, and there is none.
  assert.equal((await look(reply({ mode: 'brief' }))).body.status, 'none')

  // Past the TTL and swept: the lookup says none, so a page cannot say Ready.
  const old = new Date(Date.now() - 49 * 3_600_000)
  await fsp.utimes(path.join(app.dir, `${made.id}.mp3`), old, old)
  await app.speech.sweep()
  assert.equal((await look(reply())).body.status, 'none')
  // A bad body is the POST's own refusal.
  assert.equal((await look({ ...reply(), mode: 'sung' })).status, 400)
})

test('Files Full: frontmatter is dropped, Aoede is the default, unchanged notes hit and edited notes miss the cache', async (t) => {
  const app = await rig(t)
  const input = document({ text: '---\nprivate: never spoken\n---\n# A short note\n\nPlease decide today.' })
  const first = await ask(app, input)
  assert.equal(first.status, 202)
  const { id } = await first.json()
  assert.equal((await settled(app, id)).body.status, 'ready')
  const tts = app.gemini.seen.filter((c) => c.kind === 'tts')
  assert.equal(tts.length, 1)
  assert.deepEqual(tts[0].body.generation_config.speech_config, [{ voice: 'Aoede' }])
  assert.match(tts[0].body.input[0].content[0].annotations[0].style, /^warm, natural and unhurried/)
  assert.ok(!tts[0].body.input[0].content[0].text.includes('private'))
  const again = await ask(app, input)
  assert.equal(again.status, 200)
  assert.equal((await again.json()).id, id)
  assert.equal(app.gemini.seen.filter((c) => c.kind === 'tts').length, 1)
  const changed = await ask(app, { ...input, text: input.text + '\n\nThe decision has changed.' })
  assert.equal(changed.status, 202)
  const changedId = (await changed.json()).id
  assert.notEqual(changedId, id)
  await settled(app, changedId)
  const voiced = await ask(app, { ...input, voice: 'Kore' })
  assert.notEqual((await voiced.json()).id, id)
})

test('Files brief: a 3-minute request writes 450 words, repairs a short draft, chunks it, and logs its budget', async (t) => {
  const draft = Array.from({ length: 50 }, () => 'This is a sentence about the newly proposed design.').join('\n\n')
  assert.equal(draft.split(/\s+/).length, 450)
  const app = await rig(t, { gemini: { brief: (call, n) => {
    assert.match(call.body.system_instruction, /required range is 405 to 495/)
    assert.match(call.body.system_instruction, /Add no opinion/)
    assert.match(call.body.input, /Original document/)
    return wrote(n === 1 ? 'This is too short.' : draft)
  } } })
  const long = 'Original document.\n\n' + Array.from({ length: 400 }, () => 'This is a paragraph in a long draft.').join('\n\n')
  const input = document({ mode: 'brief', minutes: 3, text: long })
  const { id } = await (await ask(app, input)).json()
  const done = await settled(app, id)
  assert.equal(done.body.status, 'ready', JSON.stringify(done.body))
  assert.equal(done.body.brief_text.split(/\s+/).length, 450)
  assert.equal(app.gemini.seen.filter((c) => c.kind === 'brief').length, 2)
  assert.ok(app.gemini.seen.filter((c) => c.kind === 'tts').length > 1)
  const line = app.lines.find((l) => l.event === 'speech' && l.id === id)
  assert.equal(line.word_budget, 450)
  assert.equal(line.brief_words, 450)
  assert.equal(line.voice, 'Aoede')
  assert.equal((await ask(app, input)).status, 200)
  const shorter = await ask(app, { ...input, minutes: 1 })
  assert.notEqual((await shorter.json()).id, id)
})

test('Files refuses invalid stops and mode mismatches; repeated wrong-length briefs fail before TTS', async (t) => {
  const app = await rig(t)
  for (const input of [document({ minutes: 3 }), document({ mode: 'brief' }), document({ minutes: 0.5, mode: 'brief' }), document({ source: 'unknown' })]) {
    assert.equal((await ask(app, input)).status, 400)
  }
  const broken = await rig(t, { gemini: { brief: () => wrote('Too short.') } })
  const { id } = await (await ask(broken, document({ text: 'word '.repeat(1000), mode: 'brief', minutes: 3 }))).json()
  const done = await settled(broken, id)
  assert.equal(done.body.status, 'failed')
  assert.match(done.body.error, /could not keep this to 450 words/)
  assert.equal(broken.gemini.seen.filter((c) => c.kind === 'tts').length, 0)
})

test('Files permits a 15,000-word note beyond the chat cap; its own cap is enforced', async (t) => {
  const app = await rig(t, { gemini: { brief: () => wrote(Array.from({ length: 50 }, () => 'This is a sentence about the newly proposed design.').join(' ')) } })
  const text = 'draft '.repeat(15000)
  const input = document({ text, mode: 'brief', minutes: 3 })
  const { id } = await (await ask(app, input)).json()
  assert.equal((await settled(app, id)).body.status, 'ready')
  assert.equal(app.gemini.seen.find((c) => c.kind === 'brief').body.input.includes(text.trim()), true)
  assert.equal((await ask(app, document({ text: 'x'.repeat(250001) }))).status, 413)
})

// Full descriptions use the same real server pipeline and fake Gemini as TTS.
const DESCRIPTION_REPLY = '# Plan selection\n\nWe need offline sync.\n\n| Plan | Offline |\n| --- | --- |\n| Basic | No |\n| Pro | Yes |\n\n```mermaid\nflowchart LR\n  Browser --> Relay --> Host\n```\n\n```js\nstartSession()\n```'
const TABLE_DESCRIPTION = 'A table comparing the plans: Pro is the only plan with offline sync.'
const DIAGRAM_DESCRIPTION = 'A diagram showing the request path: the browser reaches the host through a relay.'

for (const source of ['reply', 'document']) test(`Full ${source}: force preserves descriptions, previous audio and independent enabled/disabled caches`, async t => {
  const app = await rig(t, { gemini: { brief: call => wrote(JSON.parse(call.body.input).kind === 'table' ? TABLE_DESCRIPTION : DIAGRAM_DESCRIPTION) } })
  const input = source === 'document' ? document({ text: DESCRIPTION_REPLY }) : reply({ text: DESCRIPTION_REPLY })
  const on = { ...input, describe_tables_and_diagrams: true }
  const off = { ...input, describe_tables_and_diagrams: false }
  const make = async body => {
    const response = await ask(app, body)
    assert.equal(response.status, 202)
    const made = await response.json()
    assert.equal((await settled(app, made.id)).body.status, 'ready')
    return made.id
  }
  const original = await make(on), disabled = await make(off)
  app.gemini.hold('tts')
  const pending = await (await ask(app, { ...on, force: true })).json()
  assert.notEqual(pending.id, original)
  assert.equal(pending.previous.id, original)
  assert.equal((await app.speech.peek(on)).body.previous.id, original)
  assert.equal((await poll(app, original)).body.status, 'ready')
  app.gemini.release('tts')
  assert.equal((await settled(app, pending.id)).body.status, 'ready')
  const said = app.gemini.seen.filter(c => c.kind === 'tts').at(-1).body.input[0].content[0].text
  assert.ok(said.includes(TABLE_DESCRIPTION) && said.includes(DIAGRAM_DESCRIPTION))
  assert.equal(app.gemini.seen.filter(c => c.kind === 'brief').length, 2, 'force replaces audio while preserving the independent description cache')
  assert.equal((await (await ask(app, off)).json()).id, disabled)
  const nextDisabled = await make({ ...off, force: true })
  assert.notEqual(nextDisabled, disabled)
  assert.equal(app.gemini.seen.filter(c => c.kind === 'tts').at(-1).body.input[0].content[0].text, speakable(DESCRIPTION_REPLY))
  assert.equal((await (await ask(app, on)).json()).id, pending.id)
})

for (const source of ['reply', 'document']) test(`Full ${source}: default-on describes tables/diagrams, caches their words across voices, off is the legacy reading`, async t => {
  const app = await rig(t, { gemini: { brief: call => {
    const block = JSON.parse(call.body.input)
    assert.equal(call.key, KEY)
    assert.equal(call.body.store, false)
    assert.match(call.body.system_instruction, /one or two short sentences/)
    assert.equal(block.heading, 'Plan selection.')
    assert.equal(block.paragraph, 'We need offline sync.')
    return wrote(block.kind === 'table' ? TABLE_DESCRIPTION : DIAGRAM_DESCRIPTION)
  } } })
  const input = source === 'document' ? document({ text: DESCRIPTION_REPLY }) : reply({ text: DESCRIPTION_REPLY, describe_tables_and_diagrams: undefined })
  const made = await (await ask(app, input)).json()
  assert.equal(made.stage, 'describing')
  assert.equal((await settled(app, made.id)).body.status, 'ready')
  const said = app.gemini.seen.filter(c => c.kind === 'tts').map(c => c.body.input[0].content[0].text).join('\n\n')
  const expected = speakable(DESCRIPTION_REPLY).replace('A table of two rows, skipped.', TABLE_DESCRIPTION).replace('A code block of two lines, skipped.', DIAGRAM_DESCRIPTION)
  assert.equal(said, expected)
  if (process.env.DESCRIPTIONS_SPEECH_EVIDENCE) {
    await fsp.mkdir(process.env.DESCRIPTIONS_SPEECH_EVIDENCE, { recursive: true })
    await fsp.writeFile(path.join(process.env.DESCRIPTIONS_SPEECH_EVIDENCE, `${source}-on.txt`), said + '\n')
  }
  assert.match(said, /A code block of one line, skipped\./)
  assert.equal(app.gemini.seen.filter(c => c.kind === 'brief').length, 2)
  assert.equal((await ask(app, input)).status, 200)
  const other = await (await ask(app, { ...input, voice: 'Kore' })).json()
  assert.equal((await settled(app, other.id)).body.status, 'ready')
  assert.equal(app.gemini.seen.filter(c => c.kind === 'brief').length, 2, 'cached blocks should survive a voice change')
  const before = app.gemini.seen.length
  const off = await (await ask(app, { ...input, describe_tables_and_diagrams: false })).json()
  assert.notEqual(off.id, made.id)
  assert.equal((await settled(app, off.id)).body.status, 'ready')
  assert.equal(app.gemini.seen.slice(before).filter(c => c.kind === 'brief').length, 0)
  const offText = app.gemini.seen.slice(before).filter(c => c.kind === 'tts').map(c => c.body.input[0].content[0].text).join('\n\n')
  assert.equal(offText, speakable(DESCRIPTION_REPLY))
  if (process.env.DESCRIPTIONS_SPEECH_EVIDENCE) await fsp.writeFile(path.join(process.env.DESCRIPTIONS_SPEECH_EVIDENCE, `${source}-off.txt`), offText + '\n')
})

test('Full falls back per block on a provider failure and still voices the rest', async t => {
  const app = await rig(t, { gemini: { brief: call => JSON.parse(call.body.input).kind === 'table'
    ? [503, { error: { message: 'Temporarily unavailable' } }] : wrote(DIAGRAM_DESCRIPTION) } })
  const made = await (await ask(app, reply({ text: DESCRIPTION_REPLY, describe_tables_and_diagrams: true }))).json()
  assert.equal((await settled(app, made.id)).body.status, 'ready')
  const said = app.gemini.seen.find(c => c.kind === 'tts').body.input[0].content[0].text
  assert.match(said, /A table of two rows, skipped\./)
  assert.ok(said.includes(DIAGRAM_DESCRIPTION))
  assert.equal(app.gemini.seen.filter(c => c.kind === 'brief').length, 2, 'descriptions must not hold the read with retries')
})

test('Full description timeout uses the legacy fallback without failing the read', async t => {
  const app = await rig(t, { descriptionTimeoutMs: 25 })
  app.gemini.hold('brief')
  const made = await (await ask(app, reply({ text: '| A | B |\n| - | - |\n| 1 | 2 |', describe_tables_and_diagrams: true }))).json()
  assert.equal((await settled(app, made.id)).body.status, 'ready')
  assert.equal(app.gemini.seen.find(c => c.kind === 'tts').body.input[0].content[0].text, 'A table of one row, skipped.')
  app.gemini.release('brief')
})

test('Brief identity and words are independent of the Full description setting', async t => {
  const app = await rig(t)
  const first = await (await ask(app, reply({ mode: 'brief', describe_tables_and_diagrams: true }))).json()
  assert.equal((await settled(app, first.id)).body.status, 'ready')
  const second = await ask(app, reply({ mode: 'brief', describe_tables_and_diagrams: false }))
  assert.equal(second.status, 200)
  assert.equal((await second.json()).id, first.id)
  assert.equal(app.gemini.seen.filter(c => c.kind === 'brief').length, 1)
})

test('a document full of stalled descriptions exhausts only the description budget, then reads with skips', async t => {
  const budget = new AbortController()
  const app = await rig(t, { descriptionBudgetSignal: () => budget.signal })
  app.gemini.hold('brief')
  const text = Array.from({ length: 10 }, (_, i) => `| Plan | Cost |\n| --- | --- |\n| Plan ${i} | ${i} |`).join('\n\n')
  const made = await (await ask(app, document({ text }))).json()
  // Expire the shared budget after the first batch starts, regardless of how
  // long cache I/O and loopback scheduling take on the test machine.
  await until(() => app.gemini.seen.filter(c => c.kind === 'brief').length === 3)
  budget.abort(new DOMException('Description budget expired', 'TimeoutError'))
  assert.equal((await settled(app, made.id)).body.status, 'ready')
  assert.equal(app.gemini.seen.filter(c => c.kind === 'brief').length, 3, 'only three calls run before the shared budget expires')
  assert.equal(app.gemini.seen.filter(c => c.kind === 'tts').map(c => c.body.input[0].content[0].text).join('\n\n'), speakable(text))
  app.gemini.release('brief')
})
