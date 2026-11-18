import { agentFace } from './agent-face.js'
import { variantFor } from './face-identity.js'

// Making elements, reconciling them, and the design's icons.
//
// This is the whole of the "view library": one function that builds a DOM node
// from a tag, some properties and some children, one that puts a freshly built
// tree into the page *by keeping the nodes already there*, and a table of
// Lucide-style outlines at the stroke width the design names. There is no
// framework and no build step.
//
// **The page is drawn whole and applied in place.** A screen is still built
// from scratch on every change, which is what keeps one code path per row — but
// what reaches the DOM is the difference. A tick that changes nothing touches
// nothing, so an element a finger is on, or a test is holding, is the same
// element a second later. `morph()` below is the whole of that, and `key` is
// how a row says which row it is when the list around it reorders.
//
// The one thing that makes this possible is that a handler is *stored on the
// node* and dispatched through one listener per event type, rather than being
// the listener itself: the node an event was bound to outlives every redraw,
// and each redraw hands it the closure that now knows the current state.

export function h(tag, props = {}, ...children) {
  const node = document.createElement(tag)
  for (const [key, value] of Object.entries(props ?? {})) {
    if (key === 'key') {
      // Not an attribute: which row this is, for the reconciler, and nothing
      // the page or a stylesheet can see.
      node.__key = value === null || value === undefined ? null : String(value)
      continue
    }
    if (value === null || value === undefined || value === false) continue
    if (key === 'class') node.className = value
    else if (key === 'text') node.textContent = value
    else if (key === 'style') dress(node, value)
    else if (key.startsWith('on') && typeof value === 'function') on(node, key.slice(2), value)
    else if (key === 'dataset') Object.assign(node.dataset, value)
    else if (value === true) node.setAttribute(key, '')
    else node.setAttribute(key, value)
  }
  append(node, children)
  return node
}

// A style, written property by property and **never as an attribute**.
//
// This page is served under `default-src 'self'` with no `style-src`, and to
// that policy a `style="..."` attribute is an inline style: a browser refuses
// it. The refusal is a line in the console and nothing else — the declaration
// never lands, and the element goes on drawing whatever it was drawing, which
// is how a blocked style reads as a layout that is subtly wrong rather than as
// an error. Assigning `node.style.foo` is not an inline style and is not policed,
// so it is the only way a style is written here, in `dress()` and in
// `restyle()` alike. Conforming to the policy costs one loop; loosening it
// would cost the page its one real defence against injected markup.
//
// **What was asked for is kept on the node**, as `__css`, and that is what
// `restyle()` reads — never the browser's own reading of the declarations. See
// the comment on `restyle()` for the afternoon that cost.
export function dress(node, declarations) {
  if (declarations === null || typeof declarations !== 'object') {
    throw new TypeError('h(): style is an object of properties — a string would become a style attribute, which this page’s content security policy refuses')
  }
  const css = { ...(node.__css ?? null) }
  for (const [name, value] of Object.entries(declarations)) {
    if (value === null || value === undefined) continue
    const text = String(value)
    // An empty declaration is the absence of one — it is how `blob()` says "no
    // size given" — so it comes off the node and off the record together, and
    // on a fresh node it is the nothing it looks like.
    if (text === '') {
      delete css[name]
      unput(node, name)
      continue
    }
    css[name] = text
    put(node, name, text)
  }
  node.__css = css
}

// One declaration onto a node. A custom property is not a key on `style`, in a
// browser — `style['--persona-gold'] = x` does nothing at all — so it goes the one way
// that works, which is also not an attribute and so not policed.
function put(node, name, value) {
  if (name.startsWith('--')) node.style.setProperty(name, value)
  else node.style[name] = value
}

function unput(node, name) {
  if (name.startsWith('--')) node.style.removeProperty(name)
  else node.style[name] = ''
}

// A handler, held on the node and called through one listener per type. The
// listener is added once and never replaced, so reconciling a node is a matter
// of handing it the new closure — the binding itself survives, which is the
// same thing as saying the node does.
export function on(node, type, fn) {
  if (!node.__on) node.__on = {}
  if (!(type in node.__on)) {
    node.addEventListener(type, (event) => node.__on[type]?.(event))
  }
  node.__on[type] = fn
}

