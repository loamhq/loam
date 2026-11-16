// Current alias targets and launching-reply navigation, synthetic broker only.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_ALIAS_TASKS_EVIDENCE
if (!out) throw new Error('Set THREAD_ALIAS_TASKS_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const results = [], id = 'a-quieter-week', at = '2026-11-11T03:00:00Z'
const paragraph = 'A clear next step leaves room to think, review the notes, and choose what matters. Keep the useful details close to the conversation.'
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), clients = new Set(), reads = [], errors = []
    const original = await app.f.api('GET', `/threads/${id}`)
    const task = { id: 'review', description: 'Brief Loam: Review the model labels and the phone layout', started_at: '2026-11-11T02:58:00Z', message: null }
    const member = { persona: 'sprout', engine: 'claude-code', model: 'fable', working: false, busy: true, background: 1, background_tasks: [task] }
    const row = { ...original.thread, title: 'A clearer next step', default: 'sprout', unread: 0, members: [member], background: 1, working: 0 }
    const provider = app.f.providers.find((p) => p.id === 'claude-code')
    provider.models = [{ id: 'fable', name: 'Fable', resolves_to: 'claude-fable-5-2' }]
    const messages = Array.from({ length: 76 }, (_, i) => ({
      id: `m${i + 1}`, kind: 'message', author: i % 5 === 0 ? 'you' : 'sprout', at,
      engine: 'claude-code', model: 'claude-fable-5-1', did: [], steps: [],
      text: i % 5 === 0 ? `Please review the next step. Thought ${i + 1}.`
        : i === 1 ? 'I’ve started a background review of the model labels and phone layout.\n\nThe review will report here when it finishes. You can carry on with the conversation while it runs.'
          : `## Review ${i + 1}\n\n${paragraph}\n\n${paragraph}`,
    }))
    let hold = true, release, failHistory = false
    const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: [row], agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'threads.snapshot', threads: [row], agents: app.f.agents })}\n\n`) }
    app.routes[`GET /v1/threads/${id}`] = async (req, res, url) => {
      const before = url.searchParams.get('before'), limit = Number(url.searchParams.get('limit') ?? 50)
      reads.push(url.search)
      if (before && failHistory) return json(res, { error: 'Earlier replies are temporarily unavailable.' }, 503)
      if (before && hold) await new Promise((r) => { release = r })
      const end = before ? messages.findIndex((m) => m.id === before) : messages.length
      json(res, { ...original, thread: row, messages: messages.slice(Math.max(0, end - limit), end), has_more: end > limit, turn: null, turns: [] })
    }
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'thread.snapshot', turns: [], pending: [], members: row.members })}\n\n`)
      clients.add(res); res.on('close', () => clients.delete(res))
    }
    const emitMembers = () => { for (const res of clients) res.write(`data: ${JSON.stringify({ type: 'members', members: row.members, background: 1, working: 0 })}\n\n`) }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, timezoneId: 'America/Los_Angeles', reducedMotion: 'reduce', isMobile: width === 390, hasTouch: width === 390 })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__aliasTasks = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', (e) => errors.push(e.message))
    const settle = () => page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
    const shot = async (state) => { await settle(); await page.screenshot({ path: path.join(out, `${state}-${width}-${theme}.png`), animations: 'disabled' }) }
    const panel = () => page.getByRole('dialog', { name: 'Tasks', exact: true })
    const details = async () => {
      await page.locator('.tt-tasks-button').click()
      await panel().locator('.tt-task-group').waitFor()
      assert.equal(await panel().locator('.tt-task-group').count(), 1)
      assert.match(await panel().locator('.tt-task-group-name').innerText(), /\bSprout$/)
      await panel().locator('.tt-task-row').click()
    }
    const offset = () => page.locator('[data-message-id="m2"]').evaluate((n) => n.getBoundingClientRect().top - n.closest('.tt-stream').getBoundingClientRect().top)
    try {
      await page.goto(`${app.url}/threads/${id}`)
      await page.locator('[data-message-id="m76"]').waitFor({ state: 'attached' })
      await page.waitForFunction(() => __aliasTasks.threads.st.open?.eventVersion > 0)
      assert.equal(await page.locator('.tt-composer .modelbtn-name').innerText(), 'Fable 5.2')
      assert.equal(await page.getByRole('button', { name: 'Providers & models', exact: true }).getAttribute('title'), "Agent's choice: Fable (now Fable 5.2)")
      assert.ok((await page.locator('.tt-author-model').allTextContents()).every((s) => s === 'Fable 5.1'))
      assert.equal(await page.locator('[data-message-id="m2"]').count(), 0)
      assert.equal(await page.locator('.tt-avatar-stack').count(), 0, 'header uses the shared thread icon without a member stack')
      assert.equal(await page.locator('.tt-head .tt-thread-icon').count(), 1)
      await page.locator('#draft').fill('Please continue the review.')
      await page.locator('.tt-listener[data-persona="sprout"]:not(.leaving)').waitFor()
      await shot('current-alias')
      await details()
      assert.equal(await panel().getByRole('button', { name: 'Started in this reply', exact: true }).count(), 0, 'null message has no link')
      task.message = 'm2'; emitMembers()
      const link = () => panel().getByRole('button', { name: 'Started in this reply', exact: true })
      await link().waitFor(); await shot('task-reply-link')
      await link().click()
      await panel().waitFor({ state: 'detached' })
      await page.locator('.tt-history-loading').waitFor()
      assert.equal(await page.locator('[data-message-id="m2"]').count(), 0, 'target is not loaded when navigation begins')
      hold = false; release?.()
      await page.locator('[data-message-id="m2"].tt-reply-highlight').waitFor()
      await page.locator('[data-message-id="m2"] .tt-prose-content:not(.tt-prose-plain)').waitFor()
      await settle()
      const landing = await offset()
      assert.ok(Math.abs(landing - 96) < 3, `target lands at 96 px, got ${landing}`)
      assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-message-id')), 'm2')
      assert.equal(await page.locator('#draft').innerText(), 'Please continue the review.', 'reply navigation retains the unsent draft')
      assert.equal(await page.locator('.tt-listener[data-persona="sprout"]:not(.leaving)').count(), 1)
      assert.deepEqual(reads.filter((r) => r.includes('before=')), ['?before=m65&limit=30', '?before=m35&limit=30', '?before=m5&limit=30'])
      await shot('reply-landing')
      await page.locator('.tt-reply-highlight').waitFor({ state: 'detached' })
      assert.ok(Math.abs(await offset() - landing) < 3, 'formatting and highlight expiry retain the landing')
      await page.locator('.tt-stream').evaluate((n) => n.dispatchEvent(new WheelEvent('wheel', { deltaY: 200 })))
      assert.equal(await page.evaluate(() => __aliasTasks.threads.st.open.taskReplyAnchor), null, 'reader movement releases the landing anchor')
      const historyReads = reads.length
      await details(); await link().click()
      await page.locator('[data-message-id="m2"].tt-reply-highlight').waitFor()
      assert.equal(reads.length, historyReads, 'a loaded target needs no history read')
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      // Depart while a page is outstanding: a late history response must not
      // jump or highlight a subsequently opened thread.
      await page.reload(); await page.locator('[data-message-id="m76"]').waitFor({ state: 'attached' })
      await page.waitForFunction(() => __aliasTasks.threads.st.open?.eventVersion > 0)
      hold = true
      await details(); await link().click(); await page.locator('.tt-history-loading').waitFor()
      await page.evaluate(() => __aliasTasks.threads.close())
      hold = false; release?.(); await settle()
      assert.equal(await page.locator('.tt-reply-highlight').count(), 0)
      // A failed page is visible and retrying the same task link recovers.
      await page.evaluate(async (id) => { await __aliasTasks.threads.open(id) }, id)
      await page.waitForFunction(() => __aliasTasks.threads.st.open?.eventVersion > 0)
      failHistory = true
      await details(); await link().click()
      await page.getByRole('alert').filter({ hasText: 'Earlier replies are temporarily unavailable.' }).waitFor()
      failHistory = false
      await details(); await link().click()
      await page.locator('[data-message-id="m2"].tt-reply-highlight').waitFor()
      assert.ok(Math.abs(await offset() - 96) < 3)
      task.message = 'missing-reply'; emitMembers()
      await details(); await link().click()
      await page.getByRole('alert').filter({ hasText: 'The reply that started this task is no longer available.' }).waitFor()
      assert.deepEqual(errors, [])
      assert.equal(app.broker.seen.some((r) => r.method === 'POST' && r.path.endsWith('/messages')), false)
      results.push({ width, theme, currentAlias: 'Fable 5.2', historicalChip: 'Fable 5.1', nullThenLinked: true,
        threadIdentity: true, groupedTaskLink: true, listeningDraftSurvivesJump: true,
        unloadedTargetPages: 3, landing, loadedTargetNoRead: true, highlightExpires: true, departureSafe: true, historyFailureAndRetry: true, missingReply: true, errors })
      console.log(`PASS ${width} ${theme}: current alias, late task link, three history pages, anchored/focused landing, expiry, retry and departure`)
    } finally { hold = false; release?.(); await context.close(); await app.close() }
  }
} finally { await browser.close() }
await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
const captures = await Promise.all((await readdir(out)).filter((f) => f.endsWith('.png')).sort().map(async (file) => {
  const bytes = await readFile(path.join(out, file))
  return { file, width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20), sha256: createHash('sha256').update(bytes).digest('hex') }
}))
await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
