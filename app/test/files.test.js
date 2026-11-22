// Files: the relay's seven routes, and the pieces of the page that decide what
// an address, a row and a preview are. Against a recorded broker — nothing here
// has a vault behind it.

import test from 'node:test'
import http from 'node:http'
import assert from 'node:assert/strict'

import { fakeBroker, appAgainst, closing, TOKEN } from './harness.js'
import { installDom } from './fakedom.js'
import { filesHref, parseFilesPath, viewOf, capsOf, rootsOf, shortcutsOf, rowsOf, nextOf, readOf, resolveOf, missingSentence, externalHref, vaultUrlRef, formatText, drawnTypeOf, bytesAre, readOnlyReason, metaOf, validName, tileOf } from '../public/files.js'
import { assetPath, PREVIEW_POLICY } from '../src/preview.js'
import { row, rootsAnswer, listAnswer, readAnswer, PNG } from './fixtures/files-contract.js'

const json = (res, status, body, headers = {}) => {
  res.writeHead(status, { 'content-type': 'application/json', ...headers })
  res.end(JSON.stringify(body))
}

const saves = []
const PAGE_HTML = [
  '<html><head><link rel="stylesheet" href="style.css"><link rel="stylesheet" href="https://evil.example/x.css">',
  '<link rel="stylesheet" href="../../../../etc/passwd.css"><base href="https://evil.example/"><meta http-equiv="refresh" content="0;url=https://evil.example">',
  '<style>body{background:url(img/bg.png)} @import url(https://evil.example/i.css);</style></head>',
  '<body onload="parent.fetch(\'/api/sessions\')"><h1>Hi</h1><img src="img/pic.png" srcset="https://evil.example/a.png 2x"><img src="../../outside.png">',
  '<img src="https://evil.example/track.png"><img src="javascript:alert(1)"><img src="img/fake.png"><img src="img/drawing.svg">',
  '<script>parent.document.title="pwned"</script><SCRIPT src=//evil.example/x.js></SCRIPT><a href="javascript:alert(1)">x</a></body></html>',
].join('')
const VAULT = {
  'Notes/page.html': PAGE_HTML,
  'Notes/style.css': 'h1{color:red;background:url("img/pic.png")} @import "other.css";',
}
const BYTES = { 'Notes/img/pic.png': PNG, 'Notes/img/bg.png': PNG, 'Notes/img/fake.png': Buffer.from('<svg onload=alert(1)>'), 'outside.png': PNG, 'Notes/img/drawing.svg': Buffer.from('<svg/>') }
const broker = await fakeBroker({
  'GET /v1/files/roots': (req, res) => json(res, 200, rootsAnswer()),
  'GET /v1/files/list': (req, res, url) => json(res, 200, { ...listAnswer(row('vault', 'Notes'), [row('vault', 'Notes/a.md')]), echo: url.search }),
  'GET /v1/files/read': (req, res, url) => {
    const p = url.searchParams.get('path')
    if (p === 'gone.md') return json(res, 404, { error: 'nothing at vault/gone.md' })
    if (p === 'huge.bin') return json(res, 413, { error: 'over 2 MiB; download it instead', size: 5000000, max: 2097152 })
    if (p === 'pic.png') return json(res, 415, { error: 'not UTF-8 text', media_type: 'image/png' })
    if (p in VAULT) return json(res, 200, readAnswer(row('vault', p), VAULT[p], 'sha256:p'))
    json(res, 200, readAnswer(row('vault', p), '# hi', 'sha256:abc'))
  },
  'GET /v1/files/resolve': (req, res, url) => json(res, 200, { status: 'found', resource: row('vault', 'x.md'), fragment: null, echo: url.search }),
  'GET /v1/files/download': (req, res, url) => {
    const p = url.searchParams.get('path')
    if (p === 'clip.webm' && url.searchParams.get('range') === 'bytes=0-1') {
      res.writeHead(206, { 'content-type': 'video/webm', 'content-range': 'bytes 0-1/8', 'accept-ranges': 'bytes', 'content-length': 2 })
      return res.end(Buffer.from([0x1a, 0x45]))
    }
    if (p === 'evil.html') {
      res.writeHead(200, { 'content-type': 'text/html', 'content-disposition': 'inline' })
      return res.end('<script>alert(document.cookie)</script>')
    }
    if (p === 'pic.png' || p in BYTES) {
      res.writeHead(200, { 'content-type': 'image/png', 'content-disposition': "attachment; filename*=UTF-8''pic.png", 'content-security-policy': "sandbox; default-src 'none'", 'x-content-type-options': 'nosniff' })
      return res.end(p === 'pic.png' ? Buffer.from([0x89, 0x50, 0x4e, 0x47]) : BYTES[p])
    }
    json(res, 404, { error: `nothing at vault/${p}` })
  },
  'PUT /v1/files/text': (req, res, url, body) => {
    const b = JSON.parse(body)
    saves.push(b)
    if (b.base_revision === 'stale') return json(res, 409, { error: 'the file changed', current_revision: 'sha256:new' })
    if (b.root === 'spaces') return json(res, 403, { error: 'AI Spaces is read-only', authority: 'persona' })
    json(res, 200, { ...readAnswer(row(b.root, b.path), `---\nlast_modified_by: "[[Someone]]"\n---\n${b.text}`, 'sha256:def'), provenance: true })
  },
  'POST /v1/files/file': (req, res, url, body) => json(res, 201, { ...readAnswer(row('vault', JSON.parse(body).path), '', 'sha256:new'), provenance: true }),
  'POST /v1/files/folder': (req, res) => json(res, 409, { error: 'a folder by that name is already here' }),
})
const app = await appAgainst(broker)
test.after(closing(broker, app))

