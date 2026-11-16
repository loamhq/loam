import { h, icon, blob } from "../ui.js"
import { agentLook } from "../sessions.js"
import { folderView, fileView } from "../files.js"
import { panelHeader, emptyConversation } from "../chat-ui.js"

// Mounted once. Cross-module state and operations are explicit host services.
export function mount(host) {


// The phone's destinations; the round button opens Settings as a bottom sheet.
function nav() {
  return h(
    'div',
    // `swap` once selection has been used: the tab bar coming back in place of
    // the selection's bar is the same change the other way.
    { class: 'nav', key: 'nav' },
    h(
      'div',
      { class: 'navpill' },
      ...host.registry.entries('rail.section').filter(entry => !entry.component && !entry.hidden).map(section => h('button', {
        class: `navbtn${section.selected?.() || host.state.screen === `plugin:${section.id}` ? ' on' : ''}`, text: section.title,
        onclick: () => host.commands.run('section.open', { id: section.id }),
      })),
    ),
    h(
      'button',
      { class: `roundbtn settings-utility${host.state.sheet?.kind === 'settings' || host.state.screen === 'activity' ? ' on' : ''}`, 'aria-current': host.state.screen === 'activity' ? 'page' : null, 'aria-expanded': String(host.state.sheet?.kind === 'settings'), title: 'Settings', 'aria-label': 'Settings', onclick: () => host.openSheet({ kind: 'settings', page: 'root' }) },
      icon.sliders(20),
    ),
  )
}




// ---------------------------------------------------------- agent profile

function profileScreen() {
  return h(
    'section',
    { class: 'screen profile', key: 'screen:profile' },
    h('div', { class: 'profile-head' }, h('button', { class: 'backbtn', onclick: host.goHome }, icon.chevronLeft(20), 'Back')),
    host.registry.draw('app.overlay', { slot: 'player' }),
    h('div', { class: 'profile-body' }, ...profileBody(host.state.profileAgent)),
    nav(),
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

function desk() {
  return h(
    'div',
    { class: `desk${host.state.collapsed ? ' collapsed' : ''}`, key: 'screen:desk' },
    rail(),
    // **The column stays in the tree when it is hidden**, which is what makes
    // hiding it an animation rather than a jump: the owner, 2026-09-22, "the
    // collapsing of the middle column should happen with an elegant animation".
    // A node removed cannot be transitioned out of, so the width and the opacity
    // are the stylesheet's to run down over `.desk.collapsed`, and the pane
    // grows into the space as it goes. It is `inert` while it is closed so that
    // nothing invisible can be tabbed to or read out.
    listColumn(),
    mainPane(),
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
      h('button', { class: `railtab${section.selected?.() || host.state.screen === `plugin:${section.id}` ? ' on' : ''}`, title: section.title,
        onclick: () => host.commands.run('section.open', { id: section.id }) },
        typeof section.icon === 'function' ? section.icon(20) : (icon[section.icon] ?? icon.apps)(20), h('span', { text: section.title })))),
    h(
      'div',
      { class: 'rail-foot' },
      h('button', { class: `iconbtn railround${host.state.sheet?.kind === 'activity' || host.state.screen === 'activity' && host.state.sheet?.kind !== 'settings' ? ' on' : ''}`, title: 'Activity', 'aria-expanded': String(host.state.sheet?.kind === 'activity'),
        'aria-label': 'Activity', onclick: host.goActivity },
        icon.clock(20)),
      h('button', { class: `iconbtn railround settings-utility${host.state.sheet?.kind === 'settings' ? ' on' : ''}`, 'aria-expanded': String(host.state.sheet?.kind === 'settings'), title: 'Settings', 'aria-label': 'Settings', onclick: settings }, icon.sliders(20)),
    ),
  )
}



// The 372px list column: the same header the phone has, plus the button that
// hides the column, and the same list under it.
function listColumn() {
  const section = host.state.sidebarSection ?? host.state.screen
  if (!['files', 'apps'].includes(section)) return host.threads.list(true)
  const collapse = h('button', { class: 'iconbtn', title: 'Hide sidebar for a focused view', onclick: () => setCollapsed(true) }, icon.panelLeft(18))
  return h('aside', host.state.collapsed ? { class: 'listcol away', inert: true, 'aria-hidden': 'true' } : { class: 'listcol' },
    section === 'apps' ? host.apps.view({ list: true, collapse })
      : folderView(host.files, { go: host.goFiles, wide: true, home: host.filesHome(), collapse }))
}

// A desktop rail choice changes the list, not the document being read. Item
// navigation still uses the ordinary main-panel router and its save guard.
function browseSection(id) {
  host.closeSheet()
  setCollapsed(false)
  const screen = host.state.screen
  const occupied = screen === 'threads' && host.threads.st.open
    || screen === 'files' && host.files.st.file
    || screen === 'apps' && host.apps.st.selected
    || screen === 'profile'
  if (!occupied) {
    host.state.sidebarSection = null
    return id === 'chats' ? host.goChats() : id === 'files' ? host.goFiles(null, '') : host.goApps()
  }
  host.state.sidebarSection = id
  if (id === 'chats') host.threads.load({ quiet: true })
  if (id === 'files' && !host.files.st.roots.loaded) host.files.loadRoots()
  if (id === 'apps' && !host.apps.st.loaded && !host.apps.st.loading) host.apps.load()
  host.schedule()
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
  const body =
    host.state.screen === 'files'
      ? filesPane()
      : host.state.screen === 'apps'
        ? host.apps.view()
      : host.state.screen === 'profile'
        ? profilePane()
        : paneEmpty()
  return h(
    'main',
    { class: 'pane', 'data-panel': 'main' },
    // With the column hidden, the way back to it sits in the pane itself; the
    // headers carry a spacer so nothing of theirs is underneath it.
    host.state.collapsed
      ? h('button', { class: 'iconbtn showbar', key: 'show-sidebar', title: 'Show sidebar', onclick: () => setCollapsed(false) }, icon.panelLeft(18))
      : null,
    body,
  )
}

function profilePane() {
  return h(
    'div',
    { class: 'pane-body', key: 'pane:profile' },
    panelHeader({ back: h('button', { class: 'backbtn', onclick: backToChat }, icon.chevronLeft(20), host.state.backToThread ? 'Back to chat' : 'Back to chats') }),
    host.registry.draw('app.overlay', { slot: 'player' }),
    h('div', { class: 'profile-body' }, ...profileBody(host.state.profileAgent)),
  )
}



// Back to the chat the profile was opened from, or to nothing in particular if
// it was opened from the list.
function backToChat() { if (host.state.backToThread) host.threads.open(host.state.backToThread); else host.goChats() }

function filesPane() {
  const f = host.files.st.file
  if (!f) {
    return h(
      'div',
      { class: 'pane-body', key: 'pane:files:empty' },
      host.registry.draw('app.overlay', { slot: 'player' }),
      h(
        'div',
        { class: 'pane-nothing' },
        h('div', { class: 'display', text: 'Pick a file' }),
        h('div', { class: 'why', text: 'One on the left. It opens here.' }),
      ),
    )
  }
  return h(
    'div',
    { class: 'pane-body', key: 'pane:files' },
    fileView(host.files, { close: null, closeLabel: '', wide: true, field: host.fileFieldFor, home: host.filesHome(), go: host.goFiles, onTool: host.applyTool, embed: host.embedFor, ...host.fileDictation() }),
  )
}

function paneEmpty() {
  return h('div', { class: 'pane-body', key: 'pane:empty' }, host.registry.draw('app.overlay', { slot: 'player' }),
    emptyConversation({ title: 'Pick a chat', body: 'One on the left, or a new one from an agent at the top of the list.' }))
}
return Object.defineProperties({}, {
    nav: { enumerable: true, get: () => nav },
    profileScreen: { enumerable: true, get: () => profileScreen },
    profileBody: { enumerable: true, get: () => profileBody },
    desk: { enumerable: true, get: () => desk },
    rail: { enumerable: true, get: () => rail },
    listColumn: { enumerable: true, get: () => listColumn },
    browseSection: { enumerable: true, get: () => browseSection },
    setCollapsed: { enumerable: true, get: () => setCollapsed },
    mainPane: { enumerable: true, get: () => mainPane },
    profilePane: { enumerable: true, get: () => profilePane },
    backToChat: { enumerable: true, get: () => backToChat },
    filesPane: { enumerable: true, get: () => filesPane },
    paneEmpty: { enumerable: true, get: () => paneEmpty }
})
}
