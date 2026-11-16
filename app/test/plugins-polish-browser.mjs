// Local page/relay with synthetic host answers; --before serves the starting sources.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { pluginHostFixture } from './plugin-host-fixture.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'
const out = process.env.PLUGINS_POLISH_EVIDENCE
if (!out) throw new Error('Set PLUGINS_POLISH_EVIDENCE')
await mkdir(out, { recursive: true })
const before = process.argv.includes('--before'), phase = before ? 'before' : 'after', baseline = '6100882'
const source = file => before ? execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' }) : readFile(`public/${file}`, 'utf8')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ headless: true, executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const results = []
try {
  for (const width of [390, 1440]) {
    const app = await pluginHostFixture(), errors = []
    app.setRows(await shippedPlugins('public'))
    app.setCSS(await readFile('public/themes/latte/theme.css', 'utf8'))
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 900 }, isMobile: width === 390, hasTouch: width === 390, colorScheme: 'light', reducedMotion: 'reduce' })
    for (const file of ['plugin-settings.js', 'style.css', 'threads.js', 'chat-ui.js', 'app.js']) {
      let body = await source(file)
      if (file === 'app.js') body += '\nglobalThis.__polish = { goSettings, closeSheet, threads, loadAgents, state, schedule };'
      await context.route(`**/${file}`, route => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body }))
    }
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message))
    const shot = async name => {
      await page.evaluate(() => document.fonts.ready)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      await page.screenshot({ path: path.join(out, `${phase}-${name}-${width}.png`), animations: 'disabled' })
    }
    try {
      // Hold both sources of the roster so the initial render is observable.
      let release
      const gate = new Promise(resolve => { release = resolve })
      await context.route('**/api/agents', async route => { await gate; await route.continue() })
      await context.route('**/api/threads?archived=1', async route => { await gate; await route.continue() })
      await page.goto(app.url)
      await page.getByText('Opening chats…', { exact: true }).waitFor()
      const pendingEmpty = await page.getByText('No agents yet.', { exact: true }).count()
      assert.equal(pendingEmpty, before ? 1 : 0)
      await shot('chats-loading')
      release()
      await page.locator('.tt-personas .persona').first().waitFor()
      assert.equal(await page.getByText('No agents yet.', { exact: true }).count(), 0)
      await context.unroute('**/api/agents'); await context.unroute('**/api/threads?archived=1')
      await page.evaluate(() => __polish.goSettings('plugins'))
      await page.getByRole('button', { name: 'About Latte & Newspaper', exact: true }).waitFor()
      await page.evaluate(() => document.fonts.ready)
      const geometry = await page.evaluate(() => {
        const themes = document.querySelector('.plugin-group[aria-label="Themes"]'), extensions = document.querySelector('.plugin-group[aria-label="Extensions"]'), install = document.querySelector('.plugin-install')
        const gap = (a, b) => b.querySelector('h3').getBoundingClientRect().top - a.querySelector('.rows').getBoundingClientRect().bottom
        return { themesToExtensions: gap(themes, extensions), extensionsToInstall: gap(extensions, install), rows: [...themes.querySelectorAll('.plugin-list-row')].map(row => ({ text: row.innerText, children: [...row.querySelector('.plugin-row').children].map(n => n.tagName) })) }
      })
      if (!before) {
        assert.equal(geometry.themesToExtensions, 24)
        assert.equal(geometry.extensionsToInstall, 24)
        for (const name of ['Latte & Newspaper', 'Plain']) {
          const row = page.getByRole('button', { name: `About ${name}`, exact: true })
          assert.equal(await row.locator('.plugin-state').innerText(), name === 'Latte & Newspaper' ? 'In use' : 'Not in use')
          assert.match(await row.getAttribute('aria-description'), name === 'Latte & Newspaper' ? / · In use$/ : / · Not in use$/)
          assert.equal(await row.evaluate(n => n.lastElementChild.tagName.toLowerCase()), 'svg')
          assert.equal(await row.locator('.plugin-state').evaluate(n => n.getBoundingClientRect().height < 20 && getComputedStyle(n).whiteSpace === 'nowrap'), true)
          await row.click(); await page.getByRole('button', { name: 'Back to Plugins', exact: true }).click()
        }
      }
      await shot('plugins')
      const summary = page.locator('.plugin-install-options > summary')
      await summary.scrollIntoViewIfNeeded()
      if (!before) assert.equal(await summary.evaluate(n => getComputedStyle(n).listStyleType), 'none')
      await shot('install')
      await summary.focus(); await page.keyboard.press('Enter')
      await page.locator('#plugin-ref').waitFor({ state: 'visible' })
      await page.locator('#plugin-url').fill('example/proof')
      await page.locator('#plugin-ref').fill('v1.0.0'); await page.locator('#plugin-path').fill('plugins/proof')
      await shot('install-expanded')
      await summary.focus(); await page.keyboard.press('Space')
      await page.locator('#plugin-ref').waitFor({ state: 'hidden' })
      // The native toggle event is queued after the open attribute changes.
      await page.waitForTimeout(50)
      // Native disclosure state and fields survive a retained Settings redraw.
      await page.evaluate(() => __polish.schedule())
      assert.equal(await page.locator('.plugin-install-options').getAttribute('open'), null)
      await summary.click(); assert.equal(await page.locator('#plugin-ref').inputValue(), 'v1.0.0')
      assert.equal(await page.locator('#plugin-path').inputValue(), 'plugins/proof')
      await page.getByRole('button', { name: 'Review plugin', exact: true }).click()
      await page.getByRole('region', { name: 'Review plugin installation' }).waitFor()
      assert.deepEqual(app.writes.at(-1), { method: 'POST', body: { url: 'example/proof', ref: 'v1.0.0', path: 'plugins/proof' } })
      if (!before) {
        // A successful empty agents answer is enough even while threads wait.
        let releaseEmpty
        const emptyGate = new Promise(resolve => { releaseEmpty = resolve })
        await context.route('**/api/agents', route => route.fulfill({ json: { agents: [] } }))
        await context.route('**/api/threads?archived=1', async route => { await emptyGate; await route.fulfill({ json: { threads: [], agents: [] } }) })
        await context.route('**/api/threads/events?archived=1', route => route.fulfill({ contentType: 'text/event-stream', body: ': open ping=15000\n\n' }))
        await page.reload(); await page.getByText('No agents yet.', { exact: true }).waitFor()
        assert.equal(await page.getByText('Opening chats…', { exact: true }).count(), 1)
        releaseEmpty()
        await page.getByText('Room for a new thought', { exact: true }).waitFor()
        await shot('chats-empty')
        await context.unroute('**/api/agents'); await context.unroute('**/api/threads?archived=1')
        await context.route('**/api/agents', route => route.fulfill({ status: 503, json: { error: 'Roster temporarily unavailable.' } }))
        await context.route('**/api/threads?archived=1', route => route.fulfill({ status: 503, json: { error: 'Threads temporarily unavailable.' } }))
        await page.reload(); await page.getByText('Threads temporarily unavailable.', { exact: true }).waitFor()
        assert.equal(await page.getByText('No agents yet.', { exact: true }).count(), 0)
        // Recover an empty roster without requiring the page to reload.
        await context.unroute('**/api/agents')
        await context.route('**/api/agents', route => route.fulfill({ json: { agents: [] } }))
        await page.waitForFunction(() => globalThis.__polish)
        await page.evaluate(() => __polish.loadAgents()); await page.getByText('No agents yet.', { exact: true }).waitFor()
      }
      assert.deepEqual(errors, [])
      results.push({ width, phase, pendingEmpty, geometry, errors })
      console.log(`PASS ${phase} ${width}: Plugins layout, detail navigation, disclosure keyboard/state, install fields and roster loading`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, `${phase}.json`), JSON.stringify({ baseline, chromium: browser.version(), fixturesOnly: true, results }, null, 2) + '\n')
} finally { await browser.close() }
