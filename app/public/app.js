import { welcomeScreen, untouchedHello } from './onboarding.js'
import { createDailyNotes } from './daily-notes.js'
import { createSecretSettings } from './plugin-secrets.js'
import { isOwner, ownerInitial, setOwner } from './owner.js'
import { createProfiles, mountProfile } from './agent-profile.js'
import { audioSessionType } from './audio-session.js'
import { serviceProviders } from './page/services.js'
import { createPluginSettings } from './plugin-settings.js'
import { createPluginThemes } from './plugin-themes.js'
import { createPluginLoader } from './plugin-loader.js'
import * as sharedUI from './ui.js'
import * as chatUI from './chat-ui.js'
import { registerPage } from './page/registrations.js'
import { createCoreRegistry } from './core-registrations.js'
import { mount as mountFiles } from './page/files.js'
import { mount as mountAccounts } from './page/accounts.js'
import { mount as mountComposer } from './page/composer.js'
import { mount as mountTranscript } from './page/transcript.js'
import { mount as mountSettings } from './page/settings.js'
import { mount as mountLayout } from './page/layout.js'
import { mount as mountSheets } from './page/sheets.js'
import { mount as mountMarket } from './page/market.js'
import { menuSurface, createMenuSurfaces } from './menu-surface.js'
import { backTitle, createMainHistory, historyBackButton, historyShortcut } from './panel-history.js'
import { createAppModalHistory } from './app-modal-history.js'
import { settingsRow, agentProse, userBubble, messageFiles, stepFold, emptyConversation, panelHeader, settingsNav, segmentedControl, createMessageState, sheetHandle } from './chat-ui.js'
import { setAvatarColours } from './sessions.js'
import { authenticatedFetch as fetch, authExpired, expireAuth, failureSentence } from './auth.js'
// Threads, Files, profiles and Activity share settings and page services.
// This adapter talks only to the app's /api; the broker token stays on the
// server. Thread records and detailed reply steps are normalized at their seams.
//
// It builds a screen whole on every change and applies it in place, through
// `morph()` in ui.js: one code path per row, and what reaches the DOM is only
// the difference. A stream tick that changes nothing visible touches nothing at
// all, so a row a finger is on is the same element a second later — a page that
// rebuilt itself on every snapshot had a tap land on a node that no longer
// existed, twice, and a phone is worse at that than a test is.
//
// A list says which row is which with `key`, so a row that moves up the list
// moves rather than being rebuilt around whatever is at that position.

import { blockView, toolSummary, foldFacts, foldsAsStep, contextWords, compactionWords, modelShortName } from './transcript.js'
import { richField, readField, caretPoint, setMarkdown } from './compose.js'
import { createFiles, folderView, fileView, fitPreviewTables, addSheet, parseFilesPath, listFromURL, filesHref, FILES_LOCATION_HIDDEN, isFolder, nameOf as fileNameOf, resolveOf, missingSentence, externalHref, vaultUrlRef, formatText, uploadAccept, ROOT_LOOK, drawnTypeOf, bytesAre, downloadHref } from './files.js'
import { createActivity } from './activity.js'
import { createApps, canFrame, buildAppThread } from './apps.js'
import { SETTINGS_SECTIONS, settingsHome } from './app-shape.js'

import { createThreads } from './threads.js'
import { threadHref, threadFromPath } from './thread-opening.js'
import { sessionFromAddress, rememberedSession, importedThreadId } from './imported-threads.js'
import { createRunning } from './running.js'
import { createPicker, pickerSheet } from './filepicker.js'
import { setFileLinks, setVaultUrls, anchorOf } from './markdown.js'
import { sentenceOf } from './link.js'


import { departureLine } from './asks.js'
import { createStore } from './store.js'

import { createSchedules, schedulePanel } from './schedules.js'
import { createTheme } from './theme.js'
import { agentLook, createAgents, providerGlyph, longAgo } from './sessions.js'
import { connectedCount, providerRows, providerDetail, loginView, listedModelName } from './providers.js'
import { providerMark } from './marks.js'
import { createProviderSignIn } from './provider-sign-in.js'
import { githubView, githubNotice, returnFrom, manifestForm, orgView, orgApps, offersOrgs, orgLogin, installOrg } from './integrations.js'
import { isMac, sends, sendHint } from './keys.js'
import { createAttachments, addFile, removeAttachment, clearAttachments, restoreAttachments, markProgress, markStored, markFailed, stored, heldFiles, uploading, tooBig, bytes, carriesFiles, filesFrom } from './attach.js'

import { markdown, strip } from './markdown.js'
import { copyText } from './clipboard.js'
import { appFrame, sameFrame } from './viewport.js'
import { layoutPixels } from './scale.js'
import { renderPanel, itemDescriptor, itemHref, itemFromURL, sideFromURL, withSide, migratePanelURL } from './panel-items.js'
import { createPanels, panelDOM, itemKey, readPanels } from './panels.js'
import { mount as mountConversation } from './page/conversation.js'
import { mountSidePanel } from './page/side-panel.js'

import { h, morph, kept, blob, icon, wave, loamMark, on as onNode } from './ui.js'

const lastClassicSession = rememberedSession()
const store = createStore()
const theme = createTheme(undefined, { changed: () => schedule(), onSaved: () => pluginLoader?.refresh() })
theme.apply()

const root = document.getElementById('app')
const menuSurfaces = createMenuSurfaces(root)

const state = {
  screen: 'home', // 'home' | 'threads' | 'profile' | 'files' | 'activity'
  // Shared settings, attachments, Running now, Files controls and integrations.
  sheet: null,
  agents: createAgents([]), // the host's agents: id, name — see sessions.js
  agentsReady: false,
  providers: [],
  profileAgent: null,
  backToThread: null,
  profileTab: 'activity',
  // What the composer is carrying, and the host's cap on it. The attachments
  // belong to the chat that is open and go with it when it closes.
  attach: createAttachments(),
  maxAttachmentBytes: null,
  dragging: false,
  brokerState: '',
  agentAvailability: null,
  // The desktop layout: three columns at 1100px and above, the phone's screens
  // below it. `wide` is read from the window on every draw rather than stored,
  // so a window dragged across the threshold is one redraw and not a listener
  // that has to remember which side it was on.
  wide: false,
  collapsed: store.sidebarHidden,
  navigator: 'chats', // desktop list only; never selects panel contents
  mainItem: null, // typed panel identity, independent of the navigator
  // The + menu, where the desktop draws it: a popover anchored above the +,
  // with two sub-pages it drills into. The phone draws the same content as the
  // + sheet, so below the width threshold this stays `null` and `state.sheet`
  // carries the page instead. `null` | { page: 'root' | 'model' | 'effort' }
  pop: null,
  // GitHub, the first integration: the broker's state as it last answered
  // (`data`), whether this broker has no such route (`missing`), the pill
  // that is going (`busy`), the broker's sentence for the last refusal
  // (`error`), the disconnect's one question (`confirm`) and its three links
  // afterwards (`links`), and an install address the register answer carried
  // (`installUrl`, or `orgInstall` — `{org, url}` — when the app it registered
  // is an organization's). `at` is the card the error and the done line are
  // drawn on: `github` for the first app's, an org's login for its own, and
  // `connect` for the Connect an organization card, whose form is `connect`.
  // See `loadGithub()`.
  github: { loaded: false, data: null, accounts: null, accountsError: '', missing: false, busy: null, error: '', done: '', at: 'github', confirm: false, links: null, installUrl: null, orgInstall: null, connect: false },

  // Whether the draft is open in the full-screen editor. The owner, 2026-09-25:
  // "Have an option in the text box to open a bigger full screen view to work on
  // the message that makes it easier to work on longer prompts." The draft
  // itself is not in here — there is one draft and it is the composer's field;
  // this is only whether the big one is drawn over the top of everything.
  editor: false,
  // Whether the composer's field has the caret, for the one thing that is drawn
  // from it: the expand glyph in the field's corner, which shows while a person
  // is writing and is gone from an empty box nobody is in.
  draftFocused: false,
  // The notice row inside the composer, as it was last drawn — `{ id, tone,
  // dot, text, until }` — so a row whose notice has cleared can shrink away
  // rather than vanish. `until` is set when it starts to leave. See `noticeRow()`.
  lastNotice: null,
}

