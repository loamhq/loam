// Actual page/relay and browser clipboard, with synthetic messages only.
import assert from 'node:assert/strict'
import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_ICONS_EVIDENCE
if (!out) throw new Error('Set THREAD_ICONS_EVIDENCE')
await mkdir(out, { recursive: true })
const baseline = process.env.THREAD_ICONS_BASELINE, phase = baseline ? 'before' : 'after'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8'), results = [], id = 'a-quieter-week'
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
try {
  for (const width of [390, 1440, 1920]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = [], checks = []
    const record = await app.f.api('GET', `/threads/${id}`)
    const messages = [
      { id: 'm1', author: 'you', text: '**Thursday sounds good.**\n\nPlease keep the notes and the `Read` step close.' },
      { id: 'm2', author: 'sprout', text: 'We can keep **Friday open** for a slower morning. I’ll gather the loose ends on Thursday and bring the notes here.\n\n- Choose one clear next step.\n- Leave some room for the unexpected.\n\nThe useful details can stay close to the conversation, with enough space to choose what matters. A calmer week begins with a short list, and we can make that list together.' },
    ].map((m) => ({ ...m, kind: 'message', at: '2026-11-11T20:45:00Z', engine: 'claude-code', did: [] }))
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...record, messages, has_more: false, turn: null, turns: [] })
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: new URL(app.url).origin })
    await context.route('**/api/health', (route) => route.fulfill({ json: { configured: true, ok: true, speech: true } }))
    await context.route('**/api/speech/lookup', (route) => route.fulfill({ json: { status: 'none' } }))
    if (baseline) for (const file of ['threads.js', 'threads.css', 'ui.js']) await context.route(`**/${file}`, (route) => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' }) }))
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__icons = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(12000)
    page.on('pageerror', (e) => errors.push(e.message))
    page.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy/.test(m.text())) errors.push(m.text()) })
    const settle = async () => { await page.waitForTimeout(100); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }) }
    const shot = async (label) => {
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      await page.screenshot({ path: path.join(out, `${phase}-${label}-${width}-${theme}.png`), animations: 'disabled' })
    }
    const bring = async (node) => { await node.evaluate((n) => { const pane = n.closest('.tt-stream'); pane.scrollTop += n.getBoundingClientRect().top - pane.getBoundingClientRect().top - 240; pane.dispatchEvent(new Event('scroll')) }); await settle() }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await settle()
      if (!baseline) {
        const active = await page.locator('.tt-row .srow-name').evaluateAll((nodes) => nodes.filter((n) => getComputedStyle(n).animationName !== 'none').map((n) => ({ activity: n.closest('.tt-row').dataset.activity, duration: getComputedStyle(n).animationDuration })))
        assert.ok(active.every((a) => ['working', 'waiting'].includes(a.activity)), 'carried-over sheen stays confined to live subtitles')
      }
      await page.evaluate((id) => __icons.threads.open(id), id)
      await page.waitForFunction(() => !__icons.threads.st.open.loading && !__icons.threads.st.open.refreshing)
      await settle()
      for (const message of messages) {
        const author = message.author === 'you' ? 'person' : 'agent'
        const article = page.locator(`[data-message-id="${message.id}"]`), footer = article.locator('.tt-reply-footer')
        await bring(footer); await page.mouse.move(0, 0); await settle(); await shot(`${author}-rest`)
        if (baseline) {
          assert.equal(await footer.locator('.tt-footer-copy').count(), 0)
          await article.hover(); await settle(); await shot(`${author}-hover`)
          continue
        }
        assert.equal(await page.locator('.tt-message-actions,.tt-reply-action,.tt-message-more').count(), 0)
        const reply = footer.getByRole('button', { name: 'Reply', exact: true }), copy = footer.getByRole('button', { name: 'Copy', exact: true })
        assert.equal(await reply.isVisible(), true); assert.equal(await copy.isVisible(), true)
        assert.deepEqual(await footer.locator('.tt-footer-action,.speakbtn').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('aria-label'))), author === 'agent' ? ['Reply', 'Branch into a new thread', 'Copy', 'Read aloud'] : ['Reply', 'Branch into a new thread', 'Copy'])
        for (const button of [reply, copy]) {
          assert.equal(await button.innerText(), '')
          assert.equal(await button.getAttribute('title'), width === 390 ? null : await button.getAttribute('aria-label'))
          const style = await button.evaluate((n) => { const s = getComputedStyle(n), svg = n.querySelector('svg'); return { color: s.color, background: s.backgroundColor, border: s.borderWidth, width: n.getBoundingClientRect().width, height: n.getBoundingClientRect().height, size: svg.getAttribute('width'), stroke: getComputedStyle(svg).strokeWidth } })
          assert.equal(style.size, '15'); assert.equal(style.stroke, '2.2px'); assert.equal(style.border, '0px'); assert.equal(style.background, 'rgba(0, 0, 0, 0)')
          assert.equal(style.color, await footer.locator('time').evaluate((n) => getComputedStyle(n).color))
          if (width === 390) assert.ok(style.width >= 36 && style.height >= 36)
        }
        const order = await footer.evaluate((n) => [n.querySelector('time'), ...n.querySelectorAll('.tt-footer-action,.speakbtn')].map((n) => { const r = n.getBoundingClientRect(); return { left: r.left, right: r.right, mid: r.y + r.height / 2 } }))
        assert.ok(order.every((r, i) => i === 0 || r.left >= order[i - 1].right && Math.abs(r.mid - order[i - 1].mid) < 2), JSON.stringify(order))
        if (width !== 390) {
          const muted = await reply.evaluate((n) => getComputedStyle(n).color)
          await reply.hover(); await settle(); await shot(`${author}-hover`)
          assert.notEqual(await reply.evaluate((n) => getComputedStyle(n).color), muted)
        } else { await reply.hover(); await shot(`${author}-hover`); await reply.focus(); await shot(`${author}-focus`) }
        await reply.click(); await page.locator('.tt-reply-chip').waitFor()
        assert.equal(await page.evaluate(() => __icons.threads.st.open.replyTo.id), message.id)
        assert.equal(await page.locator('#draft').evaluate((n) => document.activeElement === n), true)
        await page.getByRole('button', { name: 'Cancel reply', exact: true }).click(); await bring(footer)
        // Native browser clipboard, preserving source Markdown rather than rendered text.
        await copy.click(); await article.locator('.tt-footer-copy[data-copied]').waitFor()
        assert.equal(await page.evaluate(() => navigator.clipboard.readText()), message.text)
        assert.equal(await copy.getAttribute('aria-label'), 'Copy')
        await shot(`${author}-copied`)
        await article.locator('.tt-footer-copy[data-copied]').waitFor({ state: 'hidden' })
        assert.equal(await copy.isVisible(), true)
        checks.push({ author, order, copiedMarkdown: message.text, confirmationExpired: true })
      }
      if (!baseline) {
        const article = page.locator('[data-message-id="m2"]')
        await bring(article.locator('.tt-reply-footer'))
        await article.locator('.speakbtn').click(); await page.getByRole('menu', { name: 'Read aloud', exact: true }).waitFor(); await page.keyboard.press('Escape')
        await page.getByRole('menu', { name: 'Read aloud', exact: true }).waitFor({ state: 'hidden' }); await settle()
        await article.locator('.tt-footer-reply').focus(); await page.keyboard.press('Enter')
        await page.locator('.tt-reply-chip').waitFor()
        assert.equal(await page.evaluate(() => __icons.threads.st.open.replyTo.id), 'm2')
        await page.getByRole('button', { name: 'Cancel reply', exact: true }).click()
      }
      assert.deepEqual(errors, [])
      results.push({ width, theme, checks, errors })
      console.log(`PASS ${phase} ${width} ${theme}: ${baseline ? 'prior footer/hover captures; no footer Copy existed' : 'icon order, names, size/stroke, targets, native reply/copy, check expiry and read-aloud'}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, `${phase}-browser.json`), JSON.stringify({ baseline, fixturesOnly: true, results }, null, 2) + '\n')
  const captures = []
  for (const file of (await readdir(out)).filter((f) => f.startsWith(phase + '-') && f.endsWith('.png')).sort()) captures.push({ file, sha256: createHash('sha256').update(await readFile(path.join(out, file))).digest('hex') })
  await writeFile(path.join(out, `${phase}-captures.json`), JSON.stringify(captures, null, 2) + '\n')
} finally { await browser.close() }
