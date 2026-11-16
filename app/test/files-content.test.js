import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { once } from 'node:events'
import { createApp } from '../src/server.js'
import { readConfig } from '../src/config.js'
import { createContent } from '../src/content.js'
import { fakeBroker, appAgainst, closing, TOKEN } from './harness.js'
import { row } from './fixtures/files-contract.js'

const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const broker = await fakeBroker({
  'GET /v1/files/stat': (req, res, url) => {
    const p = url.searchParams.get('path')
    if (p === 'gone.html') { res.writeHead(404, { 'content-type': 'application/json' }); return res.end('{"error":"The host no longer has this file."}') }
    json(res, row(url.searchParams.get('root'), p, p === 'blocked.html' ? { capabilities: { read: true, download: false } } : {}))
  },
  'GET /v1/files/download': (req, res, url) => {
    res.writeHead(200, { 'content-type': 'application/octet-stream' })
    res.end(url.searchParams.get('path').endsWith('.js') ? 'document.title = "Running"' : '<script>document.title = "Running"</script>')
  },
  'GET /v1/agents': (req, res) => json(res, { agents: [] }),
  'POST /v1/sessions': (req, res) => json(res, { started: true }),
})
const app = await appAgainst(broker, { FILES_CONTENT_ORIGIN: 'https://content.example.org' })
test.after(closing(broker, app))

function contentRequest(path, { method = 'GET', headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname: '127.0.0.1', port: new URL(app.url).port, path, method, headers: { host: 'content.example.org', ...headers } }, (res) => {
      const chunks = []
      res.on('data', (c) => chunks.push(c))
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, text: Buffer.concat(chunks).toString() }))
    })
    req.on('error', reject)
    req.end()
  })
}
const live = async (root = 'vault') => {
  const res = await fetch(`${app.url}/api/files/live?root=${root}&path=Notes/page.html`)
  assert.equal(res.status, 200)
  return new URL((await res.json()).url)
}

test('live HTML and sibling assets are served unchanged only on the content authority, including workspaces', async () => {
  for (const root of ['vault', 'spaces']) {
    const url = await live(root)
    const html = await contentRequest(url.pathname)
    assert.equal(html.status, 200)
    assert.equal(html.headers['content-type'], 'text/html; charset=utf-8')
    assert.equal(html.text, '<script>document.title = "Running"</script>')
    assert.match(html.headers['content-security-policy'], /sandbox allow-scripts allow-same-origin allow-forms allow-popups allow-modals/)
    assert.match(html.headers['content-security-policy'], /connect-src https:\/\/content.example.org;/)
    assert.equal(html.headers['access-control-allow-origin'], undefined)
    assert.equal(html.headers['set-cookie'], undefined)
    const js = await contentRequest(new URL('app.js', url).pathname)
    assert.equal(js.headers['content-type'], 'text/javascript; charset=utf-8')
    assert.match(js.text, /Running/)
    assert.equal((await fetch(`${app.url}${url.pathname}`)).status, 404, 'never scriptful bytes on the app authority')
  }
  const csp = (await fetch(app.url)).headers.get('content-security-policy')
  assert.match(csp, /frame-src 'self' blob: https:\/\/\*\.example.com https:\/\/content.example.org/)
  assert.doesNotMatch(csp, /unsafe-inline|unsafe-eval/)
})

test('content has no app, relay, static code, or mutation routes, even with a valid capability', async () => {
  const url = await live()
  const before = broker.seen.length
  for (const path of ['/api/agents', '/api/sessions', '/app.js', '/files', '/up', '/content/no-token/vault/page.html']) {
    assert.equal((await contentRequest(path)).status, 404)
  }
  for (const method of ['POST', 'PUT', 'DELETE', 'OPTIONS']) assert.equal((await contentRequest(url.pathname, { method })).status, 405)
  assert.equal(broker.seen.length, before)
})

test('a live URL is issued only for an HTML file the broker offers for download, and grants are redacted from logs', async () => {
  for (const [path, status] of [['blocked.html', 403], ['gone.html', 404], ['file.md', 415], ['', 400], ['/absolute.html', 400]]) {
    assert.equal((await fetch(`${app.url}/api/files/live?root=vault&path=${encodeURIComponent(path)}`)).status, status)
  }
  const url = await live()
  await contentRequest(url.pathname)
  const grant = url.pathname.split('/')[2]
  const lines = app.lines.filter((line) => line.path?.startsWith('/content/'))
  assert.ok(lines.length)
  assert.equal(lines.some((line) => JSON.stringify(line).includes(grant)), false)
})

