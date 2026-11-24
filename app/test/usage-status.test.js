import test from 'node:test'
import assert from 'node:assert/strict'

import { serverRows, usageStatusLine } from '../public/page/settings.js'
import { fakeBroker, appAgainst, closing } from './harness.js'

test('usageStatusLine describes local-only and configured collector honestly', () => {
  assert.equal(usageStatusLine(null), null)
  assert.equal(usageStatusLine(undefined), null)
  assert.equal(usageStatusLine({ recording: true, sending: false, collector_host: null }), 'Kept on this server only')
  assert.equal(usageStatusLine({ recording: true, sending: true, collector_host: 'usage.example.com' }), 'Sent to usage.example.com')
  assert.equal(usageStatusLine({ recording: true, sending: true, collector_host: null }), 'Sent to configured collector')
})

test('serverRows includes Usage data only when supplied and omits it for older hosts', () => {
  const base = {
    name: 'demo-box',
    system: 'Linux 6.1',
    arch: 'x64',
    processors: 2,
    memory: { total: 4 * 1024 ** 3, free: 2 * 1024 ** 3 },
    storage: { total: 80 * 1024 ** 3, free: 60 * 1024 ** 3 },
    up_seconds: 86400 * 3,
  }
  const withoutUsage = serverRows(base, 'demo.example.com')
  assert.equal(withoutUsage.some(([k]) => k === 'Usage data'), false)

  const localOnly = serverRows({ ...base, usage: { recording: true, sending: false, collector_host: null } }, 'demo.example.com')
  assert.deepEqual(localOnly.find(([k]) => k === 'Usage data'), ['Usage data', 'Kept on this server only'])

  const sending = serverRows({ ...base, usage: { recording: true, sending: true, collector_host: 'usage.example.com' } }, 'demo.example.com')
  assert.deepEqual(sending.find(([k]) => k === 'Usage data'), ['Usage data', 'Sent to usage.example.com'])
})

test('GET /api/server enriches serverFacts with /v1/usage/status and fails soft without it', async t => {
  const json = (res, body, status = 200) => {
    res.writeHead(status, { 'content-type': 'application/json' })
    res.end(JSON.stringify(body))
  }
  const broker = await fakeBroker({
    'GET /v1/usage/status': (req, res) => json(res, {
      recording: true,
      sending: true,
      collector_host: 'usage.example.com',
      destination: 'https://usage.example.com/v1/ingest',
    }),
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const res = await fetch(`${app.url}/api/server`)
  assert.equal(res.status, 200)
  assert.equal(res.headers.get('cache-control'), 'no-store')
  const body = await res.json()
  assert.deepEqual(body.usage, {
    recording: true,
    sending: true,
    collector_host: 'usage.example.com',
  })

  const relay = await fetch(`${app.url}/api/usage/status`)
  assert.equal(relay.status, 200)
  const relayBody = await relay.json()
  assert.equal(relayBody.sending, true)
  assert.equal(relayBody.collector_host, 'usage.example.com')

  const post = await fetch(`${app.url}/api/usage/status`, { method: 'POST', body: '{}' })
  assert.equal(post.status, 405)
})

test('GET /api/server succeeds when an older broker returns 404 for /v1/usage/status', async t => {
  const broker = await fakeBroker({})
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const res = await fetch(`${app.url}/api/server`)
  assert.equal(res.status, 200)
  const body = await res.json()
  assert.equal('usage' in body, false)
  assert.ok(typeof body.name === 'string')
})
