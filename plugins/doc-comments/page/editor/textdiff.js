// The page's half of the text differences: turning one text into another as
// a few small changes, so the caret and the notes' passages map through a
// save's answer instead of jumping. Tokens are words, spaces and marks, as on
// the server (server/doccomments/textdiff.py).

const TOKEN = /\w+|[^\S\n]+|\n|[^\w\s]/gu

export function tokens(text) {
  return text.match(TOKEN) || []
}

// The changes ({from, to, insert}, in `a`'s positions) that turn `a` into `b`.
// Common head and tail first; what is left is compared token by token with a
// longest-common-subsequence table when it is small, and replaced whole when
// it is not (a whole-document rewrite gains nothing from finer changes).
export function changes(a, b) {
  if (a === b) return []
  let head = 0
  const n = Math.min(a.length, b.length)
  while (head < n && a.charCodeAt(head) === b.charCodeAt(head)) head++
  let tail = 0
  while (tail < n - head && a.charCodeAt(a.length - 1 - tail) === b.charCodeAt(b.length - 1 - tail)) tail++
  const ma = a.slice(head, a.length - tail)
  const mb = b.slice(head, b.length - tail)
  if (!ma || !mb) return [{ from: head, to: head + ma.length, insert: mb }]
  const ta = tokens(ma), tb = tokens(mb)
  if (ta.length * tb.length > 4_000_000) return [{ from: head, to: head + ma.length, insert: mb }]
  const ops = lcsOps(ta, tb)
  const out = []
  let pa = head
  let pending = null
  for (const op of ops) {
    if (op.t === '=') {
      if (pending) { out.push(pending); pending = null }
      pa += op.s.length
    } else if (op.t === '-') {
      if (!pending) pending = { from: pa, to: pa, insert: '' }
      pending.to += op.s.length
      pa += op.s.length
    } else {
      if (!pending) pending = { from: pa, to: pa, insert: '' }
      pending.insert += op.s
    }
  }
  if (pending) out.push(pending)
  return out
}

function lcsOps(a, b) {
  const m = a.length, n = b.length
  const w = n + 1
  const t = new Uint32Array((m + 1) * w)
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      t[i * w + j] = a[i] === b[j] ? t[(i + 1) * w + j + 1] + 1 : Math.max(t[(i + 1) * w + j], t[i * w + j + 1])
    }
  }
  const ops = []
  let i = 0, j = 0
  while (i < m && j < n) {
    if (a[i] === b[j]) { ops.push({ t: '=', s: a[i] }); i++; j++ }
    else if (t[(i + 1) * w + j] >= t[i * w + j + 1]) { ops.push({ t: '-', s: a[i] }); i++ }
    else { ops.push({ t: '+', s: b[j] }); j++ }
  }
  while (i < m) ops.push({ t: '-', s: a[i++] })
  while (j < n) ops.push({ t: '+', s: b[j++] })
  return ops
}

// Hunks of a suggestion draft: the original text and the edited one, as
// {from, to, insert} in the original's positions (what `changes` gives).
export const hunks = changes

// -- positions: the server's and the editor's --------------------------------
//
// The server counts positions in Unicode code points of the file's raw text.
// The editor counts UTF-16 units, and in a file whose lines all end in CRLF it
// holds each line break as one position (CodeMirror's line separator), so its
// working text ("editor space") is the raw text with CRLF as LF. `posMap(raw,
// crlf)` converts between the two for one text; both directions are the
// identity for the usual file (no CRLF, nothing outside the BMP).

export function editorText(raw, crlf) {
  return crlf ? raw.replace(/\r\n/g, '\n') : raw
}

export function posMap(raw, crlf) {
  const astral = /[\uD800-\uDBFF][\uDC00-\uDFFF]/.test(raw)
  const breaks = []
  if (crlf) {
    for (let i = raw.indexOf('\r\n'); i >= 0; i = raw.indexOf('\r\n', i + 2)) breaks.push(i)
  }
  if (!astral && !breaks.length) {
    return { toEditor: (cp) => cp, toServer: (pos) => pos, slice: (a, b) => raw.slice(a, b), identity: true }
  }
  // code point -> UTF-16 index in raw, and back
  const u16 = (cp) => {
    if (!astral) return cp
    let i = 0, n = 0
    while (n < cp && i < raw.length) {
      const c = raw.charCodeAt(i)
      i += c >= 0xD800 && c <= 0xDBFF && i + 1 < raw.length ? 2 : 1
      n++
    }
    return i
  }
  const cpOf = (idx) => {
    if (!astral) return idx
    let n = 0
    for (let i = 0; i < idx && i < raw.length; i++) {
      const c = raw.charCodeAt(i)
      if (c >= 0xD800 && c <= 0xDBFF && i + 1 < raw.length) i++
      n++
    }
    return n
  }
  // raw UTF-16 index <-> editor position: each CRLF before it counts once
  const before = (idx) => {
    let lo = 0, hi = breaks.length
    while (lo < hi) { const m = (lo + hi) >> 1; if (breaks[m] < idx) lo = m + 1; else hi = m }
    return lo
  }
  // Each CRLF wholly before the index is one position, not two; an index
  // between a CRLF's two characters is the position of the break itself.
  const rawToEditor = (idx) => idx - before(idx)
  const editorToRaw = (pos) => {
    let lo = 0, hi = breaks.length
    // the number of breaks whose editor position is below pos
    while (lo < hi) { const m = (lo + hi) >> 1; if (breaks[m] - m < pos) lo = m + 1; else hi = m }
    return pos + lo
  }
  return {
    toEditor: (cp) => rawToEditor(u16(cp)),
    toServer: (pos) => cpOf(editorToRaw(pos)),
    slice: (a, b) => raw.slice(editorToRaw(a), editorToRaw(b)),
    identity: false,
  }
}

// -- how a suggestion is drawn -------------------------------------------------
//
// A suggestion names a passage and what it becomes. Typed words are anchored
// on the word next to them (a passage cannot be empty), so "order." becoming
// "order. She smiled." is an insertion, and "very good" becoming "good" a
// deletion. Words the two share at either end are left alone when what is
// left is only added or only removed: `{from, to, insert}`, offsets in the
// passage. Anything else is drawn as the whole passage replaced.
export function suggestionShape(quote, replacement) {
  const a = tokens(quote), b = tokens(replacement)
  let p = 0
  while (p < a.length && p < b.length && a[p] === b[p]) p++
  let q = 0
  while (q < a.length - p && q < b.length - p && a[a.length - 1 - q] === b[b.length - 1 - q]) q++
  const from = a.slice(0, p).join('').length
  const to = quote.length - a.slice(a.length - q).join('').length
  const insert = b.slice(p, b.length - q).join('')
  if (from === to || !insert) return { from, to, insert, kind: from === to ? 'insert' : 'delete' }
  return { from: 0, to: quote.length, insert: replacement, kind: 'replace' }
}
