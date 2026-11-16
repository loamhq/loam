import { mergeAppearance } from '../public/theme-options.js'
// Actual first-paint, instant-switch and fresh-client checks with a local host.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { loadCoreTokens, validateThemeCSS } from '../src/themes.js'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.THEME_EVIDENCE || path.join(process.env.TMPDIR, 'theme-evidence')
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
const app = await oneProductFixture(), results = []
const values = { phone: { mode: 'system', size: 100, theme: 'latte' }, desktop: { mode: 'system', size: 100, theme: 'latte' } }
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => { for (const [kind, patch] of Object.entries(JSON.parse(raw))) values[kind] = mergeAppearance(values[kind], patch); json(res, values) }
const source = await readFile('public/app.js', 'utf8')
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) for (const look of ['latte', 'plain']) {
    const kind = width === 390 ? 'phone' : 'desktop'
    // The saved explicit mode must beat the opposite system preference.
    values[kind] = { mode, size: 100, theme: look }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode === 'dark' ? 'light' : 'dark', isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    try {
      await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__themesTest={theme,goSettings};' }))
      const page = await context.newPage(), errors = [], cssRequests = []
      page.on('pageerror', e => errors.push(e.message))
      page.on('request', r => { if (r.url().endsWith('.css')) cssRequests.push(r.url()) })
      await page.addInitScript(() => new PerformanceObserver(list => {
        if (list.getEntries().some(e => e.name === 'first-contentful-paint')) {
          const s = getComputedStyle(document.body)
          globalThis.__firstTheme = { mode: document.documentElement.dataset.theme, look: document.documentElement.dataset.look, ground: s.backgroundColor, font: s.fontFamily }
        }
      }).observe({ type: 'paint', buffered: true }))
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await page.waitForFunction(() => globalThis.__firstTheme)
      const ground = look === 'latte' ? mode === 'light' ? 'rgb(238, 234, 227)' : 'rgb(26, 25, 23)' : mode === 'light' ? 'rgb(232, 232, 232)' : 'rgb(30, 30, 30)'
      const first = await page.evaluate(() => __firstTheme)
      assert.deepEqual({ mode: first.mode, look: first.look, ground: first.ground }, { mode, look, ground })
      assert.match(first.font, look === 'latte' ? /Figtree/ : /system-ui/)
      await page.evaluate(() => __themesTest.goSettings('appearance'))
      await page.getByRole('group', { name: 'Theme', exact: true }).waitFor()
      const requests = cssRequests.length, switched = look === 'latte' ? 'plain' : 'latte'
      await page.getByRole('group', { name: 'Theme', exact: true }).getByRole('button', { name: switched === 'plain' ? 'Plain' : 'Latte & Newspaper', exact: true }).click()
      assert.equal(await page.evaluate(() => document.documentElement.dataset.look), switched)
      assert.equal(cssRequests.length, requests, 'switching must not fetch a stylesheet')
      await page.waitForFunction(() => !__themesTest.theme.status.saving)
      assert.equal(values[kind].theme, switched)
      await page.reload(); await page.locator('.tt-row').first().waitFor()
      assert.equal(await page.evaluate(() => document.documentElement.dataset.look), switched)
      assert.deepEqual(errors, [])
      results.push({ width, mode, look, first, switchWithoutFetch: true, freshNavigation: switched, errors })
    } finally { await context.close() }
  }
  // A partial extension can still intentionally change geometry. Serve the
  // validated override after the real bundle and measure a real core component.
  const override = ':root[data-look="example"] { --radius-md: 17px; }'
  validateThemeCSS(override, 'example', await loadCoreTokens())
  const context = await browser.newContext()
  try {
    await context.route('**/themes.css*', async route => {
      const response = await route.fetch()
      await route.fulfill({ response, body: await response.text() + '\n' + override })
    })
    const page = await context.newPage()
    await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
    for (const mode of ['light', 'dark']) {
      const measured = await page.evaluate(mode => {
        const root = document.documentElement
        root.dataset.look = 'example'; root.dataset.theme = mode
        const tile = document.createElement('div'); tile.className = 'icon-tile'; document.body.append(tile)
        const style = getComputedStyle(tile), result = { radius: style.borderRadius, width: style.width, fontSize: style.fontSize, background: style.backgroundColor }
        tile.remove(); return result
      }, mode)
      assert.deepEqual(measured, { radius: '17px', width: '42px', fontSize: '15px', background: mode === 'light' ? 'rgb(221, 221, 221)' : 'rgb(49, 49, 49)' })
    }
    console.log('PASS: partial extension overrides component radius in both modes; omitted colours and type inherit core defaults.')
  } finally { await context.close() }
  await writeFile(path.join(out, 'checks.json'), JSON.stringify(results, null, 2) + '\n')
  console.log(`PASS: ${results.length} first-paint cases, both widths/modes/themes; instant switches without requests; host persistence on fresh navigation; zero page errors.`)
} finally { await browser.close(); await app.close() }
