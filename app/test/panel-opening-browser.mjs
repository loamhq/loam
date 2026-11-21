// Exercise the gap between choosing an item and resolving its host metadata.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { filesHost } from './files-host.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const baseline = Boolean(process.env.PANEL_ITEMS_BASELINE), out = process.env.PANEL_ITEMS_EVIDENCE, reports = []
const source = await readFile('public/app.js', 'utf8')
if (out) await mkdir(out, { recursive: true })
try {
  for (const width of [1440, 1920]) for (const kind of ['file', 'app']) {
    const app = await oneProductFixture(), context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: 'reduce', serviceWorkers: 'block' })
    const page = await context.newPage(), errors = []
    page.on('pageerror', error => errors.push(error.message))
    let release, requested = false
    const gate = new Promise(resolve => { release = resolve })
    const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    for (const [key, handler] of Object.entries(filesHost().routes)) if (key.includes('/v1/files/')) app.routes[key] = handler
    const stat = app.routes['GET /v1/files/stat']
    app.routes['GET /v1/files/stat'] = async (...args) => { requested = true; await gate; return stat(...args) }
    app.routes['GET /v1/apps'] = async (req, res) => { requested = true; await gate; json(res, { apps: [{ name: 'Garden', url: 'https://garden.example.com/', frame: true, source: 'yours', access: 'private' }], sources: ['yours'] }) }
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__opening = { panels, threads, page, state };' }))
    await context.route('https://garden.example.com/**', route => route.fulfill({ contentType: 'text/html', body: '<h1>Garden</h1><p>The selected app opened.</p>' }))
    const capture = async phase => {
      if (out) await page.screenshot({ path: path.join(out, `${baseline ? 'before' : 'after'}-opening-${kind}-${phase}-${width}.png`), animations: 'disabled' })
    }
    try {
      await page.goto(`${app.url}/threads/a-quieter-week`)
      await page.waitForFunction(() => globalThis.__opening && !__opening.threads.st.open.loading)
      await page.evaluate(kind => {
        if (kind === 'file') __opening.page.goFiles('vault', 'Notes/plan.md')
        else __opening.panels.openMain({ kind: 'app', url: 'https://garden.example.com/' })
      }, kind)
      for (let attempt = 0; !requested && attempt < 1000; attempt++) await new Promise(resolve => setTimeout(resolve, 10))
      assert.equal(requested, true, 'the host metadata request started')
      await capture('pending')
      await page.locator('.rail-tabs [title=Chats]').click()
      const response = page.waitForResponse(response => response.url().includes(kind === 'file' ? '/api/files/stat?' : '/api/apps'))
      release(); await (await response).finished()
      await page.evaluate(async () => { await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      if (baseline) {
        await page.waitForFunction(() => __opening.state.screen === 'home')
        assert.equal(await page.locator('[data-panel=main] .fs-file,[data-panel=main] iframe').count(), 0)
      } else {
        await page.locator(kind === 'file' ? '[data-panel=main] .fs-preview' : '[data-panel=main] iframe').waitFor()
        assert.equal(await page.evaluate(() => __opening.state.mainItem.kind), kind)
        assert.equal(await page.evaluate(() => __opening.state.navigator), 'chats')
      }
      await capture('settled')
      if (!baseline && kind === 'app' && width === 1440) {
        await page.evaluate(() => __opening.page.goChats())
        await page.locator('.rail-tabs [title=Apps]').click()
        await page.locator('[data-panel=main] iframe').waitFor()
        assert.equal(await page.evaluate(() => __opening.state.mainItem.kind), 'app', 'returning to Apps keeps the mounted app')
        await page.evaluate(() => localStorage.setItem('loam.panels', JSON.stringify({ main: { kind: 'app', url: 'https://garden.example.com/' }, side: { kind: 'thread', id: 'small-details' }, split: 61, sidebar: true })))
        await page.goto(app.url + '/')
        await page.waitForFunction(() => globalThis.__opening)
        await page.locator('[data-panel=main] iframe').waitFor()
        await page.locator('[data-panel=side] .tt-thread').waitFor()
        assert.equal(new URL(page.url()).pathname, '/apps')
        assert.equal(await page.evaluate(() => __opening.panels.st.split), 61)
        await page.goto(app.url + '/')
        await page.waitForFunction(() => globalThis.__opening)
        assert.equal(await page.evaluate(() => __opening.state.mainItem), null)
        assert.equal(await page.locator('[data-panel=side]').count(), 0)
        console.log('1440 migration: legacy app/side converted to addresses once; later root navigation stays empty')
      }
      assert.deepEqual(errors, [])
      reports.push({ width, kind, result: baseline ? 'reproduced: rail cancelled the item opening' : 'passed: rail retained the item opening' })
      console.log(`${width} ${kind}: ${reports.at(-1).result}`)
    } finally { release(); await context.close(); await app.close() }
  }
  if (out) await writeFile(path.join(out, `${baseline ? 'before' : 'after'}-opening.json`), JSON.stringify(reports, null, 2) + '\n')
} finally { await browser.close() }
