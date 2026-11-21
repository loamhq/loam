import test from 'node:test'
import assert from 'node:assert/strict'
import { applyThreadEvent } from '../public/thread-record.js'
import { replyQuotes } from '../public/thread-replies.js'
import { fakeBroker, appAgainst, closing, TOKEN } from './harness.js'

const json = (res, body, status = 200) => {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}

test('opening a quoted composer is one authenticated relay request', async (t) => {
  const source = { id: 'm12', thread: 'source', title: 'Plans', author: 'agent', text: 'Keep Friday open.' }
  const composer = { id: 'new', members: [], draft: null, draft_reply: source, ordered_submissions: true }
  const payload = { message: source.id, persona: 'agent' }
  const broker = await fakeBroker({
    'POST /v1/threads/source/branch': (req, res) => json(res, composer, 201),
  })
  const app = await appAgainst(broker)
  t.after(closing(app, broker))
  const url = app.url + '/api/threads/source/branch'
  for (const method of ['GET', 'DELETE']) assert.equal((await fetch(url, { method })).status, 405)
  assert.equal((await fetch(url, { method: 'POST', body: 'broken JSON' })).status, 400)
  const response = await fetch(url, { method: 'POST', body: JSON.stringify(payload) })
  assert.equal(response.status, 201)
  assert.equal(response.headers.get('cache-control'), 'no-store')
  assert.deepEqual(await response.json(), composer)
  assert.deepEqual(broker.seen.map(row => [row.method, row.path, row.auth, JSON.parse(row.body)]),
    [['POST', '/v1/threads/source/branch', `Bearer ${TOKEN}`, payload]])
})

test('a cross-thread quote does not try to fetch the same message ID from local history', async () => {
  let reads = 0
  const quotes = replyQuotes({ read: async () => { reads++; return { has_more: false, messages: [{ id: 'm2' }] } } })
  await quotes.ensure([{ id: 'm8', reply_to: 'm2', reply_source: { thread: 'source' } }], true)
  assert.equal(reads, 0)
  await quotes.ensure([{ id: 'm9', reply_to: 'm2' }], true)
  assert.equal(reads, 1)
})

test('live branch notices insert after their source, preserve sibling branches and deduplicate HTTP/SSE; off-window notices stay off-window', () => {
  const o = { thread: { members: [] }, messages: [{ id: 'm1', author: 'you' }, { id: 'm2', author: 'sprout' }, { id: 'm3', author: 'you' }], typing: new Set(), permissions: new Map() }
  const notice = (id, after) => ({ type: 'message.done', message: { id, kind: 'system', author: 'system', after, text: 'Branched into A thought', link: { thread: 'new', title: 'A thought' } } })
  applyThreadEvent(o, notice('m4', 'm1')); applyThreadEvent(o, notice('m4', 'm1')); applyThreadEvent(o, notice('m5', 'm1'))
  assert.deepEqual(o.messages.map((m) => m.id), ['m1', 'm4', 'm5', 'm2', 'm3'])
  applyThreadEvent(o, notice('m6', 'm0'))
  assert.equal(o.messages.length, 5); assert.equal(o.after, 'm6')
})
