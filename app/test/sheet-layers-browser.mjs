// Audit the remaining actual overlays: Settings' voice picker and Activity dialogs.
// Other sheet transitions replace their former sheet; sheet-gestures-browser audits those.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { profileHost } from './agent-profile-host.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.SHEET_LAYERS_EVIDENCE
if (!out) throw new Error('Set SHEET_LAYERS_EVIDENCE')
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8'), results = []
try {
  for (const height of [844, 660]) for (const mode of ['light', 'dark']) {
    const app = await profileHost(), stamp = `390x${height}-${mode}`, errors = []
    const context = await browser.newContext({ viewport: { width: 390, height }, colorScheme: mode, isMobile: true, hasTouch: true, reducedMotion: 'reduce' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__layers={state,threads,activity,schedule,goSettings,goActivity};' }))
    const page = await context.newPage(); page.setDefaultTimeout(12000); page.on('pageerror', e => errors.push(e.message))
    const check = async (name, surface) => {
      await surface.waitFor(); await page.waitForTimeout(160)
      const box = await surface.boundingBox()
      assert.ok(box.x >= -1 && box.y >= -1 && box.x + box.width <= 391 && box.y + box.height <= height + 1, `${name}: ${JSON.stringify(box)}`)
      // Scroll through the overlay and prove the final action/option is reachable.
      await surface.evaluate(node => { node.scrollTop = node.scrollHeight })
      const last = surface.locator('button:not(:disabled)').last()
      const lastBox = await last.boundingBox()
      assert.ok(lastBox.y >= box.y - 1 && lastBox.y + lastBox.height <= box.y + box.height + 1, `${name}: last control reachable`)
      assert.equal(await surface.evaluate(node => getComputedStyle(node).overscrollBehaviorY), 'contain')
      await page.screenshot({ path: path.join(out, `${name}-${stamp}.png`), animations: 'disabled' })
      results.push({ stamp, name, box, last: lastBox })
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.getByRole('button', { name: 'Settings', exact: true }).first().click()
      await page.evaluate(() => __layers.goSettings('read-aloud')); await page.locator('#files-voice').click()
      await check('settings-voice', page.locator('.voice-menu'))
      await page.keyboard.press('Escape'); assert.equal(await page.locator('.sheet.settings').count(), 1)
      await page.keyboard.press('Escape')
      // On phones Activity is a page; its two native dialogs are the only other
      // independent layers. Long host strings force the same constrained case.
      await page.evaluate(() => __layers.goActivity())
      await page.locator('.activity-page').waitFor()
      await page.waitForFunction(() => __layers.activity.st.loaded && !__layers.activity.st.loading)
      await page.evaluate(() => {
        __layers.activity.st.detail = { item: { id: 'detail-proof', kind: 'fire', agent: 'sprout', outcome: 'failed', why: 'A long host explanation. '.repeat(80), ts: new Date().toISOString() } }
        __layers.schedule()
      })
      const detail = page.locator('dialog.activity-detail[open]')
      await check('activity-detail', detail); await page.keyboard.press('Escape'); await detail.waitFor({ state: 'hidden' })
      await page.waitForTimeout(160)
      await page.evaluate(() => { __layers.activity.st.confirm = { job: { job: 'fixture', title: 'A long job title. '.repeat(80) }, queued: true }; __layers.schedule() })
      const confirm = page.locator('dialog.activity-confirm[open]')
      await check('activity-confirm', confirm); await page.keyboard.press('Escape'); await confirm.waitFor({ state: 'hidden' })
      assert.deepEqual(errors, []); console.log(`PASS layers ${stamp}`)
    } catch (error) { await page.screenshot({ path: path.join(out, `failure-${stamp}.png`) }); throw error }
    finally { await context.close(); await app.close() }
  }
} finally { await browser.close(); await writeFile(path.join(out, 'results.json'), JSON.stringify(results, null, 2) + '\n') }
