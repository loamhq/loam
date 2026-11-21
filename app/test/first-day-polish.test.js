// Exercise first-visit flows against broker-shaped responses. Close every thread
// and abort its update stream during teardown to prevent reconnects.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { installDom } from './fakedom.js'
import { all, find, text } from './browser.js'
import { h } from '../public/ui.js'
import { createAgentDrafts, draftLine } from '../public/agent-drafts.js'
import { ordinalDraft, failedTurnInfo, formatSystemLine } from '../public/thread-experience.js'
import { createThreads } from '../public/threads.js'
import { createUpdate } from '../public/update.js'
import { createPasskey, authLogoutURL, authSettingsURL } from '../public/passkey.js'
import { createFiles } from '../public/files.js'
import { buildAppThread } from '../public/apps.js'
import { createTranscriber } from '../../plugins/_listed/dictation/server/transcribe.js'
import { readConfig } from '../src/config.js'
import { formatDate } from '../public/time.js'

installDom()

const tags = (node, name, out = []) => {
  if (node?.nodeType === 1 && node.nodeName === name.toUpperCase()) out.push(node)
  for (const child of node?.childNodes ?? []) tags(child, name, out)
  return out
}
const tick = () => new Promise((resolve) => setImmediate(resolve))

// A live-updates stream that stays open until the thread closes it.
const realFetch = globalThis.fetch
test.before(() => {
  globalThis.fetch = async (href, { signal } = {}) => new Response(new ReadableStream({ start(controller) { signal?.addEventListener('abort', () => controller.close()) } }), { headers: { 'content-type': 'text/event-stream' } })
})
test.after(() => { globalThis.fetch = realFetch })

const compose = (placeholders = []) => ({ field: () => h('textarea'), render: (ctx) => { placeholders.push(ctx.placeholder); return h('div', { class: 'composer-fixture' }) }, leave() {}, files: () => [], busy: () => false, clear() {}, restore() {}, changed() {}, dragProps: () => ({}) })
function threadsFor(t, { api, agents = [{ id: 'iris', name: 'Iris' }], providers = [], openSettings = null, placeholders } = {}) {
  const threads = createThreads({ api, redraw() {}, agents: () => agents, providers: () => providers, openSettings, rail: () => null, nav: () => null, enter() {}, leave() {}, compose: compose(placeholders) })
  t.after(() => threads.close({ navigate: false }))
  return threads
}
const record = (messages, extra = {}) => ({ thread: { id: 't-1', title: 'Hello', default: 'iris', members: [{ persona: 'iris', engine: 'claude-code' }], ...extra }, messages, has_more: false, turn: null, turns: [], board: { markdown: '', path: '' } })

// ---------------------------------------------------------------- 1
const juniper = {
  draft: 'd-5f7081e2', id: 'juniper', name: 'Juniper', rev: 1, ready: true, problems: [],
  soul: '---\nname: Juniper\n---\n# Juniper\n\nCheerful and practical. Plans your week around what is already in the fridge.\n\nA third sentence stays behind the fold.',
  config: '## Scope\n\n- **Meals** and the shopping list only',
}

