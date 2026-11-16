import test from 'node:test'
import assert from 'node:assert/strict'
import { applyThreadEvent, threadTurns } from '../public/thread-record.js'
import { liveThreadMembers, threadWaiting, taskElapsed } from '../public/thread-experience.js'

const members = [
  { persona: 'sprout', engine: 'claude-code', working: true, busy: true, background: 0, background_tasks: [] },
  { persona: 'quill', engine: 'codex', working: true, busy: true, background: 0, background_tasks: [] },
]
const turns = members.map((m, i) => ({ persona: m.persona, engine: m.engine, model: null, started_at: `2026-11-10T22:00:0${i}Z` }))
const open = () => ({ thread: { id: 'one', members: structuredClone(members) }, messages: [], typing: new Set(), permissions: new Map() })

test('reads and snapshots restore every active member in host start order; turns[] outranks turn', () => {
  assert.deepEqual(threadTurns({ turns: [], turn: turns[0] }), [])
  assert.deepEqual(threadTurns({ turn: turns[0] }), [turns[0]])
  const o = open()
  applyThreadEvent(o, { type: 'thread.snapshot', turns, turn: turns[0], members })
  assert.deepEqual([...o.activeTurns], ['sprout', 'quill'])
  assert.deepEqual(liveThreadMembers(o), ['sprout', 'quill'])
  assert.deepEqual(o.messages.map((m) => m.id), ['draft:sprout', 'draft:quill'])
  applyThreadEvent(o, { type: 'thread.snapshot', turns: [turns[1]], turn: turns[0], members: [members[1]] })
  assert.deepEqual([...o.typing], ['quill'])
  assert.deepEqual(o.messages.map((m) => m.id), ['draft:quill'])
  applyThreadEvent(o, { type: 'thread.snapshot', turns: [], members: [] })
  assert.deepEqual(liveThreadMembers(o), [])
})
test('interleaved replies, tools and reverse-order completions keep each member in its own slot', () => {
  const o = open(), emit = (e) => applyThreadEvent(o, e)
  emit({ type: 'turn.started', ...turns[0] })
  emit({ type: 'message.delta', persona: 'sprout', draft: 'First member speaks.' })
  emit({ type: 'message.done', message: { id: 'm1', author: 'you', text: '@quill A parallel question.' } })
  emit({ type: 'turn.started', ...turns[1] })
  const order = () => o.messages.map((m) => m.id)
  assert.deepEqual(order(), ['m1', 'draft:sprout', 'draft:quill'], 'live replies follow the record')
  emit({ type: 'message.delta', persona: 'quill', draft: 'Second member speaks.' })
  const replyKey = o.messages[2].viewKey
  emit({ type: 'turn.step', persona: 'sprout', label: 'Read notes' })
  assert.deepEqual(order(), ['m1', 'draft:sprout', 'draft:quill'])
  assert.equal(o.messages[2].text, 'Second member speaks.')
  emit({ type: 'message.delta', persona: 'sprout', draft: 'First member continues.' })
  assert.equal(o.messages.find((m) => m.id === 'draft:sprout').segments[0].text, 'First member speaks.')
  const done = { type: 'message.done', message: { id: 'm2', author: 'quill', text: 'Second member finishes first.' } }
  emit(done); emit(done)
  assert.deepEqual(order(), ['m1', 'm2', 'draft:sprout'])
  assert.equal(o.messages[1].viewKey, replyKey, 'the durable ID and replay preserve the streaming view identity')
  emit({ type: 'turn.finished', persona: 'quill' })
  assert.equal(o.typing.has('sprout'), true)
  emit({ type: 'message.done', message: { id: 'm3', author: 'sprout', text: 'First member finishes last.' } })
  assert.deepEqual(order(), ['m1', 'm2', 'm3'], 'the reply finishing last settles last')
  assert.equal(o.after, 'm3')
})
test('parked members never use playful phrases, and task count settling removes their line independently', () => {
  const o = open(), parked = { ...members[0], working: false, background: 2, background_tasks: [{ id: 'a', description: 'Compare the notes', started_at: turns[0].started_at }] }
  applyThreadEvent(o, { type: 'thread.snapshot', turns: [{ ...turns[0], on_background: true }, turns[1]], members: [parked, members[1]] })
  for (const elapsed of [0, 60000, 3600000]) assert.deepEqual(threadWaiting(o, 'sprout', 'Sprout', elapsed), { text: 'Sprout is waiting on 2 background tasks', out: false, nextMs: null })
  assert.match(threadWaiting(o, 'quill', 'Quill', 0).text, /Thinking/)
  applyThreadEvent(o, { type: 'members', members: [{ ...parked, background: 0, background_tasks: [] }, members[1]] })
  assert.deepEqual(liveThreadMembers(o), ['quill'])
  applyThreadEvent(o, { type: 'members', members: [{ ...parked, background: 0, working: true, background_tasks: [] }, members[1]] })
  assert.match(threadWaiting(o, 'sprout', 'Sprout', 0).text, /Thinking/)
})
test('task elapsed time uses started_at, including unknown times and hour boundaries', () => {
  const now = Date.parse('2026-11-10T23:00:00Z')
  assert.equal(taskElapsed(null, now), null)
  assert.equal(taskElapsed('invalid', now), null)
  assert.equal(taskElapsed('2026-11-10T23:01:00Z', now), '<1 min')
  assert.equal(taskElapsed('2026-11-10T22:58:00Z', now), '2 min')
  assert.equal(taskElapsed('2026-11-10T21:59:00Z', now), '1 hr 1 min')
})

