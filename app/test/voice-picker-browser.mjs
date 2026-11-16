// Real Chromium layout and native playback of a fixture tone. The tone proves
// playback/lifecycle only; it is never shipped or described as a voice sample.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { profileHost } from './agent-profile-host.mjs'
import { PREBUILT_VOICES } from '../public/plugins/read-aloud/page/document-speech.js'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.VOICE_EVIDENCE || `${process.env.TMPDIR}/voice-picker`
await mkdir(out, { recursive: true })
const app = await profileHost(), reports = []
const wav = Buffer.alloc(44 + 24000 * 4 * 2)
wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(24000, 24); wav.writeUInt32LE(48000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(wav.length - 44, 40)
for (let i = 0; i < 24000 * 4; i++) wav.writeInt16LE(Math.round(Math.sin(i / 24000 * Math.PI * 440) * 1000), 44 + i * 2)
for (const id of PREBUILT_VOICES) app.routes[`GET /v1/plugins/read-aloud/files/samples/${id}.wav`] = (req, res) => { res.writeHead(200, { 'content-type': 'audio/wav' }); res.end(wav) }
const source = await readFile('public/app.js', 'utf8')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, serviceWorkers: 'block' })
    const page = await context.newPage(), errors = [], requests = []
    page.on('pageerror', e => errors.push(e.message))
    page.on('request', req => requests.push({ method: req.method(), url: req.url() }))
    await page.route('**/app.js*', r => r.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__profile={state,profiles,settingsProfiles,goProfile,goSettings,schedule,pluginLoader};' }))
    await page.addInitScript(() => {
      globalThis.__samples = []
      const NativeAudio = Audio
      globalThis.Audio = function (...args) { const a = new NativeAudio(...args); __samples.push(a); return a }
    })
    await page.goto(app.url)
    await page.waitForFunction(() => globalThis.__profile?.state.agentsReady && __profile.pluginLoader.st.loaded)
    await page.evaluate(mode => { localStorage.setItem('loam-ui.theme', JSON.stringify({ mode })); __profile.goProfile('sprout') }, mode)
    await page.getByRole('tab', { name: 'Settings', exact: true }).click()
    await page.getByLabel('Voice', { exact: true }).click()
    await page.getByRole('dialog', { name: 'Choose a voice', exact: true }).waitFor()
    assert.equal(await page.locator('.voice-picker-choice').count(), 30)
    await page.getByText('Choose a voice. Audio samples haven’t been recorded yet.', { exact: true }).waitFor()
    assert.equal(await page.locator('.voice-picker-play:disabled').count(), 30)
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${out}/unrecorded-${width}-${mode}.png` })
    await page.keyboard.press('Escape')
    // Reload the plugin module with a test-only populated sample index.
    await page.route('**/voice-samples.js', r => r.fulfill({ contentType: 'text/javascript', body: `export const sampleText='Fixture tone'; export const samples=${JSON.stringify(Object.fromEntries(PREBUILT_VOICES.map(id => [id, { file: `../samples/${id}.wav`, duration_s: 4 }])))};` }))
    await page.reload()
    await page.waitForFunction(() => globalThis.__profile?.pluginLoader.st.loaded)
    await page.evaluate(() => __profile.goProfile('sprout'))
    await page.getByRole('tab', { name: 'Settings', exact: true }).click()
    await page.getByLabel('Voice', { exact: true }).click()
    const dialog = page.getByRole('dialog', { name: 'Choose a voice', exact: true })
    const play = page.getByRole('button', { name: 'Play Charon sample', exact: true })
    await play.click()
    await page.waitForFunction(() => __samples.at(-1)?.currentTime > 0.05 && !__samples.at(-1).paused)
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${out}/playing-${width}-${mode}.png` })
    await page.getByRole('button', { name: 'Stop Charon sample', exact: true }).click()
    assert.equal(await page.evaluate(() => __samples.at(-1).paused), true)
    await play.click()
    await page.getByRole('button', { name: 'Play Kore sample', exact: true }).click()
    assert.equal(await page.evaluate(() => __samples.at(-2).paused), true)
    await page.getByRole('button', { name: 'Choose Kore · Female', exact: true }).click()
    await dialog.waitFor({ state: 'detached' })
    assert.equal(app.voices.sprout, 'Kore')
    assert.equal(await page.evaluate(() => __samples.every(a => a.paused)), true)
    await page.getByLabel('Voice', { exact: true }).click()
    assert.equal(await page.getByRole('button', { name: 'Choose Kore · Female', exact: true }).getAttribute('aria-pressed'), 'true')
    const last = page.getByRole('button', { name: 'Play Sulafat sample', exact: true })
    await last.click()
    await page.waitForTimeout(300)
    const bounds = await dialog.boundingBox(), lastBounds = await last.boundingBox()
    assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= width + 1 && bounds.y + bounds.height <= 901)
    assert.ok(lastBounds.y + lastBounds.height <= bounds.y + bounds.height)
    assert.equal(await dialog.evaluate(node => node.scrollTop), 0)
    const heading = await dialog.locator('.surface-heading').boundingBox()
    assert.ok(heading.y >= bounds.y && heading.y + heading.height <= bounds.y + bounds.height)
    await page.screenshot({ path: `${out}/last-voice-${width}-${mode}.png` })
    await page.keyboard.press('Escape')
    await dialog.waitFor({ state: 'detached' })
    assert.equal(await page.evaluate(() => __samples.every(a => a.paused)), true)
    await page.getByLabel('Voice', { exact: true }).click()
    await page.getByRole('button', { name: 'Play Charon sample', exact: true }).click()
    await page.evaluate(() => __profile.pluginLoader.toggle('read-aloud', false))
    await dialog.waitFor({ state: 'detached' })
    assert.equal(await page.evaluate(() => __samples.every(a => a.paused)), true)
    await page.evaluate(() => __profile.pluginLoader.toggle('read-aloud', true))
    // The persona route writes the same bare value and the reloaded UI reads it.
    const response = await page.request.patch(`${app.url}/api/plugins/read-aloud/agents/sprout`, { data: { values: { voice: 'Charon' } } })
    assert.equal(response.status(), 200)
    await page.reload()
    await page.waitForFunction(() => globalThis.__profile?.pluginLoader.st.loaded)
    await page.evaluate(() => __profile.goProfile('sprout'))
    await page.getByRole('tab', { name: 'Settings', exact: true }).click()
    await page.getByLabel('Voice', { exact: true }).waitFor()
    assert.match(await page.getByLabel('Voice', { exact: true }).innerText(), /Charon · Male/)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    assert.deepEqual(errors, [])
    assert.equal(requests.filter(r => r.method === 'POST' && /\/speech(?:\?|$)/.test(r.url)).length, 0)
    assert.equal(requests.filter(r => !r.url.startsWith(app.url)).length, 0)
    reports.push({ width, mode, bounds, errors, checks: '30 gender labels; absent-recording state; native tone playback/toggle/switch; bare-id save; selected tick; last-row scroll; Escape/disable stop; persona PATCH/reload; no synthesis/external request or overflow' })
    await context.close()
  }
  await writeFile(`${out}/browser.json`, JSON.stringify(reports, null, 2) + '\n')
  console.log(JSON.stringify(reports, null, 2))
} finally { await browser.close(); await app.close() }
