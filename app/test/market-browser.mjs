// Settings › Market in a real browser (2026-11-18), against the fixture
// broker (test/market-fixture.mjs), at 390 and 1440, light and dark:
//
//   - Settings has the Market row, apart at the end; Plugins and Appearance
//     end with "Find more", which opens the Market at that kind;
//   - the front page: masthead, search, kinds, the lead story, a section per
//     kind; the typeahead (arrows, Enter, Escape); a results page with the
//     best match large, the typed words marked, and build-it at the end; a
//     search that matches nothing is the build-it panel;
//   - an empty list is the masthead, the search and the build-it panel only;
//   - a listing's page, and the one install sheet: what it declares (the
//     first POST, no confirm), then the yes (the second, with it), then the
//     button reads Installed;
//   - Try it wears a theme, and leaving the Market takes it off;
//   - build-it opens a thread with the agent picked and the words unsent;
//   - the store (2026-11-18): kinds as text tabs, themes as photographs in
//     rows (two wide, then three tall; none alone), calm cards for the rest
//     (one alone takes the row), a lead story over its photograph, a
//     listing's page with the maker's row, the README beside a column of
//     facts, and on a phone the buttons docked at the foot once scrolled;
//   - no page error, no sideways scroll, colours in their families (Latte).
//
//   LOAM_PLAYWRIGHT=…/playwright/index.mjs LOAM_CHROME=/usr/bin/chromium-headless-shell node test/market-browser.mjs
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { marketFixture } from './market-fixture.mjs'
import { inspectColourFamilies } from '../tools/colour-families.mjs'
import { auditControls, assertControls } from './control-style-guard.mjs'
import { TAGLINE } from '../public/market.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8')

async function open(width, mode, options = {}) {
  const app = await marketFixture({ mode, ...options }), errors = []
  const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
  await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__cap={page,state,schedule};' }))
  const page = await context.newPage(); page.setDefaultTimeout(12000)
  page.on('pageerror', e => errors.push(e.message))
  await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
  return { app, page, errors, close: async () => { await context.close(); await app.close() } }
}
const sideways = page => page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
const families = async (page, label) => assert.deepEqual(await page.evaluate(inspectColourFamilies), [], `${label}: colour families`)

