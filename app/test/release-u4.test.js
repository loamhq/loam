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
  const synthetic = replyChip({ engine: 'claude-code', model: '<synthetic>', text: 'Failed to authenticate' })
  assert.equal(synthetic.model, null); assert.equal(synthetic.fullModel, null)
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

test('claim is self-contained and follows server progress to sign-in', async () => {
  const { readFile } = await import('node:fs/promises'), { runInNewContext } = await import('node:vm')
  const source = await readFile('public/claim.html', 'utf8'), script = source.match(/<script>([\s\S]*?)<\/script>/)[1]
  assert.doesNotMatch(source, /<(?:script\s+src|link|img|iframe)\b|@import/i)
  const urls = [...source.matchAll(/url\(([^)]+)\)/g)].map(([, url]) => url)
  assert.equal(urls.length, 2, 'only the two embedded Latin faces load')
  for (const url of urls) assert.match(url, /^data:font\/woff2;base64,[A-Za-z0-9+/=]+$/)
  assert.match(source, /font-src data:;/)
  assert.match(source, /default-src 'none'/)

  const settle = () => new Promise(resolve => setImmediate(resolve))
  function fixture({ code = 202, hash = '#token=fixture-token', firstStage = 'open' } = {}) {
    const nodes = new Map(), posts = [], redirects = [], waits = [], windowEvents = {}, reloads = []
    const node = () => ({
      hidden: true, value: '', textContent: '', href: 'https://example.test/', events: {},
      addEventListener(name, fn) { this.events[name] = fn }, setAttribute() {}, focus() {},
    })
    const get = id => {
      if (!nodes.has(id)) nodes.set(id, node())
      return nodes.get(id)
    }
    const location = { hash, pathname: '/claim', search: '', assign(value) { redirects.push(value) }, reload() { reloads.push(this.hash) } }
    let stage = firstStage, pagePath, now = 0
    runInNewContext(script, {
      addEventListener(name, fn) { windowEvents[name] = fn },
      document: { getElementById: get }, location, URLSearchParams, AbortSignal,
      Date: { now: () => now },
      history: { replaceState(_state, _title, path) { pagePath = path } },
      setTimeout(fn, delay) { waits.push({ fn, delay }) },
      async fetch(path, options) {
        if (options.method === 'POST') {
          posts.push(JSON.parse(options.body))
          return { status: code, ok: code < 300, json: async () => ({ error: `The exact refusal for ${code}.` }) }
        }
        assert.equal(path, '/claim?status')
        assert.equal(options.redirect, 'manual')
        return stage === 'ready' ? { type: 'opaqueredirect' } : { ok: true, json: async () => ({ stage }) }
      },
    })
    assert.equal(pagePath, '/claim', 'the token leaves the address')
    get('username').value = 'PageTester'
    get('password').value = 'synthetic-password'
    return {
      get, posts, redirects, waits, location, windowEvents, reloads,
      submit: () => get('claim-form').events.submit({ preventDefault() {} }),
      async tick(value, elapsed = 1000) { stage = value; now += elapsed; waits.shift().fn(); await settle() },
    }
  }

  for (const code of [400, 403, 429, 500]) {
    const f = fixture({ code })
    await settle()
    await f.submit()
    assert.equal(f.get('error').textContent, `The exact refusal for ${code}.`)
    assert.equal(f.get('claim-form').hidden, false)
    assert.equal(f.posts[0].token, 'fixture-token')
  }
  const success = fixture()
  await settle()
  success.get('username').events.input()
  assert.match(success.get('username-hint').textContent, /Saved as pagetester/)
  const submitted = success.submit()
  assert.equal(success.get('progress').hidden, false)
  assert.match(success.get('progress-note').textContent, /Keep this page open/)
  await settle()
  await success.tick('saving', 31000)
  assert.match(success.get('delay-note').textContent, /taking longer/)
  assert.deepEqual(success.redirects, [], 'time passing alone never goes to sign-in')
  await success.tick('starting_login')
  assert.match(success.get('progress-note').textContent, /You can close this page/)
  assert.equal(success.get('password').value, '')
  await success.tick('ready')
  await submitted
  assert.equal(success.get('next').hidden, false)
  success.waits.shift().fn()
  assert.deepEqual(success.redirects, ['https://example.test/'])
  assert.equal(success.posts[0].username, 'PageTester')

  const failed = fixture()
  await settle()
  const failedSubmit = failed.submit()
  await settle()
  await failed.tick('save_failed')
  await failedSubmit
  assert.equal(failed.get('claim-form').hidden, false)
  assert.match(failed.get('error').textContent, /Try again/)
  const resumed = fixture({ hash: '', firstStage: 'starting_login' })
  await settle()
  assert.equal(resumed.get('claim-form').hidden, true)
  assert.match(resumed.get('step-text').textContent, /Getting sign-in ready/)
  await resumed.tick('finish_failed')
  assert.match(resumed.get('progress-note').textContent, /run loam status/)
  assert.equal(resumed.get('check').hidden, false)

  const missing = fixture({ hash: '' })
  await settle()
  await missing.submit()
  assert.equal(missing.posts.length, 0)
  assert.equal(missing.get('claim-form').hidden, true)
  missing.location.hash = '#fixture-token'
  missing.windowEvents.hashchange()
  assert.deepEqual(missing.reloads, ['#fixture-token'])
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
  // The last run's card said "Loam is up to date" under "Loam 0.1.2 is ready".
  const c = createUpdate({ changed() {}, reload() {}, later() {}, cancel() {}, api: async () => ({ ...data({}), run: { outcome: 'applied', from_version: '0.1.0', to_version: '0.1.1', steps: [] } }) })
  await c.check()
  assert.doesNotMatch(text(c.view()), /Loam is up to date/)
})

test('a release whose signature does not verify is never offered as ready (docs/signing.md)', async () => {
  const { installDom } = await import('./fakedom.js'), { text } = await import('./browser.js')
  installDom()
  const blocked = { code: 'signature', reason: 'unsigned', message: 'Release 0.1.1 was refused: it is not signed. It may have been tampered with, so nothing was changed and it will not be applied.' }
  const signing = { keys: 1, checked: true, message: 'Releases are checked against 1 maintainer key(s) before they run.' }
  const c = createUpdate({ changed() {}, reload() {}, later() {}, cancel() {}, api: async () => ({ current: { version: '0.1.0', kind: 'stock', signing }, check: { latest: { version: '0.1.1' }, available: true, blocked, changes: null } }) })
  await c.check()
  const words = text(c.view())
  assert.match(words, /Loam 0\.1\.1 could not be verified/)
  assert.doesNotMatch(words, /is ready|your agent will tell you what’s new|Update Loam/)
  assert.match(words, /it is not signed/)
  assert.match(words, /checked against 1 maintainer key/)
})
