import { h, icon, blob } from "../ui.js"
import { emptyState } from "../empty-state.js"
import { agentLook } from "../sessions.js"
import { folderView, fileView } from "../files.js"
import { renderPanel } from '../panel-items.js'
import { panelHeader } from "../chat-ui.js"

// Mounted once. Cross-module state and operations are explicit host services.
export function mount(host) {


// The phone's destinations; the round button opens Settings as a bottom sheet.
function nav() {
  const { navigator, sheet } = host.state
  if (host.workspaceHistory.state.front !== 'list' || sheet?.kind === 'activity') return null
  if ((navigator === 'files' && host.files.st.root) || (navigator === 'chats' && host.threads.st.archived)) return null
  return h(
    'div',
    // `swap` once selection has been used: the tab bar coming back in place of
    // the selection's bar is the same change the other way.
    { class: 'nav', key: 'nav' },
    host.state.mainItem ?
      h('button', { class: 'roundbtn', 'aria-label': 'Return to open panel', title: 'Return to open panel',
        onclick: () => { host.workspaceHistory.front('main'); host.schedule() } }, icon.panelRight(20)) : null,
    h(
      'div',
      { class: 'navpill' },
      ...host.registry.entries('rail.section').filter(entry => !entry.component && !entry.hidden).map(section => h('button', {
        class: `navbtn${host.state.navigator === section.id || section.selected?.() ? ' on' : ''}`, text: section.title,
        onclick: () => host.commands.run('section.open', { id: section.id }),
      })),
    ),
    h(
      'button',
      { class: `roundbtn settings-utility${sheet?.kind === 'settings' ? ' on' : ''}`, 'aria-expanded': String(sheet?.kind === 'settings'), title: 'Settings', 'aria-label': 'Settings', onclick: () => host.openSheet({ kind: 'settings', page: 'root' }) },
      icon.sliders(20),
    ),
  )
}




// ---------------------------------------------------------- agent profile

function profileScreen() {
  return h(
    'section',
    { class: 'screen profile', key: 'screen:profile' },
    h('div', { class: 'profile-head' }, host.backButton('main')),
    host.registry.draw('app.overlay', { slot: 'player' }),
    h('div', { class: 'profile-body' }, ...profileBody(host.state.profileAgent)),
  )
}



// The agent's own page, without whatever frame it is in: the phone's screen
// under a back button, the desktop's main pane under "Back to chat".
function profileBody(agent) {
  return host.profileUI.body(agent)
}



// ------------------------------------------------------- the desktop layout
//
// Three columns from the desktop handoff — a rail, the list column, the main
// pane — and **the same components underneath**: the rows are `sessionRow()`,
// the transcript is `threadLines()`, the composer is `composer()`, and the
// reconciler is the one in ui.js. What is different here is the frame around
// them and the header on the chat, which is the design's own: an agent button,
// a "/" and a title that renames in place.
//
// Which layout is drawn is read from the window on every draw, so a window
// dragged across 1100px is a redraw and nothing else.

function desk(beside = null) {
  return h(
    'div',
    { class: `desk${host.state.mainItem?.kind === 'thread' ? ' tt-shell has-thread' : ''}${host.state.collapsed ? ' collapsed' : ''}`, key: 'screen:workspace' },
    rail(),
    // **The column stays in the tree when it is hidden**, which is what makes
    // hiding it an animation rather than a jump: the owner, 2026-09-22, "the
    // collapsing of the middle column should happen with an elegant animation".
    // A node removed cannot be transitioned out of, so the width and the opacity
    // are the stylesheet's to run down over `.desk.collapsed`, and the pane
    // grows into the space as it goes. It is `inert` while it is closed so that
    // nothing invisible can be tabbed to or read out.
    h('div', { class: 'workspace-list', key: 'workspace-list' }, listColumn()),
    h('div', { class: `panel-pair${host.panels.visible() ? ' split' : ''}${host.panels.st.closing ? ' closing' : ''}${host.panels.st.swap ? ` swapped-${host.panels.st.swap}` : ''}`, key: 'panel-pair', 'data-focused-panel': host.panels.focused(), style: { '--panel-main': `${host.panels.st.split}%` } },
      mainPane(), beside ? host.panels.divider() : null, beside),
  )
}



// The 80px rail: the brand, the destinations, and — at the foot — Activity,
// Settings and the account tile. The latter two open the same dialog.
//
// **The account tile carries no initial**, and that is a deliberate departure
// from the design's "Y": that letter is a person's, and this app has no account
// to read one from. A glyph says the same thing without inventing a name.
function rail(...args) { return host.registry.component('core.rail', { args, defaultView: () => builtinRail(...args) }) }

function builtinRail() {
  const settings = () => host.openSheet({ kind: 'settings', page: 'root' })
  return h(
    'aside',
    { class: 'rail' },
    h('img', { class: 'brand', src: '/icon.svg', alt: 'Loam' }),
    h('div', { class: 'rail-tabs' }, ...host.registry.entries('rail.section').filter(entry => !entry.component && !entry.hidden).map(section =>
      h('button', { class: `railtab${host.state.navigator === section.id || section.selected?.() ? ' on' : ''}`, title: section.title,
        onclick: () => host.commands.run('section.open', { id: section.id }) },
        typeof section.icon === 'function' ? section.icon(20) : (icon[section.icon] ?? icon.apps)(20), h('span', { text: section.title })))),
    h(
      'div',
      { class: 'rail-foot' },
      h('button', { class: `iconbtn railround${host.state.sheet?.kind === 'activity' ? ' on' : ''}`, title: 'Activity', 'aria-expanded': String(host.state.sheet?.kind === 'activity'),
        'aria-label': 'Activity', onclick: host.goActivity },
        icon.clock(20)),
      h('button', { class: `iconbtn railround settings-utility${host.state.sheet?.kind === 'settings' ? ' on' : ''}`, 'aria-expanded': String(host.state.sheet?.kind === 'settings'), title: 'Settings', 'aria-label': 'Settings', onclick: settings }, icon.sliders(20)),
    ),
  )
}



// The 372px list column: the same header the phone has, plus the button that
// hides the column, and the same list under it.
function listColumn() {
  const section = host.registry.entries('rail.section').find(entry => !entry.component && entry.id === host.state.navigator)
  const collapse = h('button', { class: 'iconbtn', title: 'Hide sidebar for a focused view', onclick: () => setCollapsed(true) }, icon.panelLeft(18))
  const content = section ? host.registry.invoke(section, section.list ? 'list' : 'render', { host, wide: true, navigator: true, collapse }) : null
  if (content?.classList?.contains('listcol')) return content
  return h('aside', host.state.collapsed ? { class: 'listcol away', inert: true, 'aria-hidden': 'true' } : { class: 'listcol' }, content)
}

function filesList(collapse) {
  return folderView(host.files, { go: host.goFiles, wide: true, home: host.filesHome(), collapse,
    historyBack: host.backButton('list'), showParent: false })
}

// Hidden or shown, and remembered on this device — like the theme and unlike a
// pin, because it is how one screen is being looked at.
function setCollapsed(value) {
  if (!value && host.panels?.tucked()) host.panels.showSidebar()
  host.state.collapsed = value
  host.store.sidebarHidden = value
  host.schedule()
}

function mainPane() {
  const at = host.state.mainItem
  const empty = at ? null : h('div', { class: 'pane-body', key: 'empty-main' },
    host.registry.draw('app.overlay', { slot: 'player' }),
    emptyState({ object: 'window', title: 'Choose an item', body: 'Open a chat, file or app from the list.' }))
  const back = at && !['file', 'thread', 'profile', 'app', 'directory'].includes(at.kind) ? host.backButton('main') : null
  return renderPanel({ host, item: at, empty, header: back ? h('header', { class: 'panel-history-head' }, back) : null,
    before: host.state.collapsed ? [h('button', { class: 'iconbtn showbar', key: 'show-sidebar', title: 'Show sidebar', onclick: () => setCollapsed(false) }, icon.panelLeft(18))] : [] })
}

function profilePane() {
  const back = host.backButton('main')
  return h(
    'div',
    { class: 'pane-body', key: 'pane:profile' },
    panelHeader({ back }),
    host.registry.draw('app.overlay', { slot: 'player' }),
    h('div', { class: 'profile-body' }, ...profileBody(host.state.profileAgent)),
  )
}



function filesPane(empty = false) {
  const f = empty ? null : host.files.st.file
  if (!f) {
    return h(
      'div',
      { class: 'pane-body', key: 'pane:files:empty' },
      host.registry.draw('app.overlay', { slot: 'player' }),
      emptyState({ object: 'folder', title: 'Pick a file', body: 'One on the left. It opens here.' }),
    )
  }
  return h(
    'div',
    { class: 'pane-body', key: 'pane:files' },
    fileView(host.files, { historyBack: host.backAction('main'), close: null, closeLabel: '', wide: true, htmlActionsInMenu: host.files.st.chatFile && !host.panels.available(), field: host.fileFieldFor, home: host.filesHome(), go: host.goFiles, onTool: host.applyTool, embed: host.embedFor, ...host.fileDictation() }),
  )
}

return Object.defineProperties({}, {
    nav: { enumerable: true, get: () => nav },
    profileScreen: { enumerable: true, get: () => profileScreen },
    profileBody: { enumerable: true, get: () => profileBody },
    desk: { enumerable: true, get: () => desk },
    rail: { enumerable: true, get: () => rail },
    filesList: { enumerable: true, get: () => filesList },
    listColumn: { enumerable: true, get: () => listColumn },
    setCollapsed: { enumerable: true, get: () => setCollapsed },
    mainPane: { enumerable: true, get: () => mainPane },
    profilePane: { enumerable: true, get: () => profilePane },
    filesPane: { enumerable: true, get: () => filesPane }
})
}
