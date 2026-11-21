import { createUpdate } from '../update.js'
import { settingsParent } from '../app-modal-history.js'
import { createPasskey } from '../passkey.js'
import { settingsHome, settingsSection, CONNECTIONS_GROUP } from "../app-shape.js"
import { owner, ownerInitial } from "../owner.js"
import { h, icon, kept, blob } from "../ui.js"
import { settingsNav, segmentedControl, settingsRow } from "../chat-ui.js"
import { emptyState } from "../empty-state.js"
import { connectedCount, providerRows, providerDetail, loginView } from "../providers.js"
import { nameOf as fileNameOf, ROOT_LOOK } from "../files.js"
import { agentLook, createAgents, providerGlyph } from "../sessions.js"
import { providerMark } from "../marks.js"
import { githubNotice, orgApps, offersOrgs, orgView, githubView } from "../integrations.js"
import { integrationCatalog } from '../integration-catalog.js'
import { createGithubAccounts } from '../github-accounts.js'
import { createGitIdentity } from '../git-identity.js'
import { formatDate } from '../time.js'
import { themeImageURL, themeImagePosition } from '../theme-image.js'
import { themePreview } from '../theme-preview.js'
import { shownOptions } from '../theme-options.js'
import { fileShortcuts, toggleFileShortcut } from '../store.js'
import { createApiKeys } from '../api-keys.js'