test('the draft card says who the agent is in a plain line, never the soul’s words to the agent', () => {
  assert.equal(draftLine({ name: 'Juniper', soul: "You are Juniper, Sam's kitchen companion. You keep the fridge in mind." }), "Sam's kitchen companion.")
  assert.equal(draftLine({ name: 'Ash', soul: '# Ash\n\nYou are Ash — a quiet gardener who notices things.' }), 'A quiet gardener who notices things.')
  assert.equal(draftLine({ name: 'Juniper', about: 'A warm, practical cook.', soul: 'You are Juniper, Sam’s kitchen companion.' }), 'A warm, practical cook.')
  assert.equal(draftLine({ name: 'Juniper', soul: 'You care about small steps. Always be kind.' }), null)
  const c = createAgentDrafts({ redraw() {}, api: async () => ({ drafts: [] }) })
  c.st.thread = 't-1'; c.st.rows = [{ draft: 'd-1', name: 'Juniper', soul: "You are Juniper, Sam's kitchen companion. You help." }]
  const card = find(c.view('t-1'), 'agent-draft-card')
  assert.equal(text(find(card, 'agent-draft-summary')), "Sam's kitchen companion.")
  assert.doesNotMatch(text(card), /You are/)
})
test('1. the new agent is a card: face, name, two sentences, the whole soul and config rendered behind one fold, Create and Ask for changes', async () => {
  const asked = [], opened = []
  let drafts = [juniper]
  const c = createAgentDrafts({ redraw() {}, askChanges: (d) => asked.push(d.draft), openThread: (id) => opened.push(id),
    api: async (method) => method === 'GET' ? { drafts } : (drafts = [], { agent: { id: 'juniper', name: 'Juniper' }, first_thread: 't-1a2b3c4d' }) })
  await c.load('t-1')
  let card = find(c.view('t-1'), 'agent-draft-card')
  assert.ok(find(card, 'blob'), 'the agent’s face')
  assert.equal(text(tags(card, 'h3')[0]), 'Juniper')
  assert.equal(text(find(card, 'agent-draft-summary')), 'Cheerful and practical. Plans your week around what is already in the fridge.')
  assert.doesNotMatch(text(card), /Revision|draft|d-5f7081e2|---/, 'no revision number, no id, no frontmatter on a first draft')
  assert.equal(tags(card, 'details').length, 0, 'no raw <details>')
  assert.equal(find(card, 'agent-draft-full'), null, 'the whole thing waits behind the fold')

  find(card, 'agent-draft-more').fire('click')
  card = find(c.view('t-1'), 'agent-draft-card')
  const full = find(card, 'agent-draft-full')
  assert.equal(find(card, 'agent-draft-more').getAttribute('aria-expanded'), 'true')
  assert.equal(text(find(card, 'agent-draft-more')), 'Show less')
  assert.equal(tags(full, 'pre').length, 0, 'rendered, not raw')
  assert.deepEqual(tags(full, 'strong').map(text), ['Meals'])
  assert.doesNotMatch(text(full), /\*\*|^#/)
  assert.match(text(full), /A third sentence stays behind the fold/)

  const [create, change] = all(find(card, 'agent-draft-actions'), 'pillbtn')
  assert.deepEqual([text(create), text(change)], ['Create', 'Ask for changes'])
  assert.ok(create.classList.contains('primary') && change.classList.contains('secondary'))
  change.fire('click')
  assert.deepEqual(asked, ['d-5f7081e2'])

  drafts = [{ ...juniper, rev: 2 }]
  await c.load('t-1')
  assert.equal(text(find(c.view('t-1'), 'agent-draft-rev')), '2nd draft')
  assert.deepEqual([1, 2, 3, 4, 11, 12, 13, 21, 22].map(ordinalDraft), ['', '2nd draft', '3rd draft', '4th draft', '11th draft', '12th draft', '13th draft', '21st draft', '22nd draft'])

  await c.confirm(drafts[0])
  const done = c.view('t-1')
  assert.match(text(done), /Juniper is ready\./)
  assert.doesNotMatch(text(done), /t-1a2b3c4d/)
  all(done, 'pillbtn').find((b) => text(b) === 'Say hello to Juniper').fire('click')
  assert.deepEqual(opened, ['t-1a2b3c4d'])
  assert.equal(find(done, 'agent-draft-card'), null, 'a made draft leaves the thread')
})

test('1. the card sits in the thread’s flow after the line that announced it, not above the conversation', async (t) => {
  const messages = [
    { id: 'm1', author: 'you', kind: 'message', text: 'A meal planner, please.', at: '2026-11-16T17:49:00Z' },
    { id: 'm2', author: 'iris', kind: 'message', engine: 'claude-code', text: 'Here is a first sketch below.', at: '2026-11-16T17:50:00Z' },
    { id: 'm3', author: 'loam', kind: 'system', text: 'Iris drafted a new agent: Juniper (draft d-5f7081e2, revision 1)', at: '2026-11-16T17:50:01Z' },
  ]
  const threads = threadsFor(t, { api: async (method, path) => path.startsWith('/agents/drafts') ? { drafts: [juniper] } : record(messages) })
  await threads.open('t-1', { routed: true })
  for (let i = 0; i < 5; i += 1) await tick()
  const pane = threads.pane()[0], stream = find(pane, 'tt-stream')
  assert.ok(stream, 'the conversation')
  const flow = [...all(stream, 'tt-system'), ...all(stream, 'agent-drafts')]
  assert.equal(flow.length, 2)
  const order = [], walk = (node) => { if (node.nodeType !== 1) return; const cls = (node.getAttribute('class') ?? '').split(' '); if (cls.includes('tt-system')) order.push('line'); if (cls.includes('agent-drafts')) order.push('card'); node.childNodes.forEach(walk) }
  walk(stream)
  assert.deepEqual(order, ['line', 'card'], 'the card comes after the line, where Iris says it is')
  assert.equal(text(find(stream, 'tt-system')), 'Iris drafted a new agent: Juniper')
})

// ---------------------------------------------------------------- 2
test('2. Loam’s lines read as words: no draft, thread or message ids, a revision as “2nd draft”, a thread linked by its name', async (t) => {
  // As bin/session-broker writes them (agents_route).
  const lines = {
    starter: "New agent. Tell Iris who you'd like: Iris drafts their name, face, soul and config, shows you, changes it on your word, and makes them only when you say yes. (Iris: `settings describe agent.draft` says how; this thread is `t-6daf5a6f`.)",
    drafted: 'Iris drafted a new agent: Juniper (draft d-5f7081e2, revision 1)',
    redrafted: 'Iris drafted a new agent: Juniper (draft d-5f7081e2, revision 3)',
    revised: 'Iris revised draft d-5f7081e2: Juniper, revision 2 (config, soul)',
    aside: 'Iris set draft d-5f7081e2 aside',
    yes: "Juniper was made by Iris on Sam's yes (m12 in t-6daf5a6f). This is their first thread.",
    made: 'Made Juniper (juniper): their first thread is t-c4a782f2',
  }
  const said = (line, threadsList) => formatSystemLine(line, { threads: threadsList })
  assert.deepEqual(said(lines.starter), [{ text: "New agent. Tell Iris who you'd like: Iris drafts their name, face, soul and config, shows you, changes it on your word, and makes them only when you say yes." }])
  assert.deepEqual(said(lines.drafted), [{ text: 'Iris drafted a new agent: Juniper' }])
  assert.deepEqual(said(lines.redrafted), [{ text: 'Iris drafted a new agent: Juniper (3rd draft)' }])
  assert.deepEqual(said(lines.revised), [{ text: 'Iris made a 2nd draft of Juniper' }])
  assert.deepEqual(said(lines.aside), [{ text: 'Iris set the new agent’s draft aside' }])
  assert.deepEqual(said(lines.yes), [{ text: "Juniper was made by Iris on Sam's yes. This is their first thread." }])
  assert.deepEqual(said(lines.made), [{ text: 'Made Juniper. ' }, { link: { thread: 't-c4a782f2', title: 'Say hello in their first thread' } }])
  assert.deepEqual(said(lines.made, [{ id: 't-c4a782f2', title: 'Juniper says hello' }]), [{ text: 'Made Juniper. ' }, { link: { thread: 't-c4a782f2', title: 'Their first thread: Juniper says hello' } }])
  assert.deepEqual(said('Iris brought in Juniper'), [{ text: 'Iris brought in Juniper' }], 'every other line is untouched')

  // Through the thread: the words on the page, the first thread a link.
  const messages = Object.values(lines).map((line, i) => ({ id: `m${i + 1}`, author: 'loam', kind: 'system', text: line, at: '2026-11-16T17:50:00Z' }))
  const threads = threadsFor(t, { api: async (method, path) => path.startsWith('/agents/drafts') ? { drafts: [] } : record(messages) })
  await threads.open('t-1', { routed: true })
  await tick()
  const stream = find(threads.pane()[0], 'tt-stream')
  assert.equal(all(stream, 'tt-system').length, messages.length)
  assert.doesNotMatch(text(stream), /\b[dt]-[0-9a-f]{8}\b|\bm12\b|revision \d|settings describe/)
  const link = all(stream, 'tt-branch-link').find((a) => text(a) === 'Say hello in their first thread')
  assert.ok(link, 'the first thread is a link by name')
  assert.match(link.getAttribute('href'), /t-c4a782f2/)
})

// ---------------------------------------------------------------- 3
test('3. a failed turn is a system line that says what happened and what to do, the raw error behind Details', async (t) => {
  const providers = [{ id: 'claude-code', name: 'Claude Code' }, { id: 'codex', name: 'Codex' }]
  const expired = { id: 'm2', author: 'iris', kind: 'message', engine: 'claude-code', model: '<synthetic>', text: 'Failed to authenticate: OAuth session expired and could not be refreshed', at: '2026-11-16T17:49:30Z' }
  const info = failedTurnInfo(expired, { agentName: () => 'Iris', providers })
  assert.equal(info.summary, 'Iris couldn’t reach Claude. Sign in again in Settings › Connections.')
  assert.equal(info.action, 'providers')
  assert.equal(info.raw, expired.text)

  // The engine's own line ("<name>'s turn failed: …"), for another provider.
  const engine = failedTurnInfo({ id: 'm9', author: 'loam', kind: 'system', text: "Juniper's turn failed: 401 Unauthorized: token expired" }, { engineOf: (who) => who === 'Juniper' ? 'codex' : null, providers })
  assert.equal(engine.summary, 'Juniper couldn’t reach Codex. Sign in again in Settings › Connections.')
  const limit = failedTurnInfo({ id: 'm8', author: 'iris', kind: 'message', engine: 'claude-code', model: '<synthetic>', text: 'API Error: 429 rate_limit_error' }, { agentName: () => 'Iris', providers })
  assert.equal(limit.action, 'providers')
  assert.match(limit.summary, /^Claude has paused replies for now/)
  const other = failedTurnInfo({ id: 'm7', author: 'loam', kind: 'system', text: "Iris's turn failed: Process exited with status 1" }, { providers })
  assert.equal(other.summary, 'Iris’s turn didn’t finish. Send your message again, or ask another agent.')
  assert.equal(other.action, null, 'no button that would not help')

  // A real reply is the agent's, even one that talks about errors.
  assert.equal(failedTurnInfo({ id: 'm5', author: 'iris', kind: 'message', engine: 'claude-code', model: 'sonnet', text: 'Failed to authenticate is what the log says; I fixed the token.' }, { providers }), null)
  assert.equal(failedTurnInfo({ id: 'm6', author: 'iris', kind: 'message', engine: 'claude-code', model: 'sonnet', text: 'Here is what I found.' }, { providers }), null)
  assert.equal(failedTurnInfo({ id: 'm4', author: 'loam', kind: 'system', text: 'You brought in Juniper' }, { providers }), null)

  // Drawn: a system line, never the agent speaking.
  const went = []
  const messages = [{ id: 'm1', author: 'you', kind: 'message', text: 'Hi Iris, I am Sam.', at: '2026-11-16T17:49:00Z' }, expired]
  const threads = threadsFor(t, { api: async (method, path) => path.startsWith('/agents/drafts') ? { drafts: [] } : record(messages), providers, openSettings: (...args) => went.push(args) })
  await threads.open('t-1', { routed: true })
  await tick()
  const stream = find(threads.pane()[0], 'tt-stream')
  const line = find(stream, 'tt-system-failed')
  assert.ok(line)
  assert.equal(all(stream, 'tt-author-run').length, 0, 'not drawn as Iris’s message')
  assert.equal(text(find(line, 'tt-failed-summary')), 'Iris couldn’t reach Claude. Sign in again in Settings › Connections.')
  const details = tags(line, 'details')[0]
  assert.equal(Boolean(details.open), false)
  assert.equal(text(tags(details, 'summary')[0]), 'Details')
  assert.equal(text(find(details, 'tt-failed-raw')), expired.text)
  find(line, 'tt-failed-go').fire('click')
  assert.deepEqual(went, [['providers']])
})

// ---------------------------------------------------------------- 4
test('4. dictation without a key says a Gemini key is needed and goes to where it is added; the terms card speaks plainly', async () => {
  // What Dictation's own server answers a take when there is no key.
  const answer = await createTranscriber(readConfig({ LOG_LEVEL: 'off' }), { getKey: async () => '' }).transcribe({ audio: Buffer.from('take'), contentType: 'audio/webm' })
  assert.equal(answer.status, 503)
  const { mount } = await import('../../plugins/_listed/dictation/page/engine.js')
  const helpers = await import('../../plugins/_listed/dictation/page/dictate.js')
  const went = [], realRecorder = globalThis.MediaRecorder, realNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  globalThis.MediaRecorder = class {
    static isTypeSupported() { return true }
    constructor() { this.listeners = {}; this.state = 'inactive'; this.mimeType = 'audio/webm' }
    addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn) }
    start() { this.state = 'recording'; globalThis.MediaRecorder.last = this }
    stop() { this.state = 'inactive'; for (const fn of this.listeners.dataavailable ?? []) fn({ data: new Blob(['a'], { type: 'audio/webm' }) }); for (const fn of this.listeners.stop ?? []) fn() }
  }
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { language: 'en-GB', mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }), enumerateDevices: async () => [], addEventListener() {}, removeEventListener() {} } } })
  const field = h('textarea')
  const state = { rec: null, takeNotice: null, dictation: { mode: 'record', maxMinutes: 5, deviceId: null, polish: 'light', terms: [] }, mics: { loaded: false, list: [], named: false, error: '' },
    termsNote: { loaded: true, found: false, path: null, url: null, terms: [], readAt: null, error: '' } }
  const host = { ...helpers, h, icon: new Proxy({}, { get: () => () => h('span') }), draft: field, editorDraft: field, fileField: field, root: h('div'), state, store: { dictation: state.dictation },
    schedule() {}, captureStarted() {}, audioSessionType() {}, sense() {}, screen: { hold() {}, release() {}, shown() {} }, caretPoint: () => ({ x: 0, y: 0 }), readField: (f) => ({ value: f.value, caret: f.value.length }), setMarkdown() {},
    activeThread: () => null, threads: { defaultName: () => null }, longAgo: () => 'a moment',
    fetch: async () => new Response(JSON.stringify(answer.body), { status: answer.status, headers: { 'content-type': 'application/json' } }), api: async () => ({}),
    goSettings: (...args) => went.push(args) }
  const service = mount(host, { segmentedControl: () => h('div') })
  try {
    service.micButton('draft').fire('click')
    for (let i = 0; i < 40 && globalThis.MediaRecorder.last?.state !== 'recording'; i += 1) await new Promise((resolve) => setTimeout(resolve, 5))
    service.keepTake()
    for (let i = 0; i < 40 && !state.takeNotice; i += 1) await new Promise((resolve) => setTimeout(resolve, 5))
    const notice = service.takeNoticeLine()
    assert.equal(text(notice), 'Dictation needs a Gemini API key. · Add a key')
    assert.doesNotMatch(text(notice), /Couldn’t clean/)
    find(notice, 'noticerow-act').fire('click')
    assert.deepEqual(went, [['plugins', 'dictation']], 'straight to the Dictation plugin, where its key goes')
    assert.equal(service.TERMS_NOT_CONNECTED, 'No names and terms note yet. Add one to your vault to help dictation spell your names.')
    assert.doesNotMatch(service.TERMS_NOT_CONNECTED, /not connected/)
  } finally {
    service.discardTake?.(); service.stopLive?.(); service.dispose?.()
    globalThis.MediaRecorder = realRecorder
    if (realNavigator) Object.defineProperty(globalThis, 'navigator', realNavigator); else delete globalThis.navigator
  }
})

