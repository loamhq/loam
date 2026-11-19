// The owner, 2026-11-16 (t-153f9c43 m33): "if the final message actually gets
// posted after other messages, then it should be ordered that way." A real
// page and the event order the broker's thread stream gives (agents repo,
// tests/test_thread_completion_order.py): a reply working through a long tool
// call while two messages are posted, the first sent from the composer.
// Checks the drawn order at every stage, against a reload, with no
// duplicates, preserved view/streaming/audio DOM identity, completion race,
// parallel reverse completion, reconnect, and reading position.
// Run: LOAM_PLAYWRIGHT=<playwright index.mjs> THREAD_ORDER_EVIDENCE=<dir> node test/thread-completion-order-browser.mjs
import assert from 'node:assert/strict'
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { observeAudio } from './audio-probe.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'

const out = process.env.THREAD_ORDER_EVIDENCE
if (!out) throw new Error('Set THREAD_ORDER_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const wav = Buffer.alloc(44 + 24000 * 2 * 30)
wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16)
wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(24000, 24); wav.writeUInt32LE(48000, 28)
wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(wav.length - 44, 40)
const id = 'a-quieter-week', results = [], captures = []
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = [], clients = new Set()
    const at = (s) => new Date(Date.now() - 60000 + s * 1000).toISOString()
    let members = [
      { persona: 'sprout', engine: 'claude-code', model: null, running: true, busy: true, working: true, background: 0, background_tasks: [] },
      { persona: 'quill', engine: 'codex', model: null, running: false, busy: false, working: false, background: 0, background_tasks: [] },
    ]
    let turns = [{ persona: 'sprout', engine: 'claude-code', model: null, started_at: at(1) }]
    const earlier = Array.from({ length: 19 }, (_, i) => ({ id: `m${20 + i}`, author: i % 2 ? 'sprout' : 'you', kind: 'message', engine: 'claude-code', at: at(-600 + i), text: `Earlier message ${i + 1}. `.repeat(6).trim() }))
    const audioText = 'Keep room for the earlier notes before checking the slow suite. '.repeat(24).trim()
    const messages = [...earlier, { id: 'm39', author: 'sprout', kind: 'message', engine: 'claude-code', at: at(-5), text: audioText }, { id: 'm40', author: 'you', kind: 'message', at: at(0), text: '@sprout Run the long check, then tell me what it found.' }]
    const original = await app.f.api('GET', `/threads/${id}`)
    const summary = () => ({ ...original.thread, title: 'The long check', default: 'sprout', members, unread: 0, working: members.filter((m) => m.working).length, background: 0 })
    const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    const emit = (e) => { for (const res of clients) res.write(`data: ${JSON.stringify(e)}\n\n`) }
    const plugins = await shippedPlugins(path.resolve('public'))
    for (const p of plugins) if (p.id === 'read-aloud') p.on = true
    app.routes['GET /v1/plugins'] = (req, res) => json(res, { plugins })
    for (const file of await readdir('../plugins/_listed/read-aloud/page')) {
      app.routes[`GET /v1/plugins/read-aloud/files/page/${file}`] = async (req, res) => {
        res.writeHead(200, { 'content-type': file.endsWith('.css') ? 'text/css' : 'text/javascript' })
        res.end(await readFile(`../plugins/_listed/read-aloud/page/${file}`))
      }
    }
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: [summary()], agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`data: ${JSON.stringify({ type: 'threads.snapshot', threads: [summary()], agents: app.f.agents })}\n\n`) }
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...original, thread: summary(), messages, has_more: false, turns, turn: turns[0] ?? null })
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); res.write(`data: ${JSON.stringify({ type: 'thread.snapshot', thread: id, members, turns, turn: turns[0] ?? null, pending: [] })}\n\n`); clients.add(res); res.on('close', () => clients.delete(res)) }
    let release, postMode = 'normal', nextPostId = 'm41'
    app.routes[`POST /v1/threads/${id}/messages`] = async (req, res, url, raw) => {
      const body = JSON.parse(raw), message = { id: nextPostId, author: 'you', kind: 'message', at: at(20), text: body.text }
      if (postMode === 'sse-first') {
        messages.push(message); emit({ type: 'message.done', message })
        await new Promise((r) => { release = r })
        return json(res, { accepted: true, message })
      }
      await new Promise((r) => { release = r })   // held: the page shows it as being sent
      messages.push(message); emit({ type: 'message.done', message }); emit({ type: 'turn.steered', persona: 'sprout', messages: [nextPostId] })
      json(res, { accepted: true, message })
    }
    app.routes[`GET /v1/threads/${id}/steps`] = (req, res) => { res.writeHead(404, { 'content-type': 'application/json' }); res.end('{"error":"No full record for this reply"}') }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, reducedMotion: 'reduce', serviceWorkers: 'block', isMobile: width === 390, hasTouch: width === 390 })
    await observeAudio(context)
    await context.route('**/api/health', (route) => route.fulfill({ json: { configured: true, ok: true, speech: true } }))
    let speechMade = false
    const readySpeech = { id: 'fixture-audio', status: 'ready', duration_s: 30, url: '/api/speech/fixture-audio/audio.mp3' }
    await context.route(/\/api\/speech(?:\/.*)?$/, (route) => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname.endsWith('/audio.mp3')) return route.fulfill({ contentType: 'audio/wav', body: wav })
      if (pathname.endsWith('/lookup')) return route.fulfill({ json: speechMade ? readySpeech : { status: 'none' } })
      speechMade = true
      return route.fulfill({ status: 202, json: readySpeech })
    })
    await context.addInitScript(() => {
      globalThis.__players = []
      const NativeAudio = Audio
      globalThis.Audio = class extends NativeAudio { constructor() { super(); __players.push(this) } }
    })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__order = { threads };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', (e) => errors.push(e.message))
    const settle = () => page.evaluate(async () => { await document.fonts.ready; for (let i = 0; i < 4; i++) await new Promise(requestAnimationFrame) })
    const order = () => page.locator('.tt-message').evaluateAll((nodes) => nodes.map((n) => n.dataset.messageId).filter((m) => !/^m(2\d|3[0-8])$/.test(m)))
    const atEnd = () => page.evaluate(() => {
      let node = document.querySelector('.tt-message:last-of-type')
      while (node && !(node.scrollHeight > node.clientHeight + 1 && /auto|scroll/.test(getComputedStyle(node).overflowY))) node = node.parentElement
      node ??= document.scrollingElement
      return node.scrollHeight - node.scrollTop - node.clientHeight < 4
    })
    const shot = async (stage) => {
      await settle()
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${stage}: no horizontal overflow`)
      const file = `${stage}-${width}-${theme}.png`, dest = path.join(out, file)
      await page.screenshot({ path: dest, animations: 'disabled' })
      captures.push({ file, stage, width, theme, sha256: createHash('sha256').update(await readFile(dest)).digest('hex') })
    }
    const stages = {}
    const check = async (stage, want) => {
      await settle(); const got = await order(); stages[stage] = got; console.error(width, theme, stage, got.join(' '))
      assert.deepEqual(got, want, `${stage}: drawn order`)
      assert.equal(new Set(got).size, got.length, `${stage}: no duplicates`)
      assert.equal(await atEnd(), true, `${stage}: the reader stays at the end`)
      await shot(stage)
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').waitFor(); await page.waitForFunction(() => globalThis.__order)
      await page.evaluate((id) => __order.threads.open(id), id)
      await page.locator('[data-message-id="draft:sprout"]').waitFor()
      emit({ type: 'message.delta', persona: 'sprout', draft: 'Starting the long check now.' })
      emit({ type: 'turn.step', persona: 'sprout', label: 'Bash: ./check --long', steps: 1 })
      await page.locator('[data-message-id="draft:sprout"]').getByText('Starting the long check now.').waitFor()
      await page.locator('[data-message-id="draft:sprout"] .fold-head').click(); await settle()
      // Start read-aloud on m39 before mid-work sends arrive to verify audio & DOM identity.
      const speakBtn = page.locator('[data-message-id="m39"] .speakbtn')
      await speakBtn.waitFor()
      if (width === 390) await speakBtn.tap(); else await speakBtn.click()
      await page.locator('.speakmenu').getByText('Listen').click()
      await page.locator('[data-message-id="m39"] .player-play').waitFor()
      if (width === 390) await page.locator('[data-message-id="m39"] .player-play').tap(); else await page.locator('[data-message-id="m39"] .player-play').click()
      await page.waitForFunction(() => __players.length === 1 && !__players[0].paused)
      await page.evaluate(() => {
        globalThis.__replyNode = document.querySelector('[data-message-id="draft:sprout"]')
        globalThis.__foldNode = __replyNode.querySelector('.fold')
        globalThis.__playerEl = document.querySelector('[data-message-id="m39"] .player')
        globalThis.__audioNode = __players[0]
      })
      await check('1-working', ['m39', 'm40', 'draft:sprout'])
      await page.locator('#draft').fill('Also include the slow cases.')
      await page.evaluate(() => { __order.threads.send() })
      await page.locator('[data-message-id="pending-send"]').waitFor()
      await check('2-sending', ['m39', 'm40', 'pending-send', 'draft:sprout'])
      release(); await page.locator('[data-message-id="m41"]').waitFor()
      await check('3-sent', ['m39', 'm40', 'm41', 'draft:sprout'])
      const second = { id: 'm42', author: 'you', kind: 'message', at: at(25), text: 'And keep the summary short.' }
      messages.push(second); emit({ type: 'message.done', message: second }); emit({ type: 'turn.steered', persona: 'sprout', messages: ['m42'] })
      await page.locator('[data-message-id="m42"]').waitFor()
      emit({ type: 'message.delta', persona: 'sprout', draft: 'The long check finished: the slow cases pass too.' })
      await page.locator('[data-message-id="draft:sprout"]').getByText('The long check finished').waitFor()
      await check('4-second-message', ['m39', 'm40', 'm41', 'm42', 'draft:sprout'])
      const reply = { id: 'm43', author: 'sprout', kind: 'message', engine: 'claude-code', at: at(40), text: 'The long check finished: the slow cases pass too.', steered: ['m41', 'm42'],
        segments: [{ text: 'Starting the long check now.' }, { did: [{ label: 'Bash: ./check --long' }] }] }
      messages.push(reply); members = members.map((m) => ({ ...m, working: false, busy: false })); turns = []
      emit({ type: 'message.done', message: reply }); emit({ type: 'turn.finished', persona: 'sprout' }); emit({ type: 'members', members })
      await page.locator('[data-message-id="m43"]').waitFor(); await page.locator('[data-message-id="draft:sprout"]').waitFor({ state: 'detached' })
      // Assert view, streaming, fold disclosure and audio DOM identity survived.
      const identity = await page.evaluate(() => ({
        sameReplyNode: globalThis.__replyNode === document.querySelector('[data-message-id="m43"]'),
        sameFoldNode: globalThis.__foldNode === document.querySelector('[data-message-id="m43"] .fold'),
        foldExpanded: document.querySelector('[data-message-id="m43"] .fold-head')?.getAttribute('aria-expanded'),
        samePlayerEl: globalThis.__playerEl === document.querySelector('[data-message-id="m39"] .player'),
        sameAudio: globalThis.__audioNode === __players[0] && !__players[0].paused,
      }))
      assert.deepEqual(identity, { sameReplyNode: true, sameFoldNode: true, foldExpanded: 'true', samePlayerEl: true, sameAudio: true }, 'DOM and audio identity survive')
      await check('5-final', ['m39', 'm40', 'm41', 'm42', 'm43'])
      await page.evaluate((id) => { __order.threads.close(); __order.threads.open(id) }, id)
      await page.locator('[data-message-id="m43"]').waitFor()
      await check('6-reload', ['m39', 'm40', 'm41', 'm42', 'm43'])

      // Completion race: SSE message.done arrives before POST /messages resolves.
      postMode = 'sse-first'; nextPostId = 'm44'
      members = members.map((m) => m.persona === 'sprout' ? { ...m, working: true, busy: true } : m)
      turns = [{ persona: 'sprout', engine: 'claude-code', model: null, started_at: at(50) }]
      emit({ type: 'turn.started', ...turns[0] }); emit({ type: 'members', members })
      await page.locator('[data-message-id="draft:sprout"]').waitFor()
      await page.locator('#draft').fill('Race check message.')
      await page.evaluate(() => { __order.threads.send() })
      await page.locator('[data-message-id="m44"]').waitFor()
      assert.equal(await page.locator('[data-message-id="pending-send"]').count(), 0, 'SSE arrival clears pending-send immediately')
      release(); await settle()
      assert.deepEqual(await order(), ['m39', 'm40', 'm41', 'm42', 'm43', 'm44', 'draft:sprout'])

      // Reconnect mid-work: snapshot + replayed m44 + offline m45 while draft:sprout holds prose.
      emit({ type: 'message.delta', persona: 'sprout', draft: 'Mid-work prose before reconnect.' })
      await page.locator('[data-message-id="draft:sprout"]').getByText('Mid-work prose before reconnect.').waitFor()
      const reconnectReply = await page.evaluate(() => { globalThis.__reconnectNode = document.querySelector('[data-message-id="draft:sprout"]'); return Boolean(globalThis.__reconnectNode) })
      assert.equal(reconnectReply, true)
      const offline = { id: 'm45', author: 'you', kind: 'message', at: at(55), text: 'Sent while disconnected.' }
      messages.push(offline)
      emit({ type: 'thread.snapshot', thread: id, members, turns, turn: turns[0], pending: [] })
      emit({ type: 'message.done', message: messages.find((m) => m.id === 'm44') })
      emit({ type: 'message.done', message: offline })
      await page.locator('[data-message-id="m45"]').waitFor(); await settle()
      assert.deepEqual(await order(), ['m39', 'm40', 'm41', 'm42', 'm43', 'm44', 'm45', 'draft:sprout'])
      assert.equal(await page.evaluate(() => globalThis.__reconnectNode === document.querySelector('[data-message-id="draft:sprout"]')), true)
      await page.locator('[data-message-id="draft:sprout"]').getByText('Mid-work prose before reconnect.').waitFor()

      // Parallel reverse completion with an existing live draft above a newly started turn.
      members = members.map((m) => ({ ...m, working: true, busy: true }))
      turns = [...turns, { persona: 'quill', engine: 'codex', model: null, started_at: at(56) }]
      emit({ type: 'turn.started', ...turns[1] }); emit({ type: 'members', members })
      emit({ type: 'message.delta', persona: 'quill', draft: 'Quill drafting.' })
      await page.locator('[data-message-id="draft:quill"]').getByText('Quill drafting.').waitFor()
      assert.deepEqual(await order(), ['m39', 'm40', 'm41', 'm42', 'm43', 'm44', 'm45', 'draft:sprout', 'draft:quill'])
      const quillDone = { id: 'm46', author: 'quill', kind: 'message', engine: 'codex', at: at(58), text: 'Quill settled first.' }
      messages.push(quillDone); emit({ type: 'message.done', message: quillDone }); emit({ type: 'turn.finished', persona: 'quill' })
      await page.locator('[data-message-id="m46"]').waitFor(); await settle()
      assert.deepEqual(await order(), ['m39', 'm40', 'm41', 'm42', 'm43', 'm44', 'm45', 'm46', 'draft:sprout'])

      // User reading position: scroll up to m25 while m47 settles below; viewport position must stay put.
      const beforeScroll = await page.evaluate(() => {
        const pane = document.querySelector('.tt-stream'), target = document.querySelector('[data-message-id="m25"]')
        target.scrollIntoView({ block: 'center' })
        pane.dispatchEvent(new Event('scroll'))
        return { top: target.getBoundingClientRect().top, atEnd: __order.threads.st.open.atEnd }
      })
      assert.equal(beforeScroll.atEnd, false, 'scrolled up leaves atEnd false')
      await settle()
      const settledDuringScroll = { id: 'm47', author: 'sprout', kind: 'message', engine: 'claude-code', at: at(60), text: 'Sprout finished while reading earlier.' }
      messages.push(settledDuringScroll); members = members.map((m) => ({ ...m, working: false, busy: false })); turns = []
      emit({ type: 'message.done', message: settledDuringScroll }); emit({ type: 'turn.finished', persona: 'sprout' }); emit({ type: 'members', members })
      await page.locator('[data-message-id="m47"]').waitFor(); await settle()
      const afterScroll = await page.evaluate(() => ({
        top: document.querySelector('[data-message-id="m25"]').getBoundingClientRect().top,
        atEnd: __order.threads.st.open.atEnd,
      }))
      assert.equal(afterScroll.atEnd, false, 'settling below does not jump reader to end')
      assert.ok(Math.abs(afterScroll.top - beforeScroll.top) < 2, `reading anchor drifted ${afterScroll.top - beforeScroll.top}px`)

      assert.deepEqual(errors, [])
      results.push({ width, theme, ok: true, stages, identity, readingDriftPx: Math.abs(afterScroll.top - beforeScroll.top) })
    } catch (error) {
      results.push({ width, theme, ok: false, error: error.message.split('\n')[0], stages })
    } finally { release?.(); console.error(width, theme, JSON.stringify(results.at(-1))); await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
  await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
} finally { await browser.close() }
console.log(JSON.stringify(results, null, 1))
process.exit(results.every((r) => r.ok) ? 0 : 1)
