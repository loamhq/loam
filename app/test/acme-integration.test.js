import { formatDate } from '../public/time.js'
import { nodePluginPage, pluginPageRoutes, fixtureGateway } from './plugin-page-fixture.js'
// Acme's plugin page, loaded from the catalog, calling a fixture gateway.
// No device-code login, account or credential service outside loopback is used.
//
// The token is the thing this file is about. It is typed into one field, sent
// to the broker's `PUT` and nowhere else, and after the `PUT` answers it is in
// no page storage, on no console line and nowhere in the DOM — asserted with a
// sentinel string that no fixture contains.
//
// **Fixture-only, and plainly so**: `test/fakedom.js` has no layout and no
// stylesheet. What is proved is which nodes the page builds from which state,
// and which request reaches the broker when something is tapped.

import test from 'node:test'
import assert from 'node:assert/strict'

import { fakeBroker, appAgainst, closing, until } from './harness.js'
import { browser, find, all, text } from './browser.js'
import { SHAPES as GH } from './fixtures/github.js'
import { SHAPES, SEVERAL, REFUSED, REVOKE_URL, expiringIn, PENDING, LOGIN_SEVERAL, LOGIN_REFUSED, EXPIRED, DENIED } from './fixtures/acme.js'

const json = (res, body, status = 200) => {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}
const line = (obj) => `data: ${JSON.stringify(obj)}\n\n`

const RECORD = {
  session_id: 'chat-1',
  agent: 'scout',
  provider: 'claude-code',
  workspace: '/w/scout',
  created: '2026-09-30T10:00:00Z',
  last_activity: '2026-09-30T11:00:00Z',
  last_event_at: '2026-09-30T11:00:00Z',
  state: 'exited',
  status: 'done',
  title: 'A chat with a composer',
  last_message: 'Done.',
  pinned: false,
  archived: false,
  record_seq: 1,
  seen_seq: 1,
  pending_permissions: [],
  turns: [],
}

// The token typed in. No fixture, no route and no sentence contains it, so
// finding it anywhere after the `PUT` answers is finding it leaked.
const SENTINEL = 'bc-SENTINEL-7f3a91d2-token'
const SENTINEL_2 = 'bc-SENTINEL-second-0c44'

// What the broker says Acme is. A test moves it on, as the host would.
let bc = SHAPES.unconfigured
// What the next `PUT`s answer, in order: `{status, body, then?}`.
let puts = []
let logins = []
let revoked = false
let acmeReadGate = null, heldAcmeReads = 0, readFailure = null
// Every Acme request, in order: method, route, body.
const asked = []

