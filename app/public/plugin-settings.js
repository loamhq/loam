import { h, icon } from './ui.js'
import { createSecretSettings } from './plugin-secrets.js'
import { pluginGallery } from './plugin-gallery.js'
import { createThemeShowcase } from './theme-showcase.js'
import { themePreview } from './theme-preview.js'
import { themeImageURL, themeImagePosition } from './theme-image.js'
import { listingAddress, personalUpdate, skippedUpdate } from './market-card.js'
import { settingsRow } from './chat-ui.js'
import { modelLabel } from './providers.js'
const label = id => ({ 'core.message': 'the message bubble', 'core.thread-list': 'the thread list', 'core.composer': 'the composer', 'core.files.viewer': 'the Files viewer', 'core.files.editor': 'the Files editor', 'core.rail': 'the navigation rail', 'core.activity': 'Activity', 'core.apps': 'Apps', 'core.settings': 'Settings' }[id] ?? id)
const ORIGINS = { loam: 'Built into Loam', market: 'From the Market', yours: 'Made by you', link: 'From a link' }
const UNREVIEWED = 'Unreviewed by Loam'

export function pluginOrigin(row, entry) {
  const source = row.source
  if (row.layer === 'shipped' || source?.kind === 'loam') return 'loam'
  if (source?.kind !== 'link') return 'yours'
  if (source.via === 'market') return 'market'
  if (entry?.id !== row.id || !source.repo || source.repo.toLowerCase() !== entry.repo?.toLowerCase()) return 'link'
  const folder = source.path ?? ''
  const listedFolder = entry.pin?.path ?? entry.path
  const sameRelease = entry.ref && [source.ref, source.commit].includes(entry.ref) && (!listedFolder || folder === listedFolder)
  const samePin = entry.pin?.ref && source.commit === entry.pin.ref && folder === (listedFolder ?? '')
  return sameRelease || samePin ? 'market' : 'link'
}
export function touchSentences(declaration = {}) {
  const touches = declaration.touches ?? {}, lines = []
  if (declaration.page_code) lines.push('Runs code in the page.')
  for (const network of touches.network ?? []) lines.push(network.host === 'browser-managed' ? `Uses the browser’s speech service: ${network.why}` : `Contacts ${network.host}: ${network.why}`)
  if (touches.record && touches.record !== 'none') lines.push(touches.record === 'read' ? 'Reads conversation records.' : 'Reads and changes conversation records.')
  if (touches.memory && touches.memory !== 'none') lines.push(touches.memory === 'read' ? 'Reads shared memory.' : 'Reads and changes shared memory.')
  const facts = { agents: 'agent names and workspace file counts', projects: 'project folders and their file counts', files: 'the number of personal files', chats: 'chat titles, dates and lengths', apps: 'apps made here and their recent opens', 'local-plugins': 'plugins made here', installation: 'the starter date', 'public-work': 'public Market listings, reports and merged contributions for your chosen GitHub account' }
  for (const group of touches.facts ?? []) lines.push(`Reads ${facts[group] ?? group}.`)
  if (touches.server) lines.push('Contains code for the host.')
  // Shared keys (2026-11-18): the providers whose keys it is given, from
  // Settings › Connections › API keys. A plugin is handed no key it did not declare here.
  for (const key of touches.keys ?? []) lines.push(`Uses your ${key.name} key${key.optional ? ' if you have one' : ''}${key.why ? `: ${key.why}` : '.'}`)
  for (const id of declaration.overrides ?? []) lines.push(`Replaces ${label(id)}.`)
  return lines
}
// A plugin's guide (the body of its PLUGIN.md) in two parts (2026-11-19):
// the plain words a person reads first, and, after a rule (`---`) or a
// `## Technical details` heading, what a builder wants, kept at the foot of
// the page under Details. The owner, 11-19: "at the beginning, it should be
// more non-technical, with more technical details at the bottom."
export function guideParts(text) {
  const src = String(text ?? '').replace(/\r\n/g, '\n').trim()
  if (!src) return { plain: '', technical: '' }
  const lines = src.split('\n')
  let fence = false, at = -1
  for (let i = 0; i < lines.length; i++) {
    if (/^\s*(```|~~~)/.test(lines[i])) fence = !fence
    if (fence) continue
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(lines[i]) || /^#{1,3}\s+technical details\s*$/i.test(lines[i])) { at = i; break }
  }
  if (at < 0) return { plain: src, technical: '' }
  const technical = lines.slice(at + 1).join('\n').replace(/^\s*#{1,3}\s+technical details\s*\n/i, '').trim()
  return { plain: lines.slice(0, at).join('\n').trim(), technical }
}
// A guide's first heading that only repeats the plugin's name is not drawn.
const withoutTitle = (text, name) => text.replace(/^#\s+(.+)\n+/, (all, title) => title.trim().toLowerCase() === String(name ?? '').trim().toLowerCase() ? '' : all)
const SERVICE = { running: 'Running', starting: 'Starting', failing: 'Failing', stopping: 'Stopping', stopped: 'Stopped' }
const external = source => { try { const u = new URL(source?.url); return u.protocol === 'https:' ? u.href : null } catch { return null } }
export function createPluginSettings({ loader, theme, host, changed = () => {} }) {
  const st = { keys: null, how: {}, selected: null, detail: null, detailError: '', opening: false, remove: false, error: '', done: '', guide: false, focusDetail: false, restarting: null, serviceError: null, logs: new Set(), advanced: null }
  let hostSettings = null, secretSettings = null, reviewer = null, reviewerRequest = null
  const showcase = createThemeShowcase({ schedule: () => changed(), mode: () => theme.current === 'dark' ? 'dark' : 'light' })
  const failedShots = new Set()
  const failedIcons = new Set()
  const button = (text, onclick, cls = 'pillbtn', props = {}) => h('button', { type: 'button', class: cls, text, onclick, ...props })
  const notice = (text, bad = true) => text ? h('p', { class: `plugin-notice${bad ? ' err' : ''}`, role: bad ? 'alert' : 'status', text }) : null
  let updates = null, checking = null, updateWrite = null, reviewAnswer = null
  async function checkUpdates() {
    if (checking) return checking
    checking = host.api('GET', '/plugin-updates').then(async out => {
      updates = out
      if (out.adopted?.length) {
        reviewAnswer = null
        await loader.refresh()
      }
    }).catch(error => {
      if (error.status !== 404) st.error = error.message
    }).finally(() => { checking = null; changed() })
    return checking
  }
  const marketEntry = row => updates?.entries?.[row.id] ?? host.marketState?.list?.plugins?.find(entry => entry.id === row.id)
  const reviewKnown = () => ['listed', 'empty'].includes(updates?.market_state ?? host.marketState?.list?.state)
  const status = row => {
    const info = updates?.plugins?.[row.id]
    return info?.personal || (info?.version && info.version !== (row.installed?.version ?? row.version)) ? info : null
  }
  const originOf = row => row.source?.kind === 'link' && updates?.plugins?.[row.id]?.via === 'market'
    ? 'market' : pluginOrigin(row, marketEntry(row))
  function updateLine(row) {
    const info = status(row)
    if (info?.personal) return personalUpdate(info.version)
    const words = []
    if (row.updates_paused) words.push('Changed here · updates paused')
    if (info) words.push(info.held ? 'Update needs your yes' : 'Update available', info.version)
    return words.join(' · ')
  }
  async function setAuto(row) {
    if (updateWrite) return
    const on = !row.auto_update
    updateWrite = row.key; st.error = ''; changed()
    try {
      const out = await host.api('PATCH', `/plugins/${encodeURIComponent(row.id)}`,
        { auto_update: on })
      loader.st.rows = loader.st.rows.map(current => current.key === row.key ? { ...current, ...out.plugin } : current)
    } catch (error) { st.error = error.message }
    finally { updateWrite = null; changed() }
  }
  function updateControls(row) {
    if (typeof row.auto_update !== 'boolean') return null
    const info = status(row), words = updateLine(row)
    if (row.edited || row.custom_copy || row.personal || info?.personal) {
      async function decide(review) {
        if (updateWrite) return
        updateWrite = row.key; st.error = ''; changed()
        try {
          const out = await host.api('PATCH', `/plugins/${encodeURIComponent(row.id)}`, { review, version: info.version })
          if (out.adopted) { reviewAnswer = null; await loader.refresh() }
          else reviewAnswer = { key: row.key, version: info.version, text: out.skipped ? skippedUpdate(out.skipped) : 'Your agent has the update.' }
          await checkUpdates()
        } catch (error) { st.error = error.message }
        finally { updateWrite = null; changed() }
      }
      const answer = reviewAnswer?.key === row.key && (!info || info.version === reviewAnswer.version) ? reviewAnswer.text : null
      return h('section', { class: 'rows plugin-updates', 'aria-label': 'Plugin updates' },
        h('div', { class: 'setrow' }, h('span', { class: 'setrow-who', role: 'status', text: answer || (info ? words : 'Your changes are kept when Loam checks for updates.') })),
        info?.personal ? h('div', { class: 'setrow' },
          button('Keep my version', () => decide('skip'), 'pillbtn', { disabled: Boolean(updateWrite) }),
          button('Ask my agent', () => decide('ask'), 'pillbtn primary', { disabled: Boolean(updateWrite) })) : null)
    }
    const review = () => info.via === 'market'
      ? host.openMarketInstall(row.id, { ...marketEntry(row), id: row.id, name: row.name, kind: row.kind, pinned_link: info.link })
      : host.openLinkInstall({ link: info.link })
    return h('section', { class: 'rows plugin-updates', 'aria-label': 'Plugin updates' },
      info || words ? h('div', { class: 'setrow' }, h('span', { class: 'setrow-who', text: words }),
        info ? button(info.held ? 'Review update' : 'Update', review, 'pillbtn content-mark') : null) : null,
      h('div', { class: 'setrow' }, h('div', { class: 'setrow-who' },
        h('div', { class: 'setrow-title', text: 'Update automatically' }),
        h('div', { class: 'setrow-sub', text: row.custom_copy ? 'Updates apply to the original. Your copy stays yours.' : 'Updates that ask for more access wait for your yes.' })),
        h('button', { type: 'button', class: 'schedule-switch', role: 'switch', 'aria-label': 'Update automatically',
          'aria-checked': String(row.auto_update), disabled: updateWrite === row.key, onclick: () => setAuto(row) }, h('span'))))
  }
  const originText = origin => origin === 'link' && reviewKnown() ? `${ORIGINS.link} · ${UNREVIEWED}` : ORIGINS[origin]
  const meta = (row, origin) => [row.author, row.version, originText(origin)].filter(Boolean).join(' · ')
  const declarations = (declaration, title = 'What it touches') => h('section', { class: 'plugin-declaration', 'aria-label': title }, h('h3', { class: 'kicker', text: title }),
    touchSentences(declaration).length ? h('ul', {}, ...touchSentences(declaration).map(text => h('li', { text }))) : h('p', { class: 'note', text: 'Declares no network, record or memory access.' }))
  function opened() {
    const row = loader.st.rows.find(row => row.key === st.selected?.key)
    if (row?.on) host.registry?.emit('plugin.settings.opened', { id: row.id })
  }
  function clear() {
    st.selected = null; st.detail = null; st.error = ''
    changed()
  }
  const select = row => host.appModalHistory ? host.goSettings('plugins', row.id) : open(row)
  async function open(row) {
    hostSettings = null; secretSettings = null; reviewAnswer = null
    reviewer = null; reviewerRequest = null
    st.selected = row; st.detail = null; st.detailError = ''; st.opening = true; st.guide = false; st.remove = false; st.error = ''; st.focusDetail = true; changed()
    opened()
    if (row.id === 'reviewer' && row.effective && host.openReviewerSettings) {
      const request = reviewerRequest = host.api('GET', '/system/reviewer')
      request.then(out => { if (reviewerRequest === request) { reviewer = out; changed() } }).catch(() => {})
    }
    // Whether its keys are saved, for whether it is set up (below).
    if (row.keys?.length) host.api('GET', '/keys').then(out => { st.keys = out?.providers ?? null; changed() }).catch(() => {})
    try {
      const out = await host.api('GET', `/plugins/${encodeURIComponent(row.id)}`)
      if (st.selected?.key === row.key) {
        const sameCopy = copy => copy?.layer === row.layer && copy?.folder === row.folder
        const plugin = !out.rows || sameCopy(out.plugin) ? out.plugin : out.rows.find(sameCopy)
        st.detail = { plugin }
        // A plugin's own settings (`settings.entry`): until 0.2.1 only Daily
        // notes' were read and shown, so no other plugin's could be changed
        // here (the 0.2.0 walk: Comments & suggestions' `reach`).
        if (row.id === 'daily-notes' || (plugin?.contributes || row.contributes)?.['settings.entry']?.length) {
          try { const settings = await host.api('GET', `/plugins/${encodeURIComponent(row.id)}/settings`); if (st.selected?.key === row.key) hostSettings = settings }
          catch (e) { if (st.selected?.key === row.key) st.detailError = e.message }
        }
        // A plugin that declares `keys:` takes them from API keys (2026-11-18);
        // its `settings.secret` of the same name stays only so a broker from
        // before 0.2.3 still hands it the key, and is not drawn here.
        if (st.selected?.key === row.key && !(plugin?.keys || row.keys)?.length && (plugin?.contributes || row.contributes)?.['settings.secret']?.length) {
          secretSettings = createSecretSettings({ id: row.id, request: host.api, redraw: changed })
        }
        if (plugin) loader.st.rows = loader.st.rows.map(current => current.key === row.key ? { ...current, ...plugin, key: current.key } : current)
      }
    }
    catch (error) { if (st.selected?.key === row.key) st.detailError = error.message }
    finally { if (st.selected?.key === row.key) st.opening = false; changed() }
  }
  async function toggle(row) {
    st.error = ''; changed()
    try {
      await loader.toggle(row.id, !row.on)
      if (st.selected?.id === row.id && loader.st.rows.some(current => current.id === row.id && current.effective && current.on)) host.registry?.emit('plugin.settings.opened', { id: row.id })
    } catch (error) { st.error = error.message }
    changed()
  }
  async function remove(row) {
    st.error = ''; changed()
    try { const out = await loader.remove(row.id); if (out.appearance_reset?.length) await theme.refresh(); st.selected = null; st.remove = false; st.done = `${row.name || row.id} removed.` }
    catch (error) { st.error = error.message }
    changed()
  }
  async function restart(row) {
    if (st.restarting) return
    st.restarting = row.id; st.serviceError = null; changed()
    try {
      const out = await host.api('POST', `/plugins/${encodeURIComponent(row.id)}/restart`, {})
      if (st.selected?.id === row.id) st.detail = out
      await loader.refresh()
    } catch (error) { st.serviceError = { id: row.id, message: error.message } }
    finally { st.restarting = null; changed() }
  }
  function serviceView(row) {
    const service = row.service
    if (!service) return null
    const log = service.log ?? row.log
    return h('div', { class: 'plugin-service' },
      h('p', { class: 'note plugin-service-state', text: `Server · ${SERVICE[service.state] ?? service.state}` }),
      service.state === 'failing' ? h('div', { class: 'plugin-service-failure' },
        h('details', { key: `log:${row.key}`, open: st.logs.has(row.key), ontoggle: event => { if (event.currentTarget.isConnected) event.currentTarget.open ? st.logs.add(row.key) : st.logs.delete(row.key) } },
          h('summary', { text: 'Recent log' }), h('pre', { text: Array.isArray(log) ? log.join('\n') : log || 'No log lines available.' })),
        button(st.restarting === row.id ? 'Restarting…' : 'Restart', () => restart(row), 'pillbtn content-mark', { disabled: st.restarting !== null, 'aria-label': `Restart ${row.name || row.id}` })) : null,
      st.serviceError?.id === row.id ? notice(st.serviceError.message) : null)
  }
  // The row's files URL belongs to the broker. Browser assets use the app's relay.
  const letterTile = row => h('span', { class: 'plugin-icon plugin-icon-letter', 'aria-hidden': 'true', text: (row.name || row.id || '?').trim().charAt(0).toUpperCase() })
  const pluginIcon = row => {
    if (row.kind?.includes?.('theme') && !row.icon) return null
    if (!row.icon) return letterTile(row)
    const src = `/api/plugins/${encodeURIComponent(row.id)}/files/${row.icon.replace(/^\//, '').split('/').map(encodeURIComponent).join('/')}`
    if (failedIcons.has(src)) return letterTile(row)
    return h('img', { class: 'plugin-icon', src, alt: '', decoding: 'async',
      onerror: event => { if (event.currentTarget.isConnected) { failedIcons.add(src); changed() } } })
  }
  const pluginSwitch = row => row.switch === 'plugin' && !row.broken
    ? h('button', { type: 'button', class: 'schedule-switch plugin-switch', role: 'switch', 'aria-checked': String(Boolean(row.on)), 'aria-label': `Enable ${row.name || row.id}`, disabled: loader.st.writing.has(`/plugins/${row.id}`), onclick: () => toggle(row) }, h('span', { 'aria-hidden': 'true' }))
    : null
  function rowNotices(row) {
    const overrides = [...new Set([...(row.overrides ?? []), ...(host.registry?.overrides(row.id) ?? [])])]
    const broken = row.broken || loader.st.failures.get(row.id)
    const update = updateLine(row)
    return [
      update ? h('span', { class: 'plugin-update-line', text: update }) : null,
      row.shadows ? h('span', { class: 'plugin-mark', text: 'Changes a shipped plugin' }) : null,
      overrides.length ? h('span', { class: 'plugin-mark', text: `Replaces ${overrides.map(label).join(', ')}` }) : null,
      broken ? h('span', { class: 'plugin-row-error', text: broken }) : row.warnings?.length ? h('span', { class: 'plugin-row-warning', text: `${row.warnings.length} ${row.warnings.length === 1 ? 'warning' : 'warnings'}` }) : null,
    ]
  }
  function rowView(row, origin) {
    const unreviewed = origin === 'link' && reviewKnown()
    const control = pluginSwitch(row) ?? (row.switch === 'listed' ? null : h('span', { class: 'plugin-state', text: 'Unavailable' }))
    return h('div', { class: 'plugin-list-item', key: row.key }, h('div', { class: 'plugin-list-row' },
      h('button', { type: 'button', class: 'setrow plugin-row', onclick: () => select(row), 'aria-label': `About ${row.name || row.id}`, 'aria-description': meta(row, origin) },
        pluginIcon(row),
        h('div', { class: 'setrow-who' }, h('div', { class: 'setrow-title', text: row.name || row.id }), h('div', { class: 'setrow-sub', text: row.description || (row.on ? 'Enabled' : row.id === 'git-backup' ? 'Off-box backup is off' : 'Disabled') }),
          // Backup off is a state worth saying under its promise (f8d860d).
          row.description && row.id === 'git-backup' && !row.on ? h('span', { class: 'plugin-mark plugin-mark-warn', text: 'Off-box backup is off' }) : null,
          unreviewed ? h('div', { class: 'plugin-row-origin', text: UNREVIEWED }) : null,
          rowNotices(row)),
        icon.chevronRight(12)), control),
      row.on && row.service && row.service.state !== 'running' ? serviceView(row) : null)
  }
  const failedCovers = new Set()
  function themeCover(row) {
    const mode = theme.current, src = themeImageURL(row, mode)
    if (!src || failedCovers.has(src)) return null
    return h('img', { class: 'theme-cover', src, alt: '', loading: 'lazy', decoding: 'async',
      width: 640, height: 427, style: { objectPosition: themeImagePosition(row, mode) },
      onerror: () => { failedCovers.add(src); changed() } })
  }
  function themeCard(row) {
    const name = row.name || row.id, origin = originOf(row)
    const inUse = row.effective && theme.value.theme === row.id
    const state = row.broken ? 'Unavailable' : inUse ? 'In use' : 'Not in use'
    return h('div', { class: 'plugin-theme-card', key: row.key },
      h('button', { type: 'button', class: 'plugin-theme-about', onclick: () => select(row),
        'aria-label': `About ${name}`, 'aria-description': `${meta(row, origin)} · ${state}` },
        themePreview(row.id, {}, themeCover(row)),
        h('span', { class: 'plugin-theme-label' },
          h('span', { class: 'setrow-title', text: name }), icon.chevronRight(12)),
        h('span', { class: 'plugin-theme-origin', text: originText(origin) }),
        inUse ? h('span', { class: 'plugin-theme-state' }, icon.check(12), state) : null,
        rowNotices(row)),
      pluginSwitch(row),
      row.on && row.service && row.service.state !== 'running' ? serviceView(row) : null)
  }
  // Each `settings.entry` of a plugin that is on: a choice as a menu, text as
  // a field, each saved by itself (PATCH /plugins/<id>/settings).
  async function saveEntry(row, key, value) {
    st.error = ''; st.done = ''; changed()
    try {
      const out = await host.api('PATCH', `/plugins/${encodeURIComponent(row.id)}/settings`, { values: { [key]: value } })
      if (hostSettings) hostSettings = { ...hostSettings, values: { ...hostSettings.values, ...out.values } }
      st.done = 'Saved.'
    } catch (e) { st.error = e.message }
    changed()
  }
  // A choice shows its words (`labels`, 2026-11-18), a `note` sits under its
  // control, and `advanced` entries wait behind a quiet Advanced.
  function entryRow(row, entry) {
    const id = `plugin-setting-${row.id}-${entry.key}`, value = hostSettings.values?.[entry.key] ?? entry.default ?? ''
    const noteId = entry.note ? `${id}-note` : null
    const control = entry.choices?.length
      ? h('select', { id, class: 'field-input', value: String(value), ...(noteId ? { 'aria-describedby': noteId } : {}), onchange: e => saveEntry(row, entry.key, e.currentTarget.value) },
        ...entry.choices.map(c => h('option', { value: c, text: entry.labels?.[c] ?? c, selected: c === value })))
      : h('input', { id, class: 'field-input', value: String(value), ...(noteId ? { 'aria-describedby': noteId } : {}), onchange: e => saveEntry(row, entry.key, e.currentTarget.value) })
    return h('div', { class: 'setrow plugin-entry', key: entry.key }, h('label', { class: 'setrow-who', for: id }, h('div', { class: 'setrow-title', text: entry.label })), control,
      entry.note ? h('p', { class: 'note plugin-entry-note', id: noteId, text: entry.note }) : null)
  }
  function entrySettings(row) {
    const entries = hostSettings?.entries ?? []
    if (!entries.length) return null
    const plain = entries.filter(entry => !entry.advanced), advanced = entries.filter(entry => entry.advanced)
    return h('section', { class: 'plugin-entry-settings rows', 'aria-label': `${row.name || row.id} settings` },
      ...plain.map(entry => entryRow(row, entry)),
      advanced.length ? h('details', { class: 'plugin-entry-advanced', open: st.advanced === row.id, ontoggle: event => { st.advanced = event.currentTarget.open ? row.id : null } },
        h('summary', { text: 'Advanced' }), ...advanced.map(entry => entryRow(row, entry))) : null,
      notice(st.done, false))
  }
  // What a plugin looks like: a theme worn (its preview, from its installed
  // tokens), else its screenshots, else nothing: its icon is already beside
  // its name.
  function picture(row) {
    if (row.kind?.includes?.('theme')) {
      const cover = themeCover(row)
      const installed = row.effective && !row.broken && theme.installed.find(item => item.id === row.id)
      const value = theme.value, status = theme.status
      const settings = installed ? { mode: value.mode, options: value.options?.[row.id], set: theme.set } : undefined
      return [cover ? h('div', { class: 'plugin-theme-cover' }, cover) : null,
        showcase.view({ key: `plugin-${row.id}`, look: row.id, name: row.name || row.id,
          options: (installed || row).options ?? [], palette: row.palette ?? [], from: 'installed', settings }),
        settings && status.error ? h('div', { class: 'appearance-status err', role: 'status' }, status.error,
          button('Try again', () => status.saving ? theme.save() : theme.refresh(), 'quietlink')) : null]
    }
    const file = rel => `/api/plugins/${encodeURIComponent(row.id)}/files/${String(rel).split('/').map(encodeURIComponent).join('/')}`
    const shots = (row.screenshots ?? []).map(sh => ({ caption: sh.caption, src: file(sh.src), src_dark: sh.src_dark ? file(sh.src_dark) : null }))
    const gallery = pluginGallery({ shots, dark: theme.current === 'dark', name: row.name || row.id, key: `gallery:${row.id}`, failed: failedShots, redraw: changed })
    return gallery
  }
  function details(row, declaration, technical, url) {
    const who = row.author || (row.source?.kind === 'loam' ? 'Loam' : null)
    const fact = (label, value) => value ? h('div', {}, h('dt', { text: label }), h('dd', {}, value)) : null
    return h('section', { class: 'plugin-details', 'aria-labelledby': `plugin-details-${row.id}` },
      h('h3', { class: 'kicker plugin-details-title', id: `plugin-details-${row.id}`, text: 'Details' }),
      declarations(declaration),
      h('dl', { class: 'plugin-facts' },
        fact('Version', row.version || 'Unknown'),
        originOf(row) !== 'market' ? fact('Made by', who) : null,
        row.on && row.service ? fact('Its server', `${SERVICE[row.service.state] ?? row.service.state}`) : null,
        url ? fact('Source', h('a', { class: 'quietlink plugin-source', href: url, target: '_blank', rel: 'noopener noreferrer' }, 'View source', icon.external(12))) : null),
      technical ? h('details', { class: 'plugin-guide', open: st.guide, ontoggle: event => { st.guide = event.currentTarget.open } }, h('summary', { text: 'Technical details' }),
        h('div', { class: 'md' }, ...[host.markdown?.(technical, { soft: true }) ?? h('p', { text: technical })].flat())) : null)
  }
  function aboutOrigin(row) {
    const line = (...words) => h('p', { class: 'plugin-about-origin' }, ...words)
    const origin = originOf(row)
    if (origin === 'market') {
      const author = marketEntry(row)?.author || row.author
      return line(h('a', { class: 'quietlink', href: listingAddress('', row.id), onclick: event => {
        if (!host.openMarket || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
        event.preventDefault()
        host.openMarket({ id: row.id, from: { label: 'Plugins' }, back: { page: 'plugins', provider: row.id } })
      } }, ORIGINS.market), author ? ` · By ${author}` : '')
    }
    if (origin === 'yours' && row.folder) return line(`${ORIGINS.yours} · ${row.folder}`)
    return line(originText(origin))
  }
  // Set up: on, its keys saved (every one it needs, or any one of keys it
  // can choose between), and, when entries are marked `setup` (Git backup's
  // repositories), at least one of them given. Until its own answers arrive,
  // a plugin is taken as set up.
  function setUp(row) {
    if (!row.on) return false
    if (row.keys?.length && Array.isArray(st.keys)) {
      const saved = k => st.keys.some(p => p.id === k.provider && p.set)
      const needed = row.keys.filter(k => !k.optional)
      const worksWithoutKeys = host.registry?.entries('settings.section').some(entry => entry.owner === row.id && entry.worksWithoutKeys)
      if (needed.length ? !needed.every(saved) : !worksWithoutKeys && !row.keys.some(saved)) return false
    }
    const setup = (hostSettings?.entries ?? []).filter(e => e.setup)
    if (setup.length && setup.every(e => !String(hostSettings.values?.[e.key] ?? '').trim())) return false
    return true
  }
  function detail() {
    const original = st.selected, current = loader.st.rows.find(row => row.key === original.key) ?? original
    const row = current
    const declaration = row.declaration ?? { touches: row.touches, overrides: row.overrides, page_code: row.has?.page_main }
    const url = external(row.source)
    const guide = guideParts(st.detail?.plugin?.guide), plain = withoutTitle(guide.plain, row.name || row.id)
    // A plugin's page (2026-11-19): its promise first. A plugin that is on
    // and set up leads with its controls, since that is what one comes back
    // for (Git backup's status and Back up now), and its pictures and plain
    // words wait in a closed "How it works" under them; one that is off or
    // not yet set up opens with them, above its switch. Details, quietly, at
    // the foot. A theme leads with its preview, which is the point of it.
    const theme = Boolean(row.kind?.includes?.('theme')), ready = theme || setUp(row)
    const shots = theme ? null : picture(row)
    const how = shots || plain ? h('details', { class: 'plugin-how', key: `how:${row.id}`, open: st.how[row.id] ?? !ready,
      ontoggle: event => { if (event.currentTarget.isConnected) st.how[row.id] = event.currentTarget.open } },
      h('summary', { class: 'plugin-how-summary' }, h('span', { text: 'How it works' }), icon.chevronDown(14)),
      shots, plain ? h('div', { class: 'md plugin-about' }, ...[host.markdown?.(plain, { soft: true }) ?? h('p', { text: plain })].flat()) : null) : null
    const back = { class: 'backbtn plugin-back', 'aria-label': 'Back to Plugins' }
    const runsOn = reviewer?.effective
    const reviewerProvider = host.state?.providers?.find(p => p.id === runsOn?.provider)
    return [host.appModalHistory ? host.settingsBack('Plugins', 'plugins', back) : h('button', { ...back, onclick: clear }, icon.chevronLeft(18), 'Plugins'),
      h('header', { class: 'plugin-hero' }, pluginIcon(row),
        h('div', { class: 'plugin-hero-words' }, host.settingsHead(row.name || row.id),
          aboutOrigin(row), row.description ? h('p', { class: 'plugin-promise', text: row.description }) : null)),
      notice(row.broken), notice(loader.st.failures.get(row.id)),
      row.service && row.on && row.service.state !== 'running' ? serviceView(row) : null,
      theme ? picture(row) : null,
      ready ? null : how,
      row.shadows ? h('p', { class: 'plugin-mark', text: 'Changes a shipped plugin.' }) : null,
      updateControls(row),
      notice(st.error), notice(st.detailError),
      st.opening ? h('p', { class: 'note', role: 'status', text: 'Reading plugin details…' }) : null,
      row.switch === 'plugin' && row.id !== 'daily-notes' && !row.broken ? h('div', { class: 'rows plugin-enable' }, h('div', { class: 'setrow' },
        h('div', { class: 'setrow-who' },
          h('div', { class: 'setrow-title', text: row.on ? 'On' : row.id === 'git-backup' ? 'Off-box backup is off' : 'Off' }),
          h('div', { class: 'setrow-sub', text: row.on ? 'Settings are kept when you turn this off.' : row.id === 'git-backup' ? 'Nothing is backed up off this machine yet. Turn on Git backup to back up your vault and agents to private repositories.' : 'Turn on to use this plugin and its settings.' })),
        h('button', { class: 'schedule-switch', role: 'switch', 'aria-label': `Enable ${row.name || row.id}`, 'aria-checked': String(Boolean(row.on)), disabled: st.opening || loader.st.writing.has(`/plugins/${row.id}`), onclick: () => toggle(row) }, h('span')))) : null,
      row.id === 'daily-notes' ? h('section', { class: 'plugin-host-settings', 'aria-label': 'Daily notes settings' },
        h('div', { class: 'profile-choice' }, h('span', { text: 'Daily notes' }), h('button', { class: 'schedule-switch', role: 'switch', 'aria-label': 'Enable daily notes', 'aria-checked': String(Boolean(row.on)), disabled: loader.st.writing.has(`/plugins/${row.id}`), onclick: () => toggle(row) }, h('span'))),
        hostSettings ? h('form', { onsubmit: async e => {
          e.preventDefault(); st.error = ''; changed()
          try { const values = { folder: e.currentTarget.querySelector('input').value }; hostSettings = await host.api('PATCH', `/plugins/${encodeURIComponent(row.id)}/settings`, { values }); st.done = 'Folder saved.' }
          catch (e) { st.error = e.message }
          changed()
        } }, h('label', { for: 'daily-note-folder', text: 'Daily notes folder' }), h('input', { id: 'daily-note-folder', class: 'field-input', value: hostSettings.values.folder, disabled: !row.on }), h('p', { class: 'note', text: 'Daily notes use YYYY-MM-DD.md in this vault folder.' }), h('button', { class: 'pillbtn', type: 'submit', disabled: !row.on, text: 'Save folder' })) : null, notice(st.done, false)) : null,
      row.id === 'reviewer' && row.effective && host.openReviewerSettings ? h('div', { class: 'rows' },
        settingsRow({ title: 'Runs on', sub: runsOn?.provider
          ? [reviewerProvider?.name || runsOn.provider, modelLabel(reviewerProvider, runsOn.model).name || 'Default model'].join(' · ')
          : runsOn ? 'No provider connected' : 'Nightly reviewer settings', onclick: host.openReviewerSettings })) : null,
      ...(row.on ? (host.registry?.entries('settings.section') ?? []).filter(entry => entry.plugin === row.id || (entry.group === 'plugin' && entry.owner === row.id))
        .map(entry => h('section', { class: 'plugin-preferences', key: `${entry.owner}:${entry.id}`, 'aria-label': entry.title }, host.registry.invoke(entry, 'render', { plugin: true, back: false }))) : []),
      // A plugin whose own section draws its settings (`entries: 'own'`, Git
      // backup) is not given the plain form of them as well.
      row.on && row.id !== 'daily-notes' && !(host.registry?.entries('settings.section') ?? []).some(entry => entry.entries === 'own' && (entry.plugin === row.id || entry.owner === row.id)) ? entrySettings(row) : null,
      row.on ? secretSettings?.view() : null,
      row.keys?.length ? h('section', { class: 'plugin-keys', 'aria-label': 'Keys' }, h('h3', { class: 'kicker', text: 'Keys' }),
        h('p', { class: 'note', text: row.keys.every(k => k.optional) && row.keys.length > 1
          ? `Uses your ${row.keys.map(k => k.name).join(' or ')} key, whichever you have. Keys are saved under API keys, in Connections.`
          : `Uses your ${row.keys.map(k => k.name).join(' and ')} ${row.keys.length > 1 ? 'keys' : 'key'}, saved under API keys in Connections.` }),
        host.goSettings ? button('Open API keys', () => row.keys.length === 1 ? host.goSettings('api-key', row.keys[0].provider) : host.goSettings('api-keys'), 'pillbtn content-mark') : null) : null,
      loader.st.failures.has(row.id) ? button('Try again', () => loader.refresh(), 'pillbtn content-mark') : null,
      ready ? how : null,
      details(row, declaration, guide.technical, url),
      row.layer === 'yours' ? (st.remove ? h('div', { class: 'plugin-remove-question' }, h('p', { text: `Remove ${row.name || row.id}?${row.shadows ? ' The shipped copy will take over.' : ''}` }),
        button('Keep plugin', () => { st.remove = false; changed() }, 'pillbtn content-mark'), button('Remove', () => remove(row), 'pillbtn primary danger', { disabled: loader.st.writing.has(`/plugins/${row.id}`) })) : button('Remove plugin', () => { st.remove = true; changed() }, 'pillbtn content-mark danger')) : null]
  }
  function view({ back = true } = {}) {
    if (st.selected && loader.st.loaded && !loader.st.rows.some(row => row.key === st.selected.key)) { st.selected = null; st.detail = null; st.done = 'This copy was removed.' }
    const rows = loader.st.rows.filter(row => row.effective || row.broken)
    if (st.selected) return detail()
    const themes = [], groups = new Map(Object.keys(ORIGINS).map(origin => [origin, []]))
    for (const row of rows) {
      if (row.kind?.includes('theme')) themes.push(row)
      else groups.get(originOf(row)).push(row)
    }
    const group = (title, content) => h('section', { class: 'plugin-group', 'aria-label': title, key: title },
      h('h3', { class: 'kicker', text: title }), content)
    return [back ? host.settingsBack('Settings', 'root') : null, host.settingsHead('Plugins'), notice(st.error), notice(st.done, false), notice(loader.st.error),
      !loader.st.loaded ? h('p', { class: 'note', role: 'status', text: 'Reading plugins…' }) : null,
      themes.length ? group('Themes', h('div', { class: 'plugin-theme-grid' }, ...themes.map(themeCard))) : null,
      [...groups.values()].some(rows => rows.length) ? group('Extensions', h('div', { class: 'plugin-origin-groups' },
        ...Array.from(groups, ([origin, rows]) => rows.length ? h('section', { class: 'plugin-origin-group', 'aria-labelledby': `plugin-origin-${origin}`, key: origin },
          h('h4', { class: 'plugin-origin-title', id: `plugin-origin-${origin}`, text: ORIGINS[origin] }),
          h('div', { class: 'rows' }, ...rows.map(row => rowView(row, origin)))) : null))) : null,
      // Settings › Market (2026-11-18) holds what can be got; this page shows
      // what is installed, and ends with the way there and the way in from a
      // link (one row, opening the Market's own install sheet: page/market.js).
      host.marketFindMore?.('extension', 'Find more plugins'), host.marketLinkDoor?.()]
  }
  function sync() {
    if (st.focusDetail && st.selected) {
      const back = document.querySelector('.settings .plugin-back')
      if (back) {
        st.focusDetail = false
        back.focus({ preventScroll: true })
        const scroll = back.closest('.app-modal-body') ?? back.closest('.setmain') ?? back.closest('.settings')
        if (scroll) scroll.scrollTop = 0
      }
    }
  }
  return { st, view, open, clear, opened, sync, checkUpdates }
}
