// LOAM_PLAYWRIGHT and LOAM_CHROME select the browser. LOAM_LIVE_APP (optional)
// serves the deployed UI and supplies Images metadata. Switch writes stay in the fixture.
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { oneProductFixture } from './one-product-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const origin = process.env.LOAM_LIVE_APP
const evidence = process.env.IMAGES_EVIDENCE
const root = fileURLToPath(new URL('../../', import.meta.url))
const source = await (origin ? fetch(origin + '/app.js').then(r => r.text()) : readFile(new URL('../public/app.js', import.meta.url), 'utf8'))
const live = origin ? await fetch(origin + '/api/plugins/images').then(r => r.json()) : null
if (live) assert.ok(live.image_routing.providers.length, live.image_routing)
const initial = live?.plugin ?? JSON.parse(execFileSync('python3', ['-c', `
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + '/lib')
import loam_plugins
found = loam_plugins.read_folder(Path(sys.argv[1]) / 'plugins/images', 'images')
print(json.dumps({**found['manifest'], 'has': found['has'], 'guide': found['guide']}))
`, root]))
const routing = live?.image_routing ?? { providers: ['Codex', 'Antigravity'], backend: 'openai' }
if (evidence) await mkdir(evidence, { recursive: true })

const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
async function check(width, mode) {
  const app = await oneProductFixture()
  const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 960 },
    colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
  const row = { ...initial, layer: 'shipped', folder: 'images', source: { kind: 'loam' }, effective: true,
    on: false, switch: 'plugin', broken: null, warnings: [] }
  let imageRouting = routing
  const settings = { theme: 'latte', mode, size: 100, options: {} }, writes = []
  app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: settings, desktop: settings })
  app.routes['GET /v1/plugins'] = (req, res) => json(res, { plugins: [row] })
  app.routes['GET /v1/plugins/images'] = (req, res) => json(res, { plugin: row, rows: [row], image_routing: imageRouting })
  app.routes['GET /v1/plugins/images/settings'] = (req, res) => json(res, { entries: [], values: {} })
  app.routes['PATCH /v1/plugins/images'] = (req, res, url, raw) => {
    const { on } = JSON.parse(raw)
    writes.push(on); row.on = on
    json(res, { plugin: row })
  }
  try {
    if (origin) {
      // Run the deployed page with a synthetic account, roster, and plugin switch.
      // The browser never writes to the live app. Theme assets come from the deployed UI.
      await context.route('**/api/**', async route => {
        const url = new URL(route.request().url())
        if (route.request().method() === 'GET' && url.pathname.startsWith('/api/plugins/images/files/')) return route.continue()
        if (url.pathname.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: ': fixture\n\n' })
        const response = await route.fetch({ url: app.url + url.pathname + url.search })
        return route.fulfill({ response })
      })
    } else {
      await context.route('**/api/plugins/images/files/**', async route => {
        const filename = new URL(route.request().url()).pathname.split('/files/')[1]
        await route.fulfill({ path: path.join(root, 'plugins/images', filename) })
      })
    }
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__openSettings = goSettings;' }))
    const page = await context.newPage(), errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(origin ?? app.url)
    await page.waitForFunction(() => globalThis.__openSettings)
    await page.evaluate(() => __openSettings('plugins'))
    const control = page.getByRole('switch', { name: 'Enable Images' })
    const question = page.locator('.plugin-enable-question')
    const keep = question.getByRole('button', { name: 'Keep off' })
    const enable = question.getByRole('button', { name: 'Turn on Images' })
    const shot = async screen => {
      await page.evaluate(() => document.fonts.ready)
      await question.scrollIntoViewIfNeeded()
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      if (evidence) await page.screenshot({ path: path.join(evidence, `${screen}-${width}-${mode}.png`), animations: 'disabled' })
    }
    const pending = async () => {
      await control.click()
      await question.waitFor()
      for (const provider of routing.providers) assert.match(await question.innerText(), new RegExp(provider))
      assert.match(await question.innerText(), /API key instead of/)
      assert.equal(await control.getAttribute('aria-checked'), 'false')
      assert.deepEqual(writes, [])
    }
    await pending()
    await shot('images-list')
    await keep.click()
    await question.waitFor({ state: 'hidden' })
    assert.deepEqual(writes, [])
    await page.getByRole('button', { name: 'About Images', exact: true }).click()
    await pending()
    await shot('images-detail')
    await keep.click()
    await question.waitFor({ state: 'hidden' })
    await pending()
    await enable.click()
    await page.waitForFunction(() => document.querySelector('[aria-label="Enable Images"]')?.getAttribute('aria-checked') === 'true')
    await question.waitFor({ state: 'hidden' })
    await control.click()
    await page.waitForFunction(() => document.querySelector('[aria-label="Enable Images"]')?.getAttribute('aria-checked') === 'false')
    imageRouting = { providers: [], backend: 'google' }
    await control.click()
    await page.waitForFunction(() => document.querySelector('[aria-label="Enable Images"]')?.getAttribute('aria-checked') === 'true')
    assert.equal(await question.count(), 0)
    assert.deepEqual(writes, [true, false, true])
    assert.deepEqual(errors, [])
    console.log(`PASS ${width} ${mode}: list and detail consent, Keep off runs no write, confirm enables, no native provider enables directly`)
  } finally { await context.close(); await app.close() }
}

try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) await check(width, mode)
  if (origin) {
    const after = await fetch(origin + '/api/plugins/images').then(res => res.json())
    assert.equal(after.plugin.on, live.plugin.on)
    console.log(`PASS live Images state still ${after.plugin.on ? 'on' : 'off'}`)
  }
} finally { await browser.close() }
