// A card lives exactly as long as its request, and every way it goes leaves one
// line in the thread — fed through the reducer with no agent present, and the
// words read back through the same function the page draws them with.

import test from 'node:test'
import assert from 'node:assert/strict'

import { createTranscript, apply, prependEvents, resolvePermission, dropPermission, foldsAsStep, foldFacts } from '../public/transcript.js'
import { departureLine, askTarget, grantWords, askReason } from '../public/asks.js'

const WS = '/home/u/labs/ada'
const ask = (request_id, seq, extra = {}) => ({
  type: 'broker.permission_request',
  session_id: 's',
  request_id,
  kind: 'approval',
  tool_name: 'Read',
  input: { file_path: '/home/u/agents/bin/session-broker' },
  decision_reason: 'Path is outside allowed working directories',
  decision_reason_type: 'workingDir',
  seq,
  ...extra,
})
const snap = (record_seq, pending, state = 'running') => ({ type: 'broker.session', session_id: 's', agent: 'ada', workspace: WS, state, record_seq, pending_permissions: pending, turns: [] })
const lines = (t) => t.timeline.filter((n) => n.kind === 'permission').map((n) => departureLine(n, 'Ada', WS))

test('each closing the host names leaves its own line, and the card goes', () => {
  const words = {
    allow: 'Allowed · Read · agents/bin/session-broker',
    deny: 'Denied · Read · agents/bin/session-broker',
    expired: 'Not answered · Ada went on without it · Read · agents/bin/session-broker',
    cancelled: 'Withdrawn · Read · agents/bin/session-broker',
    ended: 'Ended with the chat · Read · agents/bin/session-broker',
  }
  for (const [behavior, line] of Object.entries(words)) {
    const t = createTranscript()
    apply(t, snap(0, []))
    apply(t, ask('r1', 1))
    assert.equal(t.permissions.length, 1)
    apply(t, { type: 'broker.permission_resolved', session_id: 's', request_id: 'r1', behavior, kind: 'approval', tool_name: 'Read', seq: 2 })
    assert.equal(t.permissions.length, 0, behavior)
    assert.deepEqual(lines(t), [line])
  }
})

test('an allow that carried the rule reads "Allowed for this chat"; the page’s own answer never claims it', () => {
  const t = createTranscript()
  apply(t, ask('r1', 1))
  resolvePermission(t, 'r1', { behavior: 'allow', updatedPermissions: [{ type: 'addRules' }] })
  assert.deepEqual(lines(t), ['Allowed · Read · agents/bin/session-broker'])
  apply(t, { type: 'broker.permission_resolved', request_id: 'r1', behavior: 'allow', scope: 'session', seq: 2 })
  assert.deepEqual(lines(t), ['Allowed for this chat · Read · agents/bin/session-broker'], 'one line, corrected in place')
})

test('an exit takes every card of the session, each leaving "Ended with the chat"', () => {
  const t = createTranscript()
  apply(t, ask('a', 1))
  apply(t, ask('b', 2, { tool_name: 'Grep', input: { pattern: 'x', path: '/home/u/projects/loam' } }))
  apply(t, { type: 'broker.exit', exit_code: 143, seq: 3 })
  assert.equal(t.permissions.length, 0)
  assert.deepEqual(lines(t), ['Ended with the chat · Read · agents/bin/session-broker', 'Ended with the chat · Grep · x in projects/loam'])
})

test('a snapshot that no longer lists a card takes it; one older than the request cannot', () => {
  const t = createTranscript()
  apply(t, ask('a', 5))
  apply(t, snap(4, [])) // written before the request: says nothing of it
  assert.equal(t.permissions.length, 1)
  apply(t, snap(5, [{ request_id: 'a' }]))
  assert.equal(t.permissions.length, 1)
  apply(t, snap(9, [], 'running'))
  assert.equal(t.permissions.length, 0)
  assert.deepEqual(lines(t), ['Ended with the chat · Read · agents/bin/session-broker'])
})

test('a window opened on a dead log draws no card: the record leads, the replay follows', () => {
  // What the page opens on in the chat where four cards would not go: the
  // record says nothing is pending, then the window replays requests that no
  // event ever closed.
  const t = createTranscript()
  apply(t, snap(20, []))
  apply(t, { type: 'broker.user_message', session_id: 's', text: 'go', seq: 10 })
  apply(t, ask('dead-1', 11))
  apply(t, ask('dead-2', 12))
  apply(t, ask('closed', 13))
  apply(t, { type: 'broker.permission_resolved', request_id: 'closed', behavior: 'allow', seq: 14 })
  assert.equal(t.permissions.length, 0)
  assert.deepEqual(lines(t), [
    'Ended with the chat · Read · agents/bin/session-broker',
    'Ended with the chat · Read · agents/bin/session-broker',
    'Allowed · Read · agents/bin/session-broker',
  ])
  // A live request after the record is a card.
  apply(t, ask('live', 21))
  assert.deepEqual(t.permissions.map((p) => p.request_id), ['live'])
})

