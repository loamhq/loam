import { h, icon } from './ui.js'
import { itemDescriptor } from './panel-items.js'
import { layoutPixels } from './scale.js'

export const SIDE_MIN = 1180
export const SIDEBAR_MIN = 1700
export const DEFAULT_SPLIT = 56
const key = 'loam.panels'
const deviceStorage = () => { try { return globalThis.localStorage } catch { return null } }
export function itemKey(item) {
  if (!itemDescriptor(item)) return null
  if (item.kind === 'thread') return `thread:${item.id}`
  if (item.kind === 'file') return `file:${item.root}:${item.path}`
  if (item.kind === 'app' && (item.app?.url || item.url)) return `app:${item.app?.url ?? item.url}`
  return typeof item.kind === 'string' && item.id ? `${item.kind}:${item.id}` : null
}
export function readPanels(storage = deviceStorage()) {
  try {
    const value = JSON.parse(storage?.getItem(key) ?? '{}')
    return { side: itemDescriptor(value.side), legacy: value.version !== 1, legacyMain: itemDescriptor(value.main),
      split: Math.max(32, Math.min(68, Number(value.split) || DEFAULT_SPLIT)), sidebar: value.sidebar === true }
  } catch { return { side: null, split: DEFAULT_SPLIT, sidebar: false } }
}

