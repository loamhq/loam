import test from 'node:test'
import assert from 'node:assert/strict'
import { itemDescriptor, itemHref, itemFromURL, sideFromURL, withSide, migratePanelURL, renderPanel } from '../public/panel-items.js'
import { createCoreRegistry } from '../public/core-registrations.js'
import { registerPage } from '../public/page/registrations.js'
import { mount as mountLayout } from '../public/page/layout.js'
import { installDom } from './fakedom.js'
import { h } from '../public/ui.js'

const items = [{ kind: 'thread', id: 'conversation' }, { kind: 'file', root: 'vault', path: 'Notes/a plan.md' },
  { kind: 'app', url: 'https://garden.example/?view=a#hello' }, { kind: 'plugin:notes', id: 'my note' }]
test('addresses round trip main and side identities, independently of navigator and runtime records', () => {
  for (const main of items) for (const side of items) {
    const href = withSide(itemHref(main), side)
    assert.deepEqual(itemFromURL(href), main)
    assert.deepEqual(sideFromURL(href), side)
    assert.equal(sideFromURL(withSide(href, null)), null)
  }
  assert.deepEqual(itemDescriptor({ ...items[1], row: { revision: 'private' }, draft: 'local' }), items[1])
  assert.equal(itemFromURL('/apps?url=javascript:alert(1)'), null)
  assert.equal(sideFromURL('/?side=broken'), null)
  assert.equal(itemFromURL('/panel?id=missing-kind'), null)
})
test('a rail move never changes a panel: every ordered pair renders every main kind through the same slot', () => {
  installDom()
  for (const main of items) for (const from of ['chats', 'files', 'apps', 'notes']) for (const to of ['chats', 'files', 'apps', 'notes']) {
    const registry = createCoreRegistry(), side = items.find(item => item.kind !== main.kind)
    const host = { registry, panelId: 'main', state: { wide: true, screen: 'obsolete-screen', navigator: from, mainItem: main, collapsed: true },
      store: { sidebarHidden: true }, backButton: () => null, closeSheet() {}, schedule() {},
      panels: { st: { side }, tucked: () => false },
      threads: { load() {} }, files: { st: { roots: { loaded: true } } }, apps: { st: { loaded: true } } }
    Object.assign(host, mountLayout(host))
    const scope = registry.scope({ id: '@core' })
    registerPage({ ...scope, ui: { host } })
    const plugin = registry.scope({ id: 'notes' })
    plugin.places.rail.section.add({ id: 'notes', list: () => h('div', {}, 'Note list') })
    plugin.places.panel.item.add({ id: main.kind, render: ({ item, panel }) => h('article', {}, `${panel}:${item.kind}:${item.id ?? item.url}`) })
    const before = renderPanel({ host, item: host.state.mainItem }).textContent
    scope.commands.run('section.open', { id: to })
    assert.equal(host.state.navigator, to)
    assert.equal(host.state.mainItem, main)
    assert.equal(host.panels.st.side, side)
    assert.equal(host.state.screen, 'obsolete-screen')
    assert.equal(host.state.collapsed, false)
    assert.equal(renderPanel({ host, item: host.state.mainItem }).textContent, before)
    assert.equal(host.mainPane().textContent, before)
    plugin.dispose(); scope.dispose()
  }
})
test('plugin item renderers receive the owning panel, and disappear with their registration', () => {
  installDom()
  const registry = createCoreRegistry(), plugin = registry.scope({ id: 'notes' }), item = items[3]
  plugin.places.panel.item.add({ id: item.kind, render: ({ host, item, panel }) => h('article', {}, `${host.panelId}:${panel}:${item.id}`) })
  for (const panelId of ['main', 'side']) assert.equal(renderPanel({ host: { registry, panelId }, item }).textContent, `${panelId}:${panelId}:my note`)
  plugin.dispose()
  assert.match(renderPanel({ host: { registry, panelId: 'main' }, item }).textContent, /plugin may be off/)
})
test('the old app/side store migrates once without overriding explicit panel addresses', () => {
  const saved = { legacy: true, legacyMain: items[2], side: items[1] }
  const migrated = migratePanelURL('/', saved)
  assert.deepEqual(itemFromURL(migrated), items[2])
  assert.deepEqual(sideFromURL(migrated), items[1])
  assert.deepEqual(itemFromURL(migratePanelURL(itemHref(items[0]), saved)), items[0])
  assert.deepEqual(sideFromURL(migratePanelURL(withSide('/', items[3]), saved)), items[3])
  assert.match(migratePanelURL('/?modal=settings#heading', saved), /modal=settings.*#heading$/)
  assert.equal(itemFromURL(migratePanelURL('/?threads=fixture', saved)), null)
  assert.equal(migratePanelURL('/', { ...saved, legacy: false }), '/')
})