function append(node, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue
    if (Array.isArray(child)) append(node, child)
    else node.append(child.nodeType ? child : document.createTextNode(String(child)))
  }
}

// A node the page keeps between redraws: the composer, the search field, the
// rename field — the three a cursor can live in.
//
// It is *placed*, not built into the tree. Appending the real node while a
// screen is being built would move it out of the live page before the
// reconciler ever saw it, which is a blurred field and a lost selection on
// every redraw — and a redraw that changed nothing would still have moved it.
// So the tree carries a stand-in, and `morph()` puts the real node where the
// stand-in is: already there, in the ordinary case, and then nothing happens
// at all.
export function kept(node) {
  if (!node.__stub) {
    node.__stub = document.createElement('keep-node')
    node.__stub.__keep = node
  }
  return node.__stub
}

function real(node) {
  return node.__keep ?? node
}

// A freshly built subtree on its way into the page, with every stand-in in it
// swapped for the node it stands for. A subtree that is inserted whole is never
// walked by `patch()`, so this is where its kept fields are put in.
function hydrate(node) {
  if (node.nodeType !== 1) return node
  for (let child = node.firstChild; child; ) {
    const after = child.nextSibling
    if (child.__keep) {
      node.insertBefore(child.__keep, child)
      node.removeChild(child)
    } else {
      hydrate(child)
    }
    child = after
  }
  return node
}

// ------------------------------------------------------------- reconciling

// Put `children` into `parent`, keeping every node that can be kept.
//
// What a redraw hands this is a whole tree of fresh nodes; what comes out is
// the tree that was already on the page, updated where it differs and left
// alone where it does not — so a stream tick that changes nothing does nothing,
// and a row that moves up the list moves rather than being rebuilt.
export function morph(parent, children) {
  const wanted = []
  collect(wanted, Array.isArray(children) ? children : [children])
  reconcile(parent, wanted)
}

function collect(out, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue
    if (Array.isArray(child)) collect(out, child)
    else out.push(child.nodeType ? real(child) : textNode(String(child)))
  }
}

function textNode(text) {
  return document.createTextNode(text)
}

// A node is matched by its key if it has one, and by its position if it does
// not — which is what makes `key` worth writing on exactly the lists that
// reorder, and nowhere else.
function reconcile(parent, wanted) {
  const keyed = new Map()
  for (let node = parent.firstChild; node; node = node.nextSibling) {
    if (node.__key !== null && node.__key !== undefined) keyed.set(node.__key, node)
  }

  // **A keyed node that is going away is taken out where it stands**, before
  // anything is matched against it. Left in place, it sits at the cursor and
  // no unkeyed sibling after it can match by position, so every one of them is
  // built again from scratch — which is what the loading card coming off did to
  // the transcript under it until 2026-09-29: a whole fresh `.msgs`, drawn after
  // the reader had been put on their last message, and a scroller whose place
  // was thrown away with it. Moving the survivors up instead would lose that
  // place too — a browser forgets an element's scroll when it is moved.
  const asked = new Set()
  for (const next of wanted) if (next.__key !== null && next.__key !== undefined) asked.add(next.__key)
  const leaving = (node) => node && node.__key !== null && node.__key !== undefined && !asked.has(node.__key)

  let cursor = parent.firstChild
  for (const next of wanted) {
    while (leaving(cursor)) {
      const after = cursor.nextSibling
      keyed.delete(cursor.__key)
      parent.removeChild(cursor)
      cursor = after
    }
    let node = null
    if (next.__key !== null && next.__key !== undefined) {
      const candidate = keyed.get(next.__key)
      if (candidate && alike(candidate, next)) {
        node = candidate
        keyed.delete(next.__key)
      }
    } else if (cursor && (cursor.__key === null || cursor.__key === undefined) && alike(cursor, next)) {
      node = cursor
    }

    if (node === null) {
      parent.insertBefore(hydrate(next), cursor)
      continue
    }
    if (node === cursor) cursor = cursor.nextSibling
    else parent.insertBefore(node, cursor) // a row that moved; the node goes with it
    patch(node, next)
  }

  // Whatever is left has not been asked for: matched nodes are all behind the
  // cursor by now, either because it walked past them or because they were
  // moved in front of it.
  while (cursor) {
    const after = cursor.nextSibling
    parent.removeChild(cursor)
    cursor = after
  }
}

