import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm, mkdir, writeFile, readdir, utimes, readFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { tmpdir } from 'node:os'
import path from 'node:path'
import http from 'node:http'
import { once } from 'node:events'
import { createAppMedia, previewable } from '../src/app-media.js'
import { createAppIcons } from '../src/app-icons.js'
import { fakeBroker, appAgainst, closing } from './harness.js'
const app = { name: 'Garden', url: 'https://garden.example.org/', frame: true, kind: 'site' }
const jpeg = Buffer.from([255, 216, 255, 217]), origin = 'http://127.0.0.1:8000'
async function directory(t) { const dir = await mkdtemp(path.join(tmpdir(), 'app-media-')); t.after(() => rm(dir, { recursive: true, force: true })); return dir }

test('preview cache coalesces jobs, survives restart, refreshes on age/change, and keeps the last good capture on failure', async t => {
  const dir = await directory(t); let now = 10000, captures = 0, marks = 0, fail = false
  const options = { dir, appsDomain: 'example.org' }, deps = { now: () => now, capture: async () => { captures++; if (fail) throw new Error('no WebGL'); return jpeg }, icons: async () => { marks++; return null } }
  let media = createAppMedia(options, deps)
  const loading = await Promise.all([media.resolve(app, { origin }), media.resolve(app, { origin })])
  assert.ok(loading.every(row => row.pending)); await media.idle()
  let ready = await media.resolve(app, { origin }); assert.ok(ready.preview.startsWith('/api/apps/media/image?')); assert.equal(ready.pending, false)
  assert.equal(captures, 1); assert.equal(marks, 1)
  media = createAppMedia(options, deps)
  assert.equal((await media.resolve(app, { origin })).preview, ready.preview); assert.equal(captures, 1)
  now += 7 * 3600000; fail = true
  assert.equal((await media.resolve(app, { origin })).preview, ready.preview); await media.idle(); assert.equal(captures, 2)
  assert.equal((await media.resolve(app, { origin })).preview, ready.preview)
  fail = false; await media.resolve({ ...app, version: '2' }, { origin }); await media.idle(); assert.equal(captures, 3)
})
test('two resolves at once read the note once, so a read that answers late cannot start a second capture', async t => {
  // The order a loaded box can give (a landing's full test run did): one
  // read runs before any note exists, and its answer arrives after the
  // other resolve's capture has written the note. Held here on purpose.
  const dir = await directory(t); let captures = 0, reads = 0, release
  const held = new Promise(resolve => { release = resolve })
  const read = async (file, encoding) => {
    reads++
    const answer = await readFile(file, encoding).then(text => [null, text], error => [error])
    if (reads === 1) await held
    if (answer[0]) throw answer[0]
    return answer[1]
  }
  const media = createAppMedia({ dir, appsDomain: 'example.org' }, { now: () => 10000, read, capture: async () => { captures++; return jpeg }, icons: async () => null })
  const both = Promise.all([media.resolve(app, { origin }), media.resolve(app, { origin })])
  await new Promise(resolve => setTimeout(resolve, 20)); release()
  assert.ok((await both).every(row => row.pending)); await media.idle()
  assert.equal(reads, 1); assert.equal(captures, 1)
  assert.equal((await media.resolve(app, { origin })).pending, false); assert.equal(captures, 1)
})
test('only one browser runs at a time; browser-only apps are captured while unsafe URLs and stopped plugins are excluded', async t => {
  const dir = await directory(t); let active = 0, max = 0, calls = 0
  const media = createAppMedia({ dir, appsDomain: 'example.org' }, { icons: async () => null, capture: async () => {
    calls++; active++; max = Math.max(max, active); await new Promise(r => setTimeout(r, 10)); active--; return jpeg
  } })
  const browserOnly = { ...app, frame: false, url: 'https://browser.example.org/' }
  await Promise.all([app, { ...app, url: 'https://other.example.org/' }, browserOnly, { ...app, frame: false, url: 'https://external.test/' }, { kind: 'plugin', url: '/x/off/', on: false, state: 'stopped' }].map(app => media.resolve(app, { origin })))
  await media.idle(); assert.equal(max, 1); assert.equal(calls, 3)
  assert.ok((await media.resolve(browserOnly, { origin })).preview)
  assert.equal(previewable({ ...app, url: 'http://garden.example.org/' }, 'example.org'), false)
  assert.equal(previewable({ ...app, url: 'https://garden.example.org:8443/' }, 'example.org'), false)
  assert.equal(previewable({ ...app, url: 'https://user:password@garden.example.org/' }, 'example.org'), false)
})
test('the capture revision refreshes old browser-only failures and success logs measure capture time without logging cache hits', async t => {
  const dir = await directory(t), browserOnly = { ...app, frame: false }, rows = []
  const id = createHash('sha256').update(app.url).digest('hex')
  await mkdir(path.join(dir, id))
  const oldFingerprint = createHash('sha256').update(JSON.stringify([browserOnly.url, browserOnly.kind, browserOnly.frame, browserOnly.on, browserOnly.state, browserOnly.created, browserOnly.updated, browserOnly.modified, browserOnly.version, browserOnly.icon])).digest('hex')
  await writeFile(path.join(dir, id, 'meta.json'), JSON.stringify({ fingerprint: oldFingerprint, until: Date.now() + 1800000, preview: null }))
  const media = createAppMedia({ dir, appsDomain: 'example.org' }, { icons: async () => null, capture: async () => jpeg, log: { info: row => rows.push(row) } })
  assert.equal((await media.resolve(browserOnly, { origin })).pending, true)
  await media.idle()
  assert.ok((await media.resolve(browserOnly, { origin })).preview)
  assert.equal(rows.length, 1)
  assert.deepEqual({ ...rows[0], ms: 0 }, { event: 'app-preview', app: app.name, state: 'ok', ms: 0 })
  assert.ok(Number.isInteger(rows[0].ms) && rows[0].ms >= 0)
  await media.resolve(browserOnly, { origin }); await media.idle()
  assert.equal(rows.length, 1)
})
test('cache maintenance evicts old/extra entries while preserving the current capture', async t => {
  const dir = await directory(t), ids = []
  for (let index = 0; index < 130; index++) {
    const id = createHash('sha256').update(`old-${index}`).digest('hex'); ids.push(id)
    await mkdir(path.join(dir, id))
  }
  const old = new Date(Date.now() - 8 * 24 * 3600000)
  await utimes(path.join(dir, ids[0]), old, old)
  const media = createAppMedia({ dir, appsDomain: 'example.org' }, { capture: async () => jpeg, icons: async () => null })
  await media.resolve(app, { origin }); await media.idle()
  const entries = (await readdir(dir)).filter(name => /^[a-f0-9]{64}$/.test(name))
  assert.equal(entries.length, 128); assert.ok(!entries.includes(ids[0]))
  assert.ok((await media.resolve(app, { origin })).preview)
})
test('unwritable storage and a missing browser settle to the designed fallback without blocking the catalog', async t => {
  const dir = await directory(t), blocked = path.join(dir, 'not-a-directory')
  await writeFile(blocked, 'file')
  const noStorage = createAppMedia({ dir: blocked })
  const unavailable = await noStorage.resolve(app, { origin })
  assert.equal(unavailable.pending, false); assert.equal(unavailable.preview, null)
  const noBrowser = createAppMedia({ dir, appsDomain: 'example.org', chrome: path.join(dir, 'missing-browser') }, { icons: async () => null })
  assert.equal((await noBrowser.resolve(app, { origin })).pending, true)
  await noBrowser.idle()
  const fallback = await noBrowser.resolve(app, { origin })
  assert.equal(fallback.pending, false); assert.equal(fallback.preview, null)
  assert.ok(fallback.refresh_after_ms <= 1800000)
  assert.deepEqual(await readdir(path.join(dir, 'tmp')), [])
})
test('media and image routes serve cached bytes from this origin, revalidate the catalog, and preserve legacy tabs without probes', async t => {
  const dir = await directory(t), requests = [], svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'
  const site = http.createServer((req, res) => { requests.push({ path: req.url, headers: req.headers }); res.end(req.url === '/brand.svg' ? svg : '<link rel="icon" href="/brand.svg">') })
  site.listen(0, '127.0.0.1'); await once(site, 'listening'); t.after(() => { site.closeAllConnections(); site.close() })
  const url = `http://127.0.0.1:${site.address().port}/`; let apps = [{ ...app, url }]
  const media = createAppMedia({ dir }, { icons: createAppIcons(), capture: async () => jpeg })
  const broker = await fakeBroker({ 'GET /v1/apps': (req, res) => res.end(JSON.stringify({ apps, sources: ['yours'] })) }), web = await appAgainst(broker, {}, { appMedia: media })
  t.after(closing(web, broker))
  assert.equal((await (await fetch(web.url + '/api/apps/media')).json()).apps[0].pending, true)
  await media.idle()
  const row = (await (await fetch(web.url + '/api/apps/media')).json()).apps[0]
  assert.ok(row.icon.startsWith('/api/apps/media/image?')); assert.equal(row.preview, null)
  const image = await fetch(web.url + row.icon)
  assert.equal(await image.text(), svg); assert.equal(image.headers.get('content-type'), 'image/svg+xml')
  assert.match(image.headers.get('content-security-policy'), /sandbox/)
  assert.deepEqual((await (await fetch(web.url + '/api/apps/icon?url=' + encodeURIComponent(url))).json()).icons, [row.icon])
  assert.deepEqual(requests.map(r => r.path), ['/', '/brand.svg'])
  assert.ok(requests.every(r => !r.headers.authorization && !r.headers.cookie))
  assert.equal((await fetch(web.url + '/api/apps/media/image?url=' + encodeURIComponent(url) + '&kind=missing')).status, 200)
  apps = []; assert.equal((await fetch(web.url + row.icon)).status, 404)
})
