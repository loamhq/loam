// Real page, loopback broker and isolated app frames; no live host data.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { profileHost } from './agent-profile-host.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8'), reports = []
const out = process.env.APPS_ACTIVITY_EVIDENCE
if (out) await mkdir(out, { recursive: true })
const widths = process.env.APPS_ACTIVITY_QUICK ? [1440] : [1180, 1440, 1920, 390]
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
try {
  for (const width of widths) for (const mode of ['light', 'dark']) {
    const app = await profileHost(), phone = width < 1100, label = `${width}-${mode}`, checks = [], errors = []
    const context = await browser.newContext({ viewport: { width, height: phone ? 844 : 900 }, colorScheme: mode, reducedMotion: 'reduce', serviceWorkers: 'block' })
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', error => errors.push(error.message))
    const catalog = [
      { name: 'Garden', url: 'https://garden.example.com/', owner: app.f.agents[0].id, frame: true, source: 'yours', access: 'private' },
      { name: 'Notebook', url: 'https://notebook.example.com/', owner: app.f.agents[1].id, frame: true, source: 'yours', access: 'private' },
      { name: 'Journal', url: 'https://journal.example.org/', owner: app.f.agents[0].id, frame: false, source: 'yours', access: 'public' },
    ]
    app.routes['GET /v1/apps'] = (req, res) => json(res, { apps: catalog, sources: ['yours'] })
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: { theme: 'latte', mode, size: 100 }, desktop: { theme: 'latte', mode, size: 100 } })
    app.routes['GET /v1/sessions/live'] = (req, res) => json(res, { sessions: [{ id: 'work', kind: 'thread', thread_id: 'a-quieter-week', thread_title: 'A quieter week', agent: app.f.agents[0].id, state: 'working' }], box: { sessions: { live: 1 } } })
    app.routes['GET /v1/sessions/live/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__layout = { state, apps, panels, sidePanel, threads, page, goActivity, goApps, running, schedule };' }))
    await context.route(/^https:\/\/(garden|notebook)\.example\.com\//, route => route.fulfill({ contentType: 'text/html', body: `<!doctype html><style>body{font:18px system-ui;padding:32px;background:${mode === 'dark' ? '#242722;color:#e4e7dc' : '#faf8f4;color:#35352b'}}button{padding:12px}</style><h1>${new URL(route.request().url()).hostname.split('.')[0]}</h1><p>A little room for ideas to grow.</p><button id="grow" onclick="this.textContent='Growing'">Plant an idea</button>` }))
    const main = page.locator('[data-panel=main]'), side = page.locator('[data-panel=side]'), modal = page.locator('[data-menu-centered]')
    const appsTab = () => page.getByRole('button', { name: 'Apps', exact: true })
    const row = name => page.getByRole('button', { name: `Open ${name}${phone ? ' in browser' : ''}`, exact: true })
    const close = async () => { await page.keyboard.press('Escape'); await modal.waitFor({ state: 'detached' }) }
    const capture = async name => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      if (out) await page.screenshot({ path: path.join(out, `${name}-${label}.png`), animations: 'disabled' })
    }
    const assertActivity = async () => {
      await modal.filter({ has: page.locator('.activity-page') }).waitFor()
      assert.equal(await page.locator('.activity-desk,.activity-screen').count(), 0)
      assert.equal(await modal.getAttribute('aria-label'), 'Activity')
      const box = await modal.boundingBox()
      assert.ok(Math.abs(box.x + box.width / 2 - (await page.viewportSize()).width / 2) <= 1)
      assert.ok(Math.abs(box.y + box.height / 2 - (await page.viewportSize()).height / 2) <= 1)
      assert.equal(Math.round(box.width), 960)
    }
    try {
      await page.goto(app.url)
      await page.waitForFunction(() => globalThis.__layout?.state.agentsReady)
      await appsTab().click(); await row('Garden').waitFor()
      if (phone) {
        assert.equal(await page.locator('.listcol,.row-side-action').count(), 0)
        await capture('apps-phone')
        const popupPromise = page.waitForEvent('popup')
        await row('Garden').click()
        const popup = await popupPromise; await popup.close()
        assert.equal(await page.locator('iframe').count(), 0)
        await page.locator('.settings-utility').click(); await page.locator('.settings-place .setrow').click()
        await page.locator('.activity-screen').waitFor()
        assert.equal(await modal.count(), 0)
        await capture('activity-phone')
        await page.getByRole('button', { name: 'Close Activity' }).click()
        await row('Garden').waitFor()
        checks.push('Phone catalog opens browser tabs; Activity remains a full screen and Back returns to Apps')
      } else {
        assert.equal(await main.locator('.apps-row').count(), 0)
        assert.equal(await page.locator('.workspace-list .apps-row').count(), 3)
        await main.getByText('Choose an item', { exact: true }).waitFor()
        await capture('apps-empty')
        await row('Garden').click(); await main.locator('iframe').waitFor()
        assert.equal(await row('Garden').getAttribute('aria-current'), 'page')
        await main.frameLocator('iframe').locator('#grow').click()
        await page.evaluate(() => { window.__frame = document.querySelector('[data-panel=main] iframe'); window.__frameWindow = __frame.contentWindow })
        await capture('apps-main')
        // Every desktop entry shares the app-wide shell, preserving the iframe.
        for (const entry of ['rail', 'running', 'legacy-sheet', 'settings-running', 'phone-link', 'section-command']) {
          if (entry === 'rail') await page.getByRole('button', { name: 'Activity', exact: true }).click()
          else await page.evaluate(entry => {
            if (entry === 'running') __layout.running.requestEnd({ id: 'work', state: 'working' })
            if (entry === 'legacy-sheet') __layout.page.openSheet({ kind: 'running' })
            if (entry === 'settings-running') __layout.page.goSettings('running')
            if (entry === 'phone-link') __layout.page.openPhoneActivity()
            if (entry === 'section-command') __layout.page.commands.run('section.open', { id: 'activity' })
          }, entry)
          await assertActivity()
          assert.equal(await page.evaluate(() => __frame === document.querySelector('[data-panel=main] iframe') && __frameWindow === __frame.contentWindow), true)
          assert.equal(await main.frameLocator('iframe').locator('#grow').innerText(), 'Growing')
          if (entry === 'rail') await capture('activity-main')
          // An End confirmation owns its first Escape.
          if (entry === 'running') await page.getByRole('button', { name: 'Keep running', exact: true }).click()
          await page.locator('.run-confirm').waitFor({ state: 'detached' })
          await close()
          checks.push(`${entry} opens centered Activity and preserves main app state`)
        }
        await page.locator('.settings-utility').click(); await modal.waitFor()
        const settingsBox = await modal.boundingBox(); await close()
        await page.getByRole('button', { name: 'Activity', exact: true }).click(); await assertActivity()
        const activityBox = await modal.boundingBox()
        assert.equal(activityBox.width, settingsBox.width); assert.equal(activityBox.height, settingsBox.height)
        await page.goBack(); await modal.waitFor({ state: 'detached' })
        await page.getByRole('button', { name: 'Activity', exact: true }).click(); await assertActivity()
        await page.reload(); await page.waitForFunction(() => globalThis.__layout?.state.agentsReady)
        await assertActivity(); await main.locator('iframe').waitFor()
        await close()
        checks.push('Settings and Activity share dimensions; Back dismisses Activity and reload restores it above the main app')
        // Direct URL has no app-owned preceding modal entry.
        await page.goto(`${app.url}/threads/a-quieter-week?modal=activity&keep=yes#notes`)
        await page.waitForFunction(() => globalThis.__layout?.state.agentsReady)
        await assertActivity(); await close()
        assert.ok(page.url().includes('keep=yes#notes'), page.url())
        await main.locator('.tt-stream').waitFor()
        await appsTab().click(); await row('Garden').click(); await main.locator('iframe').waitFor()
        await page.getByRole('button', { name: 'Open Notebook to the side' }).click(); await side.locator('iframe').waitFor()
        assert.equal(await page.locator('.listcol').getAttribute('aria-hidden'), width < 1700 ? 'true' : null)
        await capture('apps-side')
        await page.getByRole('button', { name: 'Activity', exact: true }).click(); await assertActivity()
        assert.equal(await page.locator('iframe').count(), 2)
        await capture('activity-side'); await close()
        await page.reload(); await page.waitForFunction(() => globalThis.__layout?.state.agentsReady)
        await main.locator('iframe').waitFor(); await side.locator('iframe').waitFor()
        assert.equal(await main.locator('iframe').getAttribute('title'), 'Garden')
        assert.equal(await side.locator('iframe').getAttribute('title'), 'Notebook')
        await appsTab().click() // Reveals the list even when automatically tucked.
        await row('Notebook').click(); await main.locator('iframe[title="Notebook"]').waitFor()
        await side.locator('iframe[title="Notebook"]').waitFor()
        await row('Garden').click({ modifiers: ['Alt'] }); await side.locator('iframe[title="Garden"]').waitFor()
        await side.getByRole('button', { name: 'Swap the two panels' }).click()
        await main.locator('iframe[title="Garden"]').waitFor(); await side.locator('iframe[title="Notebook"]').waitFor()
        await side.getByRole('button', { name: 'Open as the main panel' }).click()
        await main.locator('iframe[title="Notebook"]').waitFor(); assert.equal(await side.count(), 0)
        await row('Notebook').click({ modifiers: ['Alt'] }); await side.locator('iframe[title="Notebook"]').waitFor()
        await main.locator('iframe[title="Notebook"]').waitFor()
        if (width < 1700) await main.getByTitle('Show sidebar', { exact: true }).click()
        await capture('apps-list-side')
        const popupPromise = page.waitForEvent('popup'); await page.getByRole('button', { name: 'Open Journal in browser', exact: true }).click()
        const popup = await popupPromise; await popup.close()
        assert.equal(await main.locator('iframe').count(), 1)
        checks.push('List selections, side icon, Alt-click, independent opens, swaps, promote and external tabs work; both apps restore on reload')
        await page.setViewportSize({ width: 1179, height: 900 })
        await page.waitForFunction(() => !__layout.panels.available())
        await page.locator('.row-side-action').first().waitFor({ state: 'detached' })
        await side.waitFor({ state: 'detached' })
        await page.setViewportSize({ width: 1180, height: 900 }); await side.locator('iframe').waitFor()
        await side.getByRole('button', { name: 'Close side panel' }).click(); await side.waitFor({ state: 'detached' })
        // Reproduce the former full-page desktop path by widening phone Activity.
        await page.setViewportSize({ width: 390, height: 844 })
        await page.waitForFunction(() => !__layout.state.wide)
        await page.locator('.settings-utility').click(); await page.locator('.settings-place .setrow').click()
        await page.locator('.activity-screen').waitFor()
        await page.setViewportSize({ width, height: 900 }); await assertActivity()
        await close(); await main.locator('iframe[title="Notebook"]').waitFor()
        await page.evaluate(() => __layout.threads.open('a-quieter-week'))
        await main.locator('.tt-stream').waitFor()
        await main.locator('.composer [role=textbox]').fill('Keep this draft through the width change.')
        await page.getByRole('button', { name: 'Activity', exact: true }).click(); await assertActivity()
        await page.setViewportSize({ width: 390, height: 844 }); await page.locator('.activity-screen').waitFor()
        await page.setViewportSize({ width, height: 900 }); await assertActivity()
        assert.ok(new URL(page.url()).pathname.endsWith('/threads/a-quieter-week'))
        await close(); await main.locator('.tt-stream').waitFor()
        assert.equal(await main.locator('.composer [role=textbox]').innerText(), 'Keep this draft through the width change.')
        checks.push('1179/1180 side availability and phone-to-desktop Activity conversion preserve their destination rules')
      }
      assert.deepEqual(errors, [])
      reports.push({ width, mode, checks, errors })
      console.log(`${label}: ${checks.length} checks passed`)
    } catch (error) {
      if (out) await page.screenshot({ path: path.join(out, `failure-${label}.png`) })
      console.error('Page errors:', errors); throw error
    } finally { await context.close(); await app.close() }
  }
  if (out) await writeFile(path.join(out, 'browser.json'), JSON.stringify(reports, null, 2) + '\n')
  console.log(JSON.stringify({ cases: reports.length, checks: reports.reduce((n, report) => n + report.checks.length, 0), result: 'passed' }))
} finally { await browser.close() }