function alike(a, b) {
  return a.nodeType === b.nodeType && a.nodeName === b.nodeName
}

// The node on the page, made to say what the fresh one says. Nothing is written
// that already reads the same, so "nothing changed" costs no mutation at all.
function patch(node, next) {
  // The same object: a field a cursor lives in is made once and handed back
  // every redraw, and touching it is exactly what must not happen.
  if (node === next) return
  if (node.nodeType === 3) {
    if (node.nodeValue !== next.nodeValue) node.nodeValue = next.nodeValue
    return
  }
  if (node.nodeType !== 1) return

  // `style` is the one attribute this never copies — see `restyle()` below,
  // which carries it across declaration by declaration instead.
  const wanted = next.attributes
  for (let i = 0; i < wanted.length; i += 1) {
    const attr = wanted[i]
    if (attr.name === 'style') continue
    if (node.getAttribute(attr.name) !== attr.value) node.setAttribute(attr.name, attr.value)
  }
  const had = node.attributes
  for (let i = had.length - 1; i >= 0; i -= 1) {
    const name = had[i].name
    if (name === 'style') continue
    if (!next.hasAttribute(name)) node.removeAttribute(name)
  }
  restyle(node, next)
  // `disabled` and `value` are properties a page can set by hand — the approve
  // card disables its own button the moment it is pressed — so they are put
  // back from the attribute rather than left where the hand left them.
  if ('disabled' in node) {
    const wantsDisabled = next.hasAttribute('disabled')
    if (node.disabled !== wantsDisabled) node.disabled = wantsDisabled
  }
  // A checkbox's ticked state is a property a click moves too; the attribute,
  // which is what the note says, puts it back.
  if (node.type === 'checkbox' && 'checked' in node) {
    const wantsChecked = next.hasAttribute('checked')
    if (node.checked !== wantsChecked) node.checked = wantsChecked
  }
  // A native select changes the option's live property without changing its
  // attribute. Restore the server's selection after a refused model/engine edit.
  if (node.tagName === 'OPTION' && 'selected' in node) {
    const wantsSelected = next.hasAttribute('selected')
    if (node.selected !== wantsSelected) node.selected = wantsSelected
  }

  hand(node, next)
  const kids = []
  for (let child = next.firstChild; child; child = child.nextSibling) kids.push(real(child))
  reconcile(node, kids)
}

// The style on the node, made to say what the fresh one says — one declaration
// at a time, and never by writing the attribute.
//
// **This is where a content security policy meets a reconciler**, and it is
// worth saying why the obvious version is wrong. Copying attributes is how the
// rest of `patch()` works, and `style` is an attribute, so it used to be copied
// with the others. But `setAttribute('style', ...)` is an inline style under
// `default-src 'self'`: the browser refuses the call, and the declarations the
// redraw asked for never take effect. The element is then stale for as long as
// it stays on the page — the reconciler either believes the attribute it wrote
// and never tries again, or tries again and is refused again — so every kept
// node whose style changed went on drawing the style it had. A ring that
// followed a session's status, a blob that changed colour, a quota bar's width,
// the transcript's padding under the composer: all of them, and only on the
// deployed app, because the fixture this suite runs on had no policy in it.
//
// **The second wrong version was reading the browser back**, and it cost the
// agents' colours for an afternoon. This diffed `next.style` against
// `node.style` through `item()`, `length` and `getPropertyValue()` — and a
// shorthand set to a value holding `var()` does not read back the way it was
// written. `style.background = 'var(--persona-gold)'` is a *pending-substitution* value:
// the browser stores the shorthand's longhands (`background-image`,
// `background-color`, and seven more), enumerates every one of them, and
// answers `''` for each, because the custom property is not resolved until
// cascade time and there is nothing to hand back. So the copy loop compared
// `''` with `''` and skipped — a blob that changed colour never changed — and
// the removal loop, which took off anything the fresh node did not name, read
// the same `''` as "not named" and took the colour *off*. The owner, 2026-09-25:
// "the agent icons are acting weird right now and the icons shapes keep
// disappearing" — the shape stayed, because `border-radius` holds no `var()`
// and reads back as what it was written as; the colour went. Every blob, every
// breathing halo, the bang's `border-color`: all of them, and only in a
// browser, because the fixture expanded no shorthands and so had nothing to
// answer `''` with.
//
// So the two sides are **the declarations the page asked for**, kept on each
// node as `__css` by `dress()`, and the browser is never asked what it made of
// them. Removals go first, so that a shorthand going off cannot take a longhand
// arriving in the same redraw with it. Per-property writes are not inline
// styles and are not policed; nothing that already reads the same is written,
// so a redraw that changes no style still touches nothing.
const NO_CSS = {}

