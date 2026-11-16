// The seam, tested with no agent present: recorded events in, the shape the
// page draws out. This is the file that has to keep passing when the agent on
// the other end changes.

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  createTranscript,
  apply,
  applyAll,
  caughtUp,
  resolvePermission,
  noteUserMessage,
  labelUserMessage,
  retrySentence,
  toolSummary,
  foldFacts,
  foldsAsStep,
  blockView,
  inFlight,
  alive,
  contextWords,
  contextFill,
  contextNow,
  compactionWords,
  compactionTurn,
  prependEvents,
  CONTEXT_WARN,
  CONTEXT_WARN_AT,
  modelShortName,
} from '../public/transcript.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))

function fixture(name) {
  return readFileSync(path.join(HERE, 'fixtures', name), 'utf8')
    .split('\n')
    .filter((line) => line.trim() !== '')
    .map((line) => JSON.parse(line))
}

// A session watched from a page that joined it part-way: the record the broker
// sends first says the log reaches `record_seq: 57`, the events replay from
// there, and the snapshot is sent again when the turn at seq 64 is recorded.
const RECORDED = fixture('session-with-subagent.jsonl')

// The snapshots and the events, told apart the way the stream tells them apart:
// an event has a `seq` and a snapshot has none.
const SNAPSHOTS = RECORDED.filter((e) => e.type === 'broker.session')
const EVENTS = RECORDED.filter((e) => e.type !== 'broker.session')

function replayed(events = RECORDED) {
  return applyAll(createTranscript(), events)
}

// Everything the stream has delivered by the time the event numbered `seq`
// arrives: the events up to it, and the snapshots whose record is no later.
function upTo(seq) {
  return RECORDED.filter((e) => (e.type === 'broker.session' ? e.record_seq <= seq : e.seq <= seq))
}

test('the session record is the first thing the page knows', () => {
  const state = replayed()
  assert.equal(state.session.session_id, 's-fixture')
  assert.equal(state.session.agent, 'example')
  assert.equal(state.session.workspace, '/w/example')
})

test('text streams: partial deltas accumulate before the whole message arrives', () => {
  const state = createTranscript()
  for (const event of RECORDED) {
    apply(state, event)
    if (event.seq === 46) {
      const assistantNode = state.timeline.find((n) => n.kind === 'assistant')
      assert.equal(assistantNode.blocks[0].text, 'Look', 'the reply is visible while it is still being written')
      assert.equal(assistantNode.streaming, true)
    }
  }
})

test('the whole message replaces what was streamed rather than doubling it', () => {
  const state = replayed()
  const assistants = state.timeline.filter((n) => n.kind === 'assistant')
  assert.equal(assistants.length, 1, 'one reply, not one per event type')
  assert.equal(assistants[0].blocks[0].text, 'Looking now.')
  assert.equal(assistants[0].streaming, false)
  assert.equal(assistants[0].model, 'claude-opus-5')
})

test('a tool call collapses to one line, with its arguments and its result', () => {
  const state = replayed()
  const tool = state.timeline.find((n) => n.kind === 'assistant').blocks[1]
  assert.equal(tool.type, 'tool')
  assert.equal(tool.name, 'Task')
  assert.equal(tool.input.description, 'survey the directory')
  assert.equal(toolSummary(tool), 'survey the directory')
  assert.equal(tool.result, 'One README, nothing else.')
  assert.equal(tool.isError, false)
})

