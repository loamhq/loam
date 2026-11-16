import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { openingFixture, openingId } from './thread-opening-fixture.mjs'

const out = process.env.THREAD_OPENING_EVIDENCE
if (!out) throw new Error('Set THREAD_OPENING_EVIDENCE')
await mkdir(out, { recursive: true })
const baseline = process.argv.includes('--baseline')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const reference = process.env.THREAD_OPENING_PUBLIC
const source = await readFile(reference ? path.join(reference, 'app.js') : new URL('../public/app.js', import.meta.url), 'utf8')
const results = []
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await openingFixture(), errors = []
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, reducedMotion: 'reduce' })
    if (reference) for (const [pattern, contentType] of [['**/*.js', 'text/javascript'], ['**/*.css', 'text/css']]) {
      await context.route(pattern, async (route) => route.fulfill({ contentType, body: await readFile(path.join(reference, new URL(route.request().url()).pathname.slice(1))) }))
    }
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__opening = { threads, schedule, goChats };' }))
    try {
      const cold = [], returned = []
      const measure = (page) => page.evaluate((id) => new Promise((resolve) => {
        const start = performance.now()
        const paint = () => {
          const pane = document.querySelector('.tt-stream'), nodes = pane?.querySelectorAll('[data-message-id]')
          const painted = [...(nodes ?? [])].some((node) => { const b = node.getBoundingClientRect(), p = pane.getBoundingClientRect(); return b.bottom > p.top && b.top < p.bottom && node.textContent.trim() })
          if (painted && !document.querySelector('.thread-load')) requestAnimationFrame(() => resolve(performance.now() - start))
          else requestAnimationFrame(paint)
        }
        requestAnimationFrame(paint); __opening.threads.open(id)
      }), openingId)
      for (let n = 0; n < 5; n++) {
        const page = await context.newPage(); page.on('pageerror', (e) => errors.push(e.message))
        await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
        await page.evaluate(() => document.fonts.ready)
        cold.push(await measure(page))
        await page.waitForFunction(() => !__opening.threads.st.open.loading && !__opening.threads.st.open.refreshing)
        if (!n) await page.screenshot({ path: path.join(out, `${baseline ? 'before' : 'after'}-${width}-${theme}.png`) })
        await page.evaluate(() => { __opening.threads.close(); __opening.schedule() })
        await page.locator('.tt-stream').waitFor({ state: 'detached' })
        returned.push(await measure(page))
        await page.close()
      }
      assert.deepEqual(errors, [])
      results.push({ width, theme, source: 'synthetic: 38 messages, 19 paragraphs per reply; 80 ms read latency',
        coldTapToFirstVisibleMessageMs: cold.map((n) => Math.round(n * 10) / 10), returnTapToFirstVisibleMessageMs: returned.map((n) => Math.round(n * 10) / 10), reads: app.reads, pageErrors: errors })
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
await writeFile(path.join(out, `${baseline ? 'before' : 'after'}-timings.json`), JSON.stringify(results, null, 2) + '\n')
console.log(JSON.stringify(results, null, 2))
