import { h, on, icon } from './ui.js'
import { uiScale } from './scale.js'
import { sheetHandle } from './chat-ui.js'
import { sheetGesture } from './sheet-gesture.js'

// One shell for menus and sheets. Content stays in its reconciled tree; the
// browser's top layer lets it escape scroll containers without a DOM portal.
export function menuSurface(node, { title, close, heading = true, anchor = null, width = 360, initialFocus = null, fallbackPlacement = null, centered = false, anchored = false }) {
  node.setAttribute('data-menu-surface', '')
  node.setAttribute('data-menu-title', title)
  node.setAttribute('data-menu-width', String(width))
  // This is present in both sides of morph(), so stream redraws never remove
  // and re-add the layout attribute (which would restart the opening motion).
  node.setAttribute('data-menu-layout', !anchored && (globalThis.innerWidth ?? 0) < 700 ? 'sheet' : 'anchored')
  if (anchored) node.setAttribute('data-menu-anchored', '')
  if (centered) {
    node.setAttribute('data-menu-centered', '')
    node.setAttribute('aria-modal', 'true')
  }
  if (fallbackPlacement === 'center') node.setAttribute('data-menu-fallback', 'center')
  if (initialFocus) node.setAttribute('data-menu-initial-focus', initialFocus)
  if (anchor) node.setAttribute('data-menu-anchor', anchor)
  node.setAttribute('popover', 'manual')
  node.setAttribute('tabindex', '-1')
  node.setAttribute('aria-label', title)
  if (!node.hasAttribute('role')) node.setAttribute('role', 'dialog')
  on(node, 'menudismiss', close)
  if (heading) node.insertBefore(h('div', { class: 'surface-heading' },
    h('h2', { class: 'display', text: title }),
    h('button', { class: 'iconbtn ghost surface-close', type: 'button', 'aria-label': `Close ${title}`, onclick: close }, icon.close(14))), node.firstChild)
  if (!anchored && !node.querySelector('.sheet-top')) {
    const handle = sheetHandle(close)
    handle.className += ' surface-handle'
    node.insertBefore(handle, node.firstChild)
  }
  return node
}

// All inputs/outputs are screen CSS pixels. Conversion to layout pixels happens
// once at the DOM boundary, including CSS zoom and visual viewport movement.
export function menuPosition(anchor, size, viewport, gap = 8, inset = 12) {
  const leftEdge = viewport.left ?? 0, topEdge = viewport.top ?? 0
  const rightEdge = leftEdge + viewport.width, bottomEdge = topEdge + viewport.height
  const below = bottomEdge - anchor.bottom - gap - inset
  const above = anchor.top - topEdge - gap - inset
  const down = size.height <= below || below >= above
  const height = Math.max(0, Math.min(size.height, down ? below : above))
  const end = anchor.left + size.width > rightEdge - inset || anchor.left + anchor.width / 2 > leftEdge + viewport.width / 2
  const left = Math.max(leftEdge + inset, Math.min(end ? anchor.right - size.width : anchor.left, rightEdge - size.width - inset))
  const top = Math.max(topEdge + inset, down ? anchor.bottom + gap : anchor.top - gap - height)
  return { left, top, maxHeight: Math.max(0, down ? below : above), origin: `${Math.max(0, Math.min(size.width, (end ? anchor.right : anchor.left) - left))}px ${down ? '0px' : `${height}px`}` }
}

