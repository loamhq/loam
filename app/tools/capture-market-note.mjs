// Run with MARKET_EVIDENCE, LOAM_PLAYWRIGHT and LOAM_CHROME. Supply a JSON
// array of reviewed marketFixture listings in MARKET_NOTE_LISTINGS to check
// and photograph specific reviews, including their covers and maker content.
import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { marketFixture, LISTINGS, REVIEWED } from '../test/market-fixture.mjs'

const out = process.env.MARKET_EVIDENCE
if (!out) throw new Error('Set MARKET_EVIDENCE')
await mkdir(out, { recursive: true })
const reviewed = process.env.MARKET_NOTE_LISTINGS
  ? JSON.parse(await readFile(process.env.MARKET_NOTE_LISTINGS, 'utf8')) : [REVIEWED]
const looks = (process.env.MARKET_LOOKS || 'latte,autumn').split(',')
const entries = new Map([...LISTINGS, REVIEWED, ...reviewed].map(e => [e.id, e]))
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8')
let count = 0
try {
  for (const look of looks) for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await marketFixture({ mode, look, listings: [...entries.values()] })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    try {
      await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__openMarket = page.openMarket;' }))
      const page = await context.newPage()
      const errors = []
      page.on('pageerror', e => errors.push(e.message))
      await page.goto(app.url)
      await page.locator('.tt-row').first().waitFor()
      async function openListing(entry) {
        await page.evaluate(id => __openMarket({ id }), entry.id)
        await page.waitForFunction(name => document.querySelector('.market-listing-title')?.textContent === name, entry.name)
        await page.locator('.market-readme, .market-touches .market-touch-list').first().waitFor()
      }
      const read = page.locator('.market-note-link')
      const sheet = page.locator('.market-review-sheet')
      const scroll = () => page.locator('.market-body').evaluate(el => el.scrollTop)
      for (const entry of reviewed) {
        const note = entry.review.note
        const shot = async state => {
          await page.mouse.move(0, 0)
          await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
          await page.screenshot({ path: path.join(out, `${entry.id}${looks.length > 1 ? '-' + look : ''}-${width}-${mode}-${state}.png`), animations: 'disabled' })
          count++
        }
        await openListing(entry)
        await page.locator('.market-readme').waitFor()
        await page.locator('.market-listing-cover img').evaluateAll(imgs => Promise.all(imgs.map(i => i.decode())))
        assert.equal(await page.locator('.market-listing-head .market-note-link').count(), 1)
        assert.equal(await page.locator('.market-dock .market-note-link').count(), 0)
        assert.ok(await read.evaluate(el => el.scrollWidth <= el.clientWidth))
        const firstMakerContent = await page.locator('.market-creator').evaluate(el => el.nextElementSibling.className)
        assert.match(firstMakerContent, /theme-show|plugin-gallery|market-listing-body/)
        assert.equal(await page.locator('.market-note-link').evaluate(el => el.getBoundingClientRect().height >= 44), true)
        assert.equal(await sheet.count(), 0)
        await shot('listing')
        await page.locator('.market-listing-head').evaluate(el => {
          const scroller = el.closest('.market-body')
          scroller.scrollTop += el.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 20
        })
        await shot('maker')
        const position = await scroll()
        await read.click()
        await sheet.waitFor()
        assert.equal(await sheet.getAttribute('data-menu-layout'), width === 390 ? 'sheet' : 'anchored')
        if (width === 390) assert.ok(Math.abs((await sheet.boundingBox()).y + (await sheet.boundingBox()).height - 844) < 1)
        assert.equal(await sheet.locator('.market-loam-note-summary').textContent(), note.summary)
        assert.deepEqual(await sheet.locator('li').allTextContents(), ['changed', 'differences', 'touches', 'may_change', 'worth_knowing'].flatMap(k => note[k] ?? []))
        await shot('sheet')
        await sheet.locator('li').last().scrollIntoViewIfNeeded()
        assert.equal(await scroll(), position)
        await page.keyboard.press('Escape')
        await sheet.waitFor({ state: 'detached' })
        assert.equal(await scroll(), position)
        assert.ok(await read.evaluate(el => el === document.activeElement), 'focus returns to Read')
      }
      await openListing(REVIEWED)
      if (width === 1440) {
        // The outer button can fit even when its excerpt has been ellipsized.
        const layout = await read.evaluate(button => {
          const excerpt = button.querySelector('.market-note-excerpt')
          const range = document.createRange()
          range.selectNodeContents(excerpt)
          const box = button.getBoundingClientRect()
          const fragments = [...range.getClientRects()]
          return {
            rows: new Set(fragments.map(r => r.top)).size,
            visible: excerpt.scrollWidth <= excerpt.clientWidth && fragments.every(r =>
              r.left >= box.left && r.right <= box.right && r.top >= box.top && r.bottom <= box.bottom),
          }
        })
        assert.ok(layout.rows > 1 && layout.visible, 'the full update warning wraps within the desktop button')
      }
      await read.click()
      assert.equal(await sheet.locator('.market-note-changed h3').innerText(), 'New since 1.0.0')
      const documentBody = sheet.getByRole('document')
      await documentBody.focus()
      await page.keyboard.press('End')
      await page.waitForFunction(() => document.querySelector('.market-note-scroll').scrollTop > 0)
      await sheet.getByRole('button', { name: 'Close Loam’s note' }).click()
      await sheet.waitFor({ state: 'detached' })
      await read.click()
      await sheet.waitFor()
      await page.mouse.click(4, 4)
      await sheet.waitFor({ state: 'detached' })
      assert.equal(await page.locator('.market-listing-title').innerText(), REVIEWED.name)
      await page.locator('.market-listing-head .market-act').click()
      await page.locator('.market-install .pillbtn.primary:not([disabled])').waitFor()
      await page.screenshot({ path: path.join(out, `market-note-install-${look}-${width}-${mode}.png`), animations: 'disabled' })
      count++
      assert.equal(app.writes.filter(w => w.method === 'POST').at(-1).body.url, REVIEWED.pinned_link)
      await page.keyboard.press('Escape')
      await openListing(LISTINGS.find(e => e.id === 'moss'))
      assert.equal(await read.count(), 0)
      assert.equal(await sheet.count(), 0)
      assert.deepEqual(errors, [])
      console.log(`PASS ${width} ${mode}: ${reviewed.map(e => e.name).join(', ')}, update, pinned install, unreviewed`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
console.log(`${count} screenshots in ${out}`)
