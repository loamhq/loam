// Explicit live verification. Creates only its own threads, sends one bounded
// agent request, and deletes every created thread. Never opens an existing chat.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { once } from 'node:events'
import path from 'node:path'
import { createApp } from '../src/server.js'
import { createBroker } from '../src/broker.js'
import { readConfig } from '../src/config.js'
import { createLog } from '../src/log.js'
import { shut } from './harness.js'
const origin = process.env.LOAM_LIVE_APP, out = process.env.THREAD_ROUND_THREE_EVIDENCE
if (!origin || !out) throw new Error('Set LOAM_LIVE_APP and THREAD_ROUND_THREE_EVIDENCE; this driver writes temporary live threads.')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
await mkdir(out, { recursive: true })
const created = new Set(), checks = { live: true, checkedAt: new Date().toISOString() }, errors = []
const upstream = createBroker(readConfig({ BROKER_URL: origin }), { fetchImpl: (url, init) => {
  const u = new URL(url); u.pathname = u.pathname.replace(/^\/v1\//, '/api/')
  return fetch(u, init)
} })
const broker = { ...upstream, async json(method, route, body) {
  if (method !== 'GET' && !(method === 'POST' && route === '/v1/threads')) {
    assert.ok([...created].some((id) => route === `/v1/threads/${id}` || route.startsWith(`/v1/threads/${id}/`)), 'writes confined to driver-created threads')
  }
  const answer = await upstream.json(method, route, body)
  if (method === 'POST' && route === '/v1/threads' && answer.status === 201) created.add(JSON.parse(answer.body).id)
  return answer
} }
async function api(method, route, body) {
  const answer = await broker.json(method, '/v1'+route, body)
  assert.ok(answer.status >= 200 && answer.status < 300, `${method} ${route}: ${answer.status}`)
  return JSON.parse(answer.body)
}
const { server } = createApp({ config: readConfig(), broker, log: createLog({ level: 'error', write: () => {} }) })
server.listen(0, '127.0.0.1'); await once(server, 'listening')
const appUrl = `http://127.0.0.1:${server.address().port}`
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
try {
  const agents = (await api('GET', '/agents')).agents
  const persona = agents[0].id
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark', isMobile: true, hasTouch: true })
  const page = await context.newPage()
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto(appUrl)
  const creation = page.waitForResponse((r) => r.request().method() === 'POST' && r.url().endsWith('/api/threads'))
  await page.getByRole('button', { name: 'New thread', exact: true }).click()
  await creation
  const draft = page.locator('#draft[contenteditable=true]')
  await draft.waitFor(); await page.locator('.roundsend:not(:disabled)').waitFor({ state: 'detached' })
  await page.waitForFunction(() => !document.querySelector('.tt-composer .composehint')?.textContent.includes('Creating'))
  await page.waitForFunction(() => document.querySelector('.tt-empty-icon'))
  const id = [...created].at(-1); assert.ok(id)
  assert.equal(await page.locator('.tt-empty-icon svg').count(), 1)
  assert.equal(await page.locator('.tt-empty .blob').count(), 0)
  await page.screenshot({ path: path.join(out, 'live-empty-390-dark.png') })
  checks.emptyPeopleTile = true
  await page.getByTitle('Thread details', { exact: true }).click()
  await page.getByRole('button', { name: 'Use Conversation icon', exact: true }).click()
  await page.waitForFunction(() => !document.querySelector('[aria-label="Use Conversation icon"]').disabled)
  const gap = await page.locator('.tt-nobody-note').evaluate((p) => p.getBoundingClientRect().top - p.previousElementSibling.getBoundingClientRect().bottom)
  assert.ok(gap >= 20, `help gap ${gap}`); checks.helpGapPx = gap
  await page.getByRole('button', { name: 'Chat', exact: true }).click()
  assert.equal(await page.locator('.tt-empty-icon svg').count(), 1)
  await page.screenshot({ path: path.join(out, 'live-empty-picked-glyph-390-dark.png') })
  checks.emptyPickedIcon = true
  await draft.fill('First line'); await draft.press('Enter'); await draft.type('Second line')
  const text = await draft.evaluate(async (el) => (await import('/compose.js')).readField(el).text)
  assert.equal(text, 'First line\nSecond line')
  assert.equal((await api('GET', `/threads/${id}`)).messages.length, 0)
  checks.mobileEnterNewlineWithoutSending = true
  const message = `@${persona} Round three browser verification. Reply exactly: UI check complete. Do not use tools or change files.`
  await draft.fill(message)
  await page.locator('.roundsend').click()
  await page.waitForFunction(() => document.querySelector('.tt-title .display')?.textContent.startsWith('Round three browser verification.'), null, { timeout: 20000 })
  await page.locator('.tt-member-more').waitFor()
  assert.equal(await page.locator('.tt-member-more').innerText(), '+1')
  checks.singleLiveMemberCount = true
  await page.waitForFunction(() => document.querySelector('.tt-stream')?.textContent.includes('UI check complete.'), null, { timeout: 90000 })
  let record
  for (let n = 0; n < 60; n++) {
    record = await api('GET', `/threads/${id}`)
    if (!record.turn) break
    await new Promise((r) => setTimeout(r, 500))
  }
  assert.equal(record.turn, null)
  assert.ok(record.thread.title.startsWith('Round three browser verification.'))
  assert.equal(record.messages.find((m) => m.text === message)?.text, message)
  checks.cleanedTitlePersisted = true; checks.mentionPreservedInMessage = true; checks.liveReplyFinished = true
  await page.reload()
  await page.locator('.tt-row').filter({ hasText: record.thread.title }).click()
  await page.locator('.tt-member-more').waitFor()
  assert.equal(await page.locator('.tt-member-more').innerText(), '+1')
  checks.memberCountAfterReload = true
  await api('PATCH', `/threads/${id}`, { add: agents.slice(1, 5).map((a) => a.id) })
  await page.waitForFunction(() => document.querySelector('.tt-member-more')?.textContent === '+5')
  assert.equal(await page.locator('.tt-member-more').innerText(), '+5')
  assert.equal(record.thread.default, null)
  checks.phoneCountsAllMembersWithoutDefault = true
  await page.getByTitle('Thread details', { exact: true }).click()
  const member = page.locator('.tt-member').first()
  if (await member.locator('.tt-model-select').count()) {
    const aligned = await member.evaluate((el) => {
      const engine = el.querySelector('.tt-engine-select').getBoundingClientRect()
      const label = el.querySelector('.tt-model-select').getBoundingClientRect()
      const select = el.querySelector('.tt-model-select .modelbtn').getBoundingClientRect()
      return { engine: engine.x, label: label.x, select: select.x, tidy: label.y >= engine.bottom ? Math.abs(label.x - engine.x) < 1 : label.x >= engine.right + 7 }
    })
    assert.ok(Math.abs(aligned.label - aligned.select) < 1 && aligned.tidy)
    checks.memberControlsAligned = true
  }
  await context.close()
  assert.deepEqual(errors, []); checks.errors = errors
} finally {
  await browser.close()
  for (const id of created) {
    const result = await upstream.json('DELETE', `/v1/threads/${id}`)
    assert.equal(result.status, 200, 'temporary live thread deleted')
  }
  const listing = await upstream.json('GET', '/v1/threads?archived=1')
  assert.ok(JSON.parse(listing.body).threads.every((t) => !created.has(t.id)))
  checks.cleanedUp = true; checks.temporaryThreads = created.size
  await writeFile(path.join(out, 'live.json'), JSON.stringify(checks, null, 2)+'\n')
  await shut(server)
}
console.log(JSON.stringify(checks, null, 2))
