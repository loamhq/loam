// Run once per fresh, connected loopback install. LOAM_PLAYWRIGHT and
// LOAM_CHROME select the browser. FIRST_MINUTE_EVIDENCE receives the PNGs.
// node test/first-minute-browser.mjs http://127.0.0.1:PORT phone|desktop light|dark
import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'

const [address, device, mode] = process.argv.slice(2)
const origin = new URL(address)
assert.equal(origin.hostname, '127.0.0.1')
assert.ok(['phone', 'desktop'].includes(device) && ['light', 'dark'].includes(mode))
const out = process.env.FIRST_MINUTE_EVIDENCE
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const phone = device === 'phone'
const options = { viewport: { width: phone ? 390 : 1440, height: phone ? 844 : 960 }, colorScheme: mode,
  isMobile: phone, hasTouch: phone, reducedMotion: 'reduce', serviceWorkers: 'block' }
try {
  const page = await browser.newPage(options)
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const caption = async (name, word, title) => {
    const control = page.getByRole('button', { name, exact: true }).first()
    await control.waitFor()
    assert.equal(await control.getAttribute('data-caption'), word, name)
    if (title) assert.equal(await control.getAttribute('title'), title, name)
  }
  const checkList = async () => {
    for (const name of ['New thread', 'Search threads', 'Thread list actions']) {
      await caption(name, null, name)
    }
  }
  const shot = async name => {
    await page.evaluate(() => document.fonts.ready)
    await page.waitForFunction(theme => document.documentElement.dataset.theme === theme, mode)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    await page.screenshot({ path: join(out, name + '-' + device + '-' + mode + '.png'), animations: 'disabled' })
  }
  await page.goto(origin.href)
  const list = page.locator('.tt-row').filter({ hasText: 'Hello' })
  if (phone) {
    await list.locator('.tt-unread').waitFor()
    assert.equal(await page.locator('.tt-stream:visible').count(), 0)
    const listing = await (await page.request.get(new URL('api/threads', origin).href)).json()
    assert.equal(listing.threads.find(t => t.hello).unread, 1)
    await checkList()
    await caption('Settings', 'Settings', 'Settings')
    assert.deepEqual(await page.locator('.navpill .navbtn').allTextContents(), ['Chats', 'Files', 'Apps'])
    await shot('first-screen')
    await list.click()
  }
  const diagram = page.getByRole('img', { name: /^Chats\. Talk with your agents\./ })
  await diagram.waitFor()
  assert.ok((await page.request.get(new URL('three-places.svg', origin).href)).ok())
  await caption('Board', 'Board', 'Board')
  assert.equal(await page.getByTitle('Thread details', { exact: true }).getAttribute('data-caption'), 'Details')
  await caption('Branch into a new thread', 'Branch')
  if (!phone) await caption('Settings', 'Settings', 'Settings')
  for (const name of ['Thread actions', 'Attach a file', 'Mention an agent', 'Send']) {
    await caption(name, null, name)
  }
  for (const name of ['Reply', 'Copy']) await caption(name, null, phone ? null : name)
  if (phone) {
    assert.equal(await page.locator('.nav').count(), 0)
    const back = page.locator('.tt-head button[title^="Back"]')
    await back.waitFor()
    assert.ok(await back.getAttribute('aria-label'))
    assert.equal(await back.getAttribute('data-caption'), null)
  } else {
    assert.deepEqual(await page.locator('.rail-tabs .railtab').allTextContents(), ['Chats', 'Files', 'Apps'])
    await checkList()
    await caption('Activity', 'Activity', 'Activity')
    await caption('Hide sidebar for a focused view', 'Hide list', 'Hide sidebar for a focused view')
  }
  const board = page.getByRole('button', { name: 'Board', exact: true }).first()
  if (!phone) await shot('first-screen')
  await shot('hello')
  await board.click()
  await page.locator('.tt-board').getByText('Things to try', { exact: true }).first().waitFor()
  await page.getByRole('button', { name: 'Close board', exact: true }).last().click()
  assert.equal(await board.getAttribute('data-caption'), null)
  await shot('board-icon-used')
  const other = await browser.newPage(options)
  await other.goto(page.url())
  const nextBoard = other.getByRole('button', { name: 'Board', exact: true }).first()
  await nextBoard.waitFor()
  assert.equal(await nextBoard.getAttribute('data-caption'), null)
  if (phone) await other.locator('.tt-head button[title^="Back"]').click()
  assert.equal(await other.getByRole('button', { name: 'Settings', exact: true }).first().getAttribute('data-caption'), 'Settings')
  assert.deepEqual(errors, [])
  console.log('PASS', device, mode, 'landing, diagram, captions and accessible controls, tab names, Board dismissal across browsers')
} finally { await browser.close() }