// The width the desktop layout starts at, from the handoff: "minimum window
// width to implement: ~1100px". Below it the phone's screens stand unchanged.
const WIDE = 1100

// The page's session has ended (auth.js): Loam's sign-in look, from what the
// page already holds (its stylesheets and the mark drawn in place), since
// nothing behind the login can be fetched any more. Until 0.2.1 it was a bare
// line and a link.
function signInAgain() {
  return h('main', { class: 'signin-again', key: 'screen:signin-again' },
    h('div', { class: 'signin-again-sheet' },
      loamMark(52),
      h('h1', { class: 'display', text: 'Your session has ended' }),
      h('p', { class: 'signin-again-intro', text: 'Sign in again to carry on. Your agents kept working.' }),
      h('a', { class: 'pillbtn primary', href: globalThis.location?.href ?? '/', text: 'Sign in again' })))
}

function isWide() {
  const query = globalThis.matchMedia?.(`(min-width: ${WIDE}px)`)
  if (query) return Boolean(query.matches)
  return (globalThis.innerWidth ?? 0) >= WIDE
}

let dirty = false

// A redraw waits for the next frame, because a screen is drawn whole and there
// is no point drawing it twice between two of them.
//
// **A frame is not promised, and this is a page that lives on a phone.** A tab
// in the background, a screen that has gone off, a page restored from the
// back/forward cache: each can leave a `requestAnimationFrame` callback
// unrun. With `dirty` already set, every later `schedule()` — a stream tick,
// the heartbeat, coming back to the tab — returns at the first line, and the
// page never draws anything again until it is reloaded. So the frame is raced
// against a timer, and whichever arrives first draws, once.
const FRAME_WAIT = 250

function schedule() {
  if (dirty) return
  dirty = true
  requestAnimationFrame(draw)
  setTimeout(draw, FRAME_WAIT)
}

function draw() {
  if (!dirty) return
  dirty = false
  render()
}

// ------------------------------------------------------------------- the api

async function api(method, path, body) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? {} : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await res.text()
  let parsed = null
  try {
    parsed = text === '' ? null : JSON.parse(text)
  } catch {
    parsed = null
  }
  if (!res.ok) {
    // The broker's own words, whatever it refused for — a cap, a provider that
    // is not wired, a session it does not have. Never one of this app's.
    const err = new Error(failureSentence(sentenceOf(parsed) ?? text ?? `${method} ${path} failed`))
    err.status = res.status
    err.body = parsed
    throw err
  }
  if (!['GET', 'HEAD'].includes(method)) {
    pluginLoader?.refresh()
    if (path.startsWith('/plugins/daily-notes')) dailyNotes.refresh()
    if (/^\/plugins\/[^/]+\/secrets\//.test(path)) registry.emit('plugin.secrets.changed', { id: path.split('/')[2] })
    // A shared key changed (API keys): every plugin that uses it hears so.
    if (path.startsWith('/keys/')) for (const user of parsed?.plugins ?? []) registry.emit('plugin.secrets.changed', { id: user.id })
  }
  return parsed
}


// Lazy getters preserve initialization order without duplicating a state owner.
const modules = Object.create(null)
const moved = Object.fromEntries(Object.entries({
  files: "restoreFilePlace herePath pushAddress files filesUploadChooser chooseUpload fileField fileNameField fileFieldFor goFiles leaveFiles proceedThen openFileRef refNotice vaultUrls picker pickFromFiles applyTool embeds embedFor loadEmbed fileDictation filesScreen filesHome returnToChatButton",
  accounts: "loadGithub githubStep keepGithub setupGithub connectOrg installGithub refreshGithub disconnectGithub githubReturn providerSignIn connectKeys orgField orgAppField",
  composer: "DRAFT_MAX_PX MAC draft editorDraft openEditor closeEditor sendFromEditor fileChooser imageChooser growDraft submitDraft attachStrip chipSub loginNotice activeThread composer expandGlyph editorNodes menuPage menuRootOf menuOpen openPage openMenu togglePlus closeMenus composerMenu menuRow menuSheet menuGroups menuPopover",
  transcript: "lineKeys lineCount keyOf MINE_MAX_PX MINE_SLACK_PX fitMine COPIED_MS copied copyWithCue copyButton stepItems stepChildren itemNode foldCard stepNode askLine openBlocks longToolDetails toggleOpen toolNode toolDetail fitToolOutputs answeredLine openTurns toggleTurn turnModelName turnFacts turnLine",
  settings: "settingsBody settingsSheet settingsItems settingsDialog settingsPlugins settingsAppearance goSettings settingsBack settingsHead tile settingsRoot filesFolderFields settingsFiles settingsAgents settingsProviders settingsApiKey settingsAccount settingsConnections settingsAbout apiKeys settingsProvider usageCard providersWarn integrationsWarn listWords settingsIntegrations githubOutcome connectCard githubCard GH_PILLS githubPills githubConfirm githubLinks",
  layout: "nav profileScreen profileBody desk rail filesList listColumn browseSection setCollapsed mainPane profilePane backToChat filesPane",
  sheets: "openSheet closeSheet sheetTop SHEET_SETTLE_MS pickerNodes filesSheetNodes sheetNodes",
  market: "marketCards marketState openMarket openMarketInstall openLinkInstall marketNodes marketInstallNodes marketSync marketDoor marketFindMore marketLinkDoor loadMarket"
}).flatMap(([group, names]) => names.split(' ').map(name => [name, group])))
const shell = Object.defineProperties({}, {
  mainHistory: { get: () => mainHistory },
  backAction: { get: () => backAction },
  backButton: { get: () => backButton },
  routePanels: { get: () => routePanels },
  panels: { get: () => panels },
  panelId: { get: () => 'main' },
  panelDOM: { get: () => panelDOM('main') },
  panelHosts: { get: () => () => [page, sidePanel.host] },
  recordingAny: { get: () => () => [page, sidePanel.host].some(host => ['draft', 'editor', 'file'].some(target => host.fieldActivity(target)?.phase === 'recording')) },
  provide: { get: () => provide },
  fieldActivity: { get: () => target => registry.draw('composer.tool', { slot: 'state', target, host: page }).at(-1) ?? null },
  audioSessionType: { get: () => audioSessionType },
  agentLook: { get: () => agentLook },
  onNode: { get: () => onNode },
  h: { get: () => h },
  icon: { get: () => icon },
  kept: { get: () => kept },
  wave: { get: () => wave },
  morph: { get: () => morph },
  authExpired: { get: () => authExpired },
  fetch: { get: () => fetch },
  fileNameOf: { get: () => fileNameOf },
  menuSurface: { get: () => menuSurface },
  markdown: { get: () => markdown },
  readField: { get: () => readField },
  caretPoint: { get: () => caretPoint },
  setMarkdown: { get: () => setMarkdown },
  longAgo: { get: () => longAgo },
  registry: { get: () => registry },
  commands: { get: () => coreScope.commands },
  pluginLoader: { get: () => pluginLoader },
  pluginSettingsUI: { get: () => pluginSettingsUI },
  markdown: { get: () => markdown },
  lastClassicSession: { get: () => lastClassicSession },
  store: { get: () => store },
  theme: { get: () => theme },
  root: { get: () => root },
  menuSurfaces: { get: () => menuSurfaces },
  appModalHistory: { get: () => appModalHistory },
  state: { get: () => state },
  WIDE: { get: () => WIDE },
  isWide: { get: () => isWide },
  dirty: { get: () => dirty, set: value => { dirty = value } },
  FRAME_WAIT: { get: () => FRAME_WAIT },
  schedule: { get: () => schedule },
  draw: { get: () => draw },
  api: { get: () => api },
  activityOptions: { get: () => activityOptions },
  activity: { get: () => activity },
  profileActivity: { get: () => profileActivity },
  profiles: { get: () => profiles },
  dailyNotes: { get: () => dailyNotes },
  changedAgent: { get: () => changedAgent },
  settingsProfiles: { get: () => settingsProfiles },
  settingsProfileUI: { get: () => settingsProfileUI },
  engineerActivity: { get: () => engineerActivity },
  profileUI: { get: () => profileUI },
  schedulePanel: { get: () => schedulePanel },
  messageState: { get: () => messageState },
  longMine: { get: () => longMine },
  running: { get: () => running },
  threads: { get: () => threads },
  apps: { get: () => apps },
  goApps: { get: () => goApps },
  appsScreen: { get: () => appsScreen },
  importRequest: { get: () => importRequest, set: value => { importRequest = value } },
  openImportedSession: { get: () => openImportedSession },
  goChats: { get: () => goChats },
  goActivity: { get: () => goActivity },
  activityReturn: { get: () => activityReturn, set: value => { activityReturn = value } },
  openPhoneActivity: { get: () => openPhoneActivity },
  returnFromActivity: { get: () => returnFromActivity },
  activityScreen: { get: () => activityScreen },
  activityPanel: { get: () => activityPanel },
  schedules: { get: () => schedules },
  loadHealth: { get: () => loadHealth },
  runNotice: { get: () => runNotice },
  loadAgents: { get: () => loadAgents },
  loadProviders: { get: () => loadProviders },
  loadLimits: { get: () => loadLimits },
  goHome: { get: () => goHome },
  goProfile: { get: () => goProfile },
  closeThread: { get: () => closeThread },
  saveDraft: { get: () => saveDraft },
  chooseFiles: { get: () => chooseFiles },
  picked: { get: () => picked },
  intake: { get: () => intake },
  dragProps: { get: () => dragProps },
  uploadFile: { get: () => uploadFile },
  put: { get: () => put },
  render: { get: () => render },
  landOnFragment: { get: () => landOnFragment },
  visual: { get: () => visual },
  frame: { get: () => frame, set: value => { frame = value } },
  viewportNumbers: { get: () => viewportNumbers },
  fitViewport: { get: () => fitViewport },
  cameBack: { get: () => cameBack },
  swallow: { get: () => swallow },
  pageHeartbeat: { get: () => pageHeartbeat },
  loginHeartbeat: { get: () => loginHeartbeat },
  landedThread: { get: () => landedThread },
  landedOn: { get: () => landedOn }
})
const provide = serviceProviders(modules, schedule)
const calls = new Map()
const page = new Proxy(shell, {
  get(target, name) {
    if (!(name in moved)) return target[name]
    const part = modules[moved[name]]
    if (part) return part[name]
    if (!calls.has(name)) calls.set(name, (...args) => modules[moved[name]][name](...args))
    return calls.get(name)
  },
  set(target, name, value) {
    const part = name in moved ? modules[moved[name]] : target
    part[name] = value; return true
  },
})

