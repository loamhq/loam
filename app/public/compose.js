import { composing } from './keys.js'
// The composer's field, which shows markdown as it is typed.
//
// The owner, 2026-09-22: "please make sure bullet points and numbered lists
// render as MD in the text input" and, asked where: "I meant both in the input
// box and my messages after sent." The bubble half was already `markdown.js`;
// this is the other half — a `contenteditable` region that draws a bullet the
// moment `- ` is typed, a numbered item for `1. `, and `**bold**` and `code`
// the moment they close.
//
// **The markdown text is the truth and the DOM is a drawing of it.** Every
// answer this module gives is computed from a string: what is typed is read
// back out of the field as markdown (`readField`), the transforms are pure
// functions of that string and a caret offset into it (`enterAt`,
// `backspaceAt`, `insertAt`), and the field is then rebuilt from the string
// (`setMarkdown`). Nothing here reads a class, an `innerHTML` or a fragment the
// browser made: the two shapes a hostile paste could arrive in — HTML on the
// clipboard, and HTML in the text — are answered by the same rule, which is
// that **the field's nodes are built here, one `createElement` at a time, from
// characters**. A paste is taken as `text/plain` and inserted into the string;
// a `<script>` in it is four characters wide on the screen and is four
// characters wide in what is sent.
//
// Why a string and not the DOM: a rich field that edits nodes has to answer
// "what did the browser just do to my tree" on every keystroke, and browsers
// disagree. A field that re-derives one string answers instead "what does this
// text mean", which is the same question `markdown.js` answers on the other
// side of the send — so the two halves cannot drift, and the round trip is a
// test rather than a hope (`test/compose.test.js`).
//
// What is **not** provable without a browser, and is named here so it is not
// mistaken for tested: where the caret lands after the field is rebuilt. The
// arithmetic of it is pure and is tested (`visibleCaret`); the placing of it is
// a `Range` on a real selection, and there is no selection in the fixture.

import { mediaFrom } from './attach.js'

// A line that is a bullet, and a line that is numbered. The space after the
// mark is required: `-5` is a number a person typed, not a list.
const BULLET = /^\s*([-*])\s+(.*)$/
const NUMBER = /^\s*(\d+)[.)]\s+(.*)$/

// ------------------------------------------------------------- reading a line

// What the marks on a line mean, without the caller having to know the two
// regexes above.
export function lineKind(line) {
  const bullet = BULLET.exec(line)
  if (bullet) return { kind: 'bullet', mark: bullet[1], body: bullet[2], marker: line.length - bullet[2].length }
  const number = NUMBER.exec(line)
  if (number) return { kind: 'number', n: Number(number[1]), body: number[2], marker: line.length - number[2].length }
  return { kind: 'text', body: line, marker: 0 }
}

// The inline marks, left to right, flat. There is no nesting: a backtick inside
// a `**` run is a backtick, which is what "everything else is plain text" in
// this field means. An unclosed mark is its own characters, so `**bol` is five
// characters on the screen until the run closes — the same rule `markdown.js`
// applies to a message still being written.
function inlineTokens(text) {
  const out = []
  let plain = ''
  let i = 0
  const flush = () => {
    if (plain !== '') out.push({ kind: 'text', text: plain })
    plain = ''
  }
  while (i < text.length) {
    if (text[i] === '`') {
      const end = text.indexOf('`', i + 1)
      if (end > i + 1) {
        flush()
        out.push({ kind: 'code', text: text.slice(i + 1, end) })
        i = end + 1
        continue
      }
    }
    if (text.startsWith('**', i)) {
      const end = text.indexOf('**', i + 2)
      if (end > i + 2) {
        flush()
        out.push({ kind: 'bold', text: text.slice(i + 2, end) })
        i = end + 2
        continue
      }
    }
    plain += text[i]
    i += 1
  }
  flush()
  return out
}

