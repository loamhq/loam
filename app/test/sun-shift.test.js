import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import '../public/sun.js'
import { createTheme } from '../public/theme.js'
import {
  SUN_TRANSITIONS,
  transitionMs,
  parseColor,
  toOklch,
  fromOklch,
  mixOklch,
  formatColor,
  surfaceProgress,
  inkProgress,
  classifyTokens,
  createShift,
} from '../public/sun-shift.js'
import { loadCoreTokens } from '../src/themes.js'
import { installDom } from './fakedom.js'

const place = { latitude: 37.8, longitude: -122.4, source: 'geolocation', timeZone: 'America/Los_Angeles' }
const held = () => ({
  phone: { mode: 'system', transition: 'normal', size: 100 },
  desktop: { mode: 'sun', transition: 'normal', size: 100 },
  location: place,
})

function page({ hidden = false, visibilityState = 'visible' } = {}) {
  const document = installDom()
  document.documentElement = document.createElement('html')
  document.documentElement.dataset = {}
  document.hidden = hidden
  document.visibilityState = visibilityState
  const meta = { content: null, setAttribute(k, v) { if (k === 'content') this.content = v } }
  document.querySelector = (sel) => (sel === 'meta[name="theme-color"]' ? meta : null)
  return { document, meta }
}

function lin(v) { return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }
function lum([r, g, b]) { const f = (v) => lin(v / 255); return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b) }
function contrast(a, b) { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05) }

