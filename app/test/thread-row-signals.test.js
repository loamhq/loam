import test from 'node:test'
import assert from 'node:assert/strict'
import { createThreads } from '../public/threads.js'
import { createCoreRegistry } from '../public/core-registrations.js'
import { installDom } from './fakedom.js'
import { all, find, text } from './browser.js'
import { h } from '../public/ui.js'

const agents = [{ id: 'sprout', name: 'Sprout' }, { id: 'quill', name: 'Quill' }]
const member = (extra = {}) => ({ persona: 'sprout', engine: 'claude-code', working: false, background: 0, ...extra })
const row = (extra = {}) => ({ id: 'one', title: 'A thought', default: 'sprout', members: [member()], unread: 0, background: 0,
  last: { author: 'sprout', text: 'This preview must stay off the row', at: '2026-11-11T04:30:00Z' }, ...extra })
function setup(t, options = {}) {
  installDom(); globalThis.innerWidth = 1440; globalThis.innerHeight = 1000
  const threads = createThreads({ redraw() {}, agents: () => agents, api: async () => ({}), rail: () => null, nav: () => null,
    compose: { field: () => ({ value: '' }), render: () => h('div'), dictating: () => false, files: () => [], dragProps: () => ({}) },
    ...options })
  t.after(() => threads.pause())
  return threads
}
test('Chats renders rows while the line starts and releases an unused saved height', (t) => {
  const key = 'loam.chats.lineHeight.wide'
  const saved = new Map([[key, '62.265625'], ['loam.chats.hadLine', '1']])
  const storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: name => saved.get(name) ?? null,
    removeItem: name => saved.delete(name),
  } })
  t.after(() => {
    if (storage) Object.defineProperty(globalThis, 'localStorage', storage)
    else delete globalThis.localStorage
  })

  let pending = true
  const threads = setup(t, { linePending: () => pending })
  threads.st.threads = [row()]
  const wide = threads.list(true)
  assert.ok(find(wide, 'tt-row'))
  assert.equal(find(wide, 'tt-chats-line').style.height, '62.265625px')
  assert.equal(find(threads.list(false), 'tt-chats-line').style.height, '0px')
  threads.st.archived = true
  assert.equal(find(threads.list(true), 'tt-chats-line'), null)
  assert.equal(saved.get(key), '62.265625')

  threads.st.archived = false
  pending = false
  assert.equal(find(threads.list(true), 'tt-chats-line').style.height, '0px')
  assert.equal(saved.has(key), false)
})
test('every thread state, including General, puts unread below time and keeps state on the icon', (t) => {
  const threads = setup(t)
  for (const general of [false, true]) for (const [extra, subtitle, layer] of [
    [{ members: [member({ working: true })], working: 1 }, 'Sprout working', 'working'],
    [{ members: [member({ background: 12 })], background: 12 }, 'Sprout waiting', null],
    [{ members: [member({ asking: true, working: true })] }, 'Sprout is asking you', 'asking'],
    [{ unread: 3 }, 'Sprout replied', null],
    [{}, 'Quiet', null],
  ]) {
    threads.st.threads = [row({ ...extra, ...(general ? { id: 'general', title: 'General', general: true, pinned: true } : {}) })]
    const tree = threads.view(true), node = find(tree, 'tt-row')
    for (const removed of ['box', 'box-text', 'beating', 'srow-bg', 'ring', 'breathe']) assert.equal(all(node, removed).length, 0, removed)
    assert.doesNotMatch(text(node), /preview|LATEST|WORKING|DONE|12/)
    assert.equal(text(find(node, 'srow-name')), subtitle === 'Quiet' ? null : subtitle)
    assert.equal(Boolean(find(node, 'tt-quiet')), subtitle === 'Quiet')
    assert.equal(node.getAttribute('aria-description'), subtitle)
    assert.equal(node.getAttribute('data-activity'), subtitle.endsWith(' working') ? 'working' : subtitle.endsWith(' waiting') ? 'waiting' : null)
    assert.equal(all(node, 'tt-unread').length, extra.unread ? 1 : 0)
    assert.equal(all(find(node, 'avatar-wrap'), 'tt-unread').length, 0)
    const time = find(node, 'srow-time')
    assert.equal(all(time, 'tt-unread').length, 0)
    const notification = find(node, 'tt-row-notification')
    assert.equal(all(notification, 'tt-unread').length, extra.unread ? 1 : 0)
    if (extra.unread) assert.equal(find(notification, 'tt-unread').getAttribute('aria-label'), 'Unread')
    assert.equal(all(node, 'tt-icon-layer').length, layer ? 1 : 0)
    if (layer) assert.equal(all(node, `tt-icon-${layer}`).length, 1)
    assert.ok(find(node, 'srow-time').childNodes.some((n) => n.nodeName.toLowerCase() === 'time'))
    assert.equal(all(tree, 'pinned-card').length, 0)
  }
})
test('rows keep their main icon and name the default accessibly without a trailing face', (t) => {
  const threads = setup(t)
  for (const extra of [{}, { members: [member(), member({ persona: 'quill' })] }, { icon: 'glyph:code' }, { id: 'general', general: true }, { default: null }]) {
    threads.st.threads = [row(extra)]
    const node = find(threads.view(true), 'tt-row')
    assert.equal(all(node, 'tt-default-face').length, 0)
    assert.equal(text(find(node, 'sr-only')), extra.default === null ? null : 'Default: Sprout')
    assert.ok(find(node, 'avatar-wrap'))
  }
})
test('the header still opens named background tasks while the selected list row has no count', (t) => {
  const threads = setup(t), record = row({ members: [member({ background: 2, background_tasks: [{ id: 'a', description: 'Review the notes' }] })], background: 2 })
  threads.st.threads = [record]
  threads.st.open = { thread: record, messages: [], permissions: new Map(), typing: new Set(), turns: [] }
  let tree = threads.view(true)
  assert.equal(text(find(find(tree, 'tt-head'), 'tt-task-count')), '2')
  assert.equal(all(find(tree, 'tt-row'), 'srow-bg').length, 0)
  const control = find(tree, 'tt-tasks-button')
  control.getBoundingClientRect = () => ({ left: 200, right: 240, bottom: 60 })
  control.fire('click')
  tree = threads.view(true)
  assert.equal(all(tree, 'tt-tasks-panel').length, 1)
  assert.match(text(find(tree, 'tt-tasks-panel')), /Review the notes/)
})

