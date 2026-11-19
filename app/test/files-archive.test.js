import test from 'node:test'
import http from 'node:http'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import path from 'node:path'

import { fakeBroker, appAgainst, closing, until } from './harness.js'
import { installDom } from './fakedom.js'
import { find, all, text } from './browser.js'
import { createFiles, folderView, addSheet } from '../public/files.js'
import { shouldExclude, archiveFilename } from '../src/archive.js'
import { row, rootsAnswer, listAnswer, PNG } from './fixtures/files-contract.js'

const execFileAsync = promisify(execFile)
const json = (res, status, body) => {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}

async function inspectZip(buffer) {
  const dir = await mkdtemp(path.join(process.env.TMPDIR || '/tmp', 'loam-zip-test-'))
  const file = path.join(dir, 'archive.zip')
  try {
    await writeFile(file, buffer)
    const { stdout } = await execFileAsync('python3', [
      '-c',
      `import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1], 'r') as z:
  bad = z.testzip()
  out = {'bad': bad, 'entries': [{ 'name': i.filename, 'size': i.file_size, 'data': z.read(i.filename).decode('latin1') } for i in z.infolist()]}
print(json.dumps(out))`,
      file,
    ])
    return JSON.parse(stdout)
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
}

function treeBroker() {
  const vaultEntries = {
    '': [
      row('vault', 'Notes', { kind: 'folder' }),
      row('vault', 'EmptyFolder', { kind: 'folder' }),
      row('vault', '.git', { kind: 'folder' }),
      row('vault', 'README.md', { size: 12 }),
      row('vault', '.env', { size: 8 }),
      row('vault', 'secret.key', { size: 9 }),
      row('vault', 'credentials.json', { size: 10 }),
      row('vault', 'id_rsa', { size: 11 }),
      row('vault', 'link-out', { kind: 'symlink', size: 0 }),
    ],
    'Notes': [
      row('vault', 'Notes/plan.md', { size: 15 }),
      row('vault', 'Notes/pic.png', { size: PNG.length }),
      row('vault', 'Notes/Sub', { kind: 'folder' }),
      row('vault', 'Notes/auth.json', { size: 5 }),
      row('vault', 'Notes/token', { size: 5 }),
    ],
    'Notes/Sub': [
      row('vault', 'Notes/Sub/deep.txt', { size: 9 }),
      row('vault', 'Notes/Sub/.env.local', { size: 5 }),
    ],
    'EmptyFolder': [],
    '.git': [row('vault', '.git/config', { size: 10 })],
  }
  const spacesEntries = {
    '': [
      row('spaces', 'scout', { kind: 'folder' }),
      row('spaces', 'commons', { kind: 'folder' }),
    ],
    'scout': [
      row('spaces', 'scout/soul.md', { size: 11 }),
      row('spaces', 'scout/logs', { kind: 'folder' }),
      row('spaces', 'scout/run.log', { size: 4 }),
      row('spaces', 'scout/out.pem', { size: 4 }),
    ],
    'scout/logs': [row('spaces', 'scout/logs/debug.txt', { size: 5 })],
    'commons': [row('spaces', 'commons/shared.md', { size: 11 })],
  }
  const files = {
    'vault:README.md': Buffer.from('# Vault root'),
    'vault:Notes/plan.md': Buffer.from('# Plan\n\nHello.'),
    'vault:Notes/pic.png': PNG,
    'vault:Notes/Sub/deep.txt': Buffer.from('deep note'),
    'spaces:scout/soul.md': Buffer.from('# Scout soul'),
    'spaces:commons/shared.md': Buffer.from('# Commons\n'),
  }
  const treeFor = (root) => (root === 'spaces' ? spacesEntries : vaultEntries)
  return fakeBroker({
    'GET /v1/files/roots': (req, res) => json(res, 200, rootsAnswer()),
    'GET /v1/files/stat': (req, res, url) => {
      const root = url.searchParams.get('root') || 'vault'
      const p = (url.searchParams.get('path') || '').replace(/^\/+|\/+$/g, '')
      const tree = treeFor(root)
      if (!p) return json(res, 200, row(root, '', { kind: 'folder' }))
      if (p in tree) return json(res, 200, row(root, p, { kind: 'folder' }))
      if (`${root}:${p}` in files) return json(res, 200, row(root, p, { size: files[`${root}:${p}`].length }))
      return json(res, 404, { error: `nothing at ${root}/${p}` })
    },
    'GET /v1/files/list': (req, res, url) => {
      const root = url.searchParams.get('root') || 'vault'
      const p = (url.searchParams.get('path') || '').replace(/^\/+|\/+$/g, '')
      const cursor = url.searchParams.get('cursor')
      const tree = treeFor(root)
      if (!(p in tree)) return json(res, 404, { error: `nothing at ${root}/${p}` })
      const allEntries = tree[p]
      // Exercise pagination when cursor/limit are used on vault root.
      if (root === 'vault' && p === '' && !cursor) {
        return json(res, 200, { ...listAnswer(row(root, p, { kind: 'folder' }), allEntries.slice(0, 4)), next_cursor: 'page2' })
      }
      if (root === 'vault' && p === '' && cursor === 'page2') {
        return json(res, 200, listAnswer(row(root, p, { kind: 'folder' }), allEntries.slice(4)))
      }
      return json(res, 200, listAnswer(row(root, p, { kind: 'folder' }), allEntries))
    },
    'GET /v1/files/download': (req, res, url) => {
      const root = url.searchParams.get('root') || 'vault'
      const p = (url.searchParams.get('path') || '').replace(/^\/+|\/+$/g, '')
      const data = files[`${root}:${p}`]
      if (!data) return json(res, 404, { error: `nothing at ${root}/${p}` })
      res.writeHead(200, { 'content-type': 'application/octet-stream' })
      res.end(data)
    },
  })
}

test('shouldExclude filters .git, secrets, symlinks, and spaces churn while keeping content and .obsidian', () => {
  assert.equal(shouldExclude('vault', ['.git', 'config'], 'file'), true)
  assert.equal(shouldExclude('vault', ['Notes', '.git', 'HEAD'], 'file'), true)
  assert.equal(shouldExclude('vault', ['.stversions', 'old.md'], 'file'), true)
  for (const name of ['.env', '.env.local', 'prod.env', 'key.pem', 'priv.key', 'cert.p12', 'id_rsa', 'id_ed25519', 'auth.json', 'credentials.json', '.credentials.json', 'token', 'app.token', '.netrc', '.git-credentials', 'secrets.json']) {
    assert.equal(shouldExclude('vault', [name], 'file'), true, name)
  }
  assert.equal(shouldExclude('vault', ['Notes', 'link'], 'symlink'), true)
  assert.equal(shouldExclude('spaces', ['scout', 'logs', 'a.txt'], 'file'), true)
  assert.equal(shouldExclude('spaces', ['scout', 'cache', 'b'], 'file'), true)
  assert.equal(shouldExclude('spaces', ['scout', 'out.log'], 'file'), true)
  assert.equal(shouldExclude('vault', ['.obsidian', 'app.json'], 'file'), false)
  assert.equal(shouldExclude('vault', ['Notes', 'plan.md'], 'file'), false)
  assert.equal(shouldExclude('spaces', ['scout', 'soul.md'], 'file'), false)
  assert.equal(archiveFilename('vault', ''), 'vault.zip')
  assert.equal(archiveFilename('spaces', ''), 'ai-spaces.zip')
  assert.equal(archiveFilename('vault', '40_PROJECTS/Loam'), 'Loam.zip')
})

test('archive route refuses requests without owner login, cross-origin requests, content host, system root, and bad paths', async (t) => {
  const broker = await treeBroker()
  const app = await appAgainst(broker, { FILES_CONTENT_ORIGIN: 'https://content.example.com' })
  t.after(closing(broker, app))

  // No Remote-User header -> 403 refused.
  const noLogin = await fetch(`${app.url}/api/files/archive?root=vault`)
  assert.equal(noLogin.status, 403)
  assert.match((await noLogin.json()).error, /Sign in/)

  const noLoginCheck = await fetch(`${app.url}/api/files/archive?root=vault&check=1`)
  assert.equal(noLoginCheck.status, 403)

  const noLoginHead = await fetch(`${app.url}/api/files/archive?root=spaces`, { method: 'HEAD' })
  assert.equal(noLoginHead.status, 403)

  // Cross-site request even with header -> 403.
  const cross = await fetch(`${app.url}/api/files/archive?root=vault`, {
    headers: { 'Remote-User': 'sam', 'sec-fetch-site': 'cross-site' },
  })
  assert.equal(cross.status, 403)

  // Content host (shared preview origin) cannot reach /api/files/archive -> 404.
  const onContentStatus = await new Promise((resolve, reject) => {
    const u = new URL(app.url)
    const req = http.request({ host: u.hostname, port: u.port, path: '/api/files/archive?root=vault', headers: { Host: 'content.example.com', 'Remote-User': 'sam' } }, (res) => { res.resume(); resolve(res.statusCode) })
    req.on('error', reject)
    req.end()
  })
  assert.equal(onContentStatus, 404)

  // System root, invalid root, and traversal paths are refused.
  const authHeaders = { 'Remote-User': 'sam' }
  assert.equal((await fetch(`${app.url}/api/files/archive?root=system`, { headers: authHeaders })).status, 404)
  assert.equal((await fetch(`${app.url}/api/files/archive?root=other`, { headers: authHeaders })).status, 400)
  assert.equal((await fetch(`${app.url}/api/files/archive?root=vault&path=../secret`, { headers: authHeaders })).status, 400)
  assert.equal((await fetch(`${app.url}/api/files/archive?root=vault&path=.git`, { headers: authHeaders })).status, 404)
  assert.equal((await fetch(`${app.url}/api/files/archive?root=vault&path=README.md`, { headers: authHeaders })).status, 400)
})

test('archive route streams a valid zip excluding .git, secrets, symlinks, and spaces churn', async (t) => {
  const broker = await treeBroker()
  const app = await appAgainst(broker)
  t.after(closing(broker, app))
  const headers = { 'Remote-User': 'sam' }

  const vaultRes = await fetch(`${app.url}/api/files/archive?root=vault`, { headers })
  assert.equal(vaultRes.status, 200)
  assert.equal(vaultRes.headers.get('content-type'), 'application/zip')
  assert.equal(vaultRes.headers.get('content-disposition'), "attachment; filename*=UTF-8''vault.zip")
  assert.equal(vaultRes.headers.get('x-content-type-options'), 'nosniff')
  assert.equal(vaultRes.headers.get('cache-control'), 'private, no-store')

  const vaultZip = await inspectZip(Buffer.from(await vaultRes.arrayBuffer()))
  assert.equal(vaultZip.bad, null)
  const vaultNames = vaultZip.entries.map((e) => e.name).sort()
  assert.deepEqual(vaultNames, [
    'EmptyFolder/',
    'Notes/Sub/deep.txt',
    'Notes/pic.png',
    'Notes/plan.md',
    'README.md',
  ])
  assert.equal(vaultZip.entries.find((e) => e.name === 'Notes/plan.md').data, '# Plan\n\nHello.')
  assert.equal(vaultZip.entries.find((e) => e.name === 'Notes/pic.png').data, PNG.toString('latin1'))

  // Subfolder archive strips parent prefix cleanly.
  const subRes = await fetch(`${app.url}/api/files/archive?root=vault&path=Notes`, { headers })
  assert.equal(subRes.status, 200)
  assert.equal(subRes.headers.get('content-disposition'), "attachment; filename*=UTF-8''Notes.zip")
  const subZip = await inspectZip(Buffer.from(await subRes.arrayBuffer()))
  assert.equal(subZip.bad, null)
  assert.deepEqual(subZip.entries.map((e) => e.name).sort(), ['Sub/deep.txt', 'pic.png', 'plan.md'])

  // AI Spaces archive keeps agent & commons content and excludes logs/secrets.
  const spacesRes = await fetch(`${app.url}/api/files/archive?root=spaces`, { headers })
  assert.equal(spacesRes.status, 200)
  assert.equal(spacesRes.headers.get('content-disposition'), "attachment; filename*=UTF-8''ai-spaces.zip")
  const spacesZip = await inspectZip(Buffer.from(await spacesRes.arrayBuffer()))
  assert.equal(spacesZip.bad, null)
  assert.deepEqual(spacesZip.entries.map((e) => e.name).sort(), ['commons/shared.md', 'scout/soul.md'])
})

test('archive route relays host /v1/files/archive directly when the broker supports it', async (t) => {
  const broker = await fakeBroker({
    'GET /v1/files/archive': (req, res, url) => {
      res.writeHead(200, { 'content-type': 'application/zip' })
      res.end(`ZIP:${url.searchParams.get('root')}:${url.searchParams.get('path') ?? ''}`)
    },
  })
  const app = await appAgainst(broker)
  t.after(closing(broker, app))
  const res = await fetch(`${app.url}/api/files/archive?root=vault`, { headers: { 'Remote-User': 'sam' } })
  assert.equal(res.status, 200)
  assert.equal(await res.text(), 'ZIP:vault:')
})

test('Files UI shows Download as zip in folder menus, Preparing state, plain errors, and redesigned empty Shortcuts state', async () => {
  installDom()
  let resolveCheck
  const downloads = []
  const files = createFiles({
    api: async () => ({}),
    schedule: () => {},
    fetchBytes: () => new Promise((resolve) => { resolveCheck = resolve }),
    triggerDownload: (href, filename) => downloads.push({ href, filename }),
  })
  files.st.roots = { loaded: true, items: [{ root: 'vault', name: 'Vault', desc: 'Notes', capabilities: { read: true, write: true } }, { root: 'spaces', name: 'AI Spaces', desc: 'Agents', capabilities: { read: true } }], shortcuts: [], error: '' }

  // Empty Shortcuts state has no card and no icon tile.
  const homeTree = folderView(files, { go() {}, wide: true, home: { shortcuts: [], toggleShortcut() {}, isShortcut: () => false } })
  const noshort = find(homeTree, 'fs-noshort')
  assert.ok(noshort)
  assert.equal(noshort.className, 'fs-noshort')
  assert.equal(find(homeTree, 'fs-shortcuts').querySelector('.card'), null)
  assert.match(text(noshort), /Nothing pinned yet/)

  // Vault root ⋯ sheet offers Download as zip and transitions through Preparing -> triggered download.
  files.st.root = 'vault'
  files.st.path = ''
  const go = Object.assign(() => {}, { redraw() {} })
  const vaultTree = folderView(files, { go, wide: false, home: { shortcuts: [], toggleShortcut() {}, isShortcut: () => false } })
  find(vaultTree, 'fs-menu').fire('click', {})
  assert.equal(files.st.sheet.kind, 'folder')
  const sheetNodes = addSheet(files, { close: { redraw() {} }, nameField: {} })
  const archiveBtn = sheetNodes.find((n) => n?.className?.includes('fs-menu-archive'))
  assert.ok(archiveBtn)
  assert.equal(text(archiveBtn), 'Download as zip')

  archiveBtn.fire('click', {})
  const busyNodes = addSheet(files, { close: { redraw() {} }, nameField: {} })
  assert.equal(text(busyNodes.find((n) => n?.className?.includes('fs-menu-archive'))), 'Preparing zip…')

  resolveCheck(new Response(JSON.stringify({ ok: true, filename: 'vault.zip' }), { status: 200, headers: { 'content-type': 'application/json' } }))
  await until(() => downloads.length === 1)
  assert.deepEqual(downloads, [{ href: '/api/files/archive?root=vault', filename: 'vault.zip' }])
  assert.equal(files.st.sheet, null)

  // Failed check shows plain-worded error and keeps the sheet open.
  files.st.sheet = { kind: 'folder', resource: { root: 'spaces', path: '', kind: 'folder' }, name: 'AI Spaces', on: false, toggle() {} }
  const failNodes = addSheet(files, { close: { redraw() {} }, nameField: {} })
  failNodes.find((n) => n?.className?.includes('fs-menu-archive')).fire('click', {})
  resolveCheck(new Response(JSON.stringify({ error: 'Sign in as the owner to download an archive.' }), { status: 401, headers: { 'content-type': 'application/json' } }))
  await until(() => Boolean(files.st.archive?.error))
  assert.ok(files.st.sheet)
  const errNodes = addSheet(files, { close: { redraw() {} }, nameField: {} })
  assert.equal(text(errNodes.find((n) => n?.className?.includes('listnotice'))), 'Sign in as the owner to download an archive.')
})
