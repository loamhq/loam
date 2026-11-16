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
  const original = globalThis.fetch
  t.after(() => { globalThis.fetch = original })
  let calls = 0
  globalThis.fetch = async (input, options) => {
    calls++
    assert.equal(options.redirect, 'manual', 'the login page must not be fetched as API JSON')
    return new Response('', { status: 401 })
  }
  await assert.rejects(auth.authenticatedFetch('/api/speech/lookup'), { status: 401 })
  for (const url of ['/api/speech/lookup', '/api/threads', '/api/sessions/events']) {
    await assert.rejects(auth.authenticatedFetch(url), { status: 401 })
  }
  assert.equal(calls, 1)
})

test('route-bearing failures read as a sentence in classic and thread views', () => {
  assert.equal(failureSentence('End one with DELETE /v1/sessions/id'), 'Couldn’t start — the box is busy; try again in a moment.')
  assert.equal(failureSentence('Capacity 429 at /v1/sessions'), 'Couldn’t start — the box is busy; try again in a moment.')
  assert.equal(failureSentence('Please try again shortly.'), 'Please try again shortly.')
})
