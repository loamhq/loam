import { installation } from './installation.js'
// What a chat is carrying: the files and images picked from the + sheet, on
// their way up and once they are up.
//
// No DOM and no network, for the same reason the other model files have none —
// every decision about what the composer says is asserted in `node --test`, and
// the page is left with the two things only a browser can do: open a chooser and
// put bytes on the wire.
//
// **A file this app has is a file the host wrote down.** The bytes go to the
// broker, which puts them where the agent can read them and answers with the
// path it used; that path is what a message carries. This app never decides
// where a file lands, never keeps a copy, and has nowhere to keep one.

export function createAttachments() {
  return { items: [] }
}

let counter = 0

// A file just chosen, before a byte of it has gone anywhere. `progress` is what
// the browser reports as it goes; `path` is what the host answers with; `error`
// is a sentence, either this page's about the cap or the host's about the
// refusal.
// `held` is the bytes themselves, kept in the page because there is nowhere to
// send them yet: a chat that has not been started has no session id, and the
// attachments route is a session's. It is the one case where this app holds a
// file at all, it lasts exactly as long as the draft does, and the moment the
// session exists the file goes up like any other and `held` is done with.
export function addFile(state, file, { error = null, name = null, held = null } = {}) {
  counter += 1
  const item = {
    id: `a${counter}`,
    held,
    // The name it goes up under. A chooser's file names itself; a screenshot on
    // the clipboard does not, and the name is made for it below.
    name: name ?? file?.name ?? 'file',
    size: Number.isFinite(file?.size) ? file.size : null,
    type: typeof file?.type === 'string' && file.type !== '' ? file.type : null,
    kind: isImage(file?.type) ? 'image' : 'file',
    progress: 0,
    path: null,
    error,
  }
  state.items.push(item)
  return item
}

// A draft's files, put back when the chat it was written in is opened again.
// Only files the host has already written down can come back — they are the
// ones with a path, and the path is all this browser ever held of them. A file
// still going up when the chat was left is not restored: its bytes were in the
// page and the page has moved on, so a chip claiming to be uploading would be a
// chip about nothing.
export function restoreAttachments(state, files) {
  state.items = []
  for (const file of attachmentList(files)) {
    counter += 1
    state.items.push({
      id: `a${counter}`,
      held: null,
      name: file.name,
      size: file.size,
      type: file.type,
      kind: isImage(file.type) ? 'image' : 'file',
      progress: 1,
      path: file.path,
      error: null,
    })
  }
  return state
}

export function removeAttachment(state, id) {
  state.items = state.items.filter((item) => item.id !== id)
  return state
}

export function clearAttachments(state) {
  state.items = []
  return state
}

export function markProgress(state, item, loaded, total) {
  if (!item || item.path || item.error) return state
  const of = Number.isFinite(total) && total > 0 ? total : item.size
  if (!Number.isFinite(of) || of <= 0) return state
  item.progress = Math.max(0, Math.min(1, loaded / of))
  return state
}

// The host's answer to the upload: `{"attachments": [{path, name, size, type}]}`,
// one entry per part sent, and this app sends one part per chooser pick.
//
// **The name that comes back is the name from now on.** The host sanitises it
// and makes it unique in the day's folder — `Board notes (final).md` is stored
// as `Board-notes--final-.md`, a second `shot.png` as `shot-1.png` — so a chip
// that went on saying what the person's own disk called it would be naming a
// file that does not exist.
export function markStored(state, item, answer) {
  const file = attachmentList(answer?.attachments)[0]
  if (!file) {
    item.error = 'The file went up but the host did not say where it put it.'
    return state
  }
  item.path = file.path
  item.name = file.name
  // The bytes are the host's now, so the page lets go of its copy — see
  // `addFile`'s `held`, which exists only for the moment before there is a
  // session to send them to.
  item.held = null
  if (Number.isFinite(file.size)) item.size = file.size
  if (file.type) {
    item.type = file.type
    item.kind = isImage(file.type) ? 'image' : 'file'
  }
  item.progress = 1
  return state
}

export function markFailed(state, item, message) {
  if (!item) return state
  item.error = message || 'The file did not go up.'
  return state
}

// What a message carries: the files the host wrote down, in the order they were
// picked. A file that failed carries nothing, because nothing of it was written
// down. The shape is the record's own — the messages route takes the `path` of
// each and the drawn line keeps the rest.
export function stored(state) {
  return state.items
    .filter((item) => item.path)
    .map((item) => ({ path: item.path, name: item.name, size: item.size, type: item.type ?? null }))
}

// Whether anything is still on the wire, which is what a send waits for. A file
// a draft is holding is **not** on the wire and never will be until the session
// exists, so it is not something to wait for: waiting on one would be a send
// button that never came back, since the thing that starts the upload is the
// very send being blocked.
export function uploading(state) {
  return state.items.some((item) => !item.path && !item.error && !item.held)
}

// The files a draft is carrying, in the order they were picked — what gets
// uploaded the moment there is a session to upload them against.
export function heldFiles(state) {
  return state.items.filter((item) => item.held && !item.path && !item.error)
}

