import { formatDate } from '../public/time.js'
// Organizations' GitHub apps, driven: the page, the real `/api`, a broker that
// is a recording. Each org app its own card in each of its states, Connect an
// organization from a connected page, the org's Install asking for its own
// address, and Disconnect saying it forgets every app.
//
// **Fixture-only, and plainly so**, like `test/github.test.js`: the shapes are
// the broker's own (`test/fixtures/github.js`), and the running broker will
// not send them until the restart that brings organizations' apps.

import test from 'node:test'
import assert from 'node:assert/strict'

import { fakeBroker, appAgainst, closing, until } from './harness.js'
import { browser, find, all, text } from './browser.js'
import { SHAPES, ORG_SHAPES, withOrgs } from './fixtures/github.js'

const json = (res, body, status = 200) => {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}

const ORG_POST = 'https://github.com/organizations/octo-org/settings/apps/new?state=m2'
let gh = SHAPES.authorized
let manifestAnswer = { manifest: '{"name":"Loam for octo-org"}', post_url: ORG_POST, state: 'm2' }
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
  'POST /v1/integrations/github/manifest': (req, res, url, body) => {
    asked.push({ route: 'manifest', body: JSON.parse(body) })
    json(res, manifestAnswer)
  },
  'POST /v1/integrations/github/install': (req, res, url, body) => {
    asked.push({ route: 'install', body: JSON.parse(body) })
    json(res, { url: 'https://github.com/apps/loam-for-octo-org/installations/new?state=i2', state: 'i2' })
  },
  'DELETE /v1/integrations/github': (req, res) => {
    asked.push({ route: 'forget' })
    gh = withOrgs(SHAPES.unregistered)
    json(res, {
      forgotten: true,
      uninstall_url: 'https://github.com/settings/installations',
      revoke_url: 'https://github.com/settings/apps/authorizations',
      delete_app_url: 'https://github.com/settings/apps/loam/advanced',
    })
  },
})
const app = await appAgainst(broker)
const page = browser(app.url)

const assigned = []
globalThis.location = { pathname: '/', search: '', assign: (url) => assigned.push(url) }
globalThis.history = { replaceState() {} }
const submitted = []
document.body = { appendChild() {} }
const makeElement = document.createElement
document.createElement = (tag) => {
  const node = makeElement(tag)
  if (String(tag).toLowerCase() === 'form') node.submit = () => submitted.push(node)
  return node
}

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
const first = () => find(root, 'integration-shared-actions')
const orgCard = () => find(root, 'org')
const connect = () => find(root, 'connect')
const pills = (card) => all(card, 'ghpill').map(text)
const pill = (card, label) => all(card, 'ghpill').find((n) => text(n) === label)

function outline(node, depth = 0) {
  if (node.nodeType === 3) return []
  const cls = node.getAttribute('class')
  const href = node.getAttribute('href')
  const own = node.childNodes.every((c) => c.nodeType === 3) ? node.textContent : ''
  const svg = node.getAttribute('data-mark')
  const head = `${'  '.repeat(depth)}<${node.tagName ?? node.nodeName}${cls ? ` class="${cls}"` : ''}${href ? ` href="${href}"` : ''}${svg ? ` data-mark="${svg}"` : ''}>${own}`
  if (svg) return [head]
  return [head, ...node.childNodes.flatMap((c) => outline(c, depth + 1))]
}
const printed = (label) => {
  if (process.env.PRINT_CARDS) console.log(`--- ${label}\n${all(root, 'integration').flatMap((c) => outline(c)).join('\n')}`)
}

async function openIntegrations() {
  page.press('Escape')
  await until(() => !find(root, 'sheet'))
  click(find(root, 'roundbtn'))
  await until(() => find(root, 'settings'))
  click(all(root, 'setrow').find(n => text(find(n, 'setrow-title')) === 'Connections'))
  click(await until(() => all(root, 'setrow').find(n => text(find(n, 'setrow-title')) === 'GitHub')))
  await until(() => first())
}
async function openOrg() {
  click(await until(() => all(root, 'setrow').find(n => text(find(n, 'setrow-title')) === 'octo-org')))
  await until(() => orgCard())
}
const fields = () => all(connect(), 'field').map(f => f.childNodes[0])

