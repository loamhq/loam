// Fixture staging only: real imports/styles and browser audio APIs, no host writes.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { pluginHostFixture } from './plugin-host-fixture.mjs'
import { filesHost } from './files-host.js'
import { row, readAnswer } from './fixtures/files-contract.js'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.OPENNESS_EVIDENCE
if (out) await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] })
const baseline = process.env.OPENNESS_BASELINE_PUBLIC
const source = await readFile(path.join(baseline || 'public', 'app.js'), 'utf8'), results = []
try {
for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
  const label = `${width}-${mode}`
  if (process.env.OPENNESS_CASE && process.env.OPENNESS_CASE !== label) continue
  const app = await pluginHostFixture({ mode })
  app.setRows(app.rows().filter(row => row.layer === 'shipped').map(row => ({ ...row, effective: true })))
  Object.assign(app.routes, filesHost().routes)
  const note = '# A document\n\n' + 'Read this paragraph as written. '.repeat(150)
  app.routes['GET /v1/files/read'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(readAnswer(row('vault', 'Notes/plan.md'), note, 'sha256:fixture'))) }
  const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce', colorScheme: mode, permissions: ['microphone'] })
  const page = await context.newPage(), errors = [], requests = [], captures = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => requests.push(new URL(request.url()).pathname))
  page.setDefaultTimeout(15000)
  if (baseline) await context.route('**/*', async route => {
    const url = new URL(route.request().url()), hosted = /^\/api\/plugins\/([^/]+)\/files\/(.*)$/.exec(url.pathname)
    if (url.origin === app.url && /\.(js|css)$/.test(url.pathname) && url.pathname !== '/themes.css') {
      try { return await route.fulfill({ path: hosted ? path.join(baseline, 'plugins', hosted[1], hosted[2]) : path.join(baseline, url.pathname) }) } catch {}
    }
    return route.fallback()
  })
  await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__open={pluginLoader,registry,coreScope,state,page,threads,goSettings,closeSheet,goProfile,schedule};' }))
  await context.route('**/api/health', route => route.fulfill({ json: { configured: true, ok: true, speech: true } }))
  await context.route('**/x/read-aloud/status', r => r.fulfill({ json: { speech: true } }))
  await context.route('**/x/read-aloud/speech/lookup', route => route.fulfill({ json: { status: 'none' } }))
  await context.route('**/x/read-aloud/speech', route => route.fulfill({ status: 202, json: { id: 'a'.repeat(32), status: 'ready', duration_s: 90, url: '/x/read-aloud/speech/fixture/audio.mp3' } }))
  await context.addInitScript(() => {
    globalThis.__audios = []
    globalThis.Audio = class extends EventTarget {
      constructor() { super(); this.paused = true; this.ended = false; this.currentTime = 0; this.duration = 90; this.buffered = { length: 0 }; globalThis.__audios.push(this) }
      play() { this.paused = false; this.dispatchEvent(new Event('play')); return Promise.resolve() }
      pause() { this.paused = true; this.dispatchEvent(new Event('pause')) }
      load() {}
      removeAttribute() {}
    }
  })
  await context.route('**/x/dictation/transcribe', route => route.fulfill({ json: { text: 'The dictated words.', polish: { ran: true } } }))
  const capture = async name => {
    await page.evaluate(() => document.fonts.ready); await page.waitForTimeout(120)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${label}/${name} page overflow`)
    if (out) await page.screenshot({ path: path.join(out, `${name}-${label}.png`), animations: 'disabled' })
    captures.push(name)
    if (process.env.OPENNESS_PROGRESS) console.log(`${label}/${name}`)
  }
  const openThread = async () => { await page.evaluate(() => { __open.closeSheet(); __open.coreScope.commands.run('core.chats') }); await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click(); await page.locator('#draft').waitFor() }
  const refresh = () => page.evaluate(() => __open.pluginLoader.refresh())
  const toggle = async (id, on) => page.evaluate(([id,on]) => __open.pluginLoader.toggle(id,on), [id,on])
  try {
    await page.goto(app.url); await page.waitForFunction(() => globalThis.__open?.pluginLoader.st.loaded)
    assert.deepEqual(await page.evaluate(() => [...__open.pluginLoader.st.failures]), [])
    await openThread()
    // A long completed fixture reply proves the renderer receives normalized prose.
    app.emit('a-quieter-week', { type: 'message.done', message: { id: 'm90', author: 'sprout', kind: 'message', text: 'A reply to hear. '.repeat(100) } })
    await page.locator('.speakbtn').first().waitFor(); await page.locator('.roundmic').waitFor()
    await capture('both-on-chat')
    await page.locator('.speakbtn').last().click(); await page.locator('.speakmenu').waitFor(); await capture('read-aloud-menu')
    await page.locator('.speakmenu .setrow').first().click(); await page.locator('.player-play').waitFor()
    assert.equal(await page.evaluate(() => __audios.length), 0, 'a prepared take stays silent')
    await page.locator('.player-play').click()
    await page.evaluate(() => __open.coreScope.commands.run('core.chats'))
    await page.locator('.playerbar').waitFor(); await capture('docked-player')
    assert.equal(await page.evaluate(() => __audios.at(-1).paused), false)
    await openThread()
    await page.locator('.roundmic').click(); await page.locator('.take').waitFor(); await capture('dictation-recording')
    assert.equal(await page.evaluate(() => __audios.at(-1).paused), true, 'capture pauses playback through the public event')
    await page.locator('.takebtn.discard').click(); await page.locator('.take').waitFor({ state: 'detached' })
    await page.locator('.player-close:visible,.playerbar-close:visible').first().click()
    await page.evaluate(() => __open.goSettings('plugins')); await page.getByRole('button', { name: 'About Read aloud', exact: true }).click(); await page.locator('#files-voice').waitFor(); await capture('both-on-voice')
    await page.locator('#files-voice').click(); await page.locator('.voice-menu').waitFor(); await page.getByRole('option', { name: 'Kore', exact: true }).click()
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('loam-ui.read-aloud')).voice), 'Kore')
    await capture('voice-reader-preferences')
    await page.evaluate(() => { __open.closeSheet(); __open.page.goFiles('vault','Notes/plan.md') }); await page.locator('.fs-speak').waitFor(); await capture('both-on-files')
    await page.locator('.fs-speak').click(); await page.locator('.file-speech-slider').waitFor(); await capture('files-reader-sheet')
    await page.locator('.file-speech-close').click(); await page.locator('.sheet.file-speech').waitFor({ state: 'detached' })
    if (baseline) { results.push({ label, captures, errors }); continue }
    await toggle('dictation', false)
    await openThread(); assert.equal(await page.locator('.roundmic').count(), 0); assert.ok(await page.locator('.speakbtn').count())
    await toggle('dictation', true); await toggle('read-aloud', false)
    assert.ok(await page.locator('.roundmic').count()); assert.equal(await page.locator('.speakbtn').count(), 0)
    await toggle('dictation', false); await capture('both-off-chat')
    await page.evaluate(() => __open.goSettings('root')); assert.equal(await page.getByRole('button', { name: /Voice/ }).count(), 0)
    await capture('both-off-settings')
    await page.evaluate(() => { __open.closeSheet(); __open.page.goFiles('vault','Notes/plan.md') }); await page.locator('.fs-preview').waitFor()
    assert.equal(await page.locator('.fs-speak,.roundmic').count(), 0); await capture('both-off-files')
    requests.length = 0; await page.reload(); await page.waitForFunction(() => globalThis.__open?.pluginLoader.st.loaded)
    assert.equal(requests.some(url => /\/(?:plugins|api\/plugins)\/(dictation|read-aloud)\/.*\.(js|css)$/.test(url)), false, 'disabled packages must not be imported on boot')
    assert.equal(await page.evaluate(() => ['rec','dictation','speech','termsNote'].some(key => key in __open.state)), false)
    await toggle('dictation', true); await toggle('read-aloud', true)
    await openThread(); await page.locator('.roundmic').waitFor()
    // Disable while fake microphone capture is active, and verify core is usable.
    await page.locator('.roundmic').click(); await page.locator('.take').waitFor(); await toggle('dictation', false)
    await page.locator('.take').waitFor({ state: 'detached' })
    assert.equal(await page.locator('.take,.roundmic').count(), 0)
    await toggle('dictation', true)
    // A temporary personal page module and ordinary CSS override core together.
    const personal = { id: 'personal-proof', name: 'Personal proof', layer: 'yours', folder: 'fixture/personal-proof', api: 0, kind: ['extension'], effective: true, on: true, switch: 'plugin', has: { page_main: true, theme: true }, overrides: [], source: { kind: 'built' }, warnings: [], broken: null }
    app.routes['GET /v1/plugins/personal-proof/files/page/main.js'] = (req,res) => { res.writeHead(200, { 'content-type': 'text/javascript' }); res.end("export function activate(loam) { loam.ui.styles.add('assets/plain.css'); loam.places.message.renderer.add({id:'personal.message',component:'core.message',render:({message})=>loam.ui.h('article',{class:'personal-message',text:'Yours: '+message.text})}); }") }
    for (const file of ['theme.css','assets/plain.css']) app.routes[`GET /v1/plugins/personal-proof/files/${file}`] = (req,res) => { res.writeHead(200, { 'content-type': 'text/css' }); res.end(file === 'theme.css' ? '.personal-message { border-left: 7px solid currentColor; }' : '.tt-title { letter-spacing: 3px; }') }
    app.setRows([...app.rows(), personal]); await refresh(); await page.locator('.personal-message').first().waitFor()
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.tt-title')).letterSpacing === '3px')
    assert.equal(await page.locator('.personal-message').first().evaluate(el => getComputedStyle(el).borderLeftWidth), '7px')
    await capture('yours-override')
    await page.evaluate(() => __open.goSettings('plugins')); await page.getByText('Replaces the message bubble', { exact: true }).waitFor(); await capture('override-marked')
    app.setRows(app.rows().filter(row => row.id !== personal.id)); await refresh(); await page.evaluate(() => __open.closeSheet())
    await page.locator('.personal-message').first().waitFor({ state: 'detached' })
    assert.equal(await page.locator('.personal-message').count(), 0)
    assert.notEqual(await page.locator('.tt-title').first().evaluate(el => getComputedStyle(el).letterSpacing), '3px')
    delete app.routes['GET /v1/plugins/personal-proof/files/page/main.js']; delete app.routes['GET /v1/plugins/personal-proof/files/theme.css']; delete app.routes['GET /v1/plugins/personal-proof/files/assets/plain.css']
    // Same-id fork: only the host-selected Yours package runs; removing it restores shipped.
    const shipped = app.rows().find(row => row.id === 'dictation')
    app.setRows(app.rows().map(row => row === shipped ? { ...row, effective: false } : row).concat({ ...shipped, layer: 'yours', folder: 'fixture/dictation', effective: true, forked_from: { id: 'dictation', version: shipped.version } }))
    const original = app.routes['GET /v1/plugins/dictation/files/page/main.js']
    app.routes['GET /v1/plugins/dictation/files/page/main.js'] = (req,res) => { res.writeHead(200, { 'content-type': 'text/javascript' }); res.end("export function activate(loam) { loam.places.app.overlay.add({id:'fork',render:()=>loam.ui.h('aside',{class:'personal-fork',text:'Yours copy'})}); }") }
    await refresh(); await page.locator('.personal-fork').waitFor(); assert.equal(await page.locator('.roundmic').count(), 0); await capture('same-id-fork')
    app.setRows(app.rows().filter(row => row.id !== 'dictation' || row.layer === 'shipped').map(row => row.id === 'dictation' ? { ...row, effective: true } : row))
    app.routes['GET /v1/plugins/dictation/files/page/main.js'] = original
    await refresh(); await page.locator('.roundmic').waitFor(); assert.equal(await page.locator('.personal-fork').count(), 0)
    assert.deepEqual(errors, [])
    results.push({ label, captures, errors, disabledImports: 0, userOverrideRemoved: true, sameIdRestored: true })
    console.log(`PASS ${label}: ${captures.length} surfaces; independent toggles, fresh boot, active capture cleanup, CSS/component override/removal, same-id fork/restoration`)
  } catch (error) { console.error(label, await page.evaluate(() => ({ failures: [...__open.pluginLoader.st.failures], screen: __open.state.screen, sheet: __open.state.sheet?.kind }))); throw error }
  finally { await context.close(); await app.close() }
}
if (out) await writeFile(path.join(out, 'browser.json'), JSON.stringify({ browser: browser.version(), results }, null, 2)+'\n')
} finally { await browser.close() }
