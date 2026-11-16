import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { retirementFixture } from './retire-classic-fixture.mjs'
import { browser, find, all, text } from './browser.js'
import { until, TOKEN } from './harness.js'
import { sessionFromAddress, rememberedSession, importedThreadId } from '../public/imported-threads.js'

test('legacy addresses and stored choices resolve only through explicit import mappings', () => {
  assert.equal(sessionFromAddress('/sessions/old%20chat'), 'old chat')
  assert.equal(sessionFromAddress('/?session=old-chat'), 'old-chat')
  assert.equal(sessionFromAddress('/threads/new-chat'), null)
  assert.equal(sessionFromAddress('/sessions/%E0'), null)
  assert.equal(rememberedSession({ getItem: () => '{"id":"old-chat"}' }), 'old-chat')
  assert.equal(rememberedSession({ getItem: () => 'broken' }), null)
  assert.equal(importedThreadId({ imports: [{ session_id: 'old-chat', thread_id: 'imported-1' }] }, 'old-chat'), 'imported-1')
  assert.equal(importedThreadId({ imports: [] }, 'old-chat'), null)
  assert.equal(importedThreadId({ threads: [{ id: 'imported-1' }] }, 'old-chat'), 'imported-1')
  assert.equal(importedThreadId({ thread: { id: 'imported-1' } }, 'old-chat'), 'imported-1')
  assert.equal(importedThreadId({ imports: [{ session_id: 'another-chat', thread_id: 'wrong' }] }, 'old-chat'), null)
  assert.equal(importedThreadId({ threads: [{ id: 7 }] }, 'old-chat'), null)
})

test('the relay reserves imported before thread IDs, passes only session, and preserves failures', async () => {
  const app = await retirementFixture()
  try {
    const res = await fetch(`${app.url}/api/threads/imported?session=a%2Fb&ignored=1`)
    assert.equal(res.status, 200)
    const request = app.broker.seen.findLast((r) => r.path === '/v1/threads/imported')
    assert.equal(request.search, '?session=a%2Fb')
    assert.equal(request.auth, `Bearer ${TOKEN}`)
    assert.equal((await fetch(`${app.url}/api/threads/imported`)).status, 200)
    assert.equal((await fetch(`${app.url}/api/threads/imported`, { method: 'POST' })).status, 405)
    for (const status of [404, 503]) {
      app.routes['GET /v1/threads/imported'] = (req, res) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end('{"error":"Mapping unavailable."}') }
      const failed = await fetch(`${app.url}/api/threads/imported?session=old-chat`)
      assert.equal(failed.status, status); assert.deepEqual(await failed.json(), { error: 'Mapping unavailable.' })
    }
    assert.equal((await fetch(`${app.url}/sessions/old-chat`)).status, 200)
  } finally { await app.close() }
})

const app = await retirementFixture(), page = browser(app.url)
globalThis.location = { pathname: '/', href: `${app.url}/`, search: '' }
page.watch(); await import('../public/app.js'); page.unwatch()
test.after(async () => { page.stop(); await app.close(); setImmediate(() => process.exit(process.exitCode ?? 0)) })
const root = page.root, click = (n) => { assert.ok(n, 'control exists'); n.fire('click', { preventDefault() {}, stopPropagation() {} }) }
const buttons = (name) => all(root, 'iconbtn').find((n) => n.getAttribute('aria-label') === name)

test('Settings, archive, search and imported thread use threads exclusively', async () => {
  await until(() => all(root, 'tt-row').length === 4)
  click(find(root, 'roundbtn')); await until(() => find(root, 'settings'))
  assert.doesNotMatch(text(find(root, 'settings')), /Classic chats/)
  for (const label of ['Appearance', 'Agents', 'Providers', 'Integrations', 'Voice', 'Files', 'Plugins']) assert.match(text(find(root, 'settings')), new RegExp(label))
  page.press('Escape'); await until(() => !find(root, 'settings'))
  click(buttons('Thread list actions')); await until(() => find(root, 'tt-list-menu'))
  click(all(find(root, 'tt-list-menu'), 'setrow').find(n => /Archived threads/.test(text(n))))
  await until(() => all(root, 'tt-row').length === 50)
  for (const count of [100, 150, 153]) {
    click(find(root, 'tt-archive-more')); await until(() => all(root, 'tt-row').length === count)
  }
  const rows = all(root, 'tt-row')
  assert.equal(text(find(rows[0], 'srow-title')), 'A plan worth keeping')
  assert.equal(text(find(rows.at(-1), 'srow-title')), 'Archived conversation 153', 'old pinned imports do not leap ahead')
  assert.equal(find(rows[0], 'srow-time').childNodes[0].getAttribute('datetime'), app.archives[0].created)
  click(buttons('Search threads')); await until(() => find(root, 'tt-search'))
  const search = find(root, 'tt-search').childNodes.find((n) => n.nodeName === 'INPUT')
  search.value = 'plan worth'; search.fire('input', { target: search })
  await until(() => all(root, 'tt-row').length === 1)
  click(all(root, 'tt-row')[0]); await until(() => all(root, 'tt-message').length === 2)
  assert.match(text(root), /Can we keep the original plan/)
  assert.equal(find(root, 'classic-head'), null)
  assert.equal(app.broker.seen.some((r) => /\/v1\/sessions\/[^/]+\/events/.test(r.path)), false)
  assert.equal(app.broker.seen.some((r) => r.path.startsWith('/v1/sessions') && ['POST', 'PATCH'].includes(r.method)), false)
})

test('no production route or control can select a classic screen, send path or fallback', async () => {
  const source = (await readFile(new URL('../public/app.js', import.meta.url), 'utf8')) + (await readFile(new URL('../public/page/registrations.js', import.meta.url), 'utf8'))
  assert.doesNotMatch(source, /goClassic|startClassicListing|state\.classic|Classic chats|function openThread|function sendMessage|function startDraft|state\.screen = 'thread'/)
  assert.doesNotMatch(source, /api\([^\n]*['"`]\/sessions|\/api\/sessions\/\$\{/)
  assert.match(source, /threads\.view\(wide\)/)
})
