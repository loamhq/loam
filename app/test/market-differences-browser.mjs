// Market install & update screens with and without Loam's note differences
// (2026-11-27), verified in real Chromium at 390 and 1440, light and dark:
//
//   - a reviewed listing with no differences looks unchanged on the install
//     screen (zero .market-install-differences nodes);
//   - a reviewed listing with note.differences shows them prominently (never
//     collapsed) on the install screen above "Before you say yes", in Loam's
//     note sheet, and on the listing's note button;
//   - an update opened from Settings › Plugins -> Review update shows the
//     differences prominently on the update sheet too, even when the plugin's
//     declared touches did not widen;
//   - no sideways scroll, no page error, colour families and control styles hold.
//
// Run:
//   LOAM_PLAYWRIGHT=.../playwright/index.mjs LOAM_CHROME=/usr/bin/chromium-headless-shell \
//   MARKET_DIFF_EVIDENCE=/path/to/out node test/market-differences-browser.mjs
import assert from 'node:assert/strict'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { marketFixture, LISTINGS, REVIEWED } from './market-fixture.mjs'
import { inspectColourFamilies } from '../tools/colour-families.mjs'
import { auditControls, assertControls } from './control-style-guard.mjs'

const out = process.env.MARKET_DIFF_EVIDENCE || ''
if (out) await mkdir(out, { recursive: true })

const DIFF_LINE = 'The plugin says it runs no server code; it runs a Python script on your server every night to move finished notes into your Archive folder.'
const WITH_DIFF = {
  id: 'inbox-sweeper',
  name: 'Inbox Sweeper',
  author: 'Kestrel Okafor',
  kind: ['extension'],
  version: '1.0.0',
  description: 'Moves old, finished notes out of your inbox folder into a tidy archive every night.',
  link: 'https://github.com/example/loam-inbox-sweeper/tree/v1.0.0/inbox-sweeper',
  repo: 'example/loam-inbox-sweeper',
  ref: 'v1.0.0',
  path: 'inbox-sweeper',
  pinned_link: 'https://github.com/example/loam-inbox-sweeper/tree/' + 'd'.repeat(40) + '/inbox-sweeper',
  pin: { ref: 'd'.repeat(40), path: 'inbox-sweeper' },
  review: {
    commit: 'd'.repeat(40),
    version: '1.0.0',
    reviewed_at: '2026-11-27',
    note: {
      summary: 'Moves older, finished Markdown notes from your vault’s Inbox folder into Archive/<year>/ on a nightly schedule, and adds a settings section for the age threshold. Sends nothing over the network.',
      differences: [DIFF_LINE],
      touches: [
        'Reads and moves Markdown notes between your vault’s Inbox and Archive folders.',
        'Runs a scheduled Python script on your server every night at 02:00 PT.',
        'Sends nothing over the network.',
      ],
      may_change: [
        'Adds a settings section for choosing how many days old a note must be before archiving.',
        'Moves finished notes from Inbox into Archive/<year>/ without deleting anything.',
      ],
      worth_knowing: ['Notes containing open tasks (- [ ]) or #todo are left untouched.'],
    },
  },
  declaration: {
    touches: {
      network: [],
      record: 'none',
      memory: 'none',
      server: false,
      vault: ['Inbox', 'Archive'],
      schedule: ['sweep'],
      settings: ['min_age_days'],
    },
    overrides: [],
    page_code: true,
  },
  readme: '# Inbox Sweeper\n\nMoves old, finished notes out of your inbox folder into a tidy archive.\n',
}

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8')
let shots = 0

