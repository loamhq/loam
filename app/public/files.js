import { noteDateParts } from './time.js'
import { fileShortcuts } from './store.js'
import { createCoreRegistry } from './core-registrations.js'
import { formatDate } from './time.js'
import { segmentedControl } from './chat-ui.js'
import { authenticatedFetch as fetch } from './auth.js'
// Files: the vault and the agents' spaces, browsed,
// read and edited through the host.
//
// **Every rule is the host's.** Which roots exist, what a row may do
// (`capabilities: {read, download, write, create, rename}`), why it may not
// (`authority`), the revision a save has to name, what a save actually stored —
// each is an answer, and this page draws it. Nothing here decides that a file
// is writable: a Save appears where the host said `write`, a "+" where it said
// `create`, and "Read-only" with the host's reason everywhere else.
//
// The address is the state. `/files/<root>/<segments…>` names a folder or a
// file, each segment percent-encoded on its own, so a page loaded at one,
// refreshed on one or come back to with Back lands where it was. Which of the
// two an address names is the host's to say (`stat`), not the page's to guess
// from a dot in the name.
//
// The host's answers are read in one place — `rootsOf()`, `shortcutsOf()`,
// `rowsOf()`, `nextOf()`, `readOf()`, `resolveOf()` — against the contract in
// the README's Files section. Every row the host sends is one flat shape (see
// there); nothing here unwraps a `row` key or invents a count.

import { h, icon, on, kept } from './ui.js'
import { copyText } from './clipboard.js'
import { markdown, safeHref, anchorOf, toggleTask } from './markdown.js'

export { anchorOf }
export const FILES_LOCATION_HIDDEN = 'That location is no longer shown.'

export function copyLinkLabel(resource) {
  if (!resource?.shareLink) return 'Checking link…'
  return resource.shareLink.kind === 'Loam link' ? 'Copy app link' : 'Copy link'
}

// The two supported roots use host names and shared semantic colours.
// Their descriptions explain what can be read and written in each.
export const ROOT_LOOK = {
  vault: { name: 'Vault', color: 'var(--paper-ink-2)', desc: 'Your notes — inbox, daily notes, projects, people. The one place you and your agents both write.' },
  spaces: { name: 'AI Spaces', color: 'var(--latte-espresso)', desc: 'Each agent’s own home — its soul, memory and skills — and the Commons they share. They write here; you read.' },
}

// Why a row is read-only, by the host's `authority`. A `user` file that cannot
// be written is one that is not text, which is the second sentence.
export const AUTHORITY_NOTE = {
  transcripts: 'Conversation records are written by the host. Read them here; they are not edited.',
  persona: 'This belongs to the agent: it writes here, and you read it.',
  runtime: 'The host keeps this. It is shown read-only.',
}

export function readOnlyReason(row) {
  if (capsOf(row).write) return ''
  if (row?.authority && AUTHORITY_NOTE[row.authority]) return AUTHORITY_NOTE[row.authority]
  if (row?.authority === 'user' && row?.kind === 'file') return 'Only text files are edited here; this one can be downloaded.'
  return ''
}

const PAGE = 200

// ------------------------------------------------------------- addresses

// Today's daily note, as the vault already names them: `YYYY-MM-DD.md`, flat in
// the daily folder (Obsidian's default format, which is what that folder
// holds). The date is the person's own, not UTC.
export function dailyNotePath(folder, now = new Date()) {
  return joinPath(folder, `${ymd(now)}.md`)
}

// A new inbox note, named for the minute it was made so two never collide by
// accident; `n` is the suffix tried after a name that was taken.
export function inboxNotePath(folder, now = new Date(), n = 0) {
  const parts = noteDateParts(now)
  const hm = `${parts.hour}${parts.minute}`
  return joinPath(folder, `${ymd(now)} ${hm}${n ? ` ${n + 1}` : ''}.md`)
}

function ymd(d) {
  const parts = noteDateParts(d)
  return `${parts.year}-${parts.month}-${parts.day}`
}

// `/files/<root>/<a>/<b c>` from a root and a path, each segment encoded on its
// own so a `/` inside a name can never become a second segment.
export function filesHref(root, path = '') {
  if (!root) return '/files'
  const segments = splitPath(path).map(encodeURIComponent)
  return ['/files', encodeURIComponent(root), ...segments].join('/')
}

// The reverse, or `null` for an address that is not a Files one or that names
// a `.`/`..` segment — which this page never makes and never follows.
export function parseFilesPath(pathname) {
  if (pathname === '/files' || pathname === '/files/') return { root: null, path: '' }
  if (!pathname.startsWith('/files/')) return null
  const parts = pathname.slice('/files/'.length).split('/').filter((s) => s !== '')
  let decoded
  try {
    decoded = parts.map(decodeURIComponent)
  } catch {
    return null
  }
  if (decoded.some((s) => s === '.' || s === '..' || s.includes('/') || s.includes('\\') || s.includes('\u0000'))) return null
  const [root, ...rest] = decoded
  return { root, path: rest.join('/') }
}

export function splitPath(path) {
  return String(path ?? '').split('/').filter((s) => s !== '')
}

export function parentOf(path) {
  return splitPath(path).slice(0, -1).join('/')
}

export function nameOf(path) {
  return splitPath(path).at(-1) ?? ''
}

export function joinPath(folder, name) {
  return [...splitPath(folder), ...splitPath(name)].join('/')
}

// A name a person typed for a new file or folder: one segment, no separators,
// nothing that is not a name. The host checks again; this is only so the sheet
// can say so before a request is made.
export function validName(name) {
  const n = String(name ?? '').trim()
  if (n === '' || n === '.' || n === '..') return false
  return !/[\\/\u0000-\u001f\u007f]/.test(n)
}

// A published vault URL — one of the prefixes the app is configured with
// (`FILES_VAULT_URLS`) — read as the vault path it names, so it opens in Files
// rather than leaving the app. Only the exact configured prefix is honoured:
// the rest must be a plain path, with no query and no `.`/`..` segment.
// `{ ref, fragment }` for the resolver, or `null`.
export function vaultUrlRef(raw, prefixes) {
  if (typeof raw !== 'string' || !Array.isArray(prefixes)) return null
  const url = raw.trim()
  for (const p of prefixes) {
    const prefix = String(p ?? '').replace(/\/+$/, '')
    if (!/^https:\/\/[^/?#]+(\/[^?#]*)?$/i.test(prefix)) continue
    if (url.toLowerCase() !== prefix.toLowerCase() && !url.toLowerCase().startsWith(`${prefix.toLowerCase()}/`)) continue
    let rest = url.slice(prefix.length)
    let fragment = null
    const hash = rest.indexOf('#')
    if (hash >= 0) {
      fragment = rest.slice(hash + 1)
      rest = rest.slice(0, hash)
    }
    if (rest.includes('?')) return null
    let parts
    try {
      parts = splitPath(rest).map((s) => decodeURIComponent(s.replace(/\+/g, '%20')))
      if (fragment !== null) fragment = decodeURIComponent(fragment)
    } catch {
      return null
    }
    if (parts.some((s) => s === '.' || s === '..' || /[\\\u0000-\u001f]/.test(s))) return null
    const path = parts.join('/')
    if (path === '') return { ref: '/files/vault', fragment }
    // A path with an extension is exact; one without is how a published note
    // is addressed, and the host's wiki lookup finds the note it names.
    return { ref: extOf(path) ? filesHref('vault', path) : `[[${path}]]`, fragment }
  }
  return null
}

// ------------------------------------------------------- what a row is

export function extOf(name) {
  const m = /\.([A-Za-z0-9]{1,8})$/.exec(String(name ?? ''))
  return m ? m[1].toLowerCase() : ''
}

const TILES = {
  md: ['MD', 'var(--paper-card)', 'var(--paper-ink-2)'],
  markdown: ['MD', 'var(--paper-card)', 'var(--paper-ink-2)'],
  txt: ['TXT', 'var(--paper-card)', 'var(--paper-ink-3)'],
  html: ['HTML', 'var(--paper-card)', 'var(--paper-ink-2)'],
  htm: ['HTML', 'var(--paper-card)', 'var(--paper-ink-2)'],
  csv: ['CSV', 'var(--paper-card)', 'var(--paper-ink-3)'],
  pdf: ['PDF', 'var(--paper-card)', 'var(--paper-ink-2)'],
  json: ['JSON', 'var(--paper-card)', 'var(--paper-ink-3)'],
  jsonl: ['JSONL', 'var(--paper-card)', 'var(--paper-ink-3)'],
  cron: ['CRON', 'var(--paper-card)', 'var(--paper-ink-2)'],
  jpg: ['JPG', 'var(--paper-card)', 'var(--paper-ink-2)'],
  jpeg: ['JPG', 'var(--paper-card)', 'var(--paper-ink-2)'],
  png: ['PNG', 'var(--paper-card)', 'var(--paper-ink-2)'],
}

export function tileOf(name) {
  const ext = extOf(name)
  const known = TILES[ext]
  if (known) return { label: known[0], bg: known[1], fg: known[2] }
  return { label: ext ? ext.toUpperCase().slice(0, 4) : 'FILE', bg: 'var(--paper-card)', fg: 'var(--paper-ink-3)' }
}

export function isFolder(row) {
  return row?.kind === 'folder'
}

// The raster types and PDF this page will draw from bytes, by extension and
// media type both, and the first bytes each must start with. SVG is not here:
// it is a document that can carry script, not a picture.
const DRAWN = {
  png: { type: 'image/png', magic: [[0x89, 0x50, 0x4e, 0x47]] },
  jpg: { type: 'image/jpeg', magic: [[0xff, 0xd8, 0xff]] },
  jpeg: { type: 'image/jpeg', magic: [[0xff, 0xd8, 0xff]] },
  gif: { type: 'image/gif', magic: [[0x47, 0x49, 0x46, 0x38]] },
  webp: { type: 'image/webp', magic: [[0x52, 0x49, 0x46, 0x46]] },
  avif: { type: 'image/avif', magic: [[null, null, null, null, 0x66, 0x74, 0x79, 0x70]] },
  pdf: { type: 'application/pdf', magic: [[0x25, 0x50, 0x44, 0x46, 0x2d]] },
}

export function drawnTypeOf(row) {
  const ext = extOf(row?.name ?? row?.path)
  const want = DRAWN[ext]
  if (!want) return null
  const type = String(row?.media_type ?? '').split(';')[0].trim().toLowerCase()
  // A media type the host sent that disagrees with the extension is not drawn.
  if (type && type !== want.type && !(type === 'image/jpg' && want.type === 'image/jpeg')) return null
  return want
}

export function bytesAre(want, buf) {
  const view = new Uint8Array(buf.slice ? buf.slice(0, 16) : buf)
  return want.magic.some((sig) => sig.every((b, i) => b === null || view[i] === b))
}

// How a file is shown, from its type and name. `text` is read and drawn in a
// field; `markdown` and `html` have a preview beside it; `image` and `pdf` are
// drawn from their bytes; everything else is a download. A file the host says
// can be written is text whatever its extension, because only text is.
export function viewOf(row) {
  const type = String(row?.media_type ?? '').toLowerCase()
  const ext = extOf(row?.name ?? row?.path)
  if (ext === 'md' || ext === 'markdown' || type === 'text/markdown') return 'markdown'
  if (ext === 'html' || ext === 'htm' || type === 'text/html') return 'html'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'avif'].includes(ext)) return 'image'
  if (ext === 'pdf') return 'pdf'
  if (capsOf(row).write) return 'text'
  if (type.startsWith('text/') || /json|yaml|toml|xml/.test(type)) return 'text'
  if (['txt', 'json', 'jsonl', 'csv', 'tsv', 'cron', 'yaml', 'yml', 'toml', 'base', 'canvas', 'js', 'mjs', 'css', 'sh', 'py', 'log', 'ini', 'conf'].includes(ext)) return 'text'
  return 'other'
}

export function capsOf(row) {
  const c = row?.capabilities ?? {}
  return { read: c.read === true, download: c.download === true, write: c.write === true, create: c.create === true, rename: c.rename === true }
}

