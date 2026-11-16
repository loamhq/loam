// Product defaults use precisely the public scope used by an installed plugin.
export function registerPage(loam) {
  const host = loam.ui.host
  const openList = (id, fallback) => host.state.wide ? host.browseSection(id) : fallback()
  const selectedList = (id, fallback) => host.state.wide && host.state.sidebarSection ? host.state.sidebarSection === id : fallback()
  loam.events.on('rendered', () => host.pluginSettingsUI?.sync())
  for (const section of [
    { id: 'chats', frame: 'full', screens: ['home', 'threads'], render: ({ wide }) => host.threads.view(wide), title: 'Chats', icon: 'chat', selected: () => selectedList('chats', () => !['files', 'activity', 'apps'].includes(host.state.screen) && !host.state.screen.startsWith('plugin:')), open: () => openList('chats', () => host.goChats()) },
    { id: 'files', frame: 'full', screens: ['files'], render: ({ wide }) => wide ? host.desk() : host.filesScreen(), title: 'Files', icon: 'folder', selected: () => selectedList('files', () => host.state.screen === 'files'), open: () => openList('files', () => host.goFiles(null, '')) },
    { id: 'apps', frame: 'full', screens: ['apps'], render: ({ wide }) => wide ? host.desk() : host.appsScreen(), title: 'Apps', icon: 'apps', selected: () => selectedList('apps', () => host.state.screen === 'apps'), open: () => openList('apps', () => host.goApps()) },
  ]) {
    loam.places.rail.section.add(section)
    loam.commands.register({ id: `core.${section.id}`, title: section.title, run: section.open })
  }
  loam.places.rail.section.add({ id: 'activity', hidden: true, frame: 'full', screens: ['activity'], open: () => host.goActivity(), render: () => host.activityScreen() })
  loam.places.rail.section.add({ id: 'profile', hidden: true, frame: 'full', screens: ['profile'], render: ({ wide }) => wide ? host.desk() : host.profileScreen() })
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
    ['voice', 'Voice', 'mic', () => host.settingsVoice],
    ['files', 'Files', 'folder', () => host.settingsFiles],
    ['plugins', 'Plugins', 'puzzle', () => host.settingsPlugins],
    ['signin', 'Sign-in', 'lock', () => context => host.settingsBody({ page: 'signin' }, context.back)],
    ['update', 'Update', 'refresh', () => context => host.settingsBody({ page: 'update' }, context.back)],
  ]
  for (const [id, title, icon, view] of sections) loam.places.settings.section.add({ id, title, icon, ...(id === 'voice' ? { children: 'voice' } : {}), render: context => view()(context) })
  loam.commands.register({ id: 'core.settings', title: 'Settings', run: () => host.goSettings('root') })
  loam.commands.register({ id: 'section.open', title: 'Open a section', run: ({ id }) => {
    const section = host.registry.entries('rail.section').find(entry => entry.id === id && !entry.component)
    if (!section) throw new Error(`Unknown section: ${id}`)
    if (host.state.wide && host.state.collapsed) host.setCollapsed(false)
    if (section.open) return section.open()
    if (host.state.screen === 'files' && !host.files.guard(null)) { host.files.st.sheet.proceed = host.proceedThen(() => loam.commands.run('section.open', { id })); return }
    if (host.state.screen === 'files') host.files.closeFile()
    if (host.herePath() !== '/') host.pushAddress(null, '', '/')
    host.closeSheet(); host.threads.close(); host.closeThread()
    host.state.screen = `plugin:${id}`; host.schedule()
  } })
}
