// Real page and relay, synthetic broker records. Evidence stays outside the repo.
import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { mkdir, writeFile } from 'node:fs/promises'
import { fakeBroker, appAgainst, closing } from './harness.js'
import { row, rootsAnswer, listAnswer } from './fixtures/files-contract.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright-core')
const output = process.env.AGENT_PAGE_CHATS_EVIDENCE
if (!output) throw new Error('Set AGENT_PAGE_CHATS_EVIDENCE to a directory outside the repository')
const relative = path.relative(fileURLToPath(new URL('..', import.meta.url)), path.resolve(output))
if (!relative || (!relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative))) throw new Error('Evidence must be outside the repository')
await mkdir(output, { recursive: true })
const now = new Date().toISOString()
const records = [
  ['done', 'A plan ready to review', 'The plan is ready. The checks passed.', true],
  ['working', 'Review the implementation', 'I’m reviewing the implementation and running the checks.', false],
  ['waiting', 'Wait for the checks', 'Two checks are running in the background.', false],
  ['needs_input', 'Approve the next step', 'Can I run the final check?', false],
  ['failed', 'Retry the failed command', 'The command failed. Ready to retry.', false],
].map(([status, title, last_message, pinned]) => ({
  session_id: `fixture-${status}`, agent: 'test-agent', provider: 'claude-code',
  created: now, last_activity: now, last_event_at: now, state: 'exited', live: false,
  status, title, last_message, pinned, archived: false, record_seq: 3, seen_seq: 0,
  background_tasks: status === 'waiting' ? 2 : 0, turns: [], pending_permissions: [],
}))
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const frame = (event) => `data: ${JSON.stringify(event)}\n\n`
const refusal = 'The session cap is full. End a session before starting another.'
const routes = {
  'GET /v1/agents': (req, res) => json(res, { agents: [{ id: 'test-agent', name: 'Cinder', emoji: null }] }),
  'GET /v1/providers': (req, res) => json(res, { providers: [{ id: 'claude-code', name: 'Claude Code', connected: true, usage: null }] }),
  'GET /v1/limits': (req, res) => json(res, { attachment_max_bytes: 10000000, attachment_types: [] }),
  'GET /v1/sessions': (req, res) => json(res, { sessions: records, live_count: 0, cap: 8 }),
  'GET /v1/sessions/events': (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(': open ping=15000\n\n')
    for (const record of records) res.write(frame({ type: 'broker.session', ...record }))
  },
  'POST /v1/sessions': (req, res) => json(res, { error: refusal }, 429),
  'GET /v1/files/roots': (req, res) => json(res, rootsAnswer()),
  'GET /v1/files/stat': (req, res, url) => json(res, row(url.searchParams.get('root'), url.searchParams.get('path'))),
  'GET /v1/files/list': (req, res, url) => json(res, listAnswer(row(url.searchParams.get('root'), url.searchParams.get('path')), [])),
}
for (const record of records) {
  routes[`GET /v1/sessions/${record.session_id}/events`] = (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(': open ping=15000\n\n')
    res.write(frame({ type: 'broker.session', ...record, window_first_seq: 1, turns_before: 0 }))
    res.write(frame({ type: 'broker.user_message', seq: 1, text: 'Please review the plan.' }))
    res.write(frame({ type: 'assistant', seq: 2, message: { id: record.session_id, content: [{ type: 'text', text: record.last_message }] } }))
    res.end(frame({ type: 'result', seq: 3, is_error: false, usage: null, total_cost_usd: null }))
  }
  // Leave seen marks unchanged so both lists retain the same unread fixture.
  routes[`PATCH /v1/sessions/${record.session_id}`] = (req, res) => json(res, record)
}
const broker = await fakeBroker(routes)
const app = await appAgainst(broker)
let browser
const results = []
try {
  browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width, height: 1100 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    const page = await context.newPage()
    page.setDefaultTimeout(10000)
    const errors = []
    page.on('pageerror', (e) => errors.push(e.message))
    await page.goto(app.url)
    const main = page.locator(width === 390 ? '.home .scroll' : '.listcol')
    const profile = page.locator('.profile-chats')
    const chat = (scope, status) => scope.locator('.srow').filter({ has: page.locator('.srow-title', { hasText: records.find((r) => r.status === status).title }) })
    const paint = async (name) => {
      await page.mouse.move(width - 1, 1)
      await page.evaluate(() => document.fonts.ready)
      await page.waitForTimeout(220)
      await page.screenshot({ path: path.join(output, `${name}-${width}-${theme}.png`) })
    }
    const paintPhoneChats = async (name) => {
      if (width !== 390) return
      await page.locator('.profile-body').evaluate((el) => { el.scrollTop = el.scrollHeight })
      await paint(name)
      await page.locator('.profile-body').evaluate((el) => { el.scrollTop = 0 })
    }
    const summary = (scope) => scope.locator('.srow').evaluateAll((rows) => rows.map((el) => ({
      title: el.querySelector('.srow-title').textContent,
      classes: el.className,
      // Exclude only the measured selection-height inline value.
      content: el.innerHTML,
      fill: getComputedStyle(el).backgroundColor,
      marker: getComputedStyle(el, '::before').backgroundColor,
      pinned: Boolean(el.closest('.pinned-card')),
      bubbles: el.querySelectorAll('.box').length,
    })))
    const enterProfile = async () => {
      await main.locator('.persona').first().dispatchEvent('contextmenu')
      await profile.waitFor()
    }
    await main.locator('.srow').first().waitFor()
    const initial = await summary(main)
    await paint('main-default')
    await enterProfile()
    assert.deepEqual(await summary(profile), initial, 'profile rows differ from main rows')
    assert.equal(await page.locator('.profile-hero .profile-workspace').count(), 1)
    assert.equal(await profile.getByText('Open workspace').count(), 0)
    assert.equal(await profile.locator('.pinned-card .srow').count(), 1)
    await paint('profile-default')
    await paintPhoneChats('profile-chats-default')
    await page.getByRole('button', { name: 'Settings', exact: true }).first().click()
    await page.locator(width === 390 ? '.setrow' : '.navrow').filter({ hasText: 'Chats' }).first().click()
    const toggle = page.getByRole('switch', { name: 'Working bubble', exact: true })
    await toggle.click()
    await page.waitForFunction(() => document.querySelector('[aria-label="Working bubble"]')?.getAttribute('aria-checked') === 'false')
    assert.equal(await toggle.getAttribute('aria-checked'), 'false')
    await paint('settings-working-off')
    await page.keyboard.press('Escape')
    await page.locator('.chat-bubbles').waitFor({ state: 'detached' })
    assert.equal(await chat(profile, 'working').locator('.box').count(), 0)
    assert.equal(await chat(profile, 'working').locator('.dot').count(), 1)
    await paint('profile-working-off')
    await paintPhoneChats('profile-chats-working-off')
    const hidden = await summary(profile)
    if (width === 1440) assert.deepEqual(hidden, await summary(main))
    else {
      await page.locator('.profile-head .backbtn').click()
      await main.waitFor()
      assert.deepEqual(await summary(main), hidden)
      await paint('main-working-off')
      await enterProfile()
    }
    // The actual shared click opens the same thread, including a refused send.
    await chat(profile, 'done').click()
    await page.locator('#draft').fill('Please review the next step.')
    await page.locator('.roundsend').click()
    await page.locator('.unsent').waitFor()
    assert.ok((await page.locator('.unsent').textContent()).includes(refusal))
    assert.equal(await page.locator('.awaiting').count(), 0)
    await page.locator(width === 390 ? '.thread-head .who' : '.agentbtn').click()
    await profile.waitFor()
    if (width === 1440) {
      assert.equal(await chat(profile, 'done').getAttribute('class'), 'srow on')
      assert.deepEqual(await summary(profile), await summary(main))
      await paint('profile-selected-unsent')
      await page.getByRole('button', { name: 'Hide sidebar for a focused view', exact: true }).click()
      await page.locator('.desk.collapsed').waitFor()
      await chat(profile, 'working').click({ modifiers: ['Control'] })
      await page.waitForFunction(() => !document.querySelector('.desk')?.classList.contains('collapsed'))
    } else {
      await chat(profile, 'working').dispatchEvent('pointerdown', { pointerType: 'touch', clientX: 40, clientY: 400 })
      await page.getByText('1 chat selected', { exact: true }).waitFor()
      await chat(profile, 'working').dispatchEvent('pointerup', { pointerType: 'touch' })
    }
    await page.getByRole('toolbar', { name: 'Selected chats' }).waitFor()
    assert.equal(await chat(profile, 'working').getAttribute('aria-pressed'), 'true')
    await page.keyboard.press('Escape')
    await page.locator('.selcount').waitFor({ state: 'detached' })
    await chat(profile, 'done').click()
    await page.locator('.unsent').waitFor()
    assert.equal(await page.locator('.say.user').filter({ hasText: 'Please review the next step.' }).count(), 1)
    assert.equal(await page.locator('.awaiting').count(), 0)
    await paint('thread-unsent-preserved')
    await page.locator(width === 390 ? '.thread-head .who' : '.agentbtn').click()
    await profile.waitFor()
    const workspace = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/files/list' && new URL(response.url()).searchParams.get('path') === 'test-agent')
    await page.locator('.profile-workspace').click()
    await page.waitForURL('**/files/spaces/test-agent')
    await workspace
    assert.ok(broker.seen.some((req) => req.path === '/v1/files/list' && new URLSearchParams(req.search).get('path') === 'test-agent'))
    assert.deepEqual(errors, [])
    results.push({ width, theme, identicalRows: true, pinned: true, workingOff: true, liveDotRetained: true, selected: width === 1440, selectionToolbar: true, unsentPreserved: true, workspace: '/files/spaces/test-agent', errors })
    await context.close()
  }
  await writeFile(path.join(output, 'results.json'), JSON.stringify({ browser: browser.version(), fixture: 'synthetic records; no live agent sessions', results }, null, 2) + '\n')
  console.log('PASS: 390/1440 light/dark; identical profile/main rows, Settings Working off, pinned/selected states, selection toolbar, refused message preserved, workspace destination; no page errors.')
} finally {
  await browser?.close()
  await closing(app, broker)()
}
