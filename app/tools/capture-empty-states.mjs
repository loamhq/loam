// Captures of the app's one empty state (2026-11-18, public/empty-state.js)
// at 390 and 1440, light and dark, in Latte and in Autumn, against the
// fixture app (test/market-fixture.mjs: invented records, no person's data):
// the empty Market, the build-it state under results, a Market search with
// no match, no threads, a thread search with no match, an empty board, and
// Files with nothing open and no apps; and a specimen sheet of every object
// at every size (EMPTY_THEMES=all: in every shipped theme).
//
//   EMPTY_EVIDENCE=<folder> LOAM_PLAYWRIGHT=<playwright/index.mjs> LOAM_CHROME=<chromium> node tools/capture-empty-states.mjs
//
// EMPTY_ONLY=market-empty,board-empty narrows it to some surfaces. Fails on
// a page error, sideways overflow, or an empty state missing its drawing.
import assert from 'node:assert/strict'
import { readFile, readdir, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { marketFixture } from '../test/market-fixture.mjs'

const out = process.env.EMPTY_EVIDENCE
if (!out) throw new Error('Set EMPTY_EVIDENCE')
await mkdir(out, { recursive: true })
const only = process.env.EMPTY_ONLY ? new Set(process.env.EMPTY_ONLY.split(',')) : null
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8')
const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }

const noThreads = app => {
  app.routes['GET /v1/threads'] = async (req, res) => json(res, { ...(await app.f.api('GET', '/threads')), threads: [] })
  app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
}

// Each surface: how to set the fixture up, and how to reach the state.
const SURFACES = {
  'market-empty': { listings: [], go: async page => { await page.evaluate(() => __cap.page.openMarket({})); await page.locator('.market-build-whole .empty-state-sprout').waitFor() } },
  'market-build-under-results': { go: async page => {
    await page.evaluate(() => __cap.page.openMarket({ q: 'write' }))
    await page.locator('.market-best').waitFor()
    await page.locator('.market-build .empty-state-sprout').waitFor()
    await page.evaluate(() => document.querySelector('.market-build').scrollIntoView({ block: 'center' }))
  } },
  'market-no-results': { go: async page => { await page.evaluate(() => __cap.page.openMarket({ q: 'a zebra that tells jokes' })); await page.locator('.market-build .empty-state-search').waitFor() } },
  'no-threads': { setup: app => { noThreads(app) }, home: '.empty-state-speech',
    go: async page => { await page.locator('.empty-state-speech').first().waitFor() } },
  'threads-no-match': { go: async page => {
    await page.getByRole('button', { name: 'Search threads', exact: true }).click()
    await page.getByRole('searchbox', { name: 'Find a thread' }).fill('zebra')
    await page.locator('.empty-state-search').waitFor()
  } },
  'board-empty': { setup: async app => { const t = await app.f.api('POST', '/threads', {}); app.addThread(t.id); return t.id },
    go: async (page, app, id) => { await page.goto(`${app.url}/threads/${id}`); await page.locator('.tt-board-button').click(); await page.locator('.tt-board .empty-state-small').waitFor() } },
  'files-idle': { wide: true, go: async page => { await page.evaluate(() => __cap.page.goFiles('vault', '')); await page.locator('.pane .empty-state-folder').waitFor() } },
  'apps-empty': { setup: app => { app.routes['GET /v1/apps'] = (req, res) => json(res, { apps: [], sources: ['yours'] }) },
    go: async page => { await page.evaluate(() => __cap.page.goApps()); await page.locator('.empty-state-window').waitFor() } },
  // Every object at every size, on the pane and on the page, in one theme:
  // run across every shipped theme by EMPTY_THEMES=all.
  'specimen': { wide: true, go: async page => {
    await page.evaluate(async () => {
      const { emptyState, EMPTY_OBJECTS } = await import('/empty-state.js')
      const sheet = document.createElement('div'); sheet.className = 'specimen'
      Object.assign(sheet.style, { position: 'fixed', inset: '0', zIndex: '99', display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', alignContent: 'start', gap: '0', overflow: 'auto', background: 'var(--paper-page)' })
      for (const [i, object] of EMPTY_OBJECTS.entries()) {
        const cell = emptyState({ object, size: i ? 'small' : 'pane', title: object[0].toUpperCase() + object.slice(1), body: 'One line of words.' })
        Object.assign(cell.style, { background: i % 2 ? 'var(--paper-page)' : 'var(--paper-pane)', padding: '28px 8px' })
        sheet.append(cell)
      }
      document.body.append(sheet)
    })
    await page.locator('.specimen .empty-state').first().waitFor()
  } },
}

const THEMES = (await readdir('public/themes', { withFileTypes: true })).filter(d => d.isDirectory()).map(d => d.name)
const files = [], failed = []
try {
  for (const [surface, spec] of Object.entries(SURFACES)) {
    if (only && !only.has(surface)) continue
    const looks = surface === 'specimen' && process.env.EMPTY_THEMES === 'all' ? THEMES : ['latte', 'autumn']
    for (const look of looks) for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
      if (spec.wide && width === 390) continue
      const app = await marketFixture({ mode, look, ...(spec.listings ? { listings: spec.listings } : {}) }), errors = []
      const extra = await spec.setup?.(app)
      const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, timezoneId: 'America/Los_Angeles' })
      await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__cap={page,state,schedule};' }))
      const page = await context.newPage(); page.setDefaultTimeout(15000)
      await page.clock.setFixedTime(new Date('2026-11-18T21:30:00Z'))
      page.on('pageerror', e => errors.push(e.message))
      try {
        await page.goto(app.url)
        try {
          await page.waitForFunction(() => globalThis.__cap)
          await page.locator(spec.home ?? '.tt-row').first().waitFor()
          await spec.go(page, app, extra)
        } catch (e) {
          await page.screenshot({ path: path.join(out, `FAILED-${surface}-${look}-${width}-${mode}.png`) })
          failed.push(`${surface} ${look} ${width} ${mode}: ${e.message.split('\n')[0]}`); continue
        }
        await page.mouse.move(0, 0)
        await page.evaluate(async () => { document.activeElement?.blur(); await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
        await page.waitForTimeout(1900) // the sprout's stem and leaves, to rest
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${surface} ${look} ${width} ${mode}: sideways overflow`)
        assert.ok(await page.locator('.empty-state .empty-state-art .es-blob').count() >= 1, `${surface}: the blob is drawn`)
        const file = `${surface}-${look}-${width}-${mode}.png`
        await page.screenshot({ path: path.join(out, file) })
        files.push(file)
        assert.deepEqual(errors, [], `${surface} ${look} ${width} ${mode}: page errors`)
        console.log(`PASS ${surface} ${look} ${width} ${mode}`)
      } finally { await context.close(); await app.close() }
    }
  }
} finally { await browser.close() }
for (const line of failed) console.log(`FAIL ${line}`)
console.log(`${files.length} captures in ${out}`)
if (failed.length) process.exitCode = 1
