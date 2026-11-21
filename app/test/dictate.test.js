import { threadPage } from './thread-page.js'
// Dictation recorded whole, driven through the page: the mic pressed, a take
// recorded, the warning near the cap, the cap itself, the words back from the
// server, and both ways of keeping them.
//
// What is fixture is said plainly. There is no microphone and no audio: the
// recorder is a class this file hands the page, which answers the events a
// real `MediaRecorder` sends in the order it sends them, and its "audio" is a
// few made-up bytes. Gemini is a server on loopback that answers what this
// file tells it to. Time is the page's own `Date.now()`, moved forward by the
// test. What is proved is the page's behaviour and this app's route between
// them — not that a phone records, draws the meter, or buzzes.
//
// Its own process, because the page is a module with one instance and the
// recorder has to exist before that instance looks for one; `test/app.test.js`
// drives the browser's own speech recognition, which is what a page with no
// recorder falls back to. The recogniser `test/browser.js` installs is here
// too, so the Settings page's choice between the two can be pressed both ways. Its microphones are a list this file hands
// `enumerateDevices()`, nameless until the microphone has been allowed, as a
// browser's are.

import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { once } from 'node:events'

import { fakeBroker, appAgainst, closing, until } from './harness.js'
import { dictationApp } from './audio-servers.mjs'
import { browser, find, all, text } from './browser.js'
import { storage } from './browser.js'
import { createPreferences } from '../../plugins/_listed/dictation/page/settings.js'
import { pluginSettings } from '../public/plugin-loader.js'
const createStore = storage => createPreferences(pluginSettings('dictation', storage))
import { readFileSync } from 'node:fs'
import {
  RECORD_MAX_MS,
  RECORD_WARN_MS,
  recordMinutes,
  recordMaxMs,
  parseTermsNote,
  vaultTerms,
  VOCABULARY_MAX_TERMS,
  recordPhase,
  clock,
  recorderType,
  levelOf,
  pushLevel,
  LEVEL_BARS,
  HAPTICS,
  TONES,
  feel,
  patternMs,
  screenKeeper,
  properNouns,
  dictationVocabulary,
  mergeAt,
} from '../../plugins/_listed/dictation/page/dictate.js'

// ------------------------------------------------------------ the arithmetic

test('the cap is five minutes, and the last thirty seconds of it warn', () => {
  assert.equal(RECORD_MAX_MS, 5 * 60 * 1000)
  assert.equal(RECORD_WARN_MS, 30 * 1000)
  assert.equal(recordPhase(0), 'recording')
  assert.equal(recordPhase(4 * 60 * 1000 + 29_999), 'recording')
  assert.equal(recordPhase(4 * 60 * 1000 + 30_000), 'closing')
  assert.equal(recordPhase(RECORD_MAX_MS - 1), 'closing')
  assert.equal(recordPhase(RECORD_MAX_MS), 'over')
  // And a different cap, which is what Settings passes.
  assert.equal(recordPhase(50_000, 60_000), 'closing')
})

test('the longest take is whole minutes from one to fifteen, and anything else is five', () => {
  assert.equal(recordMaxMs(5), RECORD_MAX_MS)
  assert.equal(recordMaxMs(2), 120_000)
  assert.equal(recordMinutes(0), 1)
  assert.equal(recordMinutes(90), 15)
  assert.equal(recordMinutes(7.4), 7)
  for (const junk of [undefined, null, '', 'long', NaN, {}]) assert.equal(recordMinutes(junk), 5, String(junk))
})

// The note as a person might really keep it: frontmatter, headings, bullets
// of three kinds, a numbered line, a checkbox, commas, blank lines, a repeat in
// another case, and a line too long to be a name.
const NOTE_FIXTURE = readFileSync(new URL('./fixtures/dictation-terms.md', import.meta.url), 'utf8')

test('the terms note is read by its own rule: one a line, a comma is several, the rest ignored', () => {
  assert.deepEqual(parseTermsNote(NOTE_FIXTURE), [
    'Ada', // a bare line
    'Grace', // a line with a comma is two terms
    'Hopper',
    'Lovelace', // `- ` stripped
    'PiperBot', // `* ` stripped
    'Kestrel', // `+ ` stripped
    'Wispr Flow', // `1. ` stripped, inner spaces collapsed
    'Tessellate', // a checkbox's `- [ ] ` stripped
    'Obsidian', // two commas and a trailing one: three terms, one of them empty
    'Loam',
    'C# notes', // a `#` inside a line is not a heading
  ])
  // What was left out, and why: the frontmatter's `tags:` and `aliases:` lines,
  // the three headings, every blank line, `lovelace` (Lovelace already, in any case),
  // and a line over eighty characters.
  assert.deepEqual(parseTermsNote(''), [])
  assert.deepEqual(parseTermsNote(null), [])
  // Windows line ends read the same, and a `---` that is not the first line is
  // not frontmatter.
  assert.deepEqual(parseTermsNote('Ada\r\n---\r\nGrace\r\n'), ['Ada', '---', 'Grace'])
  // No more than Gemini takes.
  const many = Array.from({ length: 1200 }, (_, i) => `Term${i}`).join('\n')
  assert.equal(parseTermsNote(many).length, VOCABULARY_MAX_TERMS)
})

test('`%% %%` comments in the note are ignored, on one line or across several', () => {
  assert.deepEqual(parseTermsNote('Ada %%not this%%\n%%\nnor, these\n%%\nGrace'), ['Ada', 'Grace'])
})

test('the vault’s terms: the host’s parsed list as it is, once each and capped, or the note whole read by its rule', () => {
  // The host that parses the note: its order, its spelling, cleaned once more.
  assert.deepEqual(vaultTerms({ terms: ['Ada', ' Grace  Hopper ', 'ada', '', 7, 'x'.repeat(81), 'Loam'], source: 'Notes/Dictation terms.md' }), ['Ada', 'Grace Hopper', 'Loam'])
  const many = Array.from({ length: 1200 }, (_, i) => `Term${i}`)
  assert.equal(vaultTerms({ terms: many }).length, VOCABULARY_MAX_TERMS)
  // The host from 2026-09-30, which sends the note whole.
  assert.deepEqual(vaultTerms({ text: NOTE_FIXTURE }), parseTermsNote(NOTE_FIXTURE))
  assert.deepEqual(vaultTerms(null), [])
})

test('the merged terms: the person’s list first, the seed after it, once each, up to Gemini’s thousand', () => {
  const terms = dictationVocabulary({ agentName: 'Juno', agentId: 'juno', title: 'Loam deploy', extra: ['MuseBot', 'juno', 'Okafor'] })
  // Keep the person's original casing when a seed duplicates an existing term.
  assert.deepEqual(terms, ['MuseBot', 'juno', 'Okafor', 'Loam'])

  // A list longer than Gemini takes: every term sent is the person's, all
  // thousand of them — no header budget cuts it at about 380 any more — and
  // the seed is what gave way.
  const own = Array.from({ length: 1200 }, (_, i) => `Term${i}`)
  const long = dictationVocabulary({ agentName: 'Sable', extra: own })
  assert.equal(long.length, VOCABULARY_MAX_TERMS)
  assert.deepEqual(long, own.slice(0, VOCABULARY_MAX_TERMS))
})

test('the dictation setting is read whole and cleaned, and an unset one is the defaults', () => {
  const held = storage()
  const store = createStore(held)
  assert.deepEqual(store.dictation, { mode: 'record', maxMinutes: 5, deviceId: null, polish: 'light', terms: [] })
  store.dictation = { mode: 'browser', maxMinutes: 12, deviceId: 'mic-2', polish: 'full', terms: ['Juno', 7, ''] }
  assert.deepEqual(JSON.parse(held.getItem('loam-ui.dictation')), { mode: 'browser', maxMinutes: 12, deviceId: 'mic-2', polish: 'full', terms: ['Juno'] })
  held.setItem('loam-ui.dictation', JSON.stringify({ mode: 'shout', maxMinutes: 600, deviceId: '', polish: 'max', termsShown: true }))
  assert.deepEqual(createStore(held).dictation, { mode: 'record', maxMinutes: 15, deviceId: null, polish: 'light', terms: [] })
  // A setting from before Polish is Light, the default.
  held.setItem('loam-ui.dictation', JSON.stringify({ mode: 'record', maxMinutes: 5, deviceId: null, terms: [] }))
  assert.equal(createStore(held).dictation.polish, 'light')
  held.setItem('loam-ui.dictation', '{not json')
  assert.equal(createStore(held).dictation.mode, 'record')
})

test('the haptic language: three moments, three patterns, and a tone only at the two later ones', () => {
  assert.deepEqual(HAPTICS, { started: 40, closing: [150, 100, 150], capped: 500 })
  assert.equal(patternMs(HAPTICS.closing), 400)
  assert.deepEqual(TONES.closing.pattern, HAPTICS.closing)
  assert.deepEqual(TONES.capped.pattern, [HAPTICS.capped])
  assert.equal(TONES.started, undefined)

  const played = []
  const tone = (spec) => played.push(spec)
  // A buzz the browser takes is the whole of it.
  assert.equal(feel('closing', { vibrate: () => true, tone }), 'buzz')
  // A refused one, or none at all, is the tone — but never at the start.
  assert.equal(feel('closing', { vibrate: () => false, tone }), 'tone')
  assert.equal(feel('capped', { vibrate: null, tone }), 'tone')
  assert.equal(feel('capped', { vibrate: () => { throw new Error('no') }, tone }), 'tone')
  assert.equal(feel('started', { vibrate: () => false, tone }), null)
  assert.deepEqual(played, [TONES.closing, TONES.capped, TONES.capped])
  // Nothing to buzz and nothing to play: nothing, and no error.
  assert.equal(feel('capped'), null)
  assert.equal(feel('something else', { vibrate: () => true }), null)
})

