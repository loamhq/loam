import test from 'node:test'
import assert from 'node:assert/strict'
import { once } from 'node:events'
import { createApp } from '../src/server.js'
import { readConfig } from '../src/config.js'
import { fakeBroker, closing, TOKEN } from './harness.js'
import { createProfiles, soulExcerpt, mountProfile } from '../public/agent-profile.js'
import { createAgentDrafts } from '../public/agent-drafts.js'
import { createDailyNotes } from '../public/daily-notes.js'
import { createAgents, agentLook } from '../public/sessions.js'
import { createSecretSettings } from '../public/plugin-secrets.js'
import { shippedPlugins } from '../src/shipped-plugins.js'
import { installDom } from './fakedom.js'
import { text, all, find } from './browser.js'

const reply = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
async function appFor(t, broker, extra = {}) {
  const app = createApp({ config: readConfig({ BROKER_URL: broker.url, BROKER_TOKEN: TOKEN, LOG_LEVEL: 'off' }), ...extra })
  app.server.listen(0, '127.0.0.1'); await once(app.server, 'listening')
  t.after(closing(...[app, broker].filter(x => x.server)))
  return { ...app, url: `http://127.0.0.1:${app.server.address().port}` }
}

test('agent lifecycle relay preserves verbs, revisions, archived filters and multipart image bytes', async t => {
  const broker = await fakeBroker(new Proxy({}, { get: () => (req, res, url, raw) => reply(res, { method: req.method, path: url.pathname, query: url.search, raw }, req.method === 'POST' ? 201 : 200) }))
  const app = await appFor(t, broker)
  for (const [method, path, body] of [
    ['POST', '/agents/new', {}], ['POST', '/agents/drafts', { thread: 'new', soul: 'Hello.' }], ['GET', '/agents/drafts?thread=new'],
    ['PATCH', '/agents/drafts/draft%20one', { soul: 'Revised.' }], ['GET', '/agents/drafts/draft%20one'],
    ['POST', '/agents/drafts/draft%20one/confirm', { rev: 2 }], ['DELETE', '/agents/drafts/draft%20one'],
    ['POST', '/agents/test%20agent/archive', {}], ['POST', '/agents/test%20agent/restore', {}], ['GET', '/agents?archived=1'],
  ]) {
    const out = await fetch(`${app.url}/api${path}`, { method, ...(body ? { headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) } : {}) })
    assert.equal(out.status, method === 'POST' ? 201 : 200)
    assert.equal(out.headers.get('cache-control'), 'no-store')
    assert.equal(broker.seen.at(-1).auth, `Bearer ${TOKEN}`)
    if (body) assert.deepEqual(JSON.parse(broker.seen.at(-1).body), body)
    if (path.includes('?')) assert.equal(new URL((await out.json()).query, 'http://fixture').search, new URL(path, 'http://fixture').search)
  }
  const form = new FormData(); form.append('file', new Blob([Buffer.from([0, 1, 2, 127])], { type: 'image/png' }), 'face.png')
  const upload = await fetch(`${app.url}/api/agents/test/face`, { method: 'PUT', body: form })
  assert.equal(upload.status, 200); assert.equal(broker.seen.at(-1).method, 'PUT')
  assert.match(broker.seen.at(-1).body, /name="file"; filename="face.png"/)
  assert.ok(broker.seen.at(-1).body.includes('\x00\x01\x02\x7f'))
})

test('confirm relay and archive progress remain pending until the host settles', async t => {
  let release, upstreamSignal
  const broker = { json: async (method, path, body, { signal }) => { upstreamSignal = signal; await new Promise(r => { release = r }); return { status: 201, contentType: 'application/json', body: '{"agent":{"id":"throwaway"}}' } } }
  const app = await appFor(t, { url: '', server: null }, { broker })
  const pending = fetch(`${app.url}/api/agents/drafts/one/confirm`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"rev":3}' })
  while (!release) await new Promise(r => setImmediate(r))
  assert.equal(upstreamSignal.aborted, false); release(); assert.equal((await pending).status, 201)
  let finish
  const c = createProfiles({ redraw() {}, api: async (method) => method === 'GET' ? { identity: {}, agent: { id: 'throwaway' } } : new Promise(r => { finish = r }), changedAgent() {} })
  await c.load('agent', 'throwaway'); c.st.confirmArchive = true
  const archiving = c.archive(); assert.equal(c.st.busy, true)
  finish({ agent: { id: 'throwaway' }, archived: { at: 'now' } }); await archiving
  assert.equal(c.st.data.identity.archived.at, 'now'); assert.equal(c.st.busy, false)
})

