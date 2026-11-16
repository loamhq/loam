// Real page/relay, synthetic messages; external Playwright and Chromium only.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.MESSAGE_COLLAPSE_EVIDENCE
if (!out) throw new Error('Set MESSAGE_COLLAPSE_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8'), results = []
const id = 'a-quieter-week', at = '2026-11-11T20:45:00Z'
const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
const near = (a, b) => assert.ok(Math.abs(a - b) < 1, `${a} differs from ${b}`)
try {
  for (const width of [390, 1440]) for (const look of ['latte', 'plain']) for (const mode of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = []
    const record = await app.f.api('GET', `/threads/${id}`)
    const messages = [
      { id: 'short', text: 'Keep Friday open.' },
      { id: 'long', text: [
        'When I send a long message, it has "show more," which I love. But placing the control below the bubble makes the design look awkward, especially with the options below that.',
        '> I would rather the fading start higher, and then "Show more" sit at the bottom within the bubble.',
        'This paragraph should never appear below the collapsed control. Keep the first line at the top, even after expanding and collapsing the message.',
        '- Keep the options together.\n- Keep the bubble quiet.',
        'The final paragraph has a [reference link](https://example.com/reference) so keyboard focus also exercises content beyond the fold.',
      ].join('\n\n') },
    ].map(m => ({ ...m, author: 'you', kind: 'message', at, did: [] }))
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...record, messages, has_more: false, turn: null, turns: [] })
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
    const settings = { mode, theme: look, size: 100 }
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: settings, desktop: settings })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__collapse = { threads };' }))
    const page = await context.newPage()
    page.on('pageerror', e => errors.push(e.message))
    const settle = () => page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
    const geometry = () => page.evaluate(() => {
      const box = n => { const b = n.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, left: b.left, right: b.right, width: b.width, height: b.height } }
      const row = id => {
        const article = document.querySelector(`[data-message-id="${id}"]`), bubble = article.querySelector('.say.user'), control = bubble.querySelector('.morebtn')
        const first = bubble.querySelector('p'), range = document.createRange()
        if (first) { range.setStart(first.firstChild, 0); range.setEnd(first.firstChild, 1) }
        return { bubble: box(bubble), footer: box(article.querySelector('.tt-reply-footer')), control: control ? box(control) : null,
          fade: getComputedStyle(bubble, '::after').backgroundImage, fadeHeight: getComputedStyle(bubble, '::after').height,
          surface: getComputedStyle(bubble).backgroundColor,
          proseBottom: bubble.querySelector('.user-message-body').getBoundingClientRect().bottom,
          bodyScrollTop: bubble.querySelector('.user-message-body').scrollTop,
          scrollTop: bubble.scrollTop, firstLine: first ? box(range) : null, firstText: first?.textContent,
          clamp: parseFloat(getComputedStyle(bubble).getPropertyValue('--mine-max')) }
      }
      return { short: row('short'), long: row('long'), overflow: document.documentElement.scrollWidth > innerWidth }
    })
    const check = g => {
      const { bubble: b, control: c } = g.long
      assert.ok(c.top >= b.top && c.bottom <= b.bottom && c.left >= b.left && c.right <= b.right, 'control inside bubble')
      near(c.bottom, b.bottom); near(c.width, b.width)
      assert.ok(c.height >= 44, 'full-width touch target')
      near(g.long.footer.top - b.bottom, g.short.footer.top - g.short.bubble.bottom)
      assert.equal(g.short.control, null); assert.equal(g.overflow, false)
    }
    const checkCollapsed = g => {
      check(g)
      near(g.long.bubble.height, g.long.clamp)
      assert.equal(g.long.scrollTop, 0, 'collapsed content must not scroll away from the first line')
      assert.equal(g.long.bodyScrollTop, 0, 'the clipped body must not become a scroller either')
      assert.ok(g.long.proseBottom >= g.long.control.bottom - 10, 'the clipped body continues behind the mark')
      assert.ok(g.long.firstLine.top >= g.long.bubble.top && g.long.firstLine.top < g.long.bubble.top + 16, 'the first line remains at the bubble top')
      assert.ok(g.long.firstText.startsWith('When I send a long message'), 'distinct opening paragraph')
    }
    // Compare actual paint with the body hidden. Faint text must remain
    // visible in the lower line behind the bare Show more mark.
    const checkPaint = async label => {
      const bubble = page.locator('[data-message-id="long"] .say.user')
      await bubble.scrollIntoViewIfNeeded(); await settle()
      const g = await geometry(), b = g.long.bubble
      const clip = { x: b.left + 3, y: b.bottom - 43, width: b.width - 6, height: g.long.footer.top - b.bottom + 43 }
      const actual = await page.screenshot({ clip, animations: 'disabled' })
      const hide = hidden => bubble.evaluate((n, hidden) => {
        for (const child of n.children) if (!child.matches('.morebtn')) child.style.visibility = hidden ? 'hidden' : ''
      }, hidden)
      await hide(true)
      let expected
      try { expected = await page.screenshot({ clip, animations: 'disabled' }) } finally { await hide(false) }
      await writeFile(path.join(out, `${label}-${width}-${look}-${mode}-strip.png`), actual)
      if (actual.equals(expected)) {
        await writeFile(path.join(out, `${label}-${width}-${look}-${mode}-expected.png`), expected)
        await page.screenshot({ path: path.join(out, `${label}-${width}-${look}-${mode}-failure.png`), animations: 'disabled' })
      }
      assert.ok(!actual.equals(expected), `${label}: faint body text must remain behind the mark`)
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate(id => __collapse.threads.open(id), id)
      const more = page.getByRole('button', { name: 'Show more', exact: true })
      await more.waitFor(); await settle()
      await page.locator('[data-message-id="long"] blockquote').waitFor({ state: 'attached' })
      assert.deepEqual(await page.evaluate(() => [document.documentElement.dataset.look, document.documentElement.dataset.theme]), [look, mode])
      const collapsed = await geometry(); checkCollapsed(collapsed); await checkPaint('opening')
      const stop = collapsed.long.fade.match(/color\(srgb ([\d.]+) ([\d.]+) ([\d.]+) \/ ([\d.]+)\)/)
      assert.ok(stop, 'browser resolved the paper colour mix')
      assert.equal(`rgb(${stop.slice(1,4).map(n => Math.round(Number(n)*255)).join(', ')})`, collapsed.long.surface, 'fade uses the current bubble surface')
      assert.equal(Number(stop[4]), .98, 'gradient stays translucent through the lower line')
      near(collapsed.long.control.height, 44)
      assert.ok(parseFloat(collapsed.long.fadeHeight) >= 64, 'fade crosses about three lines')
      await more.focus(); checkCollapsed(await geometry())
      const focus = await more.evaluate(n => ({ visible: n.matches(':focus-visible'), style: getComputedStyle(n).outlineStyle, offset: getComputedStyle(n).outlineOffset }))
      assert.equal(focus.visible, true); assert.equal(focus.style, 'solid'); assert.equal(focus.offset, '-3px')
      if (width === 390 && look === 'latte' && mode === 'light') await page.screenshot({ path: path.join(out, 'keyboard-focus-390-light.png'), animations: 'disabled' })
      await more.evaluate(n => n.blur())
      if (look === 'latte') await page.screenshot({ path: path.join(out, `collapsed-${width}-${mode}.png`), animations: 'disabled' })
      // Tap well away from the label, on the upper edge of the faded strip.
      await more.click({ position: { x: 5, y: 5 } })
      const less = page.getByRole('button', { name: 'Show less', exact: true })
      await less.waitFor(); await settle()
      const expanded = await geometry(); check(expanded)
      assert.equal(expanded.long.fade, 'none'); assert.ok(expanded.long.bubble.height > collapsed.long.bubble.height)
      assert.ok(expanded.long.proseBottom <= expanded.long.control.top, 'expanded text clears Show less')
      assert.equal(await less.getAttribute('aria-expanded'), 'true')
      await less.focus(); await page.keyboard.press('Enter'); await more.waitFor(); await settle()
      assert.equal(await more.getAttribute('aria-expanded'), 'false'); checkCollapsed(await geometry()); await checkPaint('refolded')
      // Native focus can scroll overflow:hidden even when no scrollbar is
      // visible. This must never move the message body or its control strip.
      await page.locator('[data-message-id="long"] .say.user a').focus(); await settle()
      const afterBodyFocus = await geometry()
      await page.screenshot({ path: path.join(out, `body-focus-${width}-${look}-${mode}.png`), animations: 'disabled' })
      console.log(`BODY FOCUS ${width} ${look} ${mode}: scrollTop=${afterBodyFocus.long.scrollTop}, first-line offset=${afterBodyFocus.long.firstLine.top - afterBodyFocus.long.bubble.top}`)
      checkCollapsed(afterBodyFocus); await checkPaint('body-focus')
      await page.locator('[data-message-id="long"] .say.user').evaluate(n => { n.scrollTop = 80; n.querySelector('.user-message-body').scrollTop = 80 })
      checkCollapsed(await geometry())
      assert.deepEqual(errors, [])
      results.push({ width, look, mode, collapsed, expanded, focus, afterBodyFocus, errors })
      console.log(`PASS ${width} ${look} ${mode}: first line, fixed clip, translucent painted fade, no inner scrolling, contained control, footer gap, tap and keyboard toggle/focus`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
} finally { await browser.close() }
