import test from 'node:test'
import assert from 'node:assert/strict'
import { itemDescriptor, itemHref, itemFromURL, sideFromURL, withSide, migratePanelURL, renderPanel, placeFromURL, panelAncestors } from '../public/panel-items.js'
import { createCoreRegistry } from '../public/core-registrations.js'
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
test('a cold link gets the ancestors of its place and includes the chat behind a board', () => {
  for (const [path, place, ancestors] of [
    ['/files/vault/Notes/plan.md', 'files', ['/files', '/files/vault', '/files/vault/Notes']],
    ['/threads/week?view=board', 'chats', ['/', '/threads/week']],
    ['/panel?kind=profile&id=reader', 'chats', ['/']],
    ['/apps?url=https%3A%2F%2Fgarden.example', 'apps', ['/apps']],
    ['/?modal=settings', 'chats', []],
  ]) {
    const url = new URL(path, 'https://example.com')
    assert.equal(placeFromURL(url), place)
    assert.deepEqual(panelAncestors(url), ancestors)
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
