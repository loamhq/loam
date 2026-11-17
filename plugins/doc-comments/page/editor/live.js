// The editing surface: CodeMirror over the Markdown source, drawn so that it
// reads as the rendered doc. The file's text is the editor's text, byte for
// byte; what is drawn differently is only decoration:
//
// - Markdown's marks (`#`, `**`, `` ` ``, `[`…`](url)`, `>`) are hidden except
//   where the caret or selection is, so typed Markdown still works and can be
//   seen while it is being written.
// - Blocks that read better drawn -- the frontmatter (as a quiet Properties
//   line), tables, a horizontal rule, an embedded picture -- are drawn as
//   widgets while the caret is outside them, and as their source inside.
// - Task boxes are real checkboxes that tick the line.
// - Notes' passages, suggestions and authors are decoration layers fed by the
//   editor's controller (editor.js) through state effects.

import {
  EditorState, StateField, StateEffect, RangeSetBuilder, EditorView, Decoration, WidgetType, ViewPlugin,
  Language, defineLanguageFacet, syntaxTree, markdownParser, GFM,
} from '../vendor/codemirror.js'
import { h } from './dom.js'
import { suggestionShape } from './textdiff.js'

// -- the language ------------------------------------------------------------

const facet = defineLanguageFacet({ commentTokens: { block: { open: '<!--', close: '-->' } } })
export const markdownLanguage = new Language(facet, markdownParser.configure([GFM]), [], 'markdown')

// -- what is around the caret -------------------------------------------------

export const modeField = StateField.define({
  create: () => 'edit',
  update(v, tr) {
    for (const e of tr.effects) if (e.is(setMode)) return e.value
    return v
  },
})
export const setMode = StateEffect.define()

// Whether the editor has focus: marks show around the caret only then, so a
// doc opened and not yet touched reads as rendered throughout.
export const setFocused = StateEffect.define()
export const focusField = StateField.define({
  create: () => false,
  update(v, tr) {
    for (const e of tr.effects) if (e.is(setFocused)) return e.value
    return v
  },
})

function touches(state, from, to) {
  if (state.field(modeField, false) === 'view') return false
  if (!state.field(focusField, false)) return false
  for (const r of state.selection.ranges) if (r.from <= to && r.to >= from) return true
  return false
}

function lineTouched(state, from, to) {
  if (state.field(modeField, false) === 'view') return false
  const doc = state.doc
  const a = doc.lineAt(from).from, b = doc.lineAt(to).to
  return touches(state, a, b)
}

// -- frontmatter ----------------------------------------------------------------

export function frontmatterRange(doc) {
  const text = doc.sliceString(0, Math.min(doc.length, 20000))
  const m = /^---\r?\n(?:[\s\S]*?\r?\n)?---[ \t]*(?=\r?\n|$)/.exec(text)
  return m ? { from: 0, to: m[0].length } : null
}

const HIDDEN_KEYS = new Set(['loam_doc_id'])

// Each block widget says roughly how tall it is: until CodeMirror has drawn
// it, that is all it knows, and a table or a picture counted as one line made
// a long doc grow by thousands of pixels as it was scrolled (and left Viewing
// at the end of one almost blank).
class PropertiesWidget extends WidgetType {
  constructor(source) { super(); this.source = source }
  eq(o) { return o.source === this.source }
  get estimatedHeight() { return 44 }
  toDOM(view) {
    const rows = []
    for (const line of this.source.split(/\r?\n/).slice(1, -1)) {
      const m = /^([A-Za-z0-9_.-]+)\s*:\s*(.*)$/.exec(line)
      if (m && !HIDDEN_KEYS.has(m[1])) rows.push([m[1], m[2].replace(/^["']|["']$/g, '')])
      else if (!m && rows.length && /^\s+-\s+/.test(line)) rows[rows.length - 1][1] += (rows[rows.length - 1][1] ? ', ' : '') + line.replace(/^\s+-\s+/, '')
    }
    const wrap = h('div', { class: 'dc-props', title: 'Properties: click to edit' })
    const head = h('button', { class: 'dc-props-head', type: 'button' },
      h('span', { class: 'dc-props-label', text: 'Properties' }),
      h('span', { class: 'dc-props-keys', text: rows.map(r => r[0]).join(' · ') || 'none' }))
    const body = h('div', { class: 'dc-props-body' },
      rows.map(([k, v]) => h('div', { class: 'dc-props-row' }, h('span', { class: 'dc-props-k', text: k }), h('span', { class: 'dc-props-v', text: v.replace(/\[\[([^\]|]+)(\|[^\]]*)?\]\]/g, '$1') }))))
    head.addEventListener('mousedown', (e) => {
      e.preventDefault()
      wrap.classList.toggle('open')
    })
    body.addEventListener('mousedown', (e) => {
      e.preventDefault()
      if (view.state.readOnly) return
      view.dispatch({ selection: { anchor: Math.min(this.source.indexOf('\n') + 1, this.source.length) } })
      view.focus()
    })
    wrap.append(head, body)
    return wrap
  }
  ignoreEvent() { return true }
}

// -- tables ----------------------------------------------------------------------

function cellsOf(line) {
  let s = line.trim()
  if (s.startsWith('|')) s = s.slice(1)
  if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1)
  return s.split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, '|'))
}