const routes = {
  'GET /v1/agents': (req, res) => json(res, { agents: [{ id: 'scout', name: 'Scout', emoji: null }] }),
  'GET /v1/providers': (req, res) => json(res, { providers: [{ id: 'claude-code', name: 'Claude Code', connected: true, reason: null, usage: null }] }),
  'GET /v1/limits': (req, res) => json(res, { attachment_max_bytes: 1024, attachment_types: [] }),
  'GET /v1/sessions': (req, res) => json(res, { sessions: [RECORD], live: 0, live_count: 0, cap: 2 }),
  'GET /v1/sessions/events': (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(': open ping=15000\n\n')
    res.write(line({ type: 'broker.session', ...RECORD }))
  },
  'GET /v1/sessions/chat-1/events': (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(': open ping=15000\n\n')
    res.write(line({ type: 'broker.session', ...RECORD, window_first_seq: 1, turns_before: 0 }))
    res.write(line({ type: 'broker.user_message', seq: 1, session_id: 'chat-1', text: 'hello' }))
    res.end()
  },
  // GitHub connected and well, so every warning on the page is Acme's.
  'GET /v1/integrations/github': (req, res) => json(res, GH.authorized),
}
const pluginRoutes = {
  'GET /x/acme/': async (req, res) => {
    if (acmeReadGate) { heldAcmeReads++; await acmeReadGate }
    json(res, readFailure?.body ?? bc, readFailure?.status ?? 200)
  },
  'POST /x/acme/login': (req, res, url, body) => {
    asked.push({ method: 'POST', route: 'login', body: JSON.parse(body) })
    const next = logins.shift() ?? { status: 201, body: PENDING, then: SHAPES.pending }
    if (next.then) bc = next.then
    json(res, next.body, next.status)
  },
  'PUT /x/acme/': (req, res, url, body) => {
    asked.push({ method: 'PUT', route: 'acme', body: JSON.parse(body) })
    const next = puts.shift() ?? { status: 500, body: { error: 'no answer staged' } }
    if (next.then) bc = next.then
    json(res, next.body, next.status)
  },
  'POST /x/acme/refresh': (req, res, url, body) => {
    asked.push({ method: 'POST', route: 'refresh', body: body ? JSON.parse(body) : null })
    json(res, bc)
  },
  'DELETE /x/acme/': (req, res) => {
    asked.push({ method: 'DELETE', route: 'acme', body: null })
    bc = SHAPES.unconfigured
    json(res, { forgotten: true, revoked, revoke_url: revoked ? null : REVOKE_URL })
  },
}
const plugin = { id: 'acme', name: 'Acme', api: 0, effective: true, on: true, switch: 'plugin', has: { page_main: true }, layer: 'yours', folder: 'fixture/acme' }
pluginPageRoutes(routes, plugin, 'test/fixtures/acme-page')
const hook = nodePluginPage('acme', 'test/fixtures/acme-page')
const broker = await fakeBroker(routes)
const app = await appAgainst(broker)
const gatewaySeen = fixtureGateway(app, '/x/acme/', pluginRoutes)
const page = browser(app.url)

// Every console line the page writes, kept: the token must be on none.
const said = []
for (const level of ['log', 'info', 'warn', 'error', 'debug']) {
  const real = console[level]
  console[level] = (...args) => {
    said.push(args.map((a) => (typeof a === 'string' ? a : (() => { try { return JSON.stringify(a) } catch { return String(a) } })())).join(' '))
    if (process.env.PRINT_CARDS) real(...args)
  }
}
globalThis.sessionStorage = (await import('./browser.js')).storage()

page.watch()
await import('../public/app.js')
page.unwatch()

test.after(async () => {
  hook.deregister()
  page.stop()
  await closing(broker, app)()
  setImmediate(() => process.exit(process.exitCode ?? 0))
})

const root = page.root
const click = (node) => node.fire('click', { preventDefault() {}, stopPropagation() {}, target: node, currentTarget: node })
const classes = (node) => (node?.getAttribute?.('class') ?? '').split(' ')
const card = () => all(root, 'integration').find((n) => classes(n).includes('acme')) ?? null
const sheet = () => all(root, 'sheet').find((n) => classes(n).includes('acme')) ?? null
const pills = () => all(card(), 'ghpill').map(text)
const pill = (label) => all(card(), 'ghpill').find((n) => text(n) === label)
const field = () => find(root, 'bctoken')
let input = null

// The card or sheet as markup, for the receipt and for the reader: tag, class,
// the attributes worth reading, text, children indented.
const SHOWN = ['href', 'id', 'type', 'placeholder', 'autocomplete', 'autocapitalize', 'autocorrect', 'spellcheck', 'role', 'aria-label']
function outline(node, depth = 0) {
  if (node.nodeType === 3) return node.textContent.trim() ? [`${'  '.repeat(depth)}"${node.textContent}"`] : []
  const cls = node.getAttribute('class')
  const svg = node.getAttribute('data-mark')
  const attrs = SHOWN.filter((a) => node.hasAttribute(a)).map((a) => ` ${a}="${node.getAttribute(a)}"`).join('')
  const mixed = node.childNodes.some((c) => c.nodeType !== 3)
  const own = mixed ? '' : node.textContent
  const head = `${'  '.repeat(depth)}<${node.tagName ?? node.nodeName}${cls ? ` class="${cls}"` : ''}${attrs}${svg ? ` data-mark="${svg}"` : ''}${node.hasAttribute('disabled') ? ' disabled' : ''}>${own}`
  if (svg || !mixed) return [head]
  return [head, ...node.childNodes.flatMap((c) => outline(c, depth + 1))]
}
const printed = (label, node = card()) => {
  const out = outline(node).join('\n')
  if (process.env.PRINT_CARDS) console.log(`--- ${label}\n${out}`)
  return out
}

