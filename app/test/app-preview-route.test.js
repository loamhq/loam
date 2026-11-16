import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { once } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { gzipSync } from 'node:zlib'
import { readConfig } from '../src/config.js'
import { previewTarget, createPreviewFetch, readPreviewRoute } from '../src/app-preview-route.js'
import { createAppMedia } from '../src/app-media.js'
import { createAppIcons } from '../src/app-icons.js'

const app = { name: 'Alpha', kind: 'site', url: 'https://alpha.team.example.org/start/nested?view=cards#top', frame: true }
const origin = 'http://127.0.0.1:8080', base = 'http://preview.example.test:8123'
const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><rect width="40" height="40" fill="#315c59"/></svg>'
async function dirFor(t) { const dir = await mkdtemp(path.join(tmpdir(), 'preview-route-')); t.after(() => rm(dir, { force: true, recursive: true })); return dir }
async function internalSite(t) {
  const seen = []
  const server = http.createServer((req, res) => {
    seen.push({ path: req.url, host: req.headers.host, auth: req.headers.authorization, cookie: req.headers.cookie })
    const label = req.headers.host.split('.')[0]
    if (req.url === '/x/private/') { res.writeHead(401); return res.end('Login required') }
    if (req.url === '/hang') return
    if (['unknown', 'outside', 'expired', 'unsupported'].includes(label)) { res.writeHead({ unknown: 404, outside: 403, expired: 410, unsupported: 700 }[label]); return res.end('Unavailable') }
    if (req.url.startsWith('/start/')) { res.writeHead(307, { location: '/enter?via=redirect' }); return res.end() }
    if (req.url.startsWith('/work/')) { res.writeHead(307, { location: '/workbench' }); return res.end() }
    if (req.url === '/manifest.json') { res.writeHead(200, { 'content-type': 'application/manifest+json', 'content-encoding': 'gzip' }); return res.end(gzipSync(JSON.stringify({ icons: [{ src: '/assets/mark.svg', sizes: '64x64' }] }))) }
    if (req.url === '/assets/mark.svg') { res.writeHead(200, { 'content-type': 'image/svg+xml' }); return res.end(svg) }
    if (req.url === '/style.css') { res.writeHead(200, { 'content-type': 'text/css' }); return res.end('body{margin:0;background:#e2eada;color:#274d48;font:28px sans-serif;padding:70px}h1{font-size:64px}.panel{background:#adc7bb;border-radius:20px;padding:40px;height:300px}') }
    res.writeHead(200, { 'content-type': 'text/html' })
    // Absolute own-site metadata is also remapped; assets and navigation stay relative.
    res.end('<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/style.css"><link rel="manifest" href="https://alpha.team.example.org/manifest.json"><h1>A private little garden</h1><div class="panel">Reached through the internal route.</div>')
  })
  server.listen(0, '127.0.0.1'); await once(server, 'listening')
  t.after(() => { server.closeAllConnections(); server.close() })
  return { seen, base: `http://preview.example.test:${server.address().port}`, origin: `http://127.0.0.1:${server.address().port}` }
}