// Attention is transient view state, independent of navigation destinations.
// Each mounted view retains its own field, transport, selections and position.
export function createPanels({ main, side, width = () => layoutPixels(globalThis.innerWidth ?? 0), changed, address = () => {}, beforeOpen = () => {}, storage = deviceStorage() }) {
  const saved = readPanels(storage)
  const st = { side: saved.side, split: saved.split, sidebar: saved.sidebar, loaded: false, version: 0, focused: saved.side ? 'side' : 'main' }
  let closing = null
  let dragging = false
  let storedValue = null
  function cancelClose() { clearTimeout(closing); closing = null; st.closing = false }
  const available = () => width() >= SIDE_MIN
  const visible = () => available() && Boolean(st.side)
  const focused = () => visible() ? st.focused : 'main'
  function focus(panel) {
    if (!visible() || !['main', 'side'].includes(panel) || st.focused === panel) return
    st.focused = panel; changed()
  }
  function persist() {
    const item = st.side
    // A file's capability/revision and an app's inventory are fresh host facts.
    const value = JSON.stringify({ version: 1, side: itemDescriptor(item), split: st.split, sidebar: st.sidebar })
    if (value === storedValue) return
    try { storage?.setItem(key, value); storedValue = value } catch {}
  }
  // Side changes replace the side query in the current browser entry. Its
  // local trail belongs to this mount and ends when the pane closes.
  let trail = [], cursor = -1, serial = 0
  function recordSide() {
    const at = itemDescriptor(st.side)
    if (!at) { trail = []; cursor = -1; return }
    if (itemKey(trail[cursor]) === itemKey(at) && trail[cursor]?.view === (st.side.view || null)) return
    trail.splice(++cursor, Infinity, { ...at, view: st.side.view || null, visit: ++serial })
  }
  function sideTarget(delta) {
    let next = cursor + delta
    // An item moved into main is still open. Skip it to preserve ownership.
    while (trail[next] && itemKey(trail[next]) === itemKey(main.item())) next += delta
    return trail[next] ? next : -1
  }
  function goSide(delta) {
    const next = sideTarget(delta)
    if (next < 0 || !visible()) return false
    if (!guard(side, () => goSide(delta))) return true
    cursor = next
    openSide(trail[next])
    return true
  }
  function notify() { recordSide(); persist(); address(st.side); changed() }
  function guard(endpoint, retry) { return endpoint.guard?.(retry) !== false }
  function transfer(from, to, item) { if (item?.kind === 'thread') from.transfer?.(to, item.id) }
  function close() {
    if (!guard(side, close)) return false
    cancelClose()
    side.clear(); st.side = null; st.loaded = false; st.sidebar = false; st.focused = 'main'; st.version++; notify(); return true
  }
  function closeAnimated() {
    if (!guard(side, closeAnimated) || st.closing) return false
    if (globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return close()
    st.closing = true; notify(); closing = setTimeout(close, 220); return true
  }
  function openMain(item) {
    if (!itemKey(item)) return false
    beforeOpen()
    if (!guard(main, () => openMain(item))) return false
    if (itemKey(item) === itemKey(st.side)) {
      if (!guard(side, () => openMain(item))) return false
      if (item.kind === 'file' && side.item()?.mode) item = { ...item, mode: side.item().mode }
      cancelClose(); transfer(side, main, item); side.clear(); st.side = null; st.loaded = false; st.sidebar = false
    }
    if (!item.message && !item.fragment && !item.mode && itemKey(item) === itemKey(main.item())) { st.focused = 'main'; notify(); return true }
    main.open(item); st.focused = 'main'; st.version++; notify(); return true
  }
  function openSide(item) {
    if (!available() || !itemKey(item)) return false
    beforeOpen()
    if (!guard(side, () => openSide(item))) return false
    cancelClose()
    const current = main.item()
    if (itemKey(item) === itemKey(current)) {
      if (main.pending?.()) return false
      if (!guard(main, () => openSide(item))) return false
      if (item.kind === 'file' && current.mode) item = { ...item, mode: current.mode }
      const previous = st.side
      transfer(main, side, current); transfer(side, main, previous)
      main.clear(); side.clear()
      if (previous) main.open(previous)
      else main.emptyAddress?.()
    }
    if (itemKey(item) === itemKey(side.item()) && !item.message && !item.fragment && !item.mode) {
      side.setView?.(item.view); st.side = side.item(); st.focused = 'side'; notify(); return true
    }
    if (!st.side) st.sidebar = false
    st.side = item; st.loaded = true; side.open(item); st.focused = 'side'; st.version++; notify(); return true
  }
  function swap() {
    const current = main.item(), previous = st.side
    if (!previous || main.pending?.() || side.pending?.() || !guard(main, swap) || !guard(side, swap)) return false
    cancelClose()
    transfer(main, side, current); transfer(side, main, previous)
    main.clear(); side.clear(); main.open(previous)
    st.side = current; st.loaded = Boolean(current)
    if (current) side.open(current)
    else st.sidebar = false
    st.swap = st.swap === 1 ? 2 : 1
    st.version++; notify(); return true
  }
  function promote() { if (st.side) return openMain(st.side) }
  function restore() {
    if (!st.side || st.loaded || !available()) return
    if (itemKey(st.side) === itemKey(main.item())) { st.side = null; notify(); return }
    recordSide(); st.loaded = true; side.open(st.side)
  }
  function sync() {
    if (st.loaded && side.empty?.()) { close(); return }
    const current = side.item()
    if (st.loaded && current) st.side = current
    recordSide()
    persist(); address(st.side)
  }
  function tucked() { return visible() && !st.closing && width() < SIDEBAR_MIN && !st.sidebar }
  function showSidebar() { st.sidebar = true; notify() }
  function split(value) { st.split = Math.max(32, Math.min(68, value)); notify() }
  const action = (label, glyph, run, disabled = false) => h('button', { class: 'iconbtn ghost', type: 'button', title: label, 'aria-label': label, onclick: run, disabled }, glyph)
  function controls() { return [action('Open as the main panel', icon.arrowLeft(18), promote), action('Swap the two panels', icon.swap(18), swap, main.pending?.() || side.pending?.()), action('Close side panel', icon.close(16), closeAnimated)] }
  function affordance(item) {
    if (!available()) return null
    return action('Open to the side', icon.panelRight(16), event => { event.stopPropagation(); openSide(item) })
  }
  function divider() {
    return h('div', { class: `panel-divider${dragging ? ' dragging' : ''}`, key: 'panel-divider', role: 'separator', tabindex: '0', 'aria-label': 'Resize panels', 'aria-orientation': 'vertical', 'aria-valuemin': '32', 'aria-valuemax': '68', 'aria-valuenow': String(Math.round(st.split)),
      onpointerdown: event => { if (event.button !== 0) return; event.preventDefault(); dragging = true; event.currentTarget.setPointerCapture?.(event.pointerId); event.currentTarget.classList.add('dragging') },
      onpointermove: event => { if (!dragging) return; const box = event.currentTarget.parentElement.getBoundingClientRect(); split((event.clientX - box.left) / box.width * 100) },
      onpointerup: event => { dragging = false; event.currentTarget.releasePointerCapture?.(event.pointerId); event.currentTarget.classList.remove('dragging') },
      onpointercancel: () => { dragging = false; changed() }, ondblclick: () => split(DEFAULT_SPLIT),
      onkeydown: event => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'Enter'].includes(event.key)) return; event.preventDefault(); split(event.key === 'Home' || event.key === 'Enter' ? DEFAULT_SPLIT : st.split + (event.key === 'ArrowLeft' ? -2 : 2)) },
    }, h('span', { 'aria-hidden': 'true' }))
  }
  return { st, available, visible, focused, focus, goSide, sideVisit: () => trail[cursor]?.visit, canSideBack: () => sideTarget(-1) >= 0, tucked, showSidebar, openMain, openSide, swap, promote, close, restore, sync, split, controls, affordance, divider, persist }
}

// Selectors used by an instance must never borrow another instance's stream,
// composer or menu. Lists and global overlays belong to the main instance.
export function panelDOM(id) {
  const all = selector => [...(document.querySelectorAll?.(selector) ?? [])].filter(node => (node.closest?.('[data-panel]')?.getAttribute('data-panel') ?? 'main') === id)
  return { querySelector: selector => document.querySelectorAll ? all(selector)[0] ?? null : document.querySelector?.(selector), querySelectorAll: all }
}
