// Suggesting, recorded. While a person is Suggesting, each of their own
// pending suggestions ("drafts") is a range of the editor's text carrying the
// original words it replaces. A draft is made, grown and joined only by the
// edits the person makes: an edit inside or touching a draft grows it, an edit
// anywhere else starts a new one, and every other change moves drafts by
// CodeMirror's own position mapping. Nothing here compares texts to work out
// what was typed: comparing cannot know that two changes far apart are two
// things, or which of two equal words a change belongs to (2026-11-17, after
// three rounds of fixing a comparison).
//
// Invariant: the stored text with every draft applied is the editor's text.
//
// A draft: {key, from, to, orig, ids, sent}
//   from/to  its words in the editor (editor positions)
//   orig     the stored words they replace ('' for a pure insertion)
//   ids      the server's notes for it ([first] is the one it grows; more
//            when the person joined drafts by editing across them)
//   sent     its words when last sent (null: never sent, or changed since)

import { StateField, StateEffect, Annotation, Decoration, WidgetType } from '../vendor/codemirror.js'
import { h } from './dom.js'

export const remote = Annotation.define()
export const setDrafts = StateEffect.define()
export const setRecording = StateEffect.define()

let keys = 0
const newKey = () => `k${++keys}`

// The drafts after a change made by the person.
export function record(list, changes, oldDoc, newDoc) {
  const ivs = []
  changes.iterChanges((fromA, toA) => ivs.push({ kind: 'c', from: fromA, to: toA }))
  if (!ivs.length) return { list, gone: [] }
  for (const d of list) ivs.push({ kind: 'd', from: d.from, to: d.to, d })
  ivs.sort((a, b) => a.from - b.from || a.to - b.to)
  // Clusters of intervals that overlap or touch, or lie in one word: a
  // suggestion is made of whole words, so two drafts inside one word would
  // be sent as two passages sharing letters.
  const inWord = (a, b) => b - a <= 40 && /^\w+$/u.test(oldDoc.sliceString(a, b))
  const clusters = []
  for (const iv of ivs) {
    const c = clusters[clusters.length - 1]
    if (c && (iv.from <= c.to || inWord(c.to, iv.from))) { c.to = Math.max(c.to, iv.to); c.items.push(iv) }
    else clusters.push({ from: iv.from, to: iv.to, items: [iv] })
  }
  const out = [], gone = []
  for (const c of clusters) {
    const drafts = c.items.filter(i => i.kind === 'd').map(i => i.d)
    const changed = c.items.some(i => i.kind === 'c')
    if (!changed) {
      for (const d of drafts) {
        const from = changes.mapPos(d.from, 1), to = Math.max(from, changes.mapPos(d.to, -1))
        out.push({ ...d, from, to })
      }
      continue
    }
    // The original words of the whole cluster: drafts give theirs, the
    // rest is stored text (outside drafts the editor's text is the stored).
    let orig = '', at = c.from
    for (const d of drafts.sort((a, b) => a.from - b.from)) {
      orig += oldDoc.sliceString(at, d.from) + d.orig
      at = d.to
    }
    orig += oldDoc.sliceString(at, c.to)
    const from = changes.mapPos(c.from, -1), to = changes.mapPos(c.to, 1)
    const text = newDoc.sliceString(from, to)
    const ids = drafts.flatMap(d => d.ids || [])
    // Letters a draft shares with its original words at an edge are not
    // trimmed off: that would put its edge inside a word, so the next key
    // typed there would start a second draft sharing a letter with it.
    if (text === orig) { gone.push(...ids); continue }
    out.push({ key: drafts[0]?.key || newKey(), from, to, orig, ids, sent: null })
  }
  out.sort((a, b) => a.from - b.from)
  return { list: out, gone }
}

// Each draft's place in the stored text.
export function inBase(list, doc) {
  let shift = 0
  return [...list].sort((a, b) => a.from - b.from).map(d => {
    const start = d.from - shift
    const r = { d, start, end: start + d.orig.length, text: doc.sliceString(d.from, d.to) }
    shift += (d.to - d.from) - d.orig.length
    return r
  })
}

// The stored text with the drafts applied: the editor's text, always.
export function applied(base, list, doc) {
  let out = '', at = 0
  for (const r of inBase(list, doc)) { out += base.slice(at, r.start) + r.text; at = r.end }
  return out + base.slice(at)
}

const W = /\w/u, S = /\s/u

