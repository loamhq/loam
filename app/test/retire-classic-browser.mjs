import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { retirementFixture } from './retire-classic-fixture.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.RETIRE_CLASSIC_EVIDENCE
if (!out) throw new Error('Set RETIRE_CLASSIC_EVIDENCE')
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const results = []
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await retirementFixture(), errors = []
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width < 1100, hasTouch: width < 1100 })
    const page = await context.newPage()
    page.on('pageerror', (e) => errors.push(e.message))
    const shot = async (name) => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      await page.screenshot({ animations: 'disabled', path: path.join(out, `${name}-${width}-${theme}.png`) })
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.getByRole('button', { name: 'Settings', exact: true }).click()
      await page.locator('.sheet.settings').waitFor()
      assert.equal(await page.getByText('Classic chats', { exact: true }).count(), 0)
      for (const name of ['Running now', 'Appearance', 'Providers', 'Files']) assert.ok(await page.locator('.sheet.settings').getByText(name, { exact: true }).count())
      await shot('settings'); await page.keyboard.press('Escape')
      await page.getByRole('button', { name: 'Thread list actions', exact: true }).click()
      await page.locator('.tt-list-menu').getByRole('button', { name: /Archived threads/ }).click()
      await page.waitForFunction(() => document.querySelectorAll('.tt-row').length === 50)
      for (const count of [100, 150, 153]) {
        await page.locator('.tt-list .scroll').evaluate(n => { n.scrollTop = n.scrollHeight })
        await page.waitForFunction(count => document.querySelectorAll('.tt-row').length === count, count)
      }
      assert.equal(await page.locator('.tt-row').count(), 153)
      const titles = await page.locator('.tt-row .srow-title').allTextContents()
      assert.equal(titles[0], 'A plan worth keeping'); assert.equal(titles.at(-1), 'Archived conversation 153')
      assert.equal(await page.locator('.tt-row time').first().getAttribute('datetime'), app.archives[0].created)
      await shot('archive')
      await page.getByRole('button', { name: 'Search threads', exact: true }).click()
      await page.getByRole('searchbox', { name: 'Find a thread' }).fill('plan worth')
      await page.waitForFunction(() => document.querySelectorAll('.tt-row').length === 1)
      assert.equal(await page.locator('.tt-row').count(), 1)
      await page.locator('.tt-row').click(); await page.locator('[data-message-id="old-answer"]').waitFor()
      await shot('imported-thread')
      assert.match(await page.locator('.tt-stream').innerText(), /Can we keep the original plan/)
      for (const href of ['/sessions/old-chat', '/?session=old-chat']) {
        await page.goto(app.url + href); await page.waitForURL('**/threads/imported-1'); await page.locator('[data-message-id="old-answer"]').waitFor()
      }
      await page.evaluate(() => localStorage.setItem('loam-ui.session', JSON.stringify({ id: 'old-chat' })))
      await page.goto(app.url); await page.waitForURL('**/threads/imported-1')
      assert.equal(await page.evaluate(() => localStorage.getItem('loam-ui.session')), null)
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate(() => navigator.serviceWorker.dispatchEvent(new MessageEvent('message', { data: { session_id: 'old-chat' } })))
      await page.waitForURL('**/threads/imported-1')
      app.setMapping(false)
      await page.goto(app.url + '/?session=missing'); await page.getByRole('alert').filter({ hasText: 'No imported thread' }).waitFor()
      assert.equal(await page.locator('.tt-thread').count(), 0)
      app.setAvailable(false)
      await page.goto(app.url); await page.getByRole('alert').filter({ hasText: 'temporarily unavailable' }).waitFor()
      assert.equal(await page.locator('.tt-list').count(), 1)
      assert.equal(await page.getByText('Classic chats', { exact: true }).count(), 0)
      assert.equal(app.broker.seen.some((r) => /\/v1\/sessions\/[^/]+\/events/.test(r.path)), false)
      assert.equal(app.broker.seen.some((r) => r.path.startsWith('/v1/sessions') && ['POST', 'PATCH'].includes(r.method)), false)
      assert.deepEqual(errors, [])
      results.push({ width, theme, archiveCount: 153, first: titles[0], last: titles.at(-1), originalDate: app.archives[0].created, errors,
        passed: ['settings without Classic', 'archive date ordering including old pin', 'title search', 'imported history', 'session path', 'session query', 'remembered session', 'notification', 'missing mapping', 'thread failure has no classic fallback', 'no session transcript/send requests'] })
      console.log(`PASS ${width} ${theme}: Settings, 153 archives, search, history, four redirects, missing mapping and unavailable threads`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ results }, null, 2) + '\n')
} finally { await browser.close() }
