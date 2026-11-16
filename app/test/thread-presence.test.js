import test from 'node:test'
import assert from 'node:assert/strict'
import { applyThreadEvent, messageRecipients } from '../public/thread-record.js'
import { backgroundCount, backgroundWait, replyLabels, mentionChoices, threadWaiting } from '../public/thread-experience.js'
import { memberTakingTurn } from '../public/thread-steps.js'
import { createThreads } from '../public/threads.js'
import { installDom } from './fakedom.js'
import { all, find, text } from './browser.js'

const open = () => ({ thread: { id: 'one', members: [{ persona: 'sprout', engine: 'codex', working: true, background: 2 }] }, messages: [], typing: new Set(), permissions: new Map() })
test('members and reconnect snapshots replace the whole array, including removals, cleared counts and missing old fields', () => {
  const o = open(), previous = o.thread.members
  const members = [{ persona: 'quill', engine: 'claude-code', working: false, busy: true, background: 3 }]
  applyThreadEvent(o, { type: 'members', members, background: 3, working: 0 })
  assert.equal(o.thread.members, members); assert.notEqual(o.thread.members, previous)
  assert.equal(o.thread.background, 3); assert.equal(o.thread.working, 0)
  assert.equal(previous[0].background, 2, 'the stream does not mutate the old list reading')
  applyThreadEvent(o, { type: 'members', members: [{ persona: 'quill', background: 0, working: false }], background: 0, working: 0 })
  assert.equal(backgroundCount(o.thread.members[0]), 0)
  applyThreadEvent(o, { type: 'thread.snapshot', members: [], turn: null, pending: [] })
  assert.deepEqual(o.thread.members, []); assert.equal(o.thread.background, undefined)
  applyThreadEvent(o, { type: 'thread.snapshot', turn: null })
  assert.deepEqual(o.thread.members, [], 'an older snapshot without members is no new reading')
})
test('a background count is separate from a turn, including notification work with no turn.started', () => {
  const o = open(), member = { persona: 'sprout', working: false, busy: true, background: 2 }
  o.thread.members = [member]; o.typing.add('sprout')
  assert.equal(memberTakingTurn(o.thread, member, o, { state: 'working' }), false)
  assert.equal(threadWaiting(o, 'sprout', 'Sprout', 0).text, 'Sprout is waiting on 2 background tasks')
  member.background = 1; assert.equal(backgroundWait(member), 'waiting on 1 background task')
  member.background = 0; assert.equal(threadWaiting(o, 'sprout', 'Sprout', 0), null)
  member.working = true; o.typing.clear()
  assert.equal(memberTakingTurn(o.thread, member, o, { state: 'idle' }), true)
  assert.match(threadWaiting(o, 'sprout', 'Sprout', 0).text, /Thinking/)
  for (const record of [{}, { background: null }, { background: -1 }, { background: 1.5 }]) assert.equal(backgroundCount(record), 0)
})
test('thread-list activity stays in the subtitle and icon, without separate counts or dots', () => {
  installDom()
  const threads = createThreads({ redraw() {}, agents: () => [{ id: 'sprout', name: 'Sprout' }], compose: {}, nav: () => null })
  threads.st.threads = [{ id: 'one', title: 'Some work', members: [{ persona: 'sprout', background: 9, busy: true }], background: 2, working: 0, last: { author: 'sprout', text: 'A reply', at: new Date().toISOString() } }]
  let row = find(threads.view(false), 'tt-row')
  assert.equal(all(row, 'srow-bg').length, 0)
  assert.equal(text(find(row, 'srow-name')), 'Sprout waiting')
  assert.equal(all(row, 'beating').length, 0)
  threads.st.threads[0].working = 1
  threads.st.threads[0].members[0].working = true
  row = find(threads.view(false), 'tt-row'); assert.equal(all(row, 'beating').length, 0)
  assert.equal(all(row, 'tt-icon-working').length, 1)
  delete threads.st.threads[0].background
  row = find(threads.view(false), 'tt-row'); assert.equal(all(row, 'srow-bg').length, 0)
  threads.pause()
})
test('reply provider/model names are historical facts, with old, unknown and unavailable-list fallbacks', () => {
  const providers = [{ id: 'codex', name: 'Codex', connected: false, models: [{ id: 'gpt-example', name: 'GPT Example' }] }]
  assert.deepEqual(replyLabels({ engine: 'codex', model: 'gpt-example' }, providers), { provider: 'Codex', model: 'GPT Example' })
  assert.deepEqual(replyLabels({ engine: 'codex' }, providers), { provider: 'Codex', model: null })
  assert.deepEqual(replyLabels({ engine: 'codex', model: 'future-id' }, providers), { provider: 'Codex', model: 'future-id' })
  assert.deepEqual(replyLabels({ engine: 'codex', model: 'gpt-example' }), { provider: 'codex', model: 'gpt-example' })
  assert.deepEqual(replyLabels({}), { provider: null, model: null })
})
test('Everyone is first and filters by either alias, without hiding ordinary agent matches', () => {
  const agents = [{ id: 'sprout', name: 'Sprout' }, { id: 'calla', name: 'Calla' }]
  assert.equal(mentionChoices(agents)[0].id, 'all')
  for (const query of ['all', 'Every', 'EVERYONE']) assert.equal(mentionChoices(agents, query)[0].id, 'all')
  assert.equal(mentionChoices(agents, 'all')[1].id, 'calla')
  assert.deepEqual(mentionChoices(agents, 'spr').map((a) => a.id), ['sprout'])
  assert.deepEqual(mentionChoices(agents, 'unmatched'), [])
})
test('Everyone model recipients cover all members and a missing default, with named members first and no duplicates', () => {
  const agents = [{ id: 'sprout' }, { id: 'quill' }, { id: 'scout' }]
  const thread = { default: 'scout', members: [{ persona: 'sprout', engine: 'codex', model: null }, { persona: 'quill', engine: 'claude-code', model: 'swift' }] }
  for (const tag of ['@all', '@EVERYONE']) {
    const members = messageRecipients(thread, `${tag} @quill @quill`, agents)
    assert.deepEqual(members.map((m) => m.persona), ['quill', 'sprout', 'scout'])
    assert.equal(members[1].engine, 'codex')
  }
  assert.deepEqual(messageRecipients({ default: null, members: [] }, '@all', agents), [])
  assert.deepEqual(messageRecipients(thread, 'mail@all.com', agents), [])
})
