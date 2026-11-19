import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readConfig } from '../src/config.js'
import { INSTALLATION_DEFAULTS } from '../public/installation.js'
import { setOwner, ownerInitial, isOwner } from '../public/owner.js'
import { actorName } from '../public/schedule-change.js'
import { editWords } from '../public/schedules.js'
import { canFrame } from '../public/apps.js'
import { attachmentFilePath } from '../public/attach.js'
import { addBoardItem, applyThreadEvent, groupJoinNotices } from '../public/thread-record.js'
import { createThreads } from '../public/threads.js'
import { dailyNotePath, inboxNotePath } from '../public/files.js'
import { formatTime } from '../public/time.js'
import { createPluginLoader } from '../public/plugin-loader.js'
import { createCoreRegistry } from '../public/core-registrations.js'
import { shippedPlugins } from '../src/shipped-plugins.js'
import { audioRows, AUDIO_PLUGINS } from './audio-plugins.mjs'
import { fakeBroker, appAgainst, closing, TOKEN } from './harness.js'
import { installDom } from './fakedom.js'
import { h } from '../public/ui.js'
import { all, text } from './browser.js'

const identity = { id: 'alex', name: 'Alex', accepts: ['alex', 'legacy-owner'] }
const settings = { APPS_DOMAIN: 'apps.example.org', APP_TIME_ZONE: 'Asia/Tokyo', VAULT_INBOX_FOLDER: 'Inbox', VAULT_DAILY_FOLDER: 'Journal', VAULT_BOARDS_FOLDER: 'Boards', VAULT_GENERAL_BOARD: 'Inbox/team.md', VAULT_ATTACHMENTS_FOLDER: 'Media' }
const json = (res, body) => { res.setHeader('content-type', 'application/json'); res.end(JSON.stringify(body)) }

test('owner relay preserves runtime identity, authentication, no-store and refusals; writes never reach the host', async t => {
  const routes = { 'GET /v1/owner': (req, res) => json(res, identity) }
  const broker = await fakeBroker(routes), app = await appAgainst(broker)
  t.after(closing(app, broker))
  const out = await fetch(app.url + '/api/owner?ignored=1')
  assert.deepEqual(await out.json(), identity)
  assert.equal(out.headers.get('cache-control'), 'no-store')
  assert.equal(broker.seen[0].auth, `Bearer ${TOKEN}`)
  assert.equal(broker.seen[0].search, '')
  assert.equal((await fetch(app.url + '/api/owner', { method: 'POST', body: '{}' })).status, 405)
  assert.equal(broker.seen.length, 1)
  routes['GET /v1/owner'] = (req, res) => { res.statusCode = 503; json(res, { error: 'Owner unavailable.' }) }
  const failed = await fetch(app.url + '/api/owner')
  assert.equal(failed.status, 503)
  assert.deepEqual(await failed.json(), { error: 'Owner unavailable.' })
})

test('installation settings reach the pre-paint script and CSP without credentials; invalid settings fail startup', async t => {
  assert.deepEqual(readConfig({}).installation, INSTALLATION_DEFAULTS)
  for (const env of [{ APPS_DOMAIN: 'https://example.org' }, { APPS_DOMAIN: 'example.org; script-src *' }, { APP_TIME_ZONE: 'not/a-zone' }, { VAULT_INBOX_FOLDER: '../elsewhere' }]) assert.throws(() => readConfig(env))
  const broker = await fakeBroker({}), app = await appAgainst(broker, settings)
  t.after(closing(app, broker))
  const out = await fetch(app.url + '/appearance-state.js')
  assert.equal(out.headers.get('cache-control'), 'no-store')
  const script = await out.text(), sandbox = {}
  vm.runInNewContext(script, sandbox)
  assert.equal(sandbox.LOAM_INSTALLATION.appsDomain, 'apps.example.org')
  assert.equal(sandbox.LOAM_INSTALLATION.timeZone, 'Asia/Tokyo')
  assert.equal(sandbox.LOAM_INSTALLATION.generalBoard, 'Inbox/team.md')
  assert.ok(!script.includes(TOKEN))
  const page = await fetch(app.url)
  assert.match(page.headers.get('content-security-policy'), /https:\/\/\*\.apps\.example\.org/)
  assert.ok(!page.headers.get('content-security-policy').includes('example.com'))
})

