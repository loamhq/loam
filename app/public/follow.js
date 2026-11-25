// Remember the position at which the reader last followed the transcript. A keyboard resize,
// late image or expanding fold can move the end without the reader scrolling. Observe both
// the pane and its children so these layout changes can keep a pinned reader at the end.

// Allow for fractional scroll positions when testing whether the pane is at its end.
export const AT_BOTTOM_PX = 2

export function follower({ Observer = globalThis.ResizeObserver, pinTarget = null } = {}) {
  let pane = null
  // The `scrollTop` the page last left this pane at, at its end. `null` is "the
  // page has not put it there", and only a reader at the end then follows.
  let pinnedTop = null
  let watched = new WeakSet()
  // The next draw puts the pane at its end whatever it finds: a message sent.
  let forced = false
  const observer = Observer ? new Observer(() => grew()) : null

  const maxScroll = (p) => Math.max(0, p.scrollHeight - p.clientHeight)
  const atEnd = (p) => p.scrollHeight - p.scrollTop - p.clientHeight <= AT_BOTTOM_PX

  function targetTop(p) {
    const max = maxScroll(p)
    if (!pinTarget) return max
    const custom = pinTarget(p, max)
    return custom == null ? max : Math.max(0, Math.min(custom, max))
  }

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
    return p === pane && pinnedTop !== null && Math.abs(p.scrollTop - pinnedTop) <= AT_BOTTOM_PX
  }

  function pin(p) {
    adopt(p)
    forced = false
    p.scrollTop = targetTop(p)
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
      else if (pinnedTop !== null && Math.abs(p.scrollTop - pinnedTop) > AT_BOTTOM_PX) pinnedTop = null
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
