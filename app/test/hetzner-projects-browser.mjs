// Run from app/ with Playwright installed. HETZNER_EVIDENCE saves four screenshots.
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { oneProductFixture } from './one-product-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const evidence = process.env.HETZNER_EVIDENCE
if (evidence) await mkdir(evidence, { recursive: true })
const root = fileURLToPath(new URL('../../', import.meta.url))
const plugin = JSON.parse(execFileSync('python3', ['-c', `
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "lib"))
import loam_plugins
found = loam_plugins.read_folder(root / "plugins" / "_listed" / "hetzner-cloud")
manifest = found["manifest"]
print(json.dumps({**manifest, "guide": found["guide"], "has": found["has"],
                  "declaration": loam_plugins.declaration(manifest, found["has"])}))
`, root]))

function json(res, body, status = 200) {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}

async function check(width, mode) {
  const app = await oneProductFixture()
  const context = await browser.newContext({
    viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode,
    isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce',
  })
  const checked = { ok: true, why: 'Hetzner accepted the token.' }
  const provider = {
    id: 'hetzner', name: 'Hetzner Cloud', checks: true, set: true, source: 'saved', checked, shown: {},
    fields: [{ name: 'HCLOUD_TOKEN', label: 'API token', secret: true }],
    projects: [{ id: 'studio', checked }],
    plugins: [{ id: 'hetzner-cloud', name: 'Hetzner Cloud', on: true, uses: 'shared', has_key: true, shared_only: true }],
  }
  const calls = []
  const settings = { theme: 'latte', mode, size: 100, options: {} }
  app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: settings, desktop: settings })
  app.routes['GET /v1/keys'] = (req, res) => json(res, { providers: [provider] })
  const row = { ...plugin, layer: 'yours', folder: 'plugins/hetzner-cloud', source: { kind: 'built' },
    effective: true, on: false, switch: 'plugin', broken: null, warnings: [] }
  app.routes['GET /v1/plugins'] = (req, res) => json(res, { plugins: [row] })
  app.routes['GET /v1/plugins/hetzner-cloud'] = (req, res) => json(res, { plugin: row, rows: [row] })
  app.routes['PUT /v1/keys/hetzner/projects/example'] = (req, res, url, raw) => {
    const { value } = JSON.parse(raw)
    calls.push(value)
    if (value === 'fixture-refused') return json(res, { error: 'Hetzner refused this token.' }, 422)
    provider.projects = [{ id: 'example', checked }, { id: 'studio', checked }]
    json(res, provider)
  }
  app.routes['DELETE /v1/keys/hetzner/projects/example'] = (req, res) => {
    provider.projects = [{ id: 'studio', checked }]
    json(res, provider)
  }
  try {
    await context.route('**/app.js', route => route.fulfill({
      contentType: 'text/javascript', body: source + '\nglobalThis.__openSettings=goSettings;',
    }))
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    await page.goto(app.url)
    await page.locator('.tt-row').first().waitFor()
    await page.evaluate(() => __openSettings('accounts', 'hetzner'))
    const projects = page.getByRole('region', { name: 'Project keys' })
    await projects.getByRole('button', { name: 'Add project' }).click()
    const projectName = projects.getByLabel('Project name')
    const token = projects.getByLabel('Project API token')
    await projectName.waitFor()
    assert.equal(await token.getAttribute('type'), 'password')
    const shot = async name => {
      await page.evaluate(() => document.fonts.ready)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      if (evidence) await page.screenshot({ path: path.join(evidence, `hetzner-${name}-${width}-${mode}.png`), animations: 'disabled' })
    }
    await shot('project-keys')
    await projectName.fill('Example')
    await token.fill('fixture-refused')
    await projects.getByRole('button', { name: 'Save project key' }).click()
    await projects.getByRole('alert').getByText('Hetzner refused this token.').waitFor()
    assert.equal(await token.inputValue(), '')
    assert.equal(await projectName.inputValue(), 'Example')
    await token.fill('fixture-accepted')
    await projects.getByRole('button', { name: 'Save project key' }).click()
    await projects.getByRole('status').getByText('Key saved for example.').waitFor()
    await projects.getByRole('button', { name: /example/ }).click()
    await token.fill('fixture-replacement')
    await projects.getByRole('button', { name: 'Save project key' }).click()
    await projects.getByRole('status').getByText('Key saved for example.').waitFor()
    await projects.getByRole('button', { name: /example/ }).click()
    await projects.getByRole('button', { name: 'Forget key' }).click()
    await projects.getByRole('status').getByText('Key forgotten for example.').waitFor()
    assert.equal(await projects.getByRole('button', { name: /example/ }).count(), 0)
    assert.deepEqual(calls, ['fixture-refused', 'fixture-accepted', 'fixture-replacement'])
    await page.evaluate(() => __openSettings('plugins'))
    await page.getByRole('button', { name: 'About Hetzner Cloud' }).click()
    await page.getByText('What you can do', { exact: true }).waitFor()
    await shot('plugin')
    assert.deepEqual(errors, [])
    console.log(`PASS Hetzner project settings at ${width}px in ${mode} mode`)
  } finally {
    await context.close()
    await app.close()
  }
}

try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) await check(width, mode)
} finally {
  await browser.close()
}
