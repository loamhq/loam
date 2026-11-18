// The Market's pictures (2026-11-18), drawn rather than loaded, and only in
// the theme's own tokens: every fill and stroke is a `var(--…)`, set through
// the CSSOM (the page's policy refuses a style attribute), so a picture is
// at home in every theme, light and dark, and under Sun as it shifts.
//
//   buildArt()        "Nothing quite right? Ask one of your agents to build
//                     it": an agent at a small bench, a new piece taking
//                     shape from the soil, and a gap where it will go.
//   coverArt(entry)   the lead story's cover when a listing has no picture of
//                     its own: soil strata under a sky, and the kind's shape.
import { dress } from './ui.js'

const NS = 'http://www.w3.org/2000/svg'

function el(tag, attrs = {}, paint = {}, ...children) {
  const node = document.createElementNS(NS, tag)
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) node.setAttribute(k, String(v))
  const style = {}
  for (const [k, v] of Object.entries(paint)) style[k.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v
  if (Object.keys(style).length) dress(node, style)
  for (const child of children.flat()) if (child) node.append(child)
  return node
}
const fill = (token, extra = {}) => ({ fill: `var(${token})`, ...extra })
const line = (token, width = 2, extra = {}) => ({ fill: 'none', stroke: `var(${token})`, 'stroke-width': String(width), 'stroke-linecap': 'round', 'stroke-linejoin': 'round', ...extra })

function svg(viewBox, cls, label, fit, ...children) {
  const root = el('svg', { viewBox, class: cls, role: label ? 'img' : null, 'aria-hidden': label ? null : 'true', focusable: 'false', preserveAspectRatio: fit })
  if (label) root.append(el('title', {}, {}, label))
  root.append(...children)
  return root
}

// A puzzle piece, Loam's shape for "a plugin", as a path in a 40×40 box.
const PIECE = 'M8 10h8a4 4 0 1 1 8 0h8v8a4 4 0 1 1 0 8v8h-8a4 4 0 1 0-8 0H8v-8a4 4 0 1 0 0-8z'

function sparkle(x, y, r, token, delay) {
  const d = `M${x} ${y - r} L${x + r * 0.28} ${y - r * 0.28} L${x + r} ${y} L${x + r * 0.28} ${y + r * 0.28} L${x} ${y + r} L${x - r * 0.28} ${y + r * 0.28} L${x - r} ${y} L${x - r * 0.28} ${y - r * 0.28} Z`
  return el('path', { d, class: 'market-art-twinkle', 'data-delay': String(delay) }, fill(token, { 'animation-delay': `${delay}ms` }))
}

export function buildArt({ label = null } = {}) {
  return svg('0 0 320 200', 'market-art market-build-art', label, 'xMidYMid meet',
    // Sky and sun.
    el('circle', { cx: 232, cy: 66, r: 30 }, fill('--latte-foam')),
    el('circle', { cx: 232, cy: 66, r: 18 }, fill('--latte-track')),
    // Soil, in three strata.
    el('path', { d: 'M0 150 C40 138 80 156 128 146 S220 134 262 146 S306 150 320 144 V200 H0Z' }, fill('--paper-card')),
    el('path', { d: 'M0 168 C46 160 92 174 140 166 S232 158 276 168 S310 170 320 166 V200 H0Z' }, fill('--latte-track')),
    el('path', { d: 'M0 186 C52 180 100 190 152 184 S248 178 292 186 S314 188 320 186 V200 H0Z' }, fill('--latte-coffee-2', { opacity: '.35' })),
    // The gap where the new piece will go: dashed, waiting.
    el('g', { transform: 'translate(176 86) scale(1.5)' }, {},
      el('path', { d: PIECE }, line('--paper-rule-strong', 1.6, { 'stroke-dasharray': '3 3.4' }))),
    // The new piece, growing from the soil on a little stem.
    el('path', { d: 'M110 146 C110 132 112 122 116 112' }, line('--success', 3)),
    el('path', { d: 'M112 132 C100 128 96 120 98 112 C108 114 113 121 112 132Z' }, fill('--success')),
    el('g', { transform: 'translate(92 52) scale(1.45) rotate(-8 20 20)' }, {},
      el('path', { d: PIECE }, fill('--latte-espresso')),
      el('path', { d: 'M13 15h6M13 19h10' }, line('--latte-espresso-ink', 1.6, { opacity: '.7' }))),
    // An agent at the bench: a round face, two eyes, a pleased mouth.
    el('g', { class: 'market-art-agent' }, {},
      el('circle', { cx: 52, cy: 116, r: 22 }, fill('--persona-teal')),
      el('circle', { cx: 45, cy: 112, r: 2.6 }, fill('--text-on-avatar')),
      el('circle', { cx: 58, cy: 112, r: 2.6 }, fill('--text-on-avatar')),
      el('path', { d: 'M45 121 Q51.5 126 58 121' }, line('--text-on-avatar', 2.2)),
      // A pencil, held towards the piece.
      el('path', { d: 'M70 124 L92 102' }, line('--signal-crema', 4)),
      el('path', { d: 'M92 102 L96 98' }, line('--paper-ink', 4))),
    // Delight: a few sparkles that twinkle (still under reduced motion).
    sparkle(150, 46, 7, '--signal-crema', 0),
    sparkle(80, 34, 5, '--latte-espresso', 400),
    sparkle(268, 120, 6, '--signal-crema', 800),
    sparkle(204, 28, 4, '--latte-coffee-2', 1200))
}