// ---------------------------------------------------------------- 5
test('5. a finished update shows the outcome and the versions; its steps, paths and counts wait behind Details', async () => {
  const run = { id: 'u1', outcome: 'applied', from_version: '0.1.0', to_version: '0.1.1', steps: [
    { name: 'check', words: 'Checking the install', status: 'done' },
    { name: 'backup', words: 'Keeping a copy of System/Loam', status: 'done', detail: '/var/lib/loam/update/backups/20261117-022529/Loam' },
    { name: 'pins', words: 'Checking memory pins', status: 'done', detail: '4 memory pins checked, 0 changed' },
  ] }
  const c = createUpdate({ changed() {}, api: async () => ({ current: { version: '0.1.1' }, check: { available: false, latest: { version: '0.1.1' } }, run }) })
  await c.check()
  const card = find(c.view(), 'update-progress')
  assert.equal(text(tags(card, 'h3')[0]), 'Update finished')
  assert.match(text(card), /0\.1\.0 → 0\.1\.1/)
  const details = find(card, 'update-details')
  assert.equal(Boolean(details.open), false)
  assert.equal(text(tags(details, 'summary')[0]), 'Details')
  assert.match(text(details), /\/var\/lib\/loam\/update\/backups/)
  assert.match(text(details), /4 memory pins checked/)
  const outside = [...card.childNodes].filter((n) => n !== details).map(text).join(' ')
  assert.doesNotMatch(outside, /\/var\/lib|memory pins|Checking the install/, 'nothing but the outcome and versions outside the fold')

  // While it runs, the steps show; their paths and counts are still folded.
  c.st.data.run = { ...run, outcome: 'running', steps: run.steps.map((s, i) => ({ ...s, status: i === 2 ? 'running' : 'done' })) }
  const live = find(c.view(), 'update-progress')
  assert.match(text(live), /Checking memory pins/)
  assert.doesNotMatch([...live.childNodes].filter((n) => n !== find(live, 'update-details')).map(text).join(' '), /\/var\/lib/)
  c.stop?.()
})

