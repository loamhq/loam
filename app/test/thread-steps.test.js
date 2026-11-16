import test from 'node:test'
import assert from 'node:assert/strict'
import { fullStepSegments, createReplySteps, memberTakingTurn } from '../public/thread-steps.js'
import { messageSegments, applyThreadEvent } from '../public/thread-record.js'
import { threadWaiting } from '../public/thread-experience.js'
import { fullSteps, intro, conclusion, longOutput } from './thread-steps-fixture.mjs'
import { fakeBroker, appAgainst, closing, TOKEN } from './harness.js'

test('full steps preserve text/work/text order, untrimmed outputs, errors, thinking and nested subagents', () => {
  const segments = fullStepSegments(fullSteps)
  assert.deepEqual(segments.map((s) => s.text ?? (s.steps ? 'steps' : s.item.kind)), [intro, 'steps', 'answer', conclusion, 'result'])
  const steps = segments[1].steps
  assert.deepEqual(steps.map((s) => s.name ?? s.type ?? s.kind), ['thinking', 'Read', 'Bash', 'Agent', 'Read', 'permission'])
  assert.equal(steps[2].result, longOutput)
  assert.equal(steps[4].isError, true)
  assert.deepEqual(steps[3].children.flatMap((n) => n.blocks.map((b) => b.text ?? b.name)), ['I’ll check the smaller details.', 'Read', 'The plan leaves enough room.'])
  assert.equal(steps[3].children[1].blocks[0].result, 'Leave room between commitments.')
  assert.equal(steps[5].behavior, 'allow')
  assert.deepEqual(segments[2].item.answers, { pace: ['A little room'] })
  assert.equal(segments[4].item.usage.input, 1800)
  assert.equal(segments[4].item.totalCost, 0.02)
  assert.equal(segments[4].item.cost, null, 'never infer a per-turn cost from process totals')
  assert.equal(segments[4].item.model, 'claude-sonnet-4-6')
  assert.equal(fullStepSegments(fullSteps), segments, 'stable block identity keeps opened folds across redraw')
  const lateChild = fullStepSegments([...fullSteps, { type: 'end', parent: 'agent', event: { type: 'result', model: 'a-subagent-model' } }])
  assert.equal(lateChild.at(-1).item.model, 'claude-sonnet-4-6', 'a late subagent result never renames the top-level turn')
})
test('a recovered multi-turn reply shows every reply in its full order and never duplicates the readable tail', () => {
  const steps = [...fullSteps, { type: 'text', text: 'A second reply.' }, { type: 'end', event: { type: 'result' } }, { type: 'text', text: 'The final reply.' }]
  const parts = messageSegments({ text: 'The final reply.', did: [{ label: 'An old short label' }], steps })
  assert.deepEqual(parts.filter((p) => p.text).map((p) => p.text), [intro, conclusion, 'A second reply.', 'The final reply.'])
})
test('missing full records retain did and Said ordering; empty records retain readable text', () => {
  const old = { text: 'Last', did: [{ label: 'Said: First' }, { label: 'Read the note' }] }
  assert.deepEqual(messageSegments(old), [{ text: 'First' }, { did: [{ label: 'Read the note' }] }, { text: 'Last' }])
  assert.deepEqual(messageSegments({ ...old, steps: [] }), [{ text: 'Last' }])
})
test('completed and replayed full records beat live label segments, including repeated light message.done', () => {
  const open = { thread: { id: 'one', members: [{ persona: 'sprout' }] }, messages: [], typing: new Set(), permissions: new Map() }
  applyThreadEvent(open, { type: 'message.delta', persona: 'sprout', draft: 'A draft' })
  applyThreadEvent(open, { type: 'turn.step', persona: 'sprout', label: 'Read a note' })
  const message = { id: 'm1', author: 'sprout', text: conclusion, did: [{ label: 'Read a note' }] }
  applyThreadEvent(open, { type: 'message.done', message: { ...message, steps: fullSteps } })
  assert.equal(messageSegments(open.messages[0]), fullStepSegments(fullSteps))
  applyThreadEvent(open, { type: 'message.done', message })
  assert.equal(messageSegments(open.messages[0]), fullStepSegments(fullSteps))
  assert.equal(open.messages.length, 1)
  assert.equal(open.after, 'm1')
})
test('unknown events are ignored and an interrupted tool is ended rather than left spinning', () => {
  const parts = fullStepSegments([null, { type: 'thinking', thinking: ' ' }, { type: 'future.step' }, { type: 'event', event: { type: 'future.event' } }, { type: 'tool', id: 'unfinished', name: 'Read', input: {}, is_error: true }])
  assert.equal(parts.length, 1)
  assert.equal(parts[0].steps[0].ended, true)
  assert.equal(parts[0].steps[0].isError, true, 'an error without output still counts as a failure')
  const record = { steps: [{ type: 'text', text: 'A reply without assistant metadata.' }, { type: 'end', event: { type: 'result' } }], model: 'a-model' }
  assert.equal(messageSegments(record).at(-1).item.model, 'a-model', 'the entry’s own model is available when block metadata is absent')
})
test('a completed reply keeps the working line until the thread turn finishes, including another reply', () => {
  const open = { thread: { id: 'one', members: [] }, messages: [], typing: new Set(), permissions: new Map() }
  applyThreadEvent(open, { type: 'turn.started', persona: 'sprout' })
  for (const id of ['m1', 'm2']) {
    applyThreadEvent(open, { type: 'message.done', message: { id, author: 'sprout', text: conclusion, steps: fullSteps } })
    assert.ok(threadWaiting(open, 'sprout', 'Sprout', 0))
  }
  applyThreadEvent(open, { type: 'turn.finished', persona: 'sprout' })
  assert.equal(threadWaiting(open, 'sprout', 'Sprout', 0), null)
  assert.equal(open.messages.length, 2)
})
test('subagent and background steps do not split or duplicate the growing main draft', () => {
  const open = { thread: { id: 'one', members: [] }, messages: [], typing: new Set(), permissions: new Map() }
  applyThreadEvent(open, { type: 'message.delta', persona: 'sprout', draft: 'First words' })
  applyThreadEvent(open, { type: 'turn.step', persona: 'sprout', label: '↳ Read a smaller note' })
  applyThreadEvent(open, { type: 'message.delta', persona: 'sprout', draft: 'First words, continued.' })
  applyThreadEvent(open, { type: 'turn.step', persona: 'sprout', label: 'Waiting on a background task', waiting: true })
  assert.match(threadWaiting(open, 'sprout', 'Sprout', 0).text, /waiting on a background task/, 'the informational wait stays available beside the preserved prose')
  assert.deepEqual(messageSegments(open.messages[0]), [{ text: 'First words, continued.' }, { did: [{ label: '↳ Read a smaller note' }], concurrent: true }], 'the waiting line is activity, not a duplicate reply step')
  applyThreadEvent(open, { type: 'turn.step', persona: 'sprout', label: 'Bash: check the plan' })
  assert.deepEqual(messageSegments(open.messages[0]).filter((p) => p.text), [{ text: 'First words, continued.' }])
})

