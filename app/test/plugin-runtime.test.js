import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { all, find, text } from './browser.js'
import { h } from '../public/ui.js'
import { createPluginSettings } from '../public/plugin-settings.js'
import { createApps, appURL, canFrame } from '../public/apps.js'
import { fakeBroker, appAgainst, closing, TOKEN } from './harness.js'

const row = (state, extra = {}) => ({ id: 'demo', key: 'yours:demo', name: 'Demo', effective: true, layer: 'yours', switch: 'plugin', on: true,
  files: '/api/plugins/demo/files/', icon: 'icon.svg', service: { state, log: ['Could not start the example.', '<a log line>'] }, ...extra })
const tree = nodes => h('div', {}, nodes)
test('server states, collapsed failure logs, listed packages and restart refusals stay usable', async () => {
  installDom()
  const requests = [], loader = { st: { loaded: true, rows: [], failures: new Map(), writing: new Set() }, refresh: async () => {}, toggle() {} }
  let refused = false
  const ui = createPluginSettings({ loader, theme: { value: {} }, host: { settingsHead: text => h('h2', { text }), settingsBack() {},
    api: async (...args) => {
      requests.push(args)
      if (args[0] === 'GET') return { plugin: loader.st.rows[0] }
      if (refused) throw new Error('The plugin is off.')
      loader.st.rows = [row('starting')]
      return { plugin: row('starting') }
    } } })
  for (const state of ['running', 'starting', 'failing', 'stopping', 'stopped']) {
    loader.st.rows = [row(state)]
    const view = tree(ui.view())
    // The list speaks of a server only when it is not running (2026-11-19).
    if (state === 'running') assert.doesNotMatch(text(view), /Server ·/)
    else assert.match(text(view), new RegExp(`Server · ${state[0].toUpperCase() + state.slice(1)}`))
    assert.equal(all(view, 'plugin-icon')[0].getAttribute('src'), '/api/plugins/demo/files/icon.svg')
    assert.equal(all(view, 'plugin-service-failure').length, state === 'failing' ? 1 : 0)
    if (state === 'failing') {
      const failure = find(view, 'plugin-service-failure'), details = failure.childNodes[0]
      assert.equal(details.hasAttribute('open'), false)
      assert.match(text(details), /<a log line>/)
      all(failure, 'pillbtn')[0].fire('click')
      await new Promise(resolve => setImmediate(resolve))
      assert.deepEqual(requests.at(-1), ['POST', '/plugins/demo/restart', {}])
      assert.match(text(tree(ui.view())), /Server · Starting/)
    }
  }
  loader.st.rows = [row('failing')]; refused = true
  all(tree(ui.view()), 'pillbtn').find(n => text(n) === 'Restart').fire('click')
  await new Promise(resolve => setImmediate(resolve))
  assert.match(text(tree(ui.view())), /The plugin is off/)
  loader.st.rows = [row('stopped', { on: false })]
  assert.doesNotMatch(text(tree(ui.view())), /Server ·/)
  await ui.open(loader.st.rows[0])
  assert.doesNotMatch(text(tree(ui.view())), /Server ·|Its server/)
  ui.st.selected = null
  loader.st.rows = [row('stopped', { switch: 'listed', service: null })]
  const listed = tree(ui.view())
  assert.equal(all(listed, 'plugin-switch').length, 0)
  assert.doesNotMatch(text(find(listed, 'plugin-list-item')), /Unavailable/)
})

test('plugin apps use local paths, all three source groups and icons; unavailable main/side mounts never frame', async () => {
  installDom()
  const plugin = { name: 'Demo', kind: 'plugin', source: 'loam', url: '/x/demo/', icon: '/api/plugins/demo/files/icon.svg', on: true, state: 'running' }
  const opened = [], apps = createApps({ api: async (method, path) => path === '/apps/media' ? { apps: [{ url: plugin.url, preview: null, icon: '/api/apps/media/image?url=plugin&kind=icon', pending: false }] } : { sources: ['yours', 'installed', 'loam'], apps: [plugin] }, redraw() {}, wide: () => true,
    avatar: () => h('span'), agentName: id => id, newThread() {}, openBrowser: url => opened.push(url) })
  await apps.load()
  assert.equal(all(apps.view({ list: true }), 'apps-group')[0].getAttribute('aria-label'), 'Loam')
  assert.equal(all(apps.view({ list: true }), 'app-plugin-icon').length, 1)
  assert.equal(canFrame(plugin), true)
  assert.equal(appURL(plugin.url), '/x/demo/')
  for (const bad of ['/relative', '//evil.example/x/demo/', '/x/../other/', '/x/%2e%2e/', '/x/demo/\\evil']) assert.equal(appURL(bad), null)
  apps.open(plugin, null, { routed: true })
  assert.equal(all(apps.view(), 'apps-embed')[0].getAttribute('src'), '/x/demo/')
  for (const patch of [{ on: false }, { state: 'failing' }, { state: 'starting' }, { state: 'stopped' }]) {
    apps.open({ ...plugin, ...patch }, null, { routed: true })
    assert.equal(all(apps.view(), 'apps-embed').length, 0)
    assert.match(text(apps.view()), /Off|Failing|Starting|Stopped/)
    assert.doesNotMatch(text(apps.view()), /Open in browser/)
  }
  assert.deepEqual(opened, [])
})

test('account and restart relays preserve body/by, escaped ids, host errors and authentication; reject other verbs', async t => {
  const routes = {}, response = (req, res, url, raw) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ body: raw ? JSON.parse(raw) : null, by: url.searchParams.get('by') })) }
  for (const route of ['GET /v1/integrations/github/accounts', 'POST /v1/integrations/github/accounts', 'POST /v1/integrations/github/accounts/a b/refresh', 'DELETE /v1/integrations/github/accounts/a b', 'POST /v1/plugins/a b/restart']) routes[route] = response
  const broker = await fakeBroker(routes), app = await appAgainst(broker); t.after(closing(app, broker))
  for (const [method, route] of [['GET', '/integrations/github/accounts'], ['POST', '/integrations/github/accounts'], ['POST', '/integrations/github/accounts/a%20b/refresh'], ['DELETE', '/integrations/github/accounts/a%20b'], ['POST', '/plugins/a%20b/restart']]) {
    const body = method === 'GET' ? undefined : { by: 'person', org: 'octo-org', own_app: true }
    const out = await fetch(`${app.url}/api${route}?by=person&ignored=1`, { method, ...(body ? { body: JSON.stringify(body) } : {}) })
    assert.equal(out.status, 200); assert.equal(out.headers.get('cache-control'), 'no-store')
    assert.deepEqual(await out.json(), { body: body ?? null, by: 'person' })
    assert.equal(broker.seen.at(-1).auth, `Bearer ${TOKEN}`)
    assert.equal(broker.seen.at(-1).search, '?by=person')
  }
  for (const status of [404, 409]) {
    routes['POST /v1/plugins/a b/restart'] = (req, res) => { res.writeHead(status); res.end('{"error":"The plugin is off."}') }
    const out = await fetch(`${app.url}/api/plugins/a%20b/restart`, { method: 'POST', body: '{}' })
    assert.equal(out.status, status); assert.equal((await out.json()).error, 'The plugin is off.')
  }
  const count = broker.seen.length
  for (const [method, route] of [['DELETE', '/integrations/github/accounts'], ['GET', '/integrations/github/accounts/a'], ['DELETE', '/integrations/github/accounts/a/refresh'], ['GET', '/plugins/a/restart']]) assert.equal((await fetch(app.url + '/api' + route, { method })).status, 405)
  assert.equal(broker.seen.length, count)
})
