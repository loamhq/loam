// Link helpers (page/editor/format.js): Markdown links, wiki-links, bare URLs.
// Run: node --test plugins/comments/test/links.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { EditorState } from '../page/vendor/codemirror.js'
import { markdownLanguage } from '../page/editor/live.js'
import { active, linkAt, linkInfoAt, setLink } from '../page/editor/format.js'

function mockView(doc, anchor = 0, head = anchor) {
  let state = EditorState.create({
    doc,
    selection: { anchor, head },
    extensions: [markdownLanguage],
  })
  let focused = false
  return {
    get state() { return state },
    get focused() { return focused },
    dispatch(tr) { state = (tr.state ? tr : state.update(tr)).state },
    focus() { focused = true },
  }
}

test('linkInfoAt resolves Markdown links, wiki-links, and bare URLs', () => {
  const doc = '---\ntitle: [[Ignore]]\n---\n\nSee [[other#Focus|companion note]], [[plain]], [mockup](/files/vault/Notes/page.html), and https://example.org/docs.\n\nCode `[[not-a-link]]` stays plain.\n'
  const v = mockView(doc)

  // Frontmatter skipped
  assert.equal(linkInfoAt(v.state, doc.indexOf('Ignore')), null)

  // Wiki-link with alias
  const wikiStart = doc.indexOf('[[other#Focus|companion note]]')
  const w = linkInfoAt(v.state, wikiStart + 16)
  assert.equal(w.kind, 'wiki')
  assert.equal(w.target, 'other#Focus')
  assert.equal(w.ref, '[[other#Focus]]')
  assert.equal(w.label, 'companion note')
  assert.equal(w.hasLabel, true)
  assert.equal(linkAt(v.state, wikiStart + 16), 'other#Focus')

  // Wiki-link without alias (at boundary too)
  const plainStart = doc.indexOf('[[plain]]')
  const p = linkInfoAt(v.state, plainStart + '[[plain]]'.length)
  assert.equal(p.kind, 'wiki')
  assert.equal(p.target, 'plain')
  assert.equal(p.label, 'plain')
  assert.equal(p.hasLabel, false)

  // Markdown link (inside label and at end boundary)
  const mdStart = doc.indexOf('[mockup]')
  const mdEnd = doc.indexOf('.html)') + '.html)'.length
  const md = linkInfoAt(v.state, mdStart + 3)
  assert.equal(md.kind, 'md')
  assert.equal(md.target, '/files/vault/Notes/page.html')
  assert.equal(md.ref, '/files/vault/Notes/page.html')
  assert.equal(md.label, 'mockup')
  assert.equal(linkInfoAt(v.state, mdEnd)?.target, '/files/vault/Notes/page.html')

  // Bare URL
  const urlStart = doc.indexOf('https://example.org/docs')
  const u = linkInfoAt(v.state, urlStart + 10)
  assert.equal(u.kind, 'url')
  assert.equal(u.target, 'https://example.org/docs')
  assert.equal(u.ref, 'https://example.org/docs')

  // Inline code skipped
  assert.equal(linkInfoAt(v.state, doc.indexOf('not-a-link')), null)
})

test('setLink edits and unlinks Markdown links, wiki-links, and bare URLs', () => {
  // Edit Markdown URL without disturbing label or unrelated caret
  const mdDoc = 'Top\n\nOpen [reading list](https://example.org/old) soon.\n'
  const v1 = mockView(mdDoc, 0)
  const mdPos = mdDoc.indexOf('reading list')
  setLink(v1, 'https://example.org/new', { from: mdPos, to: mdPos })
  assert.equal(v1.state.doc.toString(), 'Top\n\nOpen [reading list](https://example.org/new) soon.\n')
  assert.equal(v1.state.selection.main.head, 0)
  assert.equal(v1.focused, true)

  // Unlink Markdown link by clearing URL
  setLink(v1, '', { from: mdPos, to: mdPos })
  assert.equal(v1.state.doc.toString(), 'Top\n\nOpen reading list soon.\n')

  // Edit wiki-link with alias preserves alias
  const wikiDoc = 'Check [[other#Focus|companion note]] today.\n'
  const v2 = mockView(wikiDoc, 0)
  const wPos = wikiDoc.indexOf('companion note')
  setLink(v2, 'other#Summary', { from: wPos, to: wPos })
  assert.equal(v2.state.doc.toString(), 'Check [[other#Summary|companion note]] today.\n')

  // Unlink wiki-link keeps label
  setLink(v2, '', { from: wPos, to: wPos })
  assert.equal(v2.state.doc.toString(), 'Check companion note today.\n')

  // Edit wiki-link without alias
  const wikiPlain = 'See [[other]] here.\n'
  const v3 = mockView(wikiPlain, wikiPlain.indexOf('other'))
  assert.equal(active(v3.state).link, true)
  setLink(v3, 'plan')
  assert.equal(v3.state.doc.toString(), 'See [[plan]] here.\n')

  // Converting wiki-link to an external URL turns it into a Markdown link
  setLink(v3, 'https://example.org/plan', { from: 6, to: 6 })
  assert.equal(v3.state.doc.toString(), 'See [plan](https://example.org/plan) here.\n')

  // Edit bare URL
  const bareDoc = 'Visit https://example.org/a now.\n'
  const v4 = mockView(bareDoc, bareDoc.indexOf('example.org'))
  setLink(v4, 'https://example.org/b')
  assert.equal(v4.state.doc.toString(), 'Visit https://example.org/b now.\n')
})
