import { serviceProviders } from '../public/page/services.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import { createCoreRegistry } from '../public/core-registrations.js'
import { createPluginLoader, pluginSettings } from '../public/plugin-loader.js'
const row = (id, extra = {}) => ({ id, api: 0, effective: true, on: true, switch: 'plugin', has: { page_main: true }, version: '1.0.0', overrides: [], ...extra })

test('real loader eligibility, isolated failures, owned resources and activation cleanup survive disable and refresh', async () => {
  let rows = [row('good'), row('bad'), row('import-failure'), row('future', { api: 1 }), row('shadow', { effective: false }), row('off', { on: false })]
  let reads = 0, removed = 0, cleaned = 0
  const imports = [], registry = createCoreRegistry()
  const loader = createPluginLoader({ registry, ui: { host: { provide: () => ({ remove: () => removed++ }) } },
    request: async (method, path, body) => { if (method === 'GET') { reads++; return { plugins: rows } }; rows = rows.map(r => path === `/plugins/${r.id}` ? { ...r, on: body.on } : r); return { plugin: rows[0] } },
    importer: async url => {
      imports.push(url)
      if (url.includes('import-failure')) throw new Error('Cannot import this module.')
      return { activate(loam) {
        loam.ui.host.provide('service', {})
        loam.places.app.overlay.add({ id: 'overlay', render: () => 'hello' })
        if (url.includes('/bad/')) throw new Error('Activation failed.')
        loam.commands.register({ id: 'example', run: () => 'a command' })
        return () => cleaned++
      } }
    },
  })
  await loader.refresh()
  assert.equal(imports.length, 3)
  assert.equal(registry.scope({ id: 'observer' }).commands.run('example'), 'a command')
  assert.deepEqual(registry.draw('app.overlay', { slot: 'overlay' }), ['hello'])
  assert.equal(loader.st.failures.get('bad'), 'Activation failed.')
  assert.equal(loader.st.failures.get('import-failure'), 'Cannot import this module.')
  assert.match(loader.st.failures.get('future'), /API 1/)
  assert.equal(removed, 1, 'failed activation releases its provided service too')
  rows = [rows[0]]
  await loader.refresh(); assert.equal(imports.length, 3, 'unchanged active code is not reactivated')
  await loader.toggle('good', false)
  assert.ok(reads >= 3); assert.equal(cleaned, 1); assert.equal(removed, 2)
  assert.deepEqual(registry.draw('app.overlay', { slot: 'overlay' }), [])
  assert.throws(() => registry.scope({ id: 'observer' }).commands.run('example'), /Unknown command/)
  await loader.dispose()
})

test('a disable during a slow activate is reread and cannot leave a zombie registration', async () => {
  let rows = [row('slow')], finish, began
  const started = new Promise(resolve => { began = resolve }), registry = createCoreRegistry()
  const loader = createPluginLoader({ registry, ui: {}, request: async () => ({ plugins: rows }), importer: async () => ({ async activate(loam) {
    loam.places.app.overlay.add({ id: 'slow', render: () => 'slow' }); began(); await new Promise(resolve => { finish = resolve })
  } }) })
  const reading = loader.refresh(); await started; rows = [row('slow', { on: false })]
  const reread = loader.refresh(); finish(); await Promise.all([reading, reread])
  assert.deepEqual(registry.draw('app.overlay', { slot: 'overlay' }), [])
  await loader.dispose()
})

test('offline fallback is explicit, scoped settings stay local, and failed storage falls back to memory', async () => {
  const registry = createCoreRegistry(), imports = []
  const loader = createPluginLoader({ registry, ui: {}, request: async () => { throw Object.assign(new Error('Host unavailable.'), { status: 404 }) }, fallback: [row('shipped', { local: '/plugins/shipped/page/main.js' })], importer: async url => { imports.push(url); return { activate() {} } } })
  await loader.refresh(); assert.deepEqual(imports, ['/plugins/shipped/page/main.js']); assert.equal(loader.st.error, 'Host unavailable.')
  const storage = { getItem() { throw new Error('No storage') }, setItem() { throw new Error('No storage') } }
  const settings = pluginSettings('one', storage); settings.set('choice', 'yes'); assert.equal(settings.get('choice'), 'yes')
  assert.equal(pluginSettings('two', storage).get('choice', 'no'), 'no')
  await loader.dispose()
})

test('nested public service providers restore the live provider, never a disabled predecessor', () => {
  const original = {}, modules = { speech: original }, provide = serviceProviders(modules)
  const a = {}, b = {}, first = provide('speech', a), second = provide('speech', b)
  first.remove(); assert.equal(modules.speech, b)
  second.remove(); assert.equal(modules.speech, original)
  second.remove(); assert.equal(modules.speech, original)
  const third = provide('speech', a), fourth = provide('speech', b)
  fourth.remove(); assert.equal(modules.speech, a)
  third.remove(); assert.equal(modules.speech, original)
})


test('a hung activation does not delay healthy registrations, and late registrations stay closed after timeout', async () => {
  const registry = createCoreRegistry(); let finish
  const loader = createPluginLoader({ registry, ui: {}, activationMs: 25,
    request: async () => ({ plugins: [row('slow'), row('healthy')] }),
    importer: async url => ({ async activate(loam) {
      if (url.includes('/slow/')) { await new Promise(resolve => { finish = resolve }); loam.places.app.overlay.add({ id: 'late', render: () => 'late' }) }
      else loam.commands.register({ id: 'healthy.ready', run: () => true })
    } }),
  })
  const loading = loader.refresh(); await new Promise(setImmediate)
  assert.equal(registry.scope({ id: 'observer' }).commands.run('healthy.ready'), true)
  await loading; assert.match(loader.st.failures.get('slow'), /activation did not finish/)
  finish(); await new Promise(setImmediate)
  assert.deepEqual(registry.draw('app.overlay', { slot: 'overlay' }), [])
  await loader.dispose()
})

test('an accepted disable removes registrations while activation is still waiting', async () => {
  let rows = [row('slow')], finish, started
  const began = new Promise(resolve => { started = resolve }), registry = createCoreRegistry()
  const loader = createPluginLoader({ registry, ui: {}, request: async (method, path, body) => {
    if (method === 'PATCH') { rows = [row('slow', { on: body.on })]; return { plugin: rows[0] } }
    return { plugins: rows }
  }, importer: async () => ({ async activate(loam) {
    loam.places.app.overlay.add({ id: 'slow', render: () => 'slow' }); started(); await new Promise(resolve => { finish = resolve })
  } }) })
  const reading = loader.refresh(); await began
  const disabling = loader.toggle('slow', false); await new Promise(setImmediate)
  assert.equal(loader.st.rows[0].on, false)
  assert.deepEqual(registry.draw('app.overlay', { slot: 'overlay' }), [])
  finish(); await Promise.all([reading, disabling]); await loader.dispose()
})

test('an initially unreachable catalog cannot re-enable a plugin whose host state is unknown', async () => {
  const registry = createCoreRegistry(), imports = []
  const loader = createPluginLoader({ registry, ui: {}, request: async () => { throw Object.assign(new Error('Temporarily unavailable.'), { status: 503 }) }, fallback: [row('shipped', { local: '/plugins/shipped/page/main.js' })], importer: async url => { imports.push(url); return { activate() {} } } })
  await loader.refresh(); assert.deepEqual(imports, [])
  assert.equal(loader.st.rows[0].switch, null)
  assert.match(loader.st.rows[0].warnings[0], /unknown/)
  await loader.dispose()
})