test('draft confirmation uses the displayed revision and reloads after a stale-revision refusal', async () => {
  const writes = [], made = [], draft = { draft: 'one', rev: 2, ready: true }
  let stale = true
  const c = createAgentDrafts({ redraw() {}, made: a => made.push(a), api: async (method, path, body) => {
    if (method === 'GET') return { drafts: [draft] }
    writes.push(body); if (stale) { stale = false; throw Object.assign(new Error('This draft has changed.'), { status: 409 }) }
    return { agent: { id: 'throwaway', name: 'Throwaway' } }
  } })
  await c.load('thread'); await c.confirm(draft)
  assert.equal(c.st.error, 'This draft has changed.'); assert.equal(c.st.rows[0].rev, 2)
  await c.confirm(draft); assert.deepEqual(writes, [{ rev: 2 }, { rev: 2 }]); assert.equal(made[0].id, 'throwaway')
})

test('daily action follows plugin switch and folder, ignores stale reads, and has no fallback', async () => {
  let on = true, folder = 'Journal', finish
  const c = createDailyNotes({ redraw() {}, api: async (method, path) => path.endsWith('/settings') ? finish ? new Promise(r => { finish = r }) : { values: { folder } } : { plugin: { on } } })
  await c.refresh(); assert.deepEqual([c.st.on, c.st.folder], [true, 'Journal'])
  folder = 'Days'; await c.refresh(); assert.equal(c.st.folder, 'Days')
  finish = true; const old = c.refresh(); await new Promise(r => setImmediate(r))
  on = false; await c.refresh(); finish({ values: { folder: 'Stale' } }); await old
  assert.deepEqual([c.st.on, c.st.folder], [false, null])
})

test('uploaded face survives theme paint resolution; the soul quote contains exactly two sentences', () => {
  const agents = createAgents([{ id: 'test', name: 'Test', face: { url: '/v1/agents/test/face?sha=abc' } }])
  assert.equal(agentLook('test', agents).imageSrc, '/api/agents/test/face?sha=abc')
  assert.equal(soulExcerpt('---\nname: Test\n---\n# Soul\n\nI listen first. Then I make things clear. More lives in the file.'), 'I listen first. Then I make things clear.')
})

test('plugin key metadata renders broker sentences; refuses never replace the saved state', async () => {
  installDom()
  const why = 'Google says this is not a valid API key. Check that it was pasted whole.'
  const c = createSecretSettings({ id: 'dictation', request: async (method) => {
    if (method === 'GET') return { secrets: [{ name: 'GEMINI_API_KEY', label: 'Gemini API key', set: true, source: 'box', checked: { ok: true, why: 'Google accepted the key.' } }] }
    throw Object.assign(new Error(why), { body: { error: why }, status: 422 })
  } })
  await c.load(); await c.act('PUT', 'GEMINI_API_KEY', { value: 'synthetic' })
  assert.equal(c.st.rows[0].source, 'box'); assert.match(text(c.view()), /Google accepted the key/)
  assert.ok(text(c.view()).includes(why)); assert.ok(!JSON.stringify(c.st).includes('synthetic'))
})

test('both shipped voice plugins declare the shared Google key, and no key of their own', async () => {
  const voice = (await shippedPlugins('public')).filter(p => ['dictation', 'read-aloud'].includes(p.id))
  assert.equal(voice.length, 2)
  for (const p of voice) { assert.deepEqual(p.keys.map(k => k.provider), ['google']); assert.equal(p.contributes?.['settings.secret'], undefined) }
})

