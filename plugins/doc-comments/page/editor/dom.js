// Small DOM helpers for the editor. It runs inside Loam's page and, from step
// 2, alone on a share page, so it depends on nothing of the page's: styles are
// set as properties (never a `style` attribute, which Loam's policy refuses),
// and icons are drawn as SVG nodes.

export function h(tag, props = {}, ...children) {
  const node = document.createElement(tag)
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue
    if (k === 'class') node.className = v
    else if (k === 'text') node.textContent = v
    else if (k === 'style') Object.assign(node.style, v)
    else if (k === 'dataset') Object.assign(node.dataset, v)
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v)
    else if (v === true) node.setAttribute(k, '')
    else node.setAttribute(k, v)
  }
  add(node, children)
  return node
}

// Replace a node's children, skipping null, false and undefined (which the
// DOM's own replaceChildren would print as text).
export function put(node, ...children) {
  node.replaceChildren()
  add(node, children)
  return node
}

function add(node, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue
    if (Array.isArray(c)) add(node, c)
    else node.append(c instanceof Node ? c : document.createTextNode(String(c)))
  }
}

const NS = 'http://www.w3.org/2000/svg'
function svg(paths, { size = 16, fill = false, stroke = 1.6 } = {}) {
  const s = document.createElementNS(NS, 'svg')
  s.setAttribute('viewBox', '0 0 24 24')
  s.setAttribute('width', size)
  s.setAttribute('height', size)
  s.setAttribute('aria-hidden', 'true')
  s.setAttribute('fill', fill ? 'currentColor' : 'none')
  s.setAttribute('stroke', fill ? 'none' : 'currentColor')
  s.setAttribute('stroke-width', stroke)
  s.setAttribute('stroke-linecap', 'round')
  s.setAttribute('stroke-linejoin', 'round')
  for (const d of paths) {
    if (typeof d === 'string') {
      const p = document.createElementNS(NS, 'path')
      p.setAttribute('d', d)
      s.append(p)
    } else {
      const el = document.createElementNS(NS, d.tag)
      for (const [k, v] of Object.entries(d)) if (k !== 'tag') el.setAttribute(k, v)
      s.append(el)
    }
  }
  return s
}

