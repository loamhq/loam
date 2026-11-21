// Combined modal/panel regression against the real app and local host fixtures.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { profileHost } from './agent-profile-host.mjs'
import { filesHost } from './files-host.js'
import { row } from './fixtures/files-contract.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const out = process.env.MODAL_PANELS_EVIDENCE
if (out) await mkdir(out, { recursive: true })
const source = await readFile('public/app.js', 'utf8'), reports = []
const cases = process.env.MODAL_PANELS_QUICK ? [[1440, 'latte', 'light']]
  : [[1180, 'latte', 'light'], [1440, 'latte', 'light'], [1440, 'latte', 'dark'], [1440, 'plain', 'light'], [1440, 'plain', 'dark'], [1920, 'latte', 'light']]
const a = 'a-quieter-week', b = 'small-details'
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
try {
  for (const [width, theme, mode] of cases) {
    const app = await profileHost(), errors = [], checks = [], label = `${width}-${theme}-${mode}`
    const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: mode, reducedMotion: 'reduce', serviceWorkers: 'block' })
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', error => errors.push(error.message))
    const catalog = [{ name: 'A living notebook', url: 'https://garden.example.com/', owner: app.f.agents[0].id, frame: true, source: 'yours', access: 'private', also_at: [] }]
    for (const [key, handler] of Object.entries(filesHost().routes)) if (key.includes('/v1/files/')) app.routes[key] = handler
    app.routes['GET /v1/files/resolve'] = (req, res) => json(res, { status: 'found', resource: row('vault', 'Notes/page.html') })
    app.routes['GET /v1/apps'] = (req, res) => json(res, { apps: catalog, sources: ['yours'] })
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: { theme, mode, size: 100 }, desktop: { theme, mode, size: 100 } })
    app.routes['GET /v1/activity'] = (req, res) => json(res, { jobs: { slots: [], queue: [], landing: [] }, items: [{ id: 'receipt', kind: 'job', title: 'View the companion file', receipt: '/files/vault/Notes/page.html', outcome: 'completed', ts: new Date().toISOString() }], total: 1, next_offset: null })
    app.routes['GET /v1/sessions/live'] = (req, res) => json(res, { sessions: [{ id: 'side-work', kind: 'thread', thread_id: b, thread_title: 'A thread kept beside the work', agent: app.f.agents[0].id, state: 'working' }], box: { sessions: { live: 1 } } })
    app.routes['GET /v1/sessions/live/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
    for (const id of [a, b]) {
      const record = await app.f.api('GET', `/threads/${id}`)
      record.messages = Array.from({ length: 32 }, (_, i) => ({ id: `${id}-${i}`, author: i % 2 ? app.f.agents[0].id : 'you', kind: 'message', at: new Date().toISOString(), text: `Note ${i}. Keep this place while checking the app.\n\nA little room for the next thought.` }))
      app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...record, has_more: false })
    }
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__modalPanels = { state, threads, panels, sidePanel, page, schedule };' }))
    await context.route('https://garden.example.com/**', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><button id="counter" onclick="this.textContent=Number(this.textContent)+1">0</button>' }))
    const main = page.locator('[data-panel="main"]'), side = page.locator('[data-panel="side"]')
    const settings = page.locator('.rail-foot .settings-utility'), activity = page.locator('.rail-foot [aria-label="Activity"]')
    const dialog = page.locator('[data-menu-centered]')
    const capture = async name => { if (out) { await page.evaluate(() => document.fonts.ready); await page.screenshot({ path: path.join(out, `${name}-${label}.png`), animations: 'disabled' }) } }
    try {
      await page.goto(`${app.url}/threads/${a}`)
      await page.waitForFunction(() => globalThis.__modalPanels?.state.agentsReady && !__modalPanels.threads.st.open?.loading)
      await page.evaluate(id => __modalPanels.panels.openSide({ kind: 'thread', id }), b)
      await side.locator('.tt-stream').waitFor()
      await page.waitForFunction(() => !__modalPanels.sidePanel.threads.st.open?.loading)
      await main.locator('.composer [role="textbox"]').fill('Main draft stays here.')
      await side.locator('.composer [role="textbox"]').fill('Side draft stays beside it.')
      await page.evaluate(() => document.fonts.ready)
      const place = async () => {
        await page.locator('.tt-stream').evaluateAll(nodes => nodes.forEach(node => { node.scrollTop = 400; node.dispatchEvent(new Event('scroll')) }))
        await page.waitForFunction(() => [...document.querySelectorAll('.tt-stream')].every(pane => {
          const box = pane.getBoundingClientRect()
          return ![...pane.querySelectorAll('[data-prose-pending]')].some(node => { const r = node.getBoundingClientRect(); return r.bottom > box.top - 160 && r.top < box.bottom + 160 })
        }))
        await page.locator('.tt-stream').evaluateAll(async nodes => { nodes.forEach(node => { node.scrollTop = 400; node.dispatchEvent(new Event('scroll')) }); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))) })
      }
      await place()
      await page.evaluate(() => { window.__kept = {
        main: __modalPanels.threads.st.open, side: __modalPanels.sidePanel.threads.st.open,
        panes: [...document.querySelectorAll('.tt-stream')], fields: [...document.querySelectorAll('.composer [role="textbox"]')],
        scroll: [...document.querySelectorAll('.tt-stream')].map(node => node.scrollTop), panel: JSON.stringify(__modalPanels.panels.st.side),
      } })
      const preserved = async () => assert.deepEqual(await page.evaluate(() => ({
        main: __kept.main === __modalPanels.threads.st.open, side: __kept.side === __modalPanels.sidePanel.threads.st.open,
        panes: __kept.panes.every(node => node.isConnected), fields: __kept.fields.every(node => node.isConnected),
        scroll: __kept.panes.every((node, i) => Math.abs(node.scrollTop - __kept.scroll[i]) <= 1),
        drafts: __kept.fields.map(node => node.textContent), panel: __kept.panel === JSON.stringify(__modalPanels.panels.st.side),
      })), { main: true, side: true, panes: true, fields: true, scroll: true, drafts: ['Main draft stays here.', 'Side draft stays beside it.'], panel: true })
      await settings.click(); await page.locator('[data-menu-centered][aria-label=Settings]').waitFor(); await preserved()
      assert.equal(await dialog.getAttribute('aria-label'), 'Settings')
      assert.equal(await side.locator('[data-menu-centered]').count(), 0)
      const box = await dialog.boundingBox()
      assert.ok(Math.abs(box.x + box.width / 2 - width / 2) < 1)
      await capture('settings-two-threads')
      for (const [id, text] of [[a, 'Main reply behind Settings.'], [b, 'Side reply behind Settings.']]) app.emit(id, { type: 'message.done', message: { id: `incoming-${id}`, author: app.f.agents[0].id, kind: 'message', text, at: new Date().toISOString() } })
      await page.waitForFunction(() => [__modalPanels.threads, __modalPanels.sidePanel.threads].every(view => view.st.open.messages.some(message => message.id.startsWith('incoming-'))))
      await preserved()
      await side.locator('.composer [role="textbox"]').evaluate(node => node.focus())
      assert.equal(await page.evaluate(() => document.querySelector('[data-menu-centered]').contains(document.activeElement)), true)
      await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'detached' }); await preserved()
      assert.equal(await settings.evaluate(node => node === document.activeElement), true)
      checks.push('Settings preserves both mounted threads, drafts, scroll and side selection; both streams continue; background focus is trapped; Escape restores rail focus')
      await activity.click(); await dialog.waitFor(); await preserved()
      await capture('activity-two-threads')
      await settings.click(); await page.locator('[data-menu-centered][aria-label=Settings]').waitFor(); await preserved()
      assert.equal(await dialog.getAttribute('aria-label'), 'Settings')
      await page.mouse.click(width - 6, 6); await dialog.waitFor({ state: 'detached' }); await preserved()
      await activity.click(); await dialog.waitFor()
      await page.getByRole('button', { name: 'Close Activity', exact: true }).click(); await dialog.waitFor({ state: 'detached' }); await preserved()
      checks.push('Activity, replacement, scrim and Close preserve both panels')
      await settings.click(); await dialog.waitFor()
      const count = await page.evaluate(() => history.length)
      await page.goBack(); await dialog.waitFor({ state: 'detached' }); await preserved()
      await settings.click(); await dialog.waitFor(); await preserved()
      assert.equal(await page.evaluate(() => history.length), count)
      await page.reload()
      await page.waitForFunction(() => globalThis.__modalPanels?.threads.st.open && __modalPanels.sidePanel.threads.st.open && !__modalPanels.threads.st.open.loading && !__modalPanels.sidePanel.threads.st.open.loading)
      await dialog.waitFor()
      assert.deepEqual(await page.evaluate(() => [__modalPanels.threads.st.open.thread.id, __modalPanels.sidePanel.threads.st.open.thread.id]), [a, b])
      checks.push('Back dismisses Settings over the same panels, and reload restores the open popup over both items')
      await activity.click(); await page.locator('.run-open').click(); await dialog.waitFor({ state: 'detached' })
      await page.waitForFunction(id => __modalPanels.threads.st.open?.thread.id === id && __modalPanels.sidePanel.threads.st.open?.thread.id === id, b)
      assert.equal(await side.count(), 1)
      checks.push('Activity link to the side thread opens it in main while side keeps the original thread')
      await page.evaluate(app => __modalPanels.panels.openSide({ kind: 'app', app }), catalog[0])
      await side.locator('iframe').waitFor()
      const frame = side.frameLocator('iframe')
      await frame.locator('#counter').click()
      await page.evaluate(() => { window.__keptFrame = document.querySelector('[data-panel="side"] iframe'); window.__keptWindow = __keptFrame.contentWindow })
      for (const control of [settings, activity]) {
        await control.click(); await dialog.waitFor()
        assert.equal(await frame.locator('#counter').innerText(), '1')
        assert.equal(await page.evaluate(() => __keptFrame === document.querySelector('[data-panel="side"] iframe') && __keptFrame.contentWindow === __keptWindow), true)
        await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'detached' })
        assert.equal(await frame.locator('#counter').innerText(), '1')
      }
      checks.push('Settings and Activity preserve a live side iframe and its edited script state')
      await page.evaluate(() => __modalPanels.page.goFiles('vault', 'Notes/plan.md', { isFile: true }))
      await main.locator('.fs-preview').waitFor()
      app.routes['PUT /v1/files/text'] = (req, res) => { res.writeHead(503, { 'content-type': 'application/json' }); res.end('{"error":"This file could not be saved."}') }
      await page.evaluate(() => __modalPanels.page.files.edit('Keep this unsaved main file.'))
      await activity.click(); await dialog.waitFor()
      await page.locator('.activity-item .activity-summary').filter({ hasText: 'View the companion file' }).click()
      await page.getByRole('button', { name: 'Keep editing', exact: true }).waitFor()
      await dialog.waitFor({ state: 'detached' })
      assert.equal(await page.evaluate(() => __modalPanels.page.files.st.file.draft), 'Keep this unsaved main file.')
      assert.equal(await frame.locator('#counter').innerText(), '1')
      await page.getByRole('button', { name: 'Keep editing', exact: true }).click()
      checks.push('File link out exposes the main save refusal while the side iframe stays mounted')
      assert.deepEqual(errors, [])
      reports.push({ width, theme, mode, checks, errors })
      console.log(`${label}: ${checks.length} combined checks passed`)
    } catch (error) {
      if (out) await page.screenshot({ path: path.join(out, `failure-${label}.png`) })
      console.error('Page errors:', errors); throw error
    } finally { await context.close(); await app.close() }
  }
  if (out) await writeFile(path.join(out, 'browser.json'), JSON.stringify(reports, null, 2) + '\n')
  console.log(JSON.stringify({ cases: reports.length, checks: reports.reduce((sum, report) => sum + report.checks.length, 0), result: 'passed' }))
} finally { await browser.close() }