try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const label = `${width} ${mode}`
    const { app, page, errors, close } = await open(width, mode)
    try {
      // Settings: the Market's own row, last.
      await page.evaluate(() => __cap.page.goSettings('root'))
      const door = page.locator('.market-door')
      await door.waitFor()
      const rows = await page.locator(width >= 1100 ? '.setnav .navrow' : '.settings .setrow-title').allTextContents()
      assert.equal(rows.at(-1).trim(), 'Market', `${label}: Market is the last row`)
      await door.click()
      await page.locator('.market-title').waitFor()
      assert.equal(await page.locator('.market-back').textContent(), 'Settings', `${label}: a way back to Settings`)
      await page.locator('.market-lead').waitFor()
      assert.deepEqual(await page.locator('.market-section h2').allTextContents(), ['Themes', 'Extensions', 'Apps', 'Agents', 'Skills'])
      assert.equal(await page.locator('#market-lead-title').textContent(), 'Images')
      assert.deepEqual((await page.locator('.market-kinds .market-kind').allTextContents()), ['Everything', 'Themes', 'Extensions', 'Apps', 'Agents', 'Skills'])
      assert.equal(await page.locator('.market-kind.on').textContent(), 'Everything')
      assert.equal(await page.locator('.market-tagline').textContent(), TAGLINE)
      assert.deepEqual(await page.locator('.market-section-sub').allTextContents(), ['Rooms to work in', 'New things your agents can do', 'Programs on your own server', 'New characters to talk with', 'Ways of working, for every agent'])
      // Three themes are one row of three; the lone agent is one wide card.
      assert.deepEqual(await page.locator('.market-section-theme .market-theme-row').evaluateAll(rs => rs.map(r => r.children.length)), [3])
      assert.equal(await page.locator('.market-section-agent .market-items-one .market-item').count(), 1)
      assert.ok((await page.locator('.market-section-agent .market-item').boundingBox()).width > (await page.locator('.market-section-extension .market-item').first().boundingBox()).width * 1.5 || width < 600, `${label}: one alone takes the row`)
      await page.waitForFunction(() => [...document.querySelectorAll('.market-swatch')].every(n => n.querySelector('.theme-preview[data-look]') || n.querySelector('img')?.complete))
      // Covers (2026-11-18): a theme with one shows it, in the mode's own
      // picture; one without shows itself, Appearance's preview in its tokens.
      const swatchOf = name => page.locator('.market-theme', { has: page.locator('.market-name', { hasText: name }) }).locator('.market-swatch')
      assert.match(await swatchOf('Ember').locator('img.market-cover-img').getAttribute('src'), mode === 'dark' ? /\/ember\/cover-dark$/ : /\/ember\/cover$/)
      assert.equal(await swatchOf('Moss').locator('img.market-cover-img').count(), 1)
      assert.equal(await swatchOf('Tide').locator('.theme-preview.market-preview[data-look="tide"] .theme-miniature').count(), 1, `${label}: a theme with no cover wears itself`)
      assert.equal(await page.evaluate(() => document.querySelector('.market-theme .market-cover-img')?.naturalWidth), 1536)
      assert.equal(await page.locator('.market-lead-plate .market-lead-cover .market-blob img.market-blob-img').count(), 1, `${label}: no cover and not a theme: a plate with its icon, large`)
      assert.equal(await page.locator('.market svg.market-art, .market-art-twinkle, .market-scene').count(), 0, `${label}: no drawn covers`)
      assert.doesNotMatch(await page.locator('.market').innerText(), /by hand/i, `${label}: no "by hand"`)
      await families(page, `${label} front`)
      assert.equal(await sideways(page), false, `${label}: front page, no sideways scroll`)
      // A swatch's own pill is drawn in its theme's colours, on purpose.
      // Glass over a photograph is the one paint outside the tokens.
      assertControls(await auditControls(page, '.market .pillbtn:not(.theme-preview *):not(.market-glass), .market .market-kind'))

      // The typeahead: the best few, arrows, Enter on the selection.
      const field = page.locator('.market-q')
      await field.click(); await field.fill('sync with my phone')
      await page.locator('.market-suggestion').first().waitFor()
      assert.equal(await page.locator('.market-suggestion .market-name').first().textContent(), 'Pocket Sync')
      assert.deepEqual(await page.locator('.market-suggestion').first().locator('mark').allTextContents(), ['Sync', 'phone'])
      await field.press('Escape')
      await page.waitForFunction(() => !document.querySelector('.market-suggest'))
      assert.equal(await page.locator('.market').count(), 1, `${label}: and not the Market`)
      await field.fill('write')
      await field.press('ArrowDown'); await field.press('ArrowDown'); await field.press('ArrowUp')
      await page.locator('.market-suggestion.on', { hasText: 'Essay shaping' }).waitFor()
      await field.press('ArrowUp')
      await page.locator('.market-suggest-all.on').waitFor()
      // Enter on "See all": the results page.
      await field.press('Enter')
      await page.locator('.market-best').waitFor()
      assert.equal(await page.locator('.market-best-name').textContent(), 'Essay shaping')
      assert.deepEqual(await page.locator('.market-best-desc mark').allTextContents(), ['writing'])
      assert.deepEqual(await page.locator('#market-also').allTextContents(), ['Also close'])
      assert.equal(await page.locator('.market-build').count(), 1, `${label}: results end with build-it`)
      await families(page, `${label} results`)

      // Nothing matches: the build-it panel, the agent picker, a thread.
      await field.fill('a zebra that tells jokes'); await field.press('Enter')
      await page.locator('.market-count').filter({ hasText: 'Nothing listed matches' }).waitFor()
      assert.equal(await page.locator('.market-best').count(), 0)
      assert.equal(await page.locator('#market-build-title').textContent(), 'Nothing quite right for “a zebra that tells jokes”?')
      assert.equal(await page.locator('.market-build .empty-state-search').count(), 1, `${label}: the lens, drawn`)
      await families(page, `${label} build-it`)

      // A kind's page, from a chip; it ends with build-it too.
      await page.locator('.market-kinds .market-kind', { hasText: 'Apps' }).click()
      await page.locator('.market-page-head h2', { hasText: 'Apps' }).waitFor()
      assert.deepEqual(await page.locator('.market-item .market-name').allTextContents(), ['Story desk', 'Habit wall'])
      assert.equal(await page.locator('.market-build').count(), 1)

      // A listing, and the install sheet: read, then yes.
      await page.locator('.market-item-open', { hasText: 'Story desk' }).click()
      await page.locator('.market-listing-title', { hasText: 'Story desk' }).waitFor()
      await page.locator('.market-back').click()
      await page.locator('.market-page-head h2', { hasText: 'Apps' }).waitFor()
      await page.evaluate(() => __cap.page.openMarket({ id: 'pocket-sync' }))
      await page.locator('.market-listing-title', { hasText: 'Pocket Sync' }).waitFor()
      await page.locator('.market-touch').first().waitFor()
      assert.deepEqual(await page.locator('.market-touch-text').allTextContents(), ['Talks to your own devices', 'Runs a small program on your server'])
      await families(page, `${label} listing`)
      const posts = () => app.writes.filter(w => w.method === 'POST')
      await page.locator('.market-listing-head .market-listing-acts .pillbtn', { hasText: 'Install' }).click()
      await page.locator('.market-install .market-touch').first().waitFor()
      assert.equal(await page.locator('.market-yes-once').textContent(), 'You say yes once, here. A later version asks again only if it reaches further.')
      assert.deepEqual(posts().map(p => p.body), [{ url: 'https://github.com/ada-park/pocket-sync/tree/v0.4.0/pocket-sync' }], `${label}: the first call reads, it does not install`)
      await page.locator('.market-install .pillbtn.primary', { hasText: 'Install' }).click()
      await page.locator('.market-install-done').waitFor()
      assert.equal(posts().at(-1).body.confirm, 'ok-pocket-sync', `${label}: the second call is the yes`)
      await page.locator('.market-install .pillbtn.primary', { hasText: 'Done' }).click()
      await page.locator('.market-listing-head .market-listing-acts .market-state', { hasText: 'Installed' }).waitFor()

      // A theme's own page: its cover, the maker with their links, the
      // README (made safe at the broker, drawn as nodes here), and no
      // "Listed" row; a theme with no cover wears itself, large.
      await page.evaluate(() => __cap.page.openMarket({ id: 'moss' }))
      await page.locator('.market-listing-title', { hasText: 'Moss' }).waitFor()
      await page.locator('.market-readme').waitFor()
      assert.match(await page.locator('.market-listing-cover img.market-cover-img').getAttribute('src'), /\/moss\/cover$/)
      assert.equal(await page.locator('.market-creator-name').textContent(), 'Ada Park')
      assert.deepEqual(await page.locator('.creator-link').evaluateAll(ns => ns.map(n => [n.className.replace('creator-link creator-link-', ''), n.textContent, n.getAttribute('target'), n.querySelector('svg') ? 'mark' : ''])),
        [['website', 'adapark.example', '_blank', 'mark'], ['github', 'GitHub', '_blank', 'mark'], ['bluesky', 'Bluesky', '_blank', 'mark'], ['mastodon', 'Mastodon', '_blank', 'mark'],
         ['patreon', 'Patreon', '_blank', 'mark'], ['kofi', 'Ko-fi', '_blank', 'mark'], ['email', 'Email', '_blank', 'mark']])
      // Its first heading only repeated the name: not drawn twice.
      assert.equal(await page.locator('.market-readme-body h1').count(), 0)
      assert.equal(await page.locator('.market-readme-body h2').first().textContent(), 'What changes')
      assert.equal(await page.locator('.market-readme-src').textContent(), 'From the maker’s README')
      assert.equal(await page.locator('.market-maker-count').textContent(), 'Made 4 things in the Market')
      assert.equal(await page.locator('.market-fine').textContent(), 'Free · changes only how Loam looks')
      assert.deepEqual(await page.locator('.market-option-list li').allTextContents(), ['Agent faces', 'Inside the face'])
      assert.equal(await page.locator('.market-listing-head .market-try').textContent(), 'Try it first')
      assert.equal(await page.locator('.market-readme-body li').count(), 3)
      await page.waitForFunction(() => document.querySelector('.market-readme-img')?.complete && document.querySelector('.market-readme-img').naturalWidth > 0)
      assert.equal(await page.locator('.market-readme-img').getAttribute('src'), '/api/marketplace/moss/readme/shots/evening.webp')
      assert.equal(await page.locator('.market-readme-body a[href^="https://github.com/ada-park/moss"]').getAttribute('rel'), 'noopener noreferrer')
      assert.deepEqual(await page.locator('.market-kv dt').allTextContents(), ['Version', 'Kind', 'Light and dark', 'Source'], `${label}: no Listed row`)
      if (width === 390) {
        // A phone: the buttons sit under the words, and dock at the foot once
        // they have scrolled away.
        assert.equal(await page.locator('.market-dock.on').count(), 0)
        await page.evaluate(() => { const b = document.querySelector('.market-body'); b.scrollTop = b.scrollHeight; b.dispatchEvent(new Event('scroll')) })
        await page.locator('.market-dock.on .pillbtn', { hasText: 'Get' }).waitFor()
        await page.evaluate(() => { const b = document.querySelector('.market-body'); b.scrollTop = 0; b.dispatchEvent(new Event('scroll')) })
        await page.waitForFunction(() => !document.querySelector('.market-dock.on'))
      } else assert.equal(await page.locator('.market-dock').count(), 0, `${label}: no dock on a wide screen`)
      assert.doesNotMatch(await page.locator('.market').innerText(), /by hand/i)
      assert.equal(await sideways(page), false, `${label}: listing, no sideways scroll`)
      await families(page, `${label} theme listing`)
      await page.evaluate(() => __cap.page.openMarket({ id: 'tide' }))
      await page.locator('.market-listing-title', { hasText: 'Tide' }).waitFor()
      await page.locator('.market-listing-cover .market-preview-large[data-look="tide"] .theme-miniature').waitFor()
      assert.equal(await page.locator('.market-listing-cover img').count(), 0)

      // An agent package is listed and not installable yet.
      // An extension without a cover: no wide frame; its mark beside its name.
      await page.evaluate(() => __cap.page.openMarket({ id: 'images' }))
      await page.locator('.market-listing.no-cover .market-listing-head .market-blob img.market-blob-img').waitFor()
      assert.equal(await page.locator('.market-listing-cover').count(), 0)
      assert.equal(await page.locator('.market-readme-src').textContent(), 'In its maker’s words')
      assert.equal(await page.locator('.market-fine').textContent(), 'Free · talks to one service')
      assert.equal(await page.locator('.market-maker-count').count(), 0, `${label}: no links, only the name`)
      assert.equal(await page.locator('.market-creator-name').textContent(), 'Loam contributors')
      await page.evaluate(() => __cap.page.openMarket({ id: 'running-coach' }))
      await page.locator('.market-listing-head .market-listing-acts .market-state', { hasText: 'Not installable yet' }).waitFor()

      // Try a theme; leaving the Market takes it off.
      await page.evaluate(() => __cap.page.openMarket({ kind: 'theme' }))
      await page.locator('.market-theme').first().waitFor()
      await page.waitForFunction(() => !document.querySelector('.market-try:disabled'))
      await page.locator('.market-theme .market-try').first().click()
      await page.waitForFunction(() => document.documentElement.dataset.look === 'ember')
      await page.locator('.market-trybar').waitFor()
      assert.equal(await sideways(page), false, `${label}: trying, no sideways scroll`)
      await page.locator('.market-close').click()
      await page.waitForFunction(() => document.documentElement.dataset.look === 'latte')
      assert.equal(await page.locator('.market').count(), 0)

      // Find more, from Plugins and from Appearance: the Market at that kind.
      await page.evaluate(() => __cap.page.goSettings('plugins'))
      await page.locator('.market-find-more .setrow').click()
      await page.locator('.market-page-head h2', { hasText: 'Extensions' }).waitFor()
      await page.evaluate(() => __cap.page.goSettings('appearance'))
      await page.locator('.market-find-more .setrow').click()
      await page.locator('.market-page-head h2', { hasText: 'Themes' }).waitFor()

      // Build it: pick an agent; a new thread, the words in its composer.
      await page.locator('.market-build-go').click()
      await page.locator('.market-face').first().click()
      await page.waitForFunction(() => /Could you build me my own theme\?/.test(document.querySelector('#draft')?.innerText ?? ''))
      assert.equal(await page.locator('.market').count(), 0)
      assert.ok(!app.writes.some(w => w.path?.includes('/messages')), `${label}: nothing was sent`)
      assert.deepEqual(errors, [], `${label}: page errors`)
      console.log(`PASS ${label}`)
    } finally { await close() }

    // An empty list: the masthead, the search, and the build-it picture.
    const empty = await open(width, mode, { listings: [] })
    try {
      await empty.page.evaluate(() => __cap.page.openMarket({}))
      await empty.page.locator('.market-build-whole').waitFor()
      assert.equal(await empty.page.locator('#market-build-title').textContent(), 'The Market is just opening')
      for (const absent of ['.market-kinds', '.market-lead', '.market-section', '.market-note']) assert.equal(await empty.page.locator(absent).count(), 0, `${label} empty: no ${absent}`)
      assert.equal(await empty.page.locator('.market-tagline').textContent(), TAGLINE)
      await families(empty.page, `${label} empty`)
      assert.equal(await sideways(empty.page), false)
      assert.deepEqual(empty.errors, [])
      console.log(`PASS ${label} empty`)
    } finally { await empty.close() }

    // A lead story with a cover is its photograph, the words in white over
    // it, Try it as glass; and a theme section of one is one wide tile.
    const { LISTINGS } = await import('./market-fixture.mjs')
    const photo = await open(width, mode, { listings: LISTINGS.filter(e => e.id !== 'tide').map(e => ({ ...e, featured: e.id === 'ember' })) })
    try {
      await photo.page.evaluate(() => __cap.page.openMarket({}))
      await photo.page.locator('.market-lead-photo img.market-cover-img').waitFor()
      assert.equal(await photo.page.locator('#market-lead-title').textContent(), 'Ember')
      assert.equal(await photo.page.locator('.market-lead-photo .market-kicker').textContent(), 'Featured theme')
      assert.equal(await photo.page.locator('.market-lead-by').textContent(), 'by Loam contributors')
      assert.equal(await photo.page.evaluate(() => getComputedStyle(document.querySelector('#market-lead-title')).color), 'rgb(255, 255, 255)')
      await photo.page.waitForFunction(() => document.querySelector('.market-lead-acts .market-try.market-glass:not(:disabled)'))
      assert.deepEqual(await photo.page.locator('.market-section-theme .market-theme-row').evaluateAll(rs => rs.map(r => r.children.length)), [1])
      assert.equal(await photo.page.locator('.market-theme-wide').count(), 1)
      await families(photo.page, `${label} photo lead`)
      assert.equal(await sideways(photo.page), false)
      assert.deepEqual(photo.errors, [])
      console.log(`PASS ${label} photo lead`)
    } finally { await photo.close() }
  }
} finally { await browser.close() }
console.log('market-browser: ok')
