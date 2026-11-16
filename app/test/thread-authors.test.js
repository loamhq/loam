import test from 'node:test'
import assert from 'node:assert/strict'
import { authorMarkers } from '../public/thread-authors.js'

test('author backdrop follows the original author line, resets on scroll-back and survives redraw; observers retire with the page', () => {
  const original = globalThis.IntersectionObserver, observers = []
  globalThis.IntersectionObserver = class {
    constructor(callback, options) { Object.assign(this, { callback, options, observed: new Set() }); observers.push(this) }
    observe(node) { this.observed.add(node) }
    unobserve(node) { this.observed.delete(node) }
    disconnect() { this.observed.clear() }
  }
  const marker = () => {
    const classes = new Set()
    return { top: 150, classes, parentNode: { classList: { toggle: (name, on) => on ? classes.add(name) : classes.delete(name) } }, getBoundingClientRect() { return { top: this.top } } }
  }
  const first = marker(), next = marker(), markers = authorMarkers()
  let nodes = [first, next]
  const pane = { querySelectorAll: () => nodes, getBoundingClientRect: () => ({ top: 100 }) }
  const notify = (node) => observers.at(-1).callback([{ target: node, rootBounds: { top: 100 } }])
  try {
    markers.watch(pane)
    assert.equal(observers[0].options.root, pane)
    assert.equal(first.classes.size, 0, 'no backdrop while the author is in its original position')
    first.top = 90; notify(first); assert.ok(first.classes.has('author-stuck'))
    assert.equal(next.classes.size, 0, 'incoming author keeps its natural author line')
    next.top = 90; notify(next); assert.ok(next.classes.has('author-stuck'), 'incoming run takes over; native sticky constrains the outgoing line')
    first.classes.clear(); markers.watch(pane); assert.ok(first.classes.has('author-stuck'), 'reconciler redraw reapplies the state before paint')
    first.top = 101; notify(first); assert.equal(first.classes.size, 0, 'scrolling back removes the backdrop')
    nodes = [next]; markers.watch(pane); assert.equal(observers[0].observed.has(first), false)
    markers.watch(null); assert.equal(observers[0].observed.size, 0)
    markers.watch(pane); assert.equal(observers.length, 2)
    markers.disconnect(); assert.equal(observers[1].observed.size, 0)
  } finally { globalThis.IntersectionObserver = original }
})
