// Full application interaction in the repository's DOM harness. No layout
// engine is present; staging must still confirm spacing and iframe behaviour.
import assert from 'node:assert/strict'
import { oneProductFixture } from './one-product-fixture.mjs'
import { filesHost } from './files-host.js'
import { browser, all, find, text } from './browser.js'
import { until } from './harness.js'
import { loadThemes } from '../src/themes.js'

const width = Number(process.argv[2]), mode = process.argv[3]
const app = await oneProductFixture()
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const host = filesHost()
for (const [key, value] of Object.entries(host.routes)) if (key.includes('/v1/files/')) app.routes[key] = value
const owner = app.f.agents[0].id
const live = { id: 'resident', kind: 'thread', thread_id: 'a-quieter-week', thread_title: 'A quieter week', agent: owner,
  state: 'working', rss_mb: 256, last_active: new Date(Date.now() - 120000).toISOString(), ending: { loses_work: true } }
let sessions = [live], liveStream, jobs = { slots: [], queue: [], landing: [] }
const box = () => ({ ram_total_mb: 32768, ram_used_mb: 16384, can_start: 12, sessions: { live: sessions.length } })
app.routes['GET /v1/sessions/live'] = (req, res) => json(res, { sessions, box: box() })
app.routes['GET /v1/sessions/live/events'] = (req, res) => { liveStream = res; res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
app.routes['DELETE /v1/sessions/resident'] = (req, res) => { sessions = []; json(res, {}); liveStream?.write(`data: ${JSON.stringify({ type: 'sessions.live', sessions, box: box() })}\n\n`) }
app.routes['GET /v1/activity'] = (req, res) => json(res, { jobs, items: [{ id: 'item', kind: 'job', title: 'Garden notes', agent: owner, outcome: 'completed', seen: false, ts: '2026-11-11T10:00:00Z' }], unread: 77, total: 1, next_offset: null })
const apps = [{ name: 'Garden', owner, source: 'yours', kind: 'site', url: 'https://garden.example.com/', also_at: [], access: 'private', frame: true, expires: '2026-11-25', serves: 'static' },
  { name: 'Journal', owner: 'owner', source: 'yours', kind: 'project', project: 'journal', url: 'https://journal.example.org/', access: 'public', frame: false }]
app.routes['GET /v1/apps'] = (req, res) => json(res, { apps, sources: ['yours'] })
const page = browser(app.url, { innerWidth: width }), root = page.root
globalThis.localStorage.setItem('loam-ui.files', JSON.stringify({ inbox: '00_INBOX', daily: '10_DAILY', shortcuts: [{ root: 'vault', path: 'Notes' }] }))
globalThis.location = { pathname: '/', href: app.url + '/', search: '' }
globalThis.__loamAppearance = { themes: (await loadThemes()).themes, available: true, values: { phone: { mode, size: 100, theme: 'latte' }, desktop: { mode, size: 100, theme: 'latte' } } }
const browserOpens = [], copies = []
globalThis.open = url => browserOpens.push(url)
Object.defineProperty(globalThis.navigator, 'clipboard', { configurable: true, value: { writeText: async value => copies.push(value) } })
const click = node => { assert.ok(node, 'control exists'); node.fire('click', { preventDefault() {}, stopPropagation() {} }) }
const section = label => all(root, width >= 1100 ? 'railtab' : 'navbtn').find(node => text(node) === label)
const utility = label => [...all(root, 'railround'), ...all(root, 'roundbtn')].find(node => node.getAttribute('aria-label') === label)
const setting = label => [...all(root, 'navrow'), ...all(root, 'setrow')].find(node => text(find(node, 'setrow-title')) === label || text(node) === label)
try {
  page.watch(); await import('../public/app.js'); page.unwatch()
  await until(() => all(root, 'tt-row').length === 4)
  assert.equal(document.documentElement.dataset.theme, mode)
  assert.deepEqual(all(root, width >= 1100 ? 'railtab' : 'navbtn').map(text), ['Chats', 'Files', 'Apps'])
  assert.equal(find(root, 'run-indicator'), null); assert.equal(find(root, 'accounttile'), null)
  const face = find(root, 'blob')
  assert.match(face.style.background, /^var\(--agent-avatar-tone-\d+, var\(--persona-/)

  for (const label of ['Appearance', 'Agents', 'Providers', 'Integrations', 'Accounts', 'Files', 'Plugins', 'Sign-in', 'Update']) {
    click(utility('Settings')); await until(() => find(root, 'settings'))
    const destinations = width >= 1100 ? all(root, 'navrow').map(text) : all(root, 'setrow-title').map(text)
    assert.deepEqual(destinations, [...(width < 1100 ? ['Activity'] : []), 'Appearance', 'Agents', 'Providers', 'Integrations', 'Accounts', 'Files', 'Plugins', 'Sign-in', 'Update'])
    if (width < 1100) await until(() => text(find(setting('Activity'), 'setrow-sub')) === '1 working')
    click(setting(label))
    await until(() => all(root, 'sheet-title').some(node => text(node) === label))
    if (label === 'Plugins') {
      assert.equal(all(root, 'plugin-row').length, (await loadThemes()).themes.length + 2)
      assert.ok(all(root, 'plugin-row').some(node => /Latte[\s\S]*Theme/.test(text(node))))
      assert.ok(all(root, 'plugin-row').every(node => !text(node).includes('1.0.0')))
      assert.ok(all(root, 'plugin-state').some(node => text(node) === 'In use'))
      assert.equal(all(root, 'plugin-switch').length, 2)
      assert.ok(all(root, 'plugin-switch').every(node => node.getAttribute('aria-checked') === 'true'))

    }
    if (label === 'Files') {
      assert.match(text(find(root, 'settings')), /Shortcuts[\s\S]*Notes/)
      click(all(root, 'iconbtn').find(node => node.getAttribute('aria-label') === 'Remove shortcut to Notes'))
      await until(() => text(find(root, 'settings')).includes('Add shortcuts from a file or folder’s menu.'))
      assert.deepEqual(JSON.parse(globalThis.localStorage.getItem('loam-ui.files')).shortcuts, [])
    }
    page.press('Escape'); await until(() => !find(root, 'settings'))
  }

  if (width < 1100) { click(utility('Settings')); await until(() => setting('Activity')); click(setting('Activity')) }
  else click(utility('Activity'))
  await until(() => find(root, 'activity-feed') && find(root, 'run-row'))
  for (const label of width < 1100 ? ['Settings'] : ['Settings', 'Activity']) {
    const button = utility(label)
    assert.equal(find(button, 'activity-live'), null, `${label} has no work indicator while a session is working`)
    assert.equal(button.childNodes.length, 1, `${label} contains only its icon`)
    assert.equal(button.firstChild.nodeName.toLowerCase(), 'svg')
  }
  assert.match(text(root), /Now[\s\S]*16 GB of 32 GB used · Room for about 12 more[\s\S]*A quieter week[\s\S]*256 MB[\s\S]*Earlier/)
  for (const cls of ['activity-count', 'activity-unread', 'tt-unread']) assert.equal(find(find(root, 'activity-page'), cls), null)
  assert.doesNotMatch(text(root), /Mark all seen|unseen/)
  click(all(root, 'pillbtn').find(node => text(node) === 'End'))
  await until(() => find(root, 'run-confirm'))
  assert.equal(app.broker.seen.some(row => row.method === 'DELETE'), false)
  click(all(root, 'pillbtn').find(node => text(node) === 'End session'))
  await until(() => !find(root, 'run-row'))
  assert.ok(app.broker.seen.some(row => row.method === 'DELETE' && row.path === '/v1/sessions/resident'))
  assert.equal(app.broker.seen.some(row => row.path.endsWith('/seen')), false)

  if (width >= 1100) { page.press('Escape'); await until(() => !find(root, 'activity-page')) }

  click(section('Files')); await until(() => all(root, 'fs-rootcard').length === 2)
  assert.deepEqual(all(root, 'fs-rootname').map(text), ['Vault', 'AI Spaces'])
  assert.equal(all(root, 'fs-shortcuts').length, 1)
  click(all(root, 'fs-rootcard')[1]); await until(() => text(root).includes('Nothing here yet.'))
  assert.equal(find(root, 'fs-host-records'), null)

  click(section('Apps')); await until(() => all(root, 'apps-row').length === 2)
  assert.equal(all(root, 'apps-group').length, 1)
  click(all(root, 'apps-row')[0])
  if (width >= 1100) {
    await until(() => find(root, 'apps-embed'))
    assert.equal(find(root, 'apps-embed').getAttribute('title'), 'Garden')
    click(all(root, 'pillbtn').find(node => text(node) === 'Copy link'))
    await until(() => text(root).includes('Copied')); assert.deepEqual(copies, [apps[0].url])
    click(all(root, 'iconbtn').find(node => node.getAttribute('aria-label') === 'Back to apps'))
    await until(() => all(root, 'apps-row').length === 2)
  } else assert.deepEqual(browserOpens, [apps[0].url])
  click(all(root, 'apps-row')[1]); assert.equal(browserOpens.at(-1), apps[1].url)
  click(section('Chats')); await until(() => all(root, 'tt-row').length === 4)
  assert.ok(find(root, 'tt-unread'), 'Chats retain their own unread dots')
  console.log(JSON.stringify({ width, mode, sections: 3, settings: 8, roots: 2, activitySeenWrites: 0, workIndicators: 0, apps: 2, result: 'passed', visualLayout: 'not evaluated' }))
} finally { page.unwatch(); page.stop(); await app.close(); setImmediate(() => process.exit(process.exitCode ?? 0)) }