test('thread.header.action reserves remembered width per thread and layout while pending and reserves nothing without a contribution', (t) => {
  const wideKey = 'loam.thread.headerActions.wide'
  const compactKey = 'loam.thread.headerActions.compact'
  const saved = new Map([
    [wideKey, JSON.stringify({ one: 38 })],
    [compactKey, JSON.stringify({ one: 44 })],
  ])
  const storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: name => saved.get(name) ?? null,
    setItem: (name, val) => saved.set(name, String(val)),
    removeItem: name => saved.delete(name),
  } })
  t.after(() => {
    if (storage) Object.defineProperty(globalThis, 'localStorage', storage)
    else delete globalThis.localStorage
  })

  let pending = true
  const threads = setup(t, { headerActionPending: () => pending })
  const withControl = row({ id: 'one', title: 'With control' })
  const withoutControl = row({ id: 'two', title: 'Without control' })
  threads.st.threads = [withControl, withoutControl]

  // Pending on a thread with a remembered contribution: reserves exact width per layout.
  threads.st.open = { thread: withControl, messages: [], permissions: new Map(), typing: new Set(), turns: [] }
  const widePending = find(threads.view(true), 'tt-header-actions')
  assert.ok(widePending)
  assert.equal(widePending.getAttribute('aria-hidden'), 'true')
  assert.equal(widePending.style.width, '38px')
  const compactPending = find(threads.view(false), 'tt-header-actions')
  assert.ok(compactPending)
  assert.equal(compactPending.style.width, '44px')

  // Pending on a thread with no remembered contribution: reserves nothing (no wrapper, no extra gap).
  threads.st.open = { thread: withoutControl, messages: [], permissions: new Map(), typing: new Set(), turns: [] }
  assert.equal(find(threads.view(true), 'tt-header-actions'), null)
  assert.equal(find(threads.view(false), 'tt-header-actions'), null)

  // Settled with no contribution clears stale storage and renders nothing.
  pending = false
  threads.st.open = { thread: withControl, messages: [], permissions: new Map(), typing: new Set(), turns: [] }
  assert.equal(find(threads.view(true), 'tt-header-actions'), null)
  assert.equal(saved.has(wideKey), false)
  assert.equal(saved.get(compactKey), JSON.stringify({ one: 44 }))
})

test('thread.header.action renders live controls in .tt-header-actions separate from tasks and records ResizeObserver width', (t) => {
  const compactKey = 'loam.thread.headerActions.compact'
  const saved = new Map()
  const storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: name => saved.get(name) ?? null,
    setItem: (name, val) => saved.set(name, String(val)),
    removeItem: name => saved.delete(name),
  } })
  const origRO = globalThis.ResizeObserver
  let observed = null, roCallback = null
  globalThis.ResizeObserver = class {
    constructor(cb) { roCallback = cb }
    observe(el) { observed = el }
    disconnect() { observed = null }
  }
  t.after(() => {
    if (storage) Object.defineProperty(globalThis, 'localStorage', storage)
    else delete globalThis.localStorage
    globalThis.ResizeObserver = origRO
  })

  const registry = createCoreRegistry()
  registry.scope({ id: 'ext' }).places.thread.header.action.add({
    id: 'btn', slot: 'control',
    render: ({ placement }) => placement === 'header' ? h('button', { class: 'iconbtn ghost ext-btn', 'aria-label': 'Extra' }) : null,
  })
  let liveSlot = null
  const threads = setup(t, {
    registry, headerActionPending: () => false,
    dom: { querySelector: sel => sel.includes('tt-header-actions') ? liveSlot : null, querySelectorAll: () => [] },
  })
  const record = row({ id: 't-live', title: 'Live', members: [member({ background: 1, background_tasks: [{ id: 'a', description: 'Work' }] })], background: 1 })
  threads.st.threads = [record]
  threads.st.open = { thread: record, messages: [], permissions: new Map(), typing: new Set(), turns: [] }
  const tree = threads.view(false)
  const slot = find(tree, 'tt-header-actions')
  assert.ok(slot)
  assert.equal(slot.getAttribute('data-header-actions-layout'), 'compact')
  assert.equal(slot.getAttribute('data-header-actions-thread'), 't-live')
  assert.equal(all(slot, 'ext-btn').length, 1)
  assert.equal(all(slot, 'tt-tasks-button').length, 0, 'task indicator stays outside the plugin header-action slot')
  assert.equal(all(find(tree, 'tt-head'), 'tt-tasks-button').length, 1)
  liveSlot = slot
  Object.defineProperty(slot, 'isConnected', { value: true, configurable: true })
  slot.dataset = { headerActionsLayout: 'compact', headerActionsThread: 't-live' }
  slot.getBoundingClientRect = () => ({ width: 44 })
  threads.afterRender()
  assert.equal(observed, slot)
  roCallback([{ target: slot }])
  assert.equal(saved.get(compactKey), JSON.stringify({ 't-live': 44 }))
})
