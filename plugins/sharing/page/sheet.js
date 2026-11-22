// A dialog's scrim that becomes a bottom sheet on a phone (2026-11-17): the
// share sheet and Shared with me's add sheet, like the notes' sheet on a
// phone. The look is CSS (`.sh-bs` in sharing.css, at 600px and less);
// this adds what CSS cannot: a drag down closes it, the sheet stands on the
// keyboard rather than under it, and the field being typed in stays in view.
// On a wider screen the dialog stays the centred card, and only the menus
// inside it are looked after (below).
//
// The scrim is the one lasting node: its dialog is drawn again on every
// change, so everything here listens on the scrim and finds the dialog anew.

const PHONE = '(max-width: 600px)'

export function bottomSheet(scrim, { close }) {
  scrim.classList.add('dc-bs')
  const mq = globalThis.matchMedia?.(PHONE)
  const phone = () => Boolean(mq?.matches)
  const sheet = () => scrim.querySelector(':scope > .sh-dlg, :scope > .dc-dlg')
  const vv = globalThis.visualViewport

  // The keyboard: the scrim is made the height of what is left of the screen
  // above it, so the sheet's foot sits on the keyboard and scrolls inside.
  function fit() {
    if (!phone() || !vv) { scrim.style.height = ''; scrim.style.top = ''; scrim.style.bottom = ''; return }
    scrim.style.top = `${vv.offsetTop}px`
    scrim.style.height = `${vv.height}px`
    scrim.style.bottom = 'auto'
  }
  vv?.addEventListener('resize', fit)
  vv?.addEventListener('scroll', fit)
  mq?.addEventListener?.('change', fit)
  fit()

  // The field being typed in, once the keyboard has risen.
  const onFocus = (e) => {
    if (!phone() || !e.target.matches?.('input, textarea, select')) return
    setTimeout(() => { if (document.activeElement === e.target) e.target.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, 320)
  }
  scrim.addEventListener('focusin', onFocus)

  // A drag down from the handle, or from anywhere once the sheet is scrolled
  // to its top, follows the finger; let go far or fast enough and it closes,
  // otherwise it settles back.
  let drag = null
  scrim.addEventListener('touchstart', (e) => {
    const s = sheet()
    if (!phone() || !s || e.touches.length !== 1 || !s.contains(e.target)) return
    if (e.target.closest('input, textarea, select, .sh-menu, .dc-menu')) return
    const fromGrab = Boolean(e.target.closest('.dc-bs-grab, .sh-head, .dc-sh-head'))
    if (!fromGrab && s.scrollTop > 0) return
    drag = { s, y: e.touches[0].clientY, t: e.timeStamp, dy: 0, moved: false }
  }, { passive: true })
  scrim.addEventListener('touchmove', (e) => {
    if (!drag) return
    const dy = e.touches[0].clientY - drag.y
    if (!drag.moved) {
      if (dy < 6) { if (dy < -6) drag = null; return }
      drag.moved = true
      drag.s.style.transition = 'none'
    }
    e.preventDefault()
    drag.dy = Math.max(0, dy)
    drag.s.style.transform = `translateY(${drag.dy}px)`
  }, { passive: false })
  const end = (e) => {
    if (!drag) return
    const { s, dy, t, moved } = drag
    drag = null
    if (!moved) return
    const fast = dy / Math.max(1, e.timeStamp - t) > 0.5
    s.style.transition = ''
    s.style.transform = ''
    if (dy > Math.min(120, s.offsetHeight * 0.3) || (fast && dy > 24)) close()
  }
  scrim.addEventListener('touchend', end)
  scrim.addEventListener('touchcancel', end)

  // A menu inside the sheet that would hang past its foot opens upward; on
  // a short sheet it may rise past the sheet's top, over the page, and the
  // sheet lets it show while it is open (the next drawing closes it). On a
  // wider screen the centred card, when it does not scroll, lets the menu
  // hang past its foot instead, where the screen has room (2026-11-17: the
  // card cut a link's ⋯ menu off at its foot).
  const flip = globalThis.MutationObserver && new MutationObserver(() => {
    const s = sheet()
    if (!s) return
    const menus = [...s.querySelectorAll('.sh-menu:not(.dc-up), .dc-menu:not(.dc-up)')]
    if (!menus.length) return
    const box = s.getBoundingClientRect()
    // Whether the sheet scrolls of itself: a menu hanging past its foot
    // makes it scroll, so it is set aside while that is measured.
    for (const m of menus) m.style.display = 'none'
    const still = s.scrollHeight <= s.clientHeight + 1
    for (const m of menus) m.style.display = ''
    for (const m of menus) {
      const r = m.getBoundingClientRect()
      if (r.bottom <= box.bottom - 8) continue
      if (!phone() && still && r.bottom <= innerHeight - 8) { s.classList.add('dc-bs-spill'); continue }
      const ddw = m.parentElement?.getBoundingClientRect()
      if (!ddw || ddw.top - r.height - 6 < 8) continue
      m.classList.add('dc-up')
      if (ddw.top - r.height - 6 < box.top && still) s.classList.add('dc-bs-spill')
    }
  })
  flip?.observe(scrim, { childList: true, subtree: true })

  return {
    // Draw a new dialog in the scrim, keeping where the sheet was scrolled.
    keep(fn) {
      const top = sheet()?.scrollTop || 0
      fn()
      const s = sheet()
      if (s && top) s.scrollTop = top
    },
    dispose() {
      vv?.removeEventListener('resize', fit)
      vv?.removeEventListener('scroll', fit)
      mq?.removeEventListener?.('change', fit)
      flip?.disconnect()
    },
  }
}

// The handle a phone's sheet shows at its top; hidden on a wider screen.
export function grab(h) {
  return h('div', { class: 'dc-bs-grab', 'aria-hidden': 'true' })
}
