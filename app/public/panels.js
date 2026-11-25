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
export function createPanels({ main, side, width = () => layoutPixels(globalThis.innerWidth ?? 0), changed, address = () => {}, revealMain = () => {}, initialSide, beforeOpen = () => {}, storage = deviceStorage() }) {
  const saved = readPanels(storage)
  const initial = initialSide === undefined ? saved.side : initialSide
  const st = { side: initial, split: saved.split, sidebar: saved.sidebar, loaded: false, version: 0, focused: initial ? 'side' : 'main' }
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
  function notify() { persist(); changed() }
  function sideChanged() { address(st.side); notify() }
  function guard(endpoint, retry) { return endpoint.guard?.(retry) !== false }
  function transfer(from, to, item) { if (item?.kind === 'thread') from.transfer?.(to, item.id) }
  function close() {
    if (!guard(side, close)) return false
    cancelClose()
    side.clear(); st.side = null; st.loaded = false; st.sidebar = false; st.focused = 'main'; st.version++; sideChanged(); return true
  }
  function closeAnimated() {
    if (!guard(side, closeAnimated) || st.closing) return false
    if (globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return close()
    st.closing = true; notify(); closing = setTimeout(close, 220); return true
  }
  function openMain(item) {
    if (!itemKey(item)) return false
    const source = beforeOpen()
    if (source) item = { ...item, ...source }
    if (!item.message && !item.fragment && !item.mode && itemKey(item) === itemKey(main.item()) && (!item.view || item.view === main.item().view)) {
      revealMain(item); st.focused = 'main'; notify(); return true
    }
    if (!guard(main, () => openMain(item))) return false
    if (item.kind === 'file' && itemKey(item) === itemKey(main.item()) && !item.fromOverlay) {
      revealMain(item)
      item = { ...item, push: false }
    }
    main.open(item); st.focused = 'main'; st.version++; notify(); return true
  }
  function openSide(item) {
    if (!available() || !itemKey(item) || (main.pending?.() && itemKey(item) === itemKey(main.item()))) return false
    beforeOpen()
    if (!guard(side, () => openSide(item))) return false
    cancelClose()
    if (itemKey(item) === itemKey(side.item()) && !item.message && !item.fragment && !item.mode) {
      side.setView?.(item.view); st.side = side.item(); st.focused = 'side'; sideChanged(); return true
    }
    if (!st.side) st.sidebar = false
    st.side = item; st.loaded = true; side.open(item); st.focused = 'side'; st.version++; sideChanged(); return true
  }
  const sameItem = () => Boolean(st.side) && itemKey(st.side) === itemKey(main.item())
  function swap() {
    const current = main.item(), previous = st.side
    if (!previous || sameItem() || main.pending?.() || side.pending?.() || !guard(main, swap) || !guard(side, swap)) return false
    cancelClose()
    transfer(main, side, current); transfer(side, main, previous)
    main.clear(); side.clear(); main.open(previous)
    st.side = current; st.loaded = Boolean(current)
    if (current) side.open(current)
    else st.sidebar = false
    st.swap = st.swap === 1 ? 2 : 1
    st.version++; sideChanged(); return true
  }
  function promote() {
    if (!st.side || sameItem() || !guard(main, promote) || !guard(side, promote)) return false
    const previous = side.item()
    transfer(side, main, previous)
    close()
    return openMain(previous)
  }
  function restore() {
    if (!st.side || st.loaded || !available()) return
    st.loaded = true; side.open(st.side)
  }
  function sync() {
    if (st.loaded && side.empty?.()) { close(); return }
    const current = side.item()
    if (st.loaded && current) st.side = current
    persist()
  }
  function tucked() { return visible() && !st.closing && width() < SIDEBAR_MIN && !st.sidebar }
  function showSidebar() { st.sidebar = true; notify() }
  function split(value) { st.split = Math.max(32, Math.min(68, value)); notify() }
  const action = (label, glyph, run, disabled = false) => h('button', { class: 'iconbtn ghost', type: 'button', title: label, 'aria-label': label, onclick: run, disabled }, glyph)
  function controls({ closeMenu } = {}) {
    const control = (label, glyph, run, disabled) => closeMenu
      ? h('button', { class: 'setrow', type: 'button', disabled, onclick: () => { closeMenu(); run() } }, glyph, label)
      : action(label, glyph, run, disabled)
    return [control('Open as the main panel', icon.arrowLeft(18), promote, sameItem()),
      control('Swap the two panels', icon.swap(18), swap, sameItem() || main.pending?.() || side.pending?.()),
      control('Close side panel', icon.close(16), closeAnimated)]
  }
  function panelActions() {
    const same = sameItem()
    return {
      promote: { label: 'Open as the main panel', disabled: same, run: promote },
      swap: { label: 'Swap the two panels', disabled: same || Boolean(main.pending?.() || side.pending?.()), run: swap },
      close: { label: 'Close side panel', disabled: false, run: closeAnimated },
    }
  }
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
  return { st, available, visible, focused, focus, tucked, showSidebar, openMain, openSide, swap, promote, close, restore, sync, split, controls, panelActions, affordance, divider, persist }
}

// Selectors used by an instance must never borrow another instance's stream,
// composer or menu. Lists and global overlays belong to the main instance.
export function panelDOM(id) {
  const all = selector => [...(document.querySelectorAll?.(selector) ?? [])].filter(node => (node.closest?.('[data-panel]')?.getAttribute('data-panel') ?? 'main') === id)
  return { querySelector: selector => document.querySelectorAll ? all(selector)[0] ?? null : document.querySelector?.(selector), querySelectorAll: all }
}
