// Real browser, synthetic day-one records: the first-day polish (2026-11-17),
// each item at 390 and 1440, light and dark. The same file run from a checkout
// of the commit before is the "before" pictures; nothing in it depends on the
// change.
//
//   FIRST_DAY_EVIDENCE=<dir> LOAM_PLAYWRIGHT=<playwright index.mjs> \
//   LOAM_CHROME=/usr/bin/chromium-headless-shell node test/first-day-polish-browser.mjs
//
// FIRST_DAY_SCENES=<a,b> takes only those scenes.
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'

const out = process.env.FIRST_DAY_EVIDENCE
if (!out) throw new Error('Set FIRST_DAY_EVIDENCE')
await mkdir(out, { recursive: true })
const only = process.env.FIRST_DAY_SCENES?.split(',')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(JSON.stringify(body)) }
const stream = (first) => (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`: open ping=15000\n\n${first ? `data: ${JSON.stringify(first)}\n\n` : ''}`) }
const at = (minute) => `2026-11-16T17:${String(minute).padStart(2, '0')}:00Z`

const iris = { id: 'iris', name: 'Iris', face: null }
const juniper = { draft: 'd-5f7081e2', rev: 1, by: 'iris', thread: 't-3b1e9c20', id: 'juniper', name: 'Juniper', ready: true, problems: [],
  soul: '# Juniper\n\nCheerful and practical. Plans your week around what you like and what is already in the fridge.\n\nJuniper keeps the shopping list honest, suggests one new dish a week, and never makes you feel bad about takeaway on a Friday.\n\n## How Juniper talks\n\n- Warm, brief, a little playful\n- Asks before changing a plan you made',
  config: '## Scope\n\n- **Meals** and the shopping list, in `Projects/Meals`\n- Reads the calendar to plan around busy nights\n\n## Never\n\n- Orders anything or spends money' }
