// Real page/relay with synthetic host records. No deployment or provider call.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { pluginHostFixture } from './plugin-host-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.PLUGIN_PREFERENCES_EVIDENCE || path.join(process.env.TMPDIR, 'plugin-preferences')
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] })
const source = await readFile('public/app.js', 'utf8'), results = []
const before = {
  'loam-ui.read-aloud': { voice: 'Kore', describeTablesAndDiagrams: false },
  'loam-ui.speech': { listenFrom: 80, briefFrom: 350, rate: 1.5 },
  'loam-ui.dictation': { mode: 'record', polish: 'full', maxMinutes: 9, deviceId: 'studio', terms: ['Previous term'] },
}
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await pluginHostFixture({ mode })
    app.setRows(app.rows().filter(row => row.layer === 'shipped').map(row => ({ ...row, effective: true, ...(row.id === 'read-aloud' ? { on: true } : {}) })))
    const secretReads = [], json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    for (const id of ['read-aloud', 'dictation']) app.routes[`GET /v1/plugins/${id}/secrets`] = (req, res) => {
      secretReads.push(id); json(res, { secrets: [{ name: 'GEMINI_API_KEY', label: 'Gemini API key', set: true, source: 'box', checked: { ok: true, why: 'Key is ready.' } }] })
    }
    app.routes['GET /v1/dictation-terms'] = (req, res) => json(res, { path: 'Notes/Dictation terms.md', obsidian_url: 'obsidian://open?vault=example&file=Notes%2FDictation%20terms.md', text: '# Terms\n\nExample term', modified: '2026-11-15T12:00:00Z' })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, reducedMotion: 'reduce', isMobile: width === 390, hasTouch: width === 390, serviceWorkers: 'block' })
    await context.addInitScript(values => {
      for (const [key, value] of Object.entries(values)) if (localStorage.getItem(key) === null) localStorage.setItem(key, JSON.stringify(value))
      navigator.mediaDevices.enumerateDevices = async () => [{ kind: 'audioinput', deviceId: 'studio', label: 'Studio microphone' }]
    }, before)
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__preferences={pluginLoader,pluginSettingsUI,registry,goSettings,state};' }))
    await context.route('**/api/health', route => route.fulfill({ json: { configured: true, ok: true, speech: true } }))
    const page = await context.newPage(), errors = [], captures = [], requests = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('request', request => requests.push(new URL(request.url()).pathname))
    page.setDefaultTimeout(15000)
    const label = `${width}-${mode}`
    const shot = async name => {
      await page.evaluate(() => document.fonts.ready)
      await page.waitForTimeout(160)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name} viewport fits`)
      await page.screenshot({ path: path.join(out, `${name}-${label}.png`), animations: 'disabled' })
      captures.push(name)
    }
    const held = key => page.evaluate(key => JSON.parse(localStorage.getItem(key)), key)
    const detail = async name => {
      await page.getByRole('button', { name: `About ${name}`, exact: true }).click()
      await page.getByRole('button', { name: 'Back to Plugins', exact: true }).waitFor()
      await page.waitForFunction(() => !__preferences.pluginSettingsUI.st.opening)
      await page.waitForFunction(() => !__preferences.pluginSettingsUI.st.focusDetail)
      const heading = await page.locator('.settings .sheet-title').last().boundingBox()
      assert.ok(heading && heading.y > 0 && heading.y < 350, 'plugin heading is visible on entry')
    }
    const back = async () => { await page.getByRole('button', { name: 'Back to Plugins', exact: true }).click() }
    const toggle = async (name, on) => {
      await page.getByRole('switch', { name: `Enable ${name}`, exact: true }).click()
      await page.waitForFunction(([id, on]) => __preferences.pluginLoader.st.rows.some(row => row.id === id && row.effective && row.on === on) && __preferences.pluginLoader.st.writing.size === 0, [name === 'Read aloud' ? 'read-aloud' : 'dictation', on])
    }
    try {
      await page.goto(app.url)
      await page.waitForFunction(() => globalThis.__preferences?.pluginLoader.st.loaded)
      await page.locator(width === 390 ? '.settings-utility' : '.railround[aria-label="Settings"]').click()
      await page.locator('.settings:popover-open').waitFor()
      assert.equal(await page.locator('.settings').getByRole('button', { name: /^Voice/ }).count(), 0)
      await shot('settings')
      await page.evaluate(() => __preferences.goSettings('plugins'))
      await page.getByRole('button', { name: 'About Read aloud', exact: true }).waitFor()
      assert.equal(await page.getByRole('switch', { name: 'Enable Dictation', exact: true }).getAttribute('aria-checked'), 'false')
      await shot('plugins')
      await detail('Read aloud')
      assert.equal(await page.locator('#files-voice .setrow-value').innerText(), 'Kore')
      assert.equal(await page.getByRole('switch', { name: 'Describe tables and diagrams' }).getAttribute('aria-checked'), 'false')
      assert.deepEqual(await held('loam-ui.read-aloud'), before['loam-ui.read-aloud'])
      await page.getByText('Key is ready.', { exact: true }).waitFor()
      assert.equal(await page.locator('.plugin-secret-settings').count(), 1)
      await shot('read-aloud-on')
      await page.locator('#files-voice').click()
      await page.getByRole('option', { name: 'Puck', exact: true }).click()
      await page.getByRole('switch', { name: 'Describe tables and diagrams' }).click()
      const readingAfter = { voice: 'Puck', describeTablesAndDiagrams: true }
      assert.deepEqual(await held('loam-ui.read-aloud'), readingAfter)
      await toggle('Read aloud', false)
      assert.equal(await page.locator('.plugin-preferences,.plugin-secret-settings').count(), 0)
      await shot('read-aloud-off')
      await toggle('Read aloud', true)
      await page.locator('#files-voice').waitFor()
      assert.equal(await page.locator('#files-voice .setrow-value').innerText(), 'Puck')
      assert.deepEqual(await held('loam-ui.read-aloud'), readingAfter)
      await back(); await detail('Dictation')
      assert.equal(await page.locator('.plugin-preferences,.plugin-secret-settings').count(), 0)
      assert.equal(secretReads.includes('dictation'), false, 'off plugin does not even load key metadata')
      await shot('dictation-off')
      await toggle('Dictation', true)
      await page.getByRole('button', { name: 'Studio microphone', exact: true }).waitFor()
      await page.getByText('Notes/Dictation terms.md', { exact: true }).waitFor()
      assert.equal(await page.locator('.stepvalue').innerText(), '9 min')
      assert.equal(await page.getByRole('button', { name: 'Full', exact: true }).getAttribute('aria-pressed'), 'true')
      assert.deepEqual(await held('loam-ui.dictation'), before['loam-ui.dictation'])
      await shot('dictation-on')
      await page.getByRole('button', { name: 'Longer', exact: true }).click()
      await page.getByRole('button', { name: 'Light', exact: true }).click()
      const dictationAfter = { ...before['loam-ui.dictation'], maxMinutes: 10, polish: 'light' }
      assert.deepEqual(await held('loam-ui.dictation'), dictationAfter)
      await toggle('Dictation', false)
      assert.equal(await page.locator('.plugin-preferences,.plugin-secret-settings').count(), 0)
      await toggle('Dictation', true)
      await page.getByRole('button', { name: 'Studio microphone', exact: true }).waitFor()
      assert.equal(await page.locator('.stepvalue').innerText(), '10 min')
      assert.deepEqual(await held('loam-ui.dictation'), dictationAfter)
      await page.reload()
      await page.waitForFunction(() => globalThis.__preferences?.pluginLoader.st.loaded)
      for (const [key, expected] of Object.entries({ ...before, 'loam-ui.read-aloud': readingAfter, 'loam-ui.dictation': dictationAfter })) assert.deepEqual(await held(key), expected)
      await page.evaluate(() => __preferences.goSettings('voice'))
      await page.getByRole('button', { name: 'About Read aloud', exact: true }).waitFor()
      assert.equal(await page.locator('.settings .sheet-title').last().innerText(), 'Plugins', 'legacy Voice resolves to Plugins')
      await page.evaluate(async () => { await __preferences.pluginLoader.toggle('read-aloud', false); await __preferences.pluginLoader.toggle('dictation', false) })
      requests.length = 0
      await page.reload()
      await page.waitForFunction(() => globalThis.__preferences?.pluginLoader.st.loaded)
      assert.equal(requests.some(url => /\/(?:plugins|api\/plugins)\/(dictation|read-aloud)\/.*\.(js|css)$/.test(url)), false)
      assert.deepEqual(await page.evaluate(() => [...__preferences.pluginLoader.st.failures]), [])
      assert.deepEqual(errors, [])
      results.push({ width, mode, captures, before, after: { reading: readingAfter, dictation: dictationAfter }, disabledImports: 0, errors })
      console.log(`PASS ${label}: no Voice; plugin controls gated; old values, edits, off/on and reload preserved; no disabled imports or page errors`)
    } catch (error) {
      await page.screenshot({ path: path.join(out, `failure-${label}.png`) })
      console.error(errors)
      throw error
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ browser: browser.version(), environment: 'loopback fixture; not staging', results }, null, 2) + '\n')
} finally { await browser.close() }
