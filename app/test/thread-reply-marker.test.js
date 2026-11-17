// A reply's `↩ m111` is Loam's linkage, never words on the page (2026-11-17).
// The record below is the shape of a real one: m112 in a thread on 11-17,
// whose full steps still opened with the marker the writer had taken off the
// message, so the page showed `↩ m111` under its own quote.
import test from 'node:test'
import assert from 'node:assert/strict'
import { quoteLine, plainLine } from '../public/thread-replies.js'
import { createThreads } from '../public/threads.js'
import { applyThreadEvent, messageSegments, withoutReplyMark } from '../public/thread-record.js'
import { installDom } from './fakedom.js'
import { h } from '../public/ui.js'
import { all, find, text } from './browser.js'

const said = '**Yes to rough edges.** Torn edges make each cover look like a sheet of rice paper.\n\nThen they go onto the blog. Go?'
const asked = { id: 'm111', author: 'you', kind: 'message', text: 'I actually like teh sameness in teh images tbh. I like the subtleness.' }
const realSteps = () => [
  { type: 'thinking', thinking: '', parent: null },
  { type: 'tool', id: 'toolu_1', name: 'Bash', input: { command: 'ls covers2' }, parent: null, is_error: false, output: 'covers' },
  { type: 'thinking', thinking: '', parent: null },
  { type: 'text', text: `↩ m111\n\n${said}`, parent: null },
  { type: 'end', event: { type: 'result' } },
]
const reply = (extra = {}) => ({ id: 'm112', author: 'sprout', kind: 'message', text: said, reply_to: 'm111',
  did: [{ label: 'Bash: ls covers2' }], ...extra })

function setup(t, messages) {
  installDom(); globalThis.innerWidth = 390
  const threads = createThreads({ api: async () => ({}), redraw() {}, agents: () => [{ id: 'sprout', name: 'Sprout' }], nav: () => null,
    compose: { field: () => ({ value: '' }), files: () => [], busy: () => false, clear() {}, render: () => h('div'), dragProps: () => ({}) } })
  threads.st.open = { thread: { id: 'one', title: 'A thread', members: [{ persona: 'sprout', engine: 'claude-code' }] }, messages,
    permissions: new Map(), typing: new Set(), steps: new Map(), board: { markdown: '', path: '' } }
  threads.st.listLive = true
  t.after(() => threads.pause())
  const view = () => threads.view(false)
  view.open = threads.st.open
  return view
}
const reader = (tree) => all(tree, 'say').map(text).join('\n')
const article = (tree, id) => all(tree, 'tt-message').find((n) => n.getAttribute('data-message-id') === id)

test('the real record: the marker comes off the words its full steps carry, and only that one', () => {
  const parts = messageSegments(reply({ steps: realSteps() }))
  const words = parts.filter((p) => p.text).map((p) => p.text)
  assert.deepEqual(words, [said])
  assert.ok(parts.some((p) => p.steps), 'the steps are still there')
  // A marker naming another id, prose naming an id, and a message with no
  // reply_to are all left exactly as written.
  assert.equal(withoutReplyMark('↩ m7 elsewhere', 'm111'), '↩ m7 elsewhere')
  assert.equal(withoutReplyMark('Your point in m111 stands.', 'm111'), 'Your point in m111 stands.')
  assert.equal(withoutReplyMark('↩ m111 hello', null), '↩ m111 hello')
  assert.equal(withoutReplyMark('<- m111: hello', 'm111'), 'hello')
  const natural = { id: 'm9', author: 'sprout', text: 'Your point about subtlety holds; ↩ m111 is how Loam links it.' }
  assert.deepEqual(messageSegments(natural).map((p) => p.text), [natural.text])
  // A reply whose first words were the marker alone keeps its later words.
  const alone = messageSegments(reply({ steps: [{ type: 'text', text: '↩ m111', parent: null }, ...realSteps().slice(0, 2), { type: 'text', text: 'Done.', parent: null }] }))
  assert.deepEqual(alone.filter((p) => p.text).map((p) => p.text), ['Done.'])
})