test('renamed owner and aliases drive schedule labels, board attribution and thread ownership; strangers stay distinct', t => {
  installDom(); setOwner(identity)
  assert.equal(ownerInitial(), 'A')
  for (const id of identity.accepts) {
    assert.equal(isOwner(id), true)
    assert.equal(actorName(id), 'you')
    assert.equal(editWords({ ts: '2026-11-14T00:00:00Z', by: id }, Date.parse('2026-11-14T00:00:01Z')), 'Edited just now by you')
  }
  assert.equal(isOwner('old-owner'), false)
  setOwner({ ...identity, accepts: ['alex', 'old-owner'] })
  assert.equal(actorName('old-owner'), 'you') // A real legacy id, only when accepted.
  setOwner(identity)
  assert.equal(isOwner('stranger'), false)
  assert.equal(actorName('stranger'), 'Stranger')
  assert.equal(addBoardItem('', 'Keep this'), '- [ ] Keep this <!-- Alex -->\n')
  const pending = { text: 'Same words' }, o = { messages: [], thread: { members: [] }, typing: new Set(), steps: new Map(), pending }
  applyThreadEvent(o, { type: 'message.done', message: { id: 'm1', author: 'stranger', text: 'Same words' } })
  assert.equal(o.pending, pending)
  applyThreadEvent(o, { type: 'message.done', message: { id: 'm2', author: 'legacy-owner', text: 'Same words' } })
  assert.equal(o.pending, null)
  const threads = createThreads({ api: async () => ({}), redraw() {}, agents: () => [], compose: { field: () => ({ value: '' }), files: () => [], busy: () => false, render: () => h('div'), dragProps: () => ({}) } })
  t.after(() => threads.pause())
  threads.st.open = { thread: { id: 'one', title: 'A thought', members: [] }, messages: [
    { id: 'm1', author: 'alex', kind: 'message', text: 'Current owner' },
    { id: 'm2', author: 'legacy-owner', kind: 'message', text: 'Earlier owner id' },
    { id: 'm3', author: 'stranger', kind: 'message', text: 'Someone else' },
  ], permissions: new Map(), typing: new Set(), steps: new Map(), board: { markdown: '', path: '' } }
  const messagesView = all(threads.view(false), 'tt-message')
  assert.equal(messagesView.length, 3)
  assert.deepEqual(messagesView.map(node => node.classList.contains('mine')), [true, true, false])
  const agents = [{ id: 'quill', name: 'Quill' }]
  const messages = [{ id: 'm1', author: 'legacy-owner', text: '@quill' }, { id: 'm2', kind: 'system', text: 'Alex brought in Quill' }]
  assert.deepEqual(groupJoinNotices(messages, agents)[1].joins.names, ['quill'])
  assert.equal(groupJoinNotices([{ ...messages[0], author: 'stranger' }, messages[1]], agents)[1].joins, undefined)
})

test('configured domain, folders and zone govern frames, note names and attachment paths; saved folder choices survive', async t => {
  globalThis.LOAM_INSTALLATION = readConfig(settings).installation
  t.after(() => { delete globalThis.LOAM_INSTALLATION })
  assert.equal(canFrame({ frame: true, url: 'https://garden.apps.example.org/' }), true)
  for (const url of ['https://garden.example.com/', 'https://evilapps.example.org/', 'https://apps.example.org.evil/']) assert.equal(canFrame({ frame: true, url }), false)
  const { filesSettings } = await import('../public/store.js?configured-installation-test')
  assert.equal(filesSettings(null).inbox, 'Inbox')
  assert.equal(filesSettings(null).daily, undefined)
  assert.equal(filesSettings({ daily: 'Old browser folder' }).daily, undefined, 'daily notes belong to the plugin')
  assert.equal(filesSettings({ inbox: 'My inbox', daily: 'My daily' }).inbox, 'My inbox')
  assert.equal(attachmentFilePath('/some/vault/Media/image.png'), 'Media/image.png')
  assert.equal(attachmentFilePath('/some/vault/98_ATTACHMENTS/image.png'), null)
  const now = new Date('2026-11-14T23:30:00Z')
  assert.equal(dailyNotePath('Journal', now), 'Journal/2026-11-15.md')
  assert.equal(inboxNotePath('Inbox', now), 'Inbox/2026-11-15 0830.md')
  assert.equal(formatTime(now, { locale: 'en-GB' }), '08:30')
  assert.equal(addBoardItem('', 'A note', 0, now, 'Inbox/team.md'), '- 2026-11-14 · A note <!-- Alex -->\n')
})