const registry = createCoreRegistry({ changed: schedule, failed: (id, error) => pluginLoader?.failed(id, error) })
let pluginLoader = null
let pluginSettingsUI = null
const coreScope = registry.scope({ id: '@core' })
const activityOptions = { registry, api, redraw: schedule, agents: () => state.agents.list,
  avatar: (id) => blob(agentLook(id, state.agents), { size: 36, font: 16, className: 'activity-avatar' }),
  openRef: (ref) => page.openFileRef(ref),
  chats: (agent) => threads.activityRows(agent),
  chatFace: record => threads.activityFace(record),
  chatInfo: record => threads.activityInfo(record),
  openChat: record => openActivityThread(record.id),
  resources: () => running.resourcesView(),
  currentWork: jobs => running.inlineView(jobs),
  providers: () => state.providers,
  openThread: id => openActivityThread(id),
}
const activity = createActivity(activityOptions)
const profileActivity = createActivity({ ...activityOptions, scoped: true })
const engineerActivity = createActivity({ ...activityOptions, scoped: true })
const dailyNotes = createDailyNotes({ api, redraw: schedule })
const changedAgent = agent => {
  if (!agent) return
  const rows = state.agents.list
  state.agents = createAgents(agent.archived ? rows.filter(row => row.id !== agent.id)
    : state.agents.byId.has(agent.id) ? rows.map(row => row.id === agent.id ? agent : row) : [...rows, agent])
  threads.refreshAgents()
  sidePanel.threads.refreshAgents()
  loadAgentAvailability(true)
}

const profiles = createProfiles({ api, redraw: schedule, changedAgent })
const profileUI = mountProfile(page)
const settingsProfiles = createProfiles({ api, redraw: schedule, changedAgent })
const settingsProfileUI = mountProfile(page, { controller: settingsProfiles, activity: engineerActivity })
const messageState = createMessageState()
const longMine = messageState.long
const running = createRunning({ api, redraw: schedule, agents: () => state.agents.list, providers: () => state.providers,
  openPanel: () => { if (isWide() ? state.sheet?.kind !== 'activity' : state.screen !== 'activity') goActivity() },
  openConversation: (row) => { page.closeSheet(); if (row.kind === 'thread') openActivityThread(row.threadId); else openImportedSession(row.id) },
})
const threads = mountConversation(page, { messageState, dom: panelDOM('main'),
  navigate: (item) => panels.openMain(item),
  side: () => panels,
  viewAddress: view => {
    const url = new URL(globalThis.location?.href ?? threadHref(threads.st.open.thread.id), 'http://placeholder')
    if (view) url.searchParams.set('view', view)
    else url.searchParams.delete('view')
    if (!view && globalThis.history?.state?.threadView) mainHistory.go(-1)
    else page.pushAddress({ threadView: Boolean(view) }, '', url.pathname + url.search)
  },
  address: (id) => {
    if (id) state.mainItem = { kind: 'thread', id }
    else if (state.mainItem?.kind === 'thread') state.mainItem = null
    const href = id ? threadHref(id) : '/'
    if ((id || threadFromPath(page.herePath())) && page.herePath() !== href) page.pushAddress(null, '', href)
  },
  enter: item => { importRequest++; closeThread(); state.screen = 'threads'; state.mainItem = item; if (!state.wide) state.navigator = 'chats'; page.closeSheet(); schedule() },
  leave: goChats,
})
const apps = createApps({ registry, api, redraw: schedule, wide: () => state.wide,
  isCurrent: () => state.screen === 'apps', historyButton: () => backButton('main'),
  side: () => panels, navigate: item => panels.openMain(item),
  avatar: (id, { size = 40, font = 18 } = {}) => isOwner(id) ? blob({ initial: ownerInitial(), color: 'var(--latte-espresso)', ink: 'var(--latte-espresso-ink)', shape: '50%' }, { size, font }) : blob(agentLook(id, state.agents), { size, font }),
  agentName: id => isOwner(id) ? 'you' : state.agents.list.find(agent => agent.id === id)?.name ?? id ?? 'Unknown',
  newThread: () => buildAppThread(threads, state.agents.list), copy: copyText, leave: () => goApps(),
})
function goApps({ push = true } = {}) {
  if (state.screen === 'files') {
    if (!page.files.guard(null)) { page.files.st.sheet.proceed = page.proceedThen(goApps); return }
    page.files.closeFile()
  }
  importRequest++; page.closeSheet(); threads.close({ navigate: false }); closeThread()
  if (push && (page.herePath() !== '/apps' || globalThis.location?.search?.includes('url='))) page.pushAddress(null, '', '/apps')
  apps.st.selected = null
  state.navigator = 'apps'
  state.mainItem = null
  state.screen = 'apps'; const pending = apps.load(); schedule(); return pending
}
function appsScreen() { return h('div', { class: 'screen apps-screen', key: 'screen:apps' }, apps.view(), page.nav()) }

let importRequest = 0
function openActivityThread(id) {
  page.closeSheet()
  if (state.screen === 'files') {
    if (!page.files.guard(null)) {
      page.files.st.sheet.proceed = page.proceedThen(() => openActivityThread(id))
      schedule()
      return
    }
    page.files.closeFile()
  }
  return threads.open(id)
}
async function openImportedSession(id) {
  if (!id) return
  const ticket = ++importRequest
  try {
    const body = await api('GET', `/threads/imported?session=${encodeURIComponent(id)}`)
    if (ticket !== importRequest) return
    const threadId = importedThreadId(body, id)
    if (!threadId) throw new Error('No imported thread was found for this chat.')
    if (state.screen === 'files') {
      if (!page.files.guard(null)) { page.files.st.sheet.proceed = page.proceedThen(() => openImportedSession(id)); schedule(); return }
      page.files.closeFile()
    }
    await threads.open(threadId)
  } catch (error) {
    if (ticket !== importRequest) return
    goChats(); threads.st.error = error.message; schedule()
  }
}