function plainInline(s) {
  return s.replace(/\*\*([^*]+)\*\*/g, '$1').replace(/__([^_]+)__/g, '$1').replace(/(?<![\w*])\*([^*]+)\*(?!\w)/g, '$1')
    .replace(/`([^`]+)`/g, '$1').replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, '$2').replace(/\[\[([^\]]+)\]\]/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/~~([^~]+)~~/g, '$1')
}

class TableWidget extends WidgetType {
  constructor(source, from) { super(); this.source = source; this.from = from }
  eq(o) { return o.source === this.source }
  get estimatedHeight() { return 22 + 34 * Math.max(1, this.source.split(/\r?\n/).filter(l => l.trim()).length - 1) }
  toDOM(view) {
    const lines = this.source.split(/\r?\n/).filter(l => l.trim())
    const head = cellsOf(lines[0] || '')
    const align = cellsOf(lines[1] || '').map(c => (c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : ''))
    const body = lines.slice(2).map(cellsOf)
    const table = h('table', { class: 'dc-table' },
      h('thead', {}, h('tr', {}, head.map((c, i) => h('th', { class: align[i] ? `al-${align[i]}` : null, text: plainInline(c) })))),
      h('tbody', {}, body.map(r => h('tr', {}, head.map((_, i) => h('td', { class: align[i] ? `al-${align[i]}` : null, text: plainInline(r[i] ?? '') }))))))
    const wrap = h('div', { class: 'dc-tablewrap' }, table)
    wrap.addEventListener('mousedown', (e) => {
      if (view.state.field(modeField, false) === 'view') return
      e.preventDefault()
      view.dispatch({ selection: { anchor: this.from + 1 } })
      view.focus()
    })
    return wrap
  }
  ignoreEvent() { return true }
}

class RuleWidget extends WidgetType {
  eq() { return true }
  get estimatedHeight() { return 29 }
  toDOM() { return h('div', { class: 'dc-hr', 'aria-hidden': 'true' }, h('span')) }
}

class EmbedWidget extends WidgetType {
  constructor(target, embed) { super(); this.target = target; this.embed = embed }
  eq(o) { return o.target === this.target }
  get estimatedHeight() { return EmbedWidget.seen.get(this.target) || 300 }
  toDOM(view) {
    const wrap = h('span', { class: 'dc-embed' })
    let tries = 0
    const fill = () => {
      let node = null
      try { node = this.embed?.(this.target) } catch { node = null }
      if (node && node.tagName === 'IMG') {
        const img = node.cloneNode(true)
        img.addEventListener('load', () => { const hgt = wrap.offsetHeight; if (hgt) EmbedWidget.seen.set(this.target, hgt) }, { once: true })
        // A picture changes the line's height when it arrives; CodeMirror is
        // told, so what is placed by its height map (the gutter's marks, the
        // margin's notes below it) moves with it.
        img.addEventListener('load', () => view.requestMeasure(), { once: true })
        wrap.replaceChildren(img)
        view.requestMeasure()
        return
      }
      wrap.replaceChildren(h('span', { class: 'dc-embed-name', text: `![[${this.target}]]` }))
      if (node && tries++ < 20) setTimeout(fill, 500)
    }
    fill()
    return wrap
  }
  ignoreEvent() { return false }
}

class BulletWidget extends WidgetType {
  eq() { return true }
  toDOM() { return h('span', { class: 'dc-bullet', 'aria-hidden': 'true', text: '•' }) }
}

class TaskWidget extends WidgetType {
  constructor(checked, at) { super(); this.checked = checked; this.at = at }
  eq(o) { return o.checked === this.checked && o.at === this.at }
  toDOM(view) {
    const box = h('span', { class: `dc-cbx${this.checked ? ' on' : ''}`, role: 'checkbox', 'aria-checked': String(this.checked), tabindex: '-1' })
    box.addEventListener('mousedown', (e) => {
      e.preventDefault()
      e.stopPropagation()
      if (view.state.facet(EditorState.readOnly) && view.state.field(modeField, false) !== 'view') return
      const ch = view.state.sliceDoc(this.at + 1, this.at + 2)
      view.dispatch({ changes: { from: this.at + 1, to: this.at + 2, insert: ch === ' ' ? 'x' : ' ' }, userEvent: 'input.tick' })
    })
    return box
  }
  ignoreEvent() { return true }
}

// Blocks drawn as widgets live in a state field: a block decoration may not
// come from a view plugin.
function blockDecos(state, embed) {
  const b = new RangeSetBuilder()
  const fm = frontmatterRange(state.doc)
  const items = []
  if (fm && !touches(state, fm.from, fm.to)) {
    items.push([fm.from, fm.to, Decoration.replace({ widget: new PropertiesWidget(state.sliceDoc(fm.from, fm.to)), block: true })])
  }
  const tree = syntaxTree(state)
  tree.iterate({
    enter(node) {
      if (fm && node.from < fm.to) return node.name === 'Document'
      if (node.name === 'Table') {
        const from = state.doc.lineAt(node.from).from, to = state.doc.lineAt(node.to).to
        if (!lineTouched(state, from, to)) items.push([from, to, Decoration.replace({ widget: new TableWidget(state.sliceDoc(from, to), from), block: true })])
        return false
      }
      if (node.name === 'HorizontalRule') {
        const line = state.doc.lineAt(node.from)
        if (!lineTouched(state, line.from, line.to)) items.push([line.from, line.to, Decoration.replace({ widget: new RuleWidget(), block: true })])
        return false
      }
      return true
    },
  })
  // Embedded pictures on a line of their own.
  for (let i = 1; i <= state.doc.lines; i++) {
    const line = state.doc.line(i)
    if (fm && line.from < fm.to) continue
    const m = /^\s*!\[\[([^\]|#]+\.(?:png|jpe?g|gif|webp|avif))(?:\|[^\]]*)?\]\]\s*$/i.exec(line.text)
    if (m && !lineTouched(state, line.from, line.to)) items.push([line.from, line.to, Decoration.replace({ widget: new EmbedWidget(m[1], embed), block: true })])
  }
  items.sort((a, c) => a[0] - c[0])
  let last = -1
  for (const [f, t, d] of items) {
    if (f < last) continue
    b.add(f, t, d)
    last = t
  }
  return b.finish()
}

export function blocks(embed) {
  return StateField.define({
    create: (state) => blockDecos(state, embed),
    update(v, tr) {
      if (tr.docChanged || tr.selection || tr.effects.some(e => e.is(setMode) || e.is(setFocused)) || syntaxTree(tr.state) !== syntaxTree(tr.startState)) return blockDecos(tr.state, embed)
      return v
    },
    provide: (f) => EditorView.decorations.from(f),
  })
}

// -- inline marks: a view plugin over what is visible ------------------------------

const hide = Decoration.replace({})
const mk = (cls) => Decoration.mark({ class: cls })
const lineDeco = (cls) => Decoration.line({ class: cls })

const CALLOUT = /^(\s*>\s*)\[!([A-Za-z-]+)\][+-]?\s?/

function inlineDecos(view) {
  const { state } = view
  const out = []
  const fm = frontmatterRange(state.doc)
  const add = (from, to, deco) => { if (to >= from) out.push(deco.range(from, to)) }
  for (const { from, to } of view.visibleRanges) {
    syntaxTree(state).iterate({
      from, to,
      enter(node) {
        const n = node.name
        if (fm && node.from < fm.to) return n === 'Document'
        if (/^ATXHeading(\d)$/.test(n)) {
          const level = n.slice(-1)
          const line = state.doc.lineAt(node.from)
          add(line.from, line.from, lineDeco(`dc-h dc-h${level}`))
          return true
        }
        if (/^SetextHeading(\d)$/.test(n)) {
          const level = n.slice(-1)
          const first = state.doc.lineAt(node.from)
          add(first.from, first.from, lineDeco(`dc-h dc-h${level}`))
          return true
        }
        if (n === 'HeaderMark') {
          const line = state.doc.lineAt(node.from)
          const parent = node.node.parent?.name || ''
          if (parent.startsWith('SetextHeading')) {
            if (!lineTouched(state, line.from, line.to)) add(line.from, line.from, lineDeco('dc-setext-mark'))
            return false
          }
          if (!lineTouched(state, line.from, line.to)) {
            let end = node.to
            if (state.sliceDoc(end, end + 1) === ' ') end++
            add(node.from, end, hide)
          } else add(node.from, node.to, mk('dc-mark'))
          return false
        }
        if (n === 'StrongEmphasis') { add(node.from, node.to, mk('dc-strong')); return true }
        if (n === 'Emphasis') { add(node.from, node.to, mk('dc-em')); return true }
        if (n === 'Strikethrough') { add(node.from, node.to, mk('dc-strike')); return true }
        if (n === 'EmphasisMark' || n === 'StrikethroughMark') {
          const p = node.node.parent
          if (p && !touches(state, p.from, p.to)) add(node.from, node.to, hide)
          else add(node.from, node.to, mk('dc-mark'))
          return false
        }
        if (n === 'InlineCode') {
          add(node.from, node.to, mk('dc-code'))
          const inner = !touches(state, node.from, node.to)
          let c = node.node.firstChild
          while (c) {
            if (c.name === 'CodeMark') add(c.from, c.to, inner ? hide : mk('dc-mark'))
            c = c.nextSibling
          }
          return false
        }
        if (n === 'Link' || n === 'Image') {
          const shown = touches(state, node.from, node.to)
          let c = node.node.firstChild
          const marks = []
          let url = null
          while (c) {
            if (c.name === 'LinkMark') marks.push(c)
            if (c.name === 'URL') url = c
            c = c.nextSibling
          }
          if (marks.length >= 2) {
            add(marks[0].to, marks[1].from, mk(n === 'Image' ? 'dc-imgalt' : 'dc-link'))
            if (!shown) {
              add(node.from, marks[0].to, hide)
              add(marks[1].from, node.to, hide)
            } else {
              for (const m of marks) add(m.from, m.to, mk('dc-mark'))
              if (url) add(url.from, url.to, mk('dc-url'))
            }
          }
          return false
        }
        if (n === 'URL') { add(node.from, node.to, mk('dc-link')); return false }
        if (n === 'FencedCode' || n === 'CodeBlock') {
          const first = state.doc.lineAt(node.from).number
          const last = state.doc.lineAt(node.to).number
          for (let i = first; i <= last; i++) {
            const line = state.doc.line(i)
            const cls = ['dc-pre']
            if (i === first) cls.push('dc-pre-first')
            if (i === last) cls.push('dc-pre-last')
            if (n === 'FencedCode' && (i === first || i === last)) cls.push('dc-pre-fence')
            add(line.from, line.from, lineDeco(cls.join(' ')))
          }
          return false
        }
        if (n === 'Blockquote') {
          const first = state.doc.lineAt(node.from)
          const call = CALLOUT.exec(first.text)
          const lastN = state.doc.lineAt(node.to).number
          for (let i = first.number; i <= lastN; i++) {
            const line = state.doc.line(i)
            const cls = call ? ['dc-callout', `dc-callout-${call[2].toLowerCase()}`] : ['dc-quote']
            if (i === first.number) cls.push('dc-q-first')
            if (i === lastN) cls.push('dc-q-last')
            add(line.from, line.from, lineDeco(cls.join(' ')))
          }
          if (call && !lineTouched(state, first.from, first.to)) {
            add(first.from + call[1].length, first.from + call[0].length, Decoration.replace({ widget: new CalloutTitle(call[2]) }))
          }
          return true
        }
        if (n === 'QuoteMark') {
          const line = state.doc.lineAt(node.from)
          if (!lineTouched(state, line.from, line.to)) {
            let end = node.to
            if (state.sliceDoc(end, end + 1) === ' ') end++
            add(node.from, end, hide)
          } else add(node.from, node.to, mk('dc-mark'))
          return false
        }
        if (n === 'ListMark') {
          const item = node.node.parent
          const task = item?.getChild('Task')
          const text = state.sliceDoc(node.from, node.to)
          const line = state.doc.lineAt(node.from)
          const near = lineTouched(state, line.from, line.to)
          if (task) {
            const end = state.sliceDoc(node.to, node.to + 1) === ' ' ? node.to + 1 : node.to
            if (!near) add(node.from, end, hide)
            else add(node.from, node.to, mk('dc-mark'))
          } else if (/^[-*+]$/.test(text) && !near) {
            add(node.from, node.to, Decoration.replace({ widget: new BulletWidget() }))
          } else add(node.from, node.to, mk('dc-listmark'))
          return false
        }
        if (n === 'TaskMarker') {
          const checked = /x/i.test(state.sliceDoc(node.from, node.to))
          const end = state.sliceDoc(node.to, node.to + 1) === ' ' ? node.to + 1 : node.to
          add(node.from, end, Decoration.replace({ widget: new TaskWidget(checked, node.from) }))
          if (checked) {
            const line = state.doc.lineAt(node.from)
            add(end, line.to, mk('dc-done'))
          }
          return false
        }
        if (n === 'BulletList' || n === 'OrderedList') return true
        if (n === 'ListItem') {
          const line = state.doc.lineAt(node.from)
          const depth = (() => { let d = 0, p = node.node.parent; while (p) { if (p.name === 'BulletList' || p.name === 'OrderedList') d++; p = p.parent } return d })()
          add(line.from, line.from, lineDeco(`dc-li dc-li-${Math.min(depth, 4)}`))
          return true
        }
        return true
      },
    })
    // Wiki-links and highlights, which the Markdown grammar does not know.
    const text = state.doc.sliceString(from, to)
    const wl = /(!?)\[\[([^\]\n]+?)\]\]/g
    let m
    while ((m = wl.exec(text))) {
      const s = from + m.index, e = s + m[0].length
      if (fm && s < fm.to) continue
      if (m[1]) continue
      const [target, label] = m[2].split('|')
      const shown = touches(state, s, e)
      if (shown) { add(s, e, mk('dc-wiki dc-wiki-src')); continue }
      if (label !== undefined) {
        add(s, s + 2 + target.length + 1, hide)
        add(s + 2 + target.length + 1, e - 2, Decoration.mark({ class: 'dc-wiki', attributes: { 'data-wiki': target } }))
      } else {
        add(s, s + 2, hide)
        add(s + 2, e - 2, Decoration.mark({ class: 'dc-wiki', attributes: { 'data-wiki': target } }))
      }
      add(e - 2, e, hide)
    }
    const hl = /==([^=\n]+)==/g
    while ((m = hl.exec(text))) {
      const s = from + m.index, e = s + m[0].length
      if (fm && s < fm.to) continue
      add(s, e, mk('dc-hl'))
      if (!touches(state, s, e)) { add(s, s + 2, hide); add(e - 2, e, hide) }
    }
  }
  return Decoration.set(out, true)
}

class CalloutTitle extends WidgetType {
  constructor(kind) { super(); this.kind = kind }
  eq(o) { return o.kind === this.kind }
  toDOM() { return h('span', { class: 'dc-callout-kind', text: this.kind.charAt(0).toUpperCase() + this.kind.slice(1).toLowerCase() }) }
}

export const inline = ViewPlugin.fromClass(class {
  constructor(view) { this.decorations = inlineDecos(view) }
  update(u) {
    if (u.docChanged || u.viewportChanged || u.selectionSet || u.transactions.some(t => t.effects.some(e => e.is(setMode) || e.is(setFocused))) || syntaxTree(u.state) !== syntaxTree(u.startState)) {
      this.decorations = inlineDecos(u.view)
    }
  }
}, { decorations: (v) => v.decorations })

// -- notes, suggestions, authors: decoration layers fed from outside ---------------

// Each note: {id, kind, from, to, replacement?, state, focus, peek}.
export const setNotes = StateEffect.define()

class InsWidget extends WidgetType {
  constructor(text, id, cls) { super(); this.text = text; this.id = id; this.cls = cls }
  eq(o) { return o.text === this.text && o.id === this.id && o.cls === this.cls }
  toDOM() {
    return h('ins', { class: this.cls, 'data-note': this.id, text: this.text })
  }
  ignoreEvent() { return false }
}

function notesDecos(state, notes) {
  const out = []
  for (const n of notes) {
    if (n.from == null || n.to == null || n.to < n.from || n.to > state.doc.length) continue
    const on = n.focus ? ' on' : n.peek ? ' peek' : ''
    if (n.kind === 'draft') continue    // one's own, drawn live over real text (liveSuggest)
    if (n.kind === 'suggestion') {
      // Only what changes is drawn: a typed insertion is its new words alone,
      // with nothing struck (textdiff.js, suggestionShape).
      const shape = suggestionShape(state.sliceDoc(n.from, n.to), n.replacement || '')
      const a = n.from + shape.from, b = n.from + shape.to
      if (b > a) out.push(Decoration.mark({ class: `dc-sg dc-sg-del${on}`, attributes: { 'data-note': n.id } }).range(a, b))
      else if (n.to > n.from) out.push(Decoration.mark({ class: `dc-sg dc-sg-at${on}`, attributes: { 'data-note': n.id } }).range(n.from, n.to))
      if (shape.insert) out.push(Decoration.widget({ widget: new InsWidget(shape.insert, n.id, `dc-sg dc-sg-ins${shape.kind === 'insert' ? ' add' : ''}${on}`), side: 1 }).range(b))
    } else if (n.to > n.from) {
      out.push(Decoration.mark({ class: `dc-pass${on}${n.changed ? ' changed' : ''}`, attributes: { 'data-note': n.id } }).range(n.from, n.to))
    }
  }
  return Decoration.set(out, true)
}

export const notesField = StateField.define({
  create: () => ({ notes: [], decos: Decoration.none }),
  update(v, tr) {
    for (const e of tr.effects) if (e.is(setNotes)) return { notes: e.value, decos: notesDecos(tr.state, e.value) }
    if (!tr.docChanged) return v
    const notes = v.notes.map(n => {
      if (n.from == null) return n
      return { ...n, from: tr.changes.mapPos(n.from, 1), to: tr.changes.mapPos(n.to, -1) }
    }).map(n => (n.to != null && n.to < n.from ? { ...n, to: n.from } : n))
    return { notes, decos: v.decos.map(tr.changes) }
  },
  provide: (f) => EditorView.decorations.from(f, (v) => v.decos),
})

// Authors: [{from, to, cls}].
export const setAuthors = StateEffect.define()
export const authorsField = StateField.define({
  create: () => Decoration.none,
  update(v, tr) {
    for (const e of tr.effects) if (e.is(setAuthors)) {
      return Decoration.set(e.value.filter(s => s.to > s.from && s.to <= tr.state.doc.length).map(s => Decoration.mark({ class: `dc-au ${s.cls}` }).range(s.from, s.to)), true)
    }
    return tr.docChanged ? v.map(tr.changes) : v
  },
  provide: (f) => EditorView.decorations.from(f),
})

// Suggesting: the text as it was when suggesting began, and live marks for
// what has been typed over it since, until those become suggestions.
export const setSuggestBase = StateEffect.define()
export const suggestBase = StateField.define({
  create: () => null,
  update(v, tr) {
    for (const e of tr.effects) if (e.is(setSuggestBase)) return e.value
    return v
  },
})

class DelWidget extends WidgetType {
  constructor(text, id = null, extra = '') { super(); this.text = text; this.id = id; this.extra = extra }
  eq(o) { return o.text === this.text && o.id === this.id && o.extra === this.extra }
  toDOM() { return h('del', { class: `dc-sg-live-del${this.extra}`, 'data-note': this.id || null, text: this.text }) }
  ignoreEvent() { return false }
}

// `noteFor(from, to)`, given a changed run in the stored text, names the
// draft it belongs to and how it is drawn ({id, on}), so the live marks of
// one's own suggestions focus their card like any other passage.
export function liveSuggest(hunksOf, noteFor = () => null) {
  return ViewPlugin.fromClass(class {
    constructor(view) { this.decorations = this.build(view) }
    update(u) {
      if (u.docChanged || u.transactions.some(t => t.effects.some(e => e.is(setSuggestBase) || e.is(setNotes)))) this.decorations = this.build(u.view)
    }
    build(view) {
      const base = view.state.field(suggestBase, false)
      if (base == null) return Decoration.none
      const now = view.state.doc.toString()
      if (now === base) return Decoration.none
      const out = []
      let delta = 0
      for (const c of hunksOf(base, now)) {
        const at = c.from + delta
        const n = noteFor(c.from, c.to)
        const extra = n ? `${n.on ? ' on' : ''}${n.peek ? ' peek' : ''}` : ''
        if (c.to > c.from) out.push(Decoration.widget({ widget: new DelWidget(base.slice(c.from, c.to), n?.id, extra), side: -1 }).range(at))
        if (c.insert) out.push(Decoration.mark({ class: `dc-sg-live-ins${extra}`, attributes: n ? { 'data-note': n.id } : {} }).range(at, at + c.insert.length))
        delta += c.insert.length - (c.to - c.from)
      }
      return Decoration.set(out, true)
    }
  }, { decorations: (v) => v.decorations })
}

EmbedWidget.seen = new Map()
