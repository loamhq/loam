// Opt-in: one short real thread turn. Mutations are confined to that new thread
// and its new CLI session. It never calls the machine-wide End all idle route.
import assert from 'node:assert/strict'
import { mkdir, writeFile, readFile } from 'node:fs/promises'
import { once } from 'node:events'
import path from 'node:path'
import { createApp } from '../src/server.js'
import { createBroker } from '../src/broker.js'
import { readConfig } from '../src/config.js'
import { createLog } from '../src/log.js'
import { shut } from './harness.js'
import { modelsOf } from '../public/providers.js'
import { chooseThreadOption } from './thread-controls.mjs'
const origin = process.env.LOAM_LIVE_APP, out = process.env.RUNNING_LIVE_EVIDENCE
if (!origin || !out) throw new Error('Set LOAM_LIVE_APP and RUNNING_LIVE_EVIDENCE; starts one real turn')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
await mkdir(out, { recursive: true })
const created = new Set(), allowedSessions = new Set(), ended = new Set(), deleted = new Set(), errors = []
const checks = { live: true, startedAt: new Date().toISOString(), completed: false }
const upstream = createBroker(readConfig({ BROKER_URL: origin }), { fetchImpl: (url, init) => {
  const u = new URL(url); u.pathname = u.pathname.replace(/^\/v1\//, '/api/')
  return fetch(u, { ...init, signal: init?.signal ?? AbortSignal.timeout(20000) })
} })
const broker = { ...upstream, async json(method, route, body) {
  if (method !== 'GET' && !(method === 'POST' && route === '/v1/threads')) {
    const ownThread = [...created].some((id) => route === `/v1/threads/${id}` || route.startsWith(`/v1/threads/${id}/`))
    const ownSession = method === 'DELETE' && [...allowedSessions].some((id) => route === `/v1/sessions/${id}`)
    assert.ok(ownThread || ownSession, 'write confined to this check’s new thread/session')
  }
  const response = await upstream.json(method, route, body)
  if (method === 'POST' && route === '/v1/threads' && response.status === 201) created.add(JSON.parse(response.body).id)
  if (method === 'DELETE' && response.status === 200) (route.startsWith('/v1/threads/') ? deleted : ended).add(route.split('/').at(-1))
  return response
} }
const api = async (method, route, body) => { const response = await broker.json(method, '/v1' + route, body); assert.ok(response.status >= 200 && response.status < 300, `${method} ${route}: ${response.status}`); return JSON.parse(response.body) }
const { server } = createApp({ config: readConfig(), broker, log: createLog({ level: 'off' }) })
server.listen(0, '127.0.0.1'); await once(server, 'listening')
const appUrl = `http://127.0.0.1:${server.address().port}`
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'], headless: true })
let context, id, sessionId
try {
  const before = new Set((await api('GET', '/sessions?archived=1')).sessions.map((s) => s.session_id))
  const listing = await api('GET', '/threads?archived=1'), agents = (await api('GET', '/agents')).agents
  const busy = new Set(listing.threads.flatMap((t) => t.members.filter((m) => m.busy || m.running).map((m) => m.persona)))
  const target = agents.find((a) => !busy.has(a.id)); assert.ok(target)
  const provider = (await api('GET', '/providers')).providers.find((p) => p.id === 'claude-code' && p.connected)
  assert.ok(provider, 'connected provider for one short turn')
  const model = modelsOf(provider).find((m) => /haiku/i.test(m.id + m.name)) ?? modelsOf(provider)[0]; assert.ok(model)
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark', reducedMotion: 'reduce' })
  const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
  await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__running = {running,threads,state,schedule};\n' }))
  const page = await context.newPage(); page.setDefaultTimeout(20000); page.on('pageerror', (e) => errors.push(e.message))
  await page.goto(appUrl); await page.getByRole('button', { name: 'New thread', exact: true }).click()
  await page.waitForFunction(() => __running.threads.st.open && !__running.threads.st.open.creating)
  id = [...created].at(-1); assert.ok(id)
  await page.locator('#draft[contenteditable=true]').fill(`@${target.id} Running now UI verification. Reply exactly "Running check complete." Do not modify files, start background tasks, or contact anyone.`)
  await page.locator('.composer .modelbtn').click()
  await page.getByRole('dialog', { name: 'Providers & models', exact: true }).getByRole('button', { name: `Add ${target.name}`, exact: true }).click()
  await page.getByRole('dialog', { name: 'Providers & models', exact: true }).getByRole('button', { name: `Model for ${target.name}`, exact: true }).waitFor()
  await chooseThreadOption(page, `Model for ${target.name}`, model.id)
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  const deadline = Date.now() + 180000
  let own, record
  while (Date.now() < deadline) {
    const all = (await api('GET', '/sessions?archived=1')).sessions
    const candidates = all.filter((s) => !before.has(s.session_id) && s.agent === target.id && s.archived)
    record = await api('GET', `/threads/${id}`)
    // The before/after id set and single new harness identify this check’s
    // process only. Runtime titles can change after the first turn; the UI
    // never uses this test-only association for navigation.
    assert.ok(candidates.length <= 1, 'only this check started a new harness for the selected agent')
    own = candidates[0]
    if (own) { sessionId = own.session_id; allowedSessions.add(sessionId); break }
    await page.waitForTimeout(200)
  }
  assert.ok(sessionId, 'this new thread’s own session identified')
  await page.getByTitle('Running now', { exact: true }).click()
  const row = page.locator(`.run-row[data-session="${sessionId}"]`)
  await row.waitFor(); assert.match(await row.locator('.run-state').innerText(), /Working|background/i)
  checks.runtimeMode = await page.evaluate(() => __running.running.st.mode)
  checks.workingObserved = true
  await page.screenshot({ path: path.join(out, 'live-working-1440-dark.png') })
  while (Date.now() < deadline) {
    own = await api('GET', `/sessions/${sessionId}`)
    record = await api('GET', `/threads/${id}`)
    if (own.live && own.status === 'done' && !record.turn) break
    await page.waitForTimeout(500)
  }
  assert.equal(own.status, 'done'); assert.equal(own.live, true)
  assert.ok(record.messages.some((m) => m.author === target.id && /Running check complete/.test(m.text)))
  await page.waitForFunction((sid) => __running.running.getSession(sid)?.state === 'idle', sessionId)
  assert.match(await row.locator('.run-state').innerText(), /Idle/)
  checks.idleObserved = true
  await page.screenshot({ path: path.join(out, 'live-idle-1440-dark.png') })
  if (checks.runtimeMode === 'rich') {
    await row.locator('.run-open').click()
    await page.waitForFunction((tid) => __running.threads.st.open?.thread.id === tid, id)
    await page.getByTitle('Thread details', { exact: true }).click()
    await page.locator('.tt-member .run-live').waitFor()
    checks.threadNavigation = true; checks.memberLiveDot = true
    await page.getByTitle('Running now', { exact: true }).click()
  } else checks.richFeaturesUnavailable = 'The running broker has not loaded the new live-sessions routes.'
  const beforeMessages = record.messages.map((m) => [m.id, m.text])
  await row.getByRole('button', { name: /^End / }).click()
  await page.waitForFunction((sid) => !__running.running.getSession(sid), sessionId)
  await page.locator('.run-toast').waitFor()
  const stopped = await api('GET', `/sessions/${sessionId}`), survived = await api('GET', `/threads/${id}`)
  assert.equal(stopped.live, false); assert.deepEqual(survived.messages.map((m) => [m.id, m.text]), beforeMessages)
  assert.equal(survived.thread.id, id); assert.equal(ended.has(sessionId), true)
  checks.endedFromUI = true; checks.threadSurvived = true; checks.messagesUnchanged = true
  await page.screenshot({ path: path.join(out, 'live-ended-1440-dark.png') })
  await api('DELETE', `/threads/${id}`)
  assert.equal(deleted.has(id), true)
  assert.equal((await api('GET', '/threads?archived=1')).threads.some((t) => t.id === id), false)
  checks.cleanedUp = true; checks.browserErrors = errors; assert.deepEqual(errors, [])
  checks.completed = true; checks.finishedAt = new Date().toISOString()
} catch (e) { checks.error = e.message; throw e }
finally {
  if (sessionId && !ended.has(sessionId)) await api('DELETE', `/sessions/${sessionId}`).catch((e) => { checks.sessionCleanupError = e.message })
  for (const ownId of created) if (!deleted.has(ownId)) await api('DELETE', `/threads/${ownId}`).catch((e) => { checks.threadCleanupError = e.message })
  await writeFile(path.join(out, 'live.json'), JSON.stringify(checks, null, 2) + '\n')
  await context?.close(); await browser.close(); await shut(server)
}
