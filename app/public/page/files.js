import { fileEditor } from '../file-editor.js'
import { parentOf, createFiles, renamedResource, uploadAccept, filesHref, vaultUrlRef, missingSentence, resolveOf, isFolder, externalHref, parseFilesPath, downloadHref, nameOf as fileNameOf, formatText, drawnTypeOf, bytesAre, folderView, fileView, ROOT_LOOK } from "../files.js"
import { h, kept, icon } from "../ui.js"
import { setFileLinks, setVaultUrls } from "../markdown.js"
import { createPicker } from "../filepicker.js"
import { sessionFromAddress } from "../imported-threads.js"
import { authenticatedFetch as fetch } from "../auth.js"
import { itemDescriptor } from '../panel-items.js'
import { fileShortcuts, toggleFileShortcut } from '../store.js'

// Mounted once. Cross-module state and operations are explicit host services.
export function mount(host, { navigation = true } = {}) {


// ----------------------------------------------------------------- files
//
// Files is a screen of its own with an address of its own: `/files/<root>/…`.
// Going there writes the address, Back comes out of it, a reload lands on it.
// The module in files.js holds what is listed and open; this is where it meets
// the rest of the page — the tab, the chat it was opened from, the composer.

function herePath() {
  return globalThis.location?.pathname ?? '/'
}

const fileOptions = {
  registry: host.registry,
  api: host.api,
  schedule: host.schedule,
  onCloseFile: rememberFilePlace,
  onRead: (row) => host.schedules.seeFile(row),
  onRename: (answer, offered) => followRename(answer, true, offered),
  deleteResource: resource => host.deleteWorkspaceFile(resource),
  hasFileDraft: resource => host.hasFileDraft(resource),
  agentLabel: id => host.state.agents.byId.get(id)?.name,
  onResolveTo: (root, path, isFile, row) => goFiles(root, path, { isFile, row }),
  // Through `put()`, the one place that can say how far an upload has got.
  sendForm: (href, form, onProgress) => host.put(href, form, onProgress),
  panelPref: { get: () => host.store.filePanel, set: (value) => { host.store.filePanel = value } },
}
const files = createFiles(fileOptions)
// An explicit directory link (for example, a profile's workspace) is a panel
// visit. Its folder controller never borrows the navigator's listing or sheet.
let directory = null
function directoryGo(root, path, options = {}) {
  if (options.isFile) return goFiles(root, path, options)
  return host.panels[host.panelId === 'side' ? 'openSide' : 'openMain']({ kind: 'directory', id: filesHref(root, path) })
}
directoryGo.redraw = host.schedule
directoryGo.side = () => host.panels
function directoryView(href) {
  if (!directory) {
    directory = createFiles({ ...fileOptions, onRename: (answer, offered) => { files.applyRename(answer); followRename(answer, true, offered) }, onResolveTo: (root, path, isFile, row) => directoryGo(root, path, { isFile, row }) })
    directory.side = () => host.panels

  }
  directory.st.upload = files.st.upload
  const at = parseFilesPath(href)
  if (at && (!directory.st.turn || directory.st.root !== at.root || directory.st.path !== at.path))
    directory.show(at.root, at.path, { isFile: false })
  return folderView(directory, { go: directoryGo, wide: false, home: filesHome(directory), historyBack: host.panelId === 'side' ? null : host.backButton('main'), showParent: false })
}
function sheetFiles() { return directory?.st.sheet && host.state.mainItem?.kind === 'directory' ? directory : files }
files.side = () => host.panels

// Other tabs on this origin follow the same host answer; there is no second
// path registry or recent-file list. Shortcuts remain device preferences.
const renameChannel = globalThis.window?.BroadcastChannel ? new BroadcastChannel('files-renamed') : null
renameChannel?.addEventListener('message', event => {
  const answer = event.data
  if (!answer?.from || typeof answer.path !== 'string' || typeof answer.root !== 'string') return
  const offered = files.st.roots.shortcuts
  files.applyRename(answer)
  followRename(answer, false, offered)
})

function followRename(answer, broadcast = true, offered = []) {
  const held = host.store.files
  const folder = path => renamedResource({ root: 'vault', path }, answer).path
  // A host suggestion may still name the old address on the next roots read.
  // Keep its new address as a personal shortcut and dismiss that old suggestion.
  const followed = fileShortcuts(held, offered).filter(s => s.offered && renamedResource(s, answer) !== s)
  host.store.files = { ...held,
    shortcuts: [...held.shortcuts.map(s => renamedResource(s, answer)), ...followed.map(s => renamedResource(s, answer))],
    hiddenShortcuts: [...held.hiddenShortcuts.flatMap(s => { const next = renamedResource(s, answer); return next === s ? [s] : [s, next] }), ...followed.map(({ root, path }) => ({ root, path }))],
    inbox: folder(held.inbox),
  }
  directory?.applyRename(answer)
  const renamedHref = href => {
    const url = new URL(href, 'http://placeholder')
    const at = parseFilesPath(url.pathname)
    const next = renamedResource(at, answer)
    if (next === at) return href
    return filesHref(next.root, next.path) + url.search + url.hash
  }
  const renamedItem = item => item?.kind === 'directory' ? { ...item, id: renamedHref(item.id) }
    : item?.kind === 'file' ? itemDescriptor(renamedResource(item, answer)) : item
  host.state.mainItem = renamedItem(host.state.mainItem)
  if (navigation) {
    const history = host.workspaceHistory
    for (const step of [...history.state.visits, ...history.state.forward]) {
      step.href = renamedHref(step.href)
      if (step.data.item) step.data.item = renamedItem(step.data.item)
    }
    history.save()
  }
  if (broadcast) renameChannel?.postMessage(answer)
  host.schedule()
}



// The chooser Files' Upload opens: in the add sheet, never drawn. Its `accept`
// is the host's list of extensions, once the host has said it.
const filesUploadChooser = h('input', {
  id: host.panelId === 'side' ? 'side-pick-upload' : 'pick-upload',
  class: 'picker',
  type: 'file',
  multiple: true,
  onchange: () => {
    const chosen = [...(filesUploadChooser.files ?? [])]
    filesUploadChooser.value = ''
    sheetFiles().upload(chosen)
  },
})

function chooseUpload() {
  filesUploadChooser.accept = uploadAccept(sheetFiles().st.upload)
  filesUploadChooser.value = ''
  filesUploadChooser.click()
}



// The editor's field: one node, kept across redraws, so the caret stays where
// the person put it. Its value is set only when a different file is opened.
// It saves itself after a pause in typing (`files.edit()`), when the caret
// leaves it, and at once on Cmd/Ctrl-S.
const fileField = fileEditor({
  edit: text => files.edit(text),
  save: () => files.save(),
  blur: () => { if (files.dirty() && !host.fieldActivity('file')) files.save() },
})

// A visit remembers only reading coordinates. Text and revisions still come
// from Files, and an editor that owns the field supplies its selection API.
const places = new Map()
let mountedFile = null, mountedVisit = null
function fileSurface(file) {
  const dom = host.panelDOM
  const field = file.owner?.field ?? (fileField.isConnected ? fileField : null)
  let scroll = dom.querySelector('.fs-preview') ?? (field === fileField ? field : null)
  if (!scroll) {
    let node = dom.querySelector('.fs-custom-content [contenteditable]')
    while (node && !node.hasAttribute('data-panel')) {
      if (node.clientHeight && /^(auto|scroll)$/.test(globalThis.getComputedStyle?.(node)?.overflowY) && node.scrollHeight > node.clientHeight) {
        scroll = node; break
      }
      node = node.parentElement
    }
  }
  return { field, scroll }
}
function rememberFilePlace(file) {
  fileField.__for = null
  if (file !== mountedFile || file.loading) return
  const { field, scroll } = fileSurface(file)
  places.set(mountedVisit, { root: file.root, path: file.path, mode: file.mode, top: scroll?.scrollTop ?? 0,
    start: field?.selectionStart, end: field?.selectionEnd })
  mountedFile = null
}
function restoreFilePlace() {
  const file = files.st.file
  if (!file || file.loading) return
  files.resumeOwner()
  if (mountedFile === file) return
  const visit = host.panelId === 'side' ? host.workspaceHistory.current('side')?.id : globalThis.history?.state?.panelVisit ?? 'initial'
  const saved = places.get(visit)
  if (saved?.root === file.root && saved.path === file.path && !file.fragment) {
    if (saved.mode !== file.mode) { files.setMode(saved.mode); return }
    const { field, scroll } = fileSurface(file)
    if ((saved.top && !scroll) || (saved.start !== undefined && !field)) return
    if (saved.start !== undefined) field?.setSelectionRange?.(saved.start, saved.end)
    if (scroll) scroll.scrollTop = saved.top
  }
  places.delete(visit)
  mountedFile = file; mountedVisit = visit
}

fileField.__for = null

const fileNameField = h('input', { class: 'field-input', type: 'text', placeholder: 'Name', autocomplete: 'off' })

function fileFieldFor(...args) { return host.registry.component('core.files.editor', { args, defaultView: () => builtinFileFieldFor(...args) }) }

function builtinFileFieldFor(f, { readOnly, mono }) {
  const key = `${f.root}:${f.path}:${f.revision}`
  if (fileField.__for !== key && !files.dirty()) {
    // A save that landed is the same file at its next revision: if the host
    // stored something other than what was typed, the field takes it, and the
    // caret stays where it was.
    const same = fileField.__for?.startsWith(`${f.root}:${f.path}:`)
    const next = f.draft ?? f.text
    if (fileField.value !== next) {
      const at = [fileField.selectionStart, fileField.selectionEnd]
      fileField.value = next
      if (same) fileField.setSelectionRange?.(...at)
    }
    fileField.__for = key
  }
  if (readOnly || host.fieldActivity('file')) fileField.setAttribute('readonly', '')
  else fileField.removeAttribute('readonly')
  fileField.className = `fs-field${mono ? ' mono' : ''}`
  return kept(fileField)
}



// Go to a folder or a file. `push: false` is for an address the browser has
// already moved to — a load, or Back.
let fileDestination = 0
function goFiles(root, path = '', { isFile = null, row = null, push = true, fragment = null, routed = false, mode = null, list = null, returnThread = null, panelLink = false, returnToSettings = false } = {}) {
  const destination = ++fileDestination
  if (isFile === null && path && root?.startsWith('@')) isFile = true
  if (isFile === null && path && root && root !== 'system') {
    const request = host.importRequest
    const open = (isFile, row) => {
      if (destination === fileDestination && request === host.importRequest)
        goFiles(root, path, { isFile, row, push, fragment, mode, list, returnThread, panelLink, returnToSettings })
    }
    files.stat(root, path).then(row => open(!isFolder(row), row), () => open(false, null))
    return true
  }
  if (!isFile || root === 'system') {
    if (root !== 'system' && (host.state.sheet || panelLink) && push) return directoryGo(root, path)
    if (host.state.sheet) host.closeSheet()
    host.browseFiles(root === 'system' ? null : root, root === 'system' ? '' : path, { push, hidden: root === 'system' })
    return true
  }
  const item = {
    kind: 'file', root, path, row, fragment, mode, push, list, returnThread,
    returnToList: !routed && !host.isWide() && host.workspaceHistory.state.front === 'list',
  }
  const sheet = host.state.sheet
  if (returnToSettings && sheet?.kind === 'settings') {
    item.returnModal = { kind: 'settings', page: sheet.page, provider: sheet.provider }
  }
  if (!routed) return host.panels[host.panelId === 'side' ? 'openSide' : 'openMain'](item)
  files.st.chatFile = Boolean(returnThread || host.activeThread() || (host.state.screen === 'files' && files.st.chatFile))
  if (host.state.screen !== 'files') {
    host.importRequest++
    host.threads.close({ navigate: false })
    host.state.profileAgent = null
  }
  host.state.mainItem = { kind: 'file', root, path }
  host.state.screen = 'files'
  const current = files.st.file
  if (!current || current.root !== root || current.path !== path) files.openFile(root, path, row, { fragment, mode })
  else {
    if (fragment) current.fragment = fragment
    if (mode && current.mode !== mode) files.setMode(mode)
  }
  host.schedule()
  return true
}

goFiles.redraw = host.schedule
goFiles.side = () => host.panels



// What "Leave without saving" does: the edit is dropped, and the way out the
// person took goes on.
function proceedThen(go) {
  return () => {
    files.st.sheet = null
    if (files.st.file) files.st.file.draft = null
    go()
    host.schedule()
  }
}



// A reference from a chat or a preview, resolved by the host and answered by
// its `status`: one file or folder is opened (at its heading, if the link
// named one), several are a choice the person makes, a link to the web is
// followed only if it is `http`, `https` or `mailto`, and one that leads
// nowhere says what the host looked for. A published vault URL is read as the
// vault path it names first.
async function openFileRef(ref, from = null, event = null, { chat = false } = {}) {
  if (navigation && event?.currentTarget?.closest?.('[data-panel]')?.getAttribute('data-panel') === 'side') return host.panels.sideHost.openFileRef(ref, from, event, { chat })
  const fullView = chat && (event?.metaKey || event?.ctrlKey)
  const toSide = !fullView && host.panels?.available() && (chat || event?.altKey || event?.openToSide || host.panelId === 'side')
  const published = vaultUrlRef(ref, vaultUrls)
  const asked = published?.ref ?? ref
  let answer
  try {
    const qs = files.q({ ref: asked, from_root: published ? null : from?.root, from_path: published ? null : from?.path })
    answer = resolveOf(await host.api('GET', `/files/resolve?${qs}`))
  } catch (err) {
    return refNotice(err.status === 404 ? `${ref} is not a file you can open in Files.` : err.message)
  }
  const fragment = answer.fragment ?? published?.fragment ?? null
  const go = (row) => {
    if (files.st.sheet?.kind === 'choose') files.st.sheet = null
    const isFile = !isFolder(row)
    if (isFile) {
      const item = { kind: 'file', root: row.root, path: row.path, row, fragment }
      if (toSide) return host.panels.openSide(item)
      if (fullView && host.panelId === 'side') return host.panels.openMain(item)
    }
    return goFiles(row.root, row.path, { isFile, row, fragment, panelLink: true })
  }
  if (answer.status === 'found') return go(answer.resource)
  if (answer.status === 'ambiguous') {
    if (answer.candidates.length === 0) return refNotice(`${ref} matches files the host did not list.`)
    files.st.sheet = { kind: 'choose', ref, candidates: answer.candidates, total: answer.total, truncated: answer.truncated, pick: go }
    host.schedule()
    return true
  }
  if (answer.status === 'external') {
    const href = externalHref(answer.url)
    if (!href) return refNotice(`${ref} is not a link this page follows.`)
    globalThis.open?.(href, '_blank', 'noopener,noreferrer')
    return true
  }
  return refNotice(missingSentence(ref, answer))
}

function refNotice(sentence) {
  if (host.activeThread()) host.activeThread().error = sentence
  files.st.notice = sentence
  host.schedule()
  return false
}



// The published vault URLs, from the server's configuration.
let vaultUrls = []

host.api('GET', '/files/config')
  .then((body) => {
    vaultUrls = Array.isArray(body?.vault_urls) ? body.vault_urls : []
    setVaultUrls(vaultUrls)
    host.schedule()
  })
  .catch(() => {})

if (navigation) setFileLinks((ref, from, event) => openFileRef(ref, from, event))



// The app owns browser routing. Standalone controllers do not write history.
if (navigation) globalThis.addEventListener?.('popstate', () => {
  const session = sessionFromAddress(globalThis.location?.href ?? herePath() + (globalThis.location?.search ?? ''))
  if (session) { host.openImportedSession(session); return }
  if (globalThis.location?.href) host.followPanelAddress(globalThis.location.href)
})

// A tab closed or reloaded over an unsaved edit asks first, as every other way
// out of one does.
if (navigation) globalThis.addEventListener?.('beforeunload', (event) => {
  const side = host.panels?.sideHost
  if (!files.dirty() && !host.threads.dirty() && !side?.files.dirty() && !side?.threads.dirty()) return
  if (files.dirty()) files.save()
  if (side?.files.dirty()) side.files.save()
  event.preventDefault()
  event.returnValue = ''
})



// "Pick from Files": a picker over the chat — a draft included — that browses
// the host's roots and hands back references, not bytes. The chat stays open
// under it, so a draft keeps its words and its held files; what is picked in a
// draft is held under the draft and goes with the first message once the
// thread exists.
const picker = createPicker({ api: host.api, schedule: host.schedule })

function pickFromFiles() {
  host.closeMenus()
  const thread = host.activeThread()
  if (thread) {
    picker.open({ agent: thread.thread.default ?? undefined, onPick: async (rows) => {
      for (const row of rows) {
        try {
          const res = await fetch(downloadHref(row.root, row.path))
          if (!res.ok) throw new Error('This file could not be attached. Try again in a moment.')
          const bytes = await res.blob()
          if (host.activeThread() !== thread) return
          const name = row.name ?? fileNameOf(row.path)
          host.intake([{ file: new File([bytes], name, { type: bytes.type }), name }])
        } catch (e) { thread.error = e.message; host.schedule() }
      }
    } })
    return
  }

}




// The editor's toolbar: one mark applied to the field's selection, the field
// given the result, and the selection put back around what was marked.
function applyTool(kind) {
  const start = fileField.selectionStart ?? fileField.value.length
  const end = fileField.selectionEnd ?? start
  const out = formatText(fileField.value, start, end, kind)
  fileField.value = out.value
  fileField.focus?.()
  fileField.setSelectionRange?.(out.start, out.end)
  files.edit(out.value)
}



// An embed in a preview, `![[picture.png]]`: resolved by the host from the
// note it is in, and a picture the host answers is fetched, checked to be the
// picture its name says, and drawn from an object URL — the same way an
// opened image is. Anything else, or anything that does not resolve, is a link
// to it. Remembered per open note, and given back when the note closes.
const embeds = { file: null, map: new Map() }

function embedFor(target) {
  const f = files.st.file
  if (!f) return null
  if (embeds.file !== f) {
    for (const e of embeds.map.values()) if (e.url) URL.revokeObjectURL?.(e.url)
    embeds.map.clear()
    embeds.file = f
  }
  let e = embeds.map.get(target)
  if (!e) {
    e = { state: 'resolving', url: null }
    embeds.map.set(target, e)
    loadEmbed(f, target, e)
  }
  if (e.state === 'image') return h('img', { class: 'md-embed', src: e.url, alt: target, key: `embed:${target}` })
  if (e.state === 'resolving' && /\.(png|jpe?g|gif|webp|avif)$/i.test(target)) return h('span', { class: 'md-embed pending', text: target })
  return null
}

async function loadEmbed(f, target, e) {
  const done = (state, url = null) => {
    e.state = state
    e.url = url
    if (files.st.file === f) host.schedule()
  }
  try {
    const answer = resolveOf(await host.api('GET', `/files/resolve?${files.q({ ref: `[[${target}]]`, from_root: f.root, from_path: f.path })}`))
    const row = answer.status === 'found' ? answer.resource : null
    const want = row ? drawnTypeOf(row) : null
    if (!want || want.type === 'application/pdf') return done('link')
    const res = await fetch(downloadHref(row.root, row.path), { credentials: 'same-origin' })
    if (!res.ok) return done('link')
    const buf = await res.arrayBuffer()
    if (!bytesAre(want, buf)) return done('link')
    if (embeds.file !== f) return
    done('image', URL.createObjectURL(new Blob([buf], { type: want.type })))
  } catch {
    done('link')
  }
}



// What Files' editor carries of dictation: the composer's mic, its take in the
// editor, and the line a take that did not land leaves.
function fileDictation() {
  const take = host.registry.draw('composer.tool', { slot: 'take', target: 'file', host })
  return {
    mic: host.registry.draw('composer.tool', { slot: 'microphone', target: 'file', host }).at(-1) ?? null,
    take: take.length ? h('div', { class: 'fs-take' }, take) : null,
    notice: host.registry.draw('composer.tool', { slot: 'notice', target: 'file', host }).at(-1) ?? null,
    readAloud: host.registry.draw('files.viewer', { slot: 'action', file: files.st.file, files, host }).at(-1) ?? null,
    audioBar: host.registry.draw('app.overlay', { slot: 'player' }),
  }

}

function filesScreen({ list = false } = {}) {
  const f = !list && files.st.file
  if (f) {
    return h(
      'section',
      { class: 'screen files', key: 'screen:files:file' },
      fileView(files, {
        openFolder: files.st.chatFile ? () => goFiles(f.root, parentOf(f.path), { isFile: false }) : null,
        historyBack: host.backAction('main', globalThis.history?.state?.returnThread ? 'Back to thread' : undefined),
        closeLabel: files.pluginRoot(f.root)?.name ?? files.pathLabels(f.root, parentOf(f.path), f.row).at(-1) ?? ROOT_LOOK[f.root]?.name ?? f.root,
        wide: false,
        htmlActionsInMenu: files.st.chatFile,
        field: fileFieldFor,
        home: filesHome(),
        go: goFiles,
        onTool: applyTool,
        embed: embedFor,
        ...fileDictation(),

      }),
    )
  }
  return h(
    'section',
    { class: 'screen files', key: 'screen:files' },
    host.registry.draw('app.overlay', { slot: 'player' }),
    folderView(files, { go: goFiles, wide: false, historyBack: host.backButton('list'), showParent: false, home: filesHome() }),
    host.nav(),
  )
}



// The Files home's own things: Pinned items (device-held until host preferences
// exist), quick actions from the host `quick-actions` plugin, and agent faces
// for Recent rows.
function filesHome(browser = files) {
  const held = host.store.files
  const shortcuts = fileShortcuts(held, browser.st.roots.shortcuts)
  const has = (root, path) => shortcuts.some((s) => s.root === root && s.path === path)
  const qa = host.quickActions?.st
  const showNewNote = Boolean(qa?.on && (qa.actions === 'both' || qa.actions === 'new-note'))
  const showDailyNote = Boolean(qa?.on && (qa.actions === 'both' || qa.actions === 'daily-note'))
  return {
    shortcuts,
    showNewNote,
    showDailyNote,
    isShortcut: has,
    toggleShortcut: (root, path, kind = 'folder') => {
      host.store.files = toggleFileShortcut(host.store.files, browser.st.roots.shortcuts, root, path, kind)
      host.schedule()
    },
    onInbox: async () => {
      await host.quickActions?.refresh()
      const cur = host.quickActions?.st
      if (cur?.on && (cur.actions === 'both' || cur.actions === 'new-note')) browser.newInbox(cur.inboxFolder)
    },
    onDaily: async () => {
      await host.quickActions?.refresh()
      const cur = host.quickActions?.st
      if (cur?.on && (cur.actions === 'both' || cur.actions === 'daily-note')) browser.openDaily(cur.dailyFolder)
    },
    agentLook: (id) => host.agentLook(id, host.state.agents),
  }
}

return Object.defineProperties({}, {
    directoryView: { enumerable: true, get: () => directoryView },
    sheetFiles: { enumerable: true, get: () => sheetFiles },
    filesControllers: { enumerable: true, get: () => () => directory ? [files, directory] : [files] },
    restoreFilePlace: { enumerable: true, get: () => restoreFilePlace },
    herePath: { enumerable: true, get: () => herePath },
    files: { enumerable: true, get: () => files },
    filesUploadChooser: { enumerable: true, get: () => filesUploadChooser },
    chooseUpload: { enumerable: true, get: () => chooseUpload },
    // A plugin's editor that holds the open file supplies its own field
    // (`files.own`), so dictation lands in it.
    fileField: { enumerable: true, get: () => files.st.file?.owner?.field ?? fileField },
    fileNameField: { enumerable: true, get: () => fileNameField },
    fileFieldFor: { enumerable: true, get: () => fileFieldFor },
    goFiles: { enumerable: true, get: () => goFiles },
    proceedThen: { enumerable: true, get: () => proceedThen },
    openFileRef: { enumerable: true, get: () => openFileRef },
    refNotice: { enumerable: true, get: () => refNotice },
    vaultUrls: { enumerable: true, get: () => vaultUrls, set: value => { vaultUrls = value } },
    picker: { enumerable: true, get: () => picker },
    pickFromFiles: { enumerable: true, get: () => pickFromFiles },
    applyTool: { enumerable: true, get: () => applyTool },
    embeds: { enumerable: true, get: () => embeds },
    embedFor: { enumerable: true, get: () => embedFor },
    loadEmbed: { enumerable: true, get: () => loadEmbed },
    fileDictation: { enumerable: true, get: () => fileDictation },
    filesScreen: { enumerable: true, get: () => filesScreen },
    filesHome: { enumerable: true, get: () => filesHome },
})
}