function goChats() {
  importRequest++
  threads.showArchived(false)
  if (state.screen === 'files') {
    if (!page.files.guard(null)) { page.files.st.sheet.proceed = page.proceedThen(goChats); return }
    page.leaveFiles(); return
  }
  threads.close({ navigate: false }); goHome(); threads.load({ quiet: true })
  if (withSide(globalThis.location?.href ?? page.herePath(), null) !== '/') page.pushAddress(null, '', '/')
}


function goActivity({ push = true } = {}) {
  if (isWide()) {
    if (state.sheet?.kind === 'activity') { page.closeSheet({ historyBack: true }); return }
    page.openSheet({ kind: 'activity' })
    activity.load()
    return
  }
  if (state.screen !== 'activity') rememberActivityReturn()
  if (state.screen === 'files') {
    if (!page.files.guard(null)) {
      page.files.st.sheet.proceed = page.proceedThen(goActivity)
      return
    }
    page.files.closeFile()
  }
  if (push && state.screen !== 'activity') page.pushAddress(null, '', '/?screen=activity')
  page.closeSheet()
  threads.close({ navigate: false })
  closeThread()
  state.profileAgent = null
  state.screen = 'activity'
  activity.load()
  schedule()
}



let activityReturn = null
function openPhoneActivity() {
  return goActivity()
}

function rememberActivityReturn() {
  if (state.screen !== 'activity') {
    const file = page.files.st.file
    activityReturn = { screen: state.screen, profileAgent: state.profileAgent,
      thread: state.screen === 'threads' ? threads.st.open?.thread.id : null,
      file: state.screen === 'files' ? { root: file?.root ?? page.files.st.root, path: file?.path ?? page.files.st.path, isFile: Boolean(file) } : null }
  }
}

function returnFromActivity() {
  const back = activityReturn
  activityReturn = null
  page.closeSheet()
  if (!back && !isWide()) { goChats(); return }
  if (back?.file) { page.goFiles(back.file.root, back.file.path, { isFile: back.file.isFile }); return }
  if (back?.thread) { threads.open(back.thread); return }
  state.screen = back?.screen ?? 'home'
  state.profileAgent = back?.profileAgent ?? null
  schedule()
}

function activityScreen() {
  return h('div', { class: 'screen activity-screen', key: 'screen:activity' }, activityPanel(), page.nav())
}

function activityPanel() {
  return h('div', { class: 'activity-panel' },
    !state.wide ? h('div', { class: 'activity-return' },
      backButton('main') ?? h('button', { class: 'backbtn', 'aria-label': 'Back from Activity', onclick: returnFromActivity }, icon.chevronLeft(18), 'Back')) : null,
    activity.view())
}
const schedules = createSchedules({ api, redraw: schedule, openFile: ({ root, path }) => page.goFiles(root, path, { isFile: true }) })


async function loadHealth() {
  const health = await api('GET', '/health').catch(() => null)
  // Nothing of Listen or Brief is drawn unless the server says it can speak.
  registry.emit('health.changed', health)
  setAvatarColours(health?.avatar_colours)
  if (!health?.configured) {
    state.brokerState = 'No broker is configured for this app (BROKER_URL is unset), so no session can be started.'
  } else if (!health.ok) {
    state.brokerState = `The broker is not answering${health.status ? ` (${health.status})` : ''}. The page is up; the sessions are not.`
  }
  schedule()
}

let availabilityReading = false
let nextAvailabilityRead = 0
async function loadAgentAvailability(force = false) {
  if (authExpired() || availabilityReading || (!force && Date.now() < nextAvailabilityRead)) return
  availabilityReading = true
  nextAvailabilityRead = Date.now() + 15000
  let notice = null
  try {
    const status = await api('GET', '/agent-availability')
    if (status?.agents_can_run === false) notice = status
  } catch {
    // The thread's reconnect feedback already covers a host that can't answer.
  } finally {
    availabilityReading = false
  }
  if (JSON.stringify(notice) !== JSON.stringify(state.agentAvailability)) {
    state.agentAvailability = notice
    schedule()
  }
}

function runNotice() {
  const status = state.agentAvailability
  if (status?.agents_can_run !== false || !status.why?.[0]) return null
  const why = status.why[0], action = status.action
  const atProvider = action?.kind === 'provider' && state.sheet?.page === 'provider' && state.sheet.provider === action.provider
  const atConnections = action?.kind === 'providers' && state.sheet?.page === 'connections'
  const atAgents = action?.kind === 'agents' && state.sheet?.page === 'agents' && !state.sheet.detail && !state.sheet.archivedAgents
  const atArchive = action?.kind === 'archived' && state.sheet?.archivedAgents
  const act = action && !atProvider && !atConnections && !atAgents && !atArchive
  return h('div', { class: 'loginwarn run-notice', key: 'agent-availability', role: 'status' },
    h('span', { text: `Agents can’t run: ${act && action.label === why ? '' : why}` }),
    act ? h('button', { class: 'loginwarn-act', type: 'button', text: action.label, onclick: () => {
      if (state.editor) page.closeEditor()
      if (action.kind === 'provider') {
        page.goSettings('provider', action.provider)
        loadProviders()
      } else if (action.kind === 'providers') {
        page.goSettings('providers')
        loadProviders()
      } else {
        page.goSettings('agents')
        if (action.kind === 'archived') {
          state.sheet.archivedAgents = true
          settingsProfiles.archived()
        }
      }
    } }) : null)
}

async function loadAgents() {
  try {
    const body = await api('GET', '/agents')
    // Objects — `{id, name}` — sorted by id. One answer, so this reads
    // one shape rather than guessing between two. `name` is what a person is
    // shown and the host is the only one that can state it; the avatar's
    // initial is a fact about the whole set, and sessions.js works it out.
    state.agents = createAgents(body?.agents)
    state.agentsReady = Array.isArray(body?.agents)
  } catch {
    state.agents = createAgents([])
    state.agentsReady = false
  }
  schedule()
}

// Every provider the host knows, wired or not. A row saying "Codex — not
// wired" is an answer; a missing row is a bug report. The sentence in `reason`
// is the host's and is shown as given.
async function loadProviders() {
  try {
    const body = await api('GET', '/providers')
    state.providers = Array.isArray(body?.providers) ? body.providers : []
    state.providersError = ''
  } catch (error) {
    state.providersError = error.message
    state.providers = []
  }
  loadAgentAvailability(true)
  schedule()
}

// The one number the page has to know before it does something the host would
// refuse: the biggest file a chat carries, which the host answers on its own
// limits route and this app relays. It is asked for rather than assumed, and
// until it answers nothing is refused in the browser — the broker refuses the
// bytes itself, in its own sentence, so an unanswered `/api/limits` costs a
// person a wasted upload and never a file that should have gone and did not.
async function loadLimits() {
  const limits = await api('GET', '/limits').catch(() => null)
  const cap = limits?.attachment_max_bytes
  state.maxAttachmentBytes = Number.isFinite(cap) ? cap : null
  page.files.setUploadLimits(limits)
}


// ---------------------------------------------------------------- navigating

function goHome() {
  importRequest++
  closeThread()
  state.screen = 'home'
  state.navigator = 'chats'
  state.mainItem = null
  state.profileAgent = null
  schedule()
}

function goProfile(agent, { push = true } = {}) {
  importRequest++
  state.backToThread = page.activeThread()?.thread.id ?? null
  threads.close({ navigate: false })
  // Which chat "Back to chat" goes back to on the desktop's profile pane. The
  // thread is closed either way — a profile is not a chat with a header on it
  // — so what is kept is the id, not the connection.
  closeThread()
  state.screen = 'profile'
  state.mainItem = { kind: 'profile', id: agent }
  if (push) page.pushAddress(null, '', itemHref(state.mainItem))
  state.profileAgent = agent
  state.profileTab = 'activity'
  profileActivity.st.kind = ''; profileActivity.st.project = ''
  profileActivity.filter('agent', agent)
  profiles.load('agent', agent)
  schedules.load(agent)
  schedule()
}

function closeThread() {
  registry.emit('field.reset', { host: page }); state.editor = false;
  page.closeMenus(); clearAttachments(state.attach); page.draft.value = ''; page.growDraft()
}

