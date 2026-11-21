import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { previewPage } from './fixtures/app-previews/pages.mjs'
import { itemHref } from '../public/panel-items.js'

const baseline = process.env.APP_VIEWER_BASELINE, out = process.env.APP_VIEWER_EVIDENCE
const assets = ['app.js', 'apps.js', 'apps.css', 'panels.js', 'panels.css', 'page/side-panel.js', 'page/layout.js', 'page/registrations.js']
const original = baseline && assets.map(file => [file, execFileSync('git', ['show', `${baseline}:app/public/${file}`], { encoding: 'utf8' })])
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
if (out) await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const fieldNotes = '/x/field-notes/', longPath = '/x/observations/', longName = 'Observations'.repeat(14)
const thread = '/threads/a-quieter-week'
const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
const fixture = previewPage(1).replace('<strong>Atlas-bench</strong>', '<strong>Field notes</strong>')
  .replace(/(<button id="counter"[^>]*>[^<]+<\/button>)(.*?)(<span class="pill">[^<]+<\/span>)/, '$2$1')
  .replace('</style>', `@media(max-width:720px){header{padding:18px 22px}nav{gap:10px}nav span{display:none}main{padding:28px 22px}h1{font-size:44px}.hero,.cards{grid-template-columns:1fr;gap:16px}.art{height:240px}} @media(prefers-color-scheme:dark){body{background:#222c26;color:#e9e7d6}header{border-color:#ffffff22}nav,p{color:#c1c6b7}.art,.card{background:#2d3930;color:#e9e7d6;border-color:#ffffff22}button{background:#3a4b3c}}</style>`)

