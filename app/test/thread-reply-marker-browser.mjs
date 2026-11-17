// Real browser, isolated synthetic records: a reply's `↩ m111` is never words
// on the page (2026-11-17). The full record served for m112 is the shape of a
// real one from 11-17, still opening with the marker (a broker from before
// the fix serves it so), and m111 is far above the first page, so the quote
// resolves and jumps without the thread being opened up to it.
//
//   THREAD_MARKER_EVIDENCE=<dir> LOAM_PLAYWRIGHT=<playwright index.mjs> \
//     node test/thread-reply-marker-browser.mjs
//
// THREAD_MARKER_BEFORE=<dir> serves the thread-record.js and thread-replies.js
// in that directory instead (e.g. from `git show <rev>:app/public/...`) and
// only takes the screenshots: the "before" pictures.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_MARKER_EVIDENCE
if (!out) throw new Error('Set THREAD_MARKER_EVIDENCE')
await mkdir(out, { recursive: true })
const before = process.env.THREAD_MARKER_BEFORE ? Object.fromEntries(await Promise.all(['thread-record.js', 'thread-replies.js'].map(async (f) => [f, await readFile(path.join(process.env.THREAD_MARKER_BEFORE, f), 'utf8')]))) : null
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8'), results = [], id = 'a-quieter-week'
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const asked = 'I actually like the sameness in the images. I like the subtleness. Maybe a little less sameness is fine.'
const said = '**Yes to rough edges.** Torn edges make each cover look like a sheet of rice paper, which is what the paintings already suggest.\n\nIf you like the mock, next I’ll regenerate the five covers with the seal and the edges added afterwards. Go?'
const fullSteps = [
  { type: 'thinking', thinking: '', parent: null },
  { type: 'tool', id: 'toolu_1', name: 'Bash', input: { command: 'ls covers2' }, parent: null, is_error: false, output: 'covers' },
  { type: 'tool', id: 'toolu_2', name: 'Read', input: { file_path: 'all.jpg' }, parent: null, is_error: false, output: 'ok' },
  { type: 'text', text: `↩ m111\n\n${said}`, parent: null },
  { type: 'end', event: { type: 'result' } },
]
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = [], clients = new Set()
    const record = await app.f.api('GET', `/threads/${id}`)
    const messages = Array.from({ length: 170 }, (_, i) => ({ id: `m${i + 1}`, author: i % 2 ? 'sprout' : 'you', kind: 'message', at: '2026-11-17T17:00:00Z', engine: 'claude-code', did: [],
      text: i % 2 ? `A clear next step, part ${i + 1}.` : `A thought, part ${i + 1}.` }))
    Object.assign(messages[110], { author: 'you', text: asked })
    Object.assign(messages[111], { author: 'sprout', text: said, reply_to: 'm111', did: [{ label: 'Bash: ls covers2' }, { label: 'Read: all.jpg' }] })
    messages.splice(112)
    messages.push(...Array.from({ length: 56 }, (_, i) => ({ id: `m${113 + i}`, author: i % 2 ? 'sprout' : 'you', kind: 'message', at: '2026-11-17T17:30:00Z', engine: 'claude-code', did: [], text: `Later, part ${113 + i}.` })))
    // The reply under test sits at the end, so it is on screen when the thread opens.
    messages.push({ ...messages[111], id: 'm169', at: '2026-11-17T17:40:00Z' })
    record.thread.unread = 0
    app.routes[`GET /v1/threads/${id}`] = (req, res, url) => {
      const b = url.searchParams.get('before'), limit = Number(url.searchParams.get('limit') ?? 50)
      const end = b ? messages.findIndex((m) => m.id === b) : messages.length
      json(res, { ...record, messages: messages.slice(Math.max(0, end - limit), end), has_more: end > limit, turn: null, turns: [] })
    }
    app.routes[`GET /v1/threads/${id}/steps`] = (req, res, url) => {
      const m = url.searchParams.get('message')
      const reply = messages.find((x) => x.id === m)
      if (!['m112', 'm169'].includes(m)) return json(res, { error: 'No full record' }, 404)
      json(res, { thread: id, steps: [{ message: m, author: reply.author, reply_to: reply.reply_to, steps: fullSteps.map((s) => s.type === 'text' ? { ...s, text: `↩ ${reply.reply_to}\n\n${said}` } : s) }] })
    }
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n')
      res.write(`data: ${JSON.stringify({ type: 'thread.snapshot', turns: [], members: record.thread.members, pending: [] })}\n\n`)
      clients.add(res); res.on('close', () => clients.delete(res))
    }
    const send = (event) => { for (const res of clients) res.write(`data: ${JSON.stringify(event)}\n\n`) }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__marker = { threads };' }))
    if (before) for (const [f, body] of Object.entries(before)) await context.route(`**/${f}`, (route) => route.fulfill({ contentType: 'text/javascript', body }))
    const page = await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', (error) => errors.push(error.message))
    const settle = async () => { await page.waitForTimeout(400); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }) }
    const shot = async (state) => { await settle(); await page.screenshot({ path: path.join(out, `${before ? 'before' : 'after'}-${state}-${width}-${theme}.png`), animations: 'disabled' }) }
    const bring = async (node) => { await node.evaluate((n) => { const pane = n.closest('.tt-stream'); pane.scrollTop += n.getBoundingClientRect().top - pane.getBoundingClientRect().top - 120; pane.dispatchEvent(new Event('scroll')) }); await settle() }
    const reply = page.locator('[data-message-id="m169"]')
    const words = (loc) => loc.locator('.say').evaluateAll((nodes) => nodes.map((n) => n.innerText).join('\n'))
    const visible = (loc) => loc.evaluate((n) => n.innerText)
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate((id) => __marker.threads.open(id), id)
      await reply.waitFor({ state: 'attached' })
      await page.waitForFunction(() => !__marker.threads.st.open.refreshing)
      await bring(reply)
      // Its full record is read once it is near the viewport.
      await page.waitForFunction(() => Array.isArray(__marker.threads.st.open.messages.find((m) => m.id === 'm169')?.steps) || __marker.threads.st.open.replySteps?.entries.get('m169')?.status === 'done')
      await reply.locator('.tt-quote').getByText('You', { exact: true }).waitFor()
      await shot('full-steps')
      if (before) { await context.close(); results.push({ width, theme, before: true }); continue }
      assert.equal(await reply.locator('.tt-quote').count(), 1, 'one quote')
      assert.match(await reply.locator('.tt-quote').innerText(), /You\s*I actually like the sameness/)
      assert.doesNotMatch(await visible(reply), /m111/, 'no id in the reply')
      assert.doesNotMatch(await words(reply), /↩/, 'no marker in the words')
      assert.match(await words(reply), /Yes to rough edges/)
      assert.equal(await page.locator('[data-message-id="m111"]').count(), 0, 'the original is far above what is loaded')

      // Keyboard: Tab-reachable, Enter jumps to the unloaded original and highlights it.
      await reply.locator('.tt-quote').focus(); await page.keyboard.press('Enter')
      await page.locator('[data-message-id="m111"].tt-reply-highlight').waitFor()
      await shot('jump-highlight')
      const offset = await page.locator('[data-message-id="m111"]').evaluate((n) => n.getBoundingClientRect().top - n.closest('.tt-stream').getBoundingClientRect().top)
      assert.ok(offset >= 0 && offset < 400, `the original is in view: ${offset}`)
      assert.equal(await page.locator('[data-message-id="m111"]').evaluate((n) => document.activeElement === n), true, 'focus moves to the original')
      // The earlier m112, now loaded, quotes it the same way.
      const earlier = page.locator('[data-message-id="m112"]')
      await bring(earlier)
      await page.waitForFunction(() => __marker.threads.st.open.replySteps?.entries.get('m112')?.status === 'done')
      await settle()
      assert.equal(await earlier.locator('.tt-quote').count(), 1)
      assert.doesNotMatch(await words(earlier), /↩/)
      await page.emulateMedia({ reducedMotion: 'reduce' })
      assert.ok(await page.locator('[data-message-id="m111"]').evaluate((n) => parseFloat(getComputedStyle(n).transitionDuration) <= .00001), 'no motion under reduced motion')

      // An original that is gone: a quiet, unlinked "Earlier message", no id.
      messages.push({ id: 'm170', author: 'sprout', kind: 'message', at: '2026-11-17T17:41:00Z', engine: 'claude-code', did: [], text: 'That earlier thought still holds.', reply_to: 'm9999' })
      send({ type: 'message.done', message: messages.at(-1) })
      const gone = page.locator('[data-message-id="m170"]')
      await gone.locator('.tt-quote-missing').waitFor()
      assert.equal(await gone.locator('.tt-quote').evaluate((n) => n.tagName), 'DIV')
      assert.doesNotMatch(await visible(gone), /m9999/)

      // Live: the quote comes with the first words and stays, once, when posted.
      await page.locator('.tt-stream').evaluate((n) => { n.scrollTop = n.scrollHeight; n.dispatchEvent(new Event('scroll')) })
      send({ type: 'turn.started', persona: 'sprout', started_at: '2026-11-17T17:42:00Z' })
      let draft = ''
      for (const piece of ['**Yes to', ' rough edges.**', ' Torn edges make each cover look like a sheet of rice paper.']) {
        draft += piece; send({ type: 'message.delta', persona: 'sprout', text: piece, draft, reply_to: 'm169' }); await page.waitForTimeout(60)
      }
      const live = page.locator('[data-live-member="sprout"], .tt-message:has(.say.streaming)').last()
      await live.locator('.tt-quote').waitFor()
      assert.equal(await live.locator('.tt-quote').count(), 1)
      assert.doesNotMatch(await words(live), /↩|m169/)
      await shot('streaming')
      const inContent = (n) => n.getBoundingClientRect().top - n.closest('.tt-stream').getBoundingClientRect().top + n.closest('.tt-stream').scrollTop
      const liveTop = await live.locator('.tt-quote').evaluate(inContent)
      messages.push({ id: 'm171', author: 'sprout', kind: 'message', at: '2026-11-17T17:42:30Z', engine: 'claude-code', did: [], text: draft, reply_to: 'm169' })
      send({ type: 'message.done', message: messages.at(-1) }); send({ type: 'turn.finished', persona: 'sprout' })
      const posted = page.locator('[data-message-id="m171"]')
      await posted.waitFor(); await settle()
      assert.equal(await posted.locator('.tt-quote').count(), 1)
      const postedTop = await posted.locator('.tt-quote').evaluate(inContent)
      assert.ok(Math.abs(postedTop - liveTop) < 2, `the quote keeps its place in the thread when the reply is posted: ${liveTop} → ${postedTop}`)
      assert.equal(await page.locator('.tt-quote').evaluateAll((nodes) => nodes.filter((n) => n.closest('[data-message-id="m171"]') || n.closest('.say.streaming')).length), 1)
      await shot('posted')
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'no sideways scroll')
      assert.deepEqual(errors, [])
      results.push({ width, theme, jumpOffset: offset, passed: ['one quote on the legacy full record', 'no id or marker in words', 'keyboard jump to unloaded original, focus, highlight', 'reduced motion', 'missing original', 'live quote once and stable when posted'] })
    } finally { await context.close() }
  }
} finally { await browser.close() }
await writeFile(path.join(out, `${before ? 'before' : 'after'}-results.json`), JSON.stringify(results, null, 2))
console.log(JSON.stringify(results))
process.exit(0)
