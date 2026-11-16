// Codex, the second provider, driven: the page, the real `/api`, a broker that
// is a recording of the shapes the host's runtime receipt quotes (2026-10-01).
//
// What is proved here is the page's half of "it will be another option for the
// main persona agents": the provider is chosen on the New-chat sheet where the
// model is, a provider nobody has signed in to is drawn and cannot be pressed,
// the model and effort lists are the chosen provider's own, each row and each
// chat header carries the provider's mark, Codex's Providers card signs in by a
// device code, and the context sheet offers no Compact now on a Codex chat.
//
// **Fixture-only, and plainly so**: `test/fakedom.js` has no layout and no
// stylesheet, and the broker is a stub — the runtime's Codex routes were not
// live when this was written. Set `PRINT_CARDS=1` to have the sheet and the
// card printed as markup.

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { fakeBroker, appAgainst, closing, until } from './harness.js'
import { browser, find, all, text } from './browser.js'

const json = (res, body, status = 200) => {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}
const line = (obj) => `data: ${JSON.stringify(obj)}\n\n`

const record = (over) => ({
  agent: 'scout',
  workspace: '/w/scout',
  created: '2026-10-01T10:00:00Z',
  last_activity: '2026-10-01T11:00:00Z',
  last_event_at: '2026-10-01T11:00:00Z',
  state: 'exited',
  status: 'done',
  last_message: 'Done.',
  pinned: false,
  archived: false,
  record_seq: 1,
  seen_seq: 1,
  pending_permissions: [],
  ...over,
})

const turn = (window) => ({ model: null, usage: null, context: { used: 120000, window }, total_cost_usd: null, cost_delta_usd: null, duration_ms: 1000, num_turns: 1, permission_denials: [] })

const CLAUDE_CHAT = record({ session_id: 'cc-1', provider: 'claude-code', title: 'A chat on Claude', last_activity: '2026-10-01T11:10:00Z', turns: [turn(1000000)] })
const CODEX_CHAT = record({
  session_id: 'cx-1',
  provider: 'codex',
  model: 'gpt-6.1-sol',
  effort: 'high',
  thread_id: 'th-1',
  title: 'A chat on Codex',
  turns: [turn(400000)],
})

const CLAUDE = {
  id: 'claude-code',
  name: 'Claude Code',
  connected: true,
  reason: null,
  usage: null,
  models: [{ id: 'opus', name: 'Opus' }, { id: 'sonnet', name: 'Sonnet' }],
  efforts: ['low', 'medium', 'high'],
  default_model: 'opus',
  default_effort: 'medium',
  login: { expires_at: '2026-10-20T09:00:00Z', days_left: 19, source: 'credentials-file' },
}