// The returned `from` is authoritative, including for a folder's descendants.
export function renamedResource(resource, answer) {
  const from = answer?.from
  if (!resource || !from || resource.root !== from.root) return resource
  if (resource.path === from.path) return { ...resource, ...answer, from: undefined }
  if (answer.kind !== 'folder' || !resource.path?.startsWith(`${from.path}/`)) return resource
  const path = answer.path + resource.path.slice(from.path.length)
  return { ...resource, root: answer.root, path, link: filesHref(answer.root, path) }
}

export function renameName(resource, value) {
  const name = String(value ?? '').trim()
  const ext = !isFolder(resource) ? /\.[^.]+$/.exec(resource.name)?.[0] : ''
  return name && name !== '.' && name !== '..' && ext && !name.includes('.') ? name + ext : name
}

export function bytes(n) {
  if (!Number.isFinite(n)) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function when(iso, now = Date.now()) {
  const t = Date.parse(iso ?? '')
  if (!Number.isFinite(t)) return ''
  const s = Math.max(0, (now - t) / 1000)
  if (s < 60) return 'Now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  if (s < 86400 * 7) return `${Math.floor(s / 86400)}d ago`
  return formatDate(t)
}

// A folder row has no size and no count on the wire, and none is made up.
export function metaOf(row) {
  if (isFolder(row)) return ['Folder', when(row.modified)].filter(Boolean).join(' · ')
  return [bytes(row.size), when(row.modified)].filter(Boolean).join(' · ')
}

// ----------------------------------------------- the host's answers, read

function isRow(r) {
  return Boolean(r) && typeof r.root === 'string' && typeof r.path === 'string' && typeof r.name === 'string' && (r.kind === 'file' || r.kind === 'folder')
}

// `GET /v1/files/roots` → `{roots: [{root, name, description, link,
// capabilities}], shortcuts}`. A root row is not a Row: no path, no kind, no
// count.
export function rootsOf(body) {
  const list = Array.isArray(body?.roots) ? body.roots : []
  return list
    .filter((r) => r && ['vault', 'spaces'].includes(r.root))
    .map((r) => {
      const look = ROOT_LOOK[r.root] ?? {}
      return {
        root: r.root,
        name: typeof r.name === 'string' && r.name ? r.name : look.name ?? r.root,
        desc: look.desc ?? (typeof r.description === 'string' ? r.description : ''),
        color: look.color ?? 'var(--paper-ink-2)',
        link: typeof r.link === 'string' ? r.link : filesHref(r.root),
        capabilities: r.capabilities ?? null,
      }
    })
}

export function shortcutsOf(body) {
  const list = Array.isArray(body?.shortcuts) ? body.shortcuts : []
  return list.filter((s) => s?.resource?.root !== 'system' && s && typeof s.label === 'string' && isRow(s.resource))
}

export function rowsOf(body) {
  return Array.isArray(body?.entries) ? body.entries.filter(isRow) : []
}

// The next page's cursor, a string to send back verbatim, or `null` on the
// last page. The answer's `cursor` is *this* page's offset and is never it.
export function nextOf(body) {
  return typeof body?.next_cursor === 'string' && body.next_cursor !== '' ? body.next_cursor : null
}

// `read`, `PUT text` and `POST file` answer a bare Row with `text` and
// `revision` (and `provenance` on the writes) beside its fields.
export function readOf(body) {
  const { text, revision, provenance, schedule, ...row } = body ?? {}
  return {
    text: typeof text === 'string' ? text : '',
    revision: typeof revision === 'string' ? revision : null,
    provenance: provenance === true,
    schedule: schedule ?? null,
    row: isRow(row) ? row : null,
  }
}

// `resolve`, by its `status`. A `missing` answer's `root`/`path` are what was
// looked for and never a hit; every key but `status` is optional on it.
export function resolveOf(body) {
  const status = body?.status
  const fragment = typeof body?.fragment === 'string' && body.fragment !== '' ? body.fragment : null
  if (status === 'found' && isRow(body.resource)) return { status, resource: body.resource, fragment }
  if (status === 'ambiguous') {
    const candidates = Array.isArray(body.candidates) ? body.candidates.filter(isRow) : []
    const total = Number.isInteger(body.candidates_total) ? body.candidates_total : candidates.length
    return { status, candidates, total, truncated: body.truncated === true, fragment }
  }
  if (status === 'external') return { status, url: typeof body.url === 'string' ? body.url : '' }
  return {
    status: 'missing',
    reason: typeof body?.reason === 'string' ? body.reason : null,
    target: typeof body?.target === 'string' ? body.target : null,
    root: typeof body?.root === 'string' ? body.root : null,
    path: typeof body?.path === 'string' ? body.path : null,
    truncated: body?.truncated === true,
  }
}

// What a link that leads nowhere says, from what the host said it looked for.
export function missingSentence(ref, r) {
  if (r.reason) return `${ref} cannot be opened: ${r.reason}.`
  if (r.truncated) return `${ref} was not found before the search was cut short.`
  if (r.target) return `There is no note called “${r.target}”.`
  if (r.root && r.path !== null) return `Nothing at ${ROOT_LOOK[r.root]?.name ?? r.root} / ${r.path}.`
  return `${ref} is not a file you can open in Files.`
}

// An external link from the resolver is followed only if it is `http`,
// `https` or `mailto` — the same allowlist as a link in a chat.
export function externalHref(url) {
  return safeHref(url)
}

// ---------------------------------------------------- the formatting bar

// One button of the editor's toolbar applied to a selection: what the field
// holds afterwards and where the selection goes. Pure, so it is tested without
// a field. Wraps (`bold`, `italic`, `link`) act on the selected characters;
// line marks (`heading`, `list`, `quote`) act on every line the selection
// touches, and take the mark off when every one of them already has it.
export const TOOLS = [
  { kind: 'heading', label: 'H', title: 'Heading' },
  { kind: 'bold', label: 'B', title: 'Bold' },
  { kind: 'italic', label: 'I', title: 'Italic' },
  { kind: 'list', glyph: 'list', title: 'List' },
  { kind: 'task', glyph: 'task', title: 'Task' },
  { kind: 'link', label: '🔗︎', title: 'Link' },
  { kind: 'wikilink', label: '[[ ]]', title: 'Link to a note' },
  { kind: 'code', label: '</>', title: 'Code' },
  { kind: 'quote', label: '❝', title: 'Quote' },
]

const LINE_MARKS = { heading: '## ', list: '- ', quote: '> ', task: '- [ ] ' }

export function formatText(value, start, end, kind) {
  const s = Math.max(0, Math.min(start, end))
  const e = Math.min(value.length, Math.max(start, end))
  const sel = value.slice(s, e)
  const wrap = (open, close, filler) => {
    if (sel.length >= open.length + close.length && sel.startsWith(open) && sel.endsWith(close)) {
      const inner = sel.slice(open.length, sel.length - close.length)
      return { value: value.slice(0, s) + inner + value.slice(e), start: s, end: s + inner.length }
    }
    const inner = sel || filler
    return { value: value.slice(0, s) + open + inner + close + value.slice(e), start: s + open.length, end: s + open.length + inner.length }
  }
  if (kind === 'bold') return wrap('**', '**', 'bold')
  if (kind === 'italic') return wrap('*', '*', 'italic')
  if (kind === 'wikilink') return wrap('[[', ']]', 'note')
  // A selection across lines is a fenced block; within one, a code span.
  if (kind === 'code') return sel.includes('\n') ? wrap('```\n', '\n```', 'code') : wrap('`', '`', 'code')
  if (kind === 'link') {
    const label = sel || 'link'
    const out = `[${label}](url)`
    const at = s + label.length + 3
    return { value: value.slice(0, s) + out + value.slice(e), start: at, end: at + 3 }
  }
  const mark = LINE_MARKS[kind]
  if (!mark) return { value, start, end }
  const lineStart = value.lastIndexOf('\n', s - 1) + 1
  const nl = value.indexOf('\n', e > s && value[e - 1] === '\n' ? e - 1 : e)
  const lineEnd = nl < 0 ? value.length : nl
  const lines = value.slice(lineStart, lineEnd).split('\n')
  const strip = kind === 'heading' ? /^#{1,6} / : kind === 'list' ? /^[-*+] (?!\[[ xX]\] )/ : kind === 'task' ? /^[-*+] \[[ xX]\] / : /^> ?/
  const all = lines.every((l) => strip.test(l))
  // A list item made a task, or a task made a list item, swaps its marker
  // rather than stacking a second one in front of it.
  const bare = (l) => (kind === 'heading' ? l.replace(/^#{1,6} /, '') : kind === 'task' || kind === 'list' ? l.replace(/^[-*+] (\[[ xX]\] )?/, '') : l)
  const next = lines.map((l) => (all ? l.replace(strip, '') : mark + bare(l))).join('\n')
  return { value: value.slice(0, lineStart) + next + value.slice(lineEnd), start: lineStart, end: lineStart + next.length }
}

// --------------------------------------------------------- HTML preview

// The legacy static fallback runs nothing. Live HTML uses a separate content
// authority returned by `/api/files/live`. `/api/files/preview` builds the
// fallback on the server (its own local images
// and stylesheets inlined, within limits) and answers it under a policy of its
// own — `sandbox` (an opaque origin, no scripts, no forms), no network, inline
// styles allowed in that document alone — and the iframe that shows it is
// `sandbox=""` as well. The app's own policy is not loosened for it.
export function previewHref(root, path) {
  return `/api/files/preview?root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`
}

export function downloadHref(root, path) {
  return `/api/files/download?root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`
}

// ------------------------------------------------------------ the module

// How long typing has to pause before the editor saves itself.
export const AUTOSAVE_MS = 1200

export function createFiles({
  registry = createCoreRegistry(),
  api,
  schedule,
  timers = { set: (fn, ms) => globalThis.setTimeout(fn, ms), clear: (t) => globalThis.clearTimeout(t) },
  onResolveTo = null,
  onRead = null,
  onRename = null,
  fetchBytes = (href) => fetch(href, { credentials: 'same-origin' }),
  urls = globalThis.URL,
  sendForm = fetchForm,
}) {
  let uploads = 0
  let copyNoticeTimer = null
  const st = {
    // What the host takes as an upload (`/api/limits`), null until it says.
    upload: { max: null, types: null },
    roots: { loaded: false, items: [], shortcuts: [], error: '' },
    // The folder being listed. `root: null` is the three cards.
    root: null,
    path: '',
    listing: { key: '', rows: [], folder: null, cursor: null, total: null, loading: false, error: '', status: null },
    // The open file, or null.
    file: null,
    renaming: null,
    // The "+" sheet, the name sheet, a choice between files, a leave question.
    sheet: null,
    // Where Back-to-chat goes.
    returnTo: null,
    notice: '',
    turn: 0,
  }

  const q = (o) =>
    Object.entries(o)
      .filter(([, v]) => v !== null && v !== undefined && v !== '')
      .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
      .join('&')

  async function loadRoots() {
    try {
      const body = await api('GET', '/files/roots')
      st.roots = { loaded: true, items: rootsOf(body), shortcuts: shortcutsOf(body), error: '' }
    } catch (err) {
      st.roots = { loaded: true, items: [], shortcuts: [], error: err.message, status: err.status }
    }
    schedule()
  }

  // A plugin's own root (2026-11-16, `files.root`; doc-comments' Shared
  // Docs first): `@<id>` in the address, listed and drawn by the plugin.
  function pluginRoot(root = st.root) {
    if (typeof root !== 'string' || !root.startsWith('@')) return null
    return registry.entries('files.root', {}).find(r => `@${r.id}` === root) ?? null
  }

  async function loadFolder(more = false) {
    if (!st.root) return
    const own = pluginRoot()
    if (!own && st.root.startsWith('@')) {
      // A plugin's root whose plugin has not registered yet (a cold load of
      // its address): nothing to ask the host; the plugin draws it when it
      // arrives.
      st.listing = { key: `${st.root}:`, rows: [], folder: null, cursor: null, total: null, loading: true, error: '', status: null }
      schedule()
      return
    }
    if (own) {
      st.listing = { key: `${st.root}:`, rows: [], folder: null, cursor: null, total: null, loading: false, error: '', status: null }
      try { await own.load?.() } catch (err) { st.listing.error = err.message }
      schedule()
      return
    }
    const key = `${st.root}:${st.path}`
    if (!more) st.listing = { key, rows: [], folder: null, cursor: null, total: null, loading: true, error: '', status: null }
    else st.listing.loading = true
    schedule()
    try {
      const body = await api('GET', `/files/list?${q({ root: st.root, path: st.path, cursor: more ? st.listing.cursor : null, limit: PAGE })}`)
      if (st.listing.key !== key) return
      st.listing.rows = more ? [...st.listing.rows, ...rowsOf(body)] : rowsOf(body)
      st.listing.cursor = nextOf(body)
      st.listing.total = Number.isInteger(body?.total) ? body.total : null
      st.listing.folder = isRow(body?.folder) ? body.folder : st.listing.folder
      st.listing.loading = false
    } catch (err) {
      if (st.listing.key !== key) return
      st.listing.loading = false
      st.listing.error = err.message
      st.listing.status = err.status
    }
    schedule()
  }

  // Whether leaving the open file would lose something.
  function dirty() {
    if (st.file?.owner?.dirty) return Boolean(st.file.owner.dirty())
    return Boolean(st.file && st.file.draft !== null && st.file.draft !== st.file.saved)
  }

  // A plugin's editor holding the open file (2026-11-16, for doc-comments;
  // any plugin may). While it does, Files' own edits, saves and the field
  // dictation writes into are its: `handlers` is `{dirty(), save(), edit(text),
  // field}`, each optional, `save` returning a promise. The way out of an
  // unsaved edit (`guard`) then waits on its save. Released by `remove()`, or
  // when another file opens.
  function own(file, handlers) {
    if (!file || st.file !== file || !handlers) return { remove() {} }
    file.owner = handlers
    return { remove() { if (file.owner === handlers) file.owner = null } }
  }

  // Every way out of an unsaved edit comes through here. Clean, `proceed` runs
  // now and this answers `true`. Dirty, the page's own sheet asks — "Keep
  // editing" or "Leave without saving" — and `proceed` runs only on the
  // second; this answers `false` so the caller stays where it is meanwhile.
  //
  // A file the editor can save is saved on the way out instead: a quiet
  // `saving` sheet holds the way out the caller set, and it is taken the
  // moment the save lands clean. A save that could not land — refused, or a
  // conflict — becomes the question, so nothing is left behind unsaid.
  function guard(proceed) {
    if (!dirty()) {
      proceed?.()
      return true
    }
    const file = st.file
    if (file.caps.write && !file.conflict) {
      const sheet = { kind: 'saving', name: nameOf(file.path), proceed: () => proceed?.() }
      st.sheet = sheet
      const flush = () =>
        save().then(() => {
          if (st.sheet !== sheet) return
          if (st.file === file && !dirty()) return sheet.proceed()
          if (st.file === file && !file.conflict && !file.failed) return flush()
          st.sheet = { ...sheet, kind: 'leave' }
          schedule()
        })
      flush()
      schedule()
      return false
    }
    st.sheet = {
      kind: 'leave',
      name: nameOf(st.file.path),
      proceed: () => {
        st.sheet = null
        if (st.file) st.file.draft = null
        proceed?.()
        schedule()
      },
    }
    schedule()
    return false
  }

  // The open file goes: its object URL, if it had one, is given back.
  function closeFile() {
    const f = st.file
    if (f?.timer) timers.clear(f.timer)
    if (f?.blobUrl) urls?.revokeObjectURL?.(f.blobUrl)
    st.file = null
  }

  // Go to an address — a folder, or a file in its folder. `isFile` is known
  // when a row was clicked; an address loaded cold is asked about.
  async function show(root, path = '', { isFile = null, row = null, fragment = null, mode = null, keepFile = false } = {}) {
    if (!st.renaming?.busy) st.renaming = null
    // The latest place asked for wins: a slow answer about an earlier one must
    // not close a file opened after it.
    const hidden = root === 'system'
    if (hidden) { root = null; path = '' }
    const turn = ++st.turn
    st.notice = hidden ? FILES_LOCATION_HIDDEN : ''
    if (st.sheet?.kind !== 'choose') st.sheet = null
    if (!root) {
      st.root = null
      st.path = ''
      if (!keepFile) closeFile()
      if (!st.roots.loaded) loadRoots()
      schedule()
      return
    }
    if (!st.roots.loaded) loadRoots()
    let kindIsFile = isFile
    let known = row
    if (kindIsFile === null && path !== '') {
      try {
        known = await api('GET', `/files/stat?${q({ root, path })}`)
        if (turn !== st.turn) return
        kindIsFile = !isFolder(known)
      } catch (err) {
        if (turn !== st.turn) return
        // A stat that failed is a folder that failed to list, so the error is
        // drawn where the listing would be and not swallowed.
        st.root = root
        st.path = path
        if (!keepFile) closeFile()
        st.listing = { key: `${root}:${path}`, rows: [], folder: null, cursor: null, total: null, loading: false, error: err.message, status: err.status }
        schedule()
        return
      }
    }
    const folder = kindIsFile ? parentOf(path) : path
    if (st.root !== root || st.path !== folder || st.listing.error) {
      st.root = root
      st.path = folder
      loadFolder()
    }
    if (kindIsFile) {
      if (!(st.file && st.file.root === root && st.file.path === path)) openFile(root, path, known, { fragment, mode })
      else if (fragment) st.file.fragment = fragment
    } else if (!keepFile) closeFile()
    schedule()
  }

  // A row the host answered with on a read or a save is newer than the one
  // the folder was listed with, so the list draws it: its size and its time
  // follow the save rather than the listing.
  function relist(row) {
    const at = st.listing.rows.findIndex((x) => x.root === row.root && x.path === row.path)
    if (at >= 0) st.listing.rows = st.listing.rows.with(at, row)
  }

  async function openFile(root, path, row = null, { fragment = null, mode = null } = {}) {
    const known = isRow(row) ? row : st.listing.rows.find((x) => x.path === path)
    const r = known ?? { root, path, name: nameOf(path), kind: 'file' }
    const view = viewOf(r)
    closeFile()
    const file = {
      root,
      path,
      row: r,
      view,
      caps: capsOf(r),
      text: '',
      saved: '',
      draft: null,
      revision: null,
      textLoaded: false,
      metadataLoading: view === 'html' && !known?.capabilities,
      mode: mode === 'edit' || mode === 'preview' ? mode : view === 'markdown' || view === 'html' ? 'preview' : 'edit',
      loading: true,
      error: '',
      status: null,
      saving: false,
      conflict: null,
      flash: '',
      fragment,
      blobUrl: null,
      blobError: '',
    }
    st.file = file
    schedule()
    prepareLink(file)
    // Rendering HTML uses the isolated download path, not the text editor.
    // In particular, embedded images can exceed the host's text-read limit.
    if (view === 'html') {
      // A cold panel address may contain only an identity. The old text read
      // supplied its row; fetch metadata alone before offering file actions.
      if (file.metadataLoading) {
        try {
          const got = await api('GET', `/files/stat?${q({ root, path })}`)
          if (st.file !== file) return
          file.row = got
          file.caps = capsOf(got)
          relist(got)
        } catch (err) {
          if (st.file !== file) return
          file.metadataError = true
          file.error = err.message
          file.status = err.status
          file.loading = false
          schedule()
          return
        } finally { file.metadataLoading = false }
      }
      loadLive(file)
      if (file.mode === 'preview') {
        file.loading = false
        schedule()
        return
      }
    }
    if (view === 'image' || view === 'pdf') return loadBytes(file)
    if (view === 'other') {
      file.loading = false
      schedule()
      return
    }
    await loadText(file)
  }

  async function loadText(file) {
    if (file.textLoaded || file.textLoading) return
    file.textLoading = true
    file.loading = true
    file.error = ''
    schedule()
    try {
      const got = readOf(await api('GET', `/files/read?${q({ root: file.root, path: file.path })}`))
      if (st.file !== file) return
      file.text = got.text
      file.saved = got.text
      file.revision = got.revision
      file.textLoaded = true
      if (file.mode === 'edit' && file.draft === null) file.draft = got.text
      if (got.row) {
        file.row = got.row
        file.caps = capsOf(got.row)
        relist(got.row)
      }
      // A schedule opened directly in Files shares the tab's host seen mark.
      // A seen refusal must not hide the file that was successfully read.
      try { await onRead?.(file.row) }
      catch (err) { if (st.file === file) st.notice = err.message }
    } catch (err) {
      if (st.file !== file) return
      file.error = err.message
      file.status = err.status
      file.errorBody = err.body ?? null
    }
    file.textLoading = false
    file.loading = false
    schedule()
  }

  async function openShortcut(shortcut, go) {
    if (shortcut.root === 'system') { go('system', shortcut.path); return }
    const turn = st.turn
    try {
      const row = await api('GET', `/files/stat?${q({ root: shortcut.root, path: shortcut.path })}`)
      if (turn !== st.turn) return
      go(shortcut.root, shortcut.path, { isFile: !isFolder(row), row })
    } catch (err) {
      if (turn !== st.turn) return
      st.notice = err.status === 404 ? `${nameOf(shortcut.path)} has moved or been deleted. You can remove its shortcut from ⋯.` : err.message
      schedule()
    }
  }

  async function loadLive(file = st.file) {
    if (!file || file.view !== 'html') return
    file.liveLoading = true
    file.liveError = ''
    schedule()
    try {
      const body = await api('GET', `/files/live?${q({ root: file.root, path: file.path })}`)
      const target = new URL(body.url)
      if (!['https:', 'http:'].includes(target.protocol) || target.origin === globalThis.location?.origin) throw new Error('Live HTML requires a different origin from this app.')
      file.liveUrl = target.href
      file.liveReload = (file.liveReload ?? 0) + 1
    } catch (err) { file.liveError = err.message }
    file.liveLoading = false
    schedule()
  }

  async function prepareLink(resource) {
    if (resource.linkPromise) return resource.linkPromise
    const path = resource.path
    resource.linkPromise = api('GET', `/files/link?${q({ root: resource.root, path: resource.path })}`)
      .catch(() => ({ url: filesHref(resource.root, resource.path), kind: 'Loam link' }))
      .then((body) => { if (resource.path === path) resource.shareLink = body; schedule(); return body })
    return resource.linkPromise
  }

  async function copyLink(resource) {
    const link = resource.shareLink ?? await prepareLink(resource)
    const origin = globalThis.location?.origin ?? globalThis.location?.href ?? 'http://localhost'
    const ok = await copyText(new URL(link.url, origin).href)
    st.notice = ok ? `Link copied · ${link.kind}` : 'Couldn’t copy the link. Try again.'
    if (copyNoticeTimer !== null) timers.clear(copyNoticeTimer)
    if (ok) {
      const notice = st.notice
      copyNoticeTimer = timers.set(() => { if (st.notice === notice) { st.notice = ''; schedule() } }, 3000)
    }
    schedule()
  }

  function resourceMenu(resource, home, { openFolder = null } = {}) {
    if (st.file?.root === resource.root && st.file.path === resource.path) {
      resource.shareLink = st.file.shareLink
      resource.linkPromise = st.file.linkPromise
      // Follow a still-pending check into the menu's own view object.
      resource.linkPromise?.then((link) => { resource.shareLink = link; schedule() })
    }
    prepareLink(resource)
    st.notice = ''
    st.sheet = { kind: 'resource', resource, name: resource.name ?? nameOf(resource.path), on: home?.isShortcut(resource.root, resource.path), viewSource: st.file?.view === 'html' && st.file.root === resource.root && st.file.path === resource.path ? () => setMode('edit') : null, toggle: () => home?.toggleShortcut(resource.root, resource.path, resource.kind ?? 'file') }
    st.sheet.openFolder = openFolder
    schedule()
  }

  // A shortcut stores an address, not capabilities. Ask before offering Rename;
  // even a missing shortcut must still be removable through its menu.
  async function shortcutMenu(shortcut, home) {
    const sheet = { kind: 'folder', name: shortcut.label || nameOf(shortcut.path), on: true, toggle: () => home?.toggleShortcut(shortcut.root, shortcut.path, shortcut.kind), renameSlot: 'shortcut' }
    st.sheet = sheet
    schedule()
    try {
      const resource = await api('GET', `/files/stat?${q({ root: shortcut.root, path: shortcut.path })}`)
      if (st.sheet === sheet) { sheet.resource = resource; prepareLink(resource) }
    } catch { /* Removal remains available for a missing location. */ }
    schedule()
  }

  function beginRename(resource, slot = 'row') {
    if (!capsOf(resource).rename || st.renaming?.busy) return
    if (st.file?.root === resource.root && st.file.path === resource.path) {
      if (st.file.loading) return
      slot = 'title'
    }
    st.sheet = null
    st.renaming = { resource, slot, value: resource.name, error: '', busy: false }
    schedule()
  }

  function cancelRename() {
    if (st.renaming?.busy) return
    st.renaming = null
    schedule()
  }

  function editRename(value) {
    if (!st.renaming || st.renaming.busy) return
    st.renaming.value = value
    st.renaming.error = ''
    schedule()
  }

  function applyRename(answer) {
    st.listing.rows = st.listing.rows.map(row => renamedResource(row, answer))
    if (st.listing.folder) st.listing.folder = renamedResource(st.listing.folder, answer)
    const folder = renamedResource({ root: st.root, path: st.path }, answer)
    st.root = folder.root
    st.path = folder.path
    st.listing.key = `${st.root}:${st.path}`
    st.roots.shortcuts = st.roots.shortcuts.map(s => ({ ...s, resource: renamedResource(s.resource, answer) }))
    if (st.file) {
      const f = st.file, row = renamedResource(f.row, answer)
      if (row !== f.row) {
        const oldView = f.view, oldType = drawnTypeOf(f.row)?.type
        f.root = row.root; f.path = row.path; f.row = row; f.caps = capsOf(row)
        f.view = viewOf(row)
        f.shareLink = null; f.linkPromise = null
        prepareLink(f)
        if (f.view === 'html') loadLive(f)
        if ((oldView !== f.view || oldType !== drawnTypeOf(row)?.type) && ['image', 'pdf'].includes(f.view)) {
          if (f.blobUrl) urls?.revokeObjectURL?.(f.blobUrl)
          f.blobUrl = null
          loadBytes(f)
        }
        else if (['image', 'pdf', 'other'].includes(oldView) && ['text', 'markdown', 'html'].includes(f.view)) openFile(f.root, f.path, row)
      }
    }
    schedule()
  }

  function focusRename() {
    const s = st.renaming
    if (!s?.field?.isConnected || s.focused) return
    s.focused = true
    s.field.focus()
    const end = !isFolder(s.resource) && s.resource.name.lastIndexOf('.') > 0 ? s.resource.name.lastIndexOf('.') : s.resource.name.length
    s.field.setSelectionRange?.(0, end)
  }

  async function rename() {
    const s = st.renaming
    if (!s || s.busy) return
    const name = renameName(s.resource, s.value)
    if (name === s.resource.name) return cancelRename()
    s.busy = true
    s.error = ''
    // Settle an already-started save before changing the path. New edits keep
    // their draft and timer, and save at the new address after rename lands.
    const file = st.file?.root === s.resource.root && (st.file.path === s.resource.path || (isFolder(s.resource) && st.file.path.startsWith(`${s.resource.path}/`))) ? st.file : null
    let finishRename
    if (file) {
      file.renaming = true
      file.renameFlight = new Promise(resolve => { finishRename = resolve })
    }
    schedule()
    try {
      if (file?.inflight && file.saving) await file.inflight
      const answer = await api('POST', '/files/rename', { root: s.resource.root, path: s.resource.path, name })
      if (!isRow(answer) || !answer.from) throw new Error('The host did not return the renamed location.')
      const offered = st.roots.shortcuts
      applyRename(answer)
      await onRename?.(answer, offered)
      registry.emit('file.renamed', answer)
      if (st.renaming === s) st.renaming = null
    } catch (err) {
      s.error = err.message
    } finally {
      s.busy = false
      if (file) {
        file.renaming = false
        finishRename()
        if (st.file === file && dirty()) soon(file)
      }
      schedule()
    }
  }

  // An image or a PDF, drawn from its bytes: fetched by this page (with its
  // own credentials, so the download's `attachment` header is beside the
  // point), checked to be the type its name says by its first bytes, and shown
  // from an object URL of that one type. Anything else is offered as a
  // download and never drawn.
  async function loadBytes(file) {
    const want = drawnTypeOf(file.row)
    const done = (error) => {
      file.blobError = error
      file.loading = false
      schedule()
    }
    if (!want || !file.caps.download) return done('')
    try {
      const res = await fetchBytes(downloadHref(file.root, file.path))
      if (st.file !== file) return
      if (!res.ok) return done(`The host would not send this file (${res.status}).`)
      const buf = await res.arrayBuffer()
      if (st.file !== file) return
      if (!bytesAre(want, buf)) return done('This file is not the picture its name says, so it is not drawn.')
      file.blobUrl = urls.createObjectURL(new Blob([buf], { type: want.type }))
      done('')
    } catch (err) {
      if (st.file !== file) return
      done(err.message)
    }
  }

  // An edit, and the save it will make once typing pauses. A file waiting on
  // the person's answer to a conflict saves nothing until they give it.
  function edit(text) {
    const file = st.file
    if (file?.owner?.edit) return file.owner.edit(text)
    if (!file || (file.view === 'html' && !file.textLoaded)) return
    file.draft = text
    file.flash = ''
    file.failed = false
    file.schedule = null
    soon(file)
    schedule()
  }

  function soon(file, ms = AUTOSAVE_MS) {
    if (file.timer) timers.clear(file.timer)
    file.timer = null
    if (!file.caps.write || file.conflict || file.renaming || !dirty()) return
    file.timer = timers.set(() => {
      file.timer = null
      if (st.file === file) save()
    }, ms)
  }

  // A task ticked in the preview is an edit of its own line, saved at once.
  // A note that has changed under the tick is read again rather than guessed
  // at.
  function tick(n, checked) {
    const file = st.file
    if (!file || !file.caps.write || file.conflict) return
    const next = toggleTask(file.draft ?? file.text, n, checked)
    if (next === null) {
      st.notice = 'That task has moved since this note was drawn. Try again.'
      schedule()
      return
    }
    file.draft = next
    file.flash = ''
    schedule()
    save()
  }

  // Save, naming the revision the edit was made against. The host answers
  // what it stored — which may differ from what was sent, because the host
  // stamps provenance — and that is what the page then holds, and its
  // revision is the next save's base. A `409` is somebody else's save in
  // between: nothing is lost, and the person chooses.
  //
  // `over` is the one way to save over a newer version, and it is only ever
  // the `current_revision` the host named in its `409`, after the person has
  // confirmed it. If the file moves again, that save is refused too.
  async function save({ over = null } = {}) {
    const file = st.file
    if (file?.owner?.save) return file.owner.save()
    if (!file || !file.caps.write || (file.view === 'html' && !file.textLoaded)) return
    if (file.renaming) return file.renameFlight.then(() => st.file === file ? save({ over }) : undefined)
    if (file.timer) timers.clear(file.timer)
    file.timer = null
    // One save at a time; a save asked for during one is made when it lands.
    if (file.saving) {
      file.again = true
      return file.inflight
    }
    const text = file.draft ?? file.text
    let done
    file.inflight = new Promise((r) => (done = r))
    file.saving = true
    file.flash = ''
    file.failed = false
    schedule()
    try {
      const got = readOf(await api('PUT', '/files/text', { root: file.root, path: file.path, text, base_revision: over ?? file.revision }))
      if (st.file !== file) return done()
      // What the host stored — not what was sent — is what the page holds and
      // what the field shows. Anything typed while the save was in flight is a
      // newer edit and stays on top of it.
      const stored = got.text
      file.text = stored
      file.saved = stored
      if (file.draft === text) file.draft = stored
      file.revision = got.revision ?? file.revision
      if (got.row) {
        file.row = got.row
        file.caps = capsOf(got.row)
        relist(got.row)
      }
      file.conflict = null
      file.schedule = got.schedule
      file.flash = got.provenance ? 'Saved · the host stamped it' : 'Saved'
      registry.emit('file.changed', { root: file.root, path: file.path, revision: file.revision })
    } catch (err) {
      if (st.file !== file) return done()
      if (err.status === 409) {
        file.conflict = { current: typeof err.body?.current_revision === 'string' ? err.body.current_revision : null, message: err.message, confirming: false }
      } else {
        file.flash = err.message
        file.failed = true
      }
    }
    file.saving = false
    // Keystrokes made while the save was away are the next save, now if one
    // was asked for and after the usual pause if not.
    const again = file.again
    file.again = false
    if (!file.conflict && !file.failed && dirty()) soon(file, again ? 0 : AUTOSAVE_MS)
    schedule()
    done()
  }

  function askOverwrite(yes = true) {
    if (!st.file?.conflict) return
    st.file.conflict.confirming = yes
    schedule()
  }

  // The other side of a conflict: throw this edit away and read what is
  // there. The button says so in its own words.
  function reload() {
    const f = st.file
    if (!f) return
    openFile(f.root, f.path, f.row)
  }

  function setMode(mode) {
    if (!st.file) return
    if (mode === 'edit' && st.file.view === 'html' && !st.file.textLoaded) loadText(st.file)
    else if (mode === 'edit' && st.file.draft === null) st.file.draft = st.file.text
    st.file.mode = mode
    schedule()
  }

  async function create(what, name) {
    const s = st.sheet
    if (!validName(name)) {
      st.sheet = { ...s, error: 'A name, without slashes.' }
      schedule()
      return
    }
    const path = joinPath(st.path, name.trim())
    st.sheet = { ...s, busy: true, error: '' }
    schedule()
    try {
      const body = await api('POST', what === 'folder' ? '/files/folder' : '/files/file', what === 'folder' ? { root: st.root, path } : { root: st.root, path, text: '' })
      st.sheet = null
      await loadFolder()
      const made = isRow(body) ? body : isRow(readOf(body).row) ? readOf(body).row : null
      if (what !== 'folder') onResolveTo?.(made?.root ?? st.root, made?.path ?? path, true, made)
    } catch (err) {
      st.sheet = { ...s, busy: false, error: err.message }
    }
    schedule()
  }

  // The home's two actions. Today's daily note opens, made empty first if it is
  // not there; a new inbox note is made and opened, under the next free name if
  // the minute's is taken. A refusal is the host's sentence on the home.
  async function openDaily(folder, now = new Date()) {
    const path = dailyNotePath(folder, now)
    st.notice = ''
    try {
      const row = await api('GET', `/files/stat?${q({ root: 'vault', path })}`)
      onResolveTo?.('vault', path, true, isRow(row) ? row : null)
    } catch (err) {
      if (err.status !== 404) return homeRefusal(err)
      await makeNote(path)
    }
  }

  async function newInbox(folder, now = new Date()) {
    st.notice = ''
    for (let n = 0; n < 5; n++) {
      const made = await makeNote(inboxNotePath(folder, now, n), { quietConflict: true })
      if (made !== 'taken') return
    }
    homeRefusal(new Error('Every name for a note this minute is taken. Try again in a minute.'))
  }

  async function makeNote(path, { quietConflict = false } = {}) {
    try {
      const body = await api('POST', '/files/file', { root: 'vault', path, text: '' })
      const made = isRow(body) ? body : readOf(body).row
      onResolveTo?.(made?.root ?? 'vault', made?.path ?? path, true, made)
      return 'made'
    } catch (err) {
      if (quietConflict && err.status === 409) return 'taken'
      homeRefusal(err)
      return 'refused'
    }
  }

  function homeRefusal(err) {
    st.notice = err.message || 'The host would not make that note.'
    schedule()
  }

  // What an upload may be, as the host answers it on `/api/limits`. Until it
  // answers nothing is refused here: the host refuses for itself, in its own
  // words, and this only saves a phone from sending bytes that would be.
  function setUploadLimits(limits) {
    const max = limits?.files_upload_max_bytes
    const types = limits?.files_upload_extensions
    st.upload = {
      max: Number.isFinite(max) && max > 0 ? max : null,
      types: Array.isArray(types) ? types.map(extWord).filter(Boolean) : null,
    }
    schedule()
  }

  // Upload what the chooser gave into the folder on screen. One file per
  // request, each with its own progress, answer and failure: a batch is never
  // reported as one success. The folder is the one chosen into, even if the
  // person moves on while the bytes go.
  function upload(chosen) {
    const list = [...(chosen ?? [])]
    if (!st.root || list.length === 0) return
    const sheet = { kind: 'upload', root: st.root, folder: st.path, turn: st.turn, items: list.map((file) => ({ id: ++uploads, file, name: file.name, size: file.size, state: 'pending', loaded: 0, total: file.size, error: '', retry: false, row: null })) }
    st.sheet = sheet
    schedule()
    return Promise.all(sheet.items.map((item) => sendUpload(sheet, item)))
  }

  async function sendUpload(sheet, item) {
    const refusal = uploadRefusal(item, st.upload)
    if (refusal) {
      Object.assign(item, { state: 'failed', error: refusal, retry: false })
      return settled(sheet)
    }
    Object.assign(item, { state: 'uploading', loaded: 0, error: '', retry: false })
    schedule()
    const form = new FormData()
    form.append('file', item.file, item.name)
    try {
      const body = await sendForm(`/api/files/upload?root=${encodeURIComponent(sheet.root)}&path=${encodeURIComponent(sheet.folder)}`, form, (loaded, total) => {
        item.loaded = loaded
        item.total = total
        schedule()
      })
      // The host's row: where the file landed and under what name, which is
      // not the chosen one when that name was taken.
      if (!isRow(body)) throw Object.assign(new Error('The host answered, but not with the file it stored.'), { status: 502 })
      Object.assign(item, { state: 'done', row: body })
    } catch (err) {
      // A refusal of these bytes is the same refusal the second time; a host
      // that could not be reached, or failed, may not be.
      const status = err?.status
      Object.assign(item, { state: 'failed', error: err?.message || 'The upload failed.', retry: !(status >= 400 && status < 500) || status === 408 || status === 429 })
    }
    return settled(sheet)
  }

  function retryUpload(item) {
    const sheet = st.sheet
    if (sheet?.kind !== 'upload' || !sheet.items.includes(item) || item.state !== 'failed' || !item.retry) return
    return sendUpload(sheet, item)
  }

  // Once every file has an answer: the folder chosen into is read again if it
  // is still on screen, and one file that went up alone opens, as the
  // design's does — unless the person has gone somewhere since. Anything else
  // stays on the sheet to be read.
  function settled(sheet) {
    if (sheet.items.some((i) => i.state === 'pending' || i.state === 'uploading')) return schedule()
    const here = st.root === sheet.root && st.path === sheet.folder
    if (here && sheet.items.some((i) => i.state === 'done')) loadFolder()
    const only = sheet.items.length === 1 && sheet.items[0].state === 'done' ? sheet.items[0].row : null
    if (only && here && st.sheet === sheet && st.turn === sheet.turn) {
      st.sheet = null
      onResolveTo?.(only.root, only.path, true, only)
    }
    schedule()
  }

  // An uploaded file, opened from the sheet that lists it.
  function openUploaded(item) {
    if (item?.state !== 'done') return
    st.sheet = null
    onResolveTo?.(item.row.root, item.row.path, true, item.row)
    schedule()
  }

  let revealed = null
  function revealOpen(root = document) {
    const f = st.file
    const key = f ? `${st.root ?? ''}:${st.path ?? ''}|${f.root}:${f.path}` : null
    const folder = [...(root?.querySelectorAll?.('.fs-folder') ?? [])].find((n) => !n.closest?.('.side-pane') && !n.closest?.('.away'))
    const row = key && folder ? (folder.querySelector('.fs-entry.on') ?? folder.querySelector('.setrow.on')) : null
    if (!row?.isConnected) { revealed = null; return }
    if (revealed?.key === key && revealed.row === row) return
    revealed = { key, row }
    row.scrollIntoView?.({ block: 'nearest' })
  }

  return { registry, st, pluginRoot, beginRename, cancelRename, editRename, rename, applyRename, focusRename, revealOpen, shortcutMenu, openShortcut, loadLive, copyLink, resourceMenu, loadRoots, loadFolder, show, openFile, closeFile, edit, save, tick, askOverwrite, reload, setMode, create, dirty, guard, own, q, setUploadLimits, upload, retryUpload, openUploaded, openDaily, newInbox }
}

function extWord(e) {
  return String(e ?? '').trim().replace(/^\./, '').toLowerCase()
}

// Why a chosen file will not be taken, before a byte moves; `''` when it may
// go. Only on the host's own numbers — with none, nothing is refused here.
export function uploadRefusal(item, limits) {
  if (limits?.max && item.size > limits.max) return `${item.name} is ${bytes(item.size)}; an upload is at most ${bytes(limits.max)}.`
  if (limits?.types && !limits.types.includes(extOf(item.name))) return `${item.name} is not a type Files takes.`
  return ''
}

// What the chooser offers: the host's extensions, or anything until it says.
export function uploadAccept(limits) {
  return limits?.types?.length ? limits.types.map((t) => `.${t}`).join(',') : ''
}

// The bytes on the wire without progress, for a page with no
// `XMLHttpRequest`. Its errors carry the host's sentence and status.
async function fetchForm(href, form) {
  const res = await fetch(href, { method: 'POST', body: form, credentials: 'same-origin' })
  let parsed = null
  try {
    parsed = await res.json()
  } catch {}
  if (!res.ok) throw Object.assign(new Error(typeof parsed?.error === 'string' ? parsed.error : `The upload was refused (${res.status}).`), { status: res.status })
  return parsed
}

// ------------------------------------------------------------ the drawing

function renamingHere(files, resource, slot) {
  const s = files.st.renaming
  return s && s.slot === slot && s.resource.root === resource?.root && s.resource.path === resource.path
}

function renameField(files, resource, slot) {
  if (!renamingHere(files, resource, slot)) return null
  const s = files.st.renaming
  if (!s.field) s.field = h('input', {
    class: 'fs-rename-input', type: 'text', value: s.value, autocomplete: 'off', spellcheck: 'false',
    'aria-label': `Rename ${s.resource.name}`, 'aria-describedby': 'fs-rename-help fs-rename-error',
    oninput: e => files.editRename(e.target.value),
    onblur: () => { if (files.st.renaming === s && !s.error) files.rename() },
    onkeydown: e => {
      if (e.key === 'Escape') { e.preventDefault(); files.cancelRename() }
      if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); files.rename() }
    },
  })
  s.field.readOnly = s.busy
  s.field.setAttribute('aria-invalid', s.error ? 'true' : 'false')
  return h('div', { class: `fs-rename-form fs-rename-${slot}`, key: `rename:${resource.root}:${resource.path}`, 'aria-busy': String(s.busy) },
    h('div', { class: 'fs-rename-line' }, kept(s.field),
      h('button', { class: 'iconbtn content-mark', type: 'button', title: 'Save name', 'aria-label': 'Save name', disabled: s.busy, onmousedown: e => e.preventDefault(), onclick: () => files.rename() }, icon.check(18)),
      h('button', { class: 'iconbtn content-mark', type: 'button', title: 'Cancel rename', 'aria-label': 'Cancel rename', disabled: s.busy, onmousedown: e => e.preventDefault(), onclick: () => files.cancelRename() }, icon.close(18))),
    h('div', { class: 'fs-rename-help', id: 'fs-rename-help', text: s.busy ? 'Renaming…' : 'Enter to save · Esc to cancel' }),
    h('div', { class: 'fs-rename-error', id: 'fs-rename-error', role: 'status', 'aria-live': 'polite', text: s.error }))
}

function renameTitle(files, resource, label, className, slot = 'title') {
  return renameField(files, resource, slot) ?? (capsOf(resource).rename
    ? h('button', { class: `${className} fs-rename-start`, type: 'button', title: `Rename ${resource.name}`, 'aria-label': `Rename ${resource.name}`, disabled: files.st.file?.loading && files.st.file.row === resource, onclick: () => files.beginRename(resource, slot) }, h('span', { text: label }), icon.pencil(14))
    : h('div', { class: className, text: label }))
}

function rowButton(row, { on, onclick, key }) {
  const folder = isFolder(row)
  const tile = tileOf(row.name)
  return h(
    'button',
    { class: `setrow${on ? ' on' : ''}`, key, 'aria-current': on ? 'page' : null, onclick },
    folder
      ? h('div', { class: 'icon-tile fs-foldertile' }, icon.folder(18))
      : h('div', { class: 'icon-tile', style: { background: tile.bg, color: tile.fg } }, icon.file(14), h('span', { text: tile.label })),
    h('div', { class: 'fs-words' }, h('div', { class: 'fs-name', text: row.name }), h('div', { class: 'fs-meta', text: metaOf(row) })),
    folder ? icon.chevronRight(10) : null,
  )
}

function openFileMarks(files, go) {
  const open = files.st.file ? `${files.st.file.root}:${files.st.file.path}` : null
  const side = go.side?.()?.visible() && go.side().st.side?.kind === 'file' ? go.side().st.side : null
  const sideKey = side ? `${side.root}:${side.path}` : null
  return (root, path, file = true) => {
    if (!file) return { isMain: false, isSide: false }
    const key = `${root}:${path}`
    const isMain = open === key
    return { isMain, isSide: !isMain && sideKey === key }
  }
}

// Chosen files and folders, in their chosen order, as one card of rows, or the
// empty state that says how to make one.
function homeShortcuts(files, home, go) {
  const list = home?.shortcuts ?? fileShortcuts(null, files.st.roots.shortcuts)
  const marksFor = openFileMarks(files, go)
  return h(
    'div',
    { class: 'fs-shortcuts', key: 'fs:myshortcuts' },
    h('div', { class: 'kicker fs-kicker', text: 'Shortcuts' }),
    list.length === 0
      ? h(
          'div',
          { class: 'card card-group fs-noshort' },
          h('div', { class: 'icon-tile fs-foldertile' }, icon.folder(18)),
          h(
            'div',
            { class: 'fs-words' },
            h('div', { class: 'fs-name', text: 'No shortcuts yet' }),
            h('div', { class: 'fs-meta fs-noshort-how', text: 'Open a file or folder, tap ⋯ and choose Add to shortcuts. It will wait for you here.' }),
          ),
        )
      : h(
          'div',
          { class: 'card card-group' },
          ...list.map((s) => {
            const { isMain, isSide } = marksFor(s.root, s.path, s.kind === 'file')
            return h('div', { class: `fs-entry${isMain ? ' on' : ''}${isSide ? ' in-side' : ''}`, key: `shortcut:${s.root}:${s.path}` }, renameField(files, s, 'shortcut') ?? h(
              'button',
              { class: `setrow ${s.offered ? 'fs-host-shortcut' : 'fs-shortcut'}${isMain ? ' on' : ''}`, key: `my:${s.root}:${s.path}`, 'aria-current': isMain ? 'page' : null, onclick: event => event.altKey && go.side?.()?.available() && s.kind === 'file' ? go.side().openSide({ kind: 'file', root: s.root, path: s.path }) : files.openShortcut(s, go) },
              s.kind === 'file' ? h('div', { class: 'icon-tile', style: { background: tileOf(s.path).bg, color: tileOf(s.path).fg } }, icon.file(14), h('span', { text: tileOf(s.path).label })) : h('div', { class: 'icon-tile fs-foldertile' }, icon.folder(18)),
              h('div', { class: 'fs-words' }, h('div', { class: 'fs-name', text: s.label || nameOf(s.path) || (ROOT_LOOK[s.root]?.name ?? s.root) }), h('div', { class: 'fs-meta', text: [ROOT_LOOK[s.root]?.name ?? s.root, ...splitPath(parentOf(s.path))].join(' / ') })),
              icon.chevronRight(10),
            ), s.kind === 'file' && go.side?.()?.available() ? h('span', { class: 'row-side-action' }, go.side().affordance({ kind: 'file', root: s.root, path: s.path })) : null,
            isSide ? h('span', { class: 'row-side-mark', title: 'Open in side panel', 'aria-label': 'Open in side panel' }, icon.panelRight(13)) : null,
            renamingHere(files, s, 'shortcut') ? null : h('button', { class: 'iconbtn content-mark fs-shortcutmenu', title: `Shortcut actions for ${s.label || nameOf(s.path)}`, 'aria-label': `Shortcut actions for ${s.label || nameOf(s.path)}`, onclick: () => files.shortcutMenu(s, home) }, icon.dots(18)))
          }),
        ),
  )
}

// The home's two actions: a new note in the inbox, today's daily note.
function homeActions(home) {
  if (!home) return null
  const act = (cls, glyph, label, sub, onclick) =>
    h('button', { class: `fs-action ${cls}`, type: 'button', onclick }, h('div', { class: 'fs-actionglyph' }, glyph), h('div', { class: 'fs-name', text: label }), h('div', { class: 'fs-meta', text: sub }))
  return h(
    'div',
    { class: 'fs-actions-home', key: 'fs:actions' },
    act('fs-newinbox', icon.plus(18), 'New inbox note', home.inbox, home.onInbox),
    home.daily !== null && home.daily !== undefined ? act('fs-daily', icon.file(16), 'Open daily note', home.daily, home.onDaily) : null,
  )
}

// The header, the title and the list of one folder — the phone's whole Files
// screen and the desktop's list column. `go(root, path, opts)` is the app's,
// because going somewhere is also writing an address.
export function folderView(files, { go, wide, collapse = null, returnBack = null, home = null }) {
  const { st } = files
  const atRoot = !st.root
  const own = files.pluginRoot?.() ?? null
  const rootRow = st.roots.items.find((r) => r.root === st.root) ?? null
  const rootName = rootRow?.name ?? ROOT_LOOK[st.root]?.name ?? st.root
  const folderRow = st.listing.folder ?? (st.path === '' && rootRow ? { capabilities: rootRow.capabilities } : null)
  const folderCaps = capsOf(folderRow)
  const canAdd = !atRoot && folderCaps.create
  // A plugin's root opened cold, before its plugin has registered: no
  // heading yet (its address, "@<id>", is not a name).
  const coldPlugin = !own && typeof st.root === 'string' && st.root.startsWith('@')
  const title = atRoot ? 'Files' : own ? own.name : coldPlugin ? '' : st.path === '' ? rootName : nameOf(st.path)
  const parent = atRoot ? null : st.path === '' ? 'Files' : parentOf(st.path) === '' ? rootName : nameOf(parentOf(st.path))
  const sub = atRoot || coldPlugin ? '' : own ? (own.desc || '') : st.path === '' ? (rootRow?.desc ?? ROOT_LOOK[st.root]?.desc ?? '') : [rootName, ...splitPath(st.path)].join(' / ')
  const pinned = !atRoot && !own && home?.isShortcut ? home.isShortcut(st.root, st.path) : false

  // The home's header is the Chats header, class for class, so the two tabs
  // start at the same place: no back row above it and no line under it.
  const head = atRoot
    ? h('div', { class: wide ? 'list-head fs-homehead' : 'home-head fs-homehead' }, h('div', { class: 'display', text: 'Files' }), returnBack, collapse)
    : h(
    'div',
    { class: 'fs-head' },
    returnBack,
    parent !== null
      ? h(
          'button',
          { class: 'backbtn fs-back', title: `Back to ${parent}`, onclick: () => go(st.path === '' ? null : st.root, parentOf(st.path)) },
          icon.chevronLeft(20),
          h('span', { text: parent }),
        )
      : null,
    h('div', { class: 'headspace' }),
    home?.toggleShortcut && !own
      ? h('button', { class: 'iconbtn fs-menu', title: 'This folder', 'aria-label': 'This folder', onclick: () => { st.sheet = { kind: 'folder', resource: folderRow, renameSlot: 'folder', name: title, on: pinned, toggle: () => home.toggleShortcut(st.root, st.path) }; go.redraw() } }, icon.dots(18))
      : null,
    canAdd
      ? h('button', { class: 'roundbtn fs-add', title: 'Add to this folder', onclick: () => { st.sheet = { kind: 'add' }; go.redraw() } }, icon.plus(20))
      : !atRoot && !own && !st.listing.loading && !st.listing.error && folderRow
        ? h('span', { class: 'fs-tag', text: 'Read-only' })
        : null,
    collapse,
  )

  const body = []
  if (atRoot) {
    if (!st.roots.loaded) body.push(h('div', { class: 'fs-quiet', text: 'Loading…' }))
    else if (st.roots.error) body.push(errorBox(st.roots.error, st.roots.status, () => files.loadRoots()))
    else if (st.roots.items.length === 0) body.push(h('div', { class: 'fs-quiet', text: 'The host offers no files.' }))
    body.push(homeActions(home))
    for (const r of st.roots.items) {
      body.push(
        h(
          'button',
          { class: 'fs-rootcard', key: `root:${r.root}`, onclick: () => go(r.root, '') },
          h('div', { class: 'fs-blob', style: { background: 'var(--paper-card)' } }, icon.folder(20)),
          h('div', { class: 'fs-rootwords' }, h('div', { class: 'display fs-rootname', text: r.name }), r.desc ? h('div', { class: 'fs-rootdesc', text: r.desc }) : null),
          icon.chevronRight(10),
        ),
      )
    }
    for (const r of files.registry.entries('files.root', {})) {
      body.push(
        h(
          'button',
          { class: 'fs-rootcard', key: `root:@${r.id}`, onclick: () => go(`@${r.id}`, '') },
          h('div', { class: 'fs-blob', style: { background: 'var(--paper-card)' } }, typeof r.icon === 'function' ? r.icon(20) : icon[r.icon]?.(20) ?? icon.folder(20)),
          h('div', { class: 'fs-rootwords' }, h('div', { class: 'display fs-rootname', text: r.name }), r.desc ? h('div', { class: 'fs-rootdesc', text: r.desc }) : null),
          icon.chevronRight(10),
        ),
      )
    }
    if (st.roots.loaded && !st.roots.error) body.push(homeShortcuts(files, home, go))
  } else if (!own && st.root?.startsWith?.('@')) {
    body.push(h('div', { class: 'fs-quiet', 'aria-busy': 'true' }))
  } else if (own) {
    if (st.listing.error) body.push(errorBox(st.listing.error, st.listing.status, () => files.loadFolder()))
    else body.push(files.registry.invoke(own, 'render', { files, go, wide }))
  } else if (st.listing.error) {
    body.push(errorBox(st.listing.error, st.listing.status, () => files.loadFolder()))
  } else if (st.listing.loading && st.listing.rows.length === 0) {
    body.push(h('div', { class: 'fs-quiet', text: 'Loading…' }))
  } else {
    if (st.listing.rows.length === 0) body.push(h('div', { class: 'fs-quiet', text: 'Nothing here yet.' }))
    else if (st.listing.rows.length > 0) {
      const marksFor = openFileMarks(files, go)
      body.push(
        h(
          'div',
          { class: 'card card-group' },
          ...st.listing.rows.map((row) => {
            const { isMain, isSide } = marksFor(row.root, row.path, !isFolder(row))
            return h('div', { class: `fs-entry${isMain ? ' on' : ''}${isSide ? ' in-side' : ''}`, key: `entry:${row.root}:${row.path}` }, renameField(files, row, 'row') ?? rowButton(row, {
              on: isMain,
              key: `row:${row.root}:${row.path}`,
              onclick: event => event.altKey && go.side?.()?.available() && !isFolder(row) ? go.side().openSide({ kind: 'file', root: row.root, path: row.path, row }) : go(row.root, row.path, { isFile: !isFolder(row), row }),
            }), !isFolder(row) && go.side?.()?.available() ? h('span', { class: 'row-side-action' }, go.side().affordance({ kind: 'file', root: row.root, path: row.path, row })) : null,
            isSide ? h('span', { class: 'row-side-mark', title: 'Open in side panel', 'aria-label': 'Open in side panel' }, icon.panelRight(13)) : null,
            renamingHere(files, row, 'row') ? null : h('button', { class: 'iconbtn content-mark fs-rowmenu', title: `Actions for ${row.name}`, 'aria-label': `Actions for ${row.name}`, onclick: () => files.resourceMenu(row, home) }, icon.dots(18)))
          }),
        ),
      )
    }
    if (st.listing.cursor) {
      body.push(
        h('button', {
          class: 'pillbtn content-mark fs-more',
          disabled: st.listing.loading,
          onclick: () => files.loadFolder(true),
          text: st.listing.loading ? 'Loading…' : st.listing.total !== null ? `Show more (${st.listing.rows.length} of ${st.listing.total})` : 'Show more',
        }),
      )
    }
  }

  return h(
    'div',
    { class: `fs-folder${wide ? ' wide' : ''}${atRoot ? ' athome' : ''}`, key: 'fs:folder' },
    head,
    atRoot ? null : h('div', { class: 'fs-titles' }, renameField(files, folderRow, 'folder') ?? h('div', { class: 'display fs-title', text: title }), sub ? h('div', { class: 'fs-sub', text: sub }) : null),
    wide && st.file ? null : noticeLine(files),
    h('div', { class: 'scroll fs-scroll' }, ...body.filter(Boolean)),
  )
}

// Host failures retain their sentence. Text size/type refusals are explained
// by the viewer with its own actions, rather than exposing transport wording.
function errorBox(message, status, retry, extra = null) {
  const lead = status === 401 ? 'The host would not let this page in.' : status === 403 ? 'The host does not allow this here.' : status === 404 ? 'Not found.' : status === 503 ? 'Files is not available from the host right now.' : null
  return h(
    'div',
    { class: 'banner banner-retry', role: 'alert' },
    lead ? h('div', { class: 'fs-errlead', text: lead }) : null,
    h('div', { text: message || 'Something went wrong.' }),
    extra,
    retry ? h('button', { class: 'pillbtn', text: 'Try again', onclick: retry }) : null,
  )
}

function downloadButton(f, label = 'Download', className = 'pillbtn content-mark fs-dlbtn') {
  return h('a', { class: className, href: downloadHref(f.root, f.path), download: nameOf(f.path), text: label })
}

// One file: its header, its name and what it is, and the file — previewed,
// edited or offered as a download.
// Where an editor's save stands, in one quiet word for the header.
export function saveState(f, changed) {
  if (f.conflict) return { word: 'Not saved · changed on the host', tone: 'bad' }
  if (f.saving) return { word: 'Saving…', tone: 'quiet' }
  if (f.failed) return { word: 'Couldn’t save', tone: 'bad' }
  if (changed) return { word: 'Edited', tone: 'quiet' }
  if (f.flash.startsWith('Saved')) return { word: f.flash, tone: 'good' }
  return null
}

export function fileView(files, options) {
  const context = { files, file: files.st.file, options, defaultView: () => builtinFileView(files, options) }
  return files.registry.component('core.files.viewer', context)
}

function builtinFileView(files, { close, closeLabel, wide, field, onLink = null, onTool = null, go = null, returnBack = null, embed = null, mic = null, take = null, notice = null, readAloud = null, audioBar = null, home = null }) {
  const context = { files, file: files.st.file, options: { close, closeLabel, wide, field, onLink, onTool, go, returnBack, embed, mic, take, notice, readAloud, audioBar, home }, slot: 'viewer' }
  const custom = files.registry.entries('files.viewer', context).findLast(entry => !entry.component && entry.slot === 'viewer')
  if (custom) return files.registry.invoke(custom, 'render', context)
  const f = files.st.file
  if (!f) return null
  // A plugin that draws Markdown files (a `files.viewer` in its manifest) may
  // still be starting on the page's first load: hold a quiet, empty place
  // until it has, rather than flash this preview first (2026-11-16).
  if (f.view === 'markdown' && !f.error && files.pluginPending?.('files.viewer')) return h('div', { key: 'files-viewer-waiting', class: 'fs-viewer-waiting', 'aria-busy': 'true' })
  const name = nameOf(f.path)
  const htmlPreview = f.view === 'html' && f.mode === 'preview' && !f.metadataLoading && !f.metadataError && !renamingHere(files, f.row, 'title')
  const editable = f.caps.write && !f.error && !f.loading && (f.view === 'markdown' || f.view === 'text' || (f.view === 'html' && f.textLoaded))
  const changed = files.dirty()
  const tabs =
    (f.view === 'markdown' && !f.error) || f.view === 'html'
      ? segmentedControl({ label: 'File view', value: f.mode, choose: mode => files.setMode(mode),
          options: [{ id: 'preview', label: 'Preview' }, { id: 'edit', label: f.view === 'html' || !editable ? 'Source' : 'Edit' }] })
      : null
  const reason = readOnlyReason(f.row)
  const saved = editable ? saveState(f, changed) : null
  const saveWord = editable && saved ? h('span', { class: `fs-saved ${saved.tone}`, role: 'status', text: saved.word }) : null
  const readOnly = !editable && !f.loading ? h('span', { class: 'fs-tag', title: reason || null, text: 'Read-only' }) : null
  const modeLabel = f.mode === 'preview' ? (editable ? 'Edit' : 'Source') : 'Preview'
  const modeToggle = tabs ? h('button', { class: 'iconbtn fs-mode-toggle', title: modeLabel, 'aria-label': modeLabel, onclick: () => files.setMode(f.mode === 'preview' ? 'edit' : 'preview') }, f.mode === 'preview' ? icon.pencil(18) : icon.file(18)) : null
  // Live HTML has no title/path area, so its folder is reached through ⋯ too.
  const openMenu = () => files.resourceMenu({ ...f.row, root: f.root, path: f.path, kind: 'file' }, home, { openFolder: returnBack ? close : null })
  // The toolbar: in the header on a desktop, and on a phone in a bar of its
  // own under the field, which the viewport keeps above the keyboard.
  const tools =
    editable && f.view === 'markdown' && f.mode === 'edit' && onTool
      ? h(
          'div',
          { class: `fs-tools${wide ? ' inhead' : ''}`, role: 'toolbar', 'aria-label': 'Formatting', key: 'fs-tools' },
          ...TOOLS.map((t) => h('button', { class: `iconbtn fs-tool fs-tool-${t.kind}`, type: 'button', title: t.title, 'aria-label': t.title, text: t.glyph ? null : t.label, disabled: Boolean(take), onmousedown: (e) => e?.preventDefault?.(), onclick: () => onTool(t.kind) }, t.glyph ? icon[t.glyph](16) : null)),
          mic ? h('span', { class: 'fs-toolsep' }) : null,
          mic,
        )
      : null

  const head = files.registry.reduce('files.action', { slot: 'toolbar', file: f, files, defaultView: () => h(
    'div',
    { class: `fs-head fs-edhead${wide && tools ? ' withtools' : ''}${close && !returnBack ? ' folder-back' : ''}` },
    returnBack ?? (close ? h('button', { class: 'backbtn fs-back', title: closeLabel, onclick: close }, icon.chevronLeft(20), h('span', { text: closeLabel })) : null),
    h('div', { class: 'headspace' }),
    wide ? saveWord : null,
    tabs,
    modeToggle,
    readAloud?.glyph,
    files.registry.draw('files.action', { slot: 'action', file: f, files }),
    wide ? readOnly : null,
    h('button', { class: 'iconbtn fs-filemenu', title: 'This file', 'aria-label': 'This file', onclick: openMenu }, icon.dots(18)),
    h('button', { class: 'iconbtn fs-copylink', title: copyLinkLabel(f), 'aria-label': copyLinkLabel(f), disabled: !f.shareLink, onclick: () => files.copyLink(f) }, icon.link(16)),
    f.caps.download ? h('a', { class: 'iconbtn fs-dl', title: 'Download', href: downloadHref(f.root, f.path), download: name }, icon.send(16)) : null,
  ) }, null)
  const toolrow = wide && tools ? h('div', { class: 'fs-toolrow' }, tools) : null

  const liveHead = htmlPreview ? h('div', { class: 'fs-htmlbar fs-livehead' },
    returnBack ?? (close ? h('button', { class: 'iconbtn fs-back', title: `Back to ${closeLabel}`, 'aria-label': `Back to ${closeLabel}`, onclick: close }, icon.chevronLeft(18)) : null),
    h('span', { class: 'fs-htmlmark', text: 'HTML' }),
    renameTitle(files, f.row, name, 'fs-htmlname'),
    h('button', { class: 'pillbtn fs-htmlreload', title: 'Reload page', disabled: f.liveLoading, text: 'Reload', onclick: () => files.loadLive(f) }),
    f.liveUrl ? h('a', { class: 'pillbtn fs-htmlopen', href: f.liveUrl, target: '_blank', rel: 'noopener noreferrer', title: 'Open in new tab', 'aria-label': 'Open in new tab' }, icon.external(16)) : null,
    h('button', { class: 'iconbtn fs-filemenu', title: 'This file', 'aria-label': 'This file', onclick: openMenu }, icon.dots(18)),
  ) : null

  // A supplied editable source opens through the same Files navigation.
  const source =
    f.row?.source && typeof f.row.source.root === 'string' && typeof f.row.source.path === 'string' && go
      ? h(
          'button',
          { class: 'fs-source', onclick: () => go(f.row.source.root, f.row.source.path, { isFile: true }) },
          icon.file(14),
          h('span', { text: `Edit the source: ${ROOT_LOOK[f.row.source.root]?.name ?? f.row.source.root} / ${f.row.source.path}` }),
        )
      : null

  let body
  if (f.loading && !htmlPreview) body = h('div', { class: 'fs-quiet', text: 'Opening…' })
  else if (f.error && !htmlPreview) {
    const limited = f.status === 415 || f.status === 413
    const size = f.errorBody?.size, max = f.errorBody?.max
    const detail = f.status === 415 ? 'This file can’t be shown as text.'
      : Number.isFinite(size) && Number.isFinite(max) ? `This file is ${bytes(size)}. The text view supports up to ${bytes(max)}.`
      : 'This file is larger than the text view supports.'
    const extra =
      limited
        ? h('div', { class: 'fs-error-detail' }, h('p', { text: detail }),
            f.view === 'html' ? h('button', { class: 'fs-error-action', text: 'Back to preview', onclick: () => files.setMode('preview') }) : null,
            f.caps.download ? downloadButton(f, 'Download', 'fs-error-action fs-dlbtn') : null)
        : null
    body = errorBox(limited ? f.view === 'html' ? 'Source unavailable' : 'Text view unavailable' : f.error, f.status, limited ? null : () => files.openFile(f.root, f.path, f.row, { mode: f.mode }), extra)
  } else if (f.view === 'image' && f.blobUrl) body = h('div', { class: 'fs-media' }, h('img', { class: 'fs-img', src: f.blobUrl, alt: name }))
  else if (f.view === 'pdf' && f.blobUrl)
    body = h(
      'div',
      { class: 'fs-pdf' },
      // No scripts and no same-origin: a browser that will not run its own
      // PDF viewer in a sandbox shows nothing here, and the download beside it
      // is the way in.
      h('iframe', { class: 'fs-pdfframe', sandbox: '', referrerpolicy: 'no-referrer', title: `Preview of ${name}`, src: f.blobUrl }),
      h('div', { class: 'fs-note' }, h('span', { text: 'If the preview stays blank, your browser will not show PDFs in a sandbox. ' }), downloadButton(f, 'Download the PDF')),
    )
  else if (f.view === 'image' || f.view === 'pdf' || f.view === 'other')
    body = h(
      'div',
      { class: 'fs-media fs-stripes' },
      h('span', { class: 'fs-chip', text: `${tileOf(name).label} file` }),
      f.blobError ? h('div', { class: 'fs-quiet', text: f.blobError }) : null,
      f.caps.download ? downloadButton(f) : h('div', { class: 'fs-quiet', text: 'The host does not offer this file for download.' }),
    )
  else if (f.mode === 'preview' && f.view === 'markdown')
    body = h(
      'div',
      { class: 'fs-preview md', key: `md:${f.path}` },
      ...markdown(f.draft ?? f.text, { from: { root: f.root, path: f.path }, onLink, anchors: true, vault: true, onTask: f.caps.write && !f.conflict ? (n, checked) => files.tick(n, checked) : null, embed }),
    )
  else if (f.mode === 'preview' && f.view === 'html')
    body = f.liveLoading ? h('div', { class: 'fs-quiet', text: 'Opening preview…' }) : h(
      'div',
      { class: 'fs-htmlframe' },
      h('iframe', { class: 'fs-iframe', key: `html:${f.liveUrl ?? 'static'}:${f.liveReload ?? 0}:${f.revision}`, sandbox: f.liveUrl ? 'allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads' : '', referrerpolicy: 'no-referrer', title: `Preview of ${name}`, src: f.liveUrl ?? `${previewHref(f.root, f.path)}&rev=${encodeURIComponent(f.revision ?? '')}` }),
      f.liveError ? h('div', { class: 'fs-note', role: 'status', text: `${f.liveError} Showing a static preview.` }) : null,
      changed ? h('div', { class: 'fs-note', text: 'This is the saved version. Save to see your changes here.' }) : null,
    )
  else body = h('div', { class: `fs-editwrap${take ? ' taking' : ''}` }, field(f, { readOnly: !editable, mono: f.view !== 'markdown' }), take, wide ? null : tools)

  return h(
    'div',
    { class: `fs-file${wide ? ' wide' : ''}${htmlPreview ? ' html-live' : ''}`, key: `fs:file:${f.root}:${f.path}` },
    liveHead ?? head,
    audioBar,
    htmlPreview ? null : h(
      'div',
      { class: 'fs-titles' },
      renameTitle(files, f.row, f.view === 'markdown' ? name.replace(/\.(?:md|markdown)$/i, '') : name, 'display fs-edtitle'),
      returnBack && close ? h('button', { class: 'fs-location', title: 'Open folder', 'aria-label': `Open folder: ${closeLabel}`, onclick: close }, icon.folder(14), h('span', { text: `${ROOT_LOOK[f.root]?.name ?? f.root}${parentOf(f.path) ? ` / ${parentOf(f.path)}` : ''}` })) : null,
      h('div', { class: 'fs-sub', text: metaOf(f.row) }),
      wide ? null : h('div', { class: 'fs-filestate' }, saveWord, readOnly),
      !editable && !f.loading && reason ? h('div', { class: 'fs-reason', text: reason }) : null,
      source,
    ),
    // On a desktop the bar sits between the title and the text it acts on.
    toolrow,
    readAloud?.player,
    noticeLine(files),
    notice,
    f.schedule && !changed && !f.saving ? h('div', { class: `fs-schedule-save${['broken', 'refused'].includes(f.schedule.state) ? ' bad' : ''}`, role: 'status' },
      h('p', { text: f.schedule.state === 'live' ? `Saved · runs ${f.schedule.words}` : `Saved · ${f.schedule.state_words}` }),
      f.schedule.problem?.line ? h('code', { text: `Line ${f.schedule.problem.line} · ${f.schedule.problem.text}` }) : null) : null,
    f.conflict ? conflictBox(files, f) : null,
    f.failed && f.flash ? h('div', { class: 'banner banner-retry', role: 'alert', text: f.flash }) : null,
    body,
  )
}

// A fade only at edges with more table to reveal. Observe both the viewport
// and its table so fonts, images and pane resizes update the hint as well.
let tableObserver = null
function tableEdges(wrap) {
  const left = wrap.scrollLeft > 1
  const right = wrap.scrollWidth - wrap.clientWidth - wrap.scrollLeft > 1
  wrap.setAttribute('data-edges', left && right ? 'both' : left ? 'left' : right ? 'right' : 'none')
  if (left || right) {
    wrap.setAttribute('tabindex', '0')
    wrap.setAttribute('role', 'region')
    wrap.setAttribute('aria-label', 'Table, scroll horizontally')
  } else {
    wrap.removeAttribute('tabindex')
    wrap.removeAttribute('role')
    wrap.removeAttribute('aria-label')
  }
}

export function fitPreviewTables(root) {
  if (!tableObserver && globalThis.ResizeObserver) tableObserver = new ResizeObserver((entries) => {
    for (const { target } of entries) tableEdges(target.tagName === 'TABLE' ? target.parentElement : target)
  })
  tableObserver?.disconnect()
  for (const wrap of root.querySelector?.('.fs-preview')?.querySelectorAll?.('.tablewrap') ?? []) {
    tableEdges(wrap)
    on(wrap, 'scroll', () => tableEdges(wrap))
    tableObserver?.observe(wrap)
    if (wrap.firstElementChild) tableObserver?.observe(wrap.firstElementChild)
  }
}

// What a link that went nowhere said, where the person is.
function noticeLine(files) {
  return files.st.notice && files.st.sheet?.kind !== 'resource' ? h('div', { class: 'listnotice inline', role: 'status', text: files.st.notice }) : null
}

// A save the host refused because the file moved under it — the vault's sync,
// or an agent writing the same note. Nothing is saved over it until the person
// says which version is kept: theirs (the edit goes, the newer file is read)
// or mine (written over the newer version, and only that version: if it moves
// again, this is asked again).
function conflictBox(files, f) {
  const c = f.conflict
  return h(
    'div',
    { class: 'banner banner-retry fs-conflict', role: 'alert' },
    h('div', { class: 'fs-errlead', text: 'This note changed on the host since you opened it.' }),
    h('div', { text: 'Your edit is kept here and has not been saved. Keep yours to write it over the newer version, or take theirs to drop your edit and read what is there now.' }),
    c.message ? h('div', { class: 'fs-hostline', text: c.message }) : null,
    h(
      'div',
      { class: 'fs-actions' },
      c.current ? h('button', { class: 'primary fs-keepmine', disabled: f.saving, text: f.saving ? 'Saving…' : 'Keep mine', onclick: () => files.save({ over: c.current }) }) : null,
      h('button', { class: 'pillbtn fs-taketheirs', disabled: f.saving, text: 'Take theirs', onclick: () => files.reload() }),
    ),
  )
}

// The "+" sheet, the name sheet, a choice between files, and the question an
// unsaved edit asks before it is left.
// One file on the upload sheet: going, with how far; stored, under the name
// the host gave it and opened by a tap; or refused, in the host's words, with a
// way to try again where trying again could help.
function uploadRow(files, item, close) {
  const stored = item.state === 'done' ? item.row : null
  const name = stored?.name ?? item.name
  const tile = tileOf(name)
  const pct = item.total > 0 ? Math.min(100, Math.round((item.loaded / item.total) * 100)) : 0
  const meta =
    item.state === 'done'
      ? `${bytes(stored.size ?? item.size)} · ${stored.renamed || stored.name !== item.name ? `saved as ${stored.name}` : 'Uploaded'}`
      : item.state === 'failed'
        ? item.error
        : item.state === 'uploading'
          ? `${bytes(item.size)} · ${pct}%`
          : `${bytes(item.size)} · Waiting`
  const words = h('div', { class: 'fs-words' }, h('div', { class: 'fs-name', text: name }), h('div', { class: `fs-meta${item.state === 'failed' ? ' fs-uploaderror' : ''}`, role: item.state === 'failed' ? 'alert' : null, text: meta }))
  const tileNode = h('div', { class: 'icon-tile', style: { background: tile.bg, color: tile.fg } }, h('span', { text: tile.label }))
  const bar = item.state === 'uploading' ? h('div', { class: 'fs-progress' }, h('div', { class: 'fs-progressbar', style: { width: `${pct}%` } })) : null
  const key = `up:${item.id}`
  if (stored) {
    return h('button', { class: 'sheetrow fs-uploaded', key, onclick: () => files.openUploaded(item) }, tileNode, words, icon.chevronRight(10))
  }
  return h(
    'div',
    { class: `sheetrow fs-uploading${item.state === 'failed' ? ' failed' : ''}`, key },
    tileNode,
    h('div', { class: 'fs-uploadwords' }, words, bar),
    item.state === 'failed' && item.retry ? h('button', { class: 'pillbtn content-mark fs-retry', text: 'Retry', onclick: () => { files.retryUpload(item); close.redraw?.() } }) : null,
  )
}

export function addSheet(files, { close, nameField, nameValue = () => nameField.value, chooseUpload = null, uploadChooser = null }) {
  const s = files.st.sheet
  // A save on the way out is quiet: the header already says "Saving…".
  if (!s || s.kind === 'saving') return null
  if (s.kind === 'leave') {
    return [
      h('div', { class: 'display sheettitle', text: 'Leave without saving?' }),
      h('div', { class: 'fs-sub fs-leavetext', text: `${s.name} has changes that are not saved.` }),
      h(
        'div',
        { class: 'fs-actions' },
        h('button', { class: 'primary fs-keepediting', text: 'Keep editing', onclick: close }),
        h('button', { class: 'pillbtn dangertext fs-leave', text: 'Leave without saving', onclick: () => s.proceed() }),
      ),
    ]
  }
  if (s.kind === 'folder' || s.kind === 'resource') {
    return [
      h('div', { class: 'display sheettitle', text: s.name }),
      s.resource && !isFolder(s.resource) && files.side?.()?.available() ? h('button', { class: 'sheetrow', onclick: () => { const row = s.resource; files.st.sheet = null; files.side().openSide({ kind: 'file', root: row.root, path: row.path, row }) } }, icon.panelRight(18), 'Open to the side') : null,
      capsOf(s.resource).rename ? h('button', { class: 'sheetrow fs-menu-rename', onclick: () => files.beginRename(s.resource, s.renameSlot ?? 'row') }, icon.pencil(18), h('span', { text: 'Rename' })) : null,
      s.openFolder ? h('button', { class: 'sheetrow fs-menu-folder', onclick: () => { files.st.sheet = null; s.openFolder() } }, icon.folder(18), h('span', { text: 'Open folder' })) : null,
      s.viewSource ? h('button', { class: 'sheetrow fs-menu-source', onclick: () => { files.st.sheet = null; s.viewSource() } }, icon.code(18), h('span', { text: 'View source' })) : null,
      s.resource?.capabilities?.download ? h('a', { class: 'sheetrow fs-menu-download', href: downloadHref(s.resource.root, s.resource.path), download: s.name }, icon.send(18), h('span', { text: 'Download' })) : null,
      s.resource ? h('button', { class: 'sheetrow fs-menu-copy', disabled: !s.resource.shareLink, onclick: () => files.copyLink(s.resource) }, icon.link(18), h('span', { text: copyLinkLabel(s.resource) })) : null,
      s.resource && files.st.notice ? h('div', { class: 'listnotice inline', role: 'status', text: files.st.notice }) : null,
      h(
        'button',
        { class: 'sheetrow fs-shortcuttoggle', onclick: () => { s.toggle(); files.st.sheet = null; close.redraw?.() } },
        s.resource && !isFolder(s.resource) ? h('div', { class: 'icon-tile', style: { background: tileOf(s.name).bg, color: tileOf(s.name).fg } }, icon.file(18), h('span', { text: tileOf(s.name).label })) : h('div', { class: 'icon-tile fs-foldertile' }, icon.folder(18)),
        h('div', { class: 'fs-words' }, h('div', { class: 'fs-name', text: s.on ? 'Remove from shortcuts' : 'Add to shortcuts' }), h('div', { class: 'fs-meta', text: s.on ? 'It leaves the Files home; the original stays.' : 'It appears on the Files home, after the others.' })),
      ),
    ]
  }
  if (s.kind === 'add') {
    const row = (what, label, sub, tile) =>
      h(
        'button',
        { class: 'sheetrow', onclick: () => { files.st.sheet = { kind: 'name', what, name: '', error: '' }; close.redraw() } },
        h('div', { class: 'icon-tile', style: { background: tile[1], color: tile[2] } }, h('span', { text: tile[0] })),
        h('div', { class: 'fs-words' }, h('div', { class: 'fs-name', text: label }), h('div', { class: 'fs-meta', text: sub })),
      )
    return [
      h('div', { class: 'display sheettitle', text: 'Add to this folder' }),
      row('note', 'New note', 'A Markdown page', ['MD', 'var(--paper-card)', 'var(--paper-ink-2)']),
      row('text', 'New text file', 'Plain text', ['TXT', 'var(--paper-card)', 'var(--paper-ink-3)']),
      row('folder', 'New folder', 'Keep things together', ['▭', 'var(--paper-card)', 'var(--paper-ink-2)']),
      // The browser's chooser is in the sheet because a browser opens only one
      // that is on the page; it is never drawn.
      chooseUpload
        ? h(
            'button',
            { class: 'sheetrow fs-upload', onclick: chooseUpload },
            h('div', { class: 'icon-tile', style: { background: 'var(--paper-card)', color: 'var(--paper-ink-2)' } }, h('span', { text: '↑' })),
            h('div', { class: 'fs-words' }, h('div', { class: 'fs-name', text: 'Upload' }), h('div', { class: 'fs-meta', text: 'Photos, PDFs, anything from your phone' })),
          )
        : null,
      uploadChooser,
    ]
  }
  if (s.kind === 'upload') {
    const going = s.items.some((i) => i.state === 'pending' || i.state === 'uploading')
    const failed = s.items.filter((i) => i.state === 'failed').length
    const where = [ROOT_LOOK[s.root]?.name ?? s.root, ...splitPath(s.folder)].join(' / ')
    return [
      h('div', { class: 'display sheettitle', text: going ? 'Uploading' : failed ? (failed === s.items.length ? 'Not uploaded' : `${failed} of ${s.items.length} not uploaded`) : 'Uploaded' }),
      h('div', { class: 'fs-sub', text: `Into ${where}` }),
      ...s.items.map((item) => uploadRow(files, item, close)),
      going ? null : h('button', { class: 'primary fs-uploaddone', text: 'Done', onclick: close }),
    ]
  }
  if (s.kind === 'name') {
    const label = s.what === 'folder' ? 'New folder' : s.what === 'note' ? 'New note' : 'New text file'
    const ext = s.what === 'note' ? '.md' : s.what === 'text' ? '.txt' : ''
    const submit = () => {
      let name = (nameValue() ?? '').trim()
      if (ext && name && !extOf(name)) name += ext
      files.create(s.what === 'folder' ? 'folder' : 'file', name)
    }
    return [
      h('div', { class: 'display sheettitle', text: label }),
      h('div', { class: 'fs-sheetfield' }, nameField),
      s.error ? h('div', { class: 'banner banner-retry', role: 'alert', text: s.error }) : null,
      h('button', { class: 'primary', disabled: s.busy, text: s.busy ? 'Creating…' : 'Create', onclick: submit }),
    ]
  }
  if (s.kind === 'choose') {
    const more = s.total > s.candidates.length
    // A bare name can be a folder and a note at once, and a folder among the
    // candidates is drawn as the folder it is and opens as one.
    const anyFolder = s.candidates.some(isFolder)
    return [
      h('div', { class: 'display sheettitle', text: anyFolder ? 'Which one?' : 'Which file?' }),
      h('div', { class: 'fs-sub', text: `“${s.ref}” matches ${s.total} ${anyFolder ? 'places' : 'files'}${more ? `; the ${s.candidates.length} nearest the top are shown` : ''}.` }),
      ...s.candidates.map((m) =>
        h(
          'button',
          { class: 'sheetrow fs-candidate', key: `cand:${m.root}:${m.path}`, onclick: () => s.pick(m) },
          isFolder(m)
            ? h('div', { class: 'icon-tile fs-foldertile' }, icon.folder(18))
            : h('div', { class: 'icon-tile', style: { background: tileOf(m.name).bg, color: tileOf(m.name).fg } }, h('span', { text: tileOf(m.name).label })),
          h('div', { class: 'fs-words' }, h('div', { class: 'fs-name', text: m.name }), h('div', { class: 'fs-meta', text: [ROOT_LOOK[m.root]?.name ?? m.root, ...splitPath(parentOf(m.path))].join(' / ') })),
        ),
      ),
      s.truncated ? h('div', { class: 'fs-quiet', text: 'The search was cut short; there may be others.' }) : null,
    ]
  }
  return null
}