test('load earlier: an older page with an unclosed request draws its line, never a card, and once', () => {
  const t = createTranscript()
  apply(t, snap(30, []))
  apply(t, { type: 'broker.permission_resolved', request_id: 'x', behavior: 'deny', tool_name: 'Read', seq: 25 })
  prependEvents(t, [snap(30, []), ask('x', 20), ask('old', 21)])
  assert.equal(t.permissions.length, 0)
  assert.deepEqual(lines(t), ['Ended with the chat · Read · agents/bin/session-broker', 'Denied · Read · agents/bin/session-broker'])
})

test('a 404 from the answer route takes the card and leaves a line', () => {
  const t = createTranscript()
  apply(t, ask('a', 1))
  dropPermission(t, 'a')
  assert.equal(t.permissions.length, 0)
  assert.equal(lines(t).length, 1)
})

test('a question that expires leaves a line with its header', () => {
  const t = createTranscript()
  apply(t, { type: 'broker.permission_request', request_id: 'q', kind: 'question', tool_name: 'AskUserQuestion', questions: [{ id: 'n', header: 'Name', question: 'Name?', options: [{ label: 'A' }] }], seq: 1 })
  apply(t, { type: 'broker.permission_resolved', request_id: 'q', kind: 'question', behavior: 'expired', seq: 2 })
  assert.deepEqual(lines(t), ['Not answered · Ada went on without it · Name'])
})

test('the words: target, reason, grant', () => {
  assert.equal(askTarget('Read', { file_path: `${WS}/notes.md` }, WS), 'notes.md')
  assert.equal(askTarget('Glob', { pattern: '**/app.js', path: '/home/u/agents' }, WS), '**/app.js in agents')
  assert.equal(askTarget('Bash', { command: 'ls -la\nrm x' }, WS), 'ls -la')
  assert.equal(askTarget('Read', { file_path: '/etc/hosts' }, WS), '/etc/hosts')
  assert.equal(askReason({ decision_reason_type: 'workingDir', decision_reason: 'x' }, 'Ada'), 'outside Ada’s folders')
  assert.equal(askReason({ decision_reason: 'It deletes a file.' }, 'Ada'), 'It deletes a file.')
  assert.equal(grantWords([{ type: 'addRules', rules: [{ toolName: 'Read', ruleContent: '//home/u/agents/bin/**' }], behavior: 'allow', destination: 'session' }], WS), 'Read under agents/bin')
  assert.equal(grantWords([{ type: 'addDirectories', directories: ['/home/u/agents'], destination: 'session' }], WS), 'everything under agents')
})

test('only session rules are kept to send: a mixed list keeps its session entries, a localSettings-only one keeps none', () => {
  const session = { type: 'addRules', rules: [{ toolName: 'Read', ruleContent: '//home/u/agents/bin/**' }], behavior: 'allow', destination: 'session' }
  const local = { type: 'addRules', rules: [{ toolName: 'Skill', ruleContent: 'deploy' }], behavior: 'allow', destination: 'localSettings' }
  const t = createTranscript()
  apply(t, ask('mixed', 1, { permission_suggestions: [local, session, { ...session, destination: 'projectSettings' }, 'junk'] }))
  apply(t, ask('local', 2, { tool_name: 'Skill', input: { skill: 'deploy' }, permission_suggestions: [local] }))
  const [mixed, onlyLocal] = t.permissions
  assert.deepEqual(mixed.suggestions, [session])
  assert.equal(grantWords(mixed.suggestions, WS), 'Read under agents/bin')
  assert.deepEqual(onlyLocal.suggestions, [], 'a request with no session rule has nothing "for this chat" to send')
})