function restyle(node, next) {
  const want = next.__css ?? NO_CSS
  const have = node.__css ?? NO_CSS
  for (const name of Object.keys(have)) {
    if (name in want) continue
    unput(node, name)
  }
  for (const [name, value] of Object.entries(want)) {
    if (have[name] === value) continue
    put(node, name, value)
  }
  node.__css = want
}

// The fresh closures, onto the node that keeps its listeners.
function hand(node, next) {
  const fresh = next.__on
  if (node.__on) {
    for (const type of Object.keys(node.__on)) {
      node.__on[type] = fresh?.[type] ?? undefined
    }
  }
  if (!fresh) return
  for (const [type, fn] of Object.entries(fresh)) on(node, type, fn)
}

// An agent's blob: its colour, its shape and its initial. The shape is an
// organic `border-radius`, which is why it is a style and not a class.
//
// **The status ring is not drawn here**, and that is the point: it used to be a
// `box-shadow` among this node's other inline declarations, so whether a row
// still had something to say was one declaration inside a style the reconciler
// carries across wholesale. It is its own element now — see `avatar()` — so a
// row that has been read loses a *node*, which is the same thing that happens
// to its status box.
// Core-defined options are interpreted here; CSS-only manifest options need no
// JavaScript. Decorations use this same paint as the face itself.
export function avatarLook(look) {
  if (look.agentId === undefined || look.avatarPaint) return look
  return { ...look,
    avatarPaint: true,
    shape: `var(--agent-avatar-shape, ${look.shape})`,
    color: `var(--agent-avatar-tone-${look.tone}, ${look.color})`,
    ink: `var(--agent-avatar-ink-${look.tone}, ${look.ink || 'var(--text-on-avatar)'})`,
    tint: `var(--agent-avatar-tint-${look.tone}, ${look.tint})`,
  }
}

export function blob(look, { size, font, className = '', key = null, inline = false, decorative = false } = {}) {
  const paint = avatarLook(look)
  const themed = look.agentId !== undefined && !look.imageSrc
  return h(inline ? 'span' : 'div', {
    key,
    class: `blob ${className}`.trim(),
    'data-agent': look.agentId,
    'data-latte-initial': themed ? look.initial : null,
    'data-face-initial': themed ? look.initial : null,
    'data-face-variant': themed ? variantFor(look.agentId) : null,
    'data-face-small': themed && size && size <= 26 ? '' : null,
    style: {
      width: size ? `${size}px` : '',
      height: size ? `${size}px` : '',
      fontSize: font ? `${font}px` : '',
      borderRadius: paint.shape,
      background: look.imageSrc ? '' : paint.color,
      color: paint.ink || '',
    },
    // Empty decorative faces keep markdown/copy/caret offsets intact. The
    // generated glyph is still drawn by this component's own CSS.
    'data-initial': decorative && !look.imageSrc ? look.initial : null,
    'aria-hidden': decorative ? 'true' : null,
    contenteditable: decorative ? 'false' : null,
  }, look.imageSrc ? h('img', { class: 'avatar-image', src: look.imageSrc, alt: '', draggable: 'false' }) : [
    !decorative ? h('span', { class: 'blob-letter', text: look.initial }) : null,
    themed ? agentFace(look.agentId) : null,
  ])
}

// ------------------------------------------------------------------- icons

const NS = 'http://www.w3.org/2000/svg'

function svg(size, paths, { stroke = 2.75, viewBox = '0 0 24 24', fill = 'none' } = {}) {
  const root = document.createElementNS(NS, 'svg')
  root.setAttribute('width', size)
  root.setAttribute('height', size)
  root.setAttribute('viewBox', viewBox)
  root.setAttribute('fill', fill)
  root.setAttribute('stroke', 'currentColor')
  root.setAttribute('stroke-width', stroke)
  root.setAttribute('stroke-linecap', 'round')
  root.setAttribute('stroke-linejoin', 'round')
  // Through `dress()` and not `root.style.flex = ...` directly: a style the
  // tree carries has to be on the node's own record, or the redraw that keeps
  // this node has no idea it is there. See `restyle()`.
  dress(root, { flex: 'none' })
  for (const [name, attrs] of paths) {
    const el = document.createElementNS(NS, name)
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v)
    root.append(el)
  }
  return root
}

