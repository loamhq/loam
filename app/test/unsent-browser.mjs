// Real Chromium with the real relay and a cap-refusing broker. Explicit output
// keeps fixture paints out of the repository. UNSENT_PUBLIC permits a baseline.
import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { mkdir, writeFile } from 'node:fs/promises'
import { once } from 'node:events'
import { createApp } from '../src/server.js'
import { readConfig } from '../src/config.js'
import { unsentHost, capReason, storedFile, resource } from './unsent-host.js'
import { TOKEN, shut } from './harness.js'
import { filesHost } from './files-host.js'
import { row, readAnswer } from './fixtures/files-contract.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright-core')
const output = process.env.UNSENT_EVIDENCE
if (!output) throw new Error('Set UNSENT_EVIDENCE to a directory outside the repository')
const repo = fileURLToPath(new URL('..', import.meta.url))
const relative = path.relative(repo, path.resolve(output))
if (!relative || (!relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative))) throw new Error('Evidence must be outside the repository')
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
const measurements = []
const baseline = Boolean(process.env.UNSENT_PUBLIC)
const paint = (page, name) => page.screenshot({ path: path.join(output, name) })
try {
  for (const width of [1440, 390]) {
    const broker = await unsentHost()
    const { server } = createApp({ config: readConfig({ BROKER_URL: broker.url, BROKER_TOKEN: TOKEN, LOG_LEVEL: 'off' }), ...(baseline ? { publicDir: process.env.UNSENT_PUBLIC } : {}) })
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    const url = `http://127.0.0.1:${server.address().port}`
    const page = await browser.newPage({ viewport: { width, height: 900 }, isMobile: width === 390, hasTouch: width === 390 })
    const errors = []
    page.on('pageerror', (e) => errors.push(e.message))
    const message = 'Please review the plan and help me choose the next step.'
    await page.goto(url)
    // Start with host-stored attachments; no fake bytes enter the composer.
    await page.evaluate(({ storedFile, resource }) => localStorage.setItem('loam-ui.draft.cap-chat', JSON.stringify({ text: '', attachments: [storedFile], resources: [resource] })), { storedFile, resource })
    await page.locator('.srow').click()
    await page.locator('.msgs').getByText(/exit 143/).waitFor()
    await page.locator('#draft').fill(message)
    await page.locator('.roundsend').click()
    if (baseline) {
      await page.getByText(capReason, { exact: true }).first().waitFor()
      broker.freeSlot()
      await page.getByRole('button', { name: 'Pin this chat', exact: true }).click()
      await page.locator('.awaiting').waitFor()
      assert.equal(await page.locator('.say.user.pending').count(), 1)
      await paint(page, `before-cap-refusal-${width}.png`)
      measurements.push({ width, baseline: true, dimmedPending: true, falseWorking: true, messageRequests: broker.seen.filter((r) => r.path.endsWith('/messages')).length })
    } else {
      const unsent = page.locator('.unsent')
      const retry = async (route = '/api/sessions') => {
        const response = page.waitForResponse((r) => new URL(r.url()).pathname === route && r.request().method() === 'POST')
        await unsent.getByRole('button', { name: 'Resend', exact: true }).click()
        await (await response).finished()
        await page.waitForTimeout(100)
      }
      await unsent.waitFor()
      assert.ok((await unsent.innerText()).includes(capReason))
      assert.equal(await page.locator('.pending, .awaiting').count(), 0)
      await page.getByRole('button', { name: 'Pin this chat', exact: true }).click()
      assert.equal(await page.locator('.awaiting').count(), 0, 'clearing an unrelated banner started work')
      await paint(page, `unsent-${width}.png`)
      await retry()
      await unsent.waitFor()
      assert.equal(await unsent.count(), 1)
      // Reload really recreates the module; the page starts at its chat list.
      await page.reload()
      await page.locator('.srow').click()
      await unsent.waitFor()
      assert.equal(await page.locator('.say.user').filter({ hasText: message }).count(), 1)
      const attempts = broker.seen.filter((r) => r.method === 'POST' && r.path === '/v1/sessions').length
      assert.equal(attempts, 2, 'reload resent a message automatically')
      await unsent.getByRole('button', { name: 'Edit', exact: true }).click()
      await unsent.waitFor({ state: 'detached' })
      assert.equal(await page.locator('#draft').innerText(), message)
      assert.equal(await page.locator('.chip-name').filter({ hasText: storedFile.name }).count(), 1)
      assert.equal(await unsent.count(), 0)
      // The edited text and both attachment kinds survive a second reload.
      await page.reload()
      await page.locator('.srow').click()
      await page.locator('.msgs').getByText(/exit 143/).waitFor()
      assert.equal(await page.locator('#draft').innerText(), message)
      await page.locator('.roundsend').click()
      await unsent.waitFor()
      broker.freeSlot()
      broker.refuse(true)
      await retry('/api/sessions/cap-chat/messages')
      await unsent.waitFor()
      assert.equal(await page.locator('.pending, .awaiting').count(), 0)
      broker.refuse(false)
      broker.eventFirst(width === 1440)
      await retry('/api/sessions/cap-chat/messages')
      await page.locator('.awaiting').waitFor()
      assert.equal(await unsent.count(), 0)
      assert.equal(await page.locator('.say.user').filter({ hasText: message }).count(), 1)
      const request = JSON.parse(broker.seen.filter((r) => r.path.endsWith('/messages')).at(-1).body)
      assert.deepEqual(request, { text: message, attachments: [storedFile.path], resources: [{ root: resource.root, path: resource.path }] })
      broker.exit()
      await page.locator('.awaiting').waitFor({ state: 'detached' })
      const stored = await page.evaluate(() => localStorage.getItem('loam-ui.unsent.cap-chat'))
      assert.equal(stored, null)
      measurements.push({ width, baseline: false, reload: true, editWithAttachments: true, refusedResend: true, acceptedExactlyOnce: true, exitClearsWorking: true, errors })
    }
    assert.deepEqual(errors, [])
    await page.close()
    await shut(server)
    await shut(broker.server)
  }
  if (!baseline) {
    const { routes } = filesHost()
    routes['GET /v1/files/stat'] = (req, res, url) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(row('vault', url.searchParams.get('path')))) }
    routes['GET /v1/files/read'] = (req, res, url) => {
      const name = url.searchParams.get('path')
      const words = name.includes('short') ? 200 : 60000
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify(readAnswer(row('vault', name), Array(words).fill('word').join(' '), 'sha256:fixture')))
    }
    const { fakeBroker } = await import('./harness.js')
    const broker = await fakeBroker(routes)
    const { server } = createApp({ config: readConfig({ BROKER_URL: broker.url, BROKER_TOKEN: TOKEN, LOG_LEVEL: 'off' }), speech: { async status() { return { available: true } }, async peek() { return { status: 200, body: { status: 'none' } } }, stop() {} } })
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } })
      for (const note of ['short', 'long']) {
        await page.goto(`http://127.0.0.1:${server.address().port}/files/vault/Notes/${note}.md`)
        await page.locator('.fs-speak').click()
        await page.locator('.file-speech-slider').waitFor()
        await page.waitForTimeout(300)
        const labels = await page.locator('.file-speech-stops span').allTextContents()
        if (note === 'short') assert.deepEqual(labels, ['30 s', '1 min', 'Full'])
        else assert.ok(labels.slice(0, -1).every((label) => label.endsWith(' min')))
        const bounds = await page.locator('.file-speech-stops').evaluate((el) => [...el.children].map((s) => {
          const r = s.getBoundingClientRect(); const style = getComputedStyle(s)
          return { left: r.left, right: r.right, top: r.top + parseFloat(style.paddingTop), bottom: r.bottom }
        }))
        for (let i = 0; i < bounds.length; i++) for (let j = i + 1; j < bounds.length; j++) {
          const a = bounds[i], b = bounds[j]
          assert.ok(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top, 'stop text overlaps')
        }
        await paint(page, `minute-stops-${note}-${width}.png`)
        measurements.push({ width, note, labels, noOverlap: true })
      }
      await page.close()
    }
    await shut(server)
    await shut(broker.server)
  }
  await writeFile(path.join(output, baseline ? 'baseline.json' : 'checks.json'), JSON.stringify(measurements, null, 2) + '\n')
  console.log(JSON.stringify(measurements, null, 2))
} finally {
  await browser.close()
}
