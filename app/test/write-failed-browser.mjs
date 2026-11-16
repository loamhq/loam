// Real Chromium/relay, synthetic broker: no live disk failure is induced.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { writeFailedHost, saveSentence } from './write-failed-host.js'
import { appAgainst, closing } from './harness.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright-core')
const output = process.env.WRITE_FAILED_EVIDENCE
if (!output) throw new Error('Set WRITE_FAILED_EVIDENCE outside the repository')
const relative = path.relative(process.cwd(), path.resolve(output))
if (!relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)) throw new Error('Evidence must stay outside the repository')
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
const results = []
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const broker = await writeFailedHost(), app = await appAgainst(broker)
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    try {
      const page = await context.newPage(), errors = []
      page.on('pageerror', (e) => errors.push(e.message))
      await page.goto(app.url)
      const open = async () => {
        await page.locator('.srow').first().click()
        await page.locator('.thread-load').waitFor({ state: 'detached' })
        await page.locator('.line.right').waitFor()
      }
      await open()
      broker.fail()
      const notice = page.locator('.save-notice')
      await notice.waitFor()
      assert.equal(await notice.count(), 1)
      assert.equal(await notice.innerText(), saveSentence)
      assert.match(await notice.getAttribute('class'), /quiet/)
      const capture = async (name) => {
        await page.evaluate(() => document.fonts.ready)
        await page.screenshot({ path: path.join(output, `${name}-${width}-${theme}.png`) })
        const geometry = await notice.evaluate((node) => {
          const r = node.getBoundingClientRect(), pane = node.closest('.msgs').getBoundingClientRect()
          return { x: r.x, right: r.right, y: r.y, bottom: r.bottom, paneBottom: pane.bottom, color: getComputedStyle(node).color, overflow: document.documentElement.scrollWidth > innerWidth }
        })
        assert.ok(geometry.x >= 0 && geometry.right <= width)
        assert.ok(geometry.y >= 0 && geometry.bottom <= geometry.paneBottom)
        assert.equal(geometry.overflow, false)
        return geometry
      }
      const live = await capture('live')
      assert.equal(await notice.evaluate((n) => n.previousElementSibling?.querySelector('.say.agent') !== null), true)
      broker.more()
      await page.getByText('The rollback step is covered, too.', { exact: true }).waitFor()
      assert.equal(await notice.count(), 1)
      assert.equal(await notice.evaluate((n) => n.previousElementSibling?.textContent), 'The rollback step is covered, too.')
      broker.finish()
      await page.locator('.save-notice.bad').waitFor()
      assert.equal(await notice.count(), 1)
      const ended = await capture('ended')
      assert.notEqual(ended.color, live.color)
      await page.reload()
      await open()
      await page.locator('.save-notice.bad').waitFor()
      assert.equal(await notice.innerText(), saveSentence)
      assert.equal(await notice.count(), 1)
      assert.equal(await page.locator('.say.agent').count(), 0, 'the lost reply must actually be absent from replay')
      const reloaded = await capture('reloaded-gap')
      assert.equal(await notice.evaluate((n) => n.previousElementSibling?.classList.contains('right')), true)
      assert.deepEqual(errors, [])
      results.push({ fixture: true, width, theme, live, ended, reloaded, pageErrors: errors })
    } finally { await context.close(); await closing(broker, app)() }
  }
  await writeFile(path.join(output, 'results.json'), JSON.stringify(results, null, 2) + '\n')
  console.log(JSON.stringify(results, null, 2))
} finally { await browser.close() }
