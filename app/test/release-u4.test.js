import test from 'node:test'
import assert from 'node:assert/strict'
import { createUpdate } from '../public/update.js'
import { createPasskey, authSettingsURL } from '../public/passkey.js'
import { untouchedHello } from '../public/onboarding.js'
import { replyChip } from '../public/thread-experience.js'
import { messageSegments } from '../public/thread-record.js'
import { fakeBroker, appAgainst, closing, TOKEN } from './harness.js'

const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
test('only an untouched runtime hello enters onboarding; presets have prose and no turn facts', () => {
  const preset = { preset: true, text: 'Hello!', engine: 'claude-code', model: 'unwanted', steps: [{ type: 'tool' }] }
  assert.equal(untouchedHello({ thread: { hello: true }, messages: [preset], has_more: false }), true)
  for (const record of [{ thread: { hello: false }, messages: [preset], has_more: false }, { thread: { hello: true }, messages: [preset, { author: 'you' }], has_more: false }, { thread: { hello: true }, messages: [preset], has_more: true }]) assert.equal(untouchedHello(record), false)
  assert.deepEqual(replyChip(preset), { provider: null, model: null, fullModel: null })
  assert.deepEqual(messageSegments(preset), [{ text: 'Hello!' }])
})
test('update routes preserve status, refusal, JSON body, bearer and no-store; other verbs stay unavailable', async t => {
  const routes = {}
  for (const [method, path] of [['GET', '/update'], ['GET', '/update/progress'], ['POST', '/update/check'], ['POST', '/update/apply'], ['POST', '/update/rollback']]) routes[`${method} /v1${path}`] = (req, res, url, raw) => json(res, { refusal: { code: 'library-edits', library: { diff: 'the diff' } }, body: raw }, method === 'POST' ? 409 : 200)
  const broker = await fakeBroker(routes), app = await appAgainst(broker); t.after(closing(app, broker))
  for (const key of Object.keys(routes)) {
    const [method, route] = key.split(' '), response = await fetch(app.url + route.replace('/v1', '/api'), { method, ...(method === 'POST' ? { headers: { 'content-type': 'application/json' }, body: JSON.stringify({ to: '1.1.0', accept_library_edits: true }) } : {}) })
    assert.equal(response.status, method === 'POST' ? 409 : 200); assert.equal(response.headers.get('cache-control'), 'no-store')
    const body = await response.json(); assert.equal(body.refusal.library.diff, 'the diff')
    assert.equal(broker.seen.at(-1).auth, `Bearer ${TOKEN}`)
    if (method === 'POST') assert.deepEqual(JSON.parse(body.body), { to: '1.1.0', accept_library_edits: true })
  }
  assert.equal((await fetch(app.url + '/api/update/apply', { method: 'GET' })).status, 404)
})
test('library refusal requires explicit acceptance; restart errors keep polling and applied reloads once', async () => {
  let reads = 0, reloads = 0, timer, body
  const controller = createUpdate({ changed() {}, reload() { reloads++ }, later(fn) { timer = fn }, cancel() {}, api: async (method, path, value) => {
    if (path === '/update') return { current: { version: '1.0.0' }, check: { latest: { version: '1.1.0' } } }
    if (path === '/update/apply') { body = value; if (!value.accept_library_edits) throw Object.assign(new Error('Your library has edits.'), { body: { refusal: { code: 'library-edits', library: { diff: 'the diff' } } } }); return { run: { outcome: 'starting' } } }
    if (path === '/update/progress') { if (++reads === 1) throw Object.assign(new Error('Restarting'), { status: 503 }); return { outcome: 'applied' } }
    return controller.st.data
  } })
  await controller.check(); await controller.run('apply'); assert.equal(controller.st.review.diff, 'the diff'); assert.equal(body.accept_library_edits, undefined)
  await controller.run('apply', true); assert.equal(body.accept_library_edits, true)
  await timer(); assert.equal(controller.st.reconnecting, true); assert.equal(reloads, 0)
  await timer(); assert.equal(reloads, 1); controller.stop()
})
test('passkey codes stay in the page, age out, stop on dismissal and ignore a late answer', async () => {
  let now = Date.parse('2026-11-15T06:00:00Z'), resolve, timer
  const paths = []
  const passkey = createPasskey({ changed() {}, now: () => now, later(fn) { timer = fn }, cancel() {}, request: async (path, options) => { paths.push([path, options]); return new Promise(r => { resolve = r }) } })
  passkey.start(); resolve({ ok: true, json: async () => ({ code: 'SECRET12', at: new Date(now - 12000).toISOString(), age_seconds: 12 }) }); await new Promise(r => setImmediate(r))
  assert.equal(passkey.st.code, 'SECRET12'); assert.equal(paths[0][0], '/_loam/login-code'); assert.equal(paths[0][1].cache, 'no-store')
  timer(); passkey.stop(); resolve({ ok: true, json: async () => ({ code: 'LATECODE', at: new Date(now).toISOString(), age_seconds: 0 }) }); await new Promise(r => setImmediate(r)); assert.equal(passkey.st.code, null)
  passkey.start(); resolve({ ok: true, json: async () => ({ code: 'OLDCODE1', at: new Date(now - 901000).toISOString(), age_seconds: 901 }) }); await new Promise(r => setImmediate(r)); assert.equal(passkey.st.code, null); passkey.stop()
  assert.equal(authSettingsURL('https://example.test/threads/one'), 'https://auth.example.test/settings')
})

