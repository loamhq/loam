import { threadSpeechSet } from '../../plugins/_listed/read-aloud/page/reply.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import { threadWaiting, composerModel, replyChip } from '../public/thread-experience.js'
import { applyThreadEvent } from '../public/thread-record.js'
import { awaitingPhrase, AWAIT_FIRST_MS, AWAIT_EVERY_MS, AWAIT_FADE_MS } from '../public/awaiting.js'

const open = () => ({ thread: { id: 'one', members: [] }, messages: [], typing: new Set(), permissions: new Map() })
test('the composer uses the current alias target before older replies, while reply chips keep their recorded version', () => {
  const member = { persona: 'sprout', engine: 'claude-code', model: 'fable' }
  const reply = { author: 'sprout', engine: 'claude-code', model: 'claude-fable-5-1', text: 'An older reply.' }
  const providers = [{ id: 'claude-code', connected: false, models: [{ id: 'fable', name: 'Fable', resolves_to: 'claude-fable-5-2' }] }]
  for (const messages of [[], [reply], [{ ...reply, streaming: true }]]) {
    assert.deepEqual(composerModel(member, messages, providers), { name: 'Fable 5.2', title: "Agent's choice: Fable (now Fable 5.2)" })
  }
  assert.equal(replyChip(reply, providers).model, 'Fable 5.1')
  assert.equal(composerModel({ ...member, model: reply.model }, [reply], providers).name, 'Fable 5.1')
  assert.equal(member.model, 'fable')
  providers[0].models[0].resolves_to = null
  assert.deepEqual(composerModel(member, [reply], providers), { name: 'Fable', title: "Agent's choice: Fable" }, 'explicit unknown is not an older broker')
  delete providers[0].models[0].resolves_to
  assert.equal(composerModel(member, [reply], providers).name, 'Fable 5.1', 'an older broker falls back to the latest reply')
  assert.equal(composerModel(member, [], providers).name, 'Fable', 'without a target or reply, use the catalog')
})
test('long-context and indirect Claude aliases use their supplied target; concrete Codex models remain concrete', () => {
  const providers = [{ id: 'claude-code', models: [
    { id: 'fable[1m]', name: 'Fable (1M context)', resolves_to: 'claude-fable-5-2' },
    { id: 'best', name: 'Best available', resolves_to: 'claude-opus-5-5' },
  ] }, { id: 'codex', models: [{ id: 'gpt-6.1-sol', name: 'GPT-6.1-Sol' }] }]
  assert.equal(composerModel({ engine: 'claude-code', model: 'fable[1m]' }, [], providers).name, 'Fable 5.2')
  assert.equal(composerModel({ engine: 'claude-code', model: 'best' }, [], providers).name, 'Opus 5.5')
  assert.equal(composerModel({ engine: 'codex', model: 'gpt-6.1-sol' }, [], providers).name, 'GPT-6.1-Sol')
})
test('an alias borrows only the latest completed matching member/provider version without changing the choice', () => {
  const member = { persona: 'sprout', engine: 'claude-code', model: 'opus' }
  const providers = [{ id: 'claude-code', models: [{ id: 'opus', name: 'Opus' }] }]
  const reply = { author: 'sprout', engine: 'claude-code', model: 'claude-opus-5-5', text: 'A reply.' }
  assert.deepEqual(composerModel(member, [reply], providers), { name: 'Opus 5.5', title: "Agent's choice: Opus (latest reply used claude-opus-5-5)" })
  for (const messages of [[], [{ ...reply, author: 'quill' }], [{ ...reply, streaming: true }], [{ ...reply, engine: 'codex' }], [{ ...reply, model: 'claude-sonnet-5-5' }]]) {
    assert.deepEqual(composerModel(member, messages, providers), { name: 'Opus', title: "Agent's choice: Opus" })
  }
  for (const latest of [{ ...reply, model: null }, { ...reply, engine: 'codex' }, { ...reply, model: 'claude-sonnet-5-5' }]) {
    assert.equal(composerModel(member, [reply, latest], providers).name, 'Opus', 'an older matching reply cannot outrank the latest reply')
  }
  assert.equal(composerModel({ ...member, model: 'claude-opus-5-4' }, [reply], providers).name, 'Opus 5.4')
  assert.equal(member.model, 'opus')
  assert.equal(composerModel(member, [reply, { ...reply, text: '', model: 'opus' }], providers).name, 'Opus 5.5', 'an empty task record is not the latest spoken reply')
})
test('thread words use the classic rotation, fade and exact next-draw timing, with the agent name', () => {
  const o = open(); applyThreadEvent(o, { type: 'turn.started', persona: 'scout' })
  for (const elapsed of [0, 5000, AWAIT_FIRST_MS - AWAIT_FADE_MS, AWAIT_FIRST_MS, AWAIT_FIRST_MS + 5 * AWAIT_EVERY_MS]) {
    const { phrase, ...clock } = awaitingPhrase(elapsed)
    assert.deepEqual(threadWaiting(o, 'scout', 'Scout', elapsed), { text: `Scout · ${phrase}…`, ...clock })
  }
  o.typing.add('quill')
  assert.match(threadWaiting(o, 'quill', 'Quill', AWAIT_FIRST_MS).text, /^Quill · Digging in/)
})
test('the line distinguishes generating gaps, streaming replies and informative waits', () => {
  const o = open(), event = (e) => applyThreadEvent(o, { persona: 'scout', ...e })
  event({ type: 'turn.started' }); assert.ok(threadWaiting(o, 'scout', 'Scout', 0))
  event({ type: 'message.delta', draft: 'First words' }); assert.equal(threadWaiting(o, 'scout', 'Scout', 0).text, 'Scout is replying…')
  event({ type: 'turn.step', label: 'Read notes' }); assert.match(threadWaiting(o, 'scout', 'Scout', 0).text, /Thinking/)
  event({ type: 'turn.step', label: 'Background task', waiting: true }); assert.deepEqual(threadWaiting(o, 'scout', 'Scout', 999999), { text: 'Scout is waiting on a background task', out: false, nextMs: null })
  event({ type: 'turn.waiting', text: 'Scout is waiting for a free seat.' }); assert.equal(threadWaiting(o, 'scout', 'Scout', 0).text, 'Scout is waiting for a free seat.')
  event({ type: 'message.delta', draft: 'Next words' }); assert.equal(threadWaiting(o, 'scout', 'Scout', 0).text, 'Scout is replying…')
  event({ type: 'turn.finished' }); assert.equal(threadWaiting(o, 'scout', 'Scout', 0), null)
})
test('permission cards replace decoration for their agent', () => {
  const o = open(); o.typing.add('scout'); o.permissions.set('p', { persona: 'scout' })
  assert.equal(threadWaiting(o, 'scout', 'Scout', 0), null)
})
test('speech is one completed agent message: prose segments only, stable across replay, distinct across message, author and thread', () => {
  const t = { id: 'one', title: 'A thought' }, m = { id: 'm1', author: 'scout', kind: 'message', text: 'Last words.', segments: [{ text: ' First words. ' }, { did: [{ label: 'SECRET TOOL' }] }] }
  const set = threadSpeechSet(t, m, 'Scout', { voice: 'Kore', style: 'Warm and clear' })
  assert.equal(set.text, 'First words.\n\nLast words.'); assert.equal(set.words, 4)
  assert.deepEqual(set.context.request, { voice: 'Kore', style: 'Warm and clear' }); assert.equal(set.context.agent, 'scout')
  assert.equal(set.key, threadSpeechSet(t, JSON.parse(JSON.stringify(m)), 'Scout').key)
  for (const other of [threadSpeechSet(t, { ...m, author: 'quill' }, 'Quill'), threadSpeechSet(t, { ...m, id: 'm2' }, 'Scout'), threadSpeechSet({ ...t, id: 'two' }, m, 'Scout')]) assert.notEqual(set.context.id, other.context.id)
  assert.equal(threadSpeechSet(t, { ...m, streaming: true }, 'Scout'), null)
  assert.equal(threadSpeechSet(t, { ...m, kind: 'system' }, 'Scout'), null)
  assert.equal(threadSpeechSet(t, { ...m, segments: [], text: '' }, 'Scout'), null)
})
