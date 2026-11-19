// Captures of Loam's note on a listing's page, and of the reviewed listing's
// install sheet, at 390 and 1440, light and dark, in Latte and Autumn,
// against the fixture app with one reviewed listing added
// (test/market-fixture.mjs, REVIEWED: a note from the reviewer's test set).
// Also checks what must hold: the note opens the body above the maker's
// README (and any showcase or screenshots), says whose words it is, shows "New since" for an update, and the
// install asks for the commit that was reviewed.
//
//   MARKET_EVIDENCE=<folder> LOAM_PLAYWRIGHT=<playwright/index.mjs> LOAM_CHROME=<chromium> node tools/capture-market-note.mjs
import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { marketFixture, LISTINGS, REVIEWED } from '../test/market-fixture.mjs'

const out = process.env.MARKET_EVIDENCE
if (!out) throw new Error('Set MARKET_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8')
const files = []
try {
  for (const look of ['latte', 'autumn']) for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await marketFixture({ mode, look, listings: [...LISTINGS, REVIEWED] }), errors = []
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce', timezoneId: 'America/Los_Angeles' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__cap={page,state,schedule};' }))
    const page = await context.newPage(); page.setDefaultTimeout(15000)
    await page.clock.setFixedTime(new Date('2026-11-19T21:30:00Z'))
    page.on('pageerror', e => errors.push(e.message))
    const shot = async surface => {
      await page.mouse.move(0, 0)
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      await page.waitForTimeout(300)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${surface} ${look} ${width} ${mode}: sideways overflow`)
      const file = `${surface}-${look}-${width}-${mode}.png`
      await page.screenshot({ path: path.join(out, file), animations: 'disabled' })
      files.push(file)
    }
    const full = async surface => {
      const height = await page.evaluate(() => document.querySelector('.market-body').scrollHeight + 80)
      await page.setViewportSize({ width, height: Math.min(height, 8000) })
      await page.waitForTimeout(400)
      await shot(`${surface}-full`)
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate(() => __cap.page.openMarket({ id: 'focus-timer' }))
      await page.locator('.market-loam-note').waitFor()
      await page.locator('.market-readme').waitFor()
      const order = await page.evaluate(() => [...document.querySelector('.market-listing').children].map(n => n.className.split(' ')[0]))
      assert.ok(order.indexOf('market-listing-note-row') === order.indexOf('market-creator') + 1 && order.indexOf('market-listing-note-row') < order.indexOf('market-listing-body'), `the note follows the maker, above the rest: ${order}`)
      assert.match(await page.locator('.market-loam-note-sub').innerText(), /Loam’s review of version 1\.1\.1 · Nov 19, 2026\. Not the maker’s words\./)
      assert.equal(await page.locator('.market-note-changed .market-note-group-title').innerText(), 'NEW SINCE 1.0.0')
      await page.evaluate(() => document.activeElement?.blur())
      await shot('market-note')
      await page.locator('.market-loam-note').scrollIntoViewIfNeeded()
      await shot('market-note-scrolled')
      await full('market-note')
      // Install asks for the commit the review read.
      await page.evaluate(() => __cap.page.openMarket({ id: 'focus-timer', install: 'focus-timer' }))
      await page.locator('.market-install .pillbtn.primary:not([disabled])').waitFor()
      await shot('market-note-install')
      assert.equal(app.writes.filter(w => w.method === 'POST').at(-1).body.url, REVIEWED.pinned_link, 'installs the reviewed commit')
      assert.deepEqual(errors, [], `${look} ${width} ${mode}: page errors`)
    } finally { await context.close(); await app.close?.() }
  }
} finally { await browser.close() }
console.log(`${files.length} captures in ${out}`)