async function snap(page, name) {
  if (!out) return
  await page.mouse.move(0, 0)
  await page.evaluate(async () => {
    await document.fonts.ready
    await new Promise(requestAnimationFrame)
    await new Promise(requestAnimationFrame)
  })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name}: no sideways scroll`)
  await page.screenshot({ path: path.join(out, `${name}.png`), animations: 'disabled' })
  shots++
}

try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const label = `${width}-${mode}`
    const app = await marketFixture({ mode, look: 'latte', listings: [...LISTINGS, REVIEWED, WITH_DIFF] })
    const context = await browser.newContext({
      viewport: { width, height: width === 390 ? 844 : 1000 },
      colorScheme: mode,
      isMobile: width === 390,
      hasTouch: width === 390,
      reducedMotion: 'reduce',
    })
    const errors = []
    try {
      await context.route('**/app.js', route => route.fulfill({
        contentType: 'text/javascript',
        body: source + '\nglobalThis.__cap = { page, state, schedule };',
      }))
      const page = await context.newPage()
      page.setDefaultTimeout(12000)
      page.on('pageerror', e => errors.push(e.message))
      await page.goto(app.url)
      await page.locator('.tt-row').first().waitFor()

      // 1. Listing WITHOUT differences: install screen must look identical to before.
      await page.evaluate(() => __cap.page.openMarket({ id: 'focus-timer' }))
      await page.locator('.market-listing-title', { hasText: 'Focus timer' }).waitFor()
      await page.locator('.market-listing-head .market-act').click()
      await page.locator('.market-install .pillbtn.primary:not([disabled])').waitFor()
      assert.equal(await page.locator('.market-install-differences').count(), 0, `${label}: no differences box when listing has none`)
      assert.equal(await page.locator('.market-install-touches').count(), 1)
      assert.deepEqual(await page.evaluate(inspectColourFamilies), [], `${label} no-diff: colour families`)
      assertControls(await auditControls(page, '.market-install .pillbtn'))
      await snap(page, `install-without-diff-${label}`)
      await page.keyboard.press('Escape')
      await page.locator('.market-install').waitFor({ state: 'detached' })

      // 2. Listing WITH differences: listing note link, note sheet, and fresh install screen.
      await page.evaluate(() => __cap.page.openMarket({ id: 'inbox-sweeper' }))
      await page.locator('.market-listing-title', { hasText: 'Inbox Sweeper' }).waitFor()
      const noteLink = page.locator('.market-note-link')
      await noteLink.waitFor()
      assert.equal(await noteLink.locator('.market-note-excerpt').textContent(), DIFF_LINE, `${label}: listing note button surfaces difference`)

      await noteLink.click()
      const noteSheet = page.locator('.market-review-sheet')
      await noteSheet.waitFor()
      assert.equal(await noteSheet.locator('.market-note-differences h3').textContent(), 'Differences from its description')
      assert.deepEqual(await noteSheet.locator('.market-note-differences li').allTextContents(), [DIFF_LINE])
      assert.deepEqual(await page.evaluate(inspectColourFamilies), [], `${label} note-sheet: colour families`)
      await snap(page, `note-sheet-with-diff-${label}`)
      await page.keyboard.press('Escape')
      await noteSheet.waitFor({ state: 'detached' })

      await page.locator('.market-listing-head .market-act').click()
      await page.locator('.market-install .pillbtn.primary:not([disabled])').waitFor()
      const diffBox = page.locator('.market-install-differences')
      await diffBox.waitFor()
      assert.equal(await diffBox.count(), 1, `${label}: differences box visible on fresh install`)
      assert.equal(await diffBox.locator('details').count(), 0, `${label}: not buried in a collapsed details element`)
      assert.equal(await diffBox.locator('.market-diff-title').textContent(), 'Differences Loam noted')
      assert.deepEqual(await diffBox.locator('li').allTextContents(), [DIFF_LINE])
      const dRect = await diffBox.boundingBox()
      const tRect = await page.locator('.market-install-touches').boundingBox()
      assert.ok(dRect.height > 40 && (dRect.y + dRect.height) <= tRect.y + 1, `${label}: differences box sits cleanly above Before you say yes`)
      assert.deepEqual(await page.evaluate(inspectColourFamilies), [], `${label} fresh-diff: colour families`)
      assertControls(await auditControls(page, '.market-install .pillbtn'))
      await snap(page, `install-with-diff-${label}`)
      await page.keyboard.press('Escape')
      await page.locator('.market-install').waitFor({ state: 'detached' })

      // 3. Update flow from Settings › Plugins -> Review update (with review carried in /v1/plugin-updates entries,
      //    and added: {} so the declaration itself says "Nothing new in what it touches").
      const json = (res, status, body) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
      const installedRow = {
        id: WITH_DIFF.id, name: WITH_DIFF.name, version: '0.9.0', author: WITH_DIFF.author, api: 0,
        kind: WITH_DIFF.kind, places: ['settings.section'], overrides: [], layer: 'yours',
        folder: 'fixtures/inbox-sweeper', effective: true, on: true, switch: 'plugin',
        broken: null, warnings: [], auto_update: true, edited: false,
        has: { page_main: true, theme: false },
        touches: WITH_DIFF.declaration.touches, declaration: WITH_DIFF.declaration,
        source: { kind: 'link', via: 'market', repo: WITH_DIFF.repo, ref: 'v0.9.0', ref_kind: 'tag', commit: '1'.repeat(40), url: 'https://github.com/example/loam-inbox-sweeper/tree/v0.9.0/inbox-sweeper' },
      }
      const origPlugins = app.routes['GET /v1/plugins']
      app.routes['GET /v1/plugins'] = (req, res, url, raw) => {
        const end = res.end.bind(res)
        res.end = body => {
          const parsed = JSON.parse(body)
          parsed.plugins.push(installedRow)
          return end(JSON.stringify(parsed))
        }
        return origPlugins(req, res, url, raw)
      }
      app.routes[`GET /v1/plugins/${WITH_DIFF.id}`] = (req, res) => json(res, 200, { plugin: installedRow })
      app.routes['GET /v1/plugin-updates'] = (req, res) => json(res, 200, {
        market_state: 'listed',
        entries: {
          [WITH_DIFF.id]: {
            id: WITH_DIFF.id, author: WITH_DIFF.author, repo: WITH_DIFF.repo, ref: WITH_DIFF.ref,
            path: WITH_DIFF.path, pin: WITH_DIFF.pin, version: WITH_DIFF.version,
            link: WITH_DIFF.link, pinned_link: WITH_DIFF.pinned_link, review: WITH_DIFF.review,
          },
        },
        plugins: {
          [WITH_DIFF.id]: { via: 'market', version: '1.0.0', link: WITH_DIFF.pinned_link, held: true },
        },
      })
      app.routes['POST /v1/plugins'] = (req, res, url, raw) => {
        const body = JSON.parse(raw)
        if (body.url === WITH_DIFF.pinned_link) {
          return json(res, 200, {
            installed: false, id: WITH_DIFF.id, name: WITH_DIFF.name, version: WITH_DIFF.version,
            author: WITH_DIFF.author, api: 0, kind: WITH_DIFF.kind, places: ['settings.section'],
            source: { kind: 'link', repo: WITH_DIFF.repo, path: WITH_DIFF.path, ref: WITH_DIFF.ref, ref_kind: 'tag', commit: WITH_DIFF.review.commit, url: WITH_DIFF.link },
            declaration: WITH_DIFF.declaration, added: {},
            replaces: { version: '0.9.0', edited: false, personal: false },
            shadows: null, warnings: [], confirm: 'ok-update-inbox-sweeper',
          })
        }
        return json(res, 404, { error: 'Not found' })
      }

      // Close Market, refresh plugin rows, and open Settings › Plugins › Inbox Sweeper.
      await page.keyboard.press('Escape')
      await page.evaluate(async () => {
        await __cap.page.pluginLoader?.refresh?.()
        __cap.page.goSettings('plugins')
      })
      const rowBtn = page.locator('.settings .plugin-row', { hasText: 'Inbox Sweeper' })
      await rowBtn.waitFor()
      await rowBtn.click()
      const reviewBtn = page.locator('.plugin-updates button', { hasText: 'Review update' })
      await reviewBtn.waitFor()
      await reviewBtn.click()
      await page.locator('.market-install .sheet-title', { hasText: 'Update Inbox Sweeper?' }).waitFor()
      await page.locator('.market-install .pillbtn.primary:not([disabled])').waitFor()
      const upDiffBox = page.locator('.market-install-differences')
      await upDiffBox.waitFor()
      assert.equal(await upDiffBox.count(), 1, `${label}: differences box visible on update`)
      assert.deepEqual(await upDiffBox.locator('li').allTextContents(), [DIFF_LINE])
      assert.equal(await page.locator('.market-install-touches .market-facts-title').textContent(), 'Nothing new in what it touches')
      assert.deepEqual(await page.evaluate(inspectColourFamilies), [], `${label} update-diff: colour families`)
      assertControls(await auditControls(page, '.market-install .pillbtn'))
      await snap(page, `update-with-diff-${label}`)

      assert.deepEqual(errors, [], `${label}: page errors`)
      console.log(`PASS ${label}`)
    } finally {
      await context.close()
      await app.close()
    }
  }
} finally {
  await browser.close()
}
console.log(`market-differences-browser: ok${out ? ` (${shots} screenshots in ${out})` : ''}`)
