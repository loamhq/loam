// The web half, against a broker that is only a recording. No agent, no
// token that means anything, no network beyond loopback.

import test from 'node:test'
import assert from 'node:assert/strict'
import { once } from 'node:events'

import { createApp } from '../src/server.js'
import { readConfig } from '../src/config.js'
import { TOKEN, fakeBroker, appAgainst, shut, closing, logInto } from './harness.js'

test('GET /up answers 200 even with no broker configured at all', async (t) => {
  const config = readConfig({})
  const { server } = createApp({ config, log: logInto([]) })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(() => shut(server))

  const res = await fetch(`http://127.0.0.1:${server.address().port}/up`)
  assert.equal(res.status, 200)
  assert.equal(await res.text(), 'ok')

  // And the page says so plainly rather than half-working.
  const health = await (await fetch(`http://127.0.0.1:${server.address().port}/api/health`)).json()
  assert.deepEqual(health, { configured: false, ok: false, speech: false })
})

// The policy the page is written against, on the one header that carries it.
//
// It is asserted here because the fix for a blocked inline style had a cheaper
// wrong answer — add `style-src 'unsafe-inline'` and the console goes quiet —
// and that answer would take this app's one real defence against injected
// markup off, permanently, for a padding value. The page conforms instead (see
// `restyle()` in `public/ui.js`), so this says what the page may rely on: no
// inline anything, from any source.
test('the page is served under a policy that allows no inline script or style', async (t) => {
  const broker = await fakeBroker()
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const res = await fetch(`${app.url}/index.html`)
  assert.equal(res.status, 200)
  const csp = res.headers.get('content-security-policy')
  assert.match(csp, /(^|;\s*)default-src 'self'/, 'the page no longer states a default source')
  assert.doesNotMatch(csp, /unsafe-inline/, 'the policy was loosened rather than the page conformed')
  assert.doesNotMatch(csp, /unsafe-eval/)
  // And nothing has quietly been given a source of its own that is wider than
  // the default — a `style-src` naming a hash or a nonce would be a decision,
  // not an oversight, and it would be written down here.
  assert.equal(csp.includes('style-src'), false, 'a style source appeared; if it is deliberate, say why here')
})

test('the tab says Loam, as the installed app does', async (t) => {
  const broker = await fakeBroker()
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const page = await (await fetch(`${app.url}/`)).text()
  assert.match(page, /<title>Loam<\/title>/)
  const manifest = await (await fetch(`${app.url}/manifest.webmanifest`)).json()
  assert.equal(manifest.name, 'Loam')
})

test('the token reaches the broker and never the browser', async (t) => {
  const broker = await fakeBroker({
    'GET /v1/agents': (req, res) => {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ agents: ['example'] })) // plain strings, sorted
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const res = await fetch(`${app.url}/api/agents`)
  assert.equal(res.status, 200)

  const text = await res.text()
  assert.match(text, /example/)
  assert.doesNotMatch(text, new RegExp(TOKEN), 'the body carries no token')
  for (const [name, value] of res.headers) {
    assert.doesNotMatch(String(value), new RegExp(TOKEN), `header ${name} carries no token`)
  }

  const call = broker.seen.find((c) => c.path === '/v1/agents')
  assert.equal(call.auth, `Bearer ${TOKEN}`, 'the token was added on the way out')
})

test('the browser cannot reach a broker route this app has not allowed', async (t) => {
  const broker = await fakeBroker()
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const res = await fetch(`${app.url}/api/v1/anything`)
  assert.equal(res.status, 404)
  assert.equal(broker.seen.length, 0, 'nothing was forwarded')
})

test('a refusal over the session cap reaches the page in the broker’s own words', async (t) => {
  const sentence = 'At most 3 sessions run at once. End one to start another; its id still resumes.'
  const broker = await fakeBroker({
    'POST /v1/threads': (req, res) => {
      res.writeHead(429, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: sentence, cap: 3, live: 3 }))
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const res = await fetch(`${app.url}/api/threads`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ persona: 'example' }),
  })
  assert.equal(res.status, 429, 'the status is the broker’s, not translated')
  const body = await res.json()
  assert.equal(body.error, sentence)
  assert.equal(body.cap, 3)

  assert.equal(JSON.parse(broker.seen.at(-1).body).persona, 'example')
})

