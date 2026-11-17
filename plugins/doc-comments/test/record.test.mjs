// Suggesting, recorded (page/editor/record.js): drafts are made and grown
// by the person's edits and moved by position mapping, never worked out by
// comparing texts. Seeded runs keep several drafts open at once and mix
// typing, deleting original words, typing over a selection, Enter, undo and
// redo, accepting and rejecting in any order, and check after every step:
//   - the stored text with every draft applied is the editor's text;
//   - rejecting every draft, in any order, gives the original exactly;
//   - accepting one writes exactly its words and changes no other draft;
//   - what is sent for a draft is a passage of the stored text that, replaced,
//     gives exactly the draft applied, and never overlaps another's.
//
// Run: node --test plugins/doc-comments/test/record.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { EditorState, ChangeSet, history, undo, redo } from '../page/vendor/codemirror.js'
import { draftsField, setRecording, setDrafts, remote, inBase, applied, passage, passages } from '../page/editor/record.js'

function editor(text) {
  let state = EditorState.create({ doc: text, extensions: [draftsField, history()] })
  const view = {
    get state() { return state },
    dispatch(tr) { state = (tr.state ? tr : state.update(tr)).state },
    run(spec) { state = state.update(spec).state },
  }
  view.run({ effects: setRecording.of(true) })
  return view
}
const drafts = (v) => v.state.field(draftsField).list
const doc = (v) => v.state.doc.toString()

function type(v, at, text) { v.run({ changes: { from: at, insert: text }, selection: { anchor: at + text.length }, userEvent: 'input.type' }) }
function del(v, from, to) { v.run({ changes: { from, to }, userEvent: 'delete' }) }
function over(v, from, to, text) { v.run({ changes: { from, to, insert: text }, userEvent: 'input.type' }) }

// Reject one draft: its words go back (a remote change, as the editor does it).
function reject(v, d) {
  const list = drafts(v).filter(x => x.key !== d.key)
  v.run({ changes: { from: d.from, to: d.to, insert: d.orig }, annotations: remote.of(true), effects: setDrafts.of({ list: list.map(x => x) }) })
  // positions of the others map through the change
}
// Accept one: the stored text gains its words; the editor does not change.
function accept(base, v, d) {
  const r = inBase(drafts(v), v.state.doc).find(x => x.d.key === d.key)
  const next = base.slice(0, r.start) + r.text + base.slice(r.end)
  v.run({ effects: setDrafts.of({ list: drafts(v).filter(x => x.key !== d.key) }) })
  return next
}

function check(base, v, label) {
  assert.equal(applied(base, drafts(v), v.state.doc), doc(v), `${label}: stored text with the drafts applied is the editor's`)
  const rs = inBase(drafts(v), v.state.doc)
  for (let i = 1; i < rs.length; i++) assert.ok(rs[i - 1].end <= rs[i].start, `${label}: drafts never overlap`)
  // Each is sent as a passage that, replaced, gives the draft applied, and
  // no two passages share a letter (so accepting one never touches another).
  let prevEnd = -1
  for (const { r, p } of passages(base, drafts(v), v.state.doc)) {
    if (!p) continue
    assert.equal(base.slice(p.start, p.end), p.quote)
    assert.equal(base.slice(0, p.start) + p.replacement + base.slice(p.end), base.slice(0, r.start) + r.text + base.slice(r.end), `${label}: the passage sent is the draft`)
    assert.ok(p.start >= prevEnd, `${label}: passages never overlap`)
    prevEnd = p.end
  }
}

const TEXT = '# Title\n\nParagraph one has some words. Nothing here is remarkable except where it sits.\n\nParagraph two closes the section with a sentence.\n\nThe last line, End words here.\n'

test('two drafts far apart stay two (the walk\'s case 1)', () => {
  const v = editor(TEXT)
  type(v, doc(v).length - 1, ' End words here.')
  type(v, doc(v).indexOf('sentence.') + 9, ' Mid.')
  assert.equal(drafts(v).length, 2)
  check(TEXT, v, 'two')
  // rejecting one leaves the other exactly
  const [a, b] = drafts(v)
  reject(v, b)
  assert.equal(doc(v), TEXT.replace('sentence.', 'sentence. Mid.'))
  check(TEXT, v, 'after reject')
})

test('deleting original words beside a draft (case 2)', () => {
  let base = TEXT
  const v = editor(base)
  const at = doc(v).indexOf('Nothing here') + 'Nothing here'.length
  type(v, at, ' at all')
  const s = doc(v).indexOf(' is remarkable')
  del(v, s, s + ' is remarkable'.length)
  check(base, v, 'after delete')
  type(v, doc(v).indexOf('at all') + 6, ' matters')
  check(base, v, 'after more')
  const shown = doc(v)
  for (const d of [...drafts(v)]) base = accept(base, v, d)
  assert.equal(base, shown, 'accepting writes exactly what was shown')
})