// The Codex row, as the runtime quoted it from a live run with no login.
const CODEX_MODELS = [
  { id: 'gpt-6.1-sol', name: 'GPT-6.1-Sol', default: true, efforts: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'] },
  { id: 'gpt-6-luna', name: 'GPT-6-Luna', default: false, efforts: ['low', 'medium', 'high', 'xhigh', 'max'] },
]
const CODEX_OUT = {
  id: 'codex',
  name: 'Codex',
  sign_in: { kind: 'device-code', start: '/v1/providers/codex/login', status: '/v1/providers/codex/login', cancel: '/v1/providers/codex/login', code: null, sign_out: null },
  connected: false,
  reason: 'no login on the box',
  models: CODEX_MODELS,
  efforts: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
  usage: null,
  login: { expires_at: null, days_left: null, source: 'none', account: null, plan: null, status: 'Not logged in' },
}
// Signed in, as the runtime's own test has it.
const CODEX_IN = {
  ...CODEX_OUT,
  connected: true,
  reason: null,
  login: { expires_at: null, days_left: null, source: 'chatgpt', account: 'person@example.invalid', plan: 'Pro', status: 'Logged in using ChatGPT' },
}

let codex = CODEX_OUT
let loginState = 'idle'
const asked = []
const compacted = []
const PENDING = {
  state: 'pending',
  url: 'https://auth.openai.com/codex/device',
  code: 'ABCD-EFGHI',
  instructions: 'Open the URL, sign in to ChatGPT, and enter the one-time code. This page notices the login by itself: GET this route again for the state.',
  expires_at: '2026-10-01T16:23:37Z',
}

const chatStream = (rec) => (req, res) => {
  res.writeHead(200, { 'content-type': 'text/event-stream' })
  res.write(': open ping=15000\n\n')
  res.write(line({ type: 'broker.session', ...rec, window_first_seq: 1, turns_before: 0 }))
  res.write(line({ type: 'broker.user_message', seq: 1, session_id: rec.session_id, text: 'hello' }))
  res.end()
}

// A Codex chat with one finished turn, whose record names the model it ran on
// by the id the providers row lists — so the line under it can be drawn in the
// row's own name for it. Off until the test that wants it.
let codexTurnModel = null
let codexReplay = null
const quotaStream = (req, res) => {
  res.writeHead(200, { 'content-type': 'text/event-stream' })
  res.write(': open ping=15000\n\n')
  res.write(line({ type: 'broker.session', ...CODEX_CHAT, last_event_at: '2026-10-14T19:24:00Z', record_seq: 2, window_first_seq: 1, turns_before: 0 }))
  res.write(line({ type: 'broker.user_message', seq: 1, session_id: 'cx-1', text: 'hello' }))
  res.write(line(codexReplay))
  res.end()
}
const turnStream = (req, res) => {
  const rec = { ...CODEX_CHAT, record_seq: 3, seen_seq: 3, turns: [{ ...turn(400000), model: codexTurnModel }] }
  res.writeHead(200, { 'content-type': 'text/event-stream' })
  res.write(': open ping=15000\n\n')
  res.write(line({ type: 'broker.session', ...rec, window_first_seq: 1, turns_before: 0 }))
  res.write(line({ type: 'broker.user_message', seq: 1, session_id: rec.session_id, text: 'hello' }))
  res.write(line({ type: 'assistant', seq: 2, session_id: rec.session_id, message: { id: 'm1', role: 'assistant', model: codexTurnModel, content: [{ type: 'text', text: 'Done.' }] } }))
  res.write(line({ type: 'result', seq: 3, session_id: rec.session_id, subtype: 'success', is_error: false, duration_ms: 1000 }))
  res.end()
}

const broker = await fakeBroker({
  'GET /v1/agents': (req, res) => json(res, { agents: [{ id: 'scout', name: 'Scout', emoji: null }] }),
  'GET /v1/providers': (req, res) =>
    json(res, { providers: [CLAUDE, codex, { id: 'grok', name: 'Grok', connected: false, reason: 'not wired' }] }),
  'GET /v1/limits': (req, res) => json(res, { attachment_max_bytes: 1024, attachment_types: [] }),
  'GET /v1/sessions': (req, res) => json(res, { sessions: [CLAUDE_CHAT, CODEX_CHAT], live: 0, live_count: 0, cap: 4 }),
  'GET /v1/sessions/events': (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(': open ping=15000\n\n')
    for (const r of [CLAUDE_CHAT, CODEX_CHAT]) res.write(line({ type: 'broker.session', ...r }))
  },
  'GET /v1/sessions/cc-1/events': chatStream(CLAUDE_CHAT),
  'GET /v1/sessions/cx-1/events': (req, res) => (codexTurnModel ? turnStream(req, res) : codexReplay ? quotaStream(req, res) : chatStream(CODEX_CHAT)(req, res)),
  'POST /v1/sessions/cx-1/compact': (req, res) => {
    compacted.push('cx-1')
    json(res, { error: 'compaction is not offered on a Codex session' }, 409)
  },
  'POST /v1/providers/codex/login': (req, res) => {
    asked.push('POST')
    const again = loginState === 'pending'
    loginState = 'pending'
    json(res, { ...PENDING, login: CODEX_OUT.login }, again ? 200 : 201)
  },
  'GET /v1/providers/codex/login': (req, res) => {
    asked.push('GET')
    if (loginState === 'pending') return json(res, { ...PENDING, login: codex.login })
    json(res, { state: loginState, url: null, code: null, login: codex.login })
  },
  'DELETE /v1/providers/codex/login': (req, res) => {
    asked.push('DELETE')
    loginState = 'idle'
    json(res, { state: 'idle', code: null, login: codex.login })
  },
})
const app = await appAgainst(broker)
const page = browser(app.url)
const copied = []
Object.defineProperty(globalThis, 'navigator', {
  value: { language: 'en-GB', clipboard: { writeText: async (value) => copied.push(value) } },
  configurable: true,
})

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
const printed = (label, node) => {
  const markup = outline(node).join('\n')
  if (process.env.PRINT_CARDS) console.log(`--- ${label}\n${markup}`)
  return markup
}

// **No inline `style`.** The fixture DOM already refuses a style attribute
// outright, as the deployed browser's content security policy does, so a page
// that wrote one would have thrown above. What is left to say is that the code
// this change added writes no style of its own at all — the stylesheet owns it.
function noInlineStyle() {
  return !/\bstyle\s*:/.test(readFileSync(new URL('../public/provider-sign-in.js', import.meta.url), 'utf8'))
}

async function closeAll() {
  page.press('Escape')
  await until(() => !find(root, 'sheet'))
}

async function home() {
  await closeAll()
  if (find(root, 'thread-head')) click(find(root, 'iconbtn'))
  await until(() => all(root, 'srow').length === 2)
}

async function newChat() {
  click(all(root, 'persona')[0])
  return await until(() => find(root, 'sheet'))
}

async function openProviderPage(id) {
  await closeAll()
  click(find(root, 'roundbtn'))
  await until(() => find(root, 'settings'))
  click(all(root, 'setrow').find((n) => text(find(n, 'setrow-title')) === 'Providers'))
  await until(() => all(root, 'setrow').some((n) => text(find(n, 'setrow-title')) === 'Codex'))
  click(all(root, 'setrow').find((n) => text(find(n, 'setrow-title')) === (id === 'codex' ? 'Codex' : 'Claude Code')))
  return await until(() => find(root, 'provider-hero') && find(root, 'signline'))
}

async function openChat(title) {
  click(all(root, 'srow').find((r) => text(find(r, 'srow-title')) === title))
  await until(() => find(root, 'msgs') && /hello/.test(find(root, 'msgs').textContent))
}

const chips = (sheet) => all(sheet, 'provider-chip').filter((c) => !/\badd\b/.test(c.getAttribute('class')))



test('the Codex card signed out: Not signed in, Sign in, and the device-code view', async (t) => {
  await openProviderPage('codex')
  const card = () => find(root, 'provider-sign-in')
  assert.equal(text(find(root, 'signline')), 'Not signed in')
  assert.equal(find(root, 'signline').getAttribute('class'), 'signline', 'never signed in drew as a warning')
  assert.equal(find(card(), 'signwho'), null)
  assert.equal(text(find(card(), 'primary')), 'Sign in')
  t.diagnostic(`signed out:\n${printed('Codex card, signed out', card())}`)

  click(find(card(), 'primary'))
  await until(() => find(root, 'devcode'))
  const flow = find(root, 'loginflow')
  t.diagnostic(`device code:\n${printed('Codex card, device code', card())}`)
  assert.equal(flow.getAttribute('class'), 'loginflow device')
  assert.equal(text(find(flow, 'devcode')), 'ABCD-EFGHI')
  assert.equal(find(flow, 'loginlink').getAttribute('href'), 'https://auth.openai.com/codex/device')
  assert.equal(text(find(flow, 'loginlink')), 'Open sign-in page')
  // No field to paste into: the code is entered on OpenAI's page, not here.
  assert.equal(find(flow, 'flat'), null)
  assert.deepEqual(all(flow, 'devcopy').map(text), ['Copy', 'Copy'])
  assert.deepEqual(find(flow, 'loginbtns').childNodes.map(text), ['Done', 'Cancel'])
  assert.match(flow.textContent, /Enter this code on the sign-in page/)

  // Copy on both, and each says so.
  click(all(flow, 'devcopy')[0])
  click(all(find(root, 'loginflow'), 'devcopy')[1])
  await until(() => copied.length === 2)
  assert.deepEqual(copied, ['https://auth.openai.com/codex/device', 'ABCD-EFGHI'])

  // Cancel calls the flow off on the host and puts the button back.
  click(all(find(root, 'loginbtns'), 'cancel')[0])
  await until(() => asked.includes('DELETE'))
  await until(() => text(find(card(), 'primary')) === 'Sign in')
  assert.ok(noInlineStyle())
})

test('Done re-reads the sign-in; signed in, the card says so with the plan and account, and no expiry', async (t) => {
  const card = () => find(root, 'provider-sign-in')
  click(find(card(), 'primary'))
  await until(() => find(root, 'devcode'))
  // The person enters the code on OpenAI's page; the host notices.
  codex = CODEX_IN
  loginState = 'done'
  const before = asked.length
  click(find(find(root, 'loginbtns'), 'primary'))
  await until(() => asked.slice(before).includes('GET'))
  await until(() => text(find(root, 'signline')) === 'Signed in as person@example.invalid')
  t.diagnostic(`signed in:\n${printed('Codex card, signed in', card())}`)
  assert.equal(text(find(card(), 'signline')), 'Signed in as person@example.invalid')
  assert.equal(card().textContent.includes('expires'), false, 'an expiry was drawn for a provider that states none')
  assert.equal(text(find(find(root, 'provider-hero'), 'note')), 'Pro · connected')
  assert.equal(find(root, 'loginwarn'), null)
})







// ------------------------------------------------- Codex's quota, drawn

// The Codex row's `usage`, in the shape the host's runtime serves it: the same
// shape as Claude Code's, with `credits` and `planType` beside the windows.
const MIN = 60000
function codexReading(now) {
  const at = (ms) => ({ resets_at: new Date(now + ms).toISOString(), resets_at_epoch: Math.floor((now + ms) / 1000) })
  return {
    provider: 'codex',
    seen_at: new Date(now - 2 * MIN).toISOString(),
    session_id: 'cx-1',
    status: null,
    is_using_overage: false,
    // 4h 02m and half a minute: the countdown floors, so a slow render still
    // reads "4h 02m".
    five_hour: { utilization: 0.12, ...at((4 * 60 + 2) * MIN + 30000) },
    seven_day: { utilization: 0.31, ...at(4 * 86400000) },
    credits: { hasCredits: false, unlimited: false, balance: null },
    planType: 'plus',
  }
}

const usageCardOf = () => all(root, 'card').find((c) => text(find(c, 'kicker')) === 'Usage')
const meters = (card) => all(card, 'meter').map((m) => `${text(find(m, 'meter-label'))} · ${text(find(m, 'meter-read'))}`)

test('no reading: the Codex card says so in words, and the header mark is the ring’s size with no gauge', async (t) => {
  codex = { ...CODEX_IN, usage: null }
  await openProviderPage('codex')
  const card = await until(() => usageCardOf())
  t.diagnostic(`Usage, no reading:\n${printed('Codex usage card, no reading', card)}`)
  assert.equal(text(find(card, 'note')), 'No quota reading available yet.')
  assert.equal(card.textContent.includes('fills in the first time'), false)
  assert.equal(find(card, 'meter'), null, 'no reading drew a bar')


})

test('a Codex reading: the ring, the Providers row, and the card with both windows, the plan and no credits', async (t) => {
  codex = { ...CODEX_IN, usage: codexReading(Date.now()) }
  await closeAll()
  click(find(root, 'roundbtn'))
  await until(() => find(root, 'settings'))
  click(all(root, 'setrow').find((n) => text(find(n, 'setrow-title')) === 'Providers'))
  const row = await until(() => all(root, 'setrow').find((n) => text(find(n, 'setrow-title')) === 'Codex' && /used/.test(text(find(n, 'setrow-sub')))))
  t.diagnostic(`Providers row: ${text(find(row, 'setrow-sub'))}`)
  assert.equal(text(find(row, 'setrow-sub')), 'Plus · 31% of this week used')

  click(row)
  const card = await until(() => find(root, 'provider-hero') && usageCardOf())
  t.diagnostic(`Usage, with a reading:\n${printed('Codex usage card, with a reading', card)}`)
  assert.equal(text(find(find(root, 'provider-hero'), 'note')), 'Plus · connected')
  assert.equal(text(find(root, 'plan-name')), 'Plus')
  const [five, week] = meters(card)
  assert.equal(five, '5-hour window · 12% · resets in 4h 02m')
  assert.match(week, /^Weekly · 31% · resets (Sun|Mon|Tue|Wed|Thu|Fri|Sat) \d{1,2}:\d\d(?: AM| PM)?$/)
  assert.deepEqual(all(card, 'meter-fill').map((f) => f.__css?.width), ['12.0%', '31.0%'])
  assert.equal(find(card, 'credits'), null, 'credits were drawn for an account that has none')
  assert.equal(card.textContent.includes('No reading yet'), false)


})

// The brief's served row, with its installation-specific account replaced.
const WEEK_ONLY = {
  status: null,
  seven_day: { utilization: 0.05, resets_at: '2026-10-21T14:24:58Z', resets_at_epoch: 1792592698, window_mins: 10080 },
  credits: { hasCredits: false, unlimited: false, balance: '0' },
  planType: 'prolite', buckets: {}, provider: 'codex', seen_at: '2026-10-14T19:39:57Z',
  source: 'read', session_id: null, account: 'person@example.invalid', is_using_overage: null,
}

test('week-only Prolite quota remains a single 5% window on the provider card', async () => {
  codex = { ...CODEX_IN, usage: WEEK_ONLY }
  await openProviderPage('codex')
  const card = await until(() => usageCardOf() && text(find(root, 'plan-name')) === 'Prolite' && usageCardOf())
  assert.equal(meters(card).length, 1)
  assert.match(meters(card)[0], /^Weekly · 5%/)
  assert.deepEqual(all(card, 'meter-fill').map((f) => f.__css?.width), ['5.0%'])
})

for (const [source, lineWord] of [['read', 'Last read 2 min ago'], ['event', 'Last seen 2 min ago, from a chat'], [undefined, 'Last read 2 min ago']]) {
  test(`usage card source ${source ?? 'absent'}: ${lineWord}, with the host’s usage_reason`, async () => {
    const usage = { ...WEEK_ONLY, seen_at: new Date(Date.now() - 2 * MIN).toISOString() }
    if (source === undefined) delete usage.source
    else usage.source = source
    codex = { ...CODEX_IN, usage, usage_reason: 'The provider has no newer reading.' }
      await openProviderPage('codex')
    const card = await until(() => {
      const card = usageCardOf()
      return card && text(find(card, 'reason')) === lineWord && root.textContent.includes(codex.usage_reason) ? card : null
    })
    assert.equal(text(find(card, 'reason')), lineWord)
    assert.ok(root.textContent.includes('The provider has no newer reading.'))
  })
}
