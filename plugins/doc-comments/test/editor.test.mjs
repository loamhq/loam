// The editor's text is the file's text: loaded into the editor's own state
// (the same separator rule and Markdown language the editor uses) and read
// back with nothing changed, every sample comes out byte for byte -- the
// frontmatter, tables, code blocks, wiki-links, CRLF and mixed line endings.
// And the diff the editor maps saves through turns one text into the other.
//
// Run: node --test plugins/doc-comments/test/editor.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { EditorState, ChangeSet } from '../page/vendor/codemirror.js'
import { lineSeparatorOf } from '../page/editor/editor.js'
import { markdownLanguage } from '../page/editor/live.js'
import { changes } from '../page/editor/textdiff.js'

const SAMPLES = {
  frontmatter: '---\ncreated_by: "[[Sam]]"\ntags: [a, b]\nloam_doc_id: d-abcdefghij\n---\n\n# Title\n\nBody.\n',
  table: '| A | B |\n| :-- | --: |\n| 1 | **2** |\n| x \\| y | `z` |\n',
  code: 'Text\n\n```python\ndef f():\n    return "```"\n```\n\n    indented code\n\n~~~\ntilde\n~~~\n',
  wiki: 'See [[Some Note]] and [[Other|label]] and ![[pic.png]] and [[Note#Heading]].\n',
  crlf: '---\r\na: 1\r\n---\r\n# Hi\r\n\r\n| a | b |\r\n|---|---|\r\n| 1 | 2 |\r\n',
  mixed: 'one\r\ntwo\nthree\r\n\r\nfour\n',
  nofinal: 'no newline at the end',
  tabs: '- item\n\t- nested with a tab\n  - two spaces\n> [!note] Callout\n> body\n',
  unicode: 'Émigré — naïve 🌱 ‍👩‍👩‍👧 nbsp​ zero-width\n',
  empty: '',
}

for (const [name, text] of Object.entries(SAMPLES)) {
  test(`round-trips byte for byte: ${name}`, () => {
    const state = EditorState.create({ doc: text, extensions: [EditorState.lineSeparator.of(lineSeparatorOf(text)), markdownLanguage] })
    assert.equal(state.sliceDoc(), text)
    assert.equal(Buffer.from(state.sliceDoc()).equals(Buffer.from(text)), true)
  })
}

test('a CRLF file keeps CRLF on new lines typed into it', () => {
  const text = 'a\r\nb\r\n'
  const state = EditorState.create({ doc: text, extensions: [EditorState.lineSeparator.of(lineSeparatorOf(text))] })
  const next = state.update({ changes: { from: 1, insert: state.lineBreak + 'x' } }).state
  assert.equal(next.sliceDoc(), 'a\r\nx\r\nb\r\n')
})

test('the diff turns one text into the other', () => {
  const pairs = [
    ['The cat sat.', 'The black cat sat down.'],
    [SAMPLES.frontmatter, SAMPLES.frontmatter.replace('Body.', 'Body, changed.').replace('tags: [a, b]', 'tags: [a]')],
    ['', 'new'], ['old', ''], ['same', 'same'],
    ['a\r\nb\r\n', 'a\r\nB\r\nc\r\n'],
  ]
  for (const [a, b] of pairs) {
    // As the editor does it: in editor space (CRLF as one break), changes
    // split on LF, the file's own separator on the way out.
    const crlf = lineSeparatorOf(a) === '\r\n'
    const state = EditorState.create({ doc: a, extensions: [EditorState.lineSeparator.of(lineSeparatorOf(a))] })
    const ea = editorText(a, crlf), eb = editorText(b, crlf)
    assert.equal(state.doc.toString(), ea)
    const cs = ChangeSet.of(changes(ea, eb), ea.length, '\n')
    assert.equal(state.update({ changes: cs }).state.sliceDoc(), b)
  }
})

test('a server change maps over what was typed meanwhile', () => {
  // Typed "very " while a save was away; the save's answer adds a stamp line.
  const sent = '---\na: 1\n---\nIt is good.\n'
  const answer = '---\na: 1\nlast_modified_by: "[[Sam]]"\n---\nIt is good.\n'
  const typed = ChangeSet.of([{ from: sent.indexOf('good'), insert: 'very ' }], sent.length)
  const server = ChangeSet.of(changes(sent, answer), sent.length, '\n')
  const doc = EditorState.create({ doc: sent, extensions: [EditorState.lineSeparator.of('\n')] }).doc
  const current = typed.apply(doc)
  const merged = server.map(typed).apply(current)
  assert.equal(merged.toString(), answer.replace('good', 'very good'))
})

import { posMap, editorText } from '../page/editor/textdiff.js'

test('server positions (code points, raw CRLF) and editor positions agree', () => {
  const raw = '---\r\na: 🌱\r\n---\r\n# Hé 🌱 there\r\nline two\r\n'
  for (const crlf of [true, false]) {
    const ed = editorText(raw, crlf)
    const pm = posMap(raw, crlf)
    const cps = [...raw]       // code points
    for (let cp = 0; cp <= cps.length; cp++) {
      // skip the middle of a CRLF, which has no editor position of its own
      const before = cps.slice(0, cp).join('')
      if (crlf && before.endsWith('\r') && raw.slice(before.length, before.length + 1) === '\n') continue
      const pos = pm.toEditor(cp)
      assert.equal(pm.toServer(pos), cp)
      assert.equal(ed.slice(0, pos), editorText(before, crlf))
    }
    const s = raw.indexOf('there'), e = s + 5
    const cs = [...raw.slice(0, s)].length
    assert.equal(pm.slice(pm.toEditor(cs), pm.toEditor(cs + 5)), 'there')
    assert.equal(ed.slice(pm.toEditor(cs), pm.toEditor(cs + 5)), 'there')
    assert.ok(e > s)
  }
})

test('the diff never splits a word', () => {
  assert.deepEqual(changes('happened, and that', 'happened, aa and that'), [{ from: 10, to: 10, insert: 'aa ' }])
})