// No inline style: the page's policy refuses a style attribute, and
// `test/fakedom.js` throws on `setAttribute('style', …)` as the browser refuses
// it — so drawing this subtree at all proves none was written. What can be
// there is a style the page set property by property (the CSSOM, which the
// policy allows) reflected back; this says every one of them is that.
function noStyle(node) {
  if (node.nodeType !== 1) return
  if (node.hasAttribute('style')) assert.ok(node.style.length > 0, `a style on <${node.tagName}> that the CSSOM did not write`)
  for (const child of node.childNodes) noStyle(child)
}

// Everywhere the page could have put the token: every node's text, every
// attribute and every field's value; both storages; every console line.
function leaks(secret) {
  const where = []
  const walk = (node) => {
    if (node.nodeType === 3) {
      if (node.textContent.includes(secret)) where.push('a text node')
      return
    }
    for (const { name, value } of node.attributes ?? []) if (value.includes(secret)) where.push(`attribute ${name}`)
    if (typeof node.value === 'string' && node.value.includes(secret)) where.push(`the value of <${node.tagName}>`)
    for (const child of node.childNodes ?? []) walk(child)
  }
  walk(root)
  // The field is kept between redraws and may be out of the tree: read it too.
  for (const store of [globalThis.localStorage, globalThis.sessionStorage]) {
    for (let i = 0; i < store.length; i += 1) {
      const key = store.key(i)
      if (key.includes(secret) || String(store.getItem(key)).includes(secret)) where.push(`storage ${key}`)
    }
  }
  for (const s of said) if (s.includes(secret)) where.push('a console line')
  return where
}

async function openIntegrations() {
  // Out and in again: opening Settings asks the broker for the state.
  page.press('Escape')
  await until(() => !find(root, 'sheet'))
  if (!find(root, 'roundbtn')) {
    click(all(find(root, 'thread-head'), 'iconbtn').find((n) => n.getAttribute('title') === 'Back'))
    await until(() => find(root, 'roundbtn'))
  }
  click(find(root, 'roundbtn'))
  await until(() => find(root, 'settings'))
  const row = all(root, 'setrow').find((n) => text(find(n, 'setrow-title')) === 'Integrations')
  click(row)
  click(await until(() => all(root, 'setrow').find(n => text(find(n, 'setrow-title')) === 'Acme')))
  // A fresh broker reading can replace the previous unsigned card with an
  // account picker. Wait for the expected account instead of accepting that
  // stale card and returning just before it disappears.
  if (bc.account) click(await until(() => all(root, 'setrow').find(n => text(find(n, 'setrow-title')) === bc.account.name)))
  await until(() => card())
}

test('acme card unconfigured: the mark and name, Not connected, what Loam uses it for, and Sign in first', async () => {
  bc = SHAPES.unconfigured
  await openIntegrations()
  await until(() => text(find(card(), 'bcline')) === 'Not connected')
  assert.equal(text(find(root, 'sheet-title')), 'Acme')
  assert.equal(find(card(), 'provider-hero'), null)
  assert.equal(find(card(), 'ghquiet'), null)
  assert.equal(text(find(card(), 'ghuses')), 'The agents read and write Acme as you.')
  assert.deepEqual(pills(), ['Sign in to Acme'])
  // Beside GitHub's, after it.
  const cards = all(root, 'integration')
  assert.equal(cards.length, 1, 'only the selected service is shown')
  noStyle(card())
  printed('unconfigured')
})

