// Real Chromium and production UI, isolated broker/speech fixtures. No paid calls.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { pluginHostFixture } from './plugin-host-fixture.mjs'
import { filesHost } from './files-host.js'
import { row, readAnswer } from './fixtures/files-contract.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const output = process.env.DESCRIPTIONS_EVIDENCE
if (!output) throw new Error('Set DESCRIPTIONS_EVIDENCE to the capture directory')
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8')
const text = '# Plans\n\n' + 'Choose the plan that fits the work. '.repeat(40) + '\n\n| Plan | Offline |\n| --- | --- |\n| Basic | No |\n| Pro | Yes |\n\n```mermaid\nflowchart LR\nBrowser --> Relay --> Host\n```'
const results = []
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await pluginHostFixture({ mode })
    app.setRows(app.rows().filter(row => row.layer === 'shipped').map(row => ({ ...row, effective: true, on: row.id === 'read-aloud' || row.on })))
    Object.assign(app.routes, filesHost().routes)
    app.routes['GET /v1/plugins/read-aloud/secrets'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ secrets: [{ name: 'GEMINI_API_KEY', label: 'Gemini API key', set: true, source: 'plugin' }] })) }
    app.routes['GET /v1/files/read'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(readAnswer(row('vault', 'Notes/plan.md'), text, 'sha256:fixture'))) }
    const record = await app.f.api('GET', '/threads/a-quieter-week')
    record.messages.push({ id: 'describe-reply', author: 'sprout', kind: 'message', text })
    app.routes['GET /v1/threads/a-quieter-week'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(record)) }
    const context = await browser.newContext({ viewport: { width, height: 1000 }, colorScheme: mode, reducedMotion: 'reduce' })
    const page = await context.newPage(), errors = [], requests = []
    page.on('pageerror', error => errors.push(error.message))
    page.setDefaultTimeout(12000)
    await context.addInitScript(() => { localStorage.setItem('loam-ui.read-aloud', JSON.stringify({ voice: 'Kore' })) })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__desc={pluginLoader,goSettings,closeSheet,coreScope,page};' }))
    await context.route('**/api/health', route => route.fulfill({ json: { ok: true, configured: true, speech: true } }))
    await context.route('**/api/speech/lookup', route => route.fulfill({ json: { status: 'none' } }))
    await context.route('**/api/speech', route => {
      requests.push(route.request().postDataJSON())
      return route.fulfill({ status: 202, json: { id: String(requests.length).padStart(32, '0'), status: 'ready', duration_s: 90, url: '/api/speech/fixture/audio.mp3' } })
    })
    const openSettings = async () => {
      await page.evaluate(() => __desc.goSettings('plugins'))
      await page.locator('button[aria-label="About Read aloud"],#read-aloud-describe-label').first().waitFor()
      const about = page.getByRole('button', { name: 'About Read aloud', exact: true })
      if (await about.count()) await about.click()
      await page.getByRole('switch', { name: 'Describe tables and diagrams' }).waitFor()
      await page.locator('.plugin-secret-settings input').waitFor()
    }
    const capture = async name => {
      await page.evaluate(() => document.fonts.ready)
      const control = page.getByRole('switch', { name: 'Describe tables and diagrams' })
      await control.evaluate(el => {
        const pane = el.closest('.app-modal-body')
        if (pane) pane.scrollTop = 0
        else el.scrollIntoView({ block: 'nearest' })
      })
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      assert.equal(await control.evaluate(el => el.getBoundingClientRect().height >= 44), true)
      await page.screenshot({ path: path.join(output, `${name}-${width}-${mode}.png`), animations: 'disabled' })
    }
    const prepareReply = async enabled => {
      await page.evaluate(() => { __desc.closeSheet(); __desc.coreScope.commands.run('core.chats') })
      await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click()
      await page.locator('.speakbtn').last().click()
      const response = page.waitForResponse(res => res.url().endsWith('/api/speech'))
      await page.locator('.speakmenu .setrow').first().click()
      await response
      await page.waitForFunction(() => document.querySelector('.player-play'))
      assert.equal(requests.at(-1).describe_tables_and_diagrams, enabled)
      assert.equal(requests.at(-1).text, text)
    }
    const prepareFile = async enabled => {
      await page.evaluate(() => { __desc.closeSheet(); __desc.page.goFiles('vault', 'Notes/plan.md') })
      await page.locator('.fs-speak').click()
      const slider = page.locator('.file-speech-slider')
      await slider.evaluate(el => { el.value = el.max; el.dispatchEvent(new Event('input', { bubbles: true })) })
      await page.getByRole('button', { name: 'Prepare', exact: true }).click()
      await page.locator('.file-speech-action[data-phase=ready]').waitFor()
      assert.equal(requests.at(-1).source, 'document')
      assert.equal(requests.at(-1).describe_tables_and_diagrams, enabled)
      assert.equal(requests.at(-1).text, text)
    }
    try {
      await page.goto(app.url)
      await page.waitForFunction(() => globalThis.__desc?.pluginLoader.st.loaded)
      await openSettings()
      assert.equal(await page.getByRole('switch', { name: 'Describe tables and diagrams' }).getAttribute('aria-checked'), 'true')
      await capture('on')
      await prepareReply(true)
      await prepareFile(true)
      await openSettings()
      await page.getByRole('switch', { name: 'Describe tables and diagrams' }).click()
      await page.locator('button[aria-labelledby="read-aloud-describe-label"][aria-checked="false"]').waitFor()
      assert.equal(await page.getByRole('switch', { name: 'Describe tables and diagrams' }).getAttribute('aria-checked'), 'false')
      await capture('off')
      await page.locator('#files-voice').click()
      await page.getByRole('option', { name: 'Aoede', exact: true }).click()
      assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('loam-ui.read-aloud'))), { voice: 'Aoede', describeTablesAndDiagrams: false })
      await prepareReply(false)
      await prepareFile(false)
      assert.equal(requests.length, 4, 'toggling must select new takes for the same reply/file')
      await page.evaluate(() => __desc.goSettings('voice'))
      await page.locator('button[aria-labelledby="read-aloud-describe-label"][aria-checked="false"]').waitFor()
      await capture('voice-off')
      assert.deepEqual(errors, [])
      results.push({ width, mode, requests: requests.map(({ source, mode, describe_tables_and_diagrams }) => ({ source: source ?? 'reply', mode, describe_tables_and_diagrams })), errors })
      console.log(`PASS ${width} ${mode}: existing preferences default on; plugin toggle, voice preservation, reply/Files on and off; no page errors`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(output, 'browser.json'), JSON.stringify({ browser: browser.version(), results }, null, 2) + '\n')
} finally { await browser.close() }
