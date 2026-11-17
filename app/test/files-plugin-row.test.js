// A plugin's root draws core's own file row (`files.root`'s `entry`), and a
// plugin may add an item, first, to a file's ⋯ menu (`files.action`, slot
// `menu`). 2026-11-17: Shared Docs had its own row, with no ⋯ menu and no
// way to open beside; now it cannot drift from a folder's row.
import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { createFiles, folderView, addSheet } from '../public/files.js'
import { createCoreRegistry } from '../public/core-registrations.js'
import { h } from '../public/ui.js'
import { row } from './fixtures/files-contract.js'

installDom()

const doc = row('vault', 'Notes/plan.md', { capabilities: { read: true, write: true, download: true, rename: true } })

function setup() {
  const registry = createCoreRegistry()
  const plugin = registry.scope({ id: 'shared', layer: 'installed' })
  const calls = []
  const api = async (method, path) => {
    calls.push(`${method} ${path}`)
    if (path.startsWith('/files/stat')) return doc
    if (path.startsWith('/files/link')) return { url: '/files/vault/Notes/plan.md', kind: 'Loam link' }
    return {}
  }
  const files = createFiles({ api, registry, schedule() {}, timers: { set: () => 1, clear() {} } })
  const side = { opened: [], available: () => true, visible: () => false, st: {}, affordance: (item) => h('button', { class: 'iconbtn side-open', onclick: () => side.opened.push(item.path) }), openSide: (item) => side.opened.push(item.path) }
  const went = []
  const go = (root, path, opts) => went.push({ root, path, opts })
  go.side = () => side
  go.redraw = () => {}
  return { registry, plugin, files, go, side, went, calls }
}

const find = (node, cls) => node.querySelector(`.${cls}`)
const all = (node, cls) => node.querySelectorAll(`.${cls}`)

test("a plugin root's entry is a folder's own row: tile, label, open beside, the ⋯ menu", async () => {
  const { plugin, files, go, side, went } = setup()
  const opened = []
  plugin.places.files.root.add({
    id: 'shared', name: 'Shared', desc: 'Shared with people',
    render: ({ entry }) => h('div', { class: 'card card-group' }, entry(doc, {
      meta: ['Edit · 3m ago'],
      badge: h('span', { class: 'p-dot' }),
      trail: h('span', { class: 'p-faces', text: 'A' }),
      onopen: (r) => opened.push(r.path),
      className: 'p-entry',
    })),
  })
  files.st.root = '@shared'
  const view = folderView(files, { go, wide: true, home: null })
  const entry = find(view, 'fs-entry')
  assert.ok(entry, 'drawn as a file entry')
  assert.match(entry.getAttribute('class'), /\bp-entry\b/)
  const button = find(entry, 'setrow')
  // The tile is the file glyph with its type label, as in a folder.
  assert.equal(find(button, 'icon-tile').textContent.includes('MD'), true)
  assert.ok(find(find(button, 'icon-tile'), 'p-dot'), 'the badge sits on the tile')
  assert.equal(find(button, 'fs-name').textContent, 'plan.md')
  assert.equal(find(button, 'fs-meta').textContent, 'Edit · 3m ago')
  assert.ok(find(button, 'p-faces'), 'the trail is in the row')
  assert.ok(find(entry, 'row-side-action'), 'open beside, as a folder row has')
  assert.ok(find(entry, 'fs-rowmenu'), 'the ⋯ menu, as a folder row has')

  // A click opens it in the main panel; with Option it opens beside.
  button.fire('click', { altKey: false })
  assert.deepEqual(went.map(w => [w.root, w.path, w.opts.isFile]), [['vault', 'Notes/plan.md', true]])
  button.fire('click', { altKey: true })
  assert.deepEqual(side.opened, ['Notes/plan.md'])
  assert.deepEqual(opened, ['Notes/plan.md', 'Notes/plan.md'])
  find(find(entry, 'row-side-action'), 'side-open').fire('click', {})
  assert.deepEqual(side.opened, ['Notes/plan.md', 'Notes/plan.md'])

  // The ⋯ menu is the resource menu: Open to the side, Rename, Download, Copy link.
  find(entry, 'fs-rowmenu').fire('click', {})
  assert.equal(files.st.sheet.kind, 'resource')
  files.side = () => side
  const sheet = addSheet(files, { close: Object.assign(() => {}, { redraw() {} }) }).filter(Boolean)
  const labels = sheet.slice(1).map(n => n.textContent)
  assert.deepEqual(labels.slice(0, 3), ['Open to the side', 'Rename', 'Download'])
  assert.match(labels[3], /link/i)
})

test('a folder row and a plugin row are drawn by the same code', () => {
  const { plugin, files, go } = setup()
  plugin.places.files.root.add({ id: 'shared', name: 'Shared', render: ({ entry }) => h('div', { class: 'card card-group' }, entry(doc)) })
  files.st.root = '@shared'
  const mine = find(folderView(files, { go, wide: true }), 'fs-entry')
  files.st.root = 'vault'
  files.st.path = 'Notes'
  files.st.listing = { ...files.st.listing, rows: [doc] }
  const theirs = find(folderView(files, { go, wide: true }), 'fs-entry')
  const shape = (n) => [n.getAttribute('class'), ...n.childNodes.map(c => c.nodeType === 1 ? c.getAttribute('class') : '#')].join('|')
  assert.equal(shape(mine), shape(theirs))
  assert.equal(find(mine, 'fs-meta').textContent, find(theirs, 'fs-meta').textContent, 'with no meta given, the size and age')
})

test("a plugin's menu item is first in a file's ⋯ menu, closes it, then runs", () => {
  const { plugin, files } = setup()
  const ran = []
  plugin.places.files.action.add({
    id: 'share', slot: 'menu',
    when: (c) => c.resource?.path?.endsWith('.md'),
    render: () => ({ id: 'share', label: 'Share…', icon: 'link', run: (r) => ran.push(r.path) }),
  })
  // An item that answers nothing adds nothing.
  plugin.places.files.action.add({ id: 'none', slot: 'menu', render: () => null })
  files.resourceMenu(doc, null)
  let redrawn = 0
  const sheet = addSheet(files, { close: Object.assign(() => {}, { redraw() { redrawn++ } }) }).filter(Boolean)
  assert.equal(sheet[1].textContent, 'Share…')
  assert.match(sheet[1].getAttribute('class'), /\bfs-menu-plugin\b/)
  sheet[1].fire('click', {})
  assert.equal(files.st.sheet, null)
  assert.equal(redrawn, 1)
  assert.deepEqual(ran, ['Notes/plan.md'])

  // Not on a file it does not want.
  files.resourceMenu(row('vault', 'Notes/a.txt'), null)
  const plain = addSheet(files, { close: Object.assign(() => {}, { redraw() {} }) }).filter(Boolean)
  assert.equal(plain.some(n => /fs-menu-plugin/.test(n.getAttribute?.('class') ?? '')), false)
  // The header's own action slot is not drawn into the menu.
  assert.equal(files.registry.draw('files.action', { slot: 'menu', files, resource: doc }).length, 1)
})

test('stat and readText are the host routes a plugin root needs', async () => {
  const { files, calls } = setup()
  assert.equal((await files.stat('vault', 'Notes/plan.md')).name, 'plan.md')
  assert.equal(calls.at(-1), 'GET /files/stat?root=vault&path=Notes%2Fplan.md')
  assert.equal(typeof files.ago('2026-10-01T10:00:00Z'), 'string')
})
