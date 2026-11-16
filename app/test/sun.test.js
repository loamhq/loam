import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'
import '../public/sun.js'
import { createTheme } from '../public/theme.js'
import { appearanceValues } from '../src/appearance.js'
import { installDom } from './fakedom.js'

const sun = globalThis.LoamSun
const place = { latitude: 37.8, longitude: -122.4, source: 'geolocation', timeZone: 'America/Los_Angeles' }
const held = () => ({ phone: { mode: 'system', size: 100 }, desktop: { mode: 'sun', size: 100 }, location: place })
function page() {
  const document = installDom()
  document.documentElement = document.createElement('html'); document.documentElement.dataset = {}
  document.querySelector = () => null
  return document
}
const near = (actual, expected) => assert.ok(Math.abs(actual - Date.parse(expected)) < 90000, `${new Date(actual).toISOString()} vs ${expected}`)

test('solar times match independent calendar references within 90 seconds', () => {
  // References and retrieval date are recorded in docs/evidence/sun-mode/README.md.
  const sf = sun.solarDay(new Date('2026-11-13'), 37.8, -122.4)
  near(sf.sunrise, '2026-11-13T06:48:00-08:00'); near(sf.sunset, '2026-11-13T16:59:00-08:00')
  const london = sun.solarDay(new Date('2026-03-20'), 51.5, -.1)
  near(london.sunrise, '2026-03-20T06:03:00Z'); near(london.sunset, '2026-03-20T18:13:00Z')
  const sydney = sun.solarDay(new Date('2026-06-21'), -33.9, 151.2)
  near(sydney.sunrise, '2026-06-21T06:59:00+10:00'); near(sydney.sunset, '2026-06-21T16:53:00+10:00')
})

test('light includes sunrise minus 30 minutes and ends at sunset plus 30 minutes', () => {
  const { start, end } = sun.state(Date.parse('2026-11-13T12:00:00-08:00'), place)
  assert.equal(sun.state(start - 1, place).mode, 'dark')
  assert.equal(sun.state(start, place).mode, 'light')
  assert.equal(sun.state(end - 1, place).mode, 'light')
  assert.equal(sun.state(end, place).mode, 'dark')
  assert.equal(sun.state(start - 1, place).next, start)
  assert.equal(sun.state(start, place).next, end)
})

test('polar day and night in both hemispheres are finite and rechecked', () => {
  for (const latitude of [78.2, 90, -78.2, -90]) for (const month of ['06', '12']) {
    const now = Date.parse(`2026-${month}-21T12:00:00Z`)
    const value = sun.state(now, { ...place, latitude, longitude: 15.6, timeZone: 'UTC' })
    const isDay = (latitude > 0) === (month === '06')
    assert.equal(value.mode, isDay ? 'light' : 'dark'); assert.equal(value.polar, isDay ? 'day' : 'night')
    assert.equal(value.start, null); assert.equal(value.end, null)
    assert.ok(Number.isFinite(value.next) && value.next > now)
  }
})

test('saved-place dates survive DST, opposite device zones and the date line', () => {
  for (const timeZone of ['Pacific/Apia', 'Pacific/Kiritimati', 'Pacific/Auckland', 'Asia/Tokyo', 'America/Los_Angeles']) {
    const location = sun.fallback(timeZone), now = Date.parse('2026-11-13T20:00:00Z'), value = sun.state(now, location)
    const date = ms => new Intl.DateTimeFormat('en', { timeZone, dateStyle: 'short' }).format(new Date(ms))
    assert.equal(date(value.sunrise), date(now)); assert.equal(date(value.sunset), date(now))
  }
  for (const date of ['2026-03-08T12:00:00-07:00', '2026-11-01T12:00:00-08:00', '2028-02-29T12:00:00-08:00']) {
    const value = sun.state(Date.parse(date), place)
    assert.equal(value.mode, 'light'); assert.ok(value.end > value.start)
  }
})

test('location normalization rounds and strips fields; fallback handles aliases and unknown zones', () => {
  assert.deepEqual(sun.location({ ...place, latitude: 37.7749, longitude: -122.4194, secret: 'discard' }), place)
  for (const invalid of [{ ...place, latitude: 91 }, { ...place, longitude: Infinity }, { ...place, timeZone: 'bad' }, { ...place, source: 'gps' }]) assert.equal(sun.location(invalid), null)
  assert.equal(sun.fallback('US/Pacific').latitude, 37.8)
  assert.equal(sun.fallback('Asia/Colombo').source, 'timezone')
  assert.equal(sun.fallback('invalid').timeZone, 'UTC')
  assert.deepEqual(appearanceValues({ ...held(), location: { ...place, secret: 'discard' } }).location, place)
})

test('first choice asks once, rounds GPS, saves shared location and leaves phone mode alone', async () => {
  page(); let asked = 0; const host = held(); host.desktop.mode = 'system'
  const calls = [], theme = createTheme(null, { kind: 'desktop', boot: { values: { ...host, location: undefined }, available: true }, locate: async () => { asked++; return { coords: { latitude: 37.7749, longitude: -122.4194 } } }, request: async (method, body) => {
    calls.push(body); if (body.desktop) Object.assign(host.desktop, body.desktop); if (body.location) host.location = body.location
    return structuredClone(host)
  } })
  theme.set({ mode: 'sun' }); await new Promise(resolve => setImmediate(resolve)); await theme.save()
  assert.equal(asked, 1); assert.equal(theme.value.location.latitude, 37.8); assert.equal(theme.value.location.longitude, -122.4)
  assert.deepEqual(calls[0], { desktop: { mode: 'sun' }, location: theme.value.location })
  assert.equal(host.phone.mode, 'system')
  theme.set({ mode: 'dark' }); theme.set({ mode: 'sun' }); await theme.save(); assert.equal(asked, 1)
  page(); const phone = createTheme(null, { kind: 'phone', boot: { values: host, available: true }, locate: () => { throw new Error('must not ask') } })
  assert.deepEqual(phone.value.location, theme.value.location)
})