export function createMenuSurfaces(root) {
  // DOM-only rendering (without a layout engine) still builds the same shell.
  if (!root.getBoundingClientRect) return { sync() {} }
  const opened = new Map()
  let lastTrigger = null, frame = null
  const controls = (node) => [...node.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')]
    .filter((item) => item.getClientRects().length && !item.closest('[inert]') && item.getAttribute('aria-hidden') !== 'true')
  const initialTarget = (node, rows) => node.dataset.menuInitialFocus === 'heading'
    ? node.querySelector('.sheet-title, .surface-heading h2') ?? node
    : node.dataset.menuInitialFocus === 'selected' ? rows.find(n => n.getAttribute('aria-selected') === 'true') ?? rows[0] ?? node
    : rows.find(n => !n.classList.contains('surface-close') && !n.closest('.sheet-top, .sheettop')) ?? rows[0] ?? node
  const revealOption = (node) => {
    const active = document.activeElement
    if (!node.hasAttribute('data-menu-anchored') || active?.getAttribute('role') !== 'option' || !node.contains(active)) return
    const box = node.getBoundingClientRect(), row = active.getBoundingClientRect(), scale = uiScale()
    const padding = getComputedStyle(node)
    const top = box.top + parseFloat(padding.paddingTop) * scale, bottom = box.bottom - parseFloat(padding.paddingBottom) * scale
    node.scrollTop += (row.bottom > bottom ? row.bottom - bottom : row.top < top ? row.top - top : 0) / scale
  }
  const dismiss = (node) => {
    const entry = opened.get(node)
    if (entry) entry.returnFocus = true
    node.dispatchEvent(new Event('menudismiss'))
  }
  // Native confirmations can sit above a surface (End / Cancel in Activity).
  const top = () => root.querySelector('dialog[open]') ? null : [...opened.keys()].filter((n) => n.isConnected).at(-1)
  const identify = (button) => {
    if (!button) return null
    const attr = ['id', 'aria-label', 'title'].find((key) => button.getAttribute(key))
    if (!attr) return null
    const message = button.closest('[data-message-id]')?.getAttribute('data-message-id')
    return `${message ? `[data-message-id=${JSON.stringify(message)}] ` : ''}${button.localName}[${attr}=${JSON.stringify(button.getAttribute(attr))}]`
  }
  const resolve = (button, selector) => button?.isConnected ? button : selector ?
    [...root.querySelectorAll(selector)].find((n) => n.getClientRects().length && !n.closest('[inert]')) : null
  const viewport = () => ({ left: window.visualViewport?.offsetLeft ?? 0, top: window.visualViewport?.offsetTop ?? 0,
    width: window.visualViewport?.width ?? innerWidth, height: window.visualViewport?.height ?? innerHeight })
  function place(node, entry, settled = false) {
    const view = viewport(), scale = uiScale(), narrow = view.width < 700 && !node.hasAttribute('data-menu-anchored')
    const scrollTop = node.scrollTop
    node.dataset.menuLayout = narrow ? 'sheet' : 'anchored'
    node.style.position = 'fixed'
    node.style.margin = '0'
    node.style.right = 'auto'
    node.style.bottom = 'auto'
    node.style.minWidth = '0'
    const width = Math.min(narrow ? view.width : Number(node.dataset.menuWidth) * scale, view.width - (narrow ? 0 : 24))
    node.style.width = `${width / scale}px`
    node.style.maxWidth = `${width / scale}px`
    // Dynamic viewport bounds include browser bars / keyboard. Reserve the
    // device's top safe area as well as the bottom padding supplied by CSS.
    const style = getComputedStyle(node)
    const safeTop = parseFloat(style.getPropertyValue('--menu-safe-top')) || 0
    const safeBottom = parseFloat(style.getPropertyValue('--menu-safe-bottom')) || 0
    node.style.maxHeight = `${Math.max(0, view.height - 24 - safeTop) / scale}px`
    if (node.hasAttribute('data-menu-centered')) {
      node.style.left = `${(view.left + (view.width - width) / 2) / scale}px`
      node.style.top = `${(view.top + Math.max(12, (view.height - node.offsetHeight * scale) / 2)) / scale}px`
      node.style.transformOrigin = '50% 50%'
      return
    }
    if (narrow) {
      node.style.left = `${view.left / scale}px`
      node.style.top = 'auto'
      node.style.bottom = `${Math.max(0, innerHeight - view.top - view.height) / scale}px`
      node.style.transformOrigin = '50% 100%'
      return
    }
    // A redraw can replace a trigger (opening Running now from Settings, for
    // example). Keep the root trigger for drill-ins, never a stale rectangle.
    let trigger = resolve(entry.trigger, node.dataset.menuAnchor ?? entry.selector)
    if (!trigger?.isConnected) trigger = resolve(entry.fallback, entry.fallbackSelector)
    const rect = trigger?.getBoundingClientRect()
    if (!rect || rect.bottom <= view.top || rect.top >= view.top + view.height || rect.right <= view.left || rect.left >= view.left + view.width) {
      if (node.dataset.menuFallback === 'center') {
        clearTimeout(entry.outsideTimer); entry.outsideTimer = null
        node.style.left = `${(view.left + (view.width - width) / 2) / scale}px`
        node.style.top = `${(view.top + Math.max(12, (view.height - node.offsetHeight * scale) / 2)) / scale}px`
        node.style.transformOrigin = '50% 50%'
        return
      }
      // A media query can lay out the old tree for a frame before render()
      // rebuilds it. Let that settle before treating a trigger as gone.
      if (settled) dismiss(node)
      else entry.outsideTimer ??= setTimeout(() => { entry.outsideTimer = null; if (node.isConnected) place(node, entry, true) }, 240)
      return
    }
    clearTimeout(entry.outsideTimer); entry.outsideTimer = null
    const pos = menuPosition(rect, { width: node.offsetWidth * scale, height: node.offsetHeight * scale },
      { ...view, top: view.top + safeTop, height: Math.max(0, view.height - safeTop - safeBottom) })
    node.style.left = `${pos.left / scale}px`
    node.style.top = `${pos.top / scale}px`
    node.style.maxHeight = `${pos.maxHeight / scale}px`
    node.style.transformOrigin = pos.origin.split(' ').map((n) => `${parseFloat(n) / scale}px`).join(' ')
    // Measuring with the full viewport cap temporarily enlarges the scroller,
    // which can clamp its scrollTop. Preserve the reader's actual position.
    node.scrollTop = scrollTop
    if (entry.maxHeight !== pos.maxHeight) revealOption(node)
    entry.maxHeight = pos.maxHeight
  }
  function update() {
    frame = null
    for (const [node, entry] of opened) if (node.isConnected) place(node, entry)
  }
  function schedule() { if (frame === null) frame = requestAnimationFrame(update) }
  document.addEventListener('click', (e) => {
    const candidate = e.target.closest?.('button, a, [role="button"]')
    if (candidate) lastTrigger = candidate
  }, true)
  document.addEventListener('pointerdown', (e) => {
    const node = top(), entry = opened.get(node)
    if (!node || node.contains(e.target) || entry.trigger?.contains(e.target)) return
    if (node.hasAttribute('data-menu-centered') && e.target.closest?.('.rail-foot button')) return
    dismiss(node)
  }, true)
  document.addEventListener('focusin', () => {
    const node = top()
    if (node?.hasAttribute('data-menu-centered') && !node.contains(document.activeElement)) initialTarget(node, controls(node)).focus({ preventScroll: true })
  })
  document.addEventListener('keydown', (e) => {
    if (['Enter', ' ', 'ArrowDown'].includes(e.key)) {
      const candidate = e.target.closest?.('button, a, [role="button"]')
      if (candidate) lastTrigger = candidate
    }
    const node = top()
    if (!node) return
    if (e.key === 'Escape') {
      if (e.target.closest?.('.run-confirm, .profile-editor')) return
      e.preventDefault(); e.stopImmediatePropagation(); dismiss(node); return
    }
    const rows = controls(node), at = rows.indexOf(document.activeElement)
    const listbox = node.getAttribute('role') === 'listbox' && node.hasAttribute('data-menu-anchored')
    // A chip inside a parent sheet owns the key that opens its child listbox.
    if (!listbox && ['ArrowDown', 'ArrowUp'].includes(e.key) && e.target.closest?.('[aria-haspopup="listbox"]')) return
    if (listbox && e.key === 'Tab') {
      e.preventDefault(); e.stopImmediatePropagation(); dismiss(node); return
    }
    const editing = document.activeElement?.matches('input, textarea, select, [contenteditable="true"]')
    // A focusable document inside a dialog uses these keys to scroll its text.
    const reading = document.activeElement?.matches('[role="document"]')
    if (!editing && !reading && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
      e.preventDefault(); e.stopPropagation()
      rows[e.key === 'Home' ? 0 : e.key === 'End' ? rows.length - 1 : (at + (e.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length]?.focus({ preventScroll: true })
      revealOption(node)
    }
    if (listbox && !editing && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && e.key !== ' ') {
      const entry = opened.get(node), now = Date.now()
      entry.search = now - (entry.searchAt ?? 0) < 600 ? (entry.search ?? '') + e.key : e.key
      entry.searchAt = now
      const ordered = [...rows.slice(at + 1), ...rows.slice(0, at + 1)]
      const match = ordered.find(row => (row.querySelector('.setrow-title')?.textContent ?? row.textContent).trim().toLocaleLowerCase().startsWith(entry.search.toLocaleLowerCase()))
      if (match) { e.preventDefault(); e.stopPropagation(); match.focus({ preventScroll: true }); revealOption(node) }
    }
    if (e.key === 'Tab' && rows.length && (at < 0 || (e.shiftKey ? at === 0 : at === rows.length - 1))) {
      e.preventDefault(); rows[e.shiftKey ? rows.length - 1 : 0].focus({ preventScroll: true })
    }
  }, true)
  window.addEventListener('resize', schedule)
  document.addEventListener('scroll', schedule, true)
  document.addEventListener('animationend', schedule, true)
  document.addEventListener('transitionend', schedule, true)
  window.visualViewport?.addEventListener('resize', schedule)
  window.visualViewport?.addEventListener('scroll', schedule)
  const resize = typeof ResizeObserver === 'function' ? new ResizeObserver(schedule) : null
  return { sync() {
    const nodes = [...root.querySelectorAll('[data-menu-surface]')]
    const previous = [...opened.values()].at(-1)
    for (const [node, entry] of opened) if (!nodes.includes(node)) {
      clearTimeout(entry.outsideTimer)
      entry.stopGesture?.()
      resize?.unobserve(node); if (entry.trigger) resize?.unobserve(entry.trigger)
      opened.delete(node)
      const target = resolve(entry.trigger, entry.selector) ?? resolve(entry.fallback, entry.fallbackSelector)
      const needsFocus = entry.returnFocus || document.activeElement === document.body || !document.activeElement?.isConnected
      if (needsFocus && target?.isConnected && (!nodes.length || nodes.some((n) => n.contains(target)))) target.focus({ preventScroll: true })
    }
    for (const node of nodes) {
      let entry = opened.get(node)
      if (!entry) {
        const active = document.activeElement
        const trigger = previous && node.hasAttribute('data-menu-centered') ? previous.trigger
          : node.hasAttribute('data-menu-centered') && active?.matches('button, a[href], input, textarea, select, [contenteditable]') ? active
          : node.dataset.menuAnchor ? root.querySelector(node.dataset.menuAnchor) : resolve(lastTrigger, previous?.selector)
        const fallback = previous?.fallback?.isConnected ? previous.fallback : previous?.trigger ?? trigger
        entry = { trigger, selector: identify(trigger) ?? previous?.selector, fallback,
          fallbackSelector: identify(fallback) ?? previous?.fallbackSelector }
        opened.set(node, entry)
        entry.stopGesture = sheetGesture(node, () => dismiss(node))
        // Manual top-layer popovers work with the existing explicit state and
        // outside dismissal; no UA auto-dismiss can leave app state stale.
        node.showPopover?.()
        resize?.observe(node); if (trigger?.isConnected) resize?.observe(trigger)
        place(node, entry)
        const rows = controls(node)
        ;initialTarget(node, rows).focus({ preventScroll: true })
        revealOption(node)
      } else {
        if (node.showPopover && !node.matches(':popover-open')) node.showPopover()
        place(node, entry)
        if (node === top() && !node.contains(document.activeElement)) {
          const rows = controls(node)
          ;initialTarget(node, rows).focus({ preventScroll: true })
        }
      }
    }
  } }
}
