// The keyboard, as arithmetic.
//
// **This is the only way the keyboard can be tested at all**, and saying so is
// the point of the file: there is no browser on the box this project is built
// on and no phone anywhere near it, so "the composer stays above the keyboard"
// cannot be caused here. What *can* be caused is the decision underneath it —
// given the numbers a phone's visual viewport reports, how tall is the app and
// where does it sit — and that is `appFrame()`, which takes four numbers and
// touches nothing.
//
// **Fixture-only, and more so than the others**: it proves the app asks for the
// right box, not that a phone draws it. What the owner should see is in the
// README, under "The keyboard, and the box the app lives in".

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { appFrame, sameFrame, KEYBOARD_MIN } from '../public/viewport.js'

const HTML = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8')

// An ordinary phone with nothing in the way: 844 points of window, and the
// visual viewport is the whole of it.
const OPEN = { innerHeight: 844, height: 844, offsetTop: 0, scale: 1 }

test('with no keyboard the app is the window, and says no keyboard', () => {
  assert.deepEqual(appFrame(OPEN), { height: 844, offset: 0, keyboard: 0 })
})

test('a keyboard shortens the app to what can actually be seen', () => {
  // Android Chrome, keyboard up: the window is still 844 — the layout viewport
  // does not move — and the visual viewport is 508. An app laid out to 844 has
  // its composer 336px underneath the keyboard, which is the defect.
  const frame = appFrame({ ...OPEN, height: 508 })
  assert.equal(frame.height, 508, 'the app was left at the height of the covered window')
  assert.equal(frame.keyboard, 336)
  assert.equal(frame.offset, 0)
})

test('the app follows the visual viewport when the browser scrolls it', () => {
  // Some browsers lift the focused field clear by scrolling the visual viewport
  // down over the layout one instead of resizing anything. The app goes with
  // it, or it is drawn where the person is no longer looking.
  const frame = appFrame({ ...OPEN, height: 508, offsetTop: 120 })
  assert.equal(frame.offset, 120)
  assert.equal(frame.height, 508)
})

test('a pinch-zoom is not a keyboard: the app keeps its height and stays put', () => {
  // Zooming in shrinks the visual viewport exactly as a keyboard does, and
  // resizing the page under a magnifying glass moves what is under the glass.
  const frame = appFrame({ innerHeight: 844, height: 300, offsetTop: 200, scale: 2.4 })
  assert.deepEqual(frame, { height: 844, offset: 0, keyboard: 0 })
})

test('an address bar collapsing is not a keyboard, and the height still follows it', () => {
  const shrunk = appFrame({ ...OPEN, height: 844 - (KEYBOARD_MIN - 20) })
  assert.equal(shrunk.keyboard, 0, 'the browser’s own furniture was reported as a keyboard')
  // It is still what can be seen, so it is still the app's height: the
  // threshold decides what the page *calls* it, never what it measures.
  assert.equal(shrunk.height, 844 - (KEYBOARD_MIN - 20))
})

test('a browser with no visual viewport gets the window, and one with neither is left alone', () => {
  assert.deepEqual(appFrame({ innerHeight: 900 }), { height: 900, offset: 0, keyboard: 0 })
  // Nothing to read at all — the height of zero is the page's signal to leave
  // the stylesheet's own `100dvh` where it is rather than write a zero onto it.
  assert.deepEqual(appFrame({}), { height: 0, offset: 0, keyboard: 0 })
  assert.deepEqual(appFrame(), { height: 0, offset: 0, keyboard: 0 })
})

test('nonsense in is not nonsense out', () => {
  assert.deepEqual(appFrame({ innerHeight: NaN, height: NaN }), { height: 0, offset: 0, keyboard: 0 })
  assert.deepEqual(appFrame({ innerHeight: 844, height: 508, offsetTop: -30 }), { height: 508, offset: 0, keyboard: 336 })
  // Device pixels are fractional and a height is not.
  assert.equal(appFrame({ innerHeight: 844.6, height: 507.4 }).height, 507)
})

test('a visual viewport taller than the window is still only the window', () => {
  // It happens while a keyboard is dismissing, and an app taller than its
  // window is a scrollbar on a page that has never had one.
  assert.equal(appFrame({ innerHeight: 844, height: 900 }).height, 844)
})

test('the page asks Chrome to resize the layout viewport rather than cover it', () => {
  // The other half of the keyboard fix, and the half that is one line: with
  // `interactive-widget=resizes-content`, Chrome on Android shortens the layout
  // viewport when the keyboard comes up instead of sliding it over a page that
  // still believes it is full height. It is in the HTML and nowhere else, which
  // is why this reads the file.
  const meta = /<meta\s+name="viewport"\s+content="([^"]+)"/s.exec(HTML.replace(/\s+/g, ' '))
  assert.notEqual(meta, null, 'there is no viewport meta to carry the hint')
  assert.match(meta[1], /interactive-widget=resizes-content/)
  // And the rest of it is unchanged: the notch is still cleared and the page is
  // still device-width at 1.
  assert.match(meta[1], /width=device-width/)
  assert.match(meta[1], /viewport-fit=cover/)
})

test('a browser that honours resizes-content needs nothing corrected, and gets nothing', () => {
  // The same 336px keyboard on the same 844px window as above — but this time
  // the browser shortened the layout viewport too, so `innerHeight` is 508 as
  // well. There is no gap between the window and what can be seen, so the app
  // is 508 either way and the page writes the height it already had.
  const frame = appFrame({ innerHeight: 508, height: 508, offsetTop: 0, scale: 1 })
  assert.equal(frame.height, 508, 'the app was not the box above the keyboard')
  assert.equal(frame.offset, 0, 'the page was shifted under a keyboard that had made room for it')
  // Nothing was covered, so nothing is reported as covering it. The two
  // browsers reach the same 508px box by different routes, which is the point:
  // one model, and the meta is an optimisation of the common case.
  assert.equal(frame.keyboard, 0)
  // The two browsers reach the same 508px box by different routes, which is the
  // point: one model, and the meta is an optimisation of the common case.
  assert.equal(frame.height, appFrame({ ...OPEN, height: 508 }).height)
})

test('the same box twice is the same box, so a resize storm writes nothing', () => {
  // A keyboard animating in sends a great many resize events; each one that
  // reached the DOM would be a layout.
  const a = appFrame({ ...OPEN, height: 508 })
  const b = appFrame({ ...OPEN, height: 508 })
  assert.equal(sameFrame(a, b), true)
  assert.equal(sameFrame(a, appFrame({ ...OPEN, height: 509 })), false)
  assert.equal(sameFrame(null, a), false, 'the first frame of a page’s life is never a no-op')
})
