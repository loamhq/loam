import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { acmePluginFixture } from './acme-plugin-fixture.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.U3_EVIDENCE || `${process.env.TMPDIR}/u3-primary`
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8'), reports = []
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await acmePluginFixture({ mode }), errors = [], external = []
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 940 }, serviceWorkers: 'block', colorScheme: mode, reducedMotion: 'reduce' })
    const page = await context.newPage()
    page.on('pageerror', e => errors.push(e.message))
    page.on('console', e => { if (e.type() === 'error') errors.push(e.text()) })
    await context.route('**/*', r => {
      const url = new URL(r.request().url())
      if (url.origin !== app.url) { external.push(url.href); return r.abort() }
      if (url.pathname === '/app.js') return r.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__primary={goSettings,pluginLoader};' })
      if (url.pathname === '/api/plugins/daily-notes') return r.fulfill({ json: { plugin: { on: false } } })
      if (url.pathname === '/api/files/roots') return r.fulfill({ json: { roots: [] } })
      return r.continue()
    })
    try {
      await page.goto(app.url)
      await page.waitForFunction(() => globalThis.__primary?.pluginLoader.st.rows.some(r => r.id === 'acme'))
      await page.evaluate(() => __primary.goSettings('integrations'))
      await page.getByRole('button', { name: /Acme Not connected/ }).click()
      const signIn = page.getByRole('button', { name: 'Sign in to Acme', exact: true })
      await signIn.waitFor()
      const paint = await signIn.evaluate(n => ({ primary: n.classList.contains('primary'), ghost: n.classList.contains('content-mark'), background: getComputedStyle(n).backgroundColor, height: n.getBoundingClientRect().height }))
      assert.equal(paint.primary, true); assert.equal(paint.ghost, false); assert.notEqual(paint.background, 'rgba(0, 0, 0, 0)'); assert.ok(paint.height >= 40)
      await page.screenshot({ path: `${out}/acme-primary-${width}-${mode}.png` })
      assert.deepEqual(errors, []); assert.deepEqual(external, [])
      reports.push({ width, mode, ...paint, errors, external })
    } finally { await context.close(); await app.close() }
  }
  await writeFile(`${out}/acme-results.json`, JSON.stringify(reports, null, 2))
} finally { await browser.close() }