test('an older host offers its one account without promising another connection', async () => {
  gh = SHAPES.authorized
  await openIntegrations()
  assert.equal(connect(), null)
  assert.equal(orgCard(), null)
  assert.deepEqual(pills(first()), ['Refresh', 'Disconnect'])
  assert.ok(all(root, 'setrow').some(n => text(find(n, 'setrow-title')) === 'octo-owner'))
})
test('adding an account stays within GitHub, below the account list', async () => {
  gh = withOrgs(SHAPES.authorized)
  await openIntegrations()
  assert.deepEqual(pills(connect()), ['Add an account'])
  assert.equal(orgCard(), null)
  assert.deepEqual(pills(first()), ['Refresh', 'Disconnect'])
})
test('Add an account validates the organization before asking the broker', async () => {
  click(pill(connect(), 'Add an account'))
  await until(() => fields().length === 2)
  assert.equal(text(find(connect(), 'setrow-title')), 'Add an account')
  assert.deepEqual(pills(connect()), ['Continue to GitHub', 'Cancel'])
  fields()[0].value = 'https://github.com/octo-org'
  click(pill(connect(), 'Continue to GitHub'))
  await until(() => find(connect(), 'gherror'))
  assert.match(text(find(connect(), 'gherror')), /not an organization’s login/)
  assert.equal(asked.length, 0)
})
test('a personal create-page answer cannot replace the organization app', async () => {
  manifestAnswer = { manifest: '{"name":"Loam"}', post_url: 'https://github.com/settings/apps/new?state=m1', state: 'm1' }
  fields()[0].value = 'octo-org'
  click(pill(connect(), 'Continue to GitHub'))
  await until(() => asked.length === 1 && find(connect(), 'gherror'))
  assert.match(text(find(connect(), 'gherror')), /did not name octo-org’s own create page/)
  assert.equal(submitted.length, 0)
  asked.length = 0
})
test('Continue preserves org/name and posts only the broker manifest to the organization create page', async () => {
  manifestAnswer = { manifest: '{"name":"Loam for octo-org"}', post_url: ORG_POST, state: 'm2' }
  await until(() => pill(connect(), 'Continue to GitHub')?.hasAttribute('disabled') === false)
  fields()[0].value = ' octo-org '
  fields()[1].value = 'Loam at Octo'
  click(pill(connect(), 'Continue to GitHub'))
  await until(() => submitted.length === 1)
  assert.deepEqual(asked, [{ route: 'manifest', body: { org: 'octo-org', name: 'Loam at Octo' } }])
  const form = submitted[0]
  assert.equal(form.getAttribute('action'), ORG_POST)
  assert.equal(form.getAttribute('method'), 'post')
  assert.deepEqual(['name', 'value'].map(k => form.childNodes[0].getAttribute(k)), ['manifest', '{"name":"Loam for octo-org"}'])
  asked.length = 0
  fields()[1].value = ''
  await until(() => pill(connect(), 'Continue to GitHub')?.hasAttribute('disabled') === false)
  click(pill(connect(), 'Continue to GitHub'))
  await until(() => submitted.length === 2)
  assert.deepEqual(asked, [{ route: 'manifest', body: { org: 'octo-org' } }])
  asked.length = 0
})
test('an unfinished account opens its own detail and Install scopes the request to that organization', async () => {
  gh = withOrgs(SHAPES.authorized, ORG_SHAPES.registered)
  await openIntegrations()
  assert.equal(all(root, 'setrow').length, 2)
  assert.deepEqual(pills(first()), ['Refresh all accounts', 'Disconnect all accounts'])
  await openOrg()
  assert.equal(text(find(root, 'sheet-title')), 'octo-org')
  assert.equal(text(find(orgCard(), 'ghword')), 'Created, not installed yet')
  assert.equal(find(orgCard(), 'ghline').getAttribute('href'), 'https://github.com/apps/loam-for-octo-org')
  click(pill(orgCard(), 'Install'))
  await until(() => assigned.length === 1)
  assert.deepEqual(asked, [{ route: 'install', body: { org: 'octo-org' } }])
  assert.deepEqual(assigned, ['https://github.com/apps/loam-for-octo-org/installations/new?state=i2'])
  asked.length = 0
})
test('an authorized account shows scope, sign-in and renewal with shared actions explicitly labelled', async () => {
  gh = withOrgs(SHAPES.authorized, ORG_SHAPES.authorized)
  await openIntegrations(); await openOrg()
  assert.deepEqual(all(orgCard(), 'ghline').map(text), ['selected repositories', 'Signed in as octo-owner', 'GitHub App: loam-for-octo-org'])
  assert.equal(text(find(orgCard(), 'ghword')), 'Connected')
  assert.equal(text(find(orgCard(), 'ghquiet')), `Sign in again by ${formatDate('2027-04-02T21:37:31Z', { year: 'numeric', month: 'short', day: 'numeric' })}`)
  assert.deepEqual(pills(orgCard()), ['Refresh all accounts', 'Disconnect all accounts'])
  assert.match(text(first()), /every GitHub account/)
})
test('an unhealthy organization keeps its own warning and reconnect scope', async () => {
  gh = withOrgs(SHAPES.authorized, ORG_SHAPES.unhealthy)
  await openIntegrations(); await openOrg()
  assert.equal(text(find(orgCard(), 'ghquiet')), ORG_SHAPES.unhealthy.health.last_error)
  assert.equal(find(orgCard(), 'ghword').getAttribute('class'), 'setrow-sub ghword warn')
  assert.deepEqual(pills(orgCard()), ['Reconnect', 'Refresh all accounts', 'Disconnect all accounts'])
})
test('Disconnect all explains scope, asks first, then retains every GitHub cleanup link', async () => {
  await openIntegrations()
  click(pill(first(), 'Disconnect all accounts'))
  const confirm = await until(() => find(first(), 'ghconfirm'))
  assert.match(text(find(confirm, 'signline')), /all GitHub accounts.*includes octo-org/)
  assert.deepEqual(pills(confirm), ['Disconnect all accounts', 'Keep it'])
  assert.equal(asked.length, 0)
  click(pill(confirm, 'Disconnect all accounts'))
  const links = await until(() => find(root, 'ghlinks'))
  assert.deepEqual(asked, [{ route: 'forget' }])
  assert.deepEqual(all(links, 'loginlink').map(a => [text(a), a.getAttribute('href')]).at(-1), ['octo-org’s app: uninstall and delete it on GitHub', 'https://github.com/apps/loam-for-octo-org'])
})