test('acme connect sheet: where the token comes from, one password field a phone will not mangle, and Connect', async () => {
  click(find(card(), 'bctokenway'))
  await until(() => sheet())
  assert.equal(text(find(sheet(), 'sheet-title')), 'Connect Acme')
  const where = find(sheet(), 'bcwhere')
  assert.equal(text(where), 'In Acme, open your profile and create a personal access token under Authentication, then paste it here.')
  const link = find(where, 'loginlink')
  assert.equal(text(link), 'personal access token')
  assert.equal(link.getAttribute('href'), 'https://app.acme.example/my/access_tokens')
  const input = field()
  assert.deepEqual(
    ['type', 'autocomplete', 'autocapitalize', 'autocorrect', 'spellcheck'].map((a) => input.getAttribute(a)),
    ['password', 'off', 'off', 'off', 'false'],
  )
  // Show and Hide swap the field's type and nothing else.
  click(find(sheet(), 'bcshow'))
  await until(() => text(find(sheet(), 'bcshow')) === 'Hide')
  assert.equal(input.getAttribute('type'), 'text')
  click(find(sheet(), 'bcshow'))
  await until(() => text(find(sheet(), 'bcshow')) === 'Show')
  assert.equal(input.getAttribute('type'), 'password')
  assert.equal(text(find(sheet(), 'bcconnect')), 'Connect')
  noStyle(sheet())
  printed('connect sheet', sheet())
})

test('acme connect: the PUT carries exactly {token}, then {token, account_id} after the several-accounts 400', async () => {
  puts = [
    { status: 400, body: SEVERAL },
    { status: 200, body: SHAPES.connected, then: SHAPES.connected },
  ]
  // The field is out of the tree while the accounts are shown, so it is held.
  input = field()
  input.value = SENTINEL
  click(find(sheet(), 'bcconnect'))
  // The accounts, as a choice, in place of the field.
  await until(() => all(sheet() ?? root, 'bcpick').length === 2)
  assert.deepEqual(all(sheet(), 'bcpick').map(text), ['Account 5550001', 'Account 5550002'])
  assert.equal(field(), null, 'the field is still drawn beside the accounts')
  assert.equal(input.value, '', 'the field still holds the token while the accounts are shown')
  printed('connect sheet, several accounts', sheet())
  click(all(sheet(), 'bcpick')[1])
  // On 200 the sheet closes and the card shows connected.
  await until(() => !sheet() && card() && text(find(card(), 'bcline')).startsWith('Connected'))
  assert.deepEqual(
    asked.filter((a) => a.method === 'PUT').map((a) => a.body),
    [{ token: SENTINEL }, { token: SENTINEL, account_id: 5550002 }],
  )
})

test('acme token: after the PUT answers, the sentinel is in no storage, no console line and nowhere in the DOM', async () => {
  assert.deepEqual(leaks(SENTINEL), [])
  // And not in the field, which is kept between redraws and out of the tree.
  assert.equal(input.value, '')
})

test('acme card connected: who on which account, the account linked, the good-until date, Refresh and Disconnect', async () => {
  const who = find(card(), 'bcline')
  assert.equal(text(who), 'Connected as Sam H on Account 5550002')
  const account = find(who, 'bcaccount')
  assert.equal(text(account), 'Account 5550002')
  assert.equal(account.getAttribute('href'), 'https://app.acme.example/5550002')
  assert.equal(text(find(card(), 'ghquiet')), `token good until ${formatDate('2027-10-07T00:00:00.000Z')}`)
  assert.equal(find(card(), 'ghquiet').getAttribute('class'), 'note ghquiet')
  assert.deepEqual(pills(), ['Refresh', 'Disconnect'])
  noStyle(card())
  printed('connected')
})

