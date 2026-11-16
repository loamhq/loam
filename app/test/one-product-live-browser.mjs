// Opt-in live verification. Two agents read their own instructions, reply,
// and the UI deletes its temporary thread. No existing conversation is opened.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { once } from 'node:events'
import path from 'node:path'
import { createApp } from '../src/server.js'
import { createBroker } from '../src/broker.js'
import { readConfig } from '../src/config.js'
import { createLog } from '../src/log.js'
import { shut } from './harness.js'
import { modelsOf } from '../public/providers.js'
import { chooseThreadOption } from './thread-controls.mjs'
const origin = process.env.LOAM_LIVE_APP, out = process.env.ONE_PRODUCT_LIVE_EVIDENCE
if (!origin || !out) throw new Error('Set LOAM_LIVE_APP and ONE_PRODUCT_LIVE_EVIDENCE; this driver starts two live agent turns.')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
await mkdir(out, { recursive: true })
let sentModel = null
const created = new Set(), deleted = new Set(), checks = { live: true, startedAt: new Date().toISOString(), completed: false }, errors = []
const upstream = createBroker(readConfig({ BROKER_URL: origin }), { fetchImpl: (url, init) => {
  const u = new URL(url); u.pathname = u.pathname.replace(/^\/v1\//, '/api/')
  return fetch(u, { ...init, signal: init?.signal ?? AbortSignal.timeout(20000) })
} })
const broker = { ...upstream, async json(method, route, body) {
  if (method !== 'GET' && !(method === 'POST' && route === '/v1/threads')) assert.ok([...created].some((id) => route === `/v1/threads/${id}` || route.startsWith(`/v1/threads/${id}/`)), 'writes confined to this driver’s new thread')
  if (method === 'POST' && route.endsWith('/messages')) sentModel = body.model
  const response = await upstream.json(method, route, body)
  if (method === 'POST' && route === '/v1/threads' && response.status === 201) created.add(JSON.parse(response.body).id)
  if (method === 'DELETE' && response.status === 200) deleted.add(route.split('/').at(-1))
  return response
} }
async function api(method, route, body) {
  const response = await broker.json(method, '/v1' + route, body)
  assert.ok(response.status >= 200 && response.status < 300, `${method}: ${response.status}`)
  return JSON.parse(response.body)
}
const { server } = createApp({ config: readConfig(), broker, log: createLog({ level: 'off' }) })
server.listen(0, '127.0.0.1'); await once(server, 'listening')
const appUrl = `http://127.0.0.1:${server.address().port}`
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
try {
  const listing = await api('GET', '/threads?archived=1'), agents = (await api('GET', '/agents')).agents
  assert.ok(listing.threads.every((t) => 'icon' in t && 'archived' in t))
  const occupied = new Set(listing.threads.flatMap((t) => t.members.filter((m) => m.busy || m.running).map((m) => m.persona)))
  const available = agents.filter((a) => !occupied.has(a.id)), targets = available.slice(0, 2)
  assert.equal(targets.length, 2)
  const desktop = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' }), page = await desktop.newPage()
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto(appUrl)
  const creation = page.waitForResponse((r) => r.request().method() === 'POST' && r.url().endsWith('/api/threads'))
  await page.getByRole('button', { name: 'New thread', exact: true }).click(); await creation
  const id = [...created].at(-1); assert.ok(id)
  const text = targets.map((a) => '@' + a.id).join(' ') + ' One product live verification. Each of you: use the Read tool once to read the first 10 lines of your own CLAUDE.md, then reply exactly "UI verification complete." Do not modify files or contact anyone.'
  const provider = (await api('GET','/providers')).providers.find((p) => p.id === 'claude-code')
  const choices = modelsOf(provider), selected = choices.find((m) => /haiku/i.test(m.id+' '+m.name)) ?? choices[0]
  assert.ok(selected, 'runtime provides a model for newly mentioned agents')
  await page.locator('#draft[contenteditable=true]').fill(text)
  await page.locator('.composer .modelbtn').waitFor()
  await page.locator('.composer .modelbtn').click()
  for (const target of targets) {
    await page.getByRole('dialog', { name: 'Providers & models', exact: true }).getByRole('button', { name: `Add ${target.name}`, exact: true }).click()
    await page.getByRole('dialog', { name: 'Providers & models', exact: true }).getByRole('button', { name: `Model for ${target.name}`, exact: true }).waitFor()
    await chooseThreadOption(page, `Model for ${target.name}`, selected.id)
    if (target !== targets.at(-1)) await page.locator('.composer .modelbtn').click()
  }
  assert.equal(await page.locator('.composer .modelbtn-name').innerText(), 'Providers & models')
  checks.savedMemberModels = true

  await page.getByRole('button', { name: 'Send', exact: true }).click()
  let record
  const deadline = Date.now() + 180000
  while (Date.now() < deadline) {
    record = await api('GET', `/threads/${id}`)
    if (!record.turn && targets.every((a) => record.messages.some((m) => m.author === a.id) && !record.thread.members.find((m) => m.persona === a.id)?.busy)) break
    await new Promise((r) => setTimeout(r, 1000))
  }
  assert.equal(record.turn, null)
  assert.equal(record.thread.default, null)
  assert.equal(sentModel, undefined, 'the saved member settings replace the message override')
  assert.ok(targets.every((a) => record.thread.members.find((m) => m.persona === a.id)?.model === selected.id), 'both member preferences are durable')
  checks.preconfiguredMembers = true
  const replies = targets.map((a) => record.messages.find((m) => m.author === a.id))
  assert.ok(replies.every((m) => typeof m.model === 'string' && m.model.toLowerCase().includes(selected.id.toLowerCase())), 'both replies report the selected model')
  assert.ok(targets.every((a) => record.thread.members.find((m) => m.persona === a.id).engine === 'claude-code'))
  checks.selectedModel = selected.id; checks.replyModels = replies.map((m) => m.model); checks.savedModelsUsedForBoth = true
  checks.configuredMemberEngineVerified = true

  const readStep = (m) => [...(m.did ?? []), ...(m.segments ?? []).flatMap((p) => p.did ?? [])].some((step) => /read/i.test(step.label ?? ''))
  assert.ok(targets.every((a) => record.messages.some((m) => m.author === a.id && /UI verification complete/i.test(m.text) && readStep(m))))
  await page.locator('.tt-stream .say.agent').last().waitFor()
  assert.ok(await page.locator('.tt-speaker').count() >= 2)
  assert.ok(await page.locator('.fold-head').count() >= 2)
  await page.locator('.fold-head').first().click()
  await page.locator('.fold-body').first().waitFor({ state: 'visible' })
  assert.equal(await page.locator('.fold-head').first().getAttribute('aria-expanded'), 'true')
  assert.ok(await page.locator('.fold-body').first().isVisible())
  assert.ok(record.thread.title.startsWith('One product live verification.'))
  checks.replyingAgents = 2; checks.readStepsForBoth = true; checks.desktopSharedCard = await page.locator('.pane .tt-thread').count() === 1
  checks.expandableSteps = true; checks.cleanTitlePersisted = true
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark', isMobile: true, hasTouch: true }), mobile = await phone.newPage()
  mobile.on('pageerror', (e) => errors.push(e.message))
  await mobile.goto(appUrl)
  await mobile.locator('.tt-row').filter({ hasText: record.thread.title }).click()
  await mobile.locator('.tt-speaker').first().waitFor()
  assert.ok(await mobile.locator('.fold-head').count() >= 2)
  assert.equal(await mobile.locator('.hint').count(), 0)
  assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
  checks.phoneReplayAndSteps = true
  await mobile.getByRole('button',{name:'Thread actions',exact:true}).click()
  await mobile.getByRole('button', { name: 'Delete thread', exact: true }).click()
  await mobile.getByRole('dialog',{name:'Confirm delete',exact:true}).waitFor()
  assert.equal(await mobile.locator('.tt-details').count(),0)
  checks.deleteConfirmedInPlace = true
  await mobile.getByRole('button', { name: 'Delete permanently', exact: true }).click()
  await mobile.getByRole('button', { name: 'New thread', exact: true }).waitFor()
  assert.ok(deleted.has(id))
  checks.deletedThroughUi = true
  await desktop.close(); await phone.close()
  assert.deepEqual(errors, []); checks.errors = errors; checks.completed = true
} catch (error) { checks.error = error.message; throw error }
finally {
  await browser.close()
  try {
    for (const id of created) {
      if (deleted.has(id)) continue
      const deadline = Date.now() + 180000
      let record
      do {
        const answer = await upstream.json('GET', `/v1/threads/${id}`)
        if (answer.status === 404) { deleted.add(id); break }
        record = JSON.parse(answer.body)
        if (!record.turn) break
        await new Promise((r) => setTimeout(r, 1000))
      } while (Date.now() < deadline)
      if (!deleted.has(id)) {
        const answer = await upstream.json('DELETE', `/v1/threads/${id}`)
        assert.equal(answer.status, 200, 'temporary thread cleanup')
        deleted.add(id)
      }
    }
    const answer = await upstream.json('GET', '/v1/threads?archived=1')
    assert.ok(JSON.parse(answer.body).threads.every((t) => !created.has(t.id)))
    checks.cleanedUp = true; checks.temporaryThreads = created.size; checks.finishedAt = new Date().toISOString()
  } finally {
    await writeFile(path.join(out, 'live.json'), JSON.stringify(checks, null, 2) + '\n')
    await shut(server)
  }
}
console.log(JSON.stringify(checks, null, 2))
