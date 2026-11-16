import { setOwner } from '../public/owner.js'
setOwner({ id: 'you', name: 'Sam', accepts: ['you', 'owner', 'person'] })
import test from 'node:test'
import assert from 'node:assert/strict'
import { applyThreadEvent, withPending } from '../public/thread-record.js'

// The owner, 2026-11-16 (t-153f9c43 m33): "if the final message actually gets
// posted after other messages, then it should be ordered that way." These are
// the event orders the broker's thread stream gives (agents repo,
// tests/test_thread_completion_order.py), replayed through the page.
const members = [{ persona: 'ada', engine: 'claude-code', working: true, busy: true, background: 0, background_tasks: [] }]
const open = () => ({ thread: { id: 't', members: structuredClone(members) }, messages: [], typing: new Set(), permissions: new Map() })
const started = { type: 'turn.started', persona: 'ada', engine: 'claude-code', model: null, started_at: '2026-11-16T16:10:31Z' }
const own = (id, text, at) => ({ type: 'message.done', message: { id, author: 'owner', kind: 'message', text, at } })
const reply = (id, text, at, extra = {}) => ({ type: 'message.done', message: { id, author: 'ada', kind: 'message', text, at, ...extra } })
const order = (o) => o.messages.map((m) => m.streaming ? `[${m.id}]` : m.id)

test('messages sent during a long tool call stay above the reply that took them in', () => {
  const o = open(), emit = (e) => applyThreadEvent(o, e)
  emit(own('m1', 'run the long thing', '2026-11-16T16:10:30Z'))
  emit(started)
  emit({ type: 'message.delta', persona: 'ada', text: 'Starting on it.', draft: 'Starting on it.' })
  emit({ type: 'turn.step', persona: 'ada', label: 'Bash: sleep 60', steps: 1 })
  const view = o.messages.find((m) => m.id === 'draft:ada').viewKey
  emit(own('m2', 'also: use the blue one', '2026-11-16T16:11:09Z'))
  assert.deepEqual(order(o), ['m1', 'm2', '[draft:ada]'], 'the working reply moves below what was just sent')
  emit({ type: 'turn.steered', persona: 'ada', messages: ['m2'] })
  emit(own('m3', 'and keep it quiet', '2026-11-16T16:11:12Z'))
  emit({ type: 'message.delta', persona: 'ada', text: 'Done.', draft: 'Done. Blue, and quietly.' })
  assert.deepEqual(order(o), ['m1', 'm2', 'm3', '[draft:ada]'])
  const done = reply('m4', 'Done. Blue, and quietly.', '2026-11-16T16:11:30Z', { steered: ['m2', 'm3'], segments: [{ text: 'Starting on it.' }, { did: [{ label: 'Bash: sleep 60' }] }] })
  emit(done); emit(done)
  emit({ type: 'turn.finished', persona: 'ada' })
  assert.deepEqual(order(o), ['m1', 'm2', 'm3', 'm4'], 'the same order a reload reads')
  assert.equal(o.messages[3].viewKey, view, 'the reply keeps its view identity as it settles')
  assert.equal(o.messages[3].segments[0].text, 'Starting on it.')
  assert.equal(o.after, 'm4')
})

test('reply, message, reply: each settles where it was posted', () => {
  const o = open(), emit = (e) => applyThreadEvent(o, e)
  emit(own('m1', 'question one', '2026-11-16T16:20:00Z'))
  emit(started)
  emit({ type: 'message.delta', persona: 'ada', text: 'Here is the first answer.', draft: 'Here is the first answer.' })
  emit(own('m2', 'follow-up, sent as it finished', '2026-11-16T16:20:05Z'))
  emit(reply('m3', 'Here is the first answer.', '2026-11-16T16:20:06Z'))
  assert.deepEqual(order(o), ['m1', 'm2', 'm3'], 'posted after the follow-up, so below it, though its words came first')
  emit({ type: 'message.delta', persona: 'ada', text: 'And the follow-up.', draft: 'And the follow-up.' })
  assert.deepEqual(order(o), ['m1', 'm2', 'm3', '[draft:ada]'])
  emit(reply('m4', 'And the follow-up.', '2026-11-16T16:20:07Z'))
  emit({ type: 'turn.finished', persona: 'ada' })
  assert.deepEqual(order(o), ['m1', 'm2', 'm3', 'm4'])
})

test('a reconnect mid-work keeps the draft below the replayed messages, without duplicates', () => {
  const o = open(), emit = (e) => applyThreadEvent(o, e)
  emit(own('m1', 'run the long thing', '2026-11-16T16:10:30Z'))
  emit(started)
  emit({ type: 'message.delta', persona: 'ada', text: 'Working.', draft: 'Working.' })
  // The stream dropped; the reconnect states liveness, then replays after m1.
  emit({ type: 'thread.snapshot', turns: [started], members })
  emit(own('m1', 'run the long thing', '2026-11-16T16:10:30Z'))
  emit(own('m2', 'sent while offline', '2026-11-16T16:11:09Z'))
  assert.deepEqual(order(o), ['m1', 'm2', '[draft:ada]'])
  assert.equal(o.messages.at(-1).text, 'Working.', 'the continuing prose survives')
  emit(reply('m3', 'Finished.', '2026-11-16T16:11:30Z'))
  assert.deepEqual(order(o), ['m1', 'm2', 'm3'])
})

