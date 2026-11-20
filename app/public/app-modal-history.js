// The path remains the document behind the modal. Only these query
// keys belong to the overlay; opening adds one history entry, drill-ins replace
// it, and Back/Forward never reopen or unmount the underlying document.
// The Market (2026-11-18) is an overlay of its own: `listing`, `kind`, `q`,
// and `install` (a GitHub link: the install-from-link sheet opens over it,
// read and shown, never installed without the person's yes).
const keys = ['modal', 'section', 'provider', 'account', 'profile', 'profile_id', 'listing', 'q', 'install']
const marketKind = url => url.pathname === '/panel' ? 'marketKind' : 'kind'
function baseURL(url) {
  const base = new URL(url)
  if (base.searchParams.get('modal') === 'market') base.searchParams.delete(marketKind(base))
  for (const key of keys) base.searchParams.delete(key)
  return base
}
export function modalFromURL(url) {
  const location = new URL(url), q = location.searchParams, kind = q.get('modal')
  if (kind === 'activity') return { kind }
  if (kind === 'market') {
    const listing = /^[a-z][a-z0-9-]*$/.test(q.get('listing') ?? '') ? q.get('listing') : null
    const k = ['theme', 'extension', 'app', 'agent', 'skill'].includes(q.get(marketKind(location))) ? q.get(marketKind(location)) : 'all'
    const query = (q.get('q') ?? '').slice(0, 200)
    const install = /^(?:https:\/\/github\.com\/|[\w-][\w.-]*\/)[^\s]{1,400}$/.test(q.get('install') ?? '') ? q.get('install') : null
    return { kind, view: listing ? 'listing' : query ? 'results' : k !== 'all' ? 'kind' : 'front', id: listing, k, q: query, ...(install ? { install } : {}) }
  }
  if (kind !== 'settings') return null
  return { kind, page: q.get('section') || 'root', provider: q.get('provider'), account: q.get('account'),
    ...(['agent', 'engineer', 'reviewer'].includes(q.get('profile')) && q.get('profile_id') ? { detail: { kind: q.get('profile'), id: q.get('profile_id') } } : {}) }
}
export function modalURL(url, sheet) {
  const next = baseURL(url)
  if (!['settings', 'activity', 'market'].includes(sheet?.kind)) return next
  next.searchParams.set('modal', sheet.kind)
  if (sheet.kind === 'market') {
    if (sheet.view === 'listing' && sheet.id) next.searchParams.set('listing', sheet.id)
    else if (sheet.view === 'results' && sheet.q) next.searchParams.set('q', sheet.q)
    if (sheet.view !== 'listing' && sheet.k && sheet.k !== 'all') next.searchParams.set(marketKind(next), sheet.k)
  }
  if (sheet.kind === 'settings') {
    if (sheet.page && sheet.page !== 'root') next.searchParams.set('section', sheet.page)
    if (sheet.provider) next.searchParams.set('provider', sheet.provider)
    if (sheet.account) next.searchParams.set('account', sheet.account)
    if (sheet.detail) { next.searchParams.set('profile', sheet.detail.kind); next.searchParams.set('profile_id', sheet.detail.id) }
  }
  return next
}
export function createAppModalHistory({ wide, push, sheet, restore, close, changed, window: win = globalThis.window }) {
  if (!win?.location?.href || !win.history?.replaceState) return { sync() {}, dismiss() {}, restore() {} }
  let base = baseURL(win.location.href).href, pending = false, ready = false
  let hadModal = Boolean(modalFromURL(win.location.href))
  const supported = value => value && (wide() || value.kind !== 'activity')
  function sync() {
    if (!ready || pending) return
    const current = new URL(win.location.href), next = modalURL(current, supported(sheet()) ? sheet() : null)
    const opening = !modalFromURL(current) && modalFromURL(next)
    // Reordering query keys on a direct modal URL must not claim that this
    // entry has an underlying document immediately before it in history.
    if (next.href !== current.href) {
      const data = { ...win.history.state, appModal: Boolean(modalFromURL(next)) && (Boolean(opening) || win.history.state?.appModal === true) }
      if (opening && push) push(data, '', next)
      else win.history[opening ? 'pushState' : 'replaceState'](data, '', next)
    }
    base = baseURL(next).href
    hadModal = Boolean(modalFromURL(next))
  }
  function apply() {
    const value = modalFromURL(win.location.href)
    if (value) restore(value)
    else close()
  }
  win.addEventListener('popstate', event => {
    const nextModal = Boolean(supported(modalFromURL(win.location.href)))
    const modalNavigation = pending || hadModal || nextModal || ['settings', 'activity', 'market'].includes(sheet()?.kind)
    pending = false
    const sameDocument = baseURL(win.location.href).href === base
    hadModal = nextModal
    // Document navigation can outrun the next render's base update. A matching
    // path alone is not evidence that this Back belongs to a modal.
    if (sameDocument && modalNavigation) { event.stopImmediatePropagation(); apply(); changed() }
    else {
      if (modalNavigation) close()
      if (nextModal) queueMicrotask(apply)
      base = baseURL(win.location.href).href
    }
  }, true)
  return {
    sync,
    dismiss() {
      if (pending) return
      if (supported(modalFromURL(win.location.href)) && win.history.state?.appModal) { pending = true; win.history.back() }
    },
    restore() { ready = true; if (supported(modalFromURL(win.location.href))) apply() },
  }
}
