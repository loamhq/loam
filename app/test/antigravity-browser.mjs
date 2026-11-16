import { providerFixture } from './fixtures/provider-sign-in.js'
// Actual page, CSS and relay; synthetic runtime. No host sessions or login.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { fakeBroker, appAgainst, closing } from './harness.js'
import { createThreadFixture } from '../public/threads-fixture.js'

async function chooseThreadOption(page, label, value) {
  await page.locator('.tt-details').getByRole('button', { name: label, exact: true }).click()
  const sheet = page.getByRole('dialog', { name: 'Providers & models', exact: true })
  await sheet.getByRole('listbox', { name: label, exact: true }).locator(`[role=option][data-value="${value}"]`).click()
  await page.waitForFunction(() => !document.querySelector('.tt-configuration [role=status]')?.textContent.includes('Saving'))
  await sheet.getByRole('button', { name: 'Done', exact: true }).click()
  await sheet.waitFor({ state: 'detached' })
}

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const output = process.env.ANTIGRAVITY_EVIDENCE
if (!output) throw new Error('Set ANTIGRAVITY_EVIDENCE to the evidence directory')
await mkdir(output, { recursive: true })
const f = createThreadFixture()
let supportsThreads = false, connected = true, future = false
const providers = () => [...f.providers.map((p) => p.id === 'antigravity' ? { ...p, sign_in: providerFixture().sign_in, login: { source: connected ? 'oauth' : 'none', connected }, connected, reason: connected ? null : 'Not signed in' } : p),
  ...(future ? [{ id: 'future', name: 'The next engine', connected: true }] : [])]
