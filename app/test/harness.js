// A broker that is only a recording, and this app in front of it. Shared by
// every test that needs the web half to actually answer: no agent, no token
// that means anything, no network beyond loopback.

import http from 'node:http'
import { once } from 'node:events'

import { createApp } from '../src/server.js'
import { readConfig, fixtureBroker } from './key-fixture.js'
import { createLog } from '../src/log.js'

export const TOKEN = 'test-token-not-a-real-one'

// A stand-in broker. It records what it was asked, so a test can assert that
// the token went out and that the page's call arrived as written.
export async function fakeBroker(handlers = {}) {
  const seen = []
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://placeholder')
    // The real broker reads a path segment as an id, so a test that uses an
    // awkward id compares the decoded path, not the escaped one.
    const pathname = decodeURIComponent(url.pathname)
    const body = await new Promise((resolve) => {
      const chunks = []
      req.on('data', (c) => chunks.push(c))
      req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    })
    seen.push({ method: req.method, path: pathname, search: url.search, auth: req.headers.authorization, body, at: Date.now() })

    if (pathname === '/up') {
      res.writeHead(200, { 'content-type': 'application/json' })
      return res.end(JSON.stringify({ ok: true, service: 'fake' }))
    }
    // Everything but the health check needs the token.
    if (req.headers.authorization !== `Bearer ${TOKEN}`) {
      res.writeHead(401, { 'content-type': 'application/json' })
      return res.end(JSON.stringify({ error: 'bearer token required' }))
    }
    const handler = handlers[`${req.method} ${pathname}`]
    if (!handler) {
      res.writeHead(404, { 'content-type': 'application/json' })
      return res.end(JSON.stringify({ error: 'no such route' }))
    }
    // The body is already read — it had to be, to be recorded — so a handler
    // that answers from what it was sent is handed it rather than asked to
    // drain a stream that has ended.
    await handler(req, res, url, body)
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  return { server, seen, url: `http://127.0.0.1:${server.address().port}` }
}

// The app's log lines, kept rather than printed: parsed, every level, so a
// test can count them and read their fields, and the test run stays quiet.
export function logInto(lines) {
  return createLog({ level: 'debug', write: (line) => lines.push(JSON.parse(line)) })
}

export async function appAgainst(broker, env = {}, overrides = {}) {
  const config = readConfig({ BROKER_URL: broker.url, BROKER_TOKEN: TOKEN, ...env })
  const lines = []
  // Fixtures never capture/fetch their synthetic catalog on the network.
  // Media integration tests explicitly install a real loopback-backed cache.
  const appMedia = { resolve: async app => ({ url: app.url, preview: null, icon: null, pending: false }), image: async () => null }
  const { server } = createApp({ config, broker: fixtureBroker(config), log: logInto(lines), appMedia, ...overrides })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  return { server, lines, config, url: `http://127.0.0.1:${server.address().port}` }
}

// Keep-alive sockets outlive the test that opened them, so they are cut
// rather than waited for.
export function shut(server) {
  return new Promise((resolve) => {
    server.closeAllConnections?.()
    server.close(resolve)
  })
}

export function closing(...servers) {
  return () => Promise.all(servers.map((s) => shut(s.server)))
}

// Wait for something a server does, rather than for a length of time.
// Eight seconds and not three, since 2026-09-22: the suite is twenty files run
// side by side and the box they run on is not always idle, so three seconds was
// a deadline a few of these waits lost under load while passing every time on
// their own. A longer deadline weakens nothing — a wait that is satisfied
// returns the moment it is — and a test that really hangs still fails.
export async function until(predicate, { timeout = 8000, every = 10 } = {}) {
  const deadline = Date.now() + timeout
  for (;;) {
    const answer = predicate()
    if (answer) return answer
    if (Date.now() > deadline) throw new Error('timed out waiting for it to happen')
    await new Promise((resolve) => setTimeout(resolve, every))
  }
}
