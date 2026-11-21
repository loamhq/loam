// Product defaults use precisely the public scope used by an installed plugin.
export function registerPage(loam) {
  const host = loam.ui.host
  loam.events.on('rendered', () => { host.pluginSettingsUI?.sync(); host.marketSync() })
  loam.events.on('settings.opened', ({ page }) => { if (page === 'plugins') host.pluginSettingsUI?.opened() })
  for (const section of [
    { id: 'chats', frame: 'full', screens: ['home', 'threads'], render: () => host.threads.view(false), list: () => host.threads.list(true), load: () => host.threads.load({ quiet: true }), title: 'Chats', icon: 'chat' },
    { id: 'files', frame: 'full', screens: ['files'], render: () => host.filesScreen(), list: context => host.filesList(context.collapse), load: () => { if (!host.files.st.roots.loaded) host.files.loadRoots() }, title: 'Files', icon: 'folder' },
    { id: 'apps', frame: 'full', screens: ['apps'], render: () => host.appsScreen(), list: context => host.apps.view({ list: true, collapse: context.collapse }), load: () => { if (!host.apps.st.loading) host.apps.load({ list: true }) }, title: 'Apps', icon: 'apps' },
  ]) {
    const open = () => host.openPlace(section.id)
    loam.places.rail.section.add({ ...section, open })
    loam.commands.register({ id: `core.${section.id}`, title: section.title, run: open })
  }
  for (const entry of [
    { id: 'thread', render: ({ host }) => host.threads.pane() },
    { id: 'file', render: ({ host, panel, fileTools }) => panel === 'main' ? host.filesPane() : host.filePanelView(fileTools) },
    { id: 'app', render: ({ host, item }) => host.apps.view({ url: item.url ?? item.app?.url }) },
    { id: 'directory', render: ({ host, item }) => host.directoryView(item.id) },
    { id: 'catalog', render: ({ host }) => host.apps.view({ list: true }) },
    { id: 'profile', render: ({ host }) => host.profilePane() },
  ]) loam.places.panel.item.add(entry)
  loam.commands.register({ id: 'panel.open', title: 'Open an item', run: ({ item, panel = 'main' }) => host.panels[panel === 'side' ? 'openSide' : 'openMain'](item) })
  loam.places.rail.section.add({ id: 'activity', hidden: true, open: () => host.goActivity() })
  loam.places.rail.section.add({ id: 'profile', hidden: true, frame: 'full', screens: ['profile'], render: () => host.profileScreen() })
  loam.places.app.overlay.add({ id: 'core.editor', render: () => host.registry.component('core.composer', { variant: 'editor', defaultView: () => host.editorNodes() }) })
  loam.places.app.overlay.add({ id: 'core.sheets', render: () => host.sheetNodes() })
  // The Market's install sheet, over the Market or over anything else (a
  // thread's listing card opens it by id), and the Market by id or kind.
  loam.places.app.overlay.add({ id: 'core.market-install', render: () => host.marketInstallNodes() })
  loam.commands.register({ id: 'chat.draft', title: 'Open a chat draft', run: ({ agent, text }) => {
    if (typeof agent !== 'string' || typeof text !== 'string' || !text.trim() || text.length > 4000) throw new Error('Choose an agent and a draft of up to 4,000 characters.')
    return host.threads.create(agent, { draft: text })
  } })
  loam.commands.register({ id: 'market.open', title: 'Open the Market', run: ({ id = null, kind = 'all', q = '' } = {}) => host.openMarket({ id, kind, q }) })
  loam.commands.register({ id: 'market.install', title: 'Install from the Market', run: ({ id }) => host.openMarketInstall(id) })
  loam.commands.register({ id: 'market.install-link', title: 'Install from a link', run: ({ link = '' } = {}) => host.openLinkInstall({ link }) })
  loam.places.app.overlay.add({ id: 'core.running-notices', render: () => host.registry.component('core.activity', { variant: 'notices', defaultView: () => host.running.toastNodes() }) })
  loam.places.app.overlay.add({ id: 'core.activity-dialogs', render: () => host.registry.component('core.activity', { variant: 'dialogs', defaultView: () => host.activity.dialogNodes() }) })
  loam.places.app.overlay.add({ id: 'core.profile-dialogs', render: () => host.registry.component('core.activity', { variant: 'profile-dialogs', defaultView: () => host.profileActivity.dialogNodes() }) })
  loam.places.app.overlay.add({ id: 'core.engineer-dialogs', render: () => host.engineerActivity.dialogNodes() })
  // Settings in seven (2026-11-18). The ids that were sections before
  // (providers, integrations, accounts, signin, update) still open, at their
  // new home: app-shape.js, settingsHome.
  const sections = [
    ['account', 'Account', 'person', () => host.settingsAccount],
    ['appearance', 'Appearance', 'sun', () => host.settingsAppearance],
    ['agents', 'Agents', 'users', () => host.settingsAgents],
    ['connections', 'Connections', 'link', () => host.settingsConnections],
    ['plugins', 'Plugins', 'puzzle', () => host.settingsPlugins],
    ['files', 'Files', 'folder', () => host.settingsFiles],
    ['about', 'About Loam', 'info', () => host.settingsAbout],
  ]
  for (const [id, title, icon, view] of sections) loam.places.settings.section.add({ id, title, icon, render: context => view()(context) })
  loam.commands.register({ id: 'core.settings', title: 'Settings', run: () => host.goSettings('root') })
  loam.commands.register({ id: 'section.open', title: 'Open a section', run: ({ id }) => {
    const section = host.registry.entries('rail.section').find(entry => entry.id === id && !entry.component)
    if (!section) throw new Error(`Unknown section: ${id}`)
    if (section.open) return section.open()
    return host.openPlace(id)
  } })
}
