// Safe, DOM-only Markdown renderer (zero innerHTML, zero script execution).
// Used on Sharing's standalone share page and `@sharing` in-app viewer when
// Comments & suggestions is off (read-only view of shared Markdown files).

import { h } from './dom.js'

function safeHref(url) {
  const s = String(url || '').trim()
  if (/^(https?:|mailto:)/i.test(s)) return s
  return null
}

function renderInline(text) {
  const out = []
  let i = 0
  const s = String(text || '')
  const rx = /(`[^`\n]+`|\*\*[^*\n]+?\*\*|__[^_\n]+?__|~~[^~\n]+?~~|\*[^*\n]+?\*|_[^_\n]+?_|\[([^\]]+)\]\(([^)\s]+)\))/g
  let m
  while ((m = rx.exec(s)) !== null) {
    if (m.index > i) out.push(s.slice(i, m.index))
    const tok = m[0]
    if (tok.startsWith('`')) {
      out.push(h('code', { text: tok.slice(1, -1) }))
    } else if (tok.startsWith('**') || tok.startsWith('__')) {
      out.push(h('strong', {}, ...renderInline(tok.slice(2, -2))))
    } else if (tok.startsWith('~~')) {
      out.push(h('del', {}, ...renderInline(tok.slice(2, -2))))
    } else if (tok.startsWith('*') || tok.startsWith('_')) {
      out.push(h('em', {}, ...renderInline(tok.slice(1, -1))))
    } else if (tok.startsWith('[')) {
      const href = safeHref(m[3])
      if (href) {
        out.push(h('a', { href, target: '_blank', rel: 'noopener noreferrer' }, ...renderInline(m[2])))
      } else {
        out.push(m[2])
      }
    }
    i = m.index + tok.length
  }
  if (i < s.length) out.push(s.slice(i))
  return out
}

function isTableRow(line) {
  return /^\s*\|.+\|\s*$/.test(line)
}

function isTableSep(line) {
  return /^\s*\|(?:\s*:?-+:?\s*\|)+\s*$/.test(line)
}

function splitCells(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim())
}

export function renderMarkdown(source) {
  const lines = String(source || '').replace(/\r\n?/g, '\n').split('\n')
  const nodes = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) { i++; continue }

    // Fenced code block
    const fence = /^(`{3,}|~{3,})(.*)$/.exec(line)
    if (fence) {
      const mark = fence[1][0]
      const len = fence[1].length
      const info = fence[2].trim()
      const lang = (info.split(/\s+/)[0] || '').toLowerCase()
      const codeLines = []
      i++
      while (i < lines.length && !new RegExp(`^${mark}{${len},}\\s*$`).test(lines[i])) {
        codeLines.push(lines[i++])
      }
      if (i < lines.length) i++
      const body = codeLines.join('\n')
      if (lang === 'visual') {
        const title = info.slice(6).trim().slice(0, 120)
        nodes.push(h('details', { class: 'sh-md-visual' },
          h('summary', { class: 'sh-md-visual-head' },
            h('span', { class: 'sh-md-visual-title', text: title ? `Visual · ${title}` : 'Visual' }),
            h('span', { class: 'sh-md-visual-note', text: 'Shown inline in Loam' })),
          h('pre', { class: 'sh-md-pre' }, h('code', { text: body }))))
      } else {
        nodes.push(h('pre', { class: 'sh-md-pre' }, h('code', { text: body })))
      }
      continue
    }

    // Horizontal rule
    if (/^\s*(?:[-*_]\s*){3,}$/.test(line)) {
      nodes.push(h('hr'))
      i++
      continue
    }

    // ATX heading
    const heading = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line)
    if (heading) {
      nodes.push(h(`h${heading[1].length}`, {}, ...renderInline(heading[2])))
      i++
      continue
    }

    // Table
    if (isTableRow(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = splitCells(line)
      i += 2
      const rows = []
      while (i < lines.length && isTableRow(lines[i])) {
        rows.push(splitCells(lines[i++]))
      }
      nodes.push(h('div', { class: 'sh-md-tblwrap' },
        h('table', {},
          h('thead', {}, h('tr', {}, head.map(c => h('th', {}, ...renderInline(c))))),
          h('tbody', {}, rows.map(r => h('tr', {}, r.map(c => h('td', {}, ...renderInline(c)))))))))
      continue
    }

    // Blockquote
    if (/^\s*>\s?/.test(line)) {
      const qlines = []
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        qlines.push(lines[i++].replace(/^\s*>\s?/, ''))
      }
      nodes.push(h('blockquote', {}, renderMarkdown(qlines.join('\n'))))
      continue
    }

    // Lists (unordered / ordered)
    if (/^\s*[-*+]\s+/.test(line)) {
      const items = []
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        const raw = lines[i++].replace(/^\s*[-*+]\s+/, '')
        const task = /^\[([ xX])\]\s+(.*)$/.exec(raw)
        if (task) {
          items.push(h('li', { class: 'sh-md-task' },
            h('input', { type: 'checkbox', disabled: true, checked: task[1].toLowerCase() === 'x' ? true : null }),
            h('span', {}, ...renderInline(task[2]))))
        } else {
          items.push(h('li', {}, ...renderInline(raw)))
        }
      }
      nodes.push(h('ul', {}, items))
      continue
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items = []
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(h('li', {}, ...renderInline(lines[i++].replace(/^\s*\d+\.\s+/, ''))))
      }
      nodes.push(h('ol', {}, items))
      continue
    }

    // Paragraph
    const para = [line]
    i++
    while (
      i < lines.length
      && lines[i].trim()
      && !/^(#{1,6}\s+|`{3,}|~{3,}|\s*>\s?|\s*[-*+]\s+|\s*\d+\.\s+|\s*(?:[-*_]\s*){3,}$)/.test(lines[i])
      && !(isTableRow(lines[i]) && i + 1 < lines.length && isTableSep(lines[i + 1]))
    ) {
      para.push(lines[i++])
    }
    nodes.push(h('p', {}, ...renderInline(para.join(' '))))
  }

  return h('div', { class: 'sh-md' }, nodes)
}
