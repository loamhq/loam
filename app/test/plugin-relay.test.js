import test from 'node:test'
import assert from 'node:assert/strict'
import { fakeBroker, appAgainst, closing, TOKEN } from './harness.js'
const json = (res, status, body) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }

test('plugin JSON relay preserves previews, stale confirmation questions, refusals and writes', async t => {
  const preview = { id: 'example', confirm: 'preview-token', declaration: { page_code: true }, source: { repo: 'example/plugin', ref: 'v1' } }
  const broker = await fakeBroker({
    'GET /v1/plugins': (req, res) => json(res, 200, { api: 0, plugins: [] }),
    'POST /v1/plugins': (req, res, url, raw) => json(res, JSON.parse(raw).confirm ? 409 : 200, JSON.parse(raw).confirm ? { error: 'The declaration changed.', preview } : preview),
    'GET /v1/plugins/example': (req, res) => json(res, 200, { plugin: { id: 'example' }, rows: [] }),
    'PATCH /v1/plugins/example': (req, res, url, raw) => json(res, 200, { plugin: { id: 'example', ...JSON.parse(raw) }, rows: [] }),
    'DELETE /v1/plugins/example': (req, res) => json(res, 409, { error: 'Shipped; turn it off instead.' }),
  }), app = await appAgainst(broker)
  t.after(closing(app, broker))
  for (const [method, path, body, status] of [['GET', '/plugins', null, 200], ['POST', '/plugins', { url: 'example/plugin' }, 200], ['POST', '/plugins', { url: 'example/plugin', confirm: 'stale' }, 409], ['GET', '/plugins/example', null, 200], ['PATCH', '/plugins/example', { on: false }, 200], ['DELETE', '/plugins/example', null, 409]]) {
    const response = await fetch(app.url + '/api' + path, { method, ...(body ? { body: JSON.stringify(body) } : {}) })
    assert.equal(response.status, status); assert.equal(response.headers.get('cache-control'), 'no-store')
    const out = await response.json()
    if (method === 'POST') assert.deepEqual(out.preview ?? out, preview)
    if (method === 'DELETE') assert.equal(out.error, 'Shipped; turn it off instead.')
  }
  assert.ok(broker.seen.every(row => row.auth === `Bearer ${TOKEN}`))
})

test('plugin bytes and HEAD stay bytes, encode segments, keep MIME/cache/nosniff and refuse writes', async t => {
  const code = 'export const answer = 42;\n'
  const serve = (req, res) => { res.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8', 'cache-control': 'no-cache' }); res.end(req.method === 'HEAD' ? '' : code) }
  const broker = await fakeBroker({ 'GET /v1/plugins/example/files/page/a b.js': serve, 'HEAD /v1/plugins/example/files/page/a b.js': serve }), app = await appAgainst(broker)
  t.after(closing(app, broker))
  for (const method of ['GET', 'HEAD']) {
    const response = await fetch(app.url + '/api/plugins/example/files/page/a%20b.js', { method })
    assert.equal(response.status, 200); assert.equal(response.headers.get('content-type'), 'text/javascript; charset=utf-8')
    assert.equal(response.headers.get('cache-control'), 'no-cache'); assert.equal(response.headers.get('x-content-type-options'), 'nosniff')
    assert.equal(await response.text(), method === 'HEAD' ? '' : code)
  }
  assert.deepEqual(broker.seen.map(row => row.method), ['GET', 'HEAD'])
  assert.equal((await fetch(app.url + '/api/plugins/example/files/page/a.js', { method: 'POST' })).status, 405)
  assert.equal(broker.seen.length, 2)
})

test('threads permission cards retain their POST answer seam and host refusal sentences', async t => {
  const broker = await fakeBroker({ 'POST /v1/sessions/session id/permissions/request id': (req, res, url, raw) => json(res, 409, { error: 'This question has already been answered.', received: JSON.parse(raw) }) }), app = await appAgainst(broker)
  t.after(closing(app, broker))
  const body = { behavior: 'deny', message: 'Keep the original.' }
  const response = await fetch(app.url + '/api/sessions/session%20id/permissions/request%20id', { method: 'POST', body: JSON.stringify(body) })
  assert.equal(response.status, 409); assert.deepEqual(await response.json(), { error: 'This question has already been answered.', received: body })
  assert.equal(broker.seen[0].auth, `Bearer ${TOKEN}`)
})
