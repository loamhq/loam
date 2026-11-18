// A listing card in an agent's reply, in a real browser (2026-11-18), against
// the fixture broker (test/market-fixture.mjs, `cards`), at 390 and 1440,
// light and dark:
//
//   - an agent's card line is a card in its place in the words (one, and two
//     together), with the icon, name, maker, kind, one line and the reach;
//   - a line naming a listing the Market does not have is its words, plain;
//   - Install opens the Market's own install sheet over the thread (the
//     first POST reads, the second is the yes), and the card then reads
//     Installed; nothing is installed by drawing a card;
//   - the name opens the listing's page, whose way back names the thread and
//     returns to it (and so does Escape);
//   - no page error, no sideways scroll, colours in their families.
//
//   LOAM_PLAYWRIGHT=…/playwright/index.mjs LOAM_CHROME=/usr/bin/chromium-headless-shell node test/market-card-browser.mjs
//
// With MARKET_CARD_EVIDENCE=<folder> it also writes the captures, in Latte
// and Autumn (tools/capture-market-cards.mjs sets that up).
import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { marketFixture, CARD_MESSAGES, UNKNOWN_CARD } from './market-fixture.mjs'
import { inspectColourFamilies } from '../tools/colour-families.mjs'
import { auditControls, assertControls } from './control-style-guard.mjs'

const out = process.env.MARKET_CARD_EVIDENCE || null
if (out) await mkdir(out, { recursive: true })
const looks = out ? ['latte', 'autumn'] : ['latte']
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8')
const sideways = page => page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
const files = []