test('the screen keeper: held, let go, asked again after the browser drops it, and a late grant let go', async () => {
  const granted = []
  let gate = null
  const lock = {
    request: async () => {
      if (gate) await gate
      const listeners = []
      const s = { released: false, addEventListener: (_, fn) => listeners.push(fn), release: async () => { s.released = true; listeners.forEach((fn) => fn()) } }
      granted.push(s)
      return s
    },
  }
  const tick = () => new Promise((resolve) => setTimeout(resolve, 0))
  const keeper = screenKeeper(lock)
  keeper.shown()
  await tick()
  assert.equal(granted.length, 0, 'shown() asked when nothing wanted it')
  keeper.hold()
  keeper.hold()
  await tick()
  assert.equal(granted.length, 1, 'held twice is one lock')
  assert.equal(keeper.held, true)
  // The browser drops it on hide; shown again, it is asked for again.
  await granted[0].release()
  assert.equal(keeper.held, false)
  keeper.shown()
  await tick()
  assert.equal(granted.length, 2)
  keeper.release()
  assert.equal(granted[1].released, true)
  assert.equal(keeper.held, false)
  // A take that ends while the request is still out lets the grant go on arrival.
  let open
  gate = new Promise((resolve) => (open = resolve))
  keeper.hold()
  keeper.release()
  open()
  await tick()
  await tick()
  assert.equal(granted.length, 3)
  assert.equal(granted[2].released, true, 'a grant that came after the take was kept')
  // No wake lock at all: nothing happens and nothing throws.
  const none = screenKeeper(undefined)
  none.hold()
  none.shown()
  none.release()
  assert.equal(none.held, false)
})

test('the clock reads minutes and seconds', () => {
  assert.equal(clock(0), '0:00')
  assert.equal(clock(7_400), '0:07')
  assert.equal(clock(271_000), '4:31')
  assert.equal(clock(RECORD_MAX_MS), '5:00')
  assert.equal(clock(-5), '0:00')
})

test('the recorder is asked for Opus in WebM first, and whatever the browser has after', () => {
  assert.equal(recorderType(() => true), 'audio/webm;codecs=opus')
  assert.equal(recorderType((t) => t === 'audio/mp4'), 'audio/mp4')
  assert.equal(recorderType(() => false), null)
  assert.equal(recorderType(undefined), null)
  assert.equal(recorderType(() => { throw new Error('no') }), null)
})

test('the meter: silence is flat, a loud slice is tall, and the oldest falls off', () => {
  assert.equal(levelOf(new Uint8Array(64).fill(128)), 0)
  assert.equal(levelOf(new Uint8Array(64).fill(255)) > 0.9, true)
  assert.equal(levelOf([]), 0)
  let levels = []
  for (let i = 0; i < 40; i += 1) levels = pushLevel(levels, i, 28)
  assert.equal(levels.length, 28)
  assert.equal(levels.at(-1), 39)
})

test('a name is a capital away from the start of a sentence, a capital inside a word, or an acronym', () => {
  assert.deepEqual(properNouns('The deploy that Juno ran broke MuseBot. Then we moved to the API.'), ['Juno', 'MuseBot', 'API'])
  // Sentence-initial capitals are grammar, not names; code and links are not speech.
  assert.deepEqual(properNouns('Yesterday it worked. Now `FooBar` fails at https://Example.com/X'), [])
  assert.deepEqual(properNouns('ask Scout’s view'), ['Scout'])
})

test('the terms: the person’s own first, then the agent, the title and the names said lately', () => {
  const terms = dictationVocabulary({
    agentName: 'Juno',
    agentId: 'juno',
    title: 'Loam deploy with GrokBot',
    texts: ['we asked Sable once', 'then MuseBot and MuseBot again', 'and MuseBot said Sable was right'],
    extra: ['Wispr'],
  })
  assert.deepEqual(terms.slice(0, 4), ['Wispr', 'Juno', 'GrokBot', 'Loam'])
  // MuseBot is said three times and lately; Sable twice.
  assert.deepEqual(terms.slice(4), ['MuseBot', 'Sable'])
  assert.deepEqual(dictationVocabulary(), [])
  assert.equal(dictationVocabulary({ texts: [Array.from({ length: 300 }, (_, i) => `x Name${i}x`).join(' ')] }).length <= 100, true)
})

test('words go in at the caret, beside what was typed, and replace none of it', () => {
  assert.deepEqual(mergeAt('', null, 'Hello there.'), { md: 'Hello there.', caret: 12 })
  assert.deepEqual(mergeAt('Draft so far', null, 'and more'), { md: 'Draft so far and more', caret: 21 })
  assert.deepEqual(mergeAt('see you tomorrow', 8, 'at the office'), { md: 'see you at the office tomorrow', caret: 21 })
  assert.deepEqual(mergeAt('line one\n', null, 'two'), { md: 'line one\ntwo', caret: 12 })
  assert.deepEqual(mergeAt('keep', 2, '   '), { md: 'keep', caret: 2 })
  // Text only after the caret: one space joins them, on the words' side.
  assert.deepEqual(mergeAt('tomorrow then', 0, 'See you'), { md: 'See you tomorrow then', caret: 7 })
  // No doubled spaces: a space already there on either side is the join.
  assert.deepEqual(mergeAt('see you ', null, 'soon'), { md: 'see you soon', caret: 12 })
  assert.deepEqual(mergeAt('see  you', 4, 'you'), { md: 'see you you', caret: 7 })
  // What comes back cleaned may end in a line break; it never lands with one.
  assert.deepEqual(mergeAt('', null, 'Hello there.\n'), { md: 'Hello there.', caret: 12 })
  assert.deepEqual(mergeAt('ends', null, '  more words\n\n'), { md: 'ends more words', caret: 15 })
})

// ------------------------------------------------------------------- the page

const line = (obj) => `data: ${JSON.stringify(obj)}\n\n`
const json = (res, body, status = 200) => {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}

const RECORD = {
  session_id: 'chat-1',
  agent: 'juno',
  provider: 'claude-code',
  workspace: '/w/juno',
  created: '2026-09-25T10:00:00Z',
  last_activity: '2026-09-25T11:00:00Z',
  last_event_at: '2026-09-25T11:00:00Z',
  state: 'running',
  status: 'done',
  title: 'Loam deploy notes',
  last_message: 'Deployed.',
  pinned: false,
  archived: false,
  record_seq: 3,
  seen_seq: 3,
  background_tasks: 0,
  pending_permissions: [],
  turns: [],
}

const posted = []
// The vault's terms as the host answers them. `null` is a host older than the
// route, which says `405`; a string is a 404's words — a host without the
// route yet, or no note; an object is the JSON answer. `NOTE` is the host that
// parses the note and sends its terms; `WHOLE_NOTE` is the host of
// 2026-09-30, which sends the note whole for the page to read.
const NOTE = {
  terms: parseTermsNote(NOTE_FIXTURE),
  source: 'Notes/Dictation terms.md',
}
const WHOLE_NOTE = {
  path: 'System/Dictation terms.md',
  obsidian_url: 'obsidian://open?vault=notes&file=System%2FDictation%20terms',
  modified: new Date(Date.now() - 2 * 3600_000).toISOString(),
  text: NOTE_FIXTURE,
}
let termsNote = NOTE
const routes = {
  'GET /v1/agents': (req, res) => json(res, { agents: [{ id: 'juno', name: 'Juno', emoji: null }] }),
  'GET /v1/providers': (req, res) => json(res, { providers: [] }),
  'GET /v1/limits': (req, res) => json(res, { attachment_max_bytes: 10 * 1024 * 1024, attachment_types: [] }),
  'GET /v1/sessions': (req, res) => json(res, { sessions: [RECORD], live: 1, cap: 4 }),
  'GET /v1/sessions/events': (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(': open ping=15000\n\n')
    res.write(line({ type: 'broker.session', ...RECORD }))
  },
  'GET /v1/sessions/chat-1/events': (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(': open ping=15000\n\n')
    res.write(line({ type: 'broker.session', ...RECORD, window_first_seq: 1, turns_before: 0 }))
    res.write(line({ type: 'broker.user_message', seq: 1, session_id: 'chat-1', text: 'did MuseBot pick up the deploy?' }))
    res.write(
      line({
        type: 'assistant',
        seq: 2,
        session_id: 'chat-1',
        message: { id: 'm1', role: 'assistant', model: 'x', content: [{ type: 'text', text: 'Yes, and Sable checked it. Everything is ready for the next step.' }] },
      }),
    )
    res.write(line({ type: 'result', seq: 3, session_id: 'chat-1', subtype: 'success', duration_ms: 1, num_turns: 1, usage: {} }))
  },
  'POST /v1/sessions/chat-1/messages': (req, res, url, body) => {
    posted.push(JSON.parse(body || '{}'))
    json(res, { accepted: true, seq: 3 + posted.length })
  },
  'PATCH /v1/sessions/chat-1': (req, res) => json(res, RECORD),
}
threadPage(routes, { agents: [{ id: RECORD.agent, name: 'Juno', emoji: null }], initial: [{ id: 'chat-1', title: RECORD.title, persona: RECORD.agent, text: 'MuseBot picked up the deploy. Yes, and Sable checked it. Everything is ready for the next step.' }], sent: posted })
const broker = await fakeBroker(routes)

