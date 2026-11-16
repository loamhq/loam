import test from 'node:test'
import assert from 'node:assert/strict'
import { applyThreadEvent, messageSegments } from '../public/thread-record.js'
import { agentLook, createAgents, setAvatarColours } from '../public/sessions.js'
import { readConfig } from '../src/config.js'
import { fakeBroker, appAgainst, closing } from './harness.js'

const open = () => ({ thread: { id: 'one', members: [] }, messages: [], typing: new Set(), permissions: new Map() })
test('durable segments outrank partial local segments and survive repeated done events and reload', () => {
  const o = open()
  applyThreadEvent(o, { type: 'message.delta', persona: 'scout', draft: 'Partial' })
  applyThreadEvent(o, { type: 'turn.step', persona: 'scout', label: 'Read the note' })
  const message = { id: 'm12', author: 'scout', text: 'Here is the answer.', segments: [{ text: 'The complete pre-tool text. '.repeat(20) }, { did: [{ label: 'Read the note' }] }], did: [{ label: 'Said: shortened' }] }
  applyThreadEvent(o, { type: 'message.done', message })
  applyThreadEvent(o, { type: 'message.done', message })
  assert.deepEqual(messageSegments(o.messages[0]), [...message.segments, { text: message.text }])
  assert.deepEqual(messageSegments(JSON.parse(JSON.stringify(o.messages[0]))), messageSegments(message))
})
test('background waits and seat waits resume into text; turn.finished settles a failed turn', () => {
  const o = open()
  applyThreadEvent(o, { type: 'thread.snapshot', turn: { persona: 'scout', waiting: 'Scout is waiting for a free seat.' } })
  assert.equal(o.waiting.get('scout'), 'Scout is waiting for a free seat.')
  applyThreadEvent(o, { type: 'message.delta', persona: 'scout', draft: 'Reading now.' })
  assert.equal(o.waiting.size, 0)
  applyThreadEvent(o, { type: 'turn.step', persona: 'scout', label: 'Waiting on a background task: Read notes', waiting: true })
  assert.equal(o.steps.get('scout').waiting, true)
  applyThreadEvent(o, { type: 'message.delta', persona: 'scout', draft: 'The next step.' })
  assert.equal(o.steps.size, 0)
  applyThreadEvent(o, { type: 'turn.waiting', persona: 'scout', text: 'Waiting again.' })
  applyThreadEvent(o, { type: 'turn.finished', persona: 'scout' })
  assert.equal(o.typing.size, 0); assert.equal(o.steps.size, 0); assert.equal(o.progress.size, 0); assert.equal(o.waiting.size, 0)
  assert.equal(o.messages.length, 0)
  assert.equal(o.after, undefined)
})
test('one configured palette exception preserves every other original ID colour', (t) => {
  t.after(() => setAvatarColours())
  const ids = Array.from({ length: 20 }, (_, n) => `persona-${n}`)
  const before = ids.map((id) => agentLook(id))
  const token = before[2].color === 'var(--persona-plum)' ? 'persona-teal' : 'persona-plum'
  setAvatarColours({ [ids[2]]: token })
  for (const [i, id] of ids.entries()) {
    const actual = agentLook(id, createAgents(ids.map((id) => ({ id, name: id }))))
    assert.equal(actual.color, i === 2 ? `var(--${token})` : before[i].color)
    assert.equal(actual.shape, before[i].shape)
  }
  assert.deepEqual(readConfig({ AVATAR_COLOURS: JSON.stringify({ sample: 'persona-plum' }) }).avatarColours, { sample: 'persona-plum' })
  assert.throws(() => readConfig({ AVATAR_COLOURS: '{"sample":"red"}' }), /palette/)
})
test('the thread-list path is SSE with archived filtering, never a JSON thread named events', async (t) => {
  const events = [{ type: 'threads.snapshot', threads: [], agents: [] }, { type: 'thread.changed', thread: 'one', summary: { id: 'one', members: [] } }, { type: 'thread.removed', thread: 'one' }]
  const broker = await fakeBroker({
    'GET /v1/threads/events': (req, res, url) => {
      assert.equal(url.search, '?archived=1')
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.end(': open ping=15000\n\n' + events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(''))
    },
  }), app = await appAgainst(broker, { AVATAR_COLOURS: '{"sample":"persona-plum"}' })
  t.after(closing(app, broker))
  const res = await fetch(app.url + '/api/threads/events?archived=1&from=99')
  assert.match(res.headers.get('content-type'), /text\/event-stream/)
  assert.match(await res.text(), /threads.snapshot.*\n\ndata:.*thread.changed.*\n\ndata:.*thread.removed/)
  assert.deepEqual((await (await fetch(app.url + '/api/health')).json()).avatar_colours, { sample: 'persona-plum' })
  assert.equal((await fetch(app.url + '/api/threads/events', { method: 'DELETE' })).status, 405)
})