// ---------------------------------------------------------------- 6
test('6. Sign-in: Sign out on the portal’s real route, and the passkeys shown with a way to remove them', async () => {
  assert.equal(authLogoutURL('https://sam.example.com/threads/t-1', 'sam.example.com'), 'https://auth.sam.example.com/logout')
  assert.equal(authSettingsURL('https://sam.example.com:8443/', 'sam.example.com'), 'https://auth.sam.example.com/settings')
  // A host outside the install's domain (the design preview): `auth.` in front of it.
  assert.equal(authSettingsURL('https://preview.test:8443/', 'sam.example.com'), 'https://auth.preview.test/settings')
  assert.equal(authSettingsURL('https://auth.preview.test/x', ''), 'https://auth.preview.test/settings')
  // On the install's domain the portal is `auth.<domain>` from the apex and
  // from staging alike (staging's link said `auth.next.*`, 2026-11-19).
  assert.equal(authSettingsURL('https://sam.example.org/', 'sam.example.org'), 'https://auth.sam.example.org/settings')
  assert.equal(authSettingsURL('https://next.sam.example.org/threads', 'sam.example.org'), 'https://auth.sam.example.org/settings')
  assert.equal(authLogoutURL('https://next.sam.example.org/', 'sam.example.org'), 'https://auth.sam.example.org/logout')
  assert.equal(authSettingsURL('https://auth.sam.example.org/x', 'sam.example.org'), 'https://auth.sam.example.org/settings')
  globalThis.location = { href: 'https://sam.example.com/' }
  globalThis.LOAM_INSTALLATION = { appsDomain: 'sam.example.com' }

  // Today: the app's host does not relay the portal's list.
  const calls = []
  const bare = createPasskey({ changed() {}, request: async (path, opts = {}) => { calls.push([opts.method ?? 'GET', path]); return new Response('not found\n', { status: 404 }) } })
  await bare.loadPasskeys()
  let view = bare.view()
  const out = all(view, 'signout')[0]
  assert.equal(text(out), 'Sign out')
  assert.equal(out.getAttribute('href'), 'https://auth.sam.example.com/logout')
  const manage = find(view, 'passkey-manage')
  assert.equal(text(manage), 'See or remove your passkeys')
  assert.equal(manage.getAttribute('href'), 'https://auth.sam.example.com/settings')
  assert.equal(text(tags(view, 'h3')[0]), 'A quicker way in')

  // With the relay: each passkey, its name and dates, and Remove.
  const listed = createPasskey({ changed() {}, request: async (path, opts = {}) => { calls.push([opts.method ?? 'GET', path]); return Response.json({ passkeys: [
    { id: 3, description: 'iPhone', created_at: '2026-11-16T18:00:00Z', last_used_at: '2026-11-17T08:00:00Z' },
    { id: 4, description: 'Mac', created_at: '2026-11-17T09:00:00Z', last_used_at: '0001-01-01T00:00:00Z' }] }) } })
  await listed.loadPasskeys()
  view = listed.view()
  assert.equal(text(tags(view, 'h3')[0]), 'Your passkeys')
  const rows = all(view, 'passkey-item')
  assert.deepEqual(rows.map((r) => text(find(r, 'passkey-item-name'))), ['iPhone', 'Mac'])
  assert.equal(text(rows[0]).includes(`Added ${formatDate('2026-11-16T18:00:00Z')} · last used ${formatDate('2026-11-17T08:00:00Z')}`), true)
  assert.equal(text(rows[1]).includes(`Added ${formatDate('2026-11-17T09:00:00Z')} · not used yet`), true, 'Authelia’s zero date is “not used yet”')
  const remove = find(rows[0], 'passkey-remove')
  assert.equal(remove.getAttribute('aria-label'), 'Remove iPhone')
  assert.equal(remove.getAttribute('href'), 'https://auth.sam.example.com/settings', 'removing is the portal’s, which confirms who you are first')
  assert.equal(find(view, 'passkey-manage'), null)
  assert.ok(all(view, 'pillbtn').some((b) => text(b) === 'Add another passkey'))
  assert.deepEqual(calls.filter(([method]) => method !== 'GET'), [], 'the app never removes a passkey itself')
})

