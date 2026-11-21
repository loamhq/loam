import { settingsSection } from './app-shape.js'

// The path remains the document behind the modal. Only these query keys
// belong to the overlay. The address can restore a popup on reload.
// The Market (2026-11-18) is an overlay of its own: `listing`, `kind`, `q`,
// and `install` (a GitHub link: the install-from-link sheet opens over it,
// read and shown, never installed without the person's yes).
const keys = ['modal', 'section', 'provider', 'account', 'profile', 'profile_id', 'reviewer_from_plugin', 'archived', 'listing', 'q', 'install']
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
    ...(q.get('archived') === '1' ? { archivedAgents: true } : {}),
    ...(q.get('profile') === 'reviewer' && q.get('reviewer_from_plugin') === '1' ? { fromReviewerPlugin: true } : {}),
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
    if (sheet.archivedAgents) next.searchParams.set('archived', '1')
    if (sheet.detail) { next.searchParams.set('profile', sheet.detail.kind); next.searchParams.set('profile_id', sheet.detail.id) }
    if (sheet.fromReviewerPlugin) next.searchParams.set('reviewer_from_plugin', '1')
  }
  return next
}
export function settingsParent(sheet) {
  const page = settingsSection(sheet.page)
  if (page === 'root') return null
  if (sheet.detail?.kind === 'reviewer' && sheet.fromReviewerPlugin) return { kind: 'settings', page: 'plugins', provider: 'reviewer' }
  if (sheet.detail || sheet.archivedAgents || (page === 'plugins' && sheet.provider)) return { kind: 'settings', page }
  if (sheet.page === 'integration' && sheet.account) return { kind: 'settings', page: 'integration', provider: sheet.provider }
  return { kind: 'settings', page: page === sheet.page ? 'root' : page }
}
// Popups replace the current address. Leaving the panel first clears its
// popup query, so Back to that entry can only restore the underlying panel.
export function createAppModalHistory({ navigation, sheet, restore, window: win = globalThis.window }) {
  let ready = false
  function write(value) {
    if (!win?.location?.href || !win.history?.replaceState || !navigation.atCurrent()) return
    const current = new URL(win.location.href), next = modalURL(current, value)
    if (next.href !== current.href) win.history.replaceState(win.history.state, '', next)
  }
  return {
    sync() { if (ready) write(sheet()) },
    leave() { write(null) },
    restore(value = win?.location?.href && modalFromURL(win.location.href)) {
      if (value) restore(value)
      ready = true
    },
  }
}