const member = { persona: 'iris', engine: 'claude-code', model: null, busy: false }
const thread = (id, title, extra = {}) => ({ id, title, default: 'iris', members: [member], created: at(30), last: { author: 'iris', text: '', at: at(50) }, unread: 0, pinned: false, ...extra })
const threads = {
  hello: { row: thread('t-6daf5a6f', 'Hello', { pinned: true }), messages: [
    { id: 'm1', author: 'iris', kind: 'message', engine: 'claude-code', at: at(34), text: 'Hi, I’m Iris. Welcome to Loam.\n\nI’m the first agent here, and the one to come to with anything about how this place works. Before anything else: what should I call you?' },
    { id: 'm2', author: 'you', kind: 'message', at: at(49), text: 'Hi Iris, I am Sam.' },
    { id: 'm3', author: 'iris', kind: 'message', engine: 'claude-code', model: '<synthetic>', at: at(49), text: 'Failed to authenticate: OAuth session expired and could not be refreshed' }] },
  meal: { row: thread('t-3b1e9c20', 'A meal planner'), messages: [
    { id: 'm1', author: 'you', kind: 'message', at: at(48), text: 'I’d like an agent who plans our meals for the week.' },
    { id: 'm2', author: 'iris', kind: 'message', engine: 'claude-code', at: at(50), text: 'Lovely, a meal planner. Here is a first sketch: Juniper, cheerful and practical, plans your week around what you like and what is already in the fridge. The whole thing is below: tell me what to change, or say yes and I will make Juniper.' },
    { id: 'm3', author: 'loam', kind: 'system', at: at(50), text: 'Iris drafted a new agent: Juniper (draft d-5f7081e2, revision 1)' }] },
  made: { row: thread('t-9e04d7b1', 'New agent'), messages: [
    { id: 'm1', author: 'loam', kind: 'system', at: at(40), text: "New agent. Tell Iris who you'd like: Iris drafts their name, face, soul and config, shows you, changes it on your word, and makes them only when you say yes. (Iris: `settings describe agent.draft` says how; this thread is `t-9e04d7b1`.)" },
    { id: 'm2', author: 'you', kind: 'message', at: at(41), text: 'Someone to keep our garden on track.' },
    { id: 'm3', author: 'iris', kind: 'message', engine: 'claude-code', at: at(43), text: 'Here’s Fern: patient, seasonal, good with a watering rota. Have a look, and say yes when it feels right.' },
    { id: 'm4', author: 'loam', kind: 'system', at: at(43), text: 'Iris drafted a new agent: Fern (draft d-a41c09e3, revision 1)' },
    { id: 'm5', author: 'loam', kind: 'system', at: at(45), text: 'Iris revised draft d-a41c09e3: Fern, revision 2 (soul)' },
    { id: 'm6', author: 'you', kind: 'message', at: at(46), text: 'Yes, make Fern.' },
    { id: 'm7', author: 'loam', kind: 'system', at: at(47), text: 'Made Fern (fern): their first thread is t-c4a782f2' }] },
}
const fernFirst = thread('t-c4a782f2', 'New thread', { default: 'fern', members: [{ ...member, persona: 'fern' }] })
const shortcuts = [
  { label: 'Vault system', resource: { root: 'vault', path: 'System', name: 'System', kind: 'folder' } },
  { label: 'Loam project', resource: { root: 'vault', path: 'Projects/Loam', name: 'Loam', kind: 'folder' } },
  { label: 'Loam infrastructure', resource: { root: 'vault', path: 'Projects/Loam/infrastructure', name: 'infrastructure', kind: 'folder' } },
]
const folder = (p, kind = 'folder') => ({ root: 'vault', path: p, name: p.split('/').at(-1), kind, modified: at(30), ...(kind === 'file' ? { size: 1200 } : {}) })
const freshVault = { System: [folder('System/Loam'), folder('System/skills')], 'Projects/Loam': [folder('Projects/Loam/infrastructure')], 'Projects/Loam/infrastructure': [folder('Projects/Loam/infrastructure/schedule.md', 'file')],
  '': [folder('Attachments'), folder('Conversations'), folder('Inbox'), folder('Projects'), folder('System'), folder('About me.md', 'file'), folder('README.md', 'file')] }
const usedVault = { ...freshVault, System: [...freshVault.System, folder('System/conventions'), folder('System/Dictation terms.md', 'file')], 'Projects/Loam': [folder('Projects/Loam/architecture'), folder('Projects/Loam/decisions'), folder('Projects/Loam/infrastructure')],
  'Projects/Loam/infrastructure': [folder('Projects/Loam/infrastructure/agent-runtime.md', 'file'), folder('Projects/Loam/infrastructure/schedule.md', 'file')] }
const updateRun = { id: 'u-1', action: 'apply', outcome: 'applied', from_version: '0.1.0', to_version: '0.1.1', steps: [
  { name: 'preflight', words: 'Checking the install', status: 'done' },
  { name: 'backup', words: 'Keeping a copy of System/Loam', status: 'done', detail: '/var/lib/loam/update/backups/20261117-022529-78b8e2/Loam' },
  { name: 'move', words: 'Moving Loam to the new release', status: 'done' },
  { name: 'pins', words: 'Checking the agents’ memory pins', status: 'done', detail: '12 memory pins checked, 0 changed' },
  { name: 'restart', words: 'Restarting Loam', status: 'done' }] }

