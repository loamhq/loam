import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { createStore } from '../public/store.js'
import { createPanelState, syncPanelState, inspectPanel, resolveFilePanels, renderHostedPanels } from '../public/file-panel.js'
import { createFiles, fileView, filePanelActions } from '../public/files.js'
import { h, kept } from '../public/ui.js'

function memoryStorage() {
  const data = new Map()
  return {
    get length() { return data.size },
    key: (i) => [...data.keys()][i] ?? null,
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => { data.set(k, String(v)) },
    removeItem: (k) => { data.delete(k) },
  }
}

function makeFakeCommentsDom({ loading = false, count = 0, resolved = 0, canComment = true } = {}) {
  const panel = h('section', { class: 'dc-panel', 'aria-label': 'Comments' })
  const head = h('div', { class: 'dc-panel-head' },
    h('div', { class: 'dc-panel-title' },
      h('h2', { text: 'Comments' }),
      count > 0 ? h('span', { class: 'dc-panel-count', text: String(count) }) : null),
    h('div', { class: 'dc-panel-head-end' },
      resolved > 0 ? h('button', { type: 'button', class: 'dc-panel-tog', text: `Resolved (${resolved})` }) : null))
  const body = h('div', { class: 'dc-panel-body' })
  if (loading) {
    body.appendChild(h('div', { class: 'dc-panel-empty', text: 'Loading comments…' }))
  } else if (count === 0) {
    body.appendChild(h('div', { class: 'dc-panel-empty' },
      h('p', { text: canComment ? 'No comments yet. Leave a note on this file below.' : 'No comments yet.' })))
  } else {
    const list = h('div', { class: 'dc-panel-list' })
    for (let i = 0; i < count; i += 1) {
      list.appendChild(h('article', { class: 'dc-pcard' }, h('p', { class: 'dc-tx', text: `Note ${i + 1}` })))
    }
    body.appendChild(list)
  }
  const foot = h('div', { class: 'dc-panel-foot' })
  if (!loading && canComment) {
    foot.appendChild(h('div', { class: 'dc-panel-comp' },
      h('textarea', { class: 'dc-panel-in', rows: '2', placeholder: 'Add a comment…' }),
      h('div', { class: 'dc-panel-comp-bar' },
        h('span', { class: 'dc-panel-hint', text: 'Whole-file comment' }),
        h('button', { type: 'button', class: 'dc-sm primary dc-panel-send', disabled: true }, 'Comment'))))
  }
  panel.appendChild(head)
  panel.appendChild(body)
  panel.appendChild(foot)
  return panel
}

function walk(node, match, out = []) {
  if (!node || typeof node !== 'object') return out
  if (match(node)) out.push(node)
  for (const child of node.childNodes ?? []) walk(child, match, out)
  return out
}

test('store.filePanel persists open/closed choices and rejects unknown values', () => {
  const storage = memoryStorage()
  const store = createStore(storage)
  assert.equal(store.filePanel, null)
  store.filePanel = 'closed'
  assert.equal(store.filePanel, 'closed')
  assert.equal(createStore(storage).filePanel, 'closed')
  store.filePanel = 'open'
  assert.equal(createStore(storage).filePanel, 'open')
  store.filePanel = 'bogus'
  assert.equal(createStore(storage).filePanel, null)
})

