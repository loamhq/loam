import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { mount } from '../public/page/settings.js'
import { createRegistry } from '../public/plugin-registry.js'
import { h } from '../public/ui.js'
import { installDom } from './fakedom.js'
import { all, text } from './browser.js'

function fixture() {
  installDom()
  const registry = createRegistry(), events = []
  const host = { registry, state: { github: { loaded: true, data: null, missing: true }, providers: [], sheet: { kind: 'settings', page: 'integrations' } },
    api: async () => ({}), schedule() {}, providerSignIn: { cancel() {} } }
  const settings = mount(host)
  registry.scope({ id: '@core' }).events.on('sheet.changing', event => events.push(event))
  return { host, registry, settings, events }
}

test('any enabled plugin can contribute an integration row, mark, detail, account route and notice', () => {
  const { host, registry, settings, events } = fixture()
  const plugin = registry.scope({ id: 'example-service', layer: 'yours' })
  const summaries = { connected: true, summary: '2 accounts' }
  plugin.places.settings.section.add({ id: 'example', title: 'Example service', group: 'integrations', icon: () => h('span', { text: 'Mark' }),
    summary: () => summaries, notice: () => ({ text: 'Needs attention' }),
    render: ({ sheet }) => [h('p', { text: `Account ${sheet.account ?? 'list'}` })] })
  const view = h('div', {}, ...settings.settingsIntegrations())
  assert.match(text(view), /Services.*Mark.*Example service.*2 accounts/)
  assert.match(text(h('div', {}, ...settings.settingsConnections())), /AI providers.*Services.*Example service.*API keys/)
  assert.equal(settings.integrationsWarn(), true)
  all(view, 'setrow')[0].fire('click', {})
  assert.equal(events.length, 1)
  assert.equal(host.state.sheet.provider, 'example')
  assert.match(text(h('div', {}, ...settings.settingsBody(host.state.sheet, true))), /Account list/)
  host.state.sheet.account = 'one'
  assert.match(text(h('div', {}, ...settings.settingsBody(host.state.sheet, true))), /Account one/)
  plugin.dispose()
  assert.equal(settings.integrationsWarn(), false)
  assert.doesNotMatch(text(h('div', {}, ...settings.settingsIntegrations())), /Example service/)
  assert.match(text(h('div', {}, ...settings.settingsBody(host.state.sheet, true))), /Integration unavailable/)
})

test('integration summary, mark, notice and detail failures release only their plugin', () => {
  for (const method of ['summary', 'icon', 'notice', 'render']) {
    const { host, registry, settings } = fixture()
    const plugin = registry.scope({ id: 'broken', layer: 'yours' })
    plugin.places.settings.section.add({ id: 'broken', title: 'Broken', group: 'integrations', icon: () => h('span'),
      summary: () => ({ connected: false, summary: 'Not connected' }), notice: () => null, render: () => [],
      [method]: () => { throw new Error('fixture failure') } })
    if (method === 'notice') settings.integrationsWarn()
    else if (method === 'render') settings.settingsBody({ ...host.state.sheet, page: 'integration', provider: 'broken' }, true)
    else settings.settingsIntegrations()
    assert.equal(registry.entries('settings.section').length, 0)
  }
})

test('core page and relay contain no integration-specific plugin code', async () => {
  // A removed integration must stay outside core, including future subfolders.
  const files = async folder => (await Promise.all((await readdir(folder, { withFileTypes: true })).map(entry => entry.isDirectory()
    ? files(`${folder}/${entry.name}`) : [`${folder}/${entry.name}`]))).flat()
  const paths = [...await files('src'), ...(await files('public')).filter(file => /\.(js|css)$/.test(file) && !file.startsWith('public/plugins/'))]
  for (const file of paths) assert.doesNotMatch(await readFile(file, 'utf8'), /acme/i, file)
})
