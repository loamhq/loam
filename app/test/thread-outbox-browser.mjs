// Run with Playwright against the real page and a synthetic broker.
import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { until } from './harness.js'
import { oneProductFixture } from './one-product-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const out = process.env.THREAD_OUTBOX_EVIDENCE
const baseline = process.argv.includes('--baseline')
const thread = 'small-details'
const endpoint = `/api/threads/${thread}/messages`
const firstText = 'Keep the notes for Thursday.'
const secondText = 'Friday is confirmed.'
const matches = (request, text) => new URL(request.url()).pathname === endpoint &&
  request.method() === 'PUT' && request.postDataJSON().text === text
if (out) await mkdir(out, { recursive: true })

async function run(width, height, theme, freeze = false) {
  const app = await oneProductFixture()
  app.routes[`GET /v1/threads/${thread}`] = async (_, res, url) => {
    const record = await app.f.api('GET', `/threads/${thread}${url.search}`)
    res.writeHead(200, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ ...record, ordered_submissions: true }))
  }
  // Loopback uses HTTP/1.1. Leave connection slots for writes in both tabs.
  for (const route of ['GET /v1/threads/events', 'GET /v1/sessions/events']) {
    app.routes[route] = (_, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.end(': open\n\n')
    }
  }
  const context = await browser.newContext({ viewport: { width, height }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
  try {
    const first = await context.newPage()
    const second = await context.newPage()
    const errors = []
    for (const page of [first, second]) {
      page.setDefaultTimeout(baseline ? 4000 : 22000)
      page.on('pageerror', error => errors.push(error.message))
    }

    const attempts = []
    let letFirstThrough = false
    let cancelled = false
    const brokerRoute = `PUT /v1/threads/${thread}/messages`
    const answer = app.routes[brokerRoute]
    app.routes[brokerRoute] = (req, res, url, raw) => {
      const body = JSON.parse(raw)
      if (body.text === firstText) {
        attempts.push(body)
        if (attempts.length === 1) res.once('close', () => { cancelled = true })
        if (!letFirstThrough) return // Leave the socket open and send nothing.
      }
      return answer(req, res, url, raw)
    }

    const draft = page => page.locator('#draft')
    const send = page => page.locator('.tt-composer button[type=submit]')
    const open = async page => {
      await page.goto(`${app.url}/threads/${thread}`)
      await page.locator('[data-message-id="m3"]').waitFor()
      assert.equal(await page.locator('html').getAttribute('data-theme'), theme)
    }

    await open(first)
    await draft(first).fill(firstText)
    const [hung] = await Promise.all([first.waitForRequest(req => matches(req, firstText)), send(first).click()])
    await open(second)
    const lifecycle = freeze ? await context.newCDPSession(first) : null
    await lifecycle?.send('Page.setWebLifecycleState', { state: 'frozen' })
    await draft(second).fill(secondText)
    const responseFor = (text) => context.waitForEvent('response', {
      predicate: res => res.ok() && matches(res.request(), text), timeout: baseline ? 4000 : 22000,
    })
    if (baseline) {
      await send(second).click()
      await assert.rejects(responseFor(secondText), { name: 'TimeoutError' })
      const locks = await second.evaluate(() => navigator.locks.query())
      assert.ok(locks.held.some(lock => lock.name === 'loam-thread-sends'))
      assert.ok(locks.pending.some(lock => lock.name === 'loam-thread-sends'))
      assert.equal(attempts.length, 1)
      assert.equal(cancelled, false)
      console.log('Baseline reproduced: the first PUT held the Web Lock and the second tab waited behind it.')
      return
    }
    const [response] = await Promise.all([responseFor(secondText), send(second).click()]).catch(error => {
      const requests = app.broker.seen.filter(row => row.method === 'PUT').map(row => JSON.parse(row.body).text)
      throw new Error(`${width}px ${theme}${freeze ? ' frozen' : ''}: broker sends ${requests.join(', ')}; page errors ${errors.join(', ')}`, { cause: error })
    })
    if (!freeze) await until(() => cancelled)
    if (freeze) {
      assert.equal(response.request().frame().page(), second)
      await lifecycle.send('Page.setWebLifecycleState', { state: 'active' })
    }
    const accepted = await response.json()
    assert.ok(accepted.message.submission_id)
    const pending = second.locator('[data-message-id^="pending-"]').filter({ hasText: firstText })
    const retry = pending.getByRole('button', { name: 'Retry', exact: true })
    await pending.getByText('Not sent yet', { exact: true }).waitFor()
    await retry.waitFor()
    await second.locator(`[data-message-id="${accepted.message.id}"]`).waitFor()
    await draft(second).fill('The composer still works.')
    await second.locator('.tt-composer button[type=submit]:enabled').waitFor()

    if (out && !freeze) {
      await pending.scrollIntoViewIfNeeded()
      await second.evaluate(() => document.fonts.ready)
      assert.equal(await second.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      await second.screenshot({ path: path.join(out, `not-sent-${width}-${theme}.png`), animations: 'disabled' })
    }

    letFirstThrough = true
    const [retried] = await Promise.all([responseFor(firstText), freeze ? null : retry.click()])
    const recovered = await retried.json()
    assert.deepEqual(retried.request().postDataJSON(), hung.postDataJSON())
    assert.ok(attempts.length >= 2)
    for (const body of attempts) assert.deepEqual(body, hung.postDataJSON())
    for (const page of [first, second]) {
      await page.locator(`[data-message-id="${recovered.message.id}"]`).waitFor()
      await page.locator('[data-message-id^="pending-"]').filter({ hasText: firstText }).waitFor({ state: 'detached' })
      assert.equal(await page.getByText(firstText, { exact: true }).count(), 1)
    }
    assert.equal(await draft(second).evaluate(node => node.value), 'The composer still works.')
    assert.deepEqual(errors, [])
    console.log(`${width}px ${theme}: ${freeze ? 'second tab sent while the first tab was frozen, automatic retry kept the ID' : 'hung PUT aborted and second tab sent, Retry kept the ID'}`)
  } finally {
    await context.close()
    await app.close()
  }
}

try {
  const sizes = baseline ? [[390, 844, 'light']] : [
    [390, 844, 'light'], [390, 844, 'dark'], [1440, 900, 'light'], [1440, 900, 'dark'],
    [390, 844, 'light', true],
  ]
  await Promise.all(sizes.map(args => run(...args)))
} finally {
  await browser.close()
}
