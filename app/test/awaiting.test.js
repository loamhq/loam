// The line where the reply will be: what it says while a first reply is slow.
//
// The owner, 2026-10-01: "the waiting for response text change way too quickly
// while we wait for the first response from the model." `awaiting.js` is given
// how long the line has stood rather than reading a clock, so this moves a fake
// one by hand and reads the line at each moment, with no waiting.

import test from 'node:test'
import assert from 'node:assert/strict'

import { awaitingPhrase, AWAIT_FIRST_MS, AWAIT_EVERY_MS, AWAIT_FADE_MS } from '../public/awaiting.js'

// A clock that starts when the line does and moves only when told.
function clock() {
  const started = 1_000_000
  let now = started
  return { at: (ms) => (now = started + ms), elapsed: () => now - started }
}

test('the awaiting line holds "Thinking…" for its first stretch, then moves on slowly', (t) => {
  const c = clock()
  const read = (ms) => {
    c.at(ms)
    return `${awaitingPhrase(c.elapsed()).phrase}…`
  }
  const readings = { '0s': read(0), '5s': read(5000), '20s': read(20000) }
  t.diagnostic(`the awaiting line at 0 s, 5 s and 20 s: ${JSON.stringify(readings)}`)
  assert.deepEqual(readings, { '0s': 'Thinking…', '5s': 'Thinking…', '20s': 'Digging in…' })

  // About nine seconds a phrase after the first, never three.
  assert.ok(AWAIT_FIRST_MS >= 9000 && AWAIT_EVERY_MS >= 8000 && AWAIT_EVERY_MS <= 10000)
  assert.equal(read(AWAIT_FIRST_MS + AWAIT_EVERY_MS - 1), 'Digging in…')
  assert.equal(read(AWAIT_FIRST_MS + AWAIT_EVERY_MS), 'Turning it over…')
})

test('the words fade out just before the phrase turns over, and the next draw is armed for each', () => {
  // Standing, then going out, then the new phrase in.
  assert.deepEqual(awaitingPhrase(0), { phrase: 'Thinking', out: false, nextMs: AWAIT_FIRST_MS - AWAIT_FADE_MS })
  assert.deepEqual(awaitingPhrase(AWAIT_FIRST_MS - AWAIT_FADE_MS), { phrase: 'Thinking', out: true, nextMs: AWAIT_FADE_MS })
  assert.deepEqual(awaitingPhrase(AWAIT_FIRST_MS), { phrase: 'Digging in', out: false, nextMs: AWAIT_EVERY_MS - AWAIT_FADE_MS })
  // And the phrases go round rather than running out on a long wait.
  assert.equal(awaitingPhrase(AWAIT_FIRST_MS + 5 * AWAIT_EVERY_MS).phrase, 'Thinking')
})