// About Loam's server card, in words.
const GB = 1024 ** 3
const size = bytes => bytes >= 100 * GB ? `${Math.round(bytes / GB)} GB` : bytes >= GB ? `${(bytes / GB).toFixed(1).replace(/\.0$/, '')} GB` : `${Math.round(bytes / 1024 ** 2)} MB`
const upFor = seconds => {
  const days = Math.floor(seconds / 86400), hours = Math.floor(seconds / 3600), minutes = Math.max(1, Math.floor(seconds / 60))
  return days >= 1 ? `${days} ${days === 1 ? 'day' : 'days'}` : hours >= 1 ? `${hours} ${hours === 1 ? 'hour' : 'hours'}` : `${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`
}
export function serverRows(facts, address = globalThis.location?.host ?? '') {
  if (!facts) return []
  return [
    ['Address', address || null],
    ['Name', facts.name || null],
    ['System', facts.system ? `${facts.system}${facts.arch ? ` · ${facts.arch}` : ''}` : null],
    ['Processors', Number.isFinite(facts.processors) ? `${facts.processors} ${facts.processors === 1 ? 'core' : 'cores'}` : null],
    ['Memory', Number.isFinite(facts.memory?.total) ? `${size(facts.memory.total)}${Number.isFinite(facts.memory.free) ? ` · ${size(facts.memory.free)} free` : ''}` : null],
    ['Storage', Number.isFinite(facts.storage?.total) ? `${size(facts.storage.free)} free of ${size(facts.storage.total)}` : null],
    ['Running for', Number.isFinite(facts.up_seconds) ? upFor(facts.up_seconds) : null],
  ].filter(([, value]) => value)
}
// Mounted once. Cross-module state and operations are explicit host services.
export function mount(host) {
const failedThemeImages = new Set()
const settingsEvents = host.registry?.scope?.({ id: '@core' })?.events
const updates = createUpdate({ api: (...args) => host.api(...args), changed: () => host.schedule() })
const passkey = createPasskey({ changed: () => host.schedule() })
settingsEvents?.on('settings.opened', ({ opening, page }) => { if (opening || page === 'about') updates.check() })
settingsEvents?.on('sheet.changing', ({ sheet }) => { if (sheet?.kind !== 'settings' || sheet.page !== 'account') passkey.stop() })
settingsEvents?.on('page.leaving', () => { updates.stop(); passkey.stop() })

// API keys by provider, entered once (2026-11-18): the third group of
// Connections, and one provider's page under it. Its list and its pages are
// api-keys.js's, drawn as they were designed; only where they sit moved.
const apiKeys = createApiKeys({ api: (...args) => host.api(...args), changed: () => host.schedule(), openPlugin: id => goSettings('plugins', id) })
function settingsApiKey() {
  const provider = host.state.sheet?.provider ?? null
  return apiKeys.view({ provider, back: settingsBack('Connections', 'connections'), open: id => goSettings('api-key', id) })
}

// Settings › Account (2026-11-18): who is signed in, the password, the
// passkeys and Sign out. The Loam account will live here too, later.
const account = { data: null, loading: false, failed: false }
async function loadAccount() {
  if (account.loading) return
  account.loading = true
  try { account.data = await host.api('GET', '/account'); account.failed = false }
  catch { account.failed = true }
  finally { account.loading = false; host.schedule() }
}
function accountName() {
  const me = owner(), name = account.data?.name || (me.id ? me.name : null)
  return { name: name || account.data?.username || 'You', username: account.data?.username ?? null }
}
function settingsAccount({ back = true } = {}) {
  if (!account.data && !account.loading && !account.failed) loadAccount()
  if (!passkey.st.listed && !passkey.st.listing) passkey.loadPasskeys()
  const who = accountName()
  return [back ? settingsBack('Settings', 'root') : null, settingsHead('Account'),
    h('section', { class: 'card account-card', 'aria-label': 'Signed in' },
      blob({ initial: Array.from(who.name.trim())[0]?.toLocaleUpperCase() || ownerInitial(), color: 'var(--latte-espresso)', ink: 'var(--latte-espresso-ink)', shape: '50%' }, { size: 52, font: 22 }),
      h('div', { class: 'account-who' },
        h('div', { class: 'account-name', text: who.name }),
        h('div', { class: 'note account-username', text: who.username ? `Signs in as ${who.username}` : 'Signed in to this Loam' }))),
    passkey.view({ password: true })]
}

// Settings › About Loam (2026-11-18): the version and its update, and the
// machine Loam runs on (GET /api/server, read where the app runs).
const server = { data: null, loading: false, failed: false }
async function loadServer() {
  if (server.loading) return
  server.loading = true
  try { server.data = await host.api('GET', '/server'); server.failed = false }
  catch { server.failed = true }
  finally { server.loading = false; host.schedule() }
}
function settingsAbout({ back = true } = {}) {
  if (!server.data && !server.loading && !server.failed) loadServer()
  const rows = serverRows(server.data)
  return [back ? settingsBack('Settings', 'root') : null, settingsHead('About Loam'),
    updates.view(),
    h('section', { class: 'card about-server', 'aria-labelledby': 'about-server' },
      h('h3', { class: 'kicker', id: 'about-server', text: 'This server' }),
      rows.length ? h('dl', { class: 'about-facts' }, ...rows.flatMap(([label, value]) => [h('dt', { text: label }), h('dd', { text: value })]))
        : h('p', { class: 'note', role: 'status', text: server.failed ? 'This server’s details couldn’t be read.' : 'Reading this server…' }))]
}

const githubAccounts = createGithubAccounts({ state: () => host.state.github, api: (...args) => host.api(...args), changed: () => host.schedule(), reload: () => host.loadGithub() })
const gitIdentity = createGitIdentity({ api: (...args) => host.api(...args), changed: () => host.schedule() })
function settingsBody(sheet, back) {
  const page = settingsHome(sheet.page, sheet.provider)
  if (page === 'agents' && sheet.detail) return [settingsBack('Agents', 'agents'), ...(sheet.detail.kind === 'agent'
    ? host.settingsProfileUI.body(sheet.detail.id) : host.settingsProfileUI.settingsDetail())]
  if (page === 'provider') return settingsProvider(sheet.provider)
  if (page === 'integration') return settingsIntegration(sheet.provider, sheet.account)
  if (page === 'api-key') return sheet.provider ? settingsApiKey() : settingsConnections({ back })
  const section = host.registry.entries('settings.section').find(entry => !entry.component && entry.id === page && (!entry.children || host.registry.entries('settings.section').some(child => child.group === entry.children)))
  return section ? host.registry.invoke(section, 'render', { back, sheet }) : settingsRoot()
}

function settingsSheet(...args) { return host.registry.component('core.settings', { args, variant: 'settingsSheet', defaultView: () => builtinSettingsSheet(...args) }) }

function builtinSettingsSheet(sheet) {
  return h('div', { class: 'sheet settings' }, host.sheetTop(), host.runNotice(), ...settingsBody(sheet, true))
}

function settingsItems() {
  return host.registry.entries('settings.section').filter(item => !item.component && !item.group && (!item.children || host.registry.entries('settings.section').some(child => child.group === item.children))).map(item => ({ ...item, label: item.title, glyph: typeof item.icon === 'function' ? item.icon(16) : (icon[item.icon] ?? icon.code)(16), dot: item.id === 'connections' && (providersWarn() || apiKeys.warn() || integrationsWarn()) }))
}

function settingsDialog(...args) { return host.registry.component('core.settings', { args, variant: 'settingsDialog', defaultView: () => builtinSettingsDialog(...args) }) }

function builtinSettingsDialog(sheet) {
  const page = settingsHome(sheet.page === 'root' ? 'account' : sheet.page, sheet.provider)
  return h('div', { class: 'sheet settings twopane' },
    settingsNav({ title: 'Settings', items: settingsItems(), active: settingsSection(page), choose: item => goSettings(item.id), footer: host.marketDoor({ nav: true }) }),
    h('div', { class: 'setmain' },
      h('header', { class: 'app-modal-head settings-modal-close' },
        h('button', { class: 'iconbtn ghost', title: 'Close', 'aria-label': 'Close settings', onclick: () => host.closeSheet({ animate: true }) }, icon.close(14))),
      h('div', { class: 'app-modal-body', key: `settings:${page}:${sheet.detail?.kind ?? ''}:${sheet.detail?.id ?? ''}:${sheet.provider ?? ''}:${sheet.account ?? ''}` }, host.runNotice(), ...settingsBody({ ...sheet, page }, sheet.page !== 'root'))))
}

function settingsPlugins(options = {}) { return host.pluginSettingsUI.view(options) }


function settingsAppearance({ back = true } = {}) {
  const { mode, transition = 'normal', size, theme: look } = host.theme.value, status = host.theme.status
  const chosen = host.theme.installed.find(t => t.id === look)
  const latte = host.theme.value.options?.[look]?.['agent-shapes'] === 'latte-art'
  const signature = host.theme.value.options?.[look]?.['agent-shapes'] === 'signature'
  const themeOptions = chosen?.options ? shownOptions(chosen.options, host.theme.value.options?.[look] ?? {}) : undefined
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
          themePreview(option.id, { cls: showImage ? 'has-image' : '' },
            showImage ? h('img', { class: 'theme-cover', src: image, alt: '', loading: 'lazy', decoding: 'async', width: 640, height: 427,
              style: { objectPosition: themeImagePosition(option, host.theme.current) },
              onerror: e => { failedThemeImages.add(image); e.currentTarget.hidden = true; e.currentTarget.parentElement.classList.remove('has-image'); host.schedule() } }) : null,
            h('span', { class: 'theme-choice-label' }, h('span', { class: 'theme-choice-name', text: option.name })),
            look === option.id ? h('span', { class: 'theme-choice-check' }, icon.check(13)) : null))
      }))),
    host.marketFindMore('theme', 'Find more themes'),
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
  const group = CONNECTIONS_GROUP[page] ?? null
  if ((page === 'backup' || page === 'git-backup') && !provider) provider = 'git-backup'
  page = settingsHome(page, provider)
  if (page !== 'provider' || host.state.sheet?.provider !== provider) host.providerSignIn.cancel()
  if (!host.state.sheet) updates.check()
  const sheet = { kind: 'settings', page, provider, ...(group ? { group } : {}) }
  host.registry.emit('sheet.changing', { previous: host.state.sheet, sheet })
  host.state.sheet = sheet
  if (page === 'plugins') syncSettingsPlugin(provider)
  if (page === 'account') { passkey.loadPasskeys(); loadAccount() }
  if (page === 'about') loadServer()
  if (page === 'connections' || page === 'api-key' || page === 'root') apiKeys.load(page === 'connections')
  if (page === 'chats') host.sendKeys.refresh()
  host.registry.emit('settings.opened', { page })
  if (page === 'agents') host.settingsProfiles.directory()
  host.schedule()
}

