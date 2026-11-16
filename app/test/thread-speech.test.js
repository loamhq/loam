// The actual app and shared take lifecycle, through a thread message.
import test from 'node:test'
import assert from 'node:assert/strict'
import { oneProductFixture } from './one-product-fixture.mjs'
import { browser, find, all, text } from './browser.js'
import { until } from './harness.js'

class Audio {
  static made = []
  constructor() { Object.assign(this, { paused: true, ended: false, currentTime: 0, duration: 90, buffered: { length: 0 }, listeners: new Map(), plays: 0 }); Audio.made.push(this) }
  addEventListener(k, fn) { if (!this.listeners.has(k)) this.listeners.set(k, []); this.listeners.get(k).push(fn) }
  emit(k) { for (const fn of this.listeners.get(k) ?? []) fn() }
  play() { this.plays++; this.paused = false; this.ended = false; this.emit('play'); return Promise.resolve() }
  pause() { this.paused = true; this.emit('pause') }
  load() {}
  removeAttribute() {}
}
const fixture = await oneProductFixture(), page = browser(fixture.url)
globalThis.Audio = Audio
const posts = [], lookups = [], known = new Map()
let fail = false, pending = false
const ready = { id: 'a'.repeat(32), status: 'ready', duration_s: 90, url: '/api/speech/' + 'a'.repeat(32) + '/audio.mp3' }
const reply = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const pass = globalThis.fetch
const key = (body) => JSON.stringify([body.chat_id, body.mode, body.text])
globalThis.fetch = async (url, options = {}) => {
  if (url === '/api/health') return reply({ configured: true, ok: true, speech: true })
  if (url === '/api/speech/lookup') { const body = JSON.parse(options.body); lookups.push(body); return reply(known.get(key(body)) ?? { status: 'none' }) }
  if (url === '/api/speech') { const body = JSON.parse(options.body); posts.push(body); if (fail) return reply({ error: 'Could not make this take.' }, 500); pending = true; return reply({ id: ready.id, status: 'preparing', stage: 'voicing' }, 202) }
  if (String(url) === `/api/speech/${ready.id}`) { if (pending) return reply({ id: ready.id, status: 'preparing' }, 202); known.set(key(posts.at(-1)), ready); return reply(ready) }
  return pass(url, options)
}
page.watch(); await import('../public/app.js'); page.unwatch()
test.after(async () => { page.stop(); await fixture.close(); setImmediate(() => process.exit(process.exitCode ?? 0)) })
const root = page.root, click = (node) => { assert.ok(node, 'control exists'); node.fire('click', { preventDefault() {}, stopPropagation() {} }) }
const glyphs = () => all(root, 'speakbtn'), player = () => find(root, 'player')
const choose = async () => { click(glyphs().at(-1)); await until(() => find(root, 'speakmenu')); click(all(find(root, 'speakmenu'), 'setrow')[0]) }
let messageText
test('thread messages use shared glyph thresholds, lookup and Listen/Brief menu', async () => {
  await until(() => all(root, 'tt-row').length)
  click(all(root, 'tt-row').find((row) => text(row).includes('A little room for the week')))
  await until(() => all(root, 'tt-message').length)
  // This fixture injects directly into attached streams rather than storing
  // a reply for replay. Opening now reads the tail before attaching at its ID.
  await until(() => fixture.broker.seen.some((r) => r.path === '/v1/threads/a-quieter-week/events' && r.search.includes('after=')))
  // Inject a durable long response via the real thread SSE adapter.
  messageText = 'A response worth hearing. '.repeat(55).trim()
  fixture.emit('a-quieter-week', { type: 'message.done', message: { id: 'm90', author: 'sprout', kind: 'message', text: messageText } })
  await until(() => glyphs().length && lookups.some((l) => l.text === messageText))
  assert.equal(lookups.at(-1).agent, 'sprout')
  click(glyphs().at(-1)); await until(() => find(root, 'speakmenu'))
  const menu = text(find(root, 'speakmenu')); assert.match(menu, /Listen/); assert.match(menu, /Brief/); assert.match(menu, /to make/)
  page.press('Escape'); await until(() => !find(root, 'speakmenu'))
})
test('none → making → ready never auto-plays; tap plays, pauses, ends and retries through the same player', async () => {
  await choose(); await until(player)
  assert.equal(posts.at(-1).text, messageText); assert.equal(posts.at(-1).agent, 'sprout'); assert.match(posts.at(-1).chat_id, /thread:/)
  assert.equal(Audio.made.length, 0); assert.match(text(player()), /Voicing|Making/)
  pending = false
  await until(() => find(player(), 'player-play'))
  assert.equal(Audio.made.length, 0)
  click(find(player(), 'player-play')); await until(() => Audio.made.at(-1)?.plays === 1)
  assert.equal(Audio.made.at(-1).paused, false)
  click(find(player(), 'player-play')); assert.equal(Audio.made.at(-1).paused, true)
  Audio.made.at(-1).ended = true; Audio.made.at(-1).emit('ended')
  click(find(player(), 'player-play')); await until(() => Audio.made.at(-1).plays === 2)
  click(find(player(), 'player-close')); await until(() => !player())
  // Another author with exactly the same words must get an independent take.
  fixture.emit('a-quieter-week', { type: 'message.done', message: { id: 'm91', author: 'scout', kind: 'message', text: messageText } })
  await until(() => lookups.some((l) => l.agent === 'scout' && l.text === messageText))
  const first = posts[0].chat_id
  fail = true; await choose(); await until(() => find(root, 'player-retry'))
  assert.notEqual(posts.at(-1).chat_id, first); assert.equal(posts.at(-1).agent, 'scout')
  assert.match(text(player()), /Could not make this take/)
  fail = false; click(find(root, 'player-retry')); await until(() => posts.length === 3)
  pending = false; await until(() => find(player(), 'player-play'))
})