test('syncPanelState defaults closed when empty, auto-opens on unresolved notes, and honors device preference', () => {
  const st = createPanelState()

  // Fresh device (pref === null) opens a 0-comment file: loading -> closed, loaded empty -> closed.
  syncPanelState(st, { key: 'vault:a.html', wide: true, info: { title: 'Comments', count: 0, resolved: 0, loading: true, showingResolved: false }, pref: null })
  assert.equal(st.open, false)
  syncPanelState(st, { key: 'vault:a.html', wide: true, info: { title: 'Comments', count: 0, resolved: 0, loading: false, showingResolved: false }, pref: null })
  assert.equal(st.open, false)

  // Fresh device opens a file with 2 unresolved comments: closed while loading, then auto-opens once loaded.
  syncPanelState(st, { key: 'vault:b.pdf', wide: true, info: { title: 'Comments', count: 0, resolved: 0, loading: true, showingResolved: false }, pref: null })
  assert.equal(st.open, false)
  syncPanelState(st, { key: 'vault:b.pdf', wide: true, info: { title: 'Comments', count: 2, resolved: 0, loading: false, showingResolved: false }, pref: null })
  assert.equal(st.open, true)
  assert.equal(st.autoOpened, true)

  // Resolving all comments on b.pdf keeps the panel calmly open during this visit.
  syncPanelState(st, { key: 'vault:b.pdf', wide: true, info: { title: 'Comments', count: 0, resolved: 2, loading: false, showingResolved: false }, pref: null })
  assert.equal(st.open, true)

  // Next empty file on the same fresh device (pref still null) starts closed again.
  syncPanelState(st, { key: 'vault:c.png', wide: true, info: { title: 'Comments', count: 0, resolved: 0, loading: false, showingResolved: false }, pref: null })
  assert.equal(st.open, false)

  // Explicit 'closed' preference suppresses auto-open even when unresolved notes exist.
  syncPanelState(st, { key: 'vault:d.pdf', wide: true, info: { title: 'Comments', count: 3, resolved: 0, loading: false, showingResolved: false }, pref: 'closed' })
  assert.equal(st.open, false)

  // Explicit 'open' preference opens even an empty file.
  syncPanelState(st, { key: 'vault:e.png', wide: true, info: { title: 'Comments', count: 0, resolved: 0, loading: false, showingResolved: false }, pref: 'open' })
  assert.equal(st.open, true)

  // Phone never auto-opens a modal sheet over a newly opened file.
  syncPanelState(st, { key: 'vault:f.pdf', wide: false, info: { title: 'Comments', count: 4, resolved: 0, loading: false, showingResolved: false }, pref: null })
  assert.equal(st.open, false)
})