// Dictation's own server answers the terms note at `/x/dictation/terms`
// (1.1.0); here a stand-in does, from \`termsNote\`, counting each ask, while
// \`/x/dictation/transcribe\` goes to the real plugin server further down.
const termsAnswer = (req, res) => {
    if (termsNote === null) {
      res.writeHead(405, { 'content-type': 'application/json' })
      return res.end(JSON.stringify({ error: 'no route' }))
    }
    if (typeof termsNote === 'string') {
      res.writeHead(404, { 'content-type': 'application/json', 'cache-control': 'no-store' })
      return res.end(JSON.stringify({ error: termsNote }))
    }
    res.writeHead(termsNote.error ? 404 : 200, { 'content-type': 'application/json', 'cache-control': 'no-store' })
    res.end(JSON.stringify(termsNote))
  }
const termsSeen = []
const termsServer = http.createServer((req, res) => { termsSeen.push(req.url); termsAnswer(req, res) })
termsServer.listen(0, '127.0.0.1')
await once(termsServer, 'listening')
const termsAsked = () => termsSeen.length

// Gemini, answering from a queue this file fills. A polish request — the one
// with a system instruction — is kept apart, in `polishes`, and answered from
// `polishAnswers` or else with the transcript it was sent, unchanged, which
// the guard lets through: every other test here runs at the default Light and
// sees its words land as they were transcribed.
const answers = []
const heard = []
const polishes = []
const polishAnswers = []
const gemini = http.createServer(async (req, res) => {
  const chunks = []
  for await (const c of req) chunks.push(c)
  const asked = JSON.parse(Buffer.concat(chunks).toString('utf8'))
  if (asked.system_instruction) {
    polishes.push(asked)
    const sent = /<transcript>\n([\s\S]*)\n<\/transcript>$/.exec(asked.input)[1]
    const [status, answer] = polishAnswers.shift() ?? words(sent)
    return json(res, answer, status)
  }
  heard.push(asked)
  // Held while a test looks at the take being cleaned; `release()` lets it go.
  await gate
  const [status, body] = answers.shift() ?? [500, { error: { message: 'no answer queued' } }]
  json(res, body, status)
})
gemini.listen(0, '127.0.0.1')
await once(gemini, 'listening')
let gate = null
let open = null
const hold = () => {
  gate = new Promise((resolve) => (open = resolve))
}
const release = () => {
  open?.()
  gate = null
}
const words = (text) => [200, { status: 'completed', steps: [{ type: 'model_output', content: [{ type: 'text', text }] }] }]

const app = await appAgainst(broker, {})
const dictation = await dictationApp({ GEMINI_API_KEY: 'test-gemini-key', GEMINI_API_URL: `http://127.0.0.1:${gemini.address().port}` })
const page = browser(app.url, { plugin: (id, rest) => id !== 'dictation' ? null
  : rest === 'terms' ? `http://127.0.0.1:${termsServer.address().port}/terms` : `${dictation.url}/${rest}` })

// The browser's recorder, its microphone, its buzzer and its wake lock.
const buzzes = []
let vibrates = true // what `vibrate()` answers: `false` is Chrome refusing
// Every wake lock the page asked for, in order; `held()` is the ones not yet
// let go. `sentinel.drop()` is the browser letting one go by itself.
const locks = []
const wakeLock = {
  request: async (type) => {
    const listeners = []
    const sentinel = {
      type,
      released: false,
      by: null,
      addEventListener: (name, fn) => name === 'release' && listeners.push(fn),
      release: async () => {
        if (sentinel.released) return
        sentinel.released = true
        sentinel.by ??= 'page'
        for (const fn of listeners) fn()
      },
      drop: () => {
        sentinel.by = 'browser'
        return sentinel.release()
      },
    }
    locks.push(sentinel)
    return sentinel
  },
}
const heldLocks = () => locks.filter((l) => !l.released)
let permission = 'granted'
let allowed = false // whether the microphone has been allowed once, which is what names the devices
const tracks = []
const asked = [] // every set of constraints getUserMedia was called with
const MICS = [
  { kind: 'audioinput', deviceId: 'default', label: 'Default - Desk mic' },
  { kind: 'audioinput', deviceId: 'mic-1', label: 'Desk mic' },
  { kind: 'audioinput', deviceId: 'mic-2', label: 'Headset' },
  { kind: 'audiooutput', deviceId: 'spk-1', label: 'Speakers' },
]
class FakeRecorder {
  static deferStop = false
  constructor(stream, options) {
    this.stream = stream
    this.options = options
    this.mimeType = options.mimeType ?? ''
    this.state = 'inactive'
    this.listeners = new Map()
    FakeRecorder.last = this
  }

  static isTypeSupported(type) {
    return type === 'audio/webm;codecs=opus'
  }

  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, [])
    this.listeners.get(type).push(fn)
  }

  emit(type, event = {}) {
    for (const fn of this.listeners.get(type) ?? []) fn(event)
  }

  start(slice) {
    this.slice = slice
    this.state = 'recording'
  }

  stop() {
    this.state = 'inactive'
    // The browser delivers the final bytes and stop event in a later task.
    if (!FakeRecorder.deferStop) setTimeout(() => this.finishStop(), 0)
  }

  finishStop() {
    this.emit('dataavailable', { data: new Blob([Uint8Array.from([0x1a, 0x45, 0xdf, 0xa3, 7, 7, 7])], { type: this.mimeType }) })
    this.emit('stop')
  }
}
globalThis.MediaRecorder = FakeRecorder
const audioSession = { type: 'auto' }
Object.defineProperty(globalThis, 'navigator', {
  value: {
    audioSession,
    language: 'en-GB',
    vibrate: (pattern) => {
      buzzes.push(pattern)
      return vibrates
    },
    wakeLock,
    mediaDevices: {
      getUserMedia: async (constraints) => {
        if (permission !== 'granted') {
          const err = new Error('Permission denied')
          err.name = 'NotAllowedError'
          throw err
        }
        const exact = constraints.audio?.deviceId?.exact
        if (exact && !MICS.some((d) => d.deviceId === exact)) {
          const err = new Error('No such device')
          err.name = 'OverconstrainedError'
          asked.push(constraints)
          throw err
        }
        asked.push(constraints)
        allowed = true
        const track = { stopped: false, stop() { this.stopped = true } }
        tracks.push(track)
        return { getTracks: () => [track] }
      },
      // Before the microphone is allowed a browser lists one nameless input
      // with no id — Chrome's way — and after it, every device by name.
      enumerateDevices: async () =>
        allowed ? MICS.map((d) => ({ ...d })) : [{ kind: 'audioinput', deviceId: '', label: '' }],
    },
  },
  configurable: true,
})

// Playback is a stand-in too. It refuses a recording category or live track,
// letting the test distinguish a play call from one that can start playback.
class FakeAudio extends EventTarget {
  constructor() {
    super()
    this.paused = true
    this.ended = false
    this.currentTime = 0
    this.duration = 60
    this.buffered = { length: 0 }
    this.plays = 0
    FakeAudio.last = this
  }
  play() {
    this.plays++
    assert.ok(tracks.every((track) => track.stopped), 'playback still holds a mic track')
    assert.equal(audioSession.type, 'playback', 'playback still uses the recording category')
    this.paused = false
    this.dispatchEvent(new Event('play'))
    return Promise.resolve()
  }
  pause() {
    this.paused = true
    this.dispatchEvent(new Event('pause'))
  }
  removeAttribute() {}
  load() {}
}
globalThis.Audio = FakeAudio
globalThis.localStorage.setItem('loam-ui.speech', JSON.stringify({ listenFrom: 10 }))
const passFetch = globalThis.fetch
globalThis.fetch = async (url, options) => {
  if (url === '/x/read-aloud/status') return Response.json({ speech: true, reason: null })
  if (url === '/x/read-aloud/speech/lookup' || url === '/x/read-aloud/speech') {
    return Response.json({ id: 'fixture-audio', status: 'ready', duration_s: 60, url: '/x/read-aloud/speech/fixture-audio/audio.mp3' })
  }
  return passFetch(url, options)
}

// The page's clock, which the test moves.
const realNow = Date.now
let skew = 0
Date.now = () => realNow() + skew

page.watch()
await import('../public/app.js')
page.unwatch()

test.after(async () => {
  Date.now = realNow
  page.stop()
  await closing(broker, app, dictation)()
  await new Promise((resolve) => termsServer.close(resolve))
  gemini.closeAllConnections?.()
  await new Promise((resolve) => gemini.close(resolve))
  setImmediate(() => process.exit(process.exitCode ?? 0))
})

const root = page.root
const box = () => find(root, 'composer-box')
const field = () => find(box(), 'draft-row').childNodes.find((node) => node.className === 'draft')
const mic = () => find(root, 'roundmic')
const take = () => find(root, 'take')
const takeClass = () => take()?.getAttribute('class') ?? null
const clockSays = () => text(find(root, 'take-clock'))
const keep = () => find(root, 'keep')
const discard = () => find(root, 'discard')
const notice = () => find(root, 'takenote')
const offer = () => (notice() ? find(notice(), 'noticerow-act') : null)
const row = () => find(root, 'composer-actions')
// Every class the card above the composer was drawn with, until 2026-09-29.
const OLD_CARD = ['recorder', 'rectake', 'rectext', 'recerror', 'recacts', 'recbtn', 'recx', 'recdot', 'recclock', 'recsay', 'levels', 'listening']
const oldCard = () => OLD_CARD.filter((cls) => find(root, cls))
const settle = () => new Promise((resolve) => setTimeout(resolve, 60))

async function openChat() {
  await until(() => all(root, 'srow').length > 0)
  all(root, 'srow')[0].fire('click')
  await until(() => find(root, 'composer'))
  // The transcript is what the take is biased towards, so it has to be on the
  // page before a take is sent — not merely the scroller it will be drawn in.
  await until(() => /Sable checked it/.test(text(find(root, 'tt-stream')) ?? ''))
}