test("a tool call's arguments are readable from the partial JSON before it closes", () => {
  const state = createTranscript()
  for (const event of RECORDED) {
    apply(state, event)
    if (event.seq === 50) {
      const tool = state.timeline.find((n) => n.kind === 'assistant').blocks[1]
      assert.match(tool.partialInput, /^\{"description"/)
    }
  }
})

test('a subagent nests under the tool call that spawned it, not in the main reply', () => {
  const state = replayed()
  const tool = state.timeline.find((n) => n.kind === 'assistant').blocks[1]
  assert.equal(tool.children.length, 1, "the subagent's reply is inside the call")
  assert.equal(tool.children[0].kind, 'assistant')
  assert.equal(tool.children[0].blocks[0].text, 'I will read the README.')

  // And its own tool call, and that call's result, are inside it too.
  const inner = tool.children[0].blocks[1]
  assert.equal(inner.name, 'Read')
  assert.equal(inner.result, '# example\n')

  // Nothing of the subagent's leaked into the top level.
  const topLevelAssistants = state.timeline.filter((n) => n.kind === 'assistant')
  assert.equal(topLevelAssistants.length, 1)
  assert.equal(topLevelAssistants[0].id, 'msg_main')
})

// ------------------------------------------------ one call, one line (defect 5)

// Every tool block the page would draw, wherever it sits in the tree.
function drawnTools(state) {
  const found = []
  const walkNodes = (nodes) => {
    for (const n of nodes ?? []) {
      if (n?.kind === 'assistant') {
        for (const block of n.blocks) {
          if (!block || block.type !== 'tool') continue
          found.push(block)
          walkNodes(block.children)
        }
      }
    }
  }
  walkNodes(state.timeline)
  return found
}

function countOf(state, name) {
  return drawnTools(state).filter((b) => b.name === name).length
}

test('a tool call is drawn once, whole stream', () => {
  const state = replayed()
  assert.equal(countOf(state, 'Task'), 1)
  assert.equal(countOf(state, 'Read'), 1)
})

test('a replay that starts mid-message draws its tool call once, not twice', () => {
  // What reopening a session does: ask for everything after the last seq this
  // browser saw. Land inside a message and its `message_start` is behind you,
  // so the partials that follow belong to a message the page cannot place.
  // They are dropped; the `assistant` event that follows is the authority.
  const midMessage = RECORDED.filter((e) => e.seq > 44)
  assert.ok(
    midMessage.some((e) => e.event?.type === 'content_block_start' && e.event.content_block?.type === 'tool_use'),
    'the cut really does land after message_start and before the tool block opens',
  )

  const state = replayed(midMessage)
  assert.equal(countOf(state, 'Task'), 1, 'the Task call is one line, not one per place it was mentioned')
  assert.equal(countOf(state, 'Read'), 1)
  assert.equal(state.timeline.filter((n) => n.kind === 'assistant').length, 1, 'no message invented from a stray partial')
  const tool = state.timeline.find((n) => n.kind === 'assistant').blocks[1]
  assert.equal(tool.result, 'One README, nothing else.', 'and it still collects its result')
})

// ------------------------------------- the person's own messages (defect 1)

test("a person's message is on the record and is drawn from the stream", () => {
  const state = replayed()
  const said = state.timeline.filter((n) => n.kind === 'user')
  assert.equal(said.length, 1)
  assert.equal(said[0].text, 'loam-ui smoke test: what is in this directory?')
  assert.equal(said[0].pending, false)
  assert.equal(said[0].seq, 43)
})

test('a message drawn when it was sent is not drawn again when its event arrives', () => {
  const state = createTranscript()
  const line = noteUserMessage(state, 'hello')
  assert.equal(state.timeline.length, 1)
  assert.equal(state.timeline[0].pending, true)

  // The messages route answers with the seq its broker.user_message will carry.
  labelUserMessage(state, line, 12)
  assert.equal(line.pending, false)

  apply(state, { type: 'broker.user_message', session_id: 's', text: 'hello', seq: 12 })
  assert.equal(state.timeline.length, 1, 'the event is this line, not a second one')
  assert.equal(state.timeline[0].seq, 12)
})

test('the same, when the event beats the answer to the send', () => {
  const state = createTranscript()
  const line = noteUserMessage(state, 'hello')
  apply(state, { type: 'broker.user_message', session_id: 's', text: 'hello', seq: 12 })
  assert.equal(state.timeline.length, 1)
  assert.equal(state.timeline[0].pending, false)
  assert.equal(state.timeline[0].seq, 12)

  labelUserMessage(state, line, 12)
  assert.equal(state.timeline.length, 1)
})

test('a message carries its files, and a replayed one draws them from the record', () => {
  // Nothing of this browser is involved: the chips on a chat reopened tomorrow
  // are the `attachments` on the record's own `broker.user_message`.
  const state = createTranscript()
  apply(state, {
    type: 'broker.user_message',
    session_id: 's',
    text: 'what is in this?',
    attachments: [
      { path: '/store/2026-09-21/bank-export-aug.csv', name: 'bank-export-aug.csv', size: 31_744, type: 'text/csv' },
    ],
    seq: 7,
  })
  assert.deepEqual(state.timeline[0].attachments, [
    { path: '/store/2026-09-21/bank-export-aug.csv', name: 'bank-export-aug.csv', size: 31_744, type: 'text/csv' },
  ])

  // A message with none carries an empty list rather than nothing, so the page
  // never asks whether the key is there.
  apply(state, { type: 'broker.user_message', session_id: 's', text: 'and this?', seq: 8 })
  assert.deepEqual(state.timeline[1].attachments, [])
})

test('the line the page drew for a sent file is the event for it, not a second line', () => {
  const state = createTranscript()
  // The files are the host's already — they came back from the uploads, as the
  // objects the record carries — so the line the page drew and the event the
  // broker logs say the same thing.
  const line = noteUserMessage(state, 'have a look', [
    { path: '/store/2026-09-21/a.csv', name: 'a.csv', size: 12, type: 'text/csv' },
  ])
  assert.deepEqual(line.attachments.map((f) => f.name), ['a.csv'])

  labelUserMessage(state, line, 12)
  apply(state, {
    type: 'broker.user_message',
    session_id: 's',
    text: 'have a look',
    attachments: [{ path: '/store/2026-09-21/a.csv', name: 'a.csv', size: 12, type: 'text/csv' }],
    seq: 12,
  })
  assert.equal(state.timeline.length, 1, 'the event is this line, not a second one')
  assert.deepEqual(state.timeline[0].attachments.map((f) => f.name), ['a.csv'])
})

test('when the event beats the answer to the send, the record’s names are the ones kept', () => {
  const state = createTranscript()
  noteUserMessage(state, 'have a look', [{ path: '/store/2026-09-21/a.csv', name: 'a.csv', size: 31_744, type: 'text/csv' }])
  // The host renamed it on the way down — a name already taken in that
  // directory. The record is the authority on what the message carried.
  apply(state, {
    type: 'broker.user_message',
    session_id: 's',
    text: 'have a look',
    attachments: [{ path: '/store/2026-09-21/a-1.csv', name: 'a-1.csv', size: 31_744, type: 'text/csv' }],
    seq: 12,
  })
  assert.equal(state.timeline.length, 1)
  assert.deepEqual(state.timeline[0].attachments, [
    { path: '/store/2026-09-21/a-1.csv', name: 'a-1.csv', size: 31_744, type: 'text/csv' },
  ])
})

test("a message sent from another device is drawn, because nothing here drew it", () => {
  const state = createTranscript()
  apply(state, { type: 'broker.user_message', session_id: 's', text: 'from the phone', seq: 12 })
  assert.equal(state.timeline.length, 1)
  assert.equal(state.timeline[0].text, 'from the phone')
})

test('a `user` event is a tool result and never a person speaking', () => {
  const state = createTranscript()
  apply(state, { type: 'user', seq: 1, message: { role: 'user', content: 'this is not a person' } })
  apply(state, { type: 'user', seq: 2, message: { role: 'user', content: [{ type: 'text', text: 'nor is this' }] } })
  assert.equal(state.timeline.length, 0, 'nothing a `user` event says is drawn as a line')
})

// --------------------------------------------- permissions, resolved (defect 2)

test('a permission request becomes a card carrying the host’s own reason', () => {
  const state = applyAll(createTranscript(), upTo(60))
  assert.equal(state.permissions.length, 1)
  const [asked] = state.permissions
  assert.equal(asked.request_id, 'req-7')
  assert.equal(asked.tool_name, 'Write')
  assert.match(asked.decision_reason, /write guard/)
  assert.equal(asked.input.file_path, '/w/example/notes.md')

  resolvePermission(state, 'req-7')
  assert.equal(state.permissions.length, 0, 'answering it takes the card away')
})

test('a request another client answered takes its card away here too', () => {
  const state = applyAll(createTranscript(), upTo(60))
  assert.equal(state.permissions.length, 1)
  apply(state, { type: 'broker.permission_resolved', session_id: 's-fixture', request_id: 'req-7', behavior: 'allow', tool_name: 'Write', message: null, seq: 62 })
  assert.equal(state.permissions.length, 0)
})

// ------------------------------------------- a question is a form (2026-10-01)
//
// The host sends every provider's asking in one shape. `kind: "question"` is a
// form; anything else — `"approval"`, no kind at all from an older broker, a
// kind not met yet, a question with no questions in it — is the approval card.

const QUESTION = {
  type: 'broker.permission_request',
  session_id: 's-q',
  request_id: 'ask-1',
  kind: 'question',
  tool_name: 'AskUserQuestion',
  provider: 'claude-code',
  message: '',
  questions: [
    {
      id: 'q1',
      header: 'Name',
      question: 'What should the plugin be called?',
      options: [{ label: 'Parlance (Recommended)', description: 'Your way of speaking.', preview: null }, { label: 'Lexicon' }],
      multi: false,
      other: true,
      secret: false,
      required: true,
    },
    { id: 'q2', header: 'Key', question: 'Its passphrase?', options: [], secret: true, required: false },
  ],
  seq: 41,
}

test('a question is read as a question, its labels kept as the model wrote them', () => {
  const state = apply(createTranscript(), QUESTION)
  const [asked] = state.permissions
  assert.equal(asked.kind, 'question')
  assert.equal(asked.message, null, 'an empty sentence is no sentence')
  assert.deepEqual(asked.questions[0].options, [
    { label: 'Parlance (Recommended)', description: 'Your way of speaking.', preview: null },
    { label: 'Lexicon', description: null, preview: null },
  ])
  // A question with no options is answered by typing, whatever `other` said;
  // `required` is true unless it says false, and `multi`/`secret` the reverse.
  assert.deepEqual(
    asked.questions.map(({ id, multi, other, secret, required }) => ({ id, multi, other, secret, required })),
    [
      { id: 'q1', multi: false, other: true, secret: false, required: true },
      { id: 'q2', multi: false, other: true, secret: true, required: false },
    ],
  )
})

test('no kind, kind "approval" and a kind never met are all the approval card', () => {
  for (const kind of [undefined, 'approval', 'survey']) {
    const state = apply(createTranscript(), { type: 'broker.permission_request', request_id: 'r', kind, tool_name: 'Bash', input: { command: 'ls' }, seq: 1 })
    assert.equal(state.permissions[0].kind, 'approval', `kind ${kind}`)
    assert.deepEqual(state.permissions[0].input, { command: 'ls' })
  }
})

test('a question with no questions, an empty list, or one without an id falls back to the approval card', () => {
  const cases = [
    { questions: undefined, input: { question: 'raw' } },
    { questions: [] },
    { questions: [{ header: 'no id', question: 'x', options: [] }] },
  ]
  for (const [i, c] of cases.entries()) {
    const state = apply(createTranscript(), { ...QUESTION, ...c, seq: i + 1 })
    const [asked] = state.permissions
    assert.equal(asked.kind, 'approval', `case ${i}`)
    assert.equal(asked.tool_name, 'AskUserQuestion')
  }
  const raw = apply(createTranscript(), { ...QUESTION, questions: undefined, input: { question: 'raw' } })
  assert.deepEqual(raw.permissions[0].input, { question: 'raw' })
  const empty = apply(createTranscript(), { ...QUESTION, questions: [] })
  assert.deepEqual(empty.permissions[0].input, { questions: [] }, 'what it did carry is the raw thing to show')
})

test('an answered question leaves one line with what was chosen, however many times it is said', () => {
  const state = apply(createTranscript(), QUESTION)
  // Answered here: the line is drawn at once from the body that was sent...
  resolvePermission(state, 'ask-1', { behavior: 'allow', answers: { q1: ['Parlance (Recommended)'], q2: [] } })
  assert.equal(state.permissions.length, 0)
  const lines = () => state.timeline.filter((n) => n.kind === 'answer')
  assert.equal(lines().length, 1)
  assert.deepEqual(lines()[0].answers, { q1: ['Parlance (Recommended)'], q2: [] })
  assert.equal(lines()[0].questions[0].header, 'Name')
  // ...and the resolution that follows lands on the same line.
  apply(state, { type: 'broker.permission_resolved', session_id: 's-q', request_id: 'ask-1', kind: 'question', behavior: 'allow', answers: { q1: ['Parlance (Recommended)'], q2: [] }, seq: 42 })
  assert.equal(lines().length, 1)
  assert.equal(lines()[0].seq, 42)
})

test('a question resolved elsewhere, or declined, is a line too; an approval is not', () => {
  const state = apply(createTranscript(), QUESTION)
  apply(state, { type: 'broker.permission_resolved', request_id: 'ask-1', kind: 'question', behavior: 'deny', message: 'declined', seq: 42 })
  const [line] = state.timeline.filter((n) => n.kind === 'answer')
  assert.equal(line.behavior, 'deny')
  assert.equal(line.answers, null)

  const approval = apply(createTranscript(), { type: 'broker.permission_request', request_id: 'r', tool_name: 'Bash', input: {}, seq: 1 })
  resolvePermission(approval, 'r', { behavior: 'allow' })
  apply(approval, { type: 'broker.permission_resolved', request_id: 'r', behavior: 'allow', seq: 2 })
  assert.equal(approval.timeline.filter((n) => n.kind === 'answer').length, 0)
})

test('a replayed session raises no card for a question it can see was answered', () => {
  const state = replayed()
  assert.equal(state.permissions.length, 0, 'the whole stream was read; the question is gone')
})

test('a record that has not caught up cannot put an answered card back', () => {
  const state = replayed()
  apply(state, {
    type: 'broker.session',
    record_seq: 62,
    session_id: 's-fixture',
    agent: 'example',
    state: 'running',
    pending_permissions: [{ request_id: 'req-7', tool_name: 'Write', input: {}, decision_reason: 'stale' }],
    turns: [],
  })
  assert.equal(state.permissions.length, 0)
})

test('a session opened mid-question shows the question from the record', () => {
  const state = createTranscript()
  apply(state, {
    type: 'broker.session',
    record_seq: 99,
    session_id: 's-open',
    agent: 'example',
    state: 'running',
    pending_permissions: [{ request_id: 'req-9', tool_name: 'Bash', input: { command: 'ls' }, decision_reason: 'asking' }],
    turns: [],
  })
  assert.equal(state.permissions.length, 1)
  assert.equal(state.permissions[0].request_id, 'req-9')
})

// ------------------------- the model and the cost, off the snapshot (defects 3, 4)

test("the turn's model is the record's, not the models `modelUsage` billed", () => {
  const state = replayed()
  const turn = state.timeline.find((n) => n.kind === 'result')
  assert.equal(turn.model, 'claude-opus-5')
  const billed = Object.keys(RECORDED.find((e) => e.type === 'result').modelUsage)
  assert.equal(billed[0], 'claude-haiku-4-5-20251001', 'the first model `modelUsage` names is not the session\u2019s')
})

test('a turn is unpriced and unnamed until the snapshot that records it arrives', () => {
  // Everything up to and including the `result` event, and not the snapshot the
  // broker sends a moment later. This is what a live page holds for that moment.
  const state = applyAll(createTranscript(), upTo(63).concat(EVENTS.filter((e) => e.seq === 64)))
  const turn = state.timeline.find((n) => n.kind === 'result')
  assert.equal(turn.cost, null, 'unknown, which the page shows as nothing')
  assert.equal(turn.model, null, 'and the model is the record\u2019s or nothing')

  // The snapshot, re-sent because the turn record was written, fills both in.
  apply(state, SNAPSHOTS[1])
  assert.equal(turn.cost, 0.0144)
  assert.equal(turn.model, 'claude-opus-5')
})

test("a turn record's `model: null` is an answer, and is shown as nothing", () => {
  const state = replayed(withTurn((turn) => ({ ...turn, model: null })))
  assert.equal(state.timeline.find((n) => n.kind === 'result').model, null)
})

test("the turn line shows what the turn cost, not the process's running total", () => {
  const state = replayed()
  const turn = state.timeline.find((n) => n.kind === 'result')
  assert.equal(turn.cost, 0.0144, 'cost_delta_usd, which the host computed')
  assert.equal(turn.totalCost, 0.0413, 'the running total, kept only to be labelled as one')
  assert.equal(turn.durationMs, 9120)
  assert.equal(turn.numTurns, 3)
  assert.equal(turn.usage.input, 42)
  assert.equal(turn.usage.output, 68)
  assert.equal(turn.usage.cacheRead, 11000)
})

test('a turn cost the host could not compute is unknown, not zero', () => {
  assert.equal(
    replayed(withTurn((turn) => ({ ...turn, cost_delta_usd: null }))).timeline.find((n) => n.kind === 'result').cost,
    null,
  )

  // And a turn recorded before the host wrote the key at all: absent reads the
  // same way as null.
  assert.equal(
    replayed(withTurn(({ cost_delta_usd, ...turn }) => turn)).timeline.find((n) => n.kind === 'result').cost,
    null,
  )
})

test('a page that joined late prices the turns it watched, not the ones before them', () => {
  // A session five turns old, rejoined: the record holds all five and this page
  // has watched only the last of them. The two are lined up at the end they
  // share, which `record_seq` is what identifies.
  const older = [0.01, 0.02, 0.03, 0.04].map((cost, i) => ({
    model: 'claude-opus-5',
    total_cost_usd: cost,
    cost_delta_usd: cost,
    duration_ms: 100 + i,
    num_turns: 1,
  }))
  const state = replayed(
    RECORDED.map((e) => (e.type === 'broker.session' ? { ...e, turns: [...older, ...e.turns] } : e)),
  )
  const turns = state.timeline.filter((n) => n.kind === 'result')
  assert.equal(turns.length, 1)
  assert.equal(turns[0].cost, 0.0144, 'the turn this page watched, not the fourth one before it')
})

// Every snapshot in the recording that carries a turn record, with that record
// put through `change`.
function withTurn(change) {
  return RECORDED.map((e) =>
    e.type === 'broker.session' && e.turns.length > 0 ? { ...e, turns: e.turns.map(change) } : e,
  )
}

// ------------------------------------------------ retries and silence (defect 5)

test('a retry is the CLI’s api_retry and nothing else, in a sentence composed here', () => {
  const state = applyAll(createTranscript(), upTo(61))
  assert.equal(state.notices.length, 1)
  assert.equal(state.notices[0].text, 'Retrying in 8s after a rate limit (HTTP 429) — attempt 1 of 10.')
})

test('the banner goes when the turn it was waiting on finishes', () => {
  const state = replayed()
  assert.equal(state.notices.length, 0)
})

test('the sentence is written from the category, the status and the wait', () => {
  assert.equal(
    retrySentence({ error: 'overloaded', error_status: 529, retry_delay_ms: 2500, attempt: 3, max_retries: 10 }),
    'Retrying in 2.5s after the model is overloaded (HTTP 529) — attempt 3 of 10.',
  )
  assert.equal(
    retrySentence({ error: 'billing_error', error_status: null, attempt: 1 }),
    'Retrying after a billing problem — attempt 1.',
  )
  assert.equal(retrySentence({ error: 'something_the_cli_added_later' }), 'Retrying after an error.')
})

test('free text that reads like a retry is not one', () => {
  const state = createTranscript()
  apply(state, { type: 'system', subtype: 'api_error', seq: 1, message: 'Credit balance is too low.' })
  apply(state, { type: 'system', subtype: 'status', seq: 2, status: 'requesting' })
  assert.equal(state.notices.length, 0, 'only subtype api_retry raises the banner')
  assert.equal(state.timeline.length, 0, 'and neither of them is a line on the page')
})

test('an event this page does not recognise is ignored, not drawn', () => {
  const state = replayed()
  // `rate_limit_event`, `system`/`status` and `system`/`thinking_tokens` are all
  // in the recording. What the page draws is the conversation and nothing else.
  assert.deepEqual(
    state.timeline.map((n) => n.kind),
    // The answered approval leaves its line where it closed, and the line is a
    // step: it folds with the tool calls of the message before it rather than
    // standing between folds.
    ['user', 'assistant', 'permission', 'result', 'exit'],
  )
  assert.deepEqual(state.timeline.map(foldsAsStep), [false, false, true, false, false])

  const fresh = createTranscript()
  apply(fresh, { type: 'something_the_cli_grew', seq: 1, note: 'from a later CLI' })
  apply(fresh, { type: 'broker.something_new', seq: 2 })
  assert.equal(fresh.timeline.length, 0)
  assert.equal(fresh.lastSeq, 2, 'ignored is not unseen: the reconnect position still moves')
})

// ------------------------------------------------------------- the mechanics

test('the process ending is recorded, and the id still resumes', () => {
  const state = replayed()
  // The exit knows where in the log it was, because that is what lets anything
  // later than it take it back: an exit is the end of the log until something
  // comes after it.
  assert.deepEqual(state.exit, { code: 0, seq: 65 })
  assert.equal(state.session.state, 'exited')
})

test('seq is followed, never assumed to start at 1', () => {
  const state = replayed()
  assert.equal(state.lastSeq, 65, 'the position to reconnect from')

  const fresh = createTranscript()
  apply(fresh, { type: 'broker.user_message', seq: 9001, session_id: 's-resumed', text: 'again' })
  assert.equal(fresh.lastSeq, 9001, 'a resumed session continues its old numbering')
})

test('an event delivered twice changes nothing', () => {
  const once = JSON.stringify(strip(replayed()))
  const doubled = createTranscript()
  for (const event of RECORDED) {
    apply(doubled, event)
    apply(doubled, event) // the broker would rather repeat an event than drop one
  }
  assert.equal(JSON.stringify(strip(doubled)), once)
})

// --------------------------------- the snapshot is not an event (defects 6, 7)

test('the snapshot is no position: it is read for the record, not for a number', () => {
  const state = createTranscript()
  apply(state, SNAPSHOTS[0])
  assert.equal(state.lastSeq, null, 'nothing has been read yet, so there is nothing to reconnect after')
  assert.equal(state.session.record_seq, 57, 'where the session is, kept as what it is')
  assert.equal(state.session.seq, undefined, 'and never under the name an event wears')
  assert.equal(state.seen.size, 0, 'no number is spent by a snapshot')
})

test('the record says where the log ends, which is how the page knows it has caught up', () => {
  // The same number, read for the one thing it does say: not where to reconnect
  // — that is an event's `seq` and nothing else — but whether what is being
  // drawn is history or is happening now.
  const state = createTranscript()
  assert.equal(caughtUp(state), true, 'nothing has said otherwise yet')
  apply(state, SNAPSHOTS[0])
  assert.equal(caughtUp(state), false, 'the log reaches 57 and this page has read nothing of it')
  applyAll(state, EVENTS.filter((e) => e.seq <= 56))
  assert.equal(caughtUp(state), false, 'one event short is still catching up')
  applyAll(state, EVENTS.filter((e) => e.seq === 57))
  assert.equal(caughtUp(state), true)
})

test('an event wearing the number the snapshot carried is still drawn', () => {
  // `record_seq: 57` is the log's end at the moment this page connected, and
  // seq 57 is a real event in the replay that follows: the subagent's tool
  // result. A page that read the snapshot as an event would have discarded it.
  const state = replayed()
  const task = state.timeline.find((n) => n.kind === 'assistant').blocks[1]
  assert.equal(task.children[0].blocks[1].result, '# example\n', 'the event numbered 57 landed')
})

test('a replay from ?from=0 draws every event, the last one included', () => {
  // Reopening a session that has ended. The record the broker sends first is
  // the whole log's, so its `record_seq` is the number of the very last event
  // — which is the exit at 65, the event this page most needs to see.
  const reopened = [{ ...SNAPSHOTS[1], record_seq: 65, state: 'exited' }, ...EVENTS]
  const state = replayed(reopened)

  assert.deepEqual(state.exit, { code: 0, seq: 65 }, 'the last event of the log is drawn, not swallowed')
  assert.equal(state.lastSeq, 65)
  assert.deepEqual(
    state.timeline.map((n) => n.kind),
    // The answered approval leaves its line where it closed, and folds there.
    ['user', 'assistant', 'permission', 'result', 'exit'],
  )
  assert.deepEqual(state.timeline.map(foldsAsStep), [false, false, true, false, false])
  assert.equal(state.timeline.find((n) => n.kind === 'result').cost, 0.0144, 'and the turn is priced from that record')
})

test('a drop five events into a replay reconnects at the sixth, not at the end of the log', () => {
  const dropped = RECORDED.slice(0, 1 + 5) // the snapshot, then five events
  const state = applyAll(createTranscript(), dropped)

  assert.equal(state.lastSeq, EVENTS[4].seq, 'the fifth event read, and not a number from the snapshot')
  assert.equal(state.lastSeq, 46)
  assert.equal(state.session.record_seq, 57, 'the log runs on past this page, which is not where it asks from')
  assert.equal(EVENTS[5].seq, 47, 'so `?from=46` asks for the sixth event and nothing is left unreachable')

  // The reconnect: the broker sends the record first, then everything after 46.
  applyAll(state, [SNAPSHOTS[0], ...RECORDED.slice(1 + 5)])
  assert.equal(JSON.stringify(strip(state)), JSON.stringify(strip(replayed())), 'the same transcript as an unbroken one')
})

test('a stream replayed from a reconnect point picks up where it left off', () => {
  const cut = RECORDED.findIndex((e) => e.seq === 57)
  const first = applyAll(createTranscript(), RECORDED.slice(0, cut))
  const from = first.lastSeq
  assert.equal(from, 56)

  // What a reconnect gets: the record first, then everything after `from`, with
  // one repeat — the broker would rather repeat an event than drop one.
  applyAll(first, [SNAPSHOTS[0], ...RECORDED.slice(cut - 1)])

  assert.equal(JSON.stringify(strip(first)), JSON.stringify(strip(replayed())))
})

// The bookkeeping maps are the reducer's own; compare what the page draws.
function strip(state) {
  return Object.fromEntries(Object.entries(state).filter(([key]) => key !== 'seen' && !key.startsWith('_')))
}

// ------------------------------------------ what a tool call is, on the page

// The design draws a tool call three ways and the page must not be the one
// deciding which: every rule below is a fact about one agent's dialect — that
// Claude Code keeps a running plan in `TodoWrite`, proposes one with
// `ExitPlanMode`, and names the file it rewrites in `file_path`. A second agent
// names all three something else and changes the seam, not the screen.

function tool(over = {}) {
  return { type: 'tool', id: 't1', name: 'Bash', input: {}, partialInput: '', result: null, isError: false, children: [], ...over }
}

test('a running plan is the plan card, and says which step is in flight', () => {
  const view = blockView(
    tool({
      name: 'TodoWrite',
      result: 'ok',
      input: {
        todos: [
          { content: 'Add the column', status: 'completed', activeForm: 'Adding the column' },
          { content: 'Run the tests', status: 'in_progress', activeForm: 'Running the tests' },
          { content: 'Open the PR', status: 'pending', activeForm: 'Opening the PR' },
        ],
      },
    }),
  )
  assert.equal(view.view, 'plan')
  assert.equal(view.title, 'Running the tests')
  assert.equal(view.sub, '1 of 3 done')
  assert.equal(view.running, true)
  assert.deepEqual(view.steps.map((s) => s.state), ['done', 'doing', 'todo'])
})

test('a finished plan is the same card, without the spinner', () => {
  const view = blockView(
    tool({
      name: 'TodoWrite',
      result: 'ok',
      input: { todos: [{ content: 'Ship it', status: 'completed', activeForm: 'Shipping it' }] },
    }),
  )
  assert.equal(view.title, 'Planned 1 step')
  assert.equal(view.running, false)
})

test('a proposed plan is its markdown list, and waits on the person', () => {
  const view = blockView(tool({ name: 'ExitPlanMode', input: { plan: 'Here is the plan:\n\n- Migrate\n- Deploy\n2. Tell you' } }))
  assert.equal(view.view, 'plan')
  assert.equal(view.title, 'Plan')
  assert.deepEqual(view.steps.map((s) => s.text), ['Migrate', 'Deploy', 'Tell you'])
  assert.equal(view.running, true, 'the turn is paused until it is answered')
})

test('an edit that happened is a diff row, counted from what it wrote', () => {
  const view = blockView(
    tool({
      name: 'Edit',
      result: 'The file has been updated.',
      input: { file_path: 'src/app.js', old_string: 'one\ntwo', new_string: 'one\ntwo\nthree\nfour' },
    }),
  )
  assert.deepEqual(view, { view: 'diff', file: 'src/app.js', added: 4, removed: 2 })
})

test('a new file is all additions, and several edits at once are the sum', () => {
  assert.deepEqual(blockView(tool({ name: 'Write', result: 'ok', input: { file_path: 'a.md', content: 'x\ny\nz' } })), {
    view: 'diff',
    file: 'a.md',
    added: 3,
    removed: 0,
  })
  const many = blockView(
    tool({
      name: 'MultiEdit',
      result: 'ok',
      input: { file_path: 'b.js', edits: [{ old_string: 'a', new_string: 'a\nb' }, { old_string: 'c\nd', new_string: 'e' }] },
    }),
  )
  assert.deepEqual(many, { view: 'diff', file: 'b.js', added: 3, removed: 3 })
})

test('an edit that has not happened yet is not a diff, because a diff would be a lie', () => {
  // Waiting on a permission: the file does not read differently yet.
  const pending = blockView(tool({ name: 'Edit', input: { file_path: 'src/app.js', old_string: 'a', new_string: 'b' } }))
  assert.equal(pending.view, 'tool')
  assert.equal(pending.sub, 'running…')

  // And one that came back an error never will.
  const failed = blockView(tool({ name: 'Edit', result: 'String not found', isError: true, input: { file_path: 'src/app.js', old_string: 'a', new_string: 'b' } }))
  assert.equal(failed.view, 'tool')
  assert.equal(failed.sub, 'failed')
})

test('everything else is the monospace command row', () => {
  const ran = blockView(tool({ name: 'Bash', result: 'one\ntwo', input: { command: 'npm test' } }))
  assert.deepEqual(ran, { view: 'tool', command: 'npm test', sub: '2 lines', running: false, isError: false })

  // A tool that is not a command line still reads as its own name and the one
  // argument worth seeing, rather than a JSON blob.
  const read = blockView(tool({ name: 'Read', result: '', input: { file_path: 'README.md' } }))
  assert.equal(read.command, 'Read README.md')
  assert.equal(read.sub, 'no output')
})

// ------------------------- a message the CLI states in pieces, and a run of tools

// A second recording, and this one is a real session's stream, recorded from a
// coding CLI and sanitised: the ids are fixture ids, the working directory is
// not a real one, and the broker's own record and the person's own message are
// written around it. Everything between them is what the CLI actually sent,
// event for event, in the order it sent it.
//
// It is here because the shape the *other* fixture was written in is not the
// shape a real session has, and one line of the difference cost the page every
// sentence an agent said before a tool call:
//
//   an `assistant` event carries **some** of a message's content blocks, not
//   all of them. A reply that says a sentence and then calls a tool is two
//   `assistant` events under one message id — `[text]`, then `[tool_use]` —
//   and the first of them arrives before the tool block has begun streaming.
//
// The turn in it is: a sentence, a call, its result, a sentence, a call, its
// result, a sentence, then five calls in a row and a closing sentence.
const RUN = fixture('turn-with-a-run-of-tools.jsonl')

const SAID = [
  'Starting with the first command.',
  'That worked, moving to the second.',
  'Now the five in a row.',
  'All seven commands ran in order.',
]

// Every text block in the transcript, in order, however many messages it took.
function textsOf(state) {
  return state.timeline
    .filter((node) => node.kind === 'assistant')
    .flatMap((node) => node.blocks.filter((b) => b && b.type === 'text' && b.text.trim() !== '').map((b) => b.text))
}

test('a message stated in two events keeps both: the sentence and the call it introduced', () => {
  const state = applyAll(createTranscript(), RUN)
  const first = state.timeline.find((n) => n.kind === 'assistant')
  assert.equal(first.blocks[0].type, 'text')
  assert.equal(first.blocks[0].text, 'Starting with the first command.')
  assert.equal(first.blocks[1].type, 'tool', 'the tool call replaced the sentence rather than following it')
  assert.equal(first.blocks[1].input.command, 'echo one')
  assert.equal(first.streaming, false)
})

test('every sentence of a turn is on the page, in order, and none of them ever leaves', () => {
  const state = createTranscript()
  const seen = []
  for (const event of RUN) {
    apply(state, event)
    // What the CLI has stated outright by now — a streamed partial is not yet
    // a sentence, but a stated one never stops being one.
    if (event.type === 'assistant') {
      for (const item of event.message?.content ?? []) if (item.type === 'text') seen.push(item.text)
    }
    const shown = textsOf(state)
    for (const text of seen) {
      assert.equal(shown.includes(text), true, `“${text}” left the transcript at seq ${event.seq}`)
    }
  }
  assert.deepEqual(textsOf(state), SAID)
})

test('a tool call is drawn once, wherever its message said it', () => {
  const state = applyAll(createTranscript(), RUN)
  const tools = state.timeline
    .filter((n) => n.kind === 'assistant')
    .flatMap((n) => n.blocks.filter((b) => b && b.type === 'tool'))
  assert.deepEqual(tools.map((t) => t.input.command), ['echo one', 'echo two', 'echo a', 'echo b', 'echo c', 'echo d', 'echo e'])
  assert.deepEqual(tools.map((t) => t.result), ['one', 'two', 'a', 'b', 'c', 'd', 'e'])
  // Seven calls, seven blocks: none of them was left behind at an index the
  // authoritative message never reached.
  assert.equal(new Set(tools.map((t) => t.id)).size, 7)
})

test('the turn’s cost and model arrive on the snapshot that records it', () => {
  const state = applyAll(createTranscript(), RUN)
  const turn = state.timeline.find((n) => n.kind === 'result')
  assert.equal(turn.cost, 0.0413)
  assert.equal(turn.model, 'claude-opus-5')
})

// The fold: one line over a run of steps, and what that line has to say without
// being opened. The rule is the seam's because it is one rule, and it is not the
// page's because it is not a picture.
//
// **Every step is inside it**, which is the owner, 2026-09-25: "still some of the
// tool calls are showing outside of the steps drop down and in the chat." The
// call running now and the call that failed used to be rows in the thread; both
// are steps like the rest, and what says so is the header.

test('a run of calls is one fold, whatever any of them is doing', () => {
  const done = (i) => tool({ id: `t${i}`, result: 'ok' })
  const facts = foldFacts([done(1), done(2), done(3), tool({ id: 't4' })])
  assert.equal(facts.count, 4, 'a call was left out of the fold')
  assert.equal(facts.running, true, 'the fold does not know one of its steps is still going')
  assert.equal(facts.failed, 0)
})

test('a run that has ended is a count and a tick', () => {
  const facts = foldFacts([tool({ id: 'a', result: 'ok' }), tool({ id: 'b', result: 'ok' })])
  assert.deepEqual(facts, { count: 2, failed: 0, running: false, thinking: null })
})

// The owner, 2026-09-22: "Even when there is only one step, the action should be
// in a collapsable step design." One rule for one call and for five, and one
// rule whatever the call did.
test('a lone call is a fold of one, finished, running or failed', () => {
  assert.equal(foldFacts([tool({ id: 'only', result: 'ok' })]).count, 1)
  assert.equal(foldFacts([tool({ id: 'only', result: null })]).running, true)
  const broke = foldFacts([tool({ id: 'only', result: 'No such file', isError: true })])
  assert.equal(broke.count, 1)
  assert.equal(broke.failed, 1)
})

// The owner, 2026-09-25: "when an action by an agent fails, please still keep it
// in the steps drop down. Maybe just have a small and discrete indicator that
// the dropdown includes a failure." The count of them is the indicator, and
// there is nothing else in the answer for a page to make a noise out of.
test('a failure is kept, counted, and says nothing more than its number', () => {
  const facts = foldFacts([
    tool({ id: 'a', result: 'ok' }),
    tool({ id: 'b', result: 'No such file', isError: true }),
    tool({ id: 'c', result: 'ok' }),
    tool({ id: 'd', result: 'Permission denied', isError: true }),
  ])
  assert.equal(facts.count, 4, 'a failure was taken out of the fold')
  assert.equal(facts.failed, 2)
  assert.equal(facts.running, false, 'a call that failed was read as one still going')
})

// The agent's own reasoning is machinery and not what it said to the person, so
// it is a step — and while the work is going on it is what the fold's one line
// reads, because a count says nothing about progress.
test('thinking is a step, and the latest line of it is what a running fold says', () => {
  const thinking = (text) => ({ type: 'thinking', text })
  const facts = foldFacts([
    thinking('First I should read the seam.\nThen the fold code.'),
    tool({ id: 'a', result: 'ok' }),
    thinking('The window is the part to change.'),
    tool({ id: 'b', result: null }),
  ])
  assert.equal(facts.count, 4, 'thinking is not a step the fold holds')
  assert.equal(facts.thinking, 'The window is the part to change.', 'the header reads something other than the latest line')
  assert.equal(facts.running, true)

  // And once the run is over the line is still there for the page to stop using:
  // the header goes back to the count when nothing is running.
  const ended = foldFacts([thinking('Only this.'), tool({ id: 'a', result: 'ok' })])
  assert.equal(ended.running, false)
  assert.equal(ended.thinking, 'Only this.')
})

// The commonest way a turn is working: thinking, before there is any call to be
// waiting on. The block itself says it is still being written — the stream is
// where that is known — so a fold reads its live line without the page having to
// ask the record what the session's status is.
test('a block of thinking still being written is a step that is running', () => {
  const state = createTranscript()
  const stream = (event) => apply(state, { type: 'stream_event', seq: (state.lastSeq ?? 0) + 1, session_id: 's', event })
  stream({ type: 'message_start', message: { id: 'm-live', model: 'claude-opus-5' } })
  stream({ type: 'content_block_start', index: 0, content_block: { type: 'thinking', thinking: '' } })
  stream({ type: 'content_block_delta', index: 0, delta: { type: 'thinking_delta', thinking: 'Two ways to do this.\nThe seam first.' } })

  const blocks = state.timeline.find((n) => n.kind === 'assistant').blocks
  let facts = foldFacts(blocks)
  assert.equal(facts.running, true, 'a fold whose thinking is still arriving is drawn as finished')
  assert.equal(facts.thinking, 'The seam first.')

  // And when the block ends it is a step like any other: the header goes back to
  // the count, with nothing left running.
  stream({ type: 'content_block_stop', index: 0 })
  facts = foldFacts(blocks)
  assert.equal(facts.running, false, 'the thinking finished and the fold is still breathing')
  assert.equal(facts.count, 1)

  // The authoritative copy of the same block, which arrives as its own
  // `assistant` event: it is **written into** rather than replaced, because the
  // page keys the fold off the identity of its first step and a new object there
  // would shut a card the reader had opened.
  const was = blocks[0]
  apply(state, {
    type: 'assistant',
    seq: 20,
    session_id: 's',
    message: { id: 'm-live', role: 'assistant', model: 'claude-opus-5', content: [{ type: 'thinking', thinking: 'Two ways to do this.\nThe seam first.' }] },
  })
  assert.equal(blocks[0], was, 'the finished thinking replaced the block the fold was keyed off')
  assert.equal(foldFacts(blocks).running, false)
})

test('the recorded run of five is one fold of five', () => {
  const state = applyAll(createTranscript(), RUN)
  const tools = state.timeline
    .filter((n) => n.kind === 'assistant')
    .flatMap((n) => n.blocks.filter((b) => b && b.type === 'tool'))
  const facts = foldFacts(tools.slice(2))
  assert.equal(facts.count, 5)
  assert.equal(facts.running, false)
  assert.equal(facts.failed, 0)
})

// A result whose call is above the window a chat opened at. It was dropped
// until 2026-09-25 — the one thing that happened and was on no line at all —
// and it is now a step of its own, in the timeline, at the point it arrived.
test('a result for a call this page never saw is a step of its own', () => {
  const state = createTranscript()
  apply(state, { type: 'broker.user_message', seq: 40, session_id: 's', text: 'carry on' })
  apply(state, {
    type: 'user',
    seq: 41,
    session_id: 's',
    message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'above-the-window', content: 'two lines\nof output' }] },
  })
  assert.deepEqual(state.timeline.map((n) => n.kind), ['user', 'tool'])
  const step = state.timeline[1].block
  assert.equal(step.result, 'two lines\nof output')
  assert.equal(step.earlier, true)
  // It reads as what it is: a step from further up, and its output.
  const view = blockView(step)
  assert.equal(view.view, 'tool')
  assert.equal(view.command, 'earlier step')
  assert.equal(view.sub, '2 lines')

  // And the call it belongs to arriving later — a page of history fetched above
  // the window — finds the same block rather than making a second one.
  apply(state, {
    type: 'user',
    seq: 42,
    session_id: 's',
    message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'above-the-window', content: 'again', is_error: true }] },
  })
  assert.deepEqual(state.timeline.map((n) => n.kind), ['user', 'tool'], 'the same result twice drew two steps')
  assert.equal(state.timeline[1].block.isError, true)
})

