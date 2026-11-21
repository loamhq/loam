import { settingsSection } from './app-shape.js'

// The path remains the document behind the modal. Only these query keys
// belong to the overlay. Settings drill-ins have their own history entries.
// The Market (2026-11-18) is an overlay of its own: `listing`, `kind`, `q`,
// and `install` (a GitHub link: the install-from-link sheet opens over it,
// read and shown, never installed without the person's yes).
const keys = ['modal', 'section', 'provider', 'account', 'profile', 'profile_id', 'archived', 'listing', 'q', 'install']
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
  }
  return next
}
function settingsParent(sheet) {
  const page = settingsSection(sheet.page)
  if (page === 'root') return null
  if (sheet.detail || sheet.archivedAgents || (page === 'plugins' && sheet.provider)) return { kind: 'settings', page }
  return { kind: 'settings', page: page === sheet.page ? 'root' : page }
}
const settingsPage = sheet => sheet?.kind === 'settings' && !sheet.detail && !sheet.archivedAgents && !sheet.provider ? sheet.page : null

export function createAppModalHistory({ wide, navigation, sheet, restore, close, changed, window: win = globalThis.window }) {
  if (!win?.location?.href || !win.history?.replaceState) return { sync() {}, dismiss() {}, restore() {}, backToSettings() { return false } }
  const { history } = win
  let base = baseURL(win.location.href).href, pending = null, ready = false
  let hadModal = Boolean(modalFromURL(win.location.href))
  let settingsUp = false
  const supported = value => value && (wide() || value.kind !== 'activity')
  const index = () => history.state?.panelIndex ?? 0
  const modalDepth = () => history.state?.appModalDepth ?? Number(Boolean(history.state?.appModal))
  function write(method, url, depth, settingsRoot = null, settingsParent = null) {
    const { desktopCarry, ...entry } = history.state ?? {}
    navigation[method]({ ...entry, appModal: depth > 0, appModalDepth: depth, settingsRoot, settingsParent }, '', url)
  }
  function traverse(steps, action) {
    pending = action
    if (navigation.go(-steps, { dismissModal: true })) return true
    pending = null
    return false
  }
  function sync() {
    if (!ready || pending || !navigation.atCurrent()) return
    const current = new URL(win.location.href), next = modalURL(current, supported(sheet()) ? sheet() : null)
    const from = modalFromURL(current), to = modalFromURL(next), depth = modalDepth()
    if (next.href !== current.href) {
      if (from?.kind === 'settings' && to && to.kind !== 'settings' && depth > 1 && traverse(depth, 'replace')) return
      if (to?.kind === 'settings' && (from?.kind !== 'settings' || (!depth && !settingsUp))) {
        // A shortcut to a Settings detail gets the same way up as a visit
        // through the Settings menu.
        if (from && !depth) write('replace', baseURL(current), 0)
        const trail = [to]
        for (let parent = settingsParent(to); parent; parent = settingsParent(parent)) trail.unshift(parent)
        const root = index() + (depth ? 0 : 1)
        trail.forEach((entry, i) => write(depth && !i ? 'replace' : 'push', modalURL(current, entry),
          (depth || 1) + i, root, settingsPage(trail[i - 1])))
      } else if (to?.kind === 'settings') {
        const root = history.state?.settingsRoot ?? index()
        if (!settingsUp && from?.kind === 'settings' && to.page !== 'root') {
          write('push', next, depth + 1, root, settingsPage(from))
        } else {
          write('replace', next, depth, to.page === 'root' ? index() : root, history.state?.settingsParent)
        }
      } else if (!from && to) write('push', next, 1)
      else write('replace', next, to ? depth : 0)
    }
    settingsUp = false
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
    const action = pending
    const modalNavigation = pending || hadModal || nextModal || ['settings', 'activity', 'market'].includes(sheet()?.kind)
    pending = null
    const sameDocument = baseURL(win.location.href).href === base
    hadModal = nextModal
    // Document navigation can outrun the next render's base update. A matching
    // path alone is not evidence that this Back belongs to a modal.
    if (sameDocument && modalNavigation) {
      event.stopImmediatePropagation()
      if (action === 'replace') sync()
      else if (action === 'dismiss') close()
      else apply()
      changed()
    } else {
      if (modalNavigation) close()
      base = baseURL(win.location.href).href
    }
  }, true)
  return {
    sync,
    backToSettings(page) {
      if (modalFromURL(win.location.href)?.kind !== 'settings') return false
      if (history.state?.settingsParent === page && navigation.go(-1)) return true
      settingsUp = true
      return false
    },
    dismiss() {
      if (pending) return
      if (!supported(modalFromURL(win.location.href))) return
      const depth = modalDepth()
      if (depth) traverse(depth, 'dismiss')
    },
    restore() { ready = true; if (supported(modalFromURL(win.location.href))) apply() },
  }
}
