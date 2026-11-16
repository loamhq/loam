import { createFiles, renamedResource, uploadAccept, FILES_LOCATION_HIDDEN, filesHref, vaultUrlRef, missingSentence, resolveOf, isFolder, externalHref, parseFilesPath, downloadHref, nameOf as fileNameOf, formatText, drawnTypeOf, bytesAre, folderView, fileView, ROOT_LOOK } from "../files.js"
import { h, kept, icon } from "../ui.js"
import { setFileLinks, setVaultUrls } from "../markdown.js"
import { createPicker } from "../filepicker.js"
import { sessionFromAddress } from "../imported-threads.js"
import { threadFromPath } from "../thread-opening.js"
import { authenticatedFetch as fetch } from "../auth.js"
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

function pushAddress(data, title, href) {
  globalThis.history?.pushState?.(data, title, href)
}

const files = createFiles({
  registry: host.registry,
  api: host.api,
  schedule: host.schedule,
  onRead: (row) => host.schedules.seeFile(row),
  onRename: (answer, offered) => followRename(answer, true, offered),
  onResolveTo: (root, path, isFile, row) => goFiles(root, path, { isFile, row }),
  // Through `put()`, the one place that can say how far an upload has got.
  sendForm: (href, form, onProgress) => host.put(href, form, onProgress),
})
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
  const at = parseFilesPath(herePath())
  if (navigation && at) {
    const next = renamedResource(at, answer)
    if (next !== at) globalThis.history?.replaceState?.({ files: true }, '', filesHref(next.root, next.path) + (globalThis.location?.hash ?? ''))
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
    files.upload(chosen)
  },
})

function chooseUpload() {
  filesUploadChooser.accept = uploadAccept(files.st.upload)
  filesUploadChooser.value = ''
  filesUploadChooser.click()
}



// The editor's field: one node, kept across redraws, so the caret stays where
// the person put it. Its value is set only when a different file is opened.
// It saves itself after a pause in typing (`files.edit()`), when the caret
// leaves it, and at once on Cmd/Ctrl-S.
const fileField = h('textarea', {
  class: 'fs-field',
  spellcheck: 'true',
  oninput: () => files.edit(fileField.value),
  onblur: () => {
    if (files.dirty() && !host.fieldActivity('file')) files.save()
  },
  onkeydown: (e) => {
    if ((e.metaKey || e.ctrlKey) && !e.altKey && (e.key === 's' || e.key === 'S')) {
      e.preventDefault?.()
      files.save()
    }
  },
})

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
function goFiles(root, path = '', { isFile = null, row = null, push = true, fragment = null, routed = false, mode = null } = {}) {
  // Expose a panel's save guard before routing can pause this navigation.
  if (host.state.wide && ['settings', 'activity'].includes(host.state.sheet?.kind)) host.closeSheet()
  const beside = host.panels?.st.side
  const heldFile = beside?.kind === 'file' && beside.root === root && beside.path === path
  if (!routed && (isFile === true || (isFile === null && heldFile)) && host.panels) return host.panels[host.panelId === 'side' ? 'openSide' : 'openMain']({ kind: 'file', root, path, row, fragment, push })
  host.state.sidebarSection = null
  const hidden = root === 'system'
  if (hidden) { root = null; path = ''; isFile = false; row = null; fragment = null }
  const leavingFile = files.st.file && !(isFile && files.st.file.root === root && files.st.file.path === path)
  if (leavingFile && !files.guard(null)) {
    // The page's own sheet asks; going on from it comes back here, clean.
    files.st.sheet.proceed = proceedThen(() => goFiles(hidden ? 'system' : root, path, { isFile, row, fragment }))
    return false
  }
  // Browsing a folder starts a Files navigation path of its own. Merely having
  // a chat to return to must not replace that folder's ordinary back button.
  files.st.chatFile = Boolean(isFile && (host.activeThread() || (host.state.screen === 'files' && files.st.chatFile)))
  if (host.state.screen !== 'files') {
    host.importRequest++
    files.st.returnTo = host.activeThread() ? { id: host.activeThread().thread.id } : files.st.returnTo
    host.threads.close({ navigate: false })
    host.state.profileAgent = null
    host.state.screen = 'files'
  }
  if (leavingFile && !isFile) files.closeFile()
  files.show(root, path, { isFile, row, fragment, mode })
  if (hidden) files.st.notice = FILES_LOCATION_HIDDEN
  const href = filesHref(root, path)
  if (navigation && hidden && herePath() !== href) globalThis.history?.replaceState?.({ files: true }, '', href)
  else if (navigation && push && herePath() !== href) pushAddress({ files: true }, '', href)
  host.schedule()
  return true
}

goFiles.redraw = host.schedule
goFiles.side = () => host.panels