test('acme refresh: calls the refresh route and redraws from its answer', async () => {
  bc = SHAPES.noExpiry
  click(pill('Refresh'))
  await until(() => find(card(), 'ghdone'))
  assert.equal(asked.at(-1).route, 'refresh')
  assert.equal(text(find(card(), 'ghdone')), 'Checked with Acme just now.')
  assert.equal(text(find(card(), 'ghquiet')), 'no expiry on this token')
})

test('acme card connected, no expiry: says so, quietly', async () => {
  bc = SHAPES.noExpiry
  await openIntegrations()
  await until(() => text(find(card(), 'ghquiet')) === 'no expiry on this token')
  assert.equal(find(card(), 'ghquiet').getAttribute('class'), 'note ghquiet')
  assert.deepEqual(pills(), ['Refresh', 'Disconnect'])
  printed('connected, no expiry')
})

test('acme card unhealthy: the host’s sentence in the warning ink, Sign in again, and a dot on Integrations', async () => {
  bc = SHAPES.unhealthy
  await openIntegrations()
  await until(() => find(card(), 'ghquiet')?.getAttribute('class') === 'note ghquiet warn')
  assert.equal(text(find(card(), 'ghquiet')), REFUSED)
  assert.deepEqual(pills(), ['Sign in to Acme', 'Refresh', 'Disconnect'])
  noStyle(card())
  printed('unhealthy')
  while (text(find(root, 'sheet-title')) !== 'Settings') { click(find(root, 'backbtn')); await new Promise(resolve => setTimeout(resolve, 5)) }
  const row = await until(() => all(root, 'setrow').find((n) => text(find(n, 'setrow-title')) === 'Integrations'))
  assert.ok(find(row, 'navdot'), 'no dot on the Integrations row')
  assert.ok(text(find(row, 'setrow-sub')).length)
})

test('acme refused: the broker’s sentence in the warning ink on the sheet, the field cleared, Connect offered again', async () => {
  await openIntegrations()
  await until(() => pill('Sign in to Acme'))
  click(find(card(), 'bctokenway'))
  await until(() => sheet())
  puts = [{ status: 400, body: { error: REFUSED } }]
  field().value = SENTINEL_2
  click(find(sheet(), 'bcconnect'))
  await until(() => find(sheet(), 'bcerror'))
  assert.equal(text(find(sheet(), 'bcerror')), REFUSED)
  assert.equal(find(sheet(), 'bcerror').getAttribute('class'), 'note warn bcerror')
  assert.equal(field().value, '')
  assert.equal(text(find(sheet(), 'bcconnect')), 'Connect')
  assert.equal(find(sheet(), 'bcconnect').hasAttribute('disabled'), false)
  assert.deepEqual(asked.at(-1).body, { token: SENTINEL_2 })
  assert.deepEqual(leaks(SENTINEL_2), [])
  printed('connect sheet, refused', sheet())
})

test('acme disconnect: asks once, calls the delete route, and ends on the revoke link', async () => {
  bc = SHAPES.connected
  await openIntegrations()
  await until(() => pill('Disconnect'))
  click(pill('Disconnect'))
  await until(() => find(card(), 'ghconfirm'))
  assert.equal(asked.filter((a) => a.method === 'DELETE').length, 0, 'it did not ask first')
  click(all(find(card(), 'ghconfirm'), 'ghpill').find((n) => text(n) === 'Disconnect'))
  const links = await until(() => find(card(), 'ghlinks'))
  assert.equal(asked.at(-1).method, 'DELETE')
  const link = find(links, 'loginlink')
  assert.deepEqual([text(link), link.getAttribute('href')], ['Revoke the token in Acme', REVOKE_URL])
  // The state underneath is the broker's again: not connected.
  await until(() => text(find(card(), 'bcline')) === 'Not connected')
  printed('after disconnect')
})



const loginSheet = () => find(root, 'acme-login')
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const getCount = () => gatewaySeen.filter((c) => c.method === 'GET' && c.path === '/x/acme/').length