test('a marked reply shows one quote with its author, and no id anywhere, before and after its steps load', (t) => {
  const messages = [asked, reply()]
  const view = setup(t, messages)
  for (const stage of ['posted', 'full steps']) {
    if (stage === 'full steps') messages[1] = reply({ steps: realSteps() })
    const tree = view(), node = article(tree, 'm112')
    const quotes = all(node, 'tt-quote')
    assert.equal(quotes.length, 1, `${stage}: one quote`)
    assert.equal(quotes[0].nodeName, 'BUTTON')
    assert.equal(text(quotes[0]), `You${asked.text}`)
    assert.doesNotMatch(text(node), /m111/, `${stage}: no id in the reply`)
    assert.doesNotMatch(reader(node), /↩/, `${stage}: no marker in its words`)
    assert.match(reader(tree), /Yes to rough edges/)
  }
})

test('quoting a reply that itself replied uses its words, never its marker, and reads them as words', () => {
  assert.equal(quoteLine(reply({ steps: realSteps() })), 'Yes to rough edges. Torn edges make each cover look like a sheet of rice paper.')
  assert.equal(plainLine('> See [[Essay covers — F.html]] and [the mock](https://x/y) `now`'), 'See Essay covers — F.html and the mock now')
  for (const kept of ['snake_case_name stays', '2 * 3 * 4 stays', 'm111 is just text here']) assert.equal(plainLine(kept), kept)
  assert.equal(quoteLine({ id: 'm3', text: '```js\nconst a = 1\n```' }), 'const a = 1')
})

test('an unavailable original degrades to a quiet "Earlier message" with no raw id', (t) => {
  const view = setup(t, [reply({ reply_to: 'm999', steps: realSteps().map((s) => s.type === 'text' ? { ...s, text: `↩ m999\n\n${said}` } : s) })])
  const node = article(view(), 'm112')
  assert.deepEqual(all(node, 'tt-quote').map(text), ['Earlier message'])
  assert.equal(all(node, 'tt-quote')[0].nodeName, 'DIV')
  assert.doesNotMatch(text(node), /m999/)
  assert.doesNotMatch(reader(node), /↩/)
})

test('streaming: the quote shows with the first words and stays the one quote, in place, when the reply is posted', (t) => {
  const view = setup(t, [asked]), o = view.open
  applyThreadEvent(o, { type: 'turn.started', persona: 'sprout', started_at: '2026-11-17T17:22:00Z' })
  applyThreadEvent(o, { type: 'message.delta', persona: 'sprout', text: '**Yes', draft: '**Yes', reply_to: 'm111' })
  applyThreadEvent(o, { type: 'message.delta', persona: 'sprout', text: ' to rough edges.**', draft: '**Yes to rough edges.**', reply_to: 'm111' })
  const live = o.messages.find((m) => m.streaming)
  assert.equal(live.reply_to, 'm111')
  let node = all(view(), 'tt-message').find((n) => n.getAttribute('data-message-id') === live.id)
  assert.deepEqual(all(node, 'tt-quote').map(text), [`You${asked.text}`])
  assert.doesNotMatch(text(node), /m111/); assert.doesNotMatch(reader(node), /↩/)
  applyThreadEvent(o, { type: 'message.done', message: { ...reply(), at: '2026-11-17T17:23:02Z' } })
  const posted = o.messages.find((m) => m.id === 'm112')
  assert.equal(posted.viewKey, live.viewKey, 'the posted reply keeps the live one’s place')
  assert.equal(o.messages.filter((m) => m.author === 'sprout').length, 1)
  node = article(view(), 'm112')
  assert.deepEqual(all(node, 'tt-quote').map(text), [`You${asked.text}`])
  assert.doesNotMatch(text(node), /m111/); assert.doesNotMatch(reader(node), /↩/)
})

test('a natural reply streams and posts with no quote', () => {
  const o = { thread: { id: 'one', members: [{ persona: 'sprout' }] }, messages: [asked], permissions: new Map(), typing: new Set() }
  applyThreadEvent(o, { type: 'message.delta', persona: 'sprout', text: 'Your point about subtlety', draft: 'Your point about subtlety' })
  assert.equal(o.messages.at(-1).reply_to, undefined)
})
