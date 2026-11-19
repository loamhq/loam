import test from 'node:test'
import assert from 'node:assert/strict'
import { applyThreadEvent, withMarks } from '../public/thread-record.js'

// A turn's mark (2026-11-19, batch B step 5): host state drawn on the turn,
// never a message in the record.

const open = () => ({ thread: { id: 't-1', members: [{ persona: 'ada' }] }, messages: [], typing: new Set(), permissions: new Map() })

test('a mark arrives on the stream, sits after what was said before it, and goes when that member next starts', () => {
  const o = open()
  o.messages = [
    { id: 'm1', author: 'owner', kind: 'message', text: 'hi', at: '2026-11-19T10:00:00Z' },
    { id: 'm2', author: 'ada', kind: 'message', text: 'later', at: '2026-11-19T10:05:00Z' },
  ]
  applyThreadEvent(o, { type: 'turn.mark', persona: 'ada', text: "Ada's turn failed: no seat", at: '2026-11-19T10:01:00Z' })
  assert.equal(o.messages.length, 2, 'nothing is added to the record')
  const drawn = withMarks(o.messages, o.marks)
  assert.deepEqual(drawn.map((m) => m.id), ['m1', 'mark:ada', 'm2'])
  assert.equal(drawn[1].kind, 'system')
  assert.equal(drawn[1].mark, true)
  applyThreadEvent(o, { type: 'turn.started', persona: 'ada' })
  assert.deepEqual(o.marks, [])
})

test('a snapshot carries the marks; a cleared mark and the thread\'s own mark', () => {
  const o = open()
  applyThreadEvent(o, { type: 'thread.snapshot', members: o.thread.members, turns: [],
    marks: [{ persona: null, text: 'Nobody to reach: bring someone in first', at: '2026-11-19T10:00:00Z' },
            { persona: 'ada', text: 'Ada is archived', at: '2026-11-19T10:00:01Z' }] })
  assert.equal(o.marks.length, 2)
  applyThreadEvent(o, { type: 'turn.mark', persona: 'ada', text: null })
  assert.deepEqual(o.marks.map((m) => m.persona), [null])
  const drawn = withMarks([{ id: 'draft:bo', author: 'bo', streaming: true }], o.marks)
  assert.deepEqual(drawn.map((m) => m.id), ['mark:', 'draft:bo'], 'before a reply still coming in')
  assert.equal(withMarks(o.messages, []), o.messages)
})
