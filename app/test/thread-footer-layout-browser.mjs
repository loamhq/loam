// Real page and relay, synthetic messages; optional external Playwright install.
import assert from 'node:assert/strict'
import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_FOOTER_LAYOUT_EVIDENCE
if (!out) throw new Error('Set THREAD_FOOTER_LAYOUT_EVIDENCE')
await mkdir(out, { recursive: true })
const baseline = process.env.THREAD_FOOTER_LAYOUT_BASELINE, phase = baseline ? 'before' : 'after'
const source = baseline ? execFileSync('git', ['show', `${baseline}:public/app.js`], { encoding: 'utf8' }) : await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const results = [], id = 'a-quieter-week', at = '2026-11-11T20:45:00Z'
const paragraph = 'Leave a little room to think. We can gather the useful details, choose the next step together, and keep Friday open for a slower morning.'
const messages = [
  { id: 'person', author: 'you', text: '**Thursday sounds good.** Let’s bring the notes then.' },
  { id: 'short', author: 'sprout', text: 'The notes are ready.' },
  { id: 'long', author: 'sprout', text: Array.from({ length: 24 }, () => paragraph).join('\n\n') },
].map((m) => ({ ...m, at, kind: 'message', engine: 'claude-code', did: [] }))
const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
const near = (a, b, label) => assert.ok(Math.abs(a - b) < 1, `${label}: ${a} vs ${b}`)
try {
  for (const width of [390, 1440, 1920]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = [], checks = []
    const record = await app.f.api('GET', `/threads/${id}`)
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...record, messages, has_more: false, turn: null, turns: [] })
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme,
      timezoneId: 'America/Los_Angeles', reducedMotion: 'reduce', isMobile: width === 390, hasTouch: width === 390 })
    await context.route('**/api/health', (route) => route.fulfill({ json: { configured: true, ok: true, speech: true } }))
    await context.route('**/api/speech/lookup', (route) => route.fulfill({ json: { status: 'none' } }))
    if (baseline) for (const file of ['threads.js', 'threads.css']) await context.route(`**/${file}`, (route) => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' }) }))
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__layout = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(12000)
    await page.clock.setFixedTime(new Date(at))
    page.on('pageerror', (e) => errors.push(e.message))
    page.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy/.test(m.text())) errors.push(m.text()) })
    const settle = async () => { await page.waitForTimeout(120); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }) }
    const bring = async (node) => { await node.evaluate((n) => { const pane = n.closest('.tt-stream'); pane.scrollTop += n.getBoundingClientRect().top - pane.getBoundingClientRect().top - 380; pane.dispatchEvent(new Event('scroll')) }); await settle() }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate((id) => __layout.threads.open(id), id)
      await page.waitForFunction(() => !__layout.threads.st.open.loading && !__layout.threads.st.open.refreshing)
      for (const message of messages) {
        const article = page.locator(`[data-message-id="${message.id}"]`), footer = article.locator('.tt-reply-footer')
        await bring(footer); await page.mouse.move(0, 0); await settle()
        const layout = await footer.evaluate((n) => {
          const box = (node) => { const r = node.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, width: r.width, height: r.height, mid: r.y + r.height / 2 } }
          const buttons = [...n.querySelectorAll('.tt-footer-action,.speakbtn')]
          const message = n.closest('.tt-message'), content = message.querySelector('.say')
          return { footer: box(n), content: box(content), time: box(n.querySelector('time')),
            domOrder: [...n.querySelectorAll('time,.tt-footer-action,.speakbtn')].map((n) => n.getAttribute('aria-label') || 'time'),
            controls: buttons.map((b) => ({ label: b.getAttribute('aria-label'), ...box(b), color: getComputedStyle(b).color })),
            timeColor: getComputedStyle(n.querySelector('time')).color, overflow: document.documentElement.scrollWidth > innerWidth }
        })
        const labels = ['Reply', 'Branch into a new thread', 'Copy', ...(message.id === 'long' ? ['Read aloud'] : [])]
        assert.deepEqual(layout.controls.map((c) => c.label), labels)
        assert.equal(layout.overflow, false)
        assert.equal(await footer.locator('time').innerText(), '12:45 PM')
        if (baseline) {
          assert.deepEqual(layout.domOrder, [...labels, 'time'])
          assert.ok(layout.controls.at(-1).right <= layout.time.left, 'baseline actions precede time')
        } else {
          assert.deepEqual(layout.domOrder, [...labels, 'time'], 'action DOM and keyboard order are preserved')
          near(layout.footer.left, layout.content.left, 'footer starts at prose/bubble edge')
          near(layout.footer.right, layout.content.right, 'footer spans prose/bubble width')
          near(layout.time.left, layout.content.left, 'time starts at prose/bubble edge')
          near(layout.controls.at(-1).right, layout.content.right, 'last action ends at prose/bubble edge')
          for (const [i, c] of layout.controls.entries()) {
            assert.ok(layout.time.right <= c.left, 'time is left of every action')
            assert.ok(c.width >= 44 && c.height >= 44, '44px action targets retained')
            if (i) near(c.left - layout.controls[i - 1].right, 8, 'shared compact-action spacing')
            assert.ok(Math.abs(c.mid - layout.time.mid) < 2, 'time and glyphs share the row centre')
            assert.equal(c.color, layout.timeColor, 'same quiet tone')
          }
          assert.ok(layout.time.right + 6 <= layout.controls[0].left, 'time has breathing room')
          assert.ok(layout.footer.left >= 14 && layout.footer.right <= width - 14, `column padding keeps actions clear of screen edges: ${JSON.stringify(layout.footer)} at ${width}`)
        }
        checks.push({ message: message.id, ...layout })
        await page.screenshot({ path: path.join(out, `${phase}-${message.id}-${width}-${theme}.png`), animations: 'disabled' })
      }
      if (!baseline) {
        const footer = page.locator('[data-message-id="long"] .tt-reply-footer')
        await footer.locator('.tt-footer-reply').focus(); await page.keyboard.press('Tab')
        assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Branch into a new thread')
        await page.keyboard.press('Tab')
        assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Copy')
        await page.keyboard.press('Tab')
        assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Read aloud')
        await page.keyboard.press('Enter'); await page.getByRole('menu', { name: 'Read aloud', exact: true }).waitFor()
        await page.keyboard.press('Escape'); await page.getByRole('menu', { name: 'Read aloud', exact: true }).waitFor({ state: 'hidden' })
      }
      assert.deepEqual(errors, [])
      results.push({ width, theme, checks, errors })
      console.log(`PASS ${phase} ${width} ${theme}: short/long agent and person; ${baseline ? 'previous actions-first footer' : 'time at left, every action at right, 44px targets, column padding, even gaps, tone and action keyboard order'}`)
    } finally { await context.close(); await app.close() }
  }
  if (!baseline) {
    // If paired captures are present, ensure moving the row did not resize controls or bubbles.
    const before = await readFile(path.join(out, 'before-browser.json'), 'utf8').then(JSON.parse).catch((e) => { if (e.code !== 'ENOENT') throw e })
    if (before) for (const [i, result] of results.entries()) for (const [j, check] of result.checks.entries()) {
      const previous = before.results[i].checks[j]
      near(check.content.width, previous.content.width, 'message width preserved')
      for (const [k, control] of check.controls.entries()) {
        near(control.width, previous.controls[k].width, 'target width preserved')
        near(control.height, previous.controls[k].height, 'target height preserved')
      }
    }
  }
  await writeFile(path.join(out, `${phase}-browser.json`), JSON.stringify({ baseline, fixturesOnly: true, results }, null, 2) + '\n')
  const captures = []
  for (const file of (await readdir(out)).filter((f) => f.startsWith(phase + '-') && f.endsWith('.png')).sort()) captures.push({ file, sha256: createHash('sha256').update(await readFile(path.join(out, file))).digest('hex') })
  await writeFile(path.join(out, `${phase}-captures.json`), JSON.stringify(captures, null, 2) + '\n')
} finally { await browser.close() }
