// Small DOM and icon helpers for Sharing (in Loam and on the standalone share page).
// Never parses HTML; styles are applied as properties so CSP stays tight.

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
function svg(paths, { size = 16, stroke = 1.6 } = {}) {
  const s = document.createElementNS(NS, 'svg')
  s.setAttribute('viewBox', '0 0 24 24')
  s.setAttribute('width', size)
  s.setAttribute('height', size)
  s.setAttribute('aria-hidden', 'true')
  s.setAttribute('fill', 'none')
  s.setAttribute('stroke', 'currentColor')
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
  chevronDown: ['M6 9l6 6 6-6'],
  chevronLeft: ['M15 6l-6 6 6 6'],
  dots: [
    { tag: 'circle', cx: 6, cy: 12, r: 1.3, fill: 'currentColor', stroke: 'none' },
    { tag: 'circle', cx: 12, cy: 12, r: 1.3, fill: 'currentColor', stroke: 'none' },
    { tag: 'circle', cx: 18, cy: 12, r: 1.3, fill: 'currentColor', stroke: 'none' },
  ],
  check: ['M5 12.5l4.5 4.5L19 7.5'],
  x: ['M6 6l12 12', 'M18 6L6 18'],
  people: [
    { tag: 'circle', cx: 9, cy: 8.5, r: 3.2 },
    'M3 19c.7-3.2 3-5 6-5s5.3 1.8 6 5',
    'M15.5 5.5a3 3 0 0 1 0 6',
    'M18 14.3c1.6.7 2.6 2.3 3 4.7',
  ],
  history: ['M3.5 12a8.5 8.5 0 1 0 2.5-6', 'M3.5 4.5V9H8', 'M12 7.5V12l3 2'],
  link: ['M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1', 'M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1'],
  comment: ['M20 11.5c0 4-3.6 7-8 7-1.2 0-2.3-.2-3.3-.6L4 19.5l1.3-3.6C4.5 14.6 4 13.1 4 11.5c0-4 3.6-7 8-7s8 3 8 7z'],
  plus: ['M12 5v14', 'M5 12h14'],
  changed: [{ tag: 'circle', cx: 12, cy: 12, r: 8.5 }, 'M12 7.5v5', 'M12 16v.4'],
  file: ['M7 3.5h6.5l4 4V20a.5.5 0 0 1-.5.5H7a.5.5 0 0 1-.5-.5V4a.5.5 0 0 1 .5-.5z', 'M13.5 3.5v4h4'],
  download: ['M12 4v11', 'M7.5 10.5L12 15l4.5-4.5', 'M5 19h14'],
  external: ['M14 4h6v6', 'M10 14L20 4', 'M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5'],
  trash: ['M5 7h14', 'M10 7V5h4v2', 'M7 7l1 12.5h8L17 7'],
  robot: [
    { tag: 'rect', x: 4.5, y: 8, width: 15, height: 11, rx: 3.5 },
    'M12 8V5',
    { tag: 'circle', cx: 12, cy: 4, r: 1.2 },
    { tag: 'circle', cx: 9.3, cy: 13, r: 1.1, fill: 'currentColor' },
    { tag: 'circle', cx: 14.7, cy: 13, r: 1.1, fill: 'currentColor' },
    'M2.5 12.5v3M21.5 12.5v3',
  ],
}

export function icon(name, size = 16) {
  return svg(ICONS[name] || ICONS.dots, { size })
}

export function wordSelect({ options, value, onChange, label, className = '' }) {
  const words = (v) => (options.find(o => o[0] === v) || options[0])[1]
  const s = h('select', { class: 'sh-sel-in', 'aria-label': label },
    options.map(([v, t]) => h('option', { value: v, selected: v === value ? true : null }, t)))
  const shown = h('span', { class: 'sh-sel-shown', text: words(value) })
  s.addEventListener('change', () => { shown.textContent = words(s.value); onChange(s.value) })
  return h('span', { class: `sh-sel-pick${className ? ` ${className}` : ''}` }, shown, icon('chevronDown', 12), s)
}

export function formatBytes(n) {
  if (typeof n !== 'number' || !Number.isFinite(n) || n < 0) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export function when(iso) {
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return ''
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min`
  if (s < 86400) return `${Math.round(s / 3600)} h`
  if (s < 2 * 86400) return 'Yesterday'
  return new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

const TONES = [['blue', '#5C6E8A'], ['plum', '#7A5A72'], ['teal', '#4E7A72'], ['slate', '#6E7A52'], ['rose', '#9C4F44'], ['olive', '#4F6478'], ['mauve', '#8A5E80'], ['taupe', '#8C6A4E']]
  .map(([hue, hex]) => `var(--persona-${hue}, ${hex})`)
export const TONE_INK = 'var(--text-on-avatar, #FFFFFF)'

export function tone(name) {
  let n = 0
  for (const ch of String(name || '').trim().toLowerCase()) n = (n * 31 + ch.codePointAt(0)) >>> 0
  return TONES[n % TONES.length]
}
