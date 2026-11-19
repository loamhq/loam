import { formatDate } from '../public/time.js'
// GitHub's card, driven: the page, the real `/api`, a broker that is a
// recording — and a browser that comes back from GitHub's install page with a
// `state` the broker never issued.
//
// `app.js` is a module with one instance and reads where it was opened once, at
// boot, so a return from GitHub is a *process*: this file is the install
// return, refused; `test/github-register.test.js` is the register return,
// taken. Everything else about the card is asked of this one page by changing
// what the broker says and opening Settings again, which is what a person does.
//
// **Fixture-only, and plainly so**: `test/fakedom.js` has no layout and no
// stylesheet. What is proved is which nodes the page builds from which state,
// and which request reaches the broker when a pill is tapped.

import test from 'node:test'
import assert from 'node:assert/strict'

import { fakeBroker, appAgainst, closing, until } from './harness.js'
import { browser, find, all, text } from './browser.js'
import { SHAPES } from './fixtures/github.js'

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

// What the broker says GitHub is. A test moves it on, as the host would.
let gh = SHAPES.installed
// Every integration request, in order: method, route, body.
const asked = []
const REFUSED = 'That state is not one this broker issued, or it has already been used.'
// What the next manifest request answers: a manifest, or GitHub's refusal.
let manifestAnswer = { status: 200, body: { manifest: '{"name":"Loam"}', post_url: 'https://github.com/settings/apps/new?state=m1', state: 'm1' } }

const note = (method, route) => (req, res, url, body) => {
  asked.push({ method, route, body: body ? JSON.parse(body) : null })
}

const broker = await fakeBroker({
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
  'GET /v1/integrations/github': (req, res) => json(res, gh),
  'POST /v1/integrations/github/callback': (req, res, url, body) => {
    note('POST', 'callback')(req, res, url, body)
    json(res, { error: REFUSED }, 400)
  },
  'POST /v1/integrations/github/manifest': (req, res, url, body) => {
    note('POST', 'manifest')(req, res, url, body)
    json(res, manifestAnswer.body, manifestAnswer.status)
  },
  'POST /v1/integrations/github/install': (req, res, url, body) => {
    note('POST', 'install')(req, res, url, body)
    json(res, { url: 'https://github.com/apps/loam/installations/new?state=i1', state: 'i1' })
  },
  'POST /v1/integrations/github/refresh': (req, res, url, body) => {
    note('POST', 'refresh')(req, res, url, body)
    json(res, gh)
  },
  'DELETE /v1/integrations/github': (req, res, url, body) => {
    note('DELETE', 'github')(req, res, url, body)
    gh = SHAPES.unregistered
    json(res, {
      forgotten: true,
      uninstall_url: 'https://github.com/settings/installations/99',
      revoke_url: 'https://github.com/settings/connections/applications/Iv1.x',
      delete_app_url: 'https://github.com/settings/apps/loam/advanced',
    })
  },
})
const app = await appAgainst(broker)
const page = browser(app.url)

// Where the page was opened: GitHub's install return, with a `state` the
// broker will refuse. And what a browser gives a page about its own address.
const assigned = []
const replaced = []
globalThis.location = {
  pathname: '/integrations/github/installed',
  search: '?code=c2&installation_id=99&setup_action=install&state=BAD',
  assign: (url) => assigned.push(url),
}
globalThis.history = { replaceState: (a, b, url) => replaced.push(url) }
// The manifest form is appended to the body and submitted; a submitted form is
// recorded rather than navigated.
const appended = []
const submitted = []
document.body = { appendChild: (node) => appended.push(node) }
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
const card = () => find(root, 'integration')
// Null-safe: the card is briefly gone while a redraw replaces it, and a
// wait that read it then threw instead of waiting (under load, 2026-11-19).
const pills = () => (card() ? all(card(), 'ghpill').map(text) : [])
const pill = (label) => (card() ? all(card(), 'ghpill').find((n) => text(n) === label) : undefined)
const lines = () => (card() ? all(card(), 'ghline').map(text) : [])

