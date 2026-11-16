// The path remains the document behind the desktop modal. Only these query
// keys belong to the overlay; opening adds one history entry, drill-ins replace
// it, and Back/Forward never reopen or unmount the underlying document.
const keys = ['modal', 'section', 'provider', 'account', 'profile', 'profile_id']
function baseURL(url) {
  const base = new URL(url)
  for (const key of keys) base.searchParams.delete(key)
  return base
}
export function modalFromURL(url) {
  const q = new URL(url).searchParams, kind = q.get('modal')
  if (kind === 'activity') return { kind }
  if (kind !== 'settings') return null
  return { kind, page: q.get('section') || 'root', provider: q.get('provider'), account: q.get('account'),
    ...(['agent', 'engineer', 'reviewer'].includes(q.get('profile')) && q.get('profile_id') ? { detail: { kind: q.get('profile'), id: q.get('profile_id') } } : {}) }
}
export function modalURL(url, sheet) {
  const next = baseURL(url)
  if (!['settings', 'activity'].includes(sheet?.kind)) return next
  next.searchParams.set('modal', sheet.kind)
  if (sheet.kind === 'settings') {
    if (sheet.page && sheet.page !== 'root') next.searchParams.set('section', sheet.page)
    if (sheet.provider) next.searchParams.set('provider', sheet.provider)
    if (sheet.account) next.searchParams.set('account', sheet.account)
    if (sheet.detail) { next.searchParams.set('profile', sheet.detail.kind); next.searchParams.set('profile_id', sheet.detail.id) }
  }
  return next
}
export function createAppModalHistory({ wide, sheet, restore, close, changed, window: win = globalThis.window }) {
  if (!win?.location?.href || !win.history?.replaceState) return { sync() {}, dismiss() {}, restore() {} }
  let base = baseURL(win.location.href).href, pending = false, ready = false
  let hadModal = Boolean(modalFromURL(win.location.href))
  function sync() {
    if (!ready || pending) return
    const current = new URL(win.location.href), next = modalURL(current, wide() ? sheet() : null)
    const opening = !modalFromURL(current) && modalFromURL(next)
    // Reordering query keys on a direct modal URL must not claim that this
    // entry has an underlying document immediately before it in history.
    if (next.href !== current.href) win.history[opening ? 'pushState' : 'replaceState']({ ...win.history.state,
      appModal: Boolean(modalFromURL(next)) && (Boolean(opening) || win.history.state?.appModal === true) }, '', next)
    base = baseURL(next).href
    hadModal = Boolean(modalFromURL(next))
  }
  function apply() {
    const value = modalFromURL(win.location.href)
    if (value) restore(value)
    else close()
  }
  win.addEventListener('popstate', event => {
    if (!wide()) return
    const nextModal = Boolean(modalFromURL(win.location.href))
    const modalNavigation = pending || hadModal || nextModal || ['settings', 'activity'].includes(sheet()?.kind)
    pending = false
    const sameDocument = baseURL(win.location.href).href === base
    hadModal = nextModal
    // Document navigation can outrun the next render's base update. A matching
    // path alone is not evidence that this Back belongs to a modal.
    if (sameDocument && modalNavigation) { event.stopImmediatePropagation(); apply(); changed() }
    else { if (modalNavigation) close(); base = baseURL(win.location.href).href }
  }, true)
  return {
    sync,
    dismiss() {
      if (pending) return
      if (wide() && modalFromURL(win.location.href) && win.history.state?.appModal) { pending = true; win.history.back() }
    },
    restore() { ready = true; if (wide() && modalFromURL(win.location.href)) apply() },
  }
}
