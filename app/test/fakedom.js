import { setOwner } from '../public/owner.js'
const fixtureOwner = { id: 'you', name: 'Sam', accepts: ['you', 'owner', 'person'] }
setOwner(fixtureOwner)
// A DOM small enough to assert against, and no smaller.
//
// There is no browser on the box this project is built on, so the one thing
// `ui.js` promises — that drawing a screen again keeps the nodes already on it
// — could otherwise only be reasoned about. This is the part of the DOM
// `h()` and `morph()` actually use: elements, text, attributes, children,
// listeners, and a `style` that behaves as a browser's does — including
// refusing the one thing this app's content security policy refuses, which is
// a style *attribute*. See `styleOf()`.
//
// It also **writes down every mutation**, which is what makes "a tick that
// changed nothing touched nothing" a thing a test can fail on rather than a
// claim in a comment. It is a fixture: it proves the reconciler's arithmetic,
// not that a phone draws what this says it does.

const EVENT = Symbol('listeners')

export const writes = []

// Mutations are only interesting on nodes that were already on the page.
// Building a fresh tree writes to fresh nodes, which is not the page moving.
export function settle(node) {
  node.__old = true
  for (const child of node.childNodes ?? []) settle(child)
}

export function sinceSettled() {
  return writes.filter((w) => w.node.__old)
}

export function clearWrites() {
  writes.length = 0
}

class FakeNode {
  constructor(nodeType, nodeName) {
    this.nodeType = nodeType
    this.nodeName = nodeName
    this.childNodes = []
    this.parentNode = null
    this.__old = false
  }

  get firstChild() {
    return this.childNodes[0] ?? null
  }

  // In the page or not: a browser's answer is whether the document is an
  // ancestor, and this fixture's document is the `#app` root `browser()` marks.
  get isConnected() {
    let at = this
    while (at.parentNode) at = at.parentNode
    return at.__document === true
  }

  get nextSibling() {
    const siblings = this.parentNode?.childNodes
    if (!siblings) return null
    return siblings[siblings.indexOf(this) + 1] ?? null
  }

  insertBefore(child, before) {
    child.parentNode?.removeChild(child)
    const at = before === null || before === undefined ? this.childNodes.length : this.childNodes.indexOf(before)
    this.childNodes.splice(at < 0 ? this.childNodes.length : at, 0, child)
    child.parentNode = this
    writes.push({ what: 'insert', node: this, child })
    return child
  }

  removeChild(child) {
    const at = this.childNodes.indexOf(child)
    if (at >= 0) this.childNodes.splice(at, 1)
    child.parentNode = null
    writes.push({ what: 'remove', node: this, child })
    return child
  }

  appendChild(child) {
    return this.insertBefore(child, null)
  }

  append(...children) {
    for (const child of children) this.appendChild(child)
  }
}

class FakeText extends FakeNode {
  constructor(text) {
    super(3, '#text')
    this._text = String(text)
  }

  get nodeValue() {
    return this._text
  }

  set nodeValue(value) {
    writes.push({ what: 'text', node: this, value })
    this._text = String(value)
  }

  get textContent() {
    return this._text
  }
}

class FakeElement extends FakeNode {
  constructor(name, { ns = null } = {}) {
    super(1, ns ? name : name.toUpperCase())
    this.tag = name
    this._attrs = new Map()
    this[EVENT] = new Map()
    this.style = styleOf(this)
    this.dataset = {}
    this.disabled = false
    // What a field holds and what it says when empty: the composer reads both
    // off the node, as it would in a browser.
    this.value = ''
    this.placeholder = ''
    // There is no layout here, so every box is zero and nothing scrolls. They
    // are declared because the page's scroll bookkeeping reads them, and
    // arithmetic on `undefined` is a silent `NaN`.
    this.scrollTop = 0
    this.scrollHeight = 0
    this.clientHeight = 0
    this.offsetTop = 0
  }

