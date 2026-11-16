// Painted full-record thread contract. Synthetic only.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { oneProductFixture } from './one-product-fixture.mjs'
import { fullSteps, classicEvents, intro, conclusion, longOutput } from './thread-steps-fixture.mjs'

const out = process.env.THREAD_STEPS_EVIDENCE
if (!out) throw new Error('Set THREAD_STEPS_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const sharp = (await import(process.env.LOAM_SHARP || 'sharp')).default
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const expose = '\nglobalThis.__steps = { threads, running, schedule, goChats, openBlocks };'
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const results = [], captures = []
const id = 'a-quieter-week', classicId = `classic-${id}`
try {
for (const width of (process.env.THREAD_STEPS_WIDTH ? [Number(process.env.THREAD_STEPS_WIDTH)] : [390, 1440])) for (const theme of (process.env.THREAD_STEPS_THEME ? [process.env.THREAD_STEPS_THEME] : ['light', 'dark'])) {
  const app = await oneProductFixture(), errors = [], reads = [], liveClients = new Set()
  let holdSteps = true, releases = [], residentState = 'idle', failNext = false
  const at = '2026-11-10T18:00:00Z'
  const reply = { id: 'm44', author: 'sprout', kind: 'message', at, text: conclusion, did: [{ label: `Said: ${intro}` }, { label: 'Read the notes' }, { label: 'Checked the weekly notes' }, { label: '↳ Reviewed the plan' }] }
  const old = { id: 'm42', author: 'sprout', kind: 'message', at, text: 'An older reply with its readable record.', did: [{ label: 'Read an older note' }] }
  const human = { id: 'm43', author: 'you', kind: 'message', at, text: 'Can you check the notes?' }
  const padding = { id: 'm41', author: 'sprout', kind: 'message', at, text: Array.from({ length: 30 }, () => 'Keep useful details close to the conversation. Leave room for the next step.').join('\n\n'), did: [] }
  const json = (res, value, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
  app.routes[`GET /v1/threads/${id}`] = async (req, res, url) => {
    const record = await app.f.api('GET', `/threads/${id}${url.search}`)
    record.messages = [{ ...padding, id: 'm40' }, padding, old, human, reply]; record.turn = null
    record.thread.members = record.thread.members.map((m) => ({ ...m, busy: false, running: true }))
    json(res, record)
  }
  const fullEntry = (message) => ({ message, thread: id, author: 'sprout', backfilled: true, replies: 3, steps: fullSteps })
  app.routes[`GET /v1/threads/${id}/steps`] = async (req, res, url) => {
    const message = url.searchParams.get('message'); reads.push(message)
    if (holdSteps) await new Promise((resolve) => releases.push(resolve))
    if (failNext && message === 'm44') { failNext = false; return json(res, { error: 'Temporary read failure' }, 503) }
    if (message === 'm44' || message === 'm45') return json(res, { thread: id, steps: [fullEntry(message)] })
    json(res, { error: 'No full record for this reply' }, 404)
  }
  const events = classicEvents(), record = app.records.find((r) => r.session_id === classicId)
  Object.assign(record, { agent: 'sprout', record_seq: events.length, seen_seq: events.length,
    turns: [{ model: 'claude-sonnet-4-6', usage: { input_tokens: 1800, output_tokens: 210 }, cost_delta_usd: 0.02 }] })
  app.routes[`GET /v1/sessions/${classicId}/events`] = (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n')
    for (const event of [{ type: 'broker.session', ...record, window_first_seq: 1, turns_before: 0 }, ...events,
      { type: 'broker.session', ...record }]) res.write(`data: ${JSON.stringify(event)}\n\n`)
  }
  const live = () => ({ sessions: [{ id: 'resident', agent: 'sprout', engine: 'claude-code', kind: 'thread', thread_id: id, thread_title: record.title, state: residentState }], box: { sessions: { live: 1 } } })
  app.routes['GET /v1/sessions/live'] = (req, res) => json(res, live())
  app.routes['GET /v1/sessions/live/events'] = (req, res) => { liveClients.add(res); res.on('close', () => liveClients.delete(res)); res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); res.write(`data: ${JSON.stringify({ type: 'sessions.live', ...live() })}\n\n`) }
  const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, reducedMotion: 'reduce', isMobile: width === 390, hasTouch: width === 390 })
  await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + expose }))
  const page = await context.newPage(); page.setDefaultTimeout(12000); page.on('pageerror', (e) => errors.push(e.message))
  const settle = async () => { await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }) }
  const thread = async () => {
    await page.evaluate((id) => { __steps.openBlocks.clear(); return __steps.threads.open(id) }, id)
    // Revisits restore their reading position; this test explicitly inspects
    // the latest reply, which makes its full record eligible to load.
    await page.locator('[data-message-id="m44"]').scrollIntoViewIfNeeded()
    await page.waitForFunction(() => Array.isArray(__steps.threads.st.open.messages.find((m) => m.id === 'm44')?.steps))
    await settle()
  }
  const fold = () => page.locator('.msgs .fold').filter({ has: page.locator('.fold-head b', { hasText: '6 steps' }) }).first()
  const openFold = async () => { const head = fold().locator(':scope > .fold-head'); if (await head.getAttribute('aria-expanded') !== 'true') await head.click(); await fold().locator('.toolrow').first().waitFor(); await settle() }
  const tool = (name, summary) => fold().locator('.toolrow').filter({ has: page.locator(':scope > .toolrow-head .sigil', { hasText: name }) }).filter({ hasText: summary }).first()
  const openTool = async (name, summary) => { const row = tool(name, summary), head = row.locator(':scope > .toolrow-head'); if (await head.getAttribute('aria-expanded') !== 'true') await head.click(); await settle(); return row }
  const align = async (locator) => { await locator.evaluate((node) => { const pane = node.closest('.msgs'); pane.scrollTop += node.getBoundingClientRect().top - pane.getBoundingClientRect().top - 60 }); await settle() }
  const capture = async () => { await page.mouse.move(0, 0); await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); return page.screenshot({ animations: 'disabled' }) }
  const pair = async (name, left) => { await left(); await settle(); const file = name + '-' + width + '-' + theme + '.png'; const dest = path.join(out, file); await page.screenshot({ path: dest, animations: 'disabled' }); captures.push({ file, width, theme, sha256: createHash('sha256').update(await readFile(dest)).digest('hex') }); console.log(file) }
  try {
    await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
    assert.deepEqual(reads, [], 'list never fetches full records')
    await page.evaluate((id) => __steps.threads.open(id), id)
    await page.locator('[data-message-id="m44"]').waitFor()
    await page.waitForFunction(() => __steps.threads.st.open.replySteps.entries.get('m44')?.status === 'loading')
    assert.ok(await page.locator('[data-message-id="m44"]').innerText().then((t) => t.includes(conclusion)), 'first paint is readable while full record waits')
    assert.equal(reads.includes('m40'), false, 'offscreen reply does not delay opening')
    assert.ok(app.broker.seen.filter((r) => r.path === `/v1/threads/${id}`).every((r) => !r.search.includes('steps=')), 'opening/history reads stay light')
    const fallbackHead = page.locator('[data-message-id="m44"] .fold-head')
    await fallbackHead.click(); holdSteps = false; releases.splice(0).forEach((resolve) => resolve())
    await page.waitForFunction(() => Array.isArray(__steps.threads.st.open.messages.find((m) => m.id === 'm44')?.steps))
    assert.equal(await fold().locator('.fold-head').getAttribute('aria-expanded'), 'true', 'opening the fallback stays open after full record arrives')
    assert.equal(reads.filter((m) => m === 'm44').length, 1)
    await page.evaluate(() => __steps.schedule()); await settle(); assert.equal(reads.filter((m) => m === 'm44').length, 1)
    await pair('closed', async () => { await thread(); await align(page.locator('[data-message-id="m44"]')) })
    await pair('opened', async () => { await thread(); await openFold(); await align(fold()) })
    assert.ok(await fold().locator('.tool-toggle').evaluateAll((nodes) => nodes.every((node) => node.getBoundingClientRect().height >= 44)), 'step controls retain their full touch targets')
    await pair('interleaved', async () => { await thread(); await align(page.locator('[data-message-id="m44"]')) })
    await pair('long-output', async () => { await thread(); await openFold(); const row = await openTool('Bash', 'check weekly-notes'); await align(row) })
    const long = tool('Bash', 'check weekly-notes')
    assert.ok(await long.getByRole('button', { name: 'Show more', exact: true }).evaluate((node) => node.getBoundingClientRect().height >= 44))
    assert.equal(await long.locator('.out.tool-content').innerText(), longOutput)
    assert.ok(await long.locator('.out.tool-content').evaluate((n) => n.clientHeight <= 241 && n.scrollHeight > n.clientHeight), 'output is visually clamped')
    await long.getByRole('button', { name: 'Show more', exact: true }).click()
    await long.locator('.out.tool-content:not(.clamped)').waitFor({ state: 'attached' })
    assert.equal(await long.locator('.out.tool-content').evaluate((n) => n.classList.contains('clamped')), false)
    assert.ok((await long.locator('.out.tool-content').innerText()).endsWith('Check 90: the weekly notes are ready to review.'))
    await long.getByRole('button', { name: 'Show less', exact: true }).click()
    await long.locator('.out.tool-content.clamped').waitFor({ state: 'attached' })
    await pair('subagent', async () => { await thread(); await openFold(); const row = await openTool('Agent', 'Review the plan'); await align(row); assert.ok((await row.innerText()).includes('I’ll check the smaller details.')); await row.locator('.fold-head').click() })
    await pair('answered', async () => { await thread(); await openFold(); await align(page.locator('.answered')) })
    assert.ok((await page.locator('.answered').innerText()).includes('A little room'))
    await thread(); await page.locator('[data-message-id="m42"]').scrollIntoViewIfNeeded(); await page.locator('[data-message-id="m42"] .fold-head').click()
    await page.getByText('Read an older note', { exact: true }).waitFor()
    assert.equal(reads.filter((m) => m === 'm42').length >= 1, true)
    const fallbackReads = reads.filter((m) => m === 'm42').length; await page.evaluate(() => __steps.schedule()); await settle(); assert.equal(reads.filter((m) => m === 'm42').length, fallbackReads)
    // A delayed full read must not move a reader who has scrolled up.
    await page.evaluate(() => { const o = __steps.threads.st.open, pane = document.querySelector('.tt-stream'); pane.scrollTop = 100; pane.dispatchEvent(new Event('scroll')); o.atEnd = false })
    const before = await page.evaluate(async () => {
      const pane = document.querySelector('.tt-stream')
      return (await import('/thread-opening.js')).readingAnchor(pane)
    })
    app.emit(id, { type: 'turn.started', persona: 'sprout' }); app.emit(id, { type: 'message.delta', persona: 'sprout', draft: 'First words.' })
    app.emit(id, { type: 'turn.step', persona: 'sprout', label: 'Read the note' })
    app.emit(id, { type: 'message.delta', persona: 'sprout', draft: 'Last words.' })
    app.emit(id, { type: 'message.done', message: { ...reply, id: 'm45' } })
    await page.locator('[data-message-id="m45"]').waitFor(); await settle()
    const anchorDelta = await page.evaluate((anchor) => {
      const pane = document.querySelector('.tt-stream'), node = document.querySelector(`[data-message-id="${anchor.id}"]`)
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT)
      let occurrence = 0
      while (walker.nextNode()) for (let at = walker.currentNode.nodeValue.indexOf(anchor.text); at >= 0; at = walker.currentNode.nodeValue.indexOf(anchor.text, at + 1)) {
        if (occurrence++ !== anchor.occurrence) continue
        const range = document.createRange(); range.setStart(walker.currentNode, at + anchor.char); range.setEnd(walker.currentNode, at + anchor.char + 1)
        return range.getBoundingClientRect().top - pane.getBoundingClientRect().top - anchor.textOffset
      }
      return node.getBoundingClientRect().top - pane.getBoundingClientRect().top - anchor.offset
    }, before)
    assert.ok(Math.abs(anchorDelta) < 2, `completed live turn moves the text at the reader's eye ${anchorDelta}px`)
    assert.equal(reads.includes('m45'), false, 'a light live completion stays deferred while offscreen')
    await page.locator('[data-message-id="m45"]').scrollIntoViewIfNeeded()
    await page.waitForFunction(() => Array.isArray(__steps.threads.st.open.messages.find((m) => m.id === 'm45')?.steps))
    assert.deepEqual(await page.locator('[data-message-id="m45"] .say.agent').evaluateAll((nodes) => nodes.map((node) => {
      const prose = node.cloneNode(true); prose.querySelector('.tt-reply-footer')?.remove(); return prose.textContent
    })), [intro, conclusion], 'finished live reply uses full order, not accumulated label fragments')
    assert.equal(await page.locator('[data-message-id="m45"] .tt-reply-footer time').count(), 1, 'the date is metadata beside read aloud, outside the prose comparison')
    assert.equal(await page.locator('.awaiting-words').filter({ hasText: 'Sprout' }).count(), 0, 'a completed reply has no extra empty waiting slot')
    assert.equal(await page.evaluate(() => __steps.threads.st.open.activeTurns.has('sprout')), true, 'turn liveness remains the runtime’s fact')
    app.emit(id, { type: 'turn.finished', persona: 'sprout' })
    await page.locator('.awaiting-words').filter({ hasText: 'Sprout' }).waitFor({ state: 'detached' })
    // Plans/diffs keep their compact summaries while exposing raw records too.
    app.emit(id, { type: 'message.done', message: { ...reply, id: 'm46', steps: [
      { type: 'tool', id: 'edit', name: 'Edit', input: { file_path: 'plan.md', old_string: 'Before', new_string: 'After' }, output: 'The plan was updated.' },
      { type: 'tool', id: 'plan', name: 'TodoWrite', input: { todos: [{ content: 'Review the plan', status: 'completed' }] }, output: 'The plan is ready.' },
      { type: 'text', text: 'A small update is ready.' },
    ] } })
    const extra = page.locator('[data-message-id="m46"]'); await extra.waitFor(); await extra.locator('.fold-head').click()
    await extra.locator('.diff-head').click(); await page.getByText('The plan was updated.', { exact: true }).waitFor()
    assert.ok((await extra.locator('.record-diff .tool-input').innerText()).includes('new_string'))
    await extra.locator('.plan-head').click(); await page.getByText('The plan is ready.', { exact: true }).waitFor()
    assert.ok((await extra.locator('.plan .tool-input').innerText()).includes('todos'))
    // A transient read preserves light prose and offers one explicit retry.
    failNext = true
    await page.evaluate((id) => __steps.threads.open(id), id)
    await page.locator('[data-message-id="m44"]').scrollIntoViewIfNeeded()
    await page.locator('[data-message-id="m44"] .tt-steps-retry').waitFor()
    assert.ok((await page.locator('[data-message-id="m44"]').innerText()).includes(conclusion))
    await page.locator('[data-message-id="m44"]').getByRole('button', { name: 'Try again', exact: true }).click()
    await page.waitForFunction(() => Array.isArray(__steps.threads.st.open.messages.find((m) => m.id === 'm44')?.steps))
    // Leaving while an enrichment read is pending cannot paint another thread.
    holdSteps = true
    await page.evaluate((id) => __steps.threads.open(id), id)
    await page.locator('[data-message-id="m44"]').scrollIntoViewIfNeeded()
    await page.waitForFunction(() => __steps.threads.st.open.replySteps.entries.get('m44')?.status === 'loading')
    await page.evaluate(() => __steps.threads.open('weekend'))
    holdSteps = false; releases.splice(0).forEach((resolve) => resolve()); await settle()
    assert.equal(await page.evaluate(() => __steps.threads.st.open.thread.id), 'weekend')
    assert.equal(await page.locator('[data-message-id="m44"]').count(), 0)
    // Residency and turn state travel independently, over the actual live view.
    await thread(); await page.getByTitle('Thread details', { exact: true }).click()
    if (width === 1440) await page.getByRole('navigation', { name: 'Thread settings' }).getByRole('button', { name: 'Members', exact: true }).click()
    else await page.locator('#tt-members').scrollIntoViewIfNeeded()
    const details = page.locator('.tt-member').first()
    assert.equal(await details.locator('.run-live').count(), 0, 'warm idle member has no dot')
    await details.getByRole('button', { name: 'End session', exact: true }).waitFor()
    const dotCapture = async (state) => { residentState = state; for (const res of liveClients) res.write(`data: ${JSON.stringify({ type: 'sessions.live', ...live() })}\n\n`); await settle(); await page.waitForTimeout(80) }
    const idleImage = await capture()
    await dotCapture('working'); await details.locator('.run-live').waitFor(); assert.equal(await details.locator('.run-live').getAttribute('aria-label'), 'Turn in progress')
    const workingImage = await capture()
    const file = `dots-${width}-${theme}.png`, labels = Buffer.from(`<svg width="${width * 2}" height="32"><text x="14" y="22" fill="${theme === 'dark' ? '#eee' : '#222'}" font-family="sans-serif" font-size="13">Warm idle · End session available</text><text x="${width + 14}" y="22" fill="${theme === 'dark' ? '#eee' : '#222'}" font-family="sans-serif" font-size="13">Turn in progress · dot</text></svg>`)
    await sharp({ create: { width: width * 2, height: idleImage.readUInt32BE(20) + 32, channels: 4, background: theme === 'dark' ? '#1a1715' : '#e6e1da' } }).composite([{ input: idleImage, left: 0, top: 32 }, { input: workingImage, left: width, top: 32 }, { input: labels, left: 0, top: 0 }]).png().toFile(path.join(out, file))
    captures.push({ file, viewportWidth: width, width: width * 2, height: idleImage.readUInt32BE(20) + 32, theme, sha256: createHash('sha256').update(await readFile(path.join(out, file))).digest('hex') })
    await dotCapture('asking'); assert.equal(await details.locator('.run-live').count(), 1)
    await dotCapture('background'); assert.equal(await details.locator('.run-live').count(), 0)
    await dotCapture('idle'); assert.equal(await details.locator('.run-live').count(), 0); await details.getByRole('button', { name: 'End session', exact: true }).waitFor()
    await page.evaluate(() => { __steps.threads.close(); __steps.goChats() }); await page.locator('.tt-row').first().waitFor()
    const listRow = page.locator('.tt-row').filter({ hasText: record.title })
    assert.equal(await listRow.locator('.dot.beating').count(), 0)
    await dotCapture('working'); assert.equal(await listRow.locator('.dot.beating').count(), 0)
    await dotCapture('asking'); assert.equal(await listRow.locator('.dot.beating').count(), 0)
    await dotCapture('idle'); assert.equal(await listRow.locator('.dot.beating').count(), 0)
    assert.deepEqual(errors, [])
    results.push({ width, theme, lazyInitialPaint: true, offscreenDeferred: true, cached: true, fallback: true, lazyRetry: true, lateLoadIgnored: true, fullLiveOrder: true, replyKeepsWorking: true, scrollBackPreserved: true, longOutputComplete: true, subagentNested: true, questions: true, planDiffDetails: true, dots: ['idle', 'working', 'asking', 'background'], idleEndSession: true, pageErrors: errors })
  } finally { holdSteps = false; releases.splice(0).forEach((resolve) => resolve()); await context.close(); await app.close() }
}
} finally { await browser.close() }
await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
console.log(JSON.stringify(results, null, 2))
