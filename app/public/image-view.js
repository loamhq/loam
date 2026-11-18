// An image seen full size (2026-11-18): a tap on an image in a message, the
// owner's or an agent's, opens it over the page; a tap outside it, Escape or
// the close button puts the page back as it was. More than one image in the
// message: the arrows, the arrow keys or a swipe go between them.
//
// Built straight onto document.body and removed whole, never through the
// page's reconciler, so a redraw underneath it cannot take it away or move
// focus out of it.
import { h, icon } from './ui.js'

let current = null

export function closeImageView() {
  if (!current) return
  const { root, before, keys } = current
  current = null
  document.removeEventListener('keydown', keys, true)
  document.documentElement.classList.remove('image-view-open')
  root.parentNode?.removeChild(root)
  if (before?.isConnected) before.focus?.({ preventScroll: true })
}

// `items`: [{ src, name, href }]; `index`: the one tapped.
export function openImageView(items, index = 0) {
  closeImageView()
  if (!items?.length) return
  let at = Math.min(Math.max(index, 0), items.length - 1)
  const many = items.length > 1
  const img = h('img', { class: 'image-view-img', alt: '', decoding: 'async' })
  const name = h('span', { class: 'image-view-name' })
  const count = many ? h('span', { class: 'image-view-count' }) : null
  const save = h('a', { class: 'image-view-save image-view-stop', download: '', title: 'Download' }, icon.download(16), h('span', { text: 'Download' }))
  const show = (i) => {
    at = (i + items.length) % items.length
    const item = items[at]
    img.src = item.src
    img.alt = item.name ?? ''
    name.textContent = item.name ?? ''
    if (count) count.textContent = `${at + 1} of ${items.length}`
    save.href = item.href ?? item.src
  }
  const step = (d) => (e) => { e?.stopPropagation(); show(at + d) }
  const close = h('button', { class: 'iconbtn image-view-close image-view-stop', type: 'button', 'aria-label': 'Close', title: 'Close', onclick: (e) => { e.stopPropagation(); closeImageView() } }, icon.close(16))
  const root = h('div', { class: 'image-view', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Image', onclick: (e) => { if (e.target === root || e.target.classList.contains('image-view-stage')) closeImageView() } },
    h('div', { class: 'image-view-top' }, h('div', { class: 'image-view-title' }, name, count), save, close),
    h('div', { class: 'image-view-stage' }, img),
    many ? h('button', { class: 'iconbtn image-view-step prev image-view-stop', type: 'button', 'aria-label': 'Previous image', onclick: step(-1) }, icon.chevronLeft(18)) : null,
    many ? h('button', { class: 'iconbtn image-view-step next image-view-stop', type: 'button', 'aria-label': 'Next image', onclick: step(1) }, icon.chevronRight(14)) : null)
  // A swipe across the image goes to the next or the previous one.
  let startX = null
  root.addEventListener('pointerdown', (e) => { startX = e.clientX })
  root.addEventListener('pointerup', (e) => {
    if (startX === null || !many) return
    const dx = e.clientX - startX
    startX = null
    if (Math.abs(dx) > 48) show(at + (dx < 0 ? 1 : -1))
  })
  const keys = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeImageView() }
    else if (many && e.key === 'ArrowRight') { e.preventDefault(); show(at + 1) }
    else if (many && e.key === 'ArrowLeft') { e.preventDefault(); show(at - 1) }
    else if (e.key === 'Tab') {
      const stops = [...root.querySelectorAll('.image-view-stop')]
      const i = stops.indexOf(document.activeElement)
      e.preventDefault()
      stops[(i + (e.shiftKey ? -1 : 1) + stops.length) % stops.length]?.focus()
    }
  }
  current = { root, before: document.activeElement, keys }
  show(at)
  document.addEventListener('keydown', keys, true)
  document.documentElement.classList.add('image-view-open')
  document.body.append(root)
  close.focus({ preventScroll: true })
}
