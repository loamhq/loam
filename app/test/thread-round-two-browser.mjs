import { chooseThreadOption } from './thread-controls.mjs'
// Exact runtime 7dcd478 events through the real relay; no production writes.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { createThreadFixture } from '../public/threads-fixture.js'
import { fakeBroker, appAgainst, closing } from './harness.js'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.THREAD_ROUND_TWO_EVIDENCE
if (!out) throw new Error('Set THREAD_ROUND_TWO_EVIDENCE')
await mkdir(out, { recursive: true })
const f = createThreadFixture(), listStreams = new Set(), streams = new Map(), extras = new Map(), results = []
let listUp = true, deleteBusy = false, refuseModel = false, peerEdit = false
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const routes = {
  'GET /v1/agents': (req, res) => json(res, { agents: f.agents }),
  'GET /v1/providers': (req, res) => json(res, { providers: f.providers }),
  'GET /v1/threads': async (req, res, url) => json(res, await f.api('GET', '/threads' + url.search)),
  'GET /v1/threads/events': (req, res) => {
    if (!listUp) return json(res, { error: 'not available' }, 405)
    res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); listStreams.add(res)
    const stop = f.subscribeList((e) => res.write(`data: ${JSON.stringify(e)}\n\n`))
    res.on('close', () => { stop(); listStreams.delete(res) })
  },
}
function emit(id, e) { for (const res of streams.get(id) ?? []) res.write(`data: ${JSON.stringify(e)}\n\n`) }
function addRoutes(id) {
  for (const [method, suffix] of [['GET', ''], ['PATCH', ''], ['DELETE', ''], ['POST', '/messages'], ['GET', '/board'], ['PUT', '/board']]) routes[`${method} /v1/threads/${id}${suffix}`] = async (req, res, url, raw) => {
    if (method === 'DELETE' && deleteBusy) return json(res, { error: 'a turn is running in this thread; delete it when the turn has finished' }, 409)
    const body = raw ? JSON.parse(raw) : undefined
    if (method === 'PATCH' && body.model && refuseModel) return json(res, { error: 'This model is not available for this engine.' }, 400)
    const answer = await f.api(method, `/threads/${id}${suffix}${url.search}`, body)
    if (method === 'PATCH' && body?.model && peerEdit) {
      peerEdit = false
      await f.api('PATCH', `/threads/${id}`, { default: 'quill' })
      await new Promise((resolve) => setTimeout(resolve, 180))
    }
    if (method === 'GET' && !suffix && extras.has(id)) answer.messages.push(...extras.get(id))
    json(res, answer)
  }
  routes[`GET /v1/threads/${id}/events`] = (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n')
    if (!streams.has(id)) streams.set(id, new Set()); streams.get(id).add(res)
    const stop = f.subscribe(id, (e) => res.write(`data: ${JSON.stringify(e)}\n\n`))
    res.on('close', () => { stop(); streams.get(id).delete(res) })
  }
}
for (const t of (await f.api('GET', '/threads')).threads) addRoutes(t.id)
const broker = await fakeBroker(routes), app = await appAgainst(broker, { AVATAR_COLOURS: '{"quill":"persona-plum"}' })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    const page = await context.newPage(), errors = []
    page.on('pageerror', (e) => { errors.push(e.message); console.error(e.message) })
    const shot = async (name) => { await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) }); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(out, `${name}-${width}-${theme}.png`), animations: 'disabled' }) }
    const id = 'a-quieter-week'
    await page.goto(app.url)
    await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).waitFor()
    assert.equal(await page.getByRole('button', { name: 'Archived threads', exact: true }).count(), 0)
    await page.clock.install()
    await page.waitForTimeout(200)
    const beforePoll = broker.seen.filter((r) => r.path === '/v1/threads' && r.method === 'GET').length
    await page.clock.fastForward(16000)
    await page.waitForTimeout(100)
    assert.equal(broker.seen.filter((r) => r.path === '/v1/threads' && r.method === 'GET').length, beforePoll, 'a healthy list stream suppresses polling')
    const external = await f.api('POST', '/threads', { title: 'A thread from another device' })
    await page.locator('.tt-row').filter({ hasText: 'New thread' }).waitFor()
    await f.api('DELETE', `/threads/${external.id}`)
    await page.locator(`.tt-row[title="New thread"]`).waitFor({ state: 'detached' })
    assert.equal(broker.seen.filter((r) => r.path === '/v1/threads' && r.method === 'GET').length, beforePoll, 'row changes/removal use SSE alone')
    await f.api('PATCH', '/threads/weekend', { archived: true })
    await page.getByRole('button', { name: 'Thread list actions', exact: true }).click()
    const archived = page.locator('.tt-list-menu').getByRole('button', { name: /Archived threads/ })
    await archived.waitFor()
    assert.equal(await page.locator('.tt-archive-link').count(), 0, 'archive entry lives in the header menu')
    await shot('chats-archived')
    await archived.click()
    await page.locator('.tt-row').filter({ hasText: 'Somewhere new for the weekend' }).waitFor()
    assert.equal(await page.locator('.tt-row').count(), 1)
    await page.getByRole('button', { name: 'Back to Chats', exact: true }).click()
    await f.api('PATCH', '/threads/weekend', { archived: false })
    await archived.waitFor({ state: 'detached' })
    await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click()
    await page.locator('#draft[contenteditable=true]').waitFor()
    await page.getByTitle('Thread details', { exact: true }).click()
    assert.equal(await page.getByRole('textbox', { name: 'Thread title', exact: true }).count(), 0)
    const rename = page.locator('.tt-details').getByTitle('Rename thread', { exact: true })
    const original = await rename.innerText()
    await rename.click()
    const field = page.getByRole('textbox', { name: 'Thread title', exact: true })
    await field.fill('Cancelled name'); await field.press('Escape')
    await rename.waitFor(); assert.equal(await rename.innerText(), original)
    await rename.click(); await field.fill('A little room for the week'); await field.press('Enter')
    await rename.waitFor()
    await rename.click(); await field.fill('A little room for the week · edited'); await field.press('Tab')
    await page.waitForFunction(() => document.querySelector('.tt-details-title')?.textContent.endsWith('· edited'))
    await rename.click(); await field.fill('A little room for the week'); await field.press('Enter')
    const defaults = page.getByRole('group', { name: 'Default agent', exact: true })
    await defaults.getByRole('button', { name: 'Nobody', exact: true }).click()
    await page.getByText('Nobody replies unless you @mention someone.', { exact: true }).waitFor()
    await shot('details-nobody')
    await defaults.getByRole('button', { name: 'Sprout', exact: true }).click()
    await page.getByText('Nobody replies unless you @mention someone.', { exact: true }).waitFor({ state: 'detached' })
    peerEdit = true
    await chooseThreadOption(page, 'Model for Sprout', 'deep')
    await page.waitForFunction(() => document.querySelector('[aria-label="Model for Sprout"]')?.getAttribute('data-value') === 'deep' && !document.querySelector('[aria-label="Model for Sprout"]').disabled)
    await page.waitForFunction(() => document.querySelector('.tt-default-chip[aria-label="Quill"]')?.getAttribute('aria-pressed') === 'true')
    await defaults.getByRole('button', { name: 'Sprout', exact: true }).click()
    refuseModel = true
    await chooseThreadOption(page, 'Model for Sprout', 'swift')
    await page.getByRole('alert').filter({ hasText: 'This model is not available' }).waitFor()
    assert.equal(await page.getByRole('button', { name: 'Model for Sprout', exact: true }).getAttribute('data-value'), 'deep')
    refuseModel = false
    await chooseThreadOption(page, 'Provider for Sprout', 'codex')
    await chooseThreadOption(page, 'Provider for Sprout', 'claude-code')
    await page.waitForFunction(() => document.querySelector('[aria-label="Model for Sprout"]')?.getAttribute('data-value') === '' && !document.querySelector('[aria-label="Model for Sprout"]').disabled)
    await page.locator('.tt-details-scroll').evaluate((node) => { node.scrollTop = 0 })
    await shot('details')
    await rename.click(); await shot('details-rename'); await field.press('Escape')
    await page.getByRole('button', { name: 'Chat', exact: true }).click()
    await page.locator('#draft[contenteditable=true]').fill('A thought for @quill')
    await shot('mentions-default-mark')
    await page.locator('#draft').fill('')
    emit(id, { type: 'turn.started', persona: 'sprout' })
    emit(id, { type: 'message.delta', persona: 'sprout', draft: 'I am checking the notes.' })
    await page.getByText('I am checking the notes.', { exact: true }).waitFor()
    emit(id, { type: 'turn.step', persona: 'sprout', label: 'Waiting on a background task: Read the notes', waiting: true })
    await page.locator('.tt-typing').filter({ hasText: 'Waiting on a background task' }).waitFor()
    await shot('waiting')
    deleteBusy = true
    await page.getByTitle('Thread details', { exact: true }).click()
    await page.getByRole('button', { name: 'Delete thread', exact: true }).click()
    await page.getByRole('button', { name: 'Delete permanently', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: 'Wait for Sprout to finish, then delete.' }).waitFor()
    await shot('delete-wait')
    deleteBusy = false
    emit(id, { type: 'turn.finished', persona: 'sprout' })
    await page.getByRole('button', { name: 'Keep thread', exact: true }).click()
    await page.getByRole('button', { name: 'Chat', exact: true }).click()
    await page.waitForFunction(() => document.querySelector('.tt-stream')?.getAttribute('aria-busy') === 'false')
    const text = 'The full text before the tool is retained. '.repeat(8)
    const durable = { id: 'm2000', author: 'sprout', kind: 'message', text: 'A clear final answer.', segments: [{ text }, { did: [{ label: 'Read the shared notes' }] }, { text: 'An intermediate answer.' }, { did: [{ label: 'Checked the details' }] }], did: [{ label: 'Said: shortened' }] }
    extras.set(id, [durable]); emit(id, { type: 'message.done', message: durable })
    await page.getByText(text.trim(), { exact: true }).waitFor()
    await page.reload()
    await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click()
    await page.getByText(text.trim(), { exact: true }).waitFor()
    await page.getByText('An intermediate answer.', { exact: true }).waitFor()
    assert.equal(await page.getByText(text.trim(), { exact: true }).count(), 1)
    await shot('segments-reloaded')
    await page.getByRole('button', { name: /Board/ }).first().click()
    await shot('board')
    if (width === 390) await page.getByRole('button', { name: 'Close board', exact: true }).filter({ hasNot: page.locator('.tt-board-scrim') }).click()
    if (width === 390) await page.getByRole('button', { name: 'Back to threads', exact: true }).click()
    listUp = false
    for (const res of listStreams) res.end()
    await page.waitForTimeout(300)
    await f.api('PATCH', '/threads/small-details', { title: 'Updated during fallback' })
    await page.clock.fastForward(16000)
    await page.locator('.tt-row').filter({ hasText: 'Updated during fallback' }).waitFor()
    listUp = true
    await page.clock.fastForward(16000)
    await page.waitForTimeout(200)
    await f.api('PATCH', '/threads/small-details', { title: 'The small details that make a difference' })
    await page.locator('.tt-row').filter({ hasText: 'The small details that make a difference' }).waitFor()
    await page.route('**/api/health', (route) => route.fulfill({ status: 401, contentType: 'application/json', body: '{"error":"Sign in again"}' }))
    await page.evaluate(async () => { const { authenticatedFetch } = await import('/auth.js'); await authenticatedFetch('/api/health').catch(() => {}) })
    await page.getByRole('link', { name: 'Sign in again', exact: true }).waitFor()
    await shot('sign-in-again')
    assert.deepEqual(errors, [])
    results.push({ width, theme, inlineRenameEnterBlurEscape: true, defaultChips: true, modelsAndRefusal: true, liveListWithoutPoll: true, fallbackRecovery: true, waiting: true, finished: true, deletion409: true, durableReload: true, errors })
    console.log(`Checked round two at ${width}px ${theme}`)
    await context.close()
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
} catch (e) {
  for (const c of browser.contexts()) for (const p of c.pages()) { console.error(await p.locator('body').innerText()); await p.screenshot({ path: path.join(out, 'failure.png') }) }
  throw e
} finally { await browser.close(); await closing(app, broker)() }
