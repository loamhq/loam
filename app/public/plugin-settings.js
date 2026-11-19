import { h, icon } from './ui.js'
import { createSecretSettings } from './plugin-secrets.js'
const label = id => ({ 'core.message': 'the message bubble', 'core.thread-list': 'the thread list', 'core.composer': 'the composer', 'core.files.viewer': 'the Files viewer', 'core.files.editor': 'the Files editor', 'core.rail': 'the navigation rail', 'core.activity': 'Activity', 'core.apps': 'Apps', 'core.settings': 'Settings' }[id] ?? id)
export const sourceLine = source => source?.kind === 'loam' ? 'Loam' : source?.kind === 'link' ? `From github.com/${source.repo}` : 'Built here'
// Installed from a link someone shared, not from the Market (2026-11-18): the
// install records `via`; one from before then is the Market's when the Market
// lists that id from that repository.
export const fromLink = (row, listed = []) => row?.source?.kind === 'link' && row.source.via !== 'market'
  && (row.source.via === 'link' || !listed.some(e => e.id === row.id && e.repo === row.source.repo))
export function touchSentences(declaration = {}) {
  const touches = declaration.touches ?? {}, lines = []
  if (declaration.page_code) lines.push('Runs code in the page.')
  for (const network of touches.network ?? []) lines.push(network.host === 'browser-managed' ? `Uses the browser’s speech recognition service: ${network.why}` : `Contacts ${network.host}: ${network.why}`)
  if (touches.record && touches.record !== 'none') lines.push(touches.record === 'read' ? 'Reads conversation records.' : 'Reads and changes conversation records.')
  if (touches.memory && touches.memory !== 'none') lines.push(touches.memory === 'read' ? 'Reads shared memory.' : 'Reads and changes shared memory.')
  if (touches.server) lines.push('Contains code for the host.')
  // Shared keys (2026-11-18): the providers whose keys it is given, from
  // Settings › Connections › API keys. A plugin is handed no key it did not declare here.
  for (const key of touches.keys ?? []) lines.push(`Uses your ${key.name} key${key.optional ? ' if you have one' : ''}${key.why ? `: ${key.why}` : '.'}`)
  for (const id of declaration.overrides ?? []) lines.push(`Replaces ${label(id)}.`)
  return lines
}
const external = source => { try { const u = new URL(source?.url); return u.protocol === 'https:' ? u.href : null } catch { return null } }
export function createPluginSettings({ loader, theme, host, changed = () => {} }) {
  const st = { selected: null, detail: null, detailError: '', opening: false, remove: false, error: '', done: '', guide: false, focusDetail: false, restarting: null, serviceError: null, logs: new Set(), advanced: null }
  let hostSettings = null, secretSettings = null
  const button = (text, onclick, cls = 'pillbtn', props = {}) => h('button', { type: 'button', class: cls, text, onclick, ...props })
  const notice = (text, bad = true) => text ? h('p', { class: `plugin-notice${bad ? ' err' : ''}`, role: bad ? 'alert' : 'status', text }) : null
  const linked = row => fromLink(row, host.marketState?.list?.plugins ?? [])
  const meta = row => [row.author || 'Unknown author', row.version || 'Version unknown', sourceLine(row.source)].join(' · ')
  // A plugin's page says who made it, its version and where to read it
  // (2026-11-18): the repository, ref and commit are the link's, behind View
  // source (a link's `url` is the tree at the commit it was installed from).
  const byline = row => {
    const url = external(row.source)
    const who = row.author ? `By ${row.author}` : row.source?.kind === 'loam' ? 'By Loam' : 'Unknown author'
    return h('p', { class: 'plugin-meta plugin-byline' },
      h('span', { text: `${who} · ${row.version ? `Version ${row.version}` : 'Version unknown'}` }),
      url ? h('a', { class: 'quietlink plugin-source', href: url, target: '_blank', rel: 'noopener noreferrer', text: 'View source' }) : null)
  }

  const declarations = (declaration, title = 'What it touches') => h('section', { class: 'plugin-declaration', 'aria-label': title }, h('h3', { class: 'kicker', text: title }),
    touchSentences(declaration).length ? h('ul', {}, ...touchSentences(declaration).map(text => h('li', { text }))) : h('p', { class: 'note', text: 'Declares no network, record or memory access.' }))
  function opened() {
    const row = loader.st.rows.find(row => row.key === st.selected?.key)
    if (row?.on) host.registry?.emit('plugin.settings.opened', { id: row.id })
  }
  async function open(row) {
    hostSettings = null; secretSettings = null
    st.selected = row; st.detail = null; st.detailError = ''; st.opening = true; st.guide = false; st.remove = false; st.error = ''; st.focusDetail = true; changed()
    opened()
    try {
      const out = await host.api('GET', `/plugins/${encodeURIComponent(row.id)}`)
      if (st.selected?.key === row.key) {
        st.detail = out
        // A plugin's own settings (`settings.entry`): until 0.2.1 only Daily
        // notes' were read and shown, so no other plugin's could be changed
        // here (the 0.2.0 walk: Comments & suggestions' `reach`).
        if (row.id === 'daily-notes' || (out.plugin?.contributes || row.contributes)?.['settings.entry']?.length) {
          try { const settings = await host.api('GET', `/plugins/${encodeURIComponent(row.id)}/settings`); if (st.selected?.key === row.key) hostSettings = settings }
          catch (e) { if (st.selected?.key === row.key) st.detailError = e.message }
        }
        // A plugin that declares `keys:` takes them from API keys (2026-11-18);
        // its `settings.secret` of the same name stays only so a broker from
        // before 0.2.3 still hands it the key, and is not drawn here.
        if (st.selected?.key === row.key && !(out.plugin?.keys || row.keys)?.length && (out.plugin?.contributes || row.contributes)?.['settings.secret']?.length) {
          secretSettings = createSecretSettings({ id: row.id, request: host.api, redraw: changed })
        }
        if (out.plugin) loader.st.rows = loader.st.rows.map(current => current.key === row.key ? { ...current, ...out.plugin, key: current.key } : current)
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
      h('p', { class: 'note plugin-service-state', text: `Server · ${{ running: 'Running', starting: 'Starting', failing: 'Failing', stopping: 'Stopping', stopped: 'Stopped' }[service.state] ?? service.state}` }),
      service.state === 'failing' ? h('div', { class: 'plugin-service-failure' },
        h('details', { key: `log:${row.key}`, open: st.logs.has(row.key), ontoggle: event => { if (event.currentTarget.isConnected) event.currentTarget.open ? st.logs.add(row.key) : st.logs.delete(row.key) } },
          h('summary', { text: 'Recent log' }), h('pre', { text: Array.isArray(log) ? log.join('\n') : log || 'No log lines available.' })),
        button(st.restarting === row.id ? 'Restarting…' : 'Restart', () => restart(row), 'pillbtn content-mark', { disabled: st.restarting !== null, 'aria-label': `Restart ${row.name || row.id}` })) : null,
      st.serviceError?.id === row.id ? notice(st.serviceError.message) : null)
  }
  // A plugin's picture (2026-11-18): its icon through the page's own files
  // route (the row's `files` is the broker's /v1 path, which the page cannot
  // load), and a calm letter tile when it has none or the file fails.
  const letterTile = row => h('span', { class: 'plugin-icon plugin-icon-letter', 'aria-hidden': 'true', text: (row.name || row.id || '?').trim().charAt(0).toUpperCase() })
  const pluginIcon = row => {
    if (row.kind?.includes?.('theme') && !row.icon) return null
    if (!row.icon) return letterTile(row)
    const img = h('img', { class: 'plugin-icon', src: `/api/plugins/${encodeURIComponent(row.id)}/files/${row.icon.replace(/^\//, '').split('/').map(encodeURIComponent).join('/')}`, alt: '', loading: 'lazy',
      onerror: e => { const tile = letterTile(row); e.currentTarget?.replaceWith?.(tile) } })
    return img
  }
  function rowView(row) {
    row = { ...row, overrides: [...new Set([...(row.overrides ?? []), ...(host.registry?.overrides(row.id) ?? [])])] }
    const failed = loader.st.failures.get(row.id), broken = row.broken || failed
    const themeState = theme.value.theme === row.id ? 'In use' : 'Not in use'
    const control = row.switch === 'plugin' && !row.broken ? h('button', { class: 'schedule-switch plugin-switch', role: 'switch', 'aria-checked': String(Boolean(row.on)), 'aria-label': `Enable ${row.name || row.id}`, disabled: loader.st.writing.has(`/plugins/${row.id}`), onclick: () => toggle(row) }, h('span', { 'aria-hidden': 'true' }))
      : row.switch === 'appearance' ? h('span', { class: 'plugin-state', text: themeState })
      : row.switch === 'listed' ? null : h('span', { class: 'plugin-state', text: 'Unavailable' })
    return h('div', { class: 'plugin-list-item', key: row.key }, h('div', { class: 'plugin-list-row' },
      h('button', { type: 'button', class: 'setrow plugin-row', onclick: () => open(row), 'aria-label': `About ${row.name || row.id}`, 'aria-description': row.switch === 'appearance' ? `${meta(row)} · ${themeState}` : meta(row) },
        pluginIcon(row),
        h('div', { class: 'setrow-who' }, h('div', { class: 'setrow-title', text: row.name || row.id }), h('div', { class: 'setrow-sub', text: row.description || (row.switch === 'appearance' ? 'Theme' : row.on ? 'Enabled' : row.id === 'git-backup' ? 'Off-box backup is off' : 'Disabled') }),
          linked(row) ? h('span', { class: 'plugin-mark plugin-mark-link' }, icon.link(11), 'From a link') : null,
          row.shadows ? h('span', { class: 'plugin-mark', text: 'Changes a shipped plugin' }) : null,
          row.overrides?.length ? h('span', { class: 'plugin-mark', text: `Replaces ${row.overrides.map(label).join(', ')}` }) : null,
          broken ? h('div', { class: 'plugin-row-error', text: broken }) : row.warnings?.length ? h('div', { class: 'plugin-row-warning', text: `${row.warnings.length} ${row.warnings.length === 1 ? 'warning' : 'warnings'}` }) : null),
        row.switch === 'appearance' ? control : null, icon.chevronRight(12)), row.switch === 'appearance' ? null : control), serviceView(row))
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
  function detail() {
    const original = st.selected, current = loader.st.rows.find(row => row.key === original.key) ?? original
    const row = current
    const declaration = row.declaration ?? { touches: row.touches, overrides: row.overrides, page_code: row.has?.page_main }
    const url = external(row.source)
    return [h('button', { class: 'backbtn plugin-back', 'aria-label': 'Back to Plugins', onclick: () => { st.selected = null; st.detail = null; st.error = ''; changed() } }, icon.chevronLeft(18), 'Plugins'),
      host.settingsHead(row.name || row.id), pluginIcon(row), byline(row), serviceView(row),
      row.shadows ? h('p', { class: 'plugin-mark', text: 'Changes a shipped plugin.' }) : null,
      linked(row) ? h('p', { class: 'note plugin-from-link' }, icon.link(13), h('span', { text: `Installed from a link: github.com/${row.source.repo}${row.source.path ? `/${row.source.path}` : ''}, at ${row.source.ref_kind === 'tag' ? row.source.ref : `commit ${String(row.source.commit ?? row.source.ref).slice(0, 7)}`}. To update it, install from its link again.` })) : null,
      row.edited ? h('p', { class: 'note', text: 'Edited here since it was installed.' }) : null,
      notice(row.broken), notice(loader.st.failures.get(row.id)), notice(st.error), notice(st.detailError),
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
      declarations(declaration), ...(row.warnings ?? []).map(warning => notice(typeof warning === 'string' ? warning : JSON.stringify(warning), false)),
      st.detail?.plugin?.guide ? h('details', { class: 'plugin-guide', open: st.guide, ontoggle: event => { st.guide = event.currentTarget.open } }, h('summary', { text: 'About this plugin' }), h('div', { class: 'md' }, host.markdown?.(st.detail.plugin.guide) ?? h('p', { text: st.detail.plugin.guide }))) : null,
      loader.st.failures.has(row.id) ? button('Try again', () => loader.refresh(), 'pillbtn content-mark') : null,
      row.layer === 'yours' ? (st.remove ? h('div', { class: 'plugin-remove-question' }, h('p', { text: `Remove ${row.name || row.id}?${row.shadows ? ' The shipped copy will take over.' : ''}` }),
        button('Keep plugin', () => { st.remove = false; changed() }, 'pillbtn content-mark'), button('Remove', () => remove(row), 'pillbtn primary danger', { disabled: loader.st.writing.has(`/plugins/${row.id}`) })) : button('Remove plugin', () => { st.remove = true; changed() }, 'pillbtn content-mark danger')) : null]
  }
  function view({ back = true } = {}) {
    if (st.selected && loader.st.loaded && !loader.st.rows.some(row => row.key === st.selected.key)) { st.selected = null; st.detail = null; st.done = 'This copy was removed.' }
    if (st.selected) return detail()
    const rows = loader.st.rows.filter(row => row.effective || row.broken)
    return [back ? host.settingsBack('Settings', 'root') : null, host.settingsHead('Plugins'), notice(st.error), notice(st.done, false), notice(loader.st.error),
      !loader.st.loaded ? h('p', { class: 'note', role: 'status', text: 'Reading plugins…' }) : null,
      ...[['Themes', true], ['Extensions', false]].map(([title, themes]) => { const group = rows.filter(row => Boolean(row.kind?.includes('theme')) === themes); return group.length ? h('section', { class: 'plugin-group', 'aria-label': title }, h('h3', { class: 'kicker', text: title }), h('div', { class: 'rows' }, ...group.map(rowView))) : null }),
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
  return { st, view, open, opened, sync }
}
