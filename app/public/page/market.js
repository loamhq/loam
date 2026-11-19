// Settings › Market (2026-11-18): the marketplace's list as a place of its
// own. Opening it fills the window (Settings steps aside), with a way back
// to Settings and a close; on a phone it is the whole screen.
//
// The front page: the masthead, one search field, the kinds, a lead story,
// and a section per kind that has listings. Search is ask-first: as a person
// types, the best few drop down under the field; Enter (or "See all
// results") is a results page with the best match large. Every results page
// and kind page ends with "Nothing quite right? Ask one of your agents to
// build it", which is also the whole of an empty Market. A listing has a
// page of its own, and every install goes through one sheet, the front of
// the install-from-link path (POST /plugins: what it declares, then a yes).
//
// Openable from anywhere by id, as a thread's listing card does:
//   host.openMarket({ id })            a listing's page (`from: { label }`:
//                                      its way back is to there, not the Market)
//   host.openMarket({ kind, q })       a kind's page, or a search's results
//   host.openMarketInstall(id)         the install sheet alone, over anything
// and as commands `market.open` and `market.install` (page/registrations.js).
// What the Market sets is a persona's to set through the same routes:
// `plugin.install` and `appearance.<kind>.theme` (bin/settings).
//
// **A listing card in a thread** (2026-11-18, `marketCards`): an agent puts a
// listing's line in its reply (public/market-card.js says the form) and the
// thread draws this card in its place: the icon, the name, the maker and
// kind, one line, how far it reaches, and the same button as the Market's,
// whose Install opens the same install sheet over the thread. The name opens
// the listing's page, whose way back is the thread. An id the Market does not
// list is the link's words, plain.
import { h, icon, kept, blob, on as onNode } from '../ui.js'
import { agentLook } from '../sessions.js'
import { menuSurface } from '../menu-surface.js'
import { sheetHandle } from '../chat-ui.js'
import { KIND_ONE, KIND_MANY, KIND_ABOUT, KIND_HOME, KIND_LINE, TAGLINE, search, highlight, leadOf, sectionsOf, kindsListed, buildRequest, actionOf, plainTouches, ROOT_LINE, reachOf, kindOf, byLoam, hueOf, tileRows, fineLine, madeBy, linkShape } from '../market.js'
import { themePreview } from '../theme-preview.js'
import { createThemeShowcase } from '../theme-showcase.js'
import { pluginGallery } from '../plugin-gallery.js'
import { creatorLinks } from '../creator-links.js'
import { emptyState } from '../empty-state.js'

const SUGGEST = 5

