import test from 'node:test'
import assert from 'node:assert/strict'
import { createRegistry } from '../public/plugin-registry.js'
import { createPluginSettings } from '../public/plugin-settings.js'
import { createPluginLoader, pluginSettings } from '../public/plugin-loader.js'
import { createPreferences as reading } from '../../plugins/_listed/read-aloud/page/settings.js'
import { createPreferences as dictation } from '../../plugins/_listed/dictation/page/settings.js'
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

// The 0.2.0 walk: Comments & suggestions' `reach` could be set only from the
// shell; the plugin page drew no setting for any plugin but Daily notes.
test('a plugin’s own settings (settings.entry) are drawn on its page and saved one by one', async () => {
  installDom()
  const registry = createRegistry(), calls = []
  const entries = [{ key: 'reach', label: 'Where the editor and share links work', choices: ['everywhere', 'staging'], default: 'everywhere' },
    { key: 'home', label: 'Where “Made with Loam” points', choices: null, default: '' }]
  const row = { id: 'doc-comments', key: 'shipped:doc-comments', name: 'Comments & suggestions', layer: 'shipped', on: true, effective: true, switch: 'plugin', contributes: { 'settings.entry': entries } }
  const loader = { st: { rows: [row], loaded: true, failures: new Map(), writing: new Set() } }
  const api = async (method, path, body) => {
    calls.push([method, path, body])
    if (path.endsWith('/settings') && method === 'GET') return { id: row.id, entries, values: { reach: 'everywhere', home: '' } }
    if (path.endsWith('/settings') && method === 'PATCH') return { id: row.id, values: body.values }
    return { plugin: row }
  }
  const settings = createPluginSettings({ loader, theme: { value: {} }, host: { registry, settingsHead: title => h('h2', { text: title }), api } })
  await settings.open(row)
  const draw = () => h('div', {}, settings.view())
  assert.equal(all(draw(), 'plugin-entry').length, 2)
  assert.match(text(draw()), /Where the editor and share links work/)
  const select = all(draw(), 'field-input').find(n => n.tagName === 'SELECT' || n.tag === 'select')
  assert.ok(select, 'a choice is a menu')
  select.value = 'staging'
  select.fire('change', { currentTarget: select })
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(calls.at(-1), ['PATCH', '/plugins/doc-comments/settings', { values: { reach: 'staging' } }])
  assert.match(text(draw()), /Saved\./)
})

// Images 1.1.0 (2026-11-18): a setting reads for a person. A choice shows its
// words, a note sits under it, and the model names wait behind Advanced.
test('a plugin’s settings show choice labels and notes, and keep advanced ones behind Advanced', async () => {
  installDom()
  const registry = createRegistry(), calls = []
  const entries = [{ key: 'backend', label: 'Service', choices: ['auto', 'openai', 'google'], labels: { auto: 'Automatic', openai: 'OpenAI', google: 'Google' }, note: 'Automatic uses OpenAI when you have that key.', default: 'auto' },
    { key: 'openai_model', label: 'OpenAI model', choices: null, default: 'gpt-image-2', advanced: true }]
  const row = { id: 'images', key: 'yours:images', name: 'Images', layer: 'yours', on: true, effective: true, switch: 'plugin', contributes: { 'settings.entry': entries } }
  const loader = { st: { rows: [row], loaded: true, failures: new Map(), writing: new Set() } }
  const api = async (method, path, body) => {
    calls.push([method, path, body])
    if (path.endsWith('/settings') && method === 'GET') return { id: row.id, entries, values: { backend: 'auto', openai_model: 'gpt-image-2' } }
    if (path.endsWith('/settings') && method === 'PATCH') return { id: row.id, values: body.values }
    return { plugin: row }
  }
  const settings = createPluginSettings({ loader, theme: { value: {} }, host: { registry, settingsHead: title => h('h2', { text: title }), api } })
  await settings.open(row)
  const page = h('div', {}, settings.view())
  const words = text(page)
  assert.match(words, /Automatic/)
  assert.doesNotMatch(words, /\bauto\b/, 'a choice shows its words, not its id')
  assert.match(words, /Automatic uses OpenAI when you have that key\./)
  const advanced = all(page, 'plugin-entry-advanced')
  assert.equal(advanced.length, 1)
  assert.match(text(advanced[0]), /^Advanced/)
  assert.match(text(advanced[0]), /OpenAI model/)
  const select = all(page, 'field-input').find(n => n.tagName === 'SELECT' || n.tag === 'select')
  select.value = 'google'
  select.fire('change', { currentTarget: select })
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(calls.at(-1), ['PATCH', '/plugins/images/settings', { values: { backend: 'google' } }], 'the value saved is the choice’s id')
})
