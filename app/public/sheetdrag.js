// A sheet's grip, made honest: what a finger dragging a bottom sheet down does.
//
// The owner, 2026-09-29: "when the modals are up, it has that really small bar at
// the top which suggests I could drag the modal down to close it, but that
// doesn't work. And worse, it actually drags it down and refreshes the page."
//
// No DOM here, and no clock of its own: touch positions and times in, what to
// draw and what to do out. `sheet-gesture.js` reads the touches and
// write the transform, which is the same split `viewport.js` and `keys.js` have.
//
// **The standard bottom-sheet rule.** A drag that starts on the grip or the
// header row takes the sheet whichever way it goes. A drag that starts in the
// sheet's content only takes the sheet when the content is already at its top
// and the finger's first real movement is downward — so a long Settings page
// scrolls as a page does, and reaching its top and pulling on is what moves the
// sheet. The decision is made once per touch, at the first movement past a
// small slop, and not revisited: a touch that began as a scroll stays a scroll.

// How far a finger moves before the touch is read as a direction at all.
export const DRAG_SLOP_PX = 6
// Released past this share of the sheet's height, it closes.
export const DRAG_CLOSE_SHARE = 1 / 3
// Or released moving down at least this fast (px per ms) — a flick — having
// moved at least `DRAG_FLICK_MIN_PX`, so a tap that jitters is never a flick.
export const DRAG_FLICK_PX_PER_MS = 0.5
export const DRAG_FLICK_MIN_PX = 24

// One touch's drag. `fromHandle` is whether it began on the grip or the header;
// `atTop` whether the sheet's content was scrolled to its top when it began.
export function startDrag({ y, t, fromHandle, atTop }) {
  return { y0: y, fromHandle, atTop, mode: 'undecided', offset: 0, last: { y, t }, prev: { y, t } }
}

// A movement. Answers the state, whose `mode` is `drag` when the sheet follows
// the finger (the caller then prevents the browser's own scroll) and `scroll`
// when the content has it. `offset` is how far down to draw the sheet, never
// above where it rests.
export function moveDrag(drag, { y, t }) {
  const dy = y - drag.y0
  let mode = drag.mode
  if (mode === 'undecided' && Math.abs(dy) >= DRAG_SLOP_PX) {
    mode = drag.fromHandle || (drag.atTop && dy > 0) ? 'drag' : 'scroll'
  }
  return {
    ...drag,
    mode,
    offset: mode === 'drag' ? Math.max(0, dy) : 0,
    prev: drag.last,
    last: { y, t },
  }
}

// The finger lifts: `close` or `back`. `height` is the sheet's own.
export function endDrag(drag, { height, t = drag.last.t }) {
  if (drag.mode !== 'drag') return 'back'
  // Include a pause before lift: a held pull is no longer a flick.
  const dt = Math.max(t, drag.last.t) - drag.prev.t
  const speed = dt > 0 ? (drag.last.y - drag.prev.y) / dt : 0
  if (height > 0 && drag.offset >= height * DRAG_CLOSE_SHARE) return 'close'
  if (speed >= DRAG_FLICK_PX_PER_MS && drag.offset >= DRAG_FLICK_MIN_PX) return 'close'
  return 'back'
}
