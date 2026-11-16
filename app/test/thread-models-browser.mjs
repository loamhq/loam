// Real page/relay, disposable synthetic thread. No production threads or agents.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { oneProductFixture } from './one-product-fixture.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const sharp = (await import(process.env.LOAM_SHARP || 'sharp')).default
const out = process.env.THREAD_MODELS_EVIDENCE
if (!out) throw new Error('Set THREAD_MODELS_EVIDENCE')
await mkdir(out, { recursive: true })
const baseline = process.env.THREAD_MODELS_BASELINE
const legacySource = baseline ? execFileSync('git', ['show', `${baseline}:public/threads.js`], { encoding: 'utf8' }) : null
const legacyCSS = baseline ? execFileSync('git', ['show', `${baseline}:public/threads.css`], { encoding: 'utf8' }) : null
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const results = [], captures = []
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = []
    app.f.providers[1].models = [{ id: 'focus', name: 'Focus' }, { id: 'reason', name: 'Reason' }]
    app.f.providers.push({ id: 'offline', name: 'Other provider', connected: false, reason: 'Sign in to connect' })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__models = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(12000); page.on('pageerror', (e) => errors.push(e.message))
    let id, baselineGeometry
    const dialog = page.getByRole('dialog', { name: 'Providers & models', exact: true })
    const pill = page.locator('.tt-composer').getByRole('button', { name: 'Providers & models', exact: true })
    const settle = async () => page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame); await Promise.all([...document.querySelectorAll('[data-menu-surface]')].flatMap(node => node.getAnimations().map(animation => animation.finished.catch(() => {})))) })
    const shot = async () => { await page.mouse.move(0, 0); await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); return page.screenshot({ animations: 'disabled' }) }
    const pair = async (name, aLabel, bLabel, a, b) => {
      const left = (await a()) ?? (await shot()); const right = (await b()) ?? (await shot())
      const height = left.readUInt32BE(20), file = `${name}-${width}-${theme}.png`, ink = theme === 'dark' ? '#eee' : '#222'
      const labels = Buffer.from(`<svg width="${width * 2}" height="32"><text x="14" y="22" fill="${ink}" font-family="sans-serif" font-size="13">${aLabel}</text><text x="${width + 14}" y="22" fill="${ink}" font-family="sans-serif" font-size="13">${bLabel}</text></svg>`)
      const dest = path.join(out, file)
      await sharp({ create: { width: width * 2, height: height + 32, channels: 4, background: theme === 'dark' ? '#1a1715' : '#e6e1da' } }).composite([{ input: left, left: 0, top: 32 }, { input: right, left: width, top: 32 }, { input: labels, left: 0, top: 0 }]).png().toFile(dest)
      captures.push({ file, width, theme, sha256: createHash('sha256').update(await readFile(dest)).digest('hex') }); console.log(file)
    }
    const open = async () => { await pill.click(); await dialog.waitFor(); await settle() }
    const close = async () => { await dialog.getByRole('button', { name: 'Done', exact: true }).click(); await dialog.waitFor({ state: 'detached' }) }
    const choose = async (label, value) => {
      const control = dialog.getByRole('button', { name: label, exact: true })
      await control.click()
      await dialog.getByRole('listbox', { name: label, exact: true }).locator(`[role=option][data-value=${JSON.stringify(value)}]`).click()
      await page.waitForFunction(({ label, value }) => { const b = document.querySelector(`.tt-configuration button[aria-label="${label}"]`); return b && !b.disabled && b.getAttribute('data-value') === value }, { label, value })
    }
    const reload = async () => { await page.reload(); await page.waitForFunction((id) => globalThis.__models?.threads.st.open?.thread.id === id && !__models.threads.st.open.loading, id); await page.locator('.tt-empty-icon').waitFor(); await settle() }
    const geometry = async () => {
      const box = await dialog.boundingBox(), viewport = page.viewportSize()
      assert.ok(box.x >= 0 && box.y >= 0 && box.x + box.width <= viewport.width + 1 && box.y + box.height <= viewport.height + 1, `fully visible: ${JSON.stringify(box)}`)
      assert.equal(await dialog.locator('select').count(), 0)
      assert.equal(await dialog.evaluate((node) => node.parentElement.classList.contains('tt-shell')), true, 'overlay is outside the dock and pane')
      assert.equal(await page.locator('.tt-thread, .tt-details').evaluate((node) => node.inert || node.closest('[inert]') !== null), true)
      const done = await dialog.getByRole('button', { name: 'Done', exact: true }).boundingBox()
      assert.ok(done.y >= box.y && done.y + done.height <= box.y + box.height, 'Done stays visible')
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      const response = await page.request.post(`${app.url}/api/threads`, { data: { persona: 'sprout' } }); assert.equal(response.status(), 201); id = (await response.json()).id
      await page.request.patch(`${app.url}/api/threads/${id}`, { data: { title: 'A clear next step' } })
      await page.evaluate((id) => __models.threads.open(id), id); await page.locator('.tt-empty-icon').waitFor()
      assert.equal(await pill.locator('.modelbtn-name').innerText(), 'Agent’s default')
      const before = await pill.getAttribute('title')
      await page.locator('#draft').fill('@quill @scout A question'); await page.locator('#draft').press('Escape')
      assert.equal(await pill.getAttribute('title'), before, 'mentions never change the default indicator')
      await page.locator('#draft').fill('')
      if (baseline) {
        const legacyContext = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
        await legacyContext.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__models = { threads, schedule };' }))
        const legacy = await legacyContext.newPage(); legacy.setDefaultTimeout(10000)
        await legacy.route('**/threads.js', (route) => route.fulfill({ contentType: 'text/javascript', body: legacySource }))
        await legacy.route('**/threads.css', (route) => route.fulfill({ contentType: 'text/css', body: legacyCSS }))
        await legacy.goto(app.url); await legacy.locator('.tt-row').first().waitFor()
        await legacy.evaluate((id) => __models.threads.open(id), id); await legacy.locator('.tt-empty-icon').waitFor()
        await legacy.locator('.tt-composer .modelbtn').click()
        await legacy.getByRole('button', { name: 'Model for this message', exact: true }).click()
        const choices = legacy.locator(width === 390 ? '.tt-choice-sheet' : '.tt-choice-menu')
        await choices.waitFor(); await legacy.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })
        baselineGeometry = await choices.boundingBox()
        const before = await legacy.screenshot({ animations: 'disabled' })
        await legacyContext.close()
        await pair('before-after', 'Before · separate message picker', 'After · one saved-settings sheet', async () => before, async () => { await open(); await geometry() })
        await close()
      }
      await pair('entry', 'Composer · saved member settings', 'One member · the shared sheet', async () => {}, async () => { await open(); await geometry(); assert.equal(await dialog.locator('.tt-configuration-member').count(), 1) })
      await choose('Model for Sprout', 'deep')
      await dialog.getByRole('status').filter({ hasText: 'Saved to this thread' }).waitFor()
      await pair('persisted', 'Model changed · saved', 'After reload · same provider and model', close, reload)
      assert.equal(await pill.locator('.modelbtn-name').innerText(), 'Deep')
      await open(); await choose('Provider for Sprout', 'codex')
      assert.equal(await dialog.getByRole('button', { name: 'Model for Sprout', exact: true }).getAttribute('data-value'), '', 'provider change resets model to agent default')
      await choose('Model for Sprout', 'reason'); await close(); await reload()
      assert.ok((await pill.getAttribute('title')).includes('Codex · Reason'))
      await open(); await choose('Model for Sprout', ''); await close(); await reload()
      assert.equal(await pill.locator('.modelbtn-name').innerText(), 'Agent’s default', 'null model persists')
      await open(); await choose('Model for Sprout', 'reason')
      await dialog.getByRole('button', { name: 'Provider for Sprout', exact: true }).click()
      const offline = dialog.getByRole('option').filter({ hasText: 'Other provider' }); assert.equal(await offline.isDisabled(), true); assert.ok((await offline.innerText()).includes('Sign in to connect'))
      await page.keyboard.press('Escape')
      for (const name of ['Quill', 'Scout', 'Ledger']) { await dialog.getByRole('button', { name: `Add ${name}`, exact: true }).click(); await dialog.getByRole('button', { name: `Provider for ${name}`, exact: true }).waitFor(); await page.waitForFunction(() => !document.querySelector('.tt-configuration .modelbtn:disabled')) }
      await choose('Model for Quill', 'swift'); await choose('Model for Scout', 'deep')
      await geometry()
      await pair('members', 'Four members · independent choices', 'Model choices · inside the same sheet', async () => {}, async () => { await dialog.getByRole('button', { name: 'Model for Ledger', exact: true }).click(); await dialog.getByRole('listbox', { name: 'Model for Ledger', exact: true }).scrollIntoViewIfNeeded(); await geometry() })
      // Roving options, two-level Escape, and a trapped modal Tab cycle.
      await dialog.getByRole('button', { name: 'Model for Ledger', exact: true }).press('ArrowDown')
      await page.waitForFunction(() => document.activeElement?.getAttribute('role') === 'option')
      await page.keyboard.press('End'); assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-value')), 'deep')
      await page.keyboard.press('Home'); assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-value')), '')
      await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter')
      await page.waitForFunction(() => !__models.threads.st.open.patching && __models.threads.st.open.thread.members.find((m) => m.persona === 'ledger').model === 'swift')
      await dialog.getByRole('button', { name: 'Model for Ledger', exact: true }).press('ArrowDown'); await dialog.getByRole('listbox').waitFor(); await page.keyboard.press('Escape')
      await dialog.getByRole('listbox').waitFor({ state: 'detached' })
      assert.equal(await dialog.getByRole('listbox').count(), 0); assert.equal(await dialog.count(), 1)
      assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Model for Ledger')
      await dialog.getByRole('button', { name: 'Done', exact: true }).focus(); await page.keyboard.press('Tab')
      assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Close')
      await page.keyboard.press('Shift+Tab'); assert.equal(await page.evaluate(() => document.activeElement?.textContent), 'Done')
      await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'detached' }); assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Providers & models')
      await reload(); await open()
      if (width === 1440) {
        await page.setViewportSize({ width, height: 560 }); await settle(); await geometry()
        await page.setViewportSize({ width, height: 1000 }); await settle(); await geometry()
      }
      for (const [name, value] of [['Sprout', 'reason'], ['Quill', 'swift'], ['Scout', 'deep'], ['Ledger', 'swift']]) assert.equal(await dialog.getByRole('button', { name: `Model for ${name}`, exact: true }).getAttribute('data-value'), value)
      if (width === 390) {
        const cdp = await context.newCDPSession(page)
        const grip = await dialog.locator('.grip').boundingBox(), x = grip.x + grip.width / 2, y = grip.y + 2
        const touch = (type, offset = 0) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' || type === 'touchCancel' ? [] : [{ x, y: y + offset }] })
        await touch('touchStart'); await touch('touchMove', 30); await touch('touchCancel')
        await page.waitForTimeout(260); assert.equal(await dialog.count(), 1, 'cancelled native drag returns')
        await touch('touchStart'); await touch('touchMove', 40)
        const transform = await dialog.evaluate((node) => node.style.transform)
        assert.ok(transform.includes('40'), 'native touch moves the sheet')
        await page.evaluate(() => __models.schedule()); await settle()
        assert.equal(await dialog.evaluate((node) => node.style.transform), transform, 'redraw preserves the held gesture')
        for (const offset of [100, 180, 260, 350]) { await touch('touchMove', offset); await page.waitForTimeout(20) }
        await touch('touchEnd'); await dialog.waitFor({ state: 'detached' })
        assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Providers & models')
        await cdp.detach()
      } else await close()
      await page.getByTitle('Thread details', { exact: true }).click()
      await page.locator('#tt-members').scrollIntoViewIfNeeded()
      const detailChoices = page.getByRole('listbox', { name: 'Model for Scout', exact: true })
      await pair('details', 'Details · saved member controls', 'Details · anchored model choices', async () => {}, async () => { await page.locator('#tt-members').getByRole('button', { name: 'Model for Scout', exact: true }).click(); await detailChoices.waitFor(); assert.equal(await dialog.count(), 0); assert.equal(await detailChoices.count(), 1) })
      await page.keyboard.press('Escape'); await detailChoices.waitFor({ state: 'detached' }); assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Model for Scout')
      await page.getByRole('button', { name: 'Chat', exact: true }).click()
      await page.request.patch(`${app.url}/api/threads/${id}`, { data: { default: null } })
      await page.waitForFunction(() => document.querySelector('.tt-composer .modelbtn-name')?.textContent === 'Providers & models')
      // No per-message override reaches the relay, even with several mentions.
      await page.locator('#draft').fill('@quill @scout Please review'); await page.locator('#draft').press('Escape')
      await page.locator('.roundsend').click()
      await page.waitForFunction(() => !__models.threads.st.open.sending)
      const sent = app.broker.seen.filter((r) => r.path === `/v1/threads/${id}/messages` && r.method === 'POST')
      assert.equal(sent.length, 1); assert.deepEqual(JSON.parse(sent[0].body), { text: '@quill @scout Please review' })
      await page.waitForFunction(() => __models.threads.st.open.typing.size === 0)
      assert.deepEqual(errors, [])
      const patches = app.broker.seen.filter((r) => r.path === `/v1/threads/${id}` && r.method === 'PATCH').map((r) => JSON.parse(r.body))
      assert.ok(patches.some((b) => b.model?.sprout === null)); assert.ok(patches.some((b) => b.engine?.sprout === 'codex'))
      results.push({ width, theme, pass: true, ...(baseline ? { baseline, baselineGeometry } : {}), checks: 'sheet geometry, single/four members, independent provider/model persistence, null default, mention-stable indicator, keyboard/focus, phone drag/cancel, details shared controls, no message override, cleanup' })
    } finally {
      if (id) { const deletion = await page.request.delete(`${app.url}/api/threads/${id}`, { timeout: 10000 }); assert.equal(deletion.status(), 200); assert.equal((await app.f.api('GET', '/threads')).threads.some((t) => t.id === id), false) }
      await context.close(); await app.close()
    }
  }
} finally { await browser.close() }
await writeFile(path.join(out, 'results.json'), JSON.stringify({ results, captures }, null, 2) + '\n')
console.log(JSON.stringify(results, null, 2))