// -------------------------------- what is running, and what is in flight
//
// The two facts the "this chat is not running" notice is made of. Both were
// one boolean read off the record at open, and neither was ever re-read: an
// exit event replayed at the end of an ended chat's log stayed set for as long
// as the screen was open, so a chat resumed by typing into it went on saying it
// was not running through the whole of the answer it was streaming.

test('an exit is the end of the log until something comes after it', () => {
  const state = createTranscript()
  apply(state, { type: 'broker.session', session_id: 's', state: 'running', status: 'done', record_seq: 0 })
  apply(state, { type: 'broker.exit', seq: 10, session_id: 's', exit_code: 0 })
  assert.deepEqual(state.exit, { code: 0, seq: 10 }, 'the process ended and the page knows where')
  assert.equal(alive(state, 'exited'), false)

  // The session is resumed under its own id, which continues the old
  // numbering: its first event is seq 11.
  apply(state, { type: 'broker.user_message', seq: 11, session_id: 's', text: 'again' })
  assert.equal(state.exit, null, 'the exit outlived the process it was about')
  assert.equal(alive(state, 'running'), true)
  // And the line stays in the transcript, because it is history and not a state.
  assert.deepEqual(state.timeline.map((n) => n.kind), ['exit', 'user'])
})

test('an exit replayed twice is still one exit, and an earlier event does not undo it', () => {
  const state = createTranscript()
  apply(state, { type: 'broker.user_message', seq: 3, session_id: 's', text: 'hello' })
  apply(state, { type: 'broker.exit', seq: 4, session_id: 's', exit_code: 0 })
  apply(state, { type: 'broker.exit', seq: 4, session_id: 's', exit_code: 0 }) // the broker repeats rather than drops
  assert.deepEqual(state.exit, { code: 0, seq: 4 })
  assert.equal(state.timeline.filter((n) => n.kind === 'exit').length, 1)
})