// The row under the field while a take is going: every control in it disabled,
// the send gone. `null` is a row at rest.
function quietRow() {
  const r = row()
  if (!/\bquiet\b/.test(r.getAttribute('class'))) return null
  return {
    disabled: ['plusbtn', 'modelbtn', 'roundmic'].filter((cls) => find(r, cls)?.hasAttribute('disabled')),
    send: find(r, 'roundsend') !== null,
  }
}

test('a take is drawn in the field: the clock, the bars, ✕ and ✓, and the row under it quiet', async () => {
  await openChat()
  const f = field()
  f.value = 'Draft so far'
  f.fire('input')

  assert.ok(mic(), 'a browser that can record draws a mic')
  mic().fire('click')
  await until(() => FakeRecorder.last?.state === 'recording')
  const rec = FakeRecorder.last
  assert.equal(rec.options.mimeType, 'audio/webm;codecs=opus')
  assert.equal(rec.options.audioBitsPerSecond, 32000)
  assert.deepEqual(asked.at(-1), { audio: true }, 'with nothing chosen, the system default is what is asked for')

  // Row one is the take, in the field's own row — and the field is still there,
  // hidden and holding what was typed.
  await until(() => take())
  const rowOne = find(box(), 'draft-row')
  assert.equal(rowOne.getAttribute('class'), 'draft-row taking')
  assert.deepEqual(
    rowOne.childNodes.map((n) => n.getAttribute('class')),
    ['draft', 'take record recording'],
    'row one is not the hidden field and the take',
  )
  assert.equal(field(), f, 'the field was rebuilt under the take')
  assert.equal(f.value, 'Draft so far')
  assert.deepEqual(
    take().childNodes.map((n) => n.getAttribute('class')),
    ['take-clock', 'take-bars', 'take-controls'],
  )
  assert.equal(clockSays(), '0:00')
  assert.equal(find(root, 'take-bars').childNodes.length, LEVEL_BARS, 'the bars do not fill the row')
  // Silence is a row of low bars, not an empty field.
  assert.ok(find(root, 'take-bars').childNodes.every((bar) => bar.style.height === '3px'))
  assert.deepEqual(
    find(root, 'take-controls').childNodes.map((b) => [b.getAttribute('class'), b.getAttribute('title')]),
    [['takebtn discard', 'Discard'], ['takebtn keep', 'Keep']],
  )
  assert.equal(find(root, 'expandbtn'), null, 'the expand glyph sat on top of the take')
  assert.deepEqual(quietRow(), { disabled: ['plusbtn', 'modelbtn', 'roundmic'], send: false })
  assert.deepEqual(oldCard(), [], 'the old card is still drawn')
  // The mic is live: one short tap, and the screen held on.
  assert.deepEqual(buzzes, [HAPTICS.started], 'the start was not one tap, or something else buzzed')
  await until(() => heldLocks().length === 1)
  assert.equal(heldLocks()[0].type, 'screen')

  // Thirty seconds out: the warning colour, and two firm pulses.
  skew = RECORD_MAX_MS - RECORD_WARN_MS + 1000
  await until(() => takeClass() === 'take record recording closing')
  assert.equal(clockSays(), '4:31')
  await new Promise((resolve) => setTimeout(resolve, 250))
  assert.deepEqual(buzzes, [HAPTICS.started, HAPTICS.closing], 'the near-cap pulses came more than once, or not at all')

  // The cap is ✓: it stops itself, the microphone is let go, and the take goes
  // to be cleaned — shown in the same row until Gemini answers.
  hold()
  answers.push(words('Hello Juno, the MuseBot deploy is done.'))
  skew = RECORD_MAX_MS
  await until(() => rec.state === 'inactive')
  assert.deepEqual(buzzes, [HAPTICS.started, HAPTICS.closing, HAPTICS.capped])
  assert.equal(tracks.at(-1).stopped, true, 'the microphone was left open')
  await until(() => heldLocks().length === 0)
  assert.equal(locks.at(-1).by, 'page', 'the cap left the screen held on')
  await until(() => takeClass() === 'take record cleaning')
  assert.equal(clockSays(), 'Cleaning…')
  assert.deepEqual(
    find(root, 'take-controls').childNodes.map((b) => [b.getAttribute('class'), b.getAttribute('title')]),
    [['takebtn discard', 'Cancel']],
    'cleaning is one ✕ and nothing else',
  )
  assert.ok(find(root, 'take-bars').childNodes.every((bar) => bar.style.height === '4px'), 'the bars did not settle')
  assert.deepEqual(quietRow(), { disabled: ['plusbtn', 'modelbtn', 'roundmic'], send: false })
  await until(() => heard.length === 1)
  assert.equal(field().value, 'Draft so far', 'the field changed before the words came back')

  // What went to Gemini: the audio as it was recorded, biased towards who and
  // what this chat is about.
  const sent = heard.at(-1)
  assert.equal(sent.input[0].mime_type, 'audio/webm')
  assert.deepEqual(Buffer.from(sent.input[0].data, 'base64'), Buffer.from([0x1a, 0x45, 0xdf, 0xa3, 7, 7, 7]))
  const terms = sent.generation_config.transcription_config.custom_vocabulary
  for (const term of ['Juno', 'Loam', 'MuseBot', 'Sable']) assert.ok(terms.includes(term), `${term} was not among the terms: ${terms}`)
  assert.equal(sent.generation_config.transcription_config.mode, 'smart')

  // Landed: the field is a field again, with the words in it after what was
  // typed, and nothing else on the screen — no card, no second action.
  // Hold deferred zero-delay UI work until after Send. Under load the send can
  // beat caret restoration; reproducing that ordering must not depend on which
  // of the suite's other processes happens to get CPU time first.
  const deferred = []
  let holding = true
  const nativeTimeout = globalThis.setTimeout
  globalThis.setTimeout = (callback, delay, ...args) => delay === 0
    ? nativeTimeout(() => holding ? deferred.push(() => callback(...args)) : callback(...args), delay)
    : nativeTimeout(callback, delay, ...args)
  try {
    release()
    await until(() => !take() && deferred.length > 0)
    assert.equal(field().value, 'Draft so far Hello Juno, the MuseBot deploy is done.')
    assert.equal(find(box(), 'draft-row').getAttribute('class'), 'draft-row')
    assert.equal(quietRow(), null)
    assert.equal(find(root, 'roundsend').hasAttribute('disabled'), false, 'the send did not wake for the words')
    assert.equal(mic().hasAttribute('disabled'), false)
    assert.equal(notice(), null)
    assert.deepEqual(oldCard(), [], 'a node of the old card came back with the words')
    assert.equal(posted.length, 0, 'the words were sent by themselves')
    skew = 0

    // And the send is the ordinary send.
    find(root, 'composer').fire('submit', { preventDefault: () => {} })
    await until(() => posted.length === 1 && field().value === '')
    assert.equal(posted[0].text, 'Draft so far Hello Juno, the MuseBot deploy is done.')
    for (const callback of deferred.splice(0)) callback()
    assert.equal(field().value, '', 'deferred caret restoration put the sent draft back')
  } finally {
    holding = false
    globalThis.setTimeout = nativeTimeout
    for (const callback of deferred.splice(0)) callback()
    skew = 0
  }
})

test('✓ and Cancel stop mic tracks synchronously, before the recorder stop event', async () => {
  if (!find(root, 'composer')) await openChat()
  FakeRecorder.deferStop = true
  try {
    for (const action of [keep, discard]) {
      mic().fire('click')
      await until(() => FakeRecorder.last?.state === 'recording' && keep())
      const recorder = FakeRecorder.last
      try {
        action().fire('click')
        assert.equal(tracks.at(-1).stopped, true, 'the action retained the microphone until the stop event')
        assert.equal(audioSession.type, 'playback')
      } finally {
        // Cancel before delivering bytes, so this check sends no transcription.
        discard()?.fire('click')
        recorder.finishStop()
        await until(() => !take())
      }
    }
  } finally {
    FakeRecorder.deferStop = false
  }
})

