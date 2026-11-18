// Settings › Plugins › Marketplace in a real browser (2026-11-17): a listed
// marketplace, "Get" asking the same install question as any link, the calm
// empty state, at 390 and 1440, light and dark.
// LOAM_PLAYWRIGHT=<playwright index.mjs> LOAM_CHROME=<chromium> MARKET_EVIDENCE=<dir> node test/plugin-market-browser.mjs
import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { pluginHostFixture } from './plugin-host-fixture.mjs'
const out = process.env.MARKET_EVIDENCE
if (!out) throw new Error('Set MARKET_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ headless: true, executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const listed = { url: 'https://raw.githubusercontent.com/loamhq/marketplace/main/list.json', state: 'listed', repo: 'loamhq/marketplace', skipped: [], plugins: [
  { id: 'dusk', name: 'Dusk', description: 'A warm dark theme for late evenings, with softer contrast on long threads.', author: 'Ada Lovelace', kind: ['theme'], link: 'https://github.com/ada/loam-dusk/tree/v1.0.0/dusk', installed: false },
  { id: 'proof', name: 'Proof plugin', description: 'Marks every message the plugin host has drawn.', author: 'Fixture contributors', kind: ['extension'], link: 'https://github.com/example/proof/tree/v1.0.0', installed: true },
  { id: 'pet', name: 'A pet', description: 'A small animal that sleeps in the corner of the page.', author: 'A friend', kind: ['extension'], link: 'https://github.com/friend/loam-pet/tree/v1.2.0/pet', installed: false }] }
const empty = { ...listed, state: 'empty', plugins: [] }
const results = []
try {
  for (const mode of ['light', 'dark']) for (const width of [390, 1440]) {
    const app = await pluginHostFixture({ mode }), errors = []
    let answer = listed
    app.routes['GET /v1/marketplace'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(answer)) }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 900 }, isMobile: width === 390, hasTouch: width === 390, colorScheme: mode, reducedMotion: 'reduce' })
    const appJs = await readFile('public/app.js', 'utf8') + '\nglobalThis.__market = { goSettings, closeSheet };'
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: appJs }))
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message))
    const shot = async name => {
      await page.evaluate(() => document.fonts.ready)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name}: no sideways scroll`)
      const file = path.join(out, `${name}-${mode}-${width}.png`)
      await page.screenshot({ path: file, animations: 'disabled' })
      return file
    }
    try {
      await page.goto(app.url)
      await page.locator('.tt-personas .persona').first().waitFor()
      await page.evaluate(() => __market.goSettings('plugins'))
      const section = page.locator('.plugin-market')
      await section.getByText('Dusk', { exact: true }).waitFor()
      await section.scrollIntoViewIfNeeded()
      const rows = await section.locator('.plugin-market-row').evaluateAll(nodes => nodes.map(n => ({ text: n.innerText, button: n.querySelector('button')?.getBoundingClientRect().height ?? null, right: n.getBoundingClientRect().right, width: n.closest('.rows').getBoundingClientRect().right })))
      assert.deepEqual(rows.map(r => r.text.split('\n')[0]), ['Dusk', 'Proof plugin', 'A pet'])
      assert.ok(rows[1].text.includes('Installed') && rows[1].button === null, 'an installed one says so and has no Get')
      for (const r of [rows[0], rows[2]]) assert.ok(r.button >= 44, 'Get is a 44px target')
      assert.ok(rows[0].text.includes('Ada Lovelace · Theme'))
      const listedShot = await shot('market-listed')
      const posted = new Promise(resolve => page.on('request', r => { if (r.url().endsWith('/api/plugins') && r.method() === 'POST') resolve(JSON.parse(r.postData())) }))
      await section.getByRole('button', { name: 'Get A pet' }).click()
      assert.deepEqual(await posted, { url: 'https://github.com/friend/loam-pet/tree/v1.2.0/pet' })
      await page.locator('#plugin-install-question h3.display').waitFor()
      const questionShot = await shot('market-get-question')
      answer = empty
      await page.evaluate(() => { __market.closeSheet() })
      await page.evaluate(() => __market.goSettings('plugins'))
      // The list is read once per page; a fresh page reads the empty one.
      await page.reload(); await page.locator('.tt-personas .persona').first().waitFor()
      await page.evaluate(() => __market.goSettings('plugins'))
      await page.locator('.plugin-market-empty').waitFor()
      assert.equal(await page.locator('.plugin-market-empty').innerText(), 'Nothing is listed yet. Anything shared as a link installs below.')
      assert.equal(await page.locator('.plugin-market [role=alert]').count(), 0, 'an empty list is not an error')
      await page.locator('.plugin-market').scrollIntoViewIfNeeded()
      const emptyShot = await shot('market-empty')
      assert.deepEqual(errors, [])
      results.push({ mode, width, rows: rows.length, shots: [listedShot, questionShot, emptyShot] })
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
console.log(JSON.stringify(results, null, 2))