test('hosted file panel renders unified toggle with count, close button, Escape-to-close, warm empty state, and side-panel actions', () => {
  installDom()
  let pref = null
  let draws = 0
  const files = createFiles({
    api: async () => ({}),
    schedule: () => { draws += 1 },
    panelPref: { get: () => pref, set: (v) => { pref = v } },
  })
  const panelDom = makeFakeCommentsDom({ loading: false, count: 0, resolved: 0 })
  files.registry.scope({ id: 'comments' }).places['files.viewer'].add({
    id: 'comments.panel',
    slot: 'panel',
    when: (c) => c.file && !/\.md$/i.test(c.file.path),
    render: () => ({ action: null, node: kept(panelDom) }),
  })
  files.st.file = {
    root: 'vault', path: 'Notes/demo.html', view: 'html', mode: 'preview',
    row: { root: 'vault', path: 'Notes/demo.html', name: 'demo.html', kind: 'file' },
    caps: { read: true, download: true, write: true },
    loading: false, metadataLoading: false, metadataError: '', error: '', status: null,
    liveLoading: false, liveUrl: 'https://preview.example.com/demo.html', liveError: '',
    revision: 'r1', text: '<p>hi</p>', saved: '<p>hi</p>', draft: null, textLoaded: true,
    saving: false, conflict: null, failed: false, flash: '', shareLink: null,
  }

  // Initial render on desktop: closed by default, toggle present with count 0, empty state polished.
  const view1 = fileView(files, { wide: true, field: () => h('textarea') })
  const toggle1 = walk(view1, n => String(n.className).includes('fs-comments-btn'))[0]
  assert.ok(toggle1)
  assert.equal(toggle1.getAttribute('aria-pressed'), 'false')
  assert.equal(toggle1.getAttribute('aria-label'), 'Comments')
  assert.equal(walk(toggle1, n => String(n.className).includes('fs-comments-badge')).length, 0)
  const aside1 = walk(view1, n => String(n.className).includes('fs-side-panel'))[0]
  assert.equal(String(aside1.className).includes('open'), false)
  assert.equal(aside1.getAttribute('inert'), '')
  // Empty state was warmly replaced with Loam's speech empty state.
  assert.ok(walk(panelDom, n => String(n.className).includes('fs-panel-empty')).length === 1)

  // Clicking toggle opens the panel and persists 'open'.
  toggle1.__on.click({})
  assert.equal(files.st.panel.open, true)
  assert.equal(pref, 'open')
  assert.ok(draws > 0)

  const view2 = fileView(files, { wide: true, field: () => h('textarea') })
  const aside2 = walk(view2, n => String(n.className).includes('fs-side-panel'))[0]
  assert.equal(String(aside2.className).includes('open'), true)
  assert.equal(aside2.getAttribute('inert'), null)

  // Side-panel header actions expose the exact same toggle button.
  const sideBtns = filePanelActions(files, { wide: true, side: true })
  assert.equal(sideBtns.length, 1)
  assert.equal(sideBtns[0].getAttribute('aria-pressed'), 'true')

  // Escape inside panel closes it and persists 'closed'.
  let prevented = false
  aside2.__on.keydown({ key: 'Escape', defaultPrevented: false, target: { closest: () => null }, preventDefault() { prevented = true }, stopPropagation() {} })
  assert.equal(prevented, true)
  assert.equal(files.st.panel.open, false)
  assert.equal(pref, 'closed')

  // Escape inside an inline comment edit field does NOT close the whole panel.
  files.st.panel.open = true
  const view3 = fileView(files, { wide: true, field: () => h('textarea') })
  const aside3 = walk(view3, n => String(n.className).includes('fs-side-panel'))[0]
  aside3.__on.keydown({ key: 'Escape', defaultPrevented: false, target: { closest: (sel) => sel === '.dc-edit-fld' ? {} : null }, preventDefault() {}, stopPropagation() {} })
  assert.equal(files.st.panel.open, true)

  // Close (×) button in panel header closes it and persists 'closed'.
  const closeBtn = walk(view3, n => String(n.className).includes('fs-panel-close'))[0]
  assert.ok(closeBtn)
  closeBtn.__on.click({})
  assert.equal(files.st.panel.open, false)
  assert.equal(pref, 'closed')

  // Phone view wraps the panel in a bottom sheet dialog (.dc-panel-dlg), never a side column.
  const phoneView = fileView(files, { wide: false, field: () => h('textarea') })
  assert.equal(walk(phoneView, n => String(n.className).includes('fs-side-panel')).length, 0)
  assert.equal(walk(phoneView, n => String(n.className).includes('dc-panel-dlg')).length, 1)
  const phoneToggle = walk(phoneView, n => String(n.className).includes('dc-pbtn'))[0]
  assert.ok(phoneToggle)

  // Inspecting a panel with open + resolved comments reads counts accurately.
  const busyDom = makeFakeCommentsDom({ loading: false, count: 3, resolved: 2 })
  const info = inspectPanel({ node: kept(busyDom) })
  assert.equal(info.count, 3)
  assert.equal(info.resolved, 2)
  assert.equal(info.loading, false)
  assert.equal(info.title, 'Comments')

  // All-resolved empty state shows "All caught up".
  const resolvedOnlyDom = makeFakeCommentsDom({ loading: false, count: 0, resolved: 2 })
  files.registry.scope({ id: 'comments2' }).places['files.viewer'].add({
    id: 'comments2.panel',
    slot: 'panel',
    when: (c) => c.file?.path === 'Notes/resolved.png',
    render: () => ({ action: null, node: kept(resolvedOnlyDom) }),
  })
  files.st.file = { ...files.st.file, path: 'Notes/resolved.png', view: 'image' }
  const res = resolveFilePanels(files, files.st.file, { wide: true })
  assert.ok(res.hosted.length >= 1)
  const emptyTitle = walk(resolvedOnlyDom, n => String(n.className).includes('empty-state-title'))[0]
  assert.equal(emptyTitle?.textContent, 'All caught up')
  const rendered = renderHostedPanels(res.hosted, { wide: true, contentParts: [h('div', { text: 'img' })] })
  assert.equal(rendered.length, 1)
})
