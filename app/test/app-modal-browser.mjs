// Real Chromium, local app + synthetic host. No installation data or live jobs.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { profileHost } from './agent-profile-host.mjs'
import { filesHost } from './files-host.js'
import { providerFixture, flowFixture } from './fixtures/provider-sign-in.js'

const out = process.env.APP_MODAL_EVIDENCE
if (out) await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8'), results = []
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const only = process.env.APP_MODAL_QUICK ? [1440] : [1180, 1440, 1920, 390, 1099, 1100]
try {
  for (const width of only) for (const theme of process.env.APP_MODAL_QUICK ? ['latte'] : ['latte', 'plain']) for (const mode of process.env.APP_MODAL_QUICK ? ['light'] : ['light', 'dark']) {
    if (process.env.APP_MODAL_CASE && process.env.APP_MODAL_CASE !== `${width}-${theme}-${mode}`) continue
    if ([1099, 1100].includes(width) && (theme !== 'latte' || mode !== 'light')) continue
    const app = await profileHost(), desktop = width >= 1100, id = 'a-quieter-week', label = `${width}-${theme}-${mode}`, checks = [], errors = []
    const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: mode, serviceWorkers: 'block', reducedMotion: width === 1440 && theme === 'plain' && mode === 'dark' ? 'no-preference' : 'reduce' })
    const page = await context.newPage()
    page.setDefaultTimeout(10000)
    page.on('pageerror', error => errors.push(error.message))
    for (const [key, value] of Object.entries(filesHost().routes)) if (key.includes('/v1/files/')) app.routes[key] = value
    const provider = app.f.providers.find(p => p.id === 'claude-code')
    provider.sign_in = providerFixture('claude-code').sign_in
    let flow = flowFixture(provider, 'idle')
    app.routes['POST /v1/providers/claude-code/login'] = (req, res) => { flow = flowFixture(provider); json(res, flow) }
    app.routes['GET /v1/providers/claude-code/login'] = (req, res) => json(res, flow)
    app.routes['DELETE /v1/providers/claude-code/login'] = (req, res) => { flow = flowFixture(provider, 'idle'); json(res, flow) }
    app.routes['POST /v1/providers/claude-code/login/code'] = (req, res) => { flow = flowFixture(provider, 'done'); json(res, flow) }
    await context.route('https://sign-in.example.invalid/**', route => route.fulfill({ contentType: 'text/html', body: '<p>Synthetic sign-in.</p>' }))
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: { theme, mode, size: 100 }, desktop: { theme, mode, size: 100 } })
    let sessions = [{ id: 'resident', kind: 'thread', thread_id: id, thread_title: 'A quieter week', agent: app.f.agents[0].id, state: 'working', ending: { loses_work: true } }]
    let jobs = { slots: [{ job: 'work', title: 'Review the shared notes', project: 'Notebook', briefed_by: app.f.agents[0].id, thread: id, thread_title: 'A quieter week', since: new Date().toISOString() }], queue: [], landing: [] }
    const liveStreams = new Set(), live = () => ({ sessions, box: { sessions: { live: sessions.length }, units: { engineer_jobs: { count: 1 } } } })
    app.routes['GET /v1/sessions/live'] = (req, res) => json(res, live())
    app.routes['GET /v1/sessions/live/events'] = (req, res) => { liveStreams.add(res); res.on('close', () => liveStreams.delete(res)); res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
    app.routes['DELETE /v1/sessions/resident'] = (req, res) => { sessions = []; json(res, {}); for (const stream of liveStreams) stream.write(`data: ${JSON.stringify({ type: 'sessions.live', ...live() })}\n\n`) }
    app.routes['POST /v1/jobs/work/cancel'] = (req, res) => { jobs = { slots: [], queue: [], landing: [] }; json(res, { cancelled: true }) }
    app.routes['GET /v1/activity'] = (req, res) => json(res, { jobs, items: [{ id: 'past', kind: 'job', title: 'Keep the notes together', project: 'Notebook', briefed_by: app.f.agents[0].id, thread: id, thread_title: 'A quieter week', receipt: '/files/vault/Notes/plan.md', outcome: 'completed', ts: new Date().toISOString() }], total: 1, next_offset: null })
    // Make an actual overflowing transcript; preserve its middle, not just 0.
    const record = await app.f.api('GET', `/threads/${id}`)
    record.messages = Array.from({ length: 40 }, (_, i) => ({ id: `long-${i}`, author: i % 2 ? app.f.agents[0].id : 'you', kind: 'message', text: `Note ${i}. A little room to read and keep our place.\n\nThe next step can wait until these notes are ready.`, at: new Date().toISOString() }))
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...record, has_more: false })
    await page.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__modal = { state, threads, activity, goSettings, page, schedule };' }))
    const settings = page.locator('.settings-utility'), activityButton = page.locator('.rail-foot [aria-label="Activity"]')
    const shot = async name => { await page.evaluate(() => document.fonts.ready); if (out) await page.screenshot({ path: path.join(out, `${name}-${label}.png`), animations: 'disabled' }) }
    const modal = () => page.locator('[data-menu-centered]')
    const settleModal = () => modal().evaluate(node => Promise.all(node.getAnimations().map(animation => animation.finished.catch(() => {}))))
    const openActivity = async () => {
      if (desktop) await activityButton.click()
      else { await settings.click(); await page.locator('.settings-place .setrow').click() }
      await page.locator('.activity-page').waitFor()
    }
    try {
      await page.goto(`${app.url}/threads/${id}`)
      await page.locator('.tt-stream').waitFor()
      await page.waitForFunction(() => globalThis.__modal?.state.agentsReady && !__modal.threads.st.open?.loading)
      await page.locator('#draft').fill('Keep this draft while I check the app.')
      await page.evaluate(() => document.fonts.ready)
      await page.waitForFunction(() => !__modal.threads.st.open?.landing)
      await page.locator('.tt-stream').evaluate(async node => { node.scrollTop = 640; node.dispatchEvent(new Event('scroll')); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); window.__preservedStream = node; window.__preservedDraft = document.querySelector('#draft'); window.__preservedThread = __modal.threads.st.open })
      await page.waitForFunction(() => {
        const pane = document.querySelector('.tt-stream'), rect = pane.getBoundingClientRect()
        return ![...pane.querySelectorAll('[data-prose-pending]')].some(n => { const r = n.getBoundingClientRect(); return r.bottom > rect.top - 160 && r.top < rect.bottom + 160 })
      })
      await page.locator('.tt-stream').evaluate(async node => { node.scrollTop = 640; node.dispatchEvent(new Event('scroll')); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))) })
      const before = await page.locator('.tt-stream').evaluate(n => n.scrollTop)
      const preserved = async () => {
        const identity = await page.evaluate(() => ({ stream: document.querySelector('.tt-stream') === __preservedStream, draft: document.querySelector('#draft') === __preservedDraft, thread: __modal.threads.st.open === __preservedThread, url: location.href }))
        assert.ok(identity.stream && identity.draft && identity.thread, JSON.stringify(identity))
        assert.equal(await page.locator('#draft').innerText(), 'Keep this draft while I check the app.')
        const after = await page.locator('.tt-stream').evaluate(n => n.scrollTop)
        assert.ok(Math.abs(after - before) <= 1, `scroll moved from ${before} to ${after}`)
      }
      if (!desktop) await page.getByRole('button', { name: 'Back to threads', exact: true }).click()
      await settings.click()
      await page.locator('.sheet.settings').waitFor()
      if (!desktop) {
        assert.equal(await modal().count(), 0)
        await shot('settings-phone')
        await page.locator('.settings-place .setrow').click()
        await page.locator('.activity-page').waitFor()
        assert.equal(await page.locator('.tt-stream').count(), 0)
        await shot('activity-phone')
        await page.getByRole('button', { name: 'Back from Activity' }).click()
        checks.push('phone Settings remains a sheet; Activity remains a full page with Back')
      } else {
        await preserved()
        assert.equal(await modal().getAttribute('role'), 'dialog')
        assert.equal(await modal().getAttribute('aria-label'), 'Settings')
        assert.equal(await modal().getAttribute('aria-modal'), 'true')
        assert.equal(await settings.getAttribute('aria-expanded'), 'true')
        await page.locator('.theme-choice').first().waitFor()
        await settleModal()
        const rect = await modal().boundingBox()
        assert.ok(Math.abs(rect.x + rect.width / 2 - width / 2) <= 1, JSON.stringify(rect))
        assert.ok(Math.abs(rect.y + rect.height / 2 - 450) <= 1, JSON.stringify(rect))
        assert.ok(rect.width >= 950 && rect.height >= 700)
        await shot('settings-appearance')
        checks.push('desktop opening Settings preserves mounted thread, scroll and draft; centered named modal')
        for (const method of ['Escape', 'scrim', 'close']) {
          if (method !== 'Escape') { await settings.click(); await modal().waitFor() }
          if (method === 'Escape') await page.keyboard.press('Escape')
          if (method === 'scrim') await page.mouse.click(width - 10, 10)
          if (method === 'close') await page.getByRole('button', { name: 'Close settings' }).click()
          await modal().waitFor({ state: 'detached' })
          assert.equal(await settings.evaluate(n => n === document.activeElement), true)
          await preserved()
          checks.push(`${method} closes and restores opener focus`)
        }
        await openActivity(); await preserved()
        await page.keyboard.press('Escape'); await modal().waitFor({ state: 'detached' }); await preserved()
        assert.equal(await activityButton.evaluate(n => n === document.activeElement), true)
        checks.push('opening and closing Activity preserves mounted thread, scroll and draft; restores Activity focus')
        await settings.click(); await modal().waitFor()
        await page.locator('.settings .navrow').filter({ hasText: /^Agents$/ }).click()
        await page.locator('.settings section[aria-label="Agents"] .setrow').first().click()
        await page.locator('.settings .profile-hero').waitFor()
        assert.ok(page.url().includes('profile=agent'))
        await preserved()
        await shot('settings-agent')
        await page.getByRole('button', { name: `Rename ${app.f.agents[0].name}`, exact: true }).click()
        await page.locator('.profile-editor input').focus(); await page.keyboard.press('Escape')
        await page.locator('.profile-editor').waitFor({ state: 'detached' })
        assert.equal(await modal().count(), 1)
        await page.reload()
        await page.locator('.settings .profile-hero').waitFor()
        await page.locator('.tt-stream').waitFor()
        await page.waitForFunction(() => globalThis.__modal?.state.agentsReady && !__modal.threads.st.open?.loading)
        assert.equal(await page.evaluate(() => __modal.threads.st.open.thread.id), id)
        assert.equal(await page.evaluate(() => __modal.page.engineerActivity.st.agent), app.f.agents[0].id)
        checks.push('agent page stays in Settings; reload restores modal detail over the addressed thread')
        // Trap Tab and programmatic attempts to focus the background.
        await modal().locator('button').last().focus(); await page.keyboard.press('Tab')
        assert.equal(await page.evaluate(() => document.querySelector('[data-menu-centered]').contains(document.activeElement)), true)
        await page.locator('#draft').evaluate(n => n.focus())
        assert.equal(await page.evaluate(() => document.querySelector('[data-menu-centered]').contains(document.activeElement)), true)
        await page.locator('.settings .navrow').filter({ hasText: /^Agents$/ }).click()
        await page.locator('.settings section[aria-label="Engineers"] .setrow').filter({ hasText: 'Notebook' }).click()
        await page.getByRole('switch', { name: 'Deploy when a job lands' }).click()
        await page.waitForFunction(() => !__modal.page.settingsProfiles.st.busy)
        assert.equal(app.writes.some(row => row.path === '/v1/engineers/Notebook' && row.body.deploy_on_land === true), true)
        for (const section of ['Providers', 'Integrations', 'Voice', 'Files', 'Plugins', 'Appearance']) {
          await page.locator('.settings .navrow').filter({ hasText: new RegExp(`^${section}$`) }).click()
          await page.locator('.sheet.settings .sheet-title').filter({ hasText: new RegExp(`^${section}$`) }).waitFor()
          assert.equal(await modal().count(), 1)
        }
        await page.locator('.settings .navrow').filter({ hasText: /^Providers$/ }).click()
        await page.locator('.sheet.settings .setrow').filter({ hasText: 'Claude Code' }).first().click()
        await page.getByRole('button', { name: 'Sign in again', exact: true }).click()
        await page.locator('#login-code').fill('synthetic-code')
        await page.locator('.loginflow').getByRole('button', { name: 'Done', exact: true }).click()
        await page.locator('.loginflow').waitFor({ state: 'detached' })
        assert.equal(await modal().count(), 1)
        assert.equal(app.broker.seen.some(row => row.path === '/v1/providers/claude-code/login/code'), true)
        checks.push('provider detail and synthetic sign-in complete inside Settings')
        checks.push('engineer changes reach host; every Settings section stays inside the same modal')
        await page.getByRole('button', { name: 'Sign in again', exact: true }).click()
        await page.locator('#login-code').waitFor()
        const cancelledLogin = page.waitForResponse(response => response.url().endsWith('/api/providers/claude-code/login') && response.request().method() === 'DELETE')
        await activityButton.click()
        await cancelledLogin
        await page.locator('.activity-modal .run-row').waitFor()
        assert.equal(flow.state, 'idle')
        checks.push('replacing Settings with Activity cancels an unfinished provider sign-in')
        assert.equal(await page.locator('.sheet.settings').count(), 0)
        assert.equal(await modal().count(), 1)
        assert.equal(await activityButton.getAttribute('aria-expanded'), 'true')
        await shot('activity-running')
        await page.locator('.run-row').getByRole('button', { name: /^End / }).click()
        await page.locator('.run-confirm').waitFor()
        await page.keyboard.press('Escape')
        await page.locator('.run-confirm').waitFor({ state: 'detached' })
        assert.equal(await modal().count(), 1)
        await page.getByRole('button', { name: 'Cancel Review the shared notes', exact: true }).click()
        await page.getByRole('dialog', { name: 'Cancel job?', exact: true }).waitFor()
        await page.keyboard.press('Escape')
        await page.getByRole('dialog', { name: 'Cancel job?', exact: true }).waitFor({ state: 'hidden' })
        assert.equal(await modal().count(), 1)
        await page.getByRole('button', { name: 'Cancel Review the shared notes', exact: true }).click()
        await page.getByRole('dialog', { name: 'Cancel job?', exact: true }).waitFor()
        await page.goBack(); await modal().waitFor({ state: 'detached' })
        await page.getByRole('dialog', { name: 'Cancel job?', exact: true }).waitFor({ state: 'hidden' })
        await page.goForward(); await modal().waitFor()
        checks.push('Back also clears nested Cancel; Forward returns to usable Activity')
        await page.getByRole('button', { name: 'Cancel Review the shared notes', exact: true }).click()
        await page.getByRole('button', { name: 'Cancel job', exact: true }).click()
        await page.getByRole('dialog', { name: 'Cancel job?', exact: true }).waitFor({ state: 'hidden' })
        assert.equal(app.broker.seen.some(row => row.path === '/v1/jobs/work/cancel'), true)
        await page.locator('.run-row').getByRole('button', { name: /^End / }).click()
        await page.getByRole('button', { name: 'End session', exact: true }).click()
        await page.locator('.run-row').waitFor({ state: 'detached' })
        sessions = [{ id: 'resident', kind: 'thread', thread_id: id, thread_title: 'A quieter week', agent: app.f.agents[0].id, state: 'working', ending: { loses_work: true } }]
        for (const stream of liveStreams) stream.write(`data: ${JSON.stringify({ type: 'sessions.live', ...live() })}\n\n`)
        await page.locator('.run-row').waitFor()
        await page.locator('.activity-chips button').filter({ hasText: /^Jobs$/ }).click()
        await page.waitForFunction(() => __modal.activity.st.kind === 'job' && !__modal.activity.st.loading)
        checks.push('profile edit, End confirmation and nested Cancel honor Escape; confirmed End/Cancel reach host; Earlier filters work')
        await settings.click(); await page.locator('.sheet.settings').waitFor()
        assert.equal(await page.locator('.activity-page').count(), 0)
        await settings.click(); await modal().waitFor({ state: 'detached' })
        checks.push('one surface at a time; both rail buttons toggle; switching replaces')
        await openActivity()
        await page.goBack(); await modal().waitFor({ state: 'detached' })
        assert.equal(new URL(page.url()).pathname, `/threads/${id}`)
        await page.goForward(); await modal().waitFor()
        checks.push('Back closes modal over same thread; Forward reopens')
        const openObject = await page.evaluate(() => { window.__openBeforeStream = __modal.threads.st.open; return true })
        app.emit(id, { type: 'message.done', message: { id: 'arriving', author: app.f.agents[0].id, kind: 'message', text: 'This reply arrived behind Activity.', at: new Date().toISOString() } })
        await page.waitForFunction(() => __modal.threads.st.open.messages.some(m => m.id === 'arriving'))
        assert.equal(await page.evaluate(() => __modal.threads.st.open === __openBeforeStream), openObject)
        checks.push('thread event stream continues behind modal')
        await page.locator('.run-open').click()
        await modal().waitFor({ state: 'detached' })
        assert.equal(new URL(page.url()).pathname, `/threads/${id}`)
        checks.push('Activity thread link closes modal and opens main thread')
        await openActivity()
        await page.locator('.activity-item').filter({ hasText: 'Keep the notes together' }).locator('.activity-summary').click()
        await modal().waitFor({ state: 'detached' })
        await page.waitForURL(`${app.url}/files/vault/Notes/plan.md`)
        await page.locator('.fs-preview').waitFor()
        await page.locator('.fs-preview').evaluate(n => window.__preservedFile = n)
        await settings.click(); await modal().waitFor()
        await page.keyboard.press('Escape'); await modal().waitFor({ state: 'detached' })
        assert.equal(await page.locator('.fs-preview').evaluate(n => n === __preservedFile), true)
        checks.push('Activity receipt opens main Files pane; Settings over a file preserves the mounted preview')
        const saveRoute = app.routes['PUT /v1/files/text']
        app.routes['PUT /v1/files/text'] = (req, res) => { res.writeHead(503, { 'content-type': 'application/json' }); res.end('{"error":"The file could not be saved."}') }
        await page.evaluate(() => __modal.page.files.edit('Keep this unsaved file draft.'))
        await openActivity()
        await page.locator('.run-open').click()
        await page.getByRole('button', { name: 'Keep editing', exact: true }).waitFor()
        await modal().waitFor({ state: 'detached' })
        assert.equal(await page.evaluate(() => __modal.state.screen), 'files')
        assert.equal(await page.evaluate(() => __modal.page.files.st.file.draft), 'Keep this unsaved file draft.')
        await page.getByRole('button', { name: 'Keep editing', exact: true }).click()
        app.routes['PUT /v1/files/text'] = saveRoute
        checks.push('Activity link out honors an unsaved file refusal and exposes Keep editing without losing the draft')
        // Short desktop viewport: headers stay put while bodies scroll.
        await page.setViewportSize({ width, height: 560 })
        await settings.click(); await modal().waitFor()
        await settleModal()
        const head = await page.locator('.settings .app-modal-head').boundingBox()
        await page.locator('.app-modal-body').evaluate(n => n.scrollTop = n.scrollHeight)
        assert.deepEqual(await page.locator('.settings .app-modal-head').boundingBox(), head)
        const short = await modal().boundingBox()
        assert.ok(short.y >= 11 && short.y + short.height <= 549)
        checks.push('short viewport constrains surface and keeps title/close visible')
      }
      assert.deepEqual(errors, [])
      results.push({ width, theme, mode, checks, errors })
      console.log(`${label}: ${checks.length} checks passed`)
    } catch (error) {
      if (out) await page.screenshot({ path: path.join(out, `failure-${label}.png`) })
      console.error('Browser errors:', errors)
      throw error
    } finally { await context.close(); await app.close() }
  }
  if (out) await writeFile(path.join(out, 'browser-checks.json'), JSON.stringify(results, null, 2) + '\n')
  console.log(JSON.stringify({ cases: results.length, checks: results.reduce((n, r) => n + r.checks.length, 0), result: 'passed' }))
} finally { await browser.close() }