test('in flight is the broker status; a local send cannot start work and exit clears it', () => {
  const state = createTranscript()
  assert.equal(inFlight(state, 'done'), false)
  assert.equal(inFlight(state, 'idle'), false)
  assert.equal(inFlight(state, 'failed'), false)
  assert.equal(inFlight(state, 'working'), true)
  // A permission outranks a turn in flight, and both are in flight.
  assert.equal(inFlight(state, 'needs_input'), true)
  // The settled four are the ones named, so the word the broker is adding
  // between working and done — and any it adds after that — is a turn in flight
  // rather than a chat that says it is not running while it is answering.
  assert.equal(inFlight(state, 'waiting'), true)
  assert.equal(inFlight(state, 'brewing'), true)
  assert.equal(inFlight(state, ''), false)
  assert.equal(inFlight(state, undefined), false)

  // Drawing a local message does not mean the host accepted a turn.
  const line = noteUserMessage(state, 'are you there?')
  assert.equal(inFlight(state, 'done'), false)
  labelUserMessage(state, line, 12)
  assert.equal(inFlight(state, 'working'), true, 'and by then the host says so')
  apply(state, { type: 'broker.exit', seq: 13, exit_code: 143 })
  assert.equal(inFlight(state, 'working'), false, 'a stale snapshot kept an ended process working')
  apply(state, { type: 'broker.user_message', seq: 14, text: 'a later accepted turn' })
  assert.equal(inFlight(state, 'working'), true)
})

