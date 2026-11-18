import { h, icon } from './ui.js'
import { createSecretSettings } from './plugin-secrets.js'
const label = id => ({ 'core.message': 'the message bubble', 'core.thread-list': 'the thread list', 'core.composer': 'the composer', 'core.files.viewer': 'the Files viewer', 'core.files.editor': 'the Files editor', 'core.rail': 'the navigation rail', 'core.activity': 'Activity', 'core.apps': 'Apps', 'core.settings': 'Settings' }[id] ?? id)
export const sourceLine = source => source?.kind === 'loam' ? 'Loam' : source?.kind === 'link' ? `${source.repo}@${source.ref}` : 'Built here'
export function touchSentences(declaration = {}) {
  const touches = declaration.touches ?? {}, lines = []
  if (declaration.page_code) lines.push('Runs code in the page.')
  for (const network of touches.network ?? []) lines.push(network.host === 'browser-managed' ? `Uses the browser’s speech recognition service: ${network.why}` : `Contacts ${network.host}: ${network.why}`)
  if (touches.record && touches.record !== 'none') lines.push(touches.record === 'read' ? 'Reads conversation records.' : 'Reads and changes conversation records.')
  if (touches.memory && touches.memory !== 'none') lines.push(touches.memory === 'read' ? 'Reads shared memory.' : 'Reads and changes shared memory.')
  if (touches.server) lines.push('Contains code for the host.')
  for (const id of declaration.overrides ?? []) lines.push(`Replaces ${label(id)}.`)
  return lines
}
const external = source => { try { const u = new URL(source?.url); return u.protocol === 'https:' ? u.href : null } catch { return null } }
export function createPluginSettings({ loader, theme, host, changed = () => {} }) {
  const st = { selected: null, detail: null, detailError: '', opening: false, remove: false, error: '', done: '', url: '', ref: '', path: '', preview: null, request: null, installError: '', busy: false, guide: false, allDeclarations: false, refOptions: false, focusPreview: false, focusDetail: false, restarting: null, serviceError: null, logs: new Set(), market: null, marketLoading: false }
  let hostSettings = null, secretSettings = null
  const button = (text, onclick, cls = 'pillbtn', props = {}) => h('button', { type: 'button', class: cls, text, onclick, ...props })
  const notice = (text, bad = true) => text ? h('p', { class: `plugin-notice${bad ? ' err' : ''}`, role: bad ? 'alert' : 'status', text }) : null
  const meta = row => [row.author || 'Unknown author', row.version || 'Version unknown', sourceLine(row.source)].join(' · ')
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
        if (st.selected?.key === row.key && (out.plugin?.contributes || row.contributes)?.['settings.secret']?.length) {
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
  const pluginIcon = row => row.icon && row.files ? h('img', { class: 'plugin-icon', src: `${row.files.replace(/\/$/, '')}/${row.icon.replace(/^\//, '')}`, alt: '', loading: 'lazy' }) : null
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
        h('div', { class: 'setrow-who' }, h('div', { class: 'setrow-title', text: row.name || row.id }), h('div', { class: 'setrow-sub', text: row.description || (row.switch === 'appearance' ? 'Theme' : row.on ? 'Enabled' : 'Disabled') }),
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
  function entrySettings(row) {
    const entries = hostSettings?.entries ?? []
    if (!entries.length) return null
    return h('section', { class: 'plugin-entry-settings rows', 'aria-label': `${row.name || row.id} settings` },
      ...entries.map(entry => {
        const id = `plugin-setting-${row.id}-${entry.key}`, value = hostSettings.values?.[entry.key] ?? entry.default ?? ''
        const control = entry.choices?.length
          ? h('select', { id, class: 'field-input', value: String(value), onchange: e => saveEntry(row, entry.key, e.currentTarget.value) },
            ...entry.choices.map(c => h('option', { value: c, text: c, selected: c === value })))
          : h('input', { id, class: 'field-input', value: String(value), onchange: e => saveEntry(row, entry.key, e.currentTarget.value) })
        return h('div', { class: 'setrow plugin-entry', key: entry.key }, h('label', { class: 'setrow-who', for: id }, h('div', { class: 'setrow-title', text: entry.label })), control)
      }), notice(st.done, false))
  }
  function detail() {
    const original = st.selected, current = loader.st.rows.find(row => row.key === original.key) ?? original
    const row = current
    const declaration = row.declaration ?? { touches: row.touches, overrides: row.overrides, page_code: row.has?.page_main }
    const url = external(row.source)
    return [h('button', { class: 'backbtn plugin-back', 'aria-label': 'Back to Plugins', onclick: () => { st.selected = null; st.detail = null; st.error = ''; changed() } }, icon.chevronLeft(18), 'Plugins'),
      host.settingsHead(row.name || row.id), pluginIcon(row), h('p', { class: 'plugin-meta', text: meta(row) }), serviceView(row),
      url ? h('a', { class: 'quietlink plugin-source', href: url, target: '_blank', rel: 'noopener noreferrer', text: 'View source' }) : null,
      row.source?.commit ? h('p', { class: 'note plugin-commit', text: row.source.commit }) : null,
      row.shadows ? h('p', { class: 'plugin-mark', text: 'Changes a shipped plugin.' }) : null,
      row.edited ? h('p', { class: 'note', text: 'Edited here since it was installed.' }) : null,
      notice(row.broken), notice(loader.st.failures.get(row.id)), notice(st.error), notice(st.detailError),
      st.opening ? h('p', { class: 'note', role: 'status', text: 'Reading plugin details…' }) : null,
      row.switch === 'plugin' && row.id !== 'daily-notes' && !row.broken ? h('div', { class: 'rows plugin-enable' }, h('div', { class: 'setrow' },
        h('div', { class: 'setrow-who' }, h('div', { class: 'setrow-title', text: row.on ? 'On' : 'Off' }), h('div', { class: 'setrow-sub', text: row.on ? 'Settings are kept when you turn this off.' : 'Turn on to use this plugin and its settings.' })),
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
      row.on && row.id !== 'daily-notes' ? entrySettings(row) : null,
      row.on ? secretSettings?.view() : null,
      declarations(declaration), ...(row.warnings ?? []).map(warning => notice(typeof warning === 'string' ? warning : JSON.stringify(warning), false)),
      st.detail?.plugin?.guide ? h('details', { class: 'plugin-guide', open: st.guide, ontoggle: event => { st.guide = event.currentTarget.open } }, h('summary', { text: 'About this plugin' }), h('div', { class: 'md' }, host.markdown?.(st.detail.plugin.guide) ?? h('p', { text: st.detail.plugin.guide }))) : null,
      loader.st.failures.has(row.id) ? button('Try again', () => loader.refresh(), 'pillbtn content-mark') : null,
      row.layer === 'yours' ? (st.remove ? h('div', { class: 'plugin-remove-question' }, h('p', { text: `Remove ${row.name || row.id}?${row.shadows ? ' The shipped copy will take over.' : ''}` }),
        button('Keep plugin', () => { st.remove = false; changed() }, 'pillbtn content-mark'), button('Remove', () => remove(row), 'pillbtn primary danger', { disabled: loader.st.writing.has(`/plugins/${row.id}`) })) : button('Remove plugin', () => { st.remove = true; changed() }, 'pillbtn content-mark danger')) : null]
  }
  async function install(confirm = false) {
    if (st.busy) return
    if (!confirm) st.request = { url: st.url.trim(), ...(st.ref.trim() ? { ref: st.ref.trim() } : {}), ...(st.path.trim() ? { path: st.path.trim() } : {}) }
    st.busy = true; st.installError = ''; st.done = ''; changed()
    try {
      const out = await loader.install({ ...st.request, ...(confirm ? { confirm: st.preview.confirm } : {}) })
      if (out.installed) { st.done = `${out.plugin?.name ?? out.id} installed.`; st.url = ''; st.ref = ''; st.path = ''; st.preview = null; st.request = null }
      else { st.preview = out; st.allDeclarations = false; st.focusPreview = true }
    } catch (error) {
      st.installError = error.message
      if (error.status === 409 && error.body?.preview) { st.preview = error.body.preview; st.allDeclarations = false; st.focusPreview = true }
    } finally { st.busy = false; changed() }
  }
  function previewView() {
    const p = st.preview, update = p.replaces, added = p.added && Object.keys(p.added).length
    const full = declarations(p.declaration)
    return h('section', { class: 'plugin-preview', id: 'plugin-install-question', 'aria-label': 'Review plugin installation' },
      h('h3', { class: 'display', tabindex: '-1', text: `${update ? 'Update' : 'Install'} ${p.name || p.id}?` }), h('p', { class: 'plugin-meta', text: meta(p) }),
      p.source?.commit ? h('p', { class: 'note plugin-commit', text: p.source.commit }) : null,
      external(p.source) ? h('a', { class: 'quietlink', href: external(p.source), target: '_blank', rel: 'noopener noreferrer', text: 'View source' }) : null,
      p.shadows ? h('p', { class: 'plugin-mark', text: 'Changes a shipped plugin.' }) : null,
      update ? h('p', { class: 'note', text: `Replaces version ${update.version ?? 'unknown'}.${update.edited ? ' Local edits will be replaced.' : ''}` }) : null,
      update && added ? declarations({ touches: p.added, overrides: p.added.overrides, page_code: p.added.page_code }, 'Added in this update') : null,
      update && !added ? h('p', { class: 'note', text: 'No new declarations.' }) : null,
      update ? h('details', { open: st.allDeclarations, ontoggle: event => { st.allDeclarations = event.currentTarget.open } }, h('summary', { text: 'All declarations' }), full) : full,
      ...(p.warnings ?? []).map(text => notice(String(text), false)), notice(st.installError),
      h('div', { class: 'plugin-preview-actions' }, button('Change link', () => { st.preview = null; st.installError = ''; changed() }, 'pillbtn content-mark', { disabled: st.busy }),
        button(st.busy ? 'Installing…' : update ? 'Update plugin' : 'Install plugin', () => install(true), 'pillbtn primary', { disabled: st.busy || !p.confirm })))
  }
  // The marketplace (2026-11-17): the list the broker reads from the
  // marketplace's repository. Never an error: a list not there yet is the
  // calm empty line, a broker without the route hides the section. "Get"
  // puts the entry's link through the same install question as any link.
  async function loadMarket() {
    if (st.marketLoading) return
    st.marketLoading = true
    try { st.market = await host.api('GET', '/marketplace') }
    catch (error) { st.market = { state: [404, 405].includes(error.status) ? 'off' : 'unreachable', plugins: [] } }
    finally { st.marketLoading = false; changed() }
  }
  const kindWords = kind => (kind ?? []).map(k => ({ theme: 'Theme', extension: 'Extension', app: 'App', agent: 'Agent', skill: 'Skill' }[k] ?? k)).join(', ')
  function getFromMarket(entry) {
    st.url = entry.link; st.ref = ''; st.path = ''; st.refOptions = false; st.preview = null
    install()
  }
  function marketView() {
    if (!st.market && !st.marketLoading) loadMarket()
    const market = st.market
    if (market?.state === 'off') return null
    const installed = new Set(loader.st.rows.filter(row => row.effective).map(row => row.id))
    const entries = market?.plugins ?? []
    return h('section', { class: 'plugin-group plugin-market', 'aria-label': 'Marketplace' }, h('h3', { class: 'kicker', text: 'Marketplace' }),
      !market ? h('p', { class: 'note', role: 'status', text: 'Reading the marketplace…' })
        : !entries.length ? h('p', { class: 'note plugin-market-empty', text: market.state === 'unreachable' ? 'The marketplace can’t be reached right now. Anything shared as a link still installs below.' : 'Nothing is listed yet. Anything shared as a link installs below.' })
        : h('div', { class: 'rows' }, ...entries.map(entry => h('div', { class: 'plugin-list-item', key: `market:${entry.id}` }, h('div', { class: 'plugin-list-row plugin-market-row' },
          h('div', { class: 'setrow-who' },
            h('div', { class: 'setrow-title', text: entry.name }),
            entry.description ? h('div', { class: 'setrow-sub', text: entry.description }) : null,
            h('div', { class: 'plugin-mark', text: [entry.author, kindWords(entry.kind)].filter(Boolean).join(' · ') })),
          installed.has(entry.id) ? h('span', { class: 'plugin-state', text: 'Installed' })
            : button(st.busy && st.request?.url === entry.link ? 'Reading…' : 'Get', () => getFromMarket(entry), 'pillbtn content-mark', { disabled: st.busy, 'aria-label': `Get ${entry.name}` }))))))
  }
  function installView() {
    return h('section', { class: 'plugin-install', 'aria-label': 'Install from a link' }, h('h3', { class: 'kicker', text: 'Install from a link' }),
      st.preview ? previewView() : h('form', { onsubmit: event => { event.preventDefault(); install() } },
        h('label', { for: 'plugin-url', text: 'GitHub link or owner/repo' }), h('input', { id: 'plugin-url', class: 'field-input', type: 'text', inputmode: 'url', autocomplete: 'off', placeholder: 'owner/repo or a tagged folder link', value: st.url, disabled: st.busy, oninput: event => { st.url = event.target.value } }),
        h('details', { class: 'plugin-install-options', open: st.refOptions, ontoggle: event => { st.refOptions = event.currentTarget.open } }, h('summary', {}, 'Ref and folder', icon.chevronRight(12)),
          h('label', { for: 'plugin-ref', text: 'Tag or full commit' }), h('input', { id: 'plugin-ref', class: 'field-input', value: st.ref, disabled: st.busy, oninput: event => { st.ref = event.target.value } }),
          h('label', { for: 'plugin-path', text: 'Folder in the repository' }), h('input', { id: 'plugin-path', class: 'field-input', value: st.path, disabled: st.busy, oninput: event => { st.path = event.target.value } })),
        notice(st.installError), h('button', { class: 'pillbtn', type: 'submit', disabled: st.busy, text: st.busy ? 'Reading declaration…' : 'Review plugin' })))
  }
  function view({ back = true } = {}) {
    if (st.selected && loader.st.loaded && !loader.st.rows.some(row => row.key === st.selected.key)) { st.selected = null; st.detail = null; st.done = 'This copy was removed.' }
    if (st.selected) return detail()
    const rows = loader.st.rows.filter(row => row.effective || row.broken)
    return [back ? host.settingsBack('Settings', 'root') : null, host.settingsHead('Plugins'), notice(st.error), notice(st.done, false), notice(loader.st.error),
      !loader.st.loaded ? h('p', { class: 'note', role: 'status', text: 'Reading plugins…' }) : null,
      ...[['Themes', true], ['Extensions', false]].map(([title, themes]) => { const group = rows.filter(row => Boolean(row.kind?.includes('theme')) === themes); return group.length ? h('section', { class: 'plugin-group', 'aria-label': title }, h('h3', { class: 'kicker', text: title }), h('div', { class: 'rows' }, ...group.map(rowView))) : null }),
      marketView(), installView()]
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
    const question = document.getElementById('plugin-install-question')
    if (!st.focusPreview || !question) return
    st.focusPreview = false
    question.querySelector('h3')?.focus({ preventScroll: true })
    question.scrollIntoView({ block: 'start' })
  }
  return { st, view, open, opened, sync }
}