export function mount(host) {
  const st = { list: null, loading: false, details: new Map(), sel: 0, drop: false, picking: false, install: null, focus: null, themes: new Map(), listRequest: 0, failed: new Set(), docked: false }
  const events = host.registry?.scope?.({ id: '@core' })?.events
  const sheet = () => host.state.sheet?.kind === 'market' ? host.state.sheet : null
  const entries = () => st.list?.plugins ?? []
  const entryOf = id => entries().find(e => e.id === id) ?? null
  const installedRow = id => host.pluginLoader?.st.rows?.find(r => r.id === id && r.effective) ?? null
  const isInstalled = entry => Boolean(installedRow(entry.id) || entry.installed)
  const themeInstalled = id => host.theme.installed.some(t => t.id === id)
  // A theme's listing shows it worn, before Try it or Get (2026-11-19).
  const showcase = createThemeShowcase({ schedule: () => host.schedule(), mode: () => host.theme.current === 'dark' ? 'dark' : 'light' })

  // ------------------------------------------------------------ the list
  async function load(refresh = false) {
    if (st.loading && !refresh) return
    const ticket = ++st.listRequest
    st.loading = true; host.schedule()
    try { const list = await host.api('GET', `/marketplace${refresh ? '?refresh=1' : ''}`); if (ticket === st.listRequest) st.list = list }
    catch (error) { if (ticket === st.listRequest) st.list = { state: [404, 405].includes(error.status) ? 'off' : 'unreachable', plugins: [], message: error.message } }
    finally { if (ticket === st.listRequest) { st.loading = false; warm(); host.schedule() } }
  }
  // What the front page draws from each listing's own link: the lead
  // story's cover and every theme's colours. The broker keeps them an hour.
  // Three at a time, the lead and the themes first: each is the broker
  // reading a listing's link once (it keeps the answer an hour).
  function warm() {
    const list = entries(), lead = leadOf(list)
    const first = [lead, ...list.filter(e => kindOf(e) === 'theme'), ...sectionsOf(list, lead).flatMap(s => s.entries)].filter(Boolean)
    for (const entry of first.slice(0, 40)) detailsOf(entry.id, { queued: true })
  }
  const queue = []
  let running = 0
  function pump() {
    while (running < 3 && queue.length) {
      const job = queue.shift()
      running++
      job().finally(() => { running--; pump() })
    }
  }
  function detailsOf(id, { queued = false } = {}) {
    let d = st.details.get(id)
    if (d && !(d.state === 'error' && Date.now() - d.at > 30000)) { if (!queued && d.waiting) { d.waiting = false; const i = queue.indexOf(d.job); if (i >= 0) { queue.splice(i, 1); d.job() } } return d }
    d = { state: 'loading', at: Date.now(), waiting: queued }
    st.details.set(id, d)
    d.job = () => { d.waiting = false; return fetchDetails(id, d) }
    if (queued) { queue.push(d.job); pump() } else d.job()
    return d
  }
  function fetchDetails(id, d) {
    return host.api('GET', `/marketplace/${encodeURIComponent(id)}`).then(body => {
      Object.assign(d, { state: 'ready', body })
      if (body?.theme) themeCss(id)
    }).catch(error => Object.assign(d, { state: 'error', error: error.message, at: Date.now() })).finally(() => host.schedule())
  }
  // A listed theme's tokens, scoped to its own look: its swatches draw from
  // them, and Try it wears them. An installed theme's are in themes.css.
  function themeCss(id) {
    if (themeInstalled(id)) return 'ready'
    let t = st.themes.get(id)
    if (t) return t.state
    const link = document.createElement('link')
    t = { state: 'loading', link }
    st.themes.set(id, t)
    link.rel = 'stylesheet'; link.href = `/api/marketplace/${encodeURIComponent(id)}/theme.css`
    link.dataset.marketTheme = id
    link.onload = () => { t.state = 'ready'; host.schedule() }
    link.onerror = () => { t.state = 'error'; link.remove(); if (host.theme.trying === id) host.theme.try(null); host.schedule() }
    document.head.append(link)
    return t.state
  }
  function dropThemes() {
    for (const [id, t] of st.themes) if (host.theme.trying !== id) { t.link.remove(); st.themes.delete(id) }
  }

  // ------------------------------------------------------------ opening
  function openMarket({ kind = 'all', q = '', id = null, install = null, back, from = null } = {}) {
    const prev = host.state.sheet
    const backTo = back !== undefined ? back : prev?.kind === 'settings' ? { page: prev.page === 'root' && host.state.wide ? 'appearance' : prev.page, provider: prev.provider ?? null } : prev?.kind === 'market' ? prev.back : null
    field.value = q ?? ''
    st.drop = false; st.sel = 0; st.picking = false
    const view = id ? 'listing' : q ? 'results' : kind && kind !== 'all' ? 'kind' : 'front'
    host.openSheet({ kind: 'market', view, q: q ?? '', k: kind || 'all', id, back: backTo, trail: [], from: from?.label ? { label: String(from.label) } : null })
    if (!st.list || Date.now() - (st.loadedAt ?? 0) > 60000) { st.loadedAt = Date.now(); load() }
    if (id) detailsOf(id)
    if (install) openInstall(install)
    st.focus = id ? 'heading' : host.state.wide ? 'field' : 'heading'
  }
  function go(next, { push = true } = {}) {
    const s = sheet()
    if (!s) return
    if (push) s.trail = [...(s.trail ?? []), { view: s.view, q: s.q, k: s.k, id: s.id, scroll: body()?.scrollTop ?? 0 }].slice(-20)
    Object.assign(s, { q: '', k: 'all', id: null }, next)
    if (s.view !== 'results') field.value = s.view === 'listing' ? field.value : ''
    else field.value = s.q
    st.drop = false; st.sel = 0; st.picking = false
    if (s.id) detailsOf(s.id)
    st.focus = s.view === 'listing' ? 'heading' : 'top'
    host.schedule()
  }
  function back() {
    const s = sheet()
    if (!s) return
    const prev = s.trail?.pop()
    // Opened from a thread's card: the first page's way back is the thread.
    if (!prev && s.from) return close()
    if (!prev) return go({ view: 'front' }, { push: false })
    Object.assign(s, { view: prev.view, q: prev.q, k: prev.k, id: prev.id })
    field.value = s.view === 'results' ? s.q : ''
    st.drop = false; st.picking = false
    st.focus = { scroll: prev.scroll }
    host.schedule()
  }
  function close() {
    const wide = host.state.wide
    host.closeSheet({ historyBack: wide })
  }
  function toSettings() {
    const s = sheet()
    const to = s?.back
    if (!to) return close()
    host.goSettings(to.page || 'root', to.provider ?? null)
  }
  events?.on('sheet.changing', ({ sheet: next }) => {
    // Leaving the Market stops a theme being tried, and lets its sheets go.
    if (next?.kind !== 'market') {
      if (host.theme.trying) host.theme.try(null)
      dropThemes(); st.drop = false; st.picking = false
    }
  })

  // ------------------------------------------------------------ the search field
  const field = h('input', { class: 'market-q', type: 'text', inputmode: 'search', enterkeyhint: 'search', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false',
    placeholder: 'Search, or say what you want', 'aria-label': 'Search the Market', role: 'combobox', 'aria-autocomplete': 'list', 'aria-controls': 'market-suggest', 'aria-expanded': 'false' })
  onNode(field, 'input', () => { st.drop = Boolean(field.value.trim()); st.sel = -1; host.schedule() })
  onNode(field, 'focus', () => { if (field.value.trim() && !st.drop && sheet()?.view !== 'results') { st.drop = true; host.schedule() } })
  onNode(field, 'keydown', e => {
    const found = suggestions()
    if (e.key === 'ArrowDown' && st.drop && found.length) { e.preventDefault(); st.sel = (st.sel + 1) % (found.length + 1); host.schedule() }
    else if (e.key === 'ArrowUp' && st.drop && found.length) { e.preventDefault(); st.sel = st.sel <= 0 ? found.length : st.sel - 1; host.schedule() }
    else if (e.key === 'Enter') {
      e.preventDefault()
      if (st.drop && st.sel >= 0 && st.sel < found.length) return openListing(found[st.sel].id)
      submit()
    } else if (e.key === 'Escape' && (st.drop || field.value)) {
      e.preventDefault(); e.stopPropagation()
      if (st.drop) st.drop = false
      else field.value = ''
      host.schedule()
    }
  })
  function submit() {
    const q = field.value.trim()
    if (!q) { st.drop = false; return go({ view: 'front' }) }
    go({ view: 'results', q, k: sheet()?.view === 'kind' ? sheet().k : 'all' }, { push: sheet()?.view !== 'results' })
    field.blur()
  }
  const suggestions = () => field.value.trim() ? search(entries(), field.value).slice(0, SUGGEST) : []
  function openListing(id) { go({ view: 'listing', id }) }

  // ------------------------------------------------------------ pieces
  const button = (text, onclick, cls = 'pillbtn', props = {}) => h('button', { type: 'button', class: cls, onclick, ...props }, ...(Array.isArray(text) ? text : [text]))
  const marked = (text, q) => highlight(text, q).map(part => part.hit ? h('mark', { class: 'market-hit', text: part.text }) : part.text)
  const fileURL = (id, name) => `/api/marketplace/${encodeURIComponent(id)}/${name}`
  // A listing's cover, once its details say it has one (the listing's own
  // `cover`, else the plugin's own picture, a theme's `image`), in the
  // current mode: the dark one when Loam is dark and there is one.
  function coverOf(entry) {
    const body = st.details.get(entry.id)?.body
    if (!body?.cover) return null
    const dark = host.theme.current === 'dark' && body.cover_dark
    const src = fileURL(entry.id, dark ? 'cover-dark' : 'cover')
    if (st.failed.has(src)) return null
    return { src, position: (dark ? body.cover_position_dark : null) ?? body.cover_position ?? '50% 50%' }
  }
  function coverImg(entry, cover, cls = 'market-cover-img') {
    return h('img', { class: cls, src: cover.src, alt: '', loading: 'lazy', decoding: 'async', key: `cover:${cover.src}`, style: { objectPosition: cover.position },
      onerror: () => { st.failed.add(cover.src); host.schedule() } })
  }
  function tile(entry, size = 'md', { cover: withCover = false } = {}) {
    const d = st.details.get(entry.id)
    const cover = withCover ? coverOf(entry) : null
    if (cover) return h('span', { class: `market-tile market-tile-${size} market-tile-cover`, 'aria-hidden': 'true' }, coverImg(entry, cover, 'market-tile-img'))
    if (kindOf(entry) === 'theme' && themeState(entry.id) === 'ready') return h('span', { class: `theme-preview market-tile market-tile-${size} market-tile-theme`, 'data-look': entry.id, 'aria-hidden': 'true' },
      h('span', { class: 'market-tile-dot espresso' }), h('span', { class: 'market-tile-dot foam' }))
    if (d?.body?.icon) return h('img', { class: `market-tile market-tile-${size} market-tile-img`, src: fileURL(entry.id, 'icon'), alt: '', loading: 'lazy', key: `icon:${entry.id}` })
    const hue = hueOf(entry.id)
    return h('span', { class: `market-tile market-tile-${size}`, 'aria-hidden': 'true', style: { '--tile': `var(${hue})` }, text: (entry.name || entry.id).trim().charAt(0).toUpperCase() })
  }
  const byline = entry => [entry.author, KIND_ONE[kindOf(entry)]].filter(Boolean).join(' · ')
  function reach(entry) {
    const d = st.details.get(entry.id)
    if (d?.state !== 'ready') return null
    const r = reachOf(d.body.declaration, kindOf(entry))
    return h('span', { class: `market-reach market-reach-${r.tone}` }, h('i', { 'aria-hidden': 'true' }), r.text)
  }
  function action(entry, { size = '', from = 'card' } = {}) {
    const a = actionOf(entry, { installed: isInstalled(entry), themeInUse: host.theme.value.theme, trying: host.theme.trying })
    const label = `${a.label} ${entry.name}`
    if (a.action === 'none') return h('span', { class: `market-state ${size}`, text: a.label, 'aria-label': `${entry.name}: ${a.label}` })
    if (a.action === 'view') return from === 'listing' ? h('span', { class: `market-state ${size}`, text: 'Not installable yet' }) : button(a.label, e => { e.stopPropagation(); openListing(entry.id) }, `pillbtn market-act ${size}`.trim(), { 'aria-label': `See ${entry.name}` })
    const primary = from === 'listing' || from === 'lead'
    return button(a.label, e => { e.stopPropagation(); act(entry, a.action) }, `pillbtn market-act ${primary ? 'primary' : 'secondary'} ${size}`.trim(), { 'aria-label': label })
  }
  function act(entry, what) {
    if (what === 'install') return openInstall(entry.id)
    if (what === 'use') { host.theme.try(null); host.theme.set({ theme: entry.id }); host.schedule(); return }
    if (what === 'open') { close(); host.goApps(); return }
  }
  // Try it: a theme only, and not the one being worn. Over a photograph it
  // is glass (`glass`); on a listing's page it reads "Try it first".
  function tryButton(entry, size = '', { glass = false, first = false } = {}) {
    if (kindOf(entry) !== 'theme' || host.theme.value.theme === entry.id) return null
    const on = host.theme.trying === entry.id
    const ready = themeInstalled(entry.id) || st.themes.get(entry.id)?.state === 'ready'
    const failed = st.themes.get(entry.id)?.state === 'error' || st.details.get(entry.id)?.state === 'error'
    if (failed) return null
    return button(on ? 'Stop trying' : first ? 'Try it first' : 'Try it', e => { e.stopPropagation(); host.theme.try(on ? null : entry.id) }, `pillbtn market-try ${size}${glass ? ' market-glass' : ''}`.trim(),
      { 'aria-pressed': String(on), disabled: !ready, 'aria-label': `${on ? 'Stop trying' : 'Try'} ${entry.name}` })
  }
  const themeState = id => themeInstalled(id) ? 'ready' : st.themes.get(id)?.state ?? (st.details.get(id)?.state === 'error' ? 'error' : 'loading')
  // What fills a listing's picture frame (2026-11-18): its cover; else, for
  // a theme, Loam wearing it (Appearance's own preview, in the theme's own
  // tokens from its theme.css); else its icon, large, on a quiet field.
  function picture(entry, { large = false } = {}) {
    const cover = coverOf(entry)
    if (cover) return coverImg(entry, cover)
    if (kindOf(entry) === 'theme' && themeState(entry.id) !== 'error') return livePreview(entry, large)
    return h('span', { class: 'market-icon-field', 'aria-hidden': 'true' }, tile(entry, large ? 'xl' : 'lg'))
  }
  function livePreview(entry, large) {
    const ready = themeState(entry.id) === 'ready'
    return themePreview(ready ? entry.id : null, { cls: `market-preview${large ? ' market-preview-large' : ''}${ready ? '' : ' is-loading'}`, initial: (entry.name || entry.id).charAt(0).toUpperCase() })
  }
  // A theme's picture in results (the best match's frame).
  const swatch = entry => h('span', { class: 'market-swatch', 'aria-hidden': 'true' }, picture(entry))
  const creatorOf = entry => entry.creator?.name || entry.author || ''
  // A theme as a large photograph (2026-11-18, the store): its cover (or,
  // without one, Loam wearing it) with Try it over it on hover, and under
  // it the name in the display face, the maker on the right, and one line.
  // `shape` is the row's: one wide tile, two half, or three tall.
  function themeTile(entry, shape = 'half', q = '') {
    const photo = Boolean(coverOf(entry))
    const open = () => openListing(entry.id)
    return h('article', { class: `market-theme market-theme-${shape}${photo ? ' has-photo' : ''}${host.theme.trying === entry.id ? ' trying' : ''}`, key: `theme:${entry.id}` },
      h('div', { class: 'market-theme-frame' },
        h('button', { type: 'button', class: 'market-theme-open', 'aria-label': `About ${entry.name}`, onclick: open },
          h('span', { class: 'market-swatch', 'aria-hidden': 'true' }, picture(entry, { large: true }))),
        h('span', { class: 'market-theme-try' }, tryButton(entry, 'small', { glass: photo }))),
      h('div', { class: 'market-theme-meta', onclick: open },
        h('h3', { class: 'display market-name' }, ...marked(entry.name, q)),
        h('span', { class: 'market-theme-by', text: creatorOf(entry) })),
      entry.description ? h('p', { class: 'market-desc', onclick: open }, ...marked(entry.description, q)) : null)
  }
  // A section's themes, in rows of two wide then three tall (tileRows).
  function themeRows(list, q = '') {
    let at = 0
    return h('div', { class: 'market-themes' }, ...tileRows(list.length).map((n, r) => {
      const row = list.slice(at, at += n)
      return h('div', { class: `market-theme-row market-row-${n}`, key: `row:${r}:${row.map(e => e.id).join(',')}` }, ...row.map(e => themeTile(e, n === 1 ? 'wide' : n === 2 ? 'half' : 'tall', q)))
    }))
  }
  // A listing's mark in Loam's blob shape: a theme's own cover, else the
  // plugin's icon, else a theme's colours, else its first letter.
  function blobMark(entry, cls = '') {
    const d = st.details.get(entry.id), kind = kindOf(entry), cover = kind === 'theme' ? coverOf(entry) : null
    const base = `market-blob ${cls}`.trim()
    if (cover) return h('span', { class: `${base} market-blob-photo`, 'aria-hidden': 'true' }, coverImg(entry, cover, 'market-blob-img market-blob-cover'))
    if (d?.body?.icon) return h('span', { class: base, 'aria-hidden': 'true' }, h('img', { class: 'market-blob-img', src: fileURL(entry.id, 'icon'), alt: '', loading: 'lazy', decoding: 'async', key: `icon:${entry.id}` }))
    if (kind === 'theme' && themeState(entry.id) === 'ready') return h('span', { class: `${base} theme-preview market-blob-theme`, 'data-look': entry.id, 'aria-hidden': 'true' },
      h('span', { class: 'market-tile-dot espresso' }), h('span', { class: 'market-tile-dot foam' }))
    return h('span', { class: `${base} market-blob-letter`, 'aria-hidden': 'true', style: { '--tile': `var(${hueOf(entry.id)})` }, text: (entry.name || entry.id).trim().charAt(0).toUpperCase() })
  }
  // An extension, app, agent or skill as a calm card: its mark, the name,
  // one line, the maker and how far it reaches, and its button. The name's
  // hit area is the whole card; the button sits above it.
  function item(entry, q = '', { kind: withKind = false } = {}) {
    const by = withKind ? byline(entry) : creatorOf(entry)
    return h('article', { class: 'market-item', key: `item:${entry.id}` },
      blobMark(entry, 'market-item-mark'),
      h('div', { class: 'market-item-words' },
        h('h3', { class: 'market-item-title' }, h('button', { type: 'button', class: 'display market-name market-item-open', onclick: () => openListing(entry.id), 'aria-label': `About ${entry.name}` }, ...marked(entry.name, q))),
        entry.description ? h('p', { class: 'market-desc' }, ...marked(entry.description, q)) : null,
        h('div', { class: 'market-item-foot' },
          h('span', { class: 'market-by' }, by ? h('span', {}, ...marked(by, q)) : null, reach(entry)),
          h('span', { class: 'market-item-act' }, action(entry, { size: 'small' })))))
  }
  const items = (list, q = '', opts) => h('div', { class: `market-items${list.length === 1 ? ' market-items-one' : ''}` }, ...list.map(e => item(e, q, opts)))
  // The kinds, as quiet text tabs with an accent line under the one shown.
  const kindChips = (active, kinds) => kinds.length ? h('nav', { class: 'market-kinds', 'aria-label': 'Kinds' },
    ...[['all', 'Everything'], ...kinds.map(k => [k, KIND_MANY[k]])].map(([k, label]) => h('button', { type: 'button', key: k, class: `market-kind${active === k ? ' on' : ''}`, 'aria-current': active === k ? 'true' : null, text: label,
      onclick: () => k === 'all' ? go({ view: 'front' }) : go({ view: 'kind', k }, { push: sheet()?.view !== 'kind' }) }))) : null

  function searchBar() {
    const found = st.drop ? suggestions() : []
    field.setAttribute('aria-expanded', String(st.drop && Boolean(field.value.trim())))
    if (st.sel >= 0 && st.sel < found.length) field.setAttribute('aria-activedescendant', `market-suggest-${st.sel}`)
    else field.removeAttribute('aria-activedescendant')
    const q = field.value.trim()
    return h('div', { class: 'market-search', key: 'market-search' },
      h('label', { class: 'market-field' }, icon.search(18), kept(field),
        field.value ? h('button', { type: 'button', class: 'iconbtn market-clear', 'aria-label': 'Clear the search', onclick: () => { field.value = ''; st.drop = false; field.focus(); host.schedule() } }, icon.close(12)) : null),
      st.drop && q ? h('div', { class: 'market-suggest', id: 'market-suggest', role: 'listbox', 'aria-label': 'Best matches' },
        ...(found.length ? found.map((entry, i) => h('div', { key: `s:${entry.id}`, id: `market-suggest-${i}`, role: 'option', 'aria-selected': String(i === st.sel), class: `market-suggestion${i === st.sel ? ' on' : ''}`,
          onpointerdown: e => e.preventDefault(), onclick: () => openListing(entry.id) },
          tile(entry, 'sm'), h('span', { class: 'market-suggest-words' }, h('span', { class: 'market-name' }, ...marked(entry.name, q)), entry.description ? h('span', { class: 'market-desc' }, ...marked(entry.description, q)) : null),
          h('span', { class: 'market-suggest-kind', text: KIND_ONE[kindOf(entry)] })))
          : [h('p', { class: 'market-suggest-none', text: 'Nothing listed matches yet. Press Enter to ask an agent to build it.' })]),
        h('div', { role: 'option', id: `market-suggest-${found.length}`, 'aria-selected': String(st.sel === found.length), class: `market-suggest-all${st.sel === found.length ? ' on' : ''}`,
          onpointerdown: e => e.preventDefault(), onclick: submit }, found.length ? `See all results for “${q}”` : `What could be built for “${q}”`, h('kbd', { text: '↵' }))) : null)
  }

  // ------------------------------------------------------------ build it
  function agents() {
    const awake = host.state.agents.list.filter(a => !a.archived)
    const first = awake.find(a => a.id === 'iris')
    return first ? [first, ...awake.filter(a => a !== first)] : awake
  }
  function startBuild(agent, q, k) {
    const text = buildRequest(q, k)
    host.closeSheet()
    host.threads.create(agent.id, { draft: text, promptTitle: `Build it with ${agent.name}`,
      promptBody: `${agent.name} will ask what’s needed, then build it as your own plugin. Nothing is sent until you send it.` })
    host.schedule()
  }
  // The build-it state is the app's one empty state (empty-state.js): a
  // sprout for the empty Market and for the end of every page, a lens when
  // a search found nothing. Its words and its one action are the Market's.
  // At the end of a page it is a band (art, words, the button in a row);
  // as the whole of an empty Market, or a search that found nothing, it is
  // the centred empty state.
  function buildIt({ q = '', k = 'all', whole = false, none = false } = {}) {
    const list = agents(), kindWord = k !== 'all' ? (k === 'extension' ? 'plugin' : KIND_ONE[k].toLowerCase()) : null
    const one = list.length === 1 ? list[0] : null
    const act = !list.length ? h('p', { class: 'note', text: 'Your agents appear here once you have one.' })
      : one ? button([icon.pencil(16), `Ask ${one.name} to build it`], () => startBuild(one, q, k), 'pillbtn primary market-build-go')
      : !st.picking ? button([icon.pencil(16), 'Ask an agent to build it'], () => { st.picking = true; st.focus = 'picker'; host.schedule() }, 'pillbtn primary market-build-go', { 'aria-expanded': 'false' })
      : h('div', { class: 'market-pick', role: 'group', 'aria-label': 'Who should build it?' },
        h('p', { class: 'market-pick-label', text: 'Who should build it?' }),
        h('div', { class: 'market-pick-faces' }, ...list.slice(0, 12).map(agent => h('button', { type: 'button', key: agent.id, class: 'market-face', onclick: () => startBuild(agent, q, k), 'aria-label': `Ask ${agent.name} to build it` },
          blob(agentLook(agent.id, host.state.agents), { size: 44, font: 19 }), h('span', { text: agent.name })))))
    return h('section', { class: `market-build${whole ? ' market-build-whole' : none ? ' market-build-none' : ' market-build-band'}`, 'aria-labelledby': 'market-build-title', key: 'market-build' },
      emptyState({ object: none ? 'search' : 'sprout', size: whole ? 'large' : 'pane', titleId: 'market-build-title', action: act,
        title: whole ? 'The Market is just opening' : q ? `Nothing quite right for “${q}”?` : 'Nothing quite right?',
        body: whole ? `Nothing is listed yet. Anything you can think of, one of your agents can build${kindWord ? ` as your own ${kindWord}` : ''}, and it stays yours.` : `Ask one of your agents to build it${kindWord ? ` as your own ${kindWord}` : ' as your own plugin'}. ${q ? 'Your words go with you, ready to send.' : 'Tell them what you have in mind.'}` }))
  }

  // ------------------------------------------------------------ views
  function masthead() {
    return h('header', { class: 'market-mast' },
      h('h1', { class: 'display market-title', tabindex: '-1', text: 'Market' }),
      h('p', { class: 'market-tagline', text: TAGLINE }))
  }
  // The lead story (2026-11-18, the store): one photograph across the page
  // with its words over a soft gradient at its foot, white on any picture.
  // Without a cover it is a plate in the page's own colours, the theme
  // wearing itself (or the plugin's mark) on the right, the words in ink.
  function leadStory(entry) {
    const cover = coverOf(entry), kind = kindOf(entry)
    const kicker = entry.featured ? `Featured ${KIND_ONE[kind].toLowerCase()}` : byLoam(entry) ? `From Loam · ${KIND_ONE[kind]}` : KIND_ONE[kind]
    const art = cover ? coverImg(entry, cover) : kind === 'theme' && themeState(entry.id) !== 'error' ? livePreview(entry, true) : h('span', { class: 'market-lead-mark' }, blobMark(entry, 'market-blob-xl'))
    const by = creatorOf(entry)
    return h('article', { class: `market-lead ${cover ? 'market-lead-photo' : 'market-lead-plate'}${kind === 'theme' ? ' market-lead-theme' : ''}`, 'aria-labelledby': 'market-lead-title', key: `lead:${entry.id}` },
      h('button', { type: 'button', class: 'market-lead-cover', 'aria-label': `About ${entry.name}`, onclick: () => openListing(entry.id) }, art),
      h('div', { class: 'market-lead-cap' },
        h('div', { class: 'market-lead-words' },
          h('p', { class: 'kicker market-kicker', text: kicker }),
          h('h2', { class: 'display', id: 'market-lead-title', text: entry.name }),
          entry.description ? h('p', { class: 'market-lead-desc', text: entry.description }) : null,
          by ? h('p', { class: 'market-lead-by', text: `by ${by}` }) : null),
        h('div', { class: 'market-lead-acts' }, tryButton(entry, '', { glass: Boolean(cover) }), action(entry, { from: 'lead' }))))
  }
  function section({ kind, entries: list, total }) {
    return h('section', { class: `market-section market-section-${kind}`, 'aria-labelledby': `market-sec-${kind}`, key: `sec:${kind}` },
      h('div', { class: 'market-section-head' },
        h('div', { class: 'market-section-titles' }, h('h2', { class: 'display', id: `market-sec-${kind}`, text: KIND_MANY[kind] }), h('span', { class: 'market-section-sub', text: KIND_LINE[kind] })),
        total > 1 || list.length > 1 ? h('button', { type: 'button', class: 'quietlink market-see-all', onclick: () => go({ view: 'kind', k: kind }), 'aria-label': `See all ${KIND_MANY[kind].toLowerCase()}` }, 'See all', icon.chevronRight(12)) : null),
      kind === 'theme' ? themeRows(list) : items(list))
  }
  function notice() {
    if (!st.list && st.loading) return h('div', { class: 'market-loading', role: 'status' }, h('span', { class: 'market-pulse', 'aria-hidden': 'true' }), 'Reading the Market…')
    if (st.list?.state === 'unreachable') return h('div', { class: 'market-note', role: 'status' }, h('p', { text: 'The Market can’t be reached right now. What you have keeps working, and anything shared as a link still installs.' }),
      h('div', { class: 'market-note-acts' }, button(st.loading ? 'Trying…' : 'Try again', () => load(true), 'pillbtn secondary', { disabled: st.loading }), button('Install from a link', () => openLinkInstall(), 'pillbtn')))
    if (st.list?.state === 'off') return h('div', { class: 'market-note', role: 'status' }, h('p', { text: 'This Loam has the Market turned off (marketplace.list in its config). Anything shared as a link still installs.' }), button('Install from a link', () => openLinkInstall(), 'pillbtn'))
    return null
  }
  function front() {
    const list = entries(), lead = leadOf(list), sections = sectionsOf(list, lead)
    if (!list.length) return [masthead(), searchBar(), notice() ?? (st.list ? buildIt({ whole: true }) : null), st.list && st.list.state !== 'off' && st.list.state !== 'unreachable' ? linkLine() : null]
    return [masthead(), searchBar(), kindChips('all', kindsListed(list)), notice(), lead ? leadStory(lead) : null, ...sections.map(section), buildIt(), linkLine()]
  }
  function kindPage(k) {
    const list = entries(), all = search(list, '', k)
    return [masthead(), searchBar(), kindChips(k, kindsListed(list).includes(k) ? kindsListed(list) : [...kindsListed(list), k]),
      h('header', { class: 'market-page-head' }, h('h2', { class: 'display', text: KIND_MANY[k] }), h('p', { class: 'market-section-sub', text: KIND_ABOUT[k] })),
      notice(),
      all.length ? (k === 'theme' ? themeRows(all) : items(all)) : null,
      st.list ? buildIt({ k, whole: !all.length && !list.length }) : null, st.list ? linkLine() : null]
  }
  function results(q, k) {
    const list = entries(), found = search(list, q, k), [best, ...rest] = found
    return [h('div', { class: 'market-results-top' }, searchBar()), kindChips(k, kindsListed(list)),
      notice(),
      st.list ? h('p', { class: 'market-count', role: 'status', text: found.length ? `${found.length} ${found.length === 1 ? 'match' : 'matches'} for “${q}”${k !== 'all' ? ` in ${KIND_MANY[k]}` : ''}` : `Nothing listed matches “${q}”${k !== 'all' ? ` in ${KIND_MANY[k]}` : ''} yet.` }) : null,
      best ? h('article', { class: 'market-best', key: `best:${best.id}`, 'aria-label': `Best match: ${best.name}` },
        kindOf(best) === 'theme' ? h('button', { type: 'button', class: 'market-best-swatch', onclick: () => openListing(best.id), 'aria-label': `About ${best.name}` }, swatch(best)) : blobMark(best, 'market-blob-lg'),
        h('div', { class: 'market-best-words' },
          h('p', { class: 'kicker market-kicker', text: 'Best match' }),
          h('h2', { class: 'display' }, h('button', { type: 'button', class: 'market-best-name', onclick: () => openListing(best.id) }, ...marked(best.name, q))),
          best.description ? h('p', { class: 'market-best-desc' }, ...marked(best.description, q)) : null,
          h('p', { class: 'market-by' }, h('span', {}, ...marked(byline(best), q)), reach(best)),
          h('div', { class: 'market-lead-acts' }, action(best, { from: 'lead' }), tryButton(best)))) : null,
      rest.length ? h('section', { class: 'market-section', 'aria-labelledby': 'market-also' }, h('div', { class: 'market-section-head' }, h('div', { class: 'market-section-titles' }, h('h2', { class: 'display', id: 'market-also', text: 'Also close' }))),
        items(rest, q, { kind: true })) : null,
      st.list ? buildIt({ q, k, none: !found.length }) : null, st.list ? linkLine() : null]
  }
  // A listing's page (2026-11-18, the store): a wide cover (the dark one in
  // dark mode; a theme without one wears itself at the same size), then the
  // name very large with Install on the right, the maker and their links,
  // and the maker's README as an article beside a sticky column of facts.
  // On a phone the buttons sit under the words, and stay at the foot of the
  // screen once they have scrolled away (`dock`).
  function listing(id) {
    const entry = entryOf(id), d = detailsOf(id)
    if (!entry) return [h('div', { class: 'market-note', role: 'status' }, st.list ? h('p', { text: 'This listing is no longer in the Market.' }) : h('p', { text: 'Reading the Market…' }))]
    const kind = kindOf(entry), body = d.body, theme = kind === 'theme'
    const cover = coverOf(entry)
    const frame = cover ? coverImg(entry, cover) : theme && themeState(id) !== 'error' ? livePreview(entry, true) : null
    const version = body?.version || entry.version
    const article = readme(entry, body)
    return [h('div', { class: `market-listing${frame ? ' has-cover' : ' no-cover'}`, key: `listing:${id}` },
      frame ? h('div', { class: 'market-listing-cover' }, frame) : null,
      h('header', { class: 'market-listing-head' },
        frame ? null : blobMark(entry, 'market-blob-xl market-listing-mark'),
        h('div', { class: 'market-listing-who' },
          h('p', { class: 'kicker market-kicker', text: [KIND_ONE[kind], version ? `version ${version}` : null].filter(Boolean).join(' · ') }),
          h('h1', { class: 'display market-listing-title', tabindex: '-1', text: entry.name }),
          entry.description ? h('p', { class: 'market-listing-desc', text: entry.description }) : null),
        listingActs(entry, body, kind)),
      maker(entry, body),
      theme && body?.theme && themeState(id) !== 'error' ? showcase.view({ key: `market-${id}`, look: id, name: entry.name, options: body.theme.options ?? [], palette: body.theme.palette ?? [], from: themeInstalled(id) ? 'installed' : 'market' }) : null,
      gallery(entry, body),
      h('div', { class: `market-listing-body${article ? '' : ' market-listing-body-facts'}` }, article, facts(entry, d, kind)))]
  }
  function listingActs(entry, body, kind, { dock = false } = {}) {
    return h('div', { class: `market-listing-acts${dock ? ' market-dock-acts' : ''}`, key: dock ? 'dock-acts' : 'acts' },
      action(entry, { from: 'listing' }), tryButton(entry, '', { first: true }),
      !dock && body ? h('p', { class: 'market-fine', text: fineLine(body.declaration, kind) }) : null)
  }
  // Its screenshots (2026-11-19, `screenshots` in its PLUGIN.md, read at its
  // link by the broker as `shot/<n>` and `shot/<n>-dark`).
  function gallery(entry, body) {
    const shots = (body?.screenshots ?? []).map((s, i) => ({ caption: s.caption, src: fileURL(entry.id, `shot/${i}`), src_dark: s.dark ? fileURL(entry.id, `shot/${i}-dark`) : null }))
    return pluginGallery({ shots, dark: host.theme.current === 'dark', name: entry.name, key: `gallery:${entry.id}`, failed: st.failed, redraw: () => host.schedule() })
  }
  // The maker: their mark and name, how many things they have in the
  // Market, and the links they chose to show. With no links, only the name.
  function maker(entry, body) {
    const creator = entry.creator ?? { name: entry.author, links: [] }
    const name = creator.name || body?.author || entry.author
    if (!name) return null
    const links = creator.links ?? [], n = madeBy(entries(), name)
    return h('section', { class: `market-creator market-maker${links.length ? '' : ' market-maker-plain'}`, 'aria-label': `Made by ${name}` },
      h('span', { class: 'market-maker-face', 'aria-hidden': 'true', text: name.trim().charAt(0).toUpperCase() }),
      h('div', { class: 'market-maker-who' },
        h('p', { class: 'market-creator-name', text: name }),
        links.length && n ? h('p', { class: 'market-maker-count', text: `Made ${n} ${n === 1 ? 'thing' : 'things'} in the Market` }) : null),
      links.length ? creatorLinks(creator) : null)
  }
  function facts(entry, d, kind) {
    const body = d.body
    const touches = body ? plainTouches(body.declaration, kind) : []
    const src = body?.source?.url ?? entry.link
    const options = body?.theme?.options ?? []
    return h('aside', { class: 'market-side', 'aria-label': 'About it' },
      h('section', { class: 'market-touches', 'aria-labelledby': 'market-touches-title' },
        h('h2', { class: 'kicker market-side-title', id: 'market-touches-title', text: 'What it touches' }),
        d.state === 'loading' ? h('p', { class: 'note market-reading', role: 'status' }, h('span', { class: 'market-pulse', 'aria-hidden': 'true' }), 'Reading what it declares, from its own link…')
          : d.state === 'error' ? h('p', { class: 'note', role: 'status', text: `Its declaration could not be read just now (${d.error}). Installing shows it before you say yes.` })
          : touchList(touches),
        body?.why ? h('p', { class: 'note market-why', text: `${body.why}: it is listed so you can see it, and it installs once Loam can.` }) : null),
      h('section', { class: 'market-details', 'aria-labelledby': 'market-details-title' },
        h('h2', { class: 'kicker market-side-title', id: 'market-details-title', text: 'Details' }),
        h('dl', { class: 'market-kv' },
          h('div', {}, h('dt', { text: 'Version' }), h('dd', { text: body?.version || entry.version || '—' })),
          h('div', {}, h('dt', { text: 'Kind' }), h('dd', { text: (entry.kind ?? []).map(k => KIND_ONE[k]).join(', ') })),
          // Every theme Loam wears has both: the host builds light and dark.
          kind === 'theme' ? h('div', {}, h('dt', { text: 'Light and dark' }), h('dd', { text: 'Both' })) : null,
          h('div', {}, h('dt', { text: 'Source' }), h('dd', {}, h('a', { class: 'quietlink', href: src, target: '_blank', rel: 'noopener noreferrer' }, `${entry.repo}`, icon.external(12)))),
          entry.homepage ? h('div', {}, h('dt', { text: 'More' }), h('dd', {}, h('a', { class: 'quietlink', href: entry.homepage, target: '_blank', rel: 'noopener noreferrer' }, 'Homepage', icon.external(12)))) : null)),
      options.length ? h('section', { class: 'market-options', 'aria-labelledby': 'market-options-title' },
        h('h2', { class: 'kicker market-side-title', id: 'market-options-title', text: 'Options' }),
        h('ul', { class: 'market-option-list' }, ...options.map((o, i) => h('li', { key: `o:${o.id ?? i}`, text: o.label || o.id })))) : null)
  }
  // The maker's README (2026-11-18), read at the listing's tag by the
  // broker with every HTML tag taken out and relative links pointed at
  // GitHub; drawn by the app's own Markdown renderer, which builds nodes
  // (never markup) and follows only http, https and mailto, in a new tab.
  // Its pictures are the ones in the plugin's folder the broker kept; any
  // other stays as its words. Without a README, the plugin's own guide. A
  // first heading that only repeats the name is not drawn twice.
  function readme(entry, body) {
    const text = body?.readme || body?.guide
    if (!text) return null
    const kept = new Set(body.readme ? body.readme_images ?? [] : [])
    const images = { draw: (alt, dest) => kept.has(dest) ? h('img', { class: 'market-readme-img', src: fileURL(entry.id, `readme/${dest}`), alt: alt || '', loading: 'lazy', decoding: 'async' }) : null,
      group: nodes => h('div', { class: 'market-readme-figures' }, ...nodes) }
    let nodes = [host.markdown?.(text, { images, soft: true }) ?? h('p', { text })].flat()
    if (nodes.length === 1 && nodes[0]?.nodeType === 11) nodes = [...nodes[0].childNodes]
    const same = n => String(n ?? '').trim().toLowerCase()
    if (nodes[0]?.tagName === 'H1' && same(nodes[0].textContent) === same(entry.name)) nodes = nodes.slice(1)
    return h('article', { class: 'market-readme', 'aria-labelledby': 'market-readme-title', key: `readme:${entry.id}` },
      h('p', { class: 'market-readme-src', id: 'market-readme-title' }, icon.file(14), body.readme ? 'From the maker’s README' : 'In its maker’s words'),
      h('div', { class: 'md market-readme-body' }, ...nodes))
  }
  const GLYPH = { out: icon.external, server: icon.code, page: icon.apps, record: icon.chat, memory: icon.brief, tool: icon.puzzle, key: icon.key, clock: icon.clock, safe: icon.check }
  const touchList = lines => h('ul', { class: 'market-touch-list' }, ...lines.map((l, i) => h('li', { key: `t:${i}`, class: `market-touch market-touch-${l.glyph}` },
    h('span', { class: 'market-touch-glyph', 'aria-hidden': 'true' }, (GLYPH[l.glyph] ?? icon.check)(15)),
    h('span', {}, h('span', { class: 'market-touch-text', text: l.text }), l.sub ? h('span', { class: 'market-touch-sub', text: l.sub }) : null))))

  function trybar() {
    const id = host.theme.trying
    if (!id) return null
    const entry = entryOf(id)
    const name = entry?.name ?? host.theme.installed.find(t => t.id === id)?.name ?? id
    return h('div', { class: 'market-trybar', role: 'status', key: 'trybar' },
      h('span', { class: 'market-trybar-words' }, h('span', { class: 'market-trybar-dot', 'aria-hidden': 'true' }), `Trying ${name}`),
      entry ? action(entry, { size: 'small', from: 'card' }) : null,
      button('Stop', () => host.theme.try(null), 'pillbtn small market-trybar-stop', { 'aria-label': `Stop trying ${name}` }))
  }

  function marketNodes(s) {
    const view = s.view === 'listing' && s.id ? listing(s.id) : s.view === 'results' && s.q ? results(s.q, s.k) : s.view === 'kind' && s.k !== 'all' ? kindPage(s.k) : front()
    const inner = s.view === 'listing' || s.view === 'results'
    const backLabel = s.view === 'listing' ? (s.trail?.at(-1)?.view === 'results' ? 'Results' : s.trail?.at(-1)?.view === 'kind' ? KIND_MANY[s.trail.at(-1).k] : 'Market') : 'Market'
    const root = h('div', { class: `market${host.state.wide ? ' market-wide' : ' market-phone'}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Market', key: 'sheet:market', 'data-view': s.view,
      onkeydown: keys },
      h('header', { class: 'market-top' },
        s.from && !s.trail?.length ? h('button', { type: 'button', class: 'backbtn market-back market-back-from', onclick: back, title: s.from.label, 'aria-label': `Back to ${s.from.label}` }, icon.chevronLeft(18), h('span', { class: 'market-back-words', text: s.from.label }))
          : inner || s.view === 'kind' ? h('button', { type: 'button', class: 'backbtn market-back', onclick: back }, icon.chevronLeft(18), backLabel)
          : s.back ? h('button', { type: 'button', class: 'backbtn market-back', onclick: toSettings }, icon.chevronLeft(18), 'Settings') : h('span', { class: 'market-top-spacer' }),
        inner || s.view === 'kind' ? null : h('span', { class: 'market-crumb', 'aria-hidden': 'true' }, h('span', { class: 'market-crumb-home', text: 'Market' })),
        trybar(),
        h('button', { type: 'button', class: 'iconbtn ghost market-close', 'aria-label': 'Close the Market', title: 'Close', onclick: close }, icon.close(14))),
      h('div', { class: 'market-body', key: `market-body`, onscroll: watchDock }, h('div', { class: `market-page market-page-${s.view}` }, ...view)),
      dock(s))
    return [h('button', { class: 'scrim market-scrim', key: 'scrim:market', 'aria-label': 'Close the Market', tabindex: '-1', onclick: close }), root]
  }
  function keys(e) {
    if (e.key === 'Escape') {
      if (e.defaultPrevented) return
      e.preventDefault()
      if (st.picking) { st.picking = false; host.schedule(); return }
      const s = sheet()
      if (s && s.view !== 'front') return back()
      return close()
    }
    if (e.key === 'Tab') {
      // Focus stays inside the Market while it is open.
      const root = e.currentTarget, items = [...root.querySelectorAll('button:not(:disabled), a[href], input, summary, [tabindex="0"]')].filter(n => n.getClientRects().length)
      if (!items.length) return
      const first = items[0], last = items.at(-1)
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
  }
  // On a phone, a listing's buttons stay at the foot of the screen once
  // the ones under its words have scrolled away.
  function dockEntry(s) {
    if (host.state.wide || s.view !== 'listing' || !s.id) return null
    const entry = entryOf(s.id)
    return entry && st.details.get(s.id)?.state === 'ready' ? entry : null
  }
  function dock(s) {
    const entry = dockEntry(s)
    if (!entry) return null
    return h('div', { class: `market-dock${st.docked ? ' on' : ''}`, key: 'market-dock', 'aria-hidden': st.docked ? null : 'true', inert: st.docked ? null : true },
      listingActs(entry, st.details.get(s.id)?.body, kindOf(entry), { dock: true }))
  }
  function watchDock() {
    const s = sheet()
    const acts = s && dockEntry(s) ? document.querySelector('.market .market-listing-head .market-listing-acts') : null
    const scroller = body()
    const docked = Boolean(acts && scroller && acts.getBoundingClientRect().bottom < scroller.getBoundingClientRect().top + 4)
    if (docked !== st.docked) { st.docked = docked; host.schedule() }
  }
  const body = () => document.querySelector('.market .market-body')
  // After a draw: where focus and the scroll go on arriving at a view.
  function sync() {
    // The link sheet moves focus as it changes: to what was found, or back
    // to the field (the surface stays open between, so it does not refocus).
    const job = st.install
    if (job?.mode === 'link' && job.move) {
      const target = job.move === 'field' ? linkField : document.querySelector('.market-install .sheet-title')
      if (target?.isConnected) { job.move = null; target.focus({ preventScroll: true }) }
    }
    if (!sheet() || !st.focus) return
    const want = st.focus
    const scroller = body()
    if (!scroller) return
    st.focus = null
    st.docked = false
    if (typeof want === 'object') { scroller.scrollTop = want.scroll ?? 0; return }
    scroller.scrollTop = 0
    if (want === 'field') field.focus({ preventScroll: true })
    else if (want === 'picker') document.querySelector('.market-face')?.focus({ preventScroll: true })
    else (document.querySelector('.market .market-listing-title') ?? document.querySelector('.market .market-title') ?? document.querySelector('.market-q'))?.focus({ preventScroll: true })
    if (want === 'picker') document.querySelector('.market-pick')?.scrollIntoView({ block: 'nearest' })
  }

  // ------------------------------------------------------------ the install sheet
  async function openInstall(id) {
    if (!st.list) await load()
    const entry = entryOf(id)
    if (!entry) { st.install = { id, phase: 'gone' }; host.schedule(); return }
    st.install = { id, entry, phase: 'reading', preview: null, error: '' }
    host.schedule()
    try {
      const preview = await host.pluginLoader.install({ url: entry.link })
      if (st.install?.id !== id) return
      Object.assign(st.install, { preview, phase: 'ready' })
    } catch (error) {
      if (st.install?.id !== id) return
      if (error.status === 409 && error.body?.preview) Object.assign(st.install, { preview: error.body.preview, phase: 'ready' })
      else Object.assign(st.install, { phase: 'error', error: error.message })
    }
    host.schedule()
  }
  function closeInstall() { st.install = null; clearTimeout(linkTimer); host.schedule() }
  async function confirmInstall() {
    const job = st.install
    if (!job?.preview?.confirm || job.phase !== 'ready') return
    job.phase = 'installing'; job.error = ''; host.schedule()
    // A link's yes names exactly what was shown (its repository, folder and
    // tag or commit: `request`), so a newer release since is never what lands.
    const request = job.mode === 'link' ? { ...(job.preview.request ?? { url: job.text }), via: 'link' } : { url: job.entry.link, via: 'market' }
    try {
      const out = await host.pluginLoader.install({ ...request, confirm: job.preview.confirm })
      if (st.install !== job) return
      job.phase = 'done'; job.result = out
      if (job.mode === 'link') job.id = out.id ?? job.preview.id
      await load(true)
      if (job.preview.kind?.includes('theme')) await until(() => themeInstalled(job.id), 4000)
    } catch (error) {
      if (st.install !== job) return
      if (error.status === 409 && error.body?.preview) Object.assign(job, { phase: 'ready', preview: error.body.preview, error: 'It changed since it was read. This is what it declares now.' })
      else Object.assign(job, { phase: 'ready', error: error.message })
    }
    host.schedule()
  }
  const until = (test, ms) => new Promise(resolve => { const end = Date.now() + ms; const tick = () => test() || Date.now() > end ? resolve() : setTimeout(tick, 120); tick() })

  // ------------------------------------------------------------ install from a link
  // (2026-11-18: "If someone wants to sideload something, they should be able
  // to do it fairly easily.") One field in the install sheet: paste what you
  // have and it reads by itself, a moment after the typing stops (at once on
  // a paste or Enter). The host works out the rest (lib/loam_plugins.fetch):
  // the newest release, the plugin's folder, or the plugins to pick from.
  // What it found is then this same sheet, as the Market's: what it is, what
  // it touches, one yes. Openable from Settings › Plugins, the Market's foot,
  // a link card in a thread, and `?modal=market&install=<link>`.
  const linkField = h('input', { class: 'field-input market-link-field', id: 'market-link-field', type: 'text', inputmode: 'url', enterkeyhint: 'go', autocomplete: 'off', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false',
    placeholder: 'github.com/owner/plugin', 'aria-label': 'GitHub link', 'aria-describedby': 'market-link-help' })
  let linkTimer = null, linkTicket = 0
  onNode(linkField, 'input', () => {
    const job = st.install
    if (job?.mode !== 'link') return
    job.text = linkField.value
    clearTimeout(linkTimer)
    if (job.read === job.text.trim()) return
    job.error = null; job.choose = null
    if (job.phase === 'reading' || job.phase === 'choose') job.phase = 'paste'
    const shape = linkShape(job.text)
    if (shape) linkTimer = setTimeout(() => readLink(), job.pasted ? 0 : 700)
    job.pasted = false
    host.schedule()
  })
  onNode(linkField, 'paste', () => { if (st.install?.mode === 'link') st.install.pasted = true })
  onNode(linkField, 'keydown', e => { if (e.key === 'Enter') { e.preventDefault(); clearTimeout(linkTimer); readLink({ now: true }) } })
  function openLinkInstall({ link = '' } = {}) {
    clearTimeout(linkTimer)
    st.install = { mode: 'link', id: null, phase: 'paste', text: String(link ?? ''), preview: null, choose: null, error: null, read: null, focus: !link }
    linkField.value = st.install.text
    host.schedule()
    if (link) readLink({ now: true })
  }
  async function readLink({ now = false, request = null } = {}) {
    const job = st.install
    if (job?.mode !== 'link') return
    const text = job.text.trim(), shape = linkShape(text)
    if (!request && !text) return
    if (!request && !shape) { if (now) { job.error = { message: 'Paste a GitHub link to the plugin: its repository, like github.com/owner/plugin, is enough.', reason: 'empty' }; host.schedule() } return }
    if (!request && !shape.ok) { job.error = { message: 'That isn’t a GitHub link. Paste the address of the plugin’s repository on GitHub, like github.com/owner/plugin.', reason: 'not-github' }; job.phase = 'paste'; host.schedule(); return }
    const ticket = ++linkTicket
    Object.assign(job, { phase: 'reading', error: null, read: text, repo: request?.url ?? shape.repo, ...(request ? {} : { choose: null }) })
    host.schedule()
    try {
      const out = await host.pluginLoader.install(request ?? { url: text })
      if (st.install !== job || ticket !== linkTicket) return
      if (out.choose) Object.assign(job, { phase: 'choose', choose: out })
      else Object.assign(job, { phase: 'ready', preview: out, id: out.id, error: '', move: 'heading' })
    } catch (error) {
      if (st.install !== job || ticket !== linkTicket) return
      if (error.status === 409 && error.body?.preview) Object.assign(job, { phase: 'ready', preview: error.body.preview, id: error.body.preview.id, error: '' })
      else Object.assign(job, { phase: job.choose ? 'choose' : 'paste', error: { message: error.body?.error ?? error.message, reason: error.body?.reason ?? null } })
    }
    host.schedule()
  }
  const pick = c => readLink({ request: { url: st.install.choose.repo, path: c.path, ...(st.install.choose.ref ? { ref: st.install.choose.ref } : {}) } })
  function backToLink() {
    const job = st.install
    if (job?.mode !== 'link' || job.phase === 'installing') return
    Object.assign(job, { phase: job.choose ? 'choose' : 'paste', preview: null, error: null, move: job.choose ? 'heading' : 'field' })
    host.schedule()
  }
  // A plugin's picture before it is installed: its icon (a data: address
  // the host read from the link), else its letter on a calm tile.
  function linkTile(p, size = 'md') {
    if (p?.pictures?.icon ?? p?.icon) return h('img', { class: `market-tile market-tile-${size} market-tile-img`, src: p.pictures?.icon ?? p.icon, alt: '', key: `link-icon:${p.id ?? p.path}` })
    const name = p?.name || p?.id || '?'
    return h('span', { class: `market-tile market-tile-${size}`, 'aria-hidden': 'true', style: { '--tile': `var(${hueOf(p?.id || name)})` }, text: name.trim().charAt(0).toUpperCase() })
  }
  const linkCover = p => {
    const dark = host.theme.current === 'dark' && p?.pictures?.cover_dark
    const src = dark ? p.pictures.cover_dark : p?.pictures?.cover
    return src ? h('div', { class: 'market-install-cover', 'aria-hidden': 'true' }, h('img', { src, alt: '', key: `link-cover:${p.id}:${dark ? 'd' : 'l'}` })) : null
  }
  const kindLine = c => [KIND_ONE[kindOf(c)], c.version ? `version ${c.version}` : null, c.author].filter(Boolean).join(' · ')
  function linkAsk() {
    const job = st.install, reading = job.phase === 'reading', err = job.error
    const choose = job.phase === 'choose' || (reading && job.choose) ? job.choose : null
    return h('div', { class: 'sheet market-install market-link', key: 'install:link' }, sheetHandle(closeInstall),
      h('div', { class: 'market-link-head' },
        h('span', { class: 'market-link-glyph', 'aria-hidden': 'true' }, icon.link(20)),
        h('h2', { class: 'display sheet-title', tabindex: '-1', text: 'Install from a link' })),
      h('p', { class: 'market-install-desc', id: 'market-link-help', text: 'Paste a GitHub link to a theme, extension or app. The repository’s address is enough: Loam finds the newest version.' }),
      h('div', { class: `market-link-row${err ? ' is-wrong' : ''}` }, kept(linkField),
        job.text ? h('button', { type: 'button', class: 'iconbtn ghost market-link-clear', 'aria-label': 'Clear the link', onclick: () => { linkField.value = ''; Object.assign(job, { text: '', error: null, choose: null, read: null, phase: 'paste' }); clearTimeout(linkTimer); linkField.focus(); host.schedule() } }, icon.close(14)) : null),
      reading ? h('p', { class: 'note market-reading', role: 'status' }, h('span', { class: 'market-pulse', 'aria-hidden': 'true' }), `Looking at github.com/${job.repo ?? ''}…`) : null,
      err ? h('div', { class: 'market-link-error', role: 'alert' }, h('p', { text: err.message }),
        err.reason === 'unreachable' && !err.message.includes('Connections › GitHub') && host.goSettings ? button('Open Connections', () => { closeInstall(); host.goSettings('connections') }, 'pillbtn small secondary') : null) : null,
      choose ? h('section', { class: 'market-link-choose', 'aria-labelledby': 'market-link-choose-title' },
        h('h3', { class: 'market-facts-title', id: 'market-link-choose-title', text: `github.com/${choose.repo} holds ${choose.choose.length} plugins. Which one?` }),
        h('ul', { class: 'market-link-picks' }, ...choose.choose.map(c => h('li', { key: `pick:${c.path}` },
          h('button', { type: 'button', class: 'market-link-pick', disabled: reading || c.broken, onclick: () => pick(c), 'aria-label': c.broken ? `${c.name}: can’t be read` : `${c.name}, ${kindLine(c)}` },
            linkTile(c, 'sm'),
            h('span', { class: 'market-link-pick-words' }, h('span', { class: 'market-link-pick-name', text: c.name || c.path }),
              h('span', { class: 'market-link-pick-sub', text: c.broken ? 'Its PLUGIN.md can’t be read' : c.description || kindLine(c) })),
            c.broken ? null : icon.chevronRight(12)))))) : null,
      !reading && !choose && !err && !job.text ? h('p', { class: 'note market-link-hint', text: 'A link to a release, a folder or a single plugin works too.' }) : null,
      reading ? h('div', { class: 'market-progress', role: 'progressbar', 'aria-label': 'Reading the link' }, h('span')) : null,
      h('div', { class: 'market-install-acts' }, button('Cancel', closeInstall, 'pillbtn')))
  }
  function linkNodes() {
    const job = st.install
    const card = ['paste', 'reading', 'choose'].includes(job.phase) ? linkAsk() : null
    if (!card) return null
    const surface = menuSurface(card, { title: 'Install from a link', close: closeInstall, heading: false, centered: host.state.wide, width: 520, initialFocus: job.focus ? 'field' : 'heading' })
    const node = host.state.wide ? h('div', { class: 'dialog market-install-dialog', onclick: e => { if (e.target === e.currentTarget) closeInstall() } }, surface) : surface
    node.__key = 'sheet:market-install'
    return [h('button', { class: 'scrim market-install-scrim', key: 'scrim:market-install', 'aria-label': 'Close', tabindex: '-1', onclick: closeInstall }), node]
  }

  function installNodes() {
    const job = st.install
    if (!job) return []
    if (job.mode === 'link') { const asked = linkNodes(); if (asked) return asked }
    const link = job.mode === 'link'
    const entry = job.entry, p = job.preview, kind = entry ? kindOf(entry) : p ? kindOf(p) : 'extension'
    const name = p?.name ?? entry?.name ?? job.id
    const update = p?.replaces
    const theme = kind === 'theme'
    const home = KIND_HOME[kind]
    const done = job.phase === 'done'
    const verb = update ? 'Update' : theme ? 'Get' : 'Install'
    const lines = p ? plainTouches(update && p.added && Object.keys(p.added).length ? { touches: p.added, overrides: p.added.overrides, page_code: p.added.page_code } : p.declaration, kind) : []
    const description = entry?.description ?? p?.description
    const from = link && p?.source ? `from github.com/${p.source.repo}` : null
    const card = h('div', { class: `sheet market-install${link ? ' market-link-found' : ''}`, key: `install:${job.id}` }, sheetHandle(closeInstall),
      job.phase === 'gone' ? [h('h2', { class: 'display sheet-title', tabindex: '-1', text: 'Not in the Market' }), h('p', { class: 'note', text: `The Market lists no ${job.id} now.` }),
        h('div', { class: 'market-install-acts' }, button('Close', closeInstall, 'pillbtn primary'))] : [
      link && !done ? linkCover(p) : null,
      h('div', { class: 'market-install-head' }, entry ? tile(entry, 'md') : link ? linkTile(p) : null,
        h('div', {}, h('h2', { class: 'display sheet-title', tabindex: '-1', text: done ? (theme ? `${name} is yours` : `${name} is installed`) : `${verb} ${name}?` }),
          h('p', { class: 'market-install-src', text: [p?.author ?? entry?.author, p?.version ? `version ${p.version}` : entry?.version ? `version ${entry.version}` : null, entry && byLoam(entry) ? 'made by Loam' : null, from].filter(Boolean).join(' · ') }))),
      done ? h('p', { class: 'market-install-done', role: 'status', text: theme ? `It’s in Appearance with your other themes.${host.theme.value.theme === job.id ? ' You’re wearing it.' : ''}` : kind === 'app' ? 'It’s in Apps, and runs on your server.' : `It’s on, and in Settings › ${home}.` })
        : description ? h('p', { class: 'market-install-desc', text: description }) : null,
      link && !done && p?.note ? h('p', { class: 'note market-link-note' }, icon.branch(14), h('span', { text: p.note })) : null,
      !done ? h('section', { class: 'market-install-touches', 'aria-label': update ? 'Added in this version' : 'Before you say yes' },
        h('h3', { class: 'market-facts-title', text: update ? (Object.keys(p.added ?? {}).length ? 'New in this version' : 'Nothing new in what it touches') : 'Before you say yes' }),
        job.phase === 'reading' ? h('p', { class: 'note market-reading', role: 'status' }, h('span', { class: 'market-pulse', 'aria-hidden': 'true' }), 'Reading what it declares, from its own link…')
          : p ? touchList(lines) : null) : null,
      p?.installable === false ? h('p', { class: 'note market-why', text: `${p.why}. It is listed so you can see it.` }) : null,
      job.error ? h('p', { class: 'plugin-notice err', role: 'alert', text: job.error }) : null,
      !done && p && p.installable !== false && !(theme && !p.declaration?.page_code) ? h('p', { class: 'note market-root', text: ROOT_LINE }) : null,
      !done && p && p.installable !== false ? h('p', { class: 'note market-yes-once', text: update ? 'You said yes to it once. A new version asks again only for what it adds.' : 'You say yes once, here. A later version asks again only if it reaches further.' }) : null,
      job.phase === 'installing' || job.phase === 'reading' ? h('div', { class: 'market-progress', role: 'progressbar', 'aria-label': job.phase === 'installing' ? `Installing ${name}` : 'Reading' }, h('span')) : null,
      h('div', { class: 'market-install-acts' }, ...(done ? [
        theme && host.theme.value.theme !== job.id ? button('Done', () => { closeInstall() }, 'pillbtn') : null,
        theme && host.theme.value.theme !== job.id ? button('Wear it now', () => { host.theme.try(null); host.theme.set({ theme: job.id }); closeInstall() }, 'pillbtn primary') : null,
        kind === 'app' ? button('Done', closeInstall, 'pillbtn') : null,
        kind === 'app' ? button('Open Apps', () => { closeInstall(); close(); host.goApps() }, 'pillbtn primary') : null,
        !theme && kind !== 'app' ? button(`See it in ${home}`, () => { closeInstall(); host.goSettings(home === 'Plugins' ? 'plugins' : home.toLowerCase(), job.id) }, 'pillbtn') : null,
        !theme && kind !== 'app' || theme && host.theme.value.theme === job.id ? button('Done', closeInstall, 'pillbtn primary') : null]
        : job.phase === 'error' ? [button('Close', closeInstall, 'pillbtn'), button('Try again', () => openInstall(job.id), 'pillbtn primary')]
        : [link ? button([icon.chevronLeft(14), job.choose ? 'Pick another' : 'Change link'], backToLink, 'pillbtn market-link-back', { disabled: job.phase === 'installing' }) : button('Not now', closeInstall, 'pillbtn', { disabled: job.phase === 'installing' }),
          p?.installable === false ? null : button(job.phase === 'installing' ? (theme ? 'Getting…' : 'Installing…') : update ? 'Update' : theme ? 'Get it' : 'Install', confirmInstall, 'pillbtn primary', { disabled: job.phase !== 'ready' || !p?.confirm })]))])
    const surface = menuSurface(card, { title: `${verb} ${name}`, close: () => { if (job.phase !== 'installing') closeInstall() }, heading: false, centered: host.state.wide, width: link ? 520 : 480, initialFocus: 'heading' })
    const node = host.state.wide ? h('div', { class: 'dialog market-install-dialog', onclick: e => { if (e.target === e.currentTarget && job.phase !== 'installing') closeInstall() } }, surface) : surface
    node.__key = 'sheet:market-install'
    return [h('button', { class: 'scrim market-install-scrim', key: 'scrim:market-install', 'aria-label': 'Close', tabindex: '-1', onclick: () => { if (job.phase !== 'installing') closeInstall() } }), node]
  }

  // ------------------------------------------------------------ Settings' doors
  // The Market's row, set a little apart at the end of Settings.
  function settingsDoor({ nav = false } = {}) {
    const glyph = icon.spark(16)
    if (nav) return h('div', { class: 'market-door-nav' }, h('div', { class: 'setnav-rule', 'aria-hidden': 'true' }),
      h('button', { type: 'button', class: 'navrow market-door', onclick: () => openMarket({}) }, h('div', { class: 'icon-tile icon-tile-small' }, glyph), h('span', { text: 'Market' })))
    return h('div', { class: 'rows market-door-rows' }, h('button', { type: 'button', class: 'setrow market-door', onclick: () => openMarket({}) },
      h('div', { class: 'icon-tile' }, icon.spark(16)),
      h('div', { class: 'setrow-who' }, h('div', { class: 'setrow-title', text: 'Market' }), h('div', { class: 'setrow-sub', text: 'Themes, extensions, apps, agents and skills' })),
      icon.chevronRight(10)))
  }
  // Settings › Plugins' way in to installing from a link: a row beside
  // "Find more", opening the same sheet as the Market's foot.
  function linkDoor() {
    return h('div', { class: 'rows market-link-door', key: 'link-door' }, h('button', { type: 'button', class: 'setrow', onclick: () => openLinkInstall() },
      h('div', { class: 'icon-tile' }, icon.link(16)),
      h('div', { class: 'setrow-who' }, h('div', { class: 'setrow-title', text: 'Install from a link' }), h('div', { class: 'setrow-sub', text: 'Paste a GitHub link someone shared with you' })),
      icon.chevronRight(10)))
  }
  // The Market's quiet line, at the foot of its pages.
  const linkLine = () => h('p', { class: 'market-link-line', key: 'link-line' }, 'Have a link? ',
    h('button', { type: 'button', class: 'quietlink', onclick: () => openLinkInstall() }, 'Install from a link'))
  // "Find more": the last row of Plugins and of Appearance's themes.
  function findMore(kind, title) {
    return h('div', { class: 'rows market-find-more', key: `find-more:${kind}` }, h('button', { type: 'button', class: 'setrow', onclick: () => openMarket({ kind }) },
      h('div', { class: 'icon-tile' }, icon.spark(16)),
      h('div', { class: 'setrow-who' }, h('div', { class: 'setrow-title', text: title }), h('div', { class: 'setrow-sub', text: `${KIND_MANY[kind]} in the Market` })),
      icon.chevronRight(10)))
  }

  // ------------------------------------------------------------ a card in a thread
  // `refs` are `[{ id, label }]` from market-card.js's splitCards; `from` is
  // where the listing's page goes back to (the thread's title). Drawn on
  // every redraw, so a card reads Installed the moment it is.
  function cardsFor(refs, { from = 'the thread', key = '' } = {}) {
    if (!refs?.length) return null
    if (!st.list && !st.loading) load()
    const ready = Boolean(st.list)
    const shown = refs.map(ref => ({ ref, entry: ready && ref.id ? entryOf(ref.id) : null }))
    if (refs.some(ref => ref.link)) return h('div', { class: `market-cards${shown.length > 1 ? ' market-cards-many' : ''}`, role: 'list', 'aria-label': shown.length > 1 ? 'From the Market' : null, key: `cards:${key}` },
      ...shown.map(({ ref, entry }, i) => ref.link ? linkCard(ref) : entry ? card(entry, from) : h('p', { class: 'market-card-plain', key: `plain:${ref.id}:${i}`, role: 'listitem', text: ref.label || ref.id })))
    // Not in the Market (an unknown id, or a Market that is off or out of
    // reach): the link's words, as plain text, and no error.
    if (ready && shown.every(c => !c.entry)) return h('p', { class: 'market-card-plain', key: `cards:${key}` }, shown.map(c => c.ref.label || c.ref.id).join(', '))
    return h('div', { class: `market-cards${shown.length > 1 ? ' market-cards-many' : ''}`, role: 'list', 'aria-label': shown.length > 1 ? 'From the Market' : null, key: `cards:${key}` },
      ...shown.map(({ ref, entry }, i) => ready && !entry ? h('p', { class: 'market-card-plain', key: `plain:${ref.id}:${i}`, role: 'listitem', text: ref.label || ref.id })
        : !entry ? h('div', { class: 'market-card is-loading', key: `card:${ref.id}`, role: 'listitem', 'aria-busy': 'true' },
          h('span', { class: 'market-tile market-card-tile market-card-ghost', 'aria-hidden': 'true' }),
          h('div', { class: 'market-card-words' },
            h('p', { class: 'market-card-kicker' }, icon.spark(11), 'Market'),
            h('span', { class: 'market-card-name', text: ref.label || ref.id }),
            h('span', { class: 'market-card-line market-card-ghost-line', 'aria-hidden': 'true' })))
        : card(entry, from)))
  }
  // A plugin offered by its link (market-card.js): its name as the agent
  // wrote it and where it lives; Install opens the link sheet, which reads it
  // and shows what it touches before anything is installed.
  function linkCard(ref) {
    const shape = linkShape(ref.link)
    return h('article', { class: 'market-card market-card-link', key: `link:${ref.link}`, role: 'listitem', 'aria-label': `${ref.label}, from a link` },
      h('span', { class: 'market-tile market-tile-md market-card-tile', 'aria-hidden': 'true', style: { '--tile': `var(${hueOf(ref.label)})` }, text: ref.label.trim().charAt(0).toUpperCase() }),
      h('div', { class: 'market-card-words' },
        h('p', { class: 'market-card-kicker' }, icon.link(11), 'From a link'),
        h('span', { class: 'market-card-name market-card-name-plain', text: ref.label }),
        h('span', { class: 'market-card-line', text: shape?.ok ? `github.com/${shape.repo}` : ref.link })),
      h('div', { class: 'market-card-acts' }, h('button', { type: 'button', class: 'pillbtn small secondary market-card-act', 'aria-label': `Install ${ref.label}`, onclick: () => openLinkInstall({ link: ref.link }) }, 'Install')))
  }
  function card(entry, from) {
    const d = detailsOf(entry.id, { queued: true })
    const kind = kindOf(entry)
    if (kind === 'theme' && d.state === 'ready') themeCss(entry.id)
    const r = d.state === 'ready' ? reachOf(d.body.declaration, kind) : null
    const a = actionOf(entry, { installed: isInstalled(entry), themeInUse: host.theme.value.theme, trying: null })
    const openIt = () => openMarket({ id: entry.id, from: { label: from } })
    const act = a.action === 'none'
      ? h('span', { class: 'market-card-state', role: 'status', 'aria-label': `${entry.name}: ${a.label}` }, icon.check(13), a.label)
      : h('button', { type: 'button', class: 'pillbtn small secondary market-card-act',
        'aria-label': a.action === 'view' ? `See ${entry.name}` : `${a.label} ${entry.name}`,
        onclick: () => a.action === 'install' ? openInstall(entry.id) : a.action === 'view' ? openIt()
          : a.action === 'use' ? (host.theme.set({ theme: entry.id }), host.schedule()) : host.goApps() }, a.label)
    return h('article', { class: `market-card market-card-${kind}${a.action === 'none' || a.action === 'use' || a.action === 'open' ? ' is-here' : ''}`, key: `card:${entry.id}`, role: 'listitem', 'aria-label': `${entry.name}, ${KIND_ONE[kind].toLowerCase()} in the Market` },
      tile(entry, 'md', { cover: true }),
      h('div', { class: 'market-card-words' },
        h('p', { class: 'market-card-kicker' }, icon.spark(11), `Market · ${KIND_ONE[kind]}`),
        h('button', { type: 'button', class: 'market-card-name', onclick: openIt, 'aria-label': `About ${entry.name}` }, entry.name),
        entry.description ? h('span', { class: 'market-card-line', text: entry.description }) : null,
        h('span', { class: 'market-card-by' }, h('span', { class: 'market-card-maker', text: entry.author }),
          r ? h('span', { class: `market-reach market-reach-${r.tone}` }, h('i', { 'aria-hidden': 'true' }), r.text) : null)),
      h('div', { class: 'market-card-acts' }, act))
  }

  return { marketCards: cardsFor, marketState: st, openMarket, openMarketInstall: openInstall, openLinkInstall, marketNodes, marketInstallNodes: installNodes, marketSync: sync, marketDoor: settingsDoor, marketFindMore: findMore, marketLinkDoor: linkDoor, loadMarket: load }
}
