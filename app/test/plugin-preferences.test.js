import test from 'node:test'
import assert from 'node:assert/strict'
import { createRegistry } from '../public/plugin-registry.js'
import { createPluginSettings } from '../public/plugin-settings.js'
import { createPluginLoader, pluginSettings } from '../public/plugin-loader.js'
import { createPreferences as reading } from '../public/plugins/read-aloud/page/settings.js'
import { createPreferences as dictation } from '../public/plugins/dictation/page/settings.js'
import { h } from '../public/ui.js'
import { installDom } from './fakedom.js'
import { all, text } from './browser.js'

test('a third-party plugin owns settings in its detail, hidden while off, with an opening lifecycle', async () => {
  installDom()
  const registry = createRegistry(), opened = []
  const row = { id: 'example', key: 'yours:example', name: 'Example', layer: 'yours', on: true, effective: true, switch: 'plugin' }
  const scope = registry.scope(row)
  scope.places.settings.section.add({ id: 'preferences', group: 'plugin', title: 'Example preferences', render: context => {
    assert.equal(context.plugin, true); assert.equal(context.back, false)
    return [h('p', { text: 'A setting declared by the plugin.' })]
  } })
  scope.events.on('plugin.settings.opened', value => opened.push(value))
  registry.scope({ id: 'other', layer: 'yours' }).places.settings.section.add({ id: 'preferences', group: 'plugin', title: 'Other preferences', render: () => h('p', { text: 'Other plugin setting' }) })
  const loader = { st: { rows: [row], loaded: true, failures: new Map(), writing: new Set() }, toggle: async (id, on) => { loader.st.rows = [{ ...row, on }] } }
  const settings = createPluginSettings({ loader, theme: { value: {} }, host: { registry, settingsHead: title => h('h2', { text: title }), api: async () => ({ plugin: loader.st.rows[0] }) } })
  await settings.open(row)
  const draw = () => h('div', {}, settings.view())
  assert.equal(all(draw(), 'plugin-preferences').length, 1)
  assert.match(text(draw()), /A setting declared by the plugin/)
  assert.doesNotMatch(text(draw()), /Other plugin setting/)
  assert.deepEqual(opened, [{ id: 'example' }])
  all(draw(), 'schedule-switch')[0].fire('click')
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(all(draw(), 'plugin-preferences').length, 0, 'off hides controls even if a registration has not yet been removed')
  all(draw(), 'schedule-switch')[0].fire('click')
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(all(draw(), 'plugin-preferences').length, 1)
  assert.deepEqual(opened, [{ id: 'example' }, { id: 'example' }])
})

test('pre-existing audio preferences survive first activation, off/on and a fresh loader without rewriting storage', async () => {
  const before = {
    'loam-ui.read-aloud': { voice: 'Kore', describeTablesAndDiagrams: false },
    'loam-ui.speech': { listenFrom: 80, briefFrom: 350, rate: 1.5 },
    'loam-ui.dictation': { mode: 'record', polish: 'full', maxMinutes: 9, deviceId: 'studio', terms: ['Previous term'] },
  }
  const data = new Map(Object.entries(before).map(([key, value]) => [key, JSON.stringify(value)])), writes = []
  const storage = { getItem: key => data.get(key) ?? null, setItem: (key, value) => { writes.push(key); data.set(key, value) } }
  let rows = ['read-aloud', 'dictation'].map(id => ({ id, layer: 'shipped', api: 0, effective: true, on: true, switch: 'plugin', has: { page_main: true } }))
  const stores = {}
  const makeLoader = () => createPluginLoader({ registry: createRegistry(), ui: {},
    request: async (method, path, body) => {
      if (method === 'PATCH') { rows = rows.map(row => path.endsWith('/' + row.id) ? { ...row, on: body.on } : row); return { plugin: rows.find(row => path.endsWith('/' + row.id)) } }
      return { plugins: rows }
    }, importer: async url => ({ activate: loam => {
      const id = url.includes('/read-aloud/') ? 'read-aloud' : 'dictation'
      stores[id] = (id === 'read-aloud' ? reading : dictation)(pluginSettings(id, storage))
    } }) })
  let loader = makeLoader()
  await loader.refresh()
  const check = () => {
    assert.deepEqual(stores['read-aloud'].readAloud, before['loam-ui.read-aloud'])
    assert.deepEqual(stores['read-aloud'].speech, before['loam-ui.speech'])
    assert.deepEqual(stores.dictation.dictation, before['loam-ui.dictation'])
  }
  check()
  for (const id of ['read-aloud', 'dictation']) { await loader.toggle(id, false); await loader.toggle(id, true) }
  check()
  await loader.dispose(); loader = makeLoader(); await loader.refresh(); check(); await loader.dispose()
  assert.deepEqual(writes, [], 'changing the settings destination needs no migration write')
})
