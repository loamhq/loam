import test from 'node:test'
import assert from 'node:assert/strict'
import { createFiles } from '../public/files.js'
import { boardLines } from '../public/thread-record.js'
import { boardProjection, docText, applyDocText, cardText, applyCardText, setBoardExpiry, recoverBoard } from '../public/board-document.js'

const file = '---\r\nlabels:\r\n  - private\r\n---\r\n# Board\r\n## Ideas\n* [X] 2026-11-14 · Read **this** and [[Notes|the note]] <!-- Iris · until 2027-01-01 · custom -->\r\n- 2026-11-17 · **iris** · Next <!-- new -->'
test('Cards and the Files projection round trip the same mixed-EOL file including author, unknown fields, expiry and frontmatter', () => {
  const item = boardLines(file).find(l => l.item)
  assert.equal(cardText(item.text), 'Read this and the note')
  const cards = applyCardText(file, item.index, 'Read these and the note')
  assert.equal(cards, file.replace('**this**', '**these**'))
  assert.doesNotMatch(docText(cards), /2026-11|<!--|private|\*\*iris\*\*/)
  const edited = applyDocText(cards, docText(cards).replace('Next', 'Another'))
  assert.equal(edited, cards.replace('Next', 'Another'))
  const rows = docText(edited).split('\n')
  const moved = applyDocText(edited, [...rows.slice(0, -2), rows.at(-1), rows.at(-2)].join('\n'))
  assert.deepEqual(boardLines(moved).filter(l => l.item).map(l => l.author), ['iris', 'Iris'])
  assert.equal(setBoardExpiry(file, item.index, ''), file.replace(' · until 2027-01-01', ''))
  assert.equal(setBoardExpiry(file, item.index, '2027-02-02'), file.replace('2027-01-01', '2027-02-02'))
})
test('independent edits can be reapplied, and overlapping edits preserve both versions', () => {
  const mine = file.replace('Next', 'My note'), theirs = file.replace('Next', 'Their note')
  assert.equal(recoverBoard(file, mine, file.replace('Read', 'Review')).markdown, mine.replace('Read', 'Review'))
  const both = recoverBoard(file, mine, theirs)
  assert.ok(both.both && both.markdown.startsWith(theirs) && both.markdown.endsWith('My note <!-- new -->'))
})

test('Files saves visible edits with the original board metadata and refuses a stale revision', async () => {
  const path = '00_INBOX/from-agents/comms.md'
  const row = { root: 'vault', path, name: 'comms.md', kind: 'file', capabilities: { read: true, write: true } }
  let version = { ...row, text: file, revision: 'opening' }
  const files = createFiles({ schedule() {}, api: async (method, route, body) => {
    if (method === 'GET' && route.startsWith('/files/read')) return version
    assert.equal(method, 'PUT')
    assert.equal(route, '/files/text')
    if (body.base_revision !== version.revision) throw Object.assign(new Error('Changed on the host'), {
      status: 409, body: { current_revision: version.revision },
    })
    version = { ...row, text: body.text, revision: 'saved' }
    return version
  } })
  await files.openFile('vault', path, row, { mode: 'edit' })
  const opened = files.st.file
  assert.doesNotMatch(opened.text, /2026-11|<!--|private/)
  files.edit(opened.text.replace('Next', 'Updated'))
  await files.save()
  assert.equal(version.text, file.replace('Next', 'Updated'))
  assert.equal(opened.revision, 'saved')

  const agentEdit = version.text + '\r\n- 2026-11-20 · Keep my update <!-- Quill -->'
  version = { ...version, text: agentEdit, revision: 'agent' }
  files.edit(opened.text.replace('Updated', 'My draft'))
  await files.save()
  assert.equal(version.text, agentEdit)
  assert.equal(opened.conflict.current, 'agent')
  assert.match(opened.draft, /My draft/)
  files.closeFile()
})

test('board file projection maps visible edit ranges across CRLF and hidden metadata', () => {
  const projection = boardProjection('vault', '00_INBOX/boards/Example.md')
  assert.equal(boardProjection('vault', '00_INBOX/boards/backup/Example.md'), null)
  assert.equal(boardProjection('spaces', '00_INBOX/boards/Example.md'), null)
  const { toSource, fromSource } = projection.offsets(file)
  const start = projection.show(file).indexOf('Next')
  assert.equal(toSource(start, 1), file.indexOf('Next'))
  assert.equal(toSource(start + 4, -1), file.indexOf('Next') + 4)
  assert.equal(fromSource(file.indexOf('<!-- new -->')), start + 4)
})