test('✓ releases capture before final bytes; Play starts during pending transcription and survives text landing', async () => {
  class MeterContext {
    constructor() { MeterContext.last = this }
    createMediaStreamSource() {
      return { connect() {}, disconnect: () => { this.disconnected = true } }
    }
    createAnalyser() { return { getByteTimeDomainData: (b) => b.fill(128), disconnect() {} } }
    close() { this.closed = true; return Promise.resolve() }
  }
  globalThis.AudioContext = MeterContext
  const f = field()
  f.value = ''; f.fire('input')
  let focused = 0
  const focus = f.focus
  f.focus = function () { focused++; return focus?.call(this) }
  try {
    find(root, 'speakbtn').fire('click')
    await until(() => find(root, 'setrow'))
    find(root, 'setrow').fire('click')
    await until(() => FakeAudio.last && !FakeAudio.last.paused && find(root, 'player-play'))
    const audio = FakeAudio.last
    audio.currentTime = 12

    mic().fire('click')
    await until(() => FakeRecorder.last?.state === 'recording' && keep())
    const recorder = FakeRecorder.last
    const context = MeterContext.last
    assert.equal(audio.paused, true, 'the mic must pause read-aloud')
    assert.equal(audio.currentTime, 12, 'the mic lost playback position')
    assert.equal(audioSession.type, 'play-and-record')
    const plays = audio.plays
    find(root, 'player-play').fire('click')
    assert.equal(audio.plays, plays, 'Play interrupted an active recording')

    FakeRecorder.deferStop = true
    hold()
    answers.push(words('Words arrive while the reply plays.'))
    const requests = heard.length
    keep().fire('click')
    // Same task as ✓: no stop event and no final bytes have been delivered.
    assert.equal(tracks.at(-1).stopped, true)
    assert.equal(heldLocks().length, 0)
    assert.equal(context.closed, true)
    assert.equal(context.disconnected, true)
    assert.equal(audioSession.type, 'playback')
    assert.equal(heard.length, requests)
    find(root, 'player-play').fire('click')
    assert.equal(audio.paused, false, 'Play waited for the recorder stop event')
    recorder.finishStop()
    await until(() => heard.length === requests + 1 && takeClass() === 'take record cleaning')

    // Transcription is still blocked at the fixture server. A fresh play tap
    // must call play synchronously; resolving the request must not call it.
    audio.pause()
    await until(() => find(root, 'player-play')?.getAttribute('title') === 'Play')
    find(root, 'player-play').fire('click')
    assert.equal(audio.paused, false)
    assert.equal(f.value, '')
    const beforeLanding = audio.plays
    const beforeFocus = focused
    release()
    await until(() => !take() && focused > beforeFocus)
    assert.equal(f.value, 'Words arrive while the reply plays.')
    assert.equal(audio.paused, false, 'text landing or field focus paused read-aloud')
    assert.equal(audio.plays, beforeLanding, 'text landing restarted read-aloud')
    assert.equal(audio.currentTime, 12)
    assert.equal(audioSession.type, 'playback')

    // The next recording still wins. Cancel releases capture in its own task.
    mic().fire('click')
    await until(() => FakeRecorder.last !== recorder && keep())
    assert.equal(audio.paused, true)
    const cancelled = FakeRecorder.last
    // Cancel in the gap after ✓, with final bytes still pending. Capture has
    // already been released, so the late stop must also check take ownership.
    keep().fire('click')
    await until(() => takeClass() === 'take record cleaning')
    discard().fire('click')
    assert.equal(tracks.at(-1).stopped, true)
    assert.equal(MeterContext.last.closed, true)
    assert.equal(audioSession.type, 'playback')
    await until(() => !take())
    find(root, 'player-play').fire('click')
    assert.equal(audio.paused, false, 'Cancel retained the audio hold')

    // A cancelled recorder's late stop must not release the next one's session.
    mic().fire('click')
    await until(() => FakeRecorder.last !== cancelled && keep())
    const next = FakeRecorder.last
    cancelled.finishStop()
    assert.equal(audioSession.type, 'play-and-record')
    assert.equal(tracks.at(-1).stopped, false)
    await settle()
    assert.equal(takeClass(), 'take record recording', 'old final bytes replaced a newer take')
    discard().fire('click')
    next.finishStop()
    await until(() => !take())
    assert.equal(heard.length, requests + 1, 'a cancelled recording was transcribed')
    find(root, 'player-close').fire('click')
    f.value = ''
    f.fire('input')
  } finally {
    release()
    FakeRecorder.deferStop = false
    f.focus = focus
    delete globalThis.AudioContext
  }
})

test('recording still works when the optional audio session API is absent or refuses changes', async () => {
  try {
    for (const session of [undefined, { set type(_) { throw new Error('unsupported') } }]) {
      globalThis.navigator.audioSession = session
      mic().fire('click')
      await until(() => FakeRecorder.last?.state === 'recording' && keep())
      discard().fire('click')
      assert.equal(tracks.at(-1).stopped, true)
      await until(() => !take())
      // Let the old recorder's asynchronous event arrive before the next take.
      await settle()
    }
  } finally {
    globalThis.navigator.audioSession = audioSession
  }
})

test('the words go in at the caret: before text, into the middle of it, and into an empty field', async () => {
  const f = field()
  // Into the middle, at a caret the page reads from the selection at the press.
  f.value = 'see you tomorrow'
  f.fire('input')
  const textNode = (function first(node) {
    if (node.nodeType === 3) return node
    for (const child of node.childNodes ?? []) {
      const hit = first(child)
      if (hit) return hit
    }
    return null
  })(f)
  assert.ok(textNode, 'the field drew no text')
  globalThis.getSelection = () => ({ focusNode: textNode, focusOffset: 8 })
  answers.push(words('at the office'))
  mic().fire('click')
  await until(() => FakeRecorder.last?.state === 'recording' && keep())
  delete globalThis.getSelection
  keep().fire('click')
  await until(() => !take())
  assert.equal(f.value, 'see you at the office tomorrow')

  // An empty field: the words and nothing else — no space before, no line after.
  f.value = ''
  f.fire('input')
  answers.push(words('Hello there.\n'))
  mic().fire('click')
  await until(() => keep())
  keep().fire('click')
  await until(() => !take())
  assert.equal(f.value, 'Hello there.')
  f.value = ''
  f.fire('input')
})

test('✕ while recording, ✕ while cleaning and Escape all leave the field as it was, and nothing lands', async () => {
  const f = field()
  f.value = 'typed before'
  f.fire('input')

  // ✕ while recording: never sent.
  const asking = heard.length
  mic().fire('click')
  await until(() => FakeRecorder.last?.state === 'recording' && discard())
  const rec = FakeRecorder.last
  discard().fire('click')
  await until(() => !take())
  assert.equal(rec.state, 'inactive')
  assert.equal(tracks.at(-1).stopped, true)
  await settle()
  assert.equal(heard.length, asking, 'a discarded take went to Gemini')
  assert.equal(f.value, 'typed before')
  assert.equal(quietRow(), null)

  // Escape is ✕.
  mic().fire('click')
  await until(() => FakeRecorder.last !== rec && FakeRecorder.last?.state === 'recording' && take())
  const escaped = FakeRecorder.last
  assert.equal(page.press('Escape').defaultPrevented, true, 'the page let Escape through')
  await until(() => !take())
  assert.equal(escaped.state, 'inactive')
  await settle()
  assert.equal(heard.length, asking, 'an escaped take went to Gemini')
  assert.equal(f.value, 'typed before')

  // ✕ while cleaning: the answer that comes back afterwards lands nowhere.
  hold()
  answers.push(words('too late'))
  mic().fire('click')
  await until(() => keep())
  keep().fire('click')
  await until(() => takeClass() === 'take record cleaning')
  // Gemini has the take before it is cancelled. On a loaded box the request can
  // still be on its way through the relay when ✕ lands, and a request that never
  // arrives never takes its answer off the queue — which then answers the next
  // test's take, and every test after that is one answer out (a landing,
  // 2026-10-06: fourteen failures from this one).
  await until(() => heard.length === asking + 1)
  discard().fire('click')
  await until(() => !take())
  release()
  await until(() => answers.length === 0)
  await settle()
  assert.equal(f.value, 'typed before', 'the words of a cancelled take landed')
  assert.equal(notice(), null)
  f.value = ''
  f.fire('input')
})

test('a take that fails: the field is kept, the quiet line offers one Retry, and a second failure offers none', async () => {
  const f = field()
  f.value = 'Keep me'
  f.fire('input')

  answers.push([500, { error: { message: 'Internal error' } }])
  mic().fire('click')
  await until(() => keep() && FakeRecorder.last?.state === 'recording')
  const before = buzzes.length
  keep().fire('click')
  await until(() => notice())
  assert.match(text(notice()), /Gemini could not transcribe this .*Retry/)
  assert.equal(take(), null)
  assert.equal(f.value, 'Keep me', 'a failed take touched the field')
  assert.equal(buzzes.length, before, 'a manual stop buzzed')
  assert.deepEqual(oldCard(), [])

  // Retry is the same take once more.
  const sent = heard.length
  answers.push(words('Ship it tonight.'))
  offer().fire('click')
  await until(() => !take() && f.value !== 'Keep me')
  assert.equal(heard.length, sent + 1)
  assert.deepEqual(heard.at(-1).input, heard.at(-2).input, 'the retry did not send the same take again')
  assert.equal(f.value, 'Keep me Ship it tonight.')
  assert.equal(notice(), null)

  // Failing twice: the second says so and offers nothing more.
  answers.push([500, { error: { message: 'Internal error' } }])
  answers.push([500, { error: { message: 'Internal error' } }])
  mic().fire('click')
  await until(() => keep())
  keep().fire('click')
  await until(() => offer())
  offer().fire('click')
  await until(() => notice() && !offer())
  assert.match(text(notice()), /Gemini could not transcribe this/)
  assert.equal(f.value, 'Keep me Ship it tonight.')

  // Any other action drops it: here, typing.
  f.value = 'Keep me Ship it tonight!'
  f.fire('input')
  await until(() => !notice())
  f.value = ''
  f.fire('input')
})

test('a take with no words in it says so, and Try again is a new take', async () => {
  answers.push(words(''))
  mic().fire('click')
  await until(() => keep())
  const first = FakeRecorder.last
  keep().fire('click')
  await until(() => notice())
  assert.equal(text(notice()), 'Heard no words · Try again')
  assert.equal(field().value, '')
  offer().fire('click')
  await until(() => FakeRecorder.last !== first && FakeRecorder.last?.state === 'recording' && take())
  assert.equal(notice(), null)
  discard().fire('click')
  await until(() => !take())
})

test('a microphone that is not allowed says so on the quiet line, and the field is untouched', async () => {
  const f = field()
  f.value = 'still here'
  f.fire('input')
  permission = 'denied'
  mic().fire('click')
  await until(() => notice())
  assert.match(text(notice()), /not allowed/)
  assert.equal(offer(), null, 'there is no take to try again')
  assert.equal(take(), null)
  assert.equal(quietRow(), null)
  assert.equal(f.value, 'still here')
  permission = 'granted'
  f.value = ''
  f.fire('input')
  await until(() => !notice())
})