test('alive is the record and the exit together, and nothing else', () => {
  const state = createTranscript()
  assert.equal(alive(state, 'running'), true)
  assert.equal(alive(state, 'exited'), false)
  apply(state, { type: 'broker.exit', seq: 1, session_id: 's', exit_code: 1 })
  assert.equal(alive(state, 'running'), false, 'the record had not caught up with the exit yet')
})

// ------------------------------------------------------- background tasks

test('the background tasks are the host’s, by name and by number', () => {
  const state = createTranscript()
  apply(state, {
    type: 'broker.background_tasks',
    seq: 8689,
    session_id: 's',
    count: 2,
    tasks: [
      { task_id: 't1', task_type: 'local_agent', description: 'Four briefs' },
      { task_id: 't2', task_type: 'local_agent', description: 'The deploy' },
    ],
  })
  assert.deepEqual(state.tasks.map((t) => t.description), ['Four briefs', 'The deploy'])
  assert.deepEqual(state.tasks.map((t) => t.id), ['t1', 't2'])
  // It is not a line on the page: a background task is happening, not said.
  assert.deepEqual(state.timeline, [])

  apply(state, { type: 'broker.background_tasks', seq: 8700, session_id: 's', count: 0, tasks: [] })
  assert.deepEqual(state.tasks, [], 'the count going to zero is an event like any other')
})

