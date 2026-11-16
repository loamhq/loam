// Actual page + relay + synthetic SSE. Baseline mode paints the starting commit
// and records its failures against exactly the same fixture and viewport.
import assert from 'node:assert/strict'
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_CONTINUITY_EVIDENCE
if (!out) throw new Error('Set THREAD_CONTINUITY_EVIDENCE')
const baseline = process.env.THREAD_CONTINUITY_BASELINE === '1'
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const results = [], id = 'a-quieter-week'
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = [], clients = new Set(), releases = [], lazyReleases = []
    const at = new Date().toISOString(), start = new Date(Date.now() - 180000).toISOString()
    let members = [{ persona: 'sprout', engine: 'claude-code', model: 'opus', working: false, background: 1,
      background_tasks: [{ id: 'one', description: 'Brief the interface: quiet waiting and steady replies', started_at: start }] }]
    let turns = [{ persona: 'sprout', engine: 'claude-code', model: 'opus', started_at: start, on_background: true }]
    const messages = [{ id: 'm1', author: 'you', kind: 'message', at, text: 'Please improve the thread experience.' }]
    const original = await app.f.api('GET', `/threads/${id}`)
    const providers = app.f.providers.map((p) => p.id === 'claude-code' ? { ...p, models: [{ id: 'opus', name: 'Opus' }] } : p)
    const summary = () => ({ ...original.thread, title: 'A little room to think', members, unread: 0, background: members[0].background, working: Number(members[0].working) })
    const snapshot = () => ({ type: 'thread.snapshot', thread: id, members, turns, turn: turns[0] ?? null, pending: [] })
    const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
    const emit = (event) => { for (const res of clients) res.write(`data: ${JSON.stringify(event)}\n\n`) }
    const publish = () => emit({ type: 'members', members })
    app.routes['GET /v1/providers'] = (req, res) => json(res, { providers })
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: [summary()], agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`data: ${JSON.stringify({ type: 'threads.snapshot', threads: [summary()], agents: app.f.agents })}\n\n`) }
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...original, thread: summary(), messages, has_more: false, turn: turns[0] ?? null, turns })
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`data: ${JSON.stringify(snapshot())}\n\n`); clients.add(res); res.on('close', () => clients.delete(res)) }
    const intro = 'I’m reading the notes and keeping the next step small.'
    const conclusion = 'The plan is ready. The reply stays here while its details arrive.'
    const steps = [{ type: 'text', text: intro }, { type: 'tool', id: 'read', name: 'Read', input: { file_path: 'plan.md' }, output: 'The plan is ready.' }, { type: 'text', text: conclusion }]
    app.routes[`GET /v1/threads/${id}/steps`] = (req, res, url) => {
      if (url.searchParams.get('message') === 'm4') releases.push(() => json(res, { steps: [{ message: 'm4', model: 'claude-opus-5-5', steps }] }))
      else if (url.searchParams.get('message') === 'lazy-5') lazyReleases.push(() => json(res, { steps: [{ message: 'lazy-5', steps: [
        { type: 'text', text: 'Earlier detail from the full record.\n\n'.repeat(180) },
        { type: 'text', text: 'Reading paragraph 5. ' + 'Keep this thought in view. '.repeat(20) },
      ] }] }))
      else { res.writeHead(404); res.end('{}') }
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, reducedMotion: 'reduce', isMobile: width === 390, hasTouch: width === 390 })
    if (baseline) for (const file of ['threads.js', 'thread-record.js', 'thread-experience.js', 'thread-opening.js', 'threads.css']) {
      const body = execFileSync('git', ['show', `23b5119:public/${file}`], { encoding: 'utf8' })
      await context.route(`**/${file}`, (route) => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body }))
    }
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__continuity = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', (error) => errors.push(error.message))
    const settle = () => page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
    const capture = async (state) => { await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(out, `${state}-${width}-${theme}.png`), animations: 'disabled' }) }
    const activity = page.locator(baseline ? '[data-live-member="sprout"]' : '.tt-tasks-button')
    try {
      await page.goto(app.url); await page.locator('.tt-row').waitFor()
      await page.evaluate((id) => __continuity.threads.open(id), id)
      await activity.waitFor(); await settle()
      emit({ type: 'turn.step', persona: 'sprout', label: '↳ mcp__engineer__brief', waiting: true })
      emit({ type: 'turn.waiting', persona: 'sprout', text: 'Sprout is waiting on 1 background task' })
      emit({ type: 'message.done', message: { id: 'm3', author: 'you', kind: 'message', at, text: 'Keep the waiting line below this newer message, too.' } })
      await page.locator('[data-message-id="m3"]').waitFor(); await settle()
      const emptyReplies = await page.locator('[data-message-id="draft:sprout"]').count()
      if (!baseline) {
        assert.equal(emptyReplies, 0, 'no reply slot before speech')
        assert.equal(await activity.count(), 1)
        assert.equal(await page.locator('.tt-live-area, .tt-member-activity').count(), 0)
        assert.doesNotMatch(await page.locator('.tt-stream').innerText(), /mcp__engineer|Sprout\s+Waiting\s+1/)
      }
      await capture('one-task')
      members = members.map((m) => ({ ...m, background: 2, background_tasks: [...m.background_tasks, { id: 'two', description: 'Check the reading position on phone and desktop', started_at: start }] })); publish()
      await settle(); await capture('two-tasks')
      if (!baseline) {
        await page.getByRole('button', { name: '2 background tasks', exact: true }).click()
        await page.waitForFunction(() => document.querySelectorAll('.tt-task-row time').length === 2)
        assert.equal(await page.locator('.tt-task-row time').count(), 2)
        assert.match(await page.locator('.tt-tasks-panel').innerText(), /3 min/)
        await capture('tasks-open')
        await page.locator('.tt-tasks-panel').press('Escape')
      }
      emit({ type: 'turn.finished', persona: 'sprout' })
      members = members.map((m) => ({ ...m, working: true, background: 0, background_tasks: [] })); publish()
      turns = [{ persona: 'sprout', engine: 'claude-code', model: 'opus', started_at: at }]
      emit({ type: 'turn.started', ...turns[0] }); await page.locator('[data-message-id="draft:sprout"] .awaiting-words').waitFor(); await settle()
      if (!baseline) { assert.equal(await page.locator('[data-message-id="draft:sprout"]').count(), 1); assert.match(await page.locator('[data-message-id="draft:sprout"]').innerText(), /Thinking/) }
      await capture('generating')
      emit({ type: 'message.delta', persona: 'sprout', draft: intro })
      await page.locator('[data-message-id="draft:sprout"]').getByText(intro, { exact: true }).waitFor()
      emit({ type: 'turn.step', persona: 'sprout', label: 'Read: plan.md' })
      emit({ type: 'message.delta', persona: 'sprout', draft: conclusion })
      await page.getByText(conclusion, { exact: true }).waitFor(); await settle()
      await page.locator('[data-message-id="draft:sprout"] .fold-head').click(); await settle()
      await page.evaluate(() => {
        globalThis.__replyNode = document.querySelector('[data-message-id="draft:sprout"]')
        globalThis.__proseNode = __replyNode.querySelector('.say')
        globalThis.__foldNode = __replyNode.querySelector('.fold')
        globalThis.__removed = []
        globalThis.__mutations = new MutationObserver((records) => {
          for (const r of records) for (const n of r.removedNodes) if (n === __replyNode || n.contains?.(__replyNode) || n === __proseNode || n.contains?.(__proseNode)) __removed.push(n.nodeName)
        })
        __mutations.observe(document.querySelector('.tt-stream'), { childList: true, subtree: true })
      })
      await capture('streaming')
      const done = { id: 'm4', author: 'sprout', kind: 'message', engine: 'claude-code', model: 'claude-opus-5-5', at, text: conclusion }
      emit({ type: 'message.done', message: done }); await page.locator('[data-message-id="m4"]').waitFor(); await settle()
      const doneSameNode = await page.evaluate(() => __replyNode === document.querySelector('[data-message-id="m4"]'))
      emit(snapshot()); publish(); await settle()
      if (!baseline) {
        assert.equal(doneSameNode, true)
        assert.equal(await page.evaluate(() => __proseNode === __replyNode.querySelector('.say')), true)
        assert.equal(await page.evaluate(() => __foldNode === __replyNode.querySelector('.fold')), true)
        assert.match(await page.locator('[aria-label="Providers & models"]').first().innerText(), /Opus 5.5/)
        assert.equal(await page.locator('[aria-label="Providers & models"]').first().getAttribute('title'), "Agent's choice: Opus (latest reply used claude-opus-5-5)")
      }
      await capture('done')
      await page.waitForFunction(() => __continuity.threads.st.open.replySteps.entries.get('m4')?.status === 'loading')
      releases.splice(0).forEach((fn) => fn())
      await page.waitForFunction(() => Array.isArray(__continuity.threads.st.open.messages.find((m) => m.id === 'm4')?.steps)); await settle()
      const stepsSameNode = await page.evaluate(() => __replyNode === document.querySelector('[data-message-id="m4"]'))
      const removed = await page.evaluate(() => { __mutations.disconnect(); return __removed })
      if (!baseline) {
        assert.equal(stepsSameNode, true); assert.deepEqual(removed, [])
        assert.equal(await page.getByText(intro, { exact: true }).count(), 1)
        assert.equal(await page.getByText(conclusion, { exact: true }).count(), 1)
        assert.equal(await page.locator('[data-message-id="m4"] .fold-head').getAttribute('aria-expanded'), 'true', 'a streamed open fold stays open when enriched')
      }
      await capture('steps-loaded')
      emit({ type: 'turn.finished', persona: 'sprout' }); members = members.map((m) => ({ ...m, working: false })); turns = []; publish()
      emit(snapshot()); await settle()
      if (!baseline) assert.equal(await activity.count(), 0)
      await capture('settled')
      // Put two large replies above a later reading point. The first reply
      // crosses the pane top but must never become the anchor at eye level.
      await page.evaluate(() => {
        const o = __continuity.threads.st.open
        o.messages = Array.from({ length: 12 }, (_, i) => ({ id: `scroll-${i}`, author: 'sprout', engine: 'claude-code', kind: 'message', text: `Reading paragraph ${i}. ` + 'Keep this thought in view. '.repeat(20) }))
        __continuity.schedule()
      })
      await page.locator('[data-message-id="scroll-6"]').waitFor({ state: 'attached' }); await settle()
      await page.locator('[data-message-id="scroll-6"]').evaluate((node) => {
        const pane = node.closest('.tt-stream'); pane.scrollTop += node.getBoundingClientRect().top - pane.getBoundingClientRect().top - pane.clientHeight / 3
        pane.dispatchEvent(new Event('scroll'))
      }); await settle()
      await capture('scroll-before')
      const growth = await page.evaluate(async () => {
        const { readingAnchor, restoreAnchor } = await import('/thread-opening.js')
        const pane = document.querySelector('.tt-stream'), anchor = readingAnchor(pane)
        const target = document.querySelector('[data-message-id="scroll-6"]')
        const before = target.getBoundingClientRect().top
        const grow = [...pane.querySelectorAll('[data-message-id]')].find((n) => n.getBoundingClientRect().bottom > pane.getBoundingClientRect().top)
        grow.style.height = `${grow.getBoundingClientRect().height + 5000}px`
        restoreAnchor(pane, anchor)
        return { anchor, grown: grow.dataset.messageId, moved: target.getBoundingClientRect().top - before }
      })
      if (!baseline) assert.ok(Math.abs(growth.moved) < 2, `5000px growth moved eye anchor ${growth.moved}px`)
      await capture('scroll-after')
      // The same compensation must happen for a resize without a render.
      const asyncGrowth = await page.evaluate(async () => {
        const { readingAnchor } = await import('/thread-opening.js'), pane = document.querySelector('.tt-stream')
        const anchor = readingAnchor(pane), node = document.querySelector(`[data-message-id="${anchor.id}"]`), before = node.getBoundingClientRect().top
        __continuity.threads.st.open.readingPosition = anchor
        const image = document.createElement('img'); image.alt = ''; image.style.display = 'block'; image.style.width = '10px'
        document.querySelector('[data-message-id="scroll-2"]').append(image)
        image.src = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="1000"/>')
        await image.decode(); await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame)
        return node.getBoundingClientRect().top - before
      })
      if (!baseline) assert.ok(Math.abs(asyncGrowth) < 2, `image growth moved anchor ${asyncGrowth}px`)
      // Exercise the real deferred steps read and redraw above the reader.
      await page.evaluate(() => {
        const o = __continuity.threads.st.open
        o.atEnd = true; o.replyAnchor = null; o.readingPosition = null
        o.messages = o.messages.map((m) => ({ ...m, id: m.id.replace('scroll-', 'lazy-') }))
        __continuity.schedule()
      }); await settle()
      await page.locator('[data-message-id="lazy-6"]').evaluate((node) => {
        const pane = node.closest('.tt-stream'); pane.scrollTop += node.getBoundingClientRect().top - pane.getBoundingClientRect().top - pane.clientHeight / 3
        pane.dispatchEvent(new Event('scroll'))
      }); await settle()
      await page.evaluate(() => __continuity.threads.st.open.replySteps.ensure('lazy-5'))
      const lazyTop = await page.locator('[data-message-id="lazy-6"]').evaluate((n) => n.getBoundingClientRect().top)
      const lazyHeight = await page.locator('[data-message-id="lazy-5"]').evaluate((n) => n.getBoundingClientRect().height)
      await capture('lazy-before')
      const deadline = Date.now() + 10000
      while (!lazyReleases.length) { assert.ok(Date.now() < deadline, 'deferred steps request reached the fixture'); await new Promise((resolve) => setTimeout(resolve, 5)) }
      lazyReleases.splice(0).forEach((fn) => fn())
      await page.waitForFunction(() => Array.isArray(__continuity.threads.st.open.messages.find((m) => m.id === 'lazy-5')?.steps)); await settle()
      const lazyMoved = await page.locator('[data-message-id="lazy-6"]').evaluate((n, top) => n.getBoundingClientRect().top - top, lazyTop)
      const lazyAdded = await page.locator('[data-message-id="lazy-5"]').evaluate((n, height) => n.getBoundingClientRect().height - height, lazyHeight)
      if (!baseline) { assert.ok(lazyAdded > 5000, `full steps added only ${lazyAdded}px`); assert.ok(Math.abs(lazyMoved) < 2, `lazy full steps moved reader ${lazyMoved}px`) }
      await capture('lazy-after')
      // Anchor inside one long reply, not merely on a later message's top.
      await page.evaluate(() => {
        const o = __continuity.threads.st.open
        o.atEnd = true; o.replyAnchor = null; o.readingPosition = null
        o.messages = [{ id: 'inside', author: 'sprout', kind: 'message', text: Array.from({ length: 20 }, (_, i) => `Thought ${i}. ` + 'Keep this paragraph still. '.repeat(10)).join('\n\n') }]
        __continuity.schedule()
      })
      await page.locator('[data-message-id="inside"] .tt-prose-content:not(.tt-prose-plain)').waitFor(); await settle()
      await page.locator('[data-message-id="inside"] p').nth(8).evaluate((node) => {
        const pane = node.closest('.tt-stream'); pane.scrollTop += node.getBoundingClientRect().top - pane.getBoundingClientRect().top - pane.clientHeight / 3
        pane.dispatchEvent(new Event('scroll'))
      }); await settle()
      const insideMoved = await page.evaluate(async () => {
        const { readingAnchor, restoreAnchor } = await import('/thread-opening.js'), pane = document.querySelector('.tt-stream')
        const anchor = readingAnchor(pane), target = document.querySelectorAll('[data-message-id="inside"] p')[8], before = target.getBoundingClientRect().top
        const previous = document.querySelector('[data-message-id="inside"] p')
        previous.style.height = `${previous.getBoundingClientRect().height + 5000}px`
        restoreAnchor(pane, anchor)
        return target.getBoundingClientRect().top - before
      })
      if (!baseline) assert.ok(Math.abs(insideMoved) < 2, `growth inside the same reply moved reader ${insideMoved}px`)
      assert.deepEqual(errors, [])
      results.push({ width, theme, baseline, emptyReplies, doneSameNode, stepsSameNode, removed, growth, asyncGrowth, lazyAdded, lazyMoved, insideMoved, errors })
      console.log(`PASS ${width} ${theme}: completion node=${doneSameNode}, steps node=${stepsSameNode}, scroll delta=${growth.moved}, image delta=${asyncGrowth}, lazy delta=${lazyMoved} (${lazyAdded}px added), inside reply delta=${insideMoved}`)
    } finally { releases.splice(0).forEach((fn) => fn()); lazyReleases.splice(0).forEach((fn) => fn()); await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
  const captures = []
  for (const file of (await readdir(out)).filter((name) => name.endsWith('.png')).sort()) {
    captures.push({ file, sha256: createHash('sha256').update(await readFile(path.join(out, file))).digest('hex') })
  }
  await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
} finally { await browser.close() }
