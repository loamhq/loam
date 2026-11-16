import { pluginHostFixture } from './plugin-host-fixture.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'
import { SHAPES } from './fixtures/github.js'

export const githubAccounts = [
  { account: 'octo-owner', kind: 'personal', status: 'authorized', disconnect_also: ['octo-team'], app: { ...SHAPES.authorized.app, owner: 'octo-owner' }, installation: SHAPES.authorized.installed,
    user: { ...SHAPES.authorized.user, refresh_expires_at: '2027-04-02T21:37:31Z' }, health: { last_ok_at: '2026-11-14T10:00:00Z', last_error: null, checked_at: '2026-11-14T10:00:00Z' }, manage_url: 'https://github.com/settings/installations/99' },
  { account: 'octo-team', kind: 'installation', status: 'installed', disconnect_also: [], app: { ...SHAPES.authorized.app, owner: 'octo-owner' }, installation: { installation_id: 100, repository_selection: 'selected', installed_at: '2026-11-01T10:00:00Z' },
    user: null, health: { last_ok_at: '2026-11-14T10:00:00Z', last_error: null, checked_at: '2026-11-14T10:00:00Z' }, manage_url: 'https://github.com/organizations/octo-team/settings/installations/100' },
  { account: 'octo-org', kind: 'org', status: 'unhealthy', disconnect_also: [], app: { ...SHAPES.authorized.app, owner: 'octo-org', slug: 'loam-for-octo-org' }, installation: null, user: null,
    health: { last_ok_at: null, checked_at: '2026-11-14T10:00:00Z', last_error: 'GitHub says this installation has been removed.' }, manage_url: 'https://github.com/organizations/octo-org/settings/installations' },
]
export async function pluginRuntimeFixture(options) {
  const app = await pluginHostFixture(options), writes = [], json = (res, value, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
  const states = ['running', 'starting', 'failing', 'stopping', 'stopped']
  const runtime = states.map(state => ({ id: `server-${state}`, name: `Example ${state}`, key: `yours:server-${state}`, on: state !== 'stopped', effective: true, layer: 'yours', switch: 'plugin', kind: ['app'],
    author: 'Fixture contributors', version: '1.0.0', description: 'A small example app.', has: { server: true }, touches: { server: true },
    icon: 'icon.svg', files: `/api/plugins/server-${state}/files/`, service: { state, log: state === 'failing' ? ['Starting example app…', 'Example could not bind its port.'] : [] } }))
  runtime.push({ id: 'example-skills', name: 'Example skills', key: 'yours:example-skills', effective: true, on: true, layer: 'yours', switch: 'listed', kind: ['skill'], has: {}, description: 'Tools for a quiet working day.' })
  runtime.push({ id: 'example-agents', name: 'Example agents', key: 'yours:example-agents', effective: true, on: true, layer: 'yours', switch: 'listed', kind: ['agent'], has: {}, description: 'A package of example agents.' })
  app.setRows([...(await shippedPlugins('public')), ...runtime])
  const icon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><rect width="40" height="40" rx="12" fill="#6e7a52"/><path d="M12 23c12 2 16-9 16-9s-15-1-16 9m3 5 9-10" fill="none" stroke="#fff" stroke-width="2"/></svg>'
  for (const row of runtime) {
    app.routes[`GET /v1/plugins/${row.id}`] = (req, res) => json(res, { plugin: row, rows: [row] })
    app.routes[`POST /v1/plugins/${row.id}/restart`] = (req, res, url, raw) => {
      writes.push({ method: 'POST', path: `/plugins/${row.id}/restart`, body: JSON.parse(raw) })
      if (!row.on) return json(res, { error: 'This plugin is off.' }, 409)
      row.service.state = 'starting'; json(res, { plugin: row, rows: [row] })
    }
    app.routes[`GET /v1/plugins/${row.id}/files/icon.svg`] = (req, res) => { res.writeHead(200, { 'content-type': 'image/svg+xml' }); res.end(icon) }
  }
  app.routes['GET /v1/apps'] = (req, res) => json(res, { sources: ['yours', 'installed', 'loam'], apps: runtime.filter(row => row.service).map(row => ({ name: row.name, kind: 'plugin', source: row.id === 'server-running' ? 'loam' : 'installed',
    plugin: row.id, url: `/x/${row.id}/`, icon: row.files + row.icon, on: row.on, state: row.service.state, access: 'private', frame: true })) })
  let accounts = structuredClone(githubAccounts)
  app.routes['GET /v1/integrations/github'] = (req, res) => json(res, SHAPES.authorized)
  app.routes['GET /v1/integrations/github/accounts'] = (req, res) => json(res, { accounts })
  app.routes['POST /v1/integrations/github/accounts'] = (req, res, url, raw) => {
    const body = JSON.parse(raw); writes.push({ method: 'POST', path: '/integrations/github/accounts', body })
    json(res, body.own_app ? { step: 'register', manifest: { name: 'Fixture app' }, post_url: `https://github.com/organizations/${body.org}/settings/apps/new?state=fixture`, state: 'fixture' }
      : { step: 'install', url: 'https://github.com/apps/example/installations/new?state=fixture', state: 'fixture' })
  }
  for (const account of accounts) {
    const route = `/integrations/github/accounts/${account.account}`
    app.routes[`POST /v1${route}/refresh`] = (req, res, url, raw) => {
      writes.push({ method: 'POST', path: route + '/refresh', body: JSON.parse(raw) }); json(res, { account, accounts })
    }
    app.routes[`DELETE /v1${route}`] = (req, res) => {
      writes.push({ method: 'DELETE', path: route }); const forgot = [account.account, ...account.disconnect_also]
      accounts = accounts.filter(row => !forgot.includes(row.account))
      json(res, { forgotten: true, account: account.account, kind: account.kind, forgot, accounts,
        uninstall_url: 'https://github.com/settings/installations/42', revoke_url: 'https://github.com/settings/connections/applications/example' })
    }
  }
  return { ...app, runtime, accountWrites: writes }
}