// ------------------------------------------- the context a turn was holding

test('a turn carries its context from the result event and from the record', () => {
  const state = createTranscript()
  apply(state, { type: 'result', seq: 5, session_id: 's', subtype: 'success', usage: {}, context: { used: 35271, window: 1000000 } })
  const turn = state.timeline.find((n) => n.kind === 'result')
  assert.deepEqual(turn.context, { used: 35271, window: 1000000 }, 'the turn that just ended can be priced without waiting')

  // And the record says the same thing when it arrives.
  apply(state, {
    type: 'broker.session',
    session_id: 's',
    record_seq: 5,
    turns: [{ model: 'claude-opus-5', cost_delta_usd: 0.0144, context: { used: 35271, window: 200000 }, effort: 'high' }],
  })
  assert.deepEqual(turn.context, { used: 35271, window: 200000 })
  assert.equal(turn.effort, 'high')
  assert.equal(turn.model, 'claude-opus-5')
})

test('either half of a context reading may be unknown, and unknown is nothing', () => {
  assert.equal(contextWords(null), null)
  assert.equal(contextWords({ used: null, window: 1000000 }), null, 'a window with no reading in it says nothing')
  assert.equal(contextWords({ used: 35271, window: null }), '35.3k', 'a reading with no window is still a reading')
  assert.equal(contextWords({ used: 35271, window: 1000000 }), '35.3k of 1M (3.5%)')
  assert.equal(contextWords({ used: 412, window: 200000 }), '412 of 200k (0.2%)')
  assert.equal(contextWords({ used: 12, window: 1000000 }), '12 of 1M (<0.1%)', 'a share too small to round is not 0.0%')
})