// What a person sees when this markdown is drawn in the field: the marks gone,
// the list markers gone — an `<ol>` draws its own numbers, so they are not in
// the text — and one `\n` per line.
//
// This is the whole of "has anything changed": what the field shows is compared
// against what the text it serialises to *would* show, and they differ exactly
// when a mark has just closed or a list has just begun.
export function renderedText(md) {
  return String(md ?? '')
    .split('\n')
    .map((line) => {
      const { body } = lineKind(line)
      return inlineTokens(body)
        .map((token) => token.text)
        .join('')
    })
    .join('\n')
}

// Where a caret sitting at `caret` characters into the markdown lands on the
// screen once the markdown is drawn: the text before it, rendered, is as long
// as the visible run before the caret. A prefix that cuts a mark in half is a
// prefix with an unclosed mark in it, which renders as its own characters — so
// the answer is right while a mark is still being typed as well as after.
export function visibleCaret(md, caret) {
  const at = Math.max(0, Math.min(Number(caret) || 0, String(md ?? '').length))
  return renderedText(String(md ?? '').slice(0, at)).length
}

// ------------------------------------------------------------ drawing the field

function tagOf(node) {
  return String(node.tag ?? node.nodeName ?? '').toLowerCase()
}

function inlineNodes(text, paintMention) {
  const out = []
  for (const token of inlineTokens(text)) {
    if (token.kind === 'text') {
      for (const part of token.text.split(/((?<![\w@])@[\p{L}\p{N}_-]+)/u)) {
        if (/^@[\p{L}\p{N}_-]+$/u.test(part)) { const span = document.createElement('span'); span.className = 'mention'; span.textContent = part; paintMention?.(span, part); out.push(span) }
        else if (part) out.push(document.createTextNode(part))
      }
      continue
    }
    const node = document.createElement(token.kind === 'bold' ? 'strong' : 'code')
    node.textContent = token.text
    out.push(node)
  }
  return out
}

// The nodes for a piece of markdown. One element per line — an `<li>` in a run
// of them, a `<div>` otherwise — because a line is the unit the caret moves in
// and the unit Enter splits.
export function nodesFor(md, paintMention) {
  const out = []
  let run = null
  for (const line of String(md ?? '').split('\n')) {
    const { kind, body } = lineKind(line)
    if (kind === 'bullet' || kind === 'number') {
      const wanted = kind === 'bullet' ? 'ul' : 'ol'
      if (!run || tagOf(run) !== wanted) {
        run = document.createElement(wanted)
        out.push(run)
      }
      run.appendChild(lineNode('li', body, paintMention))
      continue
    }
    run = null
    out.push(lineNode('div', body, paintMention))
  }
  return out
}

function lineNode(tag, body, paintMention) {
  const node = document.createElement(tag)
  if (tag === 'div') node.className = 'ln'
  const kids = inlineNodes(body, paintMention)
  // An empty line is a `<br>` and not nothing: a browser gives an empty block
  // no height, so a blank line between two paragraphs would close up.
  if (kids.length === 0) node.appendChild(document.createElement('br'))
  else for (const kid of kids) node.appendChild(kid)
  return node
}

// Draw this markdown into the field, and put the caret where the markdown says.
// Everything already in the field goes: the field is a drawing of the string,
// so there is nothing in it worth keeping that the string does not say.
export function setMarkdown(el, md, caret = null) {
  while (el.firstChild) el.removeChild(el.firstChild)
  const text = String(md ?? '')
  el.__mentions = (text.match(/(?<![\w@])@[\p{L}\p{N}_-]+/gu) ?? []).join('|')
  // Empty is *empty* — no stray `<div><br></div>` — because the placeholder is
  // the stylesheet's `:empty::before` and a field holding a blank line is not
  // empty to CSS.
  if (text !== '') for (const node of nodesFor(text, el.__paintMention)) el.appendChild(node)
  if (caret !== null) placeCaret(el, visibleCaret(text, caret))
}

// ------------------------------------------------------------ reading the field