test('in the full-screen editor the take stands in the editor’s field, and the words land there', async () => {
  const f = field()
  f.value = 'A long brief'
  f.fire('input')
  await until(() => /\bon\b/.test(find(root, 'expandbtn')?.getAttribute('class') ?? ''))
  find(root, 'expandbtn').fire('click')
  const editor = await until(() => find(root, 'editor'))
  const foot = find(editor, 'editor-foot')
  assert.ok(find(foot, 'roundmic'), 'the editor has no mic')

  answers.push(words('with a second paragraph'))
  find(foot, 'roundmic').fire('click')
  await until(() => find(editor, 'take'))
  assert.equal(find(editor, 'editor-body').getAttribute('class'), 'editor-body taking')
  assert.equal(find(find(root, 'composer-box'), 'take'), null, 'the take was drawn in the composer under the editor too')
  assert.equal(find(foot, 'roundsend'), null, 'the editor’s send stayed live under a take')
  assert.equal(find(foot, 'roundmic').hasAttribute('disabled'), true)
  find(editor, 'keep').fire('click')
  await until(() => !find(root, 'take'))
  assert.equal(find(editor, 'editor-draft').value, 'A long brief with a second paragraph')
  assert.equal(f.value, 'A long brief with a second paragraph', 'the composer’s draft did not follow')

  find(editor, 'editor-done').fire('click')
  await until(() => !find(root, 'editor'))
  f.value = ''
  f.fire('input')
})

// ------------------------------------------------ the screen, and what a take feels like

// Every way a take ends lets the screen go; a take still recording when the
// page is shown again asks for it again.
test('the screen is held while a take records, and let go by ✓, ✕, Escape, a refused mic, a live take and leaving the chat', async () => {
  const start = async () => {
    const was = FakeRecorder.last
    mic().fire('click')
    await until(() => FakeRecorder.last !== was && FakeRecorder.last?.state === 'recording' && take())
    await until(() => heldLocks().length === 1)
  }
  const letGo = async (how) => {
    await until(() => !take() || takeClass()?.includes('cleaning'))
    await until(() => heldLocks().length === 0)
    assert.equal(locks.at(-1).by, 'page', `${how} left the screen held on`)
  }

  // ✓ — and a Stop the person pressed buzzes nothing.
  answers.push(words('kept'))
  await start()
  const before = buzzes.length
  keep().fire('click')
  await letGo('✓')
  await until(() => !take())
  assert.equal(buzzes.length, before, 'a manual stop buzzed')
  field().value = ''
  field().fire('input')

  // ✕ and Escape.
  await start()
  discard().fire('click')
  await letGo('✕')
  await start()
  page.press('Escape')
  await letGo('Escape')

  // Hidden and shown while recording: the browser dropped it, the page asks again.
  await start()
  const first = heldLocks()[0]
  await first.drop()
  page.show(false)
  assert.equal(heldLocks().length, 0)
  page.show(true)
  await until(() => heldLocks().length === 1)
  assert.notEqual(heldLocks()[0], first)
  // And shown again with nothing recording asks for nothing.
  discard().fire('click')
  await letGo('✕ after a re-acquire')
  const count = locks.length
  page.show(false)
  page.show(true)
  await settle()
  assert.equal(locks.length, count, 'a page shown with no take asked for the screen')

  // A microphone that would not open.
  permission = 'denied'
  mic().fire('click')
  await until(() => notice())
  await until(() => heldLocks().length === 0)
  permission = 'granted'
  field().value = 'x'
  field().fire('input')
  field().value = ''
  field().fire('input')
  await until(() => !notice())

  // Leaving the chat with a take going.
  await start()
  all(root, 'iconbtn').find((b) => b.getAttribute('aria-label') === 'Back' || b.getAttribute('aria-label') === 'Back to threads').fire('click')
  await until(() => heldLocks().length === 0)
  assert.equal(locks.at(-1).by, 'page')
  await openChat()

  // Live mode: held while the browser listens, let go by ✕ and by a recogniser
  // that stops by itself.
  const modes = () => all(card('Mode'), 'segmented')[0].childNodes
  await openDictation()
  modes()[1].fire('click')
  await until(() => held().mode === 'browser')
  await closeSettings()
  const was = globalThis.SpeechRecognition.last
  const tapped = buzzes.length
  mic().fire('click')
  await until(() => globalThis.SpeechRecognition.last !== was && take())
  await until(() => heldLocks().length === 1)
  assert.deepEqual(buzzes.slice(tapped), [HAPTICS.started], 'a live take did not tap at the start')
  discard().fire('click')
  await letGo('✕ in Live')
  const again = globalThis.SpeechRecognition.last
  mic().fire('click')
  await until(() => globalThis.SpeechRecognition.last !== again && take())
  await until(() => heldLocks().length === 1)
  globalThis.SpeechRecognition.last.emit('end', {})
  await until(() => heldLocks().length === 0)
  discard().fire('click')
  await until(() => !take())
  await openDictation()
  modes()[0].fire('click')
  await until(() => held().mode === 'record')
  await closeSettings()
})

// A phone whose `vibrate` answers false — Chrome with the screen off, or iOS
// with none — hears a soft tone at the warning and at the cap, on the take's own
// audio context, and nothing at the start.
test('where the phone cannot buzz, the warning and the cap are a soft tone, and the start is silent', async () => {
  const tones = []
  class FakeContext {
    constructor() {
      this.currentTime = 0
      this.destination = {}
      this.closed = false
      FakeContext.last = this
    }
    createMediaStreamSource() { return { connect() {} } }
    createAnalyser() { return { fftSize: 0, connect() {}, getByteTimeDomainData: (b) => b.fill(128) } }
    createGain() {
      return { gain: { setValueAtTime() {}, linearRampToValueAtTime() {} }, connect() {} }
    }
    createOscillator() {
      const osc = { frequency: { value: 0 }, connect() {}, start: (at) => tones.push({ hz: osc.frequency.value, at }), stop: (at) => (tones.at(-1).until = at) }
      return osc
    }
    close() { this.closed = true; return Promise.resolve() }
  }
  globalThis.AudioContext = FakeContext
  vibrates = false
  try {
    const before = buzzes.length
    mic().fire('click')
    await until(() => FakeRecorder.last?.state === 'recording' && take())
    const rec = FakeRecorder.last
    const context = FakeContext.last
    await until(() => buzzes.length === before + 1)
    assert.deepEqual(tones, [], 'the start made a sound')

    skew = RECORD_MAX_MS - RECORD_WARN_MS + 1000
    await until(() => tones.length === 2)
    assert.deepEqual(tones.map((t) => t.hz), [TONES.closing.hz, TONES.closing.hz])
    assert.deepEqual(tones.map((t) => [t.at, t.until]), [[0, 0.15], [0.25, 0.4]], 'the tone did not follow the two pulses')

    answers.push(words('Toned.'))
    skew = RECORD_MAX_MS
    await until(() => rec.state === 'inactive')
    await until(() => tones.length === 3)
    assert.deepEqual(tones[2], { hz: TONES.capped.hz, at: 0, until: 0.5 })
    assert.deepEqual(buzzes.slice(before), [HAPTICS.started, HAPTICS.closing, HAPTICS.capped], 'the buzz was not tried first')
    // The context is kept until the tone has finished, then closed.
    assert.equal(context.closed, false, 'the cap’s tone was cut off')
    await until(() => context.closed)
    await until(() => !take())
    assert.equal(field().value, 'Toned.')
    field().value = ''
    field().fire('input')
  } finally {
    delete globalThis.AudioContext
    vibrates = true
    skew = 0
  }
})

// --------------------------------------------------------- Settings, dictation

const dictationPage = () => find(root, 'settings')
const cards = () => (dictationPage() ? all(dictationPage(), 'card') : [])
const card = (kicker) => cards().find((c) => text(find(c, 'kicker')) === kicker)
const held = () => JSON.parse(globalThis.localStorage.getItem('loam-ui.dictation') ?? 'null')

// Settings is opened from the home screen, so the chat is left for it and
// opened again after: the mic is a composer's, and the composer is a chat's.
async function openDictation() {
  const back = all(root, 'iconbtn').find((b) => b.getAttribute('aria-label') === 'Back' || b.getAttribute('aria-label') === 'Back to threads')
  if (back) back.fire('click')
  await until(() => all(root, 'roundbtn').some((b) => b.getAttribute('title') === 'Settings'))
  all(root, 'roundbtn').find((b) => b.getAttribute('title') === 'Settings').fire('click')
  await until(() => all(root, 'setrow').some((r) => /^Plugins/.test(text(r))))
  const row = all(root, 'setrow').find((r) => /^Plugins/.test(text(r)))
  row.fire('click')
  await until(() => card('Mode') || all(root, 'plugin-row').some(r => r.getAttribute('aria-label') === 'About Dictation'))
  if (card('Mode')) return
  all(root, 'plugin-row').find(r => r.getAttribute('aria-label') === 'About Dictation').fire('click')
  await until(() => card('Mode'))
}

async function closeSettings() {
  find(root, 'scrim').fire('click')
  await until(() => !dictationPage())
  await openChat()
}

test('the Dictation page: the longest take is a stepper, and the next take runs to it', async () => {
  await openDictation()
  assert.deepEqual(cards().map((c) => text(find(c, 'kicker'))), ['Mode', 'Polish', 'Longest recording', 'Microphone', 'Names and terms'])
  const value = () => text(find(card('Longest recording'), 'stepvalue'))
  const step = (title) => all(card('Longest recording'), 'stepbtn').find((b) => b.getAttribute('title') === title)
  assert.equal(value(), '5 min')
  for (let i = 0; i < 3; i += 1) step('Shorter').fire('click')
  await until(() => value() === '2 min')
  assert.equal(held().maxMinutes, 2, 'the setting is not in the store')
  step('Shorter').fire('click')
  await until(() => value() === '1 min')
  assert.equal(step('Shorter').hasAttribute('disabled'), true, 'the take could be made shorter than a minute')
  step('Longer').fire('click')
  await until(() => value() === '2 min')
  await closeSettings()

  const before = buzzes.length
  mic().fire('click')
  await until(() => FakeRecorder.last?.state === 'recording' && take())
  const rec = FakeRecorder.last
  assert.equal(clockSays(), '0:00')
  await until(() => buzzes.length === before + 1)
  // The warning tracks the cap: thirty seconds before two minutes.
  skew = 91_000
  await until(() => takeClass() === 'take record recording closing')
  assert.equal(clockSays(), '1:31')
  await until(() => buzzes.length === before + 2)
  answers.push(words('Two minutes is plenty.'))
  skew = 120_000
  await until(() => rec.state === 'inactive')
  assert.deepEqual(buzzes.slice(before), [HAPTICS.started, HAPTICS.closing, HAPTICS.capped])
  await until(() => !take())
  assert.equal(field().value, 'Two minutes is plenty.')
  field().value = ''
  field().fire('input')
  skew = 0
})

