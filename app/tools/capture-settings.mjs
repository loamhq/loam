// Captures of Settings in seven (2026-11-18): the first page, Account,
// Connections (its top and its API keys), an installed plugin's page, About
// Loam, and a new agent's draft card, at 390 and 1440, light and dark,
// against the fixture app: no person's data. (tools/capture-accounts.mjs captures an agent's images in a thread.)
//
//   SETTINGS_EVIDENCE=<folder> LOAM_PLAYWRIGHT=<playwright/index.mjs> LOAM_CHROME=<chromium> node tools/capture-settings.mjs
//
// Asserts no page error, no sideways overflow on any surface, and that the
// word "Accounts" is on none of them.
import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from '../test/one-product-fixture.mjs'

const out = process.env.SETTINGS_EVIDENCE
if (!out) throw new Error('Set SETTINGS_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8')
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const KEYS = JSON.parse(await readFile(new URL('./fake-keys.json', import.meta.url), 'utf8'))
const ICON = await readFile(new URL('../../plugins/images/assets/icon.svg', import.meta.url))

const at = '2026-11-18T19:30:00Z', thread = 'a-quieter-week'
const commit = '79b1459e4264b4715f016b935ca8903367ff3c4d'
const images = { id: 'images', layer: 'yours', broken: null, warnings: [], name: 'Images', version: '1.1.1', author: 'Loam contributors', api: 0,
  kind: ['extension'], places: ['agent.tool', 'agent.prompt', 'settings.entry', 'vault.folder'], overrides: [], settings: null, icon: 'assets/icon.svg', format: 'PLUGIN.md',
  has: { page: false, page_main: false, server: false, assets: true, theme: false },
  source: { kind: 'link', repo: 'loamhq/plugins', path: 'images', ref: 'images-v1.1.1', ref_kind: 'tag', commit, url: `https://github.com/loamhq/plugins/tree/${commit}/images` },
  keys: KEYS.filter(k => ['openai', 'google'].includes(k.id)).map(k => ({ provider: k.id, name: k.name, why: `Makes and changes images with ${k.name}`, optional: true, fields: k.fields })),
  touches: { network: [{ host: 'api.openai.com', why: 'Sends the prompt to OpenAI' }], record: 'none', memory: 'none', server: false },
  contributes: { 'settings.entry': [] }, edited: false, effective: true, on: true, switch: 'plugin', files: '/v1/plugins/images/files/' }
const imageSettings = { id: 'images', values: { folder: 'Images', backend: 'auto' }, entries: [
  { key: 'folder', label: 'Save new images in', choices: null, default: 'Images' },
  { key: 'backend', label: 'Made with', choices: ['auto', 'openai', 'google'], labels: { auto: 'Whichever key you have', openai: 'OpenAI', google: 'Google' }, default: 'auto',
    note: 'With both keys saved under API keys in Connections, OpenAI makes them unless you choose Google.' }] }
const draft = { draft: 'd-5f7081e2', rev: 1, thread, name: 'Juniper', ready: true, problems: [],
  soul: '# Juniper\n\nYou are Juniper, Sam’s kitchen companion. You notice what is already in the fridge and plan around it.',
  config: '# How Juniper works\n\nPlans meals a week at a time.' }

