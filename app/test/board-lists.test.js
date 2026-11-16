import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { boardSections, boardLines, addBoardItem, moveBoardItem } from '../public/thread-record.js'
import { createThreads } from '../public/threads.js'
import { installDom } from './fakedom.js'
import { all, find, text } from './browser.js'
import { h, morph } from '../public/ui.js'

const buttons = (node) => [node, ...(node?.childNodes ?? []).flatMap(buttons)].filter((n) => n?.nodeName === 'BUTTON')
const source = '# Board\r\n\r\n## First\r\n\r\n- [X] Keep **everything** <!-- Quill · new · until 2027-01-02 · extra -->\r\n- [ ] Second <!-- Sam -->\r\n\r\n### Later\r\n\r\nA note.\r\n\r\n## Empty\r\n\r\n```md\r\n## Not a list\r\n- [ ] Not an item\r\n```\r\n'
const key = (name) => boardSections(source).find((s) => s.name === name).key
test('adding targets an exact heading, retains CRLF/prose/fences, and authors every addition', () => {
  const added = addBoardItem(source, 'Do this', 7, new Date('2026-12-31T23:59:00Z'), '', key('First'))
  assert.equal(added, source.replace('- [ ] Second <!-- Sam -->\r\n', '- [ ] Second <!-- Sam -->\r\n- [ ] Do this <!-- Sam · until 2027-01-07 -->\r\n'))
  assert.equal(addBoardItem(source, 'Start here', 0, new Date(), '', key('Empty')), source.replace('## Empty\r\n', '## Empty\r\n- [ ] Start here <!-- Sam -->\r\n'))
  assert.deepEqual(boardSections(source).map((s) => s.name), ['Board', 'First', 'Later', 'Empty'])
  assert.equal(addBoardItem(source, 'No write', 0, new Date(), '', 'missing'), source)
  assert.equal(addBoardItem('# Board', 'First', 0, new Date(), '', 'top'), '# Board\n- [ ] First <!-- Sam -->\n')
})
test('moves carry original metadata, checkbox, marker and spacing across and within lists', () => {
  const item = boardLines(source).find((l) => l.item), other = boardLines(source).filter((l) => l.item)[1]
  const moved = moveBoardItem(source, item.index, key('Later'))
  assert.equal(moved, source.replace(item.raw, '').replace('### Later\r\n', '### Later\r\n' + item.raw))
  assert.equal(moveBoardItem(source, item.index, key('First')), source.replace(item.raw + other.raw, other.raw + item.raw))
  assert.equal(moveBoardItem(source, other.index, key('First'), item.index), source.replace(item.raw + other.raw, other.raw + item.raw))
  assert.equal(moveBoardItem(source, item.index, key('First'), item.index), source)
  assert.equal(moveBoardItem(source, item.index, 'missing'), source)
  assert.equal(moveBoardItem(source, item.index, key('Later'), item.index), source)
  assert.equal(moveBoardItem(source, 16, key('First')), source, 'fenced item cannot move')
})
test('duplicate headings remain distinct; unterminated final lines move with a necessary separator', () => {
  const raw = '## Same\n- [ ] A <!-- Sam -->\n## Same\n- [x] B <!-- Agent · unknown -->'
  const sections = boardSections(raw)
  assert.notEqual(sections[1].key, sections[2].key)
  assert.equal(addBoardItem(raw, 'C', 0, new Date(), '', sections[1].key), raw.replace('## Same\n- [ ] A <!-- Sam -->\n', '## Same\n- [ ] A <!-- Sam -->\n- [ ] C <!-- Sam -->\n'))
  assert.equal(moveBoardItem(raw, 3, sections[1].key, 1), '## Same\n- [x] B <!-- Agent · unknown -->\n- [ ] A <!-- Sam -->\n## Same\n')
})
test('headings honor fenced examples, closing hashes and literal hash names', () => {
  const raw = '# Board\n\n````md\n```\n## Example\n- [ ] Example\n~~~\n````\n  ## C#\n\n### Next ###\n'
  assert.deepEqual(boardSections(raw).map((s) => s.name), ['Board', 'C#', 'Next'])
  assert.equal(boardLines(raw).filter((l) => l.item).length, 0)
})