// ---------------------------------------------------------------- 7
test('7. Settings › Agents: the person’s agents first, the engineers and the reviewer in one quieter System group, each still settable', async () => {
  const { mount } = await import('../public/page/settings.js')
  const { createCoreRegistry } = await import('../public/core-registrations.js')
  const host = {
    registry: createCoreRegistry(), api: async () => ({}), schedule() {},
    state: { wide: true, sheet: { kind: 'settings', page: 'agents' }, agents: { list: [{ id: 'iris', name: 'Iris' }, { id: 'juniper', name: 'Juniper' }] }, agentsReady: true, providers: [{ id: 'claude-code', name: 'Claude Code', connected: true }], github: {} },
    settingsProfiles: { st: { engineers: { engineers: [{ project: 'loam-ui', platform: false, effective: { provider: 'claude-code' } }, { project: 'loam', platform: true, effective: { provider: 'claude-code' } }], default: { effective: { provider: 'codex' } } },
      reviewer: { name: 'Reviewer', plain: 'Every night at 4:20' }, directoryBusy: false, directoryLoading: false, directoryError: '', archived: [] }, directory() {} },
    settingsProfileUI: { body: () => [], settingsDetail: () => [] }, providerSignIn: { cancel() {} }, closeSheet() {}, goProfile() {}, sheetTop: () => h('div'),
  }
  const settings = mount(host)
  const groups = settings.settingsAgents({ back: false }).filter((n) => n?.classList?.contains('profile-group'))
  assert.deepEqual(groups.map((g) => g.getAttribute('aria-label')), ['Agents', 'System'])
  assert.deepEqual(all(groups[0], 'setrow-title').map(text), ['Iris', 'Juniper'])
  const system = groups[1]
  assert.ok(system.classList.contains('profile-group-system'))
  assert.deepEqual(all(system, 'setrow-title').map(text), ['loam', 'loam-ui', 'Every other project', 'Reviewer'])
  assert.match(text(find(system, 'profile-system-help')), /About these helpers/)
  assert.equal(all(system, 'setrow').every((row) => typeof row.fire === 'function' && row.nodeName === 'BUTTON'), true, 'every row still opens its settings')
})

