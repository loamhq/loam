import test from 'node:test'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'

import assert from 'node:assert/strict'
import { createThreads } from '../public/threads.js'
import { h, kept, morph } from '../public/ui.js'
import { installDom } from './fakedom.js'
import { find } from './browser.js'


test('thread updates retain painted content, styles and editing state at phone and desktop widths', {
  skip: !process.env.LOAM_THREAD_PAINT || !process.env.LOAM_PLAYWRIGHT ? 'Set LOAM_THREAD_PAINT=1 and LOAM_PLAYWRIGHT for frame-level browser checks.' : false,
  timeout: 240000,
}, async t => {
  const { stdout } = await promisify(execFile)(process.execPath, ['test/thread-update-paint-browser.mjs'], { timeout: 230000, maxBuffer: 2 ** 20 })
  t.diagnostic(stdout.trim())
})

// Keep the menu/conversation identity regression in the normal Node suite too.
// Browser checks above additionally catch stylesheet removal and native paint.

for (const wide of [false, true]) test(`thread header menus keep conversation and composer nodes (${wide ? 'desktop' : 'phone'})`, t => {
  installDom(); globalThis.innerWidth = wide ? 1440 : 390
  const field = h('textarea'), root = h('div')
  const row = { id: 'sample', title: 'A shared thought', default: 'sprout', members: [{ persona: 'sprout', engine: 'claude-code' }] }
  const threads = createThreads({ api: async () => row, redraw() {}, agents: () => [{ id: 'sprout', name: 'Sprout' }], rail: () => null, nav: () => null,
    compose: { field: () => field, render: () => h('div', {}, kept(field)), dictating: () => false, files: () => [], dragProps: () => ({}) } })
  t.after(() => threads.pause())
  const o = threads.st.open = { thread: row, messages: [{ id: 'm1', author: 'sprout', text: 'Keep this reply.' }], permissions: new Map(), typing: new Set(), turns: [] }
  const draw = () => morph(root, threads.view(wide))
  draw()
  const conversation = find(root, 'tt-conversation'), stream = find(root, 'tt-stream'), message = find(root, 'tt-message')
  assert.ok(conversation && stream && message)
  field.value = 'Keep this draft'; stream.scrollTop = 123
  for (const [menu, confirm] of [[true, false], [false, false], [true, false], [false, 'header'], [false, false]]) {
    o.headerMenu = menu; o.confirmDelete = confirm; draw()
    assert.equal(find(root, 'tt-conversation'), conversation)
    assert.equal(find(root, 'tt-stream'), stream)
    assert.equal(find(root, 'tt-message'), message)
    assert.equal(stream.scrollTop, 123)
    assert.equal(field.value, 'Keep this draft')
  }
})