async function themeTokenMaps(themeId) {
  const defaults = await loadCoreTokens()
  const light = new Map(defaults.light), dark = new Map(defaults.dark)
  if (themeId !== 'plain') {
    const css = (await readFile(new URL(`../public/themes/${themeId}/theme.css`, import.meta.url), 'utf8')).replace(/\/\*[\s\S]*?\*\//g, '')
    for (const block of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const sel = block[1].trim()
      if (sel.includes('[data-option-')) continue
      const targets = sel.includes('[data-theme="dark"]') ? [dark] : sel.includes('[data-theme="light"]') ? [light] : [light, dark]
      for (const decl of block[2].matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
        for (const t of targets) t.set(decl[1], decl[2].trim())
      }
    }
  }
  const resolve = (map) => {
    const out = new Map()
    for (const [k] of map) {
      let v = map.get(k)
      for (let i = 0; i < 6 && v?.startsWith('var('); i++) v = map.get(v.slice(4, -1).trim())
      if (v) out.set(k, v)
    }
    return out
  }
  return { light: resolve(light), dark: resolve(dark) }
}

test('parseColor handles hex, rgb/rgba, hsl, oklab, oklch, color(srgb) and rejects non-colours', () => {
  assert.deepEqual(parseColor('#fff'), [1, 1, 1, 1])
  assert.deepEqual(parseColor('#1c1c1c').map((n) => Math.round(n * 255)), [28, 28, 28, 255])
  assert.deepEqual(parseColor('#00ff0080').slice(0, 3), [0, 1, 0])
  assert.ok(Math.abs(parseColor('#00ff0080')[3] - 128 / 255) < 1e-6)
  assert.deepEqual(parseColor('rgba(28, 28, 28, .14)'), [28 / 255, 28 / 255, 28 / 255, 0.14])
  assert.deepEqual(parseColor('rgb(28 28 28 / 50%)'), [28 / 255, 28 / 255, 28 / 255, 0.5])
  assert.deepEqual(parseColor('hsl(0, 100%, 50%)').map((n) => Math.round(n * 255)), [255, 0, 0, 255])
  assert.deepEqual(parseColor('color(srgb 0.5 0.25 0.75 / 0.8)'), [0.5, 0.25, 0.75, 0.8])
  assert.ok(parseColor('oklch(0.6 0.15 40)'))
  assert.ok(parseColor('oklab(0.6 0.05 0.02)'))
  for (const nonColor of ['', '160ms', 'Figtree, sans-serif', '0 3px 10px rgba(28,28,28,.22)', 'ease', '14px']) {
    assert.equal(parseColor(nonColor), null, nonColor)
  }
})

test('OKLCH roundtrip and grey hue borrowing produce smooth intermediate colours', () => {
  const grey = toOklch(parseColor('#e8e8e8'))
  const warm = toOklch(parseColor('#916844'))
  const mid = mixOklch(grey, warm, 0.5)
  assert.ok(mid[0] > 0 && mid[1] > 0 && mid[2] > 0)
  // Greys borrow the chromatic end's hue rather than shifting through green/blue.
  const midOklch = toOklch(mid.map((v, i) => (i < 3 ? v / 255 : v)))
  let dH = Math.abs(midOklch[2] - warm[2])
  if (dH > Math.PI) dH = 2 * Math.PI - dH
  assert.ok(dH < 0.2, `Expected grey to inherit warm hue, dH=${dH}`)
  const round = fromOklch(toOklch(parseColor('#B0684A')))
  assert.deepEqual(round.slice(0, 3), [176, 104, 74])
  assert.equal(formatColor([28, 28, 28, 1]), 'rgb(28,28,28)')
  assert.equal(formatColor([28, 28, 28, 0.14]), 'rgba(28,28,28,0.140)')
})

test('classifyTokens sorts surfaces vs inks and pairs inverted button inks across all themes', async () => {
  for (const id of ['plain', 'latte', 'autumn', 'deep-forest', 'foundry', 'mist-trail', 'yosemite']) {
    const { light, dark } = await themeTokenMaps(id)
    const { entries } = classifyTokens(light, dark)
    const byName = new Map(entries.map((e) => [e.name, e]))
    assert.equal(byName.get('--paper-page')?.ink, false, `${id}: --paper-page drifts`)
    assert.equal(byName.get('--paper-pane')?.ink, false, `${id}: --paper-pane drifts`)
    assert.equal(byName.get('--paper-ink')?.ink, true, `${id}: --paper-ink flips`)
    assert.equal(byName.get('--latte-coffee')?.ink, true, `${id}: --latte-coffee flips`)
    assert.equal(byName.get('--latte-espresso')?.ink, true, `${id}: --latte-espresso flips`)
    assert.equal(byName.get('--latte-espresso-ink')?.ink, true, `${id}: --latte-espresso-ink stays paired with --latte-espresso`)
    assert.equal(byName.get('--shadow-ink')?.ink, false, `${id}: --shadow-ink drifts`)
    assert.equal(byName.has('--persona-rust'), false, `${id}: unchanged token skipped`)
    assert.equal(byName.has('--shadow-float'), false, `${id}: non-colour shadow token skipped`)

    // Across all three durations and both directions, body text & primary button stay <3:1 well under 1 s.
    for (const dur of Object.values(SUN_TRANSITIONS)) {
      for (const toMode of ['dark', 'light']) {
        let bodyLow = 0, btnLow = 0
        const steps = 1000
        for (let i = 0; i <= steps; i++) {
          const p = i / steps
          const sP = surfaceProgress(p), iP = inkProgress(sP)
          const mixToken = (name) => {
            const item = byName.get(name)
            const [from, to] = toMode === 'dark' ? [item.l, item.d] : [item.d, item.l]
            return mixOklch(from, to, item.ink ? iP : sP)
          }
          if (contrast(mixToken('--paper-ink'), mixToken('--paper-page')) < 3) bodyLow++
          if (contrast(mixToken('--latte-espresso-ink'), mixToken('--latte-espresso')) < 3) btnLow++
        }
        const bodySec = (bodyLow / steps) * (dur / 1000)
        const btnSec = (btnLow / steps) * (dur / 1000)
        assert.ok(bodySec < 0.5, `${id} ${toMode} ${dur}ms body <3:1 for ${bodySec.toFixed(3)}s`)
        assert.ok(btnSec < 0.5, `${id} ${toMode} ${dur}ms button <3:1 for ${btnSec.toFixed(3)}s`)
      }
    }
  }
})

test('createShift updates surfaces then flips inks and data-theme at midpoint, leaving no inline overrides at end', () => {
  const { document, meta } = page()
  const root = document.documentElement
  root.dataset.theme = 'light'
  const palette = {
    light: { '--paper-page': '#EEEAE3', '--paper-ink': '#1C1B19', '--latte-espresso': '#916844', '--latte-espresso-ink': '#FFF8EE', '--font-body': 'Figtree' },
    dark: { '--paper-page': '#1A1917', '--paper-ink': '#ECE7DF', '--latte-espresso': '#E9D5BB', '--latte-espresso-ink': '#2A1C13', '--font-body': 'Figtree' },
  }
  const fakeCS = (el) => {
    const mode = el.dataset.theme === 'dark' ? palette.dark : palette.light
    const keys = Object.keys(mode)
    return {
      length: keys.length,
      [Symbol.iterator]: function* () { yield* keys },
      getPropertyValue: (k) => mode[k] || '',
      ...Object.fromEntries(keys.map((k, i) => [i, k])),
    }
  }
  let now = 1000
  const rafQueue = []
  let midpoints = 0, completes = 0
  const shift = createShift({
    root,
    getComputedStyle: fakeCS,
    raf: (fn) => { rafQueue.push(fn); return rafQueue.length },
    caf: () => { rafQueue.length = 0 },
    perfNow: () => now,
  })
  const started = shift.start({
    fromMode: 'light',
    toMode: 'dark',
    duration: 10000,
    look: 'latte',
    onMidpoint: () => midpoints++,
    onComplete: () => completes++,
  })
  assert.equal(started, true)
  assert.equal(root.dataset.theme, 'light')
  assert.equal(root.style.getPropertyValue('--font-body'), '')

  // At 2.5s (25%): surface shifted, ink still dark, data-theme still light.
  now = 3500
  rafQueue.shift()()
  assert.equal(root.dataset.theme, 'light')
  assert.equal(midpoints, 0)
  assert.equal(root.style.getPropertyValue('--paper-ink'), 'rgb(28,27,25)')
  assert.notEqual(root.style.getPropertyValue('--paper-page'), 'rgb(238,234,227)')

  // At 5.0s (50%): midpoint reached — data-theme & theme-color switch, callback fires.
  now = 6000
  rafQueue.shift()()
  assert.equal(root.dataset.theme, 'dark')
  assert.equal(meta.content, '#1A1917')
  assert.equal(midpoints, 1)

  // At 10.0s (100%): all inline overrides cleared, final state matches snap.
  now = 11000
  rafQueue.shift()()
  assert.equal(shift.active, null)
  assert.equal(completes, 1)
  assert.equal(root.dataset.theme, 'dark')
  assert.equal(meta.content, '#1A1917')
  for (const k of ['--paper-page', '--paper-ink', '--latte-espresso', '--latte-espresso-ink']) {
    assert.equal(root.style.getPropertyValue(k), '')
  }
})

test('Sun boundary triggers shift only when visible and reduced-motion is off; hidden/reduced-motion/manual snap cleanly', async () => {
  // 1. Live boundary with visible page & reduced-motion off -> starts shift with chosen duration.
  const { start } = globalThis.LoamSun.state(Date.parse('2026-11-13T12:00:00-08:00'), place)
  let now = start - 10
  page({ hidden: false, visibilityState: 'visible' })
  const shifts = [], cancels = []
  const fakeShift = {
    _active: null,
    get active() { return this._active },
    start(opts) { shifts.push(opts); this._active = { fromMode: opts.fromMode, toMode: opts.toMode, look: opts.look, optionsKey: opts.optionsKey }; return true },
    cancel() { if (this._active) cancels.push(this._active); this._active = null },
  }
  const theme = createTheme(null, {
    kind: 'desktop',
    boot: { values: { ...held(), desktop: { mode: 'sun', transition: 'long', size: 100, theme: 'latte' } }, available: true },
    now: () => now,
    reducedMotion: { matches: false },
    shift: fakeShift,
    request: async () => held(),
  })
  assert.equal(theme.apply(), 'dark') // initial paint: no shift
  assert.equal(shifts.length, 0)

  // Advance clock across boundary and let the 10ms timer fire.
  now = start + 1
  await new Promise((resolve) => setTimeout(resolve, 40))
  assert.equal(shifts.length, 1)
  assert.deepEqual([shifts[0].fromMode, shifts[0].toMode, shifts[0].duration], ['dark', 'light', transitionMs('long')])
  assert.equal(theme.shifting, true)

  // Refresh with unchanged mode/theme while visible keeps the active shift running.
  await theme.refresh()
  assert.equal(cancels.length, 0)
  assert.equal(theme.shifting, true)

  // Manual mode change interrupts and cancels cleanly into the chosen state.
  theme.set({ mode: 'dark' })
  assert.equal(cancels.length, 1)
  assert.equal(theme.shifting, false)
  assert.equal(theme.current, 'dark')

  // 2. Reduced motion ON -> instant snap, never shifts.
  now = start - 10
  page({ hidden: false, visibilityState: 'visible' })
  shifts.length = 0
  const reducedTheme = createTheme(null, {
    kind: 'desktop',
    boot: { values: held(), available: true },
    now: () => now,
    reducedMotion: { matches: true },
    shift: fakeShift,
  })
  assert.equal(reducedTheme.apply(), 'dark')
  now = start + 1
  await new Promise((resolve) => setTimeout(resolve, 40))
  assert.equal(shifts.length, 0)
  assert.equal(reducedTheme.current, 'light')

  // 3. Hidden document at boundary -> instant snap, never shifts.
  now = start - 10
  page({ hidden: true, visibilityState: 'hidden' })
  shifts.length = 0
  const hiddenTheme = createTheme(null, {
    kind: 'desktop',
    boot: { values: held(), available: true },
    now: () => now,
    reducedMotion: { matches: false },
    shift: fakeShift,
  })
  assert.equal(hiddenTheme.apply(), 'dark')
  now = start + 1
  await new Promise((resolve) => setTimeout(resolve, 40))
  assert.equal(shifts.length, 0)
  assert.equal(hiddenTheme.current, 'light')
})

test('transition setting persists via host route and uses older-host fallback cache', async () => {
  page()
  const storage = new Map()
  globalThis.localStorage = { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) }
  try {
    const host = held()
    let refuseTransition = true, writes = 0
    const theme = createTheme(null, {
      kind: 'desktop',
      boot: { values: host, available: true },
      request: async (method, body) => {
        if (method === 'PATCH') {
          writes++
          if (refuseTransition && body.desktop?.transition) throw new Error('unknown field')
          if (body.desktop) Object.assign(host.desktop, body.desktop)
        }
        return structuredClone(host)
      },
    })
    theme.set({ transition: 'invalid' })
    assert.equal(theme.value.transition, 'normal')
    theme.set({ transition: 'fast' })
    assert.equal(theme.value.transition, 'fast')
    await theme.save()
    assert.equal(writes, 1)
    assert.equal(theme.status.error, null)
    assert.equal(theme.status.localOnly, true)
    assert.equal(globalThis.LoamSun.local('desktop').transition, 'fast')

    // Host later supports transition: next load migrates and clears local cache.
    refuseTransition = false
    page()
    const migrated = createTheme(null, {
      kind: 'desktop',
      boot: { values: host, available: true },
      request: async (method, body) => {
        if (method === 'PATCH' && body.desktop) Object.assign(host.desktop, body.desktop)
        return structuredClone(host)
      },
    })
    assert.equal(migrated.value.transition, 'fast')
    await migrated.save()
    assert.equal(host.desktop.transition, 'fast')
    assert.deepEqual(globalThis.LoamSun.local('desktop'), {})
  } finally {
    delete globalThis.localStorage
  }
})
