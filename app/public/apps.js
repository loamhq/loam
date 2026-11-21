import { firstUseActive } from './first-use.js'
import { installation } from './installation.js'
import { createCoreRegistry } from './core-registrations.js'
import { formatCalendarDate } from './time.js'
import { h, icon } from './ui.js'
import { emptyState } from './empty-state.js'
import { menuSurface } from './menu-surface.js'
import { AGENT_COLOURS } from './avatar-colours.js'

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

export function appLetter(app, catalog = []) {
  const parts = app.name.trim().split(/[\s._-]+/).filter(Boolean)
  const family = catalog.some(other => other.url !== app.url && other.name.trim().split(/[\s._-]+/)[0]?.toLocaleLowerCase() === parts[0]?.toLocaleLowerCase())
  const part = [...((family ? parts.at(-1) : parts[0]) || '?')]
  let position = 0
  if (family) for (const other of catalog) {
    const words = other.name.trim().split(/[\s._-]+/).filter(Boolean), suffix = [...(words.at(-1) || '')]
    if (other.url === app.url || words[0]?.toLocaleLowerCase() !== parts[0]?.toLocaleLowerCase() || suffix.join('').localeCompare(part.join('')) >= 0 || suffix[0]?.toLocaleLowerCase() !== part[0]?.toLocaleLowerCase()) continue
    let common = 0
    while (common < part.length && common < suffix.length && part[common].toLocaleLowerCase() === suffix[common].toLocaleLowerCase()) common++
    position = Math.max(position, common)
  }
  return (part[position] || part[0]).toLocaleUpperCase()
}

// Start the draft with the starter agent or the first awake agent. Send only when the person writes.
export function buildAppThread(threads, agents = []) {
  const awake = agents.filter((agent) => !agent.archived)
  const agent = awake.find((a) => a.id === 'iris') ?? awake[0] ?? null
  return threads.create(agent?.id ?? null, {
    promptTitle: agent ? `Build an app with ${agent.name}` : 'Build an app',
    promptBody: `Describe what you’d like in a sentence or two. ${agent ? agent.name : 'Your agent'} will ask what’s needed, then build it.`,
    placeholder: 'What would you like to build?',
  })
}

