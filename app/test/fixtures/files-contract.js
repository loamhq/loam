// The host's Files contract, as fixtures: rows in the one flat shape every
// Files answer uses, and the answers the routes give. Every shape here is the
// verified contract (README, "Files"), not a guess — a test that wants a
// different shape is testing something the host does not send.

export const USER = { read: true, download: true, write: true, create: false }
export const USER_BIN = { read: true, download: true, write: false, create: false }
export const RO = { read: true, download: true, write: false, create: false }
export const FOLDER_W = { read: true, download: false, write: false, create: true }
export const FOLDER_RO = { read: true, download: false, write: false, create: false }

const enc = (path) => path.split('/').filter(Boolean).map(encodeURIComponent).join('/')

const TYPES = { md: 'text/markdown', txt: 'text/plain', html: 'text/html', png: 'image/png', pdf: 'application/pdf', svg: 'image/svg+xml', json: 'application/json', css: 'text/css' }

export function row(root, path, { kind, size = 100, authority = 'user', capabilities, source, modified = '2026-10-01T10:00:00Z' } = {}) {
  const name = path === '' ? { vault: 'Vault', spaces: 'AI Spaces', system: 'System' }[root] : path.split('/').at(-1)
  const k = kind ?? (/\.[a-z0-9]+$/i.test(name) ? 'file' : 'folder')
  const ext = (/\.([a-z0-9]+)$/i.exec(name)?.[1] ?? '').toLowerCase()
  const r = {
    root,
    path,
    name,
    kind: k,
    size: k === 'folder' ? null : size,
    modified,
    media_type: k === 'folder' ? null : TYPES[ext] ?? 'application/octet-stream',
    authority,
    capabilities: capabilities ?? (k === 'folder' ? (root === 'vault' && authority === 'user' ? FOLDER_W : FOLDER_RO) : authority === 'user' && ['md', 'txt', 'html', 'json', 'css'].includes(ext) ? USER : RO),
    link: `/files/${root}${path ? `/${enc(path)}` : ''}`,
  }
  if (source) r.source = source
  return r
}

export function rootsAnswer() {
  return {
    roots: [
      { root: 'vault', name: 'Vault', description: 'Your notes, shared with your agents.', link: '/files/vault', capabilities: { read: true, download: false, write: false, create: true } },
      { root: 'spaces', name: 'AI Spaces', description: 'One home per agent.', link: '/files/spaces', capabilities: { read: true, download: false, write: false, create: false } },
      { root: 'system', name: 'System', description: 'The host’s own records.', link: '/files/system', capabilities: { read: true, download: false, write: false, create: false } },
    ],
    shortcuts: [
      { label: 'System notes', resource: row('vault', '00_System') },
      { label: 'App', resource: row('vault', 'Projects/App') },
    ],
  }
}

export function listAnswer(folder, entries, { cursor = 0, next = null, total = entries.length } = {}) {
  return { folder, entries, total, cursor, next_cursor: next, truncated: false }
}

export function readAnswer(r, text, revision) {
  return { ...r, size: Buffer.byteLength(text), text, revision }
}

// Real first bytes of each type, so a magic check has something true to read.
export const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52])
export const PDF = Buffer.from('%PDF-1.4\n%fake\n')

// The host's limits answer, as far as uploads read it (`GET /v1/limits`).
export const UPLOAD_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.heic', '.heif', '.pdf', '.md', '.txt', '.csv', '.json']
export const limitsAnswer = () => ({ attachment_max_bytes: 10485760, files_upload_max_bytes: 10485760, files_upload_extensions: UPLOAD_EXTENSIONS })

// `POST /v1/files/upload` → `201`: the stored file's ordinary row plus four
// fields. The verbatim end-to-end answer the host gave, revision shortened.
export const UPLOAD_ANSWER_REAL = { status: 201, root: 'vault', path: 'Uploads/IMG 2041.png', name: 'IMG 2041.png', kind: 'file', size: 20495, media_type: 'image/png', authority: 'user', capabilities: { read: true, download: true, write: false, create: false }, link: '/files/vault/Uploads/IMG%202041.png', revision: 'sha256:e8d6', requested_name: 'IMG 2041.png', renamed: false, provenance: false }

export function uploadAnswer(root, folder, stored, requested, size, { provenance = false, revision = 'sha256:up' } = {}) {
  const path = folder ? `${folder}/${stored}` : stored
  return { status: 201, ...row(root, path, { size, capabilities: /\.(md|txt|json|csv)$/i.test(stored) ? USER : USER_BIN }), revision, requested_name: requested, renamed: stored !== requested, provenance }
}

// The host's refusals, each `{error, …}`.
export const UPLOAD_REFUSALS = {
  readOnly: [403, { error: 'This folder is read-only.', authority: 'runtime' }],
  unsafeName: [400, { error: 'That name is not one Files stores.' }],
  type: [415, { error: 'Files does not take that type.', types: UPLOAD_EXTENSIONS }],
  tooBig: [413, { error: 'The file is over 10 MiB.', size: 10485761, max: 10485760 }],
  tooBigUnread: [413, { error: 'The file is over 10 MiB.' }],
  taken: [409, { error: 'There are already 999 copies of that name here.' }],
}
