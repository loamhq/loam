// Native sticky owns motion and the push-off at the end of each author run.
// Observe only its original position to ease the small backdrop in and out;
// no scroll handler, duplicate author, fixed overlay or per-frame positioning.
export function authorMarkers() {
  let root = null, observer = null
  const inset = () => typeof getComputedStyle === 'function' ? parseFloat(getComputedStyle(root).paddingTop) || 0 : 0
  const anchors = new Set()
  const paint = (anchor, top) => anchor.parentNode.classList.toggle('author-stuck', anchor.getBoundingClientRect().top < top)
  function disconnect() { observer?.disconnect(); observer = null; root = null; anchors.clear() }
  function watch(pane) {
    if (pane !== root) {
      disconnect(); root = pane
      if (root && typeof IntersectionObserver !== 'undefined') observer = new IntersectionObserver((entries) => {
        for (const entry of entries) if (anchors.has(entry.target)) paint(entry.target, entry.rootBounds?.top ?? root.getBoundingClientRect().top)
      }, { root, rootMargin: `-${inset()}px 0px 0px 0px`, threshold: [0, 1] })
    }
    if (!root || !observer) return
    const current = new Set(root.querySelectorAll('.tt-author-anchor')), top = root.getBoundingClientRect().top + inset()
    for (const anchor of anchors) if (!current.has(anchor)) { observer.unobserve(anchor); anchors.delete(anchor) }
    for (const anchor of current) {
      if (!anchors.has(anchor)) { anchors.add(anchor); observer.observe(anchor) }
      paint(anchor, top)
    }
  }
  return { watch, disconnect }
}
