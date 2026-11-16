// The grip's rule, without a DOM: when a touch takes a sheet, and when a
// release closes it. `public/sheetdrag.js` is the whole of the decision; the
// page reads touches into it and writes the transform it answers.
import test from 'node:test'
import assert from 'node:assert/strict'
import { startDrag, moveDrag, endDrag, DRAG_SLOP_PX } from '../public/sheetdrag.js'

const run = (start, moves) => moves.reduce((drag, [y, t]) => moveDrag(drag, { y, t }), startDrag(start))

test('a drag on the handle follows the finger down and never above where the sheet rests', () => {
  const start = { y: 100, t: 0, fromHandle: true, atTop: false }
  assert.equal(run(start, [[100 + DRAG_SLOP_PX - 1, 10]]).mode, 'undecided', 'a jitter under the slop took the sheet')
  const down = run(start, [[110, 10], [180, 40]])
  assert.equal(down.mode, 'drag')
  assert.equal(down.offset, 80)
  const up = run(start, [[110, 10], [40, 40]])
  assert.equal(up.mode, 'drag')
  assert.equal(up.offset, 0, 'the sheet was drawn above where it rests')
})

test('in the content, the sheet only takes a pull down from the top; a scroll stays a scroll', () => {
  // Scrolled into a long page: pulling down scrolls it back, never the sheet.
  assert.equal(run({ y: 300, t: 0, fromHandle: false, atTop: false }, [[320, 10], [500, 60]]).mode, 'scroll')
  // At the top, pulling down is the sheet's.
  const pull = run({ y: 300, t: 0, fromHandle: false, atTop: true }, [[320, 10], [420, 60]])
  assert.deepEqual([pull.mode, pull.offset], ['drag', 120])
  // At the top, pushing up scrolls the page — and stays a scroll for the whole
  // touch, even when the finger then comes back down past where it began.
  const push = run({ y: 300, t: 0, fromHandle: false, atTop: true }, [[280, 10], [420, 60]])
  assert.deepEqual([push.mode, push.offset], ['scroll', 0])
})

test('released past a third of the sheet it closes; short of it, it snaps back; a flick closes', () => {
  const height = 600
  const slow = (to) => run({ y: 0, t: 0, fromHandle: true, atTop: true }, [[10, 100], [to - 1, 900], [to, 1000]])
  assert.equal(endDrag(slow(199), { height }), 'back')
  assert.equal(endDrag(slow(200), { height }), 'close')
  // A flick: short, but fast at the release.
  const flick = run({ y: 0, t: 0, fromHandle: true, atTop: true }, [[10, 5], [40, 20], [80, 40]])
  assert.equal(endDrag(flick, { height }), 'close')
  assert.equal(endDrag(flick, { height, t: 500 }), 'back', 'a pull held still before lifting is not a flick')
  // Fast but tiny is a tap, not a flick.
  const tap = run({ y: 0, t: 0, fromHandle: true, atTop: true }, [[8, 2], [16, 4]])
  assert.equal(endDrag(tap, { height }), 'back')
  // A touch that was a scroll never closes anything.
  assert.equal(endDrag(run({ y: 0, t: 0, fromHandle: false, atTop: false }, [[400, 10]]), { height }), 'back')
})
