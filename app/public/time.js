// One display clock: browser locale/hour-cycle preference and installation zone.
// Tests may supply locale/zone; Sun labels use the saved place's zone.
const formats = new Map()
function formatter(options, { locale, timeZone = globalThis.LOAM_INSTALLATION?.timeZone } = {}) {
  const key = JSON.stringify([locale, timeZone, options])
  if (!formats.has(key)) formats.set(key, new Intl.DateTimeFormat(locale, { ...options, ...(timeZone ? { timeZone } : {}) }))
  return formats.get(key)
}
function instant(value) {
  if (value === null || value === undefined || value === '') return null
  const date = new Date(value)
  return Number.isFinite(+date) ? date : null
}
export function formatDateTime(value, options = { dateStyle: 'medium', timeStyle: 'short' }, context) {
  const date = instant(value)
  return date ? formatter(options, context).format(date) : ''
}
export function formatTime(value, context) {
  return formatDateTime(value, { timeStyle: 'short' }, context)
}
export function browserTimeZone() { return Intl.DateTimeFormat().resolvedOptions().timeZone }
export function formatDate(value, options = { day: 'numeric', month: 'short', year: 'numeric' }, context) {
  return formatDateTime(value, options, context)
}
// Board deadlines and date-only expiries name calendar days, not instants.
export function formatCalendarDate(value, options, context = {}) {
  return formatDate(value, options, /^\d{4}-\d{2}-\d{2}$/.test(value) ? { ...context, timeZone: 'UTC' } : context)
}
export function calendarDay(value, context) {
  const date = instant(value)
  if (!date) return null
  const parts = formatter({ year: 'numeric', month: 'numeric', day: 'numeric', calendar: 'gregory', numberingSystem: 'latn' }, context).formatToParts(date)
  const part = key => Number(parts.find(item => item.type === key).value)
  return { number: Date.UTC(part('year'), part('month') - 1, part('day')) / 86400000, year: part('year') }
}

// Numeric calendar parts for filenames. No configured installation in a unit
// consumer keeps the native clock; the served page always has its zone.
export function noteDateParts(value) {
  const date = instant(value)
  const parts = formatter({ year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', calendar: 'gregory', numberingSystem: 'latn' }, { locale: 'en' }).formatToParts(date)
  return Object.fromEntries(parts.filter(part => part.type !== 'literal').map(part => [part.type, part.value]))
}