test('unsupported host retains Sun locally, avoids retry loops, and migrates on next load', async () => {
  page(); const storage = new Map()
  globalThis.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) }
  try {
    let writes = 0, asks = 0
    const host = held(); host.desktop.mode = 'system'; delete host.location
    const theme = createTheme(null, { kind: 'desktop', boot: { values: host, available: true }, locate: async () => { asks++; throw new Error('denied') }, request: async method => {
      if (method === 'PATCH') { writes++; throw new Error('mode not supported') }
      return host
    } })
    theme.set({ mode: 'sun' }); await new Promise(resolve => setImmediate(resolve)); await theme.save()
    assert.equal(theme.status.error, null); assert.equal(theme.status.localOnly, true); assert.equal(theme.status.saving, false)
    assert.equal(theme.value.location.source, 'timezone')
    for (let i = 0; i < 3; i++) await theme.refresh()
    theme.set({ mode: 'sun' }); await theme.save(); assert.equal(writes, 1); assert.equal(asks, 1)
    const local = sun.local('desktop'); assert.equal(local.mode, 'sun'); assert.ok(local.location)
    assert.equal(sun.local('phone').mode, undefined); assert.deepEqual(sun.local('phone').location, local.location)
    page()
    const fresh = createTheme(null, { kind: 'desktop', boot: { values: host, available: true }, request: async (method, body) => {
      Object.assign(host.desktop, body.desktop); host.location = body.location; return host
    } })
    assert.equal(fresh.value.mode, 'sun'); await fresh.save()
    assert.deepEqual(sun.local('desktop'), {}); assert.equal(fresh.status.error, null)
  } finally { delete globalThis.localStorage }
})

test('controller rechecks immediately after sleep even when the host read fails', async () => {
  page(); let now = Date.parse('2026-11-13T12:00:00-08:00')
  const theme = createTheme(null, { kind: 'desktop', boot: { values: held(), available: true }, now: () => now, request: async () => { throw new Error('offline') } })
  assert.equal(theme.apply(), 'light')
  now = Date.parse('2026-11-13T23:00:00-08:00'); await theme.refresh(); assert.equal(theme.current, 'dark')
})

test('a silent old host is detected, and unrelated fields still save after a Sun refusal', async () => {
  page(); let calls = 0; const host = held(); host.desktop.mode = 'system'
  const theme = createTheme(null, { kind: 'desktop', boot: { values: host, available: true }, request: async (method, body) => {
    calls++
    if (body.desktop?.mode === 'sun') throw new Error('unsupported mode')
    Object.assign(host.desktop, body.desktop); return host
  } })
  theme.set({ mode: 'sun', size: 95 }); await theme.save()
  assert.equal(calls, 2); assert.equal(host.desktop.size, 95); assert.equal(host.desktop.mode, 'system')
  assert.equal(theme.value.mode, 'sun'); assert.equal(theme.status.localOnly, true); assert.equal(theme.status.error, null)
  page()
  const silent = createTheme(null, { kind: 'desktop', boot: { values: held(), available: true }, request: async () => ({ desktop: { mode: 'system', size: 100 } }) })
  silent.set({ mode: 'sun' }); await silent.save()
  assert.equal(silent.status.error, null); assert.equal(silent.status.localOnly, true)
})

test('late Sun refusal cannot restore Sun over a newer choice', async () => {
  page(); let fail, calls = 0
  const host = held(); host.desktop.mode = 'system'
  const theme = createTheme(null, { kind: 'desktop', boot: { values: host, available: true }, request: async (method, body) => {
    calls++
    if (body.desktop.mode === 'sun') await new Promise((resolve, reject) => { fail = reject })
    Object.assign(host.desktop, body.desktop); return host
  } })
  theme.set({ mode: 'sun' }); const saving = theme.save()
  theme.set({ mode: 'light' }); fail(new Error('unsupported')); await saving
  assert.equal(theme.value.mode, 'light'); assert.equal(host.desktop.mode, 'light')
  assert.equal(theme.status.localOnly, false); assert.equal(theme.status.error, null); assert.equal(calls, 2)
})

test('boot resolves Sun before paint from host or pending local settings, without geolocation', async () => {
  const source = await readFile(new URL('../public/sun.js', import.meta.url), 'utf8')
  const boot = await readFile(new URL('../public/theme-boot.js', import.meta.url), 'utf8')
  for (const coarse of [true, false]) for (const local of [true, false]) for (const hour of [4, 12, 23]) {
    const root = { dataset: {}, style: { setProperty() {} } }
    const values = held(); values.phone.mode = 'sun'
    const stored = { phone: { mode: 'sun' }, desktop: { mode: 'sun' }, location: place }
    if (local) { values.phone.mode = 'system'; values.desktop.mode = 'system'; delete values.location }
    const context = vm.createContext({ Intl, Date: class extends Date { static now() { return Date.parse(`2026-11-13T${String(hour).padStart(2, '0')}:00:00-08:00`) } }, __loamAppearance: { values }, document: { documentElement: root }, matchMedia: q => ({ matches: q.includes('coarse') ? coarse : hour === 12 }), localStorage: { getItem: () => local ? JSON.stringify(stored) : null } })
    vm.runInContext(source, context); vm.runInContext(boot, context)
    assert.equal(root.dataset.theme, hour === 12 ? 'light' : 'dark')
    assert.equal(root.dataset.deviceKind, coarse ? 'phone' : 'desktop')
  }
})
