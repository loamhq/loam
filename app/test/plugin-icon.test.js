import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { all, text } from './browser.js'
import { h } from '../public/ui.js'
import { createPluginSettings } from '../public/plugin-settings.js'

const ui = rows => {
  const loader = { st: { loaded: true, rows, failures: new Map(), writing: new Set() }, refresh: async () => {}, toggle() {} }
  return createPluginSettings({ loader, theme: { value: {} }, host: { settingsHead: t => h('h2', { text: t }), settingsBack() {}, api: async () => ({}) } })
}

test('a plugin icon loads through the page’s route, and a plugin without one gets a letter, never a broken image', () => {
  installDom()
  const rows = [
    { id: 'images', key: 'yours:images', name: 'Images', effective: true, layer: 'yours', switch: 'plugin', on: true, kind: ['extension'], files: '/v1/plugins/images/files/', icon: 'assets/icon.svg' },
    { id: 'plain-tool', key: 'yours:plain-tool', name: 'plain tool', effective: true, layer: 'yours', switch: 'plugin', on: false, kind: ['extension'], files: '/v1/plugins/plain-tool/files/' },
  ]
  const view = h('div', {}, ui(rows).view())
  const icons = all(view, 'plugin-icon')
  assert.equal(icons[0].getAttribute('src'), '/api/plugins/images/files/assets/icon.svg', 'never the broker’s /v1 path')
  assert.equal(icons[1].getAttribute('src'), null)
  assert.equal(text(icons[1]), 'P')
  assert.ok(icons[1].getAttribute('class').includes('plugin-icon-letter'))
})