function syncSettingsPlugin(provider) {
  host.pluginSettingsUI?.checkUpdates?.()
  if (!provider) {
    host.pluginSettingsUI?.clear()
    host.pluginLoader?.refresh()
    return
  }
  const openPlugin = () => {
    const sheet = host.state.sheet
    if (sheet?.kind !== 'settings' || sheet.page !== 'plugins' || sheet.provider !== provider) return
    const rows = host.pluginLoader?.st.rows ?? []
    const row = rows.find(r => r.id === provider && (r.effective || r.broken)) ?? rows.find(r => r.id === provider)
    if (row && host.pluginSettingsUI?.st.selected?.key !== row.key) host.pluginSettingsUI?.open(row)
  }
  if (host.pluginLoader?.st.rows.some(r => r.id === provider)) openPlugin()
  else host.pluginLoader?.refresh()?.then(openPlugin).catch(() => {})
}

function settingsBack(label, page, props) {
  return h('button', { class: 'backbtn', ...props, onclick: () => goSettings(page) }, icon.chevronLeft(18), label)
}

function backInSettings() {
  const sheet = host.state.sheet
  const parent = sheet?.kind === 'settings' && settingsParent(sheet)
  if (!parent) return false
  goSettings(parent.page, parent.provider)
  return true
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

function providerCount() {
  const { connected } = connectedCount(host.state.providers)
  return connected ? `${connected} AI ${connected === 1 ? 'provider' : 'providers'}` : null
}

function settingsChats({ back = true } = {}) {
  const keys = host.sendKeys
  return [back ? settingsBack('Settings', 'root') : null, settingsHead('Chats'),
    h('section', { class: 'rows' },
      h('div', { class: 'setrow chat-send-setting' },
        h('div', { class: 'setrow-who' },
          h('label', { for: 'chat-send-key', class: 'setrow-title', text: 'Send messages with' }),
          h('div', { id: 'chat-send-help', class: 'setrow-sub', text: keys.value === 'modifier'
            ? 'Enter starts a new line.' : 'Shift+Enter starts a new line.' })),
        h('select', { id: 'chat-send-key', class: 'field-input', 'aria-describedby': 'chat-send-help',
          disabled: keys.writing || keys.value === null, value: keys.value ?? '',
          onchange: event => keys.set(event.currentTarget.value) },
          keys.value === null ? h('option', { value: '', text: 'Loading…', selected: true }) : null,
          ...[['enter', 'Enter'], ['modifier', keys.modifierLabel]].map(([value, label]) =>
            h('option', { value, text: label, selected: value === keys.value })))),
      keys.error ? h('p', { class: 'note warn', role: 'alert', text: keys.error }) : null)]
}

function settingsRoot() {
  const who = accountName()
  const services = integrationCatalog(host.state).filter(service => service.connected).map(service => service.name)
  const version = updates.st.data?.current?.version
  const summaries = { account: who.username && who.username !== who.name ? `${who.name} · ${who.username}` : who.name,
    appearance: `${({ system: 'System', sun: 'Sun', dark: 'Dark', light: 'Light' })[host.theme.value.mode]} · ${host.theme.value.size}%`,
    chats: 'Message shortcuts', agents: `${host.state.agents.list.length} agents`,
    connections: [providerCount(), ...services, apiKeys.st.list ? apiKeys.summary() : null].filter(Boolean).join(' · ') || 'AI providers, services and API keys',
    files: 'Shortcuts and folders', plugins: 'Themes and extensions',
    about: updates.st.data?.check?.available ? `Loam ${updates.st.data.check.latest?.version} is ready` : version ? `Version ${version}` : 'Version and updates' }
  const jobs = host.activity.st.now
  const working = host.running.rows().filter(row => row.state === 'working').length
    + (jobs?.slots.filter(job => !job.free).length ?? 0) + (jobs?.landing.length ?? 0)
  return [settingsHead('Settings'), !host.state.wide ? h('div', { class: 'rows settings-place' }, settingsRow({
    lead: tile(icon.clock(16)), title: 'Activity', sub: working ? `${working} working` : 'Nothing running', onclick: host.openPhoneActivity })) : null,
    h('div', { class: 'rows' }, ...settingsItems().map(item => settingsRow({
    lead: tile(item.glyph), title: item.label, sub: summaries[item.id], dot: item.dot, onclick: () => goSettings(item.id) }))),
    host.marketDoor()]
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
  const archive = host.files?.st?.archive ?? null
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
    h('section', { class: 'settings-section', 'aria-label': 'Your data' },
      h('h3', { class: 'kicker', text: 'Your data' }),
      h('div', { class: 'rows' },
        h('div', { class: 'setrow fs-export-row' },
          h('div', { class: 'setrow-who' },
            h('div', { class: 'setrow-title', text: 'Download as zip' }),
            h('div', { class: 'setrow-sub', text: 'Your files in open formats, ready to take with you.' })),
          h('div', { class: 'fs-export-actions' },
            h('button', { class: 'pillbtn content-mark fs-export-btn', type: 'button', disabled: Boolean(archive?.busy), text: archive?.busy && archive.key === 'vault:' ? 'Preparing Vault…' : 'Download the Vault', onclick: () => host.files.downloadArchive('vault', '') }),
            h('button', { class: 'pillbtn content-mark fs-export-btn', type: 'button', disabled: Boolean(archive?.busy), text: archive?.busy && archive.key === 'spaces:' ? 'Preparing AI Spaces…' : 'Download AI Spaces', onclick: () => host.files.downloadArchive('spaces', '') }))),
        archive && ['vault:', 'spaces:'].includes(archive.key) && (archive.error || archive.status)
          ? h('div', { class: `setrow fs-export-feedback${archive.error ? ' warn' : ''}`, role: archive.error ? 'alert' : 'status' },
              h('span', { class: 'note', text: archive.error || archive.status }))
          : null)),
  ]
}