const HUES = ['--persona-teal', '--persona-rust', '--persona-olive', '--persona-plum', '--persona-gold', '--persona-blue', '--persona-sage', '--persona-rose', '--persona-taupe', '--persona-slate']
export function hueOf(id) {
  let n = 0
  for (const ch of String(id ?? '')) n = (n * 31 + ch.codePointAt(0)) >>> 0
  return HUES[n % HUES.length]
}

function kindShape(kind, hue) {
  if (kind === 'theme') return el('g', { transform: 'translate(150 46)' }, {},
    el('circle', { cx: 0, cy: 30, r: 26 }, fill('--paper-page')),
    el('circle', { cx: 42, cy: 30, r: 26 }, fill('--latte-espresso')),
    el('circle', { cx: 21, cy: 66, r: 26 }, fill(hue)))
  if (kind === 'app') return el('g', { transform: 'translate(130 44)' }, {},
    el('rect', { x: 0, y: 0, width: 92, height: 70, rx: 10 }, fill('--paper-raised')),
    el('rect', { x: 0, y: 0, width: 92, height: 16, rx: 8 }, fill(hue)),
    el('rect', { x: 12, y: 28, width: 40, height: 8, rx: 4 }, fill('--paper-card')),
    el('rect', { x: 12, y: 44, width: 64, height: 8, rx: 4 }, fill('--paper-card')))
  if (kind === 'agent') return el('g', { transform: 'translate(176 80)' }, {},
    el('circle', { cx: 0, cy: 0, r: 40 }, fill(hue)),
    el('circle', { cx: -13, cy: -6, r: 4.5 }, fill('--text-on-avatar')),
    el('circle', { cx: 13, cy: -6, r: 4.5 }, fill('--text-on-avatar')),
    el('path', { d: 'M-13 12 Q0 22 13 12' }, line('--text-on-avatar', 4)))
  if (kind === 'skill') return el('g', { transform: 'translate(132 46)' }, {},
    el('path', { d: 'M0 8 Q22 0 44 8 V76 Q22 68 0 76Z' }, fill('--paper-raised')),
    el('path', { d: 'M44 8 Q66 0 88 8 V76 Q66 68 44 76Z' }, fill(hue)),
    el('path', { d: 'M12 26h22M12 38h18M56 26h22M56 38h16' }, line('--paper-rule-strong', 3)))
  return el('g', { transform: 'translate(140 34) scale(2.3)' }, {}, el('path', { d: PIECE }, fill(hue)))
}

export function coverArt(entry, kind = 'extension') {
  const hue = hueOf(entry?.id)
  // Wider than any frame it fills, and cut to fit (slice): the shape sits
  // in the middle third, which every frame keeps.
  return svg('0 0 480 220', 'market-art market-cover-art', null, 'xMidYMid slice',
    el('rect', { x: 0, y: 0, width: 480, height: 220 }, fill('--latte-foam')),
    el('circle', { cx: 340, cy: 64, r: 30 }, fill('--latte-track')),
    el('path', { d: 'M0 160 C80 146 160 168 250 156 S400 142 480 154 V220 H0Z' }, fill('--paper-card')),
    el('path', { d: 'M0 184 C90 174 180 192 270 182 S420 172 480 180 V220 H0Z' }, fill(hue, { opacity: '.55' })),
    el('path', { d: 'M0 204 C100 198 200 210 300 202 S440 198 480 202 V220 H0Z' }, fill('--latte-coffee-2', { opacity: '.4' })),
    el('g', { transform: 'translate(60 6)' }, {}, kindShape(kind, hue)),
    sparkle(150, 60, 8, '--signal-crema', 0),
    sparkle(126, 104, 5, '--latte-espresso', 600),
    sparkle(312, 128, 5, '--signal-crema', 1100))
}
