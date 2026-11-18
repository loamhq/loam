import test from 'node:test'
import assert from 'node:assert/strict'
import { authFailure, failureSentence } from '../public/auth.js'

test('only portal refusals and auth redirects end the page session', () => {
  assert.equal(authFailure(new Response('', { status: 401 })), true)
  assert.equal(authFailure(new Response('', { status: 303 })), true)
  assert.equal(authFailure({ type: 'opaqueredirect' }), true)
  assert.equal(authFailure({ redirected: true, headers: new Headers({ 'content-type': 'text/html' }) }), true)
  assert.equal(authFailure(new Response('{}', { status: 429 })), false)
  assert.equal(authFailure(new Response('{}', { status: 503 })), false)
})

test('an expired portal latches: queued background calls send no more requests', async (t) => {
  const auth = await import('../public/auth.js?expired-test')
  auth.timing.confirmSchedule = [0, 0, 0]
  const original = globalThis.fetch
  t.after(() => { globalThis.fetch = original })
  const calls = []
  globalThis.fetch = async (input, options) => {
    calls.push(String(input))
    assert.equal(options.redirect, 'manual', 'the login page must not be fetched as API JSON')
    return new Response('', { status: 401 })
  }
  await assert.rejects(auth.authenticatedFetch('/api/speech/lookup'), { status: 401 })
  assert.equal(auth.authExpired(), true)
  for (const url of ['/api/speech/lookup', '/api/threads', '/api/sessions/events']) {
    await assert.rejects(auth.authenticatedFetch(url), { status: 401 })
  }
  assert.deepEqual(calls, ['/api/speech/lookup', auth.PROBE, auth.PROBE, auth.PROBE], 'one request, the probes that confirm it, then nothing')
})

// The 0.2.0 walk: adding a passkey renews the login's session, and the one poll
// in flight at that moment came back anonymous; the page said "Your session has
// ended" although a reload opened the app signed in.
test('one anonymous answer while the session is fine does not end the page', async (t) => {
  const auth = await import('../public/auth.js?renewed-test')
  auth.timing.confirmSchedule = [0, 0, 0]
  const original = globalThis.fetch
  t.after(() => { globalThis.fetch = original })
  const calls = []
  let renewing = 2   // the two requests that left with the old cookie
  globalThis.fetch = async (input) => {
    calls.push(String(input))
    if (String(input) !== auth.PROBE && renewing > 0) { renewing--; return { type: 'opaqueredirect', status: 0, headers: new Headers() } }
    return new Response('{"code":"123456"}', { status: 200, headers: { 'content-type': 'application/json' } })
  }
  const [a, b] = await Promise.all([auth.authenticatedFetch('/_loam/login-code'), auth.authenticatedFetch('/api/threads')])
  assert.equal(auth.authExpired(), false)
  assert.equal(a.status, 200)
  assert.equal(b.status, 200)
  assert.equal(calls.filter(c => c === auth.PROBE).length, 1, 'requests refused at one moment share one probe')
  assert.deepEqual(calls.filter(c => c !== auth.PROBE).sort(), ['/_loam/login-code', '/_loam/login-code', '/api/threads', '/api/threads'])
})

test('a refusal the probe calls fine but that is refused again does end the page', async (t) => {
  const auth = await import('../public/auth.js?app-refusal-test')
  auth.timing.confirmSchedule = [0, 0, 0]
  const original = globalThis.fetch
  t.after(() => { globalThis.fetch = original })
  globalThis.fetch = async (input) => new Response('', { status: String(input) === auth.PROBE ? 200 : 401 })
  await assert.rejects(auth.authenticatedFetch('/api/health'), { status: 401 })
  assert.equal(auth.authExpired(), true)
})

test('route-bearing failures read as a sentence in classic and thread views', () => {
  assert.equal(failureSentence('End one with DELETE /v1/sessions/id'), 'Couldn’t start — the box is busy; try again in a moment.')
  assert.equal(failureSentence('Capacity 429 at /v1/sessions'), 'Couldn’t start — the box is busy; try again in a moment.')
  assert.equal(failureSentence('Please try again shortly.'), 'Please try again shortly.')
})

// Measured on a fresh install: the session renewal leaves a gap of about 0.9 s
// in which every request is anonymous, longer than one probe's wait.
test('a renewal gap longer than the first probe is still ridden out', async (t) => {
  const auth = await import('../public/auth.js?gap-test')
  auth.timing.confirmSchedule = [0, 0, 0, 0]
  const original = globalThis.fetch
  t.after(() => { globalThis.fetch = original })
  let anonymous = 3   // the request and the first two probes fall in the gap
  globalThis.fetch = async () => anonymous-- > 0 ? new Response('', { status: 401 }) : new Response('{}', { status: 200 })
  const r = await auth.authenticatedFetch('/_loam/login-code')
  assert.equal(r.status, 200)
  assert.equal(auth.authExpired(), false)
})