// ---------------------------------------------------------------- 8
test('8. the claim page says “at least 10 characters” before anyone types, matching the claim service’s rule', async () => {
  const page = await readFile(new URL('../public/claim.html', import.meta.url), 'utf8')
  const hint = /<p class="hint" id="password-hint">([^<]+)<\/p>/.exec(page)?.[1]
  assert.match(hint, /^At least 10 characters\./)
  assert.match(page, /<input id="password"[^>]*aria-describedby="password-hint"/)
  assert.doesNotMatch(/<p class="hint" id="password-hint"[^>]*>/.exec(page)[0], /hidden/)
})

// ---------------------------------------------------------------- 9
const offered = [
  { label: 'Vault system', resource: { root: 'vault', path: 'System', name: 'System', kind: 'folder' } },
  { label: 'Loam project', resource: { root: 'vault', path: 'Projects/Loam', name: 'Loam', kind: 'folder' } },
  { label: 'Loam infrastructure', resource: { root: 'vault', path: 'Projects/Loam/infrastructure', name: 'infrastructure', kind: 'folder' } },
  { label: 'Conventions', resource: { root: 'vault', path: 'System/conventions', name: 'conventions', kind: 'folder' } },
]
const entry = (path, kind = 'folder') => ({ root: 'vault', path, name: path.split('/').at(-1), kind })
async function shortcutsWith(listings) {
  const listed = []
  const files = createFiles({ schedule() {}, api: async (method, path) => {
    if (path === '/files/roots') return { roots: [{ root: 'vault', name: 'Vault', capabilities: { read: true } }], shortcuts: offered }
    const folder = new URL(path, 'http://x').searchParams.get('path')
    listed.push(folder)
    if (!(folder in listings)) throw Object.assign(new Error('Not found'), { status: 404 })
    return { entries: listings[folder] }
  } })
  await files.loadRoots()
  return { labels: files.st.roots.shortcuts.map((s) => s.label), listed }
}