test('browser key routes relay metadata and refusals, and never expose the server-only value route', async t => {
  const broker = await fakeBroker(new Proxy({}, { get: () => (req, res, url, raw) => reply(res, req.method === 'PUT' ? { error: 'Google says this is not a valid API key. Check that it was pasted whole.' } : { secrets: [{ name: 'GEMINI_API_KEY', set: true, source: 'box' }] }, req.method === 'PUT' ? 422 : 200) }))
  const app = await appFor(t, broker)
  for (const [method, path, body] of [['GET', '/plugins/dictation/secrets'], ['PUT', '/plugins/read-aloud/secrets/GEMINI_API_KEY', { value: 'synthetic' }], ['POST', '/plugins/dictation/secrets/GEMINI_API_KEY/check', {}], ['DELETE', '/plugins/dictation/secrets/GEMINI_API_KEY'], ['PATCH', '/plugins/daily-notes/settings', { values: { folder: 'Days' } }]]) {
    const out = await fetch(app.url + '/api' + path, { method, ...(body ? { headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) } : {}) })
    assert.equal(out.status, method === 'PUT' ? 422 : 200)
    assert.equal(out.headers.get('cache-control'), 'no-store')
  }
  const before = broker.seen.length
  for (const path of ['/plugins/dictation/secrets/GEMINI_API_KEY/value', '/plugins/read-aloud/secrets/GEMINI_API_KEY/value', '/plugins/dictation/secrets/GEMINI_API_KEY%2Fvalue']) {
    const out = await fetch(app.url + '/api' + path)
    assert.ok([400, 404, 405].includes(out.status))
  }
  assert.equal(broker.seen.length, before)
})

test('production transcription reads the plugin key from the broker at use and observes rotation', async t => {
  const http = await import('node:http'), keys = []
  let key = 'synthetic-first'
  const vendor = http.createServer(async (req, res) => {
    keys.push(req.headers['x-goog-api-key']); for await (const chunk of req) {};
    reply(res, { status: 'completed', steps: [{ type: 'model_output', content: [{ type: 'text', text: 'A synthetic transcript.' }] }] })
  })
  vendor.listen(0, '127.0.0.1'); await once(vendor, 'listening'); t.after(closing({ server: vendor }))
  const broker = await fakeBroker({ 'GET /v1/plugins/dictation/secrets/GEMINI_API_KEY/value': (req, res) => reply(res, { name: 'GEMINI_API_KEY', value: key, source: 'box' }) })
  const config = readConfig({ BROKER_URL: broker.url, BROKER_TOKEN: TOKEN, GEMINI_API_KEY: 'ignored-environment-key', GEMINI_API_URL: `http://127.0.0.1:${vendor.address().port}`, LOG_LEVEL: 'off' })
  assert.equal(config.geminiApiKey, undefined)
  const app = await appFor(t, broker, { config })
  for (const value of ['synthetic-first', 'synthetic-second']) {
    key = value
    const out = await fetch(app.url + '/api/transcribe', { method: 'POST', headers: { 'content-type': 'audio/webm' }, body: Buffer.from([1, 2, 3]) })
    assert.equal(out.status, 200); const body = await out.text(); assert.ok(!body.includes(key)); assert.match(body, /synthetic transcript/)
  }
  assert.deepEqual(keys, ['synthetic-first', 'synthetic-second'])
  assert.ok(broker.seen.every(r => r.auth === `Bearer ${TOKEN}`))
})