// The composer's contents, under the id of the chat they were written in. The
// files are the ones the host has already written down: a path is the whole of
// what this browser holds of a file, and a chip for an upload that was still
// going when the chat was left would be a chip about nothing.
function saveDraft() { if (page.activeThread()) threads.saveCompose() }


// -------------------------------------------------------------- attachments

// A file or an image, from the phone's own chooser to the host's disk.
//
// Three things about this are deliberate. The browser's chooser is the only
// picker — "Pick from Files" is the app's own vault and Files is out of scope,
// so that row is on the sheet and disabled rather than missing. The cap is
// answered before a byte moves, because a phone on a slow connection should be
// told in the composer and not after a minute. And the bytes go through this
// app's `/api` for the same reason everything else does: the broker's token is
// in the container and never in the page.
function chooseFiles(input) {
  page.closeMenus()
  // A chooser that answers with the same file twice must fire twice, and an
  // `input` whose value still holds that file does not.
  input.value = ''
  input.click()
}

function picked(input) {
  const files = [...(input.files ?? [])]
  input.value = ''
  intake(files.map((file) => ({ file, name: file.name })))
}

// Every file this app takes in, however it arrived: the chooser above, a
// screenshot pasted into the field, a drag dropped on the chat. One path, so a
// pasted image is refused for the same reason and with the same sentence as a
// chosen one, and shows the same chip while it goes.
//
// **A draft holds its files rather than sending them**, because the attachments
// route is a session's and a draft has no session. The chip is the same chip and
// the cap is checked in the same place; what is different is only when the bytes
// move, which is the moment the first message creates the chat. See
// The thread owns the upload.
//
// It answers the uploads it started, so a caller that has to wait for them —
// there is exactly one, and it is that first message — can.
function intake(entries) {
  if (!page.activeThread() || page.activeThread().creating) return []
  const going = []
  for (const { file, name } of entries) {
    const complaint = tooBig(file, state.maxAttachmentBytes)
    const item = addFile(state.attach, file, { error: complaint, name })
    if (!complaint) going.push(uploadFile(item, file))
  }
  schedule(); return going
}

// ------------------------------------------------------------ drag and drop
//
// A file dragged onto a chat is a file attached to the message being written,
// which is what every other chat application on the machine does and what the
// browser will otherwise answer by *navigating to the file* — a dropped image
// replaces the page with the image, and the draft, the chips and the open
// stream go with it. So the document refuses the default everywhere (`swallow`
// at the foot of this file) and the chat takes it.
//
// Two facts about `dragover` shape this. It fires continuously while the
// pointer moves, so the highlight is set once and redrawn only on the change;
// and its `preventDefault` is what tells the browser a drop is allowed here at
// all — without it the drop event never comes.
function dragProps() {
  return {
    ondragover: (event) => {
      if (!carriesFiles(event.dataTransfer)) return
      event.preventDefault?.()
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
      if (state.dragging) return
      state.dragging = true
      schedule()
    },
    // A pointer leaving for a node inside this one has not left: a drag across
    // the composer crosses a dozen elements and a highlight that flickered on
    // each of them would be the page arguing with itself.
    ondragleave: (event) => {
      if (!state.dragging) return
      const to = event.relatedTarget ?? null
      if (to && event.currentTarget?.contains?.(to)) return
      state.dragging = false
      schedule()
    },
    ondrop: (event) => {
      event.preventDefault?.()
      state.dragging = false
      intake(filesFrom(event.dataTransfer))
      schedule()
    },
  }
}


async function uploadFile(item, file) {
  const session = page.activeThread()
  if (!session || session.creating) return
  const attachmentsPath = `/api/threads/${encodeURIComponent(session.thread.id)}/attachments`
  const attachmentState = state.attach
  const form = new FormData()
  form.append('file', file, item.name)
  try {
    const answer = page.activeThread() && threads.st.fixture ? await threads.uploadPreview(file, item.name) : await put(attachmentsPath, form, (loaded, total) => {
      markProgress(attachmentState, item, loaded, total)
      schedule()
    })
    markStored(attachmentState, item, answer)
  } catch (err) {
    // The host's own words for a refusal — the cap, a session it does not have.
    markFailed(attachmentState, item, page.activeThread() && [404, 405].includes(err.status) ? 'Attachments aren’t available on this runtime yet.' : err.message)
  }
  // A file the host has written down is part of the draft from here on, so it
  // is parked with the words beside it.
  saveDraft()
  schedule()
}

// The bytes on the wire, with progress where the browser can report it.
//
// `fetch` cannot say how far an upload has got — there is no progress event on
// a request body — so this is the one place in the app that uses
// `XMLHttpRequest`, and it falls back to `fetch` where there is none. A browser
// without it gets a pending chip instead of a moving one, which is honest: the
// page says a file is going up and does not invent how far.
function put(url, body, onProgress) {
  const XHR = globalThis.XMLHttpRequest
  if (!XHR) {
    return fetch(url, { method: 'POST', body }).then(async (res) => {
      const parsed = await res.json().catch(() => null)
      if (!res.ok) throw Object.assign(new Error(sentenceOf(parsed) ?? `the upload was refused (${res.status})`), { status: res.status })
      return parsed
    })
  }
  return new Promise((resolve, reject) => {
    const xhr = new XHR()
    xhr.open('POST', url)
    xhr.upload?.addEventListener('progress', (event) => {
      if (event.lengthComputable) onProgress(event.loaded, event.total)
    })
    xhr.addEventListener('load', () => {
      if (xhr.status === 401 || (xhr.responseURL && new URL(xhr.responseURL, location.href).pathname !== new URL(url, location.href).pathname)) { expireAuth(); reject(Object.assign(new Error('Sign in again'), { status: 401 })); return }
      let parsed = null
      try {
        parsed = JSON.parse(xhr.responseText || 'null')
      } catch {
        parsed = null
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(Object.assign(new Error(sentenceOf(parsed) ?? `the upload was refused (${xhr.status})`), { status: xhr.status }))
        return
      }
      resolve(parsed)
    })
    xhr.addEventListener('error', () => reject(new Error('the upload did not reach this app')))
    xhr.addEventListener('abort', () => reject(new Error('the upload was stopped')))
    xhr.send(body)
  })
}

const onboarding = { checking: true, needed: false, hello: null }
async function continueWelcome() {
  if (!state.providers.some(p => ['claude-code', 'codex', 'antigravity'].includes(p.id) && p.connected)) return
  page.providerSignIn.cancel()
  onboarding.needed = false
  await threads.open(onboarding.hello)
  schedule()
}
function render() {
  registry.emit('rendering', {})
  state.wide = isWide()
  if (!state.wide && state.sheet?.kind === 'activity') openPhoneActivity()
  if (state.wide && state.screen === 'activity') {
    returnFromActivity()
    page.openSheet({ kind: 'activity' })
  }
  appModalHistory.sync()
  panels.sync()
  state.collapsed = store.sidebarHidden || panels.tucked()
  panels.restore()
  if (!panels.visible() && ['draft', 'editor', 'file'].some(target => sidePanel.host.fieldActivity(target))) registry.emit('field.reset', { host: sidePanel.host })
  if (state.screen.startsWith('plugin:') && !registry.entries('rail.section').some(entry => entry.id === state.screen.slice(7))) goChats()
  const modern = state.screen === 'threads' || state.screen === 'home'
  const currentSection = state.wide ? state.navigator : state.screen
  if (previousSection !== currentSection) { registry.emit('section.changed', { from: previousSection, section: currentSection }); previousSection = currentSection }
  const section = registry.entries('rail.section').find(entry => !entry.component &&
    (state.screen.startsWith('plugin:') ? entry.id === state.screen.slice(7) : entry.screens?.includes(state.screen)))
  // Register visible side speech rows before the main surface asks for the one
  // floating player, so it never duplicates controls already beside it.
  const beside = panels.visible() ? sidePanel.view() : null
  const content = !state.wide && section ? registry.invoke(section, 'render', { wide: false, screen: state.screen }) : null
  const welcoming = onboarding.checking || onboarding.needed
  const screen = authExpired() ? signInAgain()
    : welcoming ? welcomeScreen({ checking: onboarding.checking, providers: state.providers, signIn: page.providerSignIn, continueWith: continueWelcome, retry: loadProviders, error: state.providersError })
    : state.wide ? page.desk(beside) : section?.frame === 'full' ? content : h('div', { class: 'screen plugin-screen' },
      backButton('main'), state.screen === 'item' ? renderPanel({ host: page, item: state.mainItem }) : content, page.nav())
  const notice = state.editor || state.sheet?.kind === 'settings' ? null : runNotice()
  morph(root, authExpired() || welcoming ? [screen] : [notice, screen, ...registry.draw('app.overlay', { slot: 'overlay', screen: state.screen, wide: state.wide })])
  page.fitToolOutputs(); if (modern || state.wide && state.navigator === 'chats') threads.afterRender()
  if (panels.visible()) sidePanel.afterRender()
  page.restoreFilePlace(); if (panels.visible()) sidePanel.host.restoreFilePlace()
  activity.syncDialog(); profileActivity.syncDialog(); engineerActivity.syncDialog(); profileUI.syncFocus(); settingsProfileUI.syncFocus()
  running.syncDialog(); fitPreviewTables(root); page.fitMine(); landOnFragment(); menuSurfaces.sync(); files.focusRename(); files.revealOpen(root); revealThemeChoice(); registry.emit('rendered', { root, screen: state.screen })
}


