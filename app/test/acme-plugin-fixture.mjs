import { oneProductFixture } from './one-product-fixture.mjs'
import { pluginPageRoutes, fixtureGateway } from './plugin-page-fixture.js'
import { SHAPES, PENDING, SEVERAL, REVOKE_URL } from './fixtures/acme.js'
import { SHAPES as GH } from './fixtures/github.js'
import { shippedPlugins } from '../src/shipped-plugins.js'

export async function acmePluginFixture({ mode = 'light', look = 'latte' } = {}) {
  const app = await oneProductFixture(), json = (res, body, status = 200) => {
    res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(JSON.stringify(body))
  }
  const st = { data: SHAPES.unconfigured, catalog: 'on', puts: [], logins: [], held: null, status: 200 }
  const row = { id: 'acme', name: 'Acme', api: 0, kind: ['extension'], on: true, effective: true, layer: 'yours', switch: 'plugin',
    has: { page_main: true, server: true }, folder: 'fixture/acme', source: { kind: 'built' }, service: { state: 'running' } }
  const shipped = await shippedPlugins('public')
  pluginPageRoutes(app.routes, row, 'test/fixtures/acme-page')
  app.routes['GET /v1/plugins'] = (req, res) => json(res, { plugins: [...shipped, ...(st.catalog === 'absent' ? [] : [{ ...row, on: st.catalog === 'on' }])] })
  app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: { theme: look, mode, size: 100 }, desktop: { theme: look, mode, size: 100 } })
  app.routes['GET /v1/integrations/github'] = (req, res) => json(res, GH.authorized)
  app.routes['GET /v1/integrations/github/accounts'] = (req, res) => json(res, { accounts: [] })
  app.routes['GET /v1/sessions/live'] = (req, res) => json(res, { sessions: [], live_count: 0, cap: 10 })
  app.routes['GET /v1/sessions/live/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
  app.routes['GET /v1/activity'] = (req, res) => json(res, { items: [], total: 0, next: null, now: { slots: [], queue: [], landing: [] } })
  app.routes['GET /v1/limits'] = (req, res) => json(res, { attachment_max_bytes: 1024, attachment_types: [] })
  const routes = {
    'GET /x/acme/': async (req, res) => { if (st.held) await st.held; json(res, st.data, st.status) },
    'POST /x/acme/login': (req, res, url, raw) => {
      const body = JSON.parse(raw), next = st.logins.shift()
      if (next) { if (next.then) st.data = next.then; return json(res, next.body, next.status) }
      if (body.account_id) { st.data = SHAPES.login; return json(res, st.data) }
      st.data = SHAPES.pending; json(res, PENDING)
    },
    'PUT /x/acme/': (req, res, url, raw) => {
      const body = JSON.parse(raw), next = st.puts.shift()
      if (next) { if (next.then) st.data = next.then; return json(res, next.body, next.status) }
      if (!body.account_id) return json(res, SEVERAL, 400)
      st.data = SHAPES.connected; json(res, st.data)
    },
    'POST /x/acme/refresh': (req, res) => json(res, st.data),
    'DELETE /x/acme/': (req, res) => { st.data = SHAPES.unconfigured; json(res, { ...st.data, forgotten: true, revoked: false, revoke_url: REVOKE_URL }) },
  }
  const seen = fixtureGateway(app, '/x/acme/', routes)
  // The legacy visual reference is also intercepted by the browser. It never
  // reaches a live route or requires the removed core relay to be restored.
  return { ...app, st, seen, gatewayRoutes: routes, plugin: row }
}
