// A fresh install whose AI provider was signed in before the first visit
// (2026-11-19) skips the provider step, not the backup offer: the offer is
// shown once before Hello, skipped in one tap, and never shown again; an
// install with Git backup on (or its switch ever set) sees nothing.
//
//   LOAM_PLAYWRIGHT=<playwright> LOAM_CHROME=<chromium> node test/onboarding-backup-entry-browser.mjs
//
// Every wait has a timeout, and the whole walk a hard stop, so it cannot hang.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { oneProductFixture } from './one-product-fixture.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.BACKUP_EVIDENCE || `${process.env.TMPDIR}/backup-entry`
await mkdir(out, { recursive: true })
const hardStop = setTimeout(() => { console.error('the walk took over 4 minutes: stopped'); process.exit(2) }, 240000)
const WAIT = { timeout: 15000 }

// A blank install as the browser meets it: Iris, the untouched Hello thread,
// and Claude Code already signed in (the shared release fixtures above
// oneProductFixture still expect read-aloud in core, which left in e45f685).
async function freshInstall() {
  const app = await oneProductFixture()
  const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(JSON.stringify(body)) }
  const providers = [
    { id: 'claude-code', name: 'Claude Code', connected: true, models: [{ id: 'fable', name: 'Fable' }] },
    { id: 'codex', name: 'Codex', connected: false, models: [] },
    { id: 'antigravity', name: 'Antigravity', connected: false, models: [] },
  ]
  app.routes['GET /v1/providers'] = (req, res) => json(res, { providers })
  const base = await app.f.api('GET', '/threads/a-quieter-week'), id = base.thread.id
  const iris = { id: 'iris', name: 'Iris', emoji: '🌸' }
  const hello = { ...base.thread, hello: true, default: 'iris', title: 'Hello, you', pinned: true, members: [{ persona: 'iris', engine: 'claude-code', model: 'fable', busy: false }], last: { author: 'iris', text: 'Hello! I’m Iris.', at: new Date().toISOString() } }
  const record = { ...base, thread: hello, has_more: false, messages: [{ id: 'hello-preset', author: 'iris', kind: 'message', preset: true, at: new Date().toISOString(), text: 'Hello! I’m Iris. I’m here to help you find your feet.\n\nWhat would you like me to call you?' }] }
  app.routes['GET /v1/agents'] = (req, res) => json(res, { agents: [iris] })
  app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: [hello], agents: [iris] })
  app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'threads.snapshot', threads: [hello], agents: [iris] })}\n\n`) }
  app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, record)
  app.routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'thread.snapshot', thread: id, turn: null })}\n\n`) }
  return app
}

function gitBackupRow({ on, chosen }) {
  return { id: 'git-backup', key: 'shipped:git-backup:', name: 'Git backup', version: '1.0.0', author: 'Loam contributors', api: 0, kind: ['extension'],
    layer: 'shipped', folder: 'git-backup', source: { kind: 'loam' }, effective: true, broken: null, warnings: [], switch: 'plugin', on, chosen,
    places: ['settings.section'], has: { page_main: false }, overrides: [],
    touches: { network: [{ host: 'the git host you connect', why: 'Sends your history.' }], record: 'write', memory: 'write', server: true } }
}

const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8'), reports = []
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) for (const box of ['fresh', 'backup-on']) {
    const app = await freshInstall(), errors = [], external = [], patches = [], missing = new Set()
    const backup = gitBackupRow(box === 'fresh' ? { on: false, chosen: false } : { on: true, chosen: true })
    const rows = [...await shippedPlugins('public'), backup]
    const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    app.routes['GET /v1/plugins'] = (req, res) => json(res, { plugins: rows })
    app.routes['GET /v1/plugins/git-backup'] = (req, res) => json(res, { plugin: backup })
    app.routes['PATCH /v1/plugins/git-backup'] = (req, res, url, raw) => { const body = JSON.parse(raw); patches.push(body); backup.on = body.on; backup.chosen = true; json(res, { plugin: backup }) }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 960 }, colorScheme: mode, serviceWorkers: 'block', reducedMotion: 'reduce' })
    await context.route('**/*', async route => {
      const url = new URL(route.request().url())
      if (url.origin !== app.url) { external.push(url.href); return route.abort() }
      if (url.pathname === '/app.js') return route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__u4={state,threads,goSettings,goProfile};' })
      return route.continue()
    })
    const page = await context.newPage()
    page.setDefaultTimeout(15000)
    page.on('pageerror', e => errors.push(e.message))
    page.on('console', e => { if (e.type() === 'error' && !e.text().startsWith('Failed to load resource')) errors.push(e.text()) })
    page.on('response', r => { if (r.status() >= 400) missing.add(`${r.status()} ${new URL(r.url()).pathname}`) })
    const shot = async name => { await page.screenshot({ path: `${out}/${box}-${name}-${width}-${mode}.png` }); console.log(`${width} ${mode} ${box}: ${name}`) }
    const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    const backupWords = async () => (await page.locator('main, nav, aside, header').allInnerTexts()).join(' ').match(/back ?up/i)?.[0] ?? null
    try {
      await page.goto(app.url)
      if (box === 'fresh') {
        // First visit: straight to the offer (the provider is already signed in).
        await page.locator('.welcome-backup').waitFor(WAIT)
        assert.equal(await page.getByRole('heading', { name: 'Back up your Loam' }).count(), 1)
        assert.equal(await page.getByText('Welcome to Loam').count(), 0, 'no provider step: it is already signed in')
        assert.equal(await overflow(), 0)
        await shot('1-offer')
        // One tap.
        await page.getByRole('button', { name: 'Skip for now' }).click()
        await page.locator('[data-message-id="hello-preset"]').waitFor(WAIT)
        await shot('2-after-skip')
        assert.equal(await page.locator('.welcome').count(), 0)
        assert.equal(await backupWords(), null, 'nothing about backup outside Settings')
        for (let i = 0; i < 50 && !patches.length; i++) await page.waitForTimeout(100)
        assert.deepEqual(patches, [{ on: false }], 'the skip is the answer: off, chosen')
        // Second visit: nothing.
        await page.goto(app.url)
        await page.waitForFunction(() => globalThis.__u4?.state.agentsReady, null, WAIT)
        await page.locator('[data-message-id="hello-preset"]').waitFor(WAIT)
        assert.equal(await page.locator('.welcome').count(), 0)
        assert.equal(await backupWords(), null)
        await shot('3-second-visit')
      } else {
        // This box: Git backup on. Hello, as before, and nothing about backup.
        await page.waitForFunction(() => globalThis.__u4?.state.agentsReady, null, WAIT)
        await page.locator('[data-message-id="hello-preset"]').waitFor(WAIT)
        assert.equal(await page.locator('.welcome').count(), 0)
        assert.equal(await backupWords(), null)
        assert.deepEqual(patches, [])
        await shot('1-first-visit')
      }
      console.log('  fixture answers ≥400:', [...missing].join(', ') || 'none')
      assert.deepEqual(errors, []); assert.deepEqual(external, [])
      reports.push({ width, mode, box, patches, errors, external })
    } finally { await context.close(); await app.close() }
  }
  await writeFile(`${out}/results.json`, JSON.stringify(reports, null, 2))
  console.log(`${reports.length} walks passed; shots in ${out}`)
} finally { await browser.close(); clearTimeout(hardStop) }
process.exit(0)
