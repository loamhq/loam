// One Loam engineer (2026-11-19): the app's work is the Loam engineer's
// app-only scope, not a second engineer. Fixture settings only: the profile
// host answers /v1/engineers in the shape the broker now gives it (Loam's row
// carries the app's deploy toggle and its label; no `loam-ui` row), and its
// activity has an app-only job, which reads as Loam's.
//
// Run: LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs LOAM_CHROME=<chromium> \
//      node test/one-loam-engineer-browser.mjs
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { profileHost } from './agent-profile-host.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.ONE_ENGINEER_EVIDENCE || `${process.env.TMPDIR}/one-loam-engineer`
await mkdir(out, { recursive: true })
const app = await profileHost()
const loam = app.engineers.engineers.find(r => r.project === 'Loam')
Object.assign(loam, { deploy_on_land: false, deploy_app: 'loam-ui', deploy_label: 'Deploy the app when an app-only job lands', scopes: ['app'] })
const notebook = app.engineers.engineers.find(r => r.project === 'Notebook')
Object.assign(notebook, { deploy_label: 'Deploy when a job lands', scopes: [] })
app.routes['PATCH /v1/engineers/Loam'] = (req, res, url, raw) => {
  const b = JSON.parse(raw); app.writes.push({ path: url.pathname, body: b })
  if ('deploy_on_land' in b) loam.deploy_on_land = b.deploy_on_land
  res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(app.engineers))
}
const jobs = [
  { id: 'job:app', kind: 'job', job: '20261119-110000-aaaa0001', agent: 'sprout', briefed_by: 'sprout', project: 'Loam', scope: 'app', title: 'A calmer composer', outcome: 'landed', landing: 'landed', ts: '2026-11-19T19:00:00Z', engine: 'claude-code', model: 'fable' },
  { id: 'job:full', kind: 'job', job: '20261119-100000-aaaa0002', agent: 'sprout', briefed_by: 'sprout', project: 'Loam', scope: null, title: 'A broker route for the composer', outcome: 'landed', landing: 'landed', ts: '2026-11-19T18:00:00Z', engine: 'claude-code', model: 'fable' }]
app.routes['GET /v1/activity'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ items: jobs, jobs: { slots: [], queue: [], landing: [] }, total: jobs.length, next_offset: null })) }
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8'), reports = []
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
    // One engineer for Loam: one row, and none for the app.
    const titles = await page.locator('.setrow').evaluateAll(rows => rows
      .filter(r => r.querySelector('.setrow-sub')?.textContent.trim() === 'Engineer')
      .map(r => r.querySelector('.setrow-title').textContent.trim()))
    assert.deepEqual(titles.filter(t => t === 'Loam'), ['Loam'])
    assert.equal(titles.some(t => /loam-ui/i.test(t)), false, 'no separate app engineer')
    await scroll.evaluate(node => { node.scrollTop = 0 })
    await page.screenshot({ path: `${out}/agents-${width}-${mode}.png` })
    await page.getByRole('button', { name: /^Loam\s*Engineer/ }).click()
    await page.getByText('Loam engineer', { exact: true }).waitFor()
    // The app's toggle is on Loam's page, labelled for what it does.
    const toggle = page.getByRole('switch', { name: 'Deploy the app when an app-only job lands' })
    await toggle.waitFor()
    assert.equal(await page.getByText('Deploy when a job lands', { exact: true }).count(), 0)
    // Its recent work: the app-only job reads as Loam's, with its scope.
    await page.getByText('A calmer composer').first().waitFor()
    await scroll.evaluate(node => { node.scrollTop = 0 })
    await page.screenshot({ path: `${out}/loam-${width}-${mode}.png` })
    await page.getByRole('button', { name: 'All jobs' }).click()
    await page.getByText(/^Loam app · /).first().waitFor()
    assert.equal(await page.getByText(/^Loam app · /).count(), 1, 'the app-only job, and only it, says "Loam app"')
    assert.equal(await page.getByText(/^Loam · /).count(), 1, 'the full Loam job says "Loam"')
    await page.getByText(/^Loam app · /).first().scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${out}/loam-jobs-${width}-${mode}.png` })
    await page.locator('.backbtn').filter({ hasText: 'Agents' }).click()
    await page.getByRole('button', { name: /^Loam\s*Engineer/ }).click()
    await toggle.waitFor()
    const writes = app.writes.length
    await toggle.click()
    await page.waitForFunction(() => !__p.settingsProfiles.st.busy)
    assert.deepEqual(app.writes.slice(writes), [{ path: '/v1/engineers/Loam', body: { deploy_on_land: true } }])
    await toggle.click()
    await page.waitForFunction(() => !__p.settingsProfiles.st.busy)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    assert.deepEqual(errors, [])
    reports.push({ width, mode, engineers: titles })
    await context.close()
  }
  await writeFile(`${out}/checks.json`, JSON.stringify({ browser: browser.version(), reports, writes: app.writes }, null, 2) + '\n')
  console.log(`PASS: ${reports.length} viewport/theme combinations; one Loam engineer, its app toggle saved to Loam, an app-only job read as Loam's.`)
} finally { await browser.close(); await app.close() }
process.exit(0)