// The card as markup, for the receipt and for the reader: tag, class, text and
// href, children indented. Everything else is this page's business.
function outline(node, depth = 0) {
  if (node.nodeType === 3) return []
  const cls = node.getAttribute('class')
  const href = node.getAttribute('href')
  const own = node.childNodes.every((c) => c.nodeType === 3) ? node.textContent : ''
  const svg = node.getAttribute('data-mark')
  const head = `${'  '.repeat(depth)}<${node.tagName ?? node.nodeName}${cls ? ` class="${cls}"` : ''}${href ? ` href="${href}"` : ''}${svg ? ` data-mark="${svg}"` : ''}${node.hasAttribute('disabled') ? ' disabled' : ''}>${own}`
  if (svg) return [head]
  return [head, ...node.childNodes.flatMap((c) => outline(c, depth + 1))]
}
const printed = (label) => {
  const text = outline(card()).join('\n')
  if (process.env.PRINT_CARDS) console.log(`--- ${label}\n${text}`)
  return text
}

async function openIntegrations() {
  // Out and in again: opening Settings asks the broker for the state.
  page.press('Escape')
  await until(() => !find(root, 'sheet'))
  click(find(root, 'roundbtn'))
  await until(() => find(root, 'settings'))
  const row = all(root, 'setrow').find((n) => text(find(n, 'setrow-title')) === 'Connections')
  click(row)
  click(await until(() => all(root, 'setrow').find(n => text(find(n, 'setrow-title')) === 'GitHub')))
  const account = await until(() => card() || all(root, 'setrow').find(n => text(find(n, 'setrow-title')) === 'octo-owner'))
  if (!card()) click(account)
  await until(() => card())
}

test('coming back from the install page hands the query to the callback route, whole', async () => {
  await until(() => asked.some((a) => a.route === 'callback'))
  assert.deepEqual(asked.find((a) => a.route === 'callback').body, { code: 'c2', installation_id: 99, setup_action: 'install', state: 'BAD' })
  // The address bar is put back, so a reload does not hand a used code over.
  assert.deepEqual(replaced, ['/'])
})

test('a refused state is the broker’s sentence on the card, in the warning ink, with the pill to try again', async () => {
  // Settings reopened on Integrations, by itself.
  await until(() => find(root, 'gherror'))
  assert.ok(find(root, 'settings'), 'Settings did not open')
  assert.equal(text(find(root, 'sheet-title')), 'octo-owner')
  assert.equal(text(find(card(), 'gherror')), REFUSED)
  assert.equal(find(card(), 'gherror').getAttribute('class'), 'note warn gherror')
  // The broker is still where it was — installed, nobody signed in — so the
  // retry is Reconnect.
  await until(() => pills().length > 0 && !all(card(), 'ghpill')[0].hasAttribute('disabled'))
  assert.deepEqual(pills(), ['Reconnect'])
  printed('installed, after a refused state')
})

test('Reconnect asks the broker for an install address and sends the browser there', async () => {
  click(pill('Reconnect'))
  await until(() => assigned.length === 1)
  assert.deepEqual(assigned, ['https://github.com/apps/loam/installations/new?state=i1'])
  assert.equal(asked.at(-1).route, 'install')
})

test('unregistered: Not set up, and Set up on GitHub', async () => {
  gh = SHAPES.unregistered
  await openIntegrations()
  await until(() => lines()[0] === 'Not connected')
  assert.deepEqual(lines(), ['Not connected'])
  assert.deepEqual(pills(), ['Set up on GitHub'])
  assert.equal(text(find(root, 'sheet-title')), 'GitHub')
  assert.equal(find(card(), 'provider-hero'), null)
  assert.equal(text(find(card(), 'ghuses')), 'Connect GitHub to create repositories and share engineering work.')
  assert.equal(find(card(), 'ghquiet'), null)
  printed('unregistered')
})

test('a refusal from GitHub on the manifest is its sentence, and no form is built', async () => {
  manifestAnswer = { status: 502, body: { error: 'GitHub refused: the name Loam is taken.' } }
  click(pill('Set up on GitHub'))
  await until(() => card() && find(card(), 'gherror'))
  assert.equal(text(find(card(), 'gherror')), 'GitHub refused: the name Loam is taken.')
  assert.equal(submitted.length, 0)
  await until(() => pill('Set up on GitHub') && !pill('Set up on GitHub').hasAttribute('disabled'))
})

test('Set up on GitHub posts the broker’s manifest to GitHub as a form, in a hidden field', async () => {
  manifestAnswer = { status: 200, body: { manifest: '{"name":"Loam"}', post_url: 'https://github.com/settings/apps/new?state=m1', state: 'm1' } }
  click(pill('Set up on GitHub'))
  await until(() => submitted.length === 1)
  const form = submitted[0]
  assert.equal(appended[0], form, 'the form was not in the document when it was submitted')
  assert.equal(form.getAttribute('method'), 'post')
  assert.equal(form.getAttribute('action'), 'https://github.com/settings/apps/new?state=m1')
  assert.equal(form.hasAttribute('hidden'), true)
  assert.equal(form.hasAttribute('style'), false)
  const [field] = form.childNodes
  assert.deepEqual(
    ['type', 'name', 'value'].map((k) => field.getAttribute(k)),
    ['hidden', 'manifest', '{"name":"Loam"}'],
  )
  if (process.env.PRINT_CARDS) console.log(`--- manifest form\n${outline(form).join('\n')}  value=${field.getAttribute('value')}`)
})

