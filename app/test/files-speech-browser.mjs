// Optional real-browser check. No production dependencies: name an installed
// Playwright module and Chromium with LOAM_PLAYWRIGHT and LOAM_CHROME.
// Broker and speech answers are fixtures; this never generates paid audio.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { once } from 'node:events'
import { createApp } from '../src/server.js'
import { readConfig } from '../src/config.js'
import { fakeBroker, TOKEN, shut } from './harness.js'
import { filesHost } from './files-host.js'
import { row, readAnswer, listAnswer } from './fixtures/files-contract.js'
import { documentWords, documentStops } from '../../plugins/_listed/read-aloud/page/document-speech.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright-core')
const output = process.env.FILES_SPEECH_EVIDENCE || 'docs/evidence/files-read-aloud'
await mkdir(output, { recursive: true })
const short = Array.from({ length: 20 }, () => 'Please review this proposal and decide on the next step.').join('\n\n')
const long = '# Architecture proposal\n\n' + Array.from({ length: 1500 }, () => 'Please review this proposal and decide on the next step.').join('\n\n')
const notes = { 'Notes/short.md': short, 'Notes/long.md': long }
const { routes } = filesHost()
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
routes['GET /v1/files/stat'] = (req, res, url) => json(res, row('vault', url.searchParams.get('path')))
routes['GET /v1/files/read'] = (req, res, url) => {
  const name = url.searchParams.get('path')
  json(res, readAnswer(row('vault', name), notes[name], 'sha256:fixture'))
}
routes['GET /v1/files/list'] = (req, res) => json(res, listAnswer(row('vault', 'Notes'), Object.keys(notes).map((name) => row('vault', name))))
const broker = await fakeBroker(routes)
const requests = []
const jobs = new Map()
const idFor = (body) => createHash('sha256').update(JSON.stringify([body.text, body.mode, body.minutes, body.voice])).digest('hex').slice(0, 32)
const view = (id) => ({ id, status: 'ready', duration_s: 180, url: `/x/read-aloud/speech/${id}/audio.mp3`, brief_text: jobs.get(id)?.mode === 'brief' ? 'A fixture brief.' : null })
const speech = {
  async status() { return { available: true } },
  async peek(body) { const id = idFor(body); return { status: 200, body: jobs.has(id) ? view(id) : { id, status: 'none' } } },
  async request(body) { requests.push(body); const id = idFor(body); jobs.set(id, body); return { status: 202, body: { id, status: 'preparing', stage: 'voicing', progress: 0 } } },
  async lookup(id) { return view(id) },
  async audio() { return null },
  stop() {},
}
const { server } = createApp({ config: readConfig({ BROKER_URL: broker.url, BROKER_TOKEN: TOKEN, LOG_LEVEL: 'off' }), speech })
server.listen(0, '127.0.0.1')
await once(server, 'listening')
const url = `http://127.0.0.1:${server.address().port}`
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
const results = []
try {
  for (const width of [390, 1280]) {
    const page = await browser.newPage({ viewport: { width, height: 844 } })
    const errors = []
    page.on('pageerror', (err) => errors.push(err.message))
    // Track actual play calls without needing fixture MP3s or an audio device.
    await page.addInitScript(() => { globalThis.__plays = 0; HTMLMediaElement.prototype.play = function () { globalThis.__plays++; return Promise.resolve() } })
    for (const note of ['short', 'long']) {
      await page.goto(`${url}/files/vault/Notes/${note}.md`)
      await page.locator('.fs-speak').click()
      await page.locator('.file-speech-slider').waitFor()
      const labels = await page.locator('.file-speech-stops span').allTextContents()
      const count = documentWords(notes[`Notes/${note}.md`])
      assert.deepEqual(labels, documentStops(count).map((s) => s.short))
      assert.equal(await page.locator('.file-speech input[type=range]').count(), 1)
      const overflow = await page.locator('.sheet.file-speech').evaluate((el) => el.scrollWidth > el.clientWidth)
      assert.equal(overflow, false)
      if (note === 'long') {
        const at = documentStops(count).findIndex((s) => s.minutes === 3)
        await page.locator('.file-speech-slider').evaluate((el, at) => { el.value = at; el.dispatchEvent(new Event('input', { bubbles: true })) }, at)
        await page.locator('.file-speech-choice').filter({ hasText: '3 min' }).waitFor()
      }
      assert.equal(await page.locator('.file-speech-value:not(.previous) .file-speech-kind').textContent(), note === 'long' ? 'Document summary' : 'Word for word')
      assert.match(await page.locator('.file-speech-slider').getAttribute('aria-valuetext'), note === 'long' ? /Document summary/ : /Word for word/)
      await page.screenshot({ path: path.join(output, `${note}-${width}.png`) })
      results.push({ width, note, words: count, labels, overflow })
      if (width === 390) {
        await page.locator('.file-speech .primary').click()
        await page.locator('.file-speech-action[data-phase=ready]').waitFor()
        await page.locator('.file-speech-close').click()
        await page.locator('.sheet.file-speech').waitFor({ state: 'detached' })
        await page.locator('.player').waitFor()
        assert.equal(await page.evaluate(() => globalThis.__plays), 0, 'prepared audio auto-played')
        await page.locator('.player-play').waitFor()
        assert.equal(requests.at(-1).source, 'document')
        assert.equal(requests.at(-1).minutes, note === 'long' ? 3 : 'full')
        assert.equal(requests.at(-1).voice, 'Aoede')
        if (note === 'short') {
          await page.locator('.fs-back').click()
          await page.locator('.playerbar').waitFor()
          await page.locator('.playerbar-text').click()
          await page.locator('.fs-preview').waitFor()
          assert.ok((await page.locator('.fs-edtitle').textContent()).includes('short'))
        }
      }
    }
    // Reach Settings from the actual navigation, at each viewport.
    await page.goto(url)
    await page.getByRole('button', { name: 'Settings', exact: true }).first().click()
    if (width === 390) await page.locator('.setrow').filter({ hasText: 'Files voice' }).click()
    else await page.locator('.navrow').filter({ hasText: 'Read aloud' }).click()
    await page.locator('#files-voice').waitFor()
    assert.equal(await page.locator('#files-voice option').count(), 30)
    assert.equal(await page.locator('#files-voice').inputValue(), 'Aoede')
    await page.screenshot({ path: path.join(output, `settings-${width}.png`) })
    await page.locator('#files-voice').selectOption('Kore')
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('loam-ui.read-aloud'))), { voice: 'Kore' })
    await page.locator('.read-aloud-preview').click()
    await page.locator('.read-aloud-sample .player-play').waitFor()
    assert.equal(requests.at(-1).voice, 'Kore')
    assert.equal(requests.at(-1).text.includes('Here is a little time to listen'), true)
    assert.deepEqual(errors, [], 'browser exceptions')
    await page.close()
  }
  assert.equal(broker.seen.some((r) => r.method === 'POST' && r.path.startsWith('/v1/sessions')), false, 'document playback woke an agent')
  await writeFile(path.join(output, 'browser.json'), JSON.stringify({ browser: browser.version(), fixtures: true, results, requests: requests.map(({ source, mode, minutes, voice }) => ({ source, mode, minutes, voice })) }, null, 2) + '\n')
  console.log(JSON.stringify({ paints: results.length + 2, widths: [390, 1280], requests: requests.length, browser_errors: 0 }))
} finally {
  await browser.close()
  await shut(server)
  await shut(broker.server)
}
