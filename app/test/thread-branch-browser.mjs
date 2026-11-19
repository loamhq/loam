// Actual page and authenticated relay; the broker's draft/branch routes are
// synthetic. This driver starts no host session and makes no model call.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_BRANCH_EVIDENCE
if (!out) throw new Error('Set THREAD_BRANCH_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8'), results = []
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), id = 'a-quieter-week', next = 'space-for-friday', errors = [], posts = [], sent = [], reads = []
    const record = await app.f.api('GET', `/threads/${id}`)
    const opening = 'Let’s explore how to keep Friday free for a slower morning. We can gather the loose ends on Thursday and leave some space to think.\n\nI’d like to start with '
    const messages = [
      { id: 'm1', author: 'you', text: 'Could we keep Friday free for a slower morning?' },
      { id: 'm2', author: 'system', kind: 'system', text: 'You brought in Quill' },
      { id: 'm3', author: 'sprout', text: 'We can gather the loose ends on Thursday and leave Friday open.\n\nA little space can make the whole week feel calmer. I’ll bring the notes and the questions that need an answer. Keep the useful details close to the conversation, with enough room to choose what matters. A calmer week begins with a short list, and we can make that list together.' },
      { id: 'm4', author: 'quill', text: 'I’ll bring the notes on Thursday, with just the questions that need an answer.' },
    ].map((m) => ({ kind: 'message', at: '2026-11-11T20:45:00Z', engine: 'claude-code', did: [], ...m }))
    const title = 'A little space for Friday'
    let draftRelease, readFailure = false, refuse = true, newRecord = null
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...record, messages, has_more: false, turn: null, turns: [] })
    app.routes[`GET /v1/threads/${id}/branch`] = async (req, res, url) => {
      reads.push(url.search)
      await new Promise((resolve) => { draftRelease = resolve })
      if (readFailure) return json(res, { error: 'This earlier message is no longer available.' }, 404)
      json(res, { thread: id, message: 'm3', persona: 'sprout', people: ['sprout', 'quill', 'scout', 'ledger', 'pip'],
        summarizer: { system_instruction: 'Synthetic prompt', input: 'Synthetic context', answer: { title: 'string', draft: 'string' } }, fallback: { title, draft: opening } })
    }
    app.routes[`POST /v1/threads/${id}/branch`] = (req, res, url, raw) => {
      const body = JSON.parse(raw); posts.push(body)
      if (refuse) return json(res, { error: 'The thread could not be created. Please try again.' }, 503)
      const summary = { ...record.thread, id: next, title: body.title, default: body.persona, members: [{ persona: body.persona, engine: 'claude-code' }],
        draft: body.draft, branched_from: { thread: id, message: body.message }, last: null, unread: 0 }
      const from = { id: 'm1', kind: 'system', author: 'system', text: `Branched from ${record.thread.title}`, link: { thread: id, message: body.message, title: record.thread.title } }
      const into = { id: 'm5', kind: 'system', author: 'system', text: `Branched into ${body.title}`, after: body.message, link: { thread: next, title: body.title } }
      messages.splice(messages.findIndex((m) => m.id === body.message) + 1, 0, into)
      newRecord = { thread: summary, messages: [from], has_more: false, turn: null, turns: [], board: { path: 'boards/friday.md', markdown: '' } }
      app.routes[`GET /v1/threads/${next}`] = (req, res) => json(res, newRecord)
      app.routes[`GET /v1/threads/${next}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); res.write(`data: ${JSON.stringify({ type: 'thread.snapshot', turn: null, pending: [] })}\n\n`) }
      app.routes[`POST /v1/threads/${next}/messages`] = (req, res, url, raw) => {
        const body = JSON.parse(raw); sent.push(body)
        const message = { id: 'm2', author: 'you', kind: 'message', text: body.text }
        newRecord.messages.push(message); newRecord.thread.draft = null
        json(res, { accepted: true, message })
      }
      // Arrive over SSE before HTTP, and repeat: it is still one line.
      app.emit(id, { type: 'message.done', message: into }); app.emit(id, { type: 'message.done', message: into })
      json(res, { thread: summary, from, into, window: ['m1', 'm3'] }, 201)
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__branch = { threads, schedule };' }))
    await context.route('**/api/health', (route) => route.fulfill({ json: { configured: true, ok: true, speech: true } }))
    await context.route('**/x/read-aloud/status', r => r.fulfill({ json: { speech: true } }))
    await context.route('**/x/read-aloud/speech/lookup', (route) => route.fulfill({ json: { status: 'none' } }))
    const page = await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', (e) => errors.push(e.message))
    page.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy/.test(m.text())) errors.push(m.text()) })
    const settle = async () => { await page.waitForTimeout(200); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }) }
    const shot = async (name) => { await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(out, `${name}-${width}-${theme}.png`), animations: 'disabled' }) }
    const draft = page.locator('#draft'), message = page.locator('[data-message-id="m3"]'), trigger = message.locator('.tt-footer-branch')
    const dialog = page.getByRole('dialog', { name: 'New thread with', exact: true })
    const release = async () => { const deadline = Date.now() + 15000; while (!draftRelease && Date.now() < deadline) await page.waitForTimeout(10); assert.ok(draftRelease, 'draft read reached the stub'); const resolve = draftRelease; draftRelease = null; resolve() }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate((id) => __branch.threads.open(id), id)
      await page.waitForFunction(() => !__branch.threads.st.open.loading && !__branch.threads.st.open.refreshing)
      await draft.fill('Keep my source note.')
      await page.locator('[data-message-id="m1"] .tt-footer-reply').evaluate((n) => n.click())
      await message.evaluate((n) => { const pane = n.closest('.tt-stream'); pane.scrollTop += n.getBoundingClientRect().top - pane.getBoundingClientRect().top - 160; pane.dispatchEvent(new Event('scroll')) })
      await settle()
      assert.equal(await page.locator('[data-message-id="m2"] .tt-footer-branch').count(), 0)
      assert.equal(await page.locator('.tt-footer-branch').count(), 3)
      assert.deepEqual(await message.locator('.tt-footer-action,.speakbtn').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('aria-label'))), ['Reply', 'Branch into a new thread', 'Copy', 'Read aloud'])
      const sizes = await message.locator('.tt-footer-action').evaluateAll((nodes) => nodes.map((n) => ({ width: n.offsetWidth, height: n.offsetHeight, color: getComputedStyle(n).color })))
      assert.ok(sizes.every((s) => JSON.stringify(s) === JSON.stringify(sizes[0])))
      await shot('footer')
      await trigger.click(); await dialog.getByRole('status').waitFor(); await shot('sheet-loading')
      assert.equal(await dialog.getAttribute('data-menu-layout'), width === 390 ? 'sheet' : 'anchored')
      const geometry = { trigger: await trigger.boundingBox(), sheet: await dialog.boundingBox() }
      assert.ok(geometry.sheet.x >= 0 && geometry.sheet.y >= 0 && geometry.sheet.x + geometry.sheet.width <= width + 1)
      if (width === 1440) assert.ok(Math.min(Math.abs(geometry.sheet.y - geometry.trigger.y - geometry.trigger.height), Math.abs(geometry.trigger.y - geometry.sheet.y - geometry.sheet.height)) <= 9)
      else assert.ok(Math.abs(geometry.sheet.y + geometry.sheet.height - 844) < 2)
      // Dismissal during generation does not create anything or resurrect UI.
      await dialog.getByRole('button', { name: 'Close New thread with', exact: true }).click()
      await release(); await settle(); assert.equal(await dialog.count(), 0); assert.deepEqual(posts, [])
      assert.equal(await trigger.evaluate((n) => n === document.activeElement), true)
      // A draft-route refusal remains visible, then Retry uses that route again.
      readFailure = true; await trigger.click(); await release()
      await dialog.getByRole('alert').filter({ hasText: 'This earlier message is no longer available.' }).waitFor()
      assert.equal(await dialog.getByRole('button', { name: 'Start thread', exact: true }).isDisabled(), true)
      readFailure = false; await dialog.getByRole('button', { name: 'Try again', exact: true }).click()
      await page.emulateMedia({ reducedMotion: 'reduce' })
      assert.equal(await dialog.locator('.tt-branch-loading span').first().evaluate((n) => getComputedStyle(n).animationName), 'none')
      await page.emulateMedia({ reducedMotion: 'no-preference' })
      assert.equal(await dialog.getByRole('button', { name: 'Sprout', exact: true }).getAttribute('aria-pressed'), 'true')
      await dialog.getByRole('button', { name: 'Quill', exact: true }).click()
      await release(); await dialog.locator('textarea').waitFor()
      assert.equal(await dialog.getByRole('button', { name: 'Quill', exact: true }).getAttribute('aria-pressed'), 'true')
      assert.deepEqual(await dialog.locator('.tt-branch-person').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('aria-label'))), ['Sprout', 'Quill', 'Scout', 'Ledger', 'Pip'])
      assert.equal(await dialog.locator('textarea').inputValue(), opening)
      const edited = opening + 'a Thursday review. What should we bring?'
      await dialog.locator('textarea').fill(edited); await shot('sheet-filled')
      await dialog.getByRole('button', { name: 'Start thread', exact: true }).click()
      await dialog.getByText('The thread could not be created. Please try again.', { exact: true }).waitFor()
      assert.equal(await dialog.locator('textarea').inputValue(), edited)
      refuse = false
      await dialog.getByRole('button', { name: 'Start thread', exact: true }).evaluate((n) => { n.click(); n.click() })
      await page.waitForFunction((id) => __branch.threads.st.open?.thread.id === id && !__branch.threads.st.open.loading, next)
      await page.waitForFunction(() => document.activeElement?.id === 'draft')
      assert.equal(await draft.evaluate((n) => n.value), edited)
      assert.deepEqual(posts.at(-1), { message: 'm3', persona: 'quill', title, draft: edited })
      assert.equal(posts.length, 2, 'one refused start and one accepted start, even after a double click')
      assert.deepEqual(sent, [], 'the opening stays unsent')
      const caret = await draft.evaluate(async (n) => { const { readField, caretPoint } = await import('/compose.js'); return readField(n, caretPoint(n)).caret })
      assert.equal(caret, edited.length, 'cursor is at the end of the opening')
      assert.ok(await draft.evaluate((n) => n.scrollHeight - n.scrollTop - n.clientHeight <= 1), 'the end of a long opening is visible')
      await shot('new-thread')
      const from = page.getByRole('link', { name: `Branched from ${record.thread.title}`, exact: true })
      assert.ok((await from.getAttribute('href')).endsWith(`#m3`)); await shot('branched-from')
      await from.click(); await page.locator('[data-message-id="m3"].tt-reply-highlight').waitFor()
      assert.equal(await draft.evaluate((n) => n.value), 'Keep my source note.')
      assert.match(await page.locator('.tt-reply-chip').innerText(), /Replying to You/)
      assert.deepEqual(await page.locator('.tt-stream [data-message-id]').evaluateAll((nodes) => nodes.map((n) => n.dataset.messageId)), ['m1', 'm2', 'm3', 'm5', 'm4'])
      const into = page.getByRole('link', { name: `Branched into ${title}`, exact: true })
      assert.equal(await into.count(), 1); await shot('branched-into')
      await into.click(); await page.waitForFunction((id) => __branch.threads.st.open?.thread.id === id && !__branch.threads.st.open.refreshing, next)
      assert.equal(await draft.evaluate((n) => n.value), edited)
      await draft.fill('')
      await page.getByRole('link', { name: `Branched from ${record.thread.title}`, exact: true }).click()
      await page.getByRole('link', { name: `Branched into ${title}`, exact: true }).click()
      await page.waitForFunction((id) => __branch.threads.st.open?.thread.id === id && !__branch.threads.st.open.refreshing, next)
      assert.equal(await draft.evaluate((n) => n.value), '', 'an intentionally cleared local draft outranks the broker seed')
      await page.reload(); await page.waitForFunction(() => globalThis.__branch?.threads.st.open && !__branch.threads.st.open.loading)
      assert.equal(await draft.evaluate((n) => n.value), edited, 'a cold read restores the durable summary draft')
      assert.deepEqual(sent, [])
      await page.evaluate(() => __branch.threads.send()); await settle()
      assert.deepEqual(sent, [{ text: edited }], 'only an explicit send starts the new conversation')
      await page.reload(); await page.waitForFunction(() => globalThis.__branch?.threads.st.open && !__branch.threads.st.open.loading)
      assert.equal(await draft.evaluate((n) => n.value), '', 'the first accepted message spends the stored opening')
      await page.evaluate(async (id) => {
        const threads = __branch.threads, row = { ...threads.st.open.thread, draft: 'An outdated list opening.' }
        threads.close({ navigate: false }); threads.st.drafts.delete(id)
        threads.st.threads = [row, ...threads.st.threads.filter((r) => r.id !== id)]
        await threads.open(id)
      }, next)
      assert.equal(await draft.evaluate((n) => n.value), '', 'a newer detail read clears an unedited seed from a stale listing')
      assert.deepEqual(errors, [])
      results.push({ width, theme, geometry, sizes, reads, posts, sent, errors, passed: ['footer for both authors', 'sheet layout', 'cancel and late read', 'draft refusal/retry', 'reduced motion', 'persona selection during loading', 'editable draft', 'branch refusal retained', 'double-start guarded', 'unsent composer and caret', 'source draft and reply preserved', 'both system links', 'notice record order and deduplication', 'local clearing wins over seed', 'draft survives reload and is spent on send', 'newer read clears stale list seed'] })
      console.log(`PASS ${width} ${theme}: branch sheet, editable draft, unsent composer, both links, ordering, retry and reload`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ results }, null, 2) + '\n')
} finally { await browser.close() }
