import test from 'node:test'
import assert from 'node:assert/strict'
import { createCoreRegistry } from '../public/core-registrations.js'

test('plugins and core share removable places, commands, events and declared whole-component overrides', () => {
  const registry = createCoreRegistry(), plugin = registry.scope({ id: 'proof', overrides: ['core.message'] })
  const registration = plugin.places.rail.section.add({ id: 'proof', title: 'Proof', render: () => 'a place' })
  const command = plugin.commands.register({ id: 'proof.open', run: context => context.id })
  let count = 0
  plugin.events.on('message.arrived', () => count++)
  plugin.places.message.renderer.add({ id: 'proof.message', component: 'core.message', render: context => `Replaced ${context.args[0]}` })
  assert.equal(registry.component('core.message', { args: ['answer'], defaultView: () => 'default' }), 'Replaced answer')
  assert.equal(plugin.commands.run('proof.open', { id: 'example' }), 'example')
  registry.emit('message.arrived', {}); assert.equal(count, 1)
  registration.remove(); registration.remove(); assert.equal(registry.entries('rail.section').length, 0)
  command.remove(); assert.throws(() => plugin.commands.run('proof.open'), /Unknown command/)
  plugin.dispose(); registry.emit('message.arrived', {}); assert.equal(count, 1)
  assert.equal(registry.component('core.message', { defaultView: () => 'default' }), 'default')
})

test('a broken renderer releases all its registrations and restores core without stopping another plugin', () => {
  const failures = [], registry = createCoreRegistry({ failed: (id, error) => failures.push([id, error.message]) })
  const broken = registry.scope({ id: 'broken', overrides: ['core.message'] }), good = registry.scope({ id: 'good' })
  broken.places.message.renderer.add({ id: 'bad.message', component: 'core.message', render: () => { throw new Error('A broken renderer') } })
  broken.places.app.overlay.add({ id: 'bad.overlay', render: () => 'bad overlay' })
  good.places.app.overlay.add({ id: 'good.overlay', render: () => 'good overlay' })
  assert.equal(registry.component('core.message', { defaultView: () => 'default' }), 'default')
  assert.deepEqual(failures, [['broken', 'A broken renderer']])
  assert.deepEqual(registry.draw('app.overlay', { slot: 'overlay' }), ['good overlay'])
  assert.equal(registry.component('core.message', { defaultView: () => 'default' }), 'default')
})

test('reserved places stay reserved and undeclared component replacements are allowed, and footer transforms compose', () => {
  const registry = createCoreRegistry(), plugin = registry.scope({ id: 'example' })
  assert.equal(plugin.places.thread.kind, undefined)
  plugin.places.message.renderer.add({ id: 'custom', component: 'core.message', render: () => 'custom' })
  assert.equal(registry.component('core.message', { defaultView: () => 'default' }), 'custom')
  plugin.places.message.footer.action.add({ id: 'extra', slot: 'row', order: 10, when: c => c.slot === 'row', render: c => [...c.view, 'extra'] })
  assert.deepEqual(registry.reduce('message.footer.action', { slot: 'row', defaultView: () => ['base'] }, []), ['base', 'extra'])
})

test('the Chats line falls through empty registrations and releases its place on disable', () => {
  const registry = createCoreRegistry()
  const a = registry.scope({ id: 'almanac' }), b = registry.scope({ id: 'journal' })
  let quiet = true
  a.places['thread.list.line'].add({ id: 'arrival', render: () => quiet ? null : 'new arrival' })
  b.places['thread.list.line'].add({ id: 'today', render: () => 'today' })
  assert.equal(registry.draw('thread.list.line')[0], 'today')
  quiet = false
  assert.equal(registry.draw('thread.list.line')[0], 'new arrival')
  a.dispose()
  assert.equal(registry.draw('thread.list.line')[0], 'today')
})

test('thread controls share the header without duplicating existing below-header actions', () => {
  const registry = createCoreRegistry()
  const plugin = registry.scope({ id: 'notes' })
  plugin.places.thread.header.action.add({ id: 'extra', render: () => 'below the header' })
  plugin.places.thread.header.action.add({ id: 'note', slot: 'control', render: ({ placement }) => placement })
  assert.deepEqual(registry.draw('thread.header.action', { slot: 'below' }), ['below the header'])
  assert.deepEqual(registry.draw('thread.header.action', { slot: 'control', placement: 'menu' }), ['menu'])
  plugin.dispose()
  assert.deepEqual(registry.draw('thread.header.action', { slot: 'control', placement: 'header' }), [])
})

