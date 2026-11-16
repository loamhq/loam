// The real page and relay, backed only by disposable in-memory host fixtures.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.THREAD_DETAILS_EVIDENCE
if (!out) throw new Error('Set THREAD_DETAILS_EVIDENCE')
await mkdir(out, { recursive: true })
const source = await readFile('public/app.js', 'utf8'), results = []
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const baseline = process.env.THREAD_DETAILS_BASELINE
const looks = process.env.THREAD_DETAILS_THEMES?.split(',') ?? ['latte', 'plain']
const cases = baseline ? [[390, 844, 'latte', 'light']] : process.env.THREAD_DETAILS_QUICK ? [[390, Number(process.env.THREAD_DETAILS_HEIGHT) || 844, 'latte', 'light']] :
  (process.env.THREAD_DETAILS_SMOKE ? [[390, 660], [1440, 900]] : [[390, 844], [390, 660], [1440, 900]])
    .flatMap(([width, height]) => looks.flatMap(look => ['light', 'dark'].map(mode => [width, height, look, mode])))
try {
  for (const [width, height, look, mode] of cases) {
    const app = await oneProductFixture(), errors = [], checks = [], stamp = `${width}x${height}-${look}-${mode}`
    const id = 'a-quieter-week'
    await app.f.api('PATCH', `/threads/${id}`, { add: ['ledger', 'pip'] })
    app.f.providers[0].models = Array.from({ length: 35 }, (_, i) => ({ id: `model-${i}`, name: `Model ${String(i + 1).padStart(2, '0')}` }))
    Object.assign(app.f.providers[2], { connected: false, reason: 'no login on the box' })
    app.f.providers.push({ id: 'grok', name: 'Grok', connected: false, reason: 'not wired' }, { id: 'opencode', name: 'OpenCode', connected: false, reason: 'not wired' })
    app.routes['GET /v1/settings/appearance'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ phone: { theme: look, mode, size: 90 }, desktop: { theme: look, mode, size: 100 } })) }
    const context = await browser.newContext({ viewport: { width, height }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__details = { threads, state, schedule, goSettings, openSheet, files, picker, activity };' }))
    await context.route('**/api/settings/appearance', route => route.fulfill({ json: { phone: { theme: look, mode, size: 90 }, desktop: { theme: look, mode, size: 100 } } }))
    if (baseline) for (const file of ['threads.js', 'threads.css', 'menu-surface.js', 'menu-surface.css']) {
      await context.route(`**/${file}`, route => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' }) }))
    }
    const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', error => errors.push(error.message))
    if (process.env.THREAD_DETAILS_SAFE_AREA && width === 390) {
      const cdp = await context.newCDPSession(page)
      await cdp.send('Emulation.setSafeAreaInsetsOverride', { insets: { top: 24, bottom: 34, left: 0, right: 0 } })
    }
    const settle = async () => { await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }); await page.waitForTimeout(80) }
    const capture = async name => { await settle(); await page.screenshot({ path: path.join(out, `${name}-${stamp}.png`), animations: 'disabled' }) }
    const inside = async locator => {
      const box = await locator.boundingBox()
      assert.ok(box && box.x >= -1 && box.y >= -1 && box.x + box.width <= width + 1 && box.y + box.height <= height + 1, `inside viewport: ${JSON.stringify(box)}`)
      return box
    }
    const choices = page.locator('.tt-configuration-options')
    const checkOptions = async all => {
      await settle(); const box = await inside(choices)
      if (process.env.THREAD_DETAILS_SAFE_AREA && width === 390) assert.ok(box.y >= 24 && box.y + box.height <= height - 34, 'popover respects both safe areas')
      assert.equal(await choices.getAttribute('data-menu-layout'), 'anchored')
      if (all) for (const option of await choices.getByRole('option').all()) await inside(option)
      assert.equal(await page.locator('.tt-configuration').count(), 0, 'details never opens a duplicate sheet')
      assert.equal(await choices.evaluate(node => node.contains(document.activeElement)), true)
    }
    const chip = label => page.getByRole('button', { name: label, exact: true })
    const openChoice = async (label, edge = 'start') => {
      const control = chip(label)
      await control.evaluate((node, edge) => {
        const target = edge === 'start' ? node.closest('.tt-member') ?? node : node
        target.scrollIntoView({ block: edge, inline: 'nearest' })
      }, edge)
      await settle(); await control.click(); await choices.waitFor(); await settle()
      assert.equal(await control.getAttribute('aria-controls'), await choices.getAttribute('id'))
      return control
    }
    const closeChoice = async label => { await page.keyboard.press('Escape'); await choices.waitFor({ state: 'detached' }); assert.equal(await chip(label).evaluate(node => node === document.activeElement), true) }
    try {
      await page.goto(`${app.url}/threads/${id}`); await page.locator('.tt-title').waitFor(); await settle()
      assert.equal(await page.evaluate(() => document.documentElement.dataset.look), look)
      assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), mode)
      if (baseline) {
        await chip('Providers & models').click(); await chip('Provider for Pip').scrollIntoViewIfNeeded(); await chip('Provider for Pip').click(); await settle()
        const measure = await page.locator('.tt-configuration').evaluate(node => {
          const body = node.querySelector('.tt-configuration-body'), done = node.querySelector('.tt-configuration-foot'), r = n => { const b = n.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, height: b.height } }
          body.scrollTop = body.scrollHeight
          return { sheet: r(node), body: r(body), foot: r(done), bodyClientHeight: body.clientHeight, bodyScrollHeight: body.scrollHeight, flexShrink: getComputedStyle(body).flexShrink, scale: getComputedStyle(document.body).zoom }
        })
        results.push({ stamp, baseline, measure }); await capture('baseline-clipped'); continue
      }
      await page.getByTitle('Thread details', { exact: true }).click()
      await openChoice('Provider for Sprout')
      await checkOptions(true); await capture('provider-top')
      assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-selected')), 'true')
      await choices.evaluate(node => window.__heldChoice = node)
      await page.evaluate(() => __details.schedule()); await settle()
      assert.equal(await choices.evaluate(node => node === window.__heldChoice), true, 'redraw preserves the open popover')
      for (const [value, reason] of [['antigravity', 'no login on the box'], ['grok', 'not wired'], ['opencode', 'not wired']]) {
        const option = choices.locator(`[data-value="${value}"]`)
        assert.equal(await option.isDisabled(), true); assert.equal(await option.getAttribute('aria-disabled'), 'true'); assert.ok((await option.innerText()).includes(reason))
      }
      await page.keyboard.press('End'); assert.equal(await page.evaluate(() => document.activeElement.dataset.value), 'codex', 'disabled choices skipped')
      await page.keyboard.press('Home'); await page.keyboard.press('c'); assert.equal(await page.evaluate(() => document.activeElement.dataset.value), 'codex', 'typeahead')
      await closeChoice('Provider for Sprout')
      await openChoice('Provider for Pip', 'end'); await checkOptions(true)
      const triggerBox = await chip('Provider for Pip').boundingBox(), menuBox = await choices.boundingBox()
      assert.ok(menuBox.y + menuBox.height <= triggerBox.y, 'last member flips above its chip')
      await capture('provider-last-flipped'); await closeChoice('Provider for Pip')
      await openChoice('Model for Sprout'); await checkOptions(false); await capture('model-top')
      await page.keyboard.press('End')
      assert.equal(await page.evaluate(() => document.activeElement.dataset.value), 'model-34'); await inside(choices.getByRole('option').last())
      await page.keyboard.press('Home'); await inside(choices.getByRole('option').first())
      await choices.hover(); await page.mouse.wheel(0, 260); await settle()
      assert.ok(await choices.evaluate(node => node.scrollTop > 100), 'wheel scrolling survives repositioning')
      await page.keyboard.press('End'); await page.keyboard.press('Home')
      if (process.env.THREAD_DETAILS_SMOKE) {
        await closeChoice('Model for Sprout'); assert.deepEqual(errors, [])
        results.push({ stamp, pass: true, checks: ['provider top, last-member flip, model scroll, disabled reasons, keyboard and theme'], errors }); console.log(`PASS ${stamp}`); continue
      }
      // Every row in a long list can be brought into view using just the keyboard.
      for (let i = 0; i < 35; i++) { await page.keyboard.press('ArrowDown'); await inside(page.locator('.tt-configuration-options [role="option"]:focus')) }
      await page.keyboard.press('Enter'); await choices.waitFor({ state: 'detached' })
      await page.waitForFunction(() => !__details.threads.st.open.patching)
      assert.equal(await chip('Model for Sprout').getAttribute('data-value'), 'model-34')
      checks.push('long model list: all options reachable; keyboard selection saves')
      await openChoice('Provider for Sprout'); await choices.locator('[data-value="codex"]').click()
      await page.waitForFunction(() => !__details.threads.st.open.patching)
      assert.equal(await chip('Provider for Sprout').getAttribute('data-value'), 'codex')
      assert.equal(await chip('Model for Sprout').getAttribute('data-value'), '', 'changing provider resets model through existing route')
      const writes = app.broker.seen.filter(req => req.method === 'PATCH' && req.path === `/v1/threads/${id}`).map(req => JSON.parse(req.body))
      assert.ok(writes.some(body => body.engine?.sprout === 'codex')); assert.ok(writes.some(body => body.model?.sprout === 'model-34'))
      await openChoice('Provider for Sprout'); await page.mouse.click(width - 2, 20); await choices.waitFor({ state: 'detached' })
      assert.equal(await chip('Provider for Sprout').getAttribute('data-value'), 'codex', 'outside dismissal does not change provider')
      await openChoice('Provider for Sprout'); await page.keyboard.press('Tab'); await choices.waitFor({ state: 'detached' })
      await page.getByRole('button', { name: 'Chat', exact: true }).click()
      await chip('Providers & models').click(); const sheet = page.locator('.tt-configuration'); await settle(); await inside(sheet)
      const body = sheet.locator('.tt-configuration-body')
      const measured = await body.evaluate(node => ({ shrink: getComputedStyle(node).flexShrink, height: node.clientHeight, scroll: node.scrollHeight }))
      assert.equal(measured.shrink, '1')
      await body.evaluate(node => node.scrollTop = node.scrollHeight); await settle(); await inside(chip('Provider for Pip')); await inside(sheet.getByRole('button', { name: 'Done', exact: true }))
      const below = await page.locator('.tt-stream').evaluate(node => ({ scroll: node.scrollTop, inert: Boolean(node.closest('[inert]')) }))
      assert.equal(below.inert, true)
      await sheet.hover(); await page.mouse.wheel(0, 1600); await settle()
      assert.equal(await page.locator('.tt-stream').evaluate(node => node.scrollTop), below.scroll)
      await chip('Provider for Pip').click(); await choices.waitFor(); await inside(choices)
      await page.keyboard.press('Escape'); await choices.waitFor({ state: 'detached' }); assert.equal(await sheet.count(), 1, 'first Escape closes the choice only')
      await capture('composer-sheet-bottom'); await page.keyboard.press('Escape'); await sheet.waitFor({ state: 'detached' })
      checks.push('retained composer sheet: last member and Done reachable; underlying pane inert; two-level Escape')
      assert.deepEqual(errors, [])
      results.push({ stamp, pass: true, checks, sheet: measured, errors })
      console.log(`PASS ${stamp}`)
    } catch (error) { await capture('failure'); throw error }
    finally { await context.close(); await app.close() }
  }
} finally { await browser.close(); await writeFile(path.join(out, 'results.json'), JSON.stringify(results, null, 2) + '\n') }