// Unlike the token sentinel, these values are intentionally shown and copied.
const copied = []
Object.defineProperty(globalThis.navigator, 'clipboard', { configurable: true, value: { writeText: async (value) => copied.push(value) } })

test('acme login pending: first pill, link and large code, two working Copy buttons, and automatic completion', async (t) => {
  bc = SHAPES.pending
  await openIntegrations()
  await until(() => pill('Sign in to Acme') && text(find(card(), 'bcline')) === 'Not connected' && !find(card(), 'ghquiet'))
  assert.equal(text(find(card(), 'bctokenway')), 'Paste a token instead')
  assert.equal(find(card(), 'bctokenway').getAttribute('class'), 'quietlink bctokenway')
  t.diagnostic(`pending card:\n${printed('pending')}`)
  click(pill('Sign in to Acme'))
  await until(() => loginSheet() && find(loginSheet(), 'devcode'))
  assert.equal(asked.at(-1).route, 'login')
  assert.deepEqual(asked.at(-1).body, {})
  assert.equal(text(find(loginSheet(), 'sheet-title')), 'Sign in to Acme')
  const link = find(loginSheet(), 'loginlink')
  assert.equal(link.getAttribute('href'), PENDING.verification_url)
  assert.equal(text(link), PENDING.verification_url)
  assert.equal(text(find(loginSheet(), 'devcode')), PENDING.user_code)
  assert.ok(text(loginSheet()).includes('Open the link on any device, sign in to Acme, and enter the code. This page notices by itself.'))
  const buttons = all(loginSheet(), 'devcopy')
  assert.deepEqual(buttons.map(text), ['Copy', 'Copy'])
  assert.deepEqual(buttons.map((n) => n.getAttribute('aria-label')), ['Copy the link', 'Copy the code'])
  noStyle(loginSheet())
  t.diagnostic(`sign-in sheet:\n${printed('sign-in', loginSheet())}`)
  click(buttons[0])
  await until(() => text(all(loginSheet(), 'devcopy')[0]) === 'Copied')
  click(all(loginSheet(), 'devcopy')[1])
  await until(() => text(all(loginSheet(), 'devcopy')[1]) === 'Copied')
  assert.deepEqual(copied, [PENDING.verification_url, PENDING.user_code])
  const before = getCount()
  bc = SHAPES.login
  await until(() => !loginSheet() && card() && text(find(card(), 'ghquiet')) === 'signed in · renews itself', { timeout: 5000 })
  assert.ok(getCount() > before, 'no automatic GET completed the sign-in')
  assert.deepEqual(pills(), ['Refresh', 'Disconnect'])
  assert.equal(text(find(card(), 'bcline')), 'Connected as Sam H on Account 5550002')
  noStyle(card())
  t.diagnostic(`connected by login:\n${printed('connected by login')}`)
  bc = SHAPES.connected
  await openIntegrations()
  await until(() => text(find(card(), 'ghquiet')) === `token good until ${formatDate('2027-10-07T00:00:00.000Z')}`)
  t.diagnostic(`connected by token:\n${printed('connected by token')}`)
})

test('acme login accounts: polling shows the picker and sends account_id to POST login, never PUT', async (t) => {
  bc = SHAPES.pending
  await openIntegrations()
  await until(() => pill('Sign in to Acme'))
  click(pill('Sign in to Acme'))
  await until(() => loginSheet() && find(loginSheet(), 'devcode'))
  bc = SHAPES.accounts
  await until(() => loginSheet() && all(loginSheet(), 'bcpick').length === 2, { timeout: 5000 })
  assert.ok(text(loginSheet()).includes('Choose the account Loam should use'))
  assert.deepEqual(all(loginSheet(), 'bcpick').map(text), ['Account 5550001', 'Account 5550002'])
  t.diagnostic(`login accounts:\n${printed('login accounts', loginSheet())}`)
  const putCount = asked.filter((a) => a.method === 'PUT').length
  logins = [{ status: 200, body: SHAPES.login, then: SHAPES.login }]
  click(all(loginSheet(), 'bcpick')[1])
  await until(() => !loginSheet() && card() && text(find(card(), 'ghquiet')) === 'signed in · renews itself')
  assert.deepEqual(asked.at(-1), { method: 'POST', route: 'login', body: { account_id: 5550002 } })
  assert.equal(asked.filter((a) => a.method === 'PUT').length, putCount)
})