function setup({ fail = false, markdown = source, width = 390 } = {}) {
  installDom(); globalThis.innerWidth = width
  const calls = [], row = { id: 'sample', title: 'A board', members: [], default: null }
  const threads = createThreads({ api: async (method, route, body) => {
    calls.push({ method, route, body })
    if (fail) throw new Error('Board write refused.')
    return { path: 'boards/sample.md', markdown: body.markdown }
  }, redraw() {}, agents: () => [], rail: () => null, nav: () => null,
  compose: { field: () => ({ value: '' }), render: () => h('div'), dictating: () => false, files: () => [], dragProps: () => ({}) } })
  threads.st.open = { thread: row, boardOpen: true, board: { path: 'boards/sample.md', markdown }, messages: [], permissions: new Map(), typing: new Set() }
  const root = h('div')
  const view = () => { morph(root, [threads.view(width === 1440)]); return find(root, 'tt-board') }
  const labelled = (label) => buttons(view()).find((n) => n.getAttribute('aria-label') === label)
  const named = (name) => buttons(view()).find((n) => text(n) === name)
  return { threads, calls, view, labelled, named }
}
test('raw edit only offers Cancel for changes, confirms discard, and Done writes only changes', async (t) => {
  const f = setup(); t.after(() => f.threads.pause())
  const originalConfirm = globalThis.confirm, questions = []
  let discard = false
  globalThis.confirm = (question) => { questions.push(question); return discard }
  t.after(() => { globalThis.confirm = originalConfirm })
  f.labelled('Edit board').fire('click')
  assert.equal(buttons(f.view()).filter((n) => n.getAttribute('aria-label') === 'Close board').length, 0)
  assert.deepEqual(buttons(find(f.view(), 'tt-board-head')).map(text), ['Done'])
  f.named('Done').fire('click')
  assert.equal(f.calls.length, 0)
  assert.equal(f.threads.st.open.editing, false)
  f.labelled('Edit board').fire('click')
  const done = f.named('Done')
  const editor = find(f.view(), 'tt-board-editor'); editor.value = source + 'Changed.\r\n'; editor.fire('input')
  assert.equal(f.named('Done'), done, 'Done retains its target when Cancel appears')
  assert.deepEqual(buttons(find(f.view(), 'tt-board-head')).map(text), ['Cancel', 'Done'])
  editor.value = source; editor.fire('input')
  assert.equal(f.named('Cancel'), undefined, 'undoing the edit removes Cancel')
  editor.value = source + 'Changed.\r\n'; editor.fire('input')
  f.named('Done').fire('click'); await new Promise(setImmediate)
  assert.deepEqual(f.calls[0], { method: 'PUT', route: '/threads/sample/board', body: { markdown: source + 'Changed.\r\n' } })
  assert.equal(f.threads.st.open.editing, false)
  f.labelled('Edit board').fire('click'); editor.value = 'Discard'; editor.fire('input'); f.named('Cancel').fire('click')
  assert.equal(f.threads.st.open.editing, true, 'declining keeps the draft open')
  assert.equal(editor.value, 'Discard')
  assert.deepEqual(questions, ['Discard changes?'])
  discard = true; f.named('Cancel').fire('click')
  assert.equal(f.threads.st.open.editing, false)
  assert.deepEqual(questions, ['Discard changes?', 'Discard changes?'])
  assert.equal(f.calls.filter((c) => c.method === 'PUT').length, 1)
  assert.equal(f.threads.st.open.board.markdown, source + 'Changed.\r\n')
})

test('Done reads the current draft even when input has not redrawn the header yet', async (t) => {
  const f = setup(); t.after(() => f.threads.pause())
  f.labelled('Edit board').fire('click')
  const clean = f.view(), done = buttons(find(clean, 'tt-board-head'))[0]
  const editor = find(clean, 'tt-board-editor')
  editor.value = source + 'A fast edit.\r\n'; editor.fire('input')
  done.fire('click'); await new Promise(setImmediate)
  assert.equal(f.calls[0].body.markdown, source + 'A fast edit.\r\n')
  assert.equal(f.threads.st.open.editing, false)
})

