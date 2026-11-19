// Real page/relay, synthetic threads, files and cross-origin app. No host data.
import assert from 'node:assert/strict'
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { filesHost } from './files-host.js'
import { shippedPlugins } from '../src/shipped-plugins.js'

const out = process.env.PANEL_FOCUS_EVIDENCE
if (out) await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8'), reports = []
const a = 'a-quieter-week', b = 'small-details'
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
try {
  for (const mode of ['light', 'dark']) {
    const app = await oneProductFixture({ APPS_DOMAIN: 'example.test' }), errors = []
    const files = filesHost({ texts: { 'vault:Notes/plan.md': '# A little room for ideas\n\nKeep the useful details beside the conversation.' } })
    for (const [route, handler] of Object.entries(files.routes)) if (route.includes('/v1/files/')) app.routes[route] = handler
    const settings = { mode, theme: 'latte', size: 100 }
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: settings, desktop: settings })
    const plugins = await shippedPlugins(path.resolve('public'))
    plugins.find(p => p.id === 'dictation').on = true
    app.routes['GET /v1/plugins'] = (req, res) => json(res, { plugins })
    for (const file of await readdir('../plugins/_listed/dictation/page')) {
      app.routes[`GET /v1/plugins/dictation/files/page/${file}`] = async (req, res) => {
        res.writeHead(200, { 'content-type': file.endsWith('.css') ? 'text/css' : 'text/javascript' })
        res.end(await readFile(`../plugins/_listed/dictation/page/${file}`))
      }
    }
    const garden = { name: 'Garden', url: 'https://garden.example.test/', frame: true, source: 'yours', access: 'private' }
    app.routes['GET /v1/apps'] = (req, res) => json(res, { apps: [garden], sources: ['yours'] })
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: mode, reducedMotion: 'no-preference' })
    await context.addInitScript(() => { globalThis.SpeechRecognition ??= class {} })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__focus={panels,threads,sidePanel,schedule};' }))
    await context.route('**/api/health', route => route.fulfill({ json: { configured: true, ok: true, speech: true } }))
    await context.route('https://garden.example.test/**', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><h1>Garden</h1><button>Plant an idea</button>' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', e => errors.push(e.message))
    const main = page.locator('[data-panel=main]'), side = page.locator('[data-panel=side]')
    const settle = async () => { await page.evaluate(() => document.fonts.ready); await page.waitForTimeout(280) }
    const shot = async name => { if (out) await page.screenshot({ path: path.join(out, `${name}-1440-${mode}.png`), animations: 'disabled' }) }
    // Inspect actual paint, including hover/pressed colors and disabled buttons;
    // leaving tier classes on a demoted control must not count as success.
    const inventory = () => page.evaluate(() => {
      const color = token => { const n = document.createElement('span'); n.style.color = `var(${token})`; document.body.append(n); const c = getComputedStyle(n).color; n.remove(); return c }
      const espresso = ['--latte-espresso', '--latte-espresso-hover', '--latte-espresso-pressed'].map(color)
      const foam = ['--latte-foam', '--latte-hover'].map(color)
      return [...document.querySelectorAll('[data-panel] button,.rail button')].filter(n => n.getClientRects().length).map(n => {
        const s = getComputedStyle(n), r = n.getBoundingClientRect()
        return { panel: n.closest('[data-panel]')?.dataset.panel ?? 'rail', label: n.getAttribute('aria-label') || n.title || n.textContent.trim(),
          tier: espresso.includes(s.backgroundColor) ? 'primary' : foam.includes(s.backgroundColor) ? 'secondary' : 'Newspaper',
          background: s.backgroundColor, color: s.color, inStream: !!n.closest('.tt-stream'), rect: { x: r.x, y: r.y, width: r.width, height: r.height } }
      })
    })
    const check = async (focused, primary = 1, secondary = 1) => {
      await settle()
      assert.equal(await page.locator('.panel-pair').getAttribute('data-focused-panel'), focused)
      const controls = await inventory(), active = controls.filter(c => c.panel === focused), inactive = controls.filter(c => c.panel === (focused === 'main' ? 'side' : 'main'))
      assert.equal(active.filter(c => c.tier === 'primary').length, primary, JSON.stringify(active))
      assert.equal(active.filter(c => c.tier === 'secondary').length, secondary, JSON.stringify(active))
      assert.equal(inactive.filter(c => c.tier !== 'Newspaper').length, 0, JSON.stringify(inactive))
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      reports.push({ mode, focused, controls })
      return controls
    }
    try {
      await page.goto(`${app.url}/threads/${a}`)
      await main.locator('.roundmic').waitFor()
      await page.waitForFunction(() => __focus.threads.st.open && !__focus.threads.st.open.loading && !__focus.threads.st.open.refreshing)
      await main.locator('.composer [role=textbox]').fill('Keep the next step clear.')
      await main.locator('.roundmic').waitFor(); await settle()
      assert.equal(await main.locator('.roundmic').evaluate(n => getComputedStyle(n).transitionDuration), '0s', 'single-pane paint motion unchanged')
      await page.evaluate(b => __focus.panels.openSide({ kind: 'thread', id: b }), b)
      await side.locator('.roundmic').waitFor()
      await page.waitForFunction(() => __focus.sidePanel.threads.st.open && !__focus.sidePanel.threads.st.open.loading && !__focus.sidePanel.threads.st.open.refreshing)
      await check('side', 0, 1)
      await side.locator('.composer [role=textbox]').fill('Leave a little room for ideas.')
      const initial = await check('side'); await shot('side-focused')
      assert.equal(await side.locator('.roundmic').evaluate(n => getComputedStyle(n).transitionDuration.split(',')[0]), '0.14s')
      await main.locator('.tt-head').click({ position: { x: 8, y: 30 } })
      const moved = await check('main'); await shot('main-focused')
      assert.deepEqual(moved.filter(c => !c.inStream).map(c => [c.panel, c.label, c.rect]), initial.filter(c => !c.inStream).map(c => [c.panel, c.label, c.rect]), 'focus changes no chrome geometry')
      assert.deepEqual(moved.map(c => [c.panel, c.label, c.rect.width, c.rect.height]), initial.map(c => [c.panel, c.label, c.rect.width, c.rect.height]), 'all control dimensions survive focus changes')
      assert.deepEqual(moved.filter(c => c.panel === 'rail'), initial.filter(c => c.panel === 'rail'), 'navigation paint and geometry unchanged')
      await side.locator('.roundmic').hover(); await check('main')
      await page.mouse.move(0, 0)
      // Keyboard focus entering either composer, without a pointer event.
      await page.keyboard.press('Tab'); await side.locator('.composer [role=textbox]').focus(); await check('side')
      await main.locator('.composer [role=textbox]').focus(); await check('main')
      await side.locator('.composer [role=textbox]').click(); await check('side')
      // A stream redraw must retain attention and both independent drafts.
      await page.evaluate(() => __focus.schedule()); await check('side')
      assert.equal(await main.locator('.composer [role=textbox]').textContent(), 'Keep the next step clear.')
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await main.locator('.composer [role=textbox]').focus(); await check('main')
      assert.ok(await main.locator('.roundmic').evaluate(n => getComputedStyle(n).transitionDuration.split(',').every(t => parseFloat(t) < .001)), 'reduced motion removes the paint transition')
      await side.getByRole('button', { name: 'Close side panel', exact: true }).click()
      await side.waitFor({ state: 'detached' }); await check('main')
      // Files use the same focus contract; their editor mic keeps its tier.
      await page.evaluate(() => __focus.panels.openSide({ kind: 'file', root: 'vault', path: 'Notes/plan.md', mode: 'edit' }))
      await side.locator('.roundmic').waitFor(); await check('side', 0, 1)
      await main.locator('.composer [role=textbox]').focus(); await check('main')
      await side.locator('textarea').focus(); await check('side', 0, 1)
      await shot('file-focused')
      await page.evaluate(() => __focus.panels.swap())
      await main.locator('.roundmic').waitFor(); await check('side')
      await main.locator('textarea').focus(); await check('main', 0, 1)
      // Restore main thread, then enter a cross-origin iframe repeatedly. The
      // second entry follows a click on non-focusable parent chrome.
      await page.evaluate(a => __focus.panels.openMain({ kind: 'thread', id: a }), a)
      await main.locator('.composer [role=textbox]').waitFor()
      await page.evaluate(garden => __focus.panels.openSide({ kind: 'app', app: garden }), garden)
      await side.locator('iframe').waitFor(); await check('side', 0, 0)
      await main.locator('.composer [role=textbox]').focus(); await check('main')
      await side.locator('iframe').focus(); await check('side', 0, 0)
      for (let i = 0; i < 2; i++) {
        await main.locator('.tt-head').click({ position: { x: 8, y: 30 } }); await check('main')
        await side.frameLocator('iframe').getByRole('button').click(); await check('side', 0, 0)
      }
      // An app in main and thread in side obey the same rule.
      await page.evaluate(() => __focus.panels.swap())
      await side.locator('.composer [role=textbox]').focus(); await check('side')
      await main.frameLocator('iframe').getByRole('button').click(); await check('main', 0, 0)
      await page.evaluate(a => __focus.panels.openMain({ kind: 'thread', id: a }), a)
      await side.waitFor({ state: 'detached' }); await check('main')
      // Restore chooses the opened side, and a narrow viewport always paints
      // main as before even while its stored side has attention.
      await page.evaluate(b => __focus.panels.openSide({ kind: 'thread', id: b }), b)
      await side.locator('.composer [role=textbox]').waitFor(); await check('side')
      await page.reload(); await side.locator('.roundmic').waitFor(); await check('side', 0, 1)
      await page.emulateMedia({ reducedMotion: 'no-preference' })
      await side.getByRole('button', { name: 'Close side panel', exact: true }).click()
      await side.waitFor({ state: 'detached' }); await check('main', 0, 1)
      await page.evaluate(b => __focus.panels.openSide({ kind: 'thread', id: b }), b)
      await side.locator('.roundmic').waitFor(); await check('side', 0, 1)
      await page.setViewportSize({ width: 1100, height: 1000 }); await side.waitFor({ state: 'detached' }); await check('main', 0, 1)
      await page.setViewportSize({ width: 390, height: 844 }); await page.locator('.roundmic').waitFor(); await settle()
      assert.equal(await page.locator('[data-panel=side]').count(), 0)
      assert.equal(await page.locator('.roundmic').evaluate(n => getComputedStyle(n).backgroundColor), await page.evaluate(() => {
        const n = document.createElement('span'); n.style.color = 'var(--latte-foam)'; document.body.append(n); const c = getComputedStyle(n).color; n.remove(); return c
      }), 'phone retains its secondary microphone')
      assert.deepEqual(errors, [])
      console.log(`PASS 1440 ${mode}: both pointer directions, keyboard, close, file/app in either slot, iframe re-entry, restore, narrow/phone, motion, geometry and rail`)
    } finally { await context.close(); await app.close() }
  }
  if (out) await writeFile(path.join(out, 'measurements.json'), '[\n' + reports.map(row => JSON.stringify(row)).join(',\n') + '\n]\n')
} finally { await browser.close() }