// The field, as markdown — and, if a caret is handed in, where that caret sits
// in it.
//
// `point` is `{ node, offset }`, which is what a selection's focus is. It is
// passed in rather than read here so that every answer this module gives is a
// function of its arguments; `caretPoint()` below is the one place that asks
// the browser.
//
// Three things come back:
//   md    the markdown, marks and list markers and all
//   text  what the field is showing right now, which is not the same string
//   caret where `point` is, as an offset into `md`, or `null`
export function readField(el, point = null) {
  let md = ''
  let text = ''
  let caret = null
  let started = false

  const hit = (node) => point && point.node === node
  const at = (node) => {
    if (hit(node)) caret = md.length
  }

  const inlineWalk = (node) => {
    for (const child of node.childNodes ?? []) {
      if (child.nodeType === 3) {
        const value = String(child.nodeValue ?? '')
        if (hit(child)) caret = md.length + Math.max(0, Math.min(Number(point.offset) || 0, value.length))
        md += value
        text += value
        continue
      }
      if (child.nodeType !== 1) continue
      const tag = tagOf(child)
      if (tag === 'br') {
        at(child)
        continue
      }
      const wrap = tag === 'strong' || tag === 'b' ? '**' : tag === 'code' ? '`' : ''
      md += wrap
      at(child)
      inlineWalk(child)
      md += wrap
    }
  }

  const line = (node, marker) => {
    if (started) {
      md += '\n'
      text += '\n'
    }
    started = true
    md += marker
    at(node)
    inlineWalk(node)
  }

  for (const child of el.childNodes ?? []) {
    if (child.nodeType === 3) {
      // A browser typing into an empty field often leaves the first line as a
      // bare text node rather than a block, so it is a line like any other.
      if (started) {
        md += '\n'
        text += '\n'
      }
      started = true
      const value = String(child.nodeValue ?? '')
      if (hit(child)) caret = md.length + Math.max(0, Math.min(Number(point.offset) || 0, value.length))
      md += value
      text += value
      continue
    }
    if (child.nodeType !== 1) continue
    const tag = tagOf(child)
    if (tag === 'ul' || tag === 'ol') {
      let n = 0
      for (const item of child.childNodes ?? []) {
        if (item.nodeType !== 1) continue
        n += 1
        line(item, tag === 'ul' ? '- ' : `${n}. `)
      }
      continue
    }
    if (tag === 'br') {
      if (started) {
        md += '\n'
        text += '\n'
      }
      started = true
      at(child)
      continue
    }
    line(child, '')
  }

  return { md, text, caret }
}

// ------------------------------------------------------------- the transforms

// Enter, on the markdown.
//
// Inside a list it continues the list — a new item with the same mark, and the
// tail of the line it was pressed in carried into it. On an item with nothing
// in it, it ends the list instead: that item becomes an empty line of plain
// text, which is how every editor lets a person out of a list without reaching
// for the mouse.
export function enterAt(md, caret) {
  const text = String(md ?? '')
  const at = Math.max(0, Math.min(Number(caret) || 0, text.length))
  const start = text.slice(0, at).lastIndexOf('\n') + 1
  const end = text.indexOf('\n', at) === -1 ? text.length : text.indexOf('\n', at)
  const line = lineKind(text.slice(start, end))

  if (line.kind === 'bullet' || line.kind === 'number') {
    if (line.body.trim() === '') {
      return { md: `${text.slice(0, start)}${text.slice(end)}`, caret: start }
    }
    const marker = line.kind === 'bullet' ? `${line.mark} ` : `${line.n + 1}. `
    return { md: `${text.slice(0, at)}\n${marker}${text.slice(at)}`, caret: at + 1 + marker.length }
  }
  return { md: `${text.slice(0, at)}\n${text.slice(at)}`, caret: at + 1 }
}

// Backspace at the start of an item's own text turns the item back into a line
// of text. Anywhere else this answers `null`, which means the browser's own
// backspace stands — deleting a character is not this module's business.
export function backspaceAt(md, caret) {
  const text = String(md ?? '')
  const at = Math.max(0, Math.min(Number(caret) || 0, text.length))
  const start = text.slice(0, at).lastIndexOf('\n') + 1
  const end = text.indexOf('\n', at) === -1 ? text.length : text.indexOf('\n', at)
  const raw = text.slice(start, end)
  const line = lineKind(raw)
  if (line.kind === 'text') return null
  if (at !== start + line.marker) return null
  return { md: `${text.slice(0, start)}${line.body}${text.slice(end)}`, caret: start }
}

