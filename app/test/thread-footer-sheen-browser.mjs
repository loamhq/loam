// Real local page and relay, synthetic records only. Optional Playwright install.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_FOOTER_EVIDENCE
if (!out) throw new Error('Set THREAD_FOOTER_EVIDENCE')
await mkdir(out, { recursive: true })
const baseline = process.env.THREAD_FOOTER_BASELINE
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8'), results = []
const id = 'a-quieter-week', phase = baseline ? 'before' : 'after'
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
try {
  for (const width of [1440, 1920, 390, 899, 900]) for (const theme of width >= 1440 || width === 390 ? ['light', 'dark'] : ['light']) {
    if (baseline && width < 1440) continue
    const app = await oneProductFixture(), errors = [], posts = []
    const original = await app.f.api('GET', `/threads/${id}`)
    const member = (persona, extra = {}) => ({ persona, engine: 'claude-code', working: false, background: 0, ...extra })
    const row = (key, title, members, extra = {}) => ({ ...original.thread, id: key, title, members, default: members[0].persona,
      pinned: false, unread: 0, icon: null, working: members.filter((m) => m.working).length,
      background: members.reduce((n, m) => n + m.background, 0), ...extra })
    const rows = [row('general', 'General', [member('sprout')], { pinned: true, general: true }),
      row(id, 'A little room to think', [member('sprout', { working: true })]),
      row('waiting', 'Checking the loose ends', [member('scout', { background: 2 })]),
      row('asking', 'Before the next step', [member('pip', { working: true, asking: true })]),
      row('quiet', 'Room for the unexpected', [member('ledger')]),
      row('replied', 'A fresh perspective', [member('quill')], { unread: 1, last: { author: 'quill', text: 'Ready.', at: '2026-11-11T20:00:00Z' } })]
    const paragraph = 'Leave a little room to think. We can gather the useful details, choose the next step together, and keep Friday open for a slower morning.'
    const messages = [
      { id: 'm1', author: 'you', text: 'Could we keep Friday free for a slower morning?' },
      { id: 'm2', author: 'sprout', text: Array.from({ length: 28 }, () => paragraph).join('\n\n') },
      { id: 'm3', author: 'you', text: 'Thursday sounds good. Let’s bring the notes then.' },
      { id: 'm4', author: 'quill', text: `${paragraph}\n\nKeep the morning open. We can gather the loose ends on Thursday and leave enough space to choose what matters. That gives us a clear plan and a calmer week.` },
      { id: 'm5', author: 'system', kind: 'system', text: 'The plan is ready to review.' },
    ].map((m) => ({ kind: 'message', at: '2026-11-11T20:00:00Z', engine: 'claude-code', did: [], ...m }))
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: rows, agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'threads.snapshot', threads: rows, agents: app.f.agents })}\n\n`)
    }
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...original, thread: { ...rows[1], members: [member('sprout'), member('quill')] }, messages, has_more: false, turn: null, turns: [] })
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
    app.routes[`POST /v1/threads/${id}/messages`] = (req, res, url, raw) => {
      const body = JSON.parse(raw); posts.push(body)
      json(res, { accepted: true, message: { id: 'm6', author: 'you', kind: 'message', ...body } })
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    await context.route('**/api/health', (route) => route.fulfill({ json: { configured: true, ok: true, speech: true } }))
    await context.route('**/x/read-aloud/status', r => r.fulfill({ json: { speech: true } }))
    await context.route('**/x/read-aloud/speech/lookup', (route) => route.fulfill({ json: { status: 'none' } }))
    if (baseline) for (const file of ['threads.js', 'threads.css']) await context.route(`**/${file}`, (route) => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' }) }))
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__footer = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(12000)
    page.on('pageerror', (e) => errors.push(e.message))
    page.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy/.test(m.text())) errors.push(m.text()) })
    const settle = async () => { await page.waitForTimeout(300); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }) }
    const shot = async (label) => { await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(out, `${phase}-${label}-${width}-${theme}.png`) }) }
    const bring = async (node, offset = 200) => { await node.evaluate((n, offset) => { const pane = n.closest('.tt-stream'); pane.scrollTop += n.getBoundingClientRect().top - pane.getBoundingClientRect().top - offset; pane.dispatchEvent(new Event('scroll')) }, offset); await settle() }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await settle()
      const animation = await page.locator('.tt-row').evaluateAll((nodes) => nodes.map((n) => { const s = n.querySelector('.srow-name'), style = s && getComputedStyle(s); return { id: n.title, state: n.dataset.activity ?? null, name: style?.animationName ?? 'none', duration: style?.animationDuration, color: style?.color } }))
      if (!baseline) {
        assert.deepEqual(animation.filter((a) => a.name !== 'none').map((a) => [a.state, a.duration]), [['working', '2.4s'], ['waiting', '5s']])
        if (width >= 1440) {
          const samples = []
          for (const elapsed of [0, 1000, 2000, 3000, 4000]) {
            samples.push(await page.evaluate((elapsed) => {
              for (const a of document.getAnimations()) a.pause()
              return [...document.querySelectorAll('.tt-row[data-activity] .srow-name')].map((n) => {
                const a = n.getAnimations()[0]; a.currentTime = elapsed
                return { text: n.textContent, position: getComputedStyle(n).backgroundPosition }
              })
            }, elapsed))
            await page.locator('.tt-list').screenshot({ path: path.join(out, `sheen-${width}-${theme}-${elapsed}ms.png`) })
          }
          assert.equal(new Set(samples.map((s) => s[0].position)).size, 5)
          assert.equal(new Set(samples.map((s) => s[1].position)).size, 5)
          await writeFile(path.join(out, `sheen-${width}-${theme}.json`), JSON.stringify({ elapsed: [0, 1000, 2000, 3000, 4000], samples }, null, 2) + '\n')
          await page.emulateMedia({ reducedMotion: 'reduce' })
          assert.ok(await page.locator('.tt-row[data-activity] .srow-name').evaluateAll((nodes) => nodes.every((n) => getComputedStyle(n).animationName === 'none' && getComputedStyle(n).color !== 'rgba(0, 0, 0, 0)')))
          await shot('reduced-motion-list')
          await page.emulateMedia({ reducedMotion: 'no-preference' })
        }
      }
      await page.evaluate((id) => __footer.threads.open(id), id)
      await page.waitForFunction(() => !__footer.threads.st.open.loading && !__footer.threads.st.open.refreshing)
      const long = page.locator('[data-message-id="m2"]'), short = page.locator('[data-message-id="m4"]'), person = page.locator('[data-message-id="m3"]')
      await short.waitFor(); await settle()
      assert.equal(await page.locator('[data-message-id="m5"] .tt-footer-reply').count(), 0)
      await bring(short); await short.locator('.md').hover(); await settle()
      let hover = null, longHover = null
      if (baseline) {
        const hoverAction = short.locator('.tt-message-actions .tt-reply-action')
        hover = await hoverAction.evaluate((n) => {
          const r = n.getBoundingClientRect(), a = n.parentElement, hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)
          return { opacity: getComputedStyle(a).opacity, pointer: getComputedStyle(a).pointerEvents, hit: n.contains(hit), x: r.x, y: r.y, width: r.width }
        })
        if (width >= 1440) { assert.equal(hover.opacity, '1'); assert.equal(hover.hit, true); await shot('short-hover') }
      } else {
        assert.equal(await page.locator('.tt-message-actions,.tt-reply-action').count(), 0)
        await shot('short-hover')
      }
      await bring(long.locator('.tt-reply-footer'), 300)
      assert.equal(await long.locator('.tt-reply-footer .speakbtn').isVisible(), true)
      if (baseline) {
        longHover = await long.locator('.tt-message-actions').evaluate((n) => ({ top: n.getBoundingClientRect().top, streamTop: n.closest('.tt-stream').getBoundingClientRect().top }))
        assert.ok(longHover.top < longHover.streamTop, 'top-edge action is outside the visible transcript at a long reply’s footer')
      }
      await page.mouse.move(5, 5)
      await shot('long-footer-rest')
      if (!baseline) {
        assert.equal(await page.locator('.tt-footer-reply').count(), 4)
        {
          const control = long.locator('.tt-footer-reply')
          assert.equal(await control.isVisible(), true)
          const muted = await control.evaluate((n) => getComputedStyle(n).color)
          assert.equal(muted, await long.locator('.tt-message-time').evaluate((n) => getComputedStyle(n).color))
          await control.hover(); await shot('long-footer-hover')
          if (width >= 900) assert.notEqual(await control.evaluate((n) => getComputedStyle(n).color), muted)
          await control.click(); await page.locator('.tt-reply-chip').waitFor()
          assert.equal(await page.evaluate(() => __footer.threads.st.open.replyTo.id), 'm2')
          assert.equal(await page.locator('#draft').evaluate((n) => n === document.activeElement), true)
          await page.getByRole('button', { name: 'Cancel reply', exact: true }).click()
          await bring(long.locator('.tt-reply-footer'), 300)
          const timeBox = await long.locator('time').boundingBox(), glyphBox = await long.locator('.speakbtn').boundingBox(), replyBox = await control.boundingBox()
          const copyBox = await long.locator('.tt-footer-copy').boundingBox()
          assert.ok(replyBox.x + replyBox.width <= copyBox.x && copyBox.x + copyBox.width <= glyphBox.x && glyphBox.x + glyphBox.width <= timeBox.x)
          assert.ok(Math.abs(glyphBox.y + glyphBox.height / 2 - replyBox.y - replyBox.height / 2) < 2)
          await long.locator('.speakbtn').click(); await page.getByRole('menu', { name: 'Read aloud', exact: true }).waitFor()
          await page.keyboard.press('Escape'); await page.getByRole('menu', { name: 'Read aloud', exact: true }).waitFor({ state: 'hidden' })
          await bring(person); await page.mouse.move(5, 5); await shot('person-footer-rest')
          await person.locator('.tt-footer-reply').hover(); await shot('person-footer-hover')
          await person.locator('.tt-footer-reply').click()
          assert.match(await page.locator('.tt-reply-chip').innerText(), /Replying to You/)
          await page.getByRole('button', { name: 'Cancel reply', exact: true }).click()
          // A real keyboard activation also reaches the existing reply handler.
          await bring(short); await short.locator('.tt-footer-reply').focus(); await page.keyboard.press('Enter')
          assert.equal(await page.evaluate(() => __footer.threads.st.open.replyTo.id), 'm4')
          await page.locator('#draft').fill('I’ll bring the notes.'); await page.evaluate(() => __footer.threads.send()); await settle()
          assert.deepEqual(posts.at(-1), { text: 'I’ll bring the notes.', reply_to: 'm4' })
          // Resizing retains the icon footer on both sides of the old threshold.
          await page.setViewportSize({ width: 899, height: 1000 }); await settle()
          assert.ok(await page.locator('.tt-footer-reply').count() > 0)
          await page.setViewportSize({ width: 900, height: 1000 }); await settle()
          assert.ok(await page.locator('.tt-footer-reply').count() > 0)
        }
      }
      assert.deepEqual(errors, [])
      results.push({ width, theme, baseline, hover, longHover, animation, posts, errors })
      console.log(`PASS ${phase} ${width} ${theme}: hover geometry, long-reply visibility${baseline ? '' : ', all-width footer actions, state-only sheen and reduced motion'}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, `${phase}-browser.json`), JSON.stringify({ baseline, fixturesOnly: true, results }, null, 2) + '\n')
} finally { await browser.close() }
