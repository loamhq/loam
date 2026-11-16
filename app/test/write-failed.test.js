import test from 'node:test'
import assert from 'node:assert/strict'
import { createTranscript, apply, applyAll, prependEvents } from '../public/transcript.js'

const reason = "couldn't save this reply: the server's disk is full"
const sentence = `${reason}. This reply may not be there after a reload.`
const user = (seq, text = 'Please review the plan.') => ({ type: 'broker.user_message', seq, text })
const reply = (seq) => ({ type: 'assistant', seq, message: { id: `reply-${seq}`, content: [{ type: 'text', text: 'The plan is ready.' }] } })
const result = (seq, extra = {}) => ({ type: 'result', seq, is_error: false, ...extra })
const snapshot = (record_seq, turns, extra = {}) => ({ type: 'broker.session', session_id: 'fixture', record_seq, turns, status: 'failed', ...extra })
const failure = { type: 'broker.write_failed', reason, from_seq: 3 }
const notices = (state) => state.timeline.filter((n) => n.kind === 'save_notice')

test('a live save notice stays under the reply, is quiet until the top-level result, and never advances seq', () => {
  const state = applyAll(createTranscript(), [user(1), reply(2), failure, failure])
  const notice = notices(state)[0]
  assert.equal(notices(state).length, 1)
  assert.equal(notice.text, sentence)
  assert.equal(notice.tone, 'quiet')
  assert.equal(state.lastSeq, 2)
  assert.deepEqual([...state.seen], [1, 2])
  apply(state, reply(3))
  apply(state, result(4, { parent_tool_use_id: 'subagent' }))
  assert.equal(state.timeline.at(-1), notice)
  assert.equal(notice.tone, 'quiet')
  apply(state, result(5))
  assert.equal(state.timeline.at(-1), notice)
  assert.equal(notice.tone, 'bad')
  apply(state, failure)
  apply(state, snapshot(5, [{ not_saved: reason }]))
  assert.equal(notices(state).length, 1)
  assert.equal(notices(state)[0], notice, 'the durable record updates the live line')
})

test('a reloaded gap needs no result or assistant event and does not invent seq or usage', () => {
  const state = applyAll(createTranscript(), [snapshot(1, [{ not_saved: reason }]), user(1)])
  assert.equal(state.timeline[0].kind, 'user')
  assert.equal(state.timeline[1].text, sentence)
  assert.equal(state.timeline[1].tone, 'bad')
  assert.equal(state.lastSeq, 1)
  assert.equal(state._results.length, 0)
  apply(state, snapshot(1, [{ not_saved: reason }]))
  assert.equal(notices(state).length, 1)
})

test('an entirely empty failed log still labels its lost reply from the durable record', () => {
  const state = apply(createTranscript(), snapshot(0, [{ not_saved: reason }]))
  assert.equal(notices(state).length, 1)
  assert.equal(notices(state)[0].text, sentence)
  assert.equal(state.lastSeq, null)
  apply(state, user(1, 'Try again.'))
  assert.equal(state.timeline[0].kind, 'save_notice', 'the old gap stays above the new turn')
  assert.equal(notices(state).length, 1)
})

test('the durable failed record can settle a live notice whose result was missed', () => {
  const state = applyAll(createTranscript(), [user(1), failure, snapshot(1, [{ not_saved: reason }])])
  const notice = notices(state)[0]
  assert.equal(notices(state).length, 1)
  assert.equal(notice.tone, 'bad')
  apply(state, user(2, 'Try again.'))
  apply(state, reply(3))
  assert.equal(notices(state).length, 1)
  assert.equal(state.timeline[1], notice, 'the settled warning stays with the failed turn')
})

test('a failed reply is labelled when only session init survived in the log', () => {
  const state = applyAll(createTranscript(), [snapshot(1, [{ not_saved: reason }]), { type: 'system', subtype: 'init', seq: 1 }])
  assert.equal(notices(state).length, 1)
  assert.equal(notices(state)[0].text, sentence)
  assert.equal(state.lastSeq, 1)
})

test('a failed first write reuses the live notice when the saved log is empty', () => {
  const state = applyAll(createTranscript(), [user(1), failure, result(2)])
  const notice = notices(state)[0]
  apply(state, snapshot(0, [{ not_saved: reason }]))
  assert.equal(notices(state).length, 1)
  assert.equal(notices(state)[0], notice)
  assert.equal(state.timeline.at(-1), notice, 'the live reply stays above its warning')
  apply(state, user(3, 'Try again.'))
  assert.equal(state.timeline.at(-2), notice, 'the new turn stays below the old warning')
})

test('a result replayed after its durable warning reuses that warning', () => {
  const state = applyAll(createTranscript(), [user(1), failure, snapshot(1, [{ not_saved: reason }])])
  const notice = notices(state)[0]
  apply(state, result(2))
  apply(state, snapshot(2, [{ not_saved: reason }]))
  assert.equal(notices(state).length, 1)
  assert.equal(state.timeline.at(-1), notice)
})

test('a lost result between saved turns labels its own gap and preserves their cost/model', () => {
  const turns = [{ model: 'first', cost_delta_usd: 0.1 }, { not_saved: reason }, { model: 'last', cost_delta_usd: 0.3 }]
  const state = applyAll(createTranscript(), [snapshot(8, turns), user(1), reply(2), result(3), user(4), reply(5), user(6), reply(7), result(8)])
  const gap = state.timeline.indexOf(notices(state)[0])
  assert.equal(state.timeline[gap - 1], state._messages.get('reply-5'))
  assert.equal(state.timeline[gap + 1].seq, 6)
  assert.deepEqual(state._results.map((r) => [r.model, r.cost]), [['first', 0.1], ['last', 0.3]])
})

test('saved results with not_saved, consecutive gaps, and a later working turn each keep one notice', () => {
  const turns = [{ not_saved: reason }, { not_saved: reason }, { not_saved: reason }]
  const state = applyAll(createTranscript(), [snapshot(6, turns, { status: 'working' }), user(1), result(2), user(3), user(4), user(5), reply(6)])
  assert.equal(notices(state).length, 3)
  assert.ok(notices(state).every((n) => n.tone === 'bad'))
  assert.equal(state.timeline.at(-1).kind, 'assistant', 'the in-flight turn is not labelled as an older failure')
})

test('loading earlier places the unsaved gap in history without moving the reconnect cursor', () => {
  const state = applyAll(createTranscript(), [snapshot(5, [{ not_saved: reason }, { model: 'last', cost_delta_usd: 0.3 }], { window_first_seq: 3, turns_before: 1 }), user(3), reply(4), result(5)])
  assert.equal(notices(state).length, 0)
  prependEvents(state, [snapshot(5, [], { window_first_seq: 1, turns_before: 0 }), user(1), reply(2)])
  assert.equal(state.lastSeq, 5)
  assert.equal(notices(state).length, 1)
  const at = state.timeline.indexOf(notices(state)[0])
  assert.equal(state.timeline[at - 1], state._messages.get('reply-2'))
  assert.equal(state.timeline[at + 1].seq, 3)
  assert.equal(state._results[0].model, 'last')
})

test('older brokers and unknown fields add no notice; malformed reasons stay harmless', () => {
  for (const value of [undefined, null, '', false, {}, 42]) {
    const state = applyAll(createTranscript(), [snapshot(3, [{ not_saved: value, future_field: true }]), user(1), reply(2), result(3), { ...failure, reason: value }])
    assert.equal(notices(state).length, 0)
    assert.equal(state.lastSeq, 3)
  }
})