  focus() { globalThis.document.activeElement = this; this.fire('focus') }
  blur() { if (globalThis.document.activeElement === this) globalThis.document.activeElement = null; this.fire('blur') }

  get attributes() {
    return [...this._attrs].map(([name, value]) => ({ name, value }))
  }

  getAttribute(name) {
    return this._attrs.has(name) ? this._attrs.get(name) : null
  }

  hasAttribute(name) {
    return this._attrs.has(name)
  }

  setAttribute(name, value) {
    // The one attribute a browser will not take from this app. See `styleOf()`
    // below for why the fixture refuses it rather than recording it.
    if (name === 'style') throw new Error(BLOCKED)
    writes.push({ what: 'attr', node: this, name, value: String(value) })
    this._attrs.set(name, String(value))
  }

  // The style attribute as a *browser* writes it: the serialisation of the
  // declarations, reflected onto the node without going through the setter a
  // policy polices. A test that reads `getAttribute('style')` — several do —
  // sees what a real one would.
  _reflect(text) {
    if (text === '') this._attrs.delete('style')
    else this._attrs.set('style', text)
  }

  removeAttribute(name) {
    // Taking the style off is not an inline style and no policy minds it — but
    // it is the declarations that are the truth here, so it goes through them.
    if (name === 'style') {
      while (this.style.length > 0) this.style.removeProperty(this.style.item(0))
      return
    }
    if (!this._attrs.has(name)) return
    writes.push({ what: 'attr-gone', node: this, name })
    this._attrs.delete(name)
  }

  // What the page asks of a node it found by selector: which of the two
  // scrollers it is holding. Reading only — nothing here adds or removes a
  // class, because the page never does either.
  get classList() {
    const classes = (this.getAttribute('class') ?? '').split(' ')
    return { contains: (name) => classes.includes(name) }
  }

  get className() {
    return this.getAttribute('class') ?? ''
  }

  set className(value) {
    this.setAttribute('class', value)
  }

  get textContent() {
    return this.childNodes.map((c) => c.textContent ?? '').join('')
  }

  set textContent(value) {
    while (this.firstChild) this.removeChild(this.firstChild)
    this.appendChild(new FakeText(value))
  }

  addEventListener(type, fn) {
    if (!this[EVENT].has(type)) this[EVENT].set(type, [])
    this[EVENT].get(type).push(fn)
  }

  // What a tap is, for a test: every listener of that type, in order.
  fire(type, event = {}) {
    for (const fn of this[EVENT].get(type) ?? []) fn({ type, target: this, currentTarget: this, ...event })
  }

  // The page asking the browser to open something — a file chooser is the only
  // one. A browser dispatches a click for this, and it is written down, so a
  // test can ask which input was opened without a chooser existing.
  click() {
    writes.push({ what: 'click', node: this })
    this.fire('click')
  }

  // Class selectors only — `.msgs`, `.line.right`, `.msgs, .scroll` — which is
  // every selector `app.js` asks this for. Anything else would be a selector
  // this fixture quietly answered wrongly, so it throws instead.
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] ?? null
  }

  querySelectorAll(selector) {
    const wanted = String(selector)
      .split(',')
      .map((part) => part.trim())
      .filter((part) => part !== '')
      .map((part) => {
        if (!/^(\.[A-Za-z0-9_-]+)+$/.test(part)) throw new Error(`test/fakedom.js: only class selectors, not ${part}`)
        return part.slice(1).split('.')
      })
    const out = []
    const walk = (node) => {
      for (const child of node.childNodes) {
        if (child.nodeType === 1) {
          const classes = (child.getAttribute('class') ?? '').split(' ')
          if (wanted.some((needed) => needed.every((c) => classes.includes(c)))) out.push(child)
          walk(child)
        }
      }
    }
    walk(this)
    return out
  }

  // A browser scrolls; this writes down that it was asked to, which is what
  // makes "the chat opened on the last thing the person said" a thing a test can
  // fail on. There is no layout here, so there is no position to check — the
  // node is the assertion.
  scrollIntoView(options) {
    writes.push({ what: 'scroll-to', node: this, options })
  }
}