test('9. Files: a fresh install’s developer folders are not offered to a new person; an install that uses them keeps every one', async () => {
  // A fresh vault (bin/library seed-vault, and the schedule page the timers write).
  const fresh = await shortcutsWith({
    System: [entry('System/Loam'), entry('System/skills')],
    'Projects/Loam': [entry('Projects/Loam/infrastructure')],
    'Projects/Loam/infrastructure': [entry('Projects/Loam/infrastructure/schedule.md', 'file')],
  })
  assert.deepEqual(fresh.labels, ['Conventions'], 'only what the host offers beyond the install’s own folders')
  assert.deepEqual(fresh.listed.sort(), ['Projects/Loam', 'Projects/Loam/infrastructure', 'System'], 'only the three are looked into')

  // A box that works in them, as the first install does.
  const used = await shortcutsWith({
    System: [entry('System/Loam'), entry('System/skills'), entry('System/conventions'), entry('System/Dictation terms.md', 'file')],
    'Projects/Loam': [entry('Projects/Loam/architecture'), entry('Projects/Loam/infrastructure')],
    'Projects/Loam/infrastructure': [entry('Projects/Loam/infrastructure/schedule.md', 'file'), entry('Projects/Loam/infrastructure/agent-runtime.md', 'file')],
  })
  assert.deepEqual(used.labels, ['Vault system', 'Loam project', 'Loam infrastructure', 'Conventions'], 'in the host’s order, as before')

  // A folder that cannot be read is not offered.
  const unread = await shortcutsWith({ System: [entry('System/Notes')] })
  assert.deepEqual(unread.labels, ['Vault system', 'Conventions'])
})