// Whether this file can be sent at all, answered before a byte of it moves. The
// cap is the host's number, relayed through this app's `/api/limits` from the
// broker's own; a phone on a slow connection should be told in the composer and
// not after a minute of uploading.
export function tooBig(file, cap) {
  if (!Number.isFinite(cap) || cap <= 0) return null
  const size = Number.isFinite(file?.size) ? file.size : null
  if (size === null || size <= cap) return null
  return `That file is ${bytes(size)}, and a chat carries ${bytes(cap)} at most.`
}

// The `attachments` the host states — on an upload's answer and on
// `broker.user_message` — as the page draws them. One shape, objects, because
// that is the one shape the interface states; the *messages route* takes bare
// paths and that is a different direction on the wire, not a second reading of
// this one.
export function attachmentList(raw) {
  const out = []
  for (const entry of Array.isArray(raw) ? raw : []) {
    if (!entry || typeof entry !== 'object') continue
    const path = typeof entry.path === 'string' && entry.path !== '' ? entry.path : null
    if (!path) continue
    const named = typeof entry.name === 'string' && entry.name !== '' ? entry.name : fileName(path)
    out.push({
      path,
      name: named,
      size: Number.isFinite(entry.size) ? entry.size : null,
      type: typeof entry.type === 'string' && entry.type !== '' ? entry.type : null,
    })
  }
  return out
}

// Files addresses are relative to their root. Older attachment records carry
// an absolute host path; only the documented upload subtree identifies its
// vault-relative suffix. Keep the original record/path for sending and titles.
export function attachmentFilePath(value) {
  if (typeof value !== 'string' || !value || /[\\\u0000-\u001f\u007f]/.test(value)) return null
  if (value.split('/').some(part => part === '.' || part === '..')) return null
  if (!value.startsWith('/')) return value
  const start = value.indexOf(`/${installation().attachments}/`)
  return start >= 0 ? value.slice(start + 1) : null
}

// ------------------------------------------- a clipboard and a drag, read
//
// Three ways a file reaches this app and one thing done with it. The chooser is
// the + menu's; the other two are a paste and a drop, which hand the page a
// `DataTransfer` — the same object under both names, which is why one reader
// serves both and why it is here, beside the model it feeds, rather than in the
// page.
//
// Every one of them answers the same shape: `{ file, name }`, the bytes and the
// name they go up under. They are two fields because they can disagree — a
// screenshot on the clipboard is a file with no name of its own, and the name
// then is this app's to make.

// Whether a drag is carrying files at all. Asked on `dragover`, where a browser
// hands over the *kinds* on the clipboard and refuses to hand over the bytes —
// so this reads `types` and an item's `kind`, and never calls `getAsFile()`,
// which answers `null` until the drop.
//
// Dragging a selection of text, or a link, is not a drop this app takes: it
// answers `false`, the page leaves the event alone, and nothing highlights.
export function carriesFiles(transfer) {
  if (!transfer) return false
  for (const type of transfer.types ?? []) if (String(type) === 'Files') return true
  for (const item of transfer.items ?? []) if (item?.kind === 'file') return true
  return false
}

// Everything dropped, in the order the browser lists it. Several at once is
// ordinary — a person selects three screenshots and drags the lot — so this is
// a list and the page uploads each.
export function filesFrom(transfer) {
  return filesIn(transfer).map((file) => ({ file, name: file.name || 'file' }))
}

// The images on a clipboard, and nothing else on it. A paste carrying text is
// text and is the field's business; a paste carrying a screenshot is a file and
// is this app's, and a paste carrying both does both.
export function imagesFrom(transfer, { now = Date.now() } = {}) {
  return filesIn(transfer)
    .filter((file) => isImage(file?.type))
    .map((file) => ({ file, name: file.name || pastedName(file.type, now) }))
}

// What to call a screenshot. A clipboard image arrives with the bytes and the
// type and often no name at all, and a file called `file` says nothing about
// when it came from or what it is — so it is named for what it is and the
// minute it was pasted, and the extension is the type's own.
export function pastedName(type, now = Date.now()) {
  const stamp = new Date(now).toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')
  return `pasted-${stamp}.${extensionOf(type)}`
}

function extensionOf(type) {
  const sub = String(type ?? '')
    .split('/')[1]
    ?.split('+')[0]
  if (!sub) return 'png'
  return sub === 'jpeg' ? 'jpg' : sub.replace(/[^a-z0-9]/gi, '') || 'png'
}

// The files on a `DataTransfer`, from whichever of its two lists has them.
// `files` is the plain one; `items` is what a clipboard fills, and an item is a
// file only when it says it is.
function filesIn(transfer) {
  const out = []
  for (const file of transfer?.files ?? []) if (file) out.push(file)
  if (out.length > 0) return out
  for (const item of transfer?.items ?? []) {
    if (item?.kind !== 'file') continue
    const file = item.getAsFile?.()
    if (file) out.push(file)
  }
  return out
}

// One place decides what an image is, so the chip in the composer and the chip
// in the bubble never disagree about which mark to draw.
export function isImage(type) {
  return typeof type === 'string' && type.startsWith('image/')
}

// The last segment of a host path, which is the only part of it worth showing:
// the rest is a directory on a machine this page cannot reach anyway.
export function fileName(path) {
  const parts = String(path).split(/[\\/]/)
  return parts[parts.length - 1] || String(path)
}

export function bytes(n) {
  if (!Number.isFinite(n) || n < 0) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${Math.round((n / (1024 * 1024)) * 10) / 10} MB`
}
