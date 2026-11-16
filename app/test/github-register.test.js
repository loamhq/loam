// Coming back from GitHub's create page, taken: the page opened at
// `/integrations/github/registered?code&state`, the query handed to the
// broker's register route, Settings open on Integrations with the card at
// Registered — and Install sending the browser to the address the register
// answer already carried, without asking the broker for another.
//
// A process of its own because the page reads where it was opened once, at
// boot; `test/github.test.js` is the other return. Fixture-only, like it.

import test from 'node:test'
import assert from 'node:assert/strict'

import { fakeBroker, appAgainst, closing, until } from './harness.js'
import { browser, find, all, text } from './browser.js'
import { SHAPES } from './fixtures/github.js'

const json = (res, body, status = 200) => {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}

const INSTALL_URL = 'https://github.com/apps/loam/installations/new?state=r1'
let gh = SHAPES.unregistered
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
    gh = SHAPES.registered
    json(res, { ...gh, install: { url: INSTALL_URL, state: 'r1' } })
  },
  'POST /v1/integrations/github/install': (req, res) => {
    asked.push({ route: 'install' })
    json(res, { url: 'https://github.com/apps/loam/installations/new?state=other', state: 'other' })
  },
})
const app = await appAgainst(broker)
const page = browser(app.url)

const assigned = []
const replaced = []
globalThis.location = { pathname: '/integrations/github/registered', search: '?code=c1&state=s1', assign: (url) => assigned.push(url) }
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
const card = () => find(root, 'integration')
const pills = () => all(card(), 'ghpill').map(text)

test('the register return hands code and state to the broker, and the card is at Registered', async () => {
  await until(() => asked.length === 1)
  assert.deepEqual(asked[0], { route: 'register', body: { code: 'c1', state: 's1' } })
  assert.deepEqual(replaced, ['/'])
  await until(() => card() && text(find(card(), 'ghline')) === 'GitHub App: loam')
  assert.equal(text(find(root, 'sheet-title')), 'GitHub')
  assert.equal(find(card(), 'gherror'), null)
  await until(() => pills()[0] === 'Install')
  assert.deepEqual(pills(), ['Install'])
})

test('Install in the same visit goes straight to the address the register answer carried', async () => {
  click(all(card(), 'ghpill')[0])
  await until(() => assigned.length === 1)
  assert.deepEqual(assigned, [INSTALL_URL])
  assert.equal(asked.some((a) => a.route === 'install'), false, 'the broker was asked for a second install address')
})
