import test from 'node:test'
import assert from 'node:assert/strict'
import { formatTime, formatDateTime, formatCalendarDate, calendarDay } from '../public/time.js'
import { replyTimestamp } from '../public/thread-experience.js'
import { readdir, readFile } from 'node:fs/promises'

test('one clock follows locale hour cycle and zone, including the DST boundary', () => {
  const value = '2026-11-01T09:30:00Z'
  for (const [locale, timeZone, expected] of [
    ['en-US', 'America/Los_Angeles', '1:30 AM'],
    ['en-GB', 'America/Los_Angeles', '01:30'],
    ['en-US', 'Asia/Tokyo', '6:30 PM'],
    ['en-GB', 'Asia/Tokyo', '18:30'],
    ['en-US-u-hc-h23', 'Asia/Tokyo', '18:30'],
  ]) {
    const context = { locale, timeZone }
    assert.equal(formatTime(value, context), expected)
    assert.equal(replyTimestamp(value, { ...context, now: +new Date(value) }).text, expected)
    assert.equal(formatDateTime(value, undefined, context), new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone }).format(new Date(value)))
  }
})
test('calendar days remain days, invalid timestamps stay absent, and non-Latin locales can bucket days', () => {
  assert.equal(formatCalendarDate('2026-11-12', undefined, { locale: 'en-US', timeZone: 'America/Los_Angeles' }), 'Nov 12, 2026')
  assert.equal(formatCalendarDate('2026-11-12T01:00:00Z', undefined, { locale: 'en-US', timeZone: 'America/Los_Angeles' }), 'Nov 11, 2026')
  for (const value of [null, undefined, '', 'not a date']) assert.equal(formatTime(value), '')
  assert.deepEqual(calendarDay('2026-11-12T01:00:00Z', { locale: 'ar-EG', timeZone: 'Asia/Tokyo' }), calendarDay('2026-11-12T01:00:00Z', { locale: 'en-US', timeZone: 'Asia/Tokyo' }))
})
test('display formatters live at one seam; other modules do not force a locale, hour cycle or zone', async () => {
  for (const name of await readdir('public')) {
    if (!name.endsWith('.js') || name === 'time.js') continue
    const source = await readFile(`public/${name}`, 'utf8')
    if (name === 'sun.js') {
      // Parser-blocking solar arithmetic needs fixed Gregorian numeric parts
      // and a zone offset before modules load; it renders no display clock.
      assert.doesNotMatch(source, /\.format\(|\.toLocale(?:Date|Time)?String\(/)
      continue
    }
    assert.doesNotMatch(source, /Intl\.DateTimeFormat|\.toLocale(?:Date|Time)?String\(|hour12\s*:|hourCycle\s*:/, name)
  }
})
