import test from 'node:test'
import assert from 'node:assert/strict'
import { createThreads } from '../public/threads.js'
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
