// Real page/relay, local synthetic host. No production credentials or writes.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { pluginHostFixture } from './plugin-host-fixture.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.THEME_CARD_EVIDENCE || path.join(process.env.TMPDIR, 'theme-cards')
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8'), results = []
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await pluginHostFixture({ mode }), errors = [], context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    try {
      await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__cards={theme,goSettings,schedule};' }))
      const page = await context.newPage()
      page.setDefaultTimeout(15000)
      page.on('pageerror', e => errors.push(e.message))
      await page.goto(app.url)
      await page.waitForFunction(() => globalThis.__cards)
      // Use the shipped layer here; the fixture normally shadows Latte with Yours.
      app.setRows(app.rows().filter(r => !(r.id === 'latte' && r.layer === 'yours')).map(r => r.id === 'latte' ? { ...r, effective: true } : r))
      await page.reload(); await page.waitForFunction(() => globalThis.__cards)
      await page.evaluate(() => __cards.goSettings('appearance'))
      const latte = page.getByRole('button', { name: 'Latte & Newspaper', exact: true })
      await latte.waitFor()
      await page.waitForFunction(() => { const n = document.querySelector('.theme-cover'); return n?.complete && n.naturalWidth > 0 })
      await page.evaluate(() => document.fonts.ready)
      const inspect = () => page.evaluate(() => {
        const cards = [...document.querySelectorAll('.theme-choice')].map(n => {
          const name = n.querySelector('.theme-choice-name'), check = n.querySelector('.theme-choice-check'), preview = n.querySelector('.theme-preview'), s = getComputedStyle(name), b = name.getBoundingClientRect(), c = check?.getBoundingClientRect(), p = preview.getBoundingClientRect()
          return { name: n.getAttribute('aria-label'), pressed: n.getAttribute('aria-pressed'), clamp: s.webkitLineClamp, font: s.fontFamily, height: b.height, lineHeight: parseFloat(s.lineHeight), overflowed: name.scrollHeight > name.clientHeight, overlap: c ? !(b.top >= c.bottom || b.right <= c.left || b.left >= c.right) : false, inside: b.right <= p.right + 1 && b.bottom <= p.bottom + 1, background: getComputedStyle(preview).backgroundColor }
        })
        const image = document.querySelector('.theme-cover'), plain = document.querySelector('.theme-preview[data-look="plain"]')
        return { cards, image: { width: image.naturalWidth, loading: image.loading, fit: getComputedStyle(image).objectFit, position: getComputedStyle(image).objectPosition, filter: getComputedStyle(image).filter }, plain: { font: getComputedStyle(plain.querySelector('.theme-miniature')).fontFamily, primary: getComputedStyle(plain.querySelector('.primary')).backgroundColor, nestedButtons: plain.querySelectorAll('button').length }, overflow: document.documentElement.scrollWidth > innerWidth }
      })
      const measured = await inspect()
      for (const card of measured.cards) { assert.equal(card.clamp, '2'); assert.ok(card.height <= card.lineHeight * 2 + 1); assert.equal(card.overlap, false); assert.equal(card.inside, true) }
      assert.equal(measured.overflow, false)
      assert.equal(measured.image.loading, 'lazy'); assert.equal(measured.image.fit, 'cover'); assert.equal(measured.image.position, '62% 45%')
      assert.equal(measured.image.filter, mode === 'dark' ? 'brightness(0.8)' : 'none')
      assert.match(measured.cards[0].font, /Caprasimo/)
      assert.match(measured.plain.font, /system-ui/)
      assert.equal(measured.plain.nestedButtons, 0)
      assert.equal(measured.plain.primary, mode === 'light' ? 'rgb(37, 37, 37)' : 'rgb(221, 221, 221)')
      const shot = `appearance-${width}-${mode}.png`
      await page.screenshot({ path: path.join(out, shot), animations: 'disabled' })
      await page.getByRole('button', { name: 'Plain', exact: true }).click()
      await page.waitForFunction(() => document.documentElement.dataset.look === 'plain')
      assert.equal(await page.locator('.theme-options').count(), 0)
      await latte.click()
      await page.waitForFunction(() => document.documentElement.dataset.look === 'latte' && !__cards.theme.status.saving)
      assert.equal(app.appearance[width === 390 ? 'phone' : 'desktop'].theme, 'latte')
      assert.equal(await page.locator('#appearance-options').textContent(), 'Latte & Newspaper')
      assert.equal(await page.getByRole('button', { name: 'Latte tones', exact: true }).count(), 1)
      // A future long name must clamp and stay clear of the selected corner mark.
      await page.evaluate(() => { __cards.theme.install(__cards.theme.installed.map(t => t.id === 'latte' ? { ...t, name: 'Latte & Newspaper — a wonderfully long morning at the café with room for every idea' } : t)); __cards.schedule() })
      await page.getByRole('button', { name: /wonderfully long morning/ }).waitFor()
      const long = (await inspect()).cards[0]
      assert.ok(Math.abs(long.height - long.lineHeight * 2) < 1); assert.equal(long.overlap, false); assert.equal(long.overflowed, true)
      await page.screenshot({ path: path.join(out, `long-name-${width}-${mode}.png`), animations: 'disabled' })
      // The already loaded image failing reveals the real-component fallback.
      await page.locator('.theme-preview[data-look="latte"] .theme-cover').evaluate(n => n.dispatchEvent(new Event('error')))
      assert.equal(await page.locator('.theme-preview[data-look="latte"].has-image').count(), 0)
      await page.waitForFunction(() => !document.querySelector('.theme-preview[data-look="latte"] .theme-cover')); await page.evaluate(() => __cards.schedule());
      assert.equal(await page.locator('.theme-preview[data-look="latte"] .theme-cover').count(), 0)
      assert.deepEqual(errors, [])
      results.push({ width, mode, shot, measured, long, stableSavedID: 'latte', errors })
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'checks.json'), JSON.stringify({ browser: browser.version(), results }, null, 2) + '\n')
  console.log('PASS: 390/1440 light/dark covers, own-token component fallback, two-line long names, selection, option grouping, stable persisted id and image failure fallback; zero page errors.')
} finally { await browser.close() }