test('speech observes broker key appearance, synthesis, rotation and removal without restarting', async t => {
  const fs = await import('node:fs/promises'), os = await import('node:os'), path = await import('node:path')
  const { createSpeech } = await import('../src/speech.js'), { createBroker } = await import('../src/broker.js'), { brokerSecret } = await import('../src/plugin-secrets.js')
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'u3-speech-'))
  t.after(() => fs.rm(dir, { recursive: true, force: true }))
  let key = ''
  const broker = await fakeBroker({ 'GET /v1/plugins/read-aloud/secrets/GEMINI_API_KEY/value': (req, res) => reply(res, { value: key, source: 'plugin' }) })
  t.after(closing(broker))
  const config = readConfig({ BROKER_URL: broker.url, BROKER_TOKEN: TOKEN, SPEECH_DIR: dir }), keys = []
  const wav = Buffer.alloc(44 + 38400)
  wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVE', 8); wav.write('fmt ', 12); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(24000, 24); wav.writeUInt32LE(48000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(38400, 40)
  const speech = createSpeech(config, { getKey: brokerSecret(createBroker(config), 'read-aloud'), encoder: { probe: async () => true, encode: async ({ outPath }) => fs.writeFile(outPath, 'fixture audio') }, fetchImpl: async (url, init) => {
    keys.push(init.headers['x-goog-api-key'])
    return new Response(JSON.stringify(url.includes('/models?') ? { models: [] } : { status: 'completed', steps: [{ type: 'model_output', content: [{ type: 'audio', mime_type: 'audio/wav', data: wav.toString('base64') }] }] }), { status: 200, headers: { 'content-type': 'application/json' } })
  } })
  t.after(() => speech.stop())
  assert.equal((await speech.status()).available, false)
  key = 'synthetic-speech-first'; assert.equal((await speech.status()).available, true)
  const out = await speech.request({ source: 'document', title: 'Fixture', mode: 'verbatim', minutes: 'full', text: 'Hello there.' })
  assert.equal(out.status, 202)
  let take
  for (let n = 0; n < 100; n++) { take = await speech.lookup(out.body.id); if (take.status !== 'preparing') break; await new Promise(r => setTimeout(r, 10)) }
  assert.equal(take.status, 'ready'); assert.ok(keys.every(k => k === key))
  key = ''; assert.equal((await speech.status()).available, false)
  key = 'synthetic-speech-second'; assert.equal((await speech.status()).available, true)
  for (const name of await fs.readdir(dir)) assert.doesNotMatch(await fs.readFile(path.join(dir, name), 'utf8'), /synthetic-speech/)
  assert.ok(broker.seen.every(r => r.path === '/v1/plugins/read-aloud/secrets/GEMINI_API_KEY/value'))
})

test('profile file actions open structured host roots and paths directly', () => {
  installDom()
  const opened = [], ref = path => ({ root: 'spaces', path, exists: true })
  const data = { identity: { display: 'Test', face: null }, status: {}, files: { workspace: ref('test'), soul: ref('test/soul.md'), config: ref('test/config.md'), memory: { always: ref('test/always.md'), folder: ref('test/memory') }, skills: ref('test/skills') }, schedule: [], plugins: [] }
  const st = { target: { kind: 'agent', id: 'test' }, data, tab: 'Files', page: 1 }
  const host = { profiles: { st }, state: { agents: createAgents([{ id: 'test', name: 'Test' }]), providers: [] }, threads: { st: { threads: [] } }, schedule() {}, goFiles: (...args) => opened.push(args) }
  const nodes = mountProfile(host).body('test'), view = nodes[0]
  all(view, 'setrow').find(n => text(n) === 'Soul').fire('click', {})
  all(view, 'setrow').find(n => text(n) === 'Open workspace').fire('click', {})
  assert.deepEqual(opened, [['spaces', 'test/soul.md', { isFile: true }], ['spaces', 'test', { isFile: false }]])
})

test('profile status refresh preserves the selected tab and cannot overwrite a later edit', async () => {
  let reads = 0, finish
  const fresh = { agent: { id: 'test', name: 'Renamed' }, identity: { display: 'Renamed' }, status: { threads: [] } }
  const c = createProfiles({ redraw() {}, api: async method => {
    if (method === 'PATCH') return fresh
    if (++reads === 2) return new Promise(r => { finish = r })
    return { ...fresh, identity: { display: 'Original' }, status: { threads: [{}] } }
  } })
  await c.load('agent', 'test'); c.st.tab = 'Files'; c.st.lastRead = 0
  const refreshing = c.refresh(); await c.patch({ display: 'Renamed' })
  finish({ ...fresh, identity: { display: 'Stale' } }); await refreshing
  assert.equal(c.st.data.identity.display, 'Renamed'); assert.equal(c.st.tab, 'Files')
})