// A node's declarations, and the policy this box cannot otherwise enforce.
//
// **The fixture refuses a style attribute, because the deployed browser does.**
// The app is served under `default-src 'self'` with no `style-src`, so
// `setAttribute('style', ...)` is an inline style and a browser refuses it,
// leaving the element drawing what it drew before. This fixture used to reflect
// every property write back through `setAttribute`, which meant the reconciler
// could copy a style attribute across all day here and only fail on a phone:
// the one defect this file exists to catch, missed for a week. So property
// writes reflect *internally* now, as a browser's own do, and the public
// `setAttribute('style', ...)` throws — which is what makes "no blocked style
// mechanism came back" something the suite fails on rather than something a
// console tells the owner.
//
// What it is otherwise: the part of `CSSStyleDeclaration` `ui.js` uses.
// `style.width = '60px'` and `style.setProperty('width', '60px')` are the same
// write, `item()`/`length` enumerate in the order they were first set, and a
// value of `''` removes.
//
// **A shorthand set to a value holding `var()` is expanded, because a browser's
// expansion of exactly that is observable and cost the agents their colour.**
// `style.background = 'var(--persona-olive)'` does not read back as what was written: the
// browser stores the shorthand's longhands as *pending-substitution* values —
// it enumerates every one of them through `item()`/`length` and answers `''`
// for each through `getPropertyValue()`, because a custom property is not
// resolved until cascade time. Only `getPropertyValue('background')`, the
// shorthand, answers the text. A reconciler that diffed the declarations by
// reading them back therefore saw "nothing there" and took the colour off; this
// fixture had no expansion at all, so it saw a tidy `background: var(--persona-olive)`
// and called it fine. It models the one case now — see `PENDING` below.
//
// A shorthand with an ordinary value (`background: red`) is still stored under
// the name it was written with. That is a simplification and a safe one: with
// or without expansion it reads back as what was written, so no code in this
// app can tell the difference. `var()` is the case where it can.
const BLOCKED =
  'test/fakedom.js: a style attribute is refused by this app’s content security policy (default-src ‘self’, no style-src). Write the properties instead — see restyle() in public/ui.js.'

// The longhands of the shorthands this app writes into a tree. A browser knows
// them all; this knows the ones a page here has ever set, and throws for a
// shorthand it has not been taught rather than expanding it wrongly — see
// `write()`.
const PENDING = {
  background: [
    'background-image',
    'background-position-x',
    'background-position-y',
    'background-size',
    'background-repeat',
    'background-attachment',
    'background-origin',
    'background-clip',
    'background-color',
  ],
  'border-radius': [
    'border-top-left-radius',
    'border-top-right-radius',
    'border-bottom-right-radius',
    'border-bottom-left-radius',
  ],
  'border-color': ['border-top-color', 'border-right-color', 'border-bottom-color', 'border-left-color'],
  padding: ['padding-top', 'padding-right', 'padding-bottom', 'padding-left'],
  flex: ['flex-grow', 'flex-shrink', 'flex-basis'],
}

// Which shorthand, if any, a longhand belongs to — for `removeProperty()` and
// for reading the shorthand back.
const OWNER = new Map()
for (const [shorthand, longhands] of Object.entries(PENDING)) {
  for (const name of longhands) OWNER.set(name, shorthand)
}