const unheadedIdeas = readFileSync(new URL('./fixtures/threads/board-unheaded-ideas.md', import.meta.url), 'utf8')
for (const width of [390, 1440]) test(`${width}: every list ends with Add, including the four-item unheaded first list`, async (t) => {
  const f = setup({ markdown: unheadedIdeas, width }); t.after(() => f.threads.pause())
  const lists = all(f.view(), 'tt-board-list').filter((n) => n.getAttribute('data-board-section'))
  assert.deepEqual(lists.map((n) => all(n, 'tt-board-item').length), [4, 2])
  for (const list of lists) {
    assert.equal(all(list, 'tt-list-add').length, 1)
    assert.equal(text(find(list, 'tt-list-add')), 'Add')
    assert.equal(list.childNodes.at(-1).className, 'tt-board-list-foot')
  }
  assert.equal(text(find(f.view(), 'tt-board-list-title')), 'Ideas')
  assert.equal(text(find(f.view(), 'tt-board-list-count')), '2')
  assert.equal(f.threads.st.open.board.markdown, unheadedIdeas, 'colon removal is display-only')
  f.labelled('Add to Board').fire('click')
  const input = find(f.view(), 'tt-quick'); input.value = 'A first-list item'
  input.fire('keydown', { key: 'Enter', preventDefault() {} }); await new Promise(setImmediate)
  assert.equal(f.calls[0].body.markdown, unheadedIdeas.replace('\n### Ideas:', '- [ ] A first-list item <!-- Sam -->\n\n### Ideas:'))
  f.labelled('Add to Ideas').fire('click')
  assert.ok(find(f.view(), 'tt-quick'))
})
for (const markdown of ['', '# Board\n', '# Board\n\n- [ ] First\n', '## Empty:\n\n### Later:\n']) test(`Add remains available for empty/heading-free lists: ${JSON.stringify(markdown)}`, (t) => {
  const f = setup({ markdown }); t.after(() => f.threads.pause())
  const lists = all(f.view(), 'tt-board-list').filter((n) => n.getAttribute('data-board-section'))
  assert.equal(lists.length, markdown.includes('## Empty') ? 2 : 1)
  for (const list of lists) assert.equal(all(list, 'tt-list-add').length, 1)
})
test('failed Done keeps the editor, draft and the refusal visible', async (t) => {
  const f = setup({ fail: true }); t.after(() => f.threads.pause())
  f.labelled('Edit board').fire('click')
  const editor = find(f.view(), 'tt-board-editor'); editor.value = 'Keep my draft'; editor.fire('input')
  f.named('Done').fire('click'); await new Promise(setImmediate)
  assert.equal(f.threads.st.open.editing, true)
  assert.equal(find(f.view(), 'tt-board-editor').value, 'Keep my draft')
  assert.match(text(f.view()), /Board write refused/)
})
test('each heading opens its own add field and Move to uses the same file route', async (t) => {
  const f = setup(); t.after(() => f.threads.pause())
  assert.equal(all(f.view(), 'tt-quick').length, 0)
  f.labelled('Add to First').fire('click')
  const input = find(f.view(), 'tt-quick'); input.value = 'A new item'
  input.fire('keydown', { key: 'Enter', preventDefault() {} }); await new Promise(setImmediate)
  assert.match(f.calls[0].body.markdown, /Second <!-- Sam -->\r\n- \[ \] A new item <!-- Sam -->\r\n\r\n### Later/)
  assert.equal(find(f.view(), 'tt-quick').value, '')
  const moved = boardLines(f.threads.st.open.board.markdown).find((l) => l.item)
  f.labelled(`Actions for ${moved.text}`).fire('click'); f.named('Move to…').fire('click'); f.named('Later').fire('click')
  await new Promise(setImmediate)
  assert.equal(f.calls.filter((c) => c.method === 'PUT').length, 2)
  assert.match(f.threads.st.open.board.markdown, /### Later\r\n- \[X\] Keep \*\*everything\*\* <!-- Quill · new · until 2027-01-02 · extra -->/)
})
test('refused adds retain their input; refused moves leave the original file intact', async (t) => {
  const f = setup({ fail: true }); t.after(() => f.threads.pause())
  f.labelled('Add to Empty').fire('click')
  const input = find(f.view(), 'tt-quick'); input.value = 'Keep this draft'
  input.fire('keydown', { key: 'Enter', preventDefault() {} }); await new Promise(setImmediate)
  assert.equal(find(f.view(), 'tt-quick').value, 'Keep this draft')
  assert.equal(f.threads.st.open.board.markdown, source)
  const item = boardLines(source).find((l) => l.item)
  f.labelled(`Actions for ${item.text}`).fire('click'); f.named('Move to…').fire('click'); f.named('Later').fire('click')
  await new Promise(setImmediate)
  assert.equal(f.threads.st.open.board.markdown, source)
  assert.match(text(f.view()), /Board write refused/)
})