try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const phone = width === 390, look = { theme: 'latte', mode, size: 100 }
    const app = await oneProductFixture({}, { appMedia: { resolve: async app => ({ url: app.url }) } })
    const context = await browser.newContext({ viewport: { width, height: phone ? 844 : 900 }, colorScheme: mode,
      isMobile: phone, hasTouch: phone, reducedMotion: 'reduce', serviceWorkers: 'block', permissions: ['clipboard-read', 'clipboard-write'] })
    try {
      app.routes['GET /v1/settings/appearance'] = (_, res) => json(res, { phone: look, desktop: look })
      app.routes['GET /v1/apps'] = (_, res) => json(res, { sources: ['installed'], apps: [
        { url: fieldNotes, name: 'Field notes' }, { url: longPath, name: longName },
      ].map(row => ({ ...row, kind: 'plugin', source: 'installed', access: 'private', on: true, state: 'running' })) })
      app.routes['POST /v1/apps/open'] = (_, res) => json(res, { ok: true })
      const record = await app.f.api('GET', thread)
      record.messages.at(-1).text += `\n\n[Open the field journal](${itemHref({ kind: 'app', url: fieldNotes })})`
      app.routes[`GET /v1${thread}`] = (_, res) => json(res, record)
      await context.route(url => [fieldNotes, longPath].includes(url.pathname), route => route.fulfill({ contentType: 'text/html', body: fixture }))
      for (const [file, body] of original || []) await context.route(`${app.url}/${file}`, route => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body }))
      const page = await context.newPage(), errors = []
      page.on('pageerror', error => errors.push(error.message))
      page.setDefaultTimeout(8000)
      const button = (name, scope = page) => scope.getByRole('button', { name, exact: true })
      const main = phone ? page.locator('.apps-screen') : page.locator('[data-panel=main]')
      const side = page.locator('[data-panel=side]')
      const actions = page.getByRole('dialog', { name: 'App actions', exact: true })
      const counter = scope => scope.frameLocator('.apps-embed').locator('#counter')
      const nav = expected => page.locator('.nav').waitFor({ state: expected ? 'visible' : 'hidden' })
      const back = scope => button('Back', scope.locator('.apps-toolbar')).click()
      const openActions = async scope => { await button('App actions', scope).click(); await actions.waitFor() }
      const shot = async name => {
        if (!out) return
        await page.evaluate(() => document.fonts.ready)
        await page.screenshot({ path: join(out, `${baseline ? 'before' : 'after'}-${name}-${width}-${mode}.png`), animations: 'disabled' })
      }
      const oneRow = async (scope, long = false) => {
        const header = scope.locator('.apps-toolbar')
        const { height, count, aligned, clipped, nowrap } = await header.evaluate(node => {
          const [back, name, menu] = [...node.children]
          const [a, b, c] = [back, name, menu].map(n => n.getBoundingClientRect())
          const mid = r => r.y + r.height / 2
          return { height: node.offsetHeight, count: node.children.length, aligned: mid(a) === mid(c) && Math.abs(mid(a) - mid(b)) <= 1 && b.x >= a.right && c.x >= b.right,
            clipped: name.scrollWidth > name.clientWidth, nowrap: getComputedStyle(name).textOverflow === 'ellipsis' && getComputedStyle(name).whiteSpace === 'nowrap' }
        })
        assert.deepEqual({ height, count, aligned, clipped, nowrap }, { height: 64, count: 3, aligned: true, clipped: long, nowrap: true })
      }

      await page.goto(app.url)
      await button('Apps').click()
      await button('Open Field notes').click()
      await counter(main).waitFor()
      await nav(false)
      await shot('viewer')
      if (!baseline) {
        assert.equal(await main.locator('.apps-frame-name').textContent(), 'Field notes')
        await oneRow(main)
        await openActions(main)
        assert.equal(await actions.getAttribute('data-menu-layout'), phone ? 'sheet' : 'anchored')
        await shot('menu')
        await button('Copy link', actions).click()
        await actions.getByRole('status').getByText('Copied', { exact: true }).waitFor()
        assert.equal(await page.evaluate(() => navigator.clipboard.readText()), app.url + fieldNotes)
        const popupPromise = page.waitForEvent('popup')
        await actions.getByRole('link', { name: 'Open in browser', exact: true }).click()
        const popup = await popupPromise
        assert.equal(popup.url(), app.url + fieldNotes)
        await popup.close()
        await page.bringToFront()
        await actions.waitFor({ state: 'detached' })
        await openActions(main)
        const { x, y, width: w, height: h } = await counter(main).boundingBox()
        if (phone) await page.touchscreen.tap(x + w / 2, y + h / 2)
        else await page.mouse.click(x + w / 2, y + h / 2)
        await actions.waitFor({ state: 'detached' })
        assert.equal(await counter(main).textContent(), '0 saved')
        assert.equal(await button('App actions', main).evaluate(node => node === document.activeElement), true)
        await counter(main).click()
        assert.equal(await counter(main).textContent(), '1')
        await back(main)
        await main.locator('.apps-embed').waitFor({ state: 'detached' })
        await button('Open Field notes').waitFor()
        await nav(phone)
        await button(`Open ${longName}`).click()
        await counter(main).waitFor(); await oneRow(main, true)
        await back(main)
        await button('Open Field notes').waitFor()
      }

      await page.goto(app.url + thread)
      const chatLink = scope => scope.getByRole('link', { name: 'Open the field journal', exact: true })
      await chatLink(page).waitFor()
      if (!baseline) {
        await chatLink(page).click(); await counter(main).waitFor()
        await nav(false); await back(main)
        await chatLink(page).waitFor()
        assert.equal(new URL(page.url()).pathname, thread)
        await nav(false)
      }
      if (!phone) {
        // The same chat in the side slot gives its link a side-panel origin.
        await page.goto(`${app.url}/?side=${encodeURIComponent(JSON.stringify({ kind: 'thread', id: 'a-quieter-week' }))}`)
        await chatLink(side).click(); await counter(side).waitFor()
        await shot('side')
        if (baseline) {
          await button('Item actions', side).click(); await actions.waitFor(); await shot('side-menu')
        } else {
          await oneRow(side)
          await openActions(side); await shot('side-menu')
          await page.keyboard.press('Escape'); await actions.waitFor({ state: 'detached' })
          await back(side); await chatLink(side).waitFor()
          await chatLink(side).click(); await counter(side).waitFor()
          await openActions(side)
          await button('Close side panel', actions).click()
          await side.waitFor({ state: 'detached' })
        }
      }
      assert.deepEqual(errors, [])
      console.log(`${width} ${mode} ${baseline ? 'baseline captured' : 'passed'}`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