test('archive and restore tell Chats by id when the host answers the agent as a string', async () => {
  // The host's real answers (lib/loam_agents): {"agent": "juniper", "archived": {...}} and
  // {"agent": "juniper", "event": {...}}. Found by the 2026-11-16 first-day walk: spread as
  // an object, the id was lost and Chats kept the archived agent until a reload.
  const changed = [], calls = []
  const api = async (method, path) => {
    calls.push([method, path])
    if (method === 'GET' && path === '/agents/juniper') return { identity: {}, agent: { id: 'juniper', name: 'Juniper', emoji: '🌿' } }
    if (path.endsWith('/archive')) return { agent: 'juniper', archived: { at: 'now', by: 'owner' } }
    if (path.endsWith('/restore')) return { agent: 'juniper', event: { kind: 'restored' } }
    if (path === '/agents?archived=1') return { agents: [] }
    return {}
  }
  const c = createProfiles({ redraw() {}, api, changedAgent: agent => changed.push(agent) })
  await c.load('agent', 'juniper'); c.st.confirmArchive = true
  assert.equal(await c.archive(), true)
  assert.deepEqual(changed.at(-1), { id: 'juniper', archived: { at: 'now', by: 'owner' } })
  let agents = createAgents([{ id: 'iris', name: 'Iris' }, { id: 'juniper', name: 'Juniper' }])
  const apply = agent => { const rows = agents.list; agents = createAgents(agent.archived ? rows.filter(r => r.id !== agent.id) : agents.byId.has(agent.id) ? rows.map(r => r.id === agent.id ? agent : r) : [...rows, agent]) }
  apply(changed.at(-1)); assert.deepEqual(agents.list.map(a => a.id), ['iris'])
  await c.restore('juniper')
  assert.deepEqual(changed.at(-1), { id: 'juniper', name: 'Juniper', emoji: '🌿', archived: null })
  apply(changed.at(-1)); assert.deepEqual(agents.list.map(a => [a.id, a.name]), [['iris', 'Iris'], ['juniper', 'Juniper']])
})

test('agent drafts and profile face controls never draw or edit emoji regardless of host emoji field', async () => {
  installDom()
  for (const emoji of ['🌱', null, undefined]) {
    const draft = { draft: 'one', thread: 't1', rev: 2, name: 'Throwaway', soul: 'Soul.', config: 'Config.', ready: true, problems: [], ...(emoji !== undefined ? { emoji } : {}) }
    const drafts = createAgentDrafts({ redraw() {}, api: async () => ({ drafts: [draft] }) })
    await drafts.load('t1')
    drafts.st.open = true
    const view = drafts.view('t1')
    assert.equal(text(view).includes('🌱'), false)
    assert.ok(text(view).includes('Throwaway'))

    for (const face of [null, { url: '/v1/agents/test/face?sha=abc' }]) {
      const data = { identity: { display: 'Test', face, ...(emoji !== undefined ? { emoji } : {}) }, status: {}, files: { memory: {} }, schedule: [], plugins: [] }
      const st = { target: { kind: 'agent', id: 'test' }, data, tab: 'Threads', page: 1, faceMenu: true }
      const host = { profiles: { st }, state: { agents: createAgents([{ id: 'test', name: 'Test', ...(emoji !== undefined ? { emoji } : {}) }]), providers: [] }, threads: { st: { threads: [] } }, schedule() {}, goFiles() {} }
      const profileView = mountProfile(host).body('test')[0]
      const menu = find(profileView, 'profile-face-menu')
      assert.ok(menu)
      assert.ok(find(menu, 'profile-face-preview'))
      const labels = all(menu, 'setrow-title').map(b => text(b))
      assert.deepEqual(labels, face ? ['Upload a picture', 'Use the theme’s face'] : ['Upload a picture'])
      assert.equal(find(profileView, 'primary').getAttribute('class').includes('quiet-primary'), false)
      assert.equal(all(profileView, 'profile-editor').length, 0)
      assert.equal(text(profileView).includes('🌱'), false)
    }
  }
})