try {
  for (const look of looks) for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const label = `${look} ${width} ${mode}`
    const app = await marketFixture({ mode, look, cards: [...CARD_MESSAGES, ...(out ? [] : [UNKNOWN_CARD])] }), errors = []
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce', timezoneId: 'America/Los_Angeles' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__cap={page,state,schedule};' }))
    const page = await context.newPage(); page.setDefaultTimeout(15000)
    page.on('pageerror', e => errors.push(e.message))
    const shot = async (surface, target = null) => {
      if (!out) return
      await page.mouse.move(0, 0)
      if (target) await target.evaluate(n => n.scrollIntoView({ block: 'center' }))
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      await page.waitForTimeout(350)
      const file = `${surface}-${look}-${width}-${mode}.png`
      await page.screenshot({ path: path.join(out, file), animations: 'disabled' })
      files.push(file)
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.locator('.tt-row', { hasText: 'A little room for the week' }).first().click()
      const sync = page.locator('.market-card', { hasText: 'Pocket Sync' })
      await sync.locator('.market-card-act').waitFor()
      await page.waitForFunction(() => document.querySelectorAll('.market-reach').length >= 3)

      // One card, in its place: after the words before it, before the words after.
      const reply = page.locator('.tt-message[data-message-id="m6"]')
      assert.deepEqual(await reply.evaluate(m => [...m.querySelectorAll('.tt-prose-content, .market-card')].map(n => n.classList.contains('market-card') ? 'card' : 'words')), ['words', 'card', 'words'], `${label}: the card sits in its place`)
      assert.equal(await reply.locator('a[href*="modal=market"]').count(), 0, `${label}: the line is a card, not a link`)
      assert.equal(await sync.locator('.market-card-name').textContent(), 'Pocket Sync')
      assert.equal(await sync.locator('.market-card-kicker').textContent(), 'Market · Extension')
      assert.equal(await sync.locator('.market-card-maker').textContent(), 'Ada Park')
      assert.match(await sync.locator('.market-card-line').textContent(), /^Keeps your vault in step/)
      assert.equal(await sync.locator('.market-reach').textContent(), 'Talks to one service')
      assert.equal(await sync.locator('.market-card-act').textContent(), 'Install')
      assert.equal(await sync.locator('.market-tile').count(), 1)
      await shot('card-one', sync)

      // Two together, themes: Get, and their own colours in the tile.
      const quill = page.locator('.tt-message[data-message-id="m8"]')
      assert.deepEqual(await quill.locator('.market-card-name').allTextContents(), ['Moss', 'Tide'])
      assert.deepEqual(await quill.locator('.market-card-act').allTextContents(), ['Get', 'Get'])
      assert.deepEqual(await quill.locator('.market-reach').allTextContents(), ['Only colours and type', 'Only colours and type'])
      await page.waitForFunction(() => [...document.querySelectorAll('.tt-message[data-message-id="m8"] .market-tile')].every(n => n.dataset.look))
      await shot('card-two', quill.locator('.market-cards'))

      if (!out) {
        // A listing the Market does not have: its words, plain.
        const gone = page.locator('.tt-message[data-message-id="m9"]')
        assert.equal(await gone.locator('.market-card').count(), 0)
        assert.equal(await gone.locator('.market-card-plain').textContent(), 'Nightjar')
        assert.equal(await gone.locator('a').count(), 0)
      }
      assert.ok(!app.writes.some(w => w.method === 'POST'), `${label}: drawing a card installs nothing`)
      await page.evaluate(inspectColourFamilies).then(found => assert.deepEqual(found, [], `${label}: colour families`))
      assertControls(await auditControls(page, '.market-card .pillbtn'))
      assert.equal(await sideways(page), false, `${label}: no sideways scroll`)

      // Install: the Market's own sheet, over the thread; then Installed.
      await sync.locator('.market-card-act').click()
      await page.locator('.market-install .market-touch').first().waitFor()
      assert.equal(await page.locator('.market').count(), 0, `${label}: the sheet alone, over the thread`)
      assert.equal(await page.locator('.market-install .sheet-title').textContent(), 'Install Pocket Sync?')
      const posts = () => app.writes.filter(w => w.method === 'POST')
      assert.deepEqual(posts().map(p => p.body), [{ url: 'https://github.com/ada-park/pocket-sync/tree/v0.4.0/pocket-sync' }], `${label}: the first call reads`)
      await shot('card-install-sheet')
      await page.locator('.market-install .pillbtn.primary', { hasText: 'Install' }).click()
      await page.locator('.market-install-done').waitFor()
      assert.equal(posts().at(-1).body.confirm, 'ok-pocket-sync', `${label}: the second call is the yes`)
      await page.locator('.market-install .pillbtn.primary', { hasText: 'Done' }).click()
      await sync.locator('.market-card-state', { hasText: 'Installed' }).waitFor()
      await shot('card-after-install', sync)

      // The name: the listing's page; its way back is the thread.
      const title = await page.locator('.tt-message[data-message-id="m8"]').evaluate(() => document.title)
      await quill.locator('.market-card', { hasText: 'Moss' }).locator('.market-card-name').click()
      await page.locator('.market-listing-title', { hasText: 'Moss' }).waitFor()
      await page.locator('.market-touch').first().waitFor()
      assert.equal(await page.locator('.market-back').textContent(), 'A little room for the week', `${label}: the way back names the thread`)
      await page.evaluate(() => document.activeElement?.blur())
      await shot('card-listing')
      await page.locator('.market-back').click()
      await page.waitForFunction(() => !document.querySelector('.market'))
      assert.equal(await page.locator('.market-card').count() >= 3, true, `${label}: back in the thread`)
      await quill.locator('.market-card', { hasText: 'Tide' }).locator('.market-card-name').click()
      await page.locator('.market-listing-title', { hasText: 'Tide' }).waitFor()
      await page.keyboard.press('Escape')
      await page.waitForFunction(() => !document.querySelector('.market'))
      assert.equal(await page.evaluate(() => document.title), title, `${label}: Escape returns to the thread`)
      assert.deepEqual(errors, [], `${label}: page errors`)
      console.log(`PASS ${label}`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
if (out) console.log(`${files.length} captures in ${out}`)
console.log('market-card-browser: ok')
process.exit(0)
