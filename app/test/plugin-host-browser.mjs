// Real plugin list/import/activate/relative imports/assets through the relay.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { inspectColourFamilies } from '../tools/colour-families.mjs'
import { pluginHostFixture } from './plugin-host-fixture.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.PLUGIN_EVIDENCE
if (out) await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const publicRoot = process.env.PLUGIN_BASELINE_PUBLIC || 'public'
const source = await readFile(path.join(publicRoot, 'app.js'), 'utf8'), results = []
try {
  for (const width of [320, 390, 1440]) for (const look of ['latte', 'plain']) for (const mode of ['light', 'dark']) {
    if (process.env.PLUGIN_CASE && process.env.PLUGIN_CASE !== `${width}-${look}-${mode}`) continue
    const app = await pluginHostFixture({ look, mode }), label = `${width}-${look}-${mode}`
    const context = await browser.newContext({ viewport: { width, height: width < 1100 ? 844 : 1000 }, isMobile: width < 1100, hasTouch: width < 1100, reducedMotion: 'reduce', colorScheme: mode })
    const page = await context.newPage(), errors = [], checks = []
    page.on('pageerror', error => errors.push(error.message)); page.setDefaultTimeout(15000)
    if (process.env.PLUGIN_BASELINE_PUBLIC) await context.route('**/*', async route => {
      const url = new URL(route.request().url())
      const hosted = /^\/api\/plugins\/([^/]+)\/files\/(.*)$/.exec(url.pathname)
      if (url.origin === app.url && /\.(js|css)$/.test(url.pathname) && url.pathname !== '/themes.css') {
        const file = hosted ? path.join(publicRoot, 'plugins', hosted[1], hosted[2]) : path.join(publicRoot, url.pathname)
        try { return await route.fulfill({ path: file }) } catch {}
      }
      return route.fallback()
    })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__plugins={registry,pluginLoader,coreScope,state,schedule,goSettings,closeSheet,threads,theme,pluginSettingsUI};' }))
    const settle = async () => { await page.evaluate(() => document.fonts.ready); await page.waitForTimeout(120) }
    const familyChecks = []
    const capture = async name => {
      await settle()
      const violations = await page.evaluate(inspectColourFamilies)
      assert.deepEqual(violations, [], `${label}/${name}: colour families`)
      const tokens = await page.evaluate(() => { const s = getComputedStyle(document.documentElement); return Object.fromEntries(['paper-page','paper-ink','paper-card','latte-foam','latte-foam-ink','latte-espresso','latte-espresso-ink'].map(k => [k,s.getPropertyValue('--'+k).trim()])) })
      familyChecks.push({ name, violations, tokens })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${label}/${name} overflow`)
      if (out) await page.screenshot({ path: path.join(out, `${name}-${label}.png`), animations: 'disabled' })
      checks.push(name)
    }
    const plugins = async () => { await page.evaluate(() => __plugins.goSettings('plugins')); await page.getByRole('button', { name: 'About Proof plugin', exact: true }).waitFor() }
    const close = async () => { await page.evaluate(() => __plugins.closeSheet()); await page.waitForFunction(() => !document.querySelector('.settings')) }
    try {
      await page.goto(app.url)
      await page.locator('.tt-row').first().waitFor()
      assert.equal(await page.evaluate(url => fetch(url).then(response => response.text()), app.remoteURL), 'A plugin can read another origin.')
      const proofTab = page.locator(width < 1100 ? '.navbtn' : '.railtab').filter({ hasText: /^Proof$/ })
      await proofTab.waitFor(); assert.equal(await page.locator('.proof-overlay').count(), 1)
      assert.equal(await page.locator('.leaked-plugin').count(), 0)
      assert.equal(await page.evaluate(() => __plugins.coreScope.commands.run('proof.greet')), 'Hello 1')
      await proofTab.click(); await page.getByRole('heading', { name: 'Proof destination' }).waitFor()
      await page.waitForFunction(() => document.querySelector('img[alt="Proof asset"]')?.naturalWidth === 44)
      await capture('proof-destination')
      await page.evaluate(() => __plugins.coreScope.commands.run('core.chats'))
      await page.locator('.tt-row').first().click(); await page.locator('.proof-message').first().waitFor()
      await capture('message-override')
      await plugins(); await page.getByText('Fixture activation failed.', { exact: true }).waitFor()
      await capture('plugins-list')
      await page.getByRole('button', { name: 'About Proof plugin', exact: true }).click()
      await page.getByText('Replaces the message bubble.', { exact: true }).waitFor()
      await capture('plugin-detail')
      await page.getByRole('button', { name: 'Back to Plugins', exact: true }).click()
      await page.getByRole('switch', { name: 'Enable Proof plugin', exact: true }).click()
      await page.waitForFunction(() => !__plugins.pluginLoader.st.rows.find(row => row.id === 'proof').on)
      await close(); await proofTab.waitFor({ state: 'hidden' })
      await page.locator('.proof-overlay').waitFor({ state: 'hidden' }); await page.locator('.proof-message').first().waitFor({ state: 'hidden' })
      assert.equal(await page.evaluate(() => { try { __plugins.coreScope.commands.run('proof.greet'); return false } catch { return true } }), true)
      await page.locator('.tt-message').first().waitFor(); await capture('proof-disabled')
      // Same plugin enabled by another client: focus re-reads and restores it.
      app.setRows(app.rows().map(row => row.id === 'proof' ? { ...row, on: true } : row))
      await page.evaluate(() => window.dispatchEvent(new Event('focus'))); await page.locator('.proof-overlay').waitFor()
      assert.equal(await page.evaluate(() => __plugins.coreScope.commands.run('proof.greet')), 'Hello 2')
      await page.evaluate(() => __plugins.coreScope.commands.run('core.chats')); await proofTab.waitFor(); await proofTab.click()
      await plugins(); await page.getByRole('switch', { name: 'Enable Proof plugin', exact: true }).click()
      await page.waitForFunction(() => __plugins.state.screen === 'home' && !__plugins.state.sheet)
      await capture('section-disabled')
      await page.locator('.tt-row').first().click(); await page.locator('#draft').waitFor(); await plugins()
      for (const name of ['Read aloud', 'Dictation']) await page.getByRole('switch', { name: `Enable ${name}`, exact: true }).click()
      await page.waitForFunction(() => ['read-aloud','dictation'].every(id => !__plugins.pluginLoader.st.rows.find(row => row.id === id).on))
      await close(); assert.equal(await page.locator('.micbtn,.turnlisten,.fs-read-aloud').count(), 0)
      await page.evaluate(() => __plugins.goSettings('voice')); await capture('voice-disabled')
      assert.equal(await page.locator('.settings-section[aria-label="Dictation"],.settings-section[aria-label="Read aloud"]').count(), 0)
      await plugins()
      for (const name of ['Read aloud', 'Dictation']) await page.getByRole('switch', { name: `Enable ${name}`, exact: true }).click()
      await page.waitForFunction(() => ['read-aloud','dictation'].every(id => __plugins.pluginLoader.st.rows.find(row => row.id === id).on))
      await page.evaluate(() => __plugins.goSettings('voice')); await page.getByText('Read aloud', { exact: true }).first().waitFor(); await capture('voice-enabled')
      await plugins()
      await page.getByRole('button', { name: 'About A broken folder', exact: true }).click()
      await page.getByText('The folder and manifest id do not match.', { exact: true }).waitFor(); await capture('broken-plugin')
      await page.getByRole('button', { name: 'Back to Plugins', exact: true }).click()
      await page.locator('#plugin-url').fill('example/refusal'); await page.getByRole('button', { name: 'Review plugin', exact: true }).click()
      await page.getByText('Use a tag, not a moving branch.', { exact: true }).waitFor(); await capture('install-refusal')
      await page.locator('#plugin-url').fill('https://github.com/example/proof/tree/v1.1.0/proof')
      await page.getByRole('button', { name: 'Review plugin', exact: true }).click()
      await page.getByRole('region', { name: 'Review plugin installation' }).waitFor()
      const preview = page.getByRole('region', { name: 'Review plugin installation' })
      assert.equal(await preview.getByRole('region', { name: 'Added in this update' }).count(), 1)
      assert.equal(await preview.getByText('All declarations', { exact: true }).count(), 1)
      assert.equal(app.writes.filter(row => row.method === 'POST' && row.body.confirm).length, 0)
      await capture('install-preview')
      await page.getByRole('button', { name: 'Update plugin', exact: true }).click()
      await page.getByText('The declaration changed. Review it again.', { exact: true }).waitFor()
      await preview.getByRole('region', { name: 'Added in this update' }).getByText('Contains code for the host.', { exact: true }).waitFor()
      assert.equal(app.writes.filter(row => row.method === 'POST' && row.body.confirm).length, 1)
      await capture('install-stale-preview')
      await page.getByRole('button', { name: 'Update plugin', exact: true }).click()
      await page.getByText('Proof plugin installed.', { exact: true }).waitFor(); await capture('installed')
      await page.getByRole('button', { name: 'About Proof plugin', exact: true }).click()
      await page.getByRole('button', { name: 'Remove plugin', exact: true }).click(); await capture('remove-question')
      await page.getByRole('button', { name: 'Remove', exact: true }).click()
      await page.getByText('Proof plugin removed.', { exact: true }).waitFor()
      assert.equal(await page.getByRole('button', { name: 'About Proof plugin', exact: true }).count(), 0)
      await capture('removed')
      // Yours is ordinary CSS: a component rule applies without a token allowlist.
      app.setCSS('.plugin-meta { letter-spacing: 2px; }')
      await page.evaluate(() => window.dispatchEvent(new Event('focus')))
      await page.getByRole('button', { name: 'About Latte & Newspaper', exact: true }).click()
      await page.waitForFunction(() => getComputedStyle(document.querySelector('.plugin-meta')).letterSpacing === '2px')
      assert.equal(await page.evaluate(() => __plugins.pluginLoader.st.failures.has('latte')), false)
      await capture('personal-css')
      await page.getByRole('button', { name: 'Back to Plugins', exact: true }).click()
      app.restoreCSS()
      await page.evaluate(() => window.dispatchEvent(new Event('focus')))
      await page.getByRole('button', { name: 'About Latte & Newspaper', exact: true }).click()
      await page.getByText('Changes a shipped plugin.', { exact: true }).waitFor(); await capture('theme-detail')
      await page.getByRole('button', { name: 'Remove plugin', exact: true }).click(); await page.getByRole('button', { name: 'Remove', exact: true }).click()
      await page.getByText('Latte & Newspaper removed.', { exact: true }).waitFor()
      if (look === 'latte') assert.equal(await page.evaluate(() => __plugins.theme.value.theme), 'plain')
      assert.deepEqual(errors, [])
      results.push({ width, look, mode, checks, familyChecks, errors, writes: app.writes, result: 'passed' })
      console.log(`PASS ${label}: ${checks.length} states, real code/relative assets, override, off/focus, first-party controls, preview/409, remove and Yours CSS ordering`)
    } catch (error) {
      console.error(JSON.stringify(await page.evaluate(() => ({ section: __plugins.state.screen, failures: [...__plugins.pluginLoader.st.failures], error: __plugins.pluginLoader.st.error, rows: __plugins.pluginLoader.st.rows.map(({id,on,effective}) => ({id,on,effective})), rail: __plugins.registry.entries('rail.section').map(entry => entry.id) }))))
      if (out) await page.screenshot({ path: path.join(out, `failure-${label}.png`) })
      throw error
    } finally { await context.close(); await app.close() }
  }
  if (out) await writeFile(path.join(out, 'browser.json'), JSON.stringify({ chromium: browser.version(), results }, null, 2) + '\n')
  console.log(JSON.stringify({ result: 'passed', variants: results.length, states: results.reduce((count, row) => count + row.checks.length, 0) }))
} finally { await browser.close() }