test('internal route configuration is optional and validates only a plain HTTP origin and address', () => {
  assert.equal(readConfig({}).appMedia.previewBase, '')
  assert.deepEqual(readPreviewRoute({ addr: 'ignored-without-base' }), { base: '', addr: '' })
  const config = readConfig({ APPS_PREVIEW_BASE: base + '/', APPS_PREVIEW_ADDR: '127.0.0.1' })
  assert.equal(config.appMedia.previewBase, base); assert.equal(config.appMedia.previewAddr, '127.0.0.1')
  for (const value of ['https://preview.example.test', 'http://person:password@preview.example.test', base + '/path', base + '?x=1', base + '#x', 'http://127.0.0.1', 'http://[::1]']) assert.throws(() => readPreviewRoute({ base: value }), /APPS_PREVIEW_BASE/)
  assert.throws(() => readPreviewRoute({ base, addr: 'not-an-ip' }), /APPS_PREVIEW_ADDR/)
})
test('sites/projects map the first host label and preserve paths, queries and fragments; plugin URLs stay on the page host', () => {
  for (const kind of ['site', 'project']) {
    const target = previewTarget({ ...app, kind, frame: false }, { origin, base, addr: '127.0.0.1' })
    assert.equal(target.url, 'http://alpha.preview.example.test:8123/start/nested?view=cards#top')
    assert.equal(target.hostResolverRules, 'MAP *.preview.example.test 127.0.0.1')
    assert.equal(target.rewrite('https://alpha.team.example.org/icon.svg'), 'http://alpha.preview.example.test:8123/icon.svg')
    assert.equal(target.rewrite('https://cdn.example.org/icon.svg'), 'https://cdn.example.org/icon.svg')
  }
  const plugin = previewTarget({ kind: 'plugin', url: '/x/tool/' }, { origin, base, addr: '127.0.0.1' })
  assert.equal(plugin.url, origin + '/x/tool/'); assert.equal(plugin.hostResolverRules, undefined)
  assert.equal(previewTarget(app, { origin }).url, app.url)
  assert.match(previewTarget(app, { origin, base, addr: '::1' }).hostResolverRules, /\[::1\]$/)
})
test('HTTP icon reads use the bridge address with virtual Host, follow relative redirects, decode gzip and never send credentials', async t => {
  const site = await internalSite(t), target = previewTarget(app, { origin, base: site.base, addr: '127.0.0.1' })
  const mapped = createPreviewFetch({ base: site.base, addr: '127.0.0.1' })
  const icon = await createAppIcons({ fetcher: mapped })(target.url, { rewrite: target.rewrite })
  assert.equal(icon.body.toString(), svg)
  assert.deepEqual(site.seen.map(row => row.path), ['/start/nested?view=cards', '/enter?via=redirect', '/manifest.json', '/assets/mark.svg'])
  assert.ok(site.seen.every(row => row.host === 'alpha.preview.example.test' && !row.auth && !row.cookie))
  const response = await mapped(target.url, { headers: { authorization: 'fixture-not-a-real-token', cookie: 'fixture=none' } }); await response.body.cancel()
  assert.equal(site.seen.at(-1).auth, undefined); assert.equal(site.seen.at(-1).cookie, undefined)
  for (const label of ['unknown', 'outside', 'expired', 'unsupported']) assert.equal(await createAppIcons({ fetcher: mapped })(target.url.replace('alpha.', `${label}.`)), null)
  const hanging = new URL(target.url); hanging.pathname = '/hang'; hanging.search = ''
  await assert.rejects(mapped(hanging.href, { signal: AbortSignal.timeout(50) }), { name: 'AbortError' })
})
test('without a configured address, ordinary fetch behavior is retained', async () => {
  const seen = [], fetcher = async (...args) => { seen.push(args); return new Response('ok') }
  assert.equal(createPreviewFetch({ fetcher }), fetcher)
  assert.equal(createPreviewFetch({ base, fetcher }), fetcher)
  await createPreviewFetch({ base, addr: '127.0.0.1', fetcher })('https://cdn.example.org/icon.svg')
  assert.equal(seen[0][0], 'https://cdn.example.org/icon.svg')
})
test('route configuration invalidates a cached public failure immediately and keeps public identities and plugin captures unchanged', async t => {
  const dir = await dirFor(t), calls = [], marks = [], jpeg = Buffer.from([255,216,255,217])
  const publicMedia = createAppMedia({ dir, appsDomain: 'team.example.org' }, { capture: async () => { throw new Error('Login required') }, icons: async () => null })
  await publicMedia.resolve(app, { origin }); await publicMedia.idle()
  assert.equal((await publicMedia.resolve(app, { origin })).preview, null)
  const media = createAppMedia({ dir, appsDomain: 'team.example.org', previewBase: base, previewAddr: '127.0.0.1' }, { capture: async options => { calls.push(options); return jpeg }, icons: async url => { marks.push(url); return null } })
  assert.equal((await media.resolve(app, { origin })).pending, true); await media.idle()
  const row = await media.resolve(app, { origin })
  assert.equal(row.url, app.url); assert.equal(new URL(row.preview, origin).searchParams.get('url'), app.url)
  assert.equal(calls[0].url, 'http://alpha.preview.example.test:8123/start/nested?view=cards#top')
  assert.equal(marks[0], calls[0].url); assert.ok(calls[0].hostResolverRules)
  const plugin = { name: 'Tool', kind: 'plugin', url: '/x/tool/', on: true, state: 'running' }
  await media.resolve(plugin, { origin }); await media.idle()
  assert.equal(calls[1].url, origin + '/x/tool/'); assert.equal(calls[1].hostResolverRules, undefined)
})
test('real headless shell captures a virtual internal host, root-relative CSS and redirect without public DNS or login', {
  skip: !process.env.APP_PREVIEW_CHROME && 'Set APP_PREVIEW_CHROME to exercise internal host resolution.',
}, async t => {
  const dir = await dirFor(t), site = await internalSite(t)
  const media = createAppMedia({ dir, chrome: process.env.APP_PREVIEW_CHROME, appsDomain: 'team.example.org', previewBase: site.base, previewAddr: '127.0.0.1' })
  const browserOnly = { ...app, frame: false }
  await media.resolve(browserOnly, { origin }); await media.idle()
  const row = await media.resolve(browserOnly, { origin })
  assert.ok(row.preview); assert.ok(row.icon)
  const picture = await media.image(app, 'preview'); assert.ok(picture.body.length > 1000)
  assert.equal((await media.image(app, 'icon')).body.toString(), svg)
  assert.ok(site.seen.some(row => row.host.startsWith('alpha.preview.example.test:') && row.path === '/style.css'))
  assert.ok(site.seen.some(row => row.host === 'alpha.preview.example.test' && row.path === '/assets/mark.svg'))
  const project = { ...browserOnly, kind: 'project', url: app.url.replace('/start/', '/work/') }
  await media.resolve(project, { origin }); await media.idle()
  assert.ok((await media.resolve(project, { origin })).preview)
  assert.ok(site.seen.some(row => row.path === '/workbench'))
  const plugin = { name: 'Tool', kind: 'plugin', url: '/x/tool/', on: true, state: 'running' }
  await media.resolve(plugin, { origin: site.origin }); await media.idle()
  assert.ok((await media.resolve(plugin, { origin: site.origin })).preview)
  assert.ok(site.seen.some(row => row.host === new URL(site.origin).host && row.path === '/x/tool/'))
  const privatePlugin = { ...plugin, url: '/x/private/' }
  await media.resolve(privatePlugin, { origin: site.origin }); await media.idle()
  assert.equal((await media.resolve(privatePlugin, { origin: site.origin })).preview, null)
  assert.ok(site.seen.every(row => !row.auth && !row.cookie))
})