test('Files keeps the projection used by an open edit and reselects it on the next open', async () => {
  const { createFiles } = await import('../public/files.js')
  const registry = createCoreRegistry()
  const plugin = registry.scope({ id: 'notes' })
  const row = { root: 'vault', path: 'notes.md', name: 'notes.md', kind: 'file', capabilities: { read: true, write: true } }
  let raw = 'keep\nold'
  plugin.places.files.viewer.add({ id: 'note-body', slot: 'projection',
    when: ({ root, path }) => root === row.root && path === row.path,
    render: () => ({
      show: text => text.slice(text.indexOf('\n') + 1),
      write: (base, text) => base.split('\n')[0] + '\n' + text,
      offsets: raw => {
        const start = raw.indexOf('\n') + 1
        return { toSource: offset => offset + start, fromSource: offset => Math.max(0, offset - start) }
      },
    }),
  })
  const files = createFiles({ registry, schedule() {}, api: async (method, path, body) => {
    if (method === 'PUT') {
      assert.equal(body.base_revision, 'original')
      raw = body.text
    }
    return { ...row, text: raw, revision: method === 'GET' ? 'original' : 'saved' }
  } })
  await files.openFile(row.root, row.path, row, { mode: 'edit' })
  assert.equal(files.st.file.text, 'old')
  plugin.dispose()
  files.edit('new')
  await files.save()
  assert.equal(raw, 'keep\nnew')
  files.closeFile()
  await files.openFile(row.root, row.path, row, { mode: 'edit' })
  assert.equal(files.st.file.text, raw)
  files.closeFile()
})

test('rail.action renders in the desktop rail and cleans up on removal, and thread.list.action renders in the Chats header', async () => {
  const { installDom } = await import('./fakedom.js')
  const { all } = await import('./browser.js')
  const { h, icon } = await import('../public/ui.js')
  const { mount: mountLayout } = await import('../public/page/layout.js')
  const { createThreads } = await import('../public/threads.js')
  installDom()
  const registry = createCoreRegistry()
  const core = registry.scope({ id: '@core' })
  core.places.rail.section.add({ id: 'chats', title: 'Chats', icon: 'chat' })
  const plugin = registry.scope({ id: 'scratchpad' })
  let open = false, clicks = 0
  const railHandle = plugin.places.rail.action.add({
    id: 'open', title: 'Pad', label: 'Open scratchpad', icon: () => icon.file(20),
    active: () => open, badge: () => 2, onclick: () => { clicks++; open = !open },
  })
  const host = {
    registry, state: { navigator: 'chats', sheet: null, collapsed: false },
    commands: { run() {} }, openSheet() {}, goActivity() {}, schedule() {},
    store: {}, workspaceHistory: { state: { front: 'list' } }, files: { st: {} },
    threads: { st: {} }, panels: { visible: () => false, st: {}, focused: () => 'main' },
  }
  const layout = mountLayout(host)
  let rail = layout.rail()
  assert.equal(all(rail, 'rail-sep').length, 1)
  const btn = all(rail, 'railaction')[0]
  assert.ok(btn)
  assert.equal(btn.getAttribute('aria-label'), 'Open scratchpad')
  assert.equal(btn.getAttribute('aria-expanded'), 'false')
  assert.equal(all(btn, 'navdot').length, 1)
  btn.fire('click')
  assert.equal(clicks, 1)
  rail = layout.rail()
  assert.equal(all(rail, 'railaction')[0].getAttribute('aria-expanded'), 'true')
  assert.ok(all(rail, 'railaction')[0].className.includes('on'))
  railHandle.remove()
  rail = layout.rail()
  assert.equal(all(rail, 'rail-sep').length, 0)
  assert.equal(all(rail, 'railaction').length, 0)

  const listHandle = plugin.places.thread.list.action.add({
    id: 'pad', render: ({ wide }) => h('button', { class: 'iconbtn pad-list-btn', 'data-wide': String(wide) }, 'P'),
  })
  const threads = createThreads({ registry, api: async () => ({ threads: [], agents: [] }), redraw() {}, agents: () => [] })
  const listView = threads.list(false)
  const head = all(listView, 'home-head')[0]
  const padBtns = all(head, 'pad-list-btn')
  assert.equal(padBtns.length, 1)
  assert.equal(padBtns[0].getAttribute('data-wide'), 'false')
  // Omitted in Archived view.
  threads.showArchived(true)
  assert.equal(all(threads.list(false), 'pad-list-btn').length, 0)
  listHandle.remove()
})