test('registered: the slug, linked to the app, and Install', async () => {
  gh = SHAPES.registered
  await openIntegrations()
  await until(() => lines()[0] === 'GitHub App: loam')
  const link = find(card(), 'ghline')
  assert.equal(String(link.tagName ?? link.nodeName).toLowerCase(), 'a')
  assert.equal(link.getAttribute('href'), 'https://github.com/apps/loam')
  assert.deepEqual(pills(), ['Install'])
  printed('registered')
})

test('connected: every fact in order, the quiet date, Refresh and Disconnect', async () => {
  gh = SHAPES.authorized
  await openIntegrations()
  // The card is drawn first from the state the test before left, then from
  // this test's answer; wait for the whole of this one (under load the quiet
  // date and the pills came a drawing after the lines, 2026-11-19).
  await until(() => lines().join('|') === 'all repositories|Signed in as octo-owner|GitHub App: loam'
    && card() && find(card(), 'ghquiet') && pills().join('|') === 'Refresh|Disconnect')
  assert.deepEqual(lines(), ['all repositories', 'Signed in as octo-owner', 'GitHub App: loam'])
  assert.equal(text(find(card(), 'ghquiet')), `Sign in again by ${formatDate('2027-04-02T21:37:31Z', { year: 'numeric', month: 'short', day: 'numeric' })}`)
  assert.equal(find(card(), 'ghquiet').getAttribute('class'), 'note ghquiet')
  assert.deepEqual(pills(), ['Refresh', 'Disconnect'])
  // Nothing needs them: no dot on the row.
  printed('connected')
})

test('Refresh calls the broker’s refresh route and redraws from its answer', async () => {
  click(pill('Refresh'))
  await until(() => card() && find(card(), 'ghdone'))
  assert.equal(asked.at(-1).route, 'refresh')
  assert.equal(text(find(card(), 'ghdone')), 'Checked with GitHub just now.')
})

test('unhealthy: the host’s error in the warning ink, Reconnect, and a dot on the tab', async () => {
  gh = SHAPES.unhealthy
  await openIntegrations()
  await until(() => card() && find(card(), 'ghquiet')?.getAttribute('class') === 'note ghquiet warn')
  assert.equal(text(find(card(), 'ghquiet')), SHAPES.unhealthy.health.last_error)
  assert.deepEqual(pills(), ['Reconnect', 'Refresh', 'Disconnect'])
  printed('unhealthy')
  // The Integrations row on Settings' first page carries the dot.
  while (text(find(root, 'sheet-title')) !== 'Settings') { click(find(root, 'backbtn')); await new Promise(resolve => setTimeout(resolve, 5)) }
  const row = await until(() => all(root, 'setrow').find((n) => text(find(n, 'setrow-title')) === 'Connections'))
  assert.ok(find(row, 'navdot'), 'no dot on the Connections row')
  assert.ok(text(find(row, 'setrow-sub')).length)

})

test('Disconnect asks once, calls the delete route, and ends on the three GitHub links', async () => {
  await openIntegrations()
  click(pill('Disconnect'))
  await until(() => card() && find(card(), 'ghconfirm'))
  assert.equal(asked.filter((a) => a.method === 'DELETE').length, 0, 'it did not ask first')
  click(all(find(card(), 'ghconfirm'), 'ghpill').find((n) => text(n) === 'Disconnect'))
  const links = await until(() => card() && find(card(), 'ghlinks'))
  assert.equal(asked.at(-1).method, 'DELETE')
  assert.deepEqual(
    all(links, 'loginlink').map((a) => [text(a), a.getAttribute('href')]),
    [
      ['Uninstall the app from your account', 'https://github.com/settings/installations/99'],
      ['Revoke your authorization', 'https://github.com/settings/connections/applications/Iv1.x'],
      ['Delete the app', 'https://github.com/settings/apps/loam/advanced'],
    ],
  )
  // The state underneath is the broker's again: not set up.
  await until(() => lines()[0] === 'Not connected')
  printed('after disconnect')
})
