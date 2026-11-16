// Real browser/page/relay, isolated synthetic records. No host thread writes.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_REPLIES_EVIDENCE
if (!out) throw new Error('Set THREAD_REPLIES_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8'), results = [], id = 'a-quieter-week'
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = [], reads = [], posts = [], clients = new Set()
    const record = await app.f.api('GET', `/threads/${id}`)
    const messages = Array.from({ length: 70 }, (_, i) => ({ id: `m${i + 1}`, author: i % 4 === 0 ? 'you' : i % 2 ? 'sprout' : 'quill', kind: 'message', at: '2026-11-11T18:00:00Z', text: i % 4 === 0 ? `Let's leave a little room to think. Thought ${i + 1}.` : `A clear next step, part ${i + 1}.\n\nKeep the useful details close to the conversation, and leave enough space to choose what matters.`, engine: 'claude-code', did: [] }))
    Object.assign(messages[0], { text: 'Could we keep Friday free for a slower morning?' })
    Object.assign(messages[1], { text: 'Keep the morning open.\n\nWe can gather the loose ends on Thursday.' })
    Object.assign(messages[65], { text: 'Let’s move the review to Thursday.\n\nThat leaves Friday open for a slower start.' })
    Object.assign(messages[68], { author: 'sprout', text: 'Yes. I’ll keep Friday open and bring the notes on Thursday.', reply_to: 'm1' })
    Object.assign(messages[69], { kind: 'system', text: 'The plan is ready to review.' })
    record.thread.unread = 0
    app.routes[`GET /v1/threads/${id}`] = (req, res, url) => {
      reads.push(url.search)
      const before = url.searchParams.get('before'), limit = Number(url.searchParams.get('limit') ?? 50)
      const end = before ? messages.findIndex((m) => m.id === before) : messages.length
      json(res, { ...record, messages: messages.slice(Math.max(0, end - limit), end), has_more: end > limit, turn: null, turns: [] })
    }
    app.routes[`GET /v1/threads/${id}/steps`] = (req, res) => json(res, { error: 'No full record' }, 404)
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n')
      res.write(`data: ${JSON.stringify({ type: 'thread.snapshot', turns: [], members: record.thread.members, pending: [] })}\n\n`)
      clients.add(res); res.on('close', () => clients.delete(res))
    }
    const emit = (message) => { messages.push(message); for (const res of clients) res.write(`data: ${JSON.stringify({ type: 'message.done', message })}\n\n`) }
    let reject = false
    app.routes[`POST /v1/threads/${id}/messages`] = (req, res, url, raw) => {
      const body = JSON.parse(raw); posts.push(body)
      if (reject) return json(res, { error: 'This earlier message is no longer available.' }, 404)
      const message = { id: `m${messages.length + 1}`, author: 'you', kind: 'message', at: '2026-11-11T19:00:00Z', ...body }
      emit(message); json(res, { accepted: true, message })
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__replies = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', (error) => errors.push(error.message))
    page.on('console', (msg) => { if (msg.type() === 'error' && /Content Security Policy/.test(msg.text())) errors.push(msg.text()) })
    const settle = async () => { await page.waitForTimeout(350); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }) }
    const shot = async (state) => { await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(out, `${state}-${width}-${theme}.png`), animations: 'disabled' }) }
    const draft = page.locator('#draft'), reply = page.locator('[data-message-id="m66"]'), quote = page.locator('[data-message-id="m69"] .tt-quote')
    const bring = async (node) => { await node.evaluate((n) => { const pane = n.closest('.tt-stream'); pane.scrollTop += n.getBoundingClientRect().top - pane.getBoundingClientRect().top - 180; pane.dispatchEvent(new Event('scroll')) }); await settle() }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate((id) => __replies.threads.open(id), id)
      await reply.waitFor({ state: 'attached' })
      await page.waitForFunction(() => !__replies.threads.st.open.refreshing)
      await quote.getByText('You', { exact: true }).waitFor()
      assert.equal(await page.locator('[data-message-id="m1"]').count(), 0, 'quote resolves without expanding the thread')
      assert.equal(await page.locator('[data-message-id="m70"] .tt-message-actions').count(), 0)
      await draft.fill('Thursday sounds good.'); await settle(); await bring(reply)
      if (width === 1440) { await reply.hover(); await shot('footer-actions') }
      else {
        const rect = await reply.boundingBox(), session = await context.newCDPSession(page)
        await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: rect.x + 40, y: rect.y + 30 }] })
        for (const dx of [20, 40, 65]) { await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: rect.x + 40 + dx, y: rect.y + 30 }] }); await page.waitForTimeout(40) }
        await page.evaluate(async () => { __replies.schedule(); await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
        assert.equal(await reply.evaluate((n) => n.classList.contains('reply-ready')), true)
        await shot('swipe-mid-gesture')
        await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
        await page.locator('.tt-reply-chip').waitFor()
        assert.equal(await draft.evaluate((n) => document.activeElement === n), true)
        await page.getByRole('button', { name: 'Cancel reply', exact: true }).click()
        await settle(); assert.equal(await draft.innerText(), 'Thursday sounds good.')
        // Native held-touch selection is covered with range assertions in
        // thread-native-selection-browser.mjs; this suite retains reply flows.
        await session.detach()
      }
      // Human messages can also be selected without changing their text.
      const person = page.locator('[data-message-id="m65"]')
      await bring(person)
      await person.locator('.tt-footer-reply').evaluate((n) => n.click())
      assert.match(await page.locator('.tt-reply-chip').innerText(), /Replying to You/)
      await page.getByRole('button', { name: 'Cancel reply', exact: true }).click(); await settle()
      assert.equal(await draft.evaluate((n) => document.activeElement === n), true)
      assert.equal(await page.locator('.tt-reply-chip').count(), 0)
      assert.equal(await draft.innerText(), 'Thursday sounds good.')
      await bring(reply)
      const readingY = (await reply.boundingBox()).y
      await reply.locator('.tt-footer-reply').evaluate((n) => n.click())
      await settle()
      assert.equal(await draft.evaluate((n) => document.activeElement === n), true)
      assert.ok(Math.abs((await reply.boundingBox()).y - readingY) < 2, 'adding a chip preserves the reading position')
      await page.locator('.tt-stream').evaluate((n) => { n.scrollTop = n.scrollHeight; n.dispatchEvent(new Event('scroll')) }); await settle()
      const stack = await page.evaluate(() => {
        const faces = [...document.querySelectorAll('.tt-listener')].map((n) => n.getBoundingClientRect())
        const chip = document.querySelector('.tt-reply-chip').getBoundingClientRect(), composer = document.querySelector('.composer-box').getBoundingClientRect()
        return { facesTop: Math.min(...faces.map((r) => r.top)), facesBottom: Math.max(...faces.map((r) => r.bottom)), chipTop: chip.top, chipBottom: chip.bottom, composerTop: composer.top,
          lastBottom: document.querySelector('[data-message-id="m70"]').getBoundingClientRect().bottom }
      })
      assert.ok(stack.facesBottom < stack.chipTop, JSON.stringify(stack)); assert.ok(stack.chipBottom <= stack.composerTop, JSON.stringify(stack))
      assert.ok(stack.lastBottom < stack.facesTop, JSON.stringify(stack))
      await shot('composing-with-faces')
      await page.evaluate((id) => __replies.threads.open(id), id)
      await page.waitForFunction(() => !__replies.threads.st.open.refreshing)
      assert.match(await page.locator('.tt-reply-chip').innerText(), /Replying to Sprout/)
      assert.equal(await draft.innerText(), 'Thursday sounds good.', 'reply intent survives the same draft lifecycle as text')
      // A refusal preserves the draft and reply intent, so the person can fix it.
      reject = true; await page.evaluate(() => __replies.threads.send()); await settle()
      assert.equal(await page.locator('.tt-reply-chip').count(), 1); assert.match(await draft.innerText(), /Thursday sounds good/)
      reject = false; await page.evaluate(() => __replies.threads.send()); await settle()
      assert.deepEqual(posts.at(-1), { text: 'Thursday sounds good.', reply_to: 'm66' })
      const sent = page.locator('[data-message-id="m71"]')
      await sent.waitFor(); assert.match(await sent.locator('.tt-quote').innerText(), /Sprout.*Let’s move/s)
      assert.equal(await page.locator('.tt-reply-chip').count(), 0)
      await shot('sent-reply')
      emit({ id: 'm72', author: 'sprout', kind: 'message', at: '2026-11-11T19:01:00Z', engine: 'claude-code', text: 'Thursday it is. I’ll bring the notes, and Friday stays clear.', reply_to: 'm71' })
      const persona = page.locator('[data-message-id="m72"] .tt-quote')
      await persona.waitFor(); assert.match(await persona.innerText(), /You.*Thursday sounds good/s)
      await shot('persona-reply')
      emit({ id: 'm73', author: 'quill', kind: 'message', text: 'I’ll keep that earlier thought in mind.', reply_to: 'm9999' })
      await page.locator('[data-message-id="m73"] .tt-quote-missing').waitFor()
      assert.equal(await page.locator('[data-message-id="m73"] .tt-quote').evaluate((n) => n.tagName), 'DIV')
      // Jump to an original that has never been rendered. Reuse quote pages,
      // prepend with the opening anchor, then highlight/focus the actual node.
      await bring(page.locator('[data-message-id="m69"]'))
      assert.equal(await page.locator('[data-message-id="m1"]').count(), 0)
      const readsBeforeJump = reads.filter((q) => q.includes('before=')).length
      await quote.click()
      await page.locator('[data-message-id="m1"].tt-reply-highlight').waitFor()
      await shot('jump-highlight')
      assert.equal(await page.locator('[data-message-id="m1"]').evaluate((n) => document.activeElement === n), true)
      const offset = await page.locator('[data-message-id="m1"]').evaluate((n) => n.getBoundingClientRect().top - n.closest('.tt-stream').getBoundingClientRect().top)
      assert.ok(offset >= 0 && offset <= 97, `original is visible at the anchor, clamped at the start: ${offset}`)
      assert.equal(reads.filter((q) => q.includes('before=')).length, readsBeforeJump, 'quote/jump reuse pages within this visit')
      await page.waitForTimeout(2100); assert.equal(await page.locator('.tt-reply-highlight').count(), 0)
      await page.emulateMedia({ reducedMotion: 'reduce' })
      assert.ok(await page.locator('[data-message-id="m1"]').evaluate((n) => parseFloat(getComputedStyle(n).transitionDuration) <= .00001))
      assert.deepEqual(errors, [])
      results.push({ width, theme, stack, reads, posts, jumpOffset: offset, errors, passed: ['footer/swipe', 'human target', 'cancel and focus', 'chip stack', 'refusal retains reply', 'POST payload', 'human and persona quotes', 'missing original', 'unloaded jump and timed highlight'] })
      console.log(`PASS ${width} ${theme}: reply gestures/actions, chip, send, quotes, unloaded jump and focus`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ results }, null, 2) + '\n')
} finally { await browser.close() }
