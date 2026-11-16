// The providers' marks, and the arc the ring draws.
//
// **Fixture-only, and plainly so**: there is no browser on the box this project
// is built on, so nothing here proves a mark *looks* like its vendor's. What it
// proves is the two things a page can get wrong without anybody noticing until
// a screenshot: that the arc's length is the percentage and nothing else, and
// that a provider with no mark falls back to the letter it drew before rather
// than to an empty tile.

import test from 'node:test'
import assert from 'node:assert/strict'

import { installDom } from './fakedom.js'
import { providerMark, usageRing, hasMark } from '../public/marks.js'
import { quotaRing } from '../public/providers.js'

installDom()

const ring = (usage, opts = {}) => usageRing('claude-code', { ...quotaRing(usage), size: 32, ...opts })
const kid = (node, cls) => node.querySelectorAll(`.${cls}`)[0] ?? null
const at = (five, seven) => ({ five_hour: { utilization: five }, seven_day: { utilization: seven } })

test('the arc is the percentage, because the circle is drawn to a circumference of 100', () => {
  // Nothing converts anything between the quota and the ring: `69 100` is 69%
  // of the way round, which is the whole reason for the radius in marks.js.
  assert.equal(kid(ring(at(0, 0)), 'arc').getAttribute('stroke-dasharray'), '0 100')
  assert.equal(kid(ring(at(0.69, 0.1)), 'arc').getAttribute('stroke-dasharray'), '69 100')
  assert.equal(kid(ring(at(0.7, 0.1)), 'arc').getAttribute('stroke-dasharray'), '70 100')
  assert.equal(kid(ring(at(0.9, 0.1)), 'arc').getAttribute('stroke-dasharray'), '90 100')
  assert.equal(kid(ring(at(0.255, 0.1)), 'arc').getAttribute('stroke-dasharray'), '25.5 100')
  // From the top, clockwise, or it is not a gauge.
  assert.equal(kid(ring(at(0.5, 0)), 'arc').getAttribute('transform'), 'rotate(-90 18 18)')
})

test('the colour is a class on the ring, and the three bands are the design’s', () => {
  assert.equal(ring(at(0, 0)).getAttribute('class'), 'qring qring-calm')
  assert.equal(ring(at(0.69, 0)).getAttribute('class'), 'qring qring-calm')
  assert.equal(ring(at(0.7, 0)).getAttribute('class'), 'qring qring-warn')
  assert.equal(ring(at(0.89, 0)).getAttribute('class'), 'qring qring-warn')
  assert.equal(ring(at(0.9, 0)).getAttribute('class'), 'qring qring-high')
  assert.equal(ring(at(1, 0)).getAttribute('class'), 'qring qring-high')
})

test('no reading is the mark alone, in the same box as a ring with a reading', () => {
  const empty = ring(null)
  const read = ring(at(0.4, 0.1))
  // Not `empty`: that word is the page's empty-state block, 36px by 30px of
  // padding, and it swelled the ring past the buttons beside it on every chat
  // with no reading (2026-10-01, the first Codex chats).
  assert.equal(empty.getAttribute('class'), 'qring qring-empty')
  assert.doesNotMatch(empty.getAttribute('class'), /(^| )empty( |$)/)
  // The box: the size asked for, with or without a reading, and nothing else.
  assert.deepEqual(empty.__css, { width: '32px', height: '32px' })
  assert.deepEqual(read.__css, empty.__css, 'a ring with no reading is not the size of one with a reading')
  assert.equal(kid(empty, 'mark').getAttribute('width'), kid(read, 'mark').getAttribute('width'))
  // No gauge at all: no arc, no track, no svg for either to be in.
  assert.equal(kid(empty, 'arc'), null, 'no reading was drawn as an arc of some length')
  assert.equal(kid(empty, 'track'), null, 'no reading drew a full grey track')
  assert.equal(kid(empty, 'qring-arc'), null)
  // And with a reading, both are there.
  assert.ok(kid(read, 'track'))
  assert.equal(kid(read, 'arc').getAttribute('stroke-dasharray'), '40 100')
  // Against 0%, which is an arc of no length — a different thing, said
  // differently.
  assert.ok(kid(ring(at(0, 0)), 'arc'), 'a ring at 0% drew no arc')
})

test('the mark inside the ring is the provider’s own, and a letter when it is not', () => {
  assert.equal(kid(ring(at(0.2, 0)), 'mark').getAttribute('data-mark'), 'claude-code')
  for (const id of ['claude-code', 'codex', 'antigravity', 'grok', 'opencode']) {
    assert.equal(hasMark(id), true, `${id} has no mark`)
    const mark = providerMark(id, 18)
    assert.equal(mark.getAttribute('data-mark'), id)
    assert.equal(mark.getAttribute('viewBox'), '0 0 24 24')
    assert.equal(mark.getAttribute('fill'), 'currentColor', 'a mark that names its own colour cannot take the tile’s')
    assert.match(mark.childNodes[0].getAttribute('d'), /^M/)
  }
  // A provider this app has never seen is not a gap to guess at: it is the
  // letter the page drew before there were marks.
  assert.equal(hasMark('something-new'), false)
  const letter = providerMark('something-new', 18, 'S')
  assert.equal(letter.getAttribute('data-mark'), null)
  assert.equal(letter.textContent, 'S')
  const unknown = usageRing('something-new', { percent: 40, level: 'calm', size: 28, fallback: 'S' })
  assert.equal(unknown.textContent, 'S', 'an unknown provider’s ring drew nothing in the middle')
})
