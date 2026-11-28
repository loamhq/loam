import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'
import { appearanceRegistry } from '../tools/appearance-registry.js'
import { loadThemes } from '../src/themes.js'
import { createTheme } from '../public/theme.js'
import { SIZE_MIN, SIZE_MAX, SIZE_STEP, SIZE_DEFAULT, sizeToScale, stepSize, migrateLegacySize, uiScale } from '../public/scale.js'
import { appearanceBoot, appearanceValues } from '../src/appearance.js'
import { fakeBroker, appAgainst, closing, TOKEN } from './harness.js'
import { installDom } from './fakedom.js'
import { allRoots, readThemeCSS } from './listed-themes.mjs'

function page() {
  const document = installDom()
  document.documentElement = document.createElement('html')
  document.documentElement.dataset = {}
  document.querySelector = () => null
  return document
}
const values = () => ({ phone: { mode: 'dark', size: 125 }, desktop: { mode: 'light', size: 90 } })

test('fresh browsers use host values, writes merge one device kind and leave other fields alone', async () => {
  const held = values(), calls = []
  const request = async (method, body) => {
    calls.push({ method, body })
    if (body) for (const [kind, patch] of Object.entries(body)) Object.assign(held[kind], patch)
    return structuredClone(held)
  }
  page()
  const desktop = createTheme(null, { kind: 'desktop', boot: { values: held, available: true }, request })
  assert.equal(desktop.apply(), 'light')
  desktop.set({ size: 95 }); desktop.set({ mode: 'system' }); await desktop.save()
  assert.deepEqual(calls[0], { method: 'PATCH', body: { desktop: { size: 95, mode: 'system' } } })
  assert.deepEqual(held.phone, { mode: 'dark', size: 125 })
  page()
  const phone = createTheme(null, { kind: 'phone', boot: { values: held, available: true }, request })
  phone.set({ mode: 'light' }); await phone.save()
  assert.equal(held.desktop.size, 95)
  page()
  const fresh = createTheme(null, { kind: 'desktop', boot: { values: structuredClone(held), available: true }, request })
  assert.deepEqual(fresh.value, { mode: 'system', transition: 'normal', size: 95, theme: 'latte' })
  held.desktop = { mode: 'dark', size: 85 }
  await fresh.refresh()
  assert.deepEqual(fresh.value, { ...held.desktop, transition: 'normal', theme: 'latte' }, 'a persona change arrives on refresh')
})

test('system mode follows media; the quick toggle writes an explicit host mode', async () => {
  page()
  let change, saved
  const media = { matches: false, addEventListener: (type, fn) => { change = fn } }
  const theme = createTheme(media, { kind: 'desktop', request: async (method, body) => { saved = body } })
  assert.equal(theme.apply(), 'light')
  media.matches = true; change(); assert.equal(theme.current, 'dark')
  theme.toggle(); await theme.save()
  assert.deepEqual(saved, { desktop: { mode: 'light' } })
  media.matches = false; change(); assert.equal(theme.current, 'light')
})

test('desktop modifier shortcuts use natural steps, enforce 60–200% bounds, and reset; phone keys pass through', async () => {
  page()
  const theme = createTheme(null, { kind: 'desktop', request: async () => ({}) })
  const key = (key, over = {}, desktop = true) => {
    const event = { key, ctrlKey: true, preventDefault() { this.prevented = true }, ...over }
    const accepted = theme.key(event, desktop)
    assert.equal(Boolean(event.prevented), accepted)
    return accepted
  }
  assert.equal(key('+'), true); assert.equal(theme.value.size, 105)
  assert.equal(key('-', { ctrlKey: false, metaKey: true }), true); assert.equal(theme.value.size, 100)
  for (let i = 0; i < 10; i++) key('=')
  assert.equal(theme.value.size, 150)
  key('='); assert.equal(theme.value.size, 160)
  for (let i = 0; i < 10; i++) key('=')
  assert.equal(theme.value.size, 200)
  key('-'); assert.equal(theme.value.size, 190)
  for (let i = 0; i < 30; i++) key('-')
  assert.equal(theme.value.size, 60)
  key('0'); assert.equal(theme.value.size, 100)
  for (const over of [{ altKey: true }, { isComposing: true }, { ctrlKey: false }]) assert.equal(key('+', over), false)
  assert.equal(key('+', {}, false), false)
  assert.equal(key('x'), false)
  await theme.save()
})

test('a failed save remains visible and retryable; in-flight edits are ordered without losing fields', async () => {
  page()
  const calls = [], queued = []
  let fail = true
  const request = async (method, body) => {
    calls.push(body)
    if (fail) throw new Error('host unavailable')
    if (calls.length === 2) await new Promise((resolve) => queued.push(resolve))
  }
  const theme = createTheme(null, { kind: 'desktop', request })
  theme.set({ size: 90 }); await theme.save()
  assert.match(theme.status.error, /host unavailable/)
  assert.equal(theme.value.size, 90)
  fail = false
  const saving = theme.save()
  theme.set({ mode: 'dark', size: 125 })
  queued.shift()(); await saving
  assert.equal(theme.status.error, null)
  assert.equal(theme.status.saving, false)
  assert.deepEqual(calls, [{ desktop: { size: 90 } }, { desktop: { size: 90 } }, { desktop: { mode: 'dark', size: 125 } }])
})

