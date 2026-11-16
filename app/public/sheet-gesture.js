import { startDrag, moveDrag, endDrag } from './sheetdrag.js'
import { layoutPixels } from './scale.js'

// Bound once by the shared surface, independent of the screen that owns it.
// Native non-passive listeners can claim a downward pull before browser scroll.
export function sheetGesture(node, dismiss) {
  let drag = null, timer = null, leaving = false, swallowClick = false
  const reduced = () => globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const duration = () => getComputedStyle(node).getPropertyValue('--motion-normal').trim() || '220ms'
  const height = () => node.offsetHeight
  const draw = (offset, settle = false) => {
    const progress = Math.min(1, offset / (height() || 1))
    node.style.animation = 'none'
    node.style.transition = settle ? `${reduced() ? 'opacity' : 'transform'} ${duration()} cubic-bezier(.2,.8,.2,1)` : 'none'
    node.style.transform = reduced() ? '' : `translateY(${offset}px)`
    node.style.opacity = reduced() ? String(1 - progress) : ''
    node.style.setProperty('--sheet-scrim-opacity', String(1 - progress))
    node.style.setProperty('--sheet-scrim-transition', settle ? `opacity ${duration()} ease` : 'none')
  }
  const clearPaint = () => {
    node.style.transition = ''; node.style.transform = ''; node.style.opacity = ''
    node.style.removeProperty('--sheet-scrim-opacity'); node.style.removeProperty('--sheet-scrim-transition')
  }
  const finish = (cancelled = false, t) => {
    const held = drag; drag = null
    if (!held || held.mode !== 'drag') return
    swallowClick = true
    const close = !cancelled && endDrag(held, { height: height(), t }) === 'close'
    leaving = close
    draw(close ? height() : 0, true)
    // Follow the theme's motion token, including seconds-valued overrides.
    const token = duration(), ms = parseFloat(token) * (token.endsWith('ms') ? 1 : 1000)
    timer = setTimeout(() => {
      timer = null
      if (close) { if (node.isConnected) dismiss() }
      else clearPaint()
      leaving = false
    }, ms)
  }
  const start = (event) => {
    if (leaving || node.dataset.menuLayout !== 'sheet') return
    if (event.touches.length !== 1) { finish(true); return }
    if (event.target.closest?.('input[type=range], .file-speech-range')) return
    clearTimeout(timer); clearPaint(); swallowClick = false
    let atTop = true
    for (let at = event.target; at && node.contains(at); at = at.parentElement) {
      if (at.scrollTop > 0) atTop = false
    }
    drag = startDrag({ y: layoutPixels(event.touches[0].clientY), t: event.timeStamp,
      fromHandle: Boolean(event.target.closest?.('.sheet-top, .sheet-head, .sheet-who, .surface-heading')), atTop })
  }
  const move = (event) => {
    if (!drag || !event.touches[0]) return
    if (event.touches.length !== 1) { finish(true); return }
    drag = moveDrag(drag, { y: layoutPixels(event.touches[0].clientY), t: event.timeStamp })
    if (drag.mode !== 'drag') return
    event.preventDefault()
    draw(drag.offset)
  }
  const end = (event) => finish(false, event.timeStamp)
  const cancel = () => finish(true)
  const click = (event) => {
    if (!leaving && (!swallowClick || event.detail === 0)) return
    swallowClick = false; event.preventDefault(); event.stopImmediatePropagation()
  }
  const bindings = { touchstart: start, touchmove: move, touchend: end, touchcancel: cancel,
    pointerdown: () => { swallowClick = false }, click }
  for (const [type, fn] of Object.entries(bindings)) node.addEventListener(type, fn, { passive: false, capture: type === 'click' })
  return () => {
    clearTimeout(timer)
    for (const [type, fn] of Object.entries(bindings)) node.removeEventListener(type, fn, { capture: type === 'click' })
  }
}
