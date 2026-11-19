import test from 'node:test'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { SETTINGS_SECTIONS, settingsHome } from '../public/app-shape.js'
import { installDom } from './fakedom.js'
import { createPicker, pickerSheet } from '../public/filepicker.js'
import { h } from '../public/ui.js'
import { all } from './browser.js'
import { rootsAnswer } from './fixtures/files-contract.js'
import { readFile } from 'node:fs/promises'

test('removed settings destinations resolve to the current settings catalog', () => {
  assert.deepEqual(SETTINGS_SECTIONS.map(item => item.label), ['Account', 'Appearance', 'Agents', 'Connections', 'Plugins', 'Files', 'About Loam'])
  assert.ok(SETTINGS_SECTIONS.every(item => !/accounts/i.test(`${item.id} ${item.label}`)), 'the word is kept for the Loam account')
  for (const page of ['providers', 'integrations', 'accounts']) assert.equal(settingsHome(page), 'connections')
  assert.equal(settingsHome('accounts', 'openai'), 'api-key')
  assert.equal(settingsHome('signin'), 'account')
  assert.equal(settingsHome('update'), 'about')
  for (const page of ['voice', 'dictation', 'read-aloud']) assert.equal(settingsHome(page), 'plugins')
  assert.equal(settingsHome('provider'), 'provider')
})
test('the settings registry handoff preserves all destinations and Appearance keys', async () => {
  const handoff = JSON.parse(await readFile('docs/settings-sections.registry.json', 'utf8'))
  const appearance = JSON.parse(await readFile('docs/appearance-settings.registry.json', 'utf8'))
  assert.deepEqual(handoff.sections.map(section => section.id), SETTINGS_SECTIONS.map(section => section.id))
  assert.deepEqual(handoff.sections.find(section => section.id === 'appearance').fields.map(field => `appearance.${field}`).sort(), appearance.settings.map(setting => setting.key).sort())
  assert.equal(handoff.threadConfiguration.effortControl, false)
})
test('the attachment picker has no host-record collection and old System requests return to its roots', async () => {
  installDom()
  const calls = []
  const picker = createPicker({ schedule() {}, api: async (method, path) => {
    calls.push(path)
    if (path === '/files/roots') return rootsAnswer()
    return { entries: [] }
  } })
  picker.st.open = true
  await picker.go(null)
  assert.deepEqual(picker.st.roots.map(root => root.root), ['vault', 'spaces'])
  await picker.go('spaces')
  assert.equal(all(h('div', {}, ...pickerSheet(picker)), 'fs-pick-host-records').length, 0)
  await picker.go('system', 'Engineer/Role.md')
  assert.equal(picker.st.root, null)
  assert.equal(picker.st.notice, 'That location is no longer shown.')
  assert.ok(calls.every(path => !path.includes('root=system')))
})
for (const width of [390, 1440]) for (const mode of ['light', 'dark']) test(`full application shape at ${width}, ${mode}`, async t => {
  const { stdout, stderr } = await promisify(execFile)(process.execPath, ['test/app-shape-driver.mjs', String(width), mode], { timeout: 15000 })
  assert.equal(stderr, '')
  const report = JSON.parse(stdout.trim())
  assert.equal(report.result, 'passed'); assert.equal(report.width, width); assert.equal(report.mode, mode)
  t.diagnostic(stdout.trim())
})