test('host boot runs before styles/app, ignores browser theme storage, and selects by pointer rather than width', async () => {
  const html = await readFile(new URL('../public/index.html', import.meta.url), 'utf8')
  assert.ok(html.indexOf('/appearance-state.js') < html.indexOf('/theme-boot.js'))
  assert.ok(html.indexOf('/theme-boot.js') < html.indexOf('rel="stylesheet"'))
  assert.ok(html.indexOf('/theme-boot.js') < html.indexOf('src="/app.js"'))
  const boot = await readFile(new URL('../public/theme-boot.js', import.meta.url), 'utf8')
  const state = await appearanceBoot({ json: async () => ({ status: 200, body: JSON.stringify(values()) }) })
  for (const coarse of [true, false]) {
    const properties = {}, root = { dataset: {}, style: { setProperty: (k, v) => { properties[k] = v } } }
    const context = vm.createContext({ document: { documentElement: root }, innerWidth: 390, matchMedia: (query) => ({ matches: query === '(pointer: coarse)' ? coarse : true }), localStorage: { getItem() { throw new Error('boot must not read storage') } } })
    vm.runInContext(state, context); vm.runInContext(boot, context)
    assert.equal(root.dataset.deviceKind, coarse ? 'phone' : 'desktop')
    assert.equal(root.dataset.theme, coarse ? 'dark' : 'light')
    assert.equal(properties['--ui-scale'], coarse ? 1.125 : 0.81)
  }
})

test('public boot filters unrelated host data; failures fall back without leaking a token', async () => {
  assert.deepEqual(appearanceValues({ phone: { mode: 'invalid', transition: 'invalid', size: 94 } }), { phone: { mode: 'system', transition: 'normal', size: 100, theme: 'latte' }, desktop: { mode: 'system', transition: 'normal', size: 100, theme: 'latte' } })
  const source = await appearanceBoot({ json: async () => ({ status: 200, body: JSON.stringify({ ...values(), secret: 'must-not-appear' }) }) })
  assert.doesNotMatch(source, /must-not-appear/)
  const failed = await appearanceBoot({ json: async () => { throw new Error('secret-in-error') } })
  assert.match(failed, /"available":false/)
  assert.doesNotMatch(failed, /secret-in-error/)
})

test('app relay authenticates both settings operations and boot, preserves host refusal, and is no-store', async (t) => {
  const held = values()
  const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
  const broker = await fakeBroker({
    'GET /v1/settings/appearance': (req, res) => json(res, held),
    'PATCH /v1/settings/appearance': (req, res, url, raw) => json(res, { error: 'size must be a multiple of 5' }, 400),
  })
  const app = await appAgainst(broker); t.after(closing(app, broker))
  const boot = await fetch(app.url + '/appearance-state.js')
  assert.equal(boot.headers.get('cache-control'), 'no-store')
  assert.match(await boot.text(), /"size":90/)
  const get = await fetch(app.url + '/api/settings/appearance')
  assert.deepEqual(await get.json(), held)
  const patch = await fetch(app.url + '/api/settings/appearance', { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ desktop: { size: 91 } }) })
  assert.equal(patch.status, 400)
  assert.deepEqual(await patch.json(), { error: 'size must be a multiple of 5' })
  assert.equal(patch.headers.get('cache-control'), 'no-store')
  assert.equal((await fetch(app.url + '/api/settings/appearance', { method: 'POST' })).status, 405)
  assert.equal((await fetch(app.url + '/api/settings/appearance', { method: 'PATCH', headers: { origin: 'https://elsewhere.invalid' } })).status, 403)
  assert.ok(broker.seen.every((r) => r.auth === `Bearer ${TOKEN}`))
  assert.equal(broker.seen.filter(row => row.path === '/v1/settings/appearance').length, 3)
  assert.equal(broker.seen.filter(row => row.path === '/v1/plugins').length, 1)
})


test('the registry handoff covers every manifest option and the UI limits', async () => {
  const registry = JSON.parse(await readFile(new URL('../docs/appearance-settings.registry.json', import.meta.url)))
  assert.equal(registry.route, '/v1/settings/appearance')
  assert.deepEqual(registry, appearanceRegistry((await loadThemes()).themes))
  assert.equal(registry.settings.length, 17)
  for (const kind of ['phone', 'desktop']) {
    const mode = registry.settings.find((s) => s.key === `appearance.${kind}.mode`)
    const transition = registry.settings.find((s) => s.key === `appearance.${kind}.transition`)
    const size = registry.settings.find((s) => s.key === `appearance.${kind}.size`)
    assert.deepEqual(mode.enum, ['light', 'dark', 'sun', 'system'])
    assert.equal(mode.default, 'system')
    assert.deepEqual(transition.enum, ['fast', 'normal', 'long'])
    assert.equal(transition.default, 'normal')
    assert.deepEqual([size.minimum, size.maximum, size.multipleOf, size.default], [SIZE_MIN, SIZE_MAX, SIZE_STEP, SIZE_DEFAULT])
  }
  const source = await readFile(new URL('../public/page/settings.js', import.meta.url), 'utf8')
  assert.match(source, /id: 'appearance-zoom', type: 'range', min: SIZE_MIN, max: SIZE_MAX, step: SIZE_STEP/)
})