test('capabilities cannot be forged, changed to another root, used without a signature, or traversed', async () => {
  const url = await live()
  const before = broker.seen.length
  for (const path of [url.pathname.replace('/vault/', '/spaces/'), url.pathname.replace(/(\/content\/)[^.]+/, '$1invalid'), url.pathname.replace('Notes/page.html', 'Notes/%2fapi'), url.pathname.replace('Notes/page.html', 'Notes/%5capi'), url.pathname.replace('Notes/page.html', '%2e%2e/page.html')]) {
    assert.ok([400, 403].includes((await contentRequest(path)).status), path)
  }
  assert.equal(broker.seen.length, before)
})

test('a capability expires and a restart revokes it', async () => {
  let clock = 0
  const local = createContent({ origin: 'https://content.example.org', broker: {}, now: () => clock })
  const url = new URL(local.issue('vault', 'page.html'))
  clock = 3600_001
  const server = http.createServer((req, res) => local.serve(req, res, new URL(req.url, 'https://content.example.org')))
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  try {
    const res = await fetch(`http://127.0.0.1:${server.address().port}${url.pathname}`)
    assert.equal(res.status, 403)
    assert.match(await res.text(), /expired/)
    assert.equal((await contentRequest(url.pathname)).status, 403, 'different process-local key')
  } finally { await closing({ server })() }
})

test('cross-origin reads, simple writes, null origins, forms and preflights are rejected before the broker', async () => {
  const before = broker.seen.length
  for (const headers of [{ 'sec-fetch-site': 'same-site' }, { 'sec-fetch-site': 'cross-site' }, { origin: 'null' }, { origin: 'https://content.example.org' }]) {
    for (const method of ['GET', 'POST', 'OPTIONS']) {
      const res = await fetch(`${app.url}/api/${method === 'GET' ? 'agents' : 'sessions'}`, { method, headers })
      assert.equal(res.status, 403)
      assert.equal(res.headers.get('access-control-allow-origin'), null)
    }
  }
  assert.equal(broker.seen.length, before)
  assert.equal((await fetch(`${app.url}/api/agents`, { headers: { 'sec-fetch-site': 'same-origin' } })).status, 200)
})

test('without a content origin the app explains the missing configuration; invalid origins fail startup', async () => {
  const unconfigured = await appAgainst(broker)
  try {
    const res = await fetch(`${unconfigured.url}/api/files/live?root=vault&path=page.html`)
    assert.equal(res.status, 503)
    assert.match((await res.json()).error, /separate content origin/)
    assert.equal((await fetch(`${unconfigured.url}/up`)).status, 200)
  } finally { await closing(unconfigured)() }
  for (const origin of ['https://example.org/path', 'https://user:pass@example.org', 'http://example.org', 'data:text/html,x']) {
    assert.throws(() => readConfig({ FILES_CONTENT_ORIGIN: origin }))
  }
  const sameHostname = await appAgainst(broker, { FILES_CONTENT_ORIGIN: 'http://127.0.0.1:1' })
  try {
    const res = await fetch(`${sameHostname.url}/api/files/live?root=vault&path=page.html`)
    assert.equal(res.status, 503)
    assert.match((await res.json()).error, /different hostname/)
  } finally { await closing(sameHostname)() }
})

test('Copy link trusts published mappings behind login; unserved spaces keep app links', async () => {
  const { server } = createApp({
    config: readConfig({ BROKER_URL: broker.url, BROKER_TOKEN: TOKEN, LOG_LEVEL: 'off', FILES_VAULT_URLS: 'https://notes.example.org',
      FILES_WEB_URLS: JSON.stringify([{ root: 'spaces', path: 'shared/www', url: 'https://files.example.org' }]),
    }),
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const origin = `http://127.0.0.1:${server.address().port}`
  try {
    const link = async (root, path) => (await fetch(`${origin}/api/files/link?root=${root}&path=${encodeURIComponent(path)}`)).json()
    assert.deepEqual(await link('vault', 'Notes/My page.html'), { url: 'https://notes.example.org/Notes/My%20page.html', kind: 'Vault web link' })
    assert.deepEqual(await link('vault', 'Notes/private.md'), { url: 'https://notes.example.org/Notes/private.md', kind: 'Vault web link' })
    assert.deepEqual(await link('spaces', 'agent/page.html'), { url: '/files/spaces/agent/page.html', kind: 'Loam link' })
    assert.deepEqual(await link('vault', 'Nested folder/Café — 東京/My #1 100%.html'), { url: 'https://notes.example.org/Nested%20folder/Caf%C3%A9%20%E2%80%94%20%E6%9D%B1%E4%BA%AC/My%20%231%20100%25.html', kind: 'Vault web link' })
    assert.deepEqual(await link('spaces', 'shared/www/Some folder/page.html'), { url: 'https://files.example.org/Some%20folder/page.html', kind: 'Web link' })
    assert.deepEqual(await link('spaces', 'shared/www-other/page.html'), { url: '/files/spaces/shared/www-other/page.html', kind: 'Loam link' })
    assert.equal((await fetch(`${origin}/api/files/link?root=vault&path=../x`)).status, 400)
  } finally { await closing({ server })() }
})
