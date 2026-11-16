import { installation } from './installation.js'
import { createCoreRegistry } from './core-registrations.js'
import { formatCalendarDate } from './time.js'
import { h, icon } from './ui.js'

export function appURL(value) {
  if (typeof value === 'string' && /^\/x\/[a-z0-9][a-z0-9_-]*\/(?:[?#].*)?$/i.test(value) && !/[\\\s]/.test(value)) return value
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null
  } catch { return null }
}

export function appAddress(value) {
  if (appURL(value)?.startsWith('/x/')) return value
  const url = new URL(value)
  return `${url.host}${url.pathname === '/' ? '' : url.pathname}${url.search}${url.hash}`
}

export function canFrame(app) {
  const href = appURL(app.url)
  if (!href) return false
  if (app.kind === 'plugin' && href.startsWith('/x/')) return true
  if (href.startsWith('/') || app.frame !== true) return false
  const url = new URL(href)
  return url.protocol === 'https:' && url.hostname.endsWith(`.${installation().appsDomain}`) && !url.port
}

export function appUnavailable(app) {
  if (app.kind !== 'plugin') return null
  if (!app.on) return 'Off'
  return app.state === 'running' ? null : ({ starting: 'Starting', failing: 'Failing', stopping: 'Stopping', stopped: 'Stopped' }[app.state] ?? 'Unavailable')
}

export function readApps(body) {
  if (!Array.isArray(body?.apps) || !Array.isArray(body?.sources)) throw new Error('Apps could not be read from this host.')
  return body.apps.filter(app => app && typeof app.name === 'string' && appURL(app.url))
}

export function createApps({ registry = createCoreRegistry(), api, redraw, wide, avatar, agentName, newThread,
  openBrowser = url => globalThis.open(url, '_blank', 'noopener,noreferrer'), copy, side = () => null, navigate = null, isCurrent = () => true }) {
  const st = { apps: [], loaded: false, loading: false, error: '', selected: null, copied: false, copyError: '' }
  let request = 0, returnTo = null
  async function load() {
    const ticket = ++request
    st.loading = true; st.error = ''; redraw()
    try {
      const apps = readApps(await api('GET', '/apps'))
      if (ticket !== request) return
      st.apps = apps; st.loaded = true
      if (st.selected) st.selected = apps.find(app => app.url === st.selected.url) ?? null
    } catch (error) {
      if (ticket === request) st.error = [404, 405].includes(error.status) ? 'Apps are not available from this host yet.' : error.message
    } finally { if (ticket === request) { st.loading = false; redraw() } }
  }
  function open(app, node, { routed = false } = {}) {
    if (appUnavailable(app) && !wide()) return
    if (!wide() || !canFrame(app)) return openBrowser(appURL(app.url))
    if (navigate && !routed) return navigate({ kind: 'app', app })
    st.selected = app; st.copied = false; st.copyError = ''; returnTo = node; redraw()
  }
  function back() {
    st.selected = null; redraw()
    requestAnimationFrame(() => (returnTo?.isConnected ? returnTo : document.querySelector('.apps-row'))?.focus?.())
  }
  async function copyLink() {
    st.copyError = ''; st.copied = false
    try { const url = appURL(st.selected.url); st.copied = await copy(url.startsWith('/') ? new URL(url, globalThis.location.origin).href : url); if (!st.copied) st.copyError = 'The link could not be copied.' }
    catch { st.copyError = 'The link could not be copied.' }
    redraw()
  }
  function row(app) {
    const who = agentName(app.owner), unavailable = appUnavailable(app)
    const address = appAddress(app.url)
    const expiry = app.expires || app.shared_until
    const date = expiry && Number.isFinite(Date.parse(expiry)) ? formatCalendarDate(expiry, { month: 'short', day: 'numeric', year: 'numeric' }) : null
    const selected = wide() && isCurrent() && st.selected?.url === app.url
    return h('button', { class: `apps-row${selected ? ' on' : ''}`, key: app.url, 'aria-current': selected ? 'page' : null, 'aria-label': `Open ${app.name}${!wide() || !canFrame(app) ? ' in browser' : ''}`, onclick: event => event.altKey && side()?.available() && canFrame(app) ? side().openSide({ kind: 'app', app }) : open(app, event.currentTarget) },
      app.icon ? h('img', { class: 'app-plugin-icon', src: app.icon, alt: '', loading: 'lazy' }) : avatar(app.owner),
      h('span', { class: 'apps-words' }, h('strong', { class: 'apps-name', text: app.name }),
        h('span', { class: 'apps-secondary', title: [app.url, ...(app.also_at || [])].join('\n') },
          app.kind === 'plugin' ? null : [h('span', { class: 'apps-by', text: `By ${who}` }), ' · '], h('span', { class: 'apps-address', text: address }))),
      h('span', { class: `apps-meta${app.on && app.state === 'failing' ? ' apps-failing' : ''}` }, h('span', { text: unavailable || ({ private: 'Private', public: 'Public', shared: 'Shared' }[app.access] || app.access) }),
        date ? h('span', { text: `${app.expires ? 'Expires' : 'Shared until'} ${date}` }) : null),
      side()?.available() && canFrame(app) ? h('span', { class: 'row-side-action', role: 'button', tabindex: '0', title: 'Open to the side', 'aria-label': `Open ${app.name} to the side`, onclick: event => { event.stopPropagation(); side().openSide({ kind: 'app', app }) }, onkeydown: event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); event.stopPropagation(); side().openSide({ kind: 'app', app }) } } }, icon.panelRight(16)) : null,
      side()?.visible() && side().st.side?.kind === 'app' && (side().st.side.app?.url ?? side().st.side.url) === app.url ? h('span', { class: 'row-side-mark', title: 'Open in side panel', 'aria-label': 'Open in side panel' }, icon.panelRight(13)) : null,
      icon.chevronRight(14))
  }
  function view(options = {}) { return registry.component('core.apps', { args: [options], variant: options.list ? 'list' : 'pane', state: st, defaultView: () => builtinView(options) }) }
  function builtinView({ list = false, collapse = null } = {}) {
    const app = st.selected
    if (!list && app && wide()) return h('div', { class: 'apps-frame', key: `app:${app.url}`, 'aria-label': app.name },
      h('div', { class: 'apps-toolbar' },
        h('button', { class: 'iconbtn', 'aria-label': 'Back to apps', onclick: back }, icon.chevronLeft(18)),
        h('span', { class: 'apps-frame-address', text: appAddress(app.url), title: app.url }),
        !appUnavailable(app) ? h('a', { class: 'pillbtn', href: appURL(app.url), target: '_blank', rel: 'noopener noreferrer', text: 'Open in browser' }) : null,
        h('button', { class: 'pillbtn', text: 'Copy link', onclick: copyLink }),
        h('span', { class: 'apps-copy-status', role: 'status', text: st.copyError || (st.copied ? 'Copied' : '') })),
      appUnavailable(app) ? h('div', { class: 'apps-empty', role: 'status' }, h('p', { text: `${app.name} · ${appUnavailable(app)}` }), h('p', { class: 'note', text: 'Manage this plugin in Settings › Plugins.' }))
        : h('iframe', { class: 'apps-embed', key: app.url, src: appURL(app.url), title: app.name, referrerpolicy: 'strict-origin-when-cross-origin', sandbox: 'allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads' }))
    if (!list && wide()) return h('div', { class: 'pane-body', key: 'pane:apps:empty' },
      h('div', { class: 'pane-nothing' }, h('div', { class: 'display', text: 'Pick an app' }),
        h('div', { class: 'why', text: st.error || 'One on the left. It opens here.' })))
    return h(list ? 'div' : 'main', { class: `apps-page${list ? ' apps-list' : ''}`, key: 'apps', 'aria-label': 'Apps' },
      list ? h('div', { class: 'list-head' }, h('h1', { class: 'display', text: 'Apps' }), collapse) : h('h1', { class: 'display', text: 'Apps' }),
      h('div', { class: list ? 'scroll apps-list-scroll' : 'apps-catalog' },
      st.error ? h('div', { class: 'apps-notice', role: 'alert' }, h('p', { text: st.error }), h('button', { class: 'pillbtn content-mark', text: 'Try again', disabled: st.loading, onclick: load })) : null,
      !st.loaded && st.loading ? h('p', { class: 'muted', role: 'status', text: 'Loading apps…' }) : null,
      ...registry.draw('apps.source', { apps: st.apps, row, open, defaultView: (source, title) => {
        const apps = st.apps.filter(app => app.source === source)
        return apps.length ? h('section', { key: source, class: 'apps-group', 'aria-label': title },
          h('h2', { class: 'kicker', text: title }), h('div', { class: 'apps-group-list' }, ...apps.map(row))) : null
      } }),
      st.loaded && !st.apps.length && !st.error ? h('div', { class: 'apps-empty' },
        h('div', { class: 'icon-tile' }, icon.code(24)),
        h('p', { text: 'An agent can build an app for you.' }),
        h('button', { class: 'pillbtn primary', text: 'Build an app', onclick: newThread })) : null))
  }
  return { st, load, open, back, copyLink, view }
}