test('fresh shipped catalog follows explicit manifest defaults: no extension ships on, themes including Latte on', async () => {
  const rows = await shippedPlugins('public')
  for (const row of rows) assert.equal(row.on, row.default_on)
  assert.deepEqual(rows.filter(row => row.switch === 'plugin' && row.on), [])
  assert.ok(rows.filter(row => row.kind.includes('theme')).every(row => row.on))
  assert.equal(rows.find(row => row.id === 'latte').on, true)
  const imports = [], loader = createPluginLoader({ registry: createCoreRegistry(), ui: {}, request: async () => { throw Object.assign(new Error('Old host'), { status: 404 }) }, fallback: rows, importer: async url => { imports.push(url); return { activate() {} } } })
  await loader.refresh()
  assert.deepEqual(imports, [])
  await loader.dispose()
})

// Dictation and Read aloud left Loam's core for the Market in 0.2.9: a fresh
// install has neither, so nothing draws a mic or a read-aloud control until
// they are installed and on. The control is the same page with the Market
// copies installed (test/audio-plugins.mjs): then both load and draw.
test('a fresh install has neither audio plugin and draws no mic or read-aloud control; installed and on, both do', async () => {
  const rows = await shippedPlugins('public')
  assert.deepEqual(rows.filter(row => AUDIO_PLUGINS.includes(row.id)), [])
  async function page(catalog) {
    const registry = createCoreRegistry(), imports = []
    const loader = createPluginLoader({ registry, ui: {}, request: async () => ({ plugins: catalog }), importer: async url => {
      imports.push(url)
      return { activate(loam) {
        if (url.includes('/dictation/')) loam.places.composer.tool.add({ id: 'dictation.microphone', slot: 'microphone', render: () => 'mic' })
        if (url.includes('/read-aloud/')) loam.places.message.footer.action.add({ id: 'read-aloud.footer', slot: 'row', render: () => 'listen' })
      } }
    } })
    await loader.refresh()
    const out = { imports: imports.map(url => AUDIO_PLUGINS.find(id => url.includes(`/${id}/`))).sort(), mic: registry.draw('composer.tool', { slot: 'microphone', target: 'draft' }).includes('mic'),
      listen: registry.draw('message.footer.action', { slot: 'row', thread: {}, message: {}, defaultView: () => 'core' }).includes('listen') }
    await loader.dispose()
    return out
  }
  assert.deepEqual(await page(rows), { imports: [], mic: false, listen: false })
  assert.deepEqual(await page([...rows, ...await audioRows()]), { imports: ['dictation', 'read-aloud'], mic: true, listen: true })
  const off = (await audioRows()).map(row => ({ ...row, on: false }))
  assert.deepEqual(await page([...rows, ...off]), { imports: [], mic: false, listen: false })
})

test('existing runtime plugin state overrides changed shipped defaults across refresh, outage and reload without writes', async () => {
  const rows = await shippedPlugins('public')
  // What a host that has the Market's audio plugins on answers (0.2.9).
  const saved = [...rows.map(row => ({ ...row, on: row.switch === 'appearance' ? row.on : false })), ...await audioRows()]
  const calls = []
  let offline = false
  const request = async (method, route) => { calls.push([method, route]); if (offline) throw new Error('Offline'); return { plugins: structuredClone(saved) } }
  for (let visit = 0; visit < 2; visit++) {
    const imports = [], loader = createPluginLoader({ registry: createCoreRegistry(), ui: {}, request, fallback: rows, importer: async url => { imports.push(url); return { activate() {} } } })
    await loader.refresh()
    assert.deepEqual(loader.st.rows.map(row => [row.id, row.on]), saved.map(row => [row.id, row.on]))
    assert.equal(imports.length, 2)
    offline = true; await loader.refresh(); offline = false
    assert.equal(loader.st.rows.find(row => row.id === 'dictation').on, true)
    await loader.dispose()
  }
  assert.ok(calls.every(([method, route]) => method === 'GET' && route === '/plugins'))
})

test('the server listens on loopback unless APP_HOST says otherwise, and reads the preview browser OOM score', async () => {
  const { readConfig } = await import('../src/config.js')
  assert.equal(readConfig({}).host, '127.0.0.1')
  assert.equal(readConfig({ APP_HOST: '0.0.0.0' }).host, '0.0.0.0')
  assert.equal(readConfig({}).appMedia.oomScoreAdj, null)
  assert.equal(readConfig({ APP_PREVIEW_OOM_SCORE_ADJ: '800' }).appMedia.oomScoreAdj, 800)
  assert.equal(readConfig({ APP_PREVIEW_OOM_SCORE_ADJ: '5000' }).appMedia.oomScoreAdj, null)
  assert.equal(readConfig({ APP_PREVIEW_OOM_SCORE_ADJ: 'high' }).appMedia.oomScoreAdj, null)
})
