import test from 'node:test'
import assert from 'node:assert/strict'
import { applyThreadEvent, messageSegments } from '../public/thread-record.js'
import { showReply, memberGenerating } from '../public/thread-experience.js'

test('thread.snapshot hydrates live helper step and did list on mid-turn open', () => {
  const open = {
    thread: { id: 't1', members: [{ persona: 'henry', engine: 'codex', model: 'gpt-6.2-sol', working: true, background: 0 }] },
    messages: [{ id: 'm1', author: 'you', kind: 'message', text: 'What do you think of City of Brass?' }],
    typing: new Set(),
    permissions: new Map(),
  }
  applyThreadEvent(open, {
    type: 'thread.snapshot',
    thread: 't1',
    members: open.thread.members,
    turns: [{
      persona: 'henry',
      engine: 'codex',
      model: 'gpt-6.2-sol',
      started_at: '2026-11-26T21:16:00Z',
      step: '↳ webSearch: City of Brass critics reviews',
      steps: 3,
      did: [
        { label: 'Agent: critics' },
        { label: 'Agent: readers' },
        { label: '↳ webSearch: City of Brass critics reviews' },
      ],
    }],
    pending: [],
  })
  assert.equal(memberGenerating(open, 'henry'), true)
  const draft = open.messages.find((m) => m.id === 'draft:henry')
  assert.ok(draft, 'mid-turn snapshot reserves streaming draft')
  assert.equal(showReply(open, draft), true)
  assert.equal(open.steps.get('henry')?.label, '↳ webSearch: City of Brass critics reviews')
  assert.deepEqual(messageSegments(draft), [{
    did: [
      { label: 'Agent: critics' },
      { label: 'Agent: readers' },
      { label: '↳ webSearch: City of Brass critics reviews' },
    ],
  }])
})

test('quiet notification turn flipping working true then false outside activeTurns leaves no draft or step state', () => {
  const open = {
    thread: { id: 't1', members: [{ persona: 'henry', engine: 'codex', working: false, background: 0 }] },
    messages: [
      { id: 'm1', author: 'you', kind: 'message', text: 'Question' },
      { id: 'm2', author: 'henry', kind: 'message', text: 'Complete answer.' },
    ],
    typing: new Set(),
    permissions: new Map(),
  }
  applyThreadEvent(open, {
    type: 'members',
    members: [{ persona: 'henry', engine: 'codex', working: true, background: 0 }],
    working: 1,
    background: 0,
  })
  assert.ok(open.messages.some((m) => m.id === 'draft:henry'))
  applyThreadEvent(open, {
    type: 'members',
    members: [{ persona: 'henry', engine: 'codex', working: false, background: 0 }],
    working: 0,
    background: 0,
  })
  assert.equal(open.messages.some((m) => m.id === 'draft:henry'), false)
  assert.equal(open.typing.has('henry'), false)
  assert.equal(open.steps.has('henry'), false)
  assert.equal(open.progress.has('henry'), false)
  assert.equal(open.waiting.has('henry'), false)
  assert.equal(open.messages.length, 2)
})