// What a paste is: characters, at the caret. The clipboard's HTML is never
// asked for and never used.
export function insertAt(md, caret, insert) {
  const text = String(md ?? '')
  const at = Math.max(0, Math.min(Number(caret) || 0, text.length))
  const said = String(insert ?? '').replace(/\r\n?/g, '\n')
  return { md: `${text.slice(0, at)}${said}${text.slice(at)}`, caret: at + said.length }
}

// ------------------------------------------------------------------ the caret

// The one place that asks the browser where the caret is. A browser without a
// selection — which is every run of the test suite — answers `null`, and every
// caller above treats that as "leave the key to the browser".
export function caretPoint(el) {
  const selection = globalThis.getSelection?.()
  const node = selection?.focusNode ?? null
  if (!node) return null
  if (!contains(el, node)) return null
  return { node, offset: selection.focusOffset ?? 0 }
}

function contains(el, node) {
  for (let at = node; at; at = at.parentNode) if (at === el) return true
  return false
}

// Put the caret `offset` visible characters into the field. Walked rather than
// computed, because the only thing that knows how long a text node is is the
// text node.
function placeCaret(el, offset) {
  const selection = globalThis.getSelection?.()
  if (!selection || !document.createRange) return
  let left = Math.max(0, offset)
  let target = null
  const walk = (node) => {
    if (target) return
    for (const child of node.childNodes ?? []) {
      if (target) return
      if (child.nodeType === 3) {
        const length = String(child.nodeValue ?? '').length
        if (left <= length) {
          target = { node: child, offset: left }
          return
        }
        left -= length
        continue
      }
      if (child.nodeType !== 1) continue
      const tag = tagOf(child)
      if (tag === 'br') continue
      const line = tag === 'li' || tag === 'div'
      walk(child)
      if (target) return
      // One `\n` per line, and the newline is *between* lines, so it is spent
      // on the way out of a line rather than on the way in.
      if (line && child.nextSibling) {
        if (left <= 0) {
          target = { node: child, offset: child.childNodes.length }
          return
        }
        left -= 1
      }
    }
  }
  walk(el)
  const range = document.createRange()
  if (target) range.setStart(target.node, target.offset)
  else {
    range.selectNodeContents(el)
    range.collapse(false)
  }
  range.collapse(true)
  selection.removeAllRanges()
  selection.addRange(range)
}

// ------------------------------------------------------------------ the field

