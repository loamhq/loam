// Opt-in keyboard check through the real page. Uses the older runtime fixture
// to also exercise the compatibility path from both header and Settings.
import assert from 'node:assert/strict'
import { writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.RUNNING_EVIDENCE
if (!out) throw new Error('Set RUNNING_EVIDENCE')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] }), checks = []
try {
  for (const width of [390, 1440]) {
    const app = await oneProductFixture(), context = await browser.newContext({ viewport: { width, height: 844 }, reducedMotion: 'reduce' }), page = await context.newPage()
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.getByTitle('Running now', { exact: true }).click()
      await page.getByRole('dialog', { name: 'Running now', exact: true }).waitFor()
      await page.waitForFunction(() => document.activeElement?.matches('.run-sheet .sheet-title'))
      await page.keyboard.press('Shift+Tab')
      assert.equal(await page.evaluate(() => document.activeElement?.textContent), 'End all idle')
      await page.keyboard.press('Tab')
      assert.equal(await page.evaluate(() => document.activeElement?.closest('.run-sheet') !== null), true)
      assert.match(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), /Close/)
      await page.keyboard.press('Escape'); await page.locator('.run-sheet').waitFor({ state: 'detached' })
      assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('run-indicator')), true)
      await page.getByTitle('Settings', { exact: true }).click()
      if (width === 390) await page.locator('.setrow').filter({ has: page.getByText('Running now', { exact: true }) }).click()
      else await page.locator('.setnav').getByRole('button', { name: 'Running now', exact: true }).click()
      await page.locator('.run-sheet').waitFor()
      await page.keyboard.press('Escape'); await page.locator('.run-sheet').waitFor({ state: 'detached' })
      assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('run-indicator')), true)
      checks.push({ width, keyboardTrap: true, escapeCloses: true, focusRestored: true, settingsEntry: true })
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'keyboard.json'), JSON.stringify({ fixture: true, checks }, null, 2) + '\n')
} finally { await browser.close() }
