import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { all, find, text } from './browser.js'
import { h } from '../public/ui.js'
import { createFiles, folderView } from '../public/files.js'
import { createThreads } from '../public/threads.js'
import { createActivity } from '../public/activity.js'

installDom()
test('Files waits for a successful roots answer before declaring shortcuts empty', () => {
  const files = createFiles({ api() {}, schedule() {} })
  const view = () => folderView(files, { go() {}, wide: false })
  assert.match(text(view()), /Loading/)
  assert.equal(find(view(), 'fs-shortcuts'), null)
  files.st.roots = { loaded: true, error: 'Unavailable', items: [], shortcuts: [] }
  assert.equal(find(view(), 'fs-shortcuts'), null)
  files.st.roots.error = ''
  assert.match(text(view()), /Nothing pinned yet/)
  files.st.roots.shortcuts = [{ label: 'Notes', resource: { root: 'vault', path: 'Notes', kind: 'folder' } }]
  assert.doesNotMatch(text(view()), /Nothing pinned yet/)
  assert.equal(all(view(), 'fs-host-shortcut').length, 1)
})

test('loading Chats has no instruction about an agent roster that has not arrived', () => {
  const threads = createThreads({ api() {}, redraw() {}, agents: () => [], rail: () => null, nav: () => null })
  threads.st.loading = true
  assert.match(text(threads.list()), /Opening chats/)
  assert.doesNotMatch(text(threads.list()), /Pick an agent/)
  threads.st.loading = false
  assert.match(text(threads.list()), /Pick an agent above or start a thread/)
  threads.pause()
})

test('Activity thread previews and their tooltips share the existing Markdown text conversion', () => {
  const markdown = '# Ready\n\n- **Changed:** _simpler_ `system` lines\n- Read [the notes](https://example.org/notes)\n\nMore\n space.'
  const chat = { id: 'one', title: 'A thread', last: { text: markdown } }
  const feed = createActivity({ api() {}, redraw() {}, agents: () => [], avatar: () => h('span'),
    chats: () => ({ pinned: [], recent: [chat] }) })
  const preview = find(feed.view(), 'activity-last-line')
  assert.equal(text(preview), 'Ready Changed: simpler system lines Read the notes More space.')
  assert.equal(preview.getAttribute('title'), text(preview))
})
