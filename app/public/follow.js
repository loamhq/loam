// The transcript follows new output while the reader is at its end.
//
// The owner, 2026-09-22: "please autoscroll when I am fully at the bottom of the
// page and there is new content coming in. Don't autoscroll if I am not fully
// at the bottom." And 2026-10-07, on their phone: "when I send out a message… the
// scroll stays at the chat, so I would have to manually scroll to go and see
// the latest responses."
//
// **Being at the end is remembered, not re-measured.** Until 2026-10-07 the
// page asked "is the pane at the bottom?" at the start of every draw, and
// anything that moved the bottom between two draws answered "no" for good: a
// phone keyboard coming up shortens the pane under a scroll position that does
// not move (Android lays the page out again before any script hears of it), an
// image arriving late or a step's fold growing pushes the end down with no draw
// at all. The reader had not moved; the end had. So the page now keeps the
// scroll position it last left at the end, and a pane still at that position is
// following whatever happened to the content under it. A reader who scrolls
// moves `scrollTop` off it, and is then following only if they are at the end.
//
// Growth that is not a draw is heard through a `ResizeObserver` on the pane and
// on each of its children: the pane for a keyboard or a window, the children
// for an image, a fold, a card, the awaiting line.
//
// No DOM is reached for here beyond the pane handed in, so the rule runs the
// same under the tests' fake DOM, in a real browser in a test, and on a phone.

// What "fully" at the bottom means once a browser's sub-pixel scroll positions
// are allowed for; anything wider is a threshold deciding for the person where
// the bottom is (it was 80px until 2026-09-22, and a line up still followed).
export const AT_BOTTOM_PX = 2

export function follower({ Observer = globalThis.ResizeObserver } = {}) {
  let pane = null
  // The `scrollTop` the page last left this pane at, at its end. `null` is "the
  // page has not put it there", and only a reader at the end then follows.
  let pinnedTop = null
  let watched = new WeakSet()
  // The next draw puts the pane at its end whatever it finds: a message sent.
  let forced = false
  const observer = Observer ? new Observer(() => grew()) : null

  const atEnd = (p) => p.scrollHeight - p.scrollTop - p.clientHeight <= AT_BOTTOM_PX

  function adopt(p) {
    if (p === pane) return
    observer?.disconnect()
    pane = p
    pinnedTop = null
    watched = new WeakSet()
  }

  // Whether this pane is following, asked before anything about it changes.
  function following(p) {
    if (forced) return true
    if (atEnd(p)) return true
    return p === pane && pinnedTop !== null && Math.abs(p.scrollTop - pinnedTop) < 1
  }

  function pin(p) {
    adopt(p)
    forced = false
    p.scrollTop = p.scrollHeight
    pinnedTop = p.scrollTop
  }

  // Something on the pane changed size without a draw.
  function grew() {
    if (pane && pane.isConnected !== false && following(pane)) pin(pane)
  }

  return {
    following,
    pin,
    // After a draw: the pane and every child it has are listened to for size.
    watch(p) {
      if (!p) { adopt(null); forced = false; return }
      if (!observer) return
      adopt(p)
      if (!watched.has(p)) {
        watched.add(p)
        observer.observe(p)
      }
      for (const child of p.children ?? (p.childNodes ?? []).filter((n) => n.nodeType === 1)) {
        if (watched.has(child)) continue
        watched.add(child)
        observer.observe(child)
      }
    },
    // The reader scrolled. Reaching the end by hand is following from there on.
    scrolled(p) {
      adopt(p)
      if (atEnd(p)) pinnedTop = p.scrollTop
    },
    // The page put the pane somewhere on purpose that is not the end.
    release() {
      pinnedTop = null
    },
    // A message was sent: the next draw lands at the end.
    send() {
      forced = true
    },
    grew,
  }
}
