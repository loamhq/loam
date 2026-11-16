import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { u4Fixture } from './release-u4-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.U5_EVIDENCE || `${process.env.TMPDIR}/u5`
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8'), claim = await readFile('public/claim.html', 'utf8'), reports = []
const policy = claim.match(/http-equiv="Content-Security-Policy" content="([^"]+)"/)[1]
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await u4Fixture(), errors = [], external = [], claimRequests = [], updates = []
    const data = { current: { kind: 'development', version: null, local_commits: 609 }, check: { error: { code: 'no-version', message: 'There is no manifest.json yet.' } }, running: false, run: null, rollback: null }
    for (const method of ['GET', 'POST']) app.routes[`${method} /v1/update${method === 'POST' ? '/check' : ''}`] = (req, res) => {
      updates.push(req.method); res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(data))
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 960 }, colorScheme: mode, serviceWorkers: 'block', reducedMotion: 'reduce' })
    const page = await context.newPage()
    let onClaim = true
    await context.route('**/*', async route => {
      const url = new URL(route.request().url())
      if (url.origin !== app.url) { external.push(url.href); return route.abort() }
      if (onClaim) claimRequests.push(url.pathname)
      if (url.pathname === '/claim') return route.fulfill({ contentType: 'text/html', headers: { 'Content-Security-Policy': policy + "; frame-ancestors 'none'" }, body: claim })
      if (url.pathname === '/app.js') return route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__u5={state,goSettings};' })
      return route.continue()
    })
    page.on('pageerror', e => errors.push(e.message))
    page.on('console', e => { if (e.type() === 'error') errors.push(e.text()) })
    const shot = async name => { await page.screenshot({ path: `${out}/${name}-${width}-${mode}.png`, animations: 'disabled' }); console.log(`${width} ${mode}: ${name}`) }
    const recheck = async () => {
      await Promise.all([
        page.waitForResponse(response => response.url() === app.url + '/api/update/check'),
        page.getByRole('button', { name: 'Check for updates', exact: true }).click(),
      ])
      // Settings paints on animation frames; a completed HTTP response can
      // precede both its busy frame and its settled frame.
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
      await page.getByRole('button', { name: 'Check for updates', exact: true }).waitFor()
    }
    const fits = async selector => assert.equal(await page.locator(selector).evaluate(el => {
      const r = el.getBoundingClientRect()
      return r.left >= 0 && r.right <= innerWidth && el.scrollWidth <= el.clientWidth + 1
    }), true, `${selector} fits at ${width} ${mode}`)
    try {
      await page.goto(app.url + '/claim#fixture-token')
      await page.evaluate(() => document.fonts.ready)
      const fonts = await page.evaluate(() => ({
        faces: [...document.fonts].map(face => ({ family: face.family, status: face.status, weight: face.weight })),
        body: getComputedStyle(document.body).fontFamily,
        heading: getComputedStyle(document.querySelector('h1')).fontFamily,
        button: getComputedStyle(document.querySelector('.primary')).fontFamily,
      }))
      assert.equal(fonts.faces.length, 2)
      for (const face of fonts.faces) assert.equal(face.status, 'loaded')
      assert.match(fonts.body, /^Figtree/); assert.match(fonts.heading, /^Caprasimo/); assert.match(fonts.button, /^Caprasimo/)
      assert.equal(new URL(page.url()).hash, '')
      await fits('.claim'); await fits('h1'); await shot('claim')
      assert.deepEqual(claimRequests, ['/claim'], 'claim makes no asset requests')
      onClaim = false
      await page.goto(app.url)
      await page.waitForFunction(() => globalThis.__u5?.state.agentsReady)
      await page.evaluate(() => __u5.goSettings('signin'))
      const passkey = page.getByRole('link', { name: 'Add a passkey', exact: true })
      await passkey.waitFor(); await page.evaluate(() => document.fonts.ready)
      const label = await passkey.evaluate(el => {
        const box = el.getBoundingClientRect(), range = document.createRange(); range.selectNodeContents(el)
        const text = range.getBoundingClientRect()
        return { delta: Math.abs((text.left + text.right - box.left - box.right) / 2), width: box.width, height: box.height }
      })
      assert.ok(label.delta < 1, `passkey label centre differs by ${label.delta}px`)
      assert.ok(label.height >= 44)
      await fits('.passkey-card'); await shot('passkey')
      await page.evaluate(() => __u5.goSettings('update'))
      await page.getByText('Development build', { exact: true }).waitFor()
      await page.getByRole('button', { name: 'Check for updates', exact: true }).waitFor({ state: 'visible' })
      await page.waitForFunction(() => !document.querySelector('.update-version button').disabled)
      assert.equal(await page.getByText('609 local commits', { exact: true }).count(), 1)
      assert.equal(await page.getByText('No numbered release is installed yet. You can keep using Loam as usual.', { exact: true }).count(), 1)
      assert.equal(await page.locator('.update-problem, .update-release, .update-rollback, .update-progress').count(), 0)
      await fits('.update-control'); await fits('.update-number'); await shot('update-development')
      const initialReads = updates.length
      await recheck()
      assert.equal(updates.length, initialReads + 2, 'manual check reads state and checks once')
      // Missing numbers remain unknown, and one commit is singular.
      delete data.current.local_commits
      await recheck()
      assert.doesNotMatch(await page.locator('.update-version').innerText(), /local commits/)
      data.current.local_commits = 1
      await recheck()
      await page.getByText('1 local commit', { exact: true }).waitFor()
      data.current.version = '1.0.0'; data.check = null
      await recheck()
      await page.getByText('Version 1.0.0', { exact: true }).waitFor()
      assert.equal(await page.getByText('1 local commit · development', { exact: true }).count(), 1)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      assert.deepEqual(errors, []); assert.deepEqual(external, [])
      reports.push({ width, mode, fonts, claimRequests, passkey: label, development: true, manualCheck: true, unknownCount: true, singularCount: true, versionRetained: true, errors, external })
    } catch (error) { await shot('failure'); throw error }
    finally { await context.close(); await app.close() }
  }
  await writeFile(`${out}/results.json`, JSON.stringify(reports, null, 2))
} finally { await browser.close() }
