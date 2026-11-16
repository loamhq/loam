import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { pluginRuntimeFixture } from './plugin-runtime-fixture.mjs'

const out = process.env.PLUGIN_RUNTIME_EVIDENCE || process.env.TMPDIR + '/plugin-runtime'
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8'), results = []
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await pluginRuntimeFixture({ mode }), errors = [], frameRequests = []
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 960 }, isMobile: width === 390, hasTouch: width === 390, colorScheme: mode, reducedMotion: 'reduce' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__runtime = { state, theme, schedule, goSettings, closeSheet, goApps, apps, panels, pluginLoader };' }))
    await context.route('**/x/**', route => {
      frameRequests.push(route.request().url())
      return route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Example app</title><p>Example app on this origin</p>' })
    })
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message))
    const shot = async name => {
      await page.evaluate(() => document.fonts.ready)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name} horizontal overflow`)
      await page.screenshot({ path: path.join(out, `${name}-${width}-${mode}.png`), animations: 'disabled' })
    }
    try {
      await page.goto(app.url); await page.waitForFunction(() => globalThis.__runtime?.pluginLoader.st.loaded)
      await page.evaluate(() => __runtime.goSettings('plugins'))
      const pluginRow = state => page.locator('.plugin-list-item').filter({ has: page.getByRole('button', { name: `About Example ${state}`, exact: true }) })
      for (const state of ['running', 'starting', 'failing', 'stopping', 'stopped']) {
        assert.equal(await pluginRow(state).locator('.plugin-service-state').innerText(), `Server · ${state[0].toUpperCase() + state.slice(1)}`)
      }
      assert.equal(await page.getByRole('button', { name: 'About Example skills', exact: true }).locator('.plugin-state').count(), 0)
      assert.equal(await page.locator('.plugin-list-item').filter({ has: page.getByRole('button', { name: 'About Example skills', exact: true }) }).getByRole('switch').count(), 0)
      assert.equal(await page.locator('.plugin-list-item').filter({ has: page.getByRole('button', { name: 'About Example agents', exact: true }) }).getByRole('switch').count(), 0)
      assert.equal(await pluginRow('failing').locator('details').getAttribute('open'), null)
      assert.equal(await pluginRow('running').locator('img').evaluate(n => n.naturalWidth > 0), true)
      await pluginRow('running').scrollIntoViewIfNeeded(); await shot('plugins-states')
      await pluginRow('failing').getByText('Recent log', { exact: true }).click()
      await pluginRow('failing').locator('pre').waitFor({ state: 'visible' })
      await page.evaluate(() => __runtime.schedule())
      await page.waitForTimeout(60)
      assert.match(await pluginRow('failing').locator('pre').innerText(), /could not bind/)
      await pluginRow('failing').scrollIntoViewIfNeeded(); await shot('plugins-log')
      await pluginRow('failing').getByRole('button', { name: 'Restart Example failing', exact: true }).click()
      await page.waitForFunction(() => __runtime.pluginLoader.st.rows.find(row => row.id === 'server-failing').service.state === 'starting')
      assert.deepEqual(app.accountWrites.at(-1), { method: 'POST', path: '/plugins/server-failing/restart', body: {} })
      await pluginRow('failing').getByRole('button', { name: 'About Example failing', exact: true }).click()
      await page.getByRole('button', { name: 'Back to Plugins', exact: true }).waitFor()
      await page.getByText('Server · Starting', { exact: true }).waitFor()
      await shot('plugin-detail')
      app.runtime.find(row => row.id === 'server-failing').service.state = 'failing'

      await page.evaluate(() => __runtime.goSettings('integration', 'github'))
      await page.getByRole('region', { name: 'GitHub account octo-owner', exact: true }).waitFor()
      const owner = page.getByRole('region', { name: 'GitHub account octo-owner', exact: true })
      assert.match(await page.locator('.github-account-list').innerText(), /installation has been removed/)
      if (width === 1440) {
        assert.equal(await page.locator('.setnav').getByText('Settings', { exact: true }).count(), 1)
        assert.equal(await page.locator('.setmain').getByText('Settings', { exact: true }).count(), 0)
        assert.equal(await page.locator('.setmain .sheet-title').innerText(), 'GitHub')
      }
      await shot('github-accounts')
      if (width === 390) {
        await page.getByRole('region', { name: 'GitHub account octo-org', exact: true }).scrollIntoViewIfNeeded()
        await shot('github-accounts-end')
      }
      await owner.getByRole('button', { name: 'Refresh octo-owner', exact: true }).click()
      await owner.getByText('Checked with GitHub just now.', { exact: true }).waitFor()
      assert.equal(app.accountWrites.at(-1).path, '/integrations/github/accounts/octo-owner/refresh')
      await owner.getByRole('button', { name: 'Disconnect octo-owner', exact: true }).click()
      await owner.getByText('Also disconnects: octo-team. They share this GitHub App.', { exact: true }).waitFor()
      await shot('github-confirm')
      await owner.getByRole('button', { name: 'Disconnect account', exact: true }).click()
      await page.getByRole('link', { name: 'Uninstall on GitHub', exact: true }).waitFor()
      assert.equal(await page.locator('.github-account-row').count(), 1)
      assert.match(await page.locator('.github-account-row').innerText(), /octo-org/)
      await shot('github-disconnected')
      await page.getByText('Organization options', { exact: true }).click()
      await page.getByLabel('Organization', { exact: true }).fill('octo-org')
      await page.getByLabel('Register its own GitHub App', { exact: true }).check()
      await shot('github-org-options')

      const navigation = []
      await context.route('https://github.com/**', route => { navigation.push(route.request().url()); return route.fulfill({ contentType: 'text/html', body: '<title>GitHub fixture</title><p>Sign-in fixture</p>' }) })
      await page.getByRole('button', { name: 'Add account', exact: true }).click()
      await page.waitForURL('https://github.com/**')
      assert.equal(app.accountWrites.at(-1).path, '/integrations/github/accounts')
      assert.deepEqual(app.accountWrites.at(-1).body, {})
      assert.equal(navigation.length, 1)

      await page.goto(app.url); await page.waitForFunction(() => globalThis.__runtime)
      await page.evaluate(() => __runtime.goSettings('integration', 'github'))
      await page.getByText('Organization options', { exact: true }).click()
      await page.getByLabel('Organization', { exact: true }).fill('octo-org')
      await page.getByLabel('Register its own GitHub App', { exact: true }).check()
      await page.getByRole('button', { name: 'Continue to GitHub', exact: true }).click()
      await page.waitForURL('https://github.com/organizations/octo-org/settings/apps/new?state=fixture')
      assert.deepEqual(app.accountWrites.at(-1).body, { org: 'octo-org', own_app: true })

      await page.goto(app.url); await page.waitForFunction(() => globalThis.__runtime)
      await page.evaluate(() => __runtime.goApps())
      await page.getByRole('button', { name: /Open Example running/ }).first().waitFor()
      await shot('apps')
      const stopped = page.getByRole('button', { name: /Open Example stopped/ }).first()
      assert.match(await stopped.innerText(), /Off/)
      const unavailable = page.getByRole('button', { name: /Open Example failing/ }).first()
      assert.match(await unavailable.innerText(), /Failing/)
      const running = page.getByRole('button', { name: 'Open Example running', exact: true })
      if (width === 1440) {
        await page.getByRole('button', { name: 'Open Example running to the side', exact: true }).click()
        await page.locator('[data-panel="side"] iframe').waitFor()
        assert.equal(await page.locator('[data-panel="side"] iframe').getAttribute('src'), '/x/server-running/')
        assert.equal(new URL(frameRequests.at(-1)).origin, app.url)
        assert.equal(await page.locator('[data-panel="main"] .apps-row').count(), 0)
        await shot('apps-side-running')
        await page.getByTitle('Show sidebar', { exact: true }).click()
        // The main catalog still says running. The side must read the current
        // catalog before creating a frame from that older row.
        app.runtime.find(row => row.id === 'server-running').service.state = 'failing'
        await page.getByRole('button', { name: 'Open Example running to the side', exact: true }).click()
        await page.locator('[data-panel="side"]').getByText('Example running · Failing', { exact: true }).waitFor()
        assert.equal(await page.locator('[data-panel="side"] iframe').count(), 0)
        app.runtime.find(row => row.id === 'server-running').service.state = 'running'
        await page.getByRole('button', { name: 'Open Example stopped to the side', exact: true }).click()
        await page.locator('[data-panel="side"]').getByText('Example stopped · Off', { exact: true }).waitFor()
        assert.equal(await page.locator('[data-panel="side"] iframe').count(), 0)
        await shot('apps-side-off')
        await page.getByRole('button', { name: 'Open Example failing to the side', exact: true }).click()
        await page.locator('[data-panel="side"]').getByText('Example failing · Failing', { exact: true }).waitFor()
        assert.equal(await page.locator('[data-panel="side"] iframe').count(), 0)
        await shot('apps-side-failing')
        await stopped.click()
        await page.locator('.apps-frame').getByText('Example stopped · Off', { exact: true }).waitFor()
        assert.equal(await page.locator('.apps-frame iframe').count(), 0)
        app.runtime.find(row => row.id === 'server-running').service.state = 'failing'
        const openRunning = () => page.evaluate(() => __runtime.panels.openMain({ kind: 'app', app: __runtime.apps.st.apps.find(app => app.plugin === 'server-running') }))
        await openRunning()
        await page.locator('[data-panel="main"]').getByText('Example running · Failing', { exact: true }).waitFor()
        assert.equal(await page.locator('[data-panel="main"] iframe').count(), 0)
        // Re-selecting the mounted item preserves it. Leave it first so this
        // checks the fresh catalog on entry, rather than demanding a remount.
        await page.evaluate(() => __runtime.panels.openMain({ kind: 'app', app: __runtime.apps.st.apps.find(app => app.plugin === 'server-stopped') }))
        await page.locator('[data-panel="main"]').getByText('Example stopped · Off', { exact: true }).waitFor()
        app.runtime.find(row => row.id === 'server-running').service.state = 'running'
        await openRunning(); await page.locator('[data-panel="main"] iframe').waitFor()
        assert.equal(await page.locator('[data-panel="main"] iframe').getAttribute('src'), '/x/server-running/')
      } else {
        const before = frameRequests.length
        await stopped.click(); assert.equal(frameRequests.length, before)
        const popupPromise = context.waitForEvent('page')
        await page.getByRole('button', { name: 'Open Example running in browser', exact: true }).click()
        const popup = await popupPromise; await popup.waitForLoadState()
        assert.equal(new URL(popup.url()).origin, app.url); await popup.close()
      }
      assert.deepEqual(errors, [])
      results.push({ width, mode, errors, actions: app.accountWrites, frameRequests })
      console.log(`PASS ${width} ${mode}: five server states, log/restart, listed package, GitHub scoped actions/add, local apps and unavailable frames`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'results.json'), JSON.stringify({ chromium: browser.version(), fixturesOnly: true, results }, null, 2) + '\n')
} finally { await browser.close() }
