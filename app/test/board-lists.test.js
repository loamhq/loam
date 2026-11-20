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
const today = new Date().toISOString().slice(0, 10)
const key = (name) => boardSections(source).find((s) => s.name === name).key
test('adding targets an exact heading, retains CRLF/prose/fences, and authors every addition', () => {
  const added = addBoardItem(source, 'Do this', 7, new Date('2026-12-31T23:59:00Z'), '', key('First'))
  assert.equal(added, source.replace('- [ ] Second <!-- Sam -->\r\n', '- [ ] Second <!-- Sam -->\r\n- [ ] 2026-12-31 · Do this <!-- Sam · until 2027-01-07 -->\r\n'))
  assert.equal(addBoardItem(source, 'Start here', 0, new Date(), '', key('Empty')), source.replace('## Empty\r\n', '## Empty\r\n- [ ] ' + today + ' · Start here <!-- Sam -->\r\n'))
  assert.deepEqual(boardSections(source).map((s) => s.name), ['Board', 'First', 'Later', 'Empty'])
  assert.equal(addBoardItem(source, 'No write', 0, new Date(), '', 'missing'), source)
  assert.equal(addBoardItem('# Board', 'First', 0, new Date(), '', 'top'), '# Board\n- [ ] ' + today + ' · First <!-- Sam -->\n')
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
  assert.equal(addBoardItem(raw, 'C', 0, new Date(), '', sections[1].key), raw.replace('## Same\n- [ ] A <!-- Sam -->\n', '## Same\n- [ ] A <!-- Sam -->\n- [ ] ' + today + ' · C <!-- Sam -->\n'))
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
    if (method === 'GET' && route.endsWith('/board')) return { path: 'boards/sample.md', revision: 'v1', markdown }
    calls.push({ method, route, body })
    if (fail) throw new Error('Board write refused.')
    return { path: 'boards/sample.md', revision: 'v2', markdown: body.markdown }
  }, redraw() {}, agents: () => [], rail: () => null, nav: () => null,
  compose: { field: () => ({ value: '' }), render: () => h('div'), dictating: () => false, files: () => [], dragProps: () => ({}) } })
  threads.st.open = { thread: row, boardOpen: true, board: { path: 'boards/sample.md', revision: 'v1', markdown }, messages: [], permissions: new Map(), typing: new Set() }
  const root = h('div')
  const view = () => { morph(root, [threads.view(width === 1440)]); return find(root, 'tt-board') }
  const labelled = (label) => buttons(view()).find((n) => n.getAttribute('aria-label') === label)
  const named = (name) => buttons(view()).find((n) => text(n) === name)
  return { threads, calls, view, labelled, named }
}
test('Doc uses the Files field and writes the original metadata through the revisioned route', async (t) => {
  const f = setup(); t.after(() => f.threads.pause())
  f.named('Doc').fire('click')
  f.named('Edit document').fire('click')
  const field = find(f.view(), 'fs-field')
  assert.ok(field)
  assert.doesNotMatch(field.value, /<!--|until 2027/)
  field.value = field.value.replace('Second', 'Updated')
  field.fire('input')
  f.named('Done').fire('click')
  await new Promise(setImmediate)
  assert.deepEqual(f.calls[0].body, { base_revision: 'v1', markdown: source.replace('Second', 'Updated') })
  assert.equal(f.threads.st.open.editing, false)
})

const unheadedIdeas = readFileSync(new URL('./fixtures/threads/board-unheaded-ideas.md', import.meta.url), 'utf8')
for (const width of [390, 1440]) test(`${width}: every list offers quick add before its cards, including the four-item unheaded first list`, async (t) => {
  const f = setup({ markdown: unheadedIdeas, width }); t.after(() => f.threads.pause())
  const lists = all(f.view(), 'tt-board-list').filter((n) => n.getAttribute('data-board-section'))
  assert.deepEqual(lists.map((n) => all(n, 'tt-board-item').length), [4, 2])
  for (const list of lists) {
    assert.equal(all(list, 'tt-list-add').length, 1)
    assert.equal(text(find(list, 'tt-list-add')), 'Add an idea…')
    assert.ok(list.childNodes.indexOf(find(list, 'tt-board-list-foot')) < list.childNodes.indexOf(find(list, 'tt-board-item')))
  }
  assert.equal(text(all(f.view(), 'tt-board-list-title').at(-1)), 'Ideas')
  assert.equal(text(all(f.view(), 'tt-board-list-count').at(-1)), '2')
  assert.equal(f.threads.st.open.board.markdown, unheadedIdeas, 'colon removal is display-only')
  f.labelled('Add to Board').fire('click')
  const input = find(f.view(), 'tt-quick'); input.value = 'A first-list item'
  input.fire('keydown', { key: 'Enter', preventDefault() {} }); await new Promise(setImmediate)
  assert.equal(f.calls[0].body.markdown, unheadedIdeas.replace('\n### Ideas:', '- [ ] ' + today + ' · A first-list item <!-- Sam -->\n\n### Ideas:'))
  f.labelled('Add to Ideas').fire('click')
  assert.ok(find(f.view(), 'tt-quick'))
})
for (const markdown of ['', '# Board\n', '# Board\n\n- [ ] First\n', '## Empty:\n\n### Later:\n']) test(`Add remains available for empty/heading-free lists: ${JSON.stringify(markdown)}`, (t) => {
  const f = setup({ markdown }); t.after(() => f.threads.pause())
  const lists = all(f.view(), 'tt-board-list').filter((n) => n.getAttribute('data-board-section'))
  assert.equal(lists.length, markdown.includes('## Empty') ? 2 : 1)
  for (const list of lists) assert.equal(all(list, 'tt-list-add').length, 1)
})
test('a refused edit keeps the draft and exposes a single retry', async (t) => {
  const f = setup({ fail: true }); t.after(() => f.threads.pause())
  f.named('Doc').fire('click'); f.named('Edit document').fire('click')
  const editor = find(f.view(), 'fs-field'); editor.value += 'Keep my draft'; editor.fire('input')
  f.named('Done').fire('click'); await new Promise(setImmediate)
  assert.match(f.threads.st.open.recovery.text, /Keep my draft/)
  assert.ok(f.named('Reapply my edit'))
  assert.match(text(f.view()), /Board write refused/)
})

test('each heading opens its own add field and Move to uses the same file route', async (t) => {
  const f = setup(); t.after(() => f.threads.pause())
  assert.equal(all(f.view(), 'tt-quick').length, 0)
  f.labelled('Add to First').fire('click')
  const input = find(f.view(), 'tt-quick'); input.value = 'A new item'
  input.fire('keydown', { key: 'Enter', preventDefault() {} }); await new Promise(setImmediate)
  assert.match(f.calls[0].body.markdown, /Second <!-- Sam -->\r\n- \[ \] \d{4}-\d{2}-\d{2} · A new item <!-- Sam -->\r\n\r\n### Later/)
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
  assert.ok(f.named('Reapply my edit'))
  assert.match(text(f.view()), /Board write refused/)
})
