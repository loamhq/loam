// Product defaults use precisely the public scope used by an installed plugin.
export function registerPage(loam) {
  const host = loam.ui.host
  const openList = (id, fallback) => host.state.wide ? host.browseSection(id) : fallback()
  const selectedList = (id, fallback) => host.state.wide ? host.state.navigator === id : fallback()
  loam.events.on('rendered', () => host.pluginSettingsUI?.sync())
  loam.events.on('settings.opened', ({ page }) => { if (page === 'plugins') host.pluginSettingsUI?.opened() })
  for (const section of [
    { id: 'chats', frame: 'full', screens: ['home', 'threads'], render: () => host.threads.view(false), list: () => host.threads.list(true), load: () => host.threads.load({ quiet: true }), title: 'Chats', icon: 'chat', selected: () => selectedList('chats', () => !['files', 'activity', 'apps'].includes(host.state.screen) && !host.state.screen.startsWith('plugin:')), open: () => openList('chats', () => host.goChats()) },
    { id: 'files', frame: 'full', screens: ['files'], render: () => host.filesScreen(), list: context => host.filesList(context.collapse), load: () => { if (!host.files.st.roots.loaded) host.files.loadRoots() }, title: 'Files', icon: 'folder', selected: () => selectedList('files', () => host.state.screen === 'files'), open: () => openList('files', () => host.goFiles(null, '')) },
    { id: 'apps', frame: 'full', screens: ['apps'], render: () => host.appsScreen(), list: context => host.apps.view({ list: true, collapse: context.collapse }), load: () => { if (!host.apps.st.loaded && !host.apps.st.loading) host.apps.load() }, title: 'Apps', icon: 'apps', selected: () => selectedList('apps', () => host.state.screen === 'apps'), open: () => openList('apps', () => host.goApps()) },
  ]) {
    loam.places.rail.section.add(section)
    loam.commands.register({ id: `core.${section.id}`, title: section.title, run: section.open })
  }
  for (const entry of [
    { id: 'thread', render: ({ host }) => host.threads.pane() },
    { id: 'file', render: ({ host, panel }) => panel === 'main' ? host.filesPane() : host.filePanelView() },
    { id: 'app', render: ({ host }) => host.apps.view() },
    { id: 'profile', render: () => host.profilePane() },
  ]) loam.places.panel.item.add(entry)
  loam.commands.register({ id: 'panel.open', title: 'Open an item', run: ({ item, panel = 'main' }) => host.panels[panel === 'side' ? 'openSide' : 'openMain'](item) })
  loam.places.rail.section.add({ id: 'activity', hidden: true, frame: 'full', screens: ['activity'], open: () => host.goActivity(), render: () => host.activityScreen() })
  loam.places.rail.section.add({ id: 'profile', hidden: true, frame: 'full', screens: ['profile'], render: () => host.profileScreen() })
  loam.places.app.overlay.add({ id: 'core.editor', render: () => host.registry.component('core.composer', { variant: 'editor', defaultView: () => host.editorNodes() }) })
  loam.places.app.overlay.add({ id: 'core.sheets', render: () => host.sheetNodes() })
  loam.places.app.overlay.add({ id: 'core.running-notices', render: () => host.registry.component('core.activity', { variant: 'notices', defaultView: () => host.running.toastNodes() }) })
  loam.places.app.overlay.add({ id: 'core.activity-dialogs', render: () => host.registry.component('core.activity', { variant: 'dialogs', defaultView: () => host.activity.dialogNodes() }) })
  loam.places.app.overlay.add({ id: 'core.profile-dialogs', render: () => host.registry.component('core.activity', { variant: 'profile-dialogs', defaultView: () => host.profileActivity.dialogNodes() }) })
  loam.places.app.overlay.add({ id: 'core.engineer-dialogs', render: () => host.engineerActivity.dialogNodes() })
  const sections = [
    ['appearance', 'Appearance', 'sun', () => host.settingsAppearance],
    ['agents', 'Agents', 'users', () => host.settingsAgents],
    ['providers', 'Providers', 'code', () => host.settingsProviders],
    ['integrations', 'Integrations', 'link', () => host.settingsIntegrations],
    ['accounts', 'Accounts', 'key', () => host.settingsAccounts],
    ['files', 'Files', 'folder', () => host.settingsFiles],
    ['plugins', 'Plugins', 'puzzle', () => host.settingsPlugins],
    ['signin', 'Sign-in', 'lock', () => context => host.settingsBody({ page: 'signin' }, context.back)],
    ['update', 'Update', 'refresh', () => context => host.settingsBody({ page: 'update' }, context.back)],
  ]
  for (const [id, title, icon, view] of sections) loam.places.settings.section.add({ id, title, icon, render: context => view()(context) })
  loam.commands.register({ id: 'core.settings', title: 'Settings', run: () => host.goSettings('root') })
  loam.commands.register({ id: 'section.open', title: 'Open a section', run: ({ id }) => {
    const section = host.registry.entries('rail.section').find(entry => entry.id === id && !entry.component)
    if (!section) throw new Error(`Unknown section: ${id}`)
    if (section.hidden && section.open) return section.open()
    if (host.state.wide) return host.browseSection(id)
    if (section.open) return section.open()
    if (host.state.screen === 'files' && !host.files.guard(null)) { host.files.st.sheet.proceed = host.proceedThen(() => loam.commands.run('section.open', { id })); return }
    if (host.state.screen === 'files') host.files.closeFile()
    if (host.herePath() !== '/') host.pushAddress(null, '', '/')
    host.closeSheet(); host.threads.close(); host.closeThread()
    host.state.screen = `plugin:${id}`; host.schedule()
  } })
}