const ICONS = {
  pencil: ['M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4z', 'M13.5 6.5l4 4'],
  suggest: ['M4 20h4L17 11', 'M15 5.5a2.8 2.8 0 0 1 4 4', 'M13 21h7', 'M17 17h3'],
  eye: ['M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z', { tag: 'circle', cx: 12, cy: 12, r: 2.8 }],
  chevronDown: ['M6 9l6 6 6-6'],
  chevronRight: ['M9 6l6 6-6 6'],
  chevronLeft: ['M15 6l-6 6 6 6'],
  dots: [{ tag: 'circle', cx: 6, cy: 12, r: 1.3, fill: 'currentColor', stroke: 'none' }, { tag: 'circle', cx: 12, cy: 12, r: 1.3, fill: 'currentColor', stroke: 'none' }, { tag: 'circle', cx: 18, cy: 12, r: 1.3, fill: 'currentColor', stroke: 'none' }],
  check: ['M5 12.5l4.5 4.5L19 7.5'],
  x: ['M6 6l12 12', 'M18 6L6 18'],
  people: [{ tag: 'circle', cx: 9, cy: 8.5, r: 3.2 }, 'M3 19c.7-3.2 3-5 6-5s5.3 1.8 6 5', 'M15.5 5.5a3 3 0 0 1 0 6', 'M18 14.3c1.6.7 2.6 2.3 3 4.7'],
  history: ['M3.5 12a8.5 8.5 0 1 0 2.5-6', 'M3.5 4.5V9H8', 'M12 7.5V12l3 2'],
  resolved: [{ tag: 'circle', cx: 12, cy: 12, r: 8.5 }, 'M8.3 12.3l2.6 2.6 4.8-5.2'],
  bold: ['M7 5h6a3.5 3.5 0 0 1 0 7H7z', 'M7 12h7a3.5 3.5 0 0 1 0 7H7z'],
  italic: ['M10 5h8', 'M6 19h8', 'M14 5l-4 14'],
  strike: ['M4 12h16', 'M16.5 7.5c-.6-1.7-2.3-2.7-4.5-2.7-2.6 0-4.4 1.4-4.4 3.3 0 1.2.7 2.1 2 2.8', 'M8 16.3c.6 1.8 2.3 2.9 4.6 2.9 2.7 0 4.6-1.4 4.6-3.5 0-.8-.3-1.5-.8-2'],
  link: ['M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1', 'M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1'],
  code: ['M8.5 7.5L4 12l4.5 4.5', 'M15.5 7.5L20 12l-4.5 4.5'],
  comment: ['M20 11.5c0 4-3.6 7-8 7-1.2 0-2.3-.2-3.3-.6L4 19.5l1.3-3.6C4.5 14.6 4 13.1 4 11.5c0-4 3.6-7 8-7s8 3 8 7z'],
  plus: ['M12 5v14', 'M5 12h14'],
  list: [{ tag: 'circle', cx: 5, cy: 7, r: 1, fill: 'currentColor' }, { tag: 'circle', cx: 5, cy: 12, r: 1, fill: 'currentColor' }, { tag: 'circle', cx: 5, cy: 17, r: 1, fill: 'currentColor' }, 'M9.5 7H20', 'M9.5 12H20', 'M9.5 17H20'],
  olist: ['M4 5.5h1.5V10', 'M4 10h3', 'M4 14.5c.4-.6 1-1 1.7-1 .8 0 1.4.5 1.4 1.2 0 1.2-3.1 2.2-3.1 3.8h3.2', 'M10.5 7H20', 'M10.5 12H20', 'M10.5 17H20'],
  checklist: [{ tag: 'rect', x: 3.5, y: 4.5, width: 6, height: 6, rx: 1.6 }, 'M5 7.6l1.2 1.2 2-2.3', { tag: 'rect', x: 3.5, y: 13.5, width: 6, height: 6, rx: 1.6 }, 'M13 7.5h7.5', 'M13 16.5h7.5'],
  quote: ['M5 5v14', 'M10 8h9', 'M10 12h9', 'M10 16h6'],
  codeblock: [{ tag: 'rect', x: 3.5, y: 4.5, width: 17, height: 15, rx: 3 }, 'M9.5 10L7.5 12l2 2', 'M14.5 10l2 2-2 2'],
  divider: ['M4 12h16', 'M8 7h8', 'M8 17h8'],
  text: ['M5 6h14', 'M12 6v13'],
  table: [{ tag: 'rect', x: 3.5, y: 5, width: 17, height: 14, rx: 2.5 }, 'M3.5 10h17', 'M3.5 14.5h17', 'M10 10v9'],
  send: ['M12 19V6', 'M6.5 11.5L12 6l5.5 5.5'],
  undo: ['M9 7L4.5 11.5 9 16', 'M4.5 11.5H15a4.5 4.5 0 0 1 0 9h-2'],
  reattach: ['M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1', 'M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1'],
  changed: [{ tag: 'circle', cx: 12, cy: 12, r: 8.5 }, 'M12 7.5v5', 'M12 16v.4'],
  file: ['M7 3.5h6.5l4 4V20a.5.5 0 0 1-.5.5H7a.5.5 0 0 1-.5-.5V4a.5.5 0 0 1 .5-.5z', 'M13.5 3.5v4h4'],
  restore: ['M3.5 12a8.5 8.5 0 1 0 2.5-6', 'M3.5 4.5V9H8'],
  trash: ['M5 7h14', 'M10 7V5h4v2', 'M7 7l1 12.5h8L17 7'],
}

export function icon(name, size = 16) {
  return svg(ICONS[name] || ICONS.dots, { size })
}

export function rel(iso, now = Date.now()) {
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return ''
  const s = Math.max(0, (now - t) / 1000)
  if (s < 45) return 'now'
  if (s < 3600) return `${Math.round(s / 60)} min`
  if (s < 86400) return `${Math.round(s / 3600)} h`
  const d = new Date(t)
  const days = Math.round(s / 86400)
  if (days < 7) return d.toLocaleDateString(undefined, { weekday: 'short' })
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function stamp(iso) {
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return ''
  const d = new Date(t)
  const today = new Date()
  const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  if (d.toDateString() === today.toDateString()) return `Today ${time}`
  const y = new Date(today.getTime() - 86400000)
  if (d.toDateString() === y.toDateString()) return `Yesterday ${time}`
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ` · ${time}`
}