// Turn a node into the field: the accessors the rest of the app already uses —
// `value` is the markdown, `placeholder` is what an empty one says — and the
// four listeners that keep the drawing in step with the string.
//
// The accessors are the point. Nothing outside this module knows the field is
// not a `<textarea>`: the draft is stored, restored, dictated into and sent as
// `draft.value`, exactly as it was when it was one.
export function richField(el, { sends = () => false, changed = () => {}, send = () => {}, files = () => {}, key = () => false } = {}) {
  let placeholder = ''
  Object.defineProperty(el, 'value', {
    get: () => readField(el).md,
    set: (md) => setMarkdown(el, md),
    configurable: true,
  })
  Object.defineProperty(el, 'placeholder', {
    get: () => placeholder,
    // Written to the node only when it changes. The composer names the agent in
    // it on every redraw, and an attribute written every tick is a page that
    // touches a node it did not change — which is the one thing `morph()`
    // promises it never does, and which `test/app.test.js` fails on.
    set: (text) => {
      const said = String(text ?? '')
      if (said === placeholder) return
      placeholder = said
      el.setAttribute('data-placeholder', said)
    },
    configurable: true,
  })

  // A phone composing a word — Android's keyboard, or any IME — rewrites the
  // text node under the caret as it goes, and rebuilding the field mid-word
  // takes the composition away from it. So nothing is redrawn while one is in
  // flight, and the redraw happens at the end of it.
  el.addEventListener('compositionstart', () => {
    el.__composing = true
  })
  el.addEventListener('compositionend', () => {
    el.__composing = false
    normalise(el)
  })

  el.addEventListener('input', () => {
    normalise(el)
    changed()
  })

  el.addEventListener('keydown', (event) => {
    if (el.__composing || composing(event) || key(event)) return
    if (sends(event)) {
      event.preventDefault?.()
      send()
      return
    }
    if (event.key === 'Enter') {
      const point = caretPoint(el)
      if (!point) return
      const read = readField(el, point)
      if (read.caret === null) return
      event.preventDefault?.()
      const next = event.shiftKey ? insertAt(read.md, read.caret, '\n') : enterAt(read.md, read.caret)
      setMarkdown(el, next.md, next.caret)
      changed()
      return
    }
    if (event.key === 'Backspace') {
      const point = caretPoint(el)
      if (!point) return
      const read = readField(el, point)
      if (read.caret === null) return
      const next = backspaceAt(read.md, read.caret)
      if (!next) return
      event.preventDefault?.()
      setMarkdown(el, next.md, next.caret)
      changed()
    }
  })

  el.addEventListener('paste', (event) => {
    const said = event.clipboardData?.getData?.('text/plain') ?? ''
    // Always: the clipboard's HTML never reaches the field, whether or not
    // there is plain text beside it to put there instead.
    event.preventDefault?.()
    // A screenshot on the clipboard is a file, and goes up the way a file
    // chosen from the + menu does. It is handed out rather than handled here:
    // this module knows what a field holds and nothing about uploads, and a
    // paste carrying an image *and* text is both things, in that order.
    const images = mediaFrom(event.clipboardData)
    if (images.length > 0) files(images)
    if (said === '') return
    const point = caretPoint(el)
    const read = readField(el, point)
    const next = insertAt(read.md, read.caret ?? read.md.length, said)
    setMarkdown(el, next.md, next.caret)
    changed()
  })

  // **The other way an Android keyboard pastes a picture.** The owner, 2026-09-25:
  // "I can't paste in an image into the chat on my mobile." Gboard and the other
  // Android keyboards have two paths into a `contenteditable`, and only one of
  // them is the `paste` above: the other is a `beforeinput` whose `inputType` is
  // `insertFromPaste` and whose `dataTransfer` carries the file, with no `paste`
  // event fired at all. A long-press "paste" over the field is the common way to
  // land on it.
  //
  // So both are listened for and both hand the files to the same callback. An
  // event that carries no file is left entirely alone — `insertFromPaste` of
  // plain text is the ordinary paste and the `paste` handler above has it, and
  // preventing this one would take a person's text away on the browsers that
  // send both.
  el.addEventListener('beforeinput', (event) => {
    const type = String(event.inputType ?? '')
    if (type !== 'insertFromPaste' && type !== 'insertFromDrop') return
    const images = mediaFrom(event.dataTransfer)
    if (images.length === 0) return
    // There is a file in it, so this event is the attachment and not text: the
    // default would drop an `<img>` into the field, which is the one node in
    // this module that would carry a `src`.
    event.preventDefault?.()
    files(images)
  })

  return el
}

// What the field does on every keystroke: read the markdown out of it, and
// redraw it **only if the drawing and the markdown disagree** — which happens
// exactly when a `- ` has just been typed, or a mark has just closed. Ordinary
// typing leaves the DOM alone, and with it the caret, the undo stack and any
// composition in flight.
export function normalise(el) {
  if (el.__composing) return
  const point = caretPoint(el)
  const read = readField(el, point)
  if (read.md === '') {
    // Emptied by hand: a browser leaves the block it was typing in behind, and
    // a field holding one empty block is not `:empty`, so the placeholder would
    // never come back.
    if (el.firstChild) setMarkdown(el, '')
    return
  }
  const mentions = (read.md.match(/(?<![\w@])@[\p{L}\p{N}_-]+/gu) ?? []).join('|')
  if (read.text === renderedText(read.md) && mentions === (el.__mentions ?? '')) return
  setMarkdown(el, read.md, read.caret)
}