const turn = () => new Promise((resolve) => setImmediate(resolve))
test('lazy records wait for visibility or explicit opening, deduplicate, and limit concurrent reads', async () => {
  let callback, observed = new Set(), calls = [], complete = new Map()
  class Observer {
    constructor(cb, options) { callback = cb; assert.equal(options.root, pane) }
    observe(node) { observed.add(node) } unobserve(node) { observed.delete(node) } disconnect() { observed.clear() }
  }
  const nodes = ['m1', 'm2', 'm3'].map((id) => ({ getAttribute: () => id })), pane = { querySelectorAll: () => nodes }
  const changed = []
  const loader = createReplySteps({ Observer, read: (id) => { calls.push(id); return new Promise((resolve) => complete.set(id, resolve)) }, changed: (id) => changed.push(id) })
  assert.deepEqual(calls, [], 'construction does not fetch')
  loader.watch(pane); assert.equal(observed.size, 3); assert.deepEqual(calls, [], 'observation does not fetch offscreen replies')
  callback([{ target: nodes[0], isIntersecting: true }, { target: nodes[1], isIntersecting: false }]); await turn()
  assert.deepEqual(calls, ['m1'])
  loader.ensure('m1'); loader.ensure('m2'); loader.ensure('m3'); await turn()
  assert.deepEqual(calls, ['m1', 'm2'])
  complete.get('m1')({ steps: [{ message: 'm1', steps: fullSteps, recovered: true }] }); await turn()
  assert.deepEqual(calls, ['m1', 'm2', 'm3'])
  assert.equal(loader.entries.get('m1').entry.recovered, true)
  assert.deepEqual(changed, ['m1'])
  loader.stop(); complete.get('m2')({ steps: [{ message: 'm2', steps: [] }] }); await turn()
  assert.deepEqual(changed, ['m1'], 'late loads do not redraw a different thread')
  assert.equal(observed.size, 0)
})
test('404/405 fall back once; transient and malformed responses offer an explicit retry', async () => {
  for (const status of [404, 405, 500, null]) {
    let calls = 0
    const loader = createReplySteps({ read: async (id) => { calls++; if (calls === 1) { if (status) throw Object.assign(new Error('Unavailable'), { status }); return { steps: [] } }; return { steps: [{ message: id, steps: fullSteps }] } }, changed() {} })
    loader.ensure('m1'); await turn(); loader.ensure('m1'); await turn(); assert.equal(calls, 1)
    assert.equal(loader.entries.get('m1').status, [404, 405].includes(status) ? 'missing' : 'error')
    loader.ensure('m1', true); await turn()
    assert.equal(calls, [404, 405].includes(status) ? 1 : 2)
    loader.stop()
  }
})
test('the member dot distinguishes working/asking from warm idle/background, independent of End session residency', () => {
  const thread = { id: 'one' }, member = { persona: 'sprout', running: true, busy: false }, open = { thread, typing: new Set(), permissions: new Map() }
  for (const [state, expected] of [['working', true], ['waiting', true], ['idle', false], ['background', false]]) assert.equal(memberTakingTurn(thread, member, open, { state }), expected)
  assert.equal(memberTakingTurn(thread, member, open), false)
  assert.equal(memberTakingTurn(thread, { persona: 'sprout', running: true }, open), true, 'older thread summaries use running as the busy alias')
  assert.equal(memberTakingTurn(thread, { ...member, busy: true }, open), true)
  open.typing.add('sprout'); assert.equal(memberTakingTurn(thread, member, open), true)
  assert.equal(memberTakingTurn(thread, member, open, { state: 'background' }), false, 'live view says the turn has ended even if a delayed thread event still decorates it')
  assert.equal(memberTakingTurn(thread, member, open, { state: 'idle' }), false)
  open.typing.clear(); open.permissions.set('r', { persona: 'sprout' }); assert.equal(memberTakingTurn(thread, member, open), true)
  assert.equal(memberTakingTurn({ id: 'other' }, member, open), false)
})
test('full-record reads and stream flags pass through the authenticated no-store relay without widening writes', async (t) => {
  const routes = {}, answer = { thread: 'one', steps: [{ message: 'm1', steps: fullSteps }] }
  for (const route of ['/v1/threads/one/steps', '/v1/threads/one']) routes[`GET ${route}`] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(answer)) }
  routes['GET /v1/threads/one/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.end('data: {"type":"message.done"}\n\n') }
  const broker = await fakeBroker(routes), app = await appAgainst(broker); t.after(closing(app, broker))
  for (const suffix of ['/steps?message=m1&ignored=no', '?steps=full&ignored=no', '/events?after=m1&steps=full&ignored=no']) {
    const res = await fetch(`${app.url}/api/threads/one${suffix}`); assert.equal(res.status, 200); assert.match(res.headers.get('cache-control'), /no-store/); await res.text()
  }
  assert.deepEqual(broker.seen.map((r) => r.search), ['?message=m1', '?steps=full', '?after=m1&steps=full'])
  assert.ok(broker.seen.every((r) => r.auth === `Bearer ${TOKEN}`))
  assert.equal((await fetch(`${app.url}/api/threads/one/steps`, { method: 'POST' })).status, 405)
})