function styleOf(element) {
  const props = new Map() // dashed name -> { value, priority, pending }

  // A shorthand whose value names a custom property. The declarations that go
  // into the block are its longhands, each unresolved: enumerated, and empty
  // to every reader but the shorthand itself.
  const substitute = (name, value, priority) => {
    for (const longhand of PENDING[name]) {
      props.set(longhand, { value: '', priority: priority ?? '', pending: { shorthand: name, text: String(value) } })
    }
    writes.push({ what: 'style', node: element, name, value: String(value) })
    reflect()
  }

  const write = (name, value, priority) => {
    if (value === '' || value === null || value === undefined) {
      const longhands = PENDING[name] ?? [name]
      const gone = longhands.filter((longhand) => props.delete(longhand))
      if (gone.length === 0) return reflect()
      writes.push({ what: 'style-gone', node: element, name })
      return reflect()
    }
    if (name in PENDING) {
      // A shorthand a browser would expand. Only the `var()` case is modelled,
      // and a plain value is kept whole — the comment above says why.
      if (/var\(/.test(String(value))) return substitute(name, value, priority)
      for (const longhand of PENDING[name]) props.delete(longhand)
    } else if (OWNER.has(name)) {
      // A longhand written by hand where its shorthand is already pending: the
      // page does not do this, and answering it wrongly would be worse than
      // saying so.
      const held = props.get(name)
      if (held?.pending) throw new Error(`test/fakedom.js: ${name} is pending on ${held.pending.shorthand}; this fixture does not model that`)
    }
    props.set(name, { value: String(value), priority: priority ?? '', pending: null })
    writes.push({ what: 'style', node: element, name, value: String(value) })
    reflect()
  }

  // What a browser serialises the block as: a pending shorthand comes back as
  // the shorthand it was written as, once, and not as nine empty longhands.
  const reflect = () => {
    const parts = []
    const said = new Set()
    for (const [name, { value, priority, pending }] of props) {
      if (pending) {
        if (said.has(pending.shorthand)) continue
        said.add(pending.shorthand)
        parts.push(`${pending.shorthand}: ${pending.text}${priority ? ` !${priority}` : ''}`)
        continue
      }
      parts.push(`${name}: ${value}${priority ? ` !${priority}` : ''}`)
    }
    element._reflect(parts.join('; '))
  }

  // The shorthand of a pending group reads back as the text it was written
  // with, and its longhands read back empty. Both are what a browser answers.
  const read = (name) => {
    const held = props.get(name)
    if (held) return held.value
    const longhands = PENDING[name]
    if (!longhands) return ''
    const pending = props.get(longhands[0])?.pending
    if (!pending || pending.shorthand !== name) return ''
    return longhands.every((longhand) => props.get(longhand)?.pending?.shorthand === name) ? pending.text : ''
  }

  const api = {
    get length() {
      return props.size
    },
    item: (i) => [...props.keys()][i] ?? '',
    getPropertyValue: (name) => read(dashed(String(name))),
    getPropertyPriority: (name) => props.get(dashed(String(name)))?.priority ?? '',
    setProperty: (name, value, priority = '') => write(dashed(String(name)), value, priority),
    removeProperty: (name) => {
      const was = read(dashed(String(name)))
      write(dashed(String(name)), '', '')
      return was
    },
  }

  return new Proxy(api, {
    get(target, key) {
      if (typeof key !== 'string') return Reflect.get(target, key)
      if (key in target) return Reflect.get(target, key)
      return read(dashed(key))
    },
    set(target, key, value) {
      if (typeof key !== 'string') return Reflect.set(target, key, value)
      write(dashed(key), value, '')
      return true
    },
  })
}

function dashed(key) {
  return key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)
}

export function installDom() {
  // Synthetic runtime identity for existing UI fixtures; production reads /owner.
  setOwner(fixtureOwner)
  const document = {
    // A `<style>` element the page builds is an inline style too, and refused
    // by the same policy for the same reason. The app has never made one; this
    // is here so that it cannot start making one unnoticed.
    createElement: (name) => {
      if (String(name).toLowerCase() === 'style') throw new Error(BLOCKED)
      return new FakeElement(name)
    },
    createElementNS: (_ns, name) => new FakeElement(name, { ns: true }),
    createTextNode: (text) => new FakeText(text),
  }
  globalThis.document = document
  clearWrites()
  return document
}

export function body() {
  return new FakeElement('div')
}