test('the Polish setting round-trips through the page: Light by default, Full and Off chosen, each sent with the take', async () => {
  await openDictation()
  const polishCard = () => card('Polish')
  const level = () => all(polishCard(), 'segmented')[0].childNodes.find((b) => b.getAttribute('aria-pressed') === 'true')
  const press = (label) => all(polishCard(), 'segmented')[0].childNodes.find((b) => text(b) === label).fire('click')
  assert.deepEqual(all(polishCard(), 'segmented')[0].childNodes.map((b) => text(b)), ['Off', 'Light', 'Full'])
  assert.equal(text(level()), 'Light')
  assert.match(text(polishCard()), /Tidy grammar and names/)
  press('Full')
  await until(() => held()?.polish === 'full')
  await until(() => text(level()) === 'Full')
  assert.match(text(polishCard()), /sentences, paragraphs and lists/)
  await closeSettings()

  // Full: the polish call carries Full's prompt and the take's terms.
  const before = polishes.length
  await oneTake('Full is chosen.')
  assert.equal(polishes.length, before + 1)
  assert.match(polishes.at(-1).system_instruction, /Markdown list/)
  assert.equal(polishes.at(-1).model, 'gemini-3.5-flash-lite')
  assert.match(polishes.at(-1).input, /<terms>\n[\s\S]*Juno[\s\S]*\n<\/terms>/)

  // Off: nothing is polished, and the words land as heard.
  await openDictation()
  press('Off')
  await until(() => held()?.polish === 'off')
  await closeSettings()
  const offAt = polishes.length
  await oneTake('Off is chosen.')
  assert.equal(polishes.length, offAt, 'a take at Off was polished')

  await openDictation()
  press('Light')
  await until(() => held()?.polish === 'light')
  await closeSettings()
})

test('a polish that fails its check still lands the words as heard, and the quiet line says why', async () => {
  // A preface: the model spoke to the speaker rather than polishing. On a take
  // this short it is the word count that catches it first.
  polishAnswers.push(words('Here’s the polished transcript:\n\nThe deploy is done.'))
  answers.push(words('the deploy is done'))
  mic().fire('click')
  await until(() => FakeRecorder.last?.state === 'recording' && keep())
  keep().fire('click')
  await until(() => !take() && field().value !== '')
  assert.equal(field().value, 'the deploy is done')
  await until(() => notice())
  assert.equal(text(notice()), 'Polish did not run: the answer had 8 words for 4 spoken')
  assert.equal(find(notice(), 'noticerow-act'), null, 'a fallback offered something to do')
  field().value = ''
  field().fire('input')

  // A polish that ran lands its words and says nothing.
  polishAnswers.push(words('The deploy is done.'))
  answers.push(words('the deploy is done'))
  mic().fire('click')
  await until(() => FakeRecorder.last?.state === 'recording' && keep())
  keep().fire('click')
  await until(() => !take() && field().value !== '')
  assert.equal(field().value, 'The deploy is done.')
  assert.equal(notice(), null)
  field().value = ''
  field().fire('input')
})

test('the Microphone card: nameless until allowed, then by name, and the choice is what a take opens', async () => {
  // Before permission: only the system default, and a way to name the rest.
  allowed = false
  await openDictation()
  await until(() => find(card('Microphone'), 'micallow'))
  const rows = () => all(card('Microphone'), 'setrow').map((r) => text(r))
  assert.deepEqual(rows(), ['System default'])
  assert.ok(find(card('Microphone'), 'setrow-value'), 'the system default is not marked as the choice')

  find(find(card('Microphone'), 'micallow'), 'chip').fire('click')
  await until(() => rows().length === 3)
  assert.deepEqual(rows(), ['System default', 'Desk mic', 'Headset'], 'the default alias or an output was listed')
  assert.equal(find(card('Microphone'), 'micallow'), null)
  assert.equal(tracks.at(-1).stopped, true, 'asking for the names left the microphone open')

  all(card('Microphone'), 'setrow')[2].fire('click')
  await until(() => held()?.deviceId === 'mic-2')
  await closeSettings()

  mic().fire('click')
  await until(() => FakeRecorder.last?.state === 'recording' && take())
  assert.deepEqual(asked.at(-1), { audio: { deviceId: { exact: 'mic-2' } } })
  discard().fire('click')
  await until(() => !take())

  // A chosen microphone that has gone is the system default, not a failure.
  MICS.splice(2, 1)
  mic().fire('click')
  await until(() => FakeRecorder.last?.state === 'recording' && take())
  assert.deepEqual(asked.slice(-2), [{ audio: { deviceId: { exact: 'mic-2' } } }, { audio: true }])
  discard().fire('click')
  await until(() => !take())

  await openDictation()
  await until(() => all(card('Microphone'), 'setrow').length === 3)
  assert.match(text(all(card('Microphone'), 'setrow')[2]), /Not connected/)
  all(card('Microphone'), 'setrow')[0].fire('click')
  await until(() => held()?.deviceId === null)
  await closeSettings()
})

// Every request the page makes to Dictation's `/x/dictation/transcribe`, as it made it: its
// headers and its body, read back out of the form it sent.
async function takesSent(run) {
  const realFetch = globalThis.fetch
  const sent = []
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).endsWith('/x/dictation/transcribe')) {
      const form = options.body instanceof FormData ? options.body : null
      sent.push({
        headers: { ...(options.headers ?? {}) },
        audio: form?.get('audio') ?? null,
        terms: form ? JSON.parse(form.get('terms')) : null,
      })
    }
    return realFetch(url, options)
  }
  try {
    await run()
  } finally {
    globalThis.fetch = realFetch
  }
  return sent
}

async function oneTake(said) {
  answers.push(words(said))
  mic().fire('click')
  await until(() => FakeRecorder.last?.state === 'recording' && keep())
  const asking = heard.length
  keep().fire('click')
  await until(() => heard.length > asking && !take())
  const composed = field()
  assert.equal(composed.value, said)
  composed.value = ''
  composed.fire('input')
  return heard.at(-1).generation_config.transcription_config.custom_vocabulary
}

const PARSED = ['Ada', 'Grace', 'Hopper', 'Lovelace', 'PiperBot', 'Kestrel', 'Wispr Flow', 'Tessellate', 'Obsidian', 'Loam', 'C# notes']
// The note's terms the chat would not have given anyway (its title has Loam).
const NOTE_ONLY = PARSED.filter((t) => t !== 'Loam')
const inputsIn = (node) => (node.nodeName === 'INPUT' || node.nodeName === 'TEXTAREA' ? [node] : (node.childNodes ?? []).flatMap(inputsIn))

test('a take sends the note’s terms in the body, first, with the chat’s own after them', async () => {
  termsNote = NOTE
  const asked = termsAsked()
  let terms
  const sent = await takesSent(async () => {
    terms = await oneTake('Ada signed off.')
  })
  // The note was read afresh for this take.
  assert.equal(termsAsked(), asked + 1)
  // In the body, as a form: the audio, and the terms beside it. No header.
  assert.equal(sent.length, 1)
  assert.equal(sent[0].headers['x-vocabulary'], undefined, 'the terms went in the header')
  assert.equal(sent[0].headers['content-type'], undefined, 'a content type was set by hand, which would lose the form’s boundary')
  assert.equal(sent[0].audio.type, 'audio/webm;codecs=opus')
  assert.deepEqual(sent[0].terms.slice(0, PARSED.length), PARSED, `the note's terms were not first: ${sent[0].terms}`)
  // And that is what Gemini was asked with, through this app's route.
  assert.deepEqual(terms, sent[0].terms)
  assert.deepEqual(terms.slice(0, PARSED.length), PARSED)
  for (const term of ['Juno', 'MuseBot', 'Sable']) assert.ok(terms.includes(term), `the seed lost ${term}: ${terms}`)
  assert.equal(notice(), null, 'a take with a note said something about it')

  // The host of 2026-09-30 sends the note whole, and the page reads it to the
  // same list.
  termsNote = WHOLE_NOTE
  const whole = await oneTake('Grace signed off.')
  assert.deepEqual(whole.slice(0, PARSED.length), PARSED)

  // A vault list longer than Gemini takes: every term sent is the vault's, in
  // its order, and the chat's own seed is what gave way.
  const many = Array.from({ length: 1200 }, (_, i) => `Term${i}`)
  termsNote = { terms: many, source: NOTE.source }
  const capped = await oneTake('Hopper signed off.')
  assert.deepEqual(capped, many.slice(0, VOCABULARY_MAX_TERMS))
  termsNote = NOTE
})