test('an approval’s line is a step of the fold, counted and never running or failed; a question’s line is not', () => {
  const t = createTranscript()
  apply(t, ask('r1', 1))
  apply(t, { type: 'broker.permission_resolved', session_id: 's', request_id: 'r1', kind: 'approval', behavior: 'deny', tool_name: 'Read', message: 'no', seq: 2 })
  apply(t, { type: 'broker.permission_request', session_id: 's', request_id: 'q1', kind: 'question', tool_name: 'AskUserQuestion', questions: [{ id: 'n', header: 'Name', question: 'Which?', options: [{ label: 'A' }] }], seq: 3 })
  apply(t, { type: 'broker.permission_resolved', session_id: 's', request_id: 'q1', kind: 'question', behavior: 'expired', tool_name: 'AskUserQuestion', message: null, seq: 4 })
  const [approval, question] = t.timeline.filter((n) => n.kind === 'permission')
  assert.equal(foldsAsStep(approval), true)
  assert.equal(foldsAsStep(question), false, 'a question’s line was folded')
  assert.deepEqual(foldFacts([approval]), { count: 1, failed: 0, running: false, thinking: null })
})

// The chat it was seen in, 2026-10-05, as a reopened window: approvals that
// ended with the process, their calls with no result, the exit. The fold holding
// the lines and the calls is a tick, not a spinner.
test('a reopened window — approval ended, its call without a result, the exit — draws a finished fold', () => {
  const t = createTranscript()
  apply(t, { ...snap(5, [], 'exited'), status: 'failed', window_first_seq: 1, turns_before: 3 })
  apply(t, { type: 'assistant', seq: 1, session_id: 's', message: { id: 'm1', role: 'assistant', content: [{ type: 'tool_use', id: 'tu-1', name: 'Read', input: { file_path: '/home/u/agents/bin/session-broker' } }] } })
  apply(t, ask('r1', 2, { tool_use_id: 'tu-1' }))
  apply(t, { type: 'broker.permission_resolved', session_id: 's', request_id: 'r1', kind: 'approval', behavior: 'ended', tool_name: 'Read', message: null, seq: 3 })
  apply(t, { type: 'broker.exit', session_id: 's', exit_code: 143, seq: 4 })
  const steps = [
    ...t.timeline.filter((n) => n.kind === 'assistant').flatMap((n) => n.blocks.filter((b) => b?.type === 'tool')),
    ...t.timeline.filter(foldsAsStep),
  ]
  assert.deepEqual(lines(t), ['Ended with the chat · Read · agents/bin/session-broker'])
  assert.deepEqual(foldFacts(steps), { count: 2, failed: 0, running: false, thinking: null })
})

// The chat it was seen in, 2026-10-06, sanitised: a Glob asked while the next
// call was streaming, that call answered, and the host's record — its
// `pending_permissions` a list of request ids, strings, as the host has always
// sent it — written past both. Read as objects, the ids were all `undefined`
// and the record "proved" the live request closed: no card, a provisional
// ended line, and the Glob's fold still running.
test('a record listing a request by its id keeps the card, however far past it the record is', () => {
  const t = createTranscript()
  apply(t, snap(230, ['r1'], 'running'))
  apply(t, { type: 'assistant', seq: 231, session_id: 's', message: { id: 'm1', role: 'assistant', content: [{ type: 'tool_use', id: 'tu-glob', name: 'Glob', input: { pattern: 'agents/*.md', path: '/home/u' } }] } })
  apply(t, { type: 'stream_event', seq: 233, session_id: 's', event: { type: 'content_block_start', index: 1, content_block: { type: 'tool_use', id: 'tu-read', name: 'Read', input: {} } } })
  apply(t, ask('r1', 235, { tool_name: 'Glob', input: { pattern: 'agents/*.md', path: '/home/u' }, tool_use_id: 'tu-glob' }))
  apply(t, { type: 'assistant', seq: 242, session_id: 's', message: { id: 'm1', role: 'assistant', content: [{ type: 'tool_use', id: 'tu-read', name: 'Read', input: { file_path: '/home/u/labs/ada/notes.md' } }] } })
  apply(t, { type: 'user', seq: 246, session_id: 's', message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'tu-read', content: 'ok' }] } })
  apply(t, { ...snap(246, ['r1']), status: 'needs_input' })
  assert.deepEqual(t.permissions.map((p) => p.request_id), ['r1'], 'the live request has no card')
  assert.deepEqual(lines(t), [], 'a live request was drawn as ended')

  // And opened fresh at its end, the record first and the request replayed below it.
  const opened = createTranscript()
  apply(opened, { ...snap(246, ['r1']), status: 'needs_input', window_first_seq: 231, turns_before: 0 })
  apply(opened, ask('r1', 235, { tool_name: 'Glob' }))
  assert.deepEqual(opened.permissions.map((p) => p.request_id), ['r1'])
  assert.deepEqual(lines(opened), [])
})