const get = (p, init) => fetch(app.url + p, init)
const lastSeen = () => broker.seen.at(-1)

test('each Files route reaches the broker with the token, and only the parameters it takes', async () => {
  const res = await get('/api/files/list?root=vault&path=Notes%2FDaily&cursor=c2&limit=50&token=x&root2=y')
  assert.equal(res.status, 200)
  assert.equal(lastSeen().auth, `Bearer ${TOKEN}`)
  assert.equal(lastSeen().path, '/v1/files/list')
  assert.equal(lastSeen().search, '?root=vault&path=Notes%2FDaily&cursor=c2&limit=50')
  assert.equal((await get('/api/files/roots?x=1')).status, 200)
  assert.equal(lastSeen().search, '')
  await get('/api/files/resolve?ref=%5B%5Bnote%5D%5D&from_root=vault&from_path=a%2Fb.md')
  assert.equal(lastSeen().search, '?ref=%5B%5Bnote%5D%5D&from_root=vault&from_path=a%2Fb.md')
  await get('/api/files/stat?root=vault&path=a.md')
  assert.equal(lastSeen().path, '/v1/files/stat')
})

test('a path with a dot segment, a backslash or a control character is refused before the broker', async () => {
  const before = broker.seen.length
  for (const bad of ['..%2Fetc%2Fpasswd', 'a%2F..%2Fb', '.', 'a%5Cb', 'a%00b']) {
    const res = await get(`/api/files/read?root=vault&path=${bad}`)
    assert.equal(res.status, 400, bad)
  }
  assert.equal((await get('/api/files/list?root=vault&limit=abc')).status, 400)
  assert.equal(broker.seen.length, before, 'a refused path reached the broker')
})