// Loam's mark, the earth blob the sign-in page and the icon wear, drawn in
// the page so it shows even when nothing more can be fetched (the ended
// session's screen, 0.2.1).
export function loamMark(size = 52) {
  const root = document.createElementNS(NS, 'svg')
  for (const [k, v] of Object.entries({ width: size, height: size, viewBox: '0 0 64 64', 'aria-hidden': 'true', focusable: 'false', class: 'loam-mark' })) root.setAttribute(k, v)
  const clip = document.createElementNS(NS, 'clipPath'), id = 'loam-mark-clip'
  clip.setAttribute('id', id)
  const blob = document.createElementNS(NS, 'path')
  blob.setAttribute('d', 'M34 3 C52 3 61 15 60 33 C59 51 49 61 31 61 C13 61 3 50 4 32 C5 14 16 3 34 3 Z')
  clip.append(blob)
  const defs = document.createElementNS(NS, 'defs'); defs.append(clip)
  const g = document.createElementNS(NS, 'g'); g.setAttribute('clip-path', `url(#${id})`)
  for (const [tag, attrs, tone] of [['rect', { width: 64, height: 64 }, 'sand'],
    ['path', { d: 'M0 27 C12 21 22 33 34 27 S54 23 64 29 V64 H0 Z' }, 'umber'],
    ['path', { d: 'M0 45 C14 39 24 51 36 45 S56 41 64 47 V64 H0 Z' }, 'earth']]) {
    const el = document.createElementNS(NS, tag)
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v)
    // Through the CSSOM, which the page's CSP allows (a style attribute it
    // would refuse); the colours are core tokens (tokens.css).
    el.style.fill = `var(--loam-mark-${tone})`
    g.append(el)
  }
  root.append(defs, g)
  return root
}

const PIN = 'M9 10.8a2 2 0 0 1-1.1 1.8l-1.8.9A2 2 0 0 0 5 15.2V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.8a2 2 0 0 0-1.1-1.8l-1.8-.9A2 2 0 0 1 15 10.8V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z'

