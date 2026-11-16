// Real page, CSS and keyboard; a fixture broker supplies the five list states.
// Optional browser tools stay outside the repository. No host sessions run.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { fakeBroker, appAgainst, closing } from './harness.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright-core')
const output = process.env.CHAT_BUBBLES_EVIDENCE
if (!output) throw new Error('Set CHAT_BUBBLES_EVIDENCE to a directory outside the repository')
const repository = fileURLToPath(new URL('..', import.meta.url))
const relative = path.relative(repository, path.resolve(output))
if (!relative || (!relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative))) throw new Error('Evidence must be outside the repository')
await mkdir(output, { recursive: true })
const now = new Date().toISOString()
const labels = { working: 'Working', waiting: 'Waiting', done: 'Done', needs_input: 'Needs your input', failed: 'Failed' }
const messages = {
  working: 'I’m checking the implementation and running the relevant tests.',
  waiting: 'The checks are running in the background. I’ll report when they finish.',
  done: 'The change is ready. All checks passed.',
  needs_input: 'Can I run the final check?',
  failed: 'The command failed. Ready to retry.',
}
const records = Object.keys(labels).map((status, i) => ({
  session_id: `fixture-${status}`, agent: 'test-agent', provider: 'claude-code',
  workspace: '/fixture', created: now, last_activity: now, last_event_at: now,
  state: status === 'done' || status === 'failed' ? 'exited' : 'running',
  status, title: ['Review the implementation', 'Wait for the checks', 'Change ready to review', 'Approve the next step', 'Retry the failed command'][i],
  last_message: messages[status], pinned: false, archived: false, record_seq: 2, seen_seq: 0,
  background_tasks: status === 'waiting' ? 2 : 0, turns: [], pending_permissions: [],
}))
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const frame = (obj) => `data: ${JSON.stringify(obj)}\n\n`
const routes = {
  // Shared first letters force two initials, exercising the compact badge.
  'GET /v1/agents': (req, res) => json(res, { agents: [{ id: 'test-agent', name: 'Cinder', emoji: null }, { id: 'other-agent', name: 'Clover', emoji: null }] }),
  'GET /v1/providers': (req, res) => json(res, { providers: [{ id: 'claude-code', name: 'Claude Code', connected: true, usage: null }] }),
  'GET /v1/limits': (req, res) => json(res, { attachment_max_bytes: 10000000, attachment_types: [] }),
  'GET /v1/sessions': (req, res) => json(res, { sessions: records, live_count: 3, cap: 8 }),
  'GET /v1/sessions/events': (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(': open ping=15000\n\n')
    for (const record of records) res.write(frame({ type: 'broker.session', ...record }))
  },
}
for (const record of records) {
  routes[`GET /v1/sessions/${record.session_id}/events`] = (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(': open ping=15000\n\n')
    res.write(frame({ type: 'broker.session', ...record, window_first_seq: 1, turns_before: 0 }))
    res.write(frame({ type: 'broker.user_message', session_id: record.session_id, seq: 1, text: 'Please help with this step.' }))
    res.write(frame({ type: 'assistant', session_id: record.session_id, seq: 2, message: { id: `m-${record.status}`, role: 'assistant', content: [{ type: 'text', text: messages[record.status] }] } }))
  }
  routes[`PATCH /v1/sessions/${record.session_id}`] = (req, res) => json(res, record)
}
const broker = await fakeBroker(routes)
const app = await appAgainst(broker)
let browser
const results = []
try {
  browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
  for (const { width, theme } of [{ width: 1440, theme: 'light' }, { width: 1440, theme: 'dark' }, { width: 390, theme: 'light' }]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', (e) => errors.push(e.message))
    await page.goto(app.url)
    await page.locator('.srow').first().waitFor()
    assert.equal(await page.locator('.srow .box').count(), 5)
    const cues = (el) => ({
      fill: getComputedStyle(el.querySelector('.box')).backgroundColor,
      label: el.querySelector('.box .kicker').textContent.split(' · ')[0],
      labelColor: getComputedStyle(el.querySelector('.box .kicker')).color,
      dotColor: el.querySelector('.dot') ? getComputedStyle(el.querySelector('.dot')).backgroundColor : null,
      ring: el.querySelector('.ring') ? getComputedStyle(el.querySelector('.ring')).boxShadow : null,
      breathing: el.querySelectorAll('.breathe').length,
    })
    const listCues = await Promise.all(records.map((record) => page.locator('.srow').filter({ hasText: record.title }).evaluate(cues)))
    const openSettings = async () => {
      await page.getByRole('button', { name: 'Settings', exact: true }).first().click()
      await page.locator(width === 390 ? '.setrow' : '.navrow').filter({ hasText: 'Chats' }).first().click()
      await page.locator('.chat-bubbles').waitFor()
    }
    await openSettings()
    for (const [i, label] of Object.values(labels).entries()) {
      const sample = page.getByRole('switch', { name: `${label} bubble`, exact: true })
      assert.deepEqual(await sample.evaluate(cues), listCues[i], `${label} sample differs from the actual list mark`)
      const preview = await sample.locator('.box-text').textContent()
      assert.ok(preview.trim().length > 1 && preview !== '…', `${label} sample has no preview line`)
    }
    await page.evaluate(() => document.fonts.ready)
    const badge = await page.getByRole('switch', { name: 'Failed bubble', exact: true }).evaluate((row) => {
      const blob = row.querySelector('.blob'), bang = row.querySelector('.bang')
      const range = document.createRange()
      range.selectNodeContents(blob)
      const rect = (r) => ({ left: r.left, right: r.right, top: r.top, bottom: r.bottom })
      return { initials: blob.textContent, text: rect(range.getBoundingClientRect()), blob: rect(blob.getBoundingClientRect()), bang: rect(bang.getBoundingClientRect()) }
    })
    assert.equal(badge.initials, 'Ci')
    assert.ok(badge.bang.left >= badge.text.right || badge.bang.top >= badge.text.bottom, `failure badge covers the initials: ${JSON.stringify(badge)}`)
    assert.ok(badge.bang.right > badge.blob.right && badge.bang.bottom > badge.blob.bottom, 'failure badge is not at the outer corner')
    await page.mouse.move(width - 1, 1)
    await page.waitForTimeout(300)
    await page.screenshot({ path: path.join(output, `settings-${width}-${theme}.png`) })
    for (const [id, label] of Object.entries(labels)) {
      const toggle = page.getByRole('switch', { name: `${label} bubble`, exact: true })
      assert.equal(await toggle.getAttribute('aria-checked'), 'true')
      await toggle.focus()
      await page.keyboard.press(id === 'waiting' ? 'Enter' : 'Space')
      await page.waitForFunction((label) => document.querySelector(`[aria-label="${label} bubble"]`)?.getAttribute('aria-checked') === 'false', label)
      assert.equal(await toggle.evaluate((el) => document.activeElement === el), true, 'redraw lost focus')
      if (id !== 'working' && id !== 'waiting') {
        await page.keyboard.press('Space')
        await page.waitForFunction((label) => document.querySelector(`[aria-label="${label} bubble"]`)?.getAttribute('aria-checked') === 'true', label)
      }
    }
    await page.getByRole('switch', { name: 'Working bubble', exact: true }).focus()
    await page.keyboard.press('Tab')
    assert.equal(await page.getByRole('switch', { name: 'Waiting bubble', exact: true }).evaluate((el) => document.activeElement === el), true)
    const geometry = await page.locator('.chat-bubbles').evaluate((card) => ({
      width: card.getBoundingClientRect().width,
      overflow: card.scrollWidth > card.clientWidth,
      rows: [...card.querySelectorAll('.bubble-toggle')].map((el) => ({ height: el.getBoundingClientRect().height, overflow: el.scrollWidth > el.clientWidth })),
      focusOutline: getComputedStyle(document.activeElement).outlineStyle,
    }))
    assert.equal(geometry.overflow, false)
    assert.equal(geometry.focusOutline, 'solid')
    assert.ok(geometry.rows.every((r) => r.height >= 44 && !r.overflow))
    await page.keyboard.press('Escape')
    await page.locator('.chat-bubbles').waitFor({ state: 'detached' })
    for (const id of Object.keys(labels)) {
      const row = page.locator('.srow').filter({ hasText: records.find((r) => r.status === id).title })
      assert.equal(await row.locator('.box').count(), id === 'working' || id === 'waiting' ? 0 : 1)
      if (id === 'working' || id === 'waiting') assert.equal(await row.locator('.dot').count(), 1)
    }
    await page.screenshot({ path: path.join(output, `list-${width}-${theme}.png`) })
    await page.reload()
    await page.locator('.srow').first().waitFor()
    assert.equal(await page.locator('.srow .box').count(), 3, 'reload lost preferences')
    // The hidden working bubble has no effect on the open thread or its header.
    await page.locator('.srow').filter({ hasText: records[0].title }).click()
    await page.getByText(messages.working, { exact: true }).last().waitFor()
    assert.equal(await page.locator('.headblob.working').count(), 1)
    assert.equal(broker.seen.some((r) => r.method === 'PATCH' && /chat-bubbles/.test(r.body)), false)
    assert.deepEqual(errors, [])
    results.push({ width, theme, badge, geometry, keyboard: 'Space, Enter, Tab and focus retention passed', reload: 'passed', thread: 'working header and reply preserved', errors })
    await context.close()
  }
  const fresh = await browser.newContext()
  const freshPage = await fresh.newPage()
  await freshPage.goto(app.url)
  await freshPage.locator('.srow .box').first().waitFor()
  assert.equal(await freshPage.locator('.srow .box').count(), 5, 'another device inherited preferences')
  await fresh.close()
  await writeFile(path.join(output, 'results.json'), JSON.stringify(results, null, 2) + '\n')
  console.log('PASS: 1440 light/dark and 390 light; five matching labels and preview lines, badge clear of two initials, keyboard, focus, geometry, persistence, device isolation and unchanged thread/header. Six screenshots saved.')
} finally {
  await browser?.close()
  await closing(app, broker)()
}
