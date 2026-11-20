import test from 'node:test'
import assert from 'node:assert/strict'
import { promisify } from 'node:util'
import { execFile } from 'node:child_process'
import { createPanels, itemKey, readPanels } from '../public/panels.js'
import { createThreads } from '../public/threads.js'
import { installDom } from './fakedom.js'
import { createCoreRegistry } from '../public/core-registrations.js'
import { registerPage } from '../public/page/registrations.js'
import { mount as mountLayout } from '../public/page/layout.js'

const thread = id => ({ kind: 'thread', id })
const file = path => ({ kind: 'file', root: 'vault', path })
function fixture() {
  const held = new Map(), storage = { getItem: key => held.get(key), setItem: (key, value) => held.set(key, value) }
  const endpoint = initial => {
    let item = initial
    return { item: () => item, open: value => { item = value }, clear: () => { item = null }, guard: () => true }
  }
  let width = 1440
  const main = endpoint(thread('main')), side = endpoint(null)
  const panels = createPanels({ main, side, width: () => width, changed() {}, storage })
  return { panels, main, side, storage, width: value => { width = value } }
}
test('plain list navigation changes main while the explicitly chosen side stays put', () => {
  const { panels, main, side } = fixture()
  panels.openSide(file('Notes/plan.md'))
  panels.openMain(thread('another'))
  assert.deepEqual(main.item(), thread('another'))
  assert.deepEqual(side.item(), file('Notes/plan.md'))
})
test('the side gesture fills the side; opening its item in main moves it and closes side', () => {
  const { panels, main, side } = fixture(), chosen = file('Notes/plan.md')
  panels.openSide(chosen)
  assert.deepEqual(side.item(), chosen); assert.deepEqual(main.item(), thread('main'))
  panels.openMain(chosen)
  assert.deepEqual(main.item(), chosen); assert.equal(side.item(), null); assert.equal(panels.visible(), false)
})
test('sending main to an occupied side swaps; sending it to an empty side leaves main empty', () => {
  const { panels, main, side } = fixture()
  panels.openSide(file('Notes/plan.md')); panels.openSide(thread('main'))
  assert.deepEqual(main.item(), file('Notes/plan.md')); assert.deepEqual(side.item(), thread('main'))
  panels.close(); panels.openSide(main.item())
  assert.equal(main.item(), null); assert.deepEqual(side.item(), file('Notes/plan.md'))
})
test('promote and swap preserve at most two different items', () => {
  const { panels, main, side } = fixture()
  panels.openSide(thread('other')); panels.swap()
  assert.deepEqual(main.item(), thread('other')); assert.deepEqual(side.item(), thread('main'))
  assert.notEqual(itemKey(main.item()), itemKey(side.item()))
  panels.promote(); assert.deepEqual(main.item(), thread('main')); assert.equal(side.item(), null)
})
test('a thread still being created cannot be swapped into a nonexistent side record', () => {
  const { panels, main, side } = fixture()
  panels.openSide(thread('other')); main.pending = () => true
  assert.equal(panels.swap(), false); assert.equal(panels.openSide(main.item()), false)
  assert.deepEqual(main.item(), thread('main')); assert.deepEqual(side.item(), thread('other'))
})
test('below 1180 there is no side operation; a held side goes and comes back without changing main', () => {
  const { panels, main, side, width } = fixture()
  panels.openSide(thread('other')); width(1179)
  assert.equal(panels.available(), false); assert.equal(panels.visible(), false)
  assert.equal(panels.openSide(thread('unwanted')), false)
  assert.deepEqual(main.item(), thread('main')); assert.deepEqual(side.item(), thread('other'))
  width(1180); assert.equal(panels.visible(), true); assert.deepEqual(side.item(), thread('other'))
})
test('the sidebar tucks below 1700, explicit Show wins, and automatic tuck ends on close', () => {
  const { panels, width } = fixture()
  panels.openSide(thread('other')); assert.equal(panels.tucked(), true)
  width(1700); assert.equal(panels.tucked(), false)
  width(1699); assert.equal(panels.tucked(), true)
  panels.showSidebar(); assert.equal(panels.tucked(), false)
  panels.close(); assert.equal(panels.tucked(), false)
  panels.openSide(thread('other')); assert.equal(panels.tucked(), true)
  panels.close(); assert.equal(panels.tucked(), false)
})
test('choosing a rail section while tucked shows its list and keeps the side item', () => {
  for (const id of ['files', 'chats', 'apps']) {
    const { panels, side } = fixture(), registry = createCoreRegistry()
    panels.openSide(thread('other'))
    const host = { registry, panels, state: { wide: true, collapsed: panels.tucked(), screen: 'threads' },
      store: { sidebarHidden: false }, schedule() {}, closeSheet() {},
      threads: { st: { open: { thread: { id: 'main' } } }, load() {} },
      files: { st: { roots: { loaded: true } } }, apps: { st: { loaded: true } },
      goFiles: () => assert.fail('a rail choice must not replace the main document'),
      goChats: () => assert.fail('a rail choice must not close the main thread'),
      goApps: () => assert.fail('a rail choice must not clear main for the catalog') }
    Object.assign(host, mountLayout(host))
    const scope = registry.scope({ id: '@core' })
    registerPage({ ...scope, ui: { host } })
    assert.equal(host.state.collapsed, true)
    scope.commands.run('section.open', { id })
    assert.equal(host.state.screen, 'threads')
    assert.equal(host.state.navigator, id)
    assert.deepEqual(host.threads.st.open, { thread: { id: 'main' } })
    assert.equal(host.state.collapsed, false, `${id} must reveal its list in the same action`)
    assert.equal(panels.tucked(), false, 'the explicit rail choice must survive the next redraw')
    assert.deepEqual(side.item(), thread('other'))
    scope.dispose()
  }
})
test('reload restores side and split using an address descriptor without file capabilities', () => {
  const f = fixture()
  f.panels.openSide({ ...file('Notes/plan.md'), row: { revision: 'stale', capabilities: { write: true } } }); f.panels.split(61)
  const saved = readPanels(f.storage)
  assert.deepEqual(saved.side, file('Notes/plan.md'))
  assert.equal(saved.split, 61)
  const restored = createPanels({ ...f, width: () => 1440, changed() {} })
  f.side.clear(); restored.restore()
  assert.deepEqual(f.side.item(), file('Notes/plan.md')); assert.equal(restored.st.split, 61)
  restored.split(1); assert.equal(restored.st.split, 32)
  restored.split(99); assert.equal(restored.st.split, 68)
})
test('desktop core list commands preserve a manually focused file or app; phone commands keep their navigation', () => {
  for (const kind of ['files', 'apps']) {
    const registry = createCoreRegistry(), navigated = [], document = { path: 'Notes/plan.md', url: 'https://garden.example/' }
    const host = { registry, state: { wide: true, screen: kind, collapsed: true },
      store: { sidebarHidden: true }, schedule() {}, closeSheet() {},
      threads: { st: { open: null }, load() {} },
      files: { st: { file: kind === 'files' ? document : null, roots: { loaded: true } } },
      apps: { st: { selected: kind === 'apps' ? document : null, loaded: true } },
      goChats: () => navigated.push('chats'), goFiles: () => navigated.push('files'), goApps: () => navigated.push('apps') }
    Object.assign(host, mountLayout(host))
    const scope = registry.scope({ id: '@core' })
    registerPage({ ...scope, ui: { host } })
    for (const id of ['chats', 'files', 'apps']) {
      host.state.collapsed = true; host.store.sidebarHidden = true
      scope.commands.run(`core.${id}`)
      assert.equal(host.state.screen, kind)
      assert.equal(host.state.navigator, id)
      assert.equal(host.state.collapsed, false)
      assert.equal(host.store.sidebarHidden, false)
      assert.equal(kind === 'files' ? host.files.st.file : host.apps.st.selected, document)
    }
    assert.deepEqual(navigated, [])
    host.state.wide = false
    for (const id of ['chats', 'files', 'apps']) scope.commands.run(`core.${id}`)
    assert.deepEqual(navigated, ['chats', 'files', 'apps'])
    scope.dispose()
  }
})
test('a browser refusing device storage still opens a usable panel', () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new Error('Storage is blocked') } })
  try {
    assert.equal(readPanels().side, null)
    const f = fixture(), panels = createPanels({ main: f.main, side: f.side, changed() {}, width: () => 1440 })
    assert.equal(panels.openSide(thread('other')), true)
    assert.deepEqual(f.side.item(), thread('other'))
  } finally { if (previous) Object.defineProperty(globalThis, 'localStorage', previous); else delete globalThis.localStorage }
})
test('an unsaved edit holds the navigation and its original panel until its guard proceeds', () => {
  const { panels, main, side } = fixture()
  panels.openSide(file('Notes/plan.md'))
  let retry
  side.guard = go => { retry = go; return false }
  assert.equal(panels.openMain(file('Notes/plan.md')), false)
  assert.deepEqual(main.item(), thread('main')); assert.deepEqual(side.item(), file('Notes/plan.md'))
  side.guard = () => true; retry()
  assert.deepEqual(main.item(), file('Notes/plan.md')); assert.equal(side.item(), null)
})
test('moving a thread carries unsaved Board edits, attachments and its reading anchor without old prose callbacks', t => {
  installDom()
  const field = { value: 'A draft to keep' }, attachment = { path: 'attachments/note.md', name: 'note.md' }
  const source = createThreads({ api: async () => ({}), redraw() {}, agents: () => [], dom: { querySelector: () => null },
    compose: { field: () => field, files: () => [attachment], leave() {} } })
  t.after(() => source.pause())
  source.st.wide = true
  source.st.open = { thread: { id: 'moving', members: [] }, messages: [{ id: 'reply', author: 'agent', text: 'Kept prose', steps: { local: 'old view' } }],
    board: { markdown: '# Board\n', path: 'boards/moving.md' }, editing: true, editText: '# An unsaved Board\n', editBase: '# Board\n',
    readingPosition: { id: 'reply', offset: 73 }, atEnd: false, has_more: true, openingUnread: 0, replyTo: 'reply' }
  let reading
  const destination = { st: { drafts: new Map(), boardDrafts: new Map(), quickDrafts: new Map() }, receiveVisit: (id, visit) => { assert.equal(id, 'moving'); reading = visit } }
  source.transferTo(destination, 'moving'); source.close({ navigate: false })
  assert.deepEqual(destination.st.drafts.get('moving'), { text: 'A draft to keep', attachments: [attachment], replyTo: 'reply' })
  assert.deepEqual(destination.st.boardDrafts.get('moving'), { text: '# An unsaved Board\n', base: '# Board\n' })
  assert.deepEqual(reading.anchor, { id: 'reply', offset: 73 }); assert.equal(reading.atEnd, false)
  assert.deepEqual(reading.messages, [{ id: 'reply', author: 'agent', text: 'Kept prose' }])
  assert.equal(Object.hasOwn(reading, 'proseCache'), false)
  assert.equal(source.dirty(), false, 'the vacated view must not retain an unsaved copy')
})
test('two live threads keep their streams, composers, width rules and reload state in Chromium', { skip: !process.env.LOAM_PLAYWRIGHT, timeout: 240000 }, async t => {
  const { stdout } = await promisify(execFile)(process.execPath, ['test/panels-browser.mjs'], { timeout: 230000, maxBuffer: 1024 * 1024 })
  assert.equal(JSON.parse(stdout).result, 'passed'); t.diagnostic('Chromium panel interaction and theme matrix passed')
})
test('Latte paint follows panel interaction across threads, files and embedded apps', { skip: !process.env.LOAM_PLAYWRIGHT, timeout: 120000 }, async t => {
  const { stdout } = await promisify(execFile)(process.execPath, ['test/panel-focus-browser.mjs'], { timeout: 110000, maxBuffer: 1024 * 1024 })
  assert.match(stdout, /PASS 1440 light/); assert.match(stdout, /PASS 1440 dark/)
  t.diagnostic(stdout.trim())
})

test('side history truncates a forward branch, skips main, and ends when the pane closes', () => {
  const { panels, main, side } = fixture()
  const first = thread('one'), second = thread('two'), third = thread('three')
  panels.openSide(first)
  assert.equal(panels.canSideBack(), false)
  panels.openSide(second); panels.openSide(third)
  panels.openMain(second)
  assert.ok(panels.goSide(-1))
  assert.equal(itemKey(side.item()), itemKey(first))
  assert.equal(itemKey(main.item()), itemKey(second))
  assert.ok(panels.goSide(1))
  assert.equal(itemKey(side.item()), itemKey(third))
  panels.goSide(-1); panels.openSide(file('Notes/outline.md'))
  assert.equal(panels.goSide(1), false)
  panels.close(); panels.openSide(first)
  assert.equal(panels.canSideBack(), false)
})

test('panel history restores reading positions in the browser and keeps edits behind the save guard', { skip: !process.env.LOAM_PLAYWRIGHT, timeout: 120000 }, async t => {
  const { stdout } = await promisify(execFile)(process.execPath, ['test/panel-history-browser.mjs'], { timeout: 110000 })
  assert.match(stdout, /390 dark passed/)
  t.diagnostic(stdout.trim())
})