const record = { session_id: 'ag-chat', agent: 'scout', provider: 'antigravity', title: 'Room for a new idea',
  created: new Date().toISOString(), last_activity: new Date().toISOString(), last_event_at: new Date().toISOString(),
  state: 'running', status: 'done', live: true, record_seq: 3, seen_seq: 3, pinned: false, archived: false,
  turns: [{ model: null, usage: null, context: { used: 12000, window: 100000 }, cost_delta_usd: null }],
  pending_permissions: [], background_tasks: 0, last_message: 'A little room for something new.' }
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const frame = (e) => `data: ${JSON.stringify(e)}\n\n`
const stream = (res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
const routes = {
  'GET /v1/agents': (req, res) => json(res, { agents: f.agents }),
  'GET /v1/providers': (req, res) => json(res, { providers: providers() }),
  'GET /v1/sessions': (req, res) => json(res, { sessions: [record], live_count: 1, cap: 4 }),
  'GET /v1/sessions/events': (req, res) => { stream(res); res.write(frame({ type: 'broker.session', ...record })) },
  'GET /v1/sessions/ag-chat/events': (req, res) => {
    stream(res)
    for (const e of [{ type: 'broker.session', ...record, window_first_seq: 1, turns_before: 0 },
      { type: 'broker.user_message', seq: 1, text: 'A new idea.' },
      { type: 'assistant', seq: 2, message: { id: 'answer', content: [{ type: 'text', text: record.last_message }] } },
      { type: 'result', seq: 3, subtype: 'success', is_error: false }]) res.write(frame(e))
  },
  'PATCH /v1/sessions/ag-chat': (req, res) => json(res, record),
  'POST /v1/sessions': (req, res, url, raw) => json(res, { ...record, session_id: 'ag-new', agent: JSON.parse(raw).agent }, 201),
  'GET /v1/sessions/ag-new/events': (req, res) => { stream(res); res.write(frame({ type: 'broker.session', ...record, session_id: 'ag-new', record_seq: 0, window_first_seq: 1, turns_before: 0 })) },
  'POST /v1/sessions/ag-new/messages': (req, res) => json(res, { accepted: true, seq: 1 }),
  'GET /v1/threads': async (req, res) => supportsThreads ? json(res, await f.api('GET', '/threads')) : json(res, { error: 'No threads yet' }, 405),
  'POST /v1/sessions/ag-chat/compact': (req, res) => json(res, { error: 'Compaction is not supported for Antigravity' }, 409),
}
for (const t of (await f.api('GET', '/threads')).threads) {
  const base = `/v1/threads/${t.id}`
  for (const method of ['GET', 'PATCH']) routes[`${method} ${base}`] = async (req, res, url, raw) => json(res, await f.api(method, `/threads/${t.id}`, raw ? JSON.parse(raw) : undefined))
  routes[`GET ${base}/events`] = (req, res) => { stream(res); const stop = f.subscribe(t.id, (e) => res.write(frame(e))); res.on('close', stop) }
}
const broker = await fakeBroker(routes), app = await appAgainst(broker)
let browser
const results = []
try {
  browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
  // The approved design is a reference, opened locally without modifying it.
  const reference = await browser.newPage()
  await reference.goto(new URL('../design/Loam App.dc.html', import.meta.url).href)
  await reference.close()
  for (const [width, theme] of [[390, 'light'], [390, 'dark'], [1440, 'light']]) {
    supportsThreads = false; connected = true
    const context = await browser.newContext({ viewport: { width, height: 844 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    const page = await context.newPage(), errors = []
    page.on('pageerror', (e) => errors.push(e.message))
    const shot = async (name) => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name} overflows`)
      await page.screenshot({ animations: 'disabled', path: path.join(output, `${name}-${width}-${theme}.png`) })
    }
    const settings = async () => { await page.getByRole('button', { name: 'Settings', exact: true }).first().click(); await page.getByRole('button', { name: /Providers/ }).first().click() }
    await page.goto(app.url)
    await page.locator('.persona').first().click()
    const chip = page.locator('.provider-chip').filter({ hasText: 'Antigravity' })
    await chip.click()
    await page.waitForFunction(() => [...document.querySelectorAll('.provider-chip')].some((n) => n.textContent.includes('Antigravity') && n.classList.contains('on')))
    await chip.locator('[data-mark="antigravity"]').waitFor()
    await shot('plus-menu')
    // The first message actually sends Antigravity through the session relay.
    await page.getByRole('button', { name: 'Start chat', exact: true }).click()
    await page.locator('.draft').fill('Carry this idea forward.')
    await Promise.all([page.waitForResponse((res) => res.url().endsWith('/api/sessions/ag-new/messages')), page.getByRole('button', { name: 'Send', exact: true }).click()])
    assert.equal(JSON.parse(broker.seen.filter((r) => r.method === 'POST' && r.path === '/v1/sessions').at(-1).body).provider, 'antigravity')
    await page.goto(app.url)
    await page.locator('.srow').filter({ hasText: record.title }).click()
    await page.locator('.qringbtn [data-mark="antigravity"]').waitFor()
    assert.equal(await page.locator('.qring-arc').count(), 0)
    await page.locator('.ctxfill-hit').click()
    await page.locator('.ctxsheet').waitFor()
    assert.equal(await page.getByRole('button', { name: 'Compact now', exact: true }).count(), 0)
    await page.keyboard.press('Escape')
    await page.goto(app.url)
    await settings()
    const row = page.locator('.setrow').filter({ has: page.locator('.setrow-title', { hasText: 'Antigravity' }) })
    await row.getByText('Connected · no quota reading yet', { exact: true }).waitFor()
    await shot('providers')
    await row.click()
    await page.locator('.provider-sign-in').getByText('Signed in', { exact: true }).waitFor()
    assert.equal(await page.locator('.meter,.loginflow').count(), 0)
    assert.equal(await page.getByRole('button', { name: /^Sign in/ }).count(), 1)
    assert.equal(await page.getByText('Your plan', { exact: true }).count(), 0)
    await shot('provider-detail')
    connected = false
    await page.goto(app.url)
    await page.locator('.persona').first().click()
    assert.equal(await chip.isDisabled(), true)
    await page.getByText('Antigravity · Sign in on the Providers page', { exact: true }).click()
    await page.locator('.provider-sign-in').getByText('Not signed in', { exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: /^Sign in/ }).count(), 1)
    connected = true; supportsThreads = true
    await page.goto(app.url)
    await page.locator('.tt-row').filter({ hasText: 'General' }).click()
    await page.getByTitle('Thread details', { exact: true }).click()
    const engine = page.getByRole('button', { name: 'Provider for Sprout', exact: true })
    await engine.click()
    assert.deepEqual(await page.getByRole('listbox', { name: 'Provider for Sprout', exact: true }).locator('[role=option]').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('data-value'))), ['claude-code', 'codex', 'antigravity'])
    await page.keyboard.press('Escape')
    await page.getByRole('listbox', { name: 'Provider for Sprout', exact: true }).waitFor({ state: 'detached' })
    const configuration = page.getByRole('dialog', { name: 'Providers & models', exact: true })
    if (await configuration.isVisible()) {
      await configuration.getByRole('button', { name: 'Done', exact: true }).click()
      await configuration.waitFor({ state: 'detached' })
    }
    await chooseThreadOption(page, 'Provider for Sprout', 'antigravity')
    await page.waitForFunction(() => document.querySelector('[aria-label="Provider for Sprout"]')?.getAttribute('data-value') === 'antigravity' && !document.querySelector('[aria-label="Provider for Sprout"]').disabled)
    await page.locator('.tt-member').first().locator('[data-mark="antigravity"]').waitFor()
    for (const member of await page.locator('.tt-member').all()) {
      const remove = member.locator('button[aria-label^="Remove "]')
      if (await remove.count()) { const a = await remove.boundingBox(), b = await member.locator('.blob').boundingBox(); assert.ok(Math.abs(a.y + a.height / 2 - b.y - b.height / 2) < 1, 'remove stays centred beside the person') }
    }
    await shot('members')
    assert.equal(JSON.parse(broker.seen.filter((r) => r.method === 'PATCH' && r.path === '/v1/threads/general' && r.body.includes('antigravity')).at(-1).body).engine.sprout, 'antigravity')
    await page.getByRole('button', { name: 'Chat', exact: true }).click()
    if (width === 1440) {
      assert.equal(await page.locator('.tt-member-strip').count(), 0)
      await page.getByTitle('Thread details', { exact: true }).click()
      const stripEngine = page.getByRole('button', { name: 'Provider for Sprout', exact: true })
      await chooseThreadOption(page, 'Provider for Sprout', 'codex')
      await page.waitForFunction(() => document.querySelector('.tt-details .modelbtn [data-mark="codex"]'))
      await chooseThreadOption(page, 'Provider for Sprout', 'antigravity')
      await page.waitForFunction(() => document.querySelector('.tt-details .modelbtn [data-mark="antigravity"]'))
      await shot('member-switch')
    }
    assert.deepEqual(errors, [])
    results.push({ width, theme, providerSent: true, descriptorSignInCard: true, noQuota: true, noCompaction: true, threadEnginePatched: true, errors })
    await context.close()
  }
  // A provider unknown to the app arrives entirely through GET /providers.
  future = true; supportsThreads = false
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
  await page.goto(app.url)
  await page.locator('.persona').first().click()
  await page.locator('.provider-chip').filter({ hasText: 'The next engine' }).click()
  supportsThreads = true
  await page.goto(app.url)
  await page.locator('.tt-row').filter({ hasText: 'General' }).click()
  await page.getByTitle('Thread details', { exact: true }).click()
  await chooseThreadOption(page, 'Provider for Sprout', 'future')
  assert.equal(broker.seen.some((r) => r.method === 'POST' && r.path.endsWith('/antigravity/login')), false)
  assert.equal(broker.seen.some((r) => r.path.endsWith('/compact')), false)
  await writeFile(path.join(output, 'browser.json'), JSON.stringify({ fixture: true, results, nextProviderFromRuntime: true, noLoginOrCompactRequests: true }, null, 2) + '\n')
  console.log(JSON.stringify({ results, nextProviderFromRuntime: true, noLoginOrCompactRequests: true }))
} finally { await browser?.close(); await closing(app, broker)() }