test('the host’s refusals come back as they are: 404, 413, 415, 403, 409', async () => {
  const r404 = await get('/api/files/read?root=vault&path=gone.md')
  assert.equal(r404.status, 404)
  assert.equal((await r404.json()).error, 'nothing at vault/gone.md')
  const r413 = await get('/api/files/read?root=vault&path=huge.bin')
  assert.equal(r413.status, 413)
  assert.deepEqual(await r413.json(), { error: 'over 2 MiB; download it instead', size: 5000000, max: 2097152 })
  const r415 = await get('/api/files/read?root=vault&path=pic.png')
  assert.equal(r415.status, 415)
  assert.equal((await r415.json()).media_type, 'image/png')
  const put = (body) => get('/api/files/text', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
  const r409 = await put({ root: 'vault', path: 'a.md', text: 'x', base_revision: 'stale' })
  assert.equal(r409.status, 409)
  assert.equal((await r409.json()).current_revision, 'sha256:new')
  const r403 = await put({ root: 'spaces', path: 'a.md', text: 'x', base_revision: 'r' })
  assert.equal(r403.status, 403)
  assert.equal((await r403.json()).authority, 'persona')
  const ok = await put({ root: 'vault', path: 'a.md', text: 'x', base_revision: 'r' })
  assert.equal(ok.status, 200)
  assert.deepEqual(saves.at(-1), { root: 'vault', path: 'a.md', text: 'x', base_revision: 'r' })
  const stored = await ok.json()
  assert.match(stored.text, /^---\nlast_modified_by/)
  assert.equal(stored.provenance, true)
  const made = await get('/api/files/file', { method: 'POST', body: JSON.stringify({ root: 'vault', path: 'n.md', text: '' }) })
  assert.equal(made.status, 201)
  assert.equal((await made.json()).path, 'n.md')
  assert.equal((await get('/api/files/folder', { method: 'POST', body: JSON.stringify({ root: 'vault', path: 'n' }) })).status, 409)
})

test('a body over the save limit is a 413 here and never reaches the broker', async () => {
  const before = broker.seen.length
  const res = await get('/api/files/text', { method: 'PUT', body: 'x'.repeat(14 * (1 << 20)) })
  assert.equal(res.status, 413)
  assert.equal(broker.seen.length, before)
})

test('only the listed methods are relayed', async () => {
  for (const [method, p] of [['DELETE', '/api/files/text'], ['POST', '/api/files/read'], ['PUT', '/api/files/folder'], ['GET', '/api/files/text'], ['GET', '/api/files/anything']]) {
    const res = await get(p, { method })
    assert.equal(res.status, 404, `${method} ${p}`)
  }
})

test('a download is always an attachment, never HTML on this origin, and sandboxed', async () => {
  const evil = await get('/api/files/download?root=vault&path=evil.html')
  assert.equal(evil.status, 200)
  assert.equal(evil.headers.get('content-type'), 'application/octet-stream')
  assert.match(evil.headers.get('content-disposition'), /^attachment/)
  assert.match(evil.headers.get('content-security-policy'), /sandbox/)
  assert.equal(evil.headers.get('x-content-type-options'), 'nosniff')
  const pic = await get('/api/files/download?root=vault&path=pic.png')
  assert.equal(pic.headers.get('content-type'), 'image/png')
  assert.equal(pic.headers.get('content-disposition'), "attachment; filename*=UTF-8''pic.png")
  assert.deepEqual([...new Uint8Array(await pic.arrayBuffer())], [0x89, 0x50, 0x4e, 0x47])
  assert.equal((await get('/api/files/download?root=vault&path=nope')).status, 404)
})

test('inline video relays a media player’s byte range and partial response', async () => {
  const video = await get('/api/files/download?root=vault&path=clip.webm', { headers: { range: 'bytes=0-1' } })
  assert.equal(video.status, 206)
  assert.equal(video.headers.get('content-type'), 'video/webm')
  assert.equal(video.headers.get('content-range'), 'bytes 0-1/8')
  assert.deepEqual([...new Uint8Array(await video.arrayBuffer())], [0x1a, 0x45])
})

test('page scripts stay same-origin while file previews retain their isolated policy', async () => {
  const res = await get('/files/vault')
  const csp = res.headers.get('content-security-policy')
  assert.match(csp, /default-src 'self'/)
  assert.match(csp, /script-src 'self';/)
  assert.match(csp, /img-src 'self' data: blob:/)
  assert.match(csp, /frame-src 'self' blob:/)
  assert.match(csp, /object-src 'none'/)
  assert.doesNotMatch(csp, /unsafe-inline|unsafe-eval|style-src/)
  assert.match(csp, /frame-src 'self' blob: https:\/\/\*\.example.com http: https:;/)
})

test('an HTML preview runs nothing, reaches nothing, and draws only its own local images and styles', async () => {
  const res = await get('/api/files/preview?root=vault&path=Notes%2Fpage.html')
  assert.equal(res.status, 200)
  assert.match(res.headers.get('content-type'), /^text\/html/)
  const csp = res.headers.get('content-security-policy')
  assert.equal(csp, PREVIEW_POLICY)
  assert.match(csp, /^sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:/)
  assert.doesNotMatch(csp, /script-src|allow-scripts|allow-same-origin/)
  assert.equal(res.headers.get('x-content-type-options'), 'nosniff')
  assert.equal(res.headers.get('cache-control'), 'private, no-store')
  const doc = await res.text()
  assert.doesNotMatch(doc, /<script/i, 'no script element survives')
  assert.doesNotMatch(doc, /evil\.example/, 'nothing on the network is named')
  assert.doesNotMatch(doc, /<base|http-equiv="refresh"/i)
  assert.doesNotMatch(doc, /passwd|outside/)
  // The local stylesheet is inlined, its own url() and the page's own <style> url() made data:.
  assert.match(doc, /<style>h1\{color:red;background:url\("data:image\/png;base64,/)
  assert.match(doc, /body\{background:url\("data:image\/png;base64,/)
  assert.doesNotMatch(doc, /@import/)
  // The local raster image is a data: URL; the climbing, remote, scriptful,
  // mislabelled and SVG ones are empty.
  const srcs = [...doc.matchAll(/<img[^>]*src="([^"]*)"/g)].map((m) => m[1].slice(0, 22))
  assert.deepEqual(srcs, ['data:image/png;base64,', '', '', '', '', ''])
  assert.doesNotMatch(doc, /srcset/)
  // Only the page's own folder was read — never the file above the root.
  const asked = broker.seen.filter((s) => s.path === '/v1/files/download' || s.path === '/v1/files/read').map((s) => new URLSearchParams(s.search).get('path'))
  assert.ok(!asked.includes('outside.png') && !asked.some((p) => p.includes('..')), asked.join(','))
})

test('a preview is for HTML only, refuses a bad path, and relays the host’s refusal', async () => {
  assert.equal((await get('/api/files/preview?root=vault&path=a.md')).status, 415)
  assert.equal((await get('/api/files/preview?root=vault&path=..%2Fx.html')).status, 400)
  const gone = await get('/api/files/preview?root=vault&path=gone.html')
  assert.equal(gone.status, 200, 'the fixture reads anything not listed as # hi')
})

test('the published vault URLs are configuration, sent to the page and nowhere hard-coded', async () => {
  assert.deepEqual(await (await get('/api/files/config')).json(), { vault_urls: [] })
  const configured = await appAgainst(broker, { FILES_VAULT_URLS: 'https://notes.example.org/, http://plain.example, javascript:alert(1)' })
  try {
    assert.deepEqual(await (await fetch(`${configured.url}/api/files/config`)).json(), { vault_urls: ['https://notes.example.org'] })
  } finally {
    await closing(configured)()
  }
})

test('a Files address, loaded directly or refreshed, is the page', async () => {
  for (const p of ['/files', '/files/vault', '/files/vault/Notes/Daily%20log.md', '/files/spaces/agent/soul.md']) {
    const res = await get(p)
    assert.equal(res.status, 200, p)
    assert.match(res.headers.get('content-type'), /text\/html/)
  }
  // A browser normalises these away; a raw client does not, and is refused.
  for (const raw of ['/files/vault/../../etc/passwd', '/files/vault/%2e%2e/x']) {
    const status = await new Promise((resolve, reject) => {
      http.get({ host: '127.0.0.1', port: new URL(app.url).port, path: raw }, (res) => { res.resume(); resolve(res.statusCode) }).on('error', reject)
    })
    assert.equal(status, 400, raw)
  }
})

test('a Files route with the broker unconfigured says so', async () => {
  const lonely = await appAgainst({ url: '' }, { BROKER_URL: '' })
  try {
    const res = await fetch(`${lonely.url}/api/files/roots`)
    assert.equal(res.status, 503)
  } finally {
    await closing(lonely)()
  }
})

test('an unauthorised answer from the host is relayed, not hidden', async () => {
  const wrong = await appAgainst(broker, { BROKER_TOKEN: 'wrong' })
  try {
    assert.equal((await fetch(`${wrong.url}/api/files/roots`)).status, 401)
  } finally {
    await closing(wrong)()
  }
})

// ------------------------------------------------------------- the page

test('an address round-trips, a / in a name stays inside its segment, and dot segments are refused', () => {
  assert.equal(filesHref('vault', 'Notes/Daily log.md'), '/files/vault/Notes/Daily%20log.md')
  assert.deepEqual(parseFilesPath('/files/vault/Notes/Daily%20log.md'), { root: 'vault', path: 'Notes/Daily log.md' })
  assert.deepEqual(parseFilesPath('/files'), { root: null, path: '' })
  assert.equal(parseFilesPath('/files/vault/a%2Fb'), null)
  assert.equal(parseFilesPath('/files/vault/..'), null)
  assert.equal(parseFilesPath('/files/vault/%2e%2e'), null)
  assert.equal(parseFilesPath('/files/vault/%E0%A4%A'), null)
  assert.equal(parseFilesPath('/sessions'), null)
  assert.equal(filesHref('spaces', ''), '/files/spaces')
})

test('what a row is: its view from its type, its capabilities only as the host gave them', () => {
  assert.equal(viewOf(row('vault', 'a.md')), 'markdown')
  assert.equal(viewOf(row('vault', 'page.HTML')), 'html')
  assert.equal(viewOf(row('vault', 'x.png')), 'image')
  assert.equal(viewOf(row('vault', 'x.svg')), 'text', 'an SVG is shown as its source, never drawn on this origin')
  assert.equal(viewOf(row('vault', 'r.pdf')), 'pdf')
  assert.equal(viewOf(row('vault', 'c.json')), 'text')
  assert.equal(viewOf({ ...row('vault', 'notes.base'), capabilities: { read: true, download: true, write: true, create: false } }), 'text', 'writable is text')
  assert.deepEqual(capsOf({ capabilities: { read: true, write: 'yes' } }), { read: true, download: false, write: false, create: false, rename: false })
  assert.deepEqual(capsOf({}), { read: false, download: false, write: false, create: false, rename: false })
  assert.equal(tileOf('a.md').label, 'MD')
  assert.equal(validName('ok.md'), true)
  for (const bad of ['', '..', 'a/b', 'a\\b']) assert.equal(validName(bad), false, bad)
  assert.equal(metaOf(row('vault', 'Notes')), 'Folder · ' + metaOf(row('vault', 'Notes')).split(' · ')[1], 'a folder has no count')
  assert.doesNotMatch(metaOf({ ...row('vault', 'Notes'), count: 9 }), /items/)
})

test('read-only says why, from the host’s authority', () => {
  assert.equal(readOnlyReason(row('vault', 'a.md')), '')
  assert.match(readOnlyReason(row('vault', '15_CONVERSATIONS/x.md', { authority: 'transcripts' })), /Conversation records/)
  assert.match(readOnlyReason(row('spaces', 'agent/MEMORY.md', { authority: 'persona' })), /agent/)
  assert.match(readOnlyReason(row('system', 'Engineer/Role.md', { authority: 'runtime' })), /host keeps/)
  assert.match(readOnlyReason(row('vault', 'pic.png')), /Only text/)
})

test('the roots, keyed by root, named by the host, with no seeded shortcuts and no count', () => {
  const roots = rootsOf(rootsAnswer())
  assert.deepEqual(roots.map((r) => [r.root, r.name]), [['vault', 'Your files'], ['spaces', 'Agent files']])
  assert.equal(roots[0].capabilities.create, true)
  assert.ok(roots.every((r) => !('count' in r) && !('id' in r)))
  assert.deepEqual(rootsOf({ roots: [{ id: 'vault' }, 'system'] }), [], 'the guessed shapes are not read')
  assert.deepEqual(shortcutsOf(rootsAnswer()), [])
  assert.deepEqual(shortcutsOf({ shortcuts: [{ label: 'App', resource: { root: 'vault', path: 'Projects/App', name: 'App', kind: 'folder' } }] }).map((s) => [s.label, s.resource.path]), [['App', 'Projects/App']])
  assert.deepEqual(shortcutsOf({ shortcuts: [{ label: 'x', resource: { root: 'vault' } }] }), [])
})

test('list: entries are rows, next_cursor is the next page, and the answer’s own cursor never is', () => {
  const page = listAnswer(row('vault', 'Notes'), [row('vault', 'Notes/a.md'), { name: 'guess' }], { cursor: 200, next: '400', total: 401 })
  assert.deepEqual(rowsOf(page).map((r) => r.path), ['Notes/a.md'])
  assert.equal(nextOf(page), '400')
  assert.equal(nextOf({ ...page, next_cursor: null }), null, 'the last page has cursor 200 and no next')
})

test('read and save: a bare row with text and revision beside it', () => {
  const got = readOf({ ...readAnswer(row('vault', 'a.md'), 'stored', 'sha256:1'), provenance: true })
  assert.equal(got.text, 'stored')
  assert.equal(got.revision, 'sha256:1')
  assert.equal(got.provenance, true)
  assert.equal(got.row.path, 'a.md')
  assert.ok(!('text' in got.row) && !('revision' in got.row))
  assert.equal(readOf({ row: { text: 'x' } }).row, null, 'nothing is unwrapped')
})

test('resolve: found, ambiguous, missing and external, by status; a missing path is never a hit', () => {
  const found = resolveOf({ status: 'found', resource: row('vault', 'Notes/a.md'), fragment: 'Some heading' })
  assert.equal(found.status, 'found')
  assert.equal(found.resource.link, '/files/vault/Notes/a.md')
  assert.equal(found.fragment, 'Some heading')
  const folder = resolveOf({ status: 'found', resource: row('vault', 'Notes'), fragment: null })
  assert.equal(folder.resource.kind, 'folder')
  const amb = resolveOf({ status: 'ambiguous', fragment: null, candidates: [row('vault', 'a/x.md'), row('vault', 'b/x.md')], candidates_total: 73, truncated: true })
  assert.deepEqual([amb.candidates.length, amb.total, amb.truncated], [2, 73, true])
  for (const body of [
    { status: 'missing', root: 'vault', path: 'Notes/gone.md', fragment: null },
    { status: 'missing', target: 'Gone', fragment: null },
    { status: 'missing', reason: 'the link climbs out of its root' },
    { status: 'missing', reason: 'outside Files’ roots', truncated: true },
    { status: 'missing' },
    { matches: [row('vault', 'x.md')] },
  ]) {
    const r = resolveOf(body)
    assert.equal(r.status, 'missing', JSON.stringify(body))
    assert.equal(r.resource, undefined)
  }
  assert.equal(missingSentence('[[Gone]]', resolveOf({ status: 'missing', target: 'Gone' })), 'There is no note called “Gone”.')
  assert.equal(missingSentence('x', resolveOf({ status: 'missing', root: 'vault', path: 'Notes/gone.md' })), 'Nothing at Your files / Notes/gone.md.')
  assert.match(missingSentence('../../x', resolveOf({ status: 'missing', reason: 'the link climbs out of its root' })), /climbs out/)
  const ext = resolveOf({ status: 'external', url: 'https://example.org/a' })
  assert.equal(externalHref(ext.url), 'https://example.org/a')
  for (const bad of ['javascript:alert(1)', 'java\tscript:alert(1)', 'data:text/html,<script>', 'vbscript:x', 'file:///etc/passwd', 'obsidian://open?x']) assert.equal(externalHref(bad), null, bad)
  assert.equal(externalHref('mailto:a@example.org'), 'mailto:a@example.org')
})

test('a published vault URL becomes a vault reference, and nothing else does', () => {
  const P = ['https://notes.example.org']
  assert.deepEqual(vaultUrlRef('https://notes.example.org/Projects/App/state.md#Next%20steps', P), { ref: '/files/vault/Projects/App/state.md', fragment: 'Next steps' })
  assert.deepEqual(vaultUrlRef('https://notes.example.org/Projects/App/state', P), { ref: '[[Projects/App/state]]', fragment: null })
  assert.deepEqual(vaultUrlRef('https://NOTES.example.org/My+Note', P), { ref: '[[My Note]]', fragment: null })
  for (const bad of [
    'https://notes.example.org.evil.example/a.md',
    'https://evil.example/https://notes.example.org/a.md',
    'http://notes.example.org/a.md',
    'https://notes.example.org/a/../../etc/passwd',
    'https://notes.example.org/%2e%2e/x.md',
    'https://notes.example.org/a.md?x=1',
    'https://notes.example.orga.md',
  ]) assert.equal(vaultUrlRef(bad, P), null, bad)
  assert.equal(vaultUrlRef('https://notes.example.org/a.md', ['javascript:alert(1)']), null)
})

test('the formatting bar applies to the selection, and takes a mark off again', () => {
  const t = 'one two\nthree'
  assert.deepEqual(formatText(t, 4, 7, 'bold'), { value: 'one **two**\nthree', start: 6, end: 9 })
  assert.deepEqual(formatText('one **two**\nthree', 4, 11, 'bold'), { value: t, start: 4, end: 7 })
  assert.deepEqual(formatText(t, 0, 3, 'italic'), { value: '*one* two\nthree', start: 1, end: 4 })
  assert.deepEqual(formatText(t, 3, 3, 'bold'), { value: 'one**bold** two\nthree', start: 5, end: 9 })
  assert.equal(formatText(t, 2, 10, 'heading').value, '## one two\n## three')
  assert.equal(formatText('## one two\n## three', 2, 14, 'heading').value, t)
  assert.equal(formatText('# one', 0, 0, 'heading').value, 'one', 'any heading is taken off')
  assert.equal(formatText(t, 0, t.length, 'list').value, '- one two\n- three')
  assert.equal(formatText('- one two\n- three', 0, 5, 'list').value, 'one two\n- three')
  assert.equal(formatText(t, 9, 9, 'quote').value, 'one two\n> three')
  const link = formatText(t, 4, 7, 'link')
  assert.equal(link.value, 'one [two](url)\nthree')
  assert.equal(link.value.slice(link.start, link.end), 'url')
})

test('only real raster images and PDFs are drawn, checked by their first bytes', () => {
  assert.equal(drawnTypeOf(row('vault', 'a.png')).type, 'image/png')
  assert.equal(drawnTypeOf(row('vault', 'r.pdf')).type, 'application/pdf')
  assert.equal(drawnTypeOf(row('vault', 'x.svg')), null)
  assert.equal(drawnTypeOf({ ...row('vault', 'a.png'), media_type: 'text/html' }), null, 'a type that disagrees is not drawn')
  assert.equal(bytesAre(drawnTypeOf(row('vault', 'a.png')), PNG.buffer.slice(PNG.byteOffset)), true)
  assert.equal(bytesAre(drawnTypeOf(row('vault', 'a.png')), new TextEncoder().encode('<svg onload=alert(1)>').buffer), false)
  assert.equal(bytesAre(drawnTypeOf(row('vault', 'r.pdf')), new TextEncoder().encode('%PDF-1.7').buffer), true)
})

test('a preview asset stays inside its root: relative only, no climbing out', () => {
  assert.equal(assetPath('Notes/page.html', 'img/a.png'), 'Notes/img/a.png')
  assert.equal(assetPath('Notes/page.html', './img/a%20b.png?x#y'), 'Notes/img/a b.png')
  assert.equal(assetPath('Notes/sub/page.html', '../a.png'), 'Notes/a.png')
  for (const bad of ['../../a.png', '/etc/passwd', '//evil.example/a.png', 'https://evil.example/a.png', 'data:image/png;base64,AA', 'javascript:alert(1)', 'a\\..\\b.png', '%2e%2e/%2e%2e/x.png', 'a%2F..%2F..%2F..%2Fb.png', '#x', '']) {
    assert.equal(assetPath('Notes/page.html', bad), null, bad)
  }
})

test('chat markdown: file links go to the page’s handler, and nothing else becomes a link', async () => {
  installDom()
  const { markdown, setFileLinks, setVaultUrls } = await import('../public/markdown.js')
  const asked = []
  const render = (text, opts) => {
    const div = document.createElement('div')
    for (const n of markdown(text, opts)) div.appendChild(n)
    return div
  }
  const links = (node, out = []) => {
    if (node.tag === 'a') out.push(node)
    for (const c of node.childNodes ?? node.children ?? []) links(c, out)
    return out
  }
  // No handler: nothing changes from before.
  assert.equal(links(render('[n](/files/vault/a.md) and [[note]]')).length, 0)
  setFileLinks((ref, from) => asked.push([ref, from]))
  try {
    const node = render('See [the plan](/files/vault/Plan.md), [[Daily log|log]], `/srv/notes/a.md`, [x](javascript:alert(1)) and <img src=x onerror=alert(1)>')
    const as = links(node)
    assert.deepEqual(as.map((a) => a.getAttribute('class')), ['filelink', 'filelink', 'filelink'])
    assert.equal(as[0].getAttribute('href'), '/files/vault/Plan.md')
    assert.equal(as[1].getAttribute('href'), '#')
    for (const a of as) a.fire('click', { preventDefault() {} })
    assert.deepEqual(asked.map((a) => a[0]), ['/files/vault/Plan.md', '[[Daily log]]', '/srv/notes/a.md'])
    assert.match(node.textContent, /\[x\]\(javascript:alert\(1\)\)/)
    assert.match(node.textContent, /<img src=x onerror=alert\(1\)>/)
    // Inside a Files preview a relative link is relative to the file.
    const rel = links(render('[next](../b.md)', { from: { root: 'vault', path: 'Notes/a.md' } }))
    rel[0].fire('click', { preventDefault() {} })
    assert.deepEqual(asked.at(-1), ['../b.md', { root: 'vault', path: 'Notes/a.md' }])
    // A published vault URL is a file link; any other web link stays a web link.
    setVaultUrls(['https://notes.example.org'])
    const web = links(render('[a](https://notes.example.org/a.md) [b](https://example.org/a.md)'))
    assert.deepEqual(web.map((a) => a.getAttribute('class')), ['filelink', null])
    assert.equal(web[1].getAttribute('rel'), 'noopener noreferrer')
    // Headings in a Files preview carry the anchor a #fragment lands on.
    const h = render('## Next steps', { anchors: true, from: { root: 'vault', path: 'a.md' } }).childNodes[0]
    assert.equal(h.getAttribute('data-anchor'), 'next-steps')
  } finally {
    setFileLinks(null)
    setVaultUrls([])
  }
})