async function day(vault) {
  const app = await oneProductFixture({ GEMINI_API_KEY: '' }), r = app.routes
  r['GET /v1/agents'] = (req, res) => json(res, { agents: [iris] })
  const rows = [threads.hello.row, threads.meal.row, threads.made.row, fernFirst]
  r['GET /v1/threads'] = (req, res) => json(res, { threads: rows, agents: [iris] })
  r['GET /v1/threads/events'] = stream({ type: 'threads.snapshot', threads: rows, agents: [iris] })
  for (const { row, messages } of [...Object.values(threads), { row: fernFirst, messages: [{ id: 'm1', author: 'loam', kind: 'system', at: at(47), text: "Fern was made by Iris on Sam's yes (m6 in t-9e04d7b1). This is their first thread." }] }]) {
    r[`GET /v1/threads/${row.id}`] = (req, res) => json(res, { thread: row, messages, has_more: false, turn: null, turns: [], board: { markdown: '', path: '' } })
    r[`GET /v1/threads/${row.id}/events`] = stream({ type: 'thread.snapshot', turns: [], members: row.members, pending: [] })
    r[`PATCH /v1/threads/${row.id}`] = (req, res) => json(res, row)
  }
  r['GET /v1/agents/drafts'] = (req, res, url) => json(res, { drafts: url.searchParams.get('thread') === threads.meal.row.id ? [juniper] : [] })
  r['POST /v1/threads'] = (req, res, url, raw) => {
    const body = JSON.parse(raw || '{}'), row = thread('t-0a9f3c57', 'New thread', { default: body.persona ?? null, members: body.persona ? [{ ...member, persona: body.persona }] : [] })
    r[`GET /v1/threads/${row.id}`] = (q, s) => json(s, { thread: row, messages: [], has_more: false, turn: null, turns: [], board: { markdown: '', path: '' } })
    r[`GET /v1/threads/${row.id}/events`] = stream({ type: 'thread.snapshot', turns: [], members: row.members, pending: [] })
    json(res, row, 201)
  }
  r['GET /v1/apps'] = (req, res) => json(res, { apps: [], sources: [] })
  const plugins = await shippedPlugins(path.resolve('public'))
  for (const p of plugins) if (p.id === 'dictation') p.on = true
  r['GET /v1/plugins'] = (req, res) => json(res, { plugins })
  r['GET /v1/plugins/dictation'] = (req, res) => json(res, { plugin: plugins.find((p) => p.id === 'dictation'), rows: plugins })
  r['GET /v1/plugins/dictation/secrets'] = (req, res) => json(res, { secrets: [{ name: 'GEMINI_API_KEY', label: 'Gemini API key', set: false }] })
  for (const file of await readdir(path.resolve('../plugins/_listed/dictation/page'))) {
    r[`GET /v1/plugins/dictation/files/page/${file}`] = async (req, res) => {
      res.writeHead(200, { 'content-type': file.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/javascript; charset=utf-8' })
      res.end(await readFile(path.resolve('../plugins/_listed/dictation/page', file)))
    }
  }
  r['GET /v1/update'] = (req, res) => json(res, { current: { version: '0.1.1', kind: 'stock' }, check: { available: false, latest: { version: '0.1.1' } }, run: updateRun })
  r['POST /v1/update/check'] = r['GET /v1/update']
  r['GET /v1/update/progress'] = (req, res) => json(res, updateRun)
  r['GET /v1/engineers'] = (req, res) => json(res, { engineers: [{ project: 'loam', platform: true, effective: { provider: 'claude-code' } }], default: { effective: { provider: 'claude-code' } } })
  r['GET /v1/system/reviewer'] = (req, res) => json(res, { name: 'Reviewer', plain: 'Reads the day’s work every night at 4:20' })
  r['GET /v1/files/roots'] = (req, res) => json(res, { roots: [{ root: 'vault', name: 'Vault', capabilities: { read: true, write: true } }, { root: 'spaces', name: 'AI Spaces', capabilities: { read: true } }], shortcuts })
  r['GET /v1/files/list'] = (req, res, url) => { const p = url.searchParams.get('path') ?? ''; return p in vault ? json(res, { root: 'vault', path: p, entries: vault[p] }) : json(res, { error: 'not found' }, 404) }
  r['GET /v1/files/stat'] = (req, res, url) => json(res, folder(url.searchParams.get('path') ?? ''))
  r['GET /v1/dictation-terms'] = (req, res) => json(res, { error: 'no note' }, 404)
  return app
}

const scenes = {
  // 1: the agent review where Iris says it is.
  async 'agent-review'({ page, go, shot }) {
    await go.thread(threads.meal.row.id)
    const opener = page.getByRole('button', { name: 'Review agent', exact: true })
    if (await opener.count()) await opener.click()
    await page.locator('.agent-drafts').first().waitFor()
    await page.locator('.agent-drafts').first().scrollIntoViewIfNeeded()
    await shot()
  },
  async 'agent-review-open'({ page, go, shot }) {
    await go.thread(threads.meal.row.id)
    const opener = page.getByRole('button', { name: 'Review agent', exact: true })
    if (await opener.count()) await opener.click()
    await page.locator('.agent-drafts').first().waitFor()
    const more = page.getByRole('button', { name: 'Read the whole thing', exact: true })
    if (await more.count()) await more.click()
    else for (const summary of await page.locator('.agent-drafts summary').all()) await summary.click()
    await page.locator('.agent-drafts h3').first().scrollIntoViewIfNeeded()
    await page.evaluate(() => { const card = document.querySelector('.agent-drafts'); card?.scrollIntoView({ block: 'start' }) })
    await shot()
  },
  // 2: the ids in Loam's own lines.
  async 'thread-lines'({ go, shot }) { await go.thread(threads.made.row.id); await shot() },
  // 3: a failed turn.
  async 'failed-turn'({ go, shot }) { await go.thread(threads.hello.row.id); await shot() },
  // 4: dictation without a key, and the terms card.
  async 'dictation-no-key'({ page, go, shot }) {
    await go.thread(threads.hello.row.id)
    await page.locator('.roundmic').first().waitFor()
    await page.locator('.roundmic').first().click()
    const keep = page.locator('.takebtn.keep, [aria-label="Keep"], .take-keep, [aria-label="Keep the take"], [aria-label="Done"]').first()
    await keep.waitFor()
    await keep.click()
    await page.locator('.takenote').first().waitFor({ timeout: 8000 })
    await shot()
  },
  async 'dictation-terms'({ page, go, shot }) {
    await go.settings('plugins')
    const row = page.getByRole('button', { name: 'About Dictation' })
    await row.waitFor()
    await row.click()
    await page.getByText('Names and terms').first().waitFor()
    await page.getByText('Names and terms').first().scrollIntoViewIfNeeded()
    await page.waitForTimeout(400)
    await shot()
  },
  // 5: the update log.
  async 'update-done'({ page, go, shot }) { await go.settings('update'); await page.locator('.update-progress').waitFor(); await page.locator('.update-progress').scrollIntoViewIfNeeded(); await shot() },
  // 6: sign out and passkeys: as the host is today, and with the passkey list relayed.
  async 'signin'({ page, go, shot }) { await go.settings('signin'); await page.locator('.passkey-card').first().waitFor(); await page.waitForTimeout(300); await shot() },
  async 'signin-passkeys'({ page, go, shot }) {
    await page.route('**/_loam/passkeys', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ passkeys: [{ id: 1, description: 'iPhone', created_at: '2026-11-16T18:02:00Z', last_used_at: '2026-11-17T08:10:00Z' }, { id: 2, description: 'Mac', created_at: '2026-11-17T09:00:00Z', last_used_at: '0001-01-01T00:00:00Z' }] }) }))
    await go.settings('signin'); await page.locator('.passkey-card').first().waitFor(); await page.waitForTimeout(400); await shot()
  },
  // 7: Settings › Agents.
  async 'settings-agents'({ page, go, shot }) { await go.settings('agents'); await page.getByText('Every other project').waitFor(); await page.waitForTimeout(300); await shot() },
  // 8: the claim page before typing.
  async 'claim'({ page, app, shot }) { await page.goto(`${app.url}/claim.html#token=example-claim-token`); await page.locator('#password').waitFor(); await page.locator('#password').scrollIntoViewIfNeeded(); await shot() },
  // 9: Files home: a fresh vault, and one that uses the developer folders.
  async 'files-home'({ page, go, shot }) { await go.files(); await page.locator('.fs-shortcuts').waitFor(); await page.waitForTimeout(500); await shot() },
  async 'files-home-used'({ page, go, shot }) { await go.files(); await page.locator('.fs-shortcuts').waitFor(); await page.waitForTimeout(500); await shot() },
  // 10: Build an app.
  async 'build-app'({ page, go, shot }) {
    await go.apps(); await page.getByRole('button', { name: 'Build an app', exact: true }).click()
    await page.locator('.tt-thread').waitFor(); await page.waitForTimeout(600); await shot()
  },
}