test('acme login reopening after approval: the existing accounts 400 becomes the login picker', async () => {
  bc = SHAPES.accounts
  await openIntegrations()
  await until(() => pill('Sign in to Acme'))
  logins = [{ status: 400, body: LOGIN_SEVERAL }]
  click(pill('Sign in to Acme'))
  await until(() => loginSheet() && all(loginSheet(), 'bcpick').length === 2)
  assert.equal(find(loginSheet(), 'bcerror'), null)
  click(find(loginSheet(), 'bccancel'))
  await until(() => !loginSheet())
})

test('acme login expired or denied: broker warning and Try again; failed sign-in is no notice', async (t) => {
  for (const [failed, sentence] of [[SHAPES.expired, EXPIRED], [SHAPES.denied, DENIED]]) {
    bc = SHAPES.pending
    await openIntegrations()
    await until(() => pill('Sign in to Acme'))
    click(pill('Sign in to Acme'))
    await until(() => loginSheet() && find(loginSheet(), 'devcode'))
    bc = failed
    await until(() => loginSheet() && find(loginSheet(), 'bcretry'), { timeout: 5000 })
    assert.equal(text(find(loginSheet(), 'bcerror')), sentence)
    assert.equal(find(loginSheet(), 'bcerror').getAttribute('class'), 'note warn bcerror')
    assert.equal(find(loginSheet(), 'devcode'), null)
    t.diagnostic(`expired/denied sheet:\n${printed('failed sign-in', loginSheet())}`)
    click(find(loginSheet(), 'bcretry'))
    await until(() => loginSheet() && find(loginSheet(), 'devcode'))
    assert.equal(find(loginSheet(), 'bcerror'), null)
    click(find(loginSheet(), 'bccancel'))
    await until(() => !loginSheet())
    bc = failed
    await openIntegrations()
    await until(() => text(find(card(), 'ghquiet')) === sentence)
    assert.equal(find(card(), 'ghquiet').getAttribute('class'), 'note ghquiet warn')
    assert.equal(pills()[0], 'Sign in to Acme')
    t.diagnostic(`expired/denied card:\n${printed('failed sign-in card')}`)
    while (text(find(root, 'sheet-title')) !== 'Settings') { click(find(root, 'backbtn')); await new Promise(resolve => setTimeout(resolve, 5)) }
    const row = await until(() => all(root, 'setrow').find((n) => text(find(n, 'setrow-title')) === 'Integrations'))
    assert.equal(find(row, 'navdot'), null)
  }
})

test('acme login unhealthy: host sentence, Sign in first, notice and tab dot', async (t) => {
  bc = SHAPES.loginUnhealthy
  await openIntegrations()
  await until(() => text(find(card(), 'ghquiet')) === LOGIN_REFUSED)
  assert.deepEqual(pills(), ['Sign in to Acme', 'Refresh', 'Disconnect'])
  assert.equal(find(card(), 'ghquiet').getAttribute('class'), 'note ghquiet warn')
  noStyle(card())
  t.diagnostic(`unhealthy by login:\n${printed('unhealthy by login')}`)
  while (text(find(root, 'sheet-title')) !== 'Settings') { click(find(root, 'backbtn')); await new Promise(resolve => setTimeout(resolve, 5)) }
  const row = await until(() => all(root, 'setrow').find((n) => text(find(n, 'setrow-title')) === 'Integrations'))
  assert.ok(find(row, 'navdot'))
  page.press('Escape')
  await until(() => !find(root, 'sheet'))

})