test('each consecutive reply owns a glyph inside its last prose footer, before trailing tools and unrelated messages', async () => {
  const intro = 'The beginning. ', final = 'The prose finishes here. '.repeat(80).trim()
  for (const id of ['anchored-1', 'anchored-2']) fixture.emit('a-quieter-week', { type: 'message.done', message: {
    id, author: 'sprout', kind: 'message', text: '', segments: [
      { text: intro }, { did: [{ label: 'Middle tool' }] }, { text: final }, { did: [{ label: 'Trailing tool' }] },
    ],
  } })
  fixture.emit('a-quieter-week', { type: 'message.done', message: { id: 'system-after', author: 'system', kind: 'system', text: 'A separate notice.' } })
  fixture.emit('a-quieter-week', { type: 'message.done', message: { id: 'short-after', author: 'sprout', kind: 'message', text: 'A short reply.' } })
  await until(() => all(root, 'tt-message').some((node) => node.getAttribute('data-message-id') === 'short-after'))
  for (const id of ['anchored-1', 'anchored-2']) {
    const node = all(root, 'tt-message').find((node) => node.getAttribute('data-message-id') === id)
    const prose = all(node, 'say'), footer = find(node, 'tt-reply-footer')
    assert.equal(all(node, 'speakbtn').length, 1)
    assert.equal(find(prose[0], 'speakbtn'), null)
    assert.equal(footer.parentNode, prose.at(-1))
    assert.ok(find(footer, 'speakbtn'))
    assert.match(text(node.childNodes.at(-1)), /1 step/, 'the final tool fold remains after the prose and its footer')
  }
  const short = all(root, 'tt-message').find((node) => node.getAttribute('data-message-id') === 'short-after')
  assert.equal(all(short, 'speakbtn').length, 0, 'device threshold still applies')
  assert.equal(all(root, 'tt-author-run').filter((node) => all(node, 'tt-message').some((m) => m.getAttribute('data-message-id') === 'anchored-1')).length, 1)
  const run = all(root, 'tt-author-run').find((node) => all(node, 'tt-message').some((m) => m.getAttribute('data-message-id') === 'anchored-1'))
  assert.equal(all(run, 'tt-speaker').length, 1, 'consecutive messages share one author line')
  assert.ok(all(run, 'tt-message').some((m) => m.getAttribute('data-message-id') === 'anchored-2'))
})

test('Regenerate forces only the chosen take, prevents double taps and preserves usable playback on failure and success', async () => {
  const message = 'Keep the current audio while a replacement is prepared. '.repeat(30).trim()
  fixture.emit('a-quieter-week', { type: 'message.done', message: { id: 'regenerate', author: 'sprout', kind: 'message', text: message } })
  await until(() => lookups.some(l => l.text === message))
  await choose(); pending = false
  const messageNode = () => all(root, 'tt-message').find(n => n.getAttribute('data-message-id') === 'regenerate')
  await until(() => find(messageNode(), 'player-play'))
  click(find(messageNode(), 'player-play'))
  const audio = Audio.made.at(-1), made = Audio.made.length
  audio.currentTime = 37; audio.emit('timeupdate')
  const menu = async () => { click(glyphs().at(-1)); await until(() => find(root, 'speakmenu')) }
  await menu()
  assert.match(text(find(root, 'speakmenu')), /Ready to play/)
  assert.equal(all(find(root, 'speakmenu'), 'speech-regenerate').length, 1, 'unmade Brief has no Regenerate')
  fail = true; click(find(root, 'speech-regenerate'))
  await until(() => text(find(root, 'speakmenu')).includes('Current audio is still available'))
  assert.equal(posts.at(-1).force, true)
  assert.equal(audio.paused, false); assert.equal(audio.currentTime, 37)
  fail = false
  const before = posts.length, control = find(root, 'speech-regenerate')
  click(control); click(control)
  await until(() => posts.length > before)
  assert.equal(posts.length, before + 1)
  assert.equal(posts.at(-1).text, message); assert.equal(posts.at(-1).mode, 'verbatim')
  pending = false
  await until(() => text(find(root, 'speakmenu')).includes('New audio ready'))
  assert.equal(Audio.made.length, made)
  assert.equal(audio.paused, false); assert.equal(audio.currentTime, 37)
  assert.equal(audio.plays, 1, 'regeneration never calls play')
})
