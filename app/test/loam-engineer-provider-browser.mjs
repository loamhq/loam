// The Loam engineer's provider is set like every other engineer's (2026-11-19).
// Fixture settings only: the profile host answers /v1/engineers itself.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { profileHost } from './agent-profile-host.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.PROVIDER_EVIDENCE || `${process.env.TMPDIR}/loam-engineer-provider`
await mkdir(out, { recursive: true })
const app = await profileHost(), browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8'), reports = []
const optionsOf = page => page.getByLabel('Provider', { exact: true }).locator('option').evaluateAll(o => o.map(x => ({ value: x.value, text: x.textContent, disabled: x.disabled })))
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width, height: 940 }, colorScheme: mode })
    const page = await context.newPage(), errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/app.js*', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__p={state,goSettings,settingsProfiles,schedule};' }))
    await page.goto(app.url)
    await page.waitForFunction(() => globalThis.__p?.state.agentsReady)
    await page.evaluate(mode => { localStorage.setItem('loam-ui.theme', JSON.stringify({ mode })); __p.goSettings('agents') }, mode)
    await page.getByText('Every other project', { exact: true }).waitFor()
    const scroll = page.locator(width === 1440 ? '.setmain' : '.sheet.settings')
    // The other engineers' choices, read off Notebook's page, are what Loam's must offer.
    await page.getByRole('button', { name: /^Notebook\s*Engineer/ }).click()
    await page.getByLabel('Provider', { exact: true }).waitFor()
    const others = await optionsOf(page)
    await page.locator('.backbtn').filter({ hasText: 'Agents' }).click()
    await page.getByRole('button', { name: /^Loam\s*Engineer/ }).click()
    const provider = page.getByLabel('Provider', { exact: true })
    await provider.waitFor()
    assert.equal(await page.locator('.profile-fixed').count(), 0, 'Loam\'s provider is a choice, not a fixed label')
    assert.deepEqual((await optionsOf(page)).filter(o => o.value), others.filter(o => o.value))
    assert.equal(await provider.inputValue(), '')
    assert.equal(await provider.locator('option:checked').textContent(), 'Automatic · Claude Code')
    assert.equal(await page.getByText('Use the built-in settings', { exact: true }).count(), 0)
    await scroll.evaluate(node => { node.scrollTop = 0 })
    await page.screenshot({ path: `${out}/loam-claude-${width}-${mode}.png` })
    const writes = app.writes.length
    await provider.selectOption('codex')
    await page.waitForFunction(() => !__p.settingsProfiles.st.busy)
    assert.deepEqual(app.writes.slice(writes), [{ path: '/v1/engineers/Loam', body: { provider: 'codex' } }])
    assert.equal(await provider.inputValue(), 'codex')
    assert.deepEqual(await page.getByLabel('Model', { exact: true }).locator('option').evaluateAll(o => o.map(x => x.value)), ['', 'code-fast', 'code-deep'])
    assert.deepEqual(await Promise.all(['Quick', 'Standard', 'Hard'].map(l => page.getByLabel(l, { exact: true }).inputValue())), ['', '', ''])
    await page.getByText('Applies to the next job. Running jobs keep their settings.', { exact: true }).waitFor()
    await page.screenshot({ path: `${out}/loam-codex-${width}-${mode}.png` })
    // A refusal reads as on every other engineer, and the choice goes back.
    const patchPath = 'PATCH /v1/engineers/Loam', patch = app.routes[patchPath]
    app.routes[patchPath] = (req, res) => { res.writeHead(400, { 'content-type': 'application/json' }); res.end(JSON.stringify({ error: '`provider` for an engineer is claude-code or codex or antigravity, or null' })) }
    await provider.selectOption('claude-code')
    await page.getByRole('alert').filter({ hasText: '`provider` for an engineer is' }).waitFor()
    assert.equal(await provider.inputValue(), 'codex')
    app.routes[patchPath] = patch
    const reset = page.getByText('Use the built-in settings', { exact: true })
    await scroll.evaluate(node => { node.scrollTop = node.scrollHeight })
    await reset.click()
    await page.waitForFunction(() => !__p.settingsProfiles.st.busy)
    assert.deepEqual(app.writes.at(-1), { path: '/v1/engineers/Loam', body: { provider: null } })
    assert.equal(await provider.inputValue(), '')
    assert.equal(await provider.locator('option:checked').textContent(), 'Automatic · Claude Code')
    assert.equal(await reset.count(), 0)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    assert.deepEqual(errors, [])
    reports.push({ width, mode, options: others.map(o => o.value) })
    await context.close()
  }
  await writeFile(`${out}/checks.json`, JSON.stringify({ browser: browser.version(), reports, writes: app.writes }, null, 2) + '\n')
  console.log(`PASS: ${reports.length} viewport/theme combinations; Loam offers the others' providers, saves one, refuses as they do, and goes back to its built-in.`)
} finally { await browser.close(); await app.close() }
process.exit(0)