// ---------------------------------------------------------------- 10
test('10. Build an app opens a thread with an agent in it and a short prompt asking what to build', async (t) => {
  for (const [agents, persona, name] of [[[{ id: 'juniper', name: 'Juniper' }, { id: 'iris', name: 'Iris' }], 'iris', 'Iris'], [[{ id: 'old', name: 'Old', archived: true }, { id: 'juniper', name: 'Juniper' }], 'juniper', 'Juniper']]) {
    const posted = [], placeholders = []
    const threads = threadsFor(t, { agents, placeholders, api: async (method, path, body) => {
      if (method === 'POST' && path === '/threads') { posted.push(body); return { id: `t-${persona}`, title: 'New thread', default: body.persona, members: [{ persona: body.persona, engine: 'claude-code' }] } }
      return path.startsWith('/agents/drafts') ? { drafts: [] } : {}
    } })
    await buildAppThread(threads, agents)
    assert.deepEqual(posted, [{ persona }], 'the thread has an agent in it')
    const pane = threads.pane()[0]
    assert.equal(text(find(pane, 'tt-empty') ?? pane).includes(`Build an app with ${name}`), true)
    assert.match(text(pane), new RegExp(`Describe what you’d like in a sentence or two\\. ${name} will ask what’s needed, then build it\\.`))
    assert.equal(placeholders.at(-1), 'What would you like to build?')
    threads.close({ navigate: false })
  }
})

// Leaving an empty new thread discards it. A draft or message preserves it.
test('an unsent new thread does not linger: leaving it empty deletes it', async (t) => {
  const field = h('textarea')
  const calls = []
  let n = 0
  const api = async (method, path, body) => {
    calls.push(`${method} ${path}`)
    if (method === 'POST' && path === '/threads') { n++; return { id: `t-new${n}`, title: 'New thread', default: body.persona, members: [{ persona: body.persona, engine: 'claude-code' }] } }
    if (method === 'POST' && path.endsWith('/messages')) return { id: 'm1', author: 'owner', kind: 'message', text: body.text, at: '2026-11-17T00:00:00Z' }
    return path.startsWith('/agents/drafts') ? { drafts: [] } : {}
  }
  const threads = createThreads({ api, redraw() {}, agents: () => [{ id: 'iris', name: 'Iris' }], providers: () => [], rail: () => null, nav: () => null, enter() {}, leave() {},
    compose: { ...compose(), field: () => field } })
  t.after(() => threads.close({ navigate: false }))

  await buildAppThread(threads, [{ id: 'iris', name: 'Iris' }])
  assert.equal(threads.st.threads.some((x) => x.id === 't-new1'), true)
  threads.close({ navigate: false })
  await tick()
  assert.equal(calls.includes('DELETE /threads/t-new1'), true, 'left empty, it is deleted')
  assert.equal(threads.st.threads.some((x) => x.id === 't-new1'), false, 'and gone from Recent at once')

  await buildAppThread(threads, [{ id: 'iris', name: 'Iris' }])
  field.value = 'A habit tracker'
  threads.close({ navigate: false })
  await tick()
  assert.equal(calls.includes('DELETE /threads/t-new2'), false, 'a thread with a draft stays')
  field.value = ''

  await buildAppThread(threads, [{ id: 'iris', name: 'Iris' }])
  field.value = 'A habit tracker'
  await threads.send()
  threads.close({ navigate: false })
  await tick()
  assert.equal(calls.includes('DELETE /threads/t-new3'), false, 'a thread with a message stays')

  // Opening an existing empty thread and leaving it deletes nothing: only one
  // this page made.
  calls.length = 0
  await threads.open('t-old', { routed: true }).catch(() => {})
  threads.close({ navigate: false })
  await tick()
  assert.equal(calls.some((c) => c.startsWith('DELETE')), false)
})
