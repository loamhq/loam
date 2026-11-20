import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { all, text } from './browser.js'
import { h } from '../public/ui.js'
import { createPluginSettings, pluginOrigin } from '../public/plugin-settings.js'

const listing = { id: 'reader', repo: 'example/reader', ref: 'v1.2.3', path: 'reader', author: 'Ada Park' }
const source = { kind: 'link', repo: 'Example/Reader', ref: 'v1.2.3', path: 'reader', via: 'link' }
const installed = { id: 'reader', source }
const commit = 'a'.repeat(40), pinned = { ...listing, pin: { ref: commit, path: 'reader' } }

test('origin is derived from the installed copy and its Market release', () => {
  assert.equal(pluginOrigin({ layer: 'shipped', id: 'reader' }, listing), 'loam')
  assert.equal(pluginOrigin({ layer: 'yours', id: 'reader', source: { kind: 'built' } }, listing), 'yours')
  assert.equal(pluginOrigin({ ...installed, source: { ...source, via: 'market' } }), 'market')
  assert.equal(pluginOrigin(installed, listing), 'market', 'a link to the same release')
  assert.equal(pluginOrigin({ ...installed, source: { ...source, via: undefined } }, listing), 'market', 'an install without via')
  assert.equal(pluginOrigin({ ...installed, source: { ...source, ref: commit, commit } }, pinned), 'market', 'a reviewed commit')
  assert.equal(pluginOrigin({ ...installed, source: { ...source, ref: 'v1.0.0' } }, listing), 'link', 'a different release')
  assert.equal(pluginOrigin(installed, { ...listing, repo: 'example/other' }), 'link', 'a different repository')
  assert.equal(pluginOrigin(installed, { ...listing, path: 'different' }), 'link', 'a different folder')
  assert.equal(pluginOrigin(installed, { ...listing, id: 'other' }), 'link', 'a different package')
  assert.equal(pluginOrigin(installed), 'link', 'no listing')
})

test('extensions group in origin order and About uses the Market author and listing or the local folder', async () => {
  installDom()
  const row = (id, fields) => ({ id, key: id, name: id, effective: true, layer: 'yours', kind: ['extension'], switch: 'plugin', ...fields })
  const market = row('reader', { source, author: 'Manifest author' })
  const local = row('mine', { source: { kind: 'built' }, folder: '/home/loam/plugins/mine' })
  const builtIn = row('loam', { layer: 'shipped' })
  const link = row('elsewhere', { source: { kind: 'link', repo: 'someone/elsewhere', ref: '1.0' } })
  const theme = row('latte', { layer: 'shipped', kind: ['theme'], switch: 'appearance' })
  const marketTheme = row('forest', { kind: ['theme'], switch: 'appearance', source: { kind: 'link', via: 'market' } })
  const rows = [link, local, market, builtIn, theme, marketTheme]
  const loader = { st: { loaded: true, rows, failures: new Map(), writing: new Set() } }
  const marketState = { loadedAt: Date.now(), list: { state: 'listed', plugins: [listing] } }
  const ui = createPluginSettings({ loader, theme: { value: { theme: 'latte' } }, host: {
    marketState, settingsHead: text => h('h2', { text }), settingsBack() {},
    api: async () => ({ plugin: ui.st.selected }),
  } })
  const view = () => h('div', {}, ui.view())
  assert.deepEqual(all(view(), 'plugin-origin-title').map(text), ['Built into Loam', 'From the Market', 'Made by you', 'From a link'])
  assert.deepEqual(all(view(), 'plugin-theme-origin').map(text), ['Built into Loam', 'From the Market'])
  assert.deepEqual(all(view(), 'plugin-row-origin').map(text), ['Unreviewed by Loam'])
  marketState.list = { state: 'unreachable', plugins: [] }
  assert.equal(all(view(), 'plugin-row-origin').length, 0)
  marketState.list = { state: 'listed', plugins: [listing] }

  await ui.open(market)
  assert.equal(text(all(view(), 'plugin-about-origin')[0]), 'From the Market · By Ada Park')
  assert.equal(all(view(), 'quietlink')[0].getAttribute('href'), '/?modal=market&listing=reader')
  await ui.open(local)
  assert.equal(text(all(view(), 'plugin-about-origin')[0]), 'Made by you · /home/loam/plugins/mine')
  ui.st.selected = null
  loader.st.rows = [builtIn]
  assert.deepEqual(all(view(), 'plugin-origin-title').map(text), ['Built into Loam'])
})
