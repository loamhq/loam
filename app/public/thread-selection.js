import { h, kept, dress } from './ui.js'
import { layoutPixels } from './scale.js'

// One pen stroke: retract toward the title's start, change rows only at zero
// width, then draw out. The final 20ms of the nominal 160ms retract hands off
// to the 260ms draw, so the two phases share a beat without two visible rules.
const RETRACT = 160, OVERLAP = 20, DRAW = 260
const translate = (x, y, scale = 1) => `translate(${x}px, ${y}px) scaleX(${scale})`
export function threadSelection({ dom = document } = {}) {
  const rule = h('div', { class: 'tt-selection', 'aria-hidden': 'true' })
  let list = null, observer = null, columnNode = null, target = null, stroke = null, motion = null
  function stop() { stroke?.cancel(); stroke = null }
  function position() {
    const row = list?.querySelector('.tt-row.on')
    const text = row?.querySelector('.srow-who')
    if (text !== columnNode) {
      if (columnNode) observer?.unobserve(columnNode)
      columnNode = text
      if (text) observer?.observe(text)
    }
    if (!text) { stop(); target = null; dress(rule, { opacity: '0' }); return }
    const parent = list.getBoundingClientRect(), box = row.getBoundingClientRect(), column = text.getBoundingClientRect()
    if (!box.width) return // A collapsed sidebar is measured when it returns.
    // The text column already leaves the row's gap on both sides, including
    // before the time/default-face column. Underline only that text column.
    const next = { row, x: layoutPixels(column.left - parent.left), y: layoutPixels(column.bottom - parent.top) + 5.5,
      width: layoutPixels(column.width) }
    const reduced = motion?.matches
    if (target?.row === row && target.x === next.x && target.y === next.y && target.width === next.width && !reduced) return
    const current = target && rule.getBoundingClientRect()
    const changing = target?.row !== row
    stop()
    dress(rule, {
      transform: translate(next.x, next.y), width: `${next.width}px`, opacity: '1',
    })
    target = next
    if (!reduced && changing && rule.animate) {
      const drawn = current ? layoutPixels(current.width) : 0
      const retract = drawn > .01 ? RETRACT - OVERLAP : 0
      const duration = retract + DRAW, handoff = retract / duration
      const origin = current ? translate(layoutPixels(current.left - parent.left), layoutPixels(current.top - parent.top), drawn / next.width) : translate(next.x, next.y, 0)
      const collapsed = current ? translate(layoutPixels(current.left - parent.left), layoutPixels(current.top - parent.top), 0) : translate(next.x, next.y, 0)
      const frames = retract ? [
        { transform: origin, offset: 0, easing: 'ease-in' },
        { transform: collapsed, offset: handoff },
        { transform: translate(next.x, next.y, 0), offset: handoff, easing: 'ease-out' },
        { transform: translate(next.x, next.y), offset: 1 },
      ] : [{ transform: translate(next.x, next.y, 0), easing: 'ease-out' }, { transform: translate(next.x, next.y) }]
      stroke = rule.animate(frames, { duration })
    }
  }
  function disconnect() {
    stop(); observer?.disconnect(); motion?.removeEventListener?.('change', position)
    observer = null; list = null; columnNode = null; target = null; motion = null
    dress(rule, { opacity: '0' })
  }
  function paint() {
    const next = dom.querySelector('.tt-rows')
    if (list !== next) {
      disconnect(); list = next
      if (list) {
        motion = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)')
        motion?.addEventListener?.('change', position)
      }
      if (list && globalThis.ResizeObserver) {
        observer = new ResizeObserver(position)
        observer.observe(list)
      }
    }
    position()
  }
  return { view: () => kept(rule), paint, disconnect }
}