// A link to a heading opens its file and then lands on the heading, once,
// when the preview that holds it has been drawn.
function revealThemeChoice() {
  const picker = root.querySelector?.('.theme-picker'), chosen = picker?.querySelector('.theme-choice.on')
  const selection = `${theme.value.theme}:${picker?.clientWidth}`
  if (!chosen?.getBoundingClientRect || picker.__chosenTheme === selection) return
  const card = chosen.getBoundingClientRect(), strip = picker.getBoundingClientRect()
  if (!strip.width) return
  picker.__chosenTheme = selection
  if (card.left < strip.left) picker.scrollLeft += layoutPixels(card.left - strip.left)
  else if (card.right > strip.right) picker.scrollLeft += layoutPixels(card.right - strip.right)
}

function landOnFragment() {
  const f = page.files.st.file
  if (!f?.fragment || f.loading || f.mode !== 'preview') return
  const want = anchorOf(f.fragment.replace(/^\^/, ''))
  const heading = [...(root.querySelectorAll?.('.anchored') ?? [])].find((n) => n.getAttribute('data-anchor') === want && (n.closest?.('[data-panel]')?.getAttribute('data-panel') ?? 'main') === 'main')
  f.fragment = null
  heading?.scrollIntoView?.({ block: 'start' })
  f.landed = heading ? want : null
}


// ------------------------------------------------- the keyboard, and the box
//
// The app is sized to the **visual** viewport rather than left at the
// stylesheet's `100dvh`, because a phone's keyboard does not shorten the page:
// it slides over the bottom of it. Laid out to the window's height, the
// composer ends up in the part of the app the keyboard is covering — the send
// button half off the screen and nothing under it, which is what the owner saw.
//
// `viewport.js` is the arithmetic and is a pure function of the numbers below;
// this is the six lines that read them and write the result onto `#app`. The
// transcript is kept at its end across the change when the reader was already
// there, which is the same rule a delta follows.
const visual = globalThis.visualViewport ?? null
let frame = null

function viewportNumbers() {
  return {
    innerHeight: globalThis.innerHeight ?? 0,
    height: visual?.height ?? null,
    offsetTop: visual?.offsetTop ?? 0,
    scale: visual?.scale ?? 1,
  }
}

function fitViewport() {
  const next = appFrame(viewportNumbers())
  if (sameFrame(frame, next)) return
  frame = next
  // A browser with no visual viewport and no `innerHeight` to read is left
  // with the stylesheet's own height rather than being given a zero.
  if (next.height === 0) return
  root.style.height = `calc(${next.height}px / var(--ui-scale, 1))`
  // The browser may scroll the visual viewport down over the layout one to
  // lift the focused field clear of the keyboard; the app goes with it.
  root.style.transform = next.offset === 0 ? '' : `translateY(calc(${next.offset}px / var(--ui-scale, 1)))`
  // For the stylesheet, which uses it for nothing yet and is the honest place
  // to say a keyboard is up if it ever needs to.
  root.dataset.keyboard = next.keyboard > 0 ? 'open' : 'closed'
}


// Register overlay history before the document router: a modal-only Back must
// never reach the thread opener and replace its in-memory conversation.
const mainHistory = createMainHistory({
  itemAt: url => itemKey(itemFromURL(url)),
  sideKey: () => itemKey(panels.st.side),
  guard: retry => {
    const target = itemKey(itemFromURL(location.href))
    if (target === itemKey(mainPanel.item())) return true
    return mainPanel.guard(retry)
  },
  syncSide: () => syncSideAddress(panels.st.side),
})
const appModalHistory = createAppModalHistory({ push: mainHistory.push, wide: isWide, sheet: () => state.sheet,
  changed: schedule, close: () => page.closeSheet(), restore: sheet => {
    if (sheet.kind === 'market') { modules.market.openMarket({ kind: sheet.k, q: sheet.q, id: sheet.id }); if (sheet.install) modules.market.openLinkInstall({ link: sheet.install }); return }
    page.openSheet(sheet)
    if (sheet.kind === 'activity') activity.load()
    if (sheet.detail) modules.settings.goSettingsDetail(sheet.detail.kind, sheet.detail.id)
  } })
modules.files = mountFiles(page)
modules.accounts = mountAccounts(page)
modules.composer = mountComposer(page)
modules.transcript = mountTranscript(page)
modules.settings = mountSettings(page)
modules.layout = mountLayout(page)
modules.sheets = mountSheets(page)
modules.market = mountMarket(page)
const sidePanel = mountSidePanel(page)
const mainPanel = {
  threads,
  pending: () => Boolean(threads.st.open?.creating || threads.st.open?.createFailed),
  item: () => state.mainItem?.kind === 'file' && files.st.file ? { ...state.mainItem, mode: files.st.file.mode }
    : state.mainItem?.kind === 'app' && apps.st.selected ? { kind: 'app', app: apps.st.selected }
    : state.mainItem?.kind === 'thread' ? { ...state.mainItem, view: threads.currentView() } : state.mainItem,
  guard: retry => {
    if (!files.dirty()) return true
    files.guard(null); files.st.sheet.proceed = page.proceedThen(retry); return false
  },
  transfer: (to, id) => threads.transferTo(to.threads, id),
  clear: () => { state.mainItem = null; threads.close({ navigate: false }); files.closeFile(); apps.st.selected = null; closeThread(); if (state.screen !== 'apps') state.screen = 'home' },
  emptyAddress: () => { if (page.herePath() !== '/') page.pushAddress(null, '', '/') },
  open: item => {
    if (item.kind === 'thread') {
      files.closeFile(); apps.st.selected = null
      const pending = threads.open(item.id, { routed: true, push: item.push !== false, listing: item.listing })
      if (item.view) threads.setView(item.view)
      if (item.message && threads.st.open) Object.assign(threads.st.open, { replyJump: item.message, jumpKind: 'quote' })
      return pending
    }
    if (item.kind === 'file') return page.goFiles(item.root, item.path, { isFile: true, row: item.row, fragment: item.fragment, mode: item.mode, routed: true, push: item.push !== false, list: item.list })
    if (item.kind === 'app' && !state.wide && item.push === false) return goApps({ push: false })
    if (item.kind === 'profile') return goProfile(item.id, { push: item.push !== false })
    const ticket = ++importRequest
    threads.close({ navigate: false }); files.closeFile(); closeThread(); page.closeSheet()
    state.mainItem = itemDescriptor(item)
    state.screen = item.kind === 'app' ? 'apps' : 'item'
    if (item.push !== false) page.pushAddress(null, '', itemHref(item))
    if (item.kind !== 'app') { schedule(); return }
    const url = item.url ?? item.app?.url
    apps.st.selected = null
    if (item.app && item.app.kind !== 'plugin') { apps.open(item.app, null, { routed: true }); return }
    apps.load().then(() => {
      if (state.mainItem?.kind !== 'app' || importRequest !== ticket || apps.st.error) return
      const app = apps.st.apps.find(app => app.url === url)
      if (app && canFrame(app)) apps.open(app, null, { routed: true })
      else { apps.st.error = app ? 'This app opens in a browser.' : 'That app is no longer available.'; schedule() }
    })
  },
}

