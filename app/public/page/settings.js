import { createUpdate } from '../update.js'
import { createPasskey } from '../passkey.js'
import { settingsHome, SETTINGS_SECTIONS } from "../app-shape.js"
import { h, icon, kept, blob } from "../ui.js"
import { settingsNav, segmentedControl, settingsRow } from "../chat-ui.js"
import { connectedCount, providerRows, providerDetail, loginView } from "../providers.js"
import { nameOf as fileNameOf, ROOT_LOOK } from "../files.js"
import { agentLook, createAgents, providerGlyph } from "../sessions.js"
import { providerMark } from "../marks.js"
import { githubNotice, orgApps, offersOrgs, orgView, githubView } from "../integrations.js"
import { integrationCatalog } from '../integration-catalog.js'
import { createGithubAccounts } from '../github-accounts.js'
import { formatDate } from '../time.js'
import { themeImageURL, themeImagePosition } from '../theme-image.js'
import { fileShortcuts, toggleFileShortcut } from '../store.js'
import { createApiKeys } from '../api-keys.js'

// Mounted once. Cross-module state and operations are explicit host services.
export function mount(host) {
const failedThemeImages = new Set()
const settingsEvents = host.registry?.scope?.({ id: '@core' })?.events
const updates = createUpdate({ api: (...args) => host.api(...args), changed: () => host.schedule() })
const passkey = createPasskey({ changed: () => host.schedule() })
settingsEvents?.on('settings.opened', ({ opening, page }) => { if (opening || page === 'update') updates.check() })
settingsEvents?.on('sheet.changing', ({ sheet }) => { if (sheet?.kind !== 'settings' || sheet.page !== 'signin') passkey.stop() })
settingsEvents?.on('page.leaving', () => { updates.stop(); passkey.stop() })

// Settings › Accounts (2026-11-18): API keys by provider, entered once.
const apiKeys = createApiKeys({ api: (...args) => host.api(...args), changed: () => host.schedule(), openPlugin: id => goSettings('plugins', id) })
function settingsAccounts({ back = true } = {}) {
  const provider = host.state.sheet?.page === 'accounts' ? host.state.sheet.provider : null
  return apiKeys.view({ provider,
    back: provider ? settingsBack('Accounts', 'accounts') : null,
    head: [back ? settingsBack('Settings', 'root') : null, settingsHead('Accounts', 'API keys for the services your plugins use, entered once.')],
    open: id => goSettings('accounts', id) })
}

const githubAccounts = createGithubAccounts({ state: () => host.state.github, api: (...args) => host.api(...args), changed: () => host.schedule(), reload: () => host.loadGithub() })
function settingsBody(sheet, back) {
  const page = settingsHome(sheet.page)
  if (page === 'agents' && sheet.detail) return sheet.detail.kind === 'agent'
    ? [settingsBack('Agents', 'agents'), ...host.settingsProfileUI.body(sheet.detail.id)] : host.settingsProfileUI.settingsDetail()
  if (page === 'update') return [back ? settingsBack('Settings', 'root') : null, settingsHead('Update', 'A little care for your Loam.'), updates.view()]
  if (page === 'signin') {
    if (!passkey.st.listed && !passkey.st.listing) passkey.loadPasskeys()
    return [back ? settingsBack('Settings', 'root') : null, settingsHead('Sign-in', 'Your way into Loam.'), passkey.view()]
  }
  if (page === 'provider') return settingsProvider(sheet.provider)
  if (page === 'integration') return settingsIntegration(sheet.provider, sheet.account)
  const section = host.registry.entries('settings.section').find(entry => !entry.component && entry.id === page && (!entry.children || host.registry.entries('settings.section').some(child => child.group === entry.children)))
  return section ? host.registry.invoke(section, 'render', { back, sheet }) : settingsRoot()
}

function settingsSheet(...args) { return host.registry.component('core.settings', { args, variant: 'settingsSheet', defaultView: () => builtinSettingsSheet(...args) }) }

function builtinSettingsSheet(sheet) {
  return h('div', { class: 'sheet settings' }, host.sheetTop(), ...settingsBody(sheet, true))
}

function settingsItems() {
  return host.registry.entries('settings.section').filter(item => !item.component && !item.group && (!item.children || host.registry.entries('settings.section').some(child => child.group === item.children))).map(item => ({ ...item, label: item.title, glyph: typeof item.icon === 'function' ? item.icon(16) : (icon[item.icon] ?? icon.code)(16), dot: item.id === 'providers' ? providersWarn() : item.id === 'accounts' ? apiKeys.warn() : item.id === 'integrations' && integrationsWarn() }))
}

function settingsDialog(...args) { return host.registry.component('core.settings', { args, variant: 'settingsDialog', defaultView: () => builtinSettingsDialog(...args) }) }

function builtinSettingsDialog(sheet) {
  const page = settingsHome(sheet.page === 'root' ? 'appearance' : sheet.page)
  return h('div', { class: 'sheet settings twopane' },
    settingsNav({ title: 'Settings', items: settingsItems(), active: page === 'provider' ? 'providers' : page === 'integration' ? 'integrations' : page, choose: item => goSettings(item.id) }),
    h('div', { class: 'setmain' },
      h('header', { class: 'app-modal-head settings-modal-close' },
        h('button', { class: 'iconbtn ghost', title: 'Close', 'aria-label': 'Close settings', onclick: () => host.closeSheet({ historyBack: true }) }, icon.close(14))),
      h('div', { class: 'app-modal-body', key: `settings:${page}:${sheet.detail?.kind ?? ''}:${sheet.detail?.id ?? ''}:${sheet.provider ?? ''}:${sheet.account ?? ''}` }, ...settingsBody({ ...sheet, page }, false))))
}

function settingsPlugins(options = {}) { return host.pluginSettingsUI.view(options) }


function settingsAppearance({ back = true } = {}) {
  const { mode, transition = 'normal', size, theme: look } = host.theme.value, status = host.theme.status
  const chosen = host.theme.installed.find(t => t.id === look)
  const latte = host.theme.value.options?.[look]?.['agent-shapes'] === 'latte-art'
  const signature = host.theme.value.options?.[look]?.['agent-shapes'] === 'signature'
  const themeOptions = chosen?.options?.filter(option =>
    option.id === 'agent-colours' ? !latte : option.id === 'face-content' ? signature : ['latte-mug', 'latte-inside'].includes(option.id) ? latte : true)
  const options = [{ id: 'light', label: 'Light' }, { id: 'dark', label: 'Dark' }, { id: 'sun', label: 'Sun' }, { id: 'system', label: 'System' }]
  const transitions = [{ id: 'fast', label: 'Fast · 3 s' }, { id: 'normal', label: 'Normal · 10 s' }, { id: 'long', label: 'Long · 20 s' }]
  const solar = mode === 'sun' ? host.theme.solar : null
  const feedback = h('div', { class: `appearance-status${status.error ? ' err' : ''}`, role: 'status' }, status.error || (status.saving ? 'Saving…' : status.localOnly ? 'Sun saved on this device until the host supports it.' : 'Changes saved'),
    status.error ? h('button', { class: 'quietlink', type: 'button', text: 'Try again', onclick: () => status.saving ? host.theme.save() : host.theme.refresh() }) : null)
  return [
    back ? settingsBack('Settings', 'root') : null,
    settingsHead('Appearance', 'Synced across browsers, with separate phone and desktop preferences.'),
    status.error ? feedback : null,
    h('section', { class: 'card appearance-card', 'aria-labelledby': 'appearance-theme' },
      h('h3', { class: 'kicker', id: 'appearance-theme', text: 'Theme' }),
      h('div', { class: 'theme-picker', role: 'group', 'aria-label': 'Theme' }, ...host.theme.installed.map((option) => {
        const image = themeImageURL(option, host.theme.current), showImage = image && !failedThemeImages.has(image)
        return h('button', { key: option.id, type: 'button', class: `theme-choice${look === option.id ? ' on' : ''}`, 'aria-pressed': String(look === option.id), 'aria-label': option.name,
          onclick: () => host.theme.set({ theme: option.id }) },
          h('span', { class: `theme-preview${showImage ? ' has-image' : ''}`, 'data-look': option.id, 'aria-hidden': 'true' },
            h('span', { class: 'theme-miniature' },
              h('span', { class: 'say user', text: 'Room to think' }),
              h('span', { class: 'theme-mini-controls' },
                blob({ color: 'var(--persona-gold)', ink: 'var(--persona-gold-ink)', shape: '34%', initial: 'A' }, { size: 26, font: 13, inline: true }),
                h('span', { class: 'pillbtn primary', text: 'Send' })),
              h('span', { class: 'theme-mini-body', text: 'A little space for ideas.' })),
            showImage ? h('img', { class: 'theme-cover', src: image, alt: '', loading: 'lazy', decoding: 'async', width: 640, height: 427,
              style: { objectPosition: themeImagePosition(option, host.theme.current) },
              onerror: e => { failedThemeImages.add(image); e.currentTarget.hidden = true; e.currentTarget.parentElement.classList.remove('has-image'); host.schedule() } }) : null,
            h('span', { class: 'theme-choice-label' }, h('span', { class: 'theme-choice-name', text: option.name })),
            look === option.id ? h('span', { class: 'theme-choice-check' }, icon.check(13)) : null))
      }))),
    chosen?.options?.length ? h('section', { class: 'card appearance-card theme-options', 'aria-labelledby': 'appearance-options' },
      h('h3', { class: 'kicker', id: 'appearance-options', text: chosen.name }),
      chosen.options.some(option => ['agent-colours', 'agent-shapes'].includes(option.id)) ?
        h('div', { class: 'theme-agent-preview', 'aria-label': 'Agent preview' }, ...host.state.agents.list.slice(0, 6).map(agent =>
          h('span', { key: agent.id, title: agent.name }, blob(agentLook(agent.id, host.state.agents), { size: latte || signature ? 44 : 32, font: latte || signature ? 19 : 14 })))) : null,
      ...themeOptions.map(option => h('div', { class: `theme-option${option.id === 'latte-mug' ? ' theme-option-mug' : ''}`, key: option.id },
        h('h4', { id: `appearance-option-${option.id}`, text: option.label }),
        option.id === 'latte-mug' ? h('button', { class: 'schedule-switch', type: 'button', role: 'switch',
          'aria-labelledby': `appearance-option-${option.id}`, 'aria-checked': String(host.theme.value.options?.[look]?.[option.id] !== 'off'),
          onclick: () => host.theme.set({ options: { [look]: { [option.id]: host.theme.value.options?.[look]?.[option.id] === 'off' ? 'on' : 'off' } } }) }, h('span', { 'aria-hidden': 'true' }))
        : segmentedControl({ label: option.label, options: option.choices, value: host.theme.value.options?.[look]?.[option.id],
          choose: id => host.theme.set({ options: { [look]: { [option.id]: id } } }),
          props: { class: 'appearance-modes theme-option-choices', 'aria-labelledby': `appearance-option-${option.id}` } })))) : null,
    h('section', { class: 'card appearance-card', 'aria-labelledby': 'appearance-mode' },
      h('h3', { class: 'kicker', id: 'appearance-mode', text: 'Mode' }),
      segmentedControl({ label: 'Mode', value: mode, options, choose: id => host.theme.set({ mode: id }), props: { class: 'appearance-modes sun-modes' } }),
      solar ? h('div', { class: 'appearance-sun' },
        h('p', { class: 'note sun-hint', text: 'Light from 30 min before sunrise to 30 min after sunset' }),
        h('p', { class: 'sun-times', text: solar.times }),
        h('p', { class: 'note sun-place', text: solar.coordinates }),
        h('div', { class: 'sun-location note' },
          solar.place.source === 'timezone' ? h('span', { text: 'Using your time zone ·' }) : null,
          h('button', { class: 'quietlink', type: 'button', disabled: status.locating, text: status.locating ? 'Finding your location…' : 'Use my location', onclick: () => host.theme.useLocation() })),
        h('div', { class: 'sun-transition' },
          h('h4', { id: 'appearance-sun-transition', text: 'Transition' }),
          h('p', { class: 'note sun-transition-hint', text: 'Gradual colour shift at sunrise and sunset.' }),
          segmentedControl({ label: 'Transition', value: transition, options: transitions, choose: id => host.theme.set({ transition: id }), props: { class: 'appearance-modes sun-transition-modes', 'aria-labelledby': 'appearance-sun-transition' } }))) : null),
    h('section', { class: 'card appearance-card', 'aria-labelledby': 'appearance-size' },
      h('div', { class: 'appearance-label' }, h('h3', { class: 'kicker', id: 'appearance-size', text: 'Size' }), h('output', { for: 'appearance-zoom', 'aria-label': 'Current size', text: `${size}%` })),
      h('p', { class: 'note', text: 'Adjust text and controls together.' }),
      h('div', { class: 'appearance-stepper', role: 'group', 'aria-label': 'App size' },
        h('button', { class: 'roundbtn', type: 'button', 'aria-label': 'Zoom out', disabled: size <= 75, onclick: () => host.theme.set({ size: size - 5 }), text: '−' }),
        h('input', { id: 'appearance-zoom', type: 'range', min: 75, max: 150, step: 5, value: size, 'aria-label': 'App zoom', 'aria-valuetext': `${size}%`, oninput: (e) => host.theme.set({ size: Number(e.target.value) }), onchange: () => host.theme.save() }),
        h('button', { class: 'roundbtn', type: 'button', 'aria-label': 'Zoom in', disabled: size >= 150, onclick: () => host.theme.set({ size: size + 5 }), text: '+' })),
      h('div', { class: 'appearance-limits' }, h('span', { text: '75%' }), h('span', { text: '150%' })),
      h('button', { class: 'quietlink appearance-reset', type: 'button', disabled: size === 100, text: 'Reset to 100%', onclick: () => host.theme.set({ size: 100 }) }),
      host.state.wide ? h('p', { class: 'note', text: 'Ctrl / ⌘ + or − to resize · 0 to reset' }) : null),
    !status.error ? feedback : null,
  ]
}

function goSettings(page, provider = null) {
  if (page === 'running') { host.goActivity(); return }
  page = settingsHome(page)
  if (page !== 'provider' || host.state.sheet?.provider !== provider) host.providerSignIn.cancel()
  if (!host.state.sheet) updates.check()
  const sheet = { kind: 'settings', page, provider }
  host.registry.emit('sheet.changing', { previous: host.state.sheet, sheet })
  host.state.sheet = sheet
  if (page === 'plugins') {
    if (provider) {
      const openPlugin = () => {
        if (host.state.sheet?.kind !== 'settings' || settingsHome(host.state.sheet.page) !== 'plugins') return
        const row = host.pluginLoader?.st.rows?.find(r => r.id === provider && (r.effective || r.broken)) ?? host.pluginLoader?.st.rows?.find(r => r.id === provider)
        if (row) host.pluginSettingsUI?.open(row)
      }
      if (host.pluginLoader?.st.rows?.some(r => r.id === provider)) openPlugin()
      else host.pluginLoader?.refresh()?.then(openPlugin).catch(() => {})
    } else host.pluginLoader?.refresh()
  }
  if (page === 'signin') passkey.loadPasskeys()
  if (page === 'accounts' || page === 'root') apiKeys.load(page === 'accounts' && !provider)
  host.registry.emit('settings.opened', { page })
  if (page === 'agents') host.settingsProfiles.directory()
  host.schedule()
}

function settingsBack(label, page) {
  return h('button', { class: 'backbtn', onclick: () => goSettings(page) }, icon.chevronLeft(18), label)
}

function settingsHead(title, why) {
  return h(
    'div',
    { class: 'sheet-head' },
    h('div', { class: 'display sheet-title', role: 'heading', 'aria-level': '2', tabindex: '-1', text: title }),
    why ? h('div', { class: 'note', text: why }) : null,
  )
}

function tile(child, { kind = '' } = {}) {
  return h('div', { class: `icon-tile ${kind}`.trim() }, child)
}

function settingsRoot() {
  const summaries = { appearance: `${({ system: 'System', sun: 'Sun', dark: 'Dark', light: 'Light' })[host.theme.value.mode]} · ${host.theme.value.size}%`,
    agents: `${host.state.agents.list.length} agents`, providers: connectedCount(host.state.providers).label,
    integrations: integrationCatalog(host.state).filter(service => service.connected).map(service => service.name).join(' · ') || 'Connect your services',
    files: 'Shortcuts and folders', plugins: 'Themes and extensions', accounts: apiKeys.summary() }
  const jobs = host.activity.st.now
  const working = host.running.rows().filter(row => row.state === 'working').length
    + (jobs?.slots.filter(job => !job.free).length ?? 0) + (jobs?.landing.length ?? 0)
  return [settingsHead('Settings'), !host.state.wide ? h('div', { class: 'rows settings-place' }, settingsRow({
    lead: tile(icon.clock(16)), title: 'Activity', sub: working ? `${working} working` : 'Nothing running', onclick: host.openPhoneActivity })) : null,
    h('div', { class: 'rows' }, ...settingsItems().map(item => settingsRow({
    lead: tile(item.glyph), title: item.label, sub: summaries[item.id], dot: item.dot, onclick: () => goSettings(item.id) })))]
}



// ---------------------------------------------------------- settings, files

// The two vault folders the Files home writes into. A field saves when it is
// left or on Enter; an empty or unusable path puts the default back, which is
// what the vault's own layout names.
const filesFolderFields = {}

for (const [which, label] of [['inbox', 'Inbox folder']]) {
  const commit = () => {
    const field = filesFolderFields[which]
    host.store.files = { ...host.store.files, [which]: field.value }
    field.value = host.store.files[which]
    host.schedule()
  }
  filesFolderFields[which] = h('input', {
    class: 'field-input fs-setfolder',
    type: 'text',
    autocomplete: 'off',
    spellcheck: 'false',
    'aria-label': label,
    onchange: commit,
    onkeydown: (e) => {
      if (e.key === 'Enter') commit()
    },
  })
}

function settingsFiles({ back = true } = {}) {
  const held = host.store.files
  const shortcuts = fileShortcuts(held, host.files.st.roots.shortcuts)
  for (const [which, field] of Object.entries(filesFolderFields)) {
    if (globalThis.document?.activeElement !== field) field.value = held[which]
  }
  const folderCard = (which, label, why) =>
    h('div', { class: 'card fs-setcard', key: `files:${which}` }, h('div', { class: 'kicker', text: label }), kept(filesFolderFields[which]), h('div', { class: 'setrow-sub', text: why }))
  return [
    back ? settingsBack('Settings', 'root') : null,
    settingsHead('Files', 'Folders and shortcuts for this device.'),
    folderCard('inbox', 'Inbox folder', 'New inbox notes are saved here.'),
    h('section', { class: 'settings-section', 'aria-label': 'Shortcuts' },
      h('h3', { class: 'kicker', text: 'Shortcuts' }),
      shortcuts.length ? h('div', { class: 'rows' }, ...shortcuts.map(shortcut =>
        h('div', { class: 'setrow', key: `${shortcut.root}:${shortcut.path}` },
          h('div', { class: 'setrow-who' }, h('div', { class: 'setrow-title', text: shortcut.label || fileNameOf(shortcut.path) || ROOT_LOOK[shortcut.root]?.name || shortcut.root }),
            h('div', { class: 'setrow-sub', text: `${ROOT_LOOK[shortcut.root]?.name ?? shortcut.root} / ${shortcut.path}` })),
          h('button', { class: 'iconbtn content-mark', 'aria-label': `Remove shortcut to ${shortcut.path || shortcut.root}`, onclick: () => {
            host.store.files = toggleFileShortcut(host.store.files, host.files.st.roots.shortcuts, shortcut.root, shortcut.path, shortcut.kind); host.schedule()
          } }, icon.close(12)))))
        : h('p', { class: 'note', text: 'Add shortcuts from a file or folder’s menu.' })),
  ]
}



// The personas the host has, and how many chats this screen knows of each.
// Tapping one opens its profile, which is where its chats are.
function goSettingsDetail(kind, id) {
  host.state.sheet = { kind: 'settings', page: 'agents', detail: { kind, id } }
  host.settingsProfiles.load(kind, id)
  if (kind === 'agent' || kind === 'engineer' && id !== 'default') {
    host.engineerActivity.st.agent = kind === 'agent' ? id : ''
    host.engineerActivity.st.kind = kind === 'engineer' ? 'job' : ''
    host.engineerActivity.st.project = kind === 'engineer' ? id : null
    host.engineerActivity.st.items = []; host.engineerActivity.load()
  }
  host.schedule()
}

function settingsAgents({ back = true } = {}) {
  const st = host.settingsProfiles.st
  // Settings can also open directly rather than through goSettings().
  if (!st.engineers && !st.reviewer && !st.directoryLoading && !st.directoryError) host.settingsProfiles.directory()
  const group = (title, rows) => h('section', { class: 'profile-group', 'aria-label': title }, h('h3', { class: 'kicker', text: title }), h('div', { class: 'rows' }, rows))
  const detail = goSettingsDetail
  const provider = row => host.state.providers.find(p => p.id === row.effective.provider)?.name || row.effective.provider
  if (host.state.sheet?.archivedAgents) return [settingsBack('Agents', 'agents'), settingsHead('Archived'),
    st.directoryError ? h('p', { role: 'alert', class: 'schedule-error', text: st.directoryError }) : null,
    st.archived === null ? h('p', { role: 'status', text: 'Loading archived agents…' }) : st.archived.length ? group('Archived agents', st.archived.map(agent => h('div', { class: 'setrow', key: agent.id },
      blob(agentLook(agent.id, createAgents(st.archived)), { size: 40, font: 18 }), h('div', { class: 'setrow-who' }, h('div', { class: 'setrow-title', text: agent.name })),
      h('button', { class: 'pillbtn', disabled: st.directoryBusy, text: 'Restore', 'aria-label': `Restore ${agent.name}`, onclick: () => host.settingsProfiles.restore(agent.id) })))) : h('p', { class: 'note profile-empty', text: 'No archived agents.' })]
  const systemRows = [
    ...(st.engineers ? [
      ...[...st.engineers.engineers].sort((a, b) => Number(b.platform) - Number(a.platform) || a.project.localeCompare(b.project)).map(row => settingsRow({ title: row.project, sub: 'Engineer', lead: tile(icon.code(18)), right: h('span', { class: 'setrow-value', text: provider(row) }), onclick: () => detail('engineer', row.project) })),
      settingsRow({ title: 'Every other project', sub: 'Default engineer', lead: tile(icon.code(18)), right: h('span', { class: 'setrow-value', text: provider(st.engineers.default) }), onclick: () => detail('engineer', 'default') }),
    ] : []),
    ...(st.reviewer ? [settingsRow({ title: st.reviewer.name, sub: st.reviewer.plain, lead: tile(icon.clock(18)), onclick: () => detail('reviewer', 'reviewer') })] : []),
  ]
  return [back ? settingsBack('Settings', 'root') : null, settingsHead('Agents'),
    h('div', { class: 'agent-directory-actions' }, h('button', { class: 'pillbtn primary', disabled: st.directoryBusy, 'aria-label': 'New agent', onclick: async () => {
      st.directoryBusy = true; st.directoryError = ''; host.schedule()
      try { const thread = await host.api('POST', '/agents/new', {}); host.closeSheet(); await host.threads.open(thread.id || thread.thread?.id); host.threads.agentDrafts.thread = thread.id || thread.thread?.id; await host.threads.loadAgentDrafts() }
      catch (e) { st.directoryError = e.message }
      finally { st.directoryBusy = false; host.schedule() }
    } }, icon.plus(16), st.directoryBusy ? 'Opening…' : 'New agent'), h('button', { class: 'pillbtn', text: 'Archived', onclick: () => { host.state.sheet.archivedAgents = true; host.settingsProfiles.archived(); host.schedule() } })),
    group('Agents', host.state.agents.list.length ? host.state.agents.list.map(agent => settingsRow({ key: agent.id,
      lead: blob(agentLook(agent.id, host.state.agents), { size: 40, font: 18 }), title: agent.name,
      onclick: () => { if (host.state.wide) detail('agent', agent.id); else { host.closeSheet(); host.goProfile(agent.id) } } })) : h('p', { class: 'note profile-empty', text: host.state.agentsReady ? 'No agents are available yet.' : 'Agents could not be loaded.' })),
    st.directoryLoading ? h('p', { class: 'note', role: 'status', text: 'Loading system…' }) : null,
    st.directoryError ? h('div', {}, h('p', { class: 'schedule-error', role: 'alert', text: st.directoryError }), h('button', { class: 'pillbtn', text: 'Try again', onclick: () => host.settingsProfiles.directory() })) : null,
    systemRows.length ? h('section', { class: 'profile-group profile-group-system', 'aria-label': 'System' },
      h('h3', { class: 'kicker', text: 'System' }),
      h('p', { class: 'note profile-system-note', text: 'The engineers who build and the nightly reviewer. Their settings are yours to change too.' }),
      h('div', { class: 'rows' }, systemRows)) : null]
}



// Every provider the host knows, wired or not. "Codex — not wired" is an
// answer; a missing row is a bug report.
function settingsProviders({ back = true, heading = true } = {}) {
  const rows = providerRows(host.state.providers)
  return [
    back ? settingsBack('Settings', 'root') : null,
    heading ? settingsHead('Providers', 'The AI services your agents use.') : null,
    // The same one line the chats carry, at the top of the list: a sign-in
    // about to run out stops every session the host starts, and this is the
    // page it is fixed on.
    ...host.state.providers.map((p) => host.loginNotice(p.id)).filter(Boolean),
    rows.length === 0
      ? h('div', { class: 'reason', text: 'Providers could not be loaded. Try reopening Settings.' })
      : h(
          'div',
          { class: 'rows' },
          ...rows.map((p) =>
            settingsRow({
              key: p.id,
              lead: tile(providerMark(p.id, 20, providerGlyph(p.id)), { kind: `glyph ${p.id}` }),
              title: p.name,
              sub: p.line,
              onclick: () => goSettings('provider', p.id),
            }),
          ),
        ),
    h('div', { class: 'footnote', text: 'Choose a provider and model in any thread.' }),
  ]
}



// One provider's page: the plan if the host named one, the quota it last read,
// and — for one that is not wired — the host's own sentence for why.
function settingsProvider(id) {
  const detail = providerDetail(host.state.providers.find((p) => p.id === id))
  if (!detail) {
    return [settingsBack('Providers', 'providers'), h('div', { class: 'reason', text: 'The host no longer lists that provider.' })]
  }
  return [
    settingsBack('Providers', 'providers'),
    h('div', { class: 'provider-hero' },
      tile(providerMark(detail.id, 20, providerGlyph(detail.id)), { kind: `glyph ${detail.id}` }),
      settingsHead(detail.name, detail.connected ? (detail.plan ? `${detail.plan} · connected` : 'Connected') : 'Not connected')),
    detail.plan
      ? h(
          'div',
          { class: 'card' },
          h('div', { class: 'kicker', text: 'Your plan' }),
          h('div', { class: 'plan-name display', text: detail.plan }),
        )
      : null,
    host.providerSignIn.card(host.state.providers.find((p) => p.id === id), { heading: false }),
    detail.connected ? usageCard(detail.usage, detail.name) : null,
    detail.usageReason ? h('div', { class: 'reason', text: detail.usageReason }) : null,
    detail.connected || host.state.providers.find((p) => p.id === id)?.sign_in
      ? null
      : h(
          'div',
          { class: 'card' },
          // The host's words for why it is not wired, shown rather than reworded.
          h('div', { class: 'reason', text: providerRows(host.state.providers).find(p => p.id === id)?.line || detail.reason }),
        ),
  ]
}



// The quota, as the design's bars.
//
// `usage` is `null` until a reading has been seen — **which is not a zeroed
// page** — so that case says so in words rather than drawing an empty bar,
// which would be a claim about a quota this app has not been told.
function usageCard(usage, name) {
  if (!usage) {
    return h(
      'div',
      { class: 'card' },
      h('div', { class: 'kicker', text: 'Usage' }),
      h('div', { class: 'note', text: 'No quota reading available yet.' }),
    )
  }
  return h(
    'div',
    { class: 'card' },
    h('div', { class: 'kicker', text: 'Usage' }),
    ...usage.windows.map((w) =>
      h(
        'div',
        { class: 'meter' },
        h(
          'div',
          { class: 'meter-head' },
          h('span', { class: 'meter-label', text: w.label }),
          h('span', { class: 'meter-read', text: w.phrase }),
        ),
        h(
          'div',
          { class: 'meter-track' },
          // `utilization` is a fraction: 0.25 is a quarter of the window, and
          // the width is worked out in providers.js and never here.
          h('div', { class: `meter-fill${w.percent !== null && w.percent >= 90 ? ' hot' : ''}`, style: { width: w.width } }),
        ),
      ),
    ),
    usage.windows.length === 0 ? h('div', { class: 'note', text: 'The reading named no windows.' }) : null,
    // Codex's credits, only when the account has some.
    usage.credits ? h('div', { class: 'note credits', text: usage.credits }) : null,
    usage.overage ? h('div', { class: 'reason', text: 'This account is into its overage.' }) : null,
    // A reading from four hours ago is not a current quota, and saying when it
    // was taken costs one line.
    h('div', {
      class: 'reason',
      text: [usage.seenWord ? `Last ${usage.seenWord}` : null, usage.status].filter(Boolean).join(' · '),
    }),
  )
}



// ------------------------------------------------------ settings, integrations

// The dot on the Providers and Integrations tabs: something there needs a
// person. The same thresholds as the notice rows, from the same functions.
function providersWarn() {
  return host.state.providers.some((p) => (loginView(p)?.level ?? 'none') !== 'none')
}

function integrationsWarn() {
  const gh = host.state.github
  return (Array.isArray(gh.accounts) ? gh.accounts.some(account => account.status === 'unhealthy' || githubNotice(account) !== null) : githubNotice(gh.data) !== null) || integrationSections().some(entry => host.registry.invoke(entry, 'notice', {}) != null)
}



// "a", "a and b", "a, b and c".
function listWords(items) {
  return items.length < 2 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items.at(-1)}`
}

function goIntegration(id, account = null) {
  goSettings('integration', id)
  host.state.sheet.account = account
}

function settingsIntegrations({ back = true } = {}) {
  const services = [...integrationCatalog(host.state), ...integrationSections().flatMap(entry => {
    const summary = host.registry.invoke(entry, 'summary', {})
    return summary ? [{ ...summary, id: entry.id, name: entry.title, mark: typeof entry.icon === 'function' ? host.registry.invoke(entry, 'icon', 20) : (icon[entry.icon] ?? icon.code)(20) }] : []
  })]
  return [back ? settingsBack('Settings', 'root') : null,
    settingsHead('Integrations', 'Connect the services your agents work with.'),
    ...[['Connected', true], ['Available', false]].map(([label, connected]) => {
      const group = services.filter(service => service.connected === connected)
      return group.length ? h('section', { class: 'settings-section', 'aria-label': label },
        h('h3', { class: 'kicker', text: label }), h('div', { class: 'rows' }, ...group.map(service => settingsRow({
          key: service.id, lead: tile(service.mark ?? providerMark(service.id, 20), { kind: `glyph ${service.id}` }),
          title: service.name, sub: service.summary, onclick: () => goIntegration(service.id) })))) : null
    }),
    !services.length ? h('p', { class: 'note', text: 'No integrations are available yet.' }) : null]
}

function integrationBack(label, id) {
  return h('button', { class: 'backbtn', onclick: () => goIntegration(id) }, icon.chevronLeft(18), label)
}

function integrationFold(key, title, children, props = {}) {
  const sheet = host.state.sheet
  return h('details', { ...props, key, open: Boolean(sheet.folds?.[key]), ontoggle: event => {
    if (host.state.sheet === sheet && event.currentTarget.isConnected) (sheet.folds ??= {})[key] = event.currentTarget.open
  } }, h('summary', { text: title, onclick: event => {
    if (host.state.sheet === sheet) (sheet.folds ??= {})[key] = !event.currentTarget.parentElement.open
  } }), ...children)
}

function integrationSections() {
  return host.registry.entries('settings.section').filter(entry => entry.group === 'integrations')
}

function settingsIntegration(id, accountId = null) {
  const extension = integrationSections().find(entry => entry.id === id)
  if (extension) return host.registry.invoke(extension, 'render', { back: true, sheet: { ...host.state.sheet, account: accountId } }) ?? []
  if (id === 'github' && Object.hasOwn(host.state.github, 'accounts')) return [settingsBack('Integrations', 'integrations'), settingsHead('GitHub'),
    ...githubOutcome(host.state.github.at),
    host.state.github.installUrl || host.state.github.orgInstall ? h('button', { class: 'pillbtn primary', text: 'Install on GitHub', onclick: () => host.installGithub('install', host.state.github.orgInstall?.org ?? null) }) : null,
    ...githubAccounts.view()]
  const service = integrationCatalog(host.state).find(service => service.id === id)
  if (!service) return [settingsBack('Integrations', 'integrations'), settingsHead('Integration unavailable')]
  const account = service.accounts.find(account => String(account.id) === String(accountId))
  if (account) return [integrationBack(service.name, id), settingsHead(account.name),
    githubAccountCard(account)]
  const gh = host.state.github
  return [settingsBack('Integrations', 'integrations'), settingsHead(service.name),
    service.accounts.length ? h('div', { class: 'rows' }, ...service.accounts.map(account => settingsRow({
      key: account.id, title: account.name, sub: account.scope || (['authorized', 'unhealthy'].includes(account.record.status) ? 'Connected' : 'Finish connecting'), onclick: () => goIntegration(id, account.id) }))) : null,
    [
      !service.accounts.length ? githubCard({ heading: false }) : null,
      service.accounts.length && gh.links ? githubLinks(gh.links) : null,
      service.accounts.length && !offersOrgs(gh.data) ? h('p', { class: 'note', text: 'Adding another account is not available yet.' }) : null,
      offersOrgs(gh.data) && !gh.links ? connectCard() : null,
      service.accounts.length ? integrationFold('github-manage', 'Manage all GitHub accounts', [
        h('p', { class: 'note', text: 'Refresh checks every account. Disconnect removes all GitHub connections.' }),
        ...githubOutcome('github'), gh.confirm ? githubConfirm() : githubPills({ actions: ['refresh', 'disconnect'] })], { class: 'integration-shared-actions' }) : null,
    ]]
}

function githubAccountCard(account) {
  const own = account.record, view = account.org ? orgView(own) : githubView(own)
  const until = own.user?.refresh_expires_at
  const renewal = until && Number.isFinite(Date.parse(until)) ? formatDate(until, { year: 'numeric', month: 'short', day: 'numeric' }) : null
  return h('div', { class: `card integration${account.org ? ' org' : ''}`, key: account.id },
    account.scope ? h('p', { class: 'ghline note', text: account.scope }) : null,
    h('p', { class: 'ghuses note', text: 'Used for repositories and engineering work.' }),
    h('div', { class: `setrow-sub ghword${own.status === 'unhealthy' ? ' warn' : ''}`, text: own.status === 'unhealthy' ? 'Needs reconnecting' : view.connected || own.status === 'authorized' ? 'Connected' : account.org ? view.word : 'Finish connecting this account' }),
    own.user?.login ? h('div', { class: 'ghline note', text: `Signed in as ${own.user.login}` }) : null,
    renewal && !own.health?.last_error ? h('div', { class: 'note ghquiet', text: `Sign in again by ${renewal}` }) : null,
    own.health?.last_error ? h('div', { class: 'note ghquiet warn', text: own.health.last_error }) : null,
    ...githubOutcome(account.org || 'github'),
    h('div', { class: 'ghpills' }, ...view.actions.filter(action => !['refresh', 'disconnect'].includes(action)).map(action => h('button', {
      class: 'pillbtn content-mark ghpill', text: GH_PILLS[action].label, disabled: host.state.github.busy !== null,
      onclick: () => action === 'setup' ? host.setupGithub() : host.installGithub(action, account.org) }))),
    integrationFold(`${account.id}-connection`, 'Connection details', [
      own.app?.url ? h('a', { class: 'ghline quietlink', href: own.app.url, target: '_blank', rel: 'noopener noreferrer', text: `GitHub App: ${own.app.slug || 'Manage access'}` }) : null]),
    host.state.github.links ? githubLinks(host.state.github.links) : integrationCatalog(host.state).find(service => service.id === 'github')?.connected ? integrationFold(`${account.id}-manage`, 'Refresh or disconnect', [
      h('p', { class: 'note', text: 'These actions apply to every GitHub account.' }),
      host.state.github.confirm ? githubConfirm() : githubPills({ actions: ['refresh', 'disconnect'] })], { class: 'integration-shared-actions' }) : null)
}

// The card's last word on the last thing tapped, on the card it was tapped on:
// the broker's sentence in the warning ink, or what happened.
function githubOutcome(at) {
  const gh = host.state.github
  if (gh.at !== at) return [null, null]
  return [
    gh.error ? h('div', { class: 'note warn gherror', role: 'status', text: gh.error }) : null,
    gh.done && !gh.error ? h('div', { class: 'note ghdone', text: gh.done }) : null,
  ]
}

// *Connect an organization*: a pill, and then the organization's login and an
// optional name for its app. It is drawn whatever the first app's state, so a
// connected page still has a way in.
function connectCard() {
  const gh = host.state.github
  const busy = gh.busy === 'connect'
  if (!gh.connect) {
    return h(
      'div',
      { class: 'integration connect', key: 'connect' },
      h('div', { class: 'ghpills' }, h('button', {
        class: 'pillbtn content-mark ghpill',
        type: 'button',
        disabled: gh.busy !== null,
        text: 'Add an account',
        onclick: () => {
          host.state.github = { ...host.state.github, connect: true, at: 'connect', error: '', done: '' }
          host.schedule()
          setTimeout(() => host.orgField.focus?.(), 0)
        },
      })),
    )
  }
  return h(
    'div',
    { class: 'card integration connect', key: 'connect' },
    h('div', { class: 'setrow-title', text: 'Add an account' }),
    h('div', { class: 'note', text: 'Enter a GitHub organization you own, then finish connecting on GitHub.' }),
    h('div', { class: 'field flat' }, kept(host.orgField)),
    h('div', { class: 'field flat' }, kept(host.orgAppField)),
    ...githubOutcome('connect'),
    h(
      'div',
      { class: 'ghpills' },
      h('button', { class: 'pillbtn primary ghpill', type: 'button', disabled: gh.busy !== null, text: busy ? 'Opening GitHub…' : 'Continue to GitHub', onclick: host.connectOrg }),
      h('button', {
        class: 'pillbtn content-mark ghpill',
        type: 'button',
        disabled: busy,
        text: 'Cancel',
        onclick: () => {
          host.state.github = { ...host.state.github, connect: false, error: gh.at === 'connect' ? '' : gh.error }
          host.schedule()
        },
      }),
    ),
  )
}



// GitHub's card: its mark and name; what the state has, in the order it gained
// it; one quiet line; what Loam uses it for; and the pills that apply.
function githubCard({ heading = true } = {}) {
  const gh = host.state.github, view = githubView(gh.data)
  return h('div', { class: 'card integration', key: 'github' },
    heading ? h('div', { class: 'provider-hero' }, tile(providerMark('github', 20), { kind: 'glyph github' }), h('div', { class: 'setrow-title', text: 'GitHub' })) : null,
    view?.status === 'unregistered' ? h('p', { class: 'ghline note', text: 'Not connected' }) : null,
    gh.data?.app?.url ? integrationFold('github-connection', 'Connection details', [h('a', { class: 'ghline quietlink', href: gh.data.app.url, target: '_blank', rel: 'noopener noreferrer', text: `GitHub App: ${gh.data.app.slug}` })]) : null,
    h('p', { class: 'ghuses note', text: gh.error || (!view ? gh.loaded ? 'The connection could not be read.' : 'Loading connection…' : 'Connect GitHub to create repositories and share engineering work.') }),
    ...githubOutcome('github'), gh.links ? githubLinks(gh.links) : gh.confirm ? githubConfirm() : view ? githubPills(view) : null)
}

const GH_PILLS = {
  setup: { label: 'Set up on GitHub', going: 'Opening GitHub…', run: () => host.setupGithub() },
  install: { label: 'Install', going: 'Opening GitHub…', run: () => host.installGithub('install') },
  reconnect: { label: 'Reconnect', going: 'Opening GitHub…', run: () => host.installGithub('reconnect') },
  refresh: { label: 'Refresh', going: 'Refreshing…', run: () => host.refreshGithub() },
  disconnect: {
    label: 'Disconnect',
    going: 'Disconnecting…',
    run: () => {
      host.state.github = { ...host.state.github, confirm: true, error: '', done: '' }
      host.schedule()
    },
  },
}



// The host's forget forgets every app it holds, so with an organization's app
// beside the first one the pill says so: there is no forgetting one here.
function githubPills(view) {
  const busy = host.state.github.busy
  const all = orgApps(host.state.github.data).length > 0
  return h(
    'div',
    { class: 'ghpills' },
    ...view.actions.map((id) =>
      h('button', {
        class: `pillbtn content-mark ghpill${id === 'disconnect' ? ' danger' : ''}`,
        key: id,
        type: 'button',
        disabled: busy !== null,
        text: busy === id ? GH_PILLS[id].going : id === 'disconnect' && all ? 'Disconnect all accounts' : id === 'refresh' && all ? 'Refresh all accounts' : GH_PILLS[id].label,
        onclick: GH_PILLS[id].run,
      }),
    ),
  )
}

function githubConfirm() {
  const busy = host.state.github.busy === 'disconnect'
  const orgs = orgApps(host.state.github.data).map((o) => o.org)
  const also = orgs.length ? ` This includes ${listWords(orgs)}.` : ''
  return h(
    'div',
    { class: 'loginflow ghconfirm' },
    h('div', { class: 'signline', text: `Disconnect all GitHub accounts? Agents will lose access to their repositories until you reconnect.${also}` }),
    h(
      'div',
      { class: 'ghpills' },
      h('button', { class: 'pillbtn primary ghpill danger', type: 'button', disabled: busy, text: busy ? 'Disconnecting…' : orgs.length ? 'Disconnect all accounts' : 'Disconnect', onclick: host.disconnectGithub }),
      h('button', {
        class: 'pillbtn content-mark ghpill',
        type: 'button',
        disabled: busy,
        text: 'Keep it',
        onclick: () => {
          host.state.github = { ...host.state.github, confirm: false }
          host.schedule()
        },
      }),
    ),
  )
}



// The last step, which only they can take: GitHub still has the app, its
// installation and their authorization until they remove them there.
function githubLinks(links) {
  const rows = [
    { href: links.uninstall_url, text: 'Uninstall the app from your account' },
    { href: links.revoke_url, text: 'Revoke your authorization' },
    { href: links.delete_app_url, text: 'Delete the app' },
    // An organization's app, forgotten with the rest: its page on GitHub,
    // where an owner of the organization uninstalls and deletes it.
    ...(Array.isArray(links.orgs) ? links.orgs : []).map((o) => ({ href: o.url, text: `${o.org}’s app: uninstall and delete it on GitHub` })),
  ].filter((r) => typeof r.href === 'string' && r.href !== '')
  return h(
    'div',
    { class: 'loginflow ghlinks' },
    h('div', { class: 'signline', text: 'Loam has forgotten it. To finish on GitHub:' }),
    ...rows.map((r) => h('a', { class: 'loginlink', key: r.text, href: r.href, target: '_blank', rel: 'noopener noreferrer', text: r.text })),
    h('button', {
      class: 'pillbtn content-mark ghpill',
      type: 'button',
      text: 'Done',
      onclick: () => {
        host.state.github = { ...host.state.github, links: null }
        host.schedule()
      },
    }),
  )
}



return Object.defineProperties({}, {
    settingsBody: { enumerable: true, get: () => settingsBody },
    settingsSheet: { enumerable: true, get: () => settingsSheet },
    settingsItems: { enumerable: true, get: () => settingsItems },
    settingsDialog: { enumerable: true, get: () => settingsDialog },
    settingsPlugins: { enumerable: true, get: () => settingsPlugins },
    settingsAppearance: { enumerable: true, get: () => settingsAppearance },
    goSettings: { enumerable: true, get: () => goSettings },
    settingsBack: { enumerable: true, get: () => settingsBack },
    settingsHead: { enumerable: true, get: () => settingsHead },
    tile: { enumerable: true, get: () => tile },
    settingsRoot: { enumerable: true, get: () => settingsRoot },
    filesFolderFields: { enumerable: true, get: () => filesFolderFields },
    settingsFiles: { enumerable: true, get: () => settingsFiles },
    settingsAgents: { enumerable: true, get: () => settingsAgents },
    goSettingsDetail: { enumerable: true, get: () => goSettingsDetail },
    settingsProviders: { enumerable: true, get: () => settingsProviders },
    settingsAccounts: { enumerable: true, get: () => settingsAccounts },
    apiKeys: { enumerable: true, get: () => apiKeys },
    settingsProvider: { enumerable: true, get: () => settingsProvider },
    usageCard: { enumerable: true, get: () => usageCard },
    providersWarn: { enumerable: true, get: () => providersWarn },
    integrationsWarn: { enumerable: true, get: () => integrationsWarn },
    listWords: { enumerable: true, get: () => listWords },
    settingsIntegrations: { enumerable: true, get: () => settingsIntegrations },
    githubOutcome: { enumerable: true, get: () => githubOutcome },
    connectCard: { enumerable: true, get: () => connectCard },
    githubCard: { enumerable: true, get: () => githubCard },
    GH_PILLS: { enumerable: true, get: () => GH_PILLS },
    githubPills: { enumerable: true, get: () => githubPills },
    githubConfirm: { enumerable: true, get: () => githubConfirm },
    githubLinks: { enumerable: true, get: () => githubLinks },
})
}
