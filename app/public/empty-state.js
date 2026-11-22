// One empty state for the whole app (2026-11-18): Loam's own blob, filled
// with a quiet tint of the theme, holding one fine-line object; under it a
// display-face title, one line of words, and at most one action. Each empty
// place has its own object in the same blob, so they read as one family.
//
//   emptyState({ object, title, body, action, size, level, props })
//
// `object` is one of EMPTY_OBJECTS. `size` is 'large' (a whole window: the
// empty Market), 'pane' (a pane or a list column, the default) or 'small' (a
// narrow panel: a profile's tab, the file picker).
//
// **Every colour is a token and every paint is a class** (`empty-state.css`):
// nothing here writes a colour, so the drawing is at home in every theme,
// light and dark, and follows Sun as it shifts. **The one touch of motion**
// is the sprout: its stem draws in and its leaves unfold when the empty
// state first appears. The reconciler keeps the node across redraws
// (`morph()` in ui.js), so it plays once per appearance, not per redraw, and
// not at all under `prefers-reduced-motion`.
//
// Where it is used, and why some places keep a line of text instead, is in
// `docs/empty-states.md`.
import { h } from './ui.js'

const NS = 'http://www.w3.org/2000/svg'

// The blob: the same organic family as the agents' faces and the L, drawn
// wider so an object sits in it with room to breathe. 164 × 140.
export const BLOB = 'M82 16c27 1 50 16 56 41s-6 52-30 63-60 9-76-9S10 63 25 41 55 15 82 16z'

function s(tag, cls, attrs = {}) {
  const node = document.createElementNS(NS, tag)
  if (cls) node.setAttribute('class', cls)
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v))
  return node
}
const path = (cls, d, attrs) => s('path', cls, { d, ...attrs })
const dot = (cls, cx, cy, r = 2.6) => s('circle', cls, { cx, cy, r })

// The objects, each in the blob's 164 × 140 box. Three paints only: a sheet
// (the raised surface with a fine ink outline), a fine ink line, and one
// accent in the theme's espresso. The sprout adds the soil.
const OBJECTS = {
  // The empty Market and its build-it state: a seed, its first stem, two leaves.
  sprout: () => [
    path('es-ground', 'M46 106 Q82 99 118 106'),
    s('ellipse', 'es-soil', { cx: 82, cy: 104, rx: 6.5, ry: 4.5 }),
    path('es-accent-line es-stem', 'M82 100 C82 88 80 76 84 60', { pathLength: 1 }),
    path('es-accent es-leaf es-leaf-1', 'M82 78 C71 77 63 69 62 58 C73 59 81 66 82 78Z'),
    path('es-accent es-leaf es-leaf-2', 'M83 66 C90 59 99 56 107 58 C103 67 93 71 83 66Z'),
  ],
  // No search results: a lens, with the sprout's first leaf inside it.
  search: () => [
    dot('es-sheet', 76, 66, 18),
    path('es-line es-line-strong', 'M89 79l14 14'),
    path('es-accent-line es-accent-line-thin', 'M76 74c0-5 0-9 2-14'),
    path('es-accent', 'M77 66c-5 0-8-3-9-7 5 0 8 3 9 7z'),
  ],
  // A page with its corner turned, two lines to come.
  page: () => [
    path('es-sheet', 'M62 44h30l12 12v42a4 4 0 0 1-4 4H62a4 4 0 0 1-4-4V48a4 4 0 0 1 4-4z'),
    path('es-line', 'M92 44v12h12'),
    path('es-accent-line', 'M68 70h26M68 80h18'),
  ],
  // No threads: a speech shape, three dots waiting.
  speech: () => [
    path('es-sheet', 'M58 52a10 10 0 0 1 10-10h32a10 10 0 0 1 10 10v22a10 10 0 0 1-10 10H78l-12 10v-10h0a10 10 0 0 1-8-10z'),
    dot('es-accent', 74, 63), dot('es-accent es-faint', 84, 63), dot('es-accent es-fainter', 94, 63),
  ],
  // No archived threads or agents: a box with its lid on, a handle.
  archive: () => [
    path('es-sheet', 'M60 60h44v34a4 4 0 0 1-4 4H64a4 4 0 0 1-4-4z'),
    path('es-sheet', 'M56 50a4 4 0 0 1 4-4h44a4 4 0 0 1 4 4v6a4 4 0 0 1-4 4H60a4 4 0 0 1-4-4z'),
    path('es-accent-line', 'M75 72h14'),
  ],
  // Files with nothing open, a folder with nothing in it: a folder, its label.
  folder: () => [
    path('es-sheet', 'M56 52a4 4 0 0 1 4-4h14l6 7h24a4 4 0 0 1 4 4v35a4 4 0 0 1-4 4H60a4 4 0 0 1-4-4z'),
    path('es-line', 'M56 64h52'),
    path('es-accent-line', 'M66 80h18'),
  ],
  // No apps: a window, one tile in it.
  window: () => [
    path('es-sheet', 'M54 50a6 6 0 0 1 6-6h44a6 6 0 0 1 6 6v42a6 6 0 0 1-6 6H60a6 6 0 0 1-6-6z'),
    path('es-line', 'M54 58h56'),
    dot('es-accent es-fainter', 61, 51, 1.8),
    s('rect', 'es-accent', { x: 64, y: 67, width: 16, height: 16, rx: 4.5 }),
    path('es-accent-line es-faint', 'M86 71h14M86 79h9'),
  ],
  // No schedules: a clock face, its hands at rest.
  clock: () => [
    dot('es-sheet', 82, 70, 24),
    path('es-line', 'M82 49v3M103 70h-3M82 91v-3M61 70h3'),
    path('es-accent-line', 'M82 59v11l8 6'),
  ],
  // Nothing has happened yet: a crescent moon and one star.
  moon: () => [
    path('es-sheet', 'M92 48a24 24 0 1 0 14 34a19 19 0 0 1-14-34z'),
    dot('es-accent', 104, 52, 2.4),
  ],
}
export const EMPTY_OBJECTS = Object.freeze(Object.keys(OBJECTS))

export function emptyArt(object) {
  const draw = OBJECTS[object]
  if (!draw) throw new TypeError(`emptyArt(): no object called “${object}” (${EMPTY_OBJECTS.join(', ')})`)
  const root = s('svg', `empty-state-art empty-state-${object}`, { viewBox: '0 0 164 140', 'aria-hidden': 'true', focusable: 'false' })
  root.append(path('es-blob', BLOB), ...draw())
  return root
}

export function emptyState({ object, title, body = null, action = null, size = 'pane', level = 'h2', titleId = null, bodyClass = null, props = {} }) {
  const { class: cls = '', ...rest } = props
  return h('div', { ...rest, class: `empty-state empty-state-${size}${cls ? ` ${cls}` : ''}`, 'data-empty': object },
    emptyArt(object),
    h(level, { class: 'display empty-state-title', id: titleId, text: title }),
    body ? h('p', { class: `empty-state-body${bodyClass ? ` ${bodyClass}` : ''}`, text: body }) : null,
    action ? h('div', { class: 'empty-state-action' }, action) : null)
}