// The personas the host has, and how many chats this screen knows of each.
// Tapping one opens its profile, which is where its chats are.
function goSettingsDetail(kind, id, options) {
  host.state.sheet = { kind: 'settings', page: 'agents', detail: { kind, id } }
  host.settingsProfiles.load(kind, id, options)
  if (kind === 'agent' || kind === 'engineer' && id !== 'default') {
    host.engineerActivity.st.agent = kind === 'agent' ? id : ''
    host.engineerActivity.st.kind = kind === 'engineer' ? 'job' : ''
    host.engineerActivity.st.project = kind === 'engineer' ? id : null
    host.engineerActivity.st.items = []; host.engineerActivity.load()
  }
  host.schedule()
}

// One quiet row (2026-11-19): the agent Loam tells when something that is
// no one agent's needs attention. It decides whether you hear of it.
function attentionRow(st) {
  const agents = host.state.agents.list, value = st.attention?.persona
  if (!st.attention || !agents.length) return null
  const hintId = 'attention-hint'
  return h('section', { class: 'profile-group attention-group', 'aria-label': 'When Loam needs attention' }, h('div', { class: 'rows' },
    h('label', { class: 'profile-choice profile-choice-hinted' }, h('span', { class: 'setrow-title', text: 'Hears when Loam needs attention' }),
      h('span', { class: 'settings-select' }, h('select', { 'aria-label': 'Hears when Loam needs attention', 'aria-describedby': hintId, disabled: st.attentionBusy,
        onchange: e => host.settingsProfiles.hears(e.target.value === st.attention.default ? null : e.target.value) },
        agents.map(agent => h('option', { value: agent.id, selected: agent.id === value, text: agent.name }))), icon.chevronDown(14)),
      h('span', { class: 'setrow-sub profile-choice-hint', id: hintId, text: 'They hear of problems no one agent owns, and tell you if you need to know.' }))),
    st.attentionError ? h('p', { class: 'schedule-error', role: 'alert', text: st.attentionError }) : null)
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
      h('button', { class: 'pillbtn', disabled: st.directoryBusy, text: 'Restore', 'aria-label': `Restore ${agent.name}`, onclick: () => host.settingsProfiles.restore(agent.id) })))) : emptyState({ object: 'archive', title: 'No archived agents', body: 'Agents you archive wait here, ready to restore.' })]
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
      onclick: () => detail('agent', agent.id) })) : h('p', { class: 'note profile-empty', text: host.state.agentsReady ? 'No agents are available yet.' : 'Agents could not be loaded.' })),
    attentionRow(st),
    st.directoryLoading ? h('p', { class: 'note', role: 'status', text: 'Loading system…' }) : null,
    st.directoryError ? h('div', {}, h('p', { class: 'schedule-error', role: 'alert', text: st.directoryError }), h('button', { class: 'pillbtn', text: 'Try again', onclick: () => host.settingsProfiles.directory() })) : null,
    systemRows.length ? h('section', { class: 'profile-group profile-group-system', 'aria-label': 'System' },
      h('h3', { class: 'kicker', text: 'System' }),
      h('p', { class: 'note profile-system-note', text: 'The engineers who build and the nightly reviewer. Their settings are yours to change too.' }),
      h('div', { class: 'rows' }, systemRows)) : null]
}



