// Read-only production inspection. Supply a URL and, when needed, an existing
// authenticated browser state outside the repo. Never save browser credentials.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { inspectColourFamilies } from './colour-families.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const url = process.env.LIVE_URL, out = process.env.LIVE_EVIDENCE
assert.ok(url && out, 'Set LIVE_URL and LIVE_EVIDENCE; optional LOAM_BROWSER_STATE supplies existing authentication.')
await mkdir(out, { recursive: true })
const expected = {
  'persona-olive': '#6E7A52', 'persona-slate': '#4F6478', 'persona-rust': '#B0684A',
  'persona-plum': '#7A5A72', 'persona-teal': '#4E7A72', 'persona-rose': '#9C4F44',
  'persona-gold': '#8A7D4A', 'persona-blue': '#5C6E8A', 'persona-taupe': '#8C6A4E',
  'persona-sage': '#6B8A6E', 'persona-mauve': '#8A5E80',
}
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
const results = []
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, reducedMotion: 'reduce', isMobile: width === 390, hasTouch: width === 390,
      ...(process.env.LOAM_BROWSER_STATE ? { storageState: process.env.LOAM_BROWSER_STATE } : {}) })
    try {
      const page = await context.newPage(), errors = []
      page.on('pageerror', e => errors.push(e.message))
      await page.goto(url)
      const available = await page.locator('.tt-shell').first().waitFor({ timeout: 15000 }).then(() => true, () => false)
      if (!available) {
        results.push({ width, mode, result: 'blocked', reason: 'The application did not appear; authentication may be required.' })
        throw new Error(results.at(-1).reason)
      }
      await page.evaluate(() => document.fonts.ready)
      // Inspect both palettes without changing the host's saved appearance.
      // These DOM-only overrides are discarded with this browser context.
      await page.evaluate(mode => { document.documentElement.dataset.look = 'latte'; document.documentElement.dataset.theme = mode }, mode)
      const tokens = await page.evaluate(names => { const s = getComputedStyle(document.documentElement); return Object.fromEntries(names.map(k => [k, s.getPropertyValue('--' + k).trim()])) }, [...Object.keys(expected), 'paper-page', 'paper-ink', 'paper-card', 'latte-foam', 'latte-foam-ink', 'latte-espresso', 'latte-espresso-ink'])
      for (const [key, value] of Object.entries(expected)) assert.equal(tokens[key].toUpperCase(), value)
      assert.equal(tokens['paper-page'].toUpperCase(), mode === 'light' ? '#EEEAE3' : '#1A1917')
      const violations = await page.evaluate(inspectColourFamilies)
      assert.deepEqual(violations, [])
      assert.deepEqual(errors, [])
      const filename = `live-${width}-${mode}.png`
      await page.screenshot({ path: path.join(out, filename), animations: 'disabled' })
      results.push({ width, mode, result: 'passed', appearance: 'DOM-only Latte/mode override; no preference writes', tokens, violations, errors, filename })
    } finally { await context.close() }
  }
} finally {
  await writeFile(path.join(out, 'live.json'), JSON.stringify({ browser: browser.version(), results }, null, 2) + '\n')
  await browser.close()
}
console.log('PASS: live computed tokens, eleven fixed hues, rendered families and screenshots at 390/1440 in light/dark.')