// The composer's top edge. The owner, 2026-10-07: the bar's length is the
// model's window, and the warning point — a number of tokens, or the model's
// window — only colours it: calm below 75% of it, amber from 75%, red from the
// point itself. A chat with no reading draws nothing rather than an empty bar.
test('the context fill is the latest reading over the model’s window, coloured by the warning point', () => {
  const turn = (used, window = 1000000) => ({ model: 'claude-opus-5', context: { used, window } })
  const fill = (used, at, window = 1000000, fallback = null) => contextFill({ turns: [turn(used, window)] }, null, at, fallback)
  assert.equal(CONTEXT_WARN, 0.75)
  assert.equal(CONTEXT_WARN_AT, 400000, 'the default warning point')
  // 312k of a 1M window is 31.2% wide; against 400k it is 78%, amber.
  assert.deepEqual(fill(312000, 400000), {
    used: 312000,
    at: 400000,
    window: 1000000,
    share: 0.312,
    width: '31.2%',
    level: 'warn',
    usedWords: '312k',
    words: '312k of 1M · warning at 400k',
  })
  // The default, with no warning point handed, is the same 400k.
  assert.equal(contextFill({ turns: [turn(312000)] }).words, '312k of 1M · warning at 400k')
  // The three bands, and their edges.
  assert.equal(fill(299999, 400000).level, 'calm')
  assert.equal(fill(300000, 400000).level, 'warn', '75% of the point is amber')
  assert.equal(fill(399999, 400000).level, 'warn')
  assert.equal(fill(400000, 400000).level, 'high', 'the point itself is red')
  // Past the point it is red and still growing, to the window and no further.
  const past = fill(700000, 400000)
  assert.equal(past.level, 'high')
  assert.equal(past.width, '70%')
  assert.equal(fill(1200000, 400000).width, '100%')
  // Another number moves the colour and not the width.
  const at600 = fill(312000, 600000)
  assert.equal(at600.width, '31.2%')
  assert.equal(at600.level, 'calm')
  assert.equal(at600.words, '312k of 1M · warning at 600k')
  // The model's context length: the point is the window.
  const whole = fill(312000, 'window')
  assert.equal(whole.width, '31.2%')
  assert.equal(whole.level, 'calm')
  assert.equal(whole.words, '312k of 1M · warning at 1M')
  assert.equal(fill(749999, 'window').level, 'calm')
  assert.equal(fill(750000, 'window').level, 'warn')
  assert.equal(fill(1000000, 'window').level, 'high')
  // A point above the window is clamped to it: 400k on a 200k model warns at
  // 200k, amber from 150k.
  const clamped = fill(150000, 400000, 200000)
  assert.equal(clamped.at, 200000)
  assert.equal(clamped.level, 'warn')
  assert.equal(clamped.width, '75%')
  assert.equal(clamped.words, '150k of 200k · warning at 200k')
  // A model change mid-chat: the latest reading's window is the scale, so the
  // bar rescales with it.
  const changed = contextFill({ turns: [turn(150000, 1000000), turn(160000, 200000)] }, null, 400000)
  assert.equal(changed.window, 200000)
  assert.equal(changed.width, '80%')
  assert.equal(changed.level, 'warn')
  // The latest turn with a reading, not the latest turn.
  assert.equal(contextFill({ turns: [turn(200000), { model: null, context: null }] }).used, 200000)
  // A reading with no window stated: the provider row's figure is the scale
  // when it has one; with neither, the bar measures the point and the words
  // claim no window; `'window'` has nothing to measure.
  const noWindow = { turns: [{ context: { used: 100000, window: null } }] }
  const listed = contextFill(noWindow, null, 400000, 200000)
  assert.equal(listed.width, '50%')
  assert.equal(listed.words, '100k of 200k · warning at 200k')
  assert.equal(contextFill(noWindow, null, 'window', 200000).at, 200000)
  assert.equal(fill(100000, 400000, 1000000, 200000).window, 1000000, 'the turn’s own window outranks the row’s')
  const bare = contextFill(noWindow, null, 400000)
  assert.equal(bare.width, '25%')
  assert.equal(bare.words, '100k · warning at 400k')
  assert.equal(contextFill(noWindow, null, 'window'), null)
  // Nothing: no turn, no reading.
  assert.equal(contextFill({ turns: [] }), null)
  assert.equal(contextFill({}), null)
  assert.equal(contextFill({ turns: [{ context: { used: null, window: 1000000 } }] }), null)
})

// The host stops compacting at 400k and stops sending `compact_window`; an
// older one still sends it. Neither moves the fill.
test('compact_window, present or absent, plays no part in the fill', () => {
  const turn = (used) => ({ context: { used, window: 1000000 } })
  for (const warnAt of [400000, 250000, 'window']) {
    const without = contextFill({ turns: [turn(312000)] }, null, warnAt)
    for (const compact_window of [400000, 967000, 100000, 0, null]) {
      assert.deepEqual(contextFill({ turns: [turn(312000)], compact_window }, null, warnAt), without, `compact_window ${compact_window} at ${warnAt}`)
    }
  }
  assert.doesNotMatch(readFileSync(path.join(HERE, '..', 'public', 'transcript.js'), 'utf8').replace(/\/\/.*$/gm, ''), /compact_window/)
})

test('a model id is shown as a name, and one this rule does not fit is shown as it is', () => {
  assert.equal(modelShortName('claude-opus-5'), 'Opus 5')
  assert.equal(modelShortName('claude-fable-5-1'), 'Fable 5.1')
  assert.equal(modelShortName('claude-haiku-4-5-20251001'), 'Haiku 4.5', 'eight digits are a release, not a version')
  assert.equal(modelShortName('gpt'), 'Gpt')
  assert.equal(modelShortName(null), null)
  assert.equal(modelShortName(''), null)
})

// ------------------------------------------------------------- compaction
//
// The runtime's contract, 2026-09-30: `broker.compaction` in the log with
// `trigger`, `tokens_before`, `tokens_after` (either may be null), and a turn
// record left behind whose context is two nulls. The CLI's own
// `system`/`compact_boundary` stays in the log and is not drawn.

const COMPACTION = {
  type: 'broker.compaction',
  session_id: 's',
  at: '2026-09-30T18:00:00Z',
  trigger: 'manual',
  tokens_before: 312000,
  tokens_after: 2000,
  duration_ms: 41000,
  seq: 21,
}

test('a compaction is a line in the timeline at its seq, and the boundary beside it is not', () => {
  const t = createTranscript()
  applyAll(t, [
    { type: 'broker.user_message', session_id: 's', text: 'hi', seq: 19 },
    { type: 'system', subtype: 'compact_boundary', session_id: 's', compact_metadata: { trigger: 'manual', pre_tokens: 312000 }, seq: 20 },
    COMPACTION,
    { type: 'broker.user_message', session_id: 's', text: 'and now?', seq: 22 },
  ])
  assert.deepEqual(
    t.timeline.map((n) => n.kind),
    ['user', 'compaction', 'user'],
    'the boundary was drawn beside the compaction, or the compaction not at all',
  )
  const node = t.timeline[1]
  assert.deepEqual(node, { kind: 'compaction', seq: 21, trigger: 'manual', before: 312000, after: 2000, at: '2026-09-30T18:00:00Z' })
  assert.equal(t.compaction, node)
  // Said twice, drawn once.
  apply(t, COMPACTION)
  assert.equal(t.timeline.filter((n) => n.kind === 'compaction').length, 1)
})

test('a compaction says its numbers, and says only "Compacted" without them', () => {
  assert.equal(compactionWords({ before: 312000, after: 2000 }), 'Compacted · 312k → 2k')
  assert.equal(compactionWords({ before: null, after: 2000 }), 'Compacted')
  assert.equal(compactionWords({ before: 312000, after: null }), 'Compacted')
  assert.equal(compactionWords(null), 'Compacted')
  const t = applyAll(createTranscript(), [{ ...COMPACTION, tokens_before: null, tokens_after: null, trigger: 'auto' }])
  assert.equal(compactionWords(t.timeline[0]), 'Compacted')
  assert.equal(t.timeline[0].trigger, 'auto')
})

test('the turn a compaction leaves is priced and names no model', () => {
  const t = createTranscript()
  applyAll(t, [COMPACTION, { type: 'result', subtype: 'success', session_id: 's', duration_ms: 41000, num_turns: 1, usage: {}, seq: 22 }])
  apply(t, {
    type: 'broker.session',
    session_id: 's',
    record_seq: 22,
    turns: [{ model: 'claude-opus-5', context: { used: null, window: null }, cost_delta_usd: 0.31 }],
  })
  const line = t.timeline.find((n) => n.kind === 'result')
  assert.equal(line.cost, 0.31, 'the summary’s cost is the turn’s cost')
  assert.equal(line.compaction, true)
  assert.equal(line.model, null, 'a compaction turn drew a model name under it')
  assert.equal(compactionTurn({ context: { used: null, window: null } }), true)
  assert.equal(compactionTurn({ context: null }), false, 'a turn from before contexts were recorded is not a compaction')
  assert.equal(compactionTurn({ context: { used: 5, window: null } }), false)
})

test('after a compaction the context is what it left, until the next real turn', () => {
  const real = (used) => ({ context: { used, window: 1000000 } })
  const compacted = { context: { used: null, window: null } }
  const record = { turns: [real(312000), compacted] }
  // The page has read the compaction: its `tokens_after`, over the window the
  // turn before it stated.
  assert.deepEqual(contextNow(record, { after: 2000 }), { used: 2000, window: 1000000 })
  assert.equal(contextFill(record, { after: 2000 }).words, '2k of 1M · warning at 400k')
  assert.equal(contextFill(record, { after: 2000 }).level, 'calm')
  // It has not: unknown, not the reading the summary replaced.
  assert.equal(contextFill(record, null), null)
  // The next real turn's reading is the context again.
  assert.equal(contextFill({ ...record, turns: [...record.turns, real(9000)] }, { after: 2000 }).used, 9000)
})

test('a page of history above keeps the newer compaction as the latest', () => {
  const t = applyAll(createTranscript(), [{ ...COMPACTION, seq: 40, tokens_after: 3000 }])
  prependEvents(t, [COMPACTION])
  assert.equal(t.compaction.seq, 40)
  assert.deepEqual(t.timeline.map((n) => n.seq), [21, 40])
  const fresh = createTranscript()
  prependEvents(fresh, [COMPACTION])
  assert.equal(fresh.compaction.seq, 21, 'the only compaction read is the latest one')
})