// Every provider the host knows, wired or not. "Codex — not wired" is an
// answer; a missing row is a bug report.
function settingsProviders({ back = true, heading = true, footnote = true } = {}) {
  const rows = providerRows(host.state.providers)
  return [
    back ? settingsBack('Settings', 'root') : null,
    heading ? settingsHead('AI providers', 'The AI services your agents use.') : null,
    // The same one line the chats carry, at the top of the list: a sign-in
    // about to run out stops every session the host starts, and this is the
    // page it is fixed on.
    ...(host.state.agentAvailability?.agents_can_run === false ? [] : host.state.providers.map((p) => host.loginNotice(p.id)).filter(Boolean)),
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
    footnote ? h('div', { class: 'footnote', text: 'Choose a provider and model in any thread.' }) : null,
  ]
}



// Settings › Connections (2026-11-18): one page, three groups, each a
// kicker and its rows, in the order a person reaches for them: the AI the
// agents run on, the services they work with, the keys the plugins use. An
// old link to Providers, Integrations or the API keys opens here, at its
// group (sheet.group).
function connectionsGroup(id, title, why, children) {
  return h('section', { class: 'settings-section connections-group', id: `connections-${id}`, 'aria-labelledby': `connections-${id}-title`, key: `connections:${id}` },
    h('div', { class: 'connections-group-head' },
      h('h3', { class: 'kicker', id: `connections-${id}-title`, text: title }),
      why ? h('p', { class: 'note', text: why }) : null),
    ...children)
}
function settingsConnections({ back = true } = {}) {
  const sheet = host.state.sheet
  if (sheet?.group && !sheet.groupShown) {
    sheet.groupShown = true
    const id = `connections-${sheet.group}`
    setTimeout(() => globalThis.document?.getElementById(id)?.scrollIntoView?.({ block: 'start' }), 0)
  }
  return [back ? settingsBack('Settings', 'root') : null,
    settingsHead('Connections', 'What your agents run on, the services they work with, and the keys your plugins use.'),
    connectionsGroup('providers', 'AI providers', 'What your agents think with. Choose one in any thread.', settingsProviders({ back: false, heading: false, footnote: false })),
    connectionsGroup('services', 'Services', 'Where your agents do their work.', servicesRows()),
    connectionsGroup('api-keys', 'API keys', 'For the services your plugins use, entered once.', apiKeys.view({ open: id => goSettings('api-key', id) }))]
}