function syncSideAddress(side) {
  if (!globalThis.location?.href || !globalThis.history?.replaceState) return
  const href = withSide(location.href, side)
  if (href !== location.pathname + location.search + location.hash) history.replaceState(history.state, '', href)
}
const panels = createPanels({ main: mainPanel, side: sidePanel, changed: schedule, address: syncSideAddress })
function backAction(panel) {
  if (panel === 'side') return panels.canSideBack() ? { title: backTitle(), go: () => panels.goSide(-1) } : null
  return mainHistory.canBack() ? { title: backTitle(), go: () => mainHistory.go(-1) } : null
}
function backButton(panel) { return historyBackButton(backAction(panel)) }
// Initial load restores both slots from the address. Traversal keeps side mounted.
if (globalThis.location?.href) {
  const migrated = migratePanelURL(location.href, readPanels())
  if (globalThis.history?.replaceState && migrated !== location.pathname + location.search + location.hash) history.replaceState(history.state, '', migrated)
  panels.st.side = sideFromURL(location.href)
}
function routePanels(value = globalThis.location?.href) {
  if (!value) return
  const url = new URL(value), item = itemFromURL(value)
  const at = parseFilesPath(url.pathname)
  if (at && (!state.wide || !item || history.state?.folder || !history.state?.files)) {
    if (itemKey(item) !== itemKey(mainPanel.item())) mainPanel.clear()
    if (state.wide && !item) state.navigator = 'files'
    page.goFiles(at.root, at.path, { push: false, isFile: history.state?.folder ? false : null, list: listFromURL(value) })
    return
  }
  if (item) {
    panels.openMain({ ...item, list: listFromURL(value), push: false })
    if (item.kind === 'thread') threads.setView(url.searchParams.get('view'))
    return
  }
  mainPanel.clear()
  if (url.pathname === '/apps') goApps({ push: false })
  else if (url.searchParams.get('screen') === 'activity') goActivity({ push: false })
  else if (url.searchParams.get('screen')?.startsWith('plugin:')) state.screen = url.searchParams.get('screen')
  else { state.screen = 'home'; if (!state.wide) state.navigator = 'chats' }
  schedule()
}

panels.sideHost = sidePanel.host
// Capture before a control can stop propagation. This observes interaction
// without moving keyboard focus, changing navigation or rebuilding a mount.
function focusPanel(target) {
  const pane = target?.closest?.('.panel-pair > [data-panel]')
  if (pane) panels.focus(pane.getAttribute('data-panel'))
}
root.addEventListener('pointerdown', event => focusPanel(event.target), true)
root.addEventListener('focusin', event => focusPanel(event.target), true)
// Embedded apps and file previews own another document (often cross-origin).
// Their events cannot bubble here; entering one focuses its iframe element.
window.addEventListener('blur', () => {
  if (document.activeElement?.tagName === 'IFRAME') focusPanel(document.activeElement)
})
const { herePath, pushAddress, files, filesUploadChooser, chooseUpload, fileField, fileNameField, fileFieldFor, goFiles, leaveFiles, proceedThen, openFileRef, refNotice, picker, pickFromFiles, applyTool, embeds, embedFor, loadEmbed, fileDictation, filesScreen, filesHome, returnToChatButton, loadGithub, githubStep, keepGithub, setupGithub, connectOrg, installGithub, refreshGithub, disconnectGithub, githubReturn, providerSignIn, connectKeys, orgField, orgAppField, DRAFT_MAX_PX, MAC, draft, editorDraft, openEditor, closeEditor, sendFromEditor, fileChooser, imageChooser, growDraft, submitDraft, attachStrip, chipSub, loginNotice, activeThread, composer, expandGlyph, editorNodes, menuPage, menuRootOf, menuOpen, openPage, openMenu, togglePlus, closeMenus, composerMenu, menuRow, menuSheet, menuGroups, menuPopover, lineKeys, keyOf, MINE_MAX_PX, MINE_SLACK_PX, fitMine, COPIED_MS, copyWithCue, copyButton, stepItems, stepChildren, itemNode, foldCard, stepNode, askLine, openBlocks, longToolDetails, toggleOpen, toolNode, toolDetail, fitToolOutputs, answeredLine, openTurns, toggleTurn, turnModelName, turnFacts, turnLine, settingsBody, settingsSheet, settingsItems, settingsDialog, settingsPlugins, settingsAppearance, goSettings, settingsBack, settingsHead, tile, settingsRoot, filesFolderFields, settingsFiles, settingsAgents, settingsProviders, settingsProvider, usageCard, providersWarn, integrationsWarn, listWords, settingsIntegrations, githubOutcome, connectCard, githubCard, GH_PILLS, githubPills, githubConfirm, githubLinks, nav, profileScreen, profileBody, desk, rail, listColumn, setCollapsed, mainPane, profilePane, backToChat, filesPane, openSheet, closeSheet, sheetTop, SHEET_SETTLE_MS, pickerNodes, filesSheetNodes, sheetNodes } = page

registerPage({ ...coreScope, ui: { host: page } })
let previousSection = null
const pluginThemes = createPluginThemes({ theme, failed: (id, error) => { if (error) pluginLoader.failed(id, error); else pluginLoader.st.failures.delete(id) } })
pluginLoader = createPluginLoader({ registry, themes: pluginThemes, request: api, changed: schedule,
  ui: { ...sharedUI, ...chatUI, createSecretSettings, menuSurface, audioSessionType, markdown, host: page, token: name => globalThis.getComputedStyle?.(document.documentElement).getPropertyValue(name).trim() },
  fallback: async () => (await (await fetch('/shipped-plugins.json')).json()).plugins,
  importer: url => import(url.startsWith('/plugins/') ? new URL('.' + url, import.meta.url).href : url),
})
pluginSettingsUI = createPluginSettings({ loader: pluginLoader, theme, host: page, changed: schedule })
// Files holds its own Markdown preview while a plugin that draws Markdown
// files is still starting (plugin-loader.js, `pending`).
files.pluginPending = (place) => pluginLoader.pending(place)
const pluginsReady = pluginLoader.refresh()
dailyNotes.refresh()


// ------------------------------------------------------------------- wiring

// Coming back is the moment this page has to act on, and a phone gives it
// several names: the tab becoming visible, a page restored from the
// back/forward cache, the network returning, the window regaining focus. All
// four mean the same thing here — check every stream and reopen at once what is
// closed or has gone quiet. `wake()` does nothing to a healthy one.
function cameBack() {
  running.wake()
  if (authExpired()) return
  loadAgentAvailability()
  fitViewport()
  threads.tick(['threads', 'home', 'activity', 'profile'].includes(state.screen) || state.wide && state.navigator === 'chats')
  sidePanel.threads.tick(panels.visible() && sidePanel.host.state.screen === 'threads')
  schedule()
}

// The keyboard opening, the keyboard closing, the address bar collapsing, the
// phone turning: all of them are the visual viewport changing size or sliding
// over the layout one, and all of them are this. `resize` on the window is the
// browser that has no `visualViewport` to offer.
visual?.addEventListener('resize', fitViewport)
visual?.addEventListener('scroll', fitViewport)
// A window that changes width may have crossed the desktop threshold, which is
// read at draw time — so a resize asks for a draw as well as for the box.
window.addEventListener('resize', () => {
  fitViewport()
  schedule()
})
window.addEventListener('orientationchange', () => setTimeout(fitViewport, 200))
fitViewport()

// **Escape closes what is over the screen.** Found on the deployed app at
// 1280px, 2026-09-22: the New-chat dialog could not be dismissed with Escape
// and carries no close control of its own, so a click on the backdrop was the
// only way out of it — which is a dialog that has to be guessed at.
//
// One handler for both chromes and both layouts. The popover goes first because
// it is the thing on top when a menu has been opened over a screen; the sheet —
// which is what a dialog is made of, and what the phone draws along its bottom
// edge — goes second. Nothing about how a sheet is drawn changes: this is a key
// added for the windows that have a keyboard, and a phone that has one gets it
// too rather than being told it does not.
//
// It acts only when something is open, so the Escape that cancels an inline
// rename is still the field's own and is not answered twice.
document.addEventListener('keydown', event => historyShortcut(event, delta => {
  if (state.sheet && !globalThis.history?.state?.appModal) return false
  if (panels.focused() === 'side' && !state.sheet) {
    panels.goSide(delta)
    return true // A side boundary must not fall through to browser navigation.
  }
  return mainHistory.go(delta)
}))

