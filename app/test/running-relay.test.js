import test from 'node:test'
import assert from 'node:assert/strict'
import { fakeBroker, appAgainst, closing, TOKEN } from './harness.js'

test('live JSON, live SSE and atomic idle ending reach the reserved runtime routes with authentication', async (t) => {
  const view = { sessions: [{ id: 'resident', agent: 'sprout', kind: 'thread', thread_id: 'garden', state: 'idle' }], box: { sessions: { live: 1 } }, at: '2026-11-10T00:00:00Z' }
  const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
  const broker = await fakeBroker({
    'GET /v1/sessions/live': (req, res) => json(res, view),
    'GET /v1/sessions/live/events': (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.end(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'sessions.live', ...view })}\n\n`) },
    'POST /v1/sessions/end-idle': (req, res) => json(res, { ended: [{ id: 'resident', agent: 'sprout' }], kept: [] }),
  })
  const app = await appAgainst(broker); t.after(closing(app, broker))
  const live = await fetch(`${app.url}/api/sessions/live`)
  assert.equal(live.status, 200); assert.deepEqual(await live.json(), view)
  assert.match(live.headers.get('cache-control'), /no-store/)
  const events = await fetch(`${app.url}/api/sessions/live/events?from=900`)
  assert.equal(events.status, 200); assert.match(events.headers.get('content-type'), /event-stream/)
  assert.match(await events.text(), /"type":"sessions.live"/)
  const end = await fetch(`${app.url}/api/sessions/end-idle`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' })
  assert.equal(end.status, 200); assert.equal((await end.json()).ended[0].id, 'resident')
  assert.deepEqual(broker.seen.map((r) => [r.method, r.path, r.search, r.auth]), [
    ['GET', '/v1/sessions/live', '', `Bearer ${TOKEN}`], ['GET', '/v1/sessions/live/events', '', `Bearer ${TOKEN}`], ['POST', '/v1/sessions/end-idle', '', `Bearer ${TOKEN}`],
  ])
})