// One provider's page: the plan if the host named one, the quota it last read,
// and — for one that is not wired — the host's own sentence for why.
function settingsProvider(id) {
  const detail = providerDetail(host.state.providers.find((p) => p.id === id))
  if (!detail) {
    return [settingsBack('Connections', 'connections'), h('div', { class: 'reason', text: 'The host no longer lists that provider.' })]
  }
  return [
    settingsBack('Connections', 'connections'),
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

function integrationServices() {
  return [...integrationCatalog(host.state), ...integrationSections().flatMap(entry => {
    const summary = host.registry.invoke(entry, 'summary', {})
    return summary ? [{ ...summary, id: entry.id, name: entry.title, mark: typeof entry.icon === 'function' ? host.registry.invoke(entry, 'icon', 20) : (icon[entry.icon] ?? icon.code)(20) }] : []
  })]
}

// The services, connected ones first, as one list: in Connections a second
// pair of headings (Connected, Available) inside a group was one level too
// many. The line under each name says which it is.
function servicesRows() {
  const services = integrationServices().sort((a, b) => Number(b.connected) - Number(a.connected))
  return services.length ? [h('div', { class: 'rows' }, ...services.map(service => settingsRow({
    key: service.id, lead: tile(service.mark ?? providerMark(service.id, 20), { kind: `glyph ${service.id}` }),
    title: service.name, sub: service.summary, onclick: () => goIntegration(service.id) })))]
    : [h('p', { class: 'note', text: 'No services are available yet.' })]
}

// The page Integrations was, for anything that still draws it by name.
function settingsIntegrations({ back = true } = {}) {
  return [back ? settingsBack('Settings', 'root') : null,
    settingsHead('Services', 'Connect the services your agents work with.'), ...servicesRows()]
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
  if (id === 'github' && Object.hasOwn(host.state.github, 'accounts')) return [settingsBack('Connections', 'connections'), settingsHead('GitHub'),
    ...githubOutcome(host.state.github.at),
    host.state.github.installUrl || host.state.github.orgInstall ? h('button', { class: 'pillbtn primary', text: 'Install on GitHub', onclick: () => host.installGithub('install', host.state.github.orgInstall?.org ?? null) }) : null,
    ...githubAccounts.view(),
    gitIdentity.view({ github: (host.state.github.accounts ?? []).some(account => account.kind === 'personal' && account.status === 'authorized') })]
  const service = integrationCatalog(host.state).find(service => service.id === id)
  if (!service) return [settingsBack('Connections', 'connections'), settingsHead('Integration unavailable')]
  const account = service.accounts.find(account => String(account.id) === String(accountId))
  if (account) return [integrationBack(service.name, id), settingsHead(account.name),
    githubAccountCard(account)]
  const gh = host.state.github
  return [settingsBack('Connections', 'connections'), settingsHead(service.name),
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
    settingsChats: { enumerable: true, get: () => settingsChats },
    goSettings: { enumerable: true, get: () => goSettings },
    settingsBack: { enumerable: true, get: () => settingsBack },
    backInSettings: { enumerable: true, get: () => backInSettings },
    settingsHead: { enumerable: true, get: () => settingsHead },
    tile: { enumerable: true, get: () => tile },
    settingsRoot: { enumerable: true, get: () => settingsRoot },
    filesFolderFields: { enumerable: true, get: () => filesFolderFields },
    settingsFiles: { enumerable: true, get: () => settingsFiles },
    settingsAgents: { enumerable: true, get: () => settingsAgents },
    goSettingsDetail: { enumerable: true, get: () => goSettingsDetail },
    syncSettingsPlugin: { enumerable: true, get: () => syncSettingsPlugin },
    settingsProviders: { enumerable: true, get: () => settingsProviders },
    settingsApiKey: { enumerable: true, get: () => settingsApiKey },
    settingsAccount: { enumerable: true, get: () => settingsAccount },
    settingsConnections: { enumerable: true, get: () => settingsConnections },
    settingsAbout: { enumerable: true, get: () => settingsAbout },
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
