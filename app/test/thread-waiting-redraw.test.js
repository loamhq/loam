import test from 'node:test'
import assert from 'node:assert/strict'
import { memberWaiting, memberGenerating, showReply, taskTitle, taskElapsed } from '../public/thread-experience.js'
import { applyThreadEvent } from '../public/thread-record.js'

const open = () => ({ thread: { id: 'one', members: [{ persona: 'sprout', working: false, background: 1 }] },
  messages: [], typing: new Set(['sprout']), permissions: new Map(), turns: [{ persona: 'sprout', on_background: true }] })

test('a parked member with no prose has no reply, including completed task-only records and stale drafts', () => {
  const o = open()
  assert.equal(memberWaiting(o, 'sprout'), true)
  assert.equal(memberGenerating(o, 'sprout'), false, 'the authoritative working field outranks an active turn and typing set')
  for (const content of [{ text: '' }, { text: '  ' }, { text: '', segments: [{ did: [{ label: 'Waiting on a task' }] }] },
    { text: '', steps: [{ type: 'tool', id: 'job', name: 'Task', input: {} }] }]) {
    for (const streaming of [false, true]) assert.equal(showReply(o, { id: 'empty', author: 'sprout', ...content, streaming }), false)
  }
  assert.equal(showReply(o, { id: 'reply', author: 'sprout', text: 'The finished reply.' }), true)
  assert.equal(showReply(o, { id: 'prefix', author: 'sprout', text: '', segments: [{ text: 'Earlier real prose.' }] }), true)
  assert.equal(showReply(o, { id: 'file', author: 'sprout', attachments: [{ path: 'report.md' }] }), true)
  o.permissions.set('approval', { message_id: 'question' })
  assert.equal(showReply(o, { id: 'question', author: 'sprout', text: '' }), true, 'an actionable permission remains visible')
})

test('only an actually generating draft gets a reply slot before prose', () => {
  const o = open(), draft = { id: 'draft:sprout', author: 'sprout', text: '', streaming: true }
  o.thread.members[0].working = true
  assert.equal(memberGenerating(o, 'sprout'), true)
  assert.equal(showReply(o, draft), true)
  assert.equal(showReply(o, { ...draft, streaming: false }), false)
  o.thread.members[0].working = false
  assert.equal(showReply(o, draft), false)
  assert.equal(showReply(o, { ...draft, text: 'A notification has arrived.' }), true)
})

test('a parked snapshot does not reserve an empty draft when there is no completed reply', () => {
  const o = open()
  applyThreadEvent(o, { type: 'thread.snapshot', turns: o.turns, members: o.thread.members })
  assert.equal(o.messages.length, 0)
  applyThreadEvent(o, { type: 'turn.step', persona: 'sprout', waiting: true, label: '↳ raw_tool_name' })
  assert.equal(o.messages.length, 0)
})

test('a notification member starts a generating slot without turn.started and keeps its view key on prose', () => {
  const o = open(); o.turns = []
  applyThreadEvent(o, { type: 'members', members: [{ ...o.thread.members[0], working: true }] })
  assert.equal(o.messages.length, 1)
  const draft = o.messages[0]
  assert.equal(showReply(o, draft), true)
  applyThreadEvent(o, { type: 'message.delta', persona: 'sprout', draft: 'A notification reply.' })
  assert.equal(o.messages[0].viewKey, draft.viewKey)
})

test('task titles remove only the leading dispatch prefix; running times stay compact or unknown', () => {
  assert.equal(taskTitle('Brief Interface: Claude Code 2.1.322, Fable 5.2'), 'Claude Code 2.1.322, Fable 5.2')
  assert.equal(taskTitle('↳ Brief a project: Review the phone layout'), 'Review the phone layout')
  assert.equal(taskTitle('Review: the brief and layout'), 'Review: the brief and layout')
  assert.equal(taskTitle(null), 'background work')
  const now = Date.parse('2026-11-11T00:36:00Z')
  assert.equal(taskElapsed('2026-11-11T00:35:20Z', now), '<1 min')
  assert.equal(taskElapsed('2026-11-11T00:34:00Z', now), '2 min')
  assert.equal(taskElapsed(null, now), null)
})
