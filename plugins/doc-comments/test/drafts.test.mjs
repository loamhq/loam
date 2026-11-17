// Suggesting kept as real text (page/editor/drafts.js): what a pause sends,
// and, over a few hundred seeded runs of typing, pausing and deleting, that
// the drafts it leaves, applied to the stored text, give exactly the text the
// person typed.
//
// Run: node --test plugins/doc-comments/test/drafts.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { applyDrafts, plan, runs } from '../page/editor/drafts.js'
import { changes } from '../page/editor/textdiff.js'

// The server's side, in miniature: drafts by id, `add` with `supersedes`.
function serve(base, ops, drafts, next) {
  const map = new Map(drafts.map(d => [d.id, d]))
  for (const o of ops) {
    if (o.op === 'withdraw') { map.delete(o.id); continue }
    if (o.supersedes) map.delete(o.supersedes)
    for (const id of o.withdraws || []) map.delete(id)
    const id = `d${next.n++}`
    map.set(id, { id, from: o.from, to: o.to, replacement: o.replacement })
  }
  return [...map.values()]
}

test('the diff never splits a word', () => {
  assert.deepEqual(changes('happened, and that', 'happened, aa and that'), [{ from: 10, to: 10, insert: 'aa ' }])
})

test('typed in bursts after "order." is one growing insertion', () => {
  const base = 'The baker learned my order. The man nodded.'
  const next = { n: 1 }
  let drafts = []
  let now = base.replace('order.', 'order. Aaa.')
  drafts = serve(base, plan(base, now, drafts), drafts, next)
  assert.equal(drafts.length, 1)
  now = now.replace('Aaa.', 'Aaa. Bbb.')
  const ops = plan(base, now, drafts)
  assert.equal(ops.length, 1)
  assert.equal(ops[0].supersedes, drafts[0].id)
  drafts = serve(base, ops, drafts, next)
  assert.equal(applyDrafts(base, drafts).text, now)
  assert.equal(applyDrafts(base, drafts).text, 'The baker learned my order. Aaa. Bbb. The man nodded.')
})

test('typing, more typing and a Backspace in one place stays one suggestion', () => {
  const base = 'nothing happened, and that was the point.'
  const next = { n: 1 }
  let drafts = []
  for (const now of ['nothing happened, aa and that was the point.', 'nothing happened, aa bb and that was the point.', 'nothing happened, aa b and that was the point.']) {
    drafts = serve(base, plan(base, now, drafts), drafts, next)
    assert.equal(drafts.length, 1, JSON.stringify(drafts))
    assert.equal(applyDrafts(base, drafts).text, now)
  }
})

test('deleting everything typed withdraws the draft', () => {
  const base = 'one two three'
  let drafts = [{ id: 'x', from: 4, to: 7, replacement: 'two more' }]
  assert.deepEqual(plan(base, base, drafts), [{ op: 'withdraw', id: 'x' }])
})

// -- seeded runs -------------------------------------------------------------

function rng(seed) {
  let s = seed >>> 0
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 2 ** 32 }
}

const BASES = [
  'The first winter I stayed, nothing happened, and that was the point. The baker learned my order.\n\nI think this is what roots are: not comfort, but accountability.\n',
  'Short.',
  '# Title\n\n- one\n- two\n\nEnd of the doc',
  'Émigré — naïve, said the café (twice).',
]
const WORDS = [' Aaa.', ' bb', 'x', ',', ' cc dd', '.', ' ', 'Zed', '\n\nNew para.']

for (let seed = 1; seed <= 400; seed++) {
  test(`seeded run ${seed}: the drafts give exactly what was typed`, () => {
    const r = rng(seed)
    const base = BASES[seed % BASES.length]
    const next = { n: 1 }
    let drafts = [], now = base, caret = Math.floor(r() * (base.length + 1))
    for (let step = 0; step < 12; step++) {
      const k = r()
      if (k < 0.45) {                                   // type
        const w = WORDS[Math.floor(r() * WORDS.length)]
        now = now.slice(0, caret) + w + now.slice(caret); caret += w.length
      } else if (k < 0.65) {                            // Backspace a few
        const n = 1 + Math.floor(r() * 4)
        const a = Math.max(0, caret - n)
        now = now.slice(0, a) + now.slice(caret); caret = a
      } else if (k < 0.8) {                             // move the caret
        caret = Math.floor(r() * (now.length + 1))
      } else {                                          // pause: sync
        drafts = serve(base, plan(base, now, drafts), drafts, next)
        const applied = applyDrafts(base, drafts).text
        // What could not be anchored (an empty doc's first words) stays only
        // typed; everything else is in a draft.
        if (base.trim()) assert.equal(applied, now, `after step ${step}`)
        for (let i = 1; i < drafts.length; i++) {
          const s = [...drafts].sort((x, y) => x.from - y.from)
          assert.ok(s[i - 1].to <= s[i].from, 'drafts never overlap')
        }
      }
    }
    drafts = serve(base, plan(base, now, drafts), drafts, next)
    assert.equal(applyDrafts(base, drafts).text, now)
    // and a second pause with nothing new sends nothing
    assert.deepEqual(plan(base, now, drafts), [])
  })
}
