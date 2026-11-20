import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { text, all } from './browser.js'
import { createPluginSettings } from '../public/plugin-settings.js'
import { settingsHome, settingsSection } from '../public/app-shape.js'
import { h } from '../public/ui.js'

function backupRow(on = false) {
  return {
    id: 'git-backup',
    key: 'shipped:git-backup:',
    name: 'Git backup',
    version: '1.0.0',
    author: 'Loam contributors',
    layer: 'shipped',
    switch: 'plugin',
    effective: true,
    broken: null,
    on,
    source: { kind: 'loam' },
    touches: { network: [{ host: 'the git host you connect', why: 'Sends your history.' }], record: 'write', memory: 'write', server: true },
  }
}

test('Settings shows the backup-off message only when Git backup is off', async () => {
  installDom()
  const offRow = backupRow(false)
  const loader = {
    st: { rows: [offRow], loaded: true, loading: false, error: '', failures: new Map(), writing: new Set() },
    toggle: async () => {},
    remove: async () => ({}),
    refresh: async () => {},
  }
  const host = {
    api: async () => ({ plugin: offRow }),
    registry: { entries: () => [], overrides: () => [], emit() {}, invoke() {} },
    settingsBack: (label) => h('button', { type: 'button', text: label }),
    settingsHead: (title) => h('h2', { text: title }),
  }
  const ui = createPluginSettings({ loader, theme: { value: { theme: 'latte' } }, host, changed() {} })

  const renderText = () => ui.view().filter(Boolean).map(text).join(' ')
  const listOff = renderText()
  assert.match(listOff, /Git backup/)
  assert.match(listOff, /Off-box backup is off/)

  await ui.open(offRow)
  const detailOff = renderText()
  assert.match(detailOff, /Off-box backup is off/)
  assert.match(detailOff, /Nothing is backed up off this machine yet/)
  const enableSwitch = ui.view().filter(Boolean).flatMap(n => all(n, 'schedule-switch'))[0]
  assert.equal(enableSwitch.getAttribute('aria-label'), 'Enable Git backup')
  assert.equal(enableSwitch.getAttribute('aria-checked'), 'false')

  // Connected / enabled owner: list row and detail header remain unchanged.
  const onRow = backupRow(true)
  loader.st.rows = [onRow]
  ui.st.selected = null
  const listOn = renderText()
  assert.match(listOn, /Enabled/)
  assert.doesNotMatch(listOn, /Off-box backup is off/)

  host.api = async () => ({ plugin: onRow })
  host.registry = {
    entries: () => [{ id: 'git-backup', group: 'plugin', owner: 'git-backup', title: 'Backup', entries: 'own' }],
    overrides: () => [],
    emit() {},
    invoke: () => h('p', { text: 'Everything is backed up.' }),
  }
  await ui.open(onRow)
  const detailOn = renderText()
  assert.doesNotMatch(detailOn, /Off-box backup is off/)
  assert.match(detailOn, /Everything is backed up\./)

  // Direct Settings aliases resolve to Plugins.
  assert.equal(settingsHome('backup'), 'plugins')
  assert.equal(settingsHome('git-backup'), 'plugins')
  assert.equal(settingsSection('backup'), 'plugins')
})
