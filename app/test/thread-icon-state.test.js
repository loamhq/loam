import test from 'node:test'
import assert from 'node:assert/strict'
import { threadIconState } from '../public/thread-identity.js'
import { createThreads } from '../public/threads.js'
import { installDom } from './fakedom.js'
import { all, find } from './browser.js'
import { h } from '../public/ui.js'

const member = (persona, extra = {}) => ({ persona, engine: 'claude-code', working: false, background: 0, ...extra })
const record = (members, extra = {}) => ({ id: 'sample', title: 'A shared thought', icon: null, default: 'sprout', members, ...extra })
test('one icon layer: asking outranks generation; waiting on tasks and quiet are still', () => {
  const thread = record([member('sprout', { working: true, background: 2 }), member('quill', { background: 1 })])
  assert.deepEqual(threadIconState(thread), { kind: 'working', persona: 'sprout' })
  assert.equal(threadIconState(thread, { pending: [{ persona: 'quill' }] }).kind, 'asking')
  for (const extra of [{ asking: true }, { pending: [{}] }, { pending_permissions: [{}] }]) {
    assert.equal(threadIconState(record([member('sprout', { ...extra, working: true, background: 2 })])).kind, 'asking')
  }
  assert.equal(threadIconState(thread, { resident: () => ({ state: 'waiting' }) }).kind, 'asking')
  thread.members[1].working = true
  assert.equal(threadIconState(thread).persona, null, 'several generators use neutral tone')
  thread.members.forEach((m) => { m.working = false })
  assert.equal(threadIconState(thread).kind, 'rest')
  thread.members.forEach((m) => { m.background = 0; m.busy = true })
  assert.equal(threadIconState(thread).kind, 'rest', 'explicit false wins over busy')
  assert.equal(threadIconState(record([member('sprout')], { background: 2 })).kind, 'rest', 'aggregate-only reading')
})
test('legacy generation matches the subtitle rule, including parked members', () => {
  assert.equal(threadIconState(record([{ persona: 'sprout', busy: true }])).kind, 'working')
  assert.equal(threadIconState(record([{ persona: 'sprout', busy: true, background: 1 }])).kind, 'rest')
})
test('the list, header and Details keep the same base and layer, without decorating default corner faces', (t) => {
  installDom(); globalThis.innerWidth = 1440; globalThis.innerHeight = 1000
  const row = record([member('sprout', { working: true })])
  const threads = createThreads({ api: async () => row, redraw() {}, agents: () => [{ id: 'sprout', name: 'Sprout' }], rail: () => null, nav: () => null,
    compose: { field: () => ({ value: '' }), render: () => h('div'), dictating: () => false, files: () => [], dragProps: () => ({}) } })
  t.after(() => threads.pause())
  threads.st.threads = [row]
  threads.st.open = { thread: row, messages: [], permissions: new Map(), typing: new Set(), turns: [] }
  const view = () => threads.view(true)
  let tree = view()
  assert.ok(find(find(tree, 'tt-row'), 'tt-thread-icon').className.includes('blob'))
  assert.equal(all(find(tree, 'tt-head'), 'tt-icon-working').length, 1)
  assert.equal(all(find(tree, 'tt-row'), 'tt-default-face').length, 0, 'the main solo face already identifies the default')
  row.icon = 'glyph:code'
  tree = view()
  assert.ok(find(find(tree, 'tt-row'), 'tt-thread-icon').className.includes('thread-tile'))
  threads.st.manage = true
  tree = view()
  const details = find(tree, 'tt-details-icon')
  assert.equal(all(details, 'tt-icon-layer').length, 1)
  assert.equal(find(details, 'tt-thread-icon').__css.width, '68px')
  for (const base of [null, 'glyph:code']) for (const state of ['waiting', 'working', 'asking', 'quiet']) {
    row.icon = base
    Object.assign(row.members[0], { working: state === 'working' || state === 'asking', asking: state === 'asking', background: state === 'quiet' ? 0 : 2 })
    const expected = state === 'working' || state === 'asking' ? 1 : 0
    for (const surface of ['tt-row', 'tt-head', 'tt-details-icon']) {
      threads.st.manage = surface === 'tt-details-icon'
      const face = find(find(view(), surface), 'tt-thread-icon')
      assert.equal(all(face, 'tt-icon-layer').length, expected, `${base} ${state} ${surface}`)
      assert.equal(face.getAttribute('data-icon-state'), expected ? state : 'rest')
      assert.match(face.getAttribute('aria-label'), new RegExp(state === 'waiting' ? 'Sprout waiting' : state === 'quiet' ? 'Quiet' : state === 'asking' ? 'Sprout is asking you' : 'Sprout working'))
    }
  }
})
