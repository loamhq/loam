// Static, local vector pours in coffee coordinates (centre 50,50).
// Order is persistent identity: append designs, never reorder existing ones.
import { variantFor } from './face-identity.js'
const cut = d => ['path', { d, fill: 'none', stroke: 'var(--latte-art-crema)', 'stroke-width': 1.6, 'stroke-linecap': 'round' }]
export const POURS = [
  ['heart', [
    ['path', { d: 'M50 73 C28 58 26 42 36 35 C43 30 50 35 50 42 C50 35 57 30 64 35 C74 42 72 58 50 73Z' }],
    cut('M50 32 L50 74'),
  ]],
  ['tulip', [
    ['path', { d: 'M28 54 C33 50 40 62 50 62 C60 62 67 50 72 54 C74 63 61 73 50 75 C39 73 26 63 28 54Z M33 43 C39 39 42 51 50 51 C58 51 61 39 67 43 C69 51 59 61 50 63 C41 61 31 51 33 43Z M39 33 C44 29 47 35 50 39 C53 35 56 29 61 33 C65 38 57 48 50 51 C43 48 35 38 39 33Z M50 24 C58 24 59 31 50 37 C41 31 42 24 50 24Z' }],
    cut('M50 27 C48 42 49 62 50 78'),
  ]],
  ['rosetta', [
    ['path', { d: 'M50 73 C37 71 27 65 26 58 C33 55 35 64 47 66 C36 60 26 55 28 48 C35 46 36 56 47 58 C36 51 30 47 33 40 C39 39 41 48 48 50 C40 44 35 38 39 33 C44 33 46 39 49 42 C44 33 44 28 50 24 C56 28 56 33 51 42 C54 39 56 33 61 33 C65 38 60 44 52 50 C59 48 61 39 67 40 C70 47 64 51 53 58 C64 56 65 46 72 48 C74 55 64 60 53 66 C65 64 67 55 74 58 C73 65 63 71 50 73Z' }],
    cut('M50 27 C49 43 50 65 50 78'),
  ]],
  ['monk', [
    ['path', { d: 'M46 34 Q43 29 47 27 Q50 32 55 34 A19 19 0 1 1 46 34Z' }],
  ]],
  ['swirl', [
    ['path', { d: 'M50 50 m0 -3 a3 3 0 1 1 -3 3 a7 7 0 0 1 7-7 a11 11 0 0 1 11 11 a15 15 0 0 1-15 15 a19 19 0 0 1-19-19', fill: 'none', stroke: 'var(--latte-art-foam)', 'stroke-width': 5.5, 'stroke-linecap': 'round' }],
  ]],
  ['twin-hearts', [
    ['path', { d: 'M42 66 C28 56 28 45 35 40.5 C39.5 37.5 43 40.5 43 45 C43 40.5 47 37.5 51 40.5 C58 45 56 56 42 66Z M61 49 C50 41.5 49 33 54.5 29.5 C58.5 27 61.5 29.5 61.5 33 C61.5 29.5 65 27 69 29.5 C74.5 33 72.5 41.5 61 49Z' }],
  ]],
  ['leaf', [
    ['path', { d: 'M50 75 C29 64 29 37 50 25 C71 37 71 64 50 75Z' }],
    cut('M50 28 L50 75 M50 41 L42 35 M50 51 L39 44 M50 62 L41 55 M50 41 L58 35 M50 51 L61 44 M50 62 L59 55'),
  ]],
  ['round', [['circle', { cx: 50, cy: 50, r: 25 }]]],
]

export function pourFor(id, slot = null) {
  return POURS[variantFor(id, { count: POURS.length, slot })]
}

const NS = 'http://www.w3.org/2000/svg'
function node(tag, attributes, children = []) {
  const el = document.createElementNS(NS, tag)
  for (const [name, value] of Object.entries(attributes)) el.setAttribute(name, value)
  for (const child of children) el.appendChild(child)
  return el
}

export function latteArt(id, slot = null) {
  const [pour, shapes] = pourFor(id, slot)
  return node('svg', { class: 'latte-art', viewBox: '-4 -4 108 108', 'aria-hidden': 'true', focusable: 'false', 'data-pour': pour }, [
    node('g', { class: 'latte-mug', fill: 'var(--latte-art-bone)', stroke: 'var(--latte-art-edge)', 'stroke-width': .7 }, [
      node('rect', { x: 88, y: 43, width: 12, height: 14, rx: 5, transform: 'rotate(35 50 50)' }),
      node('circle', { cx: 50, cy: 50, r: 42 }),
    ]),
    node('g', { class: 'latte-disc' }, [
      node('circle', { cx: 50, cy: 50, r: 37.5, fill: 'var(--latte-art-coffee)' }),
      // Translucent crema layers give a soft radial edge without paint-server
      // IDs. Every repeated face is independent, and unchanged redraws write
      // nothing; no duplicate IDs or references into another face's SVG.
      ...Array.from({ length: 16 }, (_, i) => node('circle', { class: 'latte-crema', cx: 49.5, cy: 49, r: 36.4 - i, fill: 'var(--latte-art-crema)', opacity: .15 })),
      node('g', { class: 'latte-pour', fill: 'var(--latte-art-foam)', transform: 'translate(50 50) scale(1.06) translate(-50 -50)' }, shapes.map(([tag, attrs]) => node(tag, attrs))),
    ]),
  ])
}