const results = []
try {
  for (const [name, scene] of Object.entries(scenes)) {
    if (only && !only.includes(name)) continue
    for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
      const app = await day(name === 'files-home-used' ? usedVault : freshVault), errors = []
      const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 900 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
      await context.addInitScript(() => {
        // A microphone and a recorder that answer as a browser's do, with a few
        // made-up bytes for audio.
        navigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [{ stop() {} }], getAudioTracks: () => [{ stop() {} }] })
        navigator.mediaDevices.enumerateDevices = async () => []
        globalThis.MediaRecorder = class { static isTypeSupported() { return true } constructor() { this.l = {}; this.state = 'inactive'; this.mimeType = 'audio/webm' }
          addEventListener(t, f) { (this.l[t] ??= []).push(f) } start() { this.state = 'recording' }
          stop() { this.state = 'inactive'; for (const f of this.l.dataavailable ?? []) f({ data: new Blob(['take'], { type: 'audio/webm' }) }); for (const f of this.l.stop ?? []) f() } }
      })
      await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__polish = { threads, state, theme, goSettings, goFiles, goApps, schedule };' }))
      const page = await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', (error) => errors.push(error.message))
      const settle = async () => { await page.waitForTimeout(350); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }) }
      const shot = async () => { await settle(); await page.screenshot({ path: path.join(out, `${name}-${width}-${theme}.png`), animations: 'disabled' }) }
      const go = {
        async thread(id) { await page.evaluate((id) => __polish.threads.open(id), id); await page.locator(`.tt-thread`).waitFor(); await page.waitForFunction(() => !__polish.threads.st.open?.loading && !__polish.threads.st.open?.refreshing); await settle() },
        async settings(where) { await page.evaluate((where) => __polish.goSettings(where), where); await page.locator('.sheet.settings').waitFor(); await settle() },
        async files() { await page.evaluate(() => __polish.goFiles()); await settle() },
        async apps() { await page.evaluate(() => __polish.goApps()); await page.locator('.apps-page').waitFor(); await settle() },
      }
      try {
        if (name !== 'claim') {
          await page.goto(app.url); await page.waitForFunction(() => globalThis.__polish?.state.agentsReady)
          await page.evaluate((mode) => __polish.theme.set({ mode, theme: 'latte' }), theme)
          await settle()
        }
        await scene({ page, app, go, shot })
        results.push({ name, width, theme, errors })
        console.log(`shot ${name} ${width} ${theme}${errors.length ? ` errors: ${errors.join(' | ')}` : ''}`)
      } catch (error) {
        results.push({ name, width, theme, failed: String(error.message ?? error).split('\n')[0], errors })
        console.log(`FAILED ${name} ${width} ${theme}: ${String(error.message ?? error).split('\n')[0]}`)
        await page.screenshot({ path: path.join(out, `${name}-${width}-${theme}-failed.png`) }).catch(() => {})
      } finally { await context.close(); await app.close() }
    }
  }
} finally { await browser.close() }
await writeFile(path.join(out, 'results.json'), JSON.stringify(results, null, 2))
process.exit(0)