// What a draft is sent as: a passage of the stored text (never empty, whole
// words, anchored on the word before an insertion, or after it at the top)
// and what it becomes. `lo`/`hi` keep it clear of its neighbours' passages.
export function passage(base, r, lo = 0, hi = base.length) {
  let a = r.start, b = r.end
  const t = r.text
  while (a > lo && W.test(base[a - 1]) && (W.test(t[0] || '') || W.test(base[a] || '') && b > a)) a--
  while (b < hi && W.test(base[b]) && (W.test(t[t.length - 1] || '') || W.test(base[b - 1] || '') && b > a)) b++
  if (a === b) {
    let s = a
    while (s > lo && S.test(base[s - 1])) s--
    let w = s
    while (w > lo && !S.test(base[w - 1])) w--
    if (w < s) a = w
    else {
      let e = b
      while (e < hi && S.test(base[e])) e++
      let f = e
      while (f < hi && !S.test(base[f])) f++
      if (f === e) return null              // nothing to anchor on
      b = f
    }
  }
  return { start: a, end: b, quote: base.slice(a, b), replacement: base.slice(a, r.start) + t + base.slice(r.end, b) }
}

// -- the field ---------------------------------------------------------------

export const draftsField = StateField.define({
  create: () => ({ list: [], gone: [], on: false }),
  update(v, tr) {
    for (const e of tr.effects) {
      if (e.is(setRecording)) v = { ...v, on: e.value, list: e.value ? v.list : [], gone: [] }
      if (e.is(setDrafts)) v = { ...v, ...e.value }
    }
    if (!tr.docChanged) return v
    if (!v.on || tr.annotation(remote)) {
      const list = v.list.map(d => {
        const from = tr.changes.mapPos(d.from, 1), to = Math.max(from, tr.changes.mapPos(d.to, -1))
        return { ...d, from, to }
      })
      return { ...v, list }
    }
    const r = record(v.list, tr.changes, tr.startState.doc, tr.state.doc)
    return { ...v, list: r.list, gone: [...v.gone, ...r.gone] }
  },
})

// Struck words, without striking the space around them: a space is kept
// (unstruck) only where the text beside the widget has none, so a deletion
// leaves neither a struck gap nor a double space.
class DelWidget extends WidgetType {
  constructor(text, id, extra, spaceBefore = true, spaceAfter = true) { super(); this.text = text; this.id = id; this.extra = extra; this.sb = spaceBefore; this.sa = spaceAfter }
  eq(o) { return o.text === this.text && o.id === this.id && o.extra === this.extra && o.sb === this.sb && o.sa === this.sa }
  toDOM() {
    const t = this.text.replace(/\n+$/, '')
    const core = t.trim()
    const lead = /^\s/.test(t) && !this.sb ? ' ' : ''
    const trail = /\s$/.test(t) && !this.sa ? ' ' : ''
    return h('span', { class: 'dc-sg-live-delw', 'data-note': this.id || null }, lead,
      h('del', { class: `dc-sg-live-del${this.extra}`, 'data-note': this.id || null, text: core || t }), trail)
  }
  ignoreEvent() { return false }
}

// The drafts drawn: the original words struck, the draft's words in the
// accent. `look(d)` gives {id, on, peek} for the card it belongs to.
export function draftDecos(list, look = () => null, doc = null) {
  const out = []
  for (const d of list) {
    const n = look(d)
    const extra = n ? `${n.on ? ' on' : ''}${n.peek ? ' peek' : ''}` : ''
    // A space turned into a line break (Enter at a space) is a split, not a
    // deletion: nothing is struck.
    const split = d.orig && !d.orig.trim() && !/\n/.test(d.orig) && doc && d.to > d.from && /\n/.test(doc.sliceString(d.from, d.to)) && !doc.sliceString(d.from, d.to).trim()
    if (d.orig && !split) {
      // A deleted paragraph is drawn at the end of the line before it, not
      // at the start of the next one (where a heading would give it its type).
      let at = d.from
      if (doc && /\n$/.test(d.orig) && at > 0 && doc.sliceString(at - 1, at) === '\n') at = doc.lineAt(at - 1).to
      const before = doc && at > 0 ? /\s/.test(doc.sliceString(at - 1, at)) : true
      const after = doc && d.to < doc.length ? /\s/.test(doc.sliceString(d.to, d.to + 1)) : true
      out.push(Decoration.widget({ widget: new DelWidget(d.orig, n?.id, extra, before, after), side: -1 }).range(at))
    }
    if (d.to > d.from) out.push(Decoration.mark({ class: `dc-sg-live-ins${extra}`, attributes: n?.id ? { 'data-note': n.id } : {} }).range(d.from, d.to))
  }
  return Decoration.set(out, true)
}

// Every draft's passage, left to right, each kept clear of the one before it
// (its passage, not just its words: a passage widens to whole words and
// anchors an insertion on a word) and of the next draft's words.
export function passages(base, list, doc) {
  const rs = inBase(list, doc)
  const out = []
  let lo = 0
  rs.forEach((r, i) => {
    const hi = i + 1 < rs.length ? rs[i + 1].start : base.length
    const p = passage(base, r, Math.min(lo, r.start), hi)
    out.push({ r, p })
    if (p) lo = Math.max(lo, p.end)
    else lo = Math.max(lo, r.end)
  })
  return out
}
