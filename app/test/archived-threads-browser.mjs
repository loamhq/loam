// Local page + relay with 500 synthetic archived summaries; no live host.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { oneProductFixture } from './one-product-fixture.mjs'
const out = process.env.ARCHIVED_EVIDENCE
if (!out) throw new Error('Set ARCHIVED_EVIDENCE')
await mkdir(out, { recursive: true })
const baseline = process.env.ARCHIVED_BASELINE === '1'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const app = await oneProductFixture(), results = []
const listing = await app.f.api('GET', '/threads'), base = listing.threads[1]
const archived = Array.from({ length: 500 }, (_, i) => ({ ...base, id: `archive-${i}`, title: `Archived thought ${String(i).padStart(3, '0')}`, archived: true, pinned: false, general: false, working: 0, unread: 0,
  last: { ...base.last, text: `Notes to revisit ${i}`, at: new Date(Date.UTC(2026, 9, 31, 0, -i)).toISOString() },
  members: base.members.map(m => ({ ...m, working: false, background: 0 })) }))
const rows = [...listing.threads.filter(t => !t.archived), ...archived]
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
app.routes['GET /v1/threads'] = (req, res) => json(res, { ...listing, threads: rows })
app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'threads.snapshot', threads: rows, agents: app.f.agents })}\n\n`) }
const appearance = { phone: { mode: 'light', size: 100, theme: 'latte' }, desktop: { mode: 'light', size: 100, theme: 'latte' } }
app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, appearance)
app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => { for (const [kind, changes] of Object.entries(JSON.parse(raw))) Object.assign(appearance[kind], changes); json(res, appearance) }
app.routes['POST /v1/threads/a-quieter-week/attachments'] = (req, res, url, raw) => {
  const image = raw.includes('filename="example.png"'), name = image ? 'example.png' : 'note.txt'
  json(res, { attachments: [{ path: `attachments/${name}`, name, size: image ? 68 : 6, type: image ? 'image/png' : 'text/plain' }] })
}
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const settle = page => page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
try {
  for (const width of [390, 1440]) {
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, reducedMotion: 'reduce' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__archiveTest = { threads, schedule, render, state, intake, theme };' }))
    const page = await context.newPage(), errors = []
    page.setDefaultTimeout(10000)
    page.on('pageerror', e => errors.push(e.message))
    await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await settle(page)
    // Measure the same synchronous transition in both revisions, through the
    // real renderer, ending two animation frames later (one paint opportunity).
    const timings = []
    for (let trial = 0; trial < 5; trial++) {
      await page.evaluate(() => { __archiveTest.threads.st.archived = false; __archiveTest.render() })
      timings.push(await page.evaluate(async () => {
        const start = performance.now(); __archiveTest.threads.st.archived = true; __archiveTest.render()
        const renderMs = performance.now() - start
        await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame)
        return { renderMs, paintMs: performance.now() - start, rows: document.querySelectorAll('.tt-row').length }
      }))
    }
    // Traverse at a fixed speed with a redraw every 500ms, modelling live list updates.
    const scroll = await page.evaluate(async () => {
      const pane = document.querySelector('.tt-list .scroll'), gaps = []
      let last = performance.now(), start = last, refresh = last
      await new Promise(resolve => {
        function frame(now) {
          gaps.push(now - last); last = now; pane.scrollTop += 50
          if (now - refresh >= 500) { __archiveTest.schedule(); refresh = now }
          if (now - start < 2500) requestAnimationFrame(frame); else resolve()
        }
        requestAnimationFrame(frame)
      })
      gaps.sort((a, b) => a - b)
      return { frames: gaps.length, p95FrameMs: gaps[Math.floor(gaps.length * .95)], maxFrameMs: gaps.at(-1), framesOver34ms: gaps.filter(x => x > 34).length }
    })
    if (!baseline) {
      // Entry via header menu; each promised exit, at both widths.
      await page.evaluate(() => { __archiveTest.threads.st.archived = false; __archiveTest.schedule() })
      const enter = async () => {
        await page.getByRole('button', { name: 'Thread list actions', exact: true }).click()
        await page.locator('.tt-list-menu').getByRole('button', { name: /Archived threads/ }).click()
        await page.getByRole('button', { name: 'Back to Chats', exact: true }).waitFor()
        assert.equal(await page.locator('.tt-list .display').first().textContent(), 'Archived')
      }
      await enter()
      assert.equal(await page.locator('.tt-row').count(), 50)
      await page.screenshot({ path: path.join(out, `entry-${width}.png`) })
      if (width === 390) {
        for (const size of [100, 125]) {
          await page.setViewportSize({ width: 320, height: 844 })
          await page.evaluate(size => __archiveTest.theme.set({ size }), size)
          await settle(page)
          const overlaps = await page.locator('.tt-archived-head').evaluate(n => {
            const title = n.querySelector('.display').getBoundingClientRect()
            return [...n.querySelectorAll('button')].some(b => {
              const r = b.getBoundingClientRect()
              return r.left < 0 || r.right > innerWidth || (r.left < title.right && r.right > title.left && r.top < title.bottom && r.bottom > title.top)
            })
          })
          assert.equal(overlaps, false, `compact archived header at ${size}%`)
        }
        await page.evaluate(() => __archiveTest.theme.set({ size: 100 }))
        await page.setViewportSize({ width: 390, height: 844 }); await settle(page)
      }
      await page.getByRole('button', { name: 'Back to Chats', exact: true }).click()
      await page.getByRole('button', { name: 'Back to Chats', exact: true }).waitFor({ state: 'hidden' })
      await enter(); await page.keyboard.press('Escape')
      await page.getByRole('button', { name: 'Back to Chats', exact: true }).waitFor({ state: 'hidden' })
      await enter(); await page.getByRole('button', { name: 'Chats', exact: true }).click()
      await page.getByRole('button', { name: 'Back to Chats', exact: true }).waitFor({ state: 'hidden' })
      await enter()
      // Search finds a row outside the rendered page, including last-message text.
      await page.getByRole('button', { name: 'Search threads', exact: true }).click()
      await page.getByRole('searchbox', { name: 'Find a thread' }).fill('Notes to revisit 499')
      await page.locator('.tt-row').filter({ hasText: 'Archived thought 499' }).waitFor()
      assert.equal(await page.locator('.tt-row').count(), 1)
      await page.getByRole('searchbox', { name: 'Find a thread' }).fill('')
      await page.waitForFunction(() => document.querySelectorAll('.tt-row').length === 50)
      for (let count = 100; count <= 500; count += 50) {
        await page.locator('.tt-list .scroll').evaluate(n => { n.scrollTop = n.scrollHeight })
        await page.waitForFunction(count => document.querySelectorAll('.tt-row').length === count, count)
      }
      assert.equal(await page.locator('.tt-row').count(), 500)
      assert.equal(await page.getByRole('button', { name: 'Load more archived threads' }).count(), 0)
      await page.screenshot({ path: path.join(out, `archived-${width}.png`) })
      await page.getByRole('button', { name: 'Back to Chats', exact: true }).click()
      await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click()
      await page.locator('#draft').waitFor()
      if (width === 1440) {
        await enter(); await page.keyboard.press('Escape')
        await page.getByRole('button', { name: 'Back to Chats', exact: true }).waitFor({ state: 'hidden' })
        assert.equal(await page.locator('.tt-thread').count(), 1, 'Escape leaves the open conversation in place')
      }
      const controls = []
      for (const look of ['latte', 'plain']) for (const mode of ['light', 'dark']) {
        await page.evaluate(({ look, mode }) => __archiveTest.theme.set({ theme: look, mode }), { look, mode })
        // Use the actual intake/upload path for an image and a text attachment.
        await page.evaluate(() => {
          const image = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII='), c => c.charCodeAt(0))
          __archiveTest.intake([{ file: new File([image], 'example.png', { type: 'image/png' }), name: 'example.png' },
            { file: new File(['A note'], 'note.txt', { type: 'text/plain' }), name: 'note.txt' }])
        })
        await page.waitForFunction(() => __archiveTest.state.attach.items.length === 2 && __archiveTest.state.attach.items.every(x => x.path))
        // Pointer interaction has no focus ring. Keyboard Tab does.
        const dismiss = page.locator('.attachment-chip-x')
        assert.equal(await dismiss.count(), 2)
        assert.deepEqual(await page.locator('.attachment-chip-name').allTextContents(), ['example.png', 'note.txt'])
        assert.deepEqual(await page.evaluate(() => __archiveTest.state.attach.items.map(x => x.kind)), ['image', 'file'])
        assert.equal(await page.evaluate(() => document.documentElement.dataset.look), look)
        assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), mode)
        await page.mouse.click(2, 2)
        const styles = await dismiss.evaluateAll(nodes => nodes.map(n => {
          const s = getComputedStyle(n), circle = getComputedStyle(n, '::before'), r = n.getBoundingClientRect()
          return { border: s.borderTopWidth, radius: s.borderRadius, background: s.backgroundColor, outline: s.outlineStyle,
            width: r.width, height: r.height, circleWidth: circle.width, circleRadius: circle.borderRadius, circleBackground: circle.backgroundColor,
            circleOutline: circle.outlineStyle, color: s.color }
        }))
        for (const s of styles) {
          assert.equal(s.border, '0px'); assert.equal(s.outline, 'none'); assert.equal(s.circleOutline, 'none')
          assert.equal(s.background, 'rgba(0, 0, 0, 0)'); assert.equal(s.radius, '999px')
          assert.ok(s.width >= 44 && s.height >= 44, JSON.stringify(s))
          assert.equal(s.circleWidth, '26px'); assert.equal(s.circleRadius, '999px')
          assert.notEqual(s.circleBackground, 'rgba(0, 0, 0, 0)')
        }
        await page.screenshot({ path: path.join(out, `attachments-${width}-${look}-${mode}.png`) })
        await dismiss.first().focus()
        await page.keyboard.press('Tab')
        await page.keyboard.press('Shift+Tab')
        assert.equal(await dismiss.first().evaluate(n => n.matches(':focus-visible')), true)
        assert.equal(await dismiss.first().evaluate(n => getComputedStyle(n, '::before').outlineWidth), '2px')
        if (width === 390 && look === 'latte' && mode === 'light') await page.screenshot({ path: path.join(out, 'attachment-keyboard-focus.png') })
        controls.push({ look, mode, styles })
        await page.keyboard.press('Enter')
        await page.waitForFunction(() => document.querySelectorAll('.attachment-chip-x').length === 1)
        await page.locator('.attachment-chip-x').click()
        await page.waitForFunction(() => !document.querySelector('.attachment-chip-x'))
      }
      results.push({ width, attachmentControls: controls })
    }
    results.push({ width, timings, scroll, errors })
    assert.deepEqual(errors, [])
    await context.close()
  }
} finally { await browser.close(); await app.close() }
await writeFile(path.join(out, baseline ? 'baseline.json' : 'browser.json'), JSON.stringify(results, null, 2) + '\n')
console.log(JSON.stringify(results, null, 2))
