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
import { KIND_ONE, KIND_MANY, KIND_ABOUT, KIND_HOME, search, highlight, leadOf, sectionsOf, kindsListed, dateline, buildRequest, actionOf, plainTouches, reachOf, kindOf, byLoam, listedOnly } from '../market.js'
import { buildArt, coverArt, hueOf } from '../market-art.js'

const SUGGEST = 5

export function mount(host) {
  const st = { list: null, loading: false, details: new Map(), sel: 0, drop: false, picking: false, install: null, focus: null, themes: new Map(), listRequest: 0 }
  const events = host.registry?.scope?.({ id: '@core' })?.events
  const sheet = () => host.state.sheet?.kind === 'market' ? host.state.sheet : null
  const entries = () => st.list?.plugins ?? []
  const entryOf = id => entries().find(e => e.id === id) ?? null
  const installedRow = id => host.pluginLoader?.st.rows?.find(r => r.id === id && r.effective) ?? null
  const isInstalled = entry => Boolean(installedRow(entry.id) || entry.installed)
  const themeInstalled = id => host.theme.installed.some(t => t.id === id)

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
  function tile(entry, size = 'md') {
    const d = st.details.get(entry.id)
    if (kindOf(entry) === 'theme' && themeState(entry.id) === 'ready') return h('span', { class: `theme-preview market-tile market-tile-${size} market-tile-theme`, 'data-look': entry.id, 'aria-hidden': 'true' },
      h('span', { class: 'market-tile-dot espresso' }), h('span', { class: 'market-tile-dot foam' }))
    if (d?.body?.icon) return h('img', { class: `market-tile market-tile-${size} market-tile-img`, src: `/api/marketplace/${encodeURIComponent(entry.id)}/icon`, alt: '', loading: 'lazy', key: `icon:${entry.id}` })
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
  function tryButton(entry, size = '') {
    if (kindOf(entry) !== 'theme' || host.theme.value.theme === entry.id) return null
    const on = host.theme.trying === entry.id
    const ready = themeInstalled(entry.id) || st.themes.get(entry.id)?.state === 'ready'
    const failed = st.themes.get(entry.id)?.state === 'error' || st.details.get(entry.id)?.state === 'error'
    if (failed) return null
    return button(on ? 'Stop trying' : 'Try it', e => { e.stopPropagation(); host.theme.try(on ? null : entry.id) }, `pillbtn market-try ${size}`.trim(),
      { 'aria-pressed': String(on), disabled: !ready, 'aria-label': `${on ? 'Stop trying' : 'Try'} ${entry.name}` })
  }
  const themeState = id => themeInstalled(id) ? 'ready' : st.themes.get(id)?.state ?? (st.details.get(id)?.state === 'error' ? 'error' : 'loading')
  // A theme's swatch: Appearance's own miniature, in the theme's own tokens
  // (`.theme-preview[data-look=<id>]` carries them, from its theme.css).
  function swatch(entry, big = false) {
    const id = entry.id, state = themeState(id)
    if (state === 'error') return h('span', { class: 'market-swatch market-swatch-art' }, coverArt(entry, 'theme'))
    if (big) return scene(entry, state)
    return h('span', { class: `theme-preview market-swatch${state !== 'ready' ? ' is-loading' : ''}`, 'data-look': state === 'ready' ? id : null, 'aria-hidden': 'true' },
      h('span', { class: 'theme-miniature' },
        h('span', { class: 'say user', text: 'Room to think' }),
        h('span', { class: 'theme-mini-controls' },
          blob({ color: `var(${hueOf(id)})`, ink: 'var(--text-on-avatar)', shape: '34%', initial: (entry.name || id).charAt(0).toUpperCase() }, { size: 26, font: 13, inline: true }),
          h('span', { class: 'pillbtn primary', text: 'Send' })),
        h('span', { class: 'theme-mini-body', text: 'A little space for ideas.' })))
  }
  // The big one: Loam itself in miniature, wearing the theme: a list of
  // threads, a conversation and the composer.
  function scene(entry, state) {
    const id = entry.id, line = (w, cls = '') => h('span', { class: `market-scene-line ${cls}`.trim(), style: { width: w } })
    const face = (hue, letter) => blob({ color: `var(${hue})`, ink: 'var(--text-on-avatar)', shape: '50%', initial: letter }, { size: 22, font: 11, inline: true })
    return h('span', { class: `theme-preview market-scene${state !== 'ready' ? ' is-loading' : ''}`, 'data-look': state === 'ready' ? id : null, 'aria-hidden': 'true' },
      h('span', { class: 'market-scene-list' },
        h('span', { class: 'market-scene-title', text: 'Chats' }),
        ...[['--persona-teal', 'I', '72%'], ['--persona-rust', 'S', '58%'], ['--persona-plum', 'A', '64%']].map(([hue, l, w], i) =>
          h('span', { class: `market-scene-row${i === 0 ? ' on' : ''}` }, face(hue, l), h('span', { class: 'market-scene-lines' }, line(w), line('40%', 'faint'))))),
      h('span', { class: 'market-scene-pane' },
        h('span', { class: 'market-scene-say', text: 'Room to think' }),
        h('span', { class: 'market-scene-reply' }, face('--persona-teal', 'I'), h('span', { class: 'market-scene-lines' }, line('92%'), line('80%'), line('56%'))),
        h('span', { class: 'market-scene-compose' }, h('span', { class: 'market-scene-placeholder', text: 'Message' }), h('span', { class: 'pillbtn primary', text: 'Send' }))))
  }
  function themeCard(entry, q = '') {
    return h('div', { class: `market-theme${host.theme.trying === entry.id ? ' trying' : ''}`, key: `theme:${entry.id}` },
      h('button', { type: 'button', class: 'market-theme-open', 'aria-label': `About ${entry.name}`, onclick: () => openListing(entry.id) }, swatch(entry)),
      h('div', { class: 'market-theme-words' },
        h('div', { class: 'market-name' }, ...marked(entry.name, q)),
        entry.description ? h('p', { class: 'market-desc' }, ...marked(entry.description, q)) : null,
        h('div', { class: 'market-theme-acts' }, action(entry, { size: 'small' }), tryButton(entry, 'small'))))
  }
  function item(entry, q = '') {
    return h('div', { class: 'market-item', key: `item:${entry.id}` },
      h('button', { type: 'button', class: 'market-item-open', onclick: () => openListing(entry.id), 'aria-label': `About ${entry.name}` },
        tile(entry),
        h('span', { class: 'market-item-words' },
          h('span', { class: 'market-name' }, ...marked(entry.name, q)),
          entry.description ? h('span', { class: 'market-desc' }, ...marked(entry.description, q)) : null,
          h('span', { class: 'market-by' }, h('span', {}, ...marked(byline(entry), q)), reach(entry)))),
      h('span', { class: 'market-item-act' }, action(entry, { size: 'small' })))
  }
  const kindChips = (active, kinds) => kinds.length ? h('div', { class: 'market-chips', role: 'group', 'aria-label': 'Kinds' },
    ...[['all', 'Everything'], ...kinds.map(k => [k, KIND_MANY[k]])].map(([k, label]) => h('button', { type: 'button', key: k, class: `chip${active === k ? ' on' : ''}`, 'aria-pressed': String(active === k), text: label,
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
  function buildIt({ q = '', k = 'all', whole = false } = {}) {
    const list = agents(), kindWord = k !== 'all' ? (k === 'extension' ? 'plugin' : KIND_ONE[k].toLowerCase()) : null
    const one = list.length === 1 ? list[0] : null
    return h('section', { class: `market-build${whole ? ' market-build-whole' : ''}`, 'aria-labelledby': 'market-build-title', key: 'market-build' },
      h('div', { class: 'market-build-art' }, buildArt()),
      h('div', { class: 'market-build-words' },
        h('h2', { class: 'display', id: 'market-build-title', text: whole ? 'The Market is just opening' : q ? `Nothing quite right for “${q}”?` : 'Nothing quite right?' }),
        h('p', { text: whole ? `Nothing is listed yet. Anything you can think of, one of your agents can build${kindWord ? ` as your own ${kindWord}` : ''}, and it stays yours.` : `Ask one of your agents to build it${kindWord ? ` as your own ${kindWord}` : ' as your own plugin'}. ${q ? 'Your words go with you, ready to send.' : 'Tell them what you have in mind.'}` }),
        !list.length ? h('p', { class: 'note', text: 'Your agents appear here once you have one.' })
          : one ? button([icon.pencil(16), `Ask ${one.name} to build it`], () => startBuild(one, q, k), 'pillbtn primary market-build-go')
          : !st.picking ? button([icon.pencil(16), 'Ask an agent to build it'], () => { st.picking = true; st.focus = 'picker'; host.schedule() }, 'pillbtn primary market-build-go', { 'aria-expanded': 'false' })
          : h('div', { class: 'market-pick', role: 'group', 'aria-label': 'Who should build it?' },
            h('p', { class: 'market-pick-label', text: 'Who should build it?' }),
            h('div', { class: 'market-pick-faces' }, ...list.slice(0, 12).map(agent => h('button', { type: 'button', key: agent.id, class: 'market-face', onclick: () => startBuild(agent, q, k), 'aria-label': `Ask ${agent.name} to build it` },
              blob(agentLook(agent.id, host.state.agents), { size: 44, font: 19 }), h('span', { text: agent.name })))))))
  }

  // ------------------------------------------------------------ views
  function masthead() {
    const n = entries().length
    return h('header', { class: 'market-mast' },
      h('h1', { class: 'display market-title', tabindex: '-1', text: 'Market' }),
      h('p', { class: 'market-dateline', text: st.list ? dateline(n) : 'Reading the list…' }))
  }
  function leadStory(entry) {
    const d = st.details.get(entry.id), image = d?.body?.image
    const kind = kindOf(entry)
    return h('article', { class: 'market-lead', 'aria-labelledby': 'market-lead-title', key: `lead:${entry.id}` },
      h('button', { type: 'button', class: 'market-lead-cover', 'aria-label': `About ${entry.name}`, onclick: () => openListing(entry.id) },
        image ? h('img', { src: `/api/marketplace/${encodeURIComponent(entry.id)}/image`, alt: '', loading: 'lazy' })
          : kind === 'theme' && (themeInstalled(entry.id) || st.themes.get(entry.id)?.state === 'ready') ? swatch(entry, true) : coverArt(entry, kind)),
      h('div', { class: 'market-lead-words' },
        h('p', { class: 'kicker market-kicker', text: entry.featured ? 'Featured' : byLoam(entry) ? 'From Loam' : 'New' }),
        h('h2', { class: 'display', id: 'market-lead-title', text: entry.name }),
        entry.description ? h('p', { class: 'market-lead-desc', text: entry.description }) : null,
        h('div', { class: 'market-lead-acts' }, action(entry, { from: 'lead' }), tryButton(entry), button('Read more', () => openListing(entry.id), 'pillbtn market-more')),
        h('p', { class: 'market-by' }, byline(entry), reach(entry))))
  }
  function section({ kind, entries: list, total }) {
    return h('section', { class: 'market-section', 'aria-labelledby': `market-sec-${kind}`, key: `sec:${kind}` },
      h('div', { class: 'market-section-head' }, h('h2', { class: 'display', id: `market-sec-${kind}`, text: KIND_MANY[kind] }),
        total > 1 || list.length > 1 ? h('button', { type: 'button', class: 'quietlink market-see-all', onclick: () => go({ view: 'kind', k: kind }), 'aria-label': `See all ${KIND_MANY[kind].toLowerCase()}` }, total > list.length ? `See all ${total}` : 'See all', icon.chevronRight(12)) : null),
      kind === 'theme' ? h('div', { class: 'market-themes' }, ...list.map(e => themeCard(e))) : h('div', { class: 'market-items' }, ...list.map(e => item(e))))
  }
  function notice() {
    if (!st.list && st.loading) return h('div', { class: 'market-loading', role: 'status' }, h('span', { class: 'market-pulse', 'aria-hidden': 'true' }), 'Reading the Market…')
    if (st.list?.state === 'unreachable') return h('div', { class: 'market-note', role: 'status' }, h('p', { text: 'The Market can’t be reached right now. What you have keeps working, and anything shared as a link still installs from Settings › Plugins.' }),
      button(st.loading ? 'Trying…' : 'Try again', () => load(true), 'pillbtn secondary', { disabled: st.loading }))
    if (st.list?.state === 'off') return h('div', { class: 'market-note', role: 'status' }, h('p', { text: 'This Loam has the Market turned off (marketplace.list in its config). Anything shared as a link still installs from Settings › Plugins.' }))
    return null
  }
  function front() {
    const list = entries(), lead = leadOf(list), sections = sectionsOf(list, lead)
    if (!list.length) return [masthead(), searchBar(), notice() ?? (st.list ? buildIt({ whole: true }) : null)]
    return [masthead(), searchBar(), kindChips('all', kindsListed(list)), notice(), lead ? leadStory(lead) : null, ...sections.map(section), buildIt()]
  }
  function kindPage(k) {
    const list = entries(), all = search(list, '', k)
    return [masthead(), searchBar(), kindChips(k, kindsListed(list).includes(k) ? kindsListed(list) : [...kindsListed(list), k]),
      h('header', { class: 'market-page-head' }, h('h2', { class: 'display', text: KIND_MANY[k] }), h('p', { class: 'note', text: KIND_ABOUT[k] })),
      notice(),
      all.length ? (k === 'theme' ? h('div', { class: 'market-themes' }, ...all.map(e => themeCard(e))) : h('div', { class: 'market-items' }, ...all.map(e => item(e)))) : null,
      st.list ? buildIt({ k, whole: !all.length && !list.length }) : null]
  }
  function results(q, k) {
    const list = entries(), found = search(list, q, k), [best, ...rest] = found
    return [h('div', { class: 'market-results-top' }, searchBar()), kindChips(k, kindsListed(list)),
      notice(),
      st.list ? h('p', { class: 'market-count', role: 'status', text: found.length ? `${found.length} ${found.length === 1 ? 'match' : 'matches'} for “${q}”${k !== 'all' ? ` in ${KIND_MANY[k]}` : ''}` : `Nothing listed matches “${q}”${k !== 'all' ? ` in ${KIND_MANY[k]}` : ''} yet.` }) : null,
      best ? h('article', { class: 'market-best', key: `best:${best.id}`, 'aria-label': `Best match: ${best.name}` },
        kindOf(best) === 'theme' ? h('button', { type: 'button', class: 'market-best-swatch', onclick: () => openListing(best.id), 'aria-label': `About ${best.name}` }, swatch(best)) : tile(best, 'lg'),
        h('div', { class: 'market-best-words' },
          h('p', { class: 'kicker market-kicker', text: 'Best match' }),
          h('h2', { class: 'display' }, h('button', { type: 'button', class: 'market-best-name', onclick: () => openListing(best.id) }, ...marked(best.name, q))),
          best.description ? h('p', { class: 'market-best-desc' }, ...marked(best.description, q)) : null,
          h('p', { class: 'market-by' }, h('span', {}, ...marked(byline(best), q)), reach(best)),
          h('div', { class: 'market-lead-acts' }, action(best, { from: 'lead' }), tryButton(best)))) : null,
      rest.length ? h('section', { class: 'market-section', 'aria-labelledby': 'market-also' }, h('div', { class: 'market-section-head' }, h('h2', { class: 'display', id: 'market-also', text: 'Also close' })),
        h('div', { class: 'market-items' }, ...rest.map(e => item(e, q)))) : null,
      st.list ? buildIt({ q, k }) : null]
  }
  function listing(id) {
    const entry = entryOf(id), d = detailsOf(id)
    if (!entry) return [h('div', { class: 'market-note', role: 'status' }, st.list ? h('p', { text: 'This listing is no longer in the Market.' }) : h('p', { text: 'Reading the Market…' }))]
    const kind = kindOf(entry), body = d.body, image = body?.image
    const touches = body ? plainTouches(body.declaration, kind) : []
    const src = body?.source?.url ?? entry.link
    return [h('div', { class: 'market-listing', key: `listing:${id}` },
      h('header', { class: 'market-listing-head' }, tile(entry, 'xl'),
        h('div', { class: 'market-listing-who' },
          h('h1', { class: 'display market-listing-title', tabindex: '-1', text: entry.name }),
          h('p', { class: 'market-by', text: byline(entry) })),
        h('div', { class: 'market-listing-acts' }, tryButton(entry), action(entry, { from: 'listing' }))),
      entry.description ? h('p', { class: 'market-listing-desc', text: entry.description }) : null,
      h('div', { class: 'market-listing-cover' }, kind === 'theme' && !image ? swatch(entry, true) : image ? h('img', { src: `/api/marketplace/${encodeURIComponent(id)}/image`, alt: '', loading: 'lazy' }) : coverArt(entry, kind)),
      h('div', { class: 'market-facts' },
        h('section', { class: 'market-touches', 'aria-labelledby': 'market-touches-title' },
          h('h2', { class: 'market-facts-title', id: 'market-touches-title', text: 'What it touches' }),
          d.state === 'loading' ? h('p', { class: 'note market-reading', role: 'status' }, h('span', { class: 'market-pulse', 'aria-hidden': 'true' }), 'Reading what it declares, from its own link…')
            : d.state === 'error' ? h('p', { class: 'note', role: 'status', text: `Its declaration could not be read just now (${d.error}). Installing shows it before you say yes.` })
            : touchList(touches),
          body?.why ? h('p', { class: 'note market-why', text: `${body.why}: it is listed so you can see it, and it installs once Loam can.` }) : null),
        h('dl', { class: 'market-kv' },
          h('div', {}, h('dt', { text: 'Made by' }), h('dd', { text: body?.author || entry.author })),
          h('div', {}, h('dt', { text: 'Version' }), h('dd', { text: body?.version || entry.version || '—' })),
          h('div', {}, h('dt', { text: 'Kind' }), h('dd', { text: (entry.kind ?? []).map(k => KIND_ONE[k]).join(', ') })),
          h('div', {}, h('dt', { text: 'Source' }), h('dd', {}, h('a', { class: 'quietlink', href: src, target: '_blank', rel: 'noopener noreferrer' }, `${entry.repo}`, icon.external(12)))),
          entry.homepage ? h('div', {}, h('dt', { text: 'More' }), h('dd', {}, h('a', { class: 'quietlink', href: entry.homepage, target: '_blank', rel: 'noopener noreferrer' }, 'Homepage', icon.external(12)))) : null,
          h('div', {}, h('dt', { text: 'Listed' }), h('dd', { text: 'Read by hand before it was listed' })))),
      body?.guide ? h('details', { class: 'market-guide' }, h('summary', { text: 'About it, in its maker’s words' }), h('div', { class: 'md' }, host.markdown?.(body.guide) ?? h('p', { text: body.guide }))) : null)]
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
      h('div', { class: 'market-body', key: `market-body` }, h('div', { class: `market-page market-page-${s.view}` }, ...view)))
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
  const body = () => document.querySelector('.market .market-body')
  // After a draw: where focus and the scroll go on arriving at a view.
  function sync() {
    if (!sheet() || !st.focus) return
    const want = st.focus
    const scroller = body()
    if (!scroller) return
    st.focus = null
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
  function closeInstall() { st.install = null; host.schedule() }
  async function confirmInstall() {
    const job = st.install
    if (!job?.preview?.confirm || job.phase !== 'ready') return
    job.phase = 'installing'; job.error = ''; host.schedule()
    try {
      const out = await host.pluginLoader.install({ url: job.entry.link, confirm: job.preview.confirm })
      if (st.install !== job) return
      job.phase = 'done'; job.result = out
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
  function installNodes() {
    const job = st.install
    if (!job) return []
    const entry = job.entry, p = job.preview, kind = entry ? kindOf(entry) : 'extension'
    const name = p?.name ?? entry?.name ?? job.id
    const update = p?.replaces
    const theme = kind === 'theme'
    const home = KIND_HOME[kind]
    const done = job.phase === 'done'
    const verb = update ? 'Update' : theme ? 'Get' : 'Install'
    const lines = p ? plainTouches(update && p.added && Object.keys(p.added).length ? { touches: p.added, overrides: p.added.overrides, page_code: p.added.page_code } : p.declaration, kind) : []
    const card = h('div', { class: 'sheet market-install', key: `install:${job.id}` }, sheetHandle(closeInstall),
      job.phase === 'gone' ? [h('h2', { class: 'display sheet-title', tabindex: '-1', text: 'Not in the Market' }), h('p', { class: 'note', text: `The Market lists no ${job.id} now.` }),
        h('div', { class: 'market-install-acts' }, button('Close', closeInstall, 'pillbtn primary'))] : [
      h('div', { class: 'market-install-head' }, entry ? tile(entry, 'md') : null,
        h('div', {}, h('h2', { class: 'display sheet-title', tabindex: '-1', text: done ? (theme ? `${name} is yours` : `${name} is installed`) : `${verb} ${name}?` }),
          h('p', { class: 'market-install-src', text: [p?.author ?? entry?.author, p?.version ? `version ${p.version}` : entry?.version ? `version ${entry.version}` : null, entry && byLoam(entry) ? 'made by Loam' : 'read by hand for the Market'].filter(Boolean).join(' · ') }))),
      done ? h('p', { class: 'market-install-done', role: 'status', text: theme ? `It’s in Appearance with your other themes.${host.theme.value.theme === job.id ? ' You’re wearing it.' : ''}` : kind === 'app' ? 'It’s in Apps, and runs on your server.' : `It’s on, and in Settings › ${home}.` })
        : entry?.description ? h('p', { class: 'market-install-desc', text: entry.description }) : null,
      !done ? h('section', { class: 'market-install-touches', 'aria-label': update ? 'Added in this version' : 'Before you say yes' },
        h('h3', { class: 'market-facts-title', text: update ? (Object.keys(p.added ?? {}).length ? 'New in this version' : 'Nothing new in what it touches') : 'Before you say yes' }),
        job.phase === 'reading' ? h('p', { class: 'note market-reading', role: 'status' }, h('span', { class: 'market-pulse', 'aria-hidden': 'true' }), 'Reading what it declares, from its own link…')
          : p ? touchList(lines) : null) : null,
      p?.installable === false ? h('p', { class: 'note market-why', text: `${p.why}. It is listed so you can see it.` }) : null,
      job.error ? h('p', { class: 'plugin-notice err', role: 'alert', text: job.error }) : null,
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
        : [button('Not now', closeInstall, 'pillbtn', { disabled: job.phase === 'installing' }),
          p?.installable === false ? null : button(job.phase === 'installing' ? (theme ? 'Getting…' : 'Installing…') : update ? 'Update' : theme ? 'Get it' : 'Install', confirmInstall, 'pillbtn primary', { disabled: job.phase !== 'ready' || !p?.confirm })]))])
    const surface = menuSurface(card, { title: `${verb} ${name}`, close: () => { if (job.phase !== 'installing') closeInstall() }, heading: false, centered: host.state.wide, width: 480, initialFocus: 'heading' })
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
  // "Find more": the last row of Plugins and of Appearance's themes.
  function findMore(kind, title) {
    return h('div', { class: 'rows market-find-more', key: `find-more:${kind}` }, h('button', { type: 'button', class: 'setrow', onclick: () => openMarket({ kind }) },
      h('div', { class: 'icon-tile' }, icon.spark(16)),
      h('div', { class: 'setrow-who' }, h('div', { class: 'setrow-title', text: title }), h('div', { class: 'setrow-sub', text: `${KIND_MANY[kind]} in the Market, each read by hand` })),
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
    const shown = refs.map(ref => ({ ref, entry: ready ? entryOf(ref.id) : null }))
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
      tile(entry, 'md'),
      h('div', { class: 'market-card-words' },
        h('p', { class: 'market-card-kicker' }, icon.spark(11), `Market · ${KIND_ONE[kind]}`),
        h('button', { type: 'button', class: 'market-card-name', onclick: openIt, 'aria-label': `About ${entry.name}` }, entry.name),
        entry.description ? h('span', { class: 'market-card-line', text: entry.description }) : null,
        h('span', { class: 'market-card-by' }, h('span', { class: 'market-card-maker', text: entry.author }),
          r ? h('span', { class: `market-reach market-reach-${r.tone}` }, h('i', { 'aria-hidden': 'true' }), r.text) : null)),
      h('div', { class: 'market-card-acts' }, act))
  }

  return { marketCards: cardsFor, marketState: st, openMarket, openMarketInstall: openInstall, marketNodes, marketInstallNodes: installNodes, marketSync: sync, marketDoor: settingsDoor, marketFindMore: findMore, loadMarket: load }
}
