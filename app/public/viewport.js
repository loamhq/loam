// The app's box, from the numbers a phone's viewport reports.
//
// **A phone's keyboard does not make the page shorter.** On Android Chrome it
// slides over the bottom of it, and the layout viewport — which is what `100vh`
// and `100dvh` are measured against — does not move. So an app laid out to the
// full height of the window puts its composer at the bottom of a box the
// keyboard is now covering: the send button half off the screen and no room
// beneath it, which is what the owner saw on 2026-09-20.
//
// What does shrink is the **visual** viewport, and `window.visualViewport` is
// the only thing that reports it. This file is the arithmetic of reading those
// numbers and nothing else: no DOM, no listeners, no timers. `app.js` reads the
// numbers off `window.visualViewport`, asks `appFrame()` what the box should
// be, and writes it onto `#app` — so the rule can be asserted in `node --test`
// with no browser and no keyboard anywhere near it, which is the only way it
// could be tested at all.

// A visual viewport shorter than the window by less than this is the browser's
// own furniture moving — an address bar collapsing, a rounding of device pixels
// — and not a keyboard. It decides only whether the frame *says* a keyboard is
// open; the height follows the visual viewport either way, because the visual
// viewport is what the person can actually see in every case.
export const KEYBOARD_MIN = 120

// Above this scale the page is pinch-zoomed, which shrinks the visual viewport
// for a reason that is not a keyboard. Resizing the app under a magnifying
// glass moves what is under the glass, so a zoomed page keeps its full height
// and stays exactly where it is.
const ZOOMED = 1.02

// `innerHeight` is the window's; `height`, `offsetTop` and `scale` are
// `window.visualViewport`'s, or nothing at all on a browser that has none.
//
// Out: the height `#app` should be, how far down the page it should sit, and
// how much of the window a keyboard is currently taking — which is what the
// page uses to decide whether the transcript should be pinned to its end.
export function appFrame({ innerHeight = 0, height = null, offsetTop = 0, scale = 1 } = {}) {
  const inner = whole(innerHeight)
  const visual = whole(height)

  // No visual viewport to read, or a zoomed one: the window is the app, which
  // is the stylesheet's own `100dvh` and nothing for this to do.
  if (visual === 0 || !(scale <= ZOOMED)) return { height: inner, offset: 0, keyboard: 0 }

  const shrunk = Math.max(0, inner - visual)
  return {
    // The app is the visual viewport: exactly what the person can see.
    height: inner === 0 ? visual : Math.min(inner, visual),
    // The browser may have scrolled the visual viewport down over the layout
    // one to lift a focused field clear of the keyboard. The app goes with it,
    // or it is drawn where the person is no longer looking.
    offset: whole(offsetTop),
    keyboard: shrunk >= KEYBOARD_MIN ? shrunk : 0,
  }
}

// Whether two frames are the same box, so a viewport event that changed nothing
// writes nothing — the browser sends a great many of them while a keyboard
// animates in, and each one that reached the DOM would be a layout.
export function sameFrame(a, b) {
  return Boolean(a) && Boolean(b) && a.height === b.height && a.offset === b.offset && a.keyboard === b.keyboard
}

function whole(n) {
  return Number.isFinite(n) ? Math.max(0, Math.round(n)) : 0
}