test('acme login Cancel and dismissal stop polling; reopening keeps the same pending code', async () => {
  for (const dismiss of [() => click(find(loginSheet(), 'bccancel')), () => click(find(loginSheet(), 'sheet-x')), () => page.press('Escape'), () => click(find(root, 'scrim'))]) {
    bc = SHAPES.pending
    await openIntegrations()
    await until(() => pill('Sign in to Acme'))
    logins = [{ status: 200, body: PENDING, then: SHAPES.pending }]
    click(pill('Sign in to Acme'))
    await until(() => loginSheet() && find(loginSheet(), 'devcode'))
    assert.equal(text(find(loginSheet(), 'devcode')), PENDING.user_code)
    const before = getCount()
    dismiss()
    await until(() => !loginSheet())
    await wait(3200)
    assert.equal(getCount(), before, 'a GET followed Cancel/dismissal')
  }
})

test('acme reopening waits for the connected account when a stale unsigned card is still visible', async () => {
  bc = SHAPES.unconfigured
  await openIntegrations()
  await until(() => card() && text(find(card(), 'bcline')) === 'Not connected')
  bc = SHAPES.login
  let release, opened = false
  heldAcmeReads = 0
  acmeReadGate = new Promise(resolve => { release = resolve })
  const opening = openIntegrations().then(() => { opened = true })
  try {
    await until(() => heldAcmeReads > 0 && card())
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(opened, false, 'the previous unsigned card cannot complete a connected-account open')
  } finally {
    acmeReadGate = null; release()
    await opening
  }
  assert.equal(text(find(root, 'sheet-title')), SHAPES.login.account.name)
  await until(() => pill('Disconnect'))
})

test('acme disconnect revoked: Signed out of Acme and Done, with no revoke link', async (t) => {
  bc = SHAPES.login
  revoked = true
  await openIntegrations()
  await until(() => pill('Disconnect'))
  click(pill('Disconnect'))
  await until(() => find(card(), 'ghconfirm'))
  click(all(find(card(), 'ghconfirm'), 'ghpill').find((n) => text(n) === 'Disconnect'))
  await until(() => find(card(), 'ghlinks'))
  assert.equal(text(find(find(card(), 'ghlinks'), 'signline')), 'Signed out of Acme')
  assert.equal(find(find(card(), 'ghlinks'), 'loginlink'), null)
  assert.equal(text(find(find(card(), 'ghlinks'), 'ghpill')), 'Done')
  await until(() => text(find(card(), 'bcline')) === 'Not connected')
  t.diagnostic(`revoked disconnect:\n${printed('signed out')}`)
  click(find(find(card(), 'ghlinks'), 'ghpill'))
  await until(() => !find(card(), 'ghlinks'))
  assert.equal(pills()[0], 'Sign in to Acme')
})

test('plugin traffic uses the same-origin gateway without the broker bearer', () => {
  assert.ok(gatewaySeen.length > 0)
  assert.ok(gatewaySeen.every(request => request.path.startsWith('/x/acme/') && request.auth === undefined))
  assert.equal(broker.seen.some(request => request.path.includes('/integrations/acme')), false)
})

test('an enabled plugin keeps its card and shows gateway 404/502 sentences', async () => {
  bc = SHAPES.unconfigured
  await openIntegrations()
  await until(() => text(find(card(), 'bcline')) === 'Not connected')
  try {
    for (const status of [404, 502]) {
      const error = status === 404 ? "no plugin 'acme' has a server here" : "acme's server is not answering: it is off, starting or failing"
      readFailure = { status, body: { error } }
      await openIntegrations()
      await until(() => text(find(card(), 'gherror')) === error)
      assert.ok(card(), 'catalog availability must survive a gateway error')
    }
  } finally { readFailure = null }
})
