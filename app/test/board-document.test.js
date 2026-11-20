import test from 'node:test'
import assert from 'node:assert/strict'
import { boardLines } from '../public/thread-record.js'
import { docText, applyDocText, cardText, applyCardText, setBoardExpiry, recoverBoard } from '../public/board-document.js'

const file = '---\r\nlabels:\r\n  - private\r\n---\r\n# Board\r\n## Ideas\n* [X] 2026-11-14 · Read **this** and [[Notes|the note]] <!-- Iris · until 2027-01-01 · custom -->\r\n- 2026-11-17 · **iris** · Next <!-- new -->'
test('Cards and Doc round trip the same mixed-EOL file including author, unknown fields, expiry and frontmatter', () => {
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