export function createApps({ registry = createCoreRegistry(), api, redraw, wide, avatar, agentName, newThread,
  openBrowser = url => globalThis.open(url, '_blank', 'noopener,noreferrer'), copy, side = () => null, navigate = null, isCurrent = () => true, leave = null, historyButton = () => null }) {
  const st = { details: null, media: new Map(), readyImages: new Set(), failedImages: new Set(), apps: [], loaded: false, loading: false, error: '', selected: null, copied: false, copyError: '' }
  let request = 0, returnTo = null, mediaRequest = 0, mediaTimer, pressTimer, press, suppressedURL, suppressedUntil = 0
  async function load({ list = false } = {}) {
    const ticket = ++request
    st.loading = true; st.error = ''; redraw()
    try {
      const apps = readApps(await api('GET', '/apps'))
      if (ticket !== request) return
      st.apps = apps; st.loaded = true
      if (st.details) st.details = apps.find(app => app.url === st.details.url) ?? null
      loadMedia()
      if (!list && st.selected) st.selected = apps.find(app => app.url === st.selected.url) ?? null
    } catch (error) {
      if (ticket === request) st.error = [404, 405].includes(error.status) ? 'Apps are not available from this host yet.' : error.message
    } finally { if (ticket === request) { st.loading = false; redraw() } }
  }
  function open(app, node, { routed = false } = {}) {
    if (appUnavailable(app) && !wide()) return
    if (!routed && !appUnavailable(app)) Promise.resolve(api('POST', '/apps/open', { url: app.url })).catch(() => {})
    const embeddedPlugin = app.kind === 'plugin' && canFrame(app) && navigate
    if ((!wide() && !routed && !embeddedPlugin) || !canFrame(app)) return openBrowser(appURL(app.url))
    if (navigate && !routed) return navigate({ kind: 'app', app })
    st.selected = app; st.copied = false; st.copyError = ''; returnTo = node; redraw()
  }
  function back() {
    if (leave) return leave()
    st.selected = null; redraw()
    requestAnimationFrame(() => (returnTo?.isConnected ? returnTo : document.querySelector('.apps-row'))?.focus?.())
  }
  async function copyLink(app = st.details ?? st.selected) {
    st.copyError = ''; st.copied = false
    try { const url = appURL(app.url); st.copied = await copy(url.startsWith('/') ? new URL(url, globalThis.location.origin).href : url); if (!st.copied) st.copyError = 'The link could not be copied.' }
    catch { st.copyError = 'The link could not be copied.' }
    redraw()
  }
  async function loadMedia(attempt = 0, ticket = ++mediaRequest) {
    clearTimeout(mediaTimer)
    try {
      const body = await api('GET', '/apps/media')
      if (ticket !== mediaRequest || !Array.isArray(body?.apps)) return
      const ownAsset = value => typeof value === 'string' && value.startsWith('/api/apps/media/image?') ? value : null
      for (const item of body.apps) if (st.apps.some(app => app.url === item.url)) st.media.set(item.url, { preview: ownAsset(item.preview), icon: ownAsset(item.icon), pending: item.pending === true })
      if (body.apps.some(item => item.pending) && attempt < 90) {
        mediaTimer = setTimeout(() => loadMedia(attempt + 1, ticket), attempt < 10 ? 1000 : 3000)
        mediaTimer.unref?.()
      } else {
        const delays = body.apps.map(item => item.refresh_after_ms).filter(value => Number.isFinite(value) && value >= 0)
        if (delays.length) {
          mediaTimer = setTimeout(() => loadMedia(0, ticket), Math.max(60000, Math.min(86400000, ...delays) + 1000))
          mediaTimer.unref?.()
        }
      }
      redraw()
    } catch { /* The designed tile stays; media never blocks opening an app. */ }
  }
  function appPicture(app) {
    if (app.plugin && app.on) {
      const entry = registry.entries('apps.icon', { app }).find(item => item.owner === app.plugin)
      const picture = entry && registry.invoke(entry, 'render', { app })
      if (picture) return h('span', { class: 'apps-icon apps-preview', 'aria-hidden': 'true' }, picture)
    }
    const media = st.media.get(app.url), src = media?.preview
    // Resolve palette collisions deterministically within this catalog. Names
    // in one family keep different accent colours even if every capture fails.
    const slots = new Map(), used = new Set()
    for (const item of [...st.apps].sort((a, b) => a.url.localeCompare(b.url))) {
      let hash = 2166136261
      for (const char of item.url) hash = Math.imul(hash ^ char.codePointAt(0), 16777619) >>> 0
      let slot = hash % AGENT_COLOURS.length
      while (used.size < AGENT_COLOURS.length && used.has(slot)) slot = (slot + 1) % AGENT_COLOURS.length
      used.add(slot); slots.set(item.url, slot)
    }
    const pigment = AGENT_COLOURS[slots.get(app.url) ?? 0]
    const image = (source, className) => source && !st.failedImages.has(source) ? h('img', {
      class: `${className}${st.readyImages.has(source) ? ' loaded' : ''}`, key: source, src: source, alt: '', loading: 'lazy',
      onload: () => { if (!st.readyImages.has(source)) { st.readyImages.add(source); redraw() } },
      onerror: () => { if (!st.failedImages.has(source)) { st.failedImages.add(source); redraw() } },
    }) : null
    return h('span', { class: 'apps-icon apps-preview', 'aria-hidden': 'true', style: { '--app-pigment': `var(--${pigment})` } },
      h('span', { class: 'apps-monogram', text: appLetter(app, st.apps) }),
      image(src, 'apps-preview-image'), image(media?.icon, `apps-site-icon${app.kind === 'plugin' ? ' app-plugin-icon' : ''}`))
  }
  function author(app) {
    return [app.owner ? h('span', { class: 'apps-owner', 'aria-hidden': 'true' }, avatar(app.owner, { size: 16, font: 8 })) : null,
      h('span', { class: 'apps-by', text: app.kind === 'plugin' ? 'Plugin' : agentName(app.owner) })]
  }
  function cancelPress() { clearTimeout(pressTimer); press = null }
  function startPress(app, event) {
    cancelPress()
    if (event.pointerType !== 'touch') return
    press = { x: event.clientX, y: event.clientY }
    pressTimer = setTimeout(() => { suppressedURL = app.url; suppressedUntil = Date.now() + 1200; showDetails(app); press = null }, 550)
  }
  function expiresSoon(app) {
    const expiry = app.expires || app.shared_until, time = Date.parse(expiry)
    if (!Number.isFinite(time)) return null
    const days = Math.ceil((time - Date.now()) / 86400000)
    if (days > 14) return null
    return { label: days < 0 ? 'Ended' : days === 0 ? 'Ends today' : `${days}d left`, date: formatCalendarDate(expiry, { month: 'short', day: 'numeric', year: 'numeric' }) }
  }
  const accessLabel = app => ({ private: 'Private', public: 'Public', shared: 'Shared' }[app.access] || app.access)
  function showDetails(app) { st.details = app; st.copied = false; st.copyError = ''; redraw() }
  function row(app) {
    const unavailable = appUnavailable(app), expiry = expiresSoon(app)
    const selected = wide() && isCurrent() && st.selected?.url === app.url
    const inSide = side()?.visible() && side().st.side?.kind === 'app' && (side().st.side.app?.url ?? side().st.side.url) === app.url
    return h('div', { class: 'apps-entry', key: app.url },
      h('button', { class: `apps-row${selected ? ' on' : ''}${inSide ? ' in-side' : ''}`, 'aria-current': selected ? 'page' : null,
        'aria-label': `Open ${app.name}${(!wide() && app.kind !== 'plugin') || !canFrame(app) ? ' in browser' : ''}`, title: app.name,
        oncontextmenu: event => { event.preventDefault(); cancelPress(); suppressedURL = app.url; suppressedUntil = Date.now() + 1200; showDetails(app) },
        onpointerdown: event => startPress(app, event), onpointerup: cancelPress, onpointercancel: cancelPress, onpointerleave: cancelPress,
        onpointermove: event => { if (press && Math.hypot(event.clientX - press.x, event.clientY - press.y) > 10) cancelPress() },
        onclick: event => { if (suppressedURL === app.url && event.detail !== 0 && Date.now() < suppressedUntil) { event.preventDefault(); return }; event.altKey && side()?.available() && canFrame(app) ? side().openSide({ kind: 'app', app }) : open(app, event.currentTarget) } },
        appPicture(app),
        h('span', { class: 'apps-words' }, h('strong', { class: 'apps-name', text: app.name }),
          unavailable ? h('span', { class: 'apps-state', text: unavailable }) : null,
          h('span', { class: `apps-secondary${app.on && app.state === 'failing' ? ' apps-failing' : ''}` },
            ...author(app),
            h('span', { class: 'apps-access', text: ` · ${unavailable || accessLabel(app) || ''}` }),
            !canFrame(app) ? h('span', { class: 'apps-browser-tag', title: 'Opens in browser', 'aria-label': 'Opens in browser' }, icon.external(12)) : null,
            expiry ? h('span', { class: 'apps-expiry', title: `Ends ${expiry.date}`, text: expiry.label }) : null)),
        inSide ? h('span', { class: 'row-side-mark', title: 'Open in side panel', 'aria-label': 'Open in side panel' }, icon.panelRight(13)) : null,
        !canFrame(app) ? h('span', { class: 'apps-external', title: 'Opens in browser' }, icon.external(14)) : null),
      h('div', { class: 'apps-row-actions' },
        wide() && side()?.available() && canFrame(app) ? h('button', { class: 'row-side-action iconbtn', title: 'Open to the side', 'aria-label': `Open ${app.name} to the side`, onclick: () => side().openSide({ kind: 'app', app }) }, icon.panelRight(16)) : null,
        h('button', { class: 'apps-details-button iconbtn', 'data-app-details': app.url, 'aria-label': `Details for ${app.name}`, 'aria-haspopup': 'dialog', 'aria-expanded': String(st.details?.url === app.url), onclick: () => showDetails(app) }, icon.dots(16))))
  }
  function detailsView() {
    const app = st.details
    if (!app) return null
    const expiry = app.expires || app.shared_until
    const date = expiry && Number.isFinite(Date.parse(expiry)) ? formatCalendarDate(expiry, { month: 'short', day: 'numeric', year: 'numeric' }) : null
    return menuSurface(h('div', { class: 'apps-details', key: `app-details:${app.url}` },
      h('div', { class: 'apps-details-identity' }, appPicture(app), h('div', {}, h('p', { class: 'apps-details-author' }, ...author(app)), h('p', { class: 'muted', text: [accessLabel(app), appUnavailable(app)].filter(Boolean).join(' · ') }))),
      h('p', { class: 'apps-details-address', text: appAddress(app.url) }),
      date ? h('p', { class: 'apps-details-date', text: `${app.expires ? 'Ends' : 'Shared until'} ${date}` }) : null,
      h('div', { class: 'apps-details-actions' },
        !appUnavailable(app) ? h('a', { class: 'pillbtn', href: appURL(app.url), target: '_blank', rel: 'noopener noreferrer' }, icon.external(16), 'Open in browser') : null,
        h('button', { class: 'pillbtn', text: 'Copy link', onclick: () => copyLink(app) })),
      h('p', { class: 'apps-copy-status', role: 'status', text: st.copyError || (st.copied ? 'Copied' : '') })),
      { title: app.name, width: 360, anchor: `[data-app-details=${JSON.stringify(app.url)}]`, close: () => { st.details = null; redraw() } })
  }
  function view(options = {}) { return registry.component('core.apps', { args: [options], variant: options.list ? 'list' : 'pane', state: st, defaultView: () => builtinView(options) }) }
  function builtinView({ list = false, collapse = null, empty = false, embedded = false } = {}) {
    const app = empty ? null : st.selected
    if (!list && app && (wide() || embedded)) return h('div', { class: 'apps-frame', key: `app:${app.url}`, 'aria-label': app.name },
      h('div', { class: 'apps-toolbar' },
        historyButton(),
        h('span', { class: 'apps-frame-address', text: appAddress(app.url), title: app.url }),
        !appUnavailable(app) ? h('a', { class: 'pillbtn', href: appURL(app.url), target: '_blank', rel: 'noopener noreferrer', text: 'Open in browser' }) : null,
        h('button', { class: 'pillbtn', text: 'Copy link', onclick: () => copyLink(app) }),
        h('button', { class: 'iconbtn ghost', title: 'Close app', 'aria-label': 'Close app', onclick: back }, icon.close(16)),
        h('span', { class: 'apps-copy-status', role: 'status', text: st.copyError || (st.copied ? 'Copied' : '') })),
      appUnavailable(app) ? h('div', { class: 'apps-empty', role: 'status' }, h('p', { text: `${app.name} · ${appUnavailable(app)}` }), h('p', { class: 'note', text: 'Manage this plugin in Settings › Plugins.' }))
        : h('iframe', { class: 'apps-embed', key: app.url, src: appURL(app.url), title: app.name, referrerpolicy: 'strict-origin-when-cross-origin', sandbox: 'allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads' }))
    const none = st.loaded && !st.apps.length && !st.error
    const noApps = () => emptyState({ object: 'window', title: 'No apps yet', body: firstUseActive() ? 'The apps you build and install live here.' : 'An agent can build an app for you.',
      action: h('button', { class: 'pillbtn primary', text: 'Build an app', onclick: newThread }), props: { class: 'apps-empty' } })
    if (!list && wide()) return h('div', { class: 'pane-body', key: 'pane:apps:empty' }, none ? noApps() :
      h('div', { class: 'pane-nothing apps-rest' },
        h('div', { class: 'apps-rest-icons', 'aria-hidden': 'true' }, ...st.apps.slice(0, 3).map(appPicture)),
        h('div', { class: 'display', text: 'Pick an app' }),
        st.error ? h('div', { class: 'why', text: st.error }) : null))
    return h(list ? 'div' : 'main', { class: `apps-page${list ? ' apps-list' : ''}`, key: 'apps', 'aria-label': 'Apps' },
      list ? h('div', { class: 'list-head' }, h('h1', { class: 'display', text: 'Apps' }), collapse) : h('header', { class: 'apps-list-heading' }, h('h1', { class: 'display', text: 'Apps' })),
      h('div', { class: list ? 'scroll apps-list-scroll' : 'apps-catalog' },
      st.error ? h('div', { class: 'apps-notice', role: 'alert' }, h('p', { text: st.error }), h('button', { class: 'pillbtn content-mark', text: 'Try again', disabled: st.loading, onclick: () => load({ list: true }) })) : null,
      !st.loaded && st.loading ? h('p', { class: 'muted', role: 'status', text: 'Loading apps…' }) : null,
      ...registry.draw('apps.source', { apps: st.apps, row, open, defaultView: (source, title) => {
        const apps = st.apps.filter(app => app.source === source)
        return apps.length ? h('section', { key: source, class: 'apps-group', 'aria-label': title },
          new Set(st.apps.map(app => app.source)).size > 1 ? h('h2', { class: 'kicker', text: title }) : null, h('div', { class: 'apps-group-list' }, ...apps.map(row))) : null
      } }),
      none ? noApps() : null), detailsView())
  }
  return { st, load, open, back, copyLink, view }
}
