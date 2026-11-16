// Real page and relay, synthetic broker records. No agent turns are launched.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_PRESENCE_EVIDENCE
if (!out) throw new Error('Set THREAD_PRESENCE_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const sharp = (await import(process.env.LOAM_SHARP || 'sharp')).default
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const results = [], captures = [], id = 'a-quieter-week', classicId = `classic-${id}`
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = [], listClients = new Set(), sent = []
    const at = new Date().toISOString()
    let members = [
      { persona: 'sprout', engine: 'claude-code', model: null, busy: true, working: false, background: 2, background_tasks: [
        { id: 'notes', description: 'Compare the notes', started_at: new Date(Date.now() - 120000).toISOString() },
        { id: 'details', description: 'Check the remaining details', started_at: new Date(Date.now() - 60000).toISOString() },
      ] },
      { persona: 'quill', engine: 'codex', model: null, busy: false, working: false, background: 0 },
    ]
    const messages = [
      { id: 'm41', author: 'you', kind: 'message', at, text: 'Keep the next step small, and let me know what is still running.' },
      { id: 'm42', author: 'sprout', kind: 'message', engine: 'claude-code', model: 'claude-fable-5-1', at, text: 'The plan is ready. Two helpers are checking the remaining details; I’ll bring their findings back here.', did: [] },
    ]
    app.f.providers[0].models.push({ id: 'claude-fable-5-1', name: 'Fable 5.1' })
    app.f.providers[1].models = [{ id: 'gpt-6.1-sol', name: 'GPT-6.1-Sol' }]
    const rows = (await app.f.api('GET', '/threads')).threads
    const summary = () => ({ ...rows.find((t) => t.id === id), members, background: members.reduce((n, m) => n + m.background, 0), working: members.filter((m) => m.working).length })
    const listing = () => rows.map((t) => t.id === id ? summary() : t)
    const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: listing(), agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n')
      res.write(`data: ${JSON.stringify({ type: 'threads.snapshot', threads: listing(), agents: app.f.agents })}\n\n`)
      listClients.add(res); res.on('close', () => listClients.delete(res))
    }
    app.routes[`GET /v1/threads/${id}`] = async (req, res) => json(res, { ...(await app.f.api('GET', `/threads/${id}`)), thread: summary(), messages, turn: null })
    app.routes[`GET /v1/threads/${id}/steps`] = (req, res) => { res.writeHead(404, { 'content-type': 'application/json' }); res.end('{"error":"No older full record"}') }
    app.routes[`POST /v1/threads/${id}/messages`] = (req, res, url, raw) => {
      const body = JSON.parse(raw); sent.push(body)
      const message = { id: `m${43 + sent.length}`, author: 'you', kind: 'message', text: body.text, at }
      messages.push(message); app.emit(id, { type: 'message.done', message }); json(res, { accepted: true, message })
    }
    const classicRecord = app.records.find((r) => r.session_id === classicId)
    Object.assign(classicRecord, { status: 'waiting', background_tasks: 2, last_event_at: at, last_activity: at })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, reducedMotion: 'reduce', isMobile: width === 390, hasTouch: width === 390 })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__presence = { threads, state, schedule, goChats };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', (e) => errors.push(e.message))
    const settle = async () => page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
    const screenshot = async () => { await page.mouse.move(0, 0); await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); return page.screenshot({ animations: 'disabled' }) }
    const pair = async (name, leftLabel, rightLabel, left) => { await left(); await settle(); const file = name + '-' + width + '-' + theme + '.png'; const dest = path.join(out, file); await page.screenshot({ path: dest, animations: 'disabled' }); captures.push({ file, width, theme, sha256: createHash('sha256').update(await readFile(dest)).digest('hex') }); console.log(file) }
    const thread = async () => { await page.evaluate((id) => __presence.threads.open(id), id); await page.locator('[data-message-id="m42"]').waitFor(); await page.locator('.tt-tasks-button').waitFor(); await settle() }
      const threadList = async () => { await page.evaluate(() => { __presence.threads.close(); __presence.goChats() }); await page.locator('.tt-row').first().waitFor(); await settle() }
    const emitMembers = async (next) => {
      members = next
      app.emit(id, { type: 'members', thread: id, members, background: summary().background, working: summary().working })
      const e = { type: 'thread.changed', thread: id, summary: summary() }
      for (const res of listClients) res.write(`data: ${JSON.stringify(e)}\n\n`)
      await settle()
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      const row = () => page.locator('.tt-row').filter({ hasText: 'A little room for the week' })
      assert.equal(await row().locator('.srow-time .srow-bg').count(), 0)
      await row().locator('.tt-thread-icon[data-icon-state=rest]').waitFor()
      assert.equal(await row().locator('.tt-icon-layer').count(), 0)
      assert.equal(await row().locator('.beating').count(), 0, 'parked member has no turn dot')
      await pair('list', 'Threads · quiet waiting state', 'Classic · existing background count', threadList)
      assert.equal(await page.locator('.srow').filter({ hasText: 'A little room for the week' }).locator('.srow-bg').innerText(), '2')
      await pair('background', 'Threads · background work', 'Classic · background work', thread)
      await thread()
      await page.getByRole('button', { name: '2 background tasks', exact: true }).waitFor()
      assert.equal(await page.locator('.tt-member-activity, .tt-live-area').count(), 0)
      await pair('waiting-fold', 'Background · header count', 'Background · all named tasks', async () => {})
      await page.locator('.tt-tasks-panel').press('Escape')
      await thread()
      // Natural labels are recorded per reply; sticky only keeps the name.
      await page.evaluate(() => {
        const o = __presence.threads.st.open; o.messages[1].text += '\n\n' + Array.from({ length: 24 }, () => 'A useful plan leaves room for what comes next. The important details stay close to the conversation, and each small decision has a clear place to land.').join('\n\n')
        o.atEnd = false; o.restoreTop = 0; __presence.schedule()
      })
      await settle()
      const marker = () => page.locator('.tt-author-run').first()
      const align = async (offset) => { await marker().evaluate((node, offset) => { const pane = node.closest('.msgs'); pane.scrollTop += node.getBoundingClientRect().top - pane.getBoundingClientRect().top - offset }, offset); await settle() }
      await pair('author', 'Reply · provider and model', 'Sticky · compact author', async () => {
        await align(90); assert.equal(await marker().locator('.tt-author-model').innerText(), 'Fable 5.1')
        assert.equal(await marker().locator('.tt-author-provider [data-mark="claude-code"]').isVisible(), true, 'provider mark remains at either width')
      })
      await thread()
      await pair('details', 'Member · waiting on two', 'Member · generating with two', async () => {
        await page.getByTitle('Thread details', { exact: true }).click(); await page.locator('.tt-member').first().waitFor()
        await page.locator('#tt-members').scrollIntoViewIfNeeded()
        assert.equal(await page.locator('.tt-member').first().locator('.srow-bg').innerText(), '2')
        assert.equal(await page.locator('.tt-member').first().locator('.dot.run-live').count(), 0)
      })
      // Members really replace: remove one, clear another, then restore solely
      // through a snapshot (as a reconnect does). No joins or turns required.
      await emitMembers([{ ...members[0], background: 0, working: false, busy: false }])
      await page.waitForFunction(() => __presence.threads.st.open.thread.members.length === 1)
      assert.equal(await page.locator('.tt-member .srow-bg').count(), 0)
      members = [{ ...members[0], background: 2, working: false, busy: true }, { persona: 'quill', engine: 'codex', working: false, background: 0 }]
      app.emit(id, { type: 'thread.snapshot', thread: id, turn: null, members, pending: [] })
      await page.waitForFunction(() => __presence.threads.st.open.thread.members.length === 2)
      await page.getByRole('button', { name: 'Chat', exact: true }).click()
      await page.getByRole('button', { name: '2 background tasks', exact: true }).waitFor()
      const draft = page.locator('#draft'), list = page.locator('.tt-mentions')
      await pair('everyone', 'Mention · Everyone first', 'Sent · all and everyone highlighted', async () => {
        await draft.fill(''); await page.getByRole('button', { name: 'Mention an agent', exact: true }).click()
        assert.match(await list.getByRole('option').first().innerText(), /^Everyone\neveryone in this thread/)
        assert.equal(await list.getByRole('option').first().locator('svg').count() > 0, true)
      })
      for (const query of ['all', 'EVERYONE']) {
        await settle()
        await draft.fill(''); await draft.pressSequentially(`@${query}`)
        await page.waitForFunction((query) => __presence.threads.st.mention?.query === query, query).catch(async (error) => {
          console.log('Mention diagnostic', await page.evaluate(() => ({ value: document.querySelector('#draft').value, html: document.querySelector('#draft').innerHTML, mention: __presence.threads.st.mention, focused: document.activeElement?.id, screen: __presence.state.screen })))
          throw error
        })
        await list.getByRole('option').filter({ hasText: 'Everyone' }).waitFor()
        await draft.press('Enter'); await page.waitForFunction(() => document.querySelector('#draft').value === '@all ')
        await settle()
      }
      await draft.fill(''); await draft.pressSequentially('@qui'); await draft.press('Enter')
      await page.waitForFunction(() => document.querySelector('#draft').value === '@quill ')
      // Same persona on a new provider/model gets a fresh accurate author run.
      app.emit(id, { type: 'message.done', message: { id: 'm50', author: 'sprout', kind: 'message', engine: 'codex', model: 'gpt-6.1-sol', text: 'A second response on another provider.' } })
      app.emit(id, { type: 'message.done', message: { id: 'm51', author: 'sprout', kind: 'message', engine: 'codex', model: 'future-model', text: 'A response on an unlisted model.' } })
      await page.locator('[data-message-id="m51"]').waitFor()
      assert.equal(await page.locator('.tt-author-run[data-author="sprout"]').count(), 3)
      assert.match(await page.locator('.tt-author-run').nth(1).locator('.tt-speaker').innerText(), /GPT-6.1/)
      assert.match(await page.locator('.tt-author-run').nth(2).locator('.tt-speaker').innerText(), /future-model/)
      assert.deepEqual(errors, [])
      results.push({ width, theme, memberReplacement: true, snapshotRecovery: true, rowCount: true, backgroundVersusWorking: true, providerModelNames: true, compactSticky: true, stickyModelHidden: width === 390, providerMarkVisible: true, everyoneInsertFilterAndSend: true, changedReplyIdentity: true, errors })
      console.log(`PASS ${width} ${theme}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
  await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
} finally { await browser.close() }
