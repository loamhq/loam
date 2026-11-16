import test from 'node:test'
import assert from 'node:assert/strict'
import { replyChip, replyTimestamp } from '../public/thread-experience.js'
import { hasMark, providerMark } from '../public/marks.js'
import { providerGlyph } from '../public/sessions.js'
import { installDom } from './fakedom.js'

const providers = [
  { id: 'claude-code', name: 'Claude Code', models: [{ id: 'fable', name: 'Fable 5.1' }, { id: 'opus', name: 'Claude Opus 5.5' }] },
  { id: 'codex', name: 'Codex', models: [{ id: 'sol', name: 'GPT-6.1-Sol' }] },
  { id: 'antigravity', name: 'Antigravity', models: [{ id: 'preview', name: 'Gemini 3.0 Pro Preview' }] },
]
test('chip indicators use catalog family/generation, keeping full names for the tooltip', () => {
  for (const [engine, model, short, full] of [
    ['claude-code', 'fable', 'Fable 5.1', 'Fable 5.1'],
    ['claude-code', 'opus', 'Opus 5.5', 'Claude Opus 5.5'],
    ['codex', 'sol', 'GPT-6.1-Sol', 'GPT-6.1-Sol'],
    ['antigravity', 'preview', 'Gemini 3.0', 'Gemini 3.0 Pro Preview'],
  ]) {
    const chip = replyChip({ engine, model }, providers)
    assert.equal(chip.model, short); assert.equal(chip.fullModel, full)
    assert.equal(hasMark(engine), true)
  }
})
test('old and unknown reply metadata keep honest fallbacks, including a provider with no mark', () => {
  assert.deepEqual(replyChip({ engine: 'codex' }, providers), { provider: 'Codex', model: null, fullModel: null })
  assert.equal(replyChip({ engine: 'codex', model: 'raw-unknown-6-1' }, providers).model, 'raw-unknown-6-1', 'never shorten an unlisted ID')
  assert.equal(replyChip({ engine: 'codex', model: 'sol' }).model, 'sol')
  const chip = replyChip({ engine: 'future', model: 'unlisted' }, [{ id: 'future', name: 'Future Provider' }])
  assert.equal(chip.provider, 'Future Provider'); assert.equal(hasMark('future'), false)
  installDom()
  const mark = providerMark('future', 12, providerGlyph('future'))
  assert.equal(mark.className, 'mark-letter'); assert.equal(mark.textContent, 'F')
  assert.deepEqual(replyChip({}), { provider: null, model: null, fullModel: null })
})

const stamp = (at, now = '2026-11-10T21:47:00Z', timeZone = 'America/Los_Angeles') => replyTimestamp(at, { now, timeZone })
test('reply timestamps use time today, weekday in the prior six calendar days, and a short date after that', () => {
  assert.equal(stamp('2026-11-10T21:47:00Z').text, '1:47 PM')
  assert.equal(stamp('2026-11-09T17:25:00Z').text, 'Mon 9:25 AM')
  assert.equal(stamp('2026-11-04T17:25:00Z').text, 'Wed 9:25 AM')
  assert.equal(stamp('2026-11-03T17:25:00Z').text, 'Nov 3, 9:25 AM')
  assert.equal(stamp('2025-11-03T17:25:00Z').text, 'Nov 3, 2025, 9:25 AM')
  const reading = stamp('2026-11-10T21:47:00Z')
  assert.match(reading.title, /Tuesday, November 10, 2026/)
  assert.match(reading.title, /1:47:00 PM/)
  assert.equal(reading.dateTime, '2026-11-10T21:47:00.000Z')
})
test('timestamp ranges follow the reader calendar across midnight, time zones and DST', () => {
  assert.equal(stamp('2026-11-10T07:59:00Z', '2026-11-10T08:01:00Z').text, 'Mon 11:59 PM')
  assert.equal(stamp('2026-11-10T07:59:00Z', '2026-11-10T08:01:00Z', 'UTC').text, '7:59 AM')
  // The autumn clock change makes the prior Sunday more than 24 h away,
  // while it is still just the previous calendar day.
  assert.equal(stamp('2026-11-01T07:30:00Z', '2026-11-02T08:05:00Z').text, 'Sun 12:30 AM')
  assert.equal(stamp('2026-11-11T17:25:00Z').text, 'Nov 11, 9:25 AM', 'future dates are not labelled as a past weekday')
})
test('absent, invalid and optimistic timestamps are not replaced with the current time', () => {
  for (const value of [undefined, null, '', 'not a date']) assert.equal(stamp(value), null)
  assert.equal(replyTimestamp('2026-11-10T21:47:00Z', { now: NaN }), null)
})
