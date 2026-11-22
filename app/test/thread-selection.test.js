import test from 'node:test'
import assert from 'node:assert/strict'
import { createThreads } from '../public/threads.js'
import { installDom } from './fakedom.js'
import { all, find, text } from './browser.js'
import { h } from '../public/ui.js'

function setup(t) {
  installDom()
  const threads = createThreads({ redraw() {}, agents: () => [], api: async () => ({}), rail: () => null, nav: () => null,
    compose: { field: () => ({ value: '' }), leave() {}, render: () => h('div'), dictating: () => false, files: () => [], dragProps: () => ({}) } })
  threads.st.threads = ['pinned', 'recent'].map((id) => ({ id, title: id, pinned: id === 'pinned', members: [] }))
  t.after(() => threads.pause())
  return threads
}
const select = (threads, id) => { threads.st.open = { thread: threads.st.threads.find((t) => t.id === id), messages: [], permissions: new Map(), typing: new Set(), turns: [] } }

test('pinned and recent are matching unboxed sections; only the open desktop thread is current', (t) => {
  const threads = setup(t)
  for (const id of ['pinned', 'recent']) {
    select(threads, id)
    const tree = threads.view(true), rows = all(tree, 'tt-row')
    assert.equal(all(tree, 'pinned-card').length, 0)
    assert.equal(all(tree, 'tt-section').length, 2)
    assert.deepEqual(all(tree, 'tt-section-heading').map((n) => [n.nodeName, text(n)]), [['H2', 'Pinned'], ['H2', 'Recent']])
    assert.deepEqual(rows.filter((n) => n.className.split(' ').includes('on')).map((n) => n.__key), [id])
    assert.equal(rows.find((n) => n.__key === id).getAttribute('aria-current'), 'page')
    assert.equal(rows.find((n) => n.__key !== id).getAttribute('aria-current'), null)
  }
})

test('phone return highlights the last thread for a second, then clears; pause cancels pending return', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const threads = setup(t)
  threads.view(false); select(threads, 'pinned'); threads.close()
  assert.equal(find(threads.view(false), 'tt-row').className.includes(' on'), true)
  assert.equal(find(threads.view(false), 'tt-row').getAttribute('aria-current'), null)
  t.mock.timers.tick(999)
  assert.equal(threads.st.returning, 'pinned')
  t.mock.timers.tick(1)
  assert.equal(all(threads.view(false), 'on').length, 0)
  select(threads, 'recent'); threads.close({ navigate: false })
  assert.equal(threads.st.returning, 'recent', 'browser Back also highlights its previous thread')
  threads.pause()
  assert.equal(threads.st.returning, null)
  t.mock.timers.tick(1000)
  assert.equal(threads.st.returning, null)
})