test('a message being sent is drawn where it will stand: above the live replies', () => {
  const o = open(), emit = (e) => applyThreadEvent(o, e)
  emit(own('m1', 'run the long thing', '2026-11-16T16:10:30Z'))
  emit(started)
  const pending = { id: 'pending-send', author: 'owner', kind: 'message', text: 'also this' }
  assert.deepEqual(withPending(o.messages, pending).map((m) => m.id), ['m1', 'pending-send', 'draft:ada'])
  assert.equal(withPending(o.messages, null), o.messages)
  // Another durable message settling while pending-send is still in flight goes above pending-send.
  emit(own('m2', 'from another device', '2026-11-16T16:11:05Z'))
  assert.deepEqual(withPending(o.messages, pending).map((m) => m.id), ['m1', 'm2', 'pending-send', 'draft:ada'])
  // SSE message.done arriving ahead of the HTTP response clears pending without duplicating.
  o.pending = pending
  emit(own('m3', 'also this', '2026-11-16T16:11:09Z'))
  assert.equal(o.pending, null)
  assert.deepEqual(order(o), ['m1', 'm2', 'm3', '[draft:ada]'], 'its durable copy takes the same place')
})

test('a branch notice keeps its place beneath its source even as newer messages settle', () => {
  const o = open(), emit = (e) => applyThreadEvent(o, e)
  for (const [id, at] of [['m5', '16:00'], ['m6', '16:01']]) emit(own(id, id, `2026-11-16T${at}:00Z`))
  emit({ type: 'message.done', message: { id: 'm8', author: 'system', kind: 'system', after: 'm5', text: 'Branched into another thread' } })
  emit(started)
  emit(own('m9', 'later', '2026-11-16T16:12:00Z'))
  emit(reply('m10', 'answer', '2026-11-16T16:13:00Z'))
  assert.deepEqual(order(o), ['m5', 'm8', 'm6', 'm9', 'm10'])

  // Even when a notice sits right after its source with no later sibling yet,
  // a message numbered between the source and the notice settles after the notice.
  const b = open()
  applyThreadEvent(b, own('m5', 'source', '2026-11-16T16:00:00Z'))
  applyThreadEvent(b, { type: 'message.done', message: { id: 'm8', author: 'system', kind: 'system', after: 'm5', text: 'Branched' } })
  applyThreadEvent(b, own('m7', 'settled after', '2026-11-16T16:01:00Z'))
  assert.deepEqual(order(b), ['m5', 'm8', 'm7'])
})

test('parallel reverse completion with mid-work sends preserves other members drafts and view keys', () => {
  const pairMembers = [
    { persona: 'ada', engine: 'claude-code', working: true, busy: true, background: 0, background_tasks: [] },
    { persona: 'quill', engine: 'codex', working: true, busy: true, background: 0, background_tasks: [] },
  ]
  const o = { thread: { id: 't', members: structuredClone(pairMembers) }, messages: [], typing: new Set(), permissions: new Map() }
  const emit = (e) => applyThreadEvent(o, e)
  emit(own('m1', 'start', '2026-11-16T16:30:00Z'))
  emit(started)
  emit({ type: 'message.delta', persona: 'ada', draft: 'Ada working.' })
  emit({ type: 'turn.started', persona: 'quill', engine: 'codex', model: null, started_at: '2026-11-16T16:30:02Z' })
  emit({ type: 'message.delta', persona: 'quill', draft: 'Quill working.' })
  const adaView = o.messages.find((m) => m.id === 'draft:ada').viewKey
  const quillView = o.messages.find((m) => m.id === 'draft:quill').viewKey
  emit(own('m2', 'mid-work note', '2026-11-16T16:30:05Z'))
  assert.deepEqual(order(o), ['m1', 'm2', '[draft:ada]', '[draft:quill]'])
  emit({ type: 'message.done', message: { id: 'm3', author: 'quill', kind: 'message', text: 'Quill done.', at: '2026-11-16T16:30:06Z' } })
  emit({ type: 'turn.finished', persona: 'quill' })
  assert.deepEqual(order(o), ['m1', 'm2', 'm3', '[draft:ada]'])
  assert.equal(o.messages.find((m) => m.id === 'm3').viewKey, quillView)
  assert.equal(o.messages.find((m) => m.id === 'draft:ada').text, 'Ada working.')
  assert.equal(o.messages.find((m) => m.id === 'draft:ada').viewKey, adaView)
  emit(reply('m4', 'Ada done.', '2026-11-16T16:30:08Z'))
  emit({ type: 'turn.finished', persona: 'ada' })
  assert.deepEqual(order(o), ['m1', 'm2', 'm3', 'm4'])
  assert.equal(o.messages.find((m) => m.id === 'm4').viewKey, adaView)
})
