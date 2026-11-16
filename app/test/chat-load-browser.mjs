// Real phone viewport, HTTP relay and Chromium network throttling. All data is
// synthetic; CHAT_LOAD_EVIDENCE names an output directory outside the repo.
import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { mkdir, writeFile } from 'node:fs/promises'
import { chatLoadHost, loadWarning } from './chat-load-host.js'
import { appAgainst, closing, until } from './harness.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright-core')
const output = process.env.CHAT_LOAD_EVIDENCE
if (!output) throw new Error('Set CHAT_LOAD_EVIDENCE outside the repository')
const relative = path.relative(fileURLToPath(new URL('..', import.meta.url)), path.resolve(output))
if (!relative || (!relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative))) throw new Error('Evidence must be outside the repository')
await mkdir(output, { recursive: true })
const broker = await chatLoadHost('large')
const app = await appAgainst(broker)
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
const result = { fixture: true, imageChars: 17000000, width: 390, downloadBytesPerSecond: 32768 }
try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto(app.url)
  await page.locator('.srow').waitFor()
  const cdp = await context.newCDPSession(page)
  await cdp.send('Network.enable')
  const received = new Map()
  const threadIds = new Set()
  cdp.on('Network.requestWillBeSent', ({ requestId, request }) => { if (request.url.includes('/load-fixture/events')) threadIds.add(requestId) })
  cdp.on('Network.dataReceived', ({ requestId, dataLength }) => { if (threadIds.has(requestId)) received.set(requestId, (received.get(requestId) ?? 0) + dataLength) })
  await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 150, downloadThroughput: result.downloadBytesPerSecond, uploadThroughput: 32768, connectionType: 'cellular3g' })
  await page.locator('.srow').click()
  await page.getByText(loadWarning, { exact: true }).waitFor({ timeout: 30000 })
  await page.locator('.thread-load').waitFor({ state: 'detached' })
  await page.getByRole('button', { name: 'Retry', exact: true }).waitFor()
  // Beyond the old watchdog's 25 s threshold: bytes still arriving must not
  // restart this same oversized frame from the last complete event forever.
  await page.waitForTimeout(11000)
  assert.equal(broker.requests().length, 1, 'the watchdog discarded a large event still arriving')
  assert.ok(!await page.locator('.msgs').innerText().then((text) => text.includes(broker.record.last_message)))
  result.bytesReceived = [...received.values()].reduce((a, b) => a + b, 0)
  assert.ok(result.bytesReceived > 0 && result.bytesReceived < result.imageChars)
  await page.screenshot({ path: path.join(output, 'phone-large-chat-stalled.png') })
  await page.locator('.thread-head .iconbtn').first().click()
  await page.locator('.srow .box').waitFor()
  await until(() => broker.marks().length > 0)
  assert.equal(broker.record.seen_seq, 2)
  result.afterLeaving = { seen_seq: broker.record.seen_seq, record_seq: broker.record.record_seq, marks: broker.marks() }
  await page.screenshot({ path: path.join(output, 'phone-left-unread.png') })

  // A server that closes cleanly before the end is also incomplete, even
  // though its record says exited. Retry uses only the two completed events.
  await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 })
  broker.mode('short')
  await page.locator('.srow').click()
  await page.getByText(loadWarning, { exact: true }).waitFor()
  await page.locator('.thread-load').waitFor({ state: 'detached' })
  // Park automatic recovery on an open partial stream, so it cannot finish
  // between enabling the small response and the person's explicit Retry tap.
  const beforeHold = broker.requests().length
  broker.mode('hold')
  await until(() => broker.requests().length > beforeHold)
  await page.waitForTimeout(500)
  broker.mode('complete')
  await page.getByRole('button', { name: 'Retry', exact: true }).click()
  await page.locator('.say.agent').filter({ hasText: broker.record.last_message }).waitFor()
  await page.getByText(loadWarning, { exact: true }).waitFor({ state: 'detached' })
  assert.equal(broker.requests().at(-1).search, '?from=2')
  await page.locator('.fold-head').click()
  await page.locator('.toolrow').click()
  assert.equal(await page.locator('.out').innerText(), 'Two pages.\n[image]\n[image]')
  await page.screenshot({ path: path.join(output, 'phone-retry-image-stubs.png') })
  await page.locator('.thread-head .iconbtn').first().click()
  await until(() => broker.record.seen_seq === 6)
  result.afterRetry = { seen_seq: broker.record.seen_seq, record_seq: broker.record.record_seq, lastRequest: broker.requests().at(-1).search, imageText: 'Two pages.\n[image]\n[image]' }
  assert.deepEqual(errors, [])
  result.pageErrors = errors
  await writeFile(path.join(output, 'results.json'), JSON.stringify(result, null, 2) + '\n')
  console.log(JSON.stringify(result, null, 2))
} finally {
  await browser.close()
  await closing(broker, app)()
}