test('with no note the take goes ahead with none of its terms, and the quiet line says so once', async () => {
  // The running broker before its restart: no route, `405`.
  termsNote = null
  const terms = await oneTake('Nothing to bias.')
  assert.ok(!terms.some((t) => NOTE_ONLY.includes(t)), `a note's term was sent with no note: ${terms}`)
  assert.ok(terms.includes('Juno'), 'the chat’s own terms went too')
  await until(() => notice())
  assert.equal(text(notice()), 'No terms note yet')
  assert.match(notice().getAttribute('class'), /\bquiet\b/, 'it was not said quietly')
  assert.equal(find(notice(), 'noticerow-dot'), null)

  // A `404` is the same "no note", and it is not said a second time.
  termsNote = 'no dictation terms note on this box'
  const again = await oneTake('Still nothing.')
  assert.ok(!again.some((t) => NOTE_ONLY.includes(t)))
  assert.equal(notice(), null, 'the missing note was said twice')

  // A note that cannot be read at all is the same: the take is not held for it.
  const realFetch = globalThis.fetch
  const offline = (url, options) => (String(url).endsWith('/x/dictation/terms') ? Promise.reject(new TypeError('Failed to fetch')) : realFetch(url, options))
  globalThis.fetch = offline
  try {
    const unreached = await oneTake('Offline note.')
    assert.ok(!unreached.some((t) => NOTE_ONLY.includes(t)))
  } finally {
    globalThis.fetch = realFetch
  }

  // But once the vault has answered, a read that fails keeps that answer for
  // the page load: the terms do not go with the connection.
  termsNote = NOTE
  await oneTake('Read once.')
  globalThis.fetch = offline
  try {
    const kept = await oneTake('Offline again.')
    assert.deepEqual(kept.slice(0, PARSED.length), PARSED, `the last good terms were lost: ${kept}`)
  } finally {
    globalThis.fetch = realFetch
  }
})

test('the Names and terms card: the note’s path, how many terms, when they were read, and one line', async () => {
  termsNote = NOTE
  // The card first draws what earlier takes read, and then the page's own
  // fresh read: wait for that one, by the call it makes to the host and by the
  // card it draws from the answer. The call alone was not enough (2026-10-02):
  // the host has it before the page has the answer, and until then the card
  // still carries the `Failed to fetch` the test above left on it, which under
  // load was still there when the assertions below read it.
  const asked = () => termsAsked()
  let before = asked()
  await openDictation()
  const terms = () => card('Names and terms')
  const fresh = '11 terms · read under a minute ago'
  await until(() => asked() > before && text(find(terms(), 'termsnote')) === 'Notes/Dictation terms.md' && text(find(terms(), 'termsmeta')) === fresh && !find(terms(), 'warn'))
  // The host that parses the note sends no link: the path is text.
  assert.equal(find(terms(), 'termsnote').nodeName, 'DIV')
  assert.equal(text(find(terms(), 'termsnote')), 'Notes/Dictation terms.md')
  assert.equal(text(find(terms(), 'termsmeta')), '11 terms · read under a minute ago')
  assert.match(text(terms()), /Record first sends these terms to the transcriber/)
  assert.match(text(terms()), /Add one name or term per line. Each recording reads the note fresh\./)
  assert.deepEqual(inputsIn(terms()), [], 'there is still a field to type terms in')
  assert.equal(all(terms(), 'chip').length, 0)
  assert.equal(find(terms(), 'oldterms'), null)
  await closeSettings()

  // The host of 2026-09-30 sends a link, and the path opens the note.
  termsNote = WHOLE_NOTE
  before = asked()
  await openDictation()
  await until(() => asked() > before && find(terms(), 'termsnote')?.nodeName === 'A')
  assert.equal(find(terms(), 'termsnote').getAttribute('href'), '/files/vault/System/Dictation%20terms.md')
  assert.equal(text(find(terms(), 'termsmeta')), '11 terms · read under a minute ago')
  await closeSettings()

  // No route on the host yet (404), or a host older still (405): not
  // connected, nothing linked, and not drawn as a fault.
  for (const answer of ['no such route', null]) {
    termsNote = answer
    await openDictation()
    await until(() => /This install has no terms note location in Files/.test(text(terms())))
    assert.equal(find(terms(), 'pillbtn'), null)
    assert.equal(find(card('Names and terms'), 'warn'), null, `a missing route (${answer ?? 405}) was drawn as a fault`)
    await closeSettings()
  }
  termsNote = { error: 'No dictation terms note', path: 'System/Dictation terms.md' }
  await openDictation()
  await until(() => text(find(terms(), 'pillbtn')) === 'Create terms note')
  assert.match(text(terms()), /Create the note at System\/Dictation terms\.md/)
  await closeSettings()
  termsNote = NOTE
})

test('terms saved on this device before: shown with a Copy and a Forget only while there are some, and Forget removes them', async () => {
  const copied = []
  Object.defineProperty(globalThis.navigator, 'clipboard', { value: { writeText: async (v) => { copied.push(v) } }, configurable: true })
  const hold = (terms) => globalThis.localStorage.setItem('loam-ui.dictation', JSON.stringify({ ...held(), terms }))
  const old = () => find(card('Names and terms'), 'oldterms')

  // None saved: no block.
  hold([])
  termsNote = NOTE
  await openDictation()
  await until(() => find(card('Names and terms'), 'termsnote'))
  await settle()
  assert.equal(old(), null)
  await closeSettings()

  // Saved: the whole list, whether or not the vault has them, and whether or
  // not the vault is connected — and visit after visit, until forgotten.
  hold(['Ada', 'Sable Ridge', 'Zephyr'])
  const answers = ['no such route', NOTE, NOTE]
  for (const [i, answer] of answers.entries()) {
    termsNote = answer
    await openDictation()
    await until(() => old())
    assert.equal(text(find(old(), 'setrow-sub')), 'Saved on this device before')
    assert.equal(text(find(old(), 'setup-text')), 'Ada, Sable Ridge, Zephyr')
    assert.deepEqual(all(old(), 'chip').map(text), ['Copy', 'Forget'])
    if (i < answers.length - 1) await closeSettings()
  }

  all(old(), 'chip')[0].fire('click')
  await until(() => text(all(old(), 'chip')[0]) === 'Copied')
  assert.deepEqual(copied, ['Ada\nSable Ridge\nZephyr'])
  assert.deepEqual(held().terms, ['Ada', 'Sable Ridge', 'Zephyr'], 'Copy dropped them')

  all(old(), 'chip')[1].fire('click')
  await until(() => !old())
  assert.deepEqual(held().terms, [])
  await closeSettings()
  await openDictation()
  await until(() => find(card('Names and terms'), 'termsnote'))
  await settle()
  assert.equal(old(), null, 'forgotten terms came back')
  await closeSettings()
})

test('terms saved on this device before are not sent with a take', async () => {
  globalThis.localStorage.setItem('loam-ui.dictation', JSON.stringify({ ...held(), terms: ['Zephyrine'] }))
  termsNote = NOTE
  const terms = await oneTake('Ada signed off.')
  assert.ok(!terms.includes('Zephyrine'), `a device term was sent: ${terms}`)
  globalThis.localStorage.setItem('loam-ui.dictation', JSON.stringify({ ...held(), terms: [] }))
})

test('Live makes the next press the browser’s recogniser, and Record and clean makes it a take again', async () => {
  await openDictation()
  const modes = () => all(card('Mode'), 'segmented')[0].childNodes
  assert.deepEqual(modes().map(text), ['Record first', 'As you speak'])
  assert.equal(modes()[0].getAttribute('class'), 'on')
  const recordingChoices = held()
  modes()[1].fire('click')
  await until(() => modes()[1].getAttribute('class') === 'on')
  assert.deepEqual(cards().map(c => text(find(c, 'kicker'))), ['Mode'])
  assert.deepEqual(held(), { ...recordingChoices, mode: 'browser' })
  await closeSettings()

  const recorders = FakeRecorder.last
  const before = globalThis.SpeechRecognition.last
  mic().fire('click')
  await until(() => globalThis.SpeechRecognition.last !== before && globalThis.SpeechRecognition.last.started)
  const heardBy = globalThis.SpeechRecognition.last
  assert.equal(FakeRecorder.last, recorders, 'Live mode made a recorder')
  await until(() => take())
  assert.equal(takeClass(), 'take live recording', 'Live mode drew the recorder’s take')
  discard().fire('click')
  await until(() => !heardBy.started && !take())

  await openDictation()
  modes()[0].fire('click')
  await until(() => held().mode === 'record' && card('Polish'))
  assert.deepEqual(cards().map(c => text(find(c, 'kicker'))), ['Mode', 'Polish', 'Longest recording', 'Microphone', 'Names and terms'])
  assert.deepEqual(held(), recordingChoices)
  await closeSettings()
  mic().fire('click')
  await until(() => FakeRecorder.last !== recorders && FakeRecorder.last.state === 'recording')
  assert.equal(globalThis.SpeechRecognition.last, heardBy, 'Record and clean started the recogniser')
  discard().fire('click')
  await until(() => !take())
})

test('removing the last key sends the next mic press to browser recognition and keeps the Record first preference', async () => {
  const key = dictation.config.geminiApiKey
  dictation.config.geminiApiKey = ''
  try {
    await openDictation()
    await until(() => text(find(root, 'speech-provider')).includes('Using this browser'))
    assert.deepEqual(cards().map(c => text(find(c, 'kicker'))), ['Mode'])
    assert.equal(held().mode, 'record')
    await closeSettings()
    field().value = ''
    field().fire('input')
    const before = FakeRecorder.last, calls = heard.length
    mic().fire('click')
    await until(() => takeClass() === 'take live recording')
    const result = Object.assign([{ transcript: 'The sunflowers are growing.' }], { isFinal: true })
    globalThis.SpeechRecognition.last.emit('result', { resultIndex: 0, results: [result] })
    keep().fire('click')
    await until(() => field()?.value.includes('The sunflowers are growing.'))
    assert.equal(FakeRecorder.last, before)
    assert.equal(heard.length, calls)
  } finally { dictation.config.geminiApiKey = key }
})