export const icon = {
  download: (s = 18) => svg(s, [['path', { d: 'M12 4v11m-5-5 5 5 5-5M5 20h14' }]], { stroke: 2.2 }),
  key: (s = 18) => svg(s, [['circle', { cx: 7.5, cy: 15.5, r: 4.5 }], ['path', { d: 'M10.7 12.3 20.5 2.5M17 6l3 3M14.5 8.5l2 2' }]]),
  lock: (s = 18) => svg(s, [['rect', { x: 4, y: 10, width: 16, height: 11, rx: 2 }], ['path', { d: 'M8 10V7a4 4 0 0 1 8 0v3M12 14v3' }]]),
  power: (s = 18) => svg(s, [['path', { d: 'M12 3v9M6.2 5.8a9 9 0 1 0 11.6 0' }]]),
  clock: (s = 18) => svg(s, [['circle', { cx: 12, cy: 12, r: 9 }], ['path', { d: 'M12 7v5l3 2' }]]),
  refresh: (s = 18) => svg(s, [['path', { d: 'M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3a7 7 0 0 0 11.6-1' }]]),
  search: (s = 18) => svg(s, [['circle', { cx: 11, cy: 11, r: 7 }], ['path', { d: 'M20 20l-3.5-3.5' }]]),
  plus: (s = 18) => svg(s, [['path', { d: 'M12 5v14M5 12h14' }]]),
  minus: (s = 18) => svg(s, [['path', { d: 'M5 12h14' }]]),
  close: (s = 12) => svg(s, [['path', { d: 'M6 6l12 12M18 6L6 18' }]], { stroke: 3 }),
  pin: (s = 18) => svg(s, [['path', { d: 'M12 17v5' }], ['path', { d: PIN }]], { stroke: 2.5 }),
  chevronLeft: (s = 20) => svg(s, [['path', { d: 'M10 2L2 10l8 8' }]], { viewBox: '0 0 12 20' }),
  chevronRight: (s = 10) => svg(s, [['path', { d: 'M9 6l6 6-6 6' }]], { stroke: 3 }),
  chevronDown: (s = 12) => svg(s, [['path', { d: 'M6 9l6 6 6-6' }]]),
  check: (s = 14) => svg(s, [['path', { d: 'M5 12l5 5L20 7' }]], { stroke: 3 }),
  external: (s = 18) => svg(s, [['path', { d: 'M14 3h7v7M21 3L10 14M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5' }]]),
  link: (s = 18) => svg(s, [['path', { d: 'M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2' }]]),
  send: (s = 16) => svg(s, [['path', { d: 'M12 19V5M5 12l7-7 7 7' }]], { stroke: 3 }),
  mic: (s = 16) =>
    svg(s, [
      ['rect', { x: 9, y: 2, width: 6, height: 12, rx: 3 }],
      ['path', { d: 'M5 10a7 7 0 0 0 14 0M12 17v4' }],
    ]),
  file: (s = 14) =>
    svg(s, [
      ['path', { d: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z' }],
      ['path', { d: 'M14 3v5h5' }],
    ]),
  image: (s = 14) =>
    svg(s, [
      ['rect', { x: 3, y: 4, width: 18, height: 16, rx: 3 }],
      ['circle', { cx: 8.5, cy: 9.5, r: 1.6 }],
      ['path', { d: 'M4 17l4.5-4.5L12 16l3.5-3.5L20 17' }],
    ]),
  // The editor's List and Task: three bulleted lines, and a ticked box.
  list: (s = 16) =>
    svg(s, [
      ['path', { d: 'M9 6h11M9 12h11M9 18h11' }],
      ['circle', { cx: 4.5, cy: 6, r: 1.3 }],
      ['circle', { cx: 4.5, cy: 12, r: 1.3 }],
      ['circle', { cx: 4.5, cy: 18, r: 1.3 }],
    ]),
  task: (s = 16) =>
    svg(s, [
      ['rect', { x: 3.5, y: 3.5, width: 17, height: 17, rx: 4 }],
      ['path', { d: 'M8 12.5l3 3 5-6.5' }],
    ]),
  folder: (s = 18) => svg(s, [['path', { d: 'M3 7a2 2 0 0 1 2-2h3.5l2 2.5H19a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z' }]]),
  sun: (s = 20) =>
    svg(s, [
      ['circle', { cx: 12, cy: 12, r: 4.4 }],
      ['path', { d: 'M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4' }],
    ]),
  moon: (s = 20) => svg(s, [['path', { d: 'M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z' }]]),
  // The design's settings glyph: two sliders with their handles.
  sliders: (s = 20) =>
    svg(
      s,
      [
        ['path', { d: 'M4 7h10M18 7h2M4 17h4M12 17h8' }],
        ['circle', { cx: 16, cy: 7, r: 2.2 }],
        ['circle', { cx: 10, cy: 17, r: 2.2 }],
      ],
      { stroke: 2.4 },
    ),
  grip: (s = 16) => svg(s, [8, 16].flatMap((cx) => [5, 12, 19].map((cy) => ['circle', { cx, cy, r: 1.2 }])), { fill: 'currentColor' }),
  dots: (s = 20) =>
    svg(s, [
      ['circle', { cx: 5, cy: 12, r: 1.4 }],
      ['circle', { cx: 12, cy: 12, r: 1.4 }],
      ['circle', { cx: 19, cy: 12, r: 1.4 }],
    ], { fill: 'currentColor' }),
  users: (s = 18) =>
    svg(s, [
      ['circle', { cx: 9, cy: 8, r: 3.5 }],
      ['path', { d: 'M2.5 20a6.5 6.5 0 0 1 13 0' }],
      ['path', { d: 'M16 4.5a3.5 3.5 0 0 1 0 7M21.5 20a6.5 6.5 0 0 0-5-6.3' }],
    ]),
  puzzle: (s = 18) => svg(s, [['path', { d: 'M4 4h6V3a2 2 0 0 1 4 0v1h6v6h1a2 2 0 0 1 0 4h-1v6h-6v-1a2 2 0 0 0-4 0v1H4v-6H3a2 2 0 0 1 0-4h1z' }]]),
  code: (s = 18) => svg(s, [['path', { d: 'M8 9l-4 3 4 3M16 9l4 3-4 3M14 5l-4 14' }]]),
  apps: (s = 20) => svg(s, [3, 14].flatMap((x) => [3, 14].map((y) => ['rect', { x, y, width: 7, height: 7, rx: 1.5 }]))),
  copy: (s = 13) =>
    svg(s, [
      ['rect', { x: 9, y: 9, width: 12, height: 12, rx: 2 }],
      ['path', { d: 'M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1' }],
    ]),
  reply: (s = 15) => svg(s, [['path', { d: 'M9 5 3 11l6 6M3 11h10a7 7 0 0 1 7 7v1' }]], { stroke: 2.2 }),
  branch: (s = 15) => svg(s, [['path', { d: 'M6 20V4m0 10h5a7 7 0 0 0 7-7V4m-4 4 4-4 4 4' }]], { stroke: 2.2 }),
  // The desktop rail's two tabs and the button that hides the list column.
  chat: (s = 20) =>
    svg(s, [['path', { d: 'M20.5 11.7a8 8 0 0 1-8.6 8 8.7 8.7 0 0 1-3.6-.9L3.5 20.5l1.7-4.8a8 8 0 0 1-.9-3.7 8 8 0 0 1 8-8 8 8 0 0 1 8.2 7.7z' }]]),
  panelLeft: (s = 18) =>
    svg(s, [
      ['rect', { x: 3, y: 4, width: 18, height: 16, rx: 3 }],
      ['path', { d: 'M9.5 4v16' }],
    ], { stroke: 2.4 }),
  panelRight: (s = 18) => svg(s, [['rect', { x: 3, y: 4, width: 18, height: 16, rx: 3 }], ['path', { d: 'M14.5 4v16' }]], { stroke: 2.4 }),
  arrowLeft: (s = 18) => svg(s, [['path', { d: 'M20 12H4m6-6-6 6 6 6' }]], { stroke: 2.2 }),
  swap: (s = 18) => svg(s, [['path', { d: 'M4 8h16m-4-4 4 4-4 4M20 16H4m4-4-4 4 4 4' }]], { stroke: 2.2 }),
  // The design's effort glyph: three bars, climbing.
  bars: (s = 18) => svg(s, [['path', { d: 'M6 20v-5M12 20V9M18 20V4' }]], { stroke: 2.6 }),
  // Four corners pushed outward: the draft, opened on a screen of its own. Quiet
  // by design — it sits in the corner of the field it expands and must not read
  // as a second send.
  expand: (s = 16) =>
    svg(s, [['path', { d: 'M9 4H4v5M15 4h5v5M9 20H4v-5M15 20h5v-5' }]], { stroke: 2.4 }),
  pencil: (s = 14) =>
    svg(s, [['path', { d: 'M4 20l1-4L16.5 4.5a2.1 2.1 0 0 1 3 3L8 19z' }]], { stroke: 2.4 }),
  // Listen: a speaker and two arcs of its sound. Brief: the condensed-lines
  // mark — four lines shortening, a long reply said short.
  speaker: (s = 16) =>
    svg(s, [
      ['path', { d: 'M4 9.5h3.2L12 5.5v13l-4.8-4H4z' }],
      ['path', { d: 'M15.5 9a4.2 4.2 0 0 1 0 6M18.3 6.5a8 8 0 0 1 0 11' }],
    ], { stroke: 2.2 }),
  brief: (s = 16) => svg(s, [['path', { d: 'M4 6h16M4 10.5h12M4 15h8M4 19.5h4' }]], { stroke: 2.4 }),
  // The player's own two, filled: a glyph a thumb finds in the dark.
  play: (s = 16) => svg(s, [['path', { d: 'M8 5.5v13l10.5-6.5z' }]], { stroke: 1.5, fill: 'currentColor' }),
  pause: (s = 16) =>
    svg(s, [
      ['rect', { x: 6.5, y: 5.5, width: 3.5, height: 13, rx: 1 }],
      ['rect', { x: 14, y: 5.5, width: 3.5, height: 13, rx: 1 }],
    ], { stroke: 1, fill: 'currentColor' }),
}

// The listening chip's animated bars.
export function wave() {
  return h('span', { class: 'wave' }, ...[0, 1, 2, 3, 4].map(() => h('i')))
}