test('claim is self-contained; token leaves the address; exact refusals and already-claimed link are drawn', async () => {
  const { readFile } = await import('node:fs/promises'), { runInNewContext } = await import('node:vm')
  const source = await readFile('public/claim.html', 'utf8'), script = source.match(/<script>([\s\S]*?)<\/script>/)[1]
  assert.doesNotMatch(source, /<(?:script\s+src|link|img|iframe)\b|@import/i)
  const urls = [...source.matchAll(/url\(([^)]+)\)/g)].map(([, url]) => url)
  assert.equal(urls.length, 2, 'only the two embedded Latin faces load')
  for (const url of urls) assert.match(url, /^data:font\/woff2;base64,[A-Za-z0-9+/=]+$/)
  assert.match(source, /font-src data:;/, 'font policy permits data only')
  assert.match(source, /default-src 'none'/)
  function fixture(status, hash = '#fixture-token') {
    const nodes = new Map(), calls = [], redirects = [], waits = [], next = [], windowEvents = {}, reloads = []
    const node = () => ({ hidden: false, value: '', disabled: false, events: {}, addEventListener(name, fn) { this.events[name] = fn }, setAttribute() {}, appendChild(child) { next.push(child) } })
    const get = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id) }
    const location = { hash, host: 'example.test', pathname: '/claim', search: '', href: 'https://example.test/claim' + hash, assign(value) { redirects.push(value) }, reload() { reloads.push(location.hash) } }
    runInNewContext(script, { addEventListener(name, fn) { windowEvents[name] = fn }, document: { getElementById: get, createElement: node }, location, history: { replaceState(...args) { calls.push(args) } }, URL, URLSearchParams, setTimeout(fn, delay) { waits.push({ fn, delay }) }, fetch: async (path, options) => { calls.push({ path, body: JSON.parse(options.body), type: options.headers['content-type'] }); return { status, ok: status === 200, json: async () => status === 200 ? { ok: true, username: 'person', next: 'https://auth.example.test/', wait_seconds: 3 } : { error: `The exact refusal for ${status}.` } } } })
    get('username').value = 'person'; get('password').value = 'synthetic-password'
    return { get, calls, waits, redirects, location, windowEvents, reloads, submit: () => get('claim-form').events.submit({ preventDefault() {} }) }
  }
  for (const status of [400, 403, 429, 500, 410]) {
    const f = fixture(status); assert.equal(f.calls[0][2], '/claim'); await f.submit()
    assert.equal(f.get('error').textContent, `The exact refusal for ${status}.`)
    assert.equal(f.calls[1].type, 'application/json'); assert.equal(f.calls[1].body.token, 'fixture-token')
    if (status === 410) { assert.equal(f.get('claim-form').hidden, true); assert.equal(f.get('next').href, 'https://example.test/'); assert.equal(f.get('password').value, '') }
  }
  const success = fixture(200, '#token=fixture-token'); await success.submit()
  assert.equal(success.get('password').value, ''); assert.equal(success.waits[0].delay, 3000); assert.equal(success.redirects.length, 0)
  success.waits[0].fn(); assert.equal(success.redirects[0], 'https://auth.example.test/')
  const missing = fixture(403, ''); assert.equal(missing.get('claim-form').hidden, true); await missing.submit(); assert.equal(missing.calls.length, 1)
  // The full link pasted into a tab already on /claim changes only the fragment: the page reads it again.
  missing.location.hash = '#fixture-token'; missing.windowEvents.hashchange(); assert.deepEqual(missing.reloads, ['#fixture-token'])
  missing.location.hash = ''; missing.windowEvents.hashchange(); assert.equal(missing.reloads.length, 1, 'an emptied fragment reloads nothing')
})

test('an available release with no System/Loam changes draws no empty file list', async () => {
  // Found by the 2026-11-16 first-day walk: "What's new" and "Files included in this release:"
  // over nothing. The release notes reach the person through their agent.
  const { installDom } = await import('./fakedom.js'), { text } = await import('./browser.js')
  installDom()
  const data = changes => ({ current: { version: '0.1.0', kind: 'stock' }, check: { latest: { version: '0.1.1' }, available: true, changes } })
  for (const [changes, listed] of [[{ added: [], changed: [], removed: [] }, false], [{ added: [], changed: ['System/Loam/skills/x/SKILL.md'], removed: [] }, true]]) {
    const c = createUpdate({ changed() {}, reload() {}, later() {}, cancel() {}, api: async () => data(changes) })
    await c.check()
    const words = text(c.view())
    assert.match(words, /Loam 0\.1\.1 is ready/)
    assert.match(words, /your agent will tell you what’s new/)
    assert.doesNotMatch(words, /Files included in this release/)
    assert.equal(/System\/Loam this release replaces/.test(words), listed)
    assert.equal(words.includes('SKILL.md'), listed)
  }
})
