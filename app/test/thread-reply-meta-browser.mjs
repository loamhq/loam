// Painted thread identity/time controls, synthetic records through the relay.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_REPLY_META_EVIDENCE
if (!out) throw new Error('Set THREAD_REPLY_META_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const sharp = (await import(process.env.LOAM_SHARP || 'sharp')).default
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const id = 'a-quieter-week', at = '2026-11-10T21:47:00Z', results = [], captures = []
const words = Array.from({ length: 24 }, () => 'A good plan leaves room for the unexpected. Keep the next step small, the useful details close, and the conversation easy to return to.').join('\n\n')
const reply = (id, engine, model, text = words, extra = {}) => ({ id, author: 'sprout', kind: 'message', engine, model, at, text, did: [], ...extra })
const recorded = reply('full-reply', 'claude-code', 'fable', words, { steps: [
  { type: 'text', text: 'I’ll check the notes, then bring the useful details back here.', message: { model: 'fable' } },
  { type: 'tool', id: 'read', name: 'Read', input: { file_path: 'notes.md' }, output: 'The notes leave room for the next step.' },
  { type: 'text', text: words },
  { type: 'end', event: { type: 'result', usage: { input_tokens: 1200, output_tokens: 360 }, total_cost_usd: 0.02, duration_ms: 1400, is_error: false } },
] })
try {
for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
  const app = await oneProductFixture(), errors = []
  Object.assign(app.f.providers[0], { models: [{ id: 'fable', name: 'Claude Fable 5.1' }, { id: 'opus', name: 'Opus 5.5' }] })
  app.f.providers[1].models = [{ id: 'sol', name: 'GPT-6.1-Sol' }]
  app.f.providers[2].models = [{ id: 'preview', name: 'Gemini 3.0 Pro Preview' }]
  app.f.providers.push({ id: 'future', name: 'Future Provider', connected: false })
  app.routes[`GET /v1/threads/${id}`] = async (req, res) => {
    const record = await app.f.api('GET', `/threads/${id}`)
    record.messages = [recorded]; record.turn = null
    res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(record))
  }
  app.routes[`GET /v1/threads/${id}/steps`] = (req, res) => { res.writeHead(404, { 'content-type': 'application/json' }); res.end('{"error":"No full record"}') }
  const classicId = `classic-${id}`, classicRecord = app.records.find((r) => r.session_id === classicId)
  classicRecord.turns = [{ model: 'fable', usage: { input_tokens: 1200, output_tokens: 360 }, cost_delta_usd: 0.02 }]
  const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, timezoneId: 'America/Los_Angeles', reducedMotion: 'reduce', isMobile: width === 390, hasTouch: width === 390 })
  await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__meta = { threads, state, schedule };' }))
  await context.route('**/api/health', (route) => route.fulfill({ json: { configured: true, ok: true, speech: true } }))
  await context.route('**/x/read-aloud/status', r => r.fulfill({ json: { speech: true } }))
  await context.route(/\/x\/read-aloud\/speech(?:\/.*)?$/, (route) => {
    assert.equal(new URL(route.request().url()).pathname, '/x/read-aloud/speech/lookup', 'no audio generation in this test')
    return route.fulfill({ json: { status: 'none' } })
  })
  const page = await context.newPage(); page.setDefaultTimeout(12000)
  await page.clock.setFixedTime(new Date(at))
  page.on('pageerror', (e) => errors.push(e.message))
  const settle = async () => page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
  const stage = async (messages) => {
    await page.evaluate((messages) => {
      const o = __meta.threads.st.open
      Object.assign(o, { messages, atEnd: false, restoreTop: 0, has_more: false, pending: null })
      o.typing.clear(); o.permissions.clear(); o.steps.clear(); o.turnDetails?.clear()
      __meta.schedule()
    }, messages)
    await page.locator(`[data-message-id="${messages[0].id}"]`).waitFor(); await settle()
  }
  const align = async (locator, top = 90) => {
    await locator.evaluate((node, offset) => { const pane = node.closest('.msgs'); pane.scrollTop += node.getBoundingClientRect().top - pane.getBoundingClientRect().top - offset }, top)
    await settle()
  }
  const screenshot = async () => { await page.mouse.move(0, 0); await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); return page.screenshot({ animations: 'disabled' }) }
  const pair = async (name, leftLabel, rightLabel, left, right) => {
    await left(); const a = await screenshot(); await right(); const b = await screenshot()
    const height = a.readUInt32BE(20), file = `${name}-${width}-${theme}.png`, bg = theme === 'dark' ? '#1a1715' : '#e6e1da', ink = theme === 'dark' ? '#e6e1da' : '#2a2521'
    const labels = Buffer.from(`<svg width="${width * 2}" height="32"><text x="14" y="22" fill="${ink}" font-family="sans-serif" font-size="13">${leftLabel}</text><text x="${width + 14}" y="22" fill="${ink}" font-family="sans-serif" font-size="13">${rightLabel}</text></svg>`)
    const dest = path.join(out, file)
    await sharp({ create: { width: width * 2, height: height + 32, channels: 4, background: bg } }).composite([{ input: a, left: 0, top: 32 }, { input: b, left: width, top: 32 }, { input: labels, left: 0, top: 0 }]).png().toFile(dest)
    captures.push({ file, width, theme, sha256: createHash('sha256').update(await readFile(dest)).digest('hex') }); console.log(file)
  }
  try {
    await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
    await page.evaluate((id) => __meta.threads.open(id), id)
    await page.locator('[data-message-id="full-reply"]').waitFor()
    for (const [engine, model, compact, full] of [
      ['claude-code', 'fable', 'Fable 5.1', 'Claude Code · Claude Fable 5.1'],
      ['codex', 'sol', 'GPT-6.1-Sol', 'Codex · GPT-6.1-Sol'],
      ['antigravity', 'preview', 'Gemini 3.0', 'Antigravity · Gemini 3.0 Pro Preview'],
    ]) {
      const message = reply(`provider-${engine}`, engine, model)
      const run = () => page.locator('.tt-author-run').first()
      await pair(engine, `${engine} · natural chip`, `${engine} · sticky chip`, async () => {
        await stage([message]); await align(run())
        assert.equal(await run().locator('.tt-author-model').innerText(), compact)
        assert.equal(await run().locator('.tt-author-meta').getAttribute('title'), `${full} · ${model}`)
        assert.equal(await run().locator('.tt-author-meta').getAttribute('aria-label'), `${full} · ${model}`)
        assert.equal(await run().locator(`[data-mark="${engine}"]`).isVisible(), true)
        assert.equal(await run().locator('.tt-author-provider').innerText(), '', 'provider is a mark, without words')
        assert.equal(await run().locator('svg.mark').getAttribute('width'), '12')
        assert.equal(await run().locator('svg.mark').evaluate((node) => getComputedStyle(node).fill === getComputedStyle(node.closest('.tt-speaker')).color), true, 'monochrome line ink')
        assert.equal(await run().locator('.tt-author-meta').evaluate((node) => getComputedStyle(node).pointerEvents), 'auto', 'tooltip can be hovered/focused')
      }, async () => {
        await align(run(), -200); await page.locator('.author-stuck').waitFor()
        assert.equal(await run().locator(`[data-mark="${engine}"]`).isVisible(), true)
        assert.equal(await run().locator('.tt-author-model').isVisible(), width > 480)
        const bounds = await run().locator('.tt-author-pill').boundingBox()
        assert.ok(bounds.width < (width === 390 ? 180 : 240), 'sticky chip stays compact')
      })
    }
    await pair('provider-change', 'Same provider/model · one run', 'Changed provider/model · fresh runs', async () => {
      await stage([reply('same-1', 'claude-code', 'fable', 'The first set of notes is ready.'), reply('same-2', 'claude-code', 'fable', 'One more useful detail belongs with it.')])
      assert.equal(await page.locator('.tt-author-run').count(), 1)
    }, async () => {
      await stage([reply('changed-1', 'claude-code', 'fable', 'The first set of notes is ready.'), reply('changed-2', 'codex', 'sol', 'The next response used another provider.'), reply('changed-3', 'antigravity', 'preview', 'The last response has its own recorded model.')])
      assert.equal(await page.locator('.tt-author-run').count(), 3)
      assert.deepEqual(await page.locator('.tt-author-provider svg').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('data-mark'))), ['claude-code', 'codex', 'antigravity'])
      assert.equal(await page.locator('.tt-speaker').allTextContents().then((lines) => lines.some((line) => /Claude Code|Codex|Antigravity/.test(line))), false)
    })
    const footer = () => page.locator('[data-message-id="full-reply"] .tt-reply-footer')
    const stageFooter = async () => { await stage([recorded]); await footer().locator('.speakbtn').waitFor(); await align(footer(), width === 390 ? 380 : 550) }
    await pair('timestamps', 'Agent · time at the start', 'Your message and a short reply · time', async () => {
      await stageFooter()
      assert.equal(await footer().locator('time').innerText(), '1:47 PM')
      assert.match(await footer().locator('time').getAttribute('title'), /Tuesday, November 10, 2026/)
      assert.equal(await page.locator('.tt-message .turn').count(), 0, 'no Classic model labels below thread replies')
      const a = await footer().locator('time').boundingBox(), b = await footer().locator('.speakbtn').boundingBox()
      assert.ok(a.x + a.width <= b.x, 'time precedes the action icons')
      const row = await footer().boundingBox()
      assert.ok(Math.abs(a.x - row.x) < 1, 'time sits at the footer start')
      assert.ok(Math.abs(a.y + a.height / 2 - b.y - b.height / 2) < 2)
    }, async () => {
      await stage([
        { id: 'your-message', author: 'you', kind: 'message', text: 'Leave a little room for the next step.', at: '2026-11-03T17:25:00Z' },
        reply('short-reply', 'codex', 'sol', 'The small changes are ready.', { at: '2026-11-09T17:25:00Z' }),
      ])
      assert.equal(await page.locator('[data-message-id="your-message"] .tt-reply-footer time').innerText(), 'Nov 3, 9:25 AM')
      assert.equal(await page.locator('[data-message-id="your-message"] .say time').count(), 0, 'timestamp stays outside the human bubble/clamp')
      assert.equal(await page.locator('[data-message-id="short-reply"] .tt-reply-footer time').innerText(), 'Mon 9:25 AM')
      assert.equal(await page.locator('[data-message-id="short-reply"] .speakbtn').count(), 0, 'threshold does not suppress its time')
    })
    await pair('turn-details', 'Timestamp · details folded', 'Timestamp · recorded details available', stageFooter, async () => {
      await footer().locator('.tt-time-button').click()
      await footer().locator('.tt-turn-facts').waitFor()
      assert.match(await footer().locator('.tt-turn-facts').innerText(), /Claude Fable 5.1.*1200 in \/ 360 out.*1.4s.*\$0.0200 total/)
      await align(footer(), width === 390 ? 350 : 520)
    })
    await pair('fallbacks', 'No model · provider mark only', 'Unknown model/provider · honest fallbacks', async () => {
      await stage([reply('no-model', 'codex', undefined, 'An older reply with no recorded model.')])
      assert.equal(await page.locator('.tt-author-model').count(), 0)
      assert.equal(await page.locator('.tt-author-provider [data-mark="codex"]').count(), 1)
    }, async () => {
      await stage([reply('unknown', 'future', 'future-model-with-a-long-unlisted-identifier', 'An unlisted model keeps its recorded ID.')])
      assert.equal(await page.locator('.tt-author-provider .mark-letter').innerText(), 'F')
      assert.match(await page.locator('.tt-author-meta').getAttribute('title'), /Future Provider · future-model-with-a-long-unlisted-identifier/)
      assert.equal(await page.locator('.tt-author-model').evaluate((node) => node.scrollWidth > node.clientWidth), true, 'long fallback is clamped')
    })
    // Saved member preferences and historical reply identity must coexist.
    await pair('shared-settings', 'Historical reply · original provider/model and time', 'Saved thread model · the same settings sheet', stageFooter, async () => {
      await page.getByRole('button', { name: 'Providers & models', exact: true }).click()
      const settings = page.getByRole('dialog', { name: 'Providers & models', exact: true })
      await settings.getByRole('button', { name: 'Model for Sprout', exact: true }).click()
      await settings.getByRole('listbox', { name: 'Model for Sprout', exact: true }).locator('[role=option][data-value="opus"]').click()
      await settings.locator('button[aria-label="Model for Sprout"][data-value="opus"]:not(:disabled)').waitFor()
      assert.equal(await settings.getByRole('button', { name: 'Model for Sprout', exact: true }).getAttribute('data-value'), 'opus')
      assert.equal(await page.locator('.tt-author-model').innerText(), 'Fable 5.1', 'changing saved preferences never rewrites historical reply identity')
      assert.equal(await footer().locator('time').innerText(), '1:47 PM')
    })
    await page.getByRole('dialog', { name: 'Providers & models', exact: true }).getByRole('button', { name: 'Done', exact: true }).click()
    await page.getByRole('dialog', { name: 'Providers & models', exact: true }).waitFor({ state: 'detached' })
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Providers & models')
    assert.equal(await page.locator('.tt-composer .modelbtn-name').innerText(), 'Opus 5.5')
    await footer().locator('.tt-time-button').click()
    await footer().locator('.tt-turn-facts').waitFor()
    assert.match(await footer().locator('.tt-turn-facts').innerText(), /Claude Fable 5.1/)
    // One metadata change is enough to start a new run even on one provider.
    await stage([reply('model-1', 'claude-code', 'fable', 'First.'), reply('model-2', 'claude-code', 'opus', 'Second.')])
    assert.equal(await page.locator('.tt-author-run').count(), 2)
    await page.locator('.tt-author-meta').first().focus()
    assert.equal(await page.locator('.tt-author-meta').first().evaluate((node) => document.activeElement === node), true)
    // Attachments without prose still have a footer. Pending/streaming output
    // has none until the broker supplies the final message and its date.
    await stage([{ id: 'only-file', author: 'you', kind: 'message', text: '', at, attachments: [{ name: 'plan.md', path: 'attachments/plan.md' }] }])
    await page.locator('[data-message-id="only-file"] .tt-reply-footer time').waitFor()
    await stage([reply('streaming', 'codex', 'sol', 'A reply is still growing.', { streaming: true })])
    assert.equal(await page.locator('[data-message-id="streaming"] time').count(), 0)
    app.emit(id, { type: 'message.done', message: reply('streaming', 'codex', 'sol', 'The reply is ready.') })
    await page.locator('[data-message-id="streaming"] .tt-reply-footer time').waitFor()
    await stage([{ ...recorded, id: 'failed-reply', steps: [...recorded.steps.slice(0, -1), { type: 'end', event: { ...recorded.steps.at(-1).event, is_error: true, subtype: 'error_during_execution' } }] }])
    assert.match(await page.locator('.tt-time-button.err').getAttribute('aria-label'), /Failed turn/)
    await page.locator('.tt-time-button.err').click()
    assert.match(await page.locator('.tt-turn-facts').innerText(), /error_during_execution/)
    await stage([{ ...recorded, id: 'undated-reply', at: null }])
    assert.equal(await page.locator('.tt-time-button').innerText(), 'Details', 'an older undated record retains its facts without a fabricated time')
    assert.equal(await page.locator('.tt-message-time').count(), 0)
    assert.deepEqual(errors, [])
    results.push({ width, theme, allProviderMarks: true, monochrome: true, fullNamesOnTooltip: true, stickyMarksKept: true, stickyModelHidden: width === 390,
      groupingAndProviderChange: true, sameProviderModelChange: true, timestampFormats: true, shortReplyTime: true, humanTime: true, timestampAtEnd: true,
      sharedSavedSettingsAndHistoricalReply: true, turnDetailsPreserved: true, errorCuePreserved: true, undatedDetailsPreserved: true, unknownFallbackClamped: true, attachmentsOnlyTime: true, finalMessageTime: true, errors })
    console.log(`PASS ${width} ${theme}`)
  } finally { await context.close(); await app.close() }
}
await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
} finally { await browser.close() }
