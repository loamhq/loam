// Returning installations and an already-connected fresh install have different
// destinations. Exercise both without a browser-local onboarding marker.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { u4Fixture } from './release-u4-fixture.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.U4_EVIDENCE || `${process.env.TMPDIR}/u4`
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8'), reports = []
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) for (const fresh of [false, true]) {
    const app = await u4Fixture({ fresh }), errors = [], external = []
    app.u4.providers[0].connected = true
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 960 }, colorScheme: mode, serviceWorkers: 'block', reducedMotion: 'reduce' })
    await context.route('**/*', async route => {
      const url = new URL(route.request().url())
      if (url.origin !== app.url) { external.push(url.href); return route.abort() }
      if (url.pathname === '/app.js') return route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__u4={state,threads,goSettings,goProfile};' })
      return route.continue()
    })
    const page = await context.newPage()
    page.on('pageerror', e => errors.push(e.message)); page.on('console', e => { if (e.type() === 'error') errors.push(e.text()) })
    const shot = async name => { await page.screenshot({ path: `${out}/${name}-${width}-${mode}.png` }); console.log(`${width} ${mode}: ${name}`) }
    try {
      await page.goto(app.url); await page.waitForFunction(() => globalThis.__u4?.state.agentsReady)
      assert.equal(await page.locator('.welcome').count(), 0)
      if (fresh) {
        await page.locator('[data-message-id="hello-preset"]').waitFor(); await shot('connected-first-entry')
      } else {
        await page.getByText('A little room for the week', { exact: true }).first().waitFor(); await shot('existing-install')
        await page.evaluate(() => __u4.goProfile('sprout'))
        const newThread = page.locator('button.pillbtn.primary').filter({ hasText: 'New thread' })
        await newThread.waitFor(); assert.equal(await newThread.evaluate(node => node.firstElementChild.tagName.toLowerCase()), 'svg')
        await shot('profile-leading-plus')
        await page.evaluate(() => __u4.goSettings('agents'))
        const newAgent = page.getByRole('button', { name: 'New agent', exact: true })
        await newAgent.waitFor(); assert.equal(await newAgent.evaluate(node => node.firstElementChild.tagName.toLowerCase()), 'svg')
        await shot('agents-leading-plus')
        // A sign-out on an existing installation does not turn it into setup.
        for (const p of app.u4.providers) p.connected = false
        await page.goto(app.url); await page.waitForFunction(() => globalThis.__u4?.state.agentsReady)
        assert.equal(await page.locator('.welcome').count(), 0)
      }
      assert.deepEqual(errors, []); assert.deepEqual(external, [])
      reports.push({ width, mode, fresh, connected: true, errors, external })
    } finally { await context.close(); await app.close() }
  }
  await writeFile(`${out}/entry-results.json`, JSON.stringify(reports, null, 2))
} finally { await browser.close() }