const results = []
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = []
    const keys = JSON.parse(JSON.stringify(KEYS))
    app.routes['GET /v1/keys'] = (req, res) => json(res, { providers: keys })
    for (const k of keys) app.routes[`GET /v1/keys/${k.id}`] = (req, res) => json(res, k)
    const settings = { theme: 'latte', mode, size: 100, options: {} }
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: settings, desktop: settings })
    app.routes['GET /v1/plugins'] = (req, res) => json(res, { api: 0, layers: { shipped: [], yours: '' }, plugins: [images] })
    app.routes['GET /v1/plugins/images'] = (req, res) => json(res, { plugin: { ...images, contributes: { 'settings.entry': imageSettings.entries } } })
    app.routes['GET /v1/plugins/images/settings'] = (req, res) => json(res, imageSettings)
    app.routes['GET /v1/plugins/images/files/assets/icon.svg'] = (req, res) => { res.writeHead(200, { 'content-type': 'image/svg+xml' }); res.end(ICON) }
    app.routes['GET /v1/update'] = (req, res) => json(res, { current: { version: '0.2.6', commit, branch: 'main', kind: 'stock', local_commits: 0 },
      check: { checked_at: at, latest: { version: '0.2.6', tag: '0.2.6', commit }, available: false, blocked: null, error: null, library: null, changes: null }, run: null, running: false, rollback: null })
    app.routes['POST /v1/update/check'] = app.routes['GET /v1/update']
    app.routes['GET /v1/agents/drafts'] = (req, res) => json(res, { drafts: [draft] })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode,
      isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce', timezoneId: 'UTC',
      // What the login's gate sets on a request it lets through.
      extraHTTPHeaders: { 'Remote-User': 'sam', 'Remote-Name': 'Sam Rivera' } })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__cap={threads,goSettings,openSheet,schedule,state:page.state};' }))
    const page = await context.newPage(); page.setDefaultTimeout(12000)
    await page.clock.setFixedTime(new Date(at))
    page.on('pageerror', e => errors.push(e.message))
    const shot = async (surface, scroller = null) => {
      await page.mouse.move(0, 0)
      await page.evaluate(async () => { document.activeElement?.blur(); await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      await page.waitForTimeout(250)
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth || [...document.querySelectorAll('.setmain, .sheet.settings')].some(n => n.scrollWidth > n.clientWidth + 1))
      assert.equal(overflow, false, `${surface} ${width} ${mode}: sideways overflow`)
      const words = await page.evaluate(() => document.querySelector('.sheet')?.innerText ?? document.body.innerText)
      assert.doesNotMatch(words, /\bAccounts\b/, `${surface} ${width} ${mode}: says Accounts`)
      const file = `${surface}-${width}-${mode}.png`
      await page.screenshot({ path: path.join(out, file), animations: 'disabled' })
      return file
    }
    const open = async (page_, provider = null) => page.evaluate(([p, v]) => __cap.goSettings(p, v), [page_, provider])
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      const files = []
      if (width < 1100) {
        await page.evaluate(() => __cap.openSheet({ kind: 'settings', page: 'root' }))
        await page.locator('.sheet.settings .setrow').first().waitFor()
        const rows = await page.locator('.sheet.settings .setrow-title').allTextContents()
        assert.deepEqual(rows.slice(0, 8), ['Activity', 'Account', 'Appearance', 'Agents', 'Connections', 'Plugins', 'Files', 'About Loam'])
        files.push(await shot('a-settings'))
      } else {
        await page.evaluate(() => __cap.openSheet({ kind: 'settings', page: 'root' }))
        await page.locator('.navrow').first().waitFor()
        assert.deepEqual((await page.locator('.navrow').allTextContents()).slice(0, 7), ['Account', 'Appearance', 'Agents', 'Connections', 'Plugins', 'Files', 'About Loam'])
      }
      await open('account'); await page.locator('.account-card').waitFor()
      await page.waitForFunction(() => document.querySelector('.account-username')?.textContent.includes('sam'))
      files.push(await shot('b-account'))
      await open('connections'); await page.locator('.keys-list .setrow').first().waitFor()
      files.push(await shot('c-connections'))
      await page.evaluate(() => document.getElementById('connections-api-keys').scrollIntoView({ block: 'start' }))
      files.push(await shot('c-connections-api-keys'))
      // An old id still opens: the API keys page of one provider.
      await open('accounts', 'openai'); await page.locator('.keys-card').waitFor()
      assert.equal(await page.locator('.backbtn').first().textContent(), 'Connections')
      files.push(await shot('d-api-key-openai'))
      await open('plugins', 'images'); await page.locator('.plugin-byline').waitFor()
      await page.locator('.plugin-entry-note').first().waitFor()
      const byline = await page.locator('.plugin-byline').textContent()
      assert.equal(byline, 'By Loam contributors · Version 1.1.1View source')
      assert.equal(await page.locator('text=' + commit).count(), 0, 'no commit on the page')
      files.push(await shot('e-plugin-images'))
      await open('about'); await page.locator('.about-facts dt').first().waitFor()
      files.push(await shot('f-about'))
      await page.evaluate(() => { __cap.state.sheet = null; __cap.schedule() })
      await page.evaluate(async id => { await __cap.threads.open(id); __cap.threads.agentDrafts.thread = id; await __cap.threads.loadAgentDrafts() }, thread)
      await page.locator('.agent-draft-card').waitFor()
      assert.equal(await page.locator('.agent-draft-summary').textContent(), 'Sam’s kitchen companion.')
      // The thread settles at its end after opening; scroll once it has.
      await page.waitForTimeout(600)
      await page.evaluate(() => document.querySelector('.agent-draft-card').scrollIntoView({ block: 'center' }))
      files.push(await shot('g-agent-draft'))
      assert.deepEqual(errors, [])
      results.push({ width, mode, files })
      console.log(`PASS ${width} ${mode}: ${files.join(', ')}`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