test('a delayed host refresh cannot roll back a newer saved edit', async () => {
  page()
  let finish
  const theme = createTheme(null, { kind: 'desktop', boot: { values: values(), available: true }, request: async (method) => {
    if (method === 'GET') return await new Promise((resolve) => { finish = resolve })
    return { desktop: { mode: 'dark', size: 125 } }
  } })
  const refreshing = theme.refresh()
  theme.set({ mode: 'dark', size: 125 }); await theme.save()
  finish(values()); await refreshing
  assert.deepEqual(theme.value, { mode: 'dark', transition: 'normal', size: 125, theme: 'latte' })
})

test('new 0.9-base scale, step sizes, legacy migration formula, and uiScale bounds', () => {
  assert.equal(sizeToScale(60), 0.54)
  assert.equal(sizeToScale(85), 0.765)
  assert.equal(sizeToScale(100), 0.9)
  assert.equal(sizeToScale(150), 1.35)
  assert.equal(sizeToScale(200), 1.8)
  assert.equal(stepSize(60, -1), 60)
  assert.equal(stepSize(60, 1), 65)
  assert.equal(stepSize(145, 1), 150)
  assert.equal(stepSize(150, 1), 160)
  assert.equal(stepSize(155, 1), 160)
  assert.equal(stepSize(155, -1), 150)
  assert.equal(stepSize(200, 1), 200)
  assert.equal(stepSize(200, -1), 190)
  assert.equal(migrateLegacySize(75), 85)
  assert.equal(migrateLegacySize(90), 100)
  assert.equal(migrateLegacySize(100), 100)
  assert.equal(migrateLegacySize(125), 140)
  assert.equal(migrateLegacySize(150), 165)
  const doc = page()
  doc.documentElement.style.setProperty('--ui-scale', '0.54')
  assert.equal(uiScale(), 0.54)
  doc.documentElement.style.setProperty('--ui-scale', '1.8')
  assert.equal(uiScale(), 1.8)
  doc.documentElement.style.setProperty('--ui-scale', '0.2')
  assert.equal(uiScale(), 1)
})

test('older host refusing extended sizes falls back to device-local cache and migrates once accepted', async () => {
  const store = new Map()
  const prevStorage = globalThis.localStorage
  globalThis.localStorage = {
    getItem: (k) => store.has(k) ? store.get(k) : null,
    setItem: (k, v) => { store.set(k, String(v)) },
    removeItem: (k) => { store.delete(k) },
  }
  try {
    page()
    let refuseExtended = true
    const held = { phone: { mode: 'light', size: 100, theme: 'latte' }, desktop: { mode: 'light', size: 100, theme: 'latte' } }
    const request = async (method, body) => {
      if (method === 'PATCH') {
        const s = body?.phone?.size
        if (refuseExtended && s !== undefined && (s < 75 || s > 150)) {
          throw new Error('phone size is a whole percent from 75 to 150 in steps of 5')
        }
        if (body?.phone) Object.assign(held.phone, body.phone)
      }
      return structuredClone(held)
    }
    const phone = createTheme(null, { kind: 'phone', boot: { values: structuredClone(held), available: true }, request })
    phone.set({ size: 60 }); await phone.save()
    assert.equal(phone.value.size, 60)
    assert.equal(phone.status.error, null)
    assert.equal(phone.status.localOnly, true)
    assert.equal(phone.status.sizeLocalOnly, true)
    assert.equal(JSON.parse(store.get('loam.appearance.size')).phone, 60)
    // Boot overlays the cached extended size before first paint.
    const boot = await readFile(new URL('../public/theme-boot.js', import.meta.url), 'utf8')
    const sizeLocal = await readFile(new URL('../public/size-local.js', import.meta.url), 'utf8')
    const state = await appearanceBoot({ json: async () => ({ status: 200, body: JSON.stringify(held) }) })
    const properties = {}, root = { dataset: {}, style: { setProperty: (k, v) => { properties[k] = v } } }
    const ctx = vm.createContext({ document: { documentElement: root }, matchMedia: (q) => ({ matches: q === '(pointer: coarse)' }), localStorage: globalThis.localStorage })
    vm.runInContext(state, ctx); vm.runInContext(sizeLocal, ctx); vm.runInContext(boot, ctx)
    assert.equal(properties['--ui-scale'], 0.54)
    // Once the host supports 60–200%, the next session migrates and clears the cache.
    refuseExtended = false
    page()
    const migrated = createTheme(null, { kind: 'phone', boot: { values: structuredClone(held), available: true }, request })
    assert.equal(migrated.value.size, 60)
    await migrated.save()
    assert.equal(held.phone.size, 60)
    assert.equal(migrated.status.localOnly, false)
    assert.equal(store.has('loam.appearance.size'), false)
  } finally {
    globalThis.localStorage = prevStorage
  }
})
