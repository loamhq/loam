// Run in app/ with UPDATES_OUT, LOAM_PLAYWRIGHT and optionally LOAM_CHROME.
import assert from 'node:assert/strict'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { marketFixture } from '../test/market-fixture.mjs'

const out = process.env.UPDATES_OUT
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8')
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }

try {
  for (const width of [1440, 390]) for (const mode of ['light', 'dark']) {
    const app = await marketFixture({ mode, look: 'latte' })
    const rows = [
      ['reading-list', 'Reading list', 'Save the articles you want to read next.'],
      ['pocket-sync', 'Pocket Sync', 'Keep a copy of your notes on your phone.'],
      ['field-notes', 'Field notes', 'Collect observations in your daily note.'],
    ].map(([id, name, description], i) => ({
      id, name, description, layer: 'yours', folder: 'plugins/' + id, api: 0, version: '1.0.3',
      author: 'Ada Park', kind: ['extension'], switch: 'plugin', effective: true, on: true,
      source: { kind: 'link', via: i === 2 ? 'link' : 'market', repo: 'ada-park/' + id },
      auto_update: i !== 2, edited: i === 2, updates_paused: i === 2,
    }))
    const patches = [], errors = []
    const info = Object.fromEntries(rows.slice(0, 2).map((row, i) => [row.id, {
      via: 'market', version: '1.0.4', held: i === 1, link: 'https://github.com/' + row.source.repo + '/tree/v1.0.4',
    }]))
    info['field-notes'] = { via: 'link', version: '1.0.4', personal: true, held: true }
    app.routes['GET /v1/plugins'] = (req, res) => json(res, { plugins: rows })
    app.routes['GET /v1/plugin-updates'] = (req, res) => json(res, { plugins: info, entries: {}, market_state: 'listed' })
    for (const row of rows) {
      app.routes[`GET /v1/plugins/${row.id}`] = (req, res) => json(res, { plugin: row })
      app.routes[`PATCH /v1/plugins/${row.id}`] = (req, res, url, raw) => {
        const patch = JSON.parse(raw); patches.push(patch)
        if (patch.review) {
          if (patch.review === 'skip') delete info[row.id]
          return json(res, { [patch.review === 'skip' ? 'skipped' : 'offered']: patch.version })
        }
        row.auto_update = patch.auto_update; row.updates_paused = false
        json(res, { plugin: row })
      }
    }
    app.routes['POST /v1/plugins'] = (req, res, url, raw) => {
      if (JSON.parse(raw).url?.includes('field-notes')) return json(res, {
        ...rows[2], version: '1.0.4', replaces: { version: '1.0.3', edited: true },
        added: {}, declaration: {}, confirm: 'fixture', source: { url: 'https://github.com/ada-park/field-notes/tree/v1.0.4' }
      })
      assert.deepEqual(JSON.parse(raw), { url: info['pocket-sync'].link })
      json(res, { ...rows[1], version: '1.0.4', replaces: { version: '1.0.3' },
        added: { record: 'read' }, declaration: { touches: { record: 'read' } }, confirm: 'fixture' })
    }
    const event = { id: 'update-reading', agent: 'loam', kind: 'plugin-update', actor: 'loam',
      title: 'Reading list', ts: new Date().toISOString(), from_version: '1.0.2', version: '1.0.3',
      outcome: 'updated automatically to 1.0.3' }
    app.routes['GET /v1/activity'] = (req, res) => json(res, { items: [event], jobs: { slots: [], queue: [], landing: [] }, total: 1, next_offset: null })
    const ctx = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 900 }, colorScheme: mode,
      isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    try {
      await ctx.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript',
        body: source + '\nglobalThis.__review = {goSettings, goActivity, closeSheet, modules};' }))
      const page = await ctx.newPage(); page.setDefaultTimeout(12000)
      page.on('pageerror', e => errors.push(e.message))
      const shot = async name => {
        await page.mouse.move(0, 0)
        await page.evaluate(async () => { await document.fonts.ready; document.activeElement?.blur() })
        await page.waitForTimeout(250)
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
        await page.screenshot({ path: path.join(out, `${name}-${width}-${mode}.png`), animations: 'disabled' })
      }
      await page.goto(app.url)
      await page.locator('.tt-row').first().waitFor()
      await page.evaluate(() => __review.goSettings('plugins'))
      await page.getByText('Update available · 1.0.4', { exact: true }).waitFor()
      const reason = 'Loam paused this update because you’ve changed the plugin, and your agent can bring in version 1.0.4 while keeping your edits.'
      const skipped = 'Version 1.0.4 skipped. Your agent will ask again when a newer version is available.'
      await page.getByText(reason, { exact: true }).scrollIntoViewIfNeeded()
      await shot('plugins')
      await page.getByRole('button', { name: 'About Pocket Sync' }).click()
      await page.getByRole('button', { name: 'Review update' }).waitFor()
      await shot('about')
      await page.getByRole('button', { name: 'Review update' }).click()
      await page.getByText('Reads your threads', { exact: true }).waitFor()
      await shot('review')
      await page.getByRole('button', { name: 'Not now' }).click()
      await page.getByRole('button', { name: 'Back to Plugins' }).click()
      await page.getByRole('button', { name: 'About Field notes' }).click()
      await page.getByRole('button', { name: 'Ask my agent' }).waitFor()
      const actions = page.locator('.plugin-updates button')
      assert.deepEqual(await actions.allTextContents(), ['Keep my version', 'Ask my agent'])
      await shot('personal')
      await page.getByRole('button', { name: 'Ask my agent' }).click()
      await page.getByText('Your agent has the update.', { exact: true }).waitFor()
      await page.getByRole('button', { name: 'Keep my version' }).click()
      await page.getByText(skipped, { exact: true }).waitFor()
      await shot('personal-skipped')
      assert.deepEqual(patches, [{ review: 'ask', version: '1.0.4' }, { review: 'skip', version: '1.0.4' }])
      await page.evaluate(() => __review.modules.market.openLinkInstall({ link: 'https://github.com/ada-park/field-notes/tree/v1.0.4' }))
      await page.getByRole('button', { name: 'Ask my agent' }).waitFor()
      const sheet = page.locator('.market-install')
      assert.deepEqual(await sheet.locator('.market-install-acts button').allTextContents(), ['Keep my version', 'Ask my agent'])
      await sheet.getByText(reason, { exact: true }).waitFor()
      await shot('personal-install')
      await sheet.getByRole('button', { name: 'Keep my version' }).click()
      await sheet.getByText(skipped, { exact: true }).waitFor()
      await shot('personal-install-skipped')
      await page.getByRole('button', { name: 'Done', exact: true }).click()
      await page.evaluate(() => { __review.closeSheet(); __review.goActivity() })
      await page.getByText('updated automatically to 1.0.3', { exact: true }).first().click()
      await page.getByText(/Version 1.0.2 → 1.0.3/).waitFor()
      await shot('activity')
      assert.deepEqual(errors, [])
      console.log(`PASS ${width} ${mode}: list, About, review, personal update choices and Activity`)
    } finally { await ctx.close(); await app.close() }
  }
} finally { await browser.close() }