test('events reach the browser as they happen, not when the stream closes', async (t) => {
  let write
  const broker = await fakeBroker({
    'GET /v1/threads/s-1/events': (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      write = (obj) => res.write(`data: ${JSON.stringify(obj)}\n\n`)
      write({ type: 'broker.session', seq: 41, session_id: 's-1', agent: 'example' })
      // and then nothing, until this test says so
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const res = await fetch(`${app.url}/api/threads/s-1/events?after=m40`)
  assert.equal(res.status, 200)
  assert.match(res.headers.get('content-type'), /text\/event-stream/)
  assert.equal(res.headers.get('x-accel-buffering'), 'no')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  const readUntilData = async () => {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) throw new Error('stream closed')
      const text = decoder.decode(value, { stream: true })
      const line = text.split('\n').find((l) => l.startsWith('data: '))
      if (line) return JSON.parse(line.slice(6))
    }
  }

  const first = await readUntilData()
  assert.equal(first.seq, 41, 'the first event arrived while the stream is still open')

  // Nothing has been sent since. Send one now and it must arrive now.
  const pending = readUntilData()
  const sentAt = Date.now()
  write({ type: 'assistant', seq: 42, message: { id: 'm', role: 'assistant', content: [{ type: 'text', text: 'hi' }] } })
  const second = await pending
  assert.equal(second.seq, 42)
  assert.ok(Date.now() - sentAt < 2000, 'it did not wait for a buffer or for the stream to end')

  await reader.cancel()
  assert.equal(broker.seen.at(-1).search, '?after=m40', 'the reconnect position was passed on')
})

test('the stream says how often it will speak when it has nothing to say', async (t) => {
  const broker = await fakeBroker({
    'GET /v1/threads/s-1/events': (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write('data: {"type":"broker.session","record_seq":0}\n\n')
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const res = await fetch(`${app.url}/api/threads/s-1/events?from=0`)
  const reader = res.body.getReader()
  const { value } = await reader.read()
  const first = new TextDecoder().decode(value)
  // The page tells a quiet stream from a gone one by the clock, so the interval
  // it measures against is the server's own and is read off the wire.
  assert.match(first, /^: open ping=(\d+)\n\n/, first)
  assert.ok(Number(/ping=(\d+)/.exec(first)[1]) > 0)
  await reader.cancel()
})

test('retired classic and Activity seen routes are not forwarded', async t => {
  const broker = await fakeBroker(), app = await appAgainst(broker)
  t.after(closing(app, broker))
  for (const [method, path] of [['POST', '/sessions'], ['POST', '/sessions/seen'], ['PATCH', '/sessions/old'], ['GET', '/sessions/old/events'],
    ['POST', '/sessions/old/messages'], ['POST', '/sessions/old/attachments'], ['POST', '/sessions/old/compact'],
    ['POST', '/activity/seen']]) {
    const res = await fetch(app.url + '/api' + path, { method, ...(method === 'GET' ? {} : { body: '{}' }) })
    assert.equal(res.status, 404, `${method} ${path}`)
  }
  assert.equal(broker.seen.length, 0)
})

test('the page and its installable parts are served', async (t) => {
  const broker = await fakeBroker()
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  for (const [path, type] of [
    ['/', 'text/html'],
    ['/app.js', 'text/javascript'],
    ['/transcript.js', 'text/javascript'],
    ['/manifest.webmanifest', 'application/manifest+json'],
    ['/sw.js', 'text/javascript'],
    ['/icon-192.png', 'image/png'],
    ['/icon-512.png', 'image/png'],
  ]) {
    const res = await fetch(`${app.url}${path}`)
    assert.equal(res.status, 200, path)
    assert.match(res.headers.get('content-type'), new RegExp(type.replace('+', '\\+')), path)
  }

  const outside = await fetch(`${app.url}/../package.json`)
  assert.equal(outside.status, 404, 'nothing above the public directory is reachable')
})

// Chrome's own install criteria, as far as a manifest and a server can meet
// them: a name, a start URL, a standalone display, a 192px and a 512px icon —
// and a maskable one, or Android puts the mark on a white disc. Every icon it
// names is fetched, and its PNG header must say the size the manifest claims.
test('the manifest meets Chrome’s install criteria, and every icon in it is what it says', async (t) => {
  const broker = await fakeBroker()
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const res = await fetch(`${app.url}/manifest.webmanifest`, { redirect: 'manual' })
  assert.equal(res.status, 200)
  assert.equal(res.headers.get('content-type'), 'application/manifest+json; charset=utf-8')
  const manifest = await res.json()

  assert.equal(manifest.name, 'Loam')
  assert.equal(manifest.short_name, 'Loam')
  assert.equal(manifest.start_url, '/')
  assert.equal(manifest.scope, '/')
  assert.equal(manifest.display, 'standalone')
  assert.notEqual(manifest.prefer_related_applications, true)
  assert.equal(manifest.background_color, '#E6E1DA')

  const has = (size, purpose) =>
    manifest.icons.some((i) => i.sizes.split(' ').includes(size) && (i.purpose ?? 'any').split(' ').includes(purpose))
  for (const size of ['192x192', '512x512']) {
    assert.ok(has(size, 'any'), `an "any" icon at ${size}`)
    assert.ok(has(size, 'maskable'), `a maskable icon at ${size}`)
  }

  for (const icon of manifest.icons) {
    const png = await fetch(`${app.url}${icon.src}`, { redirect: 'manual' })
    assert.equal(png.status, 200, icon.src)
    assert.equal(png.headers.get('content-type'), icon.type, icon.src)
    const bytes = Buffer.from(await png.arrayBuffer())
    assert.equal(bytes.subarray(1, 4).toString(), 'PNG', icon.src)
    assert.equal(`${bytes.readUInt32BE(16)}x${bytes.readUInt32BE(20)}`, icon.sizes, icon.src)
  }
})

test('the head links the manifest with credentials, the favicon and the home-screen icon', async (t) => {
  const broker = await fakeBroker()
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const html = await (await fetch(`${app.url}/`)).text()
  const links = [...html.matchAll(/<link\b[^>]*>/g)].map(([tag]) =>
    Object.fromEntries([...tag.matchAll(/([a-z-]+)="([^"]*)"/g)].map(([, k, v]) => [k, v])),
  )
  const link = (rel) => links.filter((l) => l.rel === rel)

  // Without `use-credentials` the browser sends no cookie for a manifest, even
  // one on its own origin, and a login portal redirects it away.
  assert.deepEqual(link('manifest'), [{ rel: 'manifest', href: '/manifest.webmanifest', crossorigin: 'use-credentials' }])
  assert.deepEqual(link('icon').map((l) => [l.href, l.type]), [['/icon.svg', 'image/svg+xml'], ['/icon-32.png', 'image/png']])
  assert.deepEqual(link('apple-touch-icon').map((l) => l.href), ['/apple-touch-icon.png'])

  for (const { href } of [...link('icon'), ...link('apple-touch-icon')]) {
    const res = await fetch(`${app.url}${href}`, { redirect: 'manual' })
    assert.equal(res.status, 200, href)
    assert.match(res.headers.get('content-type'), /^image\//, href)
  }
})

// ------------------------------------- the routes the designed screens need

test('provider sign-out relays only POST and preserves host refusals', async (t) => {
  const broker = await fakeBroker({
    'POST /v1/providers/antigravity/logout': (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ removed: true, login: { connected: false } })) },
    'POST /v1/providers/claude-code/logout': (req, res) => { res.writeHead(405, { 'content-type': 'application/json' }); res.end(JSON.stringify({ error: 'Sign-out is not offered for this provider' })) },
  })
  const app = await appAgainst(broker)
  t.after(closing(app, broker))
  const out = await fetch(`${app.url}/api/providers/antigravity/logout`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' })
  assert.equal(out.status, 200)
  assert.deepEqual(await out.json(), { removed: true, login: { connected: false } })
  assert.equal(broker.seen[0].auth, `Bearer ${TOKEN}`)
  const refused = await fetch(`${app.url}/api/providers/claude-code/logout`, { method: 'POST' })
  assert.equal(refused.status, 405)
  assert.equal((await refused.json()).error, 'Sign-out is not offered for this provider')
  const count = broker.seen.length
  assert.equal((await fetch(`${app.url}/api/providers/antigravity/logout`)).status, 404)
  assert.equal(broker.seen.length, count)
})

test('the providers reach the page whole, the ones that are not wired included', async (t) => {
  const answer = {
    providers: [
      { id: 'claude-code', name: 'Claude Code', connected: true, reason: null, usage: { five_hour: { utilization: 0.25 } } },
      { id: 'codex', name: 'Codex', connected: false, reason: 'not wired' },
    ],
  }
  const broker = await fakeBroker({
    'GET /v1/providers': (req, res) => {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify(answer))
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const body = await (await fetch(`${app.url}/api/providers`)).json()
  // A row saying "Codex — not wired" is an answer where a missing row is a bug
  // report, so nothing here filters the list down to the connected ones.
  assert.deepEqual(body, answer)
  assert.equal('usage' in body.providers[1], false, 'an unwired provider carries no zeroed quota')
})

// The terms, relayed as the host answers them — the note whole, or already
// read by its rule. Its `404` and an older host's `405` both reach the page
// with their status, which is what the page reads as no vault terms.
test('the terms note is relayed whole and uncached, and a 404 or a 405 reaches the page as it came', async (t) => {
  const NOTE = {
    path: 'System/Dictation terms.md',
    obsidian_url: 'obsidian://open?vault=notes&file=System%2FDictation%20terms',
    modified: '2026-09-30T18:13:57Z',
    text: '# Dictation terms\n\nAda\n',
  }
  let answer = [200, NOTE]
  const broker = await fakeBroker({
    'GET /v1/dictation-terms': (req, res) => {
      res.writeHead(answer[0], { 'content-type': 'application/json', 'cache-control': 'no-store' })
      res.end(JSON.stringify(answer[1]))
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  let res = await fetch(`${app.url}/api/dictation-terms`)
  assert.equal(res.status, 200)
  assert.equal(res.headers.get('cache-control'), 'no-store')
  assert.deepEqual(await res.json(), NOTE)
  const call = broker.seen.find((c) => c.path === '/v1/dictation-terms')
  assert.equal(call.method, 'GET')
  assert.equal(call.auth, `Bearer ${TOKEN}`)

  // The host that parses the note itself: its answer, as it came.
  const PARSED = { terms: ['Ada', 'Grace Hopper'], source: 'Notes/Dictation terms.md' }
  answer = [200, PARSED]
  res = await fetch(`${app.url}/api/dictation-terms`)
  assert.equal(res.status, 200)
  assert.deepEqual(await res.json(), PARSED)

  answer = [404, { error: 'no dictation terms note on this box' }]
  res = await fetch(`${app.url}/api/dictation-terms`)
  assert.equal(res.status, 404)
  assert.deepEqual(await res.json(), { error: 'no dictation terms note on this box' })

  answer = [405, { error: 'no route' }]
  res = await fetch(`${app.url}/api/dictation-terms`)
  assert.equal(res.status, 405)
  assert.equal(res.headers.get('cache-control'), 'no-store')

  // Read-only: nothing else goes through to the host.
  res = await fetch(`${app.url}/api/dictation-terms`, { method: 'POST', body: '{}', headers: { 'content-type': 'application/json' } })
  assert.equal(res.status, 404)
  assert.equal(broker.seen.filter((c) => c.path === '/v1/dictation-terms').length, 4)
})

test('a pin is a PATCH, and the record comes back as the broker wrote it', async (t) => {
  const broker = await fakeBroker({
    'PATCH /v1/threads/s-1': async (req, res) => {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ session_id: 's-1', pinned: true, title: 'Archive project' }))
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const res = await fetch(`${app.url}/api/threads/s-1`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ pinned: true }),
  })
  assert.equal(res.status, 200)
  assert.deepEqual(await res.json(), { session_id: 's-1', pinned: true, title: 'Archive project' })

  const call = broker.seen.find((c) => c.method === 'PATCH')
  assert.equal(call.path, '/v1/threads/s-1')
  assert.equal(call.body, JSON.stringify({ pinned: true }))
  assert.equal(call.auth, `Bearer ${TOKEN}`)
})

test('a PATCH the broker refuses is refused in its own words', async (t) => {
  const broker = await fakeBroker({
    'PATCH /v1/threads/s-1': (req, res) => {
      res.writeHead(400, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: 'nothing to change: send title, pinned or archived' }))
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const res = await fetch(`${app.url}/api/threads/s-1`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({}),
  })
  assert.equal(res.status, 400)
  assert.match((await res.json()).error, /nothing to change/)
})

test('the listing passes on the two things that narrow it, and nothing else', async (t) => {
  const broker = await fakeBroker({
    'GET /v1/sessions': (req, res) => {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ sessions: [], live: 0, cap: 2 }))
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  await fetch(`${app.url}/api/sessions?agent=alfred&archived=1&cap=99`)
  const call = broker.seen.at(-1)
  assert.equal(call.search, '?agent=alfred&archived=1', 'the cap is the host’s number and not a thing to ask for')
})

test('the live list is a stream of records, and it is asked for without a position', async (t) => {
  const broker = await fakeBroker({
    'GET /v1/sessions/events': (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write(': open ping=15000\n\n')
      res.write(`data: ${JSON.stringify({ type: 'broker.session', session_id: 's-1', status: 'working', record_seq: 4 })}\n\n`)
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  // A page asking for `?from=` here would be asking a stream with no position
  // where it had got to, so the route does not offer one to pass on.
  const res = await fetch(`${app.url}/api/sessions/events?from=12&archived=1`, { headers: { accept: 'text/event-stream' } })
  assert.equal(res.status, 200)
  assert.match(res.headers.get('content-type'), /text\/event-stream/)

  const call = broker.seen.at(-1)
  assert.equal(call.path, '/v1/sessions/events')
  assert.equal(call.search, '?archived=1', 'no `from` reached the broker')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let read = decoder.decode((await reader.read()).value)
  assert.match(read, /^: open ping=\d+/, 'this app announces its own keepalive interval first')
  // Then the broker's own bytes, byte for byte: its opening comment and the
  // record. Nothing here reframes, batches or translates.
  while (!read.includes('broker.session')) read += decoder.decode((await reader.read()).value)
  assert.match(read, /data: \{"type":"broker\.session"/)
  await reader.cancel()
})

// ------------------------------------------------------ files and images

test('a file goes to the broker with the token, and the page’s body is not read on the way', async (t) => {
  const broker = await fakeBroker({
    'POST /v1/threads/s-1/attachments': (req, res, url, body) => {
      res.writeHead(201, { 'content-type': 'application/json' })
      res.end(
        JSON.stringify({
          attachments: [{ path: '/store/2026-09-21/notes.md', name: 'notes.md', size: body.length, type: 'text/markdown' }],
          sawContentType: req.headers['content-type'],
        }),
      )
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const form = new FormData()
  form.append('file', new Blob(['# notes\n'], { type: 'text/markdown' }), 'notes.md')
  const res = await fetch(`${app.url}/api/threads/s-1/attachments`, { method: 'POST', body: form })

  assert.equal(res.status, 201, 'the broker’s own status, not one of this app’s')
  const answer = await res.json()
  assert.equal(answer.attachments[0].path, '/store/2026-09-21/notes.md')
  // The multipart envelope reached the broker as the browser wrote it, boundary
  // and all: this app does not parse it, rebuild it or name the file.
  assert.match(answer.sawContentType, /^multipart\/form-data; boundary=/)

  const call = broker.seen.at(-1)
  assert.equal(call.path, '/v1/threads/s-1/attachments')
  assert.equal(call.auth, `Bearer ${TOKEN}`, 'the token was added on the way out')
  assert.match(call.body, /filename="notes\.md"/)
  assert.match(call.body, /# notes/)

  const text = JSON.stringify(answer)
  assert.doesNotMatch(text, new RegExp(TOKEN), 'the browser is told nothing about the token')
})

test('the host’s refusal is the refusal, in the host’s own words and status', async (t) => {
  // The broker reads the body before it refuses — over the cap, or a type it
  // will not carry — so the sentence always arrives. This app adds no second
  // opinion about either, and holds no copy of the number that decides.
  const broker = await fakeBroker({
    'POST /v1/threads/s-1/attachments': (req, res) => {
      res.writeHead(415, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: 'A chat does not carry a .dmg.' }))
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const form = new FormData()
  form.append('file', new Blob([new Uint8Array(4096)]), 'thing.dmg')
  const res = await fetch(`${app.url}/api/threads/s-1/attachments`, { method: 'POST', body: form })

  assert.equal(res.status, 415, 'the broker’s status, unchanged')
  assert.deepEqual(await res.json(), { error: 'A chat does not carry a .dmg.' })
  assert.equal(broker.seen.length, 1, 'the bytes went, because only the host can refuse them')
})

test('the cap is the host’s number, relayed, so the page and the broker cannot disagree', async (t) => {
  const broker = await fakeBroker({
    'GET /v1/limits': (req, res) => {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ attachment_max_bytes: 10_485_760, inline_image_max_bytes: 5_242_880, attachment_types: ['text/markdown'] }))
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const limits = await (await fetch(`${app.url}/api/limits`)).json()
  assert.deepEqual(limits, {
    attachment_max_bytes: 10_485_760,
    inline_image_max_bytes: 5_242_880,
    attachment_types: ['text/markdown'],
  })
  assert.equal(broker.seen.at(-1).path, '/v1/limits', 'asked of the host, not answered from a copy here')
  assert.equal(broker.seen.at(-1).auth, `Bearer ${TOKEN}`)
})

// Renewing a provider's sign-in: four routes, and each one is a line in the
// allowlist rather than a blanket proxy. Nothing secret is on any of them — a
// URL, a state, two dates and, on failure, one line from the provider — and the
// browser still never sees the broker's token.
test('the four sign-in routes are wired, and the code goes through as written', async (t) => {
  const answers = []
  const reply = (body, status = 200) => (req, res) => {
    res.writeHead(status, { 'content-type': 'application/json' })
    res.end(JSON.stringify(body))
  }
  const broker = await fakeBroker({
    'POST /v1/providers/claude-code/login': reply({ state: 'pending', url: 'https://example.invalid/a' }, 201),
    'GET /v1/providers/claude-code/login': reply({ state: 'pending', url: 'https://example.invalid/a' }),
    'POST /v1/providers/claude-code/login/code': reply({ state: 'done', message: null }),
    'DELETE /v1/providers/claude-code/login': reply({ state: 'idle' }),
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const attempts = [
    ['POST', '/api/providers/claude-code/login', {}],
    ['GET', '/api/providers/claude-code/login', undefined],
    ['POST', '/api/providers/claude-code/login/code', { code: 'the-code-off-the-page' }],
    ['DELETE', '/api/providers/claude-code/login', undefined],
  ]
  for (const [method, path, body] of attempts) {
    const res = await fetch(`${app.url}${path}`, {
      method,
      headers: body === undefined ? {} : { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    answers.push([`${method} ${path}`, res.status])
  }
  assert.deepEqual(answers.map(([, status]) => status), [201, 200, 200, 200], JSON.stringify(answers))

  const posted = broker.seen.find((c) => c.path === '/v1/providers/claude-code/login/code')
  assert.equal(JSON.parse(posted.body).code, 'the-code-off-the-page')
  assert.equal(posted.auth, `Bearer ${TOKEN}`)
  assert.deepEqual(
    broker.seen.map((c) => `${c.method} ${c.path}`),
    [
      'POST /v1/providers/claude-code/login',
      'GET /v1/providers/claude-code/login',
      'POST /v1/providers/claude-code/login/code',
      'DELETE /v1/providers/claude-code/login',
    ],
  )
})

test('a refusal from the sign-in routes reaches the page in the host’s own status and words', async (t) => {
  const broker = await fakeBroker({
    'POST /v1/providers/claude-code/login/code': (req, res) => {
      res.writeHead(409, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: 'No sign-in is waiting for a code. Start one.' }))
    },
    'POST /v1/providers/codex/login': (req, res) => {
      res.writeHead(404, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: 'codex has no sign-in to renew.' }))
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const late = await fetch(`${app.url}/api/providers/claude-code/login/code`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ code: 'x' }),
  })
  assert.equal(late.status, 409)
  assert.match((await late.json()).error, /waiting for a code/)

  const unwired = await fetch(`${app.url}/api/providers/codex/login`, { method: 'POST' })
  assert.equal(unwired.status, 404)
  assert.match((await unwired.json()).error, /no sign-in to renew/)
})

// GitHub, the first integration: seven routes in the allowlist, each the
// broker's route of the same name, under the broker's token — and not the
// eighth, the token route, which is the host's git credential helper's.
test('the GitHub routes are wired, and the token route is not', async (t) => {
  const reply = (body, status = 200) => (req, res) => {
    res.writeHead(status, { 'content-type': 'application/json' })
    res.end(JSON.stringify(body))
  }
  const broker = await fakeBroker({
    'GET /v1/integrations/github': reply({ status: 'unregistered' }),
    'POST /v1/integrations/github/manifest': reply({ manifest: '{}', post_url: 'https://github.com/settings/apps/new', state: 's' }),
    'POST /v1/integrations/github/register': reply({ error: 'That state is not one this broker issued.' }, 400),
    'POST /v1/integrations/github/install': reply({ error: 'Register the app first.' }, 409),
    'POST /v1/integrations/github/callback': reply({ status: 'authorized' }),
    'POST /v1/integrations/github/refresh': reply({ error: 'GitHub says: bad_refresh_token' }, 502),
    'DELETE /v1/integrations/github': reply({ forgotten: true }),
    'POST /v1/integrations/github/token': reply({ token: 'never-through-here' }),
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))

  const attempts = [
    ['GET', '/api/integrations/github', undefined],
    ['POST', '/api/integrations/github/manifest', {}],
    ['POST', '/api/integrations/github/register', { code: 'c', state: 'bad' }],
    ['POST', '/api/integrations/github/install', {}],
    ['POST', '/api/integrations/github/callback', { code: 'c', installation_id: 99, setup_action: 'install', state: 's' }],
    ['POST', '/api/integrations/github/refresh', {}],
    ['DELETE', '/api/integrations/github', undefined],
    ['POST', '/api/integrations/github/token', {}],
  ]
  const answers = []
  for (const [method, path, body] of attempts) {
    const res = await fetch(`${app.url}${path}`, {
      method,
      headers: body === undefined ? {} : { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    answers.push([res.status, await res.json()])
  }
  assert.deepEqual(answers.map(([status]) => status), [200, 200, 400, 409, 200, 502, 200, 404])
  // The refusals are the broker's sentences, as they came.
  assert.equal(answers[2][1].error, 'That state is not one this broker issued.')
  assert.equal(answers[5][1].error, 'GitHub says: bad_refresh_token')
  const callback = broker.seen.find((c) => c.path === '/v1/integrations/github/callback')
  assert.deepEqual(JSON.parse(callback.body), { code: 'c', installation_id: 99, setup_action: 'install', state: 's' })
  assert.equal(callback.auth, `Bearer ${TOKEN}`)
  assert.equal(broker.seen.some((c) => c.path === '/v1/integrations/github/token'), false, 'the token route reached the broker')
})

test('plugin integration routes are absent from the core relay', async t => {
  const broker = await fakeBroker(), app = await appAgainst(broker)
  t.after(closing(broker, app))
  for (const [method, suffix] of [['GET', ''], ['PUT', ''], ['DELETE', ''], ['POST', '/refresh'], ['POST', '/login']]) {
    const res = await fetch(`${app.url}/api/integrations/acme${suffix}`, { method })
    assert.equal(res.status, 404)
  }
  assert.equal(broker.seen.some(request => request.path.includes('acme')), false)
})

test('the two paths GitHub sends the browser back to are the page, and the page may post a form to GitHub only', async (t) => {
  const broker = await fakeBroker()
  const app = await appAgainst(broker)
  t.after(closing(broker, app))
  const index = await (await fetch(`${app.url}/`)).text()
  for (const path of ['/integrations/github/registered?code=c&state=s', '/integrations/github/installed?code=c&installation_id=1&setup_action=install&state=s']) {
    const res = await fetch(`${app.url}${path}`)
    assert.equal(res.status, 200, path)
    assert.match(res.headers.get('content-type'), /^text\/html/)
    assert.equal(await res.text(), index)
    assert.match(res.headers.get('content-security-policy'), /form-action https:\/\/github\.com(;|$)/)
  }
  assert.equal((await fetch(`${app.url}/integrations/github/other`)).status, 404)
})
