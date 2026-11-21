import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { all, find, text } from './browser.js'
import { h } from '../public/ui.js'
import { createApps, appURL, canFrame, readApps, appLetter } from '../public/apps.js'
import { fakeBroker, appAgainst, closing, TOKEN } from './harness.js'

export const catalog = { sources: ['yours'], apps: [
  { name: 'Garden', url: 'https://garden.example.com/', also_at: [], owner: 'sprout', source: 'yours', kind: 'site', access: 'private', frame: true, expires: '2026-11-25', shared_until: null, serves: 'static', created: '2026-11-01' },
  { name: 'Journal', url: 'https://journal.example.org/', also_at: ['https://www.journal.example.org/'], owner: 'owner', source: 'yours', kind: 'project', project: 'journal', access: 'public', frame: false, expires: null, shared_until: null, serves: 'container', created: '2026-11-02' },
] }
function setup({ wide = true, api = async () => catalog, copy = async () => true, isCurrent = () => true } = {}) {
  installDom()
  const opened = [], copied = [], started = []
  const apps = createApps({ api, redraw() {}, wide: () => wide, isCurrent, avatar: id => h('span', { class: 'app-owner', text: id }),
    agentName: id => id === 'owner' ? 'you' : 'Sprout', newThread: () => started.push(true),
    openBrowser: url => opened.push(url), copy: async url => { copied.push(url); return copy(url) } })
  return { apps, opened, copied, started }
}
test('Apps relay preserves the catalog, bearer and host errors; it permits GET only', async t => {
  const routes = { 'GET /v1/apps': (req, res) => res.end(JSON.stringify(catalog)) }
  const broker = await fakeBroker(routes), app = await appAgainst(broker)
  t.after(closing(app, broker))
  const response = await fetch(app.url + '/api/apps?ignored=1')
  assert.deepEqual(await response.json(), catalog)
  assert.match(response.headers.get('cache-control'), /no-store/)
  assert.equal(broker.seen[0].auth, `Bearer ${TOKEN}`)
  assert.equal(broker.seen[0].search, '')
  assert.equal((await fetch(app.url + '/api/apps', { method: 'POST' })).status, 404)
  for (const status of [405, 503]) {
    routes['GET /v1/apps'] = (req, res) => { res.writeHead(status); res.end('{"error":"Apps unavailable."}') }
    const failed = await fetch(app.url + '/api/apps')
    assert.equal(failed.status, status); assert.equal((await failed.json()).error, 'Apps unavailable.')
  }
  const policy = (await fetch(app.url)).headers.get('content-security-policy')
  assert.match(policy, /frame-src 'self' blob: https:\/\/\*\.example\.com http: https:;/)
  assert.doesNotMatch(policy, /unsafe-inline|unsafe-eval/)
})
test('catalog rows use two quiet lines and distinct app tiles; one group needs no heading', async () => {
  const { apps } = setup(); await apps.load()
  const view = apps.view({ list: true })
  assert.deepEqual(all(view, 'apps-group').map(node => node.getAttribute('aria-label')), ['Yours'])
  assert.equal(all(view, 'apps-row').length, 2)
  assert.match(text(all(view, 'apps-secondary')[0]), /Sprout · Private/)
  assert.match(text(all(view, 'apps-secondary')[1]), /you · Public/)
  assert.equal(all(view, 'kicker').length, 0)
  assert.equal(all(view, 'apps-icon').length, 2)
  assert.equal(all(view, 'apps-owner').length, 2)
  assert.deepEqual(all(view, 'apps-monogram').map(text), ['G', 'J'])
  assert.doesNotMatch(text(view), /garden.example.com|journal.example.org/)
  apps.st.apps.push({ ...catalog.apps[0], name: 'Installed app', source: 'installed', url: 'https://installed.example.com/' })
  assert.equal(all(apps.view({ list: true }), 'apps-group').length, 2)
  assert.equal(all(apps.view({ list: true }), 'kicker').length, 2)
})
test('only near expiry is on a row; details retain the full address, access, date and copy', async () => {
  const { apps, copied } = setup({ wide: false })
  await apps.load()
  apps.st.apps[0] = { ...catalog.apps[0], expires: new Date(Date.now() + 3 * 864e5).toISOString() }
  apps.st.apps[1] = { ...catalog.apps[1], expires: new Date(Date.now() + 30 * 864e5).toISOString() }
  let view = apps.view()
  assert.equal(all(view, 'apps-expiry').length, 1)
  assert.equal(text(all(view, 'apps-expiry')[0]), '3d left')
  all(view, 'apps-details-button')[1].fire('click')
  view = apps.view()
  assert.match(text(all(view, 'apps-details')[0]), /youPublicjournal.example.orgEnds.*Open in browserCopy link/)
  all(view, 'pillbtn').find(node => text(node) === 'Copy link').fire('click')
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(copied, [catalog.apps[1].url]); assert.match(text(apps.view()), /Copied/)
})
test('the client loads only verified same-origin media and falls back without probing another path', async () => {
  const src = '/api/apps/media/image?url=garden&kind=preview&v=one', calls = []
  const { apps } = setup({ api: async (method, path) => { calls.push(path); return path === '/apps/media' ? { apps: [{ url: catalog.apps[0].url, preview: src, icon: 'https://remote.example.org/favicon.svg', pending: false }] } : catalog } })
  await apps.load()
  let view = apps.view({ list: true })
  assert.equal(all(view, 'apps-preview-image')[0].getAttribute('src'), src)
  assert.equal(all(view, 'apps-site-icon').length, 0)
  all(view, 'apps-preview-image')[0].fire('error')
  view = apps.view({ list: true })
  assert.equal(all(view, 'apps-preview-image').length, 0)
  assert.equal(text(all(view, 'apps-monogram')[0]), 'G')
  assert.deepEqual(calls, ['/apps', '/apps/media'])
})
test('fallback letters use the distinguishing part of a family name', () => {
  const names = ['Atlas', 'Atlas-bench', 'Atlas demo', 'Atlas_design', 'Atlas.maps'].map((name, i) => ({ name, url: `https://app-${i}.example.org` }))
  assert.deepEqual(names.map(app => appLetter(app, names)), ['A', 'B', 'D', 'S', 'M'])
})
test('wide framing keeps one iframe, names it, and offers copy and browser escape', async () => {
  const { apps, opened, copied } = setup(); await apps.load()
  apps.open(catalog.apps[0])
  const view = apps.view(), frame = all(view, 'apps-embed')[0]
  assert.ok(frame); assert.equal(frame.getAttribute('title'), 'Garden')
  assert.equal(frame.getAttribute('src'), catalog.apps[0].url)
  assert.match(text(view), /Open in browserCopy link/)
  await apps.copyLink(); assert.deepEqual(copied, [catalog.apps[0].url]); assert.match(text(apps.view()), /Copied/)
  apps.open(catalog.apps[1]); assert.deepEqual(opened, [catalog.apps[1].url])
})
test('desktop list stays available beside the chosen app; the empty main never repeats the catalog', async () => {
  const { apps } = setup(); await apps.load()
  assert.match(text(apps.view()), /Pick an app/)
  assert.equal(all(apps.view(), 'apps-row').length, 0)
  apps.open(catalog.apps[0])
  const list = apps.view({ list: true }), rows = all(list, 'apps-row')
  assert.equal(rows.length, 2)
  assert.equal(rows[0].getAttribute('aria-current'), 'page')
  assert.equal(rows[1].getAttribute('aria-current'), null)
  assert.equal(all(list, 'apps-embed').length, 0)
  assert.equal(all(apps.view(), 'apps-embed').length, 1)
})
test('all phone rows open in a separate browser and copy failures remain visible', async () => {
  const { apps, opened } = setup({ wide: false }); await apps.load()
  for (const app of catalog.apps) apps.open(app)
  assert.deepEqual(opened, catalog.apps.map(app => app.url)); assert.equal(all(apps.view(), 'apps-embed').length, 0)
  const failed = setup({ copy: async () => { throw new Error('clipboard denied') } })
  failed.apps.open(catalog.apps[0]); await failed.apps.copyLink()
  assert.match(text(failed.apps.view()), /link could not be copied/)
})
test('an app list beside another main document does not mark its earlier app as current', async () => {
  let current = true
  const { apps } = setup({ isCurrent: () => current }); await apps.load()
  apps.open(catalog.apps[0])
  assert.equal(all(apps.view({ list: true }), 'apps-row')[0].getAttribute('aria-current'), 'page')
  current = false
  assert.equal(all(apps.view({ list: true }), 'apps-row')[0].getAttribute('aria-current'), null)
})
test('empty catalog starts a thread; unavailable and malformed catalogs offer retry', async () => {
  const f = setup({ api: async () => ({ apps: [], sources: ['yours'] }) }); await f.apps.load()
  const view = f.apps.view({ list: true }); assert.match(text(view), /No apps yetAn agent can build an app for you/)
  all(view, 'pillbtn').find(node => text(node) === 'Build an app').fire('click'); assert.equal(f.started.length, 1)
  for (const status of [404, 405]) {
    const f = setup({ api: async () => { throw Object.assign(new Error('no route'), { status }) } }); await f.apps.load()
    assert.match(text(f.apps.view({ list: true })), /Apps are not available from this host yet.*Try again/)
    assert.equal(find(f.apps.view({ list: true }), 'apps-empty'), null)
  }
  assert.throws(() => readApps([]), /could not be read/)
})
test('only safe URL protocols open; framing is limited to the CSP’s allowed origins', () => {
  for (const url of ['javascript:alert(1)', 'data:text/html,hi', 'https://user:password@example.org', '/relative', 'broken']) assert.equal(appURL(url), null)
  assert.equal(canFrame(catalog.apps[0]), true)
  for (const url of ['https://example.com.evil.org', 'https://example.com', 'http://garden.example.com', 'https://garden.example.com:8080', 'https://unrelated.example.org']) assert.equal(canFrame({ frame: true, url }), false)
})
