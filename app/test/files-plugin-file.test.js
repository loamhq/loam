// A file of a plugin's own root (`/files/@<id>/<path>`, 2026-11-17, for
// doc-comments' Shared with me: a doc that lives on another Loam): it opens
// without the host being asked about it, its row is the plugin's, it has a
// panel address like any file, and a row may carry its own ⋯ menu.
import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { createFiles, folderView, addSheet } from '../public/files.js'
import { createCoreRegistry } from '../public/core-registrations.js'
import { itemDescriptor, itemHref, itemFromURL } from '../public/panel-items.js'
import { h } from '../public/ui.js'

installDom()

function setup() {
  const registry = createCoreRegistry()
  const plugin = registry.scope({ id: 'shared', layer: 'installed' })
  const calls = []
  const api = async (method, path) => { calls.push(`${method} ${path}`); return {} }
  const files = createFiles({ api, registry, schedule() {}, timers: { set: () => 1, clear() {} } })
  const went = []
  const go = (root, path, opts) => went.push({ root, path, opts })
  go.side = () => ({ available: () => false, visible: () => false, st: {} })
  go.redraw = () => {}
  return { registry, plugin, files, go, calls, went }
}

test('a file of a plugin root opens with no host call, as a Markdown file, with the row the root gives', async () => {
  const { plugin, files, calls } = setup()
  plugin.places.files.root.add({ id: 'shared', name: 'Shared', render: () => null,
    row: (path) => ({ kind: 'file', name: 'Garden plan', capabilities: { write: true } }) })
  await files.show('@shared', 'with-me/w-abcdefghij.md')
  const f = files.st.file
  assert.equal(f.root, '@shared')
  assert.equal(f.path, 'with-me/w-abcdefghij.md')
  assert.equal(f.view, 'markdown')
  assert.equal(f.loading, false)
  assert.equal(f.row.name, 'Garden plan')
  assert.equal(f.caps.write, true)
  assert.equal(files.st.root, '@shared', 'the list stays on the plugin root')
  assert.deepEqual(calls.filter(c => !c.includes('/files/roots')), [], 'the host is never asked about it')
})

test('a cold address in a plugin root is a file, even before its plugin is there', async () => {
  const { files, calls } = setup()
  await files.show('@shared', 'with-me/w-abcdefghij.md')
  assert.equal(files.st.file?.path, 'with-me/w-abcdefghij.md')
  assert.equal(calls.some(c => c.includes('/files/stat')), false)
})

test("a plugin root's file has a panel address; path tricks do not", () => {
  const item = { kind: 'file', root: '@doc-comments', path: 'with-me/w-abcdefghij.md' }
  assert.deepEqual(itemDescriptor(item), item)
  assert.deepEqual(itemFromURL(itemHref(item)), item)
  assert.equal(itemDescriptor({ kind: 'file', root: '@doc-comments', path: '../vault/x.md' }), null)
  assert.equal(itemDescriptor({ kind: 'file', root: '@Bad Root', path: 'x.md' }), null)
  assert.equal(itemDescriptor({ kind: 'file', root: 'elsewhere', path: 'x.md' }), null)
})

test("a row's own menu replaces a file's: core draws its items and runs the one chosen", () => {
  const { plugin, files, go } = setup()
  const ran = []
  const r = { kind: 'file', root: '@shared', path: 'with-me/w-abcdefghij.md', name: 'Garden plan', capabilities: {} }
  plugin.places.files.root.add({ id: 'shared', name: 'Shared', render: ({ entry }) => h('div', {}, entry(r, {
    menu: () => [{ id: 'open', label: 'Open', run: (x) => ran.push(['open', x.path]) },
      null,
      { id: 'remove', label: 'Remove', sub: 'Nothing changes for the owner.', danger: true, run: () => ran.push(['remove']) }],
  })) })
  files.st.root = '@shared'
  const view = folderView(files, { go, wide: true, home: null })
  view.querySelector('.fs-rowmenu').click()
  assert.equal(files.st.sheet.kind, 'items')
  const close = () => {}
  close.redraw = () => {}
  const sheet = h('div', {}, ...addSheet(files, { close, nameField: h('input') }))
  const rows = sheet.querySelectorAll('.fs-menu-item')
  assert.deepEqual([...rows].map(n => n.textContent), ['Open', 'RemoveNothing changes for the owner.'])
  assert.match(rows[1].getAttribute('class'), /\bdanger\b/)
  assert.equal(sheet.textContent.includes('Rename'), false, "no file actions: it is not the host's file")
  rows[0].click()
  assert.deepEqual(ran, [['open', 'with-me/w-abcdefghij.md']])
  assert.equal(files.st.sheet, null)
})