test('typing over a selection, then extending it, then accepting (case 3)', () => {
  let base = TEXT
  const v = editor(base)
  const s = doc(v).indexOf('closes the section')
  over(v, s, s + 'closes the section'.length, 'ends it')
  type(v, s + 'ends it'.length, ' nicely')
  assert.equal(drafts(v).length, 1)
  check(base, v, 'typed over')
  const shown = doc(v)
  base = accept(base, v, drafts(v)[0])
  assert.equal(base, shown)
})

test('two insertions, the later one accepted first (case 4)', () => {
  let base = TEXT
  const v = editor(base)
  type(v, doc(v).indexOf('sits.') + 5, ' First.')
  type(v, doc(v).indexOf('sentence.') + 9, ' Second.')
  const [first, second] = drafts(v)
  const p = passage(base, inBase(drafts(v), v.state.doc)[1])
  assert.equal(p.replacement.trim().endsWith('Second.'), true)
  base = accept(base, v, second)
  assert.equal(base, TEXT.replace('sentence.', 'sentence. Second.'))
  check(base, v, 'one accepted')
  assert.equal(doc(v), TEXT.replace('sits.', 'sits. First.').replace('sentence.', 'sentence. Second.'), 'the editor has it once')
})

test('undo after typing withdraws the draft; redo brings it back', () => {
  const v = editor(TEXT)
  type(v, 10, 'Hello ')
  assert.equal(drafts(v).length, 1)
  undo(v)
  assert.equal(drafts(v).length, 0)
  assert.equal(doc(v), TEXT)
  redo(v)
  assert.equal(drafts(v).length, 1)
  check(TEXT, v, 'redo')
})

// -- seeded runs ---------------------------------------------------------------

function rng(seed) { let s = seed >>> 0; return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 2 ** 32 } }
const WORDS = [' Aaa.', ' bb', 'x', ',', ' cc dd', '.', ' ', 'Zed', '\n\n', 'é']

const most = []
test.after(() => {
  // The runs did keep several drafts open: most reached two, many four or more.
  const two = most.filter(n => n >= 2).length, four = most.filter(n => n >= 4).length
  console.log(`# drafts open at once: >=2 in ${two} runs, >=4 in ${four}, at most ${Math.max(...most)}`)
  assert.ok(two > most.length * 0.6 && four > most.length * 0.2)
})
for (let seed = 1; seed <= 500; seed++) {
  test(`seeded run ${seed}: several drafts, any order of accepting and rejecting`, () => {
    const r = rng(seed)
    let base = TEXT
    const v = editor(base)
    const pick = (n) => Math.floor(r() * n)
    for (let step = 0; step < 30; step++) {
      const len = v.state.doc.length
      const k = r()
      if (k < 0.32) type(v, pick(len + 1), WORDS[pick(WORDS.length)])
      else if (k < 0.47) { const a = pick(len); del(v, a, Math.min(len, a + 1 + pick(8))) }
      else if (k < 0.6) { const a = pick(len); over(v, a, Math.min(len, a + 1 + pick(10)), WORDS[pick(WORDS.length)].trim() || 'w') }
      else if (k < 0.66) undo(v)
      else if (k < 0.7) redo(v)
      else if (k < 0.82 && drafts(v).length) {
        const d = drafts(v)[pick(drafts(v).length)]
        const others = drafts(v).filter(x => x.key !== d.key).map(x => ({ key: x.key, text: v.state.doc.sliceString(x.from, x.to), orig: x.orig }))
        base = accept(base, v, d)
        const after = drafts(v).map(x => ({ key: x.key, text: v.state.doc.sliceString(x.from, x.to), orig: x.orig }))
        assert.deepEqual(after, others, `step ${step}: accepting one changed no other`)
      } else if (k < 0.92 && drafts(v).length) {
        const d = drafts(v)[pick(drafts(v).length)]
        const others = drafts(v).filter(x => x.key !== d.key).map(x => ({ key: x.key, text: v.state.doc.sliceString(x.from, x.to), orig: x.orig }))
        reject(v, d)
        const after = drafts(v).map(x => ({ key: x.key, text: v.state.doc.sliceString(x.from, x.to), orig: x.orig }))
        assert.deepEqual(after, others, `step ${step}: rejecting one changed no other`)
      }
      check(base, v, `seed ${seed} step ${step}`)
      most[seed - 1] = Math.max(most[seed - 1] || 0, drafts(v).length)
    }
    // Rejecting all, in a random order, gives the stored text exactly.
    while (drafts(v).length) reject(v, drafts(v)[pick(drafts(v).length)])
    assert.equal(doc(v), base)
  })
}