// Out of Files, to the chats — or straight back to the chat it was opened
// from, which is what "Back to chat" says.
function leaveFiles({ toChat = false, push = true } = {}) {
  if (!files.guard(null)) {
    files.st.sheet.proceed = proceedThen(() => leaveFiles({ toChat }))
    return false
  }
  const back = files.st.returnTo
  files.closeFile()
  host.state.screen = 'home'
  if (push && herePath() !== '/') pushAddress(null, '', '/')
  if (toChat && back?.id) host.threads.open(back.id)
  else host.goHome()
  return true
}



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
async function openFileRef(ref, from = null, event = null) {
  if (navigation && event?.currentTarget?.closest?.('[data-panel]')?.getAttribute('data-panel') === 'side') return host.panels.sideHost.openFileRef(ref, from, event)
  const toSide = host.panels?.available() && (event?.altKey || event?.openToSide || host.panelId === 'side')
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
    if (toSide && !isFolder(row)) return host.panels.openSide({ kind: 'file', root: row.root, path: row.path, row, fragment })
    return goFiles(row.root, row.path, { isFile: !isFolder(row), row, fragment })
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



// Back and Forward, through Files addresses and out of them.
if (navigation) globalThis.addEventListener?.('popstate', () => {
  const session = sessionFromAddress(globalThis.location?.href ?? herePath() + (globalThis.location?.search ?? ''))
  if (session) { host.openImportedSession(session); return }
  const thread = threadFromPath(herePath())
  if (thread) {
    if (host.state.screen === 'files') {
      if (!files.guard(null)) {
        const f = files.st.file
        if (f) pushAddress({ files: true }, '', filesHref(f.root, f.path))
        files.st.sheet.proceed = proceedThen(() => host.threads.open(thread))
        host.schedule(); return
      }
      files.closeFile()
    }
    host.threads.open(thread, { push: false }); return
  }
  if (host.threads.st.open) { host.threads.close({ navigate: false }); host.schedule() }
  const at = parseFilesPath(herePath())
  if (at) {
    if (!goFiles(at.root, at.path, { push: false })) {
      // The sheet is asking: until it is answered the address stays on the file
      // the person is still on, and leaving from it goes where Back was going.
      const f = files.st.file
      if (f) pushAddress({ files: true }, '', filesHref(f.root, f.path))
      files.st.sheet.proceed = proceedThen(() => goFiles(at.root, at.path))
    }
    return
  }
  if (host.state.screen === 'files' && !leaveFiles({ toChat: true, push: false })) {
    const f = files.st.file
    if (f) pushAddress({ files: true }, '', filesHref(f.root, f.path))
  }
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

function filesScreen() {
  const f = files.st.file
  if (f) {
    return h(
      'section',
      { class: 'screen files', key: 'screen:files:file' },
      fileView(files, {
        close: () => goFiles(f.root, files.st.path),
        closeLabel: files.st.path === '' ? (ROOT_LOOK[f.root]?.name ?? f.root) : fileNameOf(files.st.path),
        wide: false,
        field: fileFieldFor,
        home: filesHome(),
        go: goFiles,
        onTool: applyTool,
        embed: embedFor,
        ...fileDictation(),
        returnBack: files.st.chatFile ? returnToChatButton({ compact: true }) : null,
      }),
    )
  }
  return h(
    'section',
    { class: 'screen files', key: 'screen:files' },
    host.registry.draw('app.overlay', { slot: 'player' }),
    folderView(files, { go: goFiles, wide: false, returnBack: returnToChatButton(), home: filesHome() }),
    host.nav(),
  )
}



// The Files home's own things: the person's shortcuts and the two folders its actions
// write into, from `store.files` (see there for why they are this device's).
function filesHome() {
  const held = host.store.files
  const shortcuts = fileShortcuts(held, files.st.roots.shortcuts)
  const has = (root, path) => shortcuts.some((s) => s.root === root && s.path === path)
  return {
    shortcuts,
    inbox: held.inbox,
    daily: host.dailyNotes?.st.on ? host.dailyNotes.st.folder : null,
    isShortcut: has,
    toggleShortcut: (root, path, kind = 'folder') => {
      host.store.files = toggleFileShortcut(host.store.files, files.st.roots.shortcuts, root, path, kind)
      host.schedule()
    },
    onInbox: () => files.newInbox(held.inbox),
    onDaily: async () => { await host.dailyNotes.refresh(); if (host.dailyNotes.st.on && host.dailyNotes.st.folder !== null) files.openDaily(host.dailyNotes.st.folder) },
  }
}

function returnToChatButton({ compact = false } = {}) {
  const back = files.st.returnTo
  if (!back?.id) return null
  return h('button', { class: compact ? 'backbtn fs-return fs-chatback' : 'pillbtn fs-return', 'aria-label': 'Back to chat', onclick: () => leaveFiles({ toChat: true }), text: compact ? null : 'Back to chat' }, compact ? icon.chevronLeft(20) : null, compact ? h('span', { text: 'Chat' }) : null)
}
return Object.defineProperties({}, {
    herePath: { enumerable: true, get: () => herePath },
    pushAddress: { enumerable: true, get: () => pushAddress },
    files: { enumerable: true, get: () => files },
    filesUploadChooser: { enumerable: true, get: () => filesUploadChooser },
    chooseUpload: { enumerable: true, get: () => chooseUpload },
    fileField: { enumerable: true, get: () => fileField },
    fileNameField: { enumerable: true, get: () => fileNameField },
    fileFieldFor: { enumerable: true, get: () => fileFieldFor },
    goFiles: { enumerable: true, get: () => goFiles },
    leaveFiles: { enumerable: true, get: () => leaveFiles },
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
    returnToChatButton: { enumerable: true, get: () => returnToChatButton }
})
}
