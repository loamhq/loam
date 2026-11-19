// Captures of Settings › Market at 390 and 1440, light and dark, in Latte and
// in Autumn, against the fixture app (test/market-fixture.mjs: an invented
// list, no person's data): Settings with the Market row, the front page,
// the typeahead open, a results page, a listing page, a theme's page with
// its cover, README and maker's links, a theme with no cover wearing itself,
// the install sheet, the empty Market, and a theme being tried. The store
// (2026-11-18) adds whole-page captures (`-full`) of the front page, a
// theme's listing and an extension's, the front page with a photograph for
// its lead story, and a phone's listing with its buttons docked at the foot.
//
//   MARKET_EVIDENCE=<folder> LOAM_PLAYWRIGHT=<playwright/index.mjs> LOAM_CHROME=<chromium> node tools/capture-market.mjs
//
// Fails on a page error or sideways overflow on any surface.
import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { marketFixture, LISTINGS } from '../test/market-fixture.mjs'

const out = process.env.MARKET_EVIDENCE
if (!out) throw new Error('Set MARKET_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8')
const files = []
try {
  for (const look of ['latte', 'autumn']) for (const width of [390, 1440]) for (const mode of ['light', 'dark']) for (const variant of ['list', 'empty', 'photo']) {
    const empty = variant === 'empty'
    const app = await marketFixture({ mode, look, ...(empty ? { listings: [] } : variant === 'photo' ? { listings: LISTINGS.map(e => ({ ...e, featured: e.id === 'ember' })) } : {}) }), errors = []
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce', timezoneId: 'America/Los_Angeles' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__cap={page,state,schedule};' }))
    const page = await context.newPage(); page.setDefaultTimeout(15000)
    await page.clock.setFixedTime(new Date('2026-11-18T21:30:00Z'))
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
    // The whole page, as tall as the Market's own scroll.
    const full = async surface => {
      const height = await page.evaluate(() => document.querySelector('.market-body').scrollHeight + 80)
      await page.setViewportSize({ width, height: Math.min(height, 8000) })
      await page.waitForTimeout(400)
      await shot(`${surface}-full`)
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      if (variant === 'photo') {
        await page.evaluate(() => __cap.page.openMarket({}))
        await page.locator('.market-lead-photo img.market-cover-img').waitFor()
        await page.waitForFunction(() => document.querySelector('.market-lead-photo img.market-cover-img').complete)
        await page.evaluate(() => document.activeElement?.blur())
        await shot('market-front-photo')
        await full('market-front-photo')
      } else if (empty) {
        await page.evaluate(() => __cap.page.openMarket({}))
        await page.locator('.market-build-whole').waitFor()
        await shot('market-empty')
      } else {
        await page.evaluate(() => __cap.page.goSettings('root'))
        await page.locator('.market-door').waitFor()
        await shot('settings-market-row')
        await page.locator('.market-door').click()
        await page.locator('.market-lead').waitFor()
        await page.waitForFunction(() => [...document.querySelectorAll('.market-swatch')].every(n => n.querySelector('.theme-preview[data-look]') || n.querySelector('img')?.complete))
        await page.evaluate(() => document.activeElement?.blur())
        await shot('market-front')
        await full('market-front')
        const field = page.locator('.market-q')
        await field.click(); await field.fill('sync with my phone')
        await page.locator('.market-suggestion').first().waitFor()
        await field.press('ArrowDown')
        await page.locator('.market-suggestion.on').waitFor()
        await shot('market-typeahead')
        await field.fill('write'); await field.press('Enter')
        await page.locator('.market-best').waitFor()
        await page.evaluate(() => document.activeElement?.blur())
        await shot('market-results')
        await page.evaluate(() => __cap.page.openMarket({ id: 'images' }))
        await page.locator('.market-touch').first().waitFor()
        await page.waitForFunction(() => document.querySelector('.market-listing-head img')?.complete)
        await shot('market-listing')
        await full('market-listing')
        // A theme's page with its cover, README and maker's links, its
        // README scrolled into view; and a theme with no cover, wearing itself.
        await page.evaluate(() => __cap.page.openMarket({ id: 'moss' }))
        await page.locator('.market-readme').waitFor()
        await page.waitForFunction(() => [...document.querySelectorAll('.market-listing-cover img, .market-readme-img')].every(i => i.complete && i.naturalWidth))
        await shot('market-listing-theme')
        await page.waitForFunction(() => [...document.querySelectorAll('.market-readme-img')].every(i => i.complete && i.naturalWidth))
        await full('market-listing-theme')
        await page.evaluate(() => document.querySelector('.market-creator').scrollIntoView({ block: 'start' }))
        await page.waitForFunction(() => [...document.querySelectorAll('.market-readme-img')].every(i => i.complete && i.naturalWidth))
        await shot('market-listing-theme-readme')
        if (width === 390) {
          await page.evaluate(() => { const b = document.querySelector('.market-body'); b.scrollTop = b.scrollHeight * .45; b.dispatchEvent(new Event('scroll')) })
          await page.locator('.market-dock.on').waitFor()
          await page.waitForTimeout(300)
          await shot('market-listing-theme-docked')
        }
        await page.evaluate(() => __cap.page.openMarket({ id: 'tide' }))
        await page.locator('.market-listing-cover .market-preview-large[data-look="tide"]').waitFor()
        await shot('market-listing-no-cover')
        await page.evaluate(() => __cap.page.openMarket({ id: 'images' }))
        await page.locator('.market-touch').first().waitFor()
        await page.locator('.market-listing-head .market-listing-acts .pillbtn.primary').click()
        await page.locator('.market-install .market-touch').first().waitFor()
        await shot('market-install-sheet')
        await page.locator('.market-install .pillbtn', { hasText: 'Not now' }).click()
        await page.evaluate(() => __cap.page.openMarket({ kind: 'theme' }))
        await page.waitForFunction(() => document.querySelector('.market-theme .market-try') && !document.querySelector('.market-try:disabled'))
        await page.locator('.market-theme .market-try').nth(1).click()
        await page.waitForFunction(() => document.documentElement.dataset.look === 'moss')
        await page.evaluate(() => document.activeElement?.blur())
        await shot('market-trying-a-theme')
      }
      assert.deepEqual(errors, [], `${look} ${width} ${mode}: page errors`)
      console.log(`PASS ${look} ${width} ${mode} ${variant}`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
console.log(`${files.length} captures in ${out}`)
