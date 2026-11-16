import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { profileHost } from './agent-profile-host.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.TIER_EVIDENCE || `${process.env.TMPDIR}/engineer-tiers`
await mkdir(out, { recursive: true })
const app = await profileHost(), browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8'), reports = []
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width, height: 940 }, colorScheme: mode })
    const page = await context.newPage(), errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/app.js*', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__tiers={state,goSettings,settingsProfiles,schedule};' }))
    await page.goto(app.url)
    await page.waitForFunction(() => globalThis.__tiers?.state.agentsReady)
    await page.evaluate(mode => { localStorage.setItem('loam-ui.theme', JSON.stringify({ mode })); __tiers.goSettings('agents') }, mode)
    await page.getByText('Every other project', { exact: true }).waitFor()
    for (const project of ['Notebook', 'default']) {
      if (project === 'default') await page.getByText('Every other project', { exact: true }).click()
      else await page.getByRole('button', { name: 'Notebook Codex', exact: false }).click()
      const choose = async (label, value, expected) => {
        await page.getByLabel(label, { exact: true }).selectOption(value)
        await page.waitForFunction(() => !__tiers.settingsProfiles.st.busy)
        assert.deepEqual(app.writes.at(-1), { path: `/v1/engineers/${project}`, body: expected })
      }
      const values = () => Promise.all(['Quick', 'Standard', 'Hard'].map(label => page.getByLabel(label, { exact: true }).inputValue()))
      await page.getByLabel('Quick', { exact: true }).waitFor()
      assert.deepEqual(await values(), ['', '', ''])
      await choose('Quick', 'code-fast', { tiers: { quick: 'code-fast' } })
      await choose('Standard', 'code-deep', { tiers: { standard: 'code-deep' } })
      await choose('Hard', 'code-deep', { tiers: { hard: 'code-deep' } })
      assert.deepEqual(await values(), ['code-fast', 'code-deep', 'code-deep'])
      await choose('Standard', '', { tiers: { standard: null } })
      assert.equal(await page.getByLabel('Standard', { exact: true }).locator('option').first().textContent(), 'Default · Code Fast')
      // Exercise an actual relay refusal and the native select's restored value.
      const patchPath = `PATCH /v1/engineers/${project}`, patch = app.routes[patchPath]
      app.routes[patchPath] = (req, res) => { res.writeHead(400, { 'content-type': 'application/json' }); res.end(JSON.stringify({ error: 'This provider does not offer that tier model.' })) }
      await page.getByLabel('Quick', { exact: true }).selectOption('code-deep')
      await page.getByRole('alert').filter({ hasText: 'This provider does not offer that tier model.' }).waitFor()
      assert.equal(await page.getByLabel('Quick', { exact: true }).inputValue(), 'code-fast')
      app.routes[patchPath] = patch
      await choose('Provider', 'claude-code', { provider: 'claude-code' })
      assert.deepEqual(await values(), ['', '', ''])
      for (const label of ['Quick', 'Standard', 'Hard']) {
        const picker = page.getByLabel(label, { exact: true })
        assert.deepEqual(await picker.locator('option').evaluateAll(options => options.map(option => option.value)), ['', 'fable', 'deep'])
        assert.equal(await picker.locator('option').first().textContent(), 'Default · Fable')
      }
      await choose('Provider', 'codex', { provider: 'codex' })
      await choose('Hard', 'code-deep', { tiers: { hard: 'code-deep' } })
      await page.evaluate(() => document.fonts.ready)
      const geometry = await page.locator('.profile-choice-hinted').evaluateAll(rows => rows.map(row => {
        const rect = row.getBoundingClientRect(), select = row.querySelector('select').getBoundingClientRect(), hint = row.querySelector('.profile-choice-hint').getBoundingClientRect()
        return { height: rect.height, selectHeight: select.height, width: rect.width, hintWidth: hint.width, fits: select.right <= rect.right && hint.right <= rect.right && select.left >= rect.left }
      }))
      assert.ok(geometry.every(row => row.fits && row.selectHeight >= 44), JSON.stringify(geometry))
      const scroll = page.locator(width === 1440 ? '.setmain' : '.sheet.settings')
      await scroll.evaluate(node => { node.scrollTop = 0 })
      await page.screenshot({ path: `${out}/${project}-${width}-${mode}.png` })
      await scroll.evaluate(node => { node.scrollTop = node.scrollHeight })
      await page.screenshot({ path: `${out}/${project}-lower-${width}-${mode}.png` })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      reports.push({ project, width, mode, geometry, errors })
      await choose('Hard', '', { tiers: { hard: null } })
      await page.locator('.backbtn').filter({ hasText: 'Agents' }).click()
    }
    assert.deepEqual(errors, [])
    await context.close()
  }
  await writeFile(`${out}/checks.json`, JSON.stringify({ browser: browser.version(), reports, writes: app.writes }, null, 2) + '\n')
  console.log(`PASS: ${reports.length} project/viewport/theme combinations; individual tier saves, null reset, host refusal and provider reset; no page errors or document overflow.`)
} finally { await browser.close(); await app.close() }
