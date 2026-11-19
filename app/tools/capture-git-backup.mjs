// Captures of Settings › Plugins › Git backup (2026-11-18) at 390 and 1440,
// light and dark, against the fixture app. The plugin's page is the real
// one (plugins/git-backup/page/); what it reads at /x/git-backup/ is the
// plugin's real server, one per state, each run on a scratch install:
//
//   GB_SERVERS='{"off":"http://127.0.0.1:9401","setup":"…","ok":"…","fail":"…"}'
//   GB_EVIDENCE=<folder> LOAM_PLAYWRIGHT=<playwright/index.mjs> LOAM_CHROME=<chromium> node tools/capture-git-backup.mjs
//
// States: nothing connected; the address form; the key step; both backed up;
// both failing (a key removed, a remote with other history) with a file too
// large to send. Fails on a page error or sideways overflow.
import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from '../test/one-product-fixture.mjs'

const out = process.env.GB_EVIDENCE
if (!out) throw new Error('Set GB_EVIDENCE')
const servers = JSON.parse(process.env.GB_SERVERS || '{}')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8')
const PLUGIN = new URL('../../plugins/git-backup/', import.meta.url)
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const manifest = await readFile(new URL('PLUGIN.md', PLUGIN), 'utf8')
const guide = manifest.split('\n---\n').slice(1).join('\n---\n').trim()
const entries = [
  { key: 'vault_remote', label: 'Vault’s repository', choices: null, default: '' },
  { key: 'spaces_remote', label: 'AI Spaces’ repository', choices: null, default: '' },
  { key: 'every', label: 'Back up', choices: ['15m', '1h', '6h', '1d'], default: '15m' }]
const row = { id: 'git-backup', layer: 'shipped', broken: null, warnings: [], name: 'Git backup', version: '1.0.0', author: 'Loam contributors', api: 0,
  kind: ['extension'], places: ['settings.section', 'agent.tool', 'agent.prompt', 'schedule.kind', 'settings.entry'], overrides: [], settings: null, icon: 'assets/icon.svg', format: 'PLUGIN.md',
  has: { page: true, page_main: true, server: true, assets: true, theme: false },
  source: { kind: 'shipped' },
  touches: { network: [{ host: 'the git host you connect (GitHub, GitLab, Codeberg, your own)', why: 'Sends your vault’s and AI Spaces’ history to the private repositories you connect' }], record: 'write', memory: 'write', server: true },
  contributes: { 'settings.entry': entries }, edited: false, effective: true, on: true, switch: 'plugin', files: '/v1/plugins/git-backup/files/',
  service: { state: 'running', wanted: true, unit: 'loam-plugin-git-backup.service', url: '/x/git-backup/' } }

const files = []
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) for (const [state, base] of Object.entries(servers)) {
    const app = await oneProductFixture(), errors = []
    const settings = { theme: 'latte', mode, size: 100, options: {} }
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: settings, desktop: settings })
    app.routes['GET /v1/plugins'] = (req, res) => json(res, { api: 0, layers: { shipped: [], yours: '' }, plugins: [row] })
    app.routes['GET /v1/plugins/git-backup'] = (req, res) => json(res, { plugin: { ...row, guide } })
    app.routes['GET /v1/plugins/git-backup/settings'] = (req, res) => json(res, { id: 'git-backup', values: {}, entries })
    for (const file of ['page/main.js', 'page/style.css', 'assets/icon.svg']) {
      const type = file.endsWith('.js') ? 'text/javascript; charset=utf-8' : file.endsWith('.css') ? 'text/css; charset=utf-8' : 'image/svg+xml'
      app.routes[`GET /v1/plugins/git-backup/files/${file}`] = async (req, res) => { res.writeHead(200, { 'content-type': type, 'cache-control': 'no-cache' }); res.end(await readFile(new URL(file, PLUGIN))) }
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode,
      isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce', timezoneId: 'America/Los_Angeles',
      extraHTTPHeaders: { 'Remote-User': 'sam', 'Remote-Name': 'Sam Rivera' } })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__cap={goSettings,openSheet,schedule,state:page.state};' }))
    // The gateway's place: /x/git-backup/* to this state's real server.
    await context.route('**/x/git-backup/**', async route => {
      const req = route.request(), rest = new URL(req.url()).pathname.replace(/^\/x\/git-backup\//, '')
      const res = await fetch(`${base}/${rest}`, { method: req.method(), headers: { 'content-type': 'application/json' }, body: req.method() === 'GET' ? undefined : req.postData() })
      await route.fulfill({ status: res.status, contentType: 'application/json', body: await res.text() })
    })
    const page = await context.newPage(); page.setDefaultTimeout(15000)
    page.on('pageerror', e => errors.push(e.message))
    const shot = async (surface, full = false) => {
      await page.mouse.move(0, 0)
      await page.evaluate(async () => { document.activeElement?.blur(); await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      await page.waitForTimeout(300)
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth || [...document.querySelectorAll('.setmain, .sheet.settings, .gb')].some(n => n.scrollWidth > n.clientWidth + 1))
      assert.equal(overflow, false, `${surface} ${width} ${mode}: sideways overflow`)
      const file = `${surface}-${width}-${mode}.png`
      await page.evaluate(end => { const n = end ? [...document.querySelectorAll('.gb > *')].at(-1) : document.querySelector('.gb'); n?.scrollIntoView({ block: end ? 'end' : 'start' }) }, full)
      await page.screenshot({ path: path.join(out, file), animations: 'disabled' })
      files.push(file)
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate(() => __cap.goSettings('plugins', 'git-backup'))
      await page.locator('.gb-hero').waitFor()
      await page.locator('.gb-card').first().waitFor()
      assert.equal(await page.locator('.plugin-entry-settings').count(), 0, 'the plain form is not drawn as well')
      await shot(`gb-${state}`)
      await shot(`gb-${state}-end`, true)
      if (state === 'off') {
        await page.locator('.gb-card').first().locator('button', { hasText: 'Connect a repository' }).click()
        await page.locator('#gb-remote-vault').fill('https://github.com/sam/my-vault')
        await shot('gb-connect-form')
      }
      assert.deepEqual(errors, [])
      console.log(`PASS ${state} ${width} ${mode}`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
console.log(files.join('\n'))
