// Formatting as Markdown edits of the source: what the selection popup, the
// block menu, the phone's bar and the keyboard shortcuts do. Each is an
// ordinary change of the text, so it is saved, merged and (in Suggesting)
// turned into a suggestion exactly like typing.

import { EditorSelection, syntaxTree } from '../vendor/codemirror.js'

export const INLINE = {
  bold: { open: '**', close: '**', label: 'Bold', md: '**text**', key: 'B' },
  italic: { open: '*', close: '*', label: 'Italic', md: '*text*', key: 'I' },
  strike: { open: '~~', close: '~~', label: 'Strikethrough', md: '~~text~~', key: 'Shift X' },
  code: { open: '`', close: '`', label: 'Code', md: '`code`', key: 'E' },
}

const NODE = { bold: 'StrongEmphasis', italic: 'Emphasis', strike: 'Strikethrough', code: 'InlineCode' }

// The node of `kind` the selection is inside, if any.
function around(state, kind, from, to) {
  let node = syntaxTree(state).resolveInner(from, 1)
  while (node) {
    if (node.name === NODE[kind] && node.from <= from && node.to >= to) return node
    node = node.parent
  }
  return null
}

export function active(state) {
  const r = state.selection.main
  const out = {}
  for (const k of Object.keys(NODE)) out[k] = Boolean(around(state, k, r.from, r.to))
  let link = syntaxTree(state).resolveInner(r.from, 1)
  out.link = false
  while (link) { if (link.name === 'Link') { out.link = true; break } link = link.parent }
  out.block = blockOf(state)
  return out
}

export function toggleInline(view, kind) {
  const { state } = view
  const f = INLINE[kind]
  const tr = state.changeByRange((r) => {
    const node = around(state, kind, r.from, r.to)
    if (node) {
      const o = f.open.length, c = f.close.length
      const inner = state.sliceDoc(node.from, node.to)
      if (inner.startsWith(f.open) && inner.endsWith(f.close)) {
        return {
          changes: [{ from: node.from, to: node.from + o }, { from: node.to - c, to: node.to }],
          range: EditorSelection.range(Math.max(node.from, r.from - o), Math.max(node.from, r.to - o)),
        }
      }
    }
    if (r.empty) {
      return { changes: { from: r.from, insert: f.open + f.close }, range: EditorSelection.cursor(r.from + f.open.length) }
    }
    // Keep spaces at the edges outside the marks, as Markdown needs.
    let a = r.from, b = r.to
    while (a < b && /\s/.test(state.sliceDoc(a, a + 1))) a++
    while (b > a && /\s/.test(state.sliceDoc(b - 1, b))) b--
    return {
      changes: [{ from: a, insert: f.open }, { from: b, insert: f.close }],
      range: EditorSelection.range(a + f.open.length, b + f.open.length),
    }
  })
  view.dispatch(state.update(tr, { scrollIntoView: true, userEvent: 'input.format' }))
  view.focus()
}

// A link: wrap the selection as [words](url) and select the url, or change
// the url of the link the caret is in.
export function setLink(view, url, range = null) {
  const { state } = view
  const r = range || state.selection.main
  let node = syntaxTree(state).resolveInner(r.from, 1)
  while (node && node.name !== 'Link') node = node.parent
  if (node) {
    const urlNode = node.getChild('URL')
    if (urlNode) {
      if (!url) {
        // Removing a link keeps its words.
        const marks = node.getChildren('LinkMark')
        const words = marks.length >= 2 ? state.sliceDoc(marks[0].to, marks[1].from) : ''
        view.dispatch({ changes: { from: node.from, to: node.to, insert: words }, userEvent: 'input.format' })
        return
      }
      view.dispatch({ changes: { from: urlNode.from, to: urlNode.to, insert: url }, userEvent: 'input.format' })
      return
    }
  }
  if (!url) return
  const words = state.sliceDoc(r.from, r.to) || url
  view.dispatch({
    changes: { from: r.from, to: r.to, insert: `[${words}](${url})` },
    selection: { anchor: r.from + words.length + url.length + 4 },
    userEvent: 'input.format',
  })
  view.focus()
}

