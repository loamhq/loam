// Compare old looks with the starting tree, in one browser/context per case.
// The old two-theme catalog isolates rendering from the intentional new cards.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
const baseline = process.env.THEME_BASELINE_ROOT
const fullCatalog = process.env.REGRESSION_CATALOG === 'all'
assert.ok(baseline, 'Set THEME_BASELINE_ROOT to a git archive of the starting commit.')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const sharp = createRequire(path.join(process.env.LOAM_IMAGE_DEPS || path.join(process.env.TMPDIR, 'deps'), 'package.json'))('sharp')
const out = process.env.REGRESSION_EVIDENCE || path.join(process.env.TMPDIR, 'regression')
await mkdir(out, { recursive: true })
const fixed = Date.parse('2026-11-13T20:00:00Z'), OriginalDate = Date
globalThis.Date = class extends OriginalDate { constructor(...args) { super(...(args.length ? args : [fixed])) } static now() { return fixed } }
const apps = []
for (const root of [baseline, process.env.THEME_COMPARISON_ROOT || process.cwd()]) {
  const { oneProductFixture } = await import(pathToFileURL(path.join(root, 'test/one-product-fixture.mjs')))
  const { shippedPlugins } = await import(pathToFileURL(path.join(root, 'src/shipped-plugins.js')))
  const app = await oneProductFixture(), values = { phone: {}, desktop: {} }
  const rows = (await shippedPlugins(path.join(root, 'public'))).filter(r => fullCatalog ? r.kind.includes('theme') : ['latte', 'plain'].includes(r.id))
  const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
  app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
  app.routes['GET /v1/plugins'] = (req, res) => json(res, { plugins: rows })
  apps.push({ ...app, values, source: await readFile(path.join(root, 'public/app.js'), 'utf8') })
}
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] }), results = []
try {
  for (const width of (process.env.REGRESSION_WIDTHS || '390,1440').split(',').map(Number)) for (const mode of ['light', 'dark']) for (const look of ['latte', 'plain']) {
    const kind = width === 390 ? 'phone' : 'desktop'
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce', locale: 'en-US', timezoneId: 'UTC' })
    await context.addInitScript(({ fixed }) => { const Original = Date; globalThis.Date = class extends Original { constructor(...args) { super(...(args.length ? args : [fixed])) } static now() { return fixed } } }, { fixed })
    const page = await context.newPage(); page.setDefaultTimeout(15000)
    const captured = new Map()
    const metrics = new Map()
    try {
      for (const [i, app] of apps.entries()) {
        app.values[kind] = { mode, theme: look, size: 100 }
        await context.unroute('**/app.js')
        await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: app.source + '\nglobalThis.__regression={threads,goSettings};' }))
        await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
        const shot = async surface => {
          await page.mouse.move(width - 1, 1)
          await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
          const name = `${look}-${width}-${mode}-${surface}`, file = `${i ? 'after' : 'before'}-${name}.png`
          const measured = await page.evaluate(() => {
            const root = getComputedStyle(document.documentElement)
            const tokens = Object.fromEntries([...root].filter(k => k.startsWith('--')).sort().map(k => [k, root.getPropertyValue(k).trim()]))
            const components = Object.fromEntries(['.railtab.on', '.tt-title', '.tt-message .md', '#draft', '.tt-board-item', '.theme-preview[data-look="latte"]', '.theme-preview[data-look="plain"]'].map(selector => {
              const el = document.querySelector(selector); if (!el) return [selector, null]
              const s = getComputedStyle(el), r = el.getBoundingClientRect()
              return [selector, { color: s.color, background: s.backgroundColor, font: s.font, radius: s.borderRadius, x: r.x, y: r.y, width: r.width, height: r.height }]
            }))
            return { tokens, components }
          })
          if (!i) metrics.set(surface, measured)
          else assert.deepEqual(measured, metrics.get(surface), `Computed tokens/geometry changed: ${name}`)
          const bytes = await page.screenshot({ path: path.join(out, file), animations: 'disabled' })
          const raw = await sharp(bytes).ensureAlpha().raw().toBuffer()
          if (!i) captured.set(surface, raw)
          else {
            const before = captured.get(surface)
            let pixels = 0
            for (let p = 0; p < raw.length; p += 4) if (!raw.subarray(p, p + 4).equals(before.subarray(p, p + 4))) pixels++
            results.push({ name, pixels, computedEqual: true, before: `before-${name}.png`, after: file })
          }
        }
        await shot('list')
        await page.evaluate(() => __regression.threads.open('a-quieter-week'))
        await page.waitForFunction(() => !__regression.threads.st.open.loading && !__regression.threads.st.open.refreshing)
        await page.locator('.tt-message').first().waitFor(); await shot('thread')
        await page.getByRole('button', { name: 'Board', exact: true }).click(); await page.locator('.tt-board-item').first().waitFor(); await shot('board')
        await page.getByRole('button', { name: 'Edit board', exact: true }).click(); await page.getByRole('button', { name: 'Done', exact: true }).waitFor(); await shot('done')
        await page.getByRole('button', { name: 'Done', exact: true }).click()
        await page.evaluate(() => __regression.goSettings('appearance')); await page.getByRole('group', { name: 'Theme', exact: true }).waitFor()
        await page.locator('.theme-cover').evaluateAll(nodes => Promise.all(nodes.map(n => { n.loading = 'eager'; return n.decode() }))); await shot('appearance')
      }
    } finally { await context.close() }
  }
} finally {
  await writeFile(path.join(out, 'comparison.json'), JSON.stringify({ browser: browser.version(), catalog: fullCatalog ? 'Full installed theme catalog on both sides.' : 'Original two-theme catalog on both sides; new cards checked separately.', results }, null, 2) + '\n')
  await browser.close(); await Promise.all(apps.map(app => app.close()))
}
console.log(`${results.filter(r => !r.pixels).length}/${results.length} pixel-identical before/after captures.`)
assert.ok(results.every(r => r.pixels === 0), JSON.stringify(results.filter(r => r.pixels)))
