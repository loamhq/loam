// Suggesting, kept as real text. While a person is Suggesting, their own open
// suggestions ("drafts") are applied to the editor's text and drawn as
// suggestions over it, so the caret always sits in real text and typing,
// pausing and typing again is one run of edits. The server holds each draft
// as an ordinary suggestion: a pause only creates, grows or withdraws them.
//
// Everything here is a pure function of texts and ranges (positions in the
// editor's text), so it is tested on its own (test/drafts.test.mjs).

import { changes } from './textdiff.js'

const W = /\w/u
const S = /\s/u

// A draft: {id, from, to, replacement}, `from`/`to` in the stored text (the
// "base"). The text with the drafts applied, and where each now sits in it.
export function applyDrafts(base, drafts) {
  const list = [...drafts].filter(d => d.from != null && d.to != null && d.from <= d.to && d.to <= base.length).sort((a, b) => a.from - b.from || a.to - b.to)
  let out = '', at = 0
  const placed = []
  for (const d of list) {
    if (d.from < at) continue                       // overlaps one already applied
    out += base.slice(at, d.from)
    const start = out.length
    out += d.replacement
    placed.push({ ...d, start, end: out.length })
    at = d.to
  }
  out += base.slice(at)
  return { text: out, placed }
}

// The changed runs between the stored text and the editor's, each widened to
// whole words and anchored on a word (a passage is never empty): `[{from, to,
// replacement}]` in base positions, merged where they touch.
export function runs(base, now) {
  const hunks = changes(base, now)
  const out = []
  for (const c of hunks) {
    let from = c.from, to = c.to
    // Whole words: a run that starts or ends inside a word takes all of it.
    while (from > 0 && W.test(base[from - 1]) && (to > from ? W.test(base[from]) : W.test(c.insert[0] || base[from] || ''))) from--
    while (to < base.length && W.test(base[to]) && (to > from ? W.test(base[to - 1]) : W.test(c.insert[c.insert.length - 1] || base[to - 1] || ''))) to++
    if (to === from) {
      // A pure insertion: anchored on the word before it, or after it at the
      // start of the text.
      let s = from
      while (s > 0 && S.test(base[s - 1])) s--
      let w = s
      while (w > 0 && !S.test(base[w - 1])) w--
      if (w < s) from = w
      else {
        let e = to
        while (e < base.length && !S.test(base[e])) e++
        if (e === to) continue                      // nothing to anchor on
        to = e
      }
    }
    out.push({ from, to })
  }
  out.sort((a, b) => a.from - b.from)
  const merged = []
  for (const r of out) {
    const last = merged[merged.length - 1]
    if (last && r.from <= last.to) last.to = Math.max(last.to, r.to)
    else merged.push({ ...r })
  }
  return merged.map(r => ({ ...r, replacement: rebuild(base, hunks, r.from, r.to) }))
}

// The editor's text for base range [from, to]: the base with every change
// inside it applied. A change lies wholly inside a run (runs only widen).
function rebuild(base, hunks, from, to) {
  let out = '', at = from
  for (const c of hunks) {
    if (c.to < from || c.from > to) continue
    out += base.slice(at, c.from) + c.insert
    at = c.to
  }
  return out + base.slice(at, to)
}

// What a pause sends: for each run, the draft it grows (or a new one), and the
// drafts no run touches any more (typed and then deleted), withdrawn.
// Returns `[{op: 'add', from, to, replacement, supersedes?} | {op: 'withdraw', id}]`.
export function plan(base, now, drafts) {
  const ops = []
  const used = new Set()
  let rs = runs(base, now)
  // A run that touches a draft covers all of it, so the draft's words are in
  // what replaces it.
  for (let pass = 0; pass < 3; pass++) {
    let grew = false
    rs = rs.map(r => {
      let { from, to } = r
      for (const d of drafts) if (d.from <= to && from <= d.to) { if (d.from < from) { from = d.from; grew = true } if (d.to > to) { to = d.to; grew = true } }
      return { from, to }
    })
    rs.sort((a, b) => a.from - b.from)
    const m = []
    for (const r of rs) { const l = m[m.length - 1]; if (l && r.from <= l.to) { l.to = Math.max(l.to, r.to); grew = true } else m.push({ ...r }) }
    rs = m
    if (!grew) break
  }
  const hunks = changes(base, now)
  for (const r of rs) {
    const replacement = rebuild(base, hunks, r.from, r.to)
    const touching = drafts.filter(d => d.from <= r.to && r.from <= d.to && !used.has(d.id))
    const same = touching.find(d => d.from === r.from && d.to === r.to && d.replacement === replacement)
    if (replacement === base.slice(r.from, r.to)) continue      // back to how it was
    if (same && touching.length === 1) { used.add(same.id); continue }
    const first = touching[0]
    for (const d of touching) used.add(d.id)
    ops.push({ op: 'add', from: r.from, to: r.to, replacement, ...(first ? { supersedes: first.id } : {}), withdraws: touching.slice(1).map(d => d.id) })
  }
  for (const d of drafts) if (!used.has(d.id)) ops.push({ op: 'withdraw', id: d.id })
  return ops
}