export function linkAt(state, pos) {
  let node = syntaxTree(state).resolveInner(pos, 1)
  while (node && node.name !== 'Link') node = node.parent
  const u = node?.getChild('URL')
  return u ? state.sliceDoc(u.from, u.to) : ''
}

// -- blocks --------------------------------------------------------------------

export const BLOCKS = [
  { id: 'p', label: 'Text', md: '', glyph: 'Aa', key: 'Ctrl Alt 0' },
  { id: 'h1', label: 'Heading 1', md: '# ', glyph: 'H1', key: 'Ctrl Alt 1' },
  { id: 'h2', label: 'Heading 2', md: '## ', glyph: 'H2', key: 'Ctrl Alt 2' },
  { id: 'h3', label: 'Heading 3', md: '### ', glyph: 'H3', key: 'Ctrl Alt 3' },
  { id: 'ul', label: 'Bulleted list', md: '- ', icon: 'list' },
  { id: 'ol', label: 'Numbered list', md: '1. ', icon: 'olist' },
  { id: 'task', label: 'Checklist', md: '- [ ] ', icon: 'checklist' },
  { id: 'quote', label: 'Quote', md: '> ', icon: 'quote' },
  { id: 'code', label: 'Code block', md: '```', icon: 'codeblock' },
  { id: 'hr', label: 'Divider', md: '---', icon: 'divider' },
  { id: 'table', label: 'Table', md: '| |', icon: 'table' },
]

const PREFIX = /^(\s*)(#{1,6} |[-*+] \[[ xX]\] |[-*+] |\d+[.)] |> ?)?/

export function blockOf(state) {
  const line = state.doc.lineAt(state.selection.main.head)
  const m = PREFIX.exec(line.text)
  const p = m[2] || ''
  if (/^# $/.test(p)) return 'h1'
  if (/^## $/.test(p)) return 'h2'
  if (/^#{3,6} $/.test(p)) return 'h3'
  if (/\[[ xX]\]/.test(p)) return 'task'
  if (/^[-*+] $/.test(p)) return 'ul'
  if (/^\d/.test(p)) return 'ol'
  if (/^>/.test(p)) return 'quote'
  return 'p'
}

export function setBlock(view, id) {
  const { state } = view
  const block = BLOCKS.find(b => b.id === id)
  if (!block) return
  const sel = state.selection.main
  const first = state.doc.lineAt(sel.from).number
  const last = state.doc.lineAt(sel.to).number
  if (id === 'code' || id === 'hr' || id === 'table') {
    const line = state.doc.line(first)
    const empty = !line.text.trim()
    const insert = id === 'code' ? '```\n\n```' : id === 'hr' ? '---\n' : '| Column | Column |\n| --- | --- |\n|  |  |'
    const from = empty ? line.from : line.to
    const pre = empty ? '' : '\n\n'
    const caret = id === 'code' ? from + pre.length + 4 : id === 'table' ? from + pre.length + insert.length - 5 : from + pre.length + insert.length
    view.dispatch({ changes: { from, to: empty ? line.to : line.to, insert: pre + insert }, selection: { anchor: caret }, userEvent: 'input.format', scrollIntoView: true })
    view.focus()
    return
  }
  const changes = []
  for (let n = first; n <= last; n++) {
    const line = state.doc.line(n)
    const m = PREFIX.exec(line.text)
    const indent = m[1] || ''
    const old = m[2] || ''
    const want = id === 'ol' ? `${n - first + 1}. ` : block.md
    changes.push({ from: line.from + indent.length, to: line.from + indent.length + old.length, insert: want })
  }
  view.dispatch({ changes, userEvent: 'input.format', scrollIntoView: true })
  view.focus()
}