// ------------------------------------------- a step dies with its process
//
// Seen 2026-10-05: a chat whose process ended (exit 143) under calls still
// waiting on their results kept their folds breathing for as long as the chat
// existed. A call with no result when its process ends is never getting one from
// that process: it is `ended`, neither running nor failed.

const call = (seq, ids, message = `m${seq}`) => ({
  type: 'assistant',
  seq,
  session_id: 's',
  message: { id: message, role: 'assistant', model: 'claude-opus-5', content: ids.map((id) => ({ type: 'tool_use', id, name: 'Bash', input: { command: `run ${id}` } })) },
})
const back = (seq, id, content = 'ok', extra = {}) => ({
  type: 'user',
  seq,
  session_id: 's',
  message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: id, content, ...extra }] },
})
const exitAt = (seq) => ({ type: 'broker.exit', session_id: 's', exit_code: 143, seq })
const stepsOf = (state) => state.timeline.filter((n) => n.kind === 'assistant').flatMap((n) => n.blocks.filter((b) => b?.type === 'tool'))

test('tool image stubs read as images beside text, without transport JSON', () => {
  const state = applyAll(createTranscript(), [call(1, ['images']), back(2, 'images', [
    { type: 'text', text: 'Two pages:' },
    { type: 'image', source: { type: 'base64', media_type: 'image/png', omitted_chars: 8000000 } },
    { type: 'image', omitted_chars: 9000000 },
  ])])
  assert.equal(stepsOf(state)[0].result, 'Two pages:\n[image]\n[image]')
})
const DIES = [
  { type: 'broker.user_message', session_id: 's', text: 'read both', seq: 1 },
  call(2, ['a', 'b']),
  back(3, 'a'),
  exitAt(4),
]

test('a fold of a finished call and an unfinished one reads done with its count once the process exits', () => {
  const state = applyAll(createTranscript(), DIES.slice(0, 3))
  assert.equal(foldFacts(stepsOf(state)).running, true, 'before the exit the second call is still going')
  apply(state, DIES[3])
  assert.deepEqual(foldFacts(stepsOf(state)), { count: 2, failed: 0, running: false, thinking: null })
  const [, cut] = stepsOf(state)
  assert.equal(cut.ended, true)
  const view = blockView(cut)
  assert.equal(view.running, false)
  assert.equal(view.isError, false, 'a call cut off by its process reads as one that failed')
  assert.equal(view.sub, 'ended with the chat')
})

test('the same log replayed from a snapshot never draws the call running', () => {
  const state = createTranscript()
  apply(state, { type: 'broker.session', session_id: 's', state: 'exited', status: 'failed', record_seq: 4, turns: [] })
  applyAll(state, DIES)
  assert.equal(foldFacts(stepsOf(state)).running, false)
  assert.equal(foldFacts(stepsOf(state)).count, 2)
  // And the record's word is what says whether the chat is working, not a step.
  assert.equal(inFlight(state), false, 'an unfinished step made the chat read as working')
  assert.equal(alive(state), false)
})

test('a result that arrives for the call after its process ended is attached, and is what the row says', () => {
  const state = applyAll(createTranscript(), DIES)
  apply(state, back(5, 'b', '[Tool call interrupted: the session ended before this call’s result was recorded]', { is_error: true }))
  const [, cut] = stepsOf(state)
  assert.equal(cut.ended, false)
  assert.match(cut.result, /Tool call interrupted/)
  assert.equal(stepsOf(state).length, 2, 'the late result became a step of its own')
})

test('thinking being written when the process went is finished too', () => {
  const state = createTranscript()
  const stream = (seq, event) => apply(state, { type: 'stream_event', seq, session_id: 's', event })
  stream(1, { type: 'message_start', message: { id: 'm-cut', model: 'claude-opus-5' } })
  stream(2, { type: 'content_block_start', index: 0, content_block: { type: 'thinking', thinking: '' } })
  stream(3, { type: 'content_block_delta', index: 0, delta: { type: 'thinking_delta', thinking: 'Half a thought' } })
  apply(state, exitAt(4))
  const blocks = state.timeline.find((n) => n.kind === 'assistant').blocks
  assert.equal(foldFacts(blocks).running, false)
})

test('a page of history above an exit: its unanswered call ended, and a result read below lands on its call', () => {
  // The window holds the result for `a` (its call is above), and the exit.
  const state = createTranscript()
  apply(state, { type: 'broker.session', session_id: 's', state: 'exited', status: 'done', record_seq: 4, turns: [], window_first_seq: 3, turns_before: 1 })
  applyAll(state, [back(3, 'a'), exitAt(4)])
  assert.equal(state.timeline.filter((n) => n.kind === 'tool').length, 1, 'the stand-in for a result whose call is above')
  // The page above holds both calls.
  prependEvents(state, [{ type: 'broker.session', session_id: 's', window_first_seq: 1, turns_before: 0 }, DIES[0], DIES[1]])
  assert.equal(state.timeline.filter((n) => n.kind === 'tool').length, 0, 'one result drawn twice: on its call and as a stand-in')
  const [a, b] = stepsOf(state)
  assert.equal(a.result, 'ok')
  assert.equal(b.ended, true)
  assert.deepEqual(foldFacts(stepsOf(state)), { count: 2, failed: 0, running: false, thinking: null })
})

// Seen 2026-10-05: a `TodoWrite` card whose last update left an item
// `in_progress` kept spinning after `broker.exit`. Its call had its result —
// the spinner was the plan's own state — so ending unanswered calls did not
// reach it. A plan stops with its process as a step does.

const planAt = (seq, id, todos, message = `p${seq}`) => ({
  type: 'assistant',
  seq,
  session_id: 's',
  message: { id: message, role: 'assistant', model: 'claude-opus-5', content: [{ type: 'tool_use', id, name: 'TodoWrite', input: { todos } }] },
})
const TODOS = [
  { content: 'Add the column', status: 'completed', activeForm: 'Adding the column' },
  { content: 'Run the tests', status: 'in_progress', activeForm: 'Running the tests' },
  { content: 'Open the PR', status: 'pending', activeForm: 'Opening the PR' },
]
const PLAN_DIES = [
  { type: 'broker.user_message', session_id: 's', text: 'ship it', seq: 1 },
  planAt(2, 'plan-1', TODOS),
  back(3, 'plan-1', 'Todos have been modified successfully.'),
  exitAt(4),
]
const plansOf = (state) => stepsOf(state).filter((b) => b.name === 'TodoWrite')

test('a plan card with a step in flight stops with its process, and reads ended rather than failed', () => {
  const state = applyAll(createTranscript(), PLAN_DIES.slice(0, 3))
  assert.equal(blockView(plansOf(state)[0]).running, true, 'before the exit the plan is running')
  apply(state, PLAN_DIES[3])
  const view = blockView(plansOf(state)[0])
  assert.equal(view.running, false, 'the plan card kept spinning after its process ended')
  assert.equal(view.sub, '1 of 3 done · ended with the chat')
  assert.equal(view.title, 'Planned 3 steps', 'the title still said a step was being done')
  assert.equal(view.isError, undefined)
  assert.deepEqual(view.steps.map((s) => s.state), ['done', 'doing', 'todo'], 'the plan’s own states are what it reached')
})

test('a finished plan at an exit is just a finished plan', () => {
  const done = TODOS.map((t) => ({ ...t, status: 'completed' }))
  const state = applyAll(createTranscript(), [PLAN_DIES[0], planAt(2, 'plan-1', done), back(3, 'plan-1', 'ok'), exitAt(4)])
  const view = blockView(plansOf(state)[0])
  assert.equal(view.running, false)
  assert.equal(view.sub, '3 of 3 done')
})

test('a later plan update, from the resumed process, is the card that runs', () => {
  const state = applyAll(createTranscript(), PLAN_DIES)
  const next = [TODOS[0], { ...TODOS[1], status: 'completed' }, { ...TODOS[2], status: 'in_progress' }]
  applyAll(state, [{ type: 'broker.user_message', session_id: 's', text: 'go on', seq: 5 }, planAt(6, 'plan-2', next), back(7, 'plan-2', 'ok')])
  const [old, now] = plansOf(state).map(blockView)
  assert.equal(old.running, false, 'the plan from the ended process started spinning again')
  assert.equal(now.running, true)
  assert.equal(now.title, 'Opening the PR')
  assert.equal(now.sub, '2 of 3 done')
})

test('a plan in a page of history above an exit is stopped too', () => {
  const state = createTranscript()
  apply(state, { type: 'broker.session', session_id: 's', state: 'exited', status: 'done', record_seq: 4, turns: [], window_first_seq: 4, turns_before: 1 })
  apply(state, exitAt(4))
  prependEvents(state, [{ type: 'broker.session', session_id: 's', window_first_seq: 1, turns_before: 0 }, ...PLAN_DIES.slice(0, 3)])
  assert.equal(blockView(plansOf(state)[0]).running, false)
})
