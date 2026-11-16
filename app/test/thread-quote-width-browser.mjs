// Actual page/CSS and relay with synthetic replies. External Playwright only.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_QUOTE_WIDTH_EVIDENCE
if (!out) throw new Error('Set THREAD_QUOTE_WIDTH_EVIDENCE')
await mkdir(out, { recursive: true })
const baseline = process.env.THREAD_QUOTE_WIDTH_BASELINE, phase = baseline ? 'before' : 'after'
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const results = [], id = 'a-quieter-week'
const quote = 'Here is how I would think about it from first principles, then one decision to start with. ' + 'The core should own the seams and let each provider supply its own behavior. '.repeat(5)
const words = 'I prefer the core model that you described, with the first-party plugins enabled by default. We can keep the optional pieces separate and still make the first experience useful.'
const unbroken = 'AnUnbrokenQuotedIdentifier'.repeat(100)
const hostile = `A long URL: https://example.com/${'a'.repeat(1800)}\n\nInline code: \`${'identifier'.repeat(150)}\`\n\n\`\`\`js\nconst value = "${'code'.repeat(600)}";\n\`\`\``
const messages = [
  { id: 'm1', author: 'sprout', text: quote },
  { id: 'm2', author: 'you', text: words, reply_to: 'm1' },
  { id: 'm3', author: 'quill', text: words, reply_to: 'm1' },
  { id: 'm4', author: 'sprout', text: unbroken },
  { id: 'm5', author: 'you', text: hostile, reply_to: 'm4' },
  { id: 'm6', author: 'quill', text: hostile, reply_to: 'm4' },
  { id: 'm7', author: 'you', text: hostile },
  { id: 'm8', author: 'you', text: 'Agreed.' },
].map((m) => ({ ...m, kind: 'message', at: '2026-11-11T20:45:00Z', engine: 'claude-code', did: [], steps: [] }))
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
try {
  for (const width of [390, 1440, 1920]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = []
    const record = await app.f.api('GET', `/threads/${id}`)
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...record, messages, has_more: false, turn: null, turns: [] })
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
    app.routes[`GET /v1/threads/${id}/steps`] = (req, res) => json(res, { steps: [] })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, reducedMotion: 'reduce', isMobile: width === 390, hasTouch: width === 390 })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__quoteWidth = { threads };' }))
    if (baseline) await context.route('**/threads.css', (route) => route.fulfill({ contentType: 'text/css', body: execFileSync('git', ['show', `${baseline}:public/threads.css`], { encoding: 'utf8' }) }))
    const page = await context.newPage(); page.setDefaultTimeout(12000)
    page.on('pageerror', (error) => errors.push(error.message))
    const settle = async () => { await page.waitForTimeout(150); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }) }
    const bring = async (selector) => { await page.locator(selector).evaluate((n) => { const pane = n.closest('.tt-stream'); pane.scrollTop += n.getBoundingClientRect().top - pane.getBoundingClientRect().top - 100; pane.dispatchEvent(new Event('scroll')) }); await settle() }
    const capture = async (name) => { if (width !== 1920) await page.screenshot({ path: path.join(out, `${phase}-${name}-${width}-${theme}.png`), animations: 'disabled' }) }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate((id) => __quoteWidth.threads.open(id), id)
      await page.waitForFunction(() => !__quoteWidth.threads.st.open.loading && !__quoteWidth.threads.st.open.refreshing)
      await settle()
      // Unfold the adversarial Markdown so overflow:hidden on long bubbles
      // cannot conceal escaping URLs/code. Keep the normal folding behavior.
      const more = page.getByRole('button', { name: 'Show more', exact: true })
      while (await more.count()) { await more.first().evaluate((n) => n.click()); await settle() }
      // Markdown is progressively formatted on entering the reading window.
      for (const messageId of ['m5', 'm6', 'm7']) {
        await bring(`[data-message-id="${messageId}"] .tt-prose-content`)
        await page.locator(`[data-message-id="${messageId}"] pre`).waitFor({ state: 'attached' })
      }
      await settle()
      await bring('[data-message-id="m2"]')
      await page.locator('[data-message-id="m1"] .tt-footer-reply').evaluate((n) => n.click())
      await settle(); await bring('[data-message-id="m2"]')
      await capture('reply-and-chip')
      const layout = await page.evaluate(() => {
        const rect = (n) => { const r = n.getBoundingClientRect(); return { left: r.left, right: r.right, width: r.width, height: r.height } }
        const pane = document.querySelector('.tt-stream'), paneBox = rect(pane)
        const bubbles = [...pane.querySelectorAll('.tt-bubble')].map((n) => {
          const line = n.closest('.line'), style = getComputedStyle(line), box = rect(line)
          return { id: n.closest('[data-message-id]').dataset.messageId, ...rect(n), allowedLeft: box.left + parseFloat(style.paddingLeft), allowedRight: box.right }
        })
        const quotes = [...document.querySelectorAll('.tt-quote span,.tt-quote-copy span')].map((n) => ({ ...rect(n), client: n.clientWidth, scroll: n.scrollWidth, overflow: getComputedStyle(n).overflowX, ellipsis: getComputedStyle(n).textOverflow, whiteSpace: getComputedStyle(n).whiteSpace, lineHeight: parseFloat(getComputedStyle(n).lineHeight) }))
        return { pane: { ...paneBox, client: pane.clientWidth, scroll: pane.scrollWidth }, bubbles, quotes, chip: rect(document.querySelector('.tt-reply-chip')), viewport: innerWidth, document: document.documentElement.scrollWidth,
          codes: [...pane.querySelectorAll('pre')].map((n) => ({ ...rect(n), client: n.clientWidth, scroll: n.scrollWidth })),
          short: rect(document.querySelector('[data-message-id="m8"] .tt-bubble')) }
      })
      if (baseline) {
        assert.ok(layout.bubbles.some((b) => b.left < b.allowedLeft - 1 || b.right > b.allowedRight + 1), 'baseline reproduces the escaped bubble')
      } else {
        assert.ok(layout.pane.scroll <= layout.pane.client, `transcript overflow: ${JSON.stringify(layout.pane)}`)
        assert.ok(layout.document <= layout.viewport, 'page has no horizontal overflow')
        for (const bubble of layout.bubbles) {
          assert.ok(bubble.left >= bubble.allowedLeft - 1 && bubble.right <= bubble.allowedRight + 1, `bubble respects its normal inset/maximum: ${JSON.stringify(bubble)}`)
        }
        const replied = layout.bubbles.find((b) => b.id === 'm5'), plain = layout.bubbles.find((b) => b.id === 'm7')
        assert.ok(Math.abs(replied.width - plain.width) < 1, 'quote and ordinary long bubbles have the same maximum')
        assert.ok(layout.short.width < plain.width / 2, 'short bubbles retain their natural width')
        for (const q of layout.quotes) {
          assert.equal(q.overflow, 'hidden'); assert.equal(q.ellipsis, 'ellipsis'); assert.equal(q.whiteSpace, 'nowrap')
          assert.ok(q.scroll > q.client, 'long quote really truncates'); assert.ok(q.height <= q.lineHeight + 1, 'quote stays on one line')
          assert.ok(q.left >= layout.pane.left && q.right <= layout.pane.right, 'human, agent and composer quotes fit')
        }
        assert.equal(layout.codes.length, 3, 'human reply, agent reply and plain human all render long code')
        assert.ok(layout.codes.every((c) => c.scroll > c.client), 'long code remains locally scrollable')
        assert.ok(await page.locator('[data-message-id="m2"] .tt-prose-content p').evaluate((n) => n.getBoundingClientRect().height > parseFloat(getComputedStyle(n).lineHeight)), 'reply prose wraps normally')
      }
      await page.getByRole('button', { name: 'Cancel reply', exact: true }).click()
      await bring('[data-message-id="m5"]'); await capture('unbroken-url-code')
      await page.locator('[data-message-id="m4"] .tt-footer-reply').evaluate((n) => n.click())
      await settle()
      const chip = await page.locator('.tt-reply-chip').evaluate((n) => ({ client: n.clientWidth, scroll: n.scrollWidth, left: n.getBoundingClientRect().left, right: n.getBoundingClientRect().right }))
      assert.ok(chip.scroll <= chip.client && chip.left >= 0 && chip.right <= width, 'unbroken composer quote fits and cancel stays available')
      // Preserve baseline exceptions in the evidence; only the fixed page is
      // required to be error-free while exercising the deliberately bad sizes.
      if (!baseline) assert.deepEqual(errors, [])
      results.push({ width, theme, layout, unbrokenChip: chip, errors })
      console.log(`${baseline ? 'REPRODUCED' : 'PASS'} ${width} ${theme}: reply widths, quote ellipsis, transcript/URL/code overflow and composer chip`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, `${phase}-browser.json`), JSON.stringify({ baseline, results }, null, 2) + '\n')
} finally { await browser.close() }