test('a reconnect preserves continuing drafts, progress and placement; replay cannot erase the next turn', () => {
  const o = open(), emit = (e) => applyThreadEvent(o, e)
  emit({ type: 'thread.snapshot', turns, members })
  emit({ type: 'message.delta', persona: 'sprout', draft: 'Before the tool.' })
  emit({ type: 'turn.step', persona: 'sprout', label: 'Read notes' })
  emit({ type: 'message.delta', persona: 'sprout', draft: 'After the tool.' })
  emit({ type: 'message.delta', persona: 'quill', draft: 'Independent words.' })
  const drafts = structuredClone(o.messages)
  emit({ type: 'thread.snapshot', turns, members })
  assert.deepEqual(o.messages, drafts)
  assert.equal(o.progress.get('sprout')[0].text, 'Before the tool.')
  const reply = { id: 'm4', author: 'sprout', text: 'Completed reply.', at: '2026-11-10T22:01:00Z' }
  emit({ type: 'message.done', message: reply })
  emit({ type: 'turn.finished', persona: 'sprout' })
  emit({ type: 'turn.started', ...turns[0], started_at: '2026-11-10T22:02:00Z' })
  emit({ type: 'message.delta', persona: 'sprout', draft: 'A new reply.' })
  emit({ type: 'message.done', message: reply })
  emit({ type: 'message.done', message: { ...reply, id: 'm3', text: 'A replay before the current turn.' } })
  assert.equal(o.messages.find((m) => m.id === 'draft:sprout').text, 'A new reply.')
  assert.equal(o.typing.has('sprout'), true)
  assert.equal(o.messages.find((m) => m.id === 'draft:quill').text, 'Independent words.')
  emit({ type: 'thread.snapshot', turns: [{ ...turns[0], started_at: '2026-11-10T22:03:00Z' }], members: members.map((m) => m.persona === 'quill' ? { ...m, working: false, busy: false } : m) })
  assert.equal(o.messages.find((m) => m.id === 'draft:sprout').text, '', 'a different turn discards its predecessor’s draft')
  assert.equal(o.messages.some((m) => m.id === 'draft:quill'), false)
})
test('seat waits clear on a delta and a generating notification outranks a parked turn hint', () => {
  const o = open()
  applyThreadEvent(o, { type: 'thread.snapshot', turns: [{ ...turns[0], waiting: 'Waiting for a free seat.' }], members })
  assert.equal(threadWaiting(o, 'sprout', 'Sprout', 0).text, 'Waiting for a free seat.')
  applyThreadEvent(o, { type: 'message.delta', persona: 'sprout', draft: 'The seat is ready.' })
  assert.equal(threadWaiting(o, 'sprout', 'Sprout', 0).text, 'Sprout is replying…')
  o.turns[0].on_background = true
  o.thread.members = o.thread.members.map((m) => m.persona === 'sprout' ? { ...m, background: 2, working: true } : m)
  assert.equal(threadWaiting(o, 'sprout', 'Sprout', 0).text, 'Sprout is replying…')
})

test('notification replies with no turn.started keep an independent completed-reply anchor', () => {
  const o = open()
  applyThreadEvent(o, { type: 'message.delta', persona: 'sprout', draft: 'An unsolicited follow-up.' })
  applyThreadEvent(o, { type: 'turn.started', ...turns[1] })
  applyThreadEvent(o, { type: 'message.delta', persona: 'quill', draft: 'Still checking.' })
  applyThreadEvent(o, { type: 'message.done', message: { id: 'm9', author: 'sprout', text: 'The follow-up is ready.' } })
  assert.equal(o.liveReplies.get('sprout'), 'm9')
  assert.deepEqual(o.messages.map((m) => m.id), ['m9', 'draft:quill'])
  assert.equal(o.messages[1].text, 'Still checking.')
})

test('a snapshot keeps notification prose while its member is working, then drops settled drafts', () => {
  const o = open()
  applyThreadEvent(o, { type: 'message.delta', persona: 'sprout', draft: 'A follow-up outside the thread turn.' })
  applyThreadEvent(o, { type: 'thread.snapshot', turns: [], members })
  assert.equal(o.messages[0].text, 'A follow-up outside the thread turn.')
  assert.match(threadWaiting(o, 'sprout', 'Sprout', 0).text, /replying/)
  applyThreadEvent(o, { type: 'thread.snapshot', turns: [], members: members.map((m) => ({ ...m, working: false, busy: false })) })
  assert.equal(o.messages.length, 0)
  assert.deepEqual(liveThreadMembers(o), [])
})