document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return
  const target = event.target?.closest?.('[data-panel="side"]') ? sidePanel.host : page
  const keyState = target.state
  // A take first: it is the thing in the field, and Escape is its ✕ — the
  // take goes and the field is what it was. Nothing else is open under a take,
  // because everything that opens one is disabled while it runs.
  event.panel = target.panelId
  registry.emit('key.escape', event)
  if (event.defaultPrevented) return
  if (document.querySelector?.('dialog[open]')) return
  if (keyState.pop) {
    keyState.pop = null
    schedule()
  } else if (keyState.sheet) {
    target.closeSheet()
  } else if (keyState.editor) {
    // Last, because it is the screen everything else opens *over*. Escape out of
    // it keeps the words, exactly as Done does — there is one draft and nothing
    // here to discard.
    target.closeEditor()
  } else if (!event.defaultPrevented && ['home', 'threads'].includes(state.screen) && threads.st.archived && (state.wide || !threads.st.open)) {
    threads.showArchived(false)
  } else return
  event.preventDefault?.()
})

document.addEventListener('visibilitychange', () => {
  // The browser let the screen's wake lock go when the page was hidden; a take
  // still recording asks for it again.
  if (document.visibilityState === 'visible') {
    registry.emit('page.visible', {})
    cameBack()
  }
  // Going away is leaving the chat as far as the mark is concerned: a phone
  // locked mid-turn has read what is on its screen, and a throttle that had not
  // fired yet would take that reading with it.
})
// A tab being closed is the same moment, and it is the one a `visibilitychange`
// is not promised for on every browser.
// A file dropped anywhere but a chat. The browser's own answer to that is to
// navigate to the file, which throws the page away — the draft, the chips and
// the open stream with it — so the document swallows it and nothing else
// happens. The chat's own handlers run first and take the ones meant for it;
// these two are what is left.
//
// `dragover` has to be refused as well as `drop`: a browser that is not told a
// drop is allowed sends no drop event and navigates anyway.
const swallow = (event) => {
  if (!carriesFiles(event.dataTransfer)) return
  event.preventDefault?.()
}
document.addEventListener('dragover', swallow)
document.addEventListener('drop', (event) => {
  swallow(event)
  // A drop that missed the chat still ended the drag, and a tint left behind
  // would be the page saying a file is still in the air.
  if (!state.dragging) return
  state.dragging = false
  schedule()
})
// A drag that leaves the window altogether reports no node it went to. The same
// clearing, for the one way out that sends no drop at all.
document.addEventListener('dragleave', (event) => {
  if (!state.dragging || event.relatedTarget) return
  state.dragging = false
  schedule()
})

window.addEventListener('pageshow', cameBack)
window.addEventListener('online', cameBack)
window.addEventListener('focus', cameBack)

// A slow heartbeat, for a page left open on a desk: none of the events above
// fires when a connection dies under a page nobody touched. It also redraws,
// which is what moves "5m" on to "6m".
const pageHeartbeat = setInterval(() => {
  running.wake()
  if (document.visibilityState !== 'hidden') {
    loadAgentAvailability()
    threads.tick(['threads', 'home', 'activity', 'profile'].includes(state.screen) || state.wide && state.navigator === 'chats')
    sidePanel.threads.tick(panels.visible() && sidePanel.host.state.screen === 'threads')
    activity.tick(state.screen === 'activity' || state.sheet?.kind === 'activity')
    if (state.screen === 'profile') { profileActivity.tick(true); profiles.refresh() }
    if (state.sheet?.detail?.kind === 'agent') settingsProfiles.refresh()
    if (state.sheet?.detail?.kind === 'engineer' || state.sheet?.detail?.kind === 'agent' && settingsProfiles.st.tab === 'Activity') engineerActivity.tick(true)
    schedules.tick(state.screen === 'profile' && profiles.st.manageSchedules || state.sheet?.detail?.kind === 'agent' && settingsProfiles.st.manageSchedules)
  }
  schedule()
}, 5000)

// A sign-in in progress, asked after: there is no stream for a login flow.
// Activity also refreshes on the heartbeat because its interface is JSON.
const loginHeartbeat = setInterval(() => page.providerSignIn.poll(), 1000)

try { setOwner(await api('GET', '/owner')) } catch { /* Unknown owner stays unknown; focus retries the runtime. */ }
window.addEventListener('focus', () => api('GET', '/owner').then(value => { setOwner(value); schedule() }).catch(() => {}))
loadHealth()
loadAgentAvailability()
running.start()
const landedThread = threadFromPath(page.herePath())
if (new URL(globalThis.location?.href ?? 'http://placeholder').searchParams.get('threads') === 'fixture') threads.preview()
else {
  const listing = threads.load()
  // Use the runtime's starter marker, never a browser-local setup flag. A
  // finished hello conversation and installations without a starter stay put.
  await Promise.all([listing, loadProviders()])
  const hello = threads.st.threads.find(row => row.hello === true)
  if (hello) {
    try {
      const record = await api('GET', `/threads/${encodeURIComponent(hello.id)}`)
      if (untouchedHello(record)) {
        onboarding.hello = hello.id
        onboarding.needed = !state.providers.some(p => ['claude-code', 'codex', 'antigravity'].includes(p.id) && p.connected)
        if (!onboarding.needed) await threads.open(hello.id)
      }
    } catch { /* An unavailable starter leaves ordinary Chats accessible. */ }
  }
  if (!onboarding.hello && landedThread) {
    threads.open(landedThread, { push: false, listing })
    threads.setView(new URL(globalThis.location?.href ?? 'http://placeholder').searchParams.get('view'))
  }
  else if (!onboarding.hello && itemFromURL(globalThis.location?.href ?? page.herePath())?.kind === 'app') { state.navigator = 'apps'; panels.openMain({ ...itemFromURL(location.href), push: false }) }
  else if (!onboarding.hello && page.herePath() === '/apps') goApps({ push: false })
  else if (!onboarding.hello && new URL(globalThis.location?.href ?? 'http://placeholder').searchParams.has('screen')) routePanels()
  else if (!onboarding.hello && page.herePath() === '/panel') panels.openMain({ ...itemFromURL(location.href), push: false })
  else if (!onboarding.hello) { const session = sessionFromAddress(globalThis.location?.href ?? page.herePath() + (globalThis.location?.search ?? '')) ?? (!parseFilesPath(page.herePath()) ? lastClassicSession : null); if (session) openImportedSession(session) }
}
onboarding.checking = false
activity.load()
loadLimits()
loadAgents()
page.loadGithub()
// Back from GitHub, at one of the two paths it was told to send the browser to.
page.githubReturn()

// Ordinary launches start on Chats. Explicit thread/Files addresses remain
// choices, and a pre-retirement stored session is resolved once into its
// imported thread above before the obsolete storage key is forgotten.
const landedOn = parseFilesPath(page.herePath())
if (landedOn) { state.navigator = 'files'; page.goFiles(landedOn.root, landedOn.path, { push: false, list: listFromURL(globalThis.location?.search ?? '') }) }
else render()
// Initial main routing may finish after the saved side was mounted during
// startup. With no interaction yet, the restored side owns attention.
if (panels.st.side) panels.focus('side')
appModalHistory.restore()

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {})
}

window.addEventListener('loam:signin', () => {
  clearInterval(pageHeartbeat); clearInterval(loginHeartbeat)
  running.stop(); threads.pause(); sidePanel.threads.pause(); registry.emit('page.leaving', {}); pluginLoader.dispose()
  schedule()
})

// App size uses the host preference rather than the browser's own zoom.
document.addEventListener('keydown', (event) => theme.key(event, theme.kind === 'desktop'))
window.addEventListener('focus', () => { theme.refresh(); pluginLoader.refresh(); dailyNotes.refresh() })
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') theme.save()
  else theme.refresh()
})

window.addEventListener('pagehide', () => theme.save())

// A notification may still carry a session ID from before history was imported.
navigator.serviceWorker?.addEventListener?.('message', (event) => {
  const id = event.data?.session_id
  if (typeof id === 'string') openImportedSession(id)
})

// Core boot and healthy plugins progress while another package is loading.
await pluginsReady
