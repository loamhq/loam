// Coming back from an organization's create page, taken, while the first app is
// connected: the register answer is the whole state plus the org app's
// install address, and the org's own card offers that address — not the first
// app's card, which has nothing to install.
//
// A process of its own because the page reads where it was opened once, at
// boot; `test/github-register.test.js` is the first app's return. Fixture-only.

import test from 'node:test'
import assert from 'node:assert/strict'

import { fakeBroker, appAgainst, closing, until } from './harness.js'
import { browser, find, all, text } from './browser.js'
import { SHAPES, ORG_SHAPES, withOrgs } from './fixtures/github.js'

const json = (res, body, status = 200) => {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}

const INSTALL_URL = 'https://github.com/apps/loam-for-octo-org/installations/new?state=r2'
let gh = withOrgs(SHAPES.authorized)
const asked = []

const broker = await fakeBroker({
  'GET /v1/agents': (req, res) => json(res, { agents: [] }),
  'GET /v1/providers': (req, res) => json(res, { providers: [] }),
  'GET /v1/limits': (req, res) => json(res, { attachment_max_bytes: 1024, attachment_types: [] }),
  'GET /v1/sessions': (req, res) => json(res, { sessions: [], live: 0, live_count: 0, cap: 2 }),
  'GET /v1/sessions/events': (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(': open ping=15000\n\n')
  },
  'GET /v1/integrations/github': (req, res) => json(res, gh),
  'POST /v1/integrations/github/register': (req, res, url, body) => {
    asked.push({ route: 'register', body: JSON.parse(body) })
    gh = withOrgs(SHAPES.authorized, ORG_SHAPES.registered)
    json(res, { ...gh, install: { url: INSTALL_URL, state: 'r2' } })
  },
  'POST /v1/integrations/github/install': (req, res) => {
    asked.push({ route: 'install' })
    json(res, { url: 'https://github.com/apps/loam-for-octo-org/installations/new?state=other', state: 'other' })
  },
})
const app = await appAgainst(broker)
const page = browser(app.url)

const assigned = []
const replaced = []
globalThis.location = { pathname: '/integrations/github/registered', search: '?code=c3&state=s3', assign: (url) => assigned.push(url) }
globalThis.history = { replaceState: (a, b, url) => replaced.push(url) }

page.watch()
await import('../public/app.js')
page.unwatch()

test.after(async () => {
  page.stop()
  await closing(broker, app)()
  setImmediate(() => process.exit(process.exitCode ?? 0))
})

const root = page.root
const click = (node) => node.fire('click', { preventDefault() {}, stopPropagation() {}, target: node, currentTarget: node })
const orgCard = () => find(root, 'org')
const first = () => all(root, 'integration').find((n) => !/\b(org|connect)\b/.test(n.getAttribute('class')))

test('the org’s register return lands on its own card, with Install and what to do next', async () => {
  await until(() => asked.length === 1)
  assert.deepEqual(asked[0], { route: 'register', body: { code: 'c3', state: 's3' } })
  assert.deepEqual(replaced, ['/'])
  await until(() => orgCard() && find(orgCard(), 'ghdone'))
  assert.equal(text(find(root, 'sheet-title')), 'octo-org')
  assert.equal(text(find(orgCard(), 'ghdone')), 'Created on GitHub. Install it on octo-org next.')
  await until(() => !all(orgCard(), 'ghpill')[0].hasAttribute('disabled'))
  assert.deepEqual(all(orgCard(), 'ghpill').map(text), ['Install', 'Refresh all accounts', 'Disconnect all accounts'])
  // The first app, connected, is as it was and says nothing about it.
  assert.equal(first(), undefined)
})

test('the org’s Install goes straight to the address the register answer carried', async () => {
  click(all(orgCard(), 'ghpill')[0])
  await until(() => assigned.length === 1)
  assert.deepEqual(assigned, [INSTALL_URL])
  assert.equal(asked.some((a) => a.route === 'install'), false, 'the broker was asked for a second install address')
})
